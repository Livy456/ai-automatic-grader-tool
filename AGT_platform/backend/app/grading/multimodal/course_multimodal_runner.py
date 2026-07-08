"""
DB / Celery entry: map assignment + raw artifact bytes → multimodal pipeline → grading dict.

Delegates to :func:`app.grading.multimodal.pipeline_runner.run_multimodal_grading` so
production grading matches the local integration test structure.
"""

from __future__ import annotations

from typing import Any

from .pipeline_runner import run_multimodal_grading, rubric_column_to_by_type_and_flat
from .rubric_fallback import DEFAULT_STANDALONE_RUBRIC

__all__ = [
    "DEFAULT_STANDALONE_RUBRIC",
    "rubric_column_to_by_type_and_flat",
    "run_db_submission_multimodal_pipeline",
    "run_standalone_multimodal_pipeline",
]


def run_db_submission_multimodal_pipeline(
    cfg: Any,
    assignment: Any,
    artifacts_bytes: dict[str, bytes],
    *,
    submission_id: int,
    assignment_id: int,
    student_id: int | None,
    rubric_text: str | None,
    answer_key_text: str | None,
) -> dict[str, Any]:
    """Course or public autograder row: grade using :class:`MultimodalGradingPipeline`."""
    envelope_sid = (
        str(student_id) if student_id is not None else f"anon_sub_{submission_id}"
    )
    stem = str(getattr(assignment, "title", None) or assignment_id).strip()
    return run_multimodal_grading(
        cfg,
        assignment=assignment,
        artifacts_bytes=artifacts_bytes,
        assignment_id=str(assignment_id),
        student_id=envelope_sid,
        rubric_column=getattr(assignment, "rubric", None),
        rubric_text=rubric_text,
        answer_key_text=answer_key_text,
        assignment_stem=stem,
        validate_output=False,
    )


def run_standalone_multimodal_pipeline(
    cfg: Any,
    artifacts_bytes: dict[str, bytes],
    submission_id: int,
    title: str,
    rubric_text: str | None,
    answer_key_text: str | None,
    rubric_file_excerpt: str | None,
    answer_key_file_excerpt: str | None,
    grading_instructions: str | None = None,
) -> dict[str, Any]:
    """Standalone autograder: default structured rubric; prose rubric/AK in the prompt."""
    from types import SimpleNamespace

    from app.grading.modality_resolution import infer_modality_from_artifacts

    merged_rubric_note_parts: list[str] = []
    if rubric_text and rubric_text.strip():
        merged_rubric_note_parts.append(rubric_text.strip())
    if rubric_file_excerpt and rubric_file_excerpt.strip():
        merged_rubric_note_parts.append(
            "Rubric (from uploaded file):\n" + rubric_file_excerpt.strip()
        )
    merged_rubric = "\n\n".join(merged_rubric_note_parts) if merged_rubric_note_parts else None

    merged_ak_parts: list[str] = []
    if answer_key_text and answer_key_text.strip():
        merged_ak_parts.append(answer_key_text.strip())
    if answer_key_file_excerpt and answer_key_file_excerpt.strip():
        merged_ak_parts.append(
            "Answer key (from uploaded file):\n" + answer_key_file_excerpt.strip()
        )
    merged_ak = "\n\n".join(merged_ak_parts) if merged_ak_parts else None

    desc_parts: list[str] = []
    base_title = (title or "Standalone submission").strip()
    if base_title:
        desc_parts.append(base_title)
    if grading_instructions and str(grading_instructions).strip():
        desc_parts.append(
            "Instructor grading instructions:\n" + str(grading_instructions).strip()
        )
    description = (
        "\n\n".join(desc_parts) if desc_parts else "Standalone autograder submission"
    )

    pseudo = SimpleNamespace(
        modality=infer_modality_from_artifacts(artifacts_bytes),
        rubric=list(DEFAULT_STANDALONE_RUBRIC),
        title=title or "Standalone submission",
        description=description,
    )
    return run_multimodal_grading(
        cfg,
        assignment=pseudo,
        artifacts_bytes=artifacts_bytes,
        assignment_id="0",
        student_id=f"standalone_{submission_id}",
        rubric_column=list(DEFAULT_STANDALONE_RUBRIC),
        rubric_text=merged_rubric,
        answer_key_text=merged_ak,
        assignment_stem=base_title,
        validate_output=False,
    )
