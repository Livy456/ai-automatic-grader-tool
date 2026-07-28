"""
Submissions: production path is browser → MinIO (presigned PUT) → finalize → Celery.

Multipart upload to the API is opt-in (ALLOW_FLASK_MULTIPART_UPLOAD=true) for local dev only.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session, selectinload
from werkzeug.utils import secure_filename

from app.database.audit import log_event
from app.config import Config
from app.deps import get_current_user, get_db
from app.database.models import AIScore, Assignment, Enrollment, Submission, SubmissionArtifact, User
from app.database.storage import (
    get_presigned_url,
    object_exists,
    presigned_put_url,
    upload_from_fastapi_file,
)
from app.grading.multimodal.grading_report_view import (
    question_from_evidence as _question_from_evidence,
    rubric_totals_from_report as _rubric_totals_from_report,
    safe_report_json as _safe_report_json,
    score_pct_from_rubric_totals as _score_pct_from_rubric_totals,
    score_to_100 as _score_to_100,
    student_evidence_from_payload as _student_evidence_from_payload,
)
from app.tasks import grade_submission

router = APIRouter()

_SUBMITTER_ROLES = frozenset({"student", "teacher", "admin"})


def _is_authorized_submitter(db: Session, user: dict, assignment_id: int) -> bool:
    """
    True if the user may create a submission for this assignment.
    Admins bypass enrollment; teachers and students must be enrolled in the course.
    """
    a = db.query(Assignment).get(assignment_id)
    if not a:
        return False
    if user.get("role") == "admin":
        return True
    return (
        db.query(Enrollment)
        .filter_by(user_id=user["id"], course_id=a.course_id)
        .first()
        is not None
    )


@router.post("/api/submissions/direct-upload/start")
def direct_upload_start(
    body: dict[str, Any] = Body(default={}),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Create submission + artifact rows and return presigned PUT URLs.
    Browser uploads bytes directly to MinIO (no large body through the API).
    """
    if user["role"] not in _SUBMITTER_ROLES:
        raise HTTPException(403, "submission not permitted for this role")

    assignment_id = body.get("assignment_id")
    files = body.get("files")
    if assignment_id is None or not files or not isinstance(files, list):
        raise HTTPException(400, "assignment_id and files[] required")

    assignment_id = int(assignment_id)
    cfg = Config()

    if not _is_authorized_submitter(db, user, assignment_id):
        raise HTTPException(403, "not enrolled or not authorized")

    sub = Submission(
        assignment_id=assignment_id,
        student_id=user["id"],
        status="uploading",
    )
    db.add(sub)
    db.flush()

    prefix = cfg.UPLOADS_OBJECT_PREFIX.rstrip("/")
    uploads_out = []
    for spec in files:
        raw_name = (spec.get("filename") or "").strip()
        filename = secure_filename(raw_name)
        if not filename:
            continue
        content_type = (spec.get("content_type") or "application/octet-stream").strip()
        kind = filename.split(".")[-1].lower()
        key = f"{prefix}/{assignment_id}/submissions/{sub.id}/{uuid.uuid4().hex}_{filename}"
        art = SubmissionArtifact(submission_id=sub.id, kind=kind, object_key=key)
        db.add(art)
        db.flush()
        url = presigned_put_url(cfg, key, content_type)
        uploads_out.append(
            {
                "artifact_id": art.id,
                "object_key": key,
                "upload_url": url,
                "content_type": content_type,
            }
        )

    if not uploads_out:
        db.rollback()
        raise HTTPException(400, "no valid files")

    db.commit()
    db.refresh(sub)
    log_event(
        user["id"],
        "CREATE_SUBMISSION_PRESIGN",
        "Submission",
        sub.id,
        {"assignment_id": assignment_id, "n_files": len(uploads_out)},
    )
    return {
        "submission_id": sub.id,
        "status": sub.status,
        "uploads": uploads_out,
    }


