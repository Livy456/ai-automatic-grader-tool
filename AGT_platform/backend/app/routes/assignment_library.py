"""
Assignment creation ("Assignment Library"): teacher/admin uploads a blank assignment
template, an answer key, and a rubric to create a reusable, course-independent
``Assignment`` row (``course_id`` is ``NULL``), plus one ``AssignmentAttachment`` row per
uploaded file.

Mirrors the standalone autograder's upload-context flow (see ``app/routes/standalone.py``):
presigned MinIO PUT URLs are handed back from ``start``, the browser PUTs file bytes directly
to object storage, then ``finalize`` verifies every object landed and marks the assignment
ready. Unlike the standalone autograder, nothing is queued for grading here — this endpoint
only persists assignment metadata + context files for later use.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, request
from werkzeug.utils import secure_filename

from app.audit import log_event
from app.config import Config
from app.extensions import SessionLocal
from app.models import Assignment, AssignmentAttachment
from app.access import require_role
from app.storage import get_object_bytes, object_exists, presigned_put_url

bp = Blueprint("assignment_library", __name__)

_MODALITIES = frozenset({"code", "written", "notebook", "video", "image"})
_ATTACHMENT_KINDS = ("blank_assignment", "answer_key", "rubric")
_SAFE_KIND_DIR = {
    "blank_assignment": "blank-templates",
    "answer_key": "answer-keys",
    "rubric": "rubrics",
}
_MAX_FILES = 10
_MAX_TITLE_LEN = 255


def _normalize_rubric(raw):
    """Same shape as ``routes/courses.py``: ``[{"criterion": str, "max_score": float}, ...]``."""
    if not isinstance(raw, list):
        return None
    out = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        crit = item.get("criterion")
        mx = item.get("max_score")
        if not isinstance(crit, str) or not crit.strip():
            return None
        try:
            mx_num = float(mx)
        except (TypeError, ValueError):
            return None
        out.append({"criterion": crit.strip(), "max_score": mx_num})
    return out


def _serialize_library_assignment(a: Assignment) -> dict:
    return {
        "id": a.id,
        "title": a.title,
        "description": a.description or "",
        "modality": a.modality,
        "rubric": a.rubric if a.rubric is not None else [],
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


@bp.post("/api/assignment-library/start")
@require_role("teacher", "admin")
def start_assignment_library_entry():
    """Create the ``Assignment`` (+ ``AssignmentAttachment`` rows) and return presigned PUT URLs."""
    user = request.user
    body = request.get_json(silent=True) or {}

    title = (body.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title is required"}), 400
    if len(title) > _MAX_TITLE_LEN:
        return jsonify({"error": "title too long"}), 400

    description = body.get("description")
    description = description.strip() if isinstance(description, str) else ""

    modality = (body.get("modality") or "written").strip().lower()
    if modality not in _MODALITIES:
        return jsonify({"error": "invalid modality"}), 400

    rubric_text = (body.get("rubric_text") or "").strip() or None
    answer_key_text = (body.get("answer_key_text") or "").strip() or None
    grading_instructions = (body.get("grading_instructions") or "").strip() or None

    files = body.get("files")
    if not files or not isinstance(files, list):
        return jsonify({"error": "files[] required"}), 400
    if len(files) > _MAX_FILES:
        return jsonify({"error": f"at most {_MAX_FILES} files"}), 400

    cfg = Config()
    db = SessionLocal()
    try:
        a = Assignment(
            course_id=None,
            title=title,
            description=description,
            modality=modality,
            rubric=[],
            created_at=datetime.utcnow(),
            grader_rubric_text=rubric_text,
            grader_answer_key_text=answer_key_text,
            grader_instructions=grading_instructions,
        )
        db.add(a)
        db.flush()

        uploads_out = []
        for spec in files:
            raw_kind = (spec.get("artifact_kind") or spec.get("kind") or "").strip().lower()
            if raw_kind not in _ATTACHMENT_KINDS:
                db.rollback()
                return (
                    jsonify(
                        {
                            "error": (
                                "each file must set artifact_kind to one of: "
                                + ", ".join(_ATTACHMENT_KINDS)
                            )
                        }
                    ),
                    400,
                )
            raw_name = (spec.get("filename") or "").strip()
            filename = secure_filename(raw_name)
            if not filename:
                continue
            content_type = (spec.get("content_type") or "application/octet-stream").strip()
            key = (
                f"assignments/by-id/{a.id}/materials/{_SAFE_KIND_DIR[raw_kind]}/"
                f"{uuid.uuid4().hex}_{filename}"
            )
            row = AssignmentAttachment(
                assignment_id=a.id,
                kind=raw_kind,
                object_key=key,
                filename=filename,
                uploaded_by_id=user.get("id"),
            )
            db.add(row)
            db.flush()
            url = presigned_put_url(cfg, key, content_type)
            uploads_out.append(
                {
                    "artifact_id": row.id,
                    "object_key": key,
                    "upload_url": url,
                    "content_type": content_type,
                    "kind": raw_kind,
                }
            )

        if not uploads_out:
            db.rollback()
            return jsonify({"error": "no valid files"}), 400

        db.commit()
        db.refresh(a)
        log_event(
            user["id"],
            "CREATE_ASSIGNMENT_LIBRARY_ENTRY",
            "Assignment",
            a.id,
            {"n_files": len(uploads_out), "title": a.title},
        )
        return jsonify(
            {
                "assignment_id": a.id,
                "status": "uploading",
                "uploads": uploads_out,
            }
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@bp.post("/api/assignment-library/<int:assignment_id>/finalize")
@require_role("teacher", "admin")
def finalize_assignment_library_entry(assignment_id: int):
    """Verify every uploaded object landed in MinIO; best-effort parse a JSON rubric file."""
    user = request.user
    cfg = Config()
    db = SessionLocal()
    try:
        a = (
            db.query(Assignment)
            .filter_by(id=assignment_id, course_id=None)
            .with_for_update()
            .one_or_none()
        )
        if not a:
            return jsonify({"error": "not found"}), 404

        attachments = (
            db.query(AssignmentAttachment).filter_by(assignment_id=assignment_id).all()
        )
        kinds = {att.kind for att in attachments}
        missing = [k for k in _ATTACHMENT_KINDS if k not in kinds]
        if missing:
            return (
                jsonify(
                    {
                        "error": "missing required context",
                        "detail": (
                            "Assignment creation requires a blank assignment template, an "
                            "answer key, and a rubric before it can be finalized."
                        ),
                        "missing": missing,
                    }
                ),
                400,
            )

        for att in attachments:
            if not object_exists(cfg, att.object_key):
                return jsonify({"error": f"missing object: {att.object_key}"}), 400

        rubric_att = next((att for att in attachments if att.kind == "rubric"), None)
        if rubric_att and rubric_att.filename.lower().endswith(".json"):
            try:
                raw = get_object_bytes(cfg, rubric_att.object_key)
                parsed = json.loads(raw.decode("utf-8"))
                normalized = _normalize_rubric(parsed)
                if normalized:
                    a.rubric = normalized
            except Exception:
                pass  # Keep a.rubric == [] and rely on the stored file if it doesn't parse.

        db.commit()
        log_event(
            user["id"],
            "FINALIZE_ASSIGNMENT_LIBRARY_ENTRY",
            "Assignment",
            a.id,
            {},
        )
        return jsonify(_serialize_library_assignment(a) | {"status": "created"})
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@bp.get("/api/assignment-library")
@require_role("teacher", "admin")
def list_assignment_library_entries():
    """Most recent course-independent assignments (created via this creation flow)."""
    db = SessionLocal()
    try:
        items = (
            db.query(Assignment)
            .filter(Assignment.course_id.is_(None))
            .order_by(Assignment.created_at.desc())
            .limit(50)
            .all()
        )
        return jsonify([_serialize_library_assignment(a) for a in items])
    finally:
        db.close()
