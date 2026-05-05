"""
Build grading units from :func:`submission_chunks.build_submission_chunks` output.

Units group a **question** line with **student response** / ``code`` chunks that share the same
``trio_id`` (legacy exports may still use ``pair_id``). Optional ``answer/reference`` rows
supply ``reference_text``. Orphan responses (no detected prompt) form a separate unit.
"""

from __future__ import annotations

from typing import Any


def _chunk_link_id(ch: dict[str, Any]) -> Any:
    tid = ch.get("trio_id")
    if tid is None and ch.get("pair_id") is not None:
        return ch.get("pair_id")
    return tid


def build_grading_units_from_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Return one dict per gradable unit:

    - ``trio_id``: int or ``None``
    - ``question_text``: prompt line(s) or empty / placeholder
    - ``response_text``: concatenated student ``response`` / ``code`` text
    - ``reference_text``: text from ``answer/reference`` rows (often filled downstream)
    - ``chunk_ids``: sorted ``chunk_index`` values included
    """
    if not chunks:
        return []

    sorted_chunks = sorted(
        chunks,
        key=lambda x: int(x.get("chunk_index", 0)),
    )

    by_trio: dict[Any, dict[str, Any]] = {}
    orphans: list[dict[str, Any]] = []

    def bucket(pid: Any) -> dict[str, Any]:
        if pid not in by_trio:
            by_trio[pid] = {
                "question_parts": [],
                "response_parts": [],
                "reference_parts": [],
                "chunk_ids": [],
            }
        return by_trio[pid]

    for ch in sorted_chunks:
        role = ch.get("role")
        pid = _chunk_link_id(ch)
        cid = ch.get("chunk_index")
        text = str(ch.get("text") or "")

        if role == "question":
            if pid is not None:
                b = bucket(pid)
                b["question_parts"].append(text)
                if cid is not None:
                    b["chunk_ids"].append(cid)
        elif role == "answer/reference":
            if pid is None:
                continue
            b = bucket(pid)
            b["reference_parts"].append(text)
            if cid is not None:
                b["chunk_ids"].append(cid)
        elif role in ("response", "code"):
            if pid is None:
                orphans.append(ch)
            else:
                b = bucket(pid)
                b["response_parts"].append(text)
                if cid is not None:
                    b["chunk_ids"].append(cid)

    units: list[dict[str, Any]] = []

    for pid in sorted((k for k in by_trio if k is not None), key=lambda x: int(x)):
        u = by_trio[pid]
        q = "\n".join(u["question_parts"]).strip()
        r = "\n\n".join(u["response_parts"]).strip()
        ref = "\n\n".join(u.get("reference_parts") or []).strip()
        cids = sorted({x for x in u["chunk_ids"] if x is not None})
        if not q and not r and not ref:
            continue
        units.append(
            {
                "trio_id": pid,
                "question_text": q,
                "response_text": r,
                "reference_text": ref,
                "chunk_ids": cids,
            }
        )

    if orphans:
        rtext = "\n\n".join(
            str(o.get("text") or "").strip()
            for o in orphans
            if str(o.get("text") or "").strip()
        )
        ocids = sorted(
            {int(o["chunk_index"]) for o in orphans if o.get("chunk_index") is not None}
        )
        if rtext.strip():
            units.insert(
                0,
                {
                    "trio_id": None,
                    "question_text": "(no detected prompt line; preamble or unstructured excerpt)",
                    "response_text": rtext.strip(),
                    "reference_text": "",
                    "chunk_ids": ocids,
                },
            )

    if not units:
        blob = "\n\n".join(
            str(c.get("text") or "").strip()
            for c in sorted_chunks
            if str(c.get("text") or "").strip()
        )
        if blob.strip():
            units.append(
                {
                    "trio_id": None,
                    "question_text": "",
                    "response_text": blob.strip(),
                    "reference_text": "",
                    "chunk_ids": sorted(
                        {
                            int(c["chunk_index"])
                            for c in sorted_chunks
                            if c.get("chunk_index") is not None
                        }
                    ),
                }
            )

    return units


def format_unit_for_grader_prompt(unit: dict[str, Any]) -> str:
    q = unit.get("question_text") or ""
    r = unit.get("response_text") or ""
    ref = unit.get("reference_text") or ""
    ref_block = f"\n\n**Reference / answer key excerpt:**\n{ref}\n\n" if ref.strip() else ""
    return (
        "\n\n---\n### Focus for this grading pass\n"
        f"**Question / prompt:**\n{q}\n\n"
        f"**Student work (response or code):**\n{r}\n"
        f"{ref_block}"
        "Apply the rubric to this excerpt. Use score 0 for criteria clearly not "
        "evidenced in this excerpt. Return the standard grading JSON.\n"
    )
