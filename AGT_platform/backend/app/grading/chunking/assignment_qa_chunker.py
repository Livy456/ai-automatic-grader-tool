"""
Assignment Q&A chunking agent: a single, Pydantic-validated LLM call that decomposes an
instructor's blank assignment template + answer key into isolated question/answer pairs — no
student submission involved. Used by the "Assignment Creation" flow (see
``app.routes.assignment_library``) to seed an editable question bank right after a blank
template + answer key are uploaded.

This is a sibling of :mod:`app.grading.chunking.claude_parsing_agent` (which pairs a
*student's* work with the blank template + answer key for grading); this module only needs
the instructor-facing sources, so its schema and prompt are simpler — ``question`` + ``answer``
only, no ``student_response`` / ``instructor_context``.

Enable / configure
-------------------
Reuses the same ``ANTHROPIC_API_KEY`` / model / max-tokens settings as
:mod:`app.grading.chunking.claude_parsing_agent` (``MULTIMODAL_CLAUDE_PARSING_AGENT_MODEL`` /
``MULTIMODAL_CLAUDE_PARSING_AGENT_MAX_TOKENS``) rather than introducing a parallel set of
config knobs for what is, functionally, the same kind of assignment-decomposition call.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.llm.llm_router import AnthropicJsonClient

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic response schema
# ---------------------------------------------------------------------------


class AssignmentQAPair(BaseModel):
    """One isolated, gradable question/answer pair."""

    question_id: str = Field(
        default="",
        description="Stable id/number for this question, e.g. '1.2', 'q3', or a short slug.",
    )
    question: str = Field(
        default="",
        description="The instructor's prompt for this question only — never another question's.",
    )
    answer: str = Field(
        default="",
        description=(
            "The reference/expected answer for this question, taken from the answer key. "
            "Empty string when no matching reference is available."
        ),
    )

    @field_validator("question_id", "question", "answer", mode="before")
    @classmethod
    def _none_to_empty_string(cls, value: object) -> str:
        return "" if value is None else str(value)


class ParsedAssignmentQAPairs(BaseModel):
    """Top-level chunking-agent response: the assignment decomposed into Q&A pairs."""

    pairs: list[AssignmentQAPair] = Field(default_factory=list)


def _response_schema_json() -> str:
    return json.dumps(ParsedAssignmentQAPairs.model_json_schema(), indent=2)


def _system_prompt() -> str:
    return (
        "You are a precise assignment-chunking agent for an automated grading pipeline.\n\n"
        "You are given an instructor's BLANK_ASSIGNMENT template and (when available) an "
        "ANSWER_KEY. Decompose the assignment into isolated, gradable question/answer pairs. "
        "Return only a single JSON object matching exactly this schema (no markdown fences, "
        "no commentary, no extra keys):\n\n"
        f"{_response_schema_json()}\n\n"
        "Strict rules:\n"
        "- One pair per distinct gradable question/part. Never merge two questions into one "
        "pair, and never split one question into two pairs.\n"
        "- `question` must contain ONLY that question's own prompt text - never another "
        "question's prompt, never instructor setup/test code, never generic assignment "
        "boilerplate.\n"
        "- `answer` is the reference/expected answer for this exact question from the answer "
        "key; use an empty string when no matching reference exists - never invent one.\n"
        "- Prefer verbatim excerpts over paraphrasing; never invent requirements or answers.\n"
        "- Preserve the assignment's own ordering and numbering in `question_id` when visible; "
        "otherwise use `q1`, `q2`, ... in document order.\n"
        "- Use empty strings for unknown/absent fields - never null and never omit a field."
    )


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class AssignmentQAChunkingAgent:
    """
    Sends one Anthropic Messages call and validates the JSON response against
    :class:`ParsedAssignmentQAPairs`. Never raises: any API or validation failure is logged and
    surfaced as ``None`` so callers fall back to an empty chunk list (the teacher can still add
    questions by hand on the review page).
    """

    def __init__(self, client: AnthropicJsonClient, model_label: str) -> None:
        self._client = client
        self.model_label = model_label

    def chunk(self, *, blank_text: str, answer_key_text: str) -> ParsedAssignmentQAPairs | None:
        if not blank_text.strip() and not answer_key_text.strip():
            return None
        try:
            raw = self._client.chat_json(
                [
                    {"role": "system", "content": _system_prompt()},
                    {
                        "role": "user",
                        "content": self._build_user_message(blank_text, answer_key_text),
                    },
                ],
                temperature=0.1,
            )
        except Exception:
            _log.warning(
                "assignment_qa_chunker: chat request failed model=%s",
                self.model_label,
                exc_info=True,
            )
            return None
        try:
            return ParsedAssignmentQAPairs.model_validate(raw)
        except ValidationError:
            _log.warning(
                "assignment_qa_chunker: response failed schema validation model=%s",
                self.model_label,
                exc_info=True,
            )
            return None

    @staticmethod
    def _build_user_message(blank_text: str, answer_key_text: str) -> str:
        parts = ["### BLANK_ASSIGNMENT\n\n" + blank_text] if blank_text.strip() else []
        if answer_key_text.strip():
            parts.append("### ANSWER_KEY\n\n" + answer_key_text)
        return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Config / enablement + entry point
# ---------------------------------------------------------------------------


def assignment_qa_chunker_enabled(cfg: Any) -> bool:
    """Requires ``ANTHROPIC_API_KEY`` — same gating as ``claude_parsing_agent``."""
    return bool((getattr(cfg, "ANTHROPIC_API_KEY", "") or "").strip())


def build_assignment_qa_chunker(cfg: Any) -> AssignmentQAChunkingAgent | None:
    """Construct the agent from ``cfg``, or ``None`` when ``ANTHROPIC_API_KEY`` is unset."""
    if not assignment_qa_chunker_enabled(cfg):
        return None
    key = (getattr(cfg, "ANTHROPIC_API_KEY", "") or "").strip()
    model = (
        getattr(cfg, "MULTIMODAL_CLAUDE_PARSING_AGENT_MODEL", "") or ""
    ).strip() or "claude-opus-4-7"
    try:
        max_tokens = int(
            getattr(cfg, "MULTIMODAL_CLAUDE_PARSING_AGENT_MAX_TOKENS", 16384) or 16384
        )
    except (TypeError, ValueError):
        max_tokens = 16384
    client = AnthropicJsonClient(key, model, max_tokens=max_tokens)
    return AssignmentQAChunkingAgent(client, f"anthropic:{model}")


def try_chunk_assignment_qa_pairs(
    *, blank_text: str, answer_key_text: str, cfg: Any
) -> list[dict[str, str]] | None:
    """
    Entry point used by ``app.routes.assignment_library``. Return a list of
    ``{"question_id", "question", "answer"}`` dicts in document order, or ``None`` when the
    agent is disabled/misconfigured, the API call fails, or the response fails schema
    validation / yields no usable pairs.
    """
    agent = build_assignment_qa_chunker(cfg)
    if agent is None:
        return None
    parsed = agent.chunk(blank_text=blank_text, answer_key_text=answer_key_text)
    if parsed is None or not parsed.pairs:
        return None

    out: list[dict[str, str]] = []
    for i, pair in enumerate(parsed.pairs):
        q = pair.question.strip()
        a = pair.answer.strip()
        if not q and not a:
            continue
        qid = pair.question_id.strip() or f"q{i + 1}"
        out.append({"question_id": qid[:120], "question": q, "answer": a})

    if not out:
        return None
    _log.info(
        "assignment_qa_chunker: model=%s pairs_in=%d pairs_out=%d",
        agent.model_label,
        len(parsed.pairs),
        len(out),
    )
    return out
