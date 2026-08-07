"""Grading pipeline integration for Assignment Creation rubric routing."""
from __future__ import annotations

import unittest

from app.grading.grading_output.grading_output import (
    _max_by_name_for_chunk,
    multimodal_assignment_to_grading_dict,
)
from app.grading.rubric_routing.assignment_rubric_routing_agent import (
    apply_assignment_creation_rubric_routing,
)
from app.grading.schemas import (
    AssignmentGradeResult,
    ChunkGradeOutcome,
    GradingChunk,
    Modality,
    ReviewStatus,
    RubricType,
    TaskType,
)


class ApplyAssignmentCreationRubricRoutingTests(unittest.TestCase):
    def test_stamps_rubric_rows_from_saved_json(self) -> None:
        chunk = GradingChunk(
            chunk_id="a1:s1:0:1",
            assignment_id="1",
            student_id="s1",
            question_id="1",
            modality=Modality.WRITTEN,
            task_type=TaskType.UNKNOWN,
            extracted_text="student answer",
        )
        applied = apply_assignment_creation_rubric_routing(
            [chunk],
            criteria_by_question_id={
                "1": [
                    {
                        "id": "code",
                        "name": "Code quality",
                        "criterion": "Code quality",
                        "max_score": 5,
                        "max_points": 5,
                    }
                ]
            },
        )
        self.assertEqual(applied, 1)
        self.assertEqual(chunk.routing_reason, "assignment_creation_rubric_routing")
        self.assertEqual(len(chunk.rubric_rows), 1)
        self.assertEqual(chunk.rubric_rows[0]["name"], "Code quality")
        self.assertEqual(chunk.rubric_type, RubricType.FREE_RESPONSE)

    def test_order_fallback_when_question_id_mismatches(self) -> None:
        chunks = [
            GradingChunk(
                chunk_id="a1:s1:0:q1",
                assignment_id="1",
                student_id="s1",
                question_id="unexpected-1",
                modality=Modality.WRITTEN,
                task_type=TaskType.UNKNOWN,
                extracted_text="a",
            ),
            GradingChunk(
                chunk_id="a1:s1:1:q2",
                assignment_id="1",
                student_id="s1",
                question_id="unexpected-2",
                modality=Modality.WRITTEN,
                task_type=TaskType.UNKNOWN,
                extracted_text="b",
            ),
        ]
        applied = apply_assignment_creation_rubric_routing(
            chunks,
            criteria_by_question_id={
                "q1": [{"name": "Clarity", "max_points": 1}],
                "q2": [{"name": "Depth of Understanding", "max_points": 2}],
            },
        )
        self.assertEqual(applied, 2)
        self.assertEqual(chunks[0].rubric_rows[0]["name"], "Clarity")
        self.assertEqual(chunks[1].rubric_rows[0]["name"], "Depth of Understanding")

    def test_partial_id_match_does_not_order_fallback(self) -> None:
        chunks = [
            GradingChunk(
                chunk_id="a1:s1:0:q1",
                assignment_id="1",
                student_id="s1",
                question_id="q1",
                modality=Modality.WRITTEN,
                task_type=TaskType.UNKNOWN,
                extracted_text="a",
            ),
            GradingChunk(
                chunk_id="a1:s1:1:q2",
                assignment_id="1",
                student_id="s1",
                question_id="unexpected-2",
                modality=Modality.WRITTEN,
                task_type=TaskType.UNKNOWN,
                extracted_text="b",
            ),
        ]
        applied = apply_assignment_creation_rubric_routing(
            chunks,
            criteria_by_question_id={
                "q1": [{"name": "Clarity", "max_points": 1}],
                "q2": [{"name": "Depth of Understanding", "max_points": 2}],
            },
        )
        self.assertEqual(applied, 1)
        self.assertEqual(chunks[0].rubric_rows[0]["name"], "Clarity")
        self.assertEqual(chunks[1].rubric_rows, [])


