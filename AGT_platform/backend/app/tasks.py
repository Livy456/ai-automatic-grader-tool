import json
import logging
from datetime import datetime
from types import SimpleNamespace

from celery import Celery
from sqlalchemy.orm import selectinload

from .config import Config
from .extensions import SessionLocal, engine, init_db
from .grading.multimodal.course_multimodal_runner import (
    run_db_submission_multimodal_pipeline,
    run_standalone_multimodal_pipeline,
)
from .grading.multimodal.pipeline_runner import (
    build_submission_artifacts,
    excerpt_attachment_bytes,
)
from .models import (
    AIScore,
    Assignment,
    AssignmentAttachment,
    StandaloneAIScore,
    StandaloneArtifact,
    StandaloneSubmission,
    Submission,
)
from .storage import get_object_bytes, minio_client

celery_app = Celery(__name__)

_log = logging.getLogger(__name__)

_cfg = Config()
celery_app.conf.broker_url = _cfg.REDIS_URL
celery_app.conf.result_backend = _cfg.REDIS_URL
celery_app.conf.task_routes = {
    "grade_submission": {"queue": "gpu"},
    "grade_standalone_submission": {"queue": "gpu"},
}
# Bound prefetch so one worker does not hoard many large grading tasks in memory.
celery_app.conf.worker_prefetch_multiplier = max(1, _cfg.CELERY_WORKER_PREFETCH)
celery_app.conf.task_acks_late = True
celery_app.conf.task_reject_on_worker_lost = True


def init_celery(app):
    celery_app.conf.broker_url = app.config["REDIS_URL"]
    celery_app.conf.result_backend = app.config["REDIS_URL"]


def _evidence_for_db(ev):
    if ev is None:
        return {}
    if isinstance(ev, dict):
        return ev
    return {"text": str(ev)}


def _rationale_for_db(c: dict) -> str:
    return (c.get("rationale") or c.get("justification") or "").strip() or ""


def _ensure_db():
    if engine is None:
        init_db(Config().DATABASE_URL)


