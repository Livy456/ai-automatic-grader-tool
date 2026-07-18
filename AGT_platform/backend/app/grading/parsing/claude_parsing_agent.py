"""
Claude assignment-parsing agent: a single, Pydantic-validated LLM call that decomposes one
student submission directly into isolated per-question ``(question, student_response, answer)``
triples.

Motivation
----------
The heuristic chunkers in :mod:`notebook_chunker` / :mod:`template_aligned_notebook_chunks` /
:mod:`chunker` isolate one question from the next (and from instructor test/setup code) using
regex cell classification and scaffold-anchor alignment. That approach is fast and free, but is
inherently prone to a whole class of chunk-boundary bugs: a neighboring question's heading or an
instructor's test/assert cell leaking into this question's text, or a single question with
multiple scaffold cells being re-emitted as several duplicate-looking chunks.

This module instead asks **Claude** to read the blank template, the student's submission, and
the answer key together and directly emit the isolated triples — isolation is the model's job,
not a set of regexes — and validates the response against :class:`ParsedAssignmentUnits` with
Pydantic before any of it is trusted. Any failure (disabled, no API key, API error, malformed or
schema-invalid JSON) causes :func:`try_build_claude_parsing_agent_chunks` to return ``None`` so
callers fall through to the existing chunkers exactly as before — this agent never raises and
never replaces the rest of the pipeline; it only pre-empts it when it succeeds.

Enable / configure
-------------------
``MULTIMODAL_CLAUDE_PARSING_AGENT``: ``off`` | **auto** (default; runs whenever
``ANTHROPIC_API_KEY`` is set) | ``on`` (same as ``auto`` here; kept for symmetry with the other
``MULTIMODAL_*`` toggles). ``MULTIMODAL_CLAUDE_PARSING_AGENT_MODEL`` (default
``claude-opus-4-7``), ``MULTIMODAL_CLAUDE_PARSING_AGENT_MAX_TOKENS`` (default ``16384``), and
``MULTIMODAL_CLAUDE_PARSING_AGENT_MAX_CHARS_PER_SOURCE`` (default ``120000``, clamped to
``[8000, 500000]``) cap each of the (blank / student / answer key) source texts before the call.

Integration
-----------
Called from :meth:`app.grading.multimodal.pipeline.MultimodalGradingPipeline.run`, ahead of the
OpenAI trio frontload and every heuristic chunker. Both the multimodal (course) grading pipeline
and the standalone autograder pipeline invoke that same ``run()``, so enabling this agent here
covers both call sites through a single integration point.

Modality coverage
------------------
The submission text handed to Claude is modality-agnostic by construction: it reuses
:mod:`artifact_plaintext`, which already flattens every artifact kind the pipeline accepts
(``ipynb``/``py`` programming, ``pdf``/``docx``/``txt``/``md`` written response, ``csv``/``xlsx``
tabular, and ``mp4``/``mp3``/``wav``/``m4a``/``webm`` oral/audio via Whisper transcription) into
one plain-text view, and multiple artifact kinds in the same submission are concatenated
together. On top of that, this module:

- Prefers an already-computed transcript from
  :func:`app.grading.parsing.audio_half_split.maybe_prepare_audio_half_split` (which runs earlier
  in ``pipeline.py``) instead of re-transcribing the same audio bytes a second time.
- Treats a Whisper failure/placeholder marker (empty transcript, oversized file, transcription
  error) as unusable input and returns ``None`` rather than sending garbage to Claude.
- Appends a short modality-specific note to the system prompt (oral transcripts tolerate
  disfluencies and are segmented by spoken turns rather than headings; code/notebooks must be
  preserved verbatim; written/tabular responses are segmented by the instructor's prompts) so one
  prompt generalizes across programming, written-response, and oral/audio assignments.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.grading.llm_router import AnthropicJsonClient
from app.grading.schemas import GradingChunk, Modality, TaskType

from .artifact_plaintext import (
    artifacts_to_concatenated_plain,
    bytes_with_suffix_to_plain,
    infer_modality_from_artifact_keys,
)
from .chunker import modality_from_hints, task_type_from_hints
from .ingestion import IngestionEnvelope

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic response schema
# ---------------------------------------------------------------------------


class ParsedQuestionUnit(BaseModel):
    """One gradable question, fully isolated from every other question and from instructor
    test/setup code — the schema Claude's JSON response is validated against."""

    question_id: str = Field(
        default="",
        description="Stable id/number for this question, e.g. '1.2', 'q3', or a short slug.",
    )
    question: str = Field(
        default="",
        description=(
            "The instructor's prompt for this question only — never another question's "
            "prompt, never instructor test/grading code, never generic setup notes."
        ),
    )
    student_response: str = Field(
        default="",
        description=(
            "Only the student's own answer/work for this question. Never instructor test "
            "code, never another question's prompt or response."
        ),
    )
    answer: str = Field(
        default="",
        description=(
            "The reference/expected answer for this question, taken from the answer key. "
            "Empty string when no matching reference is available."
        ),
    )
    instructor_context: str = Field(
        default="",
        description=(
            "Setup code, test/assert cells, or scaffolding for this question that is not "
            "itself the student's work."
        ),
    )

    @field_validator(
        "question_id", "question", "student_response", "answer", "instructor_context", mode="before"
    )
    @classmethod
    def _none_to_empty_string(cls, value: object) -> str:
        return "" if value is None else str(value)


