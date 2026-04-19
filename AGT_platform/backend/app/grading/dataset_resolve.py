"""
Match a tabular / text dataset in ``assignments_to_grade/`` to a notebook submission.

Uses the same embedding path as RAG (:func:`app.grading.rag_embeddings.compute_submission_embedding`)
and cosine similarity between the assignment plaintext vector and each candidate file’s
``filename + preview`` embedding.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

from app.grading.rag_embeddings import compute_submission_embedding

_DATA_SUFFIXES = frozenset({".csv", ".tsv", ".txt", ".json"})


def default_assignments_to_grade_dir() -> Path:
    """``…/ai-automatic-grader-tool/assignments_to_grade`` (repo root sibling of ``AGT_platform``)."""
    # .../AGT_platform/backend/app/grading/dataset_resolve.py → parents[4] = repo root
    return Path(__file__).resolve().parents[4] / "assignments_to_grade"


def list_data_asset_files(directory: Path) -> list[Path]:
    """Non-notebook data files suitable for embedding (CSV/TSV/JSON/text)."""
    out: list[Path] = []
    if not directory.is_dir():
        return out
    for p in sorted(directory.iterdir()):
        if not p.is_file() or p.name.startswith("."):
            continue
        if p.suffix.lower() not in _DATA_SUFFIXES:
            continue
        if p.name.lower().startswith("readme"):
            continue
        out.append(p)
    return out


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(a[i] * b[i] for i in range(len(a)))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return float(dot / (na * nb))


def _preview_file(path: Path, max_chars: int = 12_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except OSError:
        return ""


def resolve_dataset_for_notebook(
    assignment_plaintext: str,
    assignments_dir: Path,
    cfg: Any,
    *,
    min_similarity: float = -1.0,
) -> tuple[str | None, str | None, float]:
    """
    Return ``(preview_text, matched_filename, cosine_similarity)``.

    Embeds the full assignment text (same cap as RAG) and each candidate’s
    ``"{name}\\n{preview}"``; picks the highest cosine similarity. When
    ``min_similarity`` is greater than ``-1``, a match is returned only if the
    best score meets that floor (useful for real embeddings; hash fallbacks can
    yield negative cosine scores).
    """
    cands = list_data_asset_files(assignments_dir)
    if not cands:
        return None, None, 0.0
    a_vec, _ = compute_submission_embedding(assignment_plaintext or "", cfg)
    best_sim = -1.0
    best: Path | None = None
    for p in cands:
        blob = f"{p.name}\n{_preview_file(p)}"
        b_vec, _ = compute_submission_embedding(blob, cfg)
        sim = _cosine_similarity(a_vec, b_vec)
        if sim > best_sim:
            best_sim = sim
            best = p
    if best is None:
        return None, None, 0.0
    if best_sim < min_similarity:
        return None, None, float(best_sim)
    return _preview_file(best), best.name, float(best_sim)


def attach_dataset_context_for_notebook(
    envelope: Any,
    app_cfg: Any,
    art: Any | None,
) -> None:
    """
    If the envelope carries ``ipynb`` bytes, resolve a dataset file under
    ``assignments_to_grade`` (or ``modality_hints["assignments_data_dir"]``) and set:

    - ``dataset_context_plaintext`` — truncated preview for the grader
    - ``dataset_matched_file`` / ``dataset_match_similarity``

    When ``MULTIMODAL_REQUIRE_DATASET_FOR_IPYNB`` is truthy, raises if no match clears
    the similarity floor.
    """
    if not getattr(envelope, "artifacts", None) or not envelope.artifacts.get("ipynb"):
        return
    hints = envelope.modality_hints
    if str(hints.get("dataset_context_plaintext") or "").strip():
        return
    raw_dir = str(hints.get("assignments_data_dir") or "").strip()
    root = Path(raw_dir).expanduser() if raw_dir else default_assignments_to_grade_dir()
    text, fname, sim = resolve_dataset_for_notebook(
        str(getattr(envelope, "extracted_plaintext", "") or ""),
        root,
        app_cfg,
    )
    if text and fname:
        hints["dataset_context_plaintext"] = text[:24_000]
        hints["dataset_matched_file"] = fname
        hints["dataset_match_similarity"] = round(sim, 6)
    if art is not None and hasattr(art, "append"):
        art.append(
            "dataset",
            {
                "search_dir": str(root),
                "matched_file": fname,
                "similarity": round(sim, 6),
                "candidates": len(list_data_asset_files(root)),
            },
        )
    req = os.getenv("MULTIMODAL_REQUIRE_DATASET_FOR_IPYNB", "").strip().lower()
    if req in ("1", "true", "yes") and not str(hints.get("dataset_context_plaintext") or "").strip():
        raise RuntimeError(
            "Notebook submission requires a matching dataset file under "
            f"{root!s} (set MULTIMODAL_REQUIRE_DATASET_FOR_IPYNB=0 to allow grading without)."
        )
