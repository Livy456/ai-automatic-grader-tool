"""
Opt-in research harness: repeat multimodal grading many times for one assignment.

**Outputs** (under repository root ``research analysis/``):

- ``research analysis/<sanitized_assignment_id>_run_<NN>/grade_output.json`` — full grading
  dict for that run (same shape as ``grading_output/*_grade_output.json``).
- ``research analysis/<sanitized_assignment_id>_research_scores.csv`` — one row per
  question per run; columns include ``question_id``, ``question score``, ``rubric_type``,
  ``assignment_id``, ``run number``, and ``assignment_score`` (overall normalized score
  for that run, repeated on each question row).

**Enable:** set ``MULTIMODAL_RESEARCH_ASSIGNMENT_ID`` to an assignment **stem** that exists
under ``assignments_to_grade/`` (e.g. ``[Student 1] Journal Entry 2``). Optionally set
``MULTIMODAL_RESEARCH_RUN_COUNT`` (default **30**, max 100).

**Skip:** if the env var is unset, the test skips so CI and normal ``pytest`` runs do not
issue dozens of paid API calls.

Example::

    MULTIMODAL_RESEARCH_ASSIGNMENT_ID='[Student 1] Journal Entry 2' \\
    MULTIMODAL_RESEARCH_RUN_COUNT=5 \\
    pytest tests/test_multimodal_research_runs.py -v
"""

from __future__ import annotations

import csv
import json
import importlib.util
import logging
import os
import re
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.config import Config
from app.grading.llm_router import build_multimodal_grading_clients
from app.grading.modality_resolution import resolve_modality_profile
from app.grading.multimodal import (
    create_multimodal_pipeline_from_app_config,
    multimodal_assignment_to_grading_dict,
)
from app.grading.multimodal.generic_rubric_loader import (
    flat_rubric_rows_from_by_type,
    four_generic_rubric_files_present,
    load_four_generic_rubric_rows_by_type,
    merge_four_generics_to_sections_document,
)
from app.grading.multimodal.ingestion import ingest_raw_submission
from app.grading.multimodal.schemas import RubricType
from app.grading.answer_key_resolve import resolve_answer_key_plaintext
from app.grading.output_schema import validate_grading_output
from app.grading.submission_text import submission_text_from_artifacts