class ParsedAssignmentUnits(BaseModel):
    """Top-level Claude parsing-agent response: the assignment decomposed into question units."""

    units: list[ParsedQuestionUnit] = Field(default_factory=list)


def _response_schema_json() -> str:
    return json.dumps(ParsedAssignmentUnits.model_json_schema(), indent=2)


def _modality_guidance(modality: Modality, hints: dict[str, Any]) -> str:
    """
    Short modality-specific parsing guidance appended to the system prompt so one prompt
    generalizes across programming, written-response, and oral/audio-transcript submissions
    instead of assuming a heading-delimited written assignment.
    """
    task_type_raw = str(hints.get("task_type") or "").strip().lower()
    if modality == Modality.VIDEO_ORAL or task_type_raw == "oral_interview":
        return (
            "\nModality note: STUDENT_SUBMISSION is an automatic speech-to-text transcript of "
            "an oral/interview response (Whisper). Expect filler words, false starts, and minor "
            "transcription errors — treat those as normal speech, not something to fix or "
            "penalize. There are usually no headings; segment questions using the "
            "interviewer's spoken prompts/turns (or BLANK_ASSIGNMENT's question list) as "
            "boundaries, and keep each answer's own spoken segment verbatim in "
            "`student_response`."
        )
    if modality in (Modality.CODE, Modality.NOTEBOOK):
        return (
            "\nModality note: STUDENT_SUBMISSION is source code or a notebook. Preserve code "
            "exactly as written (indentation, variable names, comments) in `question` and "
            "`student_response` — do not reformat, fix, or complete it."
        )
    if modality == Modality.PROGRAMMING_ANALYSIS:
        return (
            "\nModality note: STUDENT_SUBMISSION is tabular/spreadsheet data (CSV/Excel). Treat "
            "each analysis question and its supporting cells/values as one unit."
        )
    if modality == Modality.WRITTEN:
        return (
            "\nModality note: STUDENT_SUBMISSION is prose (document, journal, or plain text). "
            "Segment by the instructor's questions/prompts and keep the student's paragraphs "
            "verbatim."
        )
    return ""


