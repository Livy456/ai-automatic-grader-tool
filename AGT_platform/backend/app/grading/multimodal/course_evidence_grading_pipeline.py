"""
Course/library submission-review multimodal grading pipeline: **Evidence Agent** → **Grading
Agent**.

Built directly on top of the standalone autograder's own pipeline plumbing (see
:mod:`app.grading.multimodal.pipeline_runner`) — the same envelope/rubric resolution
(:func:`~app.grading.multimodal.pipeline_runner.build_multimodal_grading_context`), the same
:class:`~app.grading.multimodal.pipeline.MultimodalGradingPipeline` grading engine (rubric
routing, per-chunk LLM grading, semantic-entropy confidence, assignment aggregation), and the same
output validation/schema
(:func:`~app.grading.grading_output.pipeline_runner.finalize_multimodal_grading_output` →
:mod:`app.grading.grading_output.output_schema`). The only thing this module owns is *which
chunks* get graded:

1. **Evidence Agent**
   (:class:`~app.grading.chunking.prechunked_response_pairing_agent.PrechunkedResponsePairingAgent`,
   invoked via :func:`~app.grading.chunking.prechunked_response_pairing_agent
   .try_build_prechunked_pairing_chunks`) associates this submission's own response text with the
   corresponding stored question + answer: it pairs the response against the assignment's
   teacher-reviewed :class:`~app.database.models.AssignmentQuestionChunk` bank (threaded through
   as ``modality_hints["prechunked_qa_pairs"]`` by ``app.tasks.grade_submission``), producing one
   :class:`~app.grading.schemas.GradingChunk` per question with ``evidence["trio"]`` =
   ``{question, student_response, answer_key_segment}`` — the evidence a grader needs.
2. **Grading Agent**: those evidence chunks are handed to
   :meth:`~app.grading.multimodal.pipeline.MultimodalGradingPipeline.grade_prebuilt_chunks`, i.e.
   the *exact same* rubric-routing + per-chunk LLM grading code
   (:mod:`app.llm.prompts_chunk`, which puts the question, answer key, and evidence into the
   grading prompt alongside the rubric) that the standalone pipeline's chunker waterfall uses for
   every other chunk source — so confidence-score calculation
   (:mod:`app.grading.confidence_calculation.semantic_confidence`) and output validation are
   unchanged.

When the assignment has no saved question/answer chunk bank yet (or the Evidence Agent can't run —
missing Anthropic API key, empty submission text, malformed response, etc.),
:func:`run_course_submission_evidence_grading_pipeline` falls back to
:meth:`MultimodalGradingPipeline.run`'s full standalone-style chunker waterfall, so every
course/library assignment keeps grading exactly as it did before this pipeline existed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.config import Config
from app.grading.chunking.prechunked_response_pairing_agent import (
    try_build_prechunked_pairing_chunks,
)

from .pipeline_runner import (
    build_multimodal_grading_context,
    finalize_multimodal_grading_output,
)

_log = logging.getLogger(__name__)


def run_course_submission_evidence_grading_pipeline(
    cfg: Config,
    *,
    assignment: Any,
    artifacts_bytes: dict[str, bytes],
    assignment_id: str,
    student_id: str,
    rubric_column: Any = None,
    rubric_text: str | None = None,
    answer_key_text: str | None = None,
    assignment_stem: str | None = None,
    rubric_dir: Path | None = None,
    answer_key_dir: Path | None = None,
    require_answer_key: bool = False,
    modality_hints_extra: dict[str, Any] | None = None,
    validate_output: bool = True,
) -> dict[str, Any]:
    """
    Grade one course/library submission via the Evidence Agent + Grading Agent pipeline described
    in this module's docstring, falling back to the full standalone-style chunker waterfall
    (:func:`~app.grading.multimodal.pipeline_runner.run_multimodal_grading`'s own code path via
    :meth:`MultimodalGradingPipeline.run`) when no evidence chunks can be built.

    Same parameters, same return shape (validated grading dict), as
    :func:`app.grading.multimodal.pipeline_runner.run_multimodal_grading` — this is a drop-in
    replacement for course/library submissions specifically.
    """
    context = build_multimodal_grading_context(
        cfg,
        assignment=assignment,
        artifacts_bytes=artifacts_bytes,
        assignment_id=assignment_id,
        student_id=student_id,
        rubric_column=rubric_column,
        rubric_text=rubric_text,
        answer_key_text=answer_key_text,
        assignment_stem=assignment_stem,
        rubric_dir=rubric_dir,
        answer_key_dir=answer_key_dir,
        require_answer_key=require_answer_key,
        modality_hints_extra=modality_hints_extra,
    )

    hints = context.envelope.modality_hints or {}
    answer_key_plain = str(hints.get("answer_key_plaintext") or "").strip()
    evidence_result = try_build_prechunked_pairing_chunks(
        context.envelope, cfg, answer_key_plaintext=answer_key_plain
    )
    if evidence_result is not None:
        chunks, chunker_mode = evidence_result
        _log.info(
            "course_evidence_grading_pipeline: Evidence Agent built %d chunk(s) "
            "(assignment_id=%s student_id=%s)",
            len(chunks),
            assignment_id,
            student_id,
        )
        mm_result = context.pipeline.grade_prebuilt_chunks(
            context.envelope, chunks, chunker_mode=chunker_mode
        )
    else:
        _log.info(
            "course_evidence_grading_pipeline: no evidence chunks available (no saved "
            "question/answer chunk bank, or the Evidence Agent could not run); falling back to "
            "the standalone-style chunker waterfall (assignment_id=%s student_id=%s)",
            assignment_id,
            student_id,
        )
        mm_result = context.pipeline.run(context.envelope)

    return finalize_multimodal_grading_output(
        mm_result,
        flat_rubric=context.flat_rubric,
        profile=context.profile,
        validate_output=validate_output,
    )
