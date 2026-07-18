"""
Teacher/admin uploads: rubric and answer-key files for a course Assignment (DB id).
Objects land in MinIO under assignments/by-id/<id>/materials/<kind>/...
"""
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session
from werkzeug.utils import secure_filename

from app.config import Config
from app.deps import get_db, require_role
from app.models import Assignment, AssignmentAttachment
from app.storage import upload_from_fastapi_file

router = APIRouter()


@router.post("/api/course-assignments/{assignment_id}/files", status_code=201)
def upload_assignment_file(
    assignment_id: int,
    kind: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("teacher", "admin")),
):
    """
    multipart/form-data:
      - file (required)
      - kind: "rubric" | "answer_key" (required)
    """
    kind = (kind or "").strip().lower()
    if kind not in ("rubric", "answer_key"):
        raise HTTPException(400, "kind must be rubric or answer_key")

    if not file or not file.filename:
        raise HTTPException(400, "empty file")

    filename = secure_filename(file.filename)
    if not filename:
        raise HTTPException(400, "invalid filename")

    cfg = Config()
    a = db.query(Assignment).filter(Assignment.id == assignment_id).one_or_none()
    if not a:
        raise HTTPException(404, "assignment not found")

    safe_kind = "answer-keys" if kind == "answer_key" else "rubrics"
    key = (
        f"assignments/by-id/{assignment_id}/materials/{safe_kind}/"
        f"{uuid.uuid4().hex}_{filename}"
    )
    upload_from_fastapi_file(cfg, file, key)

    row = AssignmentAttachment(
        assignment_id=assignment_id,
        kind=kind,
        object_key=key,
        filename=filename,
        uploaded_by_id=user.get("id"),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "assignment_id": assignment_id,
        "kind": row.kind,
        "filename": row.filename,
        "object_key": row.object_key,
    }


@router.get("/api/course-assignments/{assignment_id}/files")
def list_assignment_files(
    assignment_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_role("teacher", "admin", "student")),
):
    a = db.query(Assignment).filter(Assignment.id == assignment_id).one_or_none()
    if not a:
        raise HTTPException(404, "assignment not found")

    q = db.query(AssignmentAttachment).filter_by(assignment_id=assignment_id)
    if user.get("role") == "student":
        q = q.filter(AssignmentAttachment.kind == "rubric")

    rows = q.order_by(AssignmentAttachment.created_at.desc()).all()
    return [
        {
            "id": r.id,
            "kind": r.kind,
            "filename": r.filename,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