def _system_prompt(modality_guidance: str = "") -> str:
    return (
        "You are a precise assignment-parsing agent for an automated grading pipeline.\n\n"
        "You are given up to three labeled corpora:\n"
        "1. BLANK_ASSIGNMENT - the official instructor template (may be absent).\n"
        "2. STUDENT_SUBMISSION - the student's actual work (always present).\n"
        "3. ANSWER_KEY_OR_REFERENCE - sample solutions or reference material (may be absent).\n\n"
        "Decompose the assignment into fully isolated, gradable question units. Return only a "
        "single JSON object matching exactly this schema (no markdown fences, no commentary, "
        "no extra keys):\n\n"
        f"{_response_schema_json()}\n"
        f"{modality_guidance}\n\n"
        "Strict rules:\n"
        "- One unit per distinct gradable question/part. Never merge two questions into one "
        "unit, and never split one question into two units.\n"
        "- `question` must contain ONLY this question's own prompt text - never another "
        "question's prompt, never instructor test/assert code, never generic assignment setup "
        "notes.\n"
        "- `student_response` must contain ONLY this student's own work for this exact "
        "question - never instructor test/assert code, never scaffolding comments like "
        '"# TODO", never another question\'s prompt or response.\n'
        '- Instructor test cells, assertion checks, "DO NOT MODIFY" setup code, and boilerplate '
        "scaffolding comments belong in `instructor_context`, not in `question` or "
        "`student_response`.\n"
        "- `answer` is the reference/expected answer for this exact question from the answer "
        "key; use an empty string when no matching reference exists - never invent one.\n"
        "- Prefer verbatim excerpts over paraphrasing; never invent student work, requirements, "
        "or grades.\n"
        "- Preserve the assignment's own ordering and numbering in `question_id` when visible; "
        "otherwise use `q1`, `q2`, ... in document order.\n"
        "- Use empty strings for unknown/absent fields - never null and never omit a field."
    )


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class ClaudeAssignmentParsingAgent:
    """
    Sends one Anthropic Messages call and validates the JSON response against
    :class:`ParsedAssignmentUnits`. Never raises: any API or validation failure is logged and
    surfaced as ``None`` so callers can fall back to the heuristic chunkers.
    """

    def __init__(self, client: AnthropicJsonClient, model_label: str) -> None:
        self._client = client
        self.model_label = model_label

    def parse(
        self,
        *,
        blank_text: str,
        student_text: str,
        answer_key_text: str,
        modality_guidance: str = "",
    ) -> ParsedAssignmentUnits | None:
        if not student_text.strip():
            return None
        try:
            raw = self._client.chat_json(
                [
                    {"role": "system", "content": _system_prompt(modality_guidance)},
                    {
                        "role": "user",
                        "content": self._build_user_message(
                            blank_text, student_text, answer_key_text
                        ),
                    },
                ],
                temperature=0.1,
            )
        except Exception:
            _log.warning(
                "claude_parsing_agent: chat request failed model=%s",
                self.model_label,
                exc_info=True,
            )
            return None
        try:
            return ParsedAssignmentUnits.model_validate(raw)
        except ValidationError:
            _log.warning(
                "claude_parsing_agent: model response failed schema validation model=%s",
                self.model_label,
                exc_info=True,
            )
            return None

    @staticmethod
    def _build_user_message(blank_text: str, student_text: str, answer_key_text: str) -> str:
        parts = ["### STUDENT_SUBMISSION\n\n" + student_text]
        if blank_text.strip():
            parts.insert(0, "### BLANK_ASSIGNMENT\n\n" + blank_text)
        if answer_key_text.strip():
            parts.append("### ANSWER_KEY_OR_REFERENCE\n\n" + answer_key_text)
        return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Config / enablement
# ---------------------------------------------------------------------------


def claude_parsing_agent_enabled(cfg: Any) -> bool:
    """``off`` disables; default ``auto`` (and ``on``) require ``ANTHROPIC_API_KEY``."""
    if cfg is None:
        return False
    key = (getattr(cfg, "ANTHROPIC_API_KEY", "") or "").strip()
    if not key:
        return False
    mode = str(getattr(cfg, "MULTIMODAL_CLAUDE_PARSING_AGENT", "auto") or "auto").strip().lower()
    return mode in ("on", "auto")


def _claude_parsing_agent_client(cfg: Any) -> tuple[AnthropicJsonClient, str] | None:
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


def _max_chars_per_source(cfg: Any) -> int:
    try:
        n = int(
            getattr(cfg, "MULTIMODAL_CLAUDE_PARSING_AGENT_MAX_CHARS_PER_SOURCE", 0) or 0
        )
        if n > 0:
            return max(8_000, min(n, 500_000))
    except (TypeError, ValueError):
        pass
    return 120_000


