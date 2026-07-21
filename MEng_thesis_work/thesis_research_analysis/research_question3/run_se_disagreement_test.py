#!/usr/bin/env python3
"""
Test whether semantic entropy (SE) predicts AI-human paired disagreement.
This is the Section 4.4 analysis for Research Question 3.

The hypothesis is that the multimodal grading pipeline's semantic entropy
output, computed per assignment from the LLM's K=3 self-samples, should be
higher on assignments where the AI and human grader disagree more strongly.
If that hypothesis holds, SE can be used as a per-assignment routing signal:
escalate high-SE assignments to human review.

The script runs three complementary tests, each addressing a slightly
different framing of the question:

  TEST 1 -- Mann-Whitney U (one-sided), pooled
            Are SE values higher in the high-disagreement group than in
            the low-disagreement group, across the whole dataset?
            High-disagreement is defined as |S_AI - S_human| >= 0.10
            (one letter-grade band), matching the D_rate threshold used
            in Section 4.1.

  TEST 2 -- Mann-Whitney U (one-sided), per modality
            Same test as TEST 1, but run separately within each modality
            so the result is not confounded by between-modality variation
            in SE or in disagreement.

  TEST 3 -- Spearman rank correlation between SE and |D_score|
            (a) overall pooled,
            (b) within each modality, with one-sided p-values combined
                across modalities via Fisher's chi-square and Stouffer's
                sqrt(n)-weighted z, and
            (c) stratified Spearman -- the within-modality Spearman that
                explicitly removes the modality fixed effect by ranking
                and standardizing within stratum before pooling.
            (c) is the most defensible answer to the question
            "does SE predict disagreement after the modality confound is
             controlled for".

USAGE
-----
    python run_se_disagreement_test.py <input_csv> [output_csv] \\
        [--threshold FLOAT] [--n-perm INT] [--seed INT]

    # examples
    python run_se_disagreement_test.py code_features_extracted_final.csv
    python run_se_disagreement_test.py data.csv ./se_results.csv
    python run_se_disagreement_test.py data.csv --threshold 0.15 --n-perm 50000

OUTPUT
------
A CSV with one row per test (eight rows total: one pooled MW + three
per-modality MW + one pooled Spearman + three per-modality Spearman +
Fisher combined + Stouffer combined + stratified Spearman + permutation
test on stratified Spearman).

The console output also reports an English-language conclusion for each
test, since RQ3 is the kind of question that needs a clear directional
verdict rather than just p-values.

INPUT
-----
A CSV with these columns at minimum:
  modality, ai_score_norm, human_score_norm, semantic_entropy

Rows missing any of those four values are dropped with a warning before
the analysis runs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
from scipy.stats import (
    combine_pvalues,
    mannwhitneyu,
    rankdata,
    spearmanr,
)


DEFAULT_THRESHOLD = 0.10
DEFAULT_N_PERM = 10_000
DEFAULT_SEED = 20260513
REQUIRED_COLUMNS = ("modality", "ai_score_norm", "human_score_norm",
                    "semantic_entropy")


# ---------- helpers ----------
def load_and_prepare(csv_path: Path, threshold: float) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Input CSV missing required columns: {missing}")
    before = len(df)
    df = df.dropna(subset=list(REQUIRED_COLUMNS)).copy()
    dropped = before - len(df)
    if dropped:
        print(f"Note: dropped {dropped} row(s) missing required values.")

    # Convention here is irrelevant -- both tests use |D_score|, so the sign
    # of (AI - human) vs (human - AI) doesn't matter.
    df["D_score"] = df["ai_score_norm"] - df["human_score_norm"]
    df["abs_D"]   = df["D_score"].abs()
    df["D_high"]  = (df["abs_D"] >= threshold).astype(int)
    print(f"Loaded {len(df)} paired assignments.")
    print(f"High-disagreement threshold: |D_score| >= {threshold}")
    print(f"Overall high-disagreement rate: {df['D_high'].mean():.3f} "
          f"({int(df['D_high'].sum())} of {len(df)})")
    return df


def verdict(p_one_sided: float, alpha: float = 0.05) -> str:
    """Return a short English verdict for a one-sided p-value."""
    if np.isnan(p_one_sided):
        return "n/a (insufficient data)"
    if p_one_sided < alpha:
        return f"SUPPORTS hypothesis (p = {p_one_sided:.4f} < {alpha})"
    if p_one_sided > 1 - alpha:
        return (f"DIRECTIONALLY AGAINST hypothesis "
                f"(p = {p_one_sided:.4f}, far from {alpha})")
    return f"does NOT support hypothesis (p = {p_one_sided:.4f} >= {alpha})"


# ---------- TEST 1: pooled Mann-Whitney ----------
def mann_whitney_pooled(df: pd.DataFrame) -> dict:
    high = df.loc[df["D_high"] == 1, "semantic_entropy"].to_numpy()
    low  = df.loc[df["D_high"] == 0, "semantic_entropy"].to_numpy()
    if len(high) < 3 or len(low) < 3:
        return {"test": "MannWhitney_pooled", "scope": "all",
                "n_high": int(len(high)), "n_low": int(len(low)),
                "U": np.nan, "p_two_sided": np.nan,
                "p_one_sided_SE_higher_in_high_D": np.nan,
                "mean_SE_high_D": np.nan, "mean_SE_low_D": np.nan,
                "verdict": "n/a (insufficient sample)"}
    U_one, p_one = mannwhitneyu(high, low, alternative="greater")
    _, p_two = mannwhitneyu(high, low, alternative="two-sided")
    return {
        "test": "MannWhitney_pooled",
        "scope": "all",
        "n_high": int(len(high)),
        "n_low":  int(len(low)),
        "mean_SE_high_D": float(np.mean(high)),
        "mean_SE_low_D":  float(np.mean(low)),
        "U": float(U_one),
        "p_two_sided": float(p_two),
        "p_one_sided_SE_higher_in_high_D": float(p_one),
        "verdict": verdict(p_one),
    }


# ---------- TEST 2: per-modality Mann-Whitney ----------
def mann_whitney_per_modality(df: pd.DataFrame) -> List[dict]:
    rows = []
    for mod in sorted(df["modality"].unique()):
        sub = df[df["modality"] == mod]
        high = sub.loc[sub["D_high"] == 1, "semantic_entropy"].to_numpy()
        low  = sub.loc[sub["D_high"] == 0, "semantic_entropy"].to_numpy()
        if len(high) < 3 or len(low) < 3:
            rows.append({
                "test": "MannWhitney_per_modality", "scope": mod,
                "n_high": int(len(high)), "n_low": int(len(low)),
                "mean_SE_high_D": np.nan, "mean_SE_low_D": np.nan,
                "U": np.nan,
                "p_two_sided": np.nan,
                "p_one_sided_SE_higher_in_high_D": np.nan,
                "verdict": "n/a (insufficient sample in one group)",
            })
            continue
        U_one, p_one = mannwhitneyu(high, low, alternative="greater")
        _, p_two = mannwhitneyu(high, low, alternative="two-sided")
        rows.append({
            "test": "MannWhitney_per_modality",
            "scope": mod,
            "n_high": int(len(high)),
            "n_low":  int(len(low)),
            "mean_SE_high_D": float(np.mean(high)),
            "mean_SE_low_D":  float(np.mean(low)),
            "U": float(U_one),
            "p_two_sided": float(p_two),
            "p_one_sided_SE_higher_in_high_D": float(p_one),
            "verdict": verdict(p_one),
        })
    return rows


# ---------- TEST 3a: pooled Spearman ----------
def spearman_pooled(df: pd.DataFrame) -> dict:
    rho, p_two = spearmanr(df["semantic_entropy"], df["abs_D"])
    p_one = p_two / 2 if rho > 0 else 1 - p_two / 2
    return {
        "test": "Spearman_pooled",
        "scope": "all",
        "n": int(len(df)),
        "rho": float(rho),
        "p_two_sided": float(p_two),
        "p_one_sided_positive": float(p_one),
        "verdict": verdict(p_one),
    }


# ---------- TEST 3b: per-modality Spearman + combined ----------
def spearman_per_modality_and_combined(df: pd.DataFrame) -> List[dict]:
    rows = []
    pvals_one, weights = [], []
    for mod in sorted(df["modality"].unique()):
        sub = df[df["modality"] == mod]
        if len(sub) < 4:
            continue
        rho, p_two = spearmanr(sub["semantic_entropy"], sub["abs_D"])
        p_one = p_two / 2 if rho > 0 else 1 - p_two / 2
        rows.append({
            "test": "Spearman_per_modality", "scope": mod,
            "n": int(len(sub)), "rho": float(rho),
            "p_two_sided": float(p_two),
            "p_one_sided_positive": float(p_one),
            "verdict": verdict(p_one),
        })
        pvals_one.append(p_one)
        weights.append(np.sqrt(len(sub)))

    if len(pvals_one) >= 2:
        chi2, p_fisher = combine_pvalues(pvals_one, method="fisher")
        z_stouf, p_stouf = combine_pvalues(pvals_one, method="stouffer",
                                           weights=weights)
        rows.append({
            "test": "Combined_Fisher", "scope": "across_modalities",
            "n": int(sum(int(r["n"]) for r in rows
                         if r["test"] == "Spearman_per_modality")),
            "rho": np.nan,
            "p_two_sided": np.nan,
            "p_one_sided_positive": float(p_fisher),
            "verdict": verdict(p_fisher),
        })
        rows.append({
            "test": "Combined_Stouffer_sqrtN", "scope": "across_modalities",
            "n": int(sum(int(r["n"]) for r in rows
                         if r["test"] == "Spearman_per_modality")),
            "rho": np.nan,
            "p_two_sided": np.nan,
            "p_one_sided_positive": float(p_stouf),
            "verdict": verdict(p_stouf),
        })
    return rows


# ---------- TEST 3c: stratified Spearman + permutation ----------
def stratified_spearman_with_permutation(df: pd.DataFrame, n_perm: int,
                                          seed: int) -> List[dict]:
    """Within each modality convert SE and |D| to ranks then standardize ranks
    within stratum. Pool across modalities. The Spearman correlation of the
    pooled standardized ranks is the modality-controlled Spearman."""
    se_std, abs_std = [], []
    per_mod_ranks = {}     # for the permutation
    for mod in sorted(df["modality"].unique()):
        sub = df[df["modality"] == mod]
        if len(sub) < 4:
            continue
        se_r  = rankdata(sub["semantic_entropy"])
        abs_r = rankdata(sub["abs_D"])
        se_z  = (se_r  - se_r.mean())  / se_r.std()
        abs_z = (abs_r - abs_r.mean()) / abs_r.std()
        se_std.extend(se_z)
        abs_std.extend(abs_z)
        per_mod_ranks[mod] = se_r  # stash for the permutation

    se_std  = np.array(se_std)
    abs_std = np.array(abs_std)
    rho_strat, p_two = spearmanr(se_std, abs_std)
    p_one = p_two / 2 if rho_strat > 0 else 1 - p_two / 2

    # Permutation test: within each stratum independently permute SE ranks
    rng = np.random.default_rng(seed)
    null = np.empty(n_perm)
    for i in range(n_perm):
        perm = []
        for mod in sorted(per_mod_ranks.keys()):
            se_r_perm = rng.permutation(per_mod_ranks[mod])
            se_z_perm = (se_r_perm - se_r_perm.mean()) / se_r_perm.std()
            perm.extend(se_z_perm)
        null[i] = spearmanr(np.array(perm), abs_std)[0]
    p_perm_one_sided_positive = float((null >= rho_strat).mean())
    p_perm_two_sided = float((np.abs(null) >= abs(rho_strat)).mean())

    return [
        {
            "test": "Stratified_Spearman_asymptotic",
            "scope": "all (modality-controlled)",
            "n": int(len(se_std)),
            "rho": float(rho_strat),
            "p_two_sided": float(p_two),
            "p_one_sided_positive": float(p_one),
            "verdict": verdict(p_one),
        },
        {
            "test": f"Stratified_Spearman_permutation_{n_perm}",
            "scope": "all (modality-controlled)",
            "n": int(len(se_std)),
            "rho": float(rho_strat),
            "p_two_sided": p_perm_two_sided,
            "p_one_sided_positive": p_perm_one_sided_positive,
            "verdict": verdict(p_perm_one_sided_positive),
        },
    ]


# ---------- driver ----------
def run(input_csv: Path, output_csv: Path, threshold: float,
        n_perm: int, seed: int) -> None:
    df = load_and_prepare(input_csv, threshold)

    print(f"\n{'='*72}\nTEST 1 -- Mann-Whitney U, pooled across all modalities")
    print('='*72)
    r1 = mann_whitney_pooled(df)
    print(f"  n_high = {r1['n_high']}, n_low = {r1['n_low']}")
    print(f"  Mean SE in high-D group: {r1['mean_SE_high_D']:.4f}")
    print(f"  Mean SE in low-D group:  {r1['mean_SE_low_D']:.4f}")
    print(f"  U = {r1['U']:.1f},  two-sided p = {r1['p_two_sided']:.4f},  "
          f"one-sided p (high>low) = {r1['p_one_sided_SE_higher_in_high_D']:.4f}")
    print(f"  Verdict: {r1['verdict']}")

    print(f"\n{'='*72}\nTEST 2 -- Mann-Whitney U, per modality\n{'='*72}")
    r2 = mann_whitney_per_modality(df)
    for row in r2:
        print(f"\n  {row['scope']} (n_high={row['n_high']}, n_low={row['n_low']}):")
        if np.isnan(row.get('U', np.nan)):
            print(f"    {row['verdict']}")
            continue
        print(f"    Mean SE high-D = {row['mean_SE_high_D']:.4f}, "
              f"low-D = {row['mean_SE_low_D']:.4f}")
        print(f"    U = {row['U']:.1f},  two-sided p = {row['p_two_sided']:.4f},  "
              f"one-sided p = {row['p_one_sided_SE_higher_in_high_D']:.4f}")
        print(f"    Verdict: {row['verdict']}")

    print(f"\n{'='*72}\nTEST 3a -- Spearman ρ(SE, |D_score|), pooled\n{'='*72}")
    r3a = spearman_pooled(df)
    print(f"  n = {r3a['n']}, ρ = {r3a['rho']:+.4f}, "
          f"two-sided p = {r3a['p_two_sided']:.4f}, "
          f"one-sided p (ρ > 0) = {r3a['p_one_sided_positive']:.4f}")
    print(f"  Verdict: {r3a['verdict']}")

    print(f"\n{'='*72}\nTEST 3b -- Spearman per modality + combined\n{'='*72}")
    r3b = spearman_per_modality_and_combined(df)
    for row in r3b:
        if row["test"] == "Spearman_per_modality":
            print(f"  {row['scope']}: n={row['n']}, ρ = {row['rho']:+.4f}, "
                  f"two-sided p = {row['p_two_sided']:.4f}, "
                  f"one-sided p = {row['p_one_sided_positive']:.4f}")
            print(f"    Verdict: {row['verdict']}")
    fisher = next((r for r in r3b if r["test"] == "Combined_Fisher"), None)
    stouf  = next((r for r in r3b if r["test"] == "Combined_Stouffer_sqrtN"), None)
    if fisher:
        print(f"\n  Fisher combined (one-sided p): "
              f"p = {fisher['p_one_sided_positive']:.4f}")
        print(f"    Verdict: {fisher['verdict']}")
    if stouf:
        print(f"  Stouffer combined (√n weighted): "
              f"p = {stouf['p_one_sided_positive']:.4f}")
        print(f"    Verdict: {stouf['verdict']}")

    print(f"\n{'='*72}\nTEST 3c -- Stratified Spearman (modality-controlled) + "
          f"permutation\n{'='*72}")
    r3c = stratified_spearman_with_permutation(df, n_perm, seed)
    asymp = r3c[0]
    perm  = r3c[1]
    print(f"  n = {asymp['n']}, stratified ρ = {asymp['rho']:+.4f}")
    print(f"  Asymptotic two-sided p = {asymp['p_two_sided']:.4f},  "
          f"one-sided p = {asymp['p_one_sided_positive']:.4f}")
    print(f"  Permutation ({n_perm} resamples) two-sided p = "
          f"{perm['p_two_sided']:.4f},  one-sided p = "
          f"{perm['p_one_sided_positive']:.4f}")
    print(f"  Asymptotic verdict:  {asymp['verdict']}")
    print(f"  Permutation verdict: {perm['verdict']}")

    # ---------- save combined results CSV ----------
    out_rows = [r1] + r2 + [r3a] + r3b + r3c
    out_df = pd.DataFrame(out_rows)
    # Reorder columns for readability
    col_order = ["test", "scope",
                 "n", "n_high", "n_low",
                 "mean_SE_high_D", "mean_SE_low_D",
                 "U", "rho",
                 "p_two_sided", "p_one_sided_SE_higher_in_high_D",
                 "p_one_sided_positive",
                 "verdict"]
    for c in col_order:
        if c not in out_df.columns:
            out_df[c] = np.nan
    out_df = out_df[col_order]
    # Round floats for presentation
    float_cols = ["mean_SE_high_D", "mean_SE_low_D", "U", "rho",
                  "p_two_sided", "p_one_sided_SE_higher_in_high_D",
                  "p_one_sided_positive"]
    out_df[float_cols] = out_df[float_cols].astype(float).round(6)

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(output_csv, index=False)
    print(f"\nWrote {len(out_df)} rows to {output_csv}")


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Test the SE -> AI-human disagreement relationship.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input_csv", type=Path,
                        help="Path to the features CSV.")
    parser.add_argument("output_csv", type=Path, nargs="?", default=None,
                        help="Where to write the combined results CSV "
                             "(default: <input_dir>/se_disagreement_results.csv).")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help=f"Threshold for high-disagreement classification "
                             f"(default: {DEFAULT_THRESHOLD}).")
    parser.add_argument("--n-perm", type=int, default=DEFAULT_N_PERM,
                        help=f"Number of permutations for the stratified test "
                             f"(default: {DEFAULT_N_PERM}).")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED,
                        help=f"Random seed for the permutation test "
                             f"(default: {DEFAULT_SEED}).")
    args = parser.parse_args(argv[1:])

    input_csv = args.input_csv.expanduser().resolve()
    if not input_csv.exists():
        print(f"ERROR: input file not found: {input_csv}", file=sys.stderr)
        return 1
    output_csv = (args.output_csv.expanduser().resolve()
                  if args.output_csv
                  else input_csv.with_name("se_disagreement_results.csv"))

    run(input_csv, output_csv, args.threshold, args.n_perm, args.seed)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
