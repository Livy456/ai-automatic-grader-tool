"""
Compact .ipynb content for ``grade()`` JSON payloads without dropping Q/A structure.

- **Deterministic path**: Uses :func:`build_submission_chunks` so each logical
  question/response/code segment stays ordered with ``pair_id`` and ``chunk_index``.
  Per-chunk text is trimmed only to satisfy a byte budget; **no chunk is removed**.
- **Optional OpenAI path** (when ``OPENAI_API_KEY`` is set and digest mode allows it):
  asks ``OPENAI_MODEL`` to emit JSON ``qa_units`` mirroring prompts vs student work,
  with strict “do not omit student work; split long work across units” instructions.
  On failure or missing key, callers fall back to the deterministic chunks.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..config import Config
from .submission_chunks import build_submission_chunks
from .submission_text import submission_text_from_artifacts

_log = logging.getLogger(__name__)


def notebook_openai_digest_enabled(cfg: Config) -> bool:
    """
    ``NOTEBOOK_OPENAI_DIGEST`` env: ``auto`` (default), ``on``, ``off``.

    ``on`` → OpenAI chat reshapes the notebook export into ``qa_units`` (extra API cost).
    ``auto`` / ``off`` → use deterministic chunk packing only (still shrinks payload vs raw code).
    """
    if not (cfg.OPENAI_API_KEY or "").strip():
        return False
    raw = (cfg.NOTEBOOK_OPENAI_DIGEST or "auto").strip().lower()
    if raw in ("on", "true", "1", "yes"):
        return True
    return False


def _try_openai_notebook_qa(plain: str, cfg: Config, *, max_input_chars: int) -> list[dict[str, Any]] | None:
    try:
        from openai import OpenAI
    except ImportError:
        return None
    snippet = (plain or "")[:max_input_chars]
    if not snippet.strip():
        return None
    client = OpenAI(api_key=cfg.OPENAI_API_KEY)
    model = (cfg.OPENAI_MODEL or "gpt-4o-mini").strip()
    schema_hint = (
        '{"qa_units":[{"pair_id":null,"role":"question|response|code",'
        '"text":"verbatim or split student work; never empty if input had work"}]}'
    )
    user = (
        "Below is a plain-text export of a Jupyter notebook (may include "
        "'=== NOTEBOOK CODE (ipynb) ===' / MARKDOWN banners).\n\n"
        "Return ONLY valid JSON matching this shape (no markdown):\n"
        f"{schema_hint}\n\n"
        "Rules:\n"
        "- Preserve every assignment/prompt line as one or more qa_units with role question.\n"
        "- Preserve every student code block and markdown answer: role code or response.\n"
        "- Use the same integer pair_id to link a question to its answers when obvious; "
        "otherwise null.\n"
        "- Do NOT summarize or paraphrase student code or answers; copy text verbatim. "
        "If a segment exceeds 7000 characters, split it into multiple qa_units with the "
        "same pair_id and role, in order (part 1, part 2, ...).\n"
        "- Do not drop cells; if uncertain, include the text.\n\n"
        f"NOTEBOOK_EXPORT:\n{snippet}"
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=0.05,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": "You extract structured Q/A from notebook exports as JSON only.",
                },
                {"role": "user", "content": user},
            ],
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw)
        units = data.get("qa_units")
        if not isinstance(units, list) or not units:
            return None
        out: list[dict[str, Any]] = []
        for i, u in enumerate(units):
            if not isinstance(u, dict):
                continue
            text = str(u.get("text") or "").strip()
            if not text:
                continue
            out.append(
                {
                    "pair_id": u.get("pair_id"),
                    "role": str(u.get("role") or "response"),
                    "chunk_index": i,
                    "text": text,
                    "_source": "openai_digest",
                }
            )
        return out or None
    except Exception:
        _log.debug("OpenAI notebook digest failed; using chunk packing", exc_info=True)
        return None


def pack_notebook_qa_from_chunks(
    plain: str,
    cfg: Config,
    *,
    modality_subtype: str,
    assignment_title: str,
    budget_chars: int,
) -> list[dict[str, Any]]:
    """
    Build Q/A chunk list under ``budget_chars`` serialized JSON size.

    Shrinks per-chunk ``text`` only; **chunk count is preserved**.
    """
    budget_chars = max(4000, min(int(budget_chars or 28000), 200_000))
    cap = max(2000, min(12000, budget_chars // 3))

    def _once(max_chunk_chars: int) -> list[dict[str, Any]]:
        chunks = build_submission_chunks(
            plain,
            assignment_title=assignment_title or "",
            modality_subtype=modality_subtype or "notebook",
            max_chunk_chars=max_chunk_chars,
        )
        rows: list[dict[str, Any]] = []
        for c in chunks:
            rows.append(
                {
                    "pair_id": c.get("pair_id"),
                    "role": c.get("role"),
                    "chunk_index": c.get("chunk_index"),
                    "text": c.get("text") or "",
                    "_source": "chunks",
                }
            )
        return rows

    max_cap = cap
    rows = _once(max_cap)
    if not rows:
        return []

    suffix = "\n[... truncated in this chunk ...]"

    def _trim_rows(max_chunk_chars: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        lim = max(400, max_chunk_chars - len(suffix))
        for r in rows:
            t = str(r.get("text") or "")
            if len(t) > lim:
                t = t[:lim] + suffix
            out.append({**r, "text": t})
        return out

    packed = _trim_rows(max_cap)
    ser = json.dumps(packed)
    while len(ser) > budget_chars and max_cap > 600:
        max_cap = max(600, max_cap * 85 // 100)
        packed = _trim_rows(max_cap)
        ser = json.dumps(packed)
    return packed


def build_notebook_grader_overlay(
    artifacts_bytes: dict[str, bytes],
    cfg: Config,
    *,
    modality_subtype: str,
    assignment_title: str = "",
    budget_chars: int,
) -> dict[str, Any] | None:
    """
    Return artifact fields to merge into the grader context for .ipynb submissions.

    Sets compact ``notebook_qa`` and replaces raw ``code`` / ``markdown`` placeholders
    so the JSON payload is smaller while preserving ordered Q/A segments.
    """
    if "ipynb" not in artifacts_bytes:
        return None
    plain = submission_text_from_artifacts(artifacts_bytes).strip()
    if not plain:
        return None

    budget_chars = max(4000, min(int(budget_chars or 28000), 200_000))
    qa: list[dict[str, Any]] | None = None
    payload_source = "chunks"

    if notebook_openai_digest_enabled(cfg):
        max_in = max(20_000, min(len(plain), 120_000))
        qa = _try_openai_notebook_qa(plain, cfg, max_input_chars=max_in)
        if qa:
            ser_try = json.dumps(qa)
            if len(ser_try) <= budget_chars:
                payload_source = "openai_digest"
            else:
                _log.debug(
                    "OpenAI notebook digest over budget (%s > %s); using chunk packing",
                    len(ser_try),
                    budget_chars,
                )
                qa = None

    if not qa:
        qa = pack_notebook_qa_from_chunks(
            plain,
            cfg,
            modality_subtype=modality_subtype,
            assignment_title=assignment_title,
            budget_chars=budget_chars,
        )

    if not qa:
        return None

    stub = (
        "[Notebook raw code/markdown omitted — use `notebook_qa` list: ordered segments "
        "with role question|response|code and pair_id linking prompts to answers.]"
    )
    return {
        "notebook_qa": qa,
        "code": stub,
        "markdown": stub,
        "_notebook_grader_payload": payload_source,
    }
