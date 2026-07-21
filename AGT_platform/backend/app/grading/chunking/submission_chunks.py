"""
Split submission plain text into explicit **question** vs **answer** chunks for RAG and review.

Chunking strategy (high level)
-------------------------------

1. **Artifact sections** — If the text was produced by
   :func:`app.grading.parsing.submission_text.submission_text_from_artifacts`, it may contain
   banner lines such as ``=== NOTEBOOK CODE (ipynb) ===``. We split on those first so
   notebook *code* can be labeled ``role: \"code\"`` and prose/markdown as ``response``
   unless a line looks like source (see heuristics below).

2. **PDF vertical reflow (new chunking method)** — For ``=== PDF TEXT ===`` bodies,
   :func:`app.grading.parsing.tools.normalize_verticalized_pdf_text` runs **again** here so
   chunking stays correct even if plaintext bypassed :func:`~app.grading.parsing.tools.extract_text_from_pdf`.
   **Word (``=== DOCX ===``)** sections skip PDF reflow (paragraph structure is already normal);
   with a journal / free-response ``modality_subtype``, the same **journal ?-line** rules as PDF
   apply (see :func:`_prose_boundary_matches`).
   For PDF bodies, reflow also joins one-word-per-line extractor output and applies
   ``new_chunking_method.md`` continuation rules.

3. **Question / answer pairs (prose sections)** — Within each prose block we find
   *question-like* **single lines**:

   * **Structured headers** — ``Part 1.``, ``Question 2``, ``Q3:``, numbered items,
     ``## Heading`` (regex ``_CHUNK_HEADER``; ``Question`` must be numbered or ``Question:``,
     so the English word *questions* alone is not a header).
   * **Journal / free-response documents** — When ``modality_subtype`` suggests a journal-style
     submission (substring ``journal``, ``free_response``, ``reflection``, etc.) and the section
     is treated as structured prose (``pdf`` or ``docx`` in :func:`_infer_section_kind`), lines that look
     like full-sentence instructor prompts (start with ``What`` / ``Did`` / …, end with ``?``)
     are extra boundaries (regex ``_JOURNAL_INSTRUCTOR_PROMPT``).

   For each boundary line we emit ``role: \"question\"``; material until the next boundary
   becomes ``role: \"response\"`` (or ``code``) with the same ``trio_id``. A question is
   still emitted even when there is **no** content before the next boundary (e.g. an
   unfilled instructor template with back-to-back prompts, or a student who left a
   question blank) — it just gets no ``response``/``code`` row here (backfilled empty by
   :func:`_ensure_trio_answer_reference_rows`).

4. **Leading body before the first header** — ``role: \"response\"`` (or ``code``) with
   ``trio_id: null``.

5. **No headers found** — The whole section becomes ``response`` / ``code`` only.

6. **Trio export** — Each numbered ``trio_id`` also gets an ``answer/reference`` row (filled
   later from answer-key alignment / RAG). Export records use fields:
   ``role``, ``trio_id``, ``text``, ``chunk_index``, ``assignment_title``, ``modality_subtype``.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

from app.grading.parsing.tools import normalize_verticalized_pdf_text

_log = logging.getLogger(__name__)

# Banner lines from submission_text_from_artifacts: "=== LABEL (key) ===" or "=== LABEL ==="
_SECTION_BANNER = re.compile(
    r"(?m)^(===\s*.+?\s*===)\s*\n",
)

# One ``=== PDF TEXT ===`` block (body until the next ``===`` banner or EOF).
_PDF_TEXT_SECTION = re.compile(
    r"(?mis)^(===\s*PDF\s+TEXT\s*===)\s*\r?\n([\s\S]*?)(?=^[ \t]*===\s*.+?\s*===\s*$|\Z)",
)


def reflow_pdf_sections_in_plaintext(text: str) -> str:
    """
    Apply :func:`app.grading.parsing.tools.normalize_verticalized_pdf_text` to every
    ``=== PDF TEXT ===`` region. Used by the multimodal grading pipeline so PDF
    submissions are reflowed before LLM QA segmentation and before structured
    chunking (which also reflows per section as a second pass).
    """

    raw = text or ""
    if not raw.strip() or "PDF TEXT" not in raw.upper():
        return text or ""

    def _repl(m: re.Match[str]) -> str:
        banner, body = m.group(1), (m.group(2) or "").strip()
        if not body:
            return m.group(0)
        return f"{banner}\n{normalize_verticalized_pdf_text(body)}"

    return _PDF_TEXT_SECTION.sub(_repl, raw)

# Question / prompt lines (single line only; horizontal whitespace only after header key).
# NOTE: Avoid matching the common English word "questions" as "Question" + "s"
# (the older pattern did, which destroyed PDF journal Q/A pairing).
_CHUNK_HEADER = re.compile(
    r"(?m)^(?:"
    r"[ \t]*Part[ \t]+[\dA-Z][^\n]*|"
    r"[ \t]*Question(?:[ \t]+\d[\w.\-]*[^\n]*|\s*:[^\n]*)|"
    r"[ \t]*Q[ \t]*\d+[\w.\-]*[\.\):]?[ \t]*[^\n]*|"
    r"[ \t]*\d+[\.\)][ \t]+\S[^\n]*|"
    r"[ \t]*#{1,3}[ \t]+\S[^\n]*"
    r")\s*$",
    re.IGNORECASE,
)

# Journal / free-response PDFs: full-sentence instructor prompts often end with "?"
# without "Question 1" labels (after verticalized text has been reflowed). Students
# sometimes add their own light numbering (e.g. "1/ Which data ...?") in front of the
# instructor's sentence, so an optional leading item number is skipped before the
# keyword lookahead.
_JOURNAL_INSTRUCTOR_PROMPT = re.compile(
    r"(?m)^"
    r"(?:[ \t]*\d+[\.\)/][ \t]*)?"
    r"(?=(?:What|Which|Did|How|Why|Explain|List|Describe|Are|Is|If|Would|Could|"
    r"Should|Have|Was|Were|For|In|Can|May|Must|Shall|Who|When|Where)\b)"
    r"(?!I\b|We\b|My\b|It\b|The\b|Yes\b|No\b|This\b|That\b|Here\b|There\b)"
    r".{15,}\?\s*$",
    re.IGNORECASE,
)

# modality_subtype hints from resolve_modality_profile / LMS (see new_chunking_method.md)
_JOURNALISH_SUBSTRINGS = (
    "journal",
    "free_response",
    "reflection",
    "essay",
    "written",
    "short_answer",
)


def _notebook_markdown_body_is_instruction(ans_text: str) -> bool:
    """
    True when markdown after a short heading reads like instructor instructions
    (merge into question; real student work is in code cells).
    """
    s = (ans_text or "").strip()
    if len(s) < 50:
        return False
    if s.count("```") >= 2 and len(s) > 400:
        return False
    return bool(
        re.search(
            r"(?i)\b("
            r"hint:|make sure|you should|must include|use\s+the\s+built|documentation\s+for|"
            r"create\s+(a\s+)?new\s+dataframe|on\s+your\s+bar\s+chart|groupby\(|pandas\.|"
            r"matplotlib|seaborn|plt\.|write\s+code|complete\s+the\s+following"
            r")\b",
            s,
        )
    )


def _merge_notebook_markdown_question(
    *,
    q_line: str,
    ans_text: str,
    modality_subtype: str,
    section_banner: str,
) -> bool:
    st = (modality_subtype or "").lower()
    if st != "notebook":
        return False
    if "NOTEBOOK MARKDOWN" not in (section_banner or "").upper():
        return False
    if len((q_line or "").strip()) > 220:
        return False
    return _notebook_markdown_body_is_instruction(ans_text)


def trio_chunk_schema_record(ch: dict[str, Any]) -> dict[str, Any]:
    """Public trio-chunk JSON shape (six fields). ``pair_id`` is accepted as legacy alias."""
    tid = ch.get("trio_id")
    if tid is None and ch.get("pair_id") is not None:
        tid = ch.get("pair_id")
    return {
        "role": ch.get("role"),
        "trio_id": tid,
        "text": str(ch.get("text") or ""),
        "chunk_index": int(ch.get("chunk_index", 0)),
        "assignment_title": str(ch.get("assignment_title") or ""),
        "modality_subtype": str(ch.get("modality_subtype") or ""),
    }


def validate_trio_chunking_schema(chunks: Sequence[dict[str, Any]]) -> list[str]:
    """Return human-readable issues; empty means every ``trio_id`` has required roles."""
    errs: list[str] = []
    by_tid: dict[Any, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for i, c in enumerate(chunks):
        rec = trio_chunk_schema_record(dict(c))
        if rec["role"] is None or str(rec["role"]).strip() == "":
            errs.append(f"chunk[{i}]: missing role")
        tid = rec["trio_id"]
        if tid is None:
            continue
        by_tid[tid][str(rec["role"] or "")] += 1
    for tid, roles in sorted(by_tid.items(), key=lambda x: (x[0] is None, str(x[0]))):
        if tid is None:
            continue
        if not roles.get("answer/reference"):
            errs.append(f"trio_id={tid}: missing answer/reference row")
        if not roles.get("question"):
            errs.append(f"trio_id={tid}: missing question row")
        if not (roles.get("response") or roles.get("code")):
            errs.append(f"trio_id={tid}: missing response or code row")
    return errs


def _ensure_trio_answer_reference_rows(chunks: list[dict[str, Any]]) -> None:
    """Append missing ``answer/reference`` / ``response`` rows per ``trio_id``."""
    by_tid: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for c in chunks:
        tid = c.get("trio_id")
        if tid is None and c.get("pair_id") is not None:
            tid = c.get("pair_id")
            c["trio_id"] = tid
        if tid is None:
            continue
        by_tid[tid].append(c)
    for tid, group in by_tid.items():
        roles = {str(x.get("role") or "") for x in group}
        src = group[0]
        if "answer/reference" not in roles:
            chunks.append(
                {
                    "role": "answer/reference",
                    "trio_id": tid,
                    "text": "",
                    "chunk_index": 0,
                    "assignment_title": src.get("assignment_title", ""),
                    "modality_subtype": src.get("modality_subtype", ""),
                    "section_banner": src.get("section_banner"),
                }
            )
        if not (("response" in roles) or ("code" in roles)):
            chunks.append(
                {
                    "role": "response",
                    "trio_id": tid,
                    "text": "",
                    "chunk_index": 0,
                    "assignment_title": src.get("assignment_title", ""),
                    "modality_subtype": src.get("modality_subtype", ""),
                    "section_banner": src.get("section_banner"),
                }
            )


def _sort_and_renumber_chunk_indices(chunks: list[dict[str, Any]]) -> None:
    _order = {"question": 0, "response": 1, "code": 1, "answer/reference": 2}

    def sk(c: dict[str, Any]) -> tuple[int, int, int]:
        tid = c.get("trio_id")
        if tid is None:
            return (1 << 30, 9, int(c.get("chunk_index", 0)))
        return (int(tid), _order.get(str(c.get("role")), 5), int(c.get("chunk_index", 0)))

    chunks.sort(key=sk)
    for i, c in enumerate(chunks):
        c["chunk_index"] = i


def _pdf_uses_journal_style_prompts(modality_subtype: str) -> bool:
    st = (modality_subtype or "").lower()
    return any(s in st for s in _JOURNALISH_SUBSTRINGS)


def _prose_boundary_matches(
    body: str,
    *,
    section_kind: str,
    modality_subtype: str,
) -> list[re.Match[str]]:
    """Collect non-overlapping header matches (structured + journal-style)."""
    spans: list[tuple[int, int, re.Match[str]]] = []
    for m in _CHUNK_HEADER.finditer(body):
        spans.append((m.start(), m.end(), m))
    if section_kind in ("pdf", "docx") and _pdf_uses_journal_style_prompts(
        modality_subtype
    ):
        for m in _JOURNAL_INSTRUCTOR_PROMPT.finditer(body):
            spans.append((m.start(), m.end(), m))
    spans.sort(key=lambda x: x[0])
    out: list[re.Match[str]] = []
    prev_end = -1
    for start, end, m in spans:
        if start < prev_end:
            continue
        out.append(m)
        prev_end = end
    return out

# Lightweight "is this Python/code?" heuristic for classifying answer bodies.
_CODE_LINE_PATTERNS = (
    re.compile(r"^\s*(def |class |import |from .+ import |if __name__)"),
    re.compile(r"^\s*[{}\]\);]\s*$"),
    re.compile(r"^\s*#|^\s*//"),
    re.compile(r"=\s*\[|=\s*\{"),
)


def _infer_section_kind(banner: str, modality_subtype: str) -> str:
    u = banner.upper()
    if "NOTEBOOK CODE" in u or "PYTHON SOURCE" in u:
        return "code"
    if "AUDIO TRANSCRIPT" in u or "VIDEO / AUDIO" in u:
        return "body"
    if "NOTEBOOK MARKDOWN" in u or ".MD" in u or "MARKDOWN" in u:
        return "markdown"
    if "PDF TEXT" in u:
        return "pdf"
    if "DOCX" in u:
        return "docx"
    if "TXT" in u and "NOTEBOOK" not in u:
        return "txt"
    if modality_subtype == "notebook":
        return "markdown"
    if modality_subtype in ("code", "mixed_notebook_pdf"):
        return "mixed"
    return "body"


def _split_artifact_sections(
    text: str, *, modality_subtype: str = ""
) -> list[tuple[str, str, str]]:
    """
    Split ``text`` into ``(section_banner, section_kind, body)`` tuples.
    If there are no ``===`` banners, returns one row ``("", inferred_kind, text)``.
    """
    raw = text or ""
    if not raw.strip():
        return []

    matches = list(_SECTION_BANNER.finditer(raw))
    if not matches:
        return [("", "body", raw.strip())]

    out: list[tuple[str, str, str]] = []
    for i, m in enumerate(matches):
        banner = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        body = raw[start:end].strip()
        kind = _infer_section_kind(banner, modality_subtype)
        out.append((banner, kind, body))
    return out


def _looks_like_code(text: str, *, section_kind: str) -> bool:
    if section_kind == "code":
        return True
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    if not lines:
        return False
    hits = 0
    for ln in lines[: min(40, len(lines))]:
        for pat in _CODE_LINE_PATTERNS:
            if pat.search(ln):
                hits += 1
                break
    # Many short lines with '=' assignments / brackets
    if len(lines) >= 3 and hits >= max(2, len(lines) // 5):
        return True
    return hits >= 3


def _answer_role_for_body(
    body: str,
    *,
    section_kind: str,
    modality_subtype: str,
) -> str:
    if section_kind == "code":
        return "code"
    if _looks_like_code(body, section_kind=section_kind):
        return "code"
    st = (modality_subtype or "").lower()
    if st in ("notebook", "mixed_notebook_pdf") and section_kind == "markdown":
        # Markdown cells can still contain fenced code blocks; long fenced blocks heuristic
        if body.count("```") >= 2 and len(body) > 200:
            return "code"
    return "response"


def _pack_text_by_paragraphs(
    text: str,
    *,
    role: str,
    trio_id: int | None,
    max_chunk_chars: int | None,
    assignment_title: str,
    modality_subtype: str,
    chunk_index_start: int,
    section_banner: str,
) -> tuple[list[dict[str, Any]], int]:
    text = text.strip()
    if not text:
        return [], chunk_index_start
    out: list[dict[str, Any]] = []
    idx = chunk_index_start

    def emit_piece(piece: str) -> None:
        nonlocal idx
        out.append(
            {
                "role": role,
                "trio_id": trio_id,
                "text": piece,
                "chunk_index": idx,
                "assignment_title": assignment_title,
                "modality_subtype": modality_subtype,
                "section_banner": section_banner or None,
            }
        )
        idx += 1

    # None or non-positive = no splitting; keep full question/answer body in one chunk.
    if max_chunk_chars is None or max_chunk_chars <= 0:
        emit_piece(text)
        return out, idx

    if len(text) <= max_chunk_chars:
        emit_piece(text)
        return out, idx

    paras: list[str] = [p.strip() for p in text.split("\n\n") if p.strip()]
    buf: list[str] = []
    cur = 0
    for p in paras:
        add_len = len(p) + (2 if buf else 0)
        if cur + add_len > max_chunk_chars and buf:
            emit_piece("\n\n".join(buf))
            buf = []
            cur = 0
        buf.append(p)
        cur += add_len
    if buf:
        emit_piece("\n\n".join(buf))
    return out, idx


def _chunks_from_prose_section(
    body: str,
    *,
    section_kind: str,
    section_banner: str,
    assignment_title: str,
    modality_subtype: str,
    max_chunk_chars: int | None,
    chunk_index_start: int,
    trio_id_start: int,
) -> tuple[list[dict[str, Any]], int, int]:
    """
    Build question + response + (later) answer/reference rows. Each numbered ``trio_id`` ties
    the question line, student markdown/code, and an ``answer/reference`` placeholder row.
    """
    chunks: list[dict[str, Any]] = []
    idx = chunk_index_start
    trio_id = trio_id_start

    body = (body or "").strip()
    if section_kind == "pdf":
        body = normalize_verticalized_pdf_text(body)
    # Word bodies are already paragraph-structured; skip PDF vertical reflow.

    boundaries = _prose_boundary_matches(
        body, section_kind=section_kind, modality_subtype=modality_subtype
    )
    if not boundaries:
        ans_role = _answer_role_for_body(
            body, section_kind=section_kind, modality_subtype=modality_subtype
        )
        packed, idx = _pack_text_by_paragraphs(
            body,
            role=ans_role,
            trio_id=None,
            max_chunk_chars=max_chunk_chars,
            assignment_title=assignment_title,
            modality_subtype=modality_subtype,
            chunk_index_start=idx,
            section_banner=section_banner,
        )
        chunks.extend(packed)
        return chunks, idx, trio_id

    # Note: ``blob`` here is the content *between the previous boundary and this one*,
    # i.e. the answer belonging to ``current_q`` (or leading preamble when ``current_q``
    # is still None). We must still record ``current_q`` even when ``blob`` is empty
    # (e.g. two questions back-to-back with no answer in between, as in an unfilled
    # instructor template) — otherwise that question line is silently dropped.
    segments: list[tuple[str | None, str]] = []
    pos = 0
    current_q: str | None = None
    for m in boundaries:
        blob = body[pos : m.start()].strip()
        if blob or current_q is not None:
            segments.append((current_q, blob))
        current_q = m.group().strip()
        pos = m.end()
    tail = body[pos:].strip()
    if tail or current_q is not None:
        segments.append((current_q, tail))

    for q_line, ans_text in segments:
        if q_line is None:
            ans_role = _answer_role_for_body(
                ans_text, section_kind=section_kind, modality_subtype=modality_subtype
            )
            packed, idx = _pack_text_by_paragraphs(
                ans_text,
                role=ans_role,
                trio_id=None,
                max_chunk_chars=max_chunk_chars,
                assignment_title=assignment_title,
                modality_subtype=modality_subtype,
                chunk_index_start=idx,
                section_banner=section_banner,
            )
            chunks.extend(packed)
            continue

        pid = trio_id
        trio_id += 1
        if _merge_notebook_markdown_question(
            q_line=q_line,
            ans_text=ans_text,
            modality_subtype=modality_subtype,
            section_banner=section_banner,
        ):
            q_full = f"{q_line}\n\n{ans_text}".strip()
            chunks.append(
                {
                    "role": "question",
                    "trio_id": pid,
                    "text": q_full,
                    "chunk_index": idx,
                    "assignment_title": assignment_title,
                    "modality_subtype": modality_subtype,
                    "section_banner": section_banner or None,
                }
            )
            idx += 1
            chunks.append(
                {
                    "role": "response",
                    "trio_id": pid,
                    "text": (
                        "[Student implementation is in NOTEBOOK CODE cells following this prompt "
                        "in the original notebook order.]"
                    ),
                    "chunk_index": idx,
                    "assignment_title": assignment_title,
                    "modality_subtype": modality_subtype,
                    "section_banner": section_banner or None,
                }
            )
            idx += 1
            continue

        chunks.append(
            {
                "role": "question",
                "trio_id": pid,
                "text": q_line,
                "chunk_index": idx,
                "assignment_title": assignment_title,
                "modality_subtype": modality_subtype,
                "section_banner": section_banner or None,
            }
        )
        idx += 1
        ans_role = _answer_role_for_body(
            ans_text, section_kind=section_kind, modality_subtype=modality_subtype
        )
        packed, idx = _pack_text_by_paragraphs(
            ans_text,
            role=ans_role,
            trio_id=pid,
            max_chunk_chars=max_chunk_chars,
            assignment_title=assignment_title,
            modality_subtype=modality_subtype,
            chunk_index_start=idx,
            section_banner=section_banner,
        )
        chunks.extend(packed)

    return chunks, idx, trio_id


def build_submission_chunks(
    text: str,
    *,
    assignment_title: str = "",
    modality_subtype: str = "",
    max_chunk_chars: int | None = None,
) -> list[dict[str, Any]]:
    """
    Produce chunks with ``role`` of ``question``, ``response``, ``code``, or
    ``answer/reference`` (placeholder per ``trio_id``).

    See module docstring for the full chunking explanation.

    ``max_chunk_chars``: when ``None`` or ``<= 0``, each answer/response body is kept as a
    single chunk (no paragraph packing split). Pass a positive int to cap piece size.
    """
    raw = (text or "").strip()
    if not raw:
        return []

    sections = _split_artifact_sections(raw)
    all_chunks: list[dict[str, Any]] = []
    idx = 0
    next_trio = 0

    for banner, sec_kind, body in sections:
        if not body.strip():
            continue

        if sec_kind == "code":
            packed, idx = _pack_text_by_paragraphs(
                body,
                role="code",
                trio_id=None,
                max_chunk_chars=max_chunk_chars,
                assignment_title=assignment_title,
                modality_subtype=modality_subtype,
                chunk_index_start=idx,
                section_banner=banner,
            )
            all_chunks.extend(packed)
            continue

        if sec_kind in ("markdown", "pdf", "docx", "txt", "body", "mixed"):
            effective_kind = sec_kind
            if sec_kind == "mixed" and _looks_like_code(body, section_kind="body"):
                effective_kind = "code"
            if effective_kind == "code":
                packed, idx = _pack_text_by_paragraphs(
                    body,
                    role="code",
                    trio_id=None,
                    max_chunk_chars=max_chunk_chars,
                    assignment_title=assignment_title,
                    modality_subtype=modality_subtype,
                    chunk_index_start=idx,
                    section_banner=banner,
                )
                all_chunks.extend(packed)
                continue

            part, idx, next_trio = _chunks_from_prose_section(
                body,
                section_kind="markdown" if sec_kind == "mixed" else sec_kind,
                section_banner=banner,
                assignment_title=assignment_title,
                modality_subtype=modality_subtype,
                max_chunk_chars=max_chunk_chars,
                chunk_index_start=idx,
                trio_id_start=next_trio,
            )
            all_chunks.extend(part)

    _ensure_trio_answer_reference_rows(all_chunks)
    for msg in validate_trio_chunking_schema(all_chunks):
        _log.warning("trio_chunking_schema: %s", msg)
    _sort_and_renumber_chunk_indices(all_chunks)
    return all_chunks


def write_chunks_json(
    out_path: Path,
    *,
    chunks: Sequence[dict[str, Any]],
    assignment_title: str = "",
    source_file: str = "",
    profile: dict[str, Any] | None = None,
) -> None:
    export_chunks = [trio_chunk_schema_record(dict(c)) for c in chunks]
    payload: dict[str, Any] = {
        "assignment_title": assignment_title,
        "source_file": source_file,
        "chunk_count": len(export_chunks),
        "trio_chunking_schema_version": 1,
        "chunking_notes": (
            "Per trio_id: question (prompt/instructions), response or code (student work), "
            "answer/reference (empty here; filled from answer key during grading). "
            "trio_id null = preamble / unstructured."
        ),
        "chunks": export_chunks,
    }
    if profile:
        payload["modality"] = profile
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
