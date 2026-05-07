"""
Opt-in research harness: repeat multimodal grading many times for one or more assignments.

**Outputs** (under repository root ``research analysis/``):

- ``research analysis/<sanitized_assignment_id>_run_<NN>/grade_output.json`` — full grading
  dict for that run (same shape as ``grading_output/*_grade_output.json``).
- ``research analysis/<sanitized_assignment_id>_research_scores.csv`` — one row per
  question per run; columns include ``question_id``, ``question score``, ``rubric_type``,
  ``assignment_id``, ``run number``, and ``assignment_score`` (overall normalized score
  for that run, repeated on each question row).

**Enable:** set ``MULTIMODAL_RESEARCH_ASSIGNMENT_ID`` to either:

- A single assignment **stem** under ``assignments_to_grade/`` (e.g. ``[Student 1] Journal Entry 2``), or
- A **JSON array** of stems, e.g. ``["[Student 1] Journal Entry 1","[Student 2] week 2 part 1 colab"]``.
  Stems run **one after another** (same order as the list). Invalid JSON is treated as one
  literal stem, so stems that start with ``[`` must be JSON-encoded as above.

**Resume (default on):** set ``MULTIMODAL_RESEARCH_RESUME=0``/``false``/``off`` to always re-run
all ``MULTIMODAL_RESEARCH_RUN_COUNT`` passes from scratch. When resume is on (default), a stem
is skipped if ``research analysis/<sanitized_stem>_run_01`` … ``_run_<N>/grade_output.json`` all
exist; otherwise only **missing** run folders are executed, then the CSV is rebuilt from every
run’s ``grade_output.json`` so you can stop and continue until each stem reaches ``N`` runs.

Optionally set ``MULTIMODAL_RESEARCH_RUN_COUNT`` (default **30**, max 100).

**Skip:** if the env var is unset, the test skips so CI and normal ``pytest`` runs do not
issue dozens of paid API calls.

Example (single assignment)::

    MULTIMODAL_RESEARCH_ASSIGNMENT_ID='[Student 1] Journal Entry 2' \\
    MULTIMODAL_RESEARCH_RUN_COUNT=5 \\
    pytest tests/test_multimodal_research_runs.py -v

Example (multiple assignments, 30 runs each)::

    MULTIMODAL_RESEARCH_ASSIGNMENT_ID='["[Student 1] Journal Entry 1","[Student 2] Journal Entry 1"]' \\
    pytest tests/test_multimodal_research_runs.py -v

**Speed / parity toggles (research only):**

- ``MULTIMODAL_RESEARCH_USE_CHUNK_CACHE`` — when ``on``/unset (default **on**), run 1 writes
  ``research analysis/<stem>_multimodal_grading_chunks_cache.json`` via
  ``multimodal_chunk_cache_write_path``; runs 2+ reuse it via ``multimodal_chunk_cache_path`` so
  chunking and cached unit embeddings are skipped (grading LLM work still runs each run).
  Set to ``0``/``false``/``off`` to force a full rebuild every run (legacy behavior).
- ``MULTIMODAL_RESEARCH_FAST=on`` — sets ``MULTIMODAL_SAMPLES_PER_MODEL=1`` and
  ``MULTIMODAL_RAG_EMBED_UNITS=false`` for the test process only (restored after the test).
  **Latency only:** not parity with production multimodal + per-unit RAG embeddings.

Pipeline-wide (optional, not research-specific):

- ``MULTIMODAL_RAG_EMBED_BATCH=on`` — batch OpenAI / SentenceTransformer embedding calls in
  :func:`enrich_chunks_with_rag_embeddings`.
- ``MULTIMODAL_RAG_EMBED_BATCH_SIZE`` — max inputs per OpenAI batch (default **64**, max **128**).
- ``MULTIMODAL_RAG_PREWINDOW_EMBED=on`` — embed the first ``RAG_EMBED_MAX_CHARS`` of submission
  plaintext before chunking; non-trio chunks may **reuse** that vector when chunk text lies in
  that span and passes ``MULTIMODAL_RAG_PREWINDOW_REUSE_MAX_FRAC`` (default **0.85**).
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
_SUPPORTED_ASSIGNMENT_SUFFIXES = getattr(_tglf, "_SUPPORTED_SUFFIXES", frozenset())

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


def _research_use_chunk_cache() -> bool:
    """Default **on**: reuse serialized chunks (and cached embeddings) after run 1."""
    raw = os.getenv("MULTIMODAL_RESEARCH_USE_CHUNK_CACHE", "").strip().lower()
    if not raw:
        return True
    return raw in ("1", "true", "yes", "on")


def _research_fast_mode() -> bool:
    """Fast research profile: 1 sample per model, no per-unit RAG embed (env restored after test)."""
    return os.getenv("MULTIMODAL_RESEARCH_FAST", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def research_modality_hints_for_run(
    *,
    run_index: int,
    n_runs: int,
    use_chunk_cache: bool,
    cache_path: Path,
    modality_subtype: str,
    answer_key_plaintext: str,
) -> dict[str, str]:
    """Build ``modality_hints`` for one research pipeline run (chunk cache read/write)."""
    hints: dict[str, str] = {
        "modality_subtype": modality_subtype,
        "answer_key_plaintext": answer_key_plaintext,
    }
    if use_chunk_cache:
        if run_index == 1:
            hints["multimodal_chunk_cache_write_path"] = str(cache_path)
        elif run_index >= 2 and n_runs > 1:
            hints["multimodal_chunk_cache_path"] = str(cache_path)
    return hints


def _parse_research_assignment_ids() -> list[str]:
    """
    Parse ``MULTIMODAL_RESEARCH_ASSIGNMENT_ID``.

    - If the value is valid JSON and decodes to a list of strings, return that list
      (empty entries dropped).
    - If it decodes to a single JSON string, return a one-element list.
    - Otherwise treat the entire trimmed env value as one assignment stem (so stems that
      are not valid JSON, including ``[Student 1] foo``, remain one id).
    """
    raw = os.getenv("MULTIMODAL_RESEARCH_ASSIGNMENT_ID", "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return [raw]
    if isinstance(parsed, list):
        out = [str(x).strip() for x in parsed if str(x).strip()]
        return out if out else []
    if isinstance(parsed, str):
        s = parsed.strip()
        return [s] if s else []
    return [raw]


def _research_assignment_ids_configured() -> bool:
    return bool(_parse_research_assignment_ids())


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


def _execute_research_runs_for_stem(
    case: unittest.TestCase,
    *,
    stem: str,
    n_runs: int,
    stems_total: int,
    groups: dict,
    cfg: Config,
    generic_rubric,
    four_by_type,
    has_four_pack: bool,
    raw_json,
) -> None:
    """Run ``n_runs`` full multimodal pipeline passes for one stem; safe for ``to_thread``."""
    if stem not in groups:
        raise AssertionError(
            f"Internal: stem {stem!r} missing from groups (should be pre-validated)."
        )

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
        case.fail(
            "No rubric for this assignment: add four [Generic] JSON files, "
            "rubric/default.json, or rubric/<stem>.json."
        )

    if raw_json and isinstance(raw_json.get("sections"), list):
        rubric_rows_by_type = _build_rubric_rows_by_type(raw_json)
    elif has_four_pack:
        rubric_rows_by_type = four_by_type
    else:
        rubric_rows_by_type = {RubricType.FREE_RESPONSE: rubric_list}

    task_desc_parts: list[str] = [
        f"Research repeat-run harness ({n_runs} runs per assignment; "
        f"{stems_total} assignment(s))."
    ]
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
    case.assertGreater(len(plain.strip()), 0, "Empty submission text")
    modality_profile = resolve_modality_profile(
        SimpleNamespace(title=stem, modality=None, rubric=rubric_list, description=""),
        artifacts,
        plain,
    )

    safe = _sanitize_assignment_fs(stem)
    csv_path = RESEARCH_ROOT / f"{safe}_research_scores.csv"
    chunk_cache_path = RESEARCH_ROOT / f"{safe}_multimodal_grading_chunks_cache.json"
    use_chunk_cache = _research_use_chunk_cache()

    csv_rows: list[dict[str, str | float | int]] = []
    n_questions = 0

    for run in range(1, n_runs + 1):
        ak_plain = resolve_answer_key_plaintext(stem, ANSWER_KEY_DIR)[0]
        modality_hints = research_modality_hints_for_run(
            run_index=run,
            n_runs=n_runs,
            use_chunk_cache=use_chunk_cache,
            cache_path=chunk_cache_path,
            modality_subtype=str(modality_profile.get("modality_subtype") or ""),
            answer_key_plaintext=ak_plain,
        )
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

        qgs = [
            x for x in (result.get("question_grades") or []) if isinstance(x, dict)
        ]
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
    case.assertEqual(len(csv_rows), n_runs * n_questions)
    case.assertTrue(csv_path.is_file())


async def _run_all_stems_concurrently(
    case: unittest.TestCase,
    *,
    stems: list[str],
    n_runs: int,
    groups: dict,
    cfg: Config,
    generic_rubric,
    four_by_type,
    has_four_pack: bool,
    raw_json,
) -> None:
    stems_total = len(stems)
    cap = _research_max_concurrent_assignments()
    max_workers = max(1, min(cap, stems_total))
    sem = asyncio.Semaphore(max_workers)
    _log.info(
        "Research harness: %s assignment(s), %s runs each, up to %s concurrent (cap=%s)",
        stems_total,
        n_runs,
        max_workers,
        cap,
    )

    async def guarded(stem: str) -> None:
        async with sem:
            await asyncio.to_thread(
                partial(
                    _execute_research_runs_for_stem,
                    case,
                    stem=stem,
                    n_runs=n_runs,
                    stems_total=stems_total,
                    groups=groups,
                    cfg=cfg,
                    generic_rubric=generic_rubric,
                    four_by_type=four_by_type,
                    has_four_pack=has_four_pack,
                    raw_json=raw_json,
                )
            )

    await asyncio.gather(*(guarded(s) for s in stems))


@unittest.skipUnless(
    _fixtures_layout_ok(),
    "Requires assignments_to_grade/ and rubric/ at repository root.",
)
@unittest.skipUnless(
    _research_assignment_ids_configured(),
    "Set MULTIMODAL_RESEARCH_ASSIGNMENT_ID to a stem or JSON list of stems under assignments_to_grade/.",
)
class TestMultimodalResearchRepeatedRuns(unittest.TestCase):
    """Full pipeline runs per assignment (count configurable) for variance / reliability research.

    Multiple assignment stems run concurrently (default cap: 3) under a semaphore; each stem’s
    ``n_runs`` loop stays sequential inside its worker thread.
    """

    def test_research_multimodal_pipeline_repeat_runs(self) -> None:
        stems = _parse_research_assignment_ids()
        self.assertTrue(stems, "MULTIMODAL_RESEARCH_ASSIGNMENT_ID parsed to an empty list")
        n_runs = _research_run_count()
        groups = _assignment_groups()

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
        raw_json = _try_load_generic_rubric_raw_json()

        RESEARCH_ROOT.mkdir(parents=True, exist_ok=True)

        missing = [s for s in stems if s not in groups]
        if missing:
            raise unittest.SkipTest(
                f"No files for assignment stem(s) {missing!r} under {ASSIGNMENTS_DIR}. "
                f"Basenames must match a file stem and use a supported suffix "
                f"(e.g. audio: .mp3, .wav, .m4a, .webm, .mpa): "
                f"{sorted(_SUPPORTED_ASSIGNMENT_SUFFIXES)!r}. "
                f"Known stems (sample): {sorted(list(groups))[:12]!r}"
            )

        fast = _research_fast_mode()
        prev_rag_units = os.environ.get("MULTIMODAL_RAG_EMBED_UNITS") if fast else None
        if fast:
            os.environ["MULTIMODAL_RAG_EMBED_UNITS"] = "false"
            cfg.MULTIMODAL_SAMPLES_PER_MODEL = 1
        try:
            asyncio.run(
                _run_all_stems_concurrently(
                    self,
                    stems=stems,
                    n_runs=n_runs,
                    groups=groups,
                    cfg=cfg,
                    generic_rubric=generic_rubric,
                    four_by_type=four_by_type,
                    has_four_pack=has_four_pack,
                    raw_json=raw_json,
                )
            )
        finally:
            if fast:
                if prev_rag_units is None:
                    os.environ.pop("MULTIMODAL_RAG_EMBED_UNITS", None)
                else:
                    os.environ["MULTIMODAL_RAG_EMBED_UNITS"] = prev_rag_units


class TestResearchHarnessChunkCacheHints(unittest.TestCase):
    """Unit tests for research chunk-cache hint wiring (no API calls)."""

    def test_run1_writes_cache_path(self) -> None:
        p = Path("/tmp/research_cache_test.json")
        h = research_modality_hints_for_run(
            run_index=1,
            n_runs=5,
            use_chunk_cache=True,
            cache_path=p,
            modality_subtype="nb",
            answer_key_plaintext="ak",
        )
        self.assertEqual(h["multimodal_chunk_cache_write_path"], str(p))
        self.assertNotIn("multimodal_chunk_cache_path", h)

    def test_run2_reads_cache_path(self) -> None:
        p = Path("/tmp/research_cache_test2.json")
        h = research_modality_hints_for_run(
            run_index=2,
            n_runs=5,
            use_chunk_cache=True,
            cache_path=p,
            modality_subtype="nb",
            answer_key_plaintext="ak",
        )
        self.assertEqual(h["multimodal_chunk_cache_path"], str(p))
        self.assertNotIn("multimodal_chunk_cache_write_path", h)

    def test_cache_disabled_no_paths(self) -> None:
        p = Path("/tmp/x.json")
        h = research_modality_hints_for_run(
            run_index=1,
            n_runs=3,
            use_chunk_cache=False,
            cache_path=p,
            modality_subtype="",
            answer_key_plaintext="",
        )
        self.assertNotIn("multimodal_chunk_cache_path", h)
        self.assertNotIn("multimodal_chunk_cache_write_path", h)


if __name__ == "__main__":
    unittest.main()
