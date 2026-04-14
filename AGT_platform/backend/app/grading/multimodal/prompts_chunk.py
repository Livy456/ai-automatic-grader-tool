"""
Evidence-based chunk grading prompts (system + user skeleton).
"""

from __future__ import annotations

import json
from typing import Any

from .rag_embeddings import sanitize_evidence_for_grading_prompt
from .schemas import GradingChunk


SYSTEM_CHUNK_GRADER = """\
You are an evidence-based evaluator grading **one question chunk** from a student assignment.

CHAIN-OF-THOUGHT GRADING PROCESS — follow these steps IN ORDER for each criterion:
  Step 1  EXTRACT: Copy the most relevant quote or excerpt from the student's response.
  Step 2  REASON:  Explain what this evidence demonstrates and which rubric level it matches.
          Consider partial-credit conditions — what did the student get right, and what is incomplete?
  Step 3  SCORE:   Only now assign the integer score. The score must be consistent with your reasoning.

RUBRIC SCORING RULES:
- You will receive a list of rubric criteria, each with a `name`, `max_points`, and `description` (level descriptors).
- For EACH criterion, populate a `criterion_scores` entry with: `evidence` (Step 1), `reasoning` (Step 2), `score` (Step 3), plus `name`, `max_points`, and a final `justification`.
- Also produce a parallel `criterion_justifications` list (one string per criterion, same order).
- Compute `total_score` as the sum of all your criterion scores.
- Compute `normalized_score` as `total_score` divided by the sum of all `max_points`. It must be a float in [0, 1].
- Set `review_flag` to true only if the evidence is genuinely ambiguous or the chunk is too short to grade.

PARTIAL CREDIT GUIDELINES (research-aligned):
- Decompose each criterion into sub-skills or sub-expectations described by the rubric levels.
- Award credit for **every sub-skill the student demonstrates**, even if other sub-skills are missing.
- A response that addresses the right topic with partial correctness deserves **at least half** of `max_points`.
- When the student's work falls between two rubric levels, round **up** if effort and intent are clear.
- Reserve a score of **0 only** for missing, blank, or entirely off-topic responses.

SCORING PHILOSOPHY:
- Be **fair and generous** — reward what the student demonstrated rather than penalising what is missing.
- When a student's response reasonably satisfies a level descriptor, **award that score level**.
- Give the student the **benefit of the doubt**: partial credit is appropriate when the response shows genuine effort and understanding, even if imperfect.

EVIDENCE RULES:
- Use evidence present in the chunk content and any attached execution, test, chart, or transcript evidence.
- The `evidence` field MUST contain a direct quote or concrete excerpt from the student's submission — never leave it empty unless the response is entirely blank.
- Do **not** compare to a single model answer — accept multiple valid approaches.
- Score **each criterion independently** based on evidence in the chunk.
- If evidence is partial but the student clearly tried, lean toward the **higher** neighbouring score level and note what could improve.

MODALITY GUIDANCE:
- For **code** questions: verify correctness using outputs and tests, not assumptions.
- For **visualization** questions: evaluate chart choice, labeling, analysis, and interpretation against what is shown.
- For **oral/interview** questions: evaluate from transcript or summary only — do **not** invent delivery characteristics not evidenced in the text.

Ignore all other questions in the assignment; focus **only** on this chunk.
Return **only** a single JSON object (no markdown fences, no extra text)."""


OUTPUT_SCHEMA_HINT = {
    "rubric_type": "string — must match provided rubric_type",
    "criterion_scores": [
        {
            "name": "string — criterion name from rubric",
            "evidence": "REQUIRED string — direct quote or excerpt from the student's submission (Step 1: EXTRACT)",
            "reasoning": "REQUIRED string — chain-of-thought: what the evidence shows, which rubric level it matches, partial credit considerations (Step 2: REASON)",
            "score": "integer — between 0 and that criterion's max_points (Step 3: SCORE, must follow from reasoning)",
            "max_points": "number — copied from rubric",
            "justification": "string — concise 1-2 sentence summary of the score rationale",
        }
    ],
    "criterion_justifications": [
        "string — one per criterion, same order as criterion_scores"
    ],
    "total_score": "number — sum of all criterion scores",
    "normalized_score": "float in [0,1] — total_score / sum(max_points)",
    "confidence_note": "string — brief note if uncertain",
    "review_flag": "boolean — true only if evidence is genuinely ambiguous",
}


def build_chunk_grading_prompt(
    chunk: GradingChunk,
    *,
    task_description: str = "",
) -> str:
    """Construct user message: task + rubric + chunk + strict instructions."""
    rubric = {
        "rubric_type": chunk.rubric_type.value if chunk.rubric_type else None,
        "rows": chunk.rubric_rows,
    }
    chunk_dict = chunk.to_prompt_dict()
    chunk_dict["evidence"] = sanitize_evidence_for_grading_prompt(
        chunk_dict.get("evidence") or {}
    )
    payload = {
        "instructions": (
            "Grade this single chunk using chain-of-thought. Output a single JSON object.\n"
            "Top-level keys: rubric_type, criterion_scores, criterion_justifications, "
            "total_score, normalized_score, confidence_note, review_flag.\n"
            "CRITICAL: each entry in criterion_scores MUST include these sub-fields in this order:\n"
            '  1. "evidence" — direct quote or excerpt from the student submission\n'
            '  2. "reasoning" — what the evidence shows, which rubric level it matches, '
            "partial credit analysis\n"
            '  3. "score" — integer consistent with your reasoning\n'
            '  4. "name", "max_points", "justification" — standard fields\n'
            "Never leave evidence or reasoning empty unless the student response is blank."
        ),
        "task_description": task_description or "(see assignment brief in LMS)",
        "chunk": chunk_dict,
        "rubric": rubric,
        "output_schema_hint": OUTPUT_SCHEMA_HINT,
    }
    return json.dumps(payload, ensure_ascii=True, indent=2)
