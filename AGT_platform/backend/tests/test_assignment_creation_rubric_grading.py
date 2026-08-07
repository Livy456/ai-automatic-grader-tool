"""Grading pipeline integration for Assignment Creation rubric routing."""
from __future__ import annotations

import unittest

from app.grading.rubric_routing.assignment_rubric_routing_agent import (
    apply_assignment_creation_rubric_routing,
)
from app.grading.schemas import GradingChunk, Modality, RubricType, TaskType


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
                "1": [{"id": "code", "name": "Code quality", "criterion": "Code quality", "max_score": 5}]
            },
        )
        self.assertEqual(applied, 1)
        self.assertEqual(chunk.routing_reason, "assignment_creation_rubric_routing")
        self.assertEqual(len(chunk.rubric_rows), 1)
        self.assertEqual(chunk.rubric_rows[0]["id"], "code")
        self.assertEqual(chunk.rubric_type, RubricType.FREE_RESPONSE)


if __name__ == "__main__":
    unittest.main()
