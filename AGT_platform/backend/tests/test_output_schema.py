"""Tests for ``grading/output_schema.py`` validation."""

import unittest

from app.grading.output_schema import (
    GradingOutputValidationError,
    coerce_grading_output_shape,
    validate_grading_output,
)


class OutputSchemaTests(unittest.TestCase):
    def test_minimal_valid(self) -> None:
        d = {
            "overall": {"score": 80, "confidence": 0.8, "summary": "ok"},
            "criteria": [
                {
                    "name": "A",
                    "score": 40,
                    "confidence": 0.8,
                    "rationale": "r",
                    "evidence": {"quotes": ["x"], "notes": "n"},
                }
            ],
            "flags": [],
            "_model_used": "ollama:x",
        }
        out = validate_grading_output(d)
        self.assertAlmostEqual(out["overall"]["score"], 0.8, places=5)
        self.assertEqual(out["overall"]["max_score"], 1.0)
        self.assertEqual(out["criteria"][0]["name"], "A")

    def test_criterion_alias_max_score(self) -> None:
        d = {
            "overall": {"score": 1, "confidence": 0.5, "summary": ""},
            "criteria": [
                {"criterion": "Q1", "max_score": 10, "score": 5, "confidence": 0.7}
            ],
        }
        out = validate_grading_output(d)
        self.assertEqual(out["criteria"][0]["name"], "Q1")
        self.assertEqual(out["criteria"][0]["max_points"], 10.0)

    def test_reject_missing_overall(self) -> None:
        with self.assertRaises(GradingOutputValidationError):
            validate_grading_output({"criteria": []})

    def test_coerce_scalar_overall_then_validates(self) -> None:
        d = {
            "overall": 85.0,
            "criteria": [
                {
                    "name": "A",
                    "score": 40,
                    "max_points": 50,
                    "confidence": 0.8,
                    "rationale": "r",
                    "evidence": {"quotes": [], "notes": ""},
                }
            ],
            "flags": [],
        }
        coerce_grading_output_shape(d)
        out = validate_grading_output(d)
        self.assertAlmostEqual(out["overall"]["score"], 0.85, places=5)
        self.assertEqual(out["overall"]["max_score"], 1.0)

    def test_coerce_nested_grading(self) -> None:
        d = {
            "grading": {
                "overall": {"score": 10, "confidence": 0.9, "summary": "ok"},
                "criteria": [],
                "flags": [],
            }
        }
        coerce_grading_output_shape(d)
        validate_grading_output(d)

    def test_coerce_overall_from_criteria(self) -> None:
        d = {
            "criteria": [
                {
                    "name": "A",
                    "score": 8,
                    "max_points": 10,
                    "confidence": 0.7,
                    "rationale": "r",
                    "evidence": {"quotes": [], "notes": ""},
                },
                {
                    "name": "B",
                    "score": 5,
                    "max_points": 10,
                    "confidence": 0.7,
                    "rationale": "r",
                    "evidence": {"quotes": [], "notes": ""},
                },
            ],
            "flags": [],
        }
        coerce_grading_output_shape(d)
        out = validate_grading_output(d)
        self.assertIsInstance(out["overall"]["score"], float)
        self.assertAlmostEqual(out["overall"]["score"], 0.65, places=5)
        self.assertEqual(out["overall"]["max_score"], 1.0)

    def test_allowed_criterion_names_removes_hallucinated_rows(self) -> None:
        allowed = frozenset({"Conceptual Correctness", "Clarity"})
        d = {
            "overall": {"score": 99, "confidence": 0.5, "summary": ""},
            "criteria": [
                {
                    "name": "criterion_1",
                    "score": 50,
                    "max_points": 100,
                    "confidence": 0.0,
                    "justification": "",
                    "evidence": "",
                    "reasoning": "",
                },
                {
                    "name": "Conceptual Correctness",
                    "score": 3,
                    "max_points": 4,
                    "confidence": 0.8,
                    "justification": "ok",
                    "evidence": "quote",
                    "reasoning": "r",
                },
            ],
            "flags": [],
        }
        out = validate_grading_output(d, allowed_criterion_names=allowed)
        names = [c["name"] for c in out["criteria"]]
        self.assertEqual(names, ["Conceptual Correctness"])
        self.assertTrue(any("rubric_allowlist" in f for f in out.get("flags", [])))

    def test_question_grades_criteria_require_text_fields_and_resync_overall(self) -> None:
        d = {
            "overall": {"score": 0.2, "confidence": 0.9, "summary": "headline mismatch"},
            "criteria": [],
            "flags": [],
            "question_grades": [
                {
                    "chunk_id": "q1",
                    "criteria": [
                        {
                            "name": "Functional Correctness",
                            "score": 2.0,
                            "max_points": 4.0,
                            "confidence": 0.8,
                        }
                    ],
                    "overall": {
                        "score": 0.99,
                        "max_score": 1.0,
                        "max_points": 4.0,
                        "rubric_points_earned": 9.0,
                        "summary": "stale",
                    },
                }
            ],
        }
        out = validate_grading_output(d)
        crit = out["question_grades"][0]["criteria"][0]
        for k in ("justification", "evidence", "reasoning"):
            self.assertIn(k, crit)
            self.assertIsInstance(crit[k], str)
            self.assertGreater(len(crit[k]), 10)
        self.assertAlmostEqual(float(out["question_grades"][0]["overall"]["score"]), 0.5, places=5)
        self.assertAlmostEqual(float(out["overall"]["score"]), 0.5, places=5)
        self.assertIn("assignment_overall_resynced_from_question_grades_mean", out["flags"])
        self.assertAlmostEqual(float(out["overall"]["rubric_points_earned"]), 2.0, places=4)


if __name__ == "__main__":
    unittest.main()
