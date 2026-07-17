from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, Tuple

from flask import Blueprint, jsonify, request
from werkzeug.utils import secure_filename

from sqlalchemy.orm import Session

from .config import Config
from .extensions import SessionLocal
from .grading.llm_router import build_multimodal_grading_clients
from .models import AssignmentUpload
from .storage import upload_from_werkzeug_file
from .tasks import grade_assignment_upload

_log = logging.getLogger(__name__)


bp = Blueprint("assignments", __name__, url_prefix="/api")


# -----------------------------
# Helpers
# -----------------------------

def _db() -> Session:
    """
    Create a per-request SQLAlchemy session.
    We keep it simple for now and always close it in route handlers.
    """
    if SessionLocal is None:
        raise RuntimeError("Database not initialized. Did you call init_db() in create_app()?")

    return SessionLocal()


def _now() -> datetime:
    return datetime.now()


def _allowed_file(filename: str) -> bool:
    """
    Allowed upload extensions for assignment uploads.

    Includes notebooks / documents / code (``.ipynb``, ``.pdf``, ``.py``), audio
    (``.mp3``, ``.wav``, ``.m4a``), spreadsheets and Word (``.xlsx``, ``.docx``),
    plus common image, video, and plain-text types used in courses.
    """
    allowed = {
        # Notebooks, written PDFs, Colab / script Python
        ".ipynb",
        ".pdf",
        ".py",
        # Audio (e.g. oral / journal voice submissions)
        ".mp3",
        ".wav",
        ".m4a",
        # Tabular / Office
        ".xlsx",
        ".docx",
        # Other course artifacts
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".mp4",
        ".mov",
        ".webm",
        ".txt",
        ".md",
        ".csv",
        ".json",
    }
    ext = os.path.splitext(filename.lower())[1]
    return ext in allowed


def _save_upload_to_object_store(file_storage) -> Tuple[str, str]:
    """
    Stream file to MinIO object storage. Returns (assignment_upload_uuid, object_key).
    storage_uri in DB holds the object key.
    """
    filename = secure_filename(file_storage.filename or "upload.bin")
    if not _allowed_file(filename):
        raise ValueError(f"File type not allowed: {filename}")

    assignment_id = str(uuid.uuid4())
    cfg = Config()
    key = f"ingest/assignment-uploads/{assignment_id}/{filename}"
    upload_from_werkzeug_file(cfg, file_storage, key)
    return assignment_id, key


def _assignment_to_dict(a: AssignmentUpload) -> Dict[str, Any]:
    return {
        "id": a.id,
        "filename": a.filename,
        "status": a.status,
        "suggested_grade": a.suggested_grade,
        "feedback": a.feedback,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }


# -----------------------------
# Routes
# -----------------------------

@bp.get("/assignments")
def list_assignments():
    """
    GET /api/assignments -> list most recent assignments.
    """
    db = _db()
    try:
        items = (
            db.query(AssignmentUpload)
            .order_by(AssignmentUpload.created_at.desc())
            .limit(100)
            .all()
        )
        return jsonify([_assignment_to_dict(a) for a in items])
    finally:
        db.close()


@bp.post("/assignments")
def create_assignment():
    """
    POST /api/assignments
    - Accepts multipart/form-data with "file"
    - Stores file in MinIO object storage
    - Inserts Assignment row into Postgres
    """
    if "file" not in request.files:
        return jsonify({"error": "Missing file field 'file' in form-data"}), 400

    f = request.files["file"]
    if not f or not f.filename:
        return jsonify({"error": "Empty file upload"}), 400

    try:
        assignment_id, storage_uri = _save_upload_to_object_store(f)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    db = _db()
    try:
        a = AssignmentUpload(
            id=assignment_id,
            filename=secure_filename(f.filename),
            storage_uri=storage_uri,
            status="uploaded",
            suggested_grade=None,
            feedback=None,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(a)
        db.commit()
        return jsonify({"id": a.id}), 201
    finally:
        db.close()


@bp.get("/assignments/<assignment_id>")
def get_assignment(assignment_id: str):
    """
    GET /api/assignments/<id> -> fetch status/result
    """
    db = _db()
    try:
        a: Optional[AssignmentUpload] = db.query(AssignmentUpload).filter(AssignmentUpload.id == assignment_id).first()
        if not a:
            return jsonify({"error": "Assignment not found"}), 404
        return jsonify(_assignment_to_dict(a))
    finally:
        db.close()


@bp.post("/assignments/<assignment_id>/grade")
def grade_assignment(assignment_id: str):
    """
    POST /api/assignments/<id>/grade -> enqueue multimodal grading on the Celery "gpu" queue
    and return immediately (does not block this HTTP worker for the full pipeline duration).

    Poll GET /api/assignments/<id> for ``status`` (queued -> grading -> graded|error) and the
    ``suggested_grade`` / ``feedback`` result once ``status == "graded"``. Grading itself runs
    in :func:`app.tasks.grade_assignment_upload`, using the same :func:`run_multimodal_grading`
    path as local integration tests and the course/standalone grading tasks.
    """
    db = _db()
    try:
        a: Optional[AssignmentUpload] = db.query(AssignmentUpload).filter(AssignmentUpload.id == assignment_id).first()
        if not a:
            return jsonify({"error": "Assignment not found"}), 404

        if a.status in ("queued", "grading", "graded"):
            return jsonify({"ok": True, "status": a.status})

        cfg = Config()
        if not build_multimodal_grading_clients(cfg):
            return jsonify(
                {
                    "error": "Multimodal grading unavailable",
                    "detail": "Set OPENAI_API_KEY and OPENAI_MULTIMODAL_GRADING_MODEL.",
                }
            ), 503

        a.status = "queued"
        a.feedback = None
        a.updated_at = _now()
        db.commit()

        grade_assignment_upload.delay(str(a.id))

        return jsonify({"ok": True, "status": a.status}), 202
    finally:
        db.close()
