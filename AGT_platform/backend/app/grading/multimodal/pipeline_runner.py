"""
Shared multimodal grading entry used by Celery tasks, HTTP routes, and local tests.

Mirrors the flow in ``tests/test_grading_pipeline_local_files.py``:
resolve rubric → build envelope + modality hints → :class:`MultimodalGradingPipeline`
→ :func:`multimodal_assignment_to_grading_dict` → validate.

:func:`build_multimodal_grading_context` and :func:`finalize_multimodal_grading_output` factor
that setup/finish work out of :func:`run_multimodal_grading` so
:mod:`app.grading.multimodal.course_evidence_grading_pipeline` (the course/library
submission-review Evidence Agent + Grading Agent pipeline) can reuse the identical envelope/rubric
resolution and output validation/schema, differing only in *which chunks* get graded.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from app.config import Config
from app.grading.parsing.modality_resolution import (
    augment_prompt_for_modality_profile,
    infer_modality_from_artifacts,
    normalize_modality_hint_for_multimodal,
    resolve_modality_profile,
)
from app.grading.grading_output.output_schema import coerce_grading_output_shape, validate_grading_output
from app.grading.parsing.submission_text import submission_text_from_artifacts
from app.grading.parsing.tools import extract_text_from_pdf

from app.grading.rubric_routing.generic_rubric_loader import (
    _row_from_criterion,
    flat_rubric_rows_from_by_type,
    four_generic_rubric_files_present,
    load_four_generic_rubric_rows_by_type,
    merge_four_generics_to_sections_document,
)
from app.grading.grading_output.grading_output import multimodal_assignment_to_grading_dict
from app.grading.parsing.ingestion import ingest_raw_submission
from app.grading.rubric_routing.rubric_fallback import DEFAULT_STANDALONE_RUBRIC

from .pipeline import (
    create_multimodal_pipeline_from_app_config,
    default_answer_key_dir,
    default_rubric_dir,
)
from app.grading.schemas import MultimodalGradingConfig, RubricType

_log = logging.getLogger(__name__)

_GENERIC_BASENAMES = ("default", "generic", "rubric")

_SECTION_NAME_TO_RUBRIC_TYPE: dict[str, RubricType] = {
    "Scaffolded Coding": RubricType.PROGRAMMING_SCAFFOLDED,
    "Free Response": RubricType.FREE_RESPONSE,
    "Open-Ended EDA": RubricType.EDA_VISUALIZATION,
    "Mock Interview / Oral Assessment": RubricType.ORAL_INTERVIEW,
}

_SUFFIX_TO_ARTIFACT_KEY: dict[str, str] = {
    ".ipynb": "ipynb",
    ".py": "py",
    ".pdf": "pdf",
    ".txt": "txt",
    ".md": "md",
    ".mp4": "mp4",
    ".docx": "docx",
    ".mp3": "mp3",
    ".wav": "wav",
    ".m4a": "m4a",
    ".webm": "webm",
    ".mpa": "mp3",
    ".csv": "csv",
    ".xlsx": "xlsx",
    ".png": "png",
    ".jpg": "jpg",
    ".jpeg": "jpg",
    ".zip": "zip",
}


def artifact_key_for_filename(filename: str) -> str | None:
    """Map a submission filename suffix to a multimodal artifact key."""
    fn = (filename or "").lower()
    for ext, key in _SUFFIX_TO_ARTIFACT_KEY.items():
        if fn.endswith(ext):
            return key
    return None


def build_submission_artifacts(
    parts: list[tuple[str, bytes]],
) -> dict[str, bytes]:
    """
    Build ``{artifact_key: bytes}`` from ``(filename_or_kind, data)`` pairs.

    Duplicate keys raise :class:`ValueError` (same contract as local integration tests).
    """
    artifacts: dict[str, bytes] = {}
    for name, data in parts:
        if not data:
            continue
        key = artifact_key_for_filename(name)
        if not key:
            kind = (name or "").lower().split(".")[-1]
            key = _SUFFIX_TO_ARTIFACT_KEY.get(f".{kind}")
        if not key:
            continue
        if key in artifacts:
            raise ValueError(f"Duplicate artifact key {key!r} for {name!r}")
        artifacts[key] = bytes(data)
    return artifacts


def excerpt_attachment_bytes(filename: str, data: bytes, *, max_chars: int = 80_000) -> str:
    """Extract plain text from rubric / answer-key attachment bytes."""
    if not data:
        return ""
    fn = (filename or "").lower()
    if fn.endswith(".pdf") or (len(data) > 4 and data[:4] == b"%PDF"):
        try:
            return extract_text_from_pdf(data)
        except Exception:
            return ""
    return data.decode("utf-8", errors="ignore")[:max_chars]


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


def _flatten_sections_rubric(raw: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for sec in raw.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        sec_name = str(sec.get("name") or "Section").strip()
        for c in sec.get("criteria") or []:
            if not isinstance(c, dict):
                continue
            cname = str(c.get("name") or "Criterion").strip()
            max_pts = _max_points_from_range(c.get("points_range"))
            levels = c.get("levels")
            desc = json.dumps(levels, ensure_ascii=False) if isinstance(levels, dict) else ""
            label = f"{sec_name} — {cname}" if sec_name else cname
            out.append(
                {
                    "name": label,
                    "max_points": max_pts,
                    "criterion": label,
                    "max_score": max_pts,
                    "description": desc[:8000],
                }
            )
    return out


def _build_rubric_rows_by_type_from_sections_doc(
    rubric_json: dict[str, Any],
) -> dict[RubricType, list[dict[str, Any]]]:
    by_type: dict[RubricType, list[dict[str, Any]]] = {}
    for sec in rubric_json.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        sec_name = str(sec.get("name") or "").strip()
        rt = _SECTION_NAME_TO_RUBRIC_TYPE.get(sec_name)
        if rt is None:
            continue
        rows: list[dict[str, Any]] = []
        for c in sec.get("criteria") or []:
            if not isinstance(c, dict):
                continue
            rows.append(_row_from_criterion(c))
        by_type[rt] = rows
    return by_type


def _coerce_flat_rubric_rows(raw_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in raw_list:
        if not isinstance(c, dict):
            continue
        if c.get("points_range") is not None or isinstance(c.get("levels"), dict):
            out.append(_row_from_criterion(c))
            continue
        name = str(c.get("name") or c.get("criterion") or "").strip()
        if not name:
            continue
        try:
            mp = float(
                c.get("max_points")
                if c.get("max_points") is not None
                else (c.get("max_score") if c.get("max_score") is not None else 10.0)
            )
        except (TypeError, ValueError):
            mp = 10.0
        desc = str(c.get("description") or "")[:8000]
        out.append(
            {
                "name": name,
                "max_points": mp,
                "criterion": name,
                "max_score": mp,
                "description": desc,
            }
        )
    return out


def rubric_column_to_by_type_and_flat(
    rubric_column: Any,
) -> tuple[dict[RubricType, list[dict[str, Any]]], list[dict[str, Any]]]:
    """
    From an LMS ``Assignment.rubric`` JSON column, build ``rubric_rows_by_type`` and a flat
    list for :func:`multimodal_assignment_to_grading_dict` allowlisting.
    """
    default_flat = [dict(x) for x in DEFAULT_STANDALONE_RUBRIC]
    if rubric_column is None:
        return {RubricType.FREE_RESPONSE: _coerce_flat_rubric_rows(default_flat)}, default_flat

    if isinstance(rubric_column, dict) and isinstance(rubric_column.get("sections"), list):
        by_typed = _build_rubric_rows_by_type_from_sections_doc(rubric_column)
        if by_typed:
            flat = flat_rubric_rows_from_by_type(by_typed)
            return by_typed, flat if flat else default_flat
        flat_sec = _flatten_sections_rubric(rubric_column)
        if flat_sec:
            rows = _coerce_flat_rubric_rows(flat_sec)
            if rows:
                return {RubricType.FREE_RESPONSE: rows}, rows

    raw_list: list[dict[str, Any]] = []
    if isinstance(rubric_column, list):
        raw_list = [x for x in rubric_column if isinstance(x, dict)]
    elif isinstance(rubric_column, dict):
        for key in ("rubric", "criteria", "items"):
            chunk = rubric_column.get(key)
            if isinstance(chunk, list):
                raw_list = [x for x in chunk if isinstance(x, dict)]
                break

    if not raw_list:
        return {RubricType.FREE_RESPONSE: _coerce_flat_rubric_rows(default_flat)}, default_flat

    rows = _coerce_flat_rubric_rows(raw_list)
    if not rows:
        return {RubricType.FREE_RESPONSE: _coerce_flat_rubric_rows(default_flat)}, default_flat
    return {RubricType.FREE_RESPONSE: rows}, rows


def build_rubric_rows_by_type(rubric_json: dict[str, Any]) -> dict[RubricType, list[dict]]:
    """Map each rubric section name to :class:`RubricType` rows (local-test parity)."""
    by_type: dict[RubricType, list[dict]] = {}
    for sec in rubric_json.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        sec_name = str(sec.get("name") or "").strip()
        rt = _SECTION_NAME_TO_RUBRIC_TYPE.get(sec_name)
        if rt is None:
            continue
        rows: list[dict] = []
        for c in sec.get("criteria") or []:
            if not isinstance(c, dict):
                continue
            name = str(c.get("name") or "Criterion").strip()
            max_pts = _max_points_from_range(c.get("points_range"))
            levels = c.get("levels")
            desc = json.dumps(levels, ensure_ascii=False) if isinstance(levels, dict) else ""
            rows.append(
                {
                    "name": name,
                    "max_points": max_pts,
                    "criterion": name,
                    "max_score": max_pts,
                    "description": desc,
                }
            )
        by_type[rt] = rows
    return by_type


def _parse_rubric_json_document(data: str) -> tuple[list[dict], str | None]:
    try:
        raw = json.loads(data)
    except json.JSONDecodeError as e:
        raise ValueError("Rubric JSON must be valid JSON.") from e

    extra_prose: str | None = None
    rubric_list: list[dict] = []

    if isinstance(raw, list):
        rubric_list = [x for x in raw if isinstance(x, dict)]
    elif isinstance(raw, dict):
        instr = raw.get("llm_grading_instructions")
        if isinstance(instr, str) and instr.strip():
            extra_prose = instr.strip()
        if isinstance(raw.get("sections"), list):
            rubric_list = _flatten_sections_rubric(raw)
        for key in ("rubric", "criteria", "items"):
            if rubric_list:
                break
            chunk = raw.get(key)
            if isinstance(chunk, list):
                rubric_list = [x for x in chunk if isinstance(x, dict)]
                break

    if not rubric_list:
        rubric_list = [dict(x) for x in DEFAULT_STANDALONE_RUBRIC]
    return rubric_list, extra_prose


def _collect_prose_for_basename(rubric_dir: Path, basename: str) -> str | None:
    parts: list[str] = []
    for ext in (".md", ".txt"):
        path = rubric_dir / f"{basename}{ext}"
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                parts.append(text)
    return "\n\n".join(parts) if parts else None


def _try_load_generic_rubric(
    rubric_dir: Path,
) -> tuple[list[dict], str | None] | None:
    for base in _GENERIC_BASENAMES:
        json_path = rubric_dir / f"{base}.json"
        prose = _collect_prose_for_basename(rubric_dir, base)
        if json_path.is_file():
            rubric_list, json_prose = _parse_rubric_json_document(
                json_path.read_text(encoding="utf-8")
            )
            prose_parts: list[str] = []
            if json_prose:
                prose_parts.append(json_prose)
            if prose:
                prose_parts.append(prose)
            combined = "\n\n".join(prose_parts) if prose_parts else None
            return rubric_list, combined
        if prose is not None:
            return [dict(x) for x in DEFAULT_STANDALONE_RUBRIC], prose
    return None


def _try_load_generic_rubric_raw_json(rubric_dir: Path) -> dict | None:
    for base in _GENERIC_BASENAMES:
        json_path = rubric_dir / f"{base}.json"
        if json_path.is_file():
            try:
                return json.loads(json_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return None
    return None


def _rubric_files_exist_for_stem(rubric_dir: Path, stem: str) -> bool:
    return any((rubric_dir / f"{stem}{ext}").is_file() for ext in (".json", ".md", ".txt"))


def _load_rubric_for_stem(
    rubric_dir: Path, stem: str
) -> tuple[list[dict], str | None]:
    rubric_list: list[dict] = [dict(x) for x in DEFAULT_STANDALONE_RUBRIC]
    rubric_text: str | None = None
    json_path = rubric_dir / f"{stem}.json"
    if json_path.is_file():
        rubric_list, json_prose = _parse_rubric_json_document(
            json_path.read_text(encoding="utf-8")
        )
        if json_prose:
            rubric_text = json_prose
    for ext in (".md", ".txt"):
        prose_path = rubric_dir / f"{stem}{ext}"
        if prose_path.is_file():
            prose = prose_path.read_text(encoding="utf-8").strip()
            if prose:
                rubric_text = (rubric_text + "\n\n" + prose).strip() if rubric_text else prose
    return rubric_list, rubric_text


def _rubric_column_is_meaningful(rubric_column: Any) -> bool:
    if rubric_column is None:
        return False
    if isinstance(rubric_column, list) and rubric_column:
        return True
    if isinstance(rubric_column, dict):
        if isinstance(rubric_column.get("sections"), list) and rubric_column["sections"]:
            return True
        for key in ("rubric", "criteria", "items"):
            chunk = rubric_column.get(key)
            if isinstance(chunk, list) and chunk:
                return True
    return False


def resolve_rubric_for_pipeline(
    *,
    rubric_dir: Path,
    assignment_stem: str,
    rubric_column: Any = None,
) -> tuple[dict[RubricType, list[dict]], list[dict], str | None]:
    """
    Resolve ``rubric_rows_by_type``, flat allowlist rows, and optional rubric prose.

    Priority: LMS/DB ``rubric_column`` → per-stem files → four [Generic] pack →
    ``default.json`` / ``generic.*`` → standalone default.
    """
    if _rubric_column_is_meaningful(rubric_column):
        by_type, flat = rubric_column_to_by_type_and_flat(rubric_column)
        prose = None
        if isinstance(rubric_column, dict):
            instr = rubric_column.get("llm_grading_instructions")
            if isinstance(instr, str) and instr.strip():
                prose = instr.strip()
        return by_type, flat, prose

    four_by_type = load_four_generic_rubric_rows_by_type(rubric_dir)
    has_four_pack = four_generic_rubric_files_present(rubric_dir)
    generic = _try_load_generic_rubric(rubric_dir)
    raw_json = _try_load_generic_rubric_raw_json(rubric_dir)

    rubric_list: list[dict]
    rubric_text: str | None = None

    if generic is not None:
        rubric_list, rubric_text = generic
    elif _rubric_files_exist_for_stem(rubric_dir, assignment_stem):
        rubric_list, rubric_text = _load_rubric_for_stem(rubric_dir, assignment_stem)
    elif has_four_pack:
        rubric_list = flat_rubric_rows_from_by_type(four_by_type)
        merged = merge_four_generics_to_sections_document(rubric_dir)
        instr = merged.get("llm_grading_instructions") if isinstance(merged, dict) else None
        rubric_text = str(instr).strip() if isinstance(instr, str) else None
    else:
        rubric_list = [dict(x) for x in DEFAULT_STANDALONE_RUBRIC]

    if raw_json and isinstance(raw_json.get("sections"), list):
        rubric_rows_by_type = build_rubric_rows_by_type(raw_json)
    elif has_four_pack:
        rubric_rows_by_type = four_by_type
    else:
        rubric_rows_by_type = {RubricType.FREE_RESPONSE: rubric_list}

    flat = flat_rubric_rows_from_by_type(rubric_rows_by_type) or rubric_list
    return rubric_rows_by_type, flat, rubric_text


def compose_task_description(
    assignment: Any,
    rubric_text: str | None,
    answer_key_text: str | None,
    rubric_prose: str | None,
) -> str:
    parts: list[str] = []
    base = (
        getattr(assignment, "description", None) or getattr(assignment, "title", None) or ""
    ).strip()
    if base:
        parts.append(base)
    if answer_key_text and str(answer_key_text).strip():
        parts.append(
            "Answer key / reference (instructor context):\n" + str(answer_key_text).strip()
        )
    for block in (rubric_prose, rubric_text):
        if block and str(block).strip():
            parts.append("Additional rubric notes:\n" + str(block).strip())
    return "\n\n".join(parts) if parts else "Grade this submission."


class MultimodalGradingContext:
    """
    Everything :func:`run_multimodal_grading` and
    :func:`app.grading.multimodal.course_evidence_grading_pipeline
    .run_course_submission_evidence_grading_pipeline` need to turn a built ``envelope`` into a
    validated grading dict, but computed once so both entry points share one envelope/rubric/
    pipeline-construction implementation instead of two.
    """

    __slots__ = ("envelope", "pipeline", "flat_rubric", "profile")

    def __init__(
        self,
        envelope: Any,
        pipeline: Any,
        flat_rubric: list[dict],
        profile: dict[str, Any],
    ) -> None:
        self.envelope = envelope
        self.pipeline = pipeline
        self.flat_rubric = flat_rubric
        self.profile = profile


def build_multimodal_grading_context(
    cfg: Config,
    *,
    assignment: Any,
    artifacts_bytes: dict[str, bytes],
    assignment_id: str,
    student_id: str,
    rubric_column: Any = None,
    rubric_text: str | None = None,
    answer_key_text: str | None = None,
    assignment_stem: str | None = None,
    rubric_dir: Path | None = None,
    answer_key_dir: Path | None = None,
    require_answer_key: bool = False,
    modality_hints_extra: dict[str, Any] | None = None,
) -> MultimodalGradingContext:
    """
    Resolve rubric + modality hints, build the ingestion envelope, and construct the
    :class:`~app.grading.multimodal.pipeline.MultimodalGradingPipeline` grading engine — the
    shared setup step for both :func:`run_multimodal_grading` (the standalone autograder's own
    chunker waterfall) and the course/library submission-review pipeline's Evidence Agent entry
    point. Callers turn the returned context into an ``AssignmentGradeResult`` via
    ``context.pipeline.run(context.envelope)`` or
    ``context.pipeline.grade_prebuilt_chunks(context.envelope, chunks, chunker_mode=...)``, then
    pass that result to :func:`finalize_multimodal_grading_output`.
    """
    rubric_dir = rubric_dir or default_rubric_dir()
    answer_key_dir = answer_key_dir or default_answer_key_dir()
    stem = (assignment_stem or getattr(assignment, "title", None) or assignment_id or "").strip()

    plaintext = submission_text_from_artifacts(artifacts_bytes).strip()
    modality = getattr(assignment, "modality", None) or infer_modality_from_artifacts(
        artifacts_bytes
    )
    profile = resolve_modality_profile(assignment, artifacts_bytes, plaintext)
    if profile.get("signals", {}).get("text_too_short_for_grading"):
        _log.warning(
            "multimodal: submission text very short (%s chars); scores may be unreliable",
            profile.get("extracted_text_chars"),
        )

    rubric_rows_by_type, flat_rubric, rubric_prose = resolve_rubric_for_pipeline(
        rubric_dir=rubric_dir,
        assignment_stem=stem,
        rubric_column=rubric_column,
    )

    task_description = augment_prompt_for_modality_profile(
        compose_task_description(assignment, rubric_text, answer_key_text, rubric_prose),
        profile,
    )

    raw_assign_mod = str(getattr(assignment, "modality", None) or "").strip()
    if raw_assign_mod:
        modality_for_hints = normalize_modality_hint_for_multimodal(raw_assign_mod)
    else:
        modality_for_hints = normalize_modality_hint_for_multimodal(
            str(profile.get("modality") or modality or "")
        )

    hints: dict[str, Any] = {
        "answer_key_plaintext": (answer_key_text or "").strip(),
        "modality": modality_for_hints,
        "modality_subtype": str(profile.get("modality_subtype") or "").strip(),
        "answer_key_lookup_stem": stem,
        "blank_assignment_lookup_stem": stem,
        "answer_key_dir": str(answer_key_dir),
    }
    tt = str(getattr(assignment, "task_type", None) or "").strip()
    if tt:
        hints["task_type"] = tt
    cr_out = str(getattr(cfg, "MULTIMODAL_CUSTOM_RUBRIC_OUTPUT_DIR", None) or "").strip()
    if cr_out:
        hints["custom_rubric_output_dir"] = cr_out
    if modality_hints_extra:
        hints.update(modality_hints_extra)

    envelope = ingest_raw_submission(
        assignment_id=str(assignment_id),
        student_id=student_id,
        artifacts=dict(artifacts_bytes),
        extracted_plaintext=plaintext,
        modality_hints=hints,
    )

    mm_cfg = MultimodalGradingConfig(require_answer_key=require_answer_key)
    pipeline = create_multimodal_pipeline_from_app_config(
        cfg,
        multimodal_cfg=mm_cfg,
        rubric_rows_by_type=rubric_rows_by_type,
        classifier=None,
        task_description=task_description,
    )
    return MultimodalGradingContext(
        envelope=envelope, pipeline=pipeline, flat_rubric=flat_rubric, profile=profile
    )


def finalize_multimodal_grading_output(
    mm_result: Any,
    *,
    flat_rubric: list[dict],
    profile: dict[str, Any],
    validate_output: bool = True,
) -> dict[str, Any]:
    """
    Shared tail: an ``AssignmentGradeResult`` (from either ``pipeline.run(...)`` or
    ``pipeline.grade_prebuilt_chunks(...)``) -> the same validated grading-JSON dict shape for
    every multimodal grading entry point (standalone autograder and course/library submission
    review alike).
    """
    out = multimodal_assignment_to_grading_dict(
        mm_result,
        rubric=flat_rubric,
        modality_profile={
            "modality": profile.get("modality"),
            "modality_subtype": profile.get("modality_subtype"),
            "artifact_keys": list(profile.get("artifact_keys") or []),
            "extracted_text_chars": profile.get("extracted_text_chars"),
            "signals": profile.get("signals")
            if isinstance(profile.get("signals"), dict)
            else {},
        },
    )
    shaped = coerce_grading_output_shape(out)
    if validate_output:
        validate_grading_output(shaped)
    return shaped


def run_multimodal_grading(
    cfg: Config,
    *,
    assignment: Any,
    artifacts_bytes: dict[str, bytes],
    assignment_id: str,
    student_id: str,
    rubric_column: Any = None,
    rubric_text: str | None = None,
    answer_key_text: str | None = None,
    assignment_stem: str | None = None,
    rubric_dir: Path | None = None,
    answer_key_dir: Path | None = None,
    require_answer_key: bool = False,
    modality_hints_extra: dict[str, Any] | None = None,
    validate_output: bool = True,
) -> dict[str, Any]:
    """
    Run one multimodal grading pass (same structure as local integration tests): the standalone
    autograder's full chunker waterfall (see :meth:`MultimodalGradingPipeline.run`).

    Returns a grading dict after :func:`coerce_grading_output_shape` (and optional
    :func:`validate_grading_output`).
    """
    context = build_multimodal_grading_context(
        cfg,
        assignment=assignment,
        artifacts_bytes=artifacts_bytes,
        assignment_id=assignment_id,
        student_id=student_id,
        rubric_column=rubric_column,
        rubric_text=rubric_text,
        answer_key_text=answer_key_text,
        assignment_stem=assignment_stem,
        rubric_dir=rubric_dir,
        answer_key_dir=answer_key_dir,
        require_answer_key=require_answer_key,
        modality_hints_extra=modality_hints_extra,
    )
    mm_result = context.pipeline.run(context.envelope)
    return finalize_multimodal_grading_output(
        mm_result,
        flat_rubric=context.flat_rubric,
        profile=context.profile,
        validate_output=validate_output,
    )
