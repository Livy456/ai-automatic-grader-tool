#!/usr/bin/env python3
"""
Wilcoxon signed-rank analysis for Section 4.2.

Tests whether AI and human graders agree within each modality on the
paired difference S_AI - S_human. Reports the W statistic, raw and Holm-
Bonferroni adjusted p-values, rank-biserial effect size, and a 95%
percentile bootstrap confidence interval on the median paired difference.

USAGE
-----
    python run_wilcoxon_section_4_2.py <input_csv> [output_csv] \\
        [--routing modality|rubric_type] [--alternative two-sided|less|greater]

    # examples
    python run_wilcoxon_section_4_2.py code_features_extracted_final.csv
    python run_wilcoxon_section_4_2.py data.csv results.csv --routing rubric_type
    python run_wilcoxon_section_4_2.py data.csv --alternative less

OUTPUT
------
CSV with one row per group plus a POOLED row. Columns:
  modality (or rubric_type), n_pairs, median_paired_difference_AI_minus_human,
  test_statistic_W, p_value_raw, p_value_holm_bonferroni,
  rank_biserial_effect_size, ci95_median_difference

METHODOLOGY (matches Section 4.2)
  * Paired difference:  d = S_AI - S_human  (signed)
  * Test:               scipy.stats.wilcoxon, zero_method='wilcox'
                        (exact zeros dropped; conventional choice)
  * Effect size:        rank-biserial correlation (Kerby, 2014):
                            r = (R+ - R-) / (R+ + R-)
                        on the nonzero differences. Range -1 to +1.
                        r near -1 => AI scored lower than human on nearly
                        every paired submission.
  * 95% CI:             10,000-resample percentile bootstrap on the
                        MEDIAN paired difference (not Hodges-Lehmann,
                        which assumes symmetry the test itself probes).
  * Multiple testing:   Holm-Bonferroni applied across the per-group
                        tests only. The POOLED row is descriptive and
                        NOT part of the testing family; its p-value is
                        reported as-is with a clear annotation.
  * Pairing:            each CSV row is one matched AI-human pair on the
                        same submission. The test does NOT require students
                        to appear across groups; it only requires that each
                        row carries both an AI and a human score.

CLUSTERING NOTE
---------------
If multiple submissions come from the same student within a group (as is
the case for Programming and text in the example dataset), the signed-
rank test treats those rows as independent pairs and may understate
standard errors. The within-group cluster structure is reported alongside
the results so the reader can judge the conservatism of the inference.
For consequential claims, follow up with a mixed-effects model with random
intercepts per student or a within-student permutation test.

INPUT
-----
A CSV containing (at minimum) these columns:
  - student_id
  - assignment_id
  - modality        (required if --routing modality)
  - rubric_type     (required if --routing rubric_type)
  - ai_score_norm
  - human_score_norm
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats


# ---------- configuration ----------
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 20260513
ALPHA = 0.05


# ---------- statistical helpers ----------
def rank_biserial(diffs: np.ndarray) -> float:
    """Rank-biserial effect size for the Wilcoxon signed-rank test
    (Kerby, 2014):  r = (R+ - R-) / (R+ + R-)  on nonzero differences.

    Range -1 to +1. r near -1 means virtually every paired gap is negative;
    r near +1 means virtually every paired gap is positive; r near 0 means
    the signs balance out.
    """
    d = np.asarray(diffs, dtype=float)
    d = d[d != 0]
    if d.size == 0:
        return float('nan')
    ranks = stats.rankdata(np.abs(d))
    return float((ranks[d > 0].sum() - ranks[d < 0].sum()) / ranks.sum())


def bootstrap_ci_median(diffs: np.ndarray,
                        n_boot: int = BOOTSTRAP_RESAMPLES,
                        alpha: float = ALPHA,
                        rng: Optional[np.random.Generator] = None,
                        ) -> tuple[float, float]:
    """Percentile bootstrap 95% CI on the median paired difference."""
    d = np.asarray(diffs, dtype=float)
    n = d.size
    if n < 2:
        return (float('nan'), float('nan'))
    rng = rng or np.random.default_rng(BOOTSTRAP_SEED)
    idx = rng.integers(0, n, size=(n_boot, n))
    medians = np.median(d[idx], axis=1)
    lo, hi = np.percentile(medians, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lo), float(hi)


def wilcoxon_test(diffs: np.ndarray,
                  alternative: str = 'two-sided',
                  ) -> tuple[float, float]:
    """Returns (W statistic, p-value). Drops exact zeros (zero_method='wilcox')."""
    res = stats.wilcoxon(diffs, zero_method='wilcox',
                         alternative=alternative, method='auto')
    return float(res.statistic), float(res.pvalue)


def holm_bonferroni(pvals: np.ndarray) -> np.ndarray:
    """Holm-Bonferroni step-down adjustment.

    Sort p-values ascending, multiply the i-th smallest by (m - i + 1),
    enforce monotonicity, cap at 1.
    """
    p = np.asarray(pvals, dtype=float)
    m = p.size
    order = np.argsort(p)
    adj = np.empty(m)
    running_max = 0.0
    for i, idx in enumerate(order):
        val = min(1.0, p[idx] * (m - i))
        running_max = max(running_max, val)
        adj[idx] = running_max
    return adj


# ---------- pipeline ----------
def load_pairs(csv_path: Path, routing_col: str) -> pd.DataFrame:
    """Read the CSV, drop rows with missing scores, compute signed diffs."""
    df = pd.read_csv(csv_path)
    required = ['student_id', 'assignment_id', routing_col,
                'ai_score_norm', 'human_score_norm']
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"CSV missing required columns: {missing}")

    df = df[required].copy()
    before = len(df)
    dropped = df[df[['ai_score_norm', 'human_score_norm']].isna().any(axis=1)]
    df = df.dropna(subset=['ai_score_norm', 'human_score_norm']).copy()
    df['diff'] = df['ai_score_norm'] - df['human_score_norm']

    print(f"Loaded {before} rows; {len(df)} usable matched pairs "
          f"({before - len(df)} dropped for missing scores).")
    if len(dropped):
        print("Dropped rows:")
        print(dropped[['student_id', 'assignment_id', routing_col,
                       'ai_score_norm', 'human_score_norm']].to_string(index=False))
    print(f"\nPer-{routing_col} sample sizes:")
    print(df[routing_col].value_counts().sort_index().to_string())

    # Cluster diagnostic: how many distinct students per group?
    print(f"\nWithin-group student clustering (informational):")
    for g, sub in df.groupby(routing_col):
        n_students = sub['student_id'].nunique()
        max_per_student = sub['student_id'].value_counts().max()
        print(f"  {g}: n={len(sub)} pairs from {n_students} distinct students "
              f"(max {max_per_student} pairs per student)")
    return df


def analyze_group(label: str, diffs: np.ndarray,
                  rng: np.random.Generator,
                  alternative: str = 'two-sided') -> dict:
    W, p = wilcoxon_test(diffs, alternative=alternative)
    rb = rank_biserial(diffs)
    lo, hi = bootstrap_ci_median(diffs, rng=rng)
    return {
        'group': label,
        'n_pairs': int(diffs.size),
        'median_diff': float(np.median(diffs)),
        'W': W,
        'p_raw': p,
        'rank_biserial': rb,
        'ci_lo': lo,
        'ci_hi': hi,
    }


def run(input_csv: Path, output_csv: Path, routing_col: str,
        alternative: str) -> pd.DataFrame:
    df = load_pairs(input_csv, routing_col)
    rng = np.random.default_rng(BOOTSTRAP_SEED)

    # One row per group (sorted alphabetically for stable output)
    rows = [analyze_group(g,
                          df.loc[df[routing_col] == g, 'diff'].to_numpy(),
                          rng, alternative)
            for g in sorted(df[routing_col].unique())]
    # Pooled row (descriptive)
    rows.append(analyze_group('POOLED', df['diff'].to_numpy(), rng, alternative))

    res = pd.DataFrame(rows)

    # Holm-Bonferroni across the per-group tests only
    is_group = res['group'] != 'POOLED'
    res.loc[is_group, 'p_holm'] = holm_bonferroni(
        res.loc[is_group, 'p_raw'].to_numpy())
    res.loc[~is_group, 'p_holm'] = np.nan

    # Format the output to match the Section 4.2 table style
    out = pd.DataFrame({
        routing_col: res['group'],
        'n_pairs': res['n_pairs'].astype(int),
        'median_paired_difference_AI_minus_human': res['median_diff'].round(6),
        'test_statistic_W': res['W'].round(4),
        'p_value_raw': res['p_raw'].apply(lambda x: f"{x:.6g}"),
        'p_value_holm_bonferroni': res['p_holm'].apply(
            lambda x: f"{x:.6g}" if pd.notna(x) else "n/a (pooled, not in family)"),
        'rank_biserial_effect_size': res['rank_biserial'].round(4),
        'ci95_median_difference': res.apply(
            lambda r: f"[{r['ci_lo']:.4f}, {r['ci_hi']:.4f}]", axis=1),
    })

    print(f"\n=== Wilcoxon signed-rank: AI score - Human score, by {routing_col} ===")
    print(f"  alternative: {alternative}")
    print(f"  alpha = {ALPHA}, Holm-Bonferroni across {(is_group).sum()} group tests")
    print(f"  bootstrap CI: {BOOTSTRAP_RESAMPLES:,} resamples, seed={BOOTSTRAP_SEED}\n")
    print(out.to_string(index=False))

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_csv, index=False)
    print(f"\nWrote results -> {output_csv}")
    return out


# ---------- entry point ----------
def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Run Wilcoxon signed-rank analysis (Section 4.2).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input_csv", type=Path,
                        help="Path to the features CSV.")
    parser.add_argument("output_csv", type=Path, nargs='?', default=None,
                        help="Where to write the results CSV "
                             "(default: <input_dir>/wilcoxon_results_section_4_2.csv).")
    parser.add_argument("--routing", choices=['modality', 'rubric_type'],
                        default='modality',
                        help="Column to group by (default: modality, matching "
                             "Section 4.2). Use rubric_type to split "
                             "Programming into scaffolded_coding and open_end_eda.")
    parser.add_argument("--alternative", choices=['two-sided', 'less', 'greater'],
                        default='two-sided',
                        help="Wilcoxon alternative hypothesis (default: two-sided, "
                             "matching Section 4.2).")
    args = parser.parse_args(argv[1:])

    input_csv = args.input_csv.expanduser().resolve()
    if not input_csv.exists():
        print(f"ERROR: input file not found: {input_csv}", file=sys.stderr)
        return 1

    output_csv = (args.output_csv.expanduser().resolve()
                  if args.output_csv
                  else input_csv.with_name('wilcoxon_results_section_4_2.csv'))

    run(input_csv, output_csv, args.routing, args.alternative)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
