"""
Command-line assignment parsing inspector.

Ingests the **real** blank instructor template (``blank_assignments/``) and the matching
**student-submitted** version (``assignments_to_grade/``) for one assignment, runs them
through the actual chunking pipeline (:func:`app.grading.submission_chunks.build_submission_chunks`),
and prints/writes the parsed **question <-> student-response** pairing.

No mock data — every artifact is read from the real fixture files that live at the
repository root (next to ``AGT_platform/``), the same layout used by
``tests/test_grading_pipeline_local_files.py``:

- ``blank_assignments/`` — the blank instructor template (e.g. ``[Blank Copy] <name>.docx``).
- ``assignments_to_grade/`` — the student's submitted file(s) (e.g. ``[Student 1] <name>.docx``).

Usage
-----

Set ``ASSIGNMENT_NAME`` to the assignment to parse (matches against the blank template /
student submission stems the same fuzzy way ``answer_key_resolve.py`` matches answer keys —
prefixes like ``[Student 1]``, ``[Blank Copy]``, ``[Answer_Key]`` are ignored), then run
pytest from ``AGT_platform/backend/``::

    ASSIGNMENT_NAME="Week7_JournalEntry7.3" pytest tests/test_assignment_parsing.py -v -s

``-s`` shows the printed question/response breakdown in the terminal. A JSON copy is also
written to ``grading_output/<assignment_name>_parsed_qa_pairs.json``.

If ``ASSIGNMENT_NAME`` is not set, the test auto-detects it when exactly one submission
basename exists under ``assignments_to_grade/``; otherwise it is skipped (never silently
grades the wrong assignment).

You can also run this file directly as a script (from ``AGT_platform/backend/``) and pass
the assignment name as a plain command-line argument (no env var needed). ``PYTHONPATH=.``
is required in this mode since plain ``python <file>`` does not get the ``app`` package
path that pytest sets up automatically via ``pyproject.toml``::

    PYTHONPATH=. python tests/test_assignment_parsing.py "Week7_JournalEntry7.3"
"""

from __future__ import annotations

import difflib
import json
import os
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.grading.answer_key_resolve import resolve_blank_assignment_template
from app.grading.modality_resolution import resolve_modality_profile
from app.grading.submission_chunks import build_submission_chunks
from app.grading.submission_text import submission_text_from_artifacts

# Repo root: .../ai-automatic-grader-tool (contains AGT_platform/, assignments_to_grade/, ...)
REPO_ROOT = Path(__file__).resolve().parents[3]
BLANK_DIR = REPO_ROOT / "blank_assignments"
ASSIGNMENTS_DIR = REPO_ROOT / "assignments_to_grade"
OUTPUT_DIR = REPO_ROOT / "grading_output"

_MIN_MATCH_RATIO = 0.38

_SUFFIX_TO_ARTIFACT_KEY = {
    ".ipynb": "ipynb",
    ".py": "py",
    ".pdf": "pdf",
    ".txt": "txt",
    ".md": "md",
    ".docx": "docx",
    ".mp4": "mp4",
    ".mp3": "mp3",
    ".wav": "wav",
    ".m4a": "m4a",
    ".webm": "webm",
}


def _artifact_key_for_suffix(suffix: str) -> str | None:
    return _SUFFIX_TO_ARTIFACT_KEY.get(suffix.lower())


def _normalize_for_match(name: str) -> str:
    """Strip bracketed prefixes (``[Student 1]``, ``[Blank Copy]``, ...) for fuzzy matching."""
    t = name.lower()
    t = re.sub(r"\[[^\]]*\]\s*", " ", t)
    t = re.sub(r"[^\w\s]+", " ", t)
    return " ".join(t.split())


def resolve_student_submission_files(
    assignment_name: str, assignments_dir: Path
) -> list[Path]:
    """
    Return every file under ``assignments_dir`` that belongs to the best-matching student
    submission for ``assignment_name`` (fuzzy match, same style as
    :func:`app.grading.answer_key_resolve.resolve_blank_assignment_template`).

    Multiple files can share one submission stem (e.g. a notebook plus a PDF export), so
    every file whose normalized stem matches the best-scoring stem is returned together.
    """
    if not assignment_name.strip() or not assignments_dir.is_dir():
        return []

    target = _normalize_for_match(assignment_name)
    scored: list[tuple[float, str, Path]] = []
    for p in sorted(assignments_dir.iterdir()):
        if not p.is_file() or p.name.startswith("."):
            continue
        if _artifact_key_for_suffix(p.suffix) is None:
            continue
        stem_n = _normalize_for_match(p.stem)
        if not stem_n:
            continue
        ratio = difflib.SequenceMatcher(None, target, stem_n).ratio()
        if target and (target in stem_n or stem_n in target):
            ratio = max(ratio, 0.88)
        scored.append((ratio, stem_n, p))

    if not scored:
        return []

    scored.sort(key=lambda x: x[0], reverse=True)
    best_ratio, best_stem_n, _best_path = scored[0]
    if best_ratio < _MIN_MATCH_RATIO:
        return []
    return [p for ratio, stem_n, p in scored if stem_n == best_stem_n]


