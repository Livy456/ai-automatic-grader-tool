"""
Assignment Creation rubric routing agent: given an assignment rubric and one or more parsed
question/answer pairs, selects which rubric criteria apply to each question.

Runs during ``app.routes.assignment_library`` finalize, immediately after the parsing +
chunking agents seed ``AssignmentQuestionChunk`` rows.

Criterion rows are indexed by stable IDs for the LLM call (explicit ``id`` / ``criterion_id``
on the rubric JSON when present, otherwise ``crit_N`` / ``secN_critM``). The selected full
criterion dicts (including ``id``) are persisted as JSON on
``AssignmentQuestionChunk.rubric_criteria``.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.grading.schemas import GradingChunk, RubricType
from app.llm.llm_router import AnthropicJsonClient

_log = logging.getLogger(__name__)


def _max_points_from_range(points_range: object) -> float:
    if points_range is None:
        return 10.0
    s = str(points_range).strip().replace(" ", "")
    if "-" in s:
        parts = s.split("-", 1)
        try:
            return float(parts[1])
        except (IndexError, ValueError):
            pass
    try:
        return float(s)
    except ValueError:
        return 10.0


# ---------------------------------------------------------------------------
# Pydantic response schema
# ---------------------------------------------------------------------------


class QuestionRubricRoute(BaseModel):
    """Rubric criteria selected for one parsed question."""

    question_id: str = Field(
        default="",
        description="Must match the question_id from the input list.",
    )
    criterion_ids: list[str] = Field(
        default_factory=list,
        description="Exact criterion IDs copied from the provided rubric index.",
    )
    routing_reason: str = Field(
        default="",
        description="Brief explanation of why these criteria apply to this question.",
    )

    @field_validator("question_id", "routing_reason", mode="before")
    @classmethod
    def _none_to_empty_string(cls, value: object) -> str:
        return "" if value is None else str(value)

    @field_validator("criterion_ids", mode="before")
    @classmethod
    def _coerce_ids(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(x).strip() for x in value if str(x).strip()]


class AssignmentRubricRoutingResponse(BaseModel):
    """Top-level routing-agent response: one entry per input question."""

    routes: list[QuestionRubricRoute] = Field(default_factory=list)


def _response_schema_json() -> str:
    return json.dumps(AssignmentRubricRoutingResponse.model_json_schema(), indent=2)


def _system_prompt() -> str:
    return (
        "You are a precise rubric-routing agent for an automated grading pipeline.\n\n"
        "You are given an assignment RUBRIC (indexed by stable criterion IDs) and a list of "
        "QUESTIONS (each with question_id, question text, and optional reference answer). For "
        "each question, select which rubric criteria apply based on what the question asks the "
        "student to do.\n\n"
        "Return only a single JSON object matching exactly this schema (no markdown fences, "
        "no commentary, no extra keys):\n\n"
        f"{_response_schema_json()}\n\n"
        "Strict rules:\n"
        "- Emit exactly one `routes` entry per input question, in the same order, with matching "
        "`question_id` values.\n"
        "- `criterion_ids` must be a **non-empty** subset of the allowed criterion IDs provided "
        "in the user message — copy IDs exactly.\n"
        "- Every question must receive at least one criterion ID.\n"
        "- Prefer a tight, relevant set; omit criteria clearly not evidenced by the question.\n"
        "- When a question spans multiple skill areas, include every criterion that genuinely "
        "applies.\n"
        "- Use empty strings for unknown/absent optional fields — never null and never omit a "
        "required field."
    )


# ---------------------------------------------------------------------------
# Rubric criterion index (ID → row)
# ---------------------------------------------------------------------------


def _rubric_has_content(rubric: Any) -> bool:
    if rubric is None:
        return False
    if isinstance(rubric, list):
        return bool(rubric)
    if isinstance(rubric, dict):
        for key in ("sections", "criteria", "items", "rubric"):
            val = rubric.get(key)
            if isinstance(val, list) and val:
                return True
        return bool(rubric)
    return False


def _explicit_criterion_id(raw: dict[str, Any]) -> str:
    for key in ("id", "criterion_id", "uuid"):
        val = raw.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def _row_from_raw_criterion(
    raw: dict[str, Any],
    *,
    criterion_id: str,
    display_name: str | None = None,
) -> dict[str, Any]:
    name = (display_name or str(raw.get("name") or raw.get("criterion") or "Criterion")).strip()
    if raw.get("points_range") is not None or isinstance(raw.get("levels"), dict):
        max_pts = _max_points_from_range(raw.get("points_range"))
        levels = raw.get("levels")
        if isinstance(levels, dict) and levels:
            # Human-readable score bands for the review page (highest score first).
            def _level_key(item: tuple[Any, Any]) -> float:
                try:
                    return -float(item[0])
                except (TypeError, ValueError):
                    return 0.0

            desc = "\n".join(
                f"{k} pts: {v}" for k, v in sorted(levels.items(), key=_level_key)
            )[:8000]
        else:
            desc = ""
    else:
        try:
            max_pts = float(
                raw.get("max_points")
                if raw.get("max_points") is not None
                else (raw.get("max_score") if raw.get("max_score") is not None else 10.0)
            )
        except (TypeError, ValueError):
            max_pts = 10.0
        desc = str(raw.get("description") or "")[:8000]
    return {
        "id": criterion_id,
        "name": name,
        "max_points": max_pts,
        "criterion": name,
        "max_score": max_pts,
        "description": desc,
    }


def build_rubric_criterion_index(
    rubric: Any,
    *,
    rubric_text: str = "",
) -> dict[str, dict[str, Any]]:
    """
    Build a lookup map ``{criterion_id: grader_row}`` from ``Assignment.rubric`` and/or pasted
    rubric prose.
    """
    index: dict[str, dict[str, Any]] = {}

    if _rubric_has_content(rubric):
        if isinstance(rubric, dict) and isinstance(rubric.get("sections"), list):
            for si, sec in enumerate(rubric["sections"]):
                if not isinstance(sec, dict):
                    continue
                sec_name = str(sec.get("name") or "").strip()
                for ci, raw in enumerate(sec.get("criteria") or []):
                    if not isinstance(raw, dict):
                        continue
                    cid = _explicit_criterion_id(raw) or f"sec{si}_crit{ci}"
                    cname = str(raw.get("name") or raw.get("criterion") or "Criterion").strip()
                    label = f"{sec_name} — {cname}" if sec_name else cname
                    index[cid] = _row_from_raw_criterion(raw, criterion_id=cid, display_name=label)
        else:
            raw_list: list[dict[str, Any]] = []
            if isinstance(rubric, list):
                raw_list = [x for x in rubric if isinstance(x, dict)]
            elif isinstance(rubric, dict):
                for key in ("criteria", "items", "rubric"):
                    chunk = rubric.get(key)
                    if isinstance(chunk, list):
                        raw_list = [x for x in chunk if isinstance(x, dict)]
                        break
            for i, raw in enumerate(raw_list):
                cid = _explicit_criterion_id(raw) or f"crit_{i}"
                name = str(raw.get("name") or raw.get("criterion") or "Criterion").strip()
                index[cid] = _row_from_raw_criterion(raw, criterion_id=cid, display_name=name)

    if index:
        return index

    prose = (rubric_text or "").strip()
    if not prose:
        return {}

    return {
        "prose_0": {
            "id": "prose_0",
            "name": "Rubric (full text)",
            "criterion": "Rubric (full text)",
            "max_score": 0.0,
            "max_points": 0.0,
            "description": prose[:12000],
        }
    }


def extract_rubric_criteria(
    rubric: Any,
    *,
    rubric_text: str = "",
) -> list[dict[str, Any]]:
    """Flat criterion rows (each includes ``id``) for the routing agent."""
    return list(build_rubric_criterion_index(rubric, rubric_text=rubric_text).values())


def lookup_rubric_criteria_by_ids(
    rubric: Any,
    criterion_ids: list[str],
    *,
    rubric_text: str = "",
) -> list[dict[str, Any]]:
    """Resolve persisted criterion IDs back to full grader rows."""
    if not criterion_ids:
        return []
    index = build_rubric_criterion_index(rubric, rubric_text=rubric_text)
    out: list[dict[str, Any]] = []
    for cid in criterion_ids:
        row = index.get(str(cid).strip())
        if row:
            out.append(dict(row))
    return out


def normalize_chunk_rubric_criteria(
    raw: Any,
    *,
    rubric: Any,
    rubric_text: str = "",
) -> list[dict[str, Any]]:
    """
    Coerce stored ``AssignmentQuestionChunk.rubric_criteria`` into display/grade-ready rows.

    Accepts either full criterion dicts or legacy ID-string lists (``["crit_0", ...]``).
    """
    if not isinstance(raw, list) or not raw:
        return []

    if all(isinstance(x, str) for x in raw):
        return lookup_rubric_criteria_by_ids(
            rubric, [str(x).strip() for x in raw if str(x).strip()], rubric_text=rubric_text
        )

    if not all(isinstance(x, dict) for x in raw):
        return []

    rows = [dict(x) for x in raw]
    # Already have human-readable names — keep as-is.
    if any(str(r.get("name") or r.get("criterion") or "").strip() for r in rows):
        # Fill missing names from the assignment rubric when only an id is present.
        index = build_rubric_criterion_index(rubric, rubric_text=rubric_text)
        out: list[dict[str, Any]] = []
        for r in rows:
            name = str(r.get("name") or r.get("criterion") or "").strip()
            if name and name != "Criterion":
                out.append(r)
                continue
            cid = str(r.get("id") or "").strip()
            resolved = index.get(cid) if cid else None
            out.append(dict(resolved) if resolved else r)
        return out

    ids = [str(r.get("id") or "").strip() for r in rows if str(r.get("id") or "").strip()]
    return lookup_rubric_criteria_by_ids(rubric, ids, rubric_text=rubric_text) if ids else rows


def _default_criterion_id(index: dict[str, dict[str, Any]]) -> str:
    return sorted(index.keys())[0]


def _ensure_at_least_one_id(
    picked: list[str],
    *,
    index: dict[str, dict[str, Any]],
) -> list[str]:
    valid = [cid for cid in picked if cid in index]
    if valid:
        return valid
    if index:
        return [_default_criterion_id(index)]
    return []


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class AssignmentRubricRoutingAgent:
    """
    One Anthropic Messages call that routes rubric criteria to each parsed question.
    Never raises: failures are logged and surfaced as ``None``.
    """

    def __init__(self, client: AnthropicJsonClient, model_label: str) -> None:
        self._client = client
        self.model_label = model_label

    def route(
        self,
        *,
        criteria_rows: list[dict[str, Any]],
        questions: list[dict[str, str]],
        rubric_text: str = "",
    ) -> AssignmentRubricRoutingResponse | None:
        if not questions:
            return None
        allowed_ids = [
            str(r.get("id") or "").strip() for r in criteria_rows if str(r.get("id") or "").strip()
        ]
        if not allowed_ids:
            return None

        try:
            raw = self._client.chat_json(
                [
                    {"role": "system", "content": _system_prompt()},
                    {
                        "role": "user",
                        "content": self._build_user_message(
                            criteria_rows=criteria_rows,
                            allowed_ids=allowed_ids,
                            questions=questions,
                            rubric_text=rubric_text,
                        ),
                    },
                ],
                temperature=0.1,
            )
        except Exception:
            _log.warning(
                "assignment_rubric_routing_agent: chat request failed model=%s",
                self.model_label,
                exc_info=True,
            )
            return None
        try:
            return AssignmentRubricRoutingResponse.model_validate(raw)
        except ValidationError:
            _log.warning(
                "assignment_rubric_routing_agent: response failed schema validation model=%s",
                self.model_label,
                exc_info=True,
            )
            return None

    @staticmethod
    def _build_user_message(
        *,
        criteria_rows: list[dict[str, Any]],
        allowed_ids: list[str],
        questions: list[dict[str, str]],
        rubric_text: str,
    ) -> str:
        rubric_block = json.dumps(
            [
                {
                    "id": str(r.get("id") or "").strip(),
                    "name": str(r.get("name") or r.get("criterion") or "").strip(),
                    "max_score": r.get("max_score", r.get("max_points")),
                    "description": str(r.get("description") or "")[:2000],
                }
                for r in criteria_rows
            ],
            ensure_ascii=False,
            indent=2,
        )
        questions_block = json.dumps(questions, ensure_ascii=False, indent=2)
        parts = [
            "### ALLOWED_CRITERION_IDS\n\n" + json.dumps(allowed_ids, ensure_ascii=False, indent=2),
            "### RUBRIC_CRITERIA\n\n" + rubric_block,
            "### QUESTIONS\n\n" + questions_block,
        ]
        prose = (rubric_text or "").strip()
        if prose:
            parts.append("### RUBRIC_PROSE\n\n" + prose[:12000])
        return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Grading pipeline integration
# ---------------------------------------------------------------------------


def _grading_row_from_saved_criterion(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a saved criterion dict into the shape the grading prompt / report expect."""
    name = str(raw.get("name") or raw.get("criterion") or "").strip()
    if not name or name == "Criterion":
        return None
    try:
        max_pts = float(
            raw.get("max_points")
            if raw.get("max_points") is not None
            else (raw.get("max_score") if raw.get("max_score") is not None else 0.0)
        )
    except (TypeError, ValueError):
        max_pts = 0.0
    out = dict(raw)
    out["name"] = name
    out["criterion"] = name
    out["max_points"] = max_pts
    out["max_score"] = max_pts
    if "description" not in out:
        out["description"] = str(raw.get("description") or "")
    return out