# ---------------------------------------------------------------------------
# Source text gathering
# ---------------------------------------------------------------------------


def _truncate(text: str, cap: int) -> str:
    t = (text or "").strip()
    if len(t) <= cap:
        return t
    return t[:cap] + "\n...[truncated]"


def _blank_template_text(hints: dict[str, Any], cap: int) -> str:
    raw_tpl = hints.get("blank_assignment_template_bytes")
    raw_nb = hints.get("blank_assignment_ipynb_bytes")
    data: bytes | bytearray | None = (
        raw_tpl if isinstance(raw_tpl, (bytes, bytearray)) and raw_tpl else raw_nb
    )
    if not isinstance(data, (bytes, bytearray)) or not bytes(data).strip():
        return ""
    suffix = str(hints.get("blank_assignment_template_suffix") or "").strip() or ".ipynb"
    return _truncate(bytes_with_suffix_to_plain(bytes(data), suffix), cap)


def _audio_half_split_transcript_text(hints: dict[str, Any]) -> str:
    """
    Prefer the transcript :func:`app.grading.parsing.audio_half_split.maybe_prepare_audio_half_split`
    already computed (it runs earlier in ``pipeline.py``) over re-transcribing the same raw audio
    bytes a second time here.
    """
    half_split = hints.get("audio_half_split")
    if not isinstance(half_split, dict) or not half_split.get("enabled"):
        return ""
    transcripts = half_split.get("transcripts") or []
    if not isinstance(transcripts, list):
        return ""
    return "\n\n".join(str(t).strip() for t in transcripts if str(t or "").strip()).strip()


def _student_submission_text(envelope: IngestionEnvelope, cap: int) -> str:
    hints = envelope.modality_hints or {}
    half_split_text = _audio_half_split_transcript_text(hints)
    if half_split_text:
        return _truncate(half_split_text, cap)
    arts = envelope.artifacts or {}
    bmap = {
        str(k): bytes(v) for k, v in arts.items() if isinstance(v, (bytes, bytearray)) and v
    }
    text = artifacts_to_concatenated_plain(bmap).strip() if bmap else ""
    if not text:
        text = (envelope.extracted_plaintext or "").strip()
    return _truncate(text, cap)


# Markers ``app.grading.parsing.tools.transcribe_submission_media_bytes`` returns in place of a
# real transcript when Whisper is unavailable, the file is too large, or the call fails.
_TRANSCRIPT_FAILURE_MARKERS: tuple[str, ...] = (
    "[WHISPER_EMPTY_TRANSCRIPT]",
    "[AUDIO_TOO_LARGE_FOR_WHISPER_API]",
    "[WHISPER_TRANSCRIPTION_FAILED:",
)


def _is_unusable_transcript(text: str) -> bool:
    """True when ``text`` is essentially just a Whisper failure/placeholder marker (see
    :data:`_TRANSCRIPT_FAILURE_MARKERS`) with no other real content worth sending to Claude."""
    t = (text or "").strip()
    if not t:
        return True
    if not any(marker in t for marker in _TRANSCRIPT_FAILURE_MARKERS):
        return False
    residual = t
    for marker in _TRANSCRIPT_FAILURE_MARKERS:
        residual = residual.replace(marker, "")
    residual = residual.replace("=== AUDIO TRANSCRIPT ===", "").strip()
    return len(residual) < 20


def _answer_key_text(hints: dict[str, Any], answer_key_plaintext: str, cap: int) -> str:
    ak = (answer_key_plaintext or str(hints.get("answer_key_plaintext") or "")).strip()
    return _truncate(ak, cap)


def _modality_for_units(envelope: IngestionEnvelope, hints: dict[str, Any]) -> Modality:
    raw = str(hints.get("modality") or "").strip().lower()
    if raw:
        for m in Modality:
            if m.value == raw:
                return m
    arts = envelope.artifacts or {}
    bmap = {
        str(k): bytes(v) for k, v in arts.items() if isinstance(v, (bytes, bytearray)) and v
    }
    if bmap:
        try:
            return Modality(infer_modality_from_artifact_keys(bmap))
        except ValueError:
            pass
    return modality_from_hints(hints)


