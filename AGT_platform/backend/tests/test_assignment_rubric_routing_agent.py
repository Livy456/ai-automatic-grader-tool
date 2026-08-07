"""Tests for the Assignment Creation rubric routing agent."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.grading.rubric_routing.assignment_rubric_routing_agent import (
    build_rubric_criterion_index,
    extract_rubric_criteria,
    lookup_rubric_criteria_by_ids,
    normalize_chunk_rubric_criteria,
    try_route_rubric_for_questions,
)


class RubricCriterionIndexTests(unittest.TestCase):
    def test_flat_list_assigns_stable_ids(self) -> None:
        index = build_rubric_criterion_index(
            [
                {"criterion": "Clarity", "max_score": 5},
                {"criterion": "Correctness", "max_score": 10},
            ]
        )
        self.assertEqual(set(index.keys()), {"crit_0", "crit_1"})
        self.assertEqual(index["crit_0"]["name"], "Clarity")

    def test_explicit_id_is_preserved(self) -> None:
        index = build_rubric_criterion_index([{"id": "c1", "criterion": "Clarity", "max_score": 5}])
        self.assertIn("c1", index)
        self.assertEqual(
            lookup_rubric_criteria_by_ids(
                [{"id": "c1", "criterion": "Clarity", "max_score": 5}], ["c1"]
            )[0]["name"],
            "Clarity",
        )

    def test_prose_fallback_when_no_structured_rubric(self) -> None:
        index = build_rubric_criterion_index([], rubric_text="Grade clarity and evidence.")
        self.assertEqual(list(index.keys()), ["prose_0"])
        rows = extract_rubric_criteria([], rubric_text="Grade clarity and evidence.")
        self.assertEqual(len(rows), 1)
        self.assertIn("Grade clarity", rows[0]["description"])


class NormalizeChunkRubricCriteriaTests(unittest.TestCase):
    def test_resolves_legacy_id_strings(self) -> None:
        rubric = {
            "criteria": [
                {"name": "Conceptual Correctness", "points_range": "0-4", "levels": {"4": "Great"}},
                {"name": "Clarity", "points_range": "0-1", "levels": {"1": "Clear"}},
            ]
        }
        rows = normalize_chunk_rubric_criteria(
            ["crit_0", "crit_1"],
            rubric=rubric,
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["name"], "Conceptual Correctness")
        self.assertEqual(rows[0]["max_score"], 4.0)
        self.assertIn("Great", rows[0]["description"])
        self.assertEqual(rows[1]["name"], "Clarity")


class AssignmentRubricRoutingAgentTests(unittest.TestCase):
    def test_routes_criterion_rows_per_question(self) -> None:
        cfg = MagicMock()
        cfg.ANTHROPIC_API_KEY = "test-key"
        cfg.MULTIMODAL_CLAUDE_PARSING_AGENT_MODEL = "claude-test"
        cfg.MULTIMODAL_CLAUDE_PARSING_AGENT_MAX_TOKENS = 4096

        rubric = [
            {"id": "code", "criterion": "Code quality", "max_score": 5},
            {"id": "write", "criterion": "Written explanation", "max_score": 5},
        ]
        pairs = [
            {"question_id": "1", "question": "Implement merge sort.", "answer": "..."},
            {"question_id": "2", "question": "Explain time complexity.", "answer": "O(n log n)"},
        ]

        mock_response = {
            "routes": [
                {
                    "question_id": "1",
                    "criterion_ids": ["code"],
                    "routing_reason": "Coding task.",
                },
                {
                    "question_id": "2",
                    "criterion_ids": ["write"],
                    "routing_reason": "Prose explanation.",
                },
            ]
        }

        with patch(
            "app.grading.rubric_routing.assignment_rubric_routing_agent.AnthropicJsonClient"
        ) as mock_client_cls:
            mock_client_cls.return_value.chat_json.return_value = mock_response
            routed = try_route_rubric_for_questions(
                pairs=pairs,
                rubric=rubric,
                cfg=cfg,
            )

        self.assertIsNotNone(routed)
        assert routed is not None
        self.assertEqual(routed[0][0]["id"], "code")
        self.assertEqual(routed[1][0]["id"], "write")

    def test_ensures_at_least_one_criterion_row(self) -> None:
        cfg = MagicMock()
        cfg.ANTHROPIC_API_KEY = "test-key"
        cfg.MULTIMODAL_CLAUDE_PARSING_AGENT_MODEL = "claude-test"
        cfg.MULTIMODAL_CLAUDE_PARSING_AGENT_MAX_TOKENS = 4096

        rubric = [{"id": "only", "criterion": "Only", "max_score": 5}]
        mock_response = {
            "routes": [
                {"question_id": "1", "criterion_ids": [], "routing_reason": "empty"},
            ]
        }

        with patch(
            "app.grading.rubric_routing.assignment_rubric_routing_agent.AnthropicJsonClient"
        ) as mock_client_cls:
            mock_client_cls.return_value.chat_json.return_value = mock_response
            routed = try_route_rubric_for_questions(
                pairs=[{"question_id": "1", "question": "Q", "answer": "A"}],
                rubric=rubric,
                cfg=cfg,
            )

        self.assertIsNotNone(routed)
        assert routed is not None
        self.assertEqual(routed[0][0]["id"], "only")

    def test_returns_none_when_api_key_missing(self) -> None:
        cfg = MagicMock()
        cfg.ANTHROPIC_API_KEY = ""
        result = try_route_rubric_for_questions(
            pairs=[{"question_id": "1", "question": "Q", "answer": "A"}],
            rubric=[{"id": "c1", "criterion": "Clarity", "max_score": 5}],
            cfg=cfg,
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