def apply_assignment_creation_rubric_routing(
    chunks: list[GradingChunk],
    *,
    criteria_by_question_id: dict[str, list[dict[str, Any]]],
) -> int:
    """
    Stamp pre-routed criterion rows from Assignment Creation onto grading chunks so
    :func:`app.grading.rubric_routing.rubric_router.route_rubric` keeps them.

    Matches by ``question_id`` first, then falls back to document order so every saved
    question still receives only its routed subset (never the full assignment rubric).

    Returns the number of chunks that received at least one criterion row.
    """
    if not chunks or not criteria_by_question_id:
        return 0

    # Preserve insertion order from AssignmentQuestionChunk.order_index (tasks.py).
    ordered_rows: list[list[dict[str, Any]]] = []
    by_qid: dict[str, list[dict[str, Any]]] = {}
    for qid, raw_rows in criteria_by_question_id.items():
        key = str(qid or "").strip()
        cleaned = [
            row
            for row in (
                _grading_row_from_saved_criterion(r)
                for r in (raw_rows or [])
                if isinstance(r, dict)
            )
            if row is not None
        ]
        if not cleaned:
            continue
        ordered_rows.append(cleaned)
        if key:
            by_qid[key] = cleaned

    if not by_qid and not ordered_rows:
        return 0

    # Prefer stable question_id matches. Only fall back to document order when *no*
    # chunk matched by id — partial matches must not steal another question's rows.
    by_index: list[list[dict[str, Any]] | None] = []
    id_hits = 0
    for ch in chunks:
        qid = str(ch.question_id or "").strip()
        rows = list(by_qid.get(qid) or []) if qid else []
        if rows:
            id_hits += 1
            by_index.append(rows)
        else:
            by_index.append(None)

    if id_hits == 0 and ordered_rows:
        by_index = [
            list(ordered_rows[i]) if i < len(ordered_rows) else None
            for i in range(len(chunks))
        ]

    applied = 0
    for ch, rows in zip(chunks, by_index, strict=True):
        if not rows:
            continue
        ch.rubric_rows = rows
        if ch.rubric_type is None:
            ch.rubric_type = RubricType.FREE_RESPONSE
        ch.routing_reason = "assignment_creation_rubric_routing"
        applied += 1
    return applied