def _grading_pipeline_local_fixtures():
    """Load sibling ``test_grading_pipeline_local_files`` (path-based; avoids import path issues)."""
    path = Path(__file__).resolve().parent / "test_grading_pipeline_local_files.py"
    spec = importlib.util.spec_from_file_location(
        "_grading_pipeline_local_fixtures", path
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load fixtures from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_tglf = _grading_pipeline_local_fixtures()
REPO_ROOT = _tglf.REPO_ROOT
ANSWER_KEY_DIR = _tglf.ANSWER_KEY_DIR
ASSIGNMENTS_DIR = _tglf.ASSIGNMENTS_DIR
RUBRIC_DIR = _tglf.RUBRIC_DIR
_fixtures_layout_ok = _tglf._fixtures_layout_ok
_assignment_groups = _tglf._assignment_groups
_build_artifacts = _tglf._build_artifacts
_build_rubric_rows_by_type = _tglf._build_rubric_rows_by_type
_load_rubric_for_stem = _tglf._load_rubric_for_stem
_rubric_files_exist_for_stem = _tglf._rubric_files_exist_for_stem
_try_load_generic_rubric = _tglf._try_load_generic_rubric
_try_load_generic_rubric_raw_json = _tglf._try_load_generic_rubric_raw_json

_log = logging.getLogger(__name__)

RESEARCH_ROOT = REPO_ROOT / "research analysis"


def _sanitize_assignment_fs(assignment_id: str) -> str:
    """Filesystem-safe folder/file stem from assignment stem."""
    s = re.sub(r"[^\w\-.]+", "_", (assignment_id or "").strip())
    s = s.strip("_") or "assignment"
    return s[:180]


def _research_run_count() -> int:
    raw = os.getenv("MULTIMODAL_RESEARCH_RUN_COUNT", "").strip()
    if not raw:
        return 30
    try:
        return max(1, min(int(raw, 10), 100))
    except ValueError:
        return 30


def _rubric_type_map_from_audit(audit: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in audit.get("rubric_routing") or []:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("chunk_id") or "").strip()
        if cid:
            out[cid] = str(row.get("rubric_type") or "").strip()
    return out


def _question_id_from_grade_row(qg: dict) -> str:
    src = str(qg.get("_source_chunk_id") or qg.get("chunk_id") or "").strip()
    if ":" in src:
        tail = src.rsplit(":", 1)[-1]
        if tail.startswith("pair_") or tail:
            return tail
    return src or "unknown"


@unittest.skipUnless(
    _fixtures_layout_ok(),
    "Requires assignments_to_grade/ and rubric/ at repository root.",
)
@unittest.skipUnless(
    bool(os.getenv("MULTIMODAL_RESEARCH_ASSIGNMENT_ID", "").strip()),
    "Set MULTIMODAL_RESEARCH_ASSIGNMENT_ID to an assignment stem under assignments_to_grade/.",
)
class TestMultimodalResearchRepeatedRuns(unittest.TestCase):
    """30 (configurable) full pipeline runs for variance / reliability research."""

    def test_research_multimodal_pipeline_repeat_runs(self) -> None:
        stem = os.getenv("MULTIMODAL_RESEARCH_ASSIGNMENT_ID", "").strip()
        n_runs = _research_run_count()
        groups = _assignment_groups()
        if stem not in groups:
            raise unittest.SkipTest(
                f"No files for assignment stem {stem!r} under {ASSIGNMENTS_DIR}. "
                f"Known stems (sample): {sorted(list(groups))[:12]!r}"
            )

        cfg = Config()
        cfg.MULTIMODAL_SAMPLES_PER_MODEL = max(
            1, int(getattr(cfg, "MULTIMODAL_SAMPLES_PER_MODEL", 1) or 1)
        )
        if not build_multimodal_grading_clients(cfg):
            raise unittest.SkipTest(
                "OPENAI_API_KEY and OPENAI_MULTIMODAL_GRADING_MODEL required for multimodal grading."
            )

        generic_rubric = _try_load_generic_rubric()
        four_by_type = load_four_generic_rubric_rows_by_type(RUBRIC_DIR)
        has_four_pack = four_generic_rubric_files_present(RUBRIC_DIR)
        if generic_rubric is not None:
            rubric_list, rubric_text = generic_rubric
        elif _rubric_files_exist_for_stem(stem):
            rubric_list, rubric_text = _load_rubric_for_stem(stem)
        elif has_four_pack:
            rubric_list = flat_rubric_rows_from_by_type(four_by_type)
            merged = merge_four_generics_to_sections_document(RUBRIC_DIR)
            instr = (
                merged.get("llm_grading_instructions")
                if isinstance(merged, dict)
                else None
            )
            rubric_text = str(instr).strip() if isinstance(instr, str) else None
        else:
            self.fail(
                "No rubric for this assignment: add four [Generic] JSON files, "
                "rubric/default.json, or rubric/<stem>.json."
            )

        raw_json = _try_load_generic_rubric_raw_json()
        if raw_json and isinstance(raw_json.get("sections"), list):
            rubric_rows_by_type = _build_rubric_rows_by_type(raw_json)
        elif has_four_pack:
            rubric_rows_by_type = four_by_type
        else:
            rubric_rows_by_type = {RubricType.FREE_RESPONSE: rubric_list}

        task_desc_parts: list[str] = [f"Research repeat-run harness ({n_runs} runs)."]
        if rubric_text:
            task_desc_parts.append(rubric_text)
        task_description = "\n\n".join(task_desc_parts)

        pipeline = create_multimodal_pipeline_from_app_config(
            cfg,
            rubric_rows_by_type=rubric_rows_by_type,
            task_description=task_description,
        )

        artifacts = _build_artifacts(groups[stem])
        plain = submission_text_from_artifacts(artifacts)
        self.assertGreater(len(plain.strip()), 0, "Empty submission text")
        modality_profile = resolve_modality_profile(
            SimpleNamespace(title=stem, modality=None, rubric=rubric_list, description=""),
            artifacts,
            plain,
        )

        safe = _sanitize_assignment_fs(stem)
        RESEARCH_ROOT.mkdir(parents=True, exist_ok=True)
        csv_path = RESEARCH_ROOT / f"{safe}_research_scores.csv"

        csv_rows: list[dict[str, str | float | int]] = []
        n_questions = 0

        for run in range(1, n_runs + 1):
            with self.subTest(run=run):
                modality_hints = {
                    "modality_subtype": str(
                        modality_profile.get("modality_subtype") or ""
                    ),
                    "answer_key_plaintext": resolve_answer_key_plaintext(
                        stem, ANSWER_KEY_DIR
                    )[0],
                }
                envelope = ingest_raw_submission(
                    assignment_id=stem,
                    student_id="research_run",
                    artifacts=dict(artifacts),
                    extracted_plaintext=plain,
                    modality_hints=modality_hints,
                )
                mm_result = pipeline.run(envelope)
                result = multimodal_assignment_to_grading_dict(
                    mm_result,
                    rubric=rubric_list,
                    modality_profile=modality_profile,
                )
                validate_grading_output(result)

                qgs = [x for x in (result.get("question_grades") or []) if isinstance(x, dict)]
                if run == 1:
                    n_questions = len(qgs)

                run_folder = RESEARCH_ROOT / f"{safe}_run_{run:02d}"
                run_folder.mkdir(parents=True, exist_ok=True)
                json_path = run_folder / "grade_output.json"
                json_path.write_text(
                    json.dumps(result, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )

                audit = (result.get("_multimodal_pipeline_audit") or {})  # type: ignore[arg-type]
                rt_by_chunk = _rubric_type_map_from_audit(audit)
                assign_score = float((result.get("overall") or {}).get("score") or 0.0)

                for qg in qgs:
                    qid = _question_id_from_grade_row(qg)
                    src_cid = str(qg.get("_source_chunk_id") or "").strip()
                    rt = rt_by_chunk.get(src_cid, "")
                    q_score = float((qg.get("overall") or {}).get("score") or 0.0)
                    csv_rows.append(
                        {
                            "question_id": qid,
                            "question score": round(q_score, 6),
                            "rubric_type": rt,
                            "assignment_id": stem,
                            "run number": run,
                            "assignment_score": round(assign_score, 6),
                        }
                    )

        fieldnames = [
            "question_id",
            "question score",
            "rubric_type",
            "assignment_id",
            "run number",
            "assignment_score",
        ]
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for row in csv_rows:
                w.writerow(row)

        _log.warning(
            "Research runs complete: assignment=%r runs=%s csv=%s json_pattern=%s_run_*",
            stem,
            n_runs,
            csv_path,
            safe,
        )
        self.assertEqual(len(csv_rows), n_runs * n_questions)
        self.assertTrue(csv_path.is_file())


if __name__ == "__main__":
    unittest.main()
