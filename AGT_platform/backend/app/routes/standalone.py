"""
Public standalone autograder: JWT required for uploads.

Uses StandaloneSubmission + StandaloneArtifact + StandaloneAIScore and grade_standalone_submission.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request
from sqlalchemy.orm import selectinload
from werkzeug.utils import secure_filename

from app.audit import log_event
from app.config import Config
from app.extensions import SessionLocal
from app.models import StandaloneAIScore, StandaloneArtifact, StandaloneSubmission
from app.rbac import get_user_from_token
from app.storage import get_presigned_url, object_exists, presigned_put_url
from app.tasks import grade_standalone_submission

bp = Blueprint("standalone", __name__)

_MAX_FILES = 20
_MAX_TITLE_LEN = 512
_STANDALONE_RATE_WINDOW_HOURS = 1
_STANDALONE_RATE_MAX = 10


def _client_ip() -> str:
    xff = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    if xff:
        return xff[:64]
    return (request.remote_addr or "")[:64]


def _optional_user() -> dict | None:
    return get_user_from_token()


def _can_view_standalone(sub: StandaloneSubmission, user: dict | None) -> bool:
    if not user:
        return False
    if user.get("role") == "admin":
        return True
    return sub.user_id is not None and int(sub.user_id) == int(user["id"])


def _can_mutate_standalone(sub: StandaloneSubmission, user: dict | None) -> bool:
    return _can_view_standalone(sub, user)


def _kind_for_spec(spec: dict, default: str) -> str:
    raw = (spec.get("artifact_kind") or spec.get("kind") or default).strip().lower()
    if raw in ("submission", "rubric", "answer_key"):
        return raw
    return default


def _storage_kind_for_file(spec: dict, filename: str) -> str:
    role = _kind_for_spec(spec, "submission")
    if role == "rubric":
        return "rubric"
    if role == "answer_key":
        return "answer_key"
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    return ext or "bin"


def _parse_enqueue_grading(body: dict) -> bool:
    if body.get("enqueue_grading") is False:
        return False
    if body.get("defer_grading") is True:
        return False
    return True


@bp.post("/api/standalone/submissions/start")
def standalone_start():
    """Create StandaloneSubmission + StandaloneArtifact rows; return presigned PUT URLs."""
    user = _optional_user()
    if not user:
        return jsonify({"error": "authentication required"}), 401

    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title is required"}), 400
    if len(title) > _MAX_TITLE_LEN:
        return jsonify({"error": "title too long"}), 400

    files = body.get("files")
    if not files or not isinstance(files, list):
        return jsonify({"error": "files[] required"}), 400
    if len(files) > _MAX_FILES:
        return jsonify({"error": f"at most {_MAX_FILES} files"}), 400

    rubric_text = (body.get("rubric_text") or "").strip() or None
    answer_key_text = (body.get("answer_key_text") or "").strip() or None
    grading_instructions = (body.get("grading_instructions") or "").strip() or None

    cfg = Config()
    db = SessionLocal()
    try:
        since = datetime.utcnow() - timedelta(hours=_STANDALONE_RATE_WINDOW_HOURS)
        recent = (
            db.query(StandaloneSubmission)
            .filter(
                StandaloneSubmission.user_id == user["id"],
                StandaloneSubmission.created_at >= since,
                StandaloneSubmission.status != "deleted",
            )
            .count()
        )
        if recent >= _STANDALONE_RATE_MAX:
            return (
                jsonify(
                    {
                        "error": "rate limit",
                        "detail": f"max {_STANDALONE_RATE_MAX} autograder uploads per {_STANDALONE_RATE_WINDOW_HOURS}h",
                    }
                ),
                429,
            )

        sub = StandaloneSubmission(
            user_id=int(user["id"]),
            title=title,
            status="uploading",
            rubric_text=rubric_text,
            answer_key_text=answer_key_text,
            grading_instructions=grading_instructions,
        )
        db.add(sub)
        db.flush()

        uploads_out = []
        for spec in files:
            raw_name = (spec.get("filename") or "").strip()
            filename = secure_filename(raw_name)
            if not filename:
                continue
            content_type = (spec.get("content_type") or "application/octet-stream").strip()
            skind = _storage_kind_for_file(spec, filename)
            key = f"standalone/{sub.id}/{uuid.uuid4().hex}_{filename}"
            art = StandaloneArtifact(
                submission_id=sub.id,
                kind=skind,
                s3_key=key,
                filename=filename,
            )
            db.add(art)
            db.flush()
            url = presigned_put_url(cfg, key, content_type)
            uploads_out.append(
                {
                    "artifact_id": art.id,
                    "s3_key": key,
                    "upload_url": url,
                    "content_type": content_type,
                }
            )

        if not uploads_out:
            db.rollback()
            return jsonify({"error": "no valid files"}), 400

        db.commit()
        db.refresh(sub)
        log_event(
            user["id"],
            "CREATE_STANDALONE_AUTOGRADER",
            "StandaloneSubmission",
            sub.id,
            {"n_files": len(uploads_out)},
        )
        return jsonify(
            {
                "submission_id": sub.id,
                "status": sub.status,
                "uploads": uploads_out,
            }
        )
    finally:
        db.close()


@bp.post("/api/standalone/submissions/<int:submission_id>/finalize")
def standalone_finalize(submission_id: int):
    user = _optional_user()
    if not user:
        return jsonify({"error": "authentication required"}), 401

    body = request.get_json(silent=True) or {}
    enqueue_grading = _parse_enqueue_grading(body)
    cfg = Config()
    db = SessionLocal()
    try:
        sub = (
            db.query(StandaloneSubmission)
            .options(selectinload(StandaloneSubmission.artifacts))
            .filter_by(id=submission_id)
            .with_for_update()
            .first()
        )
        if not sub or sub.status == "deleted":
            return jsonify({"error": "not found"}), 404
        if not _can_mutate_standalone(sub, user):
            return jsonify({"error": "forbidden"}), 403

        if sub.grading_dispatch_at is not None:
            db.commit()
            return jsonify(
                {
                    "submission_id": sub.id,
                    "status": sub.status,
                    "already_enqueued": True,
                }
            )

        if sub.status in ("queued", "grading", "graded", "needs_review", "error"):
            return jsonify(
                {
                    "submission_id": sub.id,
                    "status": sub.status,
                    "already_finalized": True,
                }
            )

        if sub.status not in ("uploading", "uploaded"):
            return jsonify({"error": f"invalid state: {sub.status}"}), 409

        for art in sub.artifacts:
            if not object_exists(cfg, art.s3_key):
                return jsonify({"error": f"missing object: {art.s3_key}"}), 400

        sub.status = "uploaded"
        sub.updated_at = datetime.utcnow()
        db.flush()

        if not enqueue_grading:
            db.commit()
            log_event(
                user["id"],
                "FINALIZE_STANDALONE_UPLOAD",
                "StandaloneSubmission",
                sub.id,
                {"enqueue_grading": False},
            )
            return jsonify(
                {
                    "submission_id": sub.id,
                    "status": "uploaded",
                    "enqueue_grading": False,
                }
            )

        sub.status = "queued"
        sub.grading_dispatch_at = datetime.utcnow()
        try:
            task = grade_standalone_submission.delay(sub.id)
        except Exception:
            db.rollback()
            return jsonify({"error": "failed to enqueue grading job"}), 503
        sub.grading_celery_task_id = task.id
        sub.updated_at = datetime.utcnow()
        db.commit()

        log_event(
            user["id"],
            "FINALIZE_STANDALONE_AUTOGRADER",
            "StandaloneSubmission",
            sub.id,
            {"celery_task_id": task.id},
        )
        return jsonify(
            {
                "submission_id": sub.id,
                "status": "queued",
                "celery_task_id": task.id,
            }
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@bp.patch("/api/standalone/submissions/<int:submission_id>/context")
def standalone_patch_context(submission_id: int):
    user = _optional_user()
    if not user:
        return jsonify({"error": "authentication required"}), 401

    body = request.get_json(silent=True) or {}
    db = SessionLocal()
    try:
        sub = (
            db.query(StandaloneSubmission)
            .filter_by(id=submission_id)
            .with_for_update()
            .first()
        )
        if not sub or sub.status == "deleted":
            return jsonify({"error": "not found"}), 404
        if not _can_mutate_standalone(sub, user):
            return jsonify({"error": "forbidden"}), 403
        if sub.status != "uploaded" or sub.grading_dispatch_at is not None:
            return (
                jsonify(
                    {
                        "error": "invalid state",
                        "detail": "context can only be edited after upload and before grading is queued",
                    }
                ),
                409,
            )

        if "rubric_text" in body:
            v = body.get("rubric_text")
            sub.rubric_text = (str(v).strip() if v is not None else "") or None
        if "answer_key_text" in body:
            v = body.get("answer_key_text")
            sub.answer_key_text = (str(v).strip() if v is not None else "") or None
        if "grading_instructions" in body:
            v = body.get("grading_instructions")
            sub.grading_instructions = (str(v).strip() if v is not None else "") or None

        sub.updated_at = datetime.utcnow()
        db.commit()
        log_event(
            user["id"],
            "PATCH_STANDALONE_CONTEXT",
            "StandaloneSubmission",
            sub.id,
            {},
        )
        return jsonify({"submission_id": sub.id, "ok": True})
    finally:
        db.close()


@bp.post("/api/standalone/submissions/<int:submission_id>/context_files/presign")
def standalone_presign_context_files(submission_id: int):
    user = _optional_user()
    if not user:
        return jsonify({"error": "authentication required"}), 401

    body = request.get_json(silent=True) or {}
    files = body.get("files")
    if not files or not isinstance(files, list):
        return jsonify({"error": "files[] required"}), 400

    cfg = Config()
    db = SessionLocal()
    try:
        sub = (
            db.query(StandaloneSubmission)
            .options(selectinload(StandaloneSubmission.artifacts))
            .filter_by(id=submission_id)
            .with_for_update()
            .first()
        )
        if not sub or sub.status == "deleted":
            return jsonify({"error": "not found"}), 404
        if not _can_mutate_standalone(sub, user):
            return jsonify({"error": "forbidden"}), 403
        if sub.status != "uploaded" or sub.grading_dispatch_at is not None:
            return jsonify({"error": "invalid state for context file upload"}), 409

        if len(sub.artifacts) + len(files) > _MAX_FILES:
            return jsonify({"error": f"at most {_MAX_FILES} files per submission"}), 400

        uploads_out = []
        for spec in files:
            raw_name = (spec.get("filename") or "").strip()
            filename = secure_filename(raw_name)
            if not filename:
                continue
            role = _kind_for_spec(spec, "rubric")
            if role not in ("rubric", "answer_key"):
                return jsonify({"error": "context files must be rubric or answer_key"}), 400
            content_type = (spec.get("content_type") or "application/octet-stream").strip()
            skind = _storage_kind_for_file(spec, filename)
            key = f"standalone/{sub.id}/{uuid.uuid4().hex}_{filename}"
            art = StandaloneArtifact(
                submission_id=sub.id,
                kind=skind,
                s3_key=key,
                filename=filename,
            )
            db.add(art)
            db.flush()
            url = presigned_put_url(cfg, key, content_type)
            uploads_out.append(
                {
                    "artifact_id": art.id,
                    "s3_key": key,
                    "upload_url": url,
                    "content_type": content_type,
                }
            )

        if not uploads_out:
            db.rollback()
            return jsonify({"error": "no valid files"}), 400

        db.commit()
        log_event(
            user["id"],
            "PRESIGN_STANDALONE_CONTEXT",
            "StandaloneSubmission",
            sub.id,
            {"n_files": len(uploads_out)},
        )
        return jsonify(
            {
                "submission_id": sub.id,
                "uploads": uploads_out,
            }
        )
    finally:
        db.close()


@bp.post("/api/standalone/submissions/<int:submission_id>/enqueue_grading")
def standalone_enqueue_grading(submission_id: int):
    user = _optional_user()
    if not user:
        return jsonify({"error": "authentication required"}), 401

    cfg = Config()
    db = SessionLocal()
    try:
        sub = (
            db.query(StandaloneSubmission)
            .options(selectinload(StandaloneSubmission.artifacts))
            .filter_by(id=submission_id)
            .with_for_update()
            .first()
        )
        if not sub or sub.status == "deleted":
            return jsonify({"error": "not found"}), 404
        if not _can_mutate_standalone(sub, user):
            return jsonify({"error": "forbidden"}), 403

        if sub.grading_dispatch_at is not None:
            db.commit()
            return jsonify(
                {
                    "submission_id": sub.id,
                    "status": sub.status,
                    "already_enqueued": True,
                }
            )

        if sub.status != "uploaded":
            return jsonify({"error": f"expected status uploaded, got {sub.status}"}), 409

        for art in sub.artifacts:
            if not object_exists(cfg, art.s3_key):
                return jsonify({"error": f"missing object: {art.s3_key}"}), 400

        sub.status = "queued"
        sub.grading_dispatch_at = datetime.utcnow()
        try:
            task = grade_standalone_submission.delay(sub.id)
        except Exception:
            db.rollback()
            return jsonify({"error": "failed to enqueue grading job"}), 503
        sub.grading_celery_task_id = task.id
        sub.updated_at = datetime.utcnow()
        db.commit()

        log_event(
            user["id"],
            "ENQUEUE_STANDALONE_AUTOGRADER",
            "StandaloneSubmission",
            sub.id,
            {"celery_task_id": task.id},
        )
        return jsonify(
            {
                "submission_id": sub.id,
                "status": "queued",
                "celery_task_id": task.id,
            }
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@bp.get("/api/standalone/submissions")
def standalone_list():
    user = _optional_user()
    if not user:
        return jsonify({"error": "authentication required"}), 401

    page = int(request.args.get("page") or 1)
    per_page = min(int(request.args.get("per_page") or 20), 100)
    if page < 1:
        page = 1

    db = SessionLocal()
    try:
        q = db.query(StandaloneSubmission).filter(
            StandaloneSubmission.user_id == user["id"],
            StandaloneSubmission.status != "deleted",
        )
        total = q.count()
        rows = (
            q.order_by(StandaloneSubmission.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )
        items = []
        for r in rows:
            items.append(
                {
                    "id": r.id,
                    "title": r.title,
                    "status": r.status,
                    "final_score": float(r.final_score) if r.final_score is not None else None,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
            )
        return jsonify(
            {"items": items, "total": total, "page": page, "per_page": per_page}
        )
    finally:
        db.close()


@bp.get("/api/standalone/submissions/<int:submission_id>")
def standalone_get(submission_id: int):
    user = _optional_user()
    if not user:
        return jsonify({"error": "authentication required"}), 401

    db = SessionLocal()
    try:
        sub = db.query(StandaloneSubmission).filter_by(id=submission_id).first()
        if not sub or sub.status == "deleted":
            return jsonify({"error": "not found"}), 404
        if not _can_view_standalone(sub, user):
            return jsonify({"error": "forbidden"}), 403

        scores = db.query(StandaloneAIScore).filter_by(submission_id=sub.id).all()
        log_event(user["id"], "VIEW_STANDALONE_AUTOGRADER", "StandaloneSubmission", sub.id, {})
        return jsonify(
            {
                "id": sub.id,
                "title": sub.title,
                "status": sub.status,
                "final_score": float(sub.final_score) if sub.final_score is not None else None,
                "final_feedback": sub.final_feedback,
                "grading_instructions": sub.grading_instructions,
                "grading_dispatch_at": sub.grading_dispatch_at.isoformat()
                if sub.grading_dispatch_at
                else None,
                "created_at": sub.created_at.isoformat() if sub.created_at else None,
                "grading_report_s3_key": sub.grading_report_s3_key,
                "ai_scores": [
                    {
                        "criterion": s.criterion,
                        "score": float(s.score),
                        "confidence": float(s.confidence),
                        "rationale": s.rationale,
                    }
                    for s in scores
                ],
            }
        )
    finally:
        db.close()


@bp.get("/api/standalone/submissions/<int:submission_id>/report")
def standalone_get_report(submission_id: int):
    """Return a presigned GET URL for the grading report JSON in S3."""
    user = _optional_user()
    if not user:
        return jsonify({"error": "authentication required"}), 401

    cfg = Config()
    db = SessionLocal()
    try:
        sub = db.query(StandaloneSubmission).filter_by(id=submission_id).first()
        if not sub or sub.status == "deleted":
            return jsonify({"error": "not found"}), 404
        if not _can_view_standalone(sub, user):
            return jsonify({"error": "forbidden"}), 403
        if not sub.grading_report_s3_key:
            return jsonify({"error": "report not available yet"}), 404
        url = get_presigned_url(
            cfg,
            sub.grading_report_s3_key,
            method="GET",
            expires=3600,
            bucket=cfg.S3_GRADING_REPORTS_BUCKET,
        )
        return jsonify(
            {"download_url": url, "s3_key": sub.grading_report_s3_key}
        )
    finally:
        db.close()


@bp.delete("/api/standalone/submissions/<int:submission_id>")
def standalone_delete(submission_id: int):
    user = _optional_user()
    if not user:
        return jsonify({"error": "authentication required"}), 401

    db = SessionLocal()
    try:
        sub = (
            db.query(StandaloneSubmission)
            .filter_by(id=submission_id)
            .with_for_update()
            .first()
        )
        if not sub or sub.status == "deleted":
            return jsonify({"error": "not found"}), 404
        if not _can_mutate_standalone(sub, user):
            return jsonify({"error": "forbidden"}), 403

        sub.status = "deleted"
        sub.updated_at = datetime.utcnow()
        db.commit()
        log_event(user["id"], "DELETE_STANDALONE_AUTOGRADER", "StandaloneSubmission", sub.id, {})
        return jsonify({"ok": True})
    finally:
        db.close()