@celery_app.task(name="grade_submission", bind=True, max_retries=2)
def grade_submission(self, submission_id: int):
    """
    GPU-queue grading. Idempotent: only one successful transition from queued → grading
    per submission; duplicate deliveries no-op after work started.
    """
    _ensure_db()
    cfg = Config()
    db = SessionLocal()
    sub = None
    try:
        sub = (
            db.query(Submission)
            .options(selectinload(Submission.artifacts))
            .filter_by(id=submission_id)
            .with_for_update()
            .first()
        )
        if not sub:
            return
        # Another worker already owns or finished
        if sub.status in ("grading", "graded", "needs_review"):
            return
        if sub.status == "error":
            return
        if sub.status == "deleted":
            return
        if sub.status != "queued":
            # e.g. still uploading — do not grade
            return

        sub.status = "grading"
        sub.updated_at = datetime.utcnow()
        db.commit()

        assignment = db.query(Assignment).get(sub.assignment_id)
        if not assignment:
            sub.status = "error"
            db.commit()
            return

        def _filename_hint_from_object_key(key: str) -> str:
            part = (key or "").rsplit("/", 1)[-1]
            if "_" in part:
                return part.split("_", 1)[-1]
            return part

        submission_parts: list[tuple[str, bytes]] = []
        rubric_ex = ""
        answer_ex = ""
        for art in sub.artifacts:
            data = get_object_bytes(cfg, art.object_key)
            fn_hint = _filename_hint_from_object_key(art.object_key)
            if art.kind in ("rubric", "answer_key"):
                ex = excerpt_attachment_bytes(fn_hint, data)
                if art.kind == "rubric":
                    rubric_ex = (rubric_ex + "\n\n" + ex).strip() if rubric_ex else ex
                else:
                    answer_ex = (answer_ex + "\n\n" + ex).strip() if answer_ex else ex
                continue
            submission_parts.append((fn_hint or art.kind, data))

        is_public_autograder = assignment.course_id is None
        if not is_public_autograder:
            for att in (
                db.query(AssignmentAttachment)
                .filter_by(assignment_id=assignment.id)
                .order_by(AssignmentAttachment.created_at.desc())
                .all()
            ):
                data = get_object_bytes(cfg, att.object_key)
                ex = excerpt_attachment_bytes(att.filename, data)
                if att.kind == "rubric":
                    rubric_ex = (rubric_ex + "\n\n" + ex).strip() if rubric_ex else ex
                elif att.kind == "answer_key":
                    answer_ex = (answer_ex + "\n\n" + ex).strip() if answer_ex else ex

        artifacts = build_submission_artifacts(submission_parts)
        if is_public_autograder:
            merged_rubric_parts = []
            grt = getattr(assignment, "grader_rubric_text", None)
            if grt and str(grt).strip():
                merged_rubric_parts.append(str(grt).strip())
            if rubric_ex:
                merged_rubric_parts.append("Rubric (from uploaded file):\n" + rubric_ex.strip())
            merged_rubric = "\n\n".join(merged_rubric_parts) if merged_rubric_parts else None

            merged_ak_parts = []
            gak = getattr(assignment, "grader_answer_key_text", None)
            if gak and str(gak).strip():
                merged_ak_parts.append(str(gak).strip())
            if answer_ex:
                merged_ak_parts.append("Answer key (from uploaded file):\n" + answer_ex.strip())
            merged_ak = "\n\n".join(merged_ak_parts) if merged_ak_parts else None

            instr = getattr(assignment, "grader_instructions", None)
            desc_parts = []
            base = (assignment.description or assignment.title or "").strip()
            if base:
                desc_parts.append(base)
            if instr and str(instr).strip():
                desc_parts.append("Instructor grading instructions:\n" + str(instr).strip())
            merged_desc = "\n\n".join(desc_parts) if desc_parts else (assignment.title or "Submission")

            assign_for_prompt = SimpleNamespace(
                modality=assignment.modality,
                rubric=assignment.rubric,
                title=assignment.title,
                description=merged_desc,
            )
        else:
            merged_rubric_parts = []
            if rubric_ex:
                merged_rubric_parts.append(
                    "Rubric (from uploaded file):\n" + rubric_ex.strip()
                )
            merged_rubric = "\n\n".join(merged_rubric_parts) if merged_rubric_parts else None
            merged_ak_parts = []
            if answer_ex:
                merged_ak_parts.append(
                    "Answer key (from uploaded file):\n" + answer_ex.strip()
                )
            merged_ak = "\n\n".join(merged_ak_parts) if merged_ak_parts else None
            assign_for_prompt = assignment

        result = run_db_submission_multimodal_pipeline(
            cfg,
            assign_for_prompt,
            artifacts,
            submission_id=sub.id,
            assignment_id=assignment.id,
            student_id=sub.student_id,
            rubric_text=merged_rubric,
            answer_key_text=merged_ak,
        )
        _default_ml = f"openai:{(cfg.OPENAI_MODEL or 'gpt-4o-mini').strip()}"
        model_used = (result.pop("_model_used", None) or _default_ml)[:200]
        models_used = result.pop("_models_used", [model_used])
        result.pop("_used_openai_arbitration", None)
        result.pop("_pipeline_meta", None)
        entropy_meta = result.pop("_entropy_meta", None)

        criteria = result.get("criteria", [])
        overall = result.get("overall", {})
        flags = set(result.get("flags", []))
        mm_review = str(result.get("_assignment_review_status", "") or "").lower()
        multimodal_needs_review = mm_review in ("caution", "flagged", "escalation")

        # Idempotent persistence: delete prior AI scores for this submission then insert
        db.query(AIScore).filter_by(submission_id=sub.id).delete()
        for c in criteria:
            db.add(
                AIScore(
                    submission_id=sub.id,
                    criterion=c["name"],
                    score=c["score"],
                    confidence=c.get("confidence", 0.5),
                    rationale=_rationale_for_db(c),
                    evidence=_evidence_for_db(c.get("evidence")),
                    model=model_used,
                )
            )

        sub = db.query(Submission).get(submission_id)
        sub.final_score = overall.get("score", 0)
        sub.final_feedback = overall.get("summary", "")
        low_conf = any(float(c.get("confidence", 0)) < 0.70 for c in criteria)
        ent_conf = overall.get("confidence_from_entropy")
        try:
            if ent_conf is not None and float(ent_conf) < 0.5:
                low_conf = True
        except (TypeError, ValueError):
            pass
        if low_conf or "needs_review" in flags or multimodal_needs_review:
            sub.status = "needs_review"
        else:
            sub.status = "graded"
        sub.updated_at = datetime.utcnow()
        db.commit()

        try:
            sub_ref = db.query(Submission).get(submission_id)
            if sub_ref and sub_ref.status in ("graded", "needs_review"):
                report_key = f"grading-reports/course/{submission_id}/{submission_id}_report.json"
                grading_report = {
                    "submission_id": submission_id,
                    "title": getattr(assignment, "title", None),
                    "status": sub_ref.status,
                    "final_score": float(sub_ref.final_score)
                    if sub_ref.final_score is not None
                    else None,
                    "final_feedback": sub_ref.final_feedback,
                    "model_used": model_used,
                    "models_used": models_used,
                    "graded_at": sub_ref.updated_at.isoformat()
                    if sub_ref.updated_at
                    else None,
                    "criteria": [
                        {
                            "criterion": c.get("name", ""),
                            "score": c.get("score", 0),
                            "confidence": c.get("confidence", 0.5),
                            "rationale": _rationale_for_db(c),
                        }
                        for c in criteria
                    ],
                }
                if entropy_meta is not None:
                    grading_report["entropy_meta"] = entropy_meta
                minio_client(cfg).put_object(
                    Bucket=cfg.MINIO_GRADING_REPORTS_BUCKET,
                    Key=report_key,
                    Body=json.dumps(grading_report, indent=2).encode("utf-8"),
                    ContentType="application/json",
                )
                sub_ref.grading_report_object_key = report_key
                db.commit()
        except Exception as e:
            _log.error(
                "Failed to upload grading report for submission %s: %s",
                submission_id,
                e,
                exc_info=True,
            )
    except Exception:
        db.rollback()
        if sub:
            s2 = db.query(Submission).get(submission_id)
            if s2 and s2.status == "grading":
                s2.status = "error"
                s2.updated_at = datetime.utcnow()
                db.commit()
        raise
    finally:
        db.close()


