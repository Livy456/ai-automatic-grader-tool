from celery import Celery
from .config import Config
from .extensions import SessionLocal, engine, init_db
from .models import Submission, Assignment, AIScore
from .storage import get_object_bytes
from .grading.pipelines import run_grading_pipeline

celery_app = Celery(__name__)

# Workers load this module without Flask; broker must come from env/Config.
_cfg = Config()
celery_app.conf.broker_url = _cfg.REDIS_URL
celery_app.conf.result_backend = _cfg.REDIS_URL
celery_app.conf.task_routes = {"grade_submission": {"queue": "gpu"}}


def init_celery(app):
    celery_app.conf.broker_url = app.config["REDIS_URL"]
    celery_app.conf.result_backend = app.config["REDIS_URL"]


def _ensure_db():
    """Workers do not run Flask create_app(); bind SQLAlchemy before any DB access."""
    if engine is None:
        init_db(Config().DATABASE_URL)


@celery_app.task(name="grade_submission")
def grade_submission(submission_id: int):
    _ensure_db()
    cfg = Config()

    db = SessionLocal()
    try:
        sub = db.query(Submission).get(submission_id)
        if not sub:
            return
        sub.status = "grading"; db.commit()

        assignment = db.query(Assignment).get(sub.assignment_id)

        artifacts = {}
        for art in sub.artifacts:
            data = get_object_bytes(cfg, art.s3_key)
            # normalize kinds
            if art.kind.endswith("pdf"): artifacts["pdf"] = data
            if art.kind.endswith("txt"): artifacts["txt"] = data
            if art.kind.endswith("ipynb"): artifacts["ipynb"] = data
            if art.kind.endswith("py"): artifacts["py"] = data
            if art.kind.endswith("mp4"): artifacts["mp4"] = data

        result = run_grading_pipeline(cfg, assignment, artifacts)

        # store criterion scores
        criteria = result.get("criteria", [])
        overall = result.get("overall", {})
        flags = set(result.get("flags", []))

        for c in criteria:
            db.add(AIScore(
                submission_id=sub.id,
                criterion=c["name"],
                score=c["score"],
                confidence=c.get("confidence", 0.5),
                rationale=c.get("rationale",""),
                evidence=c.get("evidence", {}),
                model=cfg.OLLAMA_MODEL
            ))

        sub.final_score = overall.get("score", 0)
        sub.final_feedback = overall.get("summary", "")

        # basic “needs review” policy for reflective/critical criteria
        low_conf = any(float(c.get("confidence",0)) < 0.70 for c in criteria)
        if low_conf or "needs_review" in flags:
            sub.status = "needs_review"
        else:
            sub.status = "graded"

        db.commit()
    except Exception:
        db.rollback()
        if "sub" in locals() and sub:
            sub.status = "error"
            db.commit()
        raise
    finally:
        db.close()
