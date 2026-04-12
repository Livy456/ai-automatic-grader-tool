"""
Multimodal grading pipeline: ingestion → chunking → rubric routing → grading →
uncertainty → aggregation → output.

See ``AGT_platform/backend/docs/multimodal_grading_pipeline.md`` for architecture.
"""

from __future__ import annotations

from .grading_output import multimodal_assignment_to_grading_dict
from .model_runner import ChunkModelRunner, MockChunkModelRunner, MultiModelChunkRunner
from .semantic_confidence import (
    aggregate_assignment_confidence,
    cluster_assignment,
    compute_semantic_entropy,
    estimate_cluster_distribution,
    normalize_entropy_to_confidence,
    summarize_chunk_confidence_from_counts,
)
from .pipeline import (
    MultimodalGradingPipeline,
    PipelineArtifactStore,
    build_envelope_from_plaintext,
    create_multimodal_pipeline_from_app_config,
)
from .schemas import (
    AssignmentGradeResult,
    ChunkGradeOutcome,
    GradingChunk,
    Modality,
    MultimodalGradingConfig,
    ParsedChunkGrade,
    ReviewStatus,
    RubricType,
    SampledChunkGrade,
    TaskType,
)

__all__ = [
    "aggregate_assignment_confidence",
    "AssignmentGradeResult",
    "ChunkGradeOutcome",
    "ChunkModelRunner",
    "cluster_assignment",
    "compute_semantic_entropy",
    "estimate_cluster_distribution",
    "GradingChunk",
    "MockChunkModelRunner",
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
]