@celery_app.task(name="grade_standalone_submission", bind=True, max_retries=2)
def grade_standalone_submission(self, submission_id: int):
    _ensure_db()
    cfg = Config()
    db = SessionLocal()
    sub = None
    try:
        sub = (
            db.query(StandaloneSubmission)
            .filter_by(id=submission_id)
            .with_for_update()
            .first()
        )
        if not sub:
            return
        if sub.status in ("grading", "graded", "needs_review"):
            return
        if sub.status == "error":
            return
        if sub.status != "queued":
            return

        sub.status = "grading"
        sub.updated_at = datetime.utcnow()
        db.commit()

        arts = db.query(StandaloneArtifact).filter_by(submission_id=sub.id).all()
        submission_parts: list[tuple[str, bytes]] = []
        rubric_ex = ""
        answer_ex = ""
        for art in arts:
            data = get_object_bytes(cfg, art.object_key)
            if art.kind in ("rubric", "answer_key"):
                ex = excerpt_attachment_bytes(art.filename, data)
                if art.kind == "rubric":
                    rubric_ex = (rubric_ex + "\n\n" + ex).strip() if rubric_ex else ex
                else:
                    answer_ex = (answer_ex + "\n\n" + ex).strip() if answer_ex else ex
                continue
            submission_parts.append((art.filename or art.kind, data))
        main = build_submission_artifacts(submission_parts)

        result = run_standalone_multimodal_pipeline(
            cfg,
            main,
            sub.id,
            sub.title or "Untitled",
            sub.rubric_text,
            sub.answer_key_text,
            rubric_ex or None,
            answer_ex or None,
            getattr(sub, "grading_instructions", None),
        )
        _default_ml = f"openai:{(cfg.OPENAI_MODEL or 'gpt-4o-mini').strip()}"
        model_used = (result.pop("_model_used", None) or _default_ml)[:200]
        models_used = result.pop("_models_used", [model_used])
        result.pop("_used_openai_arbitration", None)
        result.pop("_pipeline_meta", None)
        entropy_meta = result.pop("_entropy_meta", None)

        criteria = result.get("criteria", [])
        overall = result.get("overall", {})
        flags = set(result.get("flags", []))
        mm_review = str(result.get("_assignment_review_status", "") or "").lower()
        multimodal_needs_review = mm_review in ("caution", "flagged", "escalation")

        db.query(StandaloneAIScore).filter_by(submission_id=sub.id).delete()
        for c in criteria:
            db.add(
                StandaloneAIScore(
                    submission_id=sub.id,
                    criterion=c["name"],
                    score=c["score"],
                    confidence=c.get("confidence", 0.5),
                    rationale=_rationale_for_db(c),
                    evidence=_evidence_for_db(c.get("evidence")),
                    model=model_used,
                )
            )

        sub = db.query(StandaloneSubmission).get(submission_id)
        sub.final_score = overall.get("score", 0)
        sub.final_feedback = overall.get("summary", "")
        low_conf = any(float(c.get("confidence", 0)) < 0.70 for c in criteria)
        ent_conf = overall.get("confidence_from_entropy")
        try:
            if ent_conf is not None and float(ent_conf) < 0.5:
                low_conf = True
        except (TypeError, ValueError):
            pass
        if low_conf or "needs_review" in flags or multimodal_needs_review:
            sub.status = "needs_review"
        else:
            sub.status = "graded"
        sub.updated_at = datetime.utcnow()
        db.commit()

        try:
            sub_ref = db.query(StandaloneSubmission).get(submission_id)
            if sub_ref and sub_ref.status in ("graded", "needs_review"):
                report_key = (
                    f"grading-reports/standalone/{submission_id}/{submission_id}_report.json"
                )
                grading_report = {
                    "submission_id": submission_id,
                    "title": sub_ref.title,
                    "status": sub_ref.status,
                    "final_score": float(sub_ref.final_score)
                    if sub_ref.final_score is not None
                    else None,
                    "final_feedback": sub_ref.final_feedback,
                    "model_used": model_used,
                    "models_used": models_used,
                    "graded_at": sub_ref.updated_at.isoformat()
                    if sub_ref.updated_at
                    else None,
                    "criteria": [
                        {
                            "criterion": c.get("name", ""),
                            "score": c.get("score", 0),
                            "confidence": c.get("confidence", 0.5),
                            "rationale": _rationale_for_db(c),
                        }
                        for c in criteria
                    ],
                }
                if entropy_meta is not None:
                    grading_report["entropy_meta"] = entropy_meta
                minio_client(cfg).put_object(
                    Bucket=cfg.MINIO_GRADING_REPORTS_BUCKET,
                    Key=report_key,
                    Body=json.dumps(grading_report, indent=2).encode("utf-8"),
                    ContentType="application/json",
                )
                sub_ref.grading_report_object_key = report_key
                db.commit()
        except Exception as e:
            _log.error(
                "Failed to upload grading report for standalone submission %s: %s",
                submission_id,
                e,
                exc_info=True,
            )
    except Exception:
        db.rollback()
        if sub:
            s2 = db.query(StandaloneSubmission).get(submission_id)
            if s2 and s2.status == "grading":
                s2.status = "error"
                s2.updated_at = datetime.utcnow()
                db.commit()
        raise
    finally:
        db.close()
