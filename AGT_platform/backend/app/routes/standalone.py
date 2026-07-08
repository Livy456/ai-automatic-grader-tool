"""
Public standalone autograder: JWT required for uploads.

Uses StandaloneSubmission + StandaloneArtifact + StandaloneAIScore and grade_standalone_submission.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request
from sqlalchemy.orm import selectinload
from werkzeug.utils import secure_filename

from app.audit import log_event
from app.config import Config
from app.extensions import SessionLocal
from app.models import StandaloneAIScore, StandaloneArtifact, StandaloneSubmission, User
from app.access import get_user_from_token
from app.storage import get_object_bytes, get_presigned_url, object_exists, presigned_put_url
from app.tasks import grade_standalone_submission

bp = Blueprint("standalone", __name__)

_MAX_FILES = 20
_MAX_TITLE_LEN = 512
_STANDALONE_RATE_WINDOW_HOURS = 1
_STANDALONE_RATE_MAX = 10
_GUEST_EMAIL = "guest@local.ai-grader"


def _client_ip() -> str:
    xff = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    if xff:
        return xff[:64]
    return (request.remote_addr or "")[:64]


def _optional_user() -> dict | None:
    return get_user_from_token()


def _standalone_user() -> dict:
    """
    Standalone autograder supports both authenticated and anonymous flows.
    When no auth token is present, use a persisted local guest account.
    """
    user = _optional_user()
    if user:
        return user
    db = SessionLocal()
    try:
        guest = db.query(User).filter_by(email=_GUEST_EMAIL).one_or_none()
        if guest is None:
            guest = User(
                email=_GUEST_EMAIL,
                name="Guest User",
                role="admin",
            )
            db.add(guest)
            db.commit()
            db.refresh(guest)
        return {"id": int(guest.id), "email": guest.email, "role": guest.role}
    finally:
        db.close()


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
    if raw in ("submission", "rubric", "answer_key", "blank_assignment"):
        return raw
    return default


def _storage_kind_for_file(spec: dict, filename: str) -> str:
    role = _kind_for_spec(spec, "submission")
    if role == "rubric":
        return "rubric"
    if role == "answer_key":
        return "answer_key"
    if role == "blank_assignment":
        return "blank_assignment"
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    return ext or "bin"


def _parse_enqueue_grading(body: dict) -> bool:
    if body.get("enqueue_grading") is False:
        return False
    if body.get("defer_grading") is True:
        return False
    return True


def _required_context_missing(sub: StandaloneSubmission, artifacts: list[StandaloneArtifact]) -> list[str]:
    kinds = {str(a.kind or "").strip().lower() for a in artifacts}
    has_rubric_json = any(
        str(a.kind or "").strip().lower() == "rubric"
        and str(a.filename or "").strip().lower().endswith(".json")
        for a in artifacts
    )
    has_answer_key = bool((sub.answer_key_text or "").strip()) or "answer_key" in kinds
    # Force uploaded JSON rubric usage so standalone criteria come from uploaded rubric rows.
    has_rubric = has_rubric_json
    has_blank_assignment = "blank_assignment" in kinds
    missing: list[str] = []
    if not has_answer_key:
        missing.append("answer_key")
    if not has_rubric:
        missing.append("rubric_json")
    if not has_blank_assignment:
        missing.append("blank_assignment")
    return missing


def _question_from_evidence(ev: object) -> str | None:
    if not isinstance(ev, dict):
        return None
    trio = ev.get("trio")
    if isinstance(trio, dict):
        q = str(trio.get("question") or "").strip()
        if q:
            return q
    q = str(ev.get("question") or "").strip()
    return q or None


def _student_evidence_from_payload(ev: object) -> str:
    if not isinstance(ev, dict):
        return ""
    trio = ev.get("trio")
    if isinstance(trio, dict):
        sr = str(trio.get("student_response") or "").strip()
        if sr:
            return sr
    txt = str(ev.get("text") or "").strip()
    return txt


def _score_to_100(raw: object) -> float | None:
    try:
        score = float(raw)
    except (TypeError, ValueError):
        return None
    if 0.0 <= score <= 1.0:
        score *= 100.0
    if score < 0.0:
        score = 0.0
    if score > 100.0:
        score = 100.0
    return score


def _safe_report_json(cfg: Config, object_key: str | None) -> dict:
    if not object_key:
        return {}
    try:
        raw = get_object_bytes(cfg, object_key)
        if not raw:
            return {}
        parsed = json.loads(raw.decode("utf-8", errors="ignore"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _iso_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


@bp.post("/api/standalone/submissions/start")
def standalone_start():
    """Create StandaloneSubmission + StandaloneArtifact rows; return presigned PUT URLs."""
    user = _standalone_user()

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
                object_key=key,
                filename=filename,
            )
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
    user = _standalone_user()

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

        missing = _required_context_missing(sub, list(sub.artifacts))
        if missing:
            return (
                jsonify(
                    {
                        "error": "missing required context",
                        "detail": (
                            "Standalone grading requires answer key, uploaded JSON rubric, and blank "
                            "assignment template in Additional Context before finalize."
                        ),
                        "missing": missing,
                    }
                ),
                400,
            )

        for art in sub.artifacts:
            if not object_exists(cfg, art.object_key):
                return jsonify({"error": f"missing object: {art.object_key}"}), 400

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
    user = _standalone_user()

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
    user = _standalone_user()

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
            if role not in ("rubric", "answer_key", "blank_assignment"):
                return (
                    jsonify(
                        {
                            "error": (
                                "context files must be rubric, answer_key, or blank_assignment"
                            )
                        }
                    ),
                    400,
                )
            content_type = (spec.get("content_type") or "application/octet-stream").strip()
            skind = _storage_kind_for_file(spec, filename)
            key = f"standalone/{sub.id}/{uuid.uuid4().hex}_{filename}"
            art = StandaloneArtifact(
                submission_id=sub.id,
                kind=skind,
                object_key=key,
                filename=filename,
            )
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
    user = _standalone_user()

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

        missing = _required_context_missing(sub, list(sub.artifacts))
        if missing:
            return (
                jsonify(
                    {
                        "error": "missing required context",
                        "detail": (
                            "Standalone grading requires answer key, uploaded JSON rubric, and blank "
                            "assignment template in Additional Context before enqueue."
                        ),
                        "missing": missing,
                    }
                ),
                400,
            )

        for art in sub.artifacts:
            if not object_exists(cfg, art.object_key):
                return jsonify({"error": f"missing object: {art.object_key}"}), 400

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
    user = _standalone_user()

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
            score = _score_to_100(r.final_score)
            items.append(
                {
                    "id": r.id,
                    "title": r.title,
                    "status": r.status,
                    "final_score": score,
                    "created_at": _iso_utc(r.created_at),
                }
            )
        return jsonify(
            {"items": items, "total": total, "page": page, "per_page": per_page}
        )
    finally:
        db.close()


@bp.get("/api/standalone/submissions/<int:submission_id>")
def standalone_get(submission_id: int):
    user = _standalone_user()

    db = SessionLocal()
    try:
        sub = db.query(StandaloneSubmission).filter_by(id=submission_id).first()
        if not sub or sub.status == "deleted":
            return jsonify({"error": "not found"}), 404
        if not _can_view_standalone(sub, user):
            return jsonify({"error": "forbidden"}), 403

        cfg = Config()
        report = _safe_report_json(cfg, sub.grading_report_object_key)
        scores = db.query(StandaloneAIScore).filter_by(submission_id=sub.id).all()
        log_event(user["id"], "VIEW_STANDALONE_AUTOGRADER", "StandaloneSubmission", sub.id, {})
        final_score = _score_to_100(sub.final_score)
        if final_score is None and report.get("final_score") is not None:
            final_score = _score_to_100(report.get("final_score"))
        question_grades = report.get("question_grades")
        if not isinstance(question_grades, list):
            question_grades = []
        q_earned = 0.0
        q_max = 0.0
        for qg in question_grades:
            if not isinstance(qg, dict):
                continue
            ov = qg.get("overall") or {}
            try:
                q_earned += float(ov.get("rubric_points_earned") or 0.0)
            except (TypeError, ValueError):
                pass
            try:
                q_max += float(ov.get("max_points") or 0.0)
            except (TypeError, ValueError):
                pass
        rubric_points_earned = q_earned if q_earned > 0.0 else None
        rubric_points_max = q_max if q_max > 0.0 else None
        if rubric_points_earned is None or rubric_points_max is None:
            # Fallback to report-level aggregates when per-question totals are unavailable.
            try:
                if report.get("rubric_points_earned") is not None and rubric_points_earned is None:
                    rubric_points_earned = float(report.get("rubric_points_earned"))
                if report.get("max_points") is not None and rubric_points_max is None:
                    rubric_points_max = float(report.get("max_points"))
            except (TypeError, ValueError):
                pass
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
                for qc in (qg.get("criteria") or []):
                    if not isinstance(qc, dict):
                        continue
                    try:
                        d_max += float(qc.get("max_points") or 0.0)
                    except (TypeError, ValueError):
                        pass
            rubric_points_max = d_max
        return jsonify(
            {
                "id": sub.id,
                "title": sub.title,
                "status": sub.status,
                "final_score": final_score,
                "max_points": round(float(rubric_points_max), 4) if rubric_points_max is not None else None,
                "rubric_points_earned": round(float(rubric_points_earned), 4) if rubric_points_earned is not None else None,
                "grading_instructions": sub.grading_instructions,
                "grading_dispatch_at": _iso_utc(sub.grading_dispatch_at),
                "created_at": _iso_utc(sub.created_at),
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
        )
    finally:
        db.close()


@bp.get("/api/standalone/submissions/<int:submission_id>/report")
def standalone_get_report(submission_id: int):
    """Return a presigned GET URL for the grading report JSON in MinIO."""
    user = _standalone_user()

    cfg = Config()
    db = SessionLocal()
    try:
        sub = db.query(StandaloneSubmission).filter_by(id=submission_id).first()
        if not sub or sub.status == "deleted":
            return jsonify({"error": "not found"}), 404
        if not _can_view_standalone(sub, user):
            return jsonify({"error": "forbidden"}), 403
        if not sub.grading_report_object_key:
            return jsonify({"error": "report not available yet"}), 404
        url = get_presigned_url(
            cfg,
            sub.grading_report_object_key,
            method="GET",
            expires=3600,
            bucket=cfg.MINIO_GRADING_REPORTS_BUCKET,
        )
        return jsonify(
            {"download_url": url, "object_key": sub.grading_report_object_key}
        )
    finally:
        db.close()


@bp.delete("/api/standalone/submissions/<int:submission_id>")
def standalone_delete(submission_id: int):
    user = _standalone_user()

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
