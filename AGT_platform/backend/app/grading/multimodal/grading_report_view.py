"""
Shared read-side helpers for rendering a submission's grading results, used by both
``app.routes.standalone`` (``GET /api/standalone/submissions/{id}``) and
``app.routes.submissions`` (``GET /api/submissions/{id}``) so the course/library submission
review page can present the same rubric-totals/question-breakdown view the standalone autograder
results page does, from the same MinIO-stored grading report shape (see
:mod:`app.grading.multimodal.grading_report`, which builds that report at grading time).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.config import Config
from app.database.storage import get_object_bytes


def safe_report_json(cfg: Config, object_key: str | None) -> dict[str, Any]:
    """Best-effort fetch + parse of the MinIO grading-report JSON; never raises."""
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


def rubric_totals_from_report(report: dict[str, Any]) -> tuple[float | None, float | None]:
    """``(rubric_points_earned, rubric_points_max)`` derived from a grading report's totals,
    preferring the per-question breakdown, falling back to the report's own top-level totals."""
    if not isinstance(report, dict):
        return (None, None)
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
        try:
            if report.get("rubric_points_earned") is not None and rubric_points_earned is None:
                rubric_points_earned = float(report.get("rubric_points_earned"))
            if report.get("max_points") is not None and rubric_points_max is None:
                rubric_points_max = float(report.get("max_points"))
        except (TypeError, ValueError):
            pass

    if (rubric_points_max or 0.0) <= 0.0:
        d_max = 0.0
        for qg in question_grades:
            if not isinstance(qg, dict):
                continue
            for qc in qg.get("criteria") or []:
                if not isinstance(qc, dict):
                    continue
                try:
                    d_max += float(qc.get("max_points") or 0.0)
                except (TypeError, ValueError):
                    pass
        rubric_points_max = d_max if d_max > 0.0 else rubric_points_max

    return (rubric_points_earned, rubric_points_max)


def score_pct_from_rubric_totals(
    rubric_points_earned: float | None, rubric_points_max: float | None
) -> float | None:
    if rubric_points_earned is None or (rubric_points_max or 0.0) <= 0.0:
        return None
    return max(
        0.0,
        min(100.0, (float(rubric_points_earned) / float(rubric_points_max)) * 100.0),
    )


def score_to_100(raw: object) -> float | None:
    """Normalize a score that may be a ``0..1`` fraction or already a ``0..100`` value."""
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


def question_from_evidence(ev: object) -> str | None:
    if not isinstance(ev, dict):
        return None
    trio = ev.get("trio")
    if isinstance(trio, dict):
        q = str(trio.get("question") or "").strip()
        if q:
            return q
    q = str(ev.get("question") or "").strip()
    return q or None


def student_evidence_from_payload(ev: object) -> str:
    if not isinstance(ev, dict):
        return ""
    trio = ev.get("trio")
    if isinstance(trio, dict):
        sr = str(trio.get("student_response") or "").strip()
        if sr:
            return sr
    txt = str(ev.get("text") or "").strip()
    return txt


def iso_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")
