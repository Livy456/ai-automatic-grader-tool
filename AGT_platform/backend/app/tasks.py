import json
import logging
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
    run_multimodal_grading,
)
from .models import (
    AIScore,
    Assignment,
    AssignmentAttachment,
    AssignmentUpload,
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
    "grade_assignment_upload": {"queue": "gpu"},
}
# Bound prefetch so one worker does not hoard many large grading tasks in memory.
celery_app.conf.worker_prefetch_multiplier = max(1, _cfg.CELERY_WORKER_PREFETCH)
# Default worker process concurrency: 3 grading tasks in parallel (see Config docstring). A
# worker started with an explicit ``--concurrency=N`` CLI flag still overrides this default.
celery_app.conf.worker_concurrency = max(1, _cfg.CELERY_WORKER_CONCURRENCY)
celery_app.conf.task_acks_late = True
celery_app.conf.task_reject_on_worker_lost = True


def init_celery(cfg: Config | None = None) -> None:
    """
    Re-apply broker/result-backend URLs from ``Config`` (or the module-level default).
    ``celery_app`` is already configured at import time from ``Config()`` above; call this
    at app-startup only if you need to refresh it against a differently-sourced ``Config``
    instance (e.g. in tests).
    """
    c = cfg or Config()
    celery_app.conf.broker_url = c.REDIS_URL
    celery_app.conf.result_backend = c.REDIS_URL


def _evidence_for_db(ev):
    if ev is None:
        return {}
    if isinstance(ev, dict):
        return ev
    return {"text": str(ev)}


def _rationale_for_db(c: dict) -> str:
    return (c.get("rationale") or c.get("justification") or "").strip() or ""


def _student_evidence_from_criterion(c: dict) -> str:
    ev = c.get("evidence")
    if isinstance(ev, dict):
        trio = ev.get("trio")
        if isinstance(trio, dict):
            s = str(trio.get("student_response") or "").strip()
            if s:
                return s
        txt = str(ev.get("text") or "").strip()
        return txt
    if isinstance(ev, str):
        return ev.strip()
    return ""


def _parse_uploaded_rubric_column(filename: str, data: bytes):
    """Best-effort parse of uploaded rubric JSON into a structured rubric column."""
    fn = (filename or "").strip().lower()
    if not fn.endswith(".json"):
        return None
    try:
        raw = json.loads(data.decode("utf-8", errors="ignore"))
    except Exception:
        return None
    if isinstance(raw, (list, dict)):
        return raw
    return None


def _question_text_from_chunking_row(row: dict[str, Any]) -> str:
    q = str(row.get("question_text") or "").strip()
    if q:
        return q
    unit = row.get("unit")
    if isinstance(unit, dict):
        q = str(unit.get("question_text") or "").strip()
        if q:
            return q
    trio = row.get("trio")
    if isinstance(trio, dict):
        q = str(trio.get("question") or "").strip()
        if q:
            return q
    ev = row.get("evidence")
    if isinstance(ev, dict):
        q = str(ev.get("question_text") or "").strip()
        if q:
            return q
        ev_unit = ev.get("unit")
        if isinstance(ev_unit, dict):
            q = str(ev_unit.get("question_text") or "").strip()
            if q:
                return q
        trio = ev.get("trio")
        if isinstance(trio, dict):
            q = str(trio.get("question") or "").strip()
            if q:
                return q
        q = str(ev.get("question") or "").strip()
        if q:
            return q
    qid = str(row.get("question_id") or "").strip()
    return qid


def _student_response_from_chunking_row(row: dict[str, Any]) -> str:
    ev = row.get("evidence")
    if isinstance(ev, dict):
        ev_trio = ev.get("trio")
        if isinstance(ev_trio, dict):
            s = str(ev_trio.get("student_response") or "").strip()
            if s:
                return s
    s = str(row.get("response_text") or "").strip()
    if s:
        return s
    unit = row.get("unit")
    if isinstance(unit, dict):
        s = str(unit.get("response_text") or "").strip()
        if s:
            return s
        s = str(unit.get("student_response") or "").strip()
        if s:
            return s
    trio = row.get("trio")
    if isinstance(trio, dict):
        s = str(trio.get("student_response") or "").strip()
        if s:
            return s
    if isinstance(ev, dict):
        s = str(ev.get("response_text") or "").strip()
        if s:
            return s
        ev_unit = ev.get("unit")
        if isinstance(ev_unit, dict):
            s = str(ev_unit.get("response_text") or "").strip()
            if s:
                return s
            s = str(ev_unit.get("student_response") or "").strip()
            if s:
                return s
        ev_trio = ev.get("trio")
        if isinstance(ev_trio, dict):
            s = str(ev_trio.get("student_response") or "").strip()
            if s:
                return s
    return ""


