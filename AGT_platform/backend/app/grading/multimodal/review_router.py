"""
Human-review routing from AI confidence (normalized semantic entropy), parses, and flags.
"""

from __future__ import annotations

from .schemas import ChunkGradeOutcome, MultimodalGradingConfig, ReviewStatus, SampledChunkGrade


def evaluate_chunk_review(
    outcome: ChunkGradeOutcome,
    samples: list[SampledChunkGrade],  # retained for API stability / future use
    cfg: MultimodalGradingConfig,
) -> ChunkGradeOutcome:
    _ = samples
    reasons: list[str] = []
    status = ReviewStatus.AUTO_ACCEPTED

    parse_fail = float(outcome.auxiliary.get("parse_fail_rate", 0) or 0)
    review_flag_rate = float(outcome.auxiliary.get("review_flag_rate", 0) or 0)

    # Data-quality gates (override confidence bands).
    if parse_fail > cfg.parse_fail_rate_high:
        reasons.append("parse_failure_rate_high")
        outcome.review_status = ReviewStatus.FLAGGED
        outcome.review_reasons = list(dict.fromkeys(reasons))
        return outcome

    if cfg.review_if_any_sample_flag and review_flag_rate > 0:
        reasons.append("sample_review_flag")
        outcome.review_status = ReviewStatus.FLAGGED
        outcome.review_reasons = list(dict.fromkeys(reasons))
        return outcome

    conf = float(outcome.ai_confidence)
    hi = float(cfg.confidence_ai_auto_accept_min)
    med = float(cfg.confidence_ai_caution_min)

    if conf >= hi:
        status = ReviewStatus.AUTO_ACCEPTED
    elif conf >= med:
        status = ReviewStatus.CAUTION
        reasons.append("ai_confidence_caution_band")
    else:
        status = ReviewStatus.FLAGGED
        reasons.append("ai_confidence_low")

    # Secondary: extreme score spread with already-low confidence → escalation.
    spread = float(outcome.auxiliary.get("score_std_across_samples", 0) or 0)
    if conf < med and spread > cfg.score_spread_high * 1.5:
        status = ReviewStatus.ESCALATION
        reasons.append("low_ai_confidence_and_high_score_spread")

    # Optional: high criterion disagreement soft-bumps acceptance to caution.
    disagree = float(outcome.auxiliary.get("criterion_disagreement_max", 0) or 0)
    if (
        cfg.confidence_caution_on_high_criterion_disagreement
        and disagree > cfg.criterion_disagreement_high
        and status == ReviewStatus.AUTO_ACCEPTED
    ):
        status = ReviewStatus.CAUTION
        reasons.append("criterion_disagreement_above_threshold")

    outcome.review_status = status
    outcome.review_reasons = list(dict.fromkeys(reasons))
    return outcome
