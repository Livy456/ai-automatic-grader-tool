"""Smoke tests for multimodal grading pipeline scaffolding."""

from __future__ import annotations

import unittest

from app.grading.multimodal import (
    MultimodalGradingConfig,
    MultimodalGradingPipeline,
    Modality,
    PipelineArtifactStore,
    TaskType,
)
from app.grading.multimodal.pipeline import build_envelope_from_plaintext
from app.grading.multimodal.schemas import GradingChunk, ReviewStatus, RubricType
from app.grading.multimodal.model_runner import MockChunkModelRunner
from app.grading.multimodal.rubric_router import route_rubric
from app.grading.multimodal.entropy import semantic_entropy_from_cluster_counts


def _sample_json(norm: float, total: float = 10.0) -> str:
    return (
        '{"rubric_type":"free_response",'
        f'"criterion_scores":[{{"name":"A","score":{norm * 10},"max_points":10}}],'
        '"criterion_justifications":["e"],'
        f'"total_score":{total},"normalized_score":{norm},'
        '"confidence_note":"","review_flag":false}'
    )


class MultimodalRoutingTests(unittest.TestCase):
    def test_deterministic_routing_programming(self) -> None:
        ch = GradingChunk(
            chunk_id="c1",
            assignment_id="a1",
            student_id="s1",
            question_id="q1",
            modality=Modality.CODE,
            task_type=TaskType.SCAFFOLDED_CODING,
            extracted_text="print(1)",
        )
        route_rubric(ch, rubric_rows_by_type={})
        self.assertEqual(ch.rubric_type, RubricType.PROGRAMMING_SCAFFOLDED)

    def test_semantic_entropy_two_clusters(self) -> None:
        h = semantic_entropy_from_cluster_counts({"A": 1, "B": 1})
        self.assertGreater(h, 0.0)


class MultimodalPipelineRunTests(unittest.TestCase):
    def test_full_run_mock_runner(self) -> None:
        env = build_envelope_from_plaintext(
            assignment_id="a1",
            student_id="s1",
            plaintext=(
                "=== NOTEBOOK MARKDOWN (ipynb) ===\n# Q1\nDo thing.\n\n"
                "=== NOTEBOOK CODE (ipynb) ===\nprint('hello')\n"
            ),
            modality_hints={"modality": "notebook", "task_type": "free_response_short"},
        )
        cfg = MultimodalGradingConfig(
            confidence_ai_auto_accept_min=0.5,
            confidence_ai_caution_min=0.25,
            score_spread_high=2.0,
        )
        runner = MockChunkModelRunner(
            responses=[
                _sample_json(0.8),
                _sample_json(0.85),
            ]
        )
        pipe = MultimodalGradingPipeline(cfg, runner, rubric_rows_by_type={})
        art = PipelineArtifactStore()
        result = pipe.run(env, artifacts=art)
        self.assertIsNotNone(result.assignment_normalized_score)
        self.assertGreaterEqual(result.assignment_ai_confidence, 0.0)
        self.assertLessEqual(result.assignment_ai_confidence, 1.0)
        self.assertTrue(result.chunk_results)
        for ch in result.chunk_results:
            self.assertGreaterEqual(ch.ai_confidence, 0.0)
            self.assertLessEqual(ch.ai_confidence, 1.0)
            self.assertIn("confidence_trace", ch.stage_artifacts)
        self.assertIn("chunking", art.stages)
        self.assertIn("pipeline_audit", result.stage_artifacts)
        self.assertIn("assignment_confidence_trace", result.stage_artifacts)


if __name__ == "__main__":
    unittest.main()
