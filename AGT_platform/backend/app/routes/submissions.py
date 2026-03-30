from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename
from app.rbac import require_auth
from app.extensions import SessionLocal
from app.models import Submission, SubmissionArtifact
from app.config import Config
from app.storage import upload_from_werkzeug_file
from app.tasks import grade_submission
from app.audit import log_event
import uuid

bp = Blueprint("submissions", __name__)

@bp.post("/api/submissions")
@require_auth
def submit():
    user = request.user
    assignment_id = int(request.form["assignment_id"])
    db = SessionLocal()
    cfg = Config()

    try:
        sub = Submission(assignment_id=assignment_id, student_id=user["id"], status="queued")
        db.add(sub); db.commit(); db.refresh(sub)

        for f in request.files.getlist("files"):
            filename = secure_filename(f.filename or "")
            if not filename:
                continue
            kind = filename.split(".")[-1].lower()
            key = (
                f"assignments/by-id/{assignment_id}/submissions/{sub.id}/"
                f"{uuid.uuid4().hex}_{filename}"
            )
            upload_from_werkzeug_file(cfg, f, key)
            db.add(SubmissionArtifact(submission_id=sub.id, kind=kind, s3_key=key))

        db.commit()

        log_event(user["id"], "CREATE_SUBMISSION", "Submission", sub.id, {"assignment_id": assignment_id})
        grade_submission.delay(sub.id)

        return jsonify({"submission_id": sub.id, "status": sub.status})
    finally:
        db.close()

@bp.get("/api/submissions/<int:submission_id>")
@require_auth
def get_submission(submission_id: int):
    from ..models import AIScore
    user = request.user
    db = SessionLocal()
    try:
        sub = db.query(Submission).get(submission_id)
        if not sub:
            return jsonify({"error":"not found"}), 404

        # FERPA-style authorization: student can view only their own submission
        if user["role"] == "student" and sub.student_id != user["id"]:
            return jsonify({"error":"forbidden"}), 403

        scores = db.query(AIScore).filter_by(submission_id=sub.id).all()
        log_event(user["id"], "VIEW_SUBMISSION", "Submission", sub.id, {})
        return jsonify({
            "id": sub.id,
            "status": sub.status,
            "final_score": float(sub.final_score) if sub.final_score is not None else None,
            "final_feedback": sub.final_feedback,
            "ai_scores": [{
                "criterion": s.criterion,
                "score": float(s.score),
                "confidence": float(s.confidence),
                "rationale": s.rationale
            } for s in scores]
        })
    finally:
        db.close()
