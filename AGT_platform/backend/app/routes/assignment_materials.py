"""
Teacher/admin uploads: rubric and answer-key files for a course Assignment (DB id).
Objects land in S3 under assignments/by-id/<id>/materials/<kind>/...
"""
import uuid

from flask import Blueprint, jsonify, request
from werkzeug.utils import secure_filename

from app.config import Config
from app.extensions import SessionLocal
from app.models import Assignment, AssignmentAttachment
from app.rbac import require_role
from app.storage import upload_from_werkzeug_file

bp = Blueprint("assignment_materials", __name__)


@bp.post("/api/course-assignments/<int:assignment_id>/files")
@require_role("teacher", "admin")
def upload_assignment_file(assignment_id: int):
    """
    multipart/form-data:
      - file (required)
      - kind: "rubric" | "answer_key" (required)
    """
    user = request.user
    kind = (request.form.get("kind") or "").strip().lower()
    if kind not in ("rubric", "answer_key"):
        return jsonify({"error": "kind must be rubric or answer_key"}), 400

    if "file" not in request.files:
        return jsonify({"error": "missing file"}), 400
    f = request.files["file"]
    if not f or not f.filename:
        return jsonify({"error": "empty file"}), 400

    filename = secure_filename(f.filename)
    if not filename:
        return jsonify({"error": "invalid filename"}), 400

    db = SessionLocal()
    cfg = Config()
    try:
        a = db.query(Assignment).filter(Assignment.id == assignment_id).one_or_none()
        if not a:
            return jsonify({"error": "assignment not found"}), 404

        safe_kind = "answer-keys" if kind == "answer_key" else "rubrics"
        key = (
            f"assignments/by-id/{assignment_id}/materials/{safe_kind}/"
            f"{uuid.uuid4().hex}_{filename}"
        )
        upload_from_werkzeug_file(cfg, f, key)

        row = AssignmentAttachment(
            assignment_id=assignment_id,
            kind=kind,
            s3_key=key,
            filename=filename,
            uploaded_by_id=user.get("id"),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return jsonify(
            {
                "id": row.id,
                "assignment_id": assignment_id,
                "kind": row.kind,
                "filename": row.filename,
                "s3_key": row.s3_key,
            }
        ), 201
    finally:
        db.close()


@bp.get("/api/course-assignments/<int:assignment_id>/files")
@require_role("teacher", "admin", "student")
def list_assignment_files(assignment_id: int):
    user = request.user
    db = SessionLocal()
    try:
        a = db.query(Assignment).filter(Assignment.id == assignment_id).one_or_none()
        if not a:
            return jsonify({"error": "assignment not found"}), 404

        q = db.query(AssignmentAttachment).filter_by(assignment_id=assignment_id)
        if user.get("role") == "student":
            q = q.filter(AssignmentAttachment.kind == "rubric")

        rows = q.order_by(AssignmentAttachment.created_at.desc()).all()
        return jsonify(
            [
                {
                    "id": r.id,
                    "kind": r.kind,
                    "filename": r.filename,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]
        )
    finally:
        db.close()