def _build_artifacts(paths: list[Path]) -> dict[str, bytes]:
    artifacts: dict[str, bytes] = {}
    for p in paths:
        key = _artifact_key_for_suffix(p.suffix)
        if key:
            artifacts[key] = p.read_bytes()
    return artifacts


def _auto_detect_single_assignment_name(assignments_dir: Path) -> str | None:
    """When exactly one submission basename exists, return its (un-normalized) stem."""
    if not assignments_dir.is_dir():
        return None
    stems: dict[str, str] = {}
    for p in sorted(assignments_dir.iterdir()):
        if not p.is_file() or p.name.startswith("."):
            continue
        if _artifact_key_for_suffix(p.suffix) is None:
            continue
        norm = _normalize_for_match(p.stem)
        if norm:
            stems.setdefault(norm, p.stem)
    if len(stems) == 1:
        return next(iter(stems.values()))
    return None


def _running_as_direct_script() -> bool:
    """True only for ``python test_assignment_parsing.py ...`` (not under pytest)."""
    try:
        return Path(sys.argv[0]).resolve() == Path(__file__).resolve()
    except (OSError, ValueError):
        return False


def resolve_assignment_name() -> str | None:
    """
    Priority: explicit script argument (only when run directly as
    ``python test_assignment_parsing.py <name>`` — never scanned under pytest, so
    ``pytest tests/`` or ``pytest tests/test_assignment_parsing.py`` never mistakes their
    own path arguments for an assignment name) > ``ASSIGNMENT_NAME`` env var >
    auto-detected single fixture under ``assignments_to_grade/``.
    """
    if _running_as_direct_script():
        for arg in sys.argv[1:]:
            if arg.startswith("-"):
                continue
            return arg
    env_name = os.getenv("ASSIGNMENT_NAME", "").strip()
    if env_name:
        return env_name
    return _auto_detect_single_assignment_name(ASSIGNMENTS_DIR)