# ---------------------------------------------------------------------------
# Config / enablement + entry points
# ---------------------------------------------------------------------------


def assignment_rubric_routing_agent_enabled(cfg: Any) -> bool:
    """Requires ``ANTHROPIC_API_KEY`` — same gating as the assignment Q&A chunker."""
    return bool((getattr(cfg, "ANTHROPIC_API_KEY", "") or "").strip())


def build_assignment_rubric_routing_agent(cfg: Any) -> AssignmentRubricRoutingAgent | None:
    if not assignment_rubric_routing_agent_enabled(cfg):
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
    return AssignmentRubricRoutingAgent(client, f"anthropic:{model}")


def try_route_rubric_for_question(
    *,
    question_id: str,
    question_text: str,
    answer_text: str,
    rubric: Any,
    rubric_text: str = "",
    cfg: Any,
) -> list[dict[str, Any]] | None:
    """Route rubric criteria for a single question. Returns criterion dicts or ``None``."""
    routed = try_route_rubric_for_questions(
        pairs=[
            {
                "question_id": question_id,
                "question": question_text,
                "answer": answer_text,
            }
        ],
        rubric=rubric,
        rubric_text=rubric_text,
        cfg=cfg,
    )
    if routed is None:
        return None
    return routed[0] if routed else None


