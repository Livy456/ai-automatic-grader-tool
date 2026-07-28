"""
Pre-chunked response-pairing agent: a single, Pydantic-validated LLM call that pairs a *student's*
submission text against an assignment's already-chunked ``question`` / ``answer`` pairs (the
:class:`~app.database.models.AssignmentQuestionChunk` rows a teacher created — and can hand-edit —
via the Assignment Creation flow, see ``app.routes.assignment_library``).

Motivation
----------
:mod:`claude_parsing_agent` decomposes the assignment into question/answer/response triples from
scratch, for every single submission — useful when there's no pre-existing chunk bank, but
wasteful (and a source of chunk-boundary drift across submissions) once an assignment already has
a stable, teacher-reviewed set of question/answer chunks. This agent instead keeps the
question/answer text fixed (already known, no need to (re)invent it) and asks the model to do the
one thing that's actually submission-specific: find *this* student's response for each question.

This is a drop-in alternative **chunk source** for
:meth:`app.grading.multimodal.pipeline.MultimodalGradingPipeline.run`'s chunk-selection step (see
:func:`try_build_prechunked_pairing_chunks`, called before
:func:`app.grading.chunking.claude_parsing_agent.try_build_claude_parsing_agent_chunks`) — it
produces the exact same :class:`~app.grading.schemas.GradingChunk` shape (including the
``evidence["trio"]`` used by the grading/confidence/report code), so grading, the
multi-sample-per-chunk confidence ensemble, aggregation, and report-shaping are all 100% reused,
unchanged. Chunking (whichever source wins) still happens exactly once per submission; only the
*grading* of each resulting chunk is repeated for confidence (see
``MULTIMODAL_SAMPLES_PER_MODEL``) — this module does not touch that.

Any failure (no pre-chunked pairs supplied, no API key, API error, malformed/schema-invalid JSON,
empty student submission) causes :func:`try_build_prechunked_pairing_chunks` to return ``None`` so
the pipeline falls through to :mod:`claude_parsing_agent` and the other chunkers exactly as before.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.grading.chunking.claude_parsing_agent import (
    _is_unusable_transcript,
    _max_chars_per_source,
    _modality_for_units,
    _student_submission_text,
    _safe_question_id,
)
from app.grading.chunking.chunker import task_type_from_hints
from app.grading.parsing.ingestion import IngestionEnvelope
from app.grading.schemas import GradingChunk, Modality, TaskType
from app.llm.llm_router import AnthropicJsonClient

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic response schema
# ---------------------------------------------------------------------------


class PairedStudentResponse(BaseModel):
    """One question's matched student-response slice — the model's only job here."""

    question_id: str = Field(default="", description="Must exactly match one of the input question ids.")
    student_response: str = Field(
        default="",
        description=(
            "Only the student's own answer/work for this exact question. Empty string when the "
            "student did not answer this question. Never another question's work."
        ),
    )

    @field_validator("question_id", "student_response", mode="before")
    @classmethod
    def _none_to_empty_string(cls, value: object) -> str:
        return "" if value is None else str(value)


class PairedStudentResponses(BaseModel):
    """Top-level response: one entry per input question, in the same order."""

    pairs: list[PairedStudentResponse] = Field(default_factory=list)


def _response_schema_json() -> str:
    return json.dumps(PairedStudentResponses.model_json_schema(), indent=2)


def _normalize_qid(raw: object) -> str:
    """Whitespace/case-insensitive key for matching a model-echoed ``question_id`` back to the
    input question it belongs to (see ``try_build_prechunked_pairing_chunks``'s positional
    fallback for when this normalized match still misses)."""
    return re.sub(r"\s+", " ", str(raw or "").strip()).lower()


_ANSWER_HINT_MAX_CHARS = 1200


def _questions_block(qa_pairs: list[dict[str, str]]) -> str:
    lines = []
    for p in qa_pairs:
        qid = str(p.get("question_id") or "").strip()
        qtext = str(p.get("question_text") or "").strip()
        line = f"- question_id: {qid}\n  question: {qtext}"
        # The teacher's saved answer for this question — included only as a hint for *locating*
        # the matching response (expected terms/values/code shape) in a long submission; never
        # the response itself, and never conflated with the student's own words (see the system
        # prompt's "reference_answer" rule and _grading_chunk_from_pair, which keeps this in
        # evidence.trio.answer_key_segment, separate from evidence.trio.student_response).
        answer = str(p.get("answer_text") or "").strip()[:_ANSWER_HINT_MAX_CHARS]
        if answer:
            line += f"\n  reference_answer: {answer}"
        lines.append(line)
    return "\n".join(lines)


def _system_prompt(modality_guidance: str = "") -> str:
    return (
        "You are a precise response-pairing agent for an automated grading pipeline.\n\n"
        "You are given QUESTIONS - a fixed list of already-isolated questions, each with a "
        "stable question_id and, when available, a `reference_answer` - and STUDENT_SUBMISSION, "
        "one student's full submission. For EVERY question_id in QUESTIONS (same count, same "
        "order, same ids), find and return only that student's own response for that exact "
        "question. Return only a single JSON object matching exactly this schema (no markdown "
        "fences, no commentary, no extra keys):\n\n"
        f"{_response_schema_json()}\n"
        f"{modality_guidance}\n\n"
        "Strict rules:\n"
        "- Return exactly one pair per input question_id, in the same order - never add, drop, "
        "merge, or reorder question ids.\n"
        "- `student_response` must contain ONLY this student's own work for this exact "
        "question - never another question's response, never instructor test/assert code, never "
        'scaffolding comments like "# TODO".\n'
        "- When a question has a `reference_answer`, use it only to help you locate and recognize "
        "the matching response in the submission (e.g. the terms, values, or code it implies) - "
        "never copy the `reference_answer` itself into `student_response`, and never let it "
        "override what the student actually wrote.\n"
        "- The submission is often much longer than any single question; scan the whole "
        "STUDENT_SUBMISSION for each question's own section (e.g. the cell/paragraph that follows "
        "that exact question's own instructions or restates it) rather than assuming responses "
        "appear in the first portion of the text.\n"
        "- Prefer verbatim excerpts over paraphrasing; never invent student work.\n"
        "- If the student did not answer a question, use an empty string for that pair - never "
        "null and never omit the pair."
    )


def _build_user_message(qa_pairs: list[dict[str, str]], student_text: str) -> str:
    return (
        "### QUESTIONS\n\n"
        + _questions_block(qa_pairs)
        + "\n\n### STUDENT_SUBMISSION\n\n"
        + student_text
    )


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class PrechunkedResponsePairingAgent:
    """Sends one Anthropic Messages call and validates the JSON response against
    :class:`PairedStudentResponses`. Never raises; returns ``None`` on any failure."""

    def __init__(self, client: AnthropicJsonClient, model_label: str) -> None:
        self._client = client
        self.model_label = model_label

    def pair(
        self, *, qa_pairs: list[dict[str, str]], student_text: str, modality_guidance: str = ""
    ) -> PairedStudentResponses | None:
        if not qa_pairs or not student_text.strip():
            return None
        try:
            raw = self._client.chat_json(
                [
                    {"role": "system", "content": _system_prompt(modality_guidance)},
                    {"role": "user", "content": _build_user_message(qa_pairs, student_text)},
                ],
                temperature=0.1,
            )
        except Exception:
            _log.warning(
                "prechunked_response_pairing_agent: chat request failed model=%s",
                self.model_label,
                exc_info=True,
            )
            return None
        try:
            return PairedStudentResponses.model_validate(raw)
        except ValidationError:
            _log.warning(
                "prechunked_response_pairing_agent: response failed schema validation model=%s",
                self.model_label,
                exc_info=True,
            )
            return None


# ---------------------------------------------------------------------------
# Config / enablement
# ---------------------------------------------------------------------------


def _client_for(cfg: Any) -> tuple[AnthropicJsonClient, str] | None:
    key = (getattr(cfg, "ANTHROPIC_API_KEY", "") or "").strip()
    if not key:
        return None
    model = (
        getattr(cfg, "MULTIMODAL_CLAUDE_PARSING_AGENT_MODEL", "") or ""
    ).strip() or "claude-opus-4-7"
    try:
        max_tokens = int(
            getattr(cfg, "MULTIMODAL_CLAUDE_PARSING_AGENT_MAX_TOKENS", 16384) or 16384
        )
    except (TypeError, ValueError):
        max_tokens = 16384
    return AnthropicJsonClient(key, model, max_tokens=max_tokens), f"anthropic:{model}"


# ---------------------------------------------------------------------------
# Pairing result -> GradingChunk
# ---------------------------------------------------------------------------


def _grading_chunk_from_pair(
    qa_pair: dict[str, str],
    student_response: str,
    index: int,
    *,
    envelope: IngestionEnvelope,
    modality: Modality,
    task_type: TaskType,
    model_label: str,
) -> GradingChunk | None:
    q = str(qa_pair.get("question_text") or "").strip()
    sr = (student_response or "").strip()
    if not q and not sr:
        return None
    ans = str(qa_pair.get("answer_text") or "").strip()
    qid = _safe_question_id(str(qa_pair.get("question_id") or ""), index)
    extracted = "\n\n".join(p for p in (q, sr) if p).strip()
    return GradingChunk(
        chunk_id=f"{envelope.student_id}:{envelope.assignment_id}:prechunked_qa_pairing:{index}:{qid}",
        assignment_id=envelope.assignment_id,
        student_id=envelope.student_id,
        question_id=qid,
        modality=modality,
        task_type=task_type,
        extracted_text=extracted,
        evidence={
            "chunker": "prechunked_qa_pairing",
            "question_id": qid,
            "question_text": q,
            "response_preview": sr,
            "trio": {
                "question": q,
                "student_response": sr,
                "answer_key_segment": ans,
                "instructor_context": "",
            },
            "_prechunked_qa_pairing": True,
            "prechunked_qa_pairing_model": model_label,
        },
    )


# ---------------------------------------------------------------------------
# Entry point (mirrors ``try_build_claude_parsing_agent_chunks``'s calling convention so it slots
# into the same chunker-selection waterfall in ``pipeline.py``)
# ---------------------------------------------------------------------------


def try_build_prechunked_pairing_chunks(
    envelope: IngestionEnvelope,
    cfg: Any,
    *,
    answer_key_plaintext: str = "",
) -> tuple[list[GradingChunk], str] | None:
    """
    Return ``(chunks, "prechunked_qa_pairing")``, or ``None`` — never raises — when the
    assignment has no pre-chunked question/answer pairs (``modality_hints["prechunked_qa_pairs"]``,
    set by ``app.tasks.grade_submission`` from the ``Assignment.question_chunks`` saved via
    Assignment Creation), when disabled/misconfigured, the student submission is empty/too short,
    the API call fails, or the response fails validation.
    """
    hints = envelope.modality_hints or {}
    qa_pairs = hints.get("prechunked_qa_pairs")
    if not isinstance(qa_pairs, list) or not qa_pairs:
        return None
    qa_pairs = [p for p in qa_pairs if isinstance(p, dict) and str(p.get("question_text") or "").strip()]
    if not qa_pairs:
        return None

    pair_client = _client_for(cfg)
    if pair_client is None:
        return None
    client, model_label = pair_client

    cap = _max_chars_per_source(cfg)
    student_text = _student_submission_text(envelope, cap)
    if len(student_text.strip()) < 8 or _is_unusable_transcript(student_text):
        _log.info(
            "prechunked_response_pairing_agent: student submission plaintext too short/unusable; "
            "skipping"
        )
        return None

    modality = _modality_for_units(envelope, hints)
    if modality == Modality.UNKNOWN:
        modality = Modality.MIXED
    task_type = task_type_from_hints(hints)

    agent = PrechunkedResponsePairingAgent(client, model_label)
    paired = agent.pair(qa_pairs=qa_pairs, student_text=student_text)
    if paired is None:
        return None

    response_by_qid = {
        _normalize_qid(p.question_id): p.student_response
        for p in paired.pairs
        if str(p.question_id).strip()
    }

    chunks: list[GradingChunk] = []
    for i, qa_pair in enumerate(qa_pairs):
        qid = str(qa_pair.get("question_id") or "").strip() or f"q{i + 1}"
        sr = response_by_qid.get(_normalize_qid(qid), "")
        if not sr and i < len(paired.pairs):
            # The model didn't echo back a ``question_id`` matching this question (whitespace
            # or casing drift, or it slightly reworded the id) even though the system prompt
            # guarantees "same count, same order, same ids" — fall back to positional
            # alignment instead of silently treating this student's response as missing.
            sr = paired.pairs[i].student_response
        ch = _grading_chunk_from_pair(
            qa_pair,
            sr,
            i,
            envelope=envelope,
            modality=modality,
            task_type=task_type,
            model_label=model_label,
        )
        if ch is not None:
            chunks.append(ch)

    if not chunks:
        return None
    _log.info(
        "prechunked_response_pairing_agent: model=%s questions_in=%d chunks_out=%d",
        model_label,
        len(qa_pairs),
        len(chunks),
    )
    return chunks, "prechunked_qa_pairing"
