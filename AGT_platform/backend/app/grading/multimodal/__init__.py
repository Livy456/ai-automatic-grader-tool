"""
Multimodal grading pipeline: ingestion → chunking → rubric routing → per-chunk grading →
uncertainty → aggregation → output.

**Scope:** This package owns the core multimodal orchestration only (:mod:`pipeline`,
:mod:`pipeline_runner`, :mod:`model_runner`, ``schemas``, :mod:`aggregator`,
:mod:`review_router`). Submission parsing, chunking, LLM routing/prompts, output-shape
validation, AI-confidence math, and rubric routing live in their own sibling/top-level
packages (:mod:`app.grading.parsing`, :mod:`app.grading.chunking`, :mod:`app.llm`,
:mod:`app.grading.grading_output`, :mod:`app.grading.confidence_calculation`,
:mod:`app.grading.rubric_routing`) so they can be reused outside multimodal without circular
imports.

**Celery / DB grading:** :mod:`app.tasks` calls
:func:`~app.grading.multimodal.course_multimodal_runner.run_db_submission_multimodal_pipeline`
and :func:`~app.grading.multimodal.course_multimodal_runner.run_standalone_multimodal_pipeline`
(both wrap :class:`MultimodalGradingPipeline`). Local multimodal runs use the same factory;
tests live under ``tests/test_multimodal_pipeline.py``.

See ``AGT_platform/backend/docs/multimodal_grading_pipeline.md`` for architecture.

The public names below are resolved **lazily** (`PEP 562 <https://peps.python.org/pep-0562/>`_
module ``__getattr__``) rather than imported eagerly at package-import time. Several of them
(``.pipeline``, ``.pipeline_runner``) transitively import from the sibling ``parsing`` /
``chunking`` / ``rubric_routing`` / ``confidence_calculation`` / ``grading_output`` packages
and from :mod:`app.llm` — eagerly importing that whole web here would risk a circular import
the moment any of those siblings is imported before this package finishes initializing.
"""

from __future__ import annotations

import importlib
from typing import Any

__all__ = [
    "aggregate_assignment_confidence",
    "AssignmentGradeResult",
    "ChunkGradeOutcome",
    "ChunkModelRunner",
    "cluster_assignment",
    "compute_semantic_entropy",
    "estimate_cluster_distribution",
    "GradingChunk",
    "MultiModelChunkRunner",
    "multimodal_assignment_to_grading_dict",
    "normalize_entropy_to_confidence",
    "summarize_chunk_confidence_from_counts",
    "Modality",
    "MultimodalGradingConfig",
    "MultimodalGradingPipeline",
    "ParsedChunkGrade",
    "PipelineArtifactStore",
    "ReviewStatus",
    "RubricType",
    "SampledChunkGrade",
    "TaskType",
    "build_envelope_from_plaintext",
    "create_multimodal_pipeline_from_app_config",
    "default_rubric_dir",
    "run_multimodal_grading",
]

# name -> module that actually defines it. Leading-dot entries are submodules of this
# package (resolved relative to __name__); others are fully-qualified absolute imports.
_ATTR_SOURCES = {
    "multimodal_assignment_to_grading_dict": "app.grading.grading_output.grading_output",
    "aggregate_assignment_confidence": "app.grading.confidence_calculation.semantic_confidence",
    "cluster_assignment": "app.grading.confidence_calculation.semantic_confidence",
    "compute_semantic_entropy": "app.grading.confidence_calculation.semantic_confidence",
    "estimate_cluster_distribution": "app.grading.confidence_calculation.semantic_confidence",
    "normalize_entropy_to_confidence": "app.grading.confidence_calculation.semantic_confidence",
    "summarize_chunk_confidence_from_counts": "app.grading.confidence_calculation.semantic_confidence",
    "ChunkModelRunner": ".model_runner",
    "MultiModelChunkRunner": ".model_runner",
    "MultimodalGradingPipeline": ".pipeline",
    "PipelineArtifactStore": ".pipeline",
    "build_envelope_from_plaintext": ".pipeline",
    "create_multimodal_pipeline_from_app_config": ".pipeline",
    "default_rubric_dir": ".pipeline",
    "run_multimodal_grading": ".pipeline_runner",
    "AssignmentGradeResult": "app.grading.schemas",
    "ChunkGradeOutcome": "app.grading.schemas",
    "GradingChunk": "app.grading.schemas",
    "Modality": "app.grading.schemas",
    "MultimodalGradingConfig": "app.grading.schemas",
    "ParsedChunkGrade": "app.grading.schemas",
    "ReviewStatus": "app.grading.schemas",
    "RubricType": "app.grading.schemas",
    "SampledChunkGrade": "app.grading.schemas",
    "TaskType": "app.grading.schemas",
}


def __getattr__(name: str) -> Any:
    source = _ATTR_SOURCES.get(name)
    if source is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(source, __name__ if source.startswith(".") else None)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