def group_question_response_pairs(
    chunks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """
    Collapse chunker output into one row per ``trio_id``: ``{trio_id, question, response,
    response_role}``. Rows with no ``trio_id`` (preamble / unstructured text) are skipped —
    they carry no distinct question.
    """
    by_tid: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    for c in chunks:
        tid = c.get("trio_id")
        if tid is None and c.get("pair_id") is not None:
            tid = c.get("pair_id")
        if tid is None:
            continue
        tid = int(tid)
        if tid not in by_tid:
            by_tid[tid] = {
                "trio_id": tid,
                "question": "",
                "response": "",
                "response_role": "response",
            }
            order.append(tid)
        row = by_tid[tid]
        role = str(c.get("role") or "")
        text = str(c.get("text") or "").strip()
        if not text:
            continue
        if role == "question":
            row["question"] = f"{row['question']}\n\n{text}".strip() if row["question"] else text
        elif role in ("response", "code"):
            row["response_role"] = role
            row["response"] = f"{row['response']}\n\n{text}".strip() if row["response"] else text
    return [by_tid[tid] for tid in order]


def _print_parsed_assignment(
    *,
    assignment_name: str,
    blank_name: str,
    submission_names: list[str],
    modality_subtype: str,
    blank_questions: list[str],
    pairs: list[dict[str, Any]],
) -> None:
    sep = "=" * 78
    print(f"\n{sep}")
    print(f"Parsed assignment: {assignment_name!r}")
    print(f"  blank template file:   {blank_name}")
    print(f"  student submission:    {', '.join(submission_names)}")
    print(f"  modality_subtype:      {modality_subtype}")
    print(f"  instructor questions found in blank template: {len(blank_questions)}")
    print(f"  question/response pairs found in submission:  {len(pairs)}")
    print(sep)

    for i, blank_q in enumerate(blank_questions, start=1):
        preview = blank_q.strip().replace("\n", " ")
        print(f"[Blank template Q{i}] {preview[:160]}")
    if blank_questions:
        print("-" * 78)

    for row in pairs:
        print(f"\n--- trio_id={row['trio_id']} ---")
        print(f"QUESTION:\n{row['question'] or '(none)'}")
        print(f"\n{row['response_role'].upper()}:\n{row['response'] or '(none)'}")
    print(f"\n{sep}\n")


def _write_parsed_assignment_json(
    *,
    out_path: Path,
    assignment_name: str,
    blank_name: str,
    submission_names: list[str],
    modality_subtype: str,
    blank_questions: list[str],
    pairs: list[dict[str, Any]],
) -> None:
    payload = {
        "assignment_name": assignment_name,
        "blank_template_file": blank_name,
        "student_submission_files": submission_names,
        "modality_subtype": modality_subtype,
        "blank_template_questions": blank_questions,
        "question_response_pairs": pairs,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _fixtures_layout_ok() -> bool:
    return BLANK_DIR.is_dir() and ASSIGNMENTS_DIR.is_dir()


@unittest.skipUnless(
    _fixtures_layout_ok(),
    "Create blank_assignments/ and assignments_to_grade/ at the repository root.",
)
class TestAssignmentParsing(unittest.TestCase):
    """Parse a real blank template + real student submission into question/response pairs."""

    def test_parse_blank_and_submitted_assignment(self) -> None:
        assignment_name = resolve_assignment_name()
        if not assignment_name:
            self.skipTest(
                "No assignment specified. Set ASSIGNMENT_NAME=<name> (or pass it as a script "
                "argument) — see module docstring for usage. Multiple submissions exist under "
                f"{ASSIGNMENTS_DIR}, so auto-detection was skipped."
            )

        blank_bytes, blank_name, blank_suffix = resolve_blank_assignment_template(
            assignment_name, BLANK_DIR
        )
        self.assertTrue(
            blank_bytes,
            f"No blank template found under {BLANK_DIR} matching {assignment_name!r}.",
        )

        submission_paths = resolve_student_submission_files(assignment_name, ASSIGNMENTS_DIR)
        self.assertTrue(
            submission_paths,
            f"No student submission found under {ASSIGNMENTS_DIR} matching {assignment_name!r}.",
        )

        blank_key = _artifact_key_for_suffix(blank_suffix)
        blank_plain = (
            submission_text_from_artifacts({blank_key: blank_bytes}) if blank_key else ""
        )
        self.assertGreater(
            len(blank_plain.strip()),
            0,
            f"Blank template {blank_name!r} produced no extractable text.",
        )

        submission_artifacts = _build_artifacts(submission_paths)
        submission_plain = submission_text_from_artifacts(submission_artifacts)
        self.assertGreater(
            len(submission_plain.strip()),
            0,
            f"Student submission {[p.name for p in submission_paths]!r} produced no "
            "extractable text.",
        )

        modality_profile = resolve_modality_profile(
            SimpleNamespace(title=assignment_name, description=""),
            submission_artifacts,
            submission_plain,
        )
        modality_subtype = str(modality_profile.get("modality_subtype") or "")

        blank_chunks = build_submission_chunks(
            blank_plain,
            assignment_title=f"{assignment_name} (blank template)",
            modality_subtype=modality_subtype,
            max_chunk_chars=None,
        )
        submission_chunks = build_submission_chunks(
            submission_plain,
            assignment_title=assignment_name,
            modality_subtype=modality_subtype,
            max_chunk_chars=None,
        )

        blank_questions = [c["text"] for c in blank_chunks if c.get("role") == "question"]
        pairs = group_question_response_pairs(submission_chunks)

        self.assertTrue(
            pairs,
            "No question/response pairs were parsed from the student submission "
            f"({[p.name for p in submission_paths]!r}).",
        )

        submission_names = [p.name for p in submission_paths]
        _print_parsed_assignment(
            assignment_name=assignment_name,
            blank_name=blank_name,
            submission_names=submission_names,
            modality_subtype=modality_subtype,
            blank_questions=blank_questions,
            pairs=pairs,
        )

        safe_stem = re.sub(r"[^\w\-. ]+", "_", assignment_name).strip() or "assignment"
        _write_parsed_assignment_json(
            out_path=OUTPUT_DIR / f"{safe_stem}_parsed_qa_pairs.json",
            assignment_name=assignment_name,
            blank_name=blank_name,
            submission_names=submission_names,
            modality_subtype=modality_subtype,
            blank_questions=blank_questions,
            pairs=pairs,
        )


if __name__ == "__main__":
    # Allow "python test_assignment_parsing.py <assignment_name>" without unittest trying
    # to interpret <assignment_name> as a test name/pattern (it was already consumed by
    # resolve_assignment_name() above via _running_as_direct_script()).
    _argv = [sys.argv[0]] + [a for a in sys.argv[1:] if a.startswith("-")]
    unittest.main(argv=_argv)
