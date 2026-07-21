from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session
from werkzeug.utils import secure_filename

from .config import Config
from .deps import get_db
from .llm.llm_router import build_multimodal_grading_clients
from .database.models import AssignmentUpload
from .database.storage import upload_from_fastapi_file
from .tasks import grade_assignment_upload

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


# -----------------------------
# Helpers
# -----------------------------

def _now() -> datetime:
    """ 
    This function returns the current date and time.
    """
    return datetime.now()

def _allowed_file(filename: str) -> bool:
    """
    This function checks if the file extension is allowed for assignment uploads.
    """
    allowed = {
        # Python Notebooks, written PDFs, Colab / script Python
        ".ipynb",
        ".pdf",
        ".py",
        # Audio (e.g. oral / journal voice submissions)
        ".mp3",
        ".wav",
        ".m4a",
       
       # Spreadsheet or Word documents (Excel, Word)
        ".xlsx",
        ".docx",
        ".csv",
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

        # JSON files are used for rubric extraction
        ".json",
    }
    ext = os.path.splitext(filename.lower())[1]
    return ext in allowed


def _save_upload_to_object_store(upload_file: UploadFile) -> Tuple[str, str]:
    """
    Stream file to MinIO object storage. Returns (assignment_upload_uuid, object_key).
    storage_uri in DB holds the object key.
    """
    filename = secure_filename(upload_file.filename or "upload.bin")
    if not _allowed_file(filename):
        raise ValueError(f"File type not allowed: {filename}")

    assignment_id = str(uuid.uuid4())
    cfg = Config()
    key = f"ingest/assignment-uploads/{assignment_id}/{filename}"
    upload_from_fastapi_file(cfg, upload_file, key)
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

@router.get("/assignments")
def list_assignments(db: Session = Depends(get_db)):
    """
    GET /api/assignments -> list most recent assignments.
    """
    items = (
        db.query(AssignmentUpload)
        .order_by(AssignmentUpload.created_at.desc())
        .limit(100)
        .all()
    )
    return [_assignment_to_dict(a) for a in items]


@router.post("/assignments", status_code=201)
def create_assignment(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    POST /api/assignments
    - Accepts multipart/form-data with "file"
    - Stores file in MinIO object storage
    - Inserts Assignment row into Postgres
    """
    if not file or not file.filename:
        raise HTTPException(400, "Empty file upload")

    try:
        assignment_id, storage_uri = _save_upload_to_object_store(file)
    except ValueError as e:
        raise HTTPException(400, str(e))

    a = AssignmentUpload(
        id=assignment_id,
        filename=secure_filename(file.filename),
        storage_uri=storage_uri,
        status="uploaded",
        suggested_grade=None,
        feedback=None,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(a)
    db.commit()
    return {"id": a.id}


@router.get("/assignments/{assignment_id}")
def get_assignment(assignment_id: str, db: Session = Depends(get_db)):
    """
    GET /api/assignments/<id> -> fetch status/result
    """
    a: Optional[AssignmentUpload] = (
        db.query(AssignmentUpload).filter(AssignmentUpload.id == assignment_id).first()
    )
    if not a:
        raise HTTPException(404, "Assignment not found")
    return _assignment_to_dict(a)


@router.post("/assignments/{assignment_id}/grade")
def grade_assignment(assignment_id: str, response: Response, db: Session = Depends(get_db)):
    """
    POST /api/assignments/<id>/grade -> enqueue multimodal grading on the Celery queue
    and return immediately (does not block this HTTP worker for the full pipeline duration).

    Poll GET /api/assignments/<id> for ``status`` (queued -> grading -> graded|error) and the
    ``suggested_grade`` / ``feedback`` result once ``status == "graded"``. Grading itself runs
    in :func:`app.tasks.grade_assignment_upload`, using the same :func:`run_multimodal_grading`
    path as local integration tests and the course/standalone grading tasks.
    """
    a: Optional[AssignmentUpload] = (
        db.query(AssignmentUpload).filter(AssignmentUpload.id == assignment_id).first()
    )
    if not a:
        raise HTTPException(404, "Assignment not found")

    if a.status in ("queued", "grading", "graded"):
        return {"ok": True, "status": a.status}

    cfg = Config()
    if not build_multimodal_grading_clients(cfg):
        raise HTTPException(
            503,
            {
                "error": "Multimodal grading unavailable",
                "detail": "Set OPENAI_API_KEY and OPENAI_MULTIMODAL_GRADING_MODEL.",
            },
        )

    a.status = "queued"
    a.feedback = None
    a.updated_at = _now()
    db.commit()

    grade_assignment_upload.delay(str(a.id))

    response.status_code = 202
    return {"ok": True, "status": a.status}