def try_route_rubric_for_questions(
    *,
    pairs: list[dict[str, str]],
    rubric: Any,
    rubric_text: str = "",
    cfg: Any,
) -> list[list[dict[str, Any]]] | None:
    """
    Entry point for ``app.routes.assignment_library``. For each parsed Q&A pair, return the
    full rubric criterion rows that apply (same order as ``pairs``), or ``None`` when the
    agent is disabled, the rubric is empty, or the LLM call fails.
    """
    if not pairs:
        return None

    index = build_rubric_criterion_index(rubric, rubric_text=rubric_text)
    if not index:
        return None

    criteria_rows = list(index.values())
    agent = build_assignment_rubric_routing_agent(cfg)
    if agent is None:
        return None

    questions = [
        {
            "question_id": str(p.get("question_id") or f"q{i + 1}"),
            "question": str(p.get("question") or ""),
            "answer": str(p.get("answer") or ""),
        }
        for i, p in enumerate(pairs)
    ]

    parsed = agent.route(
        criteria_rows=criteria_rows,
        questions=questions,
        rubric_text=rubric_text,
    )
    if parsed is None or not parsed.routes:
        return None

    allowed_ids = set(index.keys())
    by_qid: dict[str, QuestionRubricRoute] = {}
    for route in parsed.routes:
        qid = route.question_id.strip()
        if qid:
            by_qid[qid] = route

    out: list[list[dict[str, Any]]] = []
    for i, q in enumerate(questions):
        qid = q["question_id"]
        route = by_qid.get(qid)
        if route is None and i < len(parsed.routes):
            route = parsed.routes[i]
        if route is None:
            ids = _ensure_at_least_one_id([], index=index)
        else:
            picked = [cid for cid in route.criterion_ids if cid in allowed_ids]
            ids = _ensure_at_least_one_id(picked, index=index)
        out.append([dict(index[cid]) for cid in ids if cid in index])

    if not any(out):
        return None

    _log.info(
        "assignment_rubric_routing_agent: model=%s questions_in=%d questions_routed=%d",
        agent.model_label,
        len(questions),
        sum(1 for rows in out if rows),
    )
    return out