@router.post("/api/submissions/{submission_id}/finalize")
def direct_upload_finalize(
    submission_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Verify object-store files exist, then enqueue grading at most once (row lock)."""
    cfg = Config()
    if user["role"] not in _SUBMITTER_ROLES:
        raise HTTPException(403, "submission not permitted for this role")

    sub = (
        db.query(Submission)
        .options(selectinload(Submission.artifacts))
        .filter_by(id=submission_id)
        .with_for_update()
        .first()
    )
    if not sub:
        raise HTTPException(404, "not found")
    if sub.student_id is None:
        raise HTTPException(403, "forbidden")
    if sub.student_id != user["id"]:
        raise HTTPException(403, "forbidden")

    if sub.grading_dispatch_at is not None:
        db.commit()
        return {
            "submission_id": sub.id,
            "status": sub.status,
            "already_enqueued": True,
        }

    if sub.status in ("queued", "grading", "graded", "needs_review", "error"):
        return {
            "submission_id": sub.id,
            "status": sub.status,
            "already_finalized": True,
        }

    if sub.status not in ("uploading", "uploaded"):
        raise HTTPException(409, f"invalid state: {sub.status}")

    for art in sub.artifacts:
        if not object_exists(cfg, art.object_key):
            raise HTTPException(400, f"missing object: {art.object_key}")

    sub.status = "uploaded"
    sub.updated_at = datetime.utcnow()
    db.flush()

    sub.status = "queued"
    sub.grading_dispatch_at = datetime.utcnow()
    try:
        task = grade_submission.delay(sub.id)
    except Exception:
        db.rollback()
        raise HTTPException(503, "failed to enqueue grading job")
    sub.grading_celery_task_id = task.id
    sub.updated_at = datetime.utcnow()
    db.commit()

    log_event(
        user["id"],
        "FINALIZE_SUBMISSION",
        "Submission",
        sub.id,
        {"celery_task_id": task.id},
    )
    return {
        "submission_id": sub.id,
        "status": "queued",
        "celery_task_id": task.id,
    }


@router.post("/api/submissions")
def submit(
    assignment_id: int = Form(...),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Legacy multipart → API → MinIO. Disabled in production (use direct-upload flow)."""
    cfg = Config()
    if not cfg.ALLOW_FLASK_MULTIPART_UPLOAD:
        raise HTTPException(
            410,
            {
                "error": "multipart upload disabled",
                "hint": "Use POST /api/submissions/direct-upload/start then object-store PUT then finalize",
            },
        )

    sub = Submission(
        assignment_id=assignment_id,
        student_id=user["id"],
        status="queued",
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)

    for f in files:
        filename = secure_filename(f.filename or "")
        if not filename:
            continue
        kind = filename.split(".")[-1].lower()
        key = (
            f"{cfg.UPLOADS_OBJECT_PREFIX.rstrip('/')}/{assignment_id}/submissions/{sub.id}/"
            f"{uuid.uuid4().hex}_{filename}"
        )
        upload_from_fastapi_file(cfg, f, key)
        db.add(SubmissionArtifact(submission_id=sub.id, kind=kind, object_key=key))

    sub.grading_dispatch_at = datetime.utcnow()
    task = grade_submission.delay(sub.id)
    sub.grading_celery_task_id = task.id
    db.commit()

    log_event(
        user["id"], "CREATE_SUBMISSION", "Submission", sub.id, {"assignment_id": assignment_id}
    )
    return {"submission_id": sub.id, "status": sub.status, "celery_task_id": task.id}


def _display_name(u: User | None) -> str:
    if not u:
        return "—"
    return u.name or u.email or "—"


@router.get("/api/teacher/submissions")
def list_recent_submissions(
    limit: int = 200,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Recent submissions, newest first, for the "View Submissions" history page. Students see only
    their own submissions; teachers/admins see everything (same scoping rule already used by
    ``GET /api/submissions/{id}``).
    """
    q = (
        db.query(Submission)
        .options(selectinload(Submission.assignment).selectinload(Assignment.course))
        .order_by(Submission.created_at.desc())
    )
    if user["role"] == "student":
        q = q.filter(Submission.student_id == user["id"])
    subs = q.limit(max(1, min(limit, 500))).all()

    student_ids = {s.student_id for s in subs if s.student_id is not None}
    students_by_id = (
        {u.id: u for u in db.query(User).filter(User.id.in_(student_ids)).all()} if student_ids else {}
    )

    return [
        {
            "id": s.id,
            "student_id": s.student_id,
            "assignment_id": s.assignment_id,
            "course": (s.assignment.course.code if s.assignment and s.assignment.course else None) or "—",
            "student": _display_name(students_by_id.get(s.student_id)),
            "assignment": (s.assignment.title if s.assignment else None) or "—",
            "submitted": s.created_at.isoformat() if s.created_at else None,
            "status": s.status,
            "final_score": float(s.final_score) if s.final_score is not None else None,
        }
        for s in subs
    ]


@router.get("/api/submissions/{submission_id}")
def get_submission(
    submission_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Course/library submission results — same response shape as the standalone autograder's
    ``GET /api/standalone/submissions/{id}`` (see ``app.routes.standalone.standalone_get``) so
    both results pages can share one frontend view: rubric totals + a per-question breakdown are
    read back from the MinIO grading report (built by ``app.tasks.grade_submission`` via
    :mod:`app.grading.multimodal.grading_report`), falling back to the flat ``ai_scores`` list
    when no report/question breakdown is available yet.
    """
    sub = (
        db.query(Submission)
        .options(selectinload(Submission.assignment))
        .filter_by(id=submission_id)
        .first()
    )
    if not sub:
        raise HTTPException(404, "not found")

    if (
        user["role"] == "student"
        and sub.student_id is not None
        and sub.student_id != user["id"]
    ):
        raise HTTPException(403, "forbidden")

    cfg = Config()
    report = _safe_report_json(cfg, sub.grading_report_object_key)
    scores = db.query(AIScore).filter_by(submission_id=sub.id).all()
    log_event(user["id"], "VIEW_SUBMISSION", "Submission", sub.id, {})

    final_score = _score_to_100(sub.final_score)
    if final_score is None and report.get("final_score") is not None:
        final_score = _score_to_100(report.get("final_score"))
    question_grades = report.get("question_grades")
    if not isinstance(question_grades, list):
        question_grades = []
    rubric_points_earned, rubric_points_max = _rubric_totals_from_report(report)
    if (rubric_points_earned or 0.0) <= 0.0:
        d_earned = 0.0
        for s in scores:
            try:
                d_earned += float(s.score or 0.0)
            except (TypeError, ValueError):
                pass
        rubric_points_earned = d_earned
    if (rubric_points_max or 0.0) <= 0.0:
        d_max = 0.0
        for qg in question_grades:
            if not isinstance(qg, dict):
                continue
            for qc in qg.get("criteria") or []:
                if not isinstance(qc, dict):
                    continue
                try:
                    d_max += float(qc.get("max_points") or 0.0)
                except (TypeError, ValueError):
                    pass
        rubric_points_max = d_max
    rubric_based_score = _score_pct_from_rubric_totals(rubric_points_earned, rubric_points_max)
    if rubric_based_score is not None:
        final_score = rubric_based_score

    return {
        "id": sub.id,
        "status": sub.status,
        "assignment_title": sub.assignment.title if sub.assignment else None,
        "student_id": sub.student_id,
        "final_score": final_score,
        "final_feedback": sub.final_feedback,
        "max_points": round(float(rubric_points_max), 4) if rubric_points_max is not None else None,
        "rubric_points_earned": (
            round(float(rubric_points_earned), 4) if rubric_points_earned is not None else None
        ),
        "grading_dispatch_at": sub.grading_dispatch_at.isoformat()
        if sub.grading_dispatch_at
        else None,
        "created_at": sub.created_at.isoformat() if sub.created_at else None,
        "grading_report_object_key": sub.grading_report_object_key,
        "question_grades": question_grades,
        "ai_scores": [
            {
                "criterion": s.criterion,
                "score": float(s.score) if s.score is not None else 0.0,
                "confidence": float(s.confidence),
                "justification": s.rationale,
                "rationale": s.rationale,
                "question": _question_from_evidence(s.evidence),
                "student_evidence": _student_evidence_from_payload(s.evidence),
                "evidence": s.evidence if isinstance(s.evidence, dict) else {},
            }
            for s in scores
        ],
    }


@router.get("/api/submissions/{submission_id}/report")
def get_submission_report(
    submission_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Presigned GET URL for the grading report JSON in MinIO (mirrors the standalone autograder's
    ``GET /api/standalone/submissions/{id}/report``)."""
    cfg = Config()
    sub = db.query(Submission).get(submission_id)
    if not sub:
        raise HTTPException(404, "not found")
    if (
        user["role"] == "student"
        and sub.student_id is not None
        and sub.student_id != user["id"]
    ):
        raise HTTPException(403, "forbidden")
    if not sub.grading_report_object_key:
        raise HTTPException(404, "report not available yet")
    url = get_presigned_url(
        cfg,
        sub.grading_report_object_key,
        method="GET",
        expires=3600,
        bucket=cfg.MINIO_GRADING_REPORTS_BUCKET,
    )
    return {"download_url": url, "object_key": sub.grading_report_object_key}
