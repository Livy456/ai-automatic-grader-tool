"""Unit tests for semantic entropy → normalized AI confidence."""

from __future__ import annotations

import math
import unittest

from app.grading.multimodal.semantic_confidence import (
    aggregate_assignment_confidence,
    estimate_cluster_distribution,
    normalize_entropy_to_confidence,
    summarize_chunk_confidence_from_counts,
)


class NormalizeEntropyTests(unittest.TestCase):
    def test_single_cluster_confidence_one(self) -> None:
        self.assertEqual(
            normalize_entropy_to_confidence(0.0, 1),
            1.0,
        )

    def test_two_clusters_uniform_entropy_confidence_zero(self) -> None:
        # p = (0.5, 0.5) → H = log 2
        h = math.log(2)
        conf = normalize_entropy_to_confidence(h, 2)
        self.assertAlmostEqual(conf, 0.0, places=6)

    def test_empty_counts_zero_confidence(self) -> None:
        s = summarize_chunk_confidence_from_counts({})
        self.assertEqual(s["n_valid_samples"], 0)
        self.assertEqual(s["ai_confidence"], 0.0)


class DistributionTests(unittest.TestCase):
    def test_p_hat_sums_to_one(self) -> None:
        p = estimate_cluster_distribution({"a": 2, "b": 3})
        self.assertAlmostEqual(sum(p.values()), 1.0, places=6)


class AggregateAssignmentConfidenceTests(unittest.TestCase):
    def test_equal_weights_when_no_explicit_map(self) -> None:
        class _C:
            def __init__(self, cid: str, conf: float, qw: float) -> None:
                self.chunk_id = cid
                self.ai_confidence = conf
                self.auxiliary = {"question_point_weight": qw}

        chunks = [_C("a", 1.0, 2.0), _C("b", 0.0, 2.0)]
        agg, trace = aggregate_assignment_confidence(chunks, weights={})
        self.assertAlmostEqual(agg, 0.5, places=6)
        self.assertEqual(len(trace["per_chunk"]), 2)


if __name__ == "__main__":
    unittest.main()
