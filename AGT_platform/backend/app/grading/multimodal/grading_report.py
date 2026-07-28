"""
Shared grading-report shaping helpers used by both course/library (``app.tasks.grade_submission``)
and standalone (``app.tasks.grade_standalone_submission``) Celery tasks.

Both tasks run the exact same :func:`app.grading.multimodal.pipeline_runner.run_multimodal_grading`
under the hood, so their raw ``result`` dicts have identical shape (``overall``, ``criteria``,
``question_grades``). Centralizing the report-row/rubric-totals shaping here means the MinIO-stored
grading report — and therefore the review UI, which reads it back via ``app.routes.submissions`` /
``app.routes.standalone`` — has one consistent shape regardless of which submission type produced
it, instead of two hand-maintained copies of the same logic.
"""
from __future__ import annotations

from typing import Any


def evidence_for_db(ev: Any) -> dict[str, Any]:
    if ev is None:
        return {}
    if isinstance(ev, dict):
        return ev
    return {"text": str(ev)}


def rationale_for_db(c: dict[str, Any]) -> str:
    return (c.get("rationale") or c.get("justification") or "").strip() or ""


def student_evidence_from_criterion(c: dict[str, Any]) -> str:
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


def rubric_totals_and_score_fraction(
    *,
    overall: dict[str, Any],
    criteria: list[dict[str, Any]],
    question_grades: list[dict[str, Any]],
) -> tuple[float, float, float]:
    """
    Best-effort ``(total_max_points, total_points_earned, score_fraction)`` from a multimodal
    grading result: prefer ``overall``'s own totals, then sum ``question_grades``, then fall back
    to summing the flat ``criteria`` list. ``score_fraction`` is always clamped to ``[0, 1]``.
    """
    total_max = 0.0
    total_earned = 0.0
    try:
        if overall.get("max_points") is not None:
            total_max = float(overall.get("max_points") or 0.0)
        if overall.get("rubric_points_earned") is not None:
            total_earned = float(overall.get("rubric_points_earned") or 0.0)
    except (TypeError, ValueError):
        total_max = 0.0
        total_earned = 0.0

    if total_max <= 0.0 or total_earned <= 0.0:
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
        if total_max <= 0.0:
            total_max = qp_max
        if total_earned <= 0.0:
            total_earned = qp_earned

    if total_max <= 0.0:
        for c in criteria:
            if not isinstance(c, dict):
                continue
            try:
                total_max += float(c.get("max_points", 0.0))
            except (TypeError, ValueError):
                continue
    if total_earned <= 0.0:
        for c in criteria:
            if not isinstance(c, dict):
                continue
            try:
                total_earned += float(c.get("score", 0.0))
            except (TypeError, ValueError):
                continue

    if total_max > 0.0:
        score_fraction = total_earned / total_max
    else:
        try:
            score_fraction = float(overall.get("score") or 0.0)
        except (TypeError, ValueError):
            score_fraction = 0.0
        if score_fraction > 1.0:
            score_fraction /= 100.0
    score_fraction = max(0.0, min(1.0, score_fraction))
    return total_max, total_earned, score_fraction


def report_criteria_rows(criteria: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-criterion rows for the grading-report JSON (criterion/score/max_points/confidence/...)."""
    return [
        {
            "criterion": c.get("name", ""),
            "score": c.get("score", 0),
            "max_points": c.get("max_points"),
            "rubric_points_earned": c.get("score", 0),
            "confidence": c.get("confidence", 0.5),
            "justification": rationale_for_db(c),
            "rationale": rationale_for_db(c),
            "student_evidence": student_evidence_from_criterion(c),
            "evidence": evidence_for_db(c.get("evidence")),
        }
        for c in criteria
        if isinstance(c, dict)
    ]


def report_question_grades_rows(
    question_grades: list[dict[str, Any]],
    source_chunk_payload: dict[str, dict[str, str]],
    assignment_question_text_by_id: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """
    Per-question-grade rows (question/response text + per-criterion breakdown) for the report.

    ``assignment_question_text_by_id`` (optional, ``{AssignmentQuestionChunk.question_id:
    question_text}``) takes precedence over whatever question text this submission's own
    chunking/trio-refine pass produced — it's the exact, teacher-reviewed text saved during
    Assignment Creation (see ``app.tasks.grade_submission``), so course/library submissions with a
    saved question/answer chunk bank always show that stored text rather than a per-submission
    re-derivation of it. Standalone submissions (no chunk bank) simply omit this argument.
    """
    question_text_by_id = assignment_question_text_by_id or {}
    rows: list[dict[str, Any]] = []
    for qg in question_grades:
        if not isinstance(qg, dict):
            continue
        source_id = str(qg.get("_source_chunk_id") or "").strip()
        payload = source_chunk_payload.get(source_id, {})
        overall = qg.get("overall") or {}
        stored_question = question_text_by_id.get(str(payload.get("question_id") or "").strip())
        rows.append(
            {
                "chunk_id": qg.get("chunk_id"),
                "source_chunk_id": qg.get("_source_chunk_id"),
                "overall": {
                    "score": overall.get("score"),
                    "max_points": overall.get("max_points"),
                    "rubric_points_earned": overall.get("rubric_points_earned"),
                    "confidence": overall.get("confidence"),
                },
                "question_payload": {
                    "question": (
                        stored_question
                        or payload.get("question")
                        or str(qg.get("chunk_id") or "").strip()
                    ),
                    "question_chunk_text": payload.get("question_chunk_text") or "",
                    "student_response": payload.get("student_response") or "",
                    "response_text": payload.get("response_text") or "",
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
                        "evidence": evidence_for_db(qc.get("evidence")),
                    }
                    for qc in (qg.get("criteria") or [])
                    if isinstance(qc, dict)
                ],
            }
        )
    return rows