def _load_chunk_rows_from_export_path(path_value: object) -> list[dict[str, Any]]:
    path_text = str(path_value or "").strip()
    if not path_text:
        return []
    try:
        payload = json.loads(Path(path_text).read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    chunks = payload.get("chunks")
    if not isinstance(chunks, list):
        return []
    return [row for row in chunks if isinstance(row, dict)]


def _upsert_source_chunk_payload(
    out: dict[str, dict[str, str]],
    row: dict[str, Any],
) -> None:
    cid = str(row.get("chunk_id") or "").strip()
    if not cid:
        return
    question = _question_text_from_chunking_row(row)
    extracted = str(row.get("extracted_text") or "").strip()
    student_response = _student_response_from_chunking_row(row)
    existing = out.get(cid, {})
    out[cid] = {
        "question": question or str(existing.get("question") or "").strip(),
        # Keep the underlying chunk text so UI can show the exact block the grader used.
        "question_chunk_text": extracted
        or str(existing.get("question_chunk_text") or "").strip(),
        "response_text": student_response
        or str(existing.get("response_text") or "").strip(),
        "student_response": student_response
        or str(existing.get("student_response") or "").strip(),
    }


def _build_source_chunk_payload_map(result_dict: dict[str, Any]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    mm_audit = result_dict.get("_multimodal_pipeline_audit")
    if not isinstance(mm_audit, dict):
        return out
    pipeline_audit = mm_audit.get("pipeline_audit")
    if not isinstance(pipeline_audit, dict):
        return out
    chunking_entries = pipeline_audit.get("chunking")
    if not isinstance(chunking_entries, list):
        return out
    for entry in chunking_entries:
        if not isinstance(entry, dict):
            continue
        chunks = entry.get("chunks")
        if not isinstance(chunks, list):
            continue
        for row in chunks:
            if not isinstance(row, dict):
                continue
            _upsert_source_chunk_payload(out, row)

    workflow_rows: list[dict[str, Any]] = []
    for candidate in (
        result_dict.get("_agentic_workflow"),
        mm_audit.get("agentic_workflow"),
    ):
        if isinstance(candidate, list):
            workflow_rows.extend(
                [row for row in candidate if isinstance(row, dict)]
            )

    export_paths: list[str] = []
    for row in workflow_rows:
        phase = str(row.get("phase") or "").strip()
        if phase not in (
            "persist_assignment_chunking_json",
            "persist_trio_chunks_json",
        ):
            continue
        p = str(row.get("path") or "").strip()
        if p and p not in export_paths:
            export_paths.append(p)

    for export_path in export_paths:
        for row in _load_chunk_rows_from_export_path(export_path):
            _upsert_source_chunk_payload(out, row)
    return out


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
        blank_template_bytes: bytes | None = None
        blank_template_filename: str = ""
        blank_template_suffix: str = ""
        rubric_column_override = None
        for art in arts:
            data = get_object_bytes(cfg, art.object_key)
            if art.kind in ("rubric", "answer_key"):
                ex = excerpt_attachment_bytes(art.filename, data)
                if art.kind == "rubric":
                    rubric_ex = (rubric_ex + "\n\n" + ex).strip() if rubric_ex else ex
                    parsed = _parse_uploaded_rubric_column(str(art.filename or ""), data)
                    if parsed is not None:
                        rubric_column_override = parsed
                else:
                    answer_ex = (answer_ex + "\n\n" + ex).strip() if answer_ex else ex
                continue
            if art.kind == "blank_assignment":
                blank_template_bytes = bytes(data)
                blank_template_filename = str(art.filename or "").strip()
                blank_template_suffix = Path(blank_template_filename).suffix.lower()
                continue
            submission_parts.append((art.filename or art.kind, data))
        if rubric_column_override is None:
            raise ValueError(
                "Standalone grading requires an uploaded, parseable JSON rubric file."
            )
        main = build_submission_artifacts(submission_parts)
        modality_hints_extra: dict[str, object] = {}
        if blank_template_bytes:
            modality_hints_extra["blank_assignment_template_bytes"] = blank_template_bytes
            modality_hints_extra["blank_assignment_template_suffix"] = blank_template_suffix
            if blank_template_filename:
                modality_hints_extra["blank_assignment_matched_file"] = blank_template_filename
            if blank_template_suffix == ".ipynb":
                modality_hints_extra["blank_assignment_ipynb_bytes"] = blank_template_bytes

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
            modality_hints_extra or None,
            rubric_column_override,
        )
        _default_ml = f"openai:{(cfg.OPENAI_MODEL or 'gpt-4o-mini').strip()}"
        model_used = (result.pop("_model_used", None) or _default_ml)[:200]
        models_used = result.pop("_models_used", [model_used])
        result.pop("_used_openai_arbitration", None)
        result.pop("_pipeline_meta", None)
        entropy_meta = result.pop("_entropy_meta", None)

        criteria = result.get("criteria", [])
        overall = result.get("overall", {})
        question_grades = result.get("question_grades", [])
        source_chunk_payload = _build_source_chunk_payload_map(result)
        total_rubric_points = 0.0
        total_rubric_points_earned = 0.0
        try:
            if overall.get("max_points") is not None:
                total_rubric_points = float(overall.get("max_points") or 0.0)
            if overall.get("rubric_points_earned") is not None:
                total_rubric_points_earned = float(overall.get("rubric_points_earned") or 0.0)
        except (TypeError, ValueError):
            total_rubric_points = 0.0
            total_rubric_points_earned = 0.0
        if total_rubric_points <= 0.0 or total_rubric_points_earned <= 0.0:
            qp_max = 0.0
            qp_earned = 0.0
            for qg in question_grades:
                if not isinstance(qg, dict):
                    continue
                ov = qg.get("overall") or {}
                try:
                    qp_max += float(ov.get("max_points") or 0.0)
                except (TypeError, ValueError):
                    pass
                try:
                    qp_earned += float(ov.get("rubric_points_earned") or 0.0)
                except (TypeError, ValueError):
                    pass
            if total_rubric_points <= 0.0:
                total_rubric_points = qp_max
            if total_rubric_points_earned <= 0.0:
                total_rubric_points_earned = qp_earned
        if total_rubric_points <= 0.0:
            for c in criteria:
                if not isinstance(c, dict):
                    continue
                try:
                    total_rubric_points += float(c.get("max_points", 0.0))
                except (TypeError, ValueError):
                    continue
        if total_rubric_points_earned <= 0.0:
            for c in criteria:
                if not isinstance(c, dict):
                    continue
                try:
                    total_rubric_points_earned += float(c.get("score", 0.0))
                except (TypeError, ValueError):
                    continue
        score_fraction = 0.0
        if total_rubric_points > 0.0:
            score_fraction = total_rubric_points_earned / total_rubric_points
        else:
            try:
                score_fraction = float(overall.get("score") or 0.0)
            except (TypeError, ValueError):
                score_fraction = 0.0
            if score_fraction > 1.0:
                score_fraction /= 100.0
        score_fraction = max(0.0, min(1.0, score_fraction))
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
        sub.final_score = score_fraction
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
                    "max_points": round(total_rubric_points, 4),
                    "rubric_points_earned": round(total_rubric_points_earned, 4),
                    "model_used": model_used,
                    "models_used": models_used,
                    "graded_at": sub_ref.updated_at.isoformat()
                    if sub_ref.updated_at
                    else None,
                    "criteria": [
                        {
                            "criterion": c.get("name", ""),
                            "score": c.get("score", 0),
                            "max_points": c.get("max_points"),
                            "rubric_points_earned": c.get("score", 0),
                            "confidence": c.get("confidence", 0.5),
                            "justification": _rationale_for_db(c),
                            "rationale": _rationale_for_db(c),
                            "student_evidence": _student_evidence_from_criterion(c),
                            "evidence": _evidence_for_db(c.get("evidence")),
                        }
                        for c in criteria
                    ],
                    "question_grades": [
                        {
                            "chunk_id": qg.get("chunk_id"),
                            "source_chunk_id": qg.get("_source_chunk_id"),
                            "overall": {
                                "score": (qg.get("overall") or {}).get("score"),
                                "max_points": (qg.get("overall") or {}).get("max_points"),
                                "rubric_points_earned": (qg.get("overall") or {}).get("rubric_points_earned"),
                                "confidence": (qg.get("overall") or {}).get("confidence"),
                            },
                            "question_payload": {
                                "question": (
                                    source_chunk_payload.get(
                                        str(qg.get("_source_chunk_id") or "").strip(), {}
                                    ).get("question")
                                    or str(qg.get("chunk_id") or "").strip()
                                ),
                                "question_chunk_text": (
                                    source_chunk_payload.get(
                                        str(qg.get("_source_chunk_id") or "").strip(), {}
                                    ).get("question_chunk_text")
                                    or ""
                                ),
                                "student_response": (
                                    source_chunk_payload.get(
                                        str(qg.get("_source_chunk_id") or "").strip(), {}
                                    ).get("student_response")
                                    or ""
                                ),
                                "response_text": (
                                    source_chunk_payload.get(
                                        str(qg.get("_source_chunk_id") or "").strip(), {}
                                    ).get("response_text")
                                    or ""
                                ),
                            },
                            "criteria": [
                                {
                                    "criterion": qc.get("name", ""),
                                    "score": qc.get("score", 0),
                                    "max_points": qc.get("max_points"),
                                    "rubric_points_earned": qc.get("score", 0),
                                    "confidence": qc.get("confidence", 0.5),
                                    "justification": str(qc.get("justification") or "").strip(),
                                    "student_evidence": str(qc.get("evidence") or "").strip(),
                                    "evidence": _evidence_for_db(qc.get("evidence")),
                                }
                                for qc in (qg.get("criteria") or [])
                                if isinstance(qc, dict)
                            ],
                        }
                        for qg in question_grades
                        if isinstance(qg, dict)
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


@celery_app.task(name="grade_assignment_upload", bind=True, max_retries=2)
def grade_assignment_upload(self, assignment_id: str):
    """
    GPU-queue grading for the public "upload one file, grade it" assignment-upload flow
    (``POST /api/assignments/<id>/grade``). Idempotent, same pattern as grade_submission /
    grade_standalone_submission: only one successful queued -> grading transition per
    assignment; duplicate deliveries no-op after work started.
    """
    _ensure_db()
    cfg = Config()
    db = SessionLocal()
    a = None
    try:
        a = (
            db.query(AssignmentUpload)
            .filter(AssignmentUpload.id == assignment_id)
            .with_for_update()
            .first()
        )
        if not a:
            return
        if a.status in ("grading", "graded"):
            return
        if a.status == "error":
            return
        if a.status != "queued":
            # e.g. still uploading — do not grade
            return

        a.status = "grading"
        a.updated_at = datetime.utcnow()
        db.commit()

        raw = get_object_bytes(cfg, a.storage_uri)
        stem = Path(a.filename or "upload").stem
        artifacts = build_submission_artifacts([(a.filename or "upload", raw)])
        if not artifacts:
            raise ValueError(
                f"Unsupported file type for multimodal grading: {a.filename!r}"
            )

        assignment = SimpleNamespace(
            modality=None,
            rubric=None,
            title=stem,
            description=f"Assignment upload: {a.filename}",
        )
        result = run_multimodal_grading(
            cfg,
            assignment=assignment,
            artifacts_bytes=artifacts,
            assignment_id=str(a.id),
            student_id="assignment_upload",
            rubric_column=None,
            assignment_stem=stem,
            validate_output=True,
        )
        overall = result.get("overall") or {}
        score = overall.get("score")
        a.suggested_grade = float(score) if score is not None else None
        a.feedback = str(overall.get("summary") or "").strip() or None
        a.status = "graded"
        a.updated_at = datetime.utcnow()
        db.commit()
    except Exception as exc:
        db.rollback()
        if a is not None:
            a2 = db.query(AssignmentUpload).filter(AssignmentUpload.id == assignment_id).first()
            if a2 and a2.status == "grading":
                a2.status = "error"
                a2.feedback = f"Grading failed: {type(exc).__name__}: {exc}"
                a2.updated_at = datetime.utcnow()
                db.commit()
        raise
    finally:
        db.close()
