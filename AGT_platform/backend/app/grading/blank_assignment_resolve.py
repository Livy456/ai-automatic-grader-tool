"""
Resolve a **blank** instructor copy of an assignment (questions + instructions only).

Files live under ``blank_assignments/`` at the repository root (or a caller-provided
directory). Matching mirrors :func:`app.grading.answer_key_resolve.resolve_answer_key_plaintext`
stem logic but returns **raw ``.ipynb`` bytes** for notebook-aware chunking.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Final

_SUFFIX: Final[tuple[str, ...]] = (".ipynb",)
_MIN_RATIO: Final[float] = 0.38


def _normalize_for_match(s: str) -> str:
    t = s.lower()
    t = re.sub(r"\[student\s*\d+\]\s*", "", t, flags=re.I)
    t = re.sub(r"\[[^\]]*\]", " ", t)
    t = re.sub(r"[^\w\s]+", " ", t)
    return " ".join(t.split())


def resolve_blank_assignment_ipynb(
    assignment_stem: str,
    blank_dir: Path,
) -> tuple[bytes, str]:
    """
    Return ``(ipynb_bytes, matched_relative_name)``.

    Empty bytes when no suitable ``.ipynb`` is found under ``blank_dir``.
    """
    if not assignment_stem.strip() or not blank_dir.is_dir():
        return b"", ""

    for suf in _SUFFIX:
        exact = blank_dir / f"{assignment_stem}{suf}"
        if exact.is_file():
            try:
                return exact.read_bytes(), exact.name
            except OSError:
                break

    stem_n = _normalize_for_match(assignment_stem)
    best_path: Path | None = None
    best_ratio = 0.0

    for path in sorted(blank_dir.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.name.lower() == "readme.md":
            continue
        if path.suffix.lower() != ".ipynb":
            continue
        key_n = _normalize_for_match(path.stem)
        if not key_n:
            continue
        ratio = difflib.SequenceMatcher(None, stem_n, key_n).ratio()
        if stem_n and (stem_n in key_n or key_n in stem_n):
            ratio = max(ratio, 0.88)
        if ratio > best_ratio:
            best_ratio = ratio
            best_path = path

    if best_path is None or best_ratio < _MIN_RATIO:
        return b"", ""

    try:
        return best_path.read_bytes(), best_path.name
    except OSError:
        return b"", ""
