"""
AI confidence from semantic entropy over multi-model, multi-sample grading clusters.

Chunk-level flow: assign each valid parsed grade to a semantic cluster, estimate
:math:`\\hat p(c\\mid x)`, compute Shannon entropy (nats), then normalize by
:math:`\\log |C_x|` and invert to obtain a confidence in ``[0, 1]``.
"""

from __future__ import annotations

import math
from typing import Any

from .entropy import semantic_entropy_from_cluster_counts
from .schemas import ParsedChunkGrade
from .semantic_clusterer import assign_cluster


def cluster_assignment(
    parsed: ParsedChunkGrade | None,
    *,
    strong_pattern: bool = True,
) -> str | None:
    """
    Map a parsed grading output to a discrete cluster label.

    * ``strong_pattern=True``: normalized total plus discretized per-criterion ratios.
    * ``strong_pattern=False``: shared normalized score bin only (weaker clustering).
    """
    if parsed is None:
        return None
    return assign_cluster(parsed, strong_pattern=strong_pattern)


def estimate_cluster_distribution(cluster_counts: dict[str, int]) -> dict[str, float]:
    """
    Empirical :math:`\\hat p(c \\mid x) = n_c / \\sum_{c'} n_{c'}` over **valid**
    samples only (same keys as ``cluster_counts``).
    """
    total = int(sum(cluster_counts.values()))
    if total <= 0:
        return {}
    return {k: float(v) / float(total) for k, v in sorted(cluster_counts.items())}


def compute_semantic_entropy(
    *,
    cluster_counts: dict[str, int] | None = None,
    probability_by_cluster: dict[str, float] | None = None,
) -> float:
    """
    :math:`\\hat H(x) = -\\sum_c \\hat p(c)\\log \\hat p(c)` with natural log.

    Pass either ``cluster_counts`` (preferred) or a probability map that sums to ~1.
    """
    if cluster_counts is not None and cluster_counts:
        return semantic_entropy_from_cluster_counts(cluster_counts)
    if not probability_by_cluster:
        return 0.0
    h = 0.0
    for p in probability_by_cluster.values():
        pf = float(p)
        if pf > 0:
            h -= pf * math.log(pf)
    return float(h)


def normalize_entropy_to_confidence(
    semantic_entropy_nats: float,
    n_observed_clusters: int,
) -> float:
    """
    :math:`\\mathrm{Conf}_{AI}(x) = 1 - \\hat H(x) / \\log |C_x|`, clipped to ``[0, 1]``.

    If ``n_observed_clusters <= 1``, returns ``1.0`` (no ambiguity across clusters).
    """
    if n_observed_clusters <= 1:
        return 1.0
    denom = math.log(float(n_observed_clusters))
    if denom <= 0:
        return 1.0
    conf = 1.0 - float(semantic_entropy_nats) / denom
    return max(0.0, min(1.0, conf))


def summarize_chunk_confidence_from_counts(
    cluster_counts: dict[str, int],
) -> dict[str, Any]:
    """
    Full chunk-level confidence state for aggregation, logging, and export.

    Returns keys: ``semantic_entropy_nats``, ``ai_confidence``, ``entropy_max_reference_nats``,
    ``n_observed_clusters``, ``n_valid_samples``, ``p_hat``.
    """
    valid = int(sum(cluster_counts.values()))
    if valid <= 0:
        return {
            "semantic_entropy_nats": 0.0,
            "ai_confidence": 0.0,
            "entropy_max_reference_nats": 0.0,
            "n_observed_clusters": 0,
            "n_valid_samples": 0,
            "p_hat": {},
        }
    p_hat = estimate_cluster_distribution(cluster_counts)
    se = compute_semantic_entropy(cluster_counts=cluster_counts)
    n_c = sum(1 for v in cluster_counts.values() if v > 0)
    conf = normalize_entropy_to_confidence(se, n_c)
    denom = float(math.log(float(n_c))) if n_c > 1 else 0.0
    return {
        "semantic_entropy_nats": float(se),
        "ai_confidence": float(conf),
        "entropy_max_reference_nats": denom,
        "n_observed_clusters": int(n_c),
        "n_valid_samples": valid,
        "p_hat": p_hat,
    }


def aggregate_assignment_confidence(
    chunk_results: list[Any],
) -> tuple[float, dict[str, Any]]:
    """Arithmetic mean of chunk ``ai_confidence`` values."""
    if not chunk_results:
        return 0.0, {"assignment_ai_confidence": 0.0, "per_chunk": []}
    per_chunk: list[dict[str, Any]] = []
    total = 0.0
    for c in chunk_results:
        cid = str(getattr(c, "chunk_id", "") or "")
        conf = float(getattr(c, "ai_confidence", 0.0))
        total += conf
        per_chunk.append({"chunk_id": cid, "ai_confidence": conf})
    agg = total / len(chunk_results)
    trace = {
        "assignment_ai_confidence": float(agg),
        "per_chunk": per_chunk,
    }
    return float(agg), trace