# ---------------------------------------------------------------------------
# Pydantic unit -> GradingChunk
# ---------------------------------------------------------------------------


def _safe_question_id(raw: str, index: int) -> str:
    s = re.sub(r"\s+", " ", (raw or "").strip())
    return s[:120] if s else f"q{index + 1}"


def _unit_to_grading_chunk(
    unit: ParsedQuestionUnit,
    index: int,
    *,
    envelope: IngestionEnvelope,
    modality: Modality,
    task_type: TaskType,
    model_label: str,
) -> GradingChunk | None:
    q = unit.question.strip()
    sr = unit.student_response.strip()
    if not q and not sr:
        return None
    ans = unit.answer.strip()
    ic = unit.instructor_context.strip()
    qid = _safe_question_id(unit.question_id, index)
    extracted = "\n\n".join(p for p in (q, ic, sr) if p).strip()
    return GradingChunk(
        chunk_id=f"{envelope.student_id}:{envelope.assignment_id}:claude_parsing_agent:{index}:{qid}",
        assignment_id=envelope.assignment_id,
        student_id=envelope.student_id,
        question_id=qid,
        modality=modality,
        task_type=task_type,
        extracted_text=extracted,
        evidence={
            "chunker": "claude_parsing_agent",
            "question_id": qid,
            "question_text": q,
            "response_preview": sr,
            "trio": {
                "question": q,
                "student_response": sr,
                "answer_key_segment": ans,
                "instructor_context": ic,
            },
            "_claude_parsing_agent": True,
            "claude_parsing_agent_model": model_label,
        },
    )


# ---------------------------------------------------------------------------
# Entry point (mirrors the other ``try_build_*_chunks`` chunkers' calling convention)
# ---------------------------------------------------------------------------


def try_build_claude_parsing_agent_chunks(
    envelope: IngestionEnvelope,
    cfg: Any,
    *,
    answer_key_plaintext: str = "",
) -> tuple[list[GradingChunk], str] | None:
    """
    Return ``(chunks, "claude_parsing_agent")``, or ``None`` — never raises — when disabled,
    misconfigured, the student submission is empty/too short, the API call fails, or the
    response fails Pydantic validation or yields no usable units.
    """
    if not claude_parsing_agent_enabled(cfg):
        return None
    pair = _claude_parsing_agent_client(cfg)
    if pair is None:
        return None
    client, model_label = pair

    hints = envelope.modality_hints or {}
    cap = _max_chars_per_source(cfg)
    student_text = _student_submission_text(envelope, cap)
    if len(student_text.strip()) < 8 or _is_unusable_transcript(student_text):
        _log.info(
            "claude_parsing_agent: student submission plaintext too short/unusable "
            "(e.g. empty or failed audio transcript); skipping"
        )
        return None
    blank_text = _blank_template_text(hints, cap)
    answer_key_text = _answer_key_text(hints, answer_key_plaintext, cap)

    modality = _modality_for_units(envelope, hints)
    modality_guidance = _modality_guidance(modality, hints)
    if modality == Modality.UNKNOWN:
        modality = Modality.MIXED
    task_type = task_type_from_hints(hints)

    agent = ClaudeAssignmentParsingAgent(client, model_label)
    parsed = agent.parse(
        blank_text=blank_text,
        student_text=student_text,
        answer_key_text=answer_key_text,
        modality_guidance=modality_guidance,
    )
    if parsed is None or not parsed.units:
        return None

    max_units: int | None = None
    cap_u = hints.get("max_grading_units")
    if cap_u is not None:
        try:
            max_units = int(cap_u)
        except (TypeError, ValueError):
            max_units = None

    chunks: list[GradingChunk] = []
    for i, unit in enumerate(parsed.units):
        if max_units is not None and max_units >= 1 and len(chunks) >= max_units:
            break
        ch = _unit_to_grading_chunk(
            unit,
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
        "claude_parsing_agent: model=%s units_in=%d chunks_out=%d",
        model_label,
        len(parsed.units),
        len(chunks),
    )
    return chunks, "claude_parsing_agent"