class MaxByNameForChunkTests(unittest.TestCase):
    def _make_chunk_outcome(self, *, names: list[str], max_pts: dict[str, float]) -> ChunkGradeOutcome:
        return ChunkGradeOutcome(
            chunk_id="c1",
            normalized_score_estimate=0.5,
            semantic_entropy_nats=0.0,
            ai_confidence=0.9,
            entropy_max_reference_nats=1.0,
            cluster_counts={},
            cluster_distribution={},
            samples=[],
            criterion_consensus={},
            auxiliary={
                "rubric_criterion_names": names,
                "rubric_criterion_max_points": max_pts,
            },
            review_status=ReviewStatus.AUTO_ACCEPTED,
            review_reasons=[],
            stage_artifacts={},
        )

    def test_does_not_expand_to_full_rubric_when_routed_subset_present(self) -> None:
        full = {
            "Conceptual Correctness": 4.0,
            "Evidence & Justification": 3.0,
            "Depth of Understanding": 2.0,
            "Clarity": 1.0,
        }
        out = _max_by_name_for_chunk(
            self._make_chunk_outcome(
                names=["Clarity", "Conceptual Correctness"],
                max_pts={"Clarity": 1.0, "Conceptual Correctness": 4.0},
            ),
            full,
        )
        self.assertEqual(set(out.keys()), {"Clarity", "Conceptual Correctness"})
        self.assertEqual(out["Clarity"], 1.0)

    def test_uses_routed_max_when_name_missing_from_flat_rubric(self) -> None:
        out = _max_by_name_for_chunk(
            self._make_chunk_outcome(names=["Custom Criterion"], max_pts={"Custom Criterion": 7.0}),
            {"Other": 5.0},
        )
        self.assertEqual(out, {"Custom Criterion": 7.0})


class MultimodalAssignmentToGradingDictRoutedSubsetTests(unittest.TestCase):
    def test_question_grades_keep_only_routed_criteria(self) -> None:
        rubric = [
            {"name": "Conceptual Correctness", "max_points": 4},
            {"name": "Evidence & Justification", "max_points": 3},
            {"name": "Depth of Understanding", "max_points": 2},
            {"name": "Clarity", "max_points": 1},
        ]

        def _outcome(
            chunk_id: str, names: list[str], max_pts: dict[str, float], scores: dict[str, float]
        ) -> ChunkGradeOutcome:
            return ChunkGradeOutcome(
                chunk_id=chunk_id,
                normalized_score_estimate=0.5,
                semantic_entropy_nats=0.0,
                ai_confidence=0.9,
                entropy_max_reference_nats=1.0,
                cluster_counts={},
                cluster_distribution={},
                samples=[],
                criterion_consensus={},
                auxiliary={
                    "rubric_criterion_names": names,
                    "rubric_criterion_max_points": max_pts,
                    "criterion_raw_scores": scores,
                    "criterion_justifications": {n: f"ok:{n}" for n in names},
                    "criterion_evidence": {n: f"ev:{n}" for n in names},
                    "criterion_reasoning": {n: f"why:{n}" for n in names},
                },
                review_status=ReviewStatus.AUTO_ACCEPTED,
                review_reasons=[],
                stage_artifacts={},
            )

        result = AssignmentGradeResult(
            assignment_id="a1",
            student_id="s1",
            assignment_normalized_score=0.5,
            assignment_ai_confidence=0.9,
            chunk_results=[
                _outcome(
                    "c1",
                    ["Clarity"],
                    {"Clarity": 1.0},
                    {"Clarity": 1.0},
                ),
                _outcome(
                    "c2",
                    ["Conceptual Correctness", "Depth of Understanding"],
                    {"Conceptual Correctness": 4.0, "Depth of Understanding": 2.0},
                    {"Conceptual Correctness": 4.0, "Depth of Understanding": 1.0},
                ),
            ],
        )
        out = multimodal_assignment_to_grading_dict(result, rubric=rubric)
        qgs = out["question_grades"]
        self.assertEqual(len(qgs), 2)
        self.assertEqual([c["name"] for c in qgs[0]["criteria"]], ["Clarity"])
        self.assertEqual(
            {c["name"] for c in qgs[1]["criteria"]},
            {"Conceptual Correctness", "Depth of Understanding"},
        )
        self.assertNotIn(
            "Evidence & Justification",
            {c["name"] for qg in qgs for c in qg["criteria"]},
        )


if __name__ == "__main__":
    unittest.main()
