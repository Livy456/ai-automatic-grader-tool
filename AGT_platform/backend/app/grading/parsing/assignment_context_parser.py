"""
Assignment context parsing agent: extracts plain text from an instructor's uploaded blank
assignment template and answer key so :mod:`app.grading.chunking.assignment_qa_chunker` (the
question/answer chunking agent) has plain text to work from instead of raw file bytes.

Used by the "Assignment Creation" flow (see ``app.routes.assignment_library``). Deliberately
has no LLM dependency — it only does deterministic byte -> plaintext extraction, reusing
:mod:`app.grading.parsing.artifact_plaintext` (the same extraction the multimodal grading
pipeline uses for student submissions and blank templates), so it is fast, free, and never
fails outright: unsupported/unreadable bytes simply yield an empty string for that source.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.grading.parsing.artifact_plaintext import bytes_with_suffix_to_plain


@dataclass
class ParsedAssignmentContext:
    """Plain-text view of an assignment's uploaded blank template + answer key."""

    blank_text: str
    answer_key_text: str


def _suffix_from_filename(filename: str) -> str:
    name = (filename or "").strip()
    if "." not in name:
        return ""
    return "." + name.rsplit(".", 1)[-1].lower()


def parse_assignment_context(
    *,
    blank_bytes: bytes | None,
    blank_filename: str,
    answer_key_bytes: bytes | None,
    answer_key_filename: str,
) -> ParsedAssignmentContext:
    """Extract plaintext for the blank template and answer key uploads (best-effort)."""
    blank_text = (
        bytes_with_suffix_to_plain(blank_bytes, _suffix_from_filename(blank_filename))
        if blank_bytes
        else ""
    )
    answer_key_text = (
        bytes_with_suffix_to_plain(answer_key_bytes, _suffix_from_filename(answer_key_filename))
        if answer_key_bytes
        else ""
    )
    return ParsedAssignmentContext(blank_text=blank_text, answer_key_text=answer_key_text)
