"""
Evidence-based chunk grading prompts (system + user skeleton).
"""

from __future__ import annotations

import json
from typing import Any

from app.grading.parsing.answer_key_chunk_enrich import (
    code_reference_matches_student,
    grading_student_code_blob,
)

from .rag_embeddings import (
    _optional_positive_int_env,
    sanitize_evidence_for_grading_prompt,
)
from app.grading.schemas import GradingChunk, RubricType, TaskType


SYSTEM_CHUNK_GRADER = """\
You are an evidence-based evaluator grading **one question chunk** from a student assignment.
Be **calibrated and humane**: many chunks are short, noisy, or PDF-extracted; default to **generous
partial credit** when there is any on-topic student language, not an all-zero rubric sweep.

CHAIN-OF-THOUGHT — for **each** rubric criterion, in order:
  Step 1  EXTRACT: Quote or excerpt only what appears in the submission (prefer ``chunk.evidence.trio.student_response`` when substantive; otherwise use ``chunk.extracted_text`` — see STUDENT TEXT SOURCES below).
  Step 2  REASON:  Say what that evidence shows **for this criterion only**. Do not infer unstated student reasoning. If ``reference_answer_key``, ``matched_answer_key_for_question``, or ``trio_reference_answer_for_this_chunk`` is non-empty, briefly compare the student evidence to that reference (match, partial match, or mismatch). Put that comparison in ``reasoning`` / ``justification`` only — never inside ``evidence``.
  Step 3  SCORE:   Assign the **raw rubric level** for this criterion (see RAW SCORE GRID below). It must follow from Steps 1–2.

RAW SCORE GRID — **mandatory; no exceptions**
- For each criterion, **R** = that row's `max_points` (top of the ordinal scale: levels **0 through R** inclusive).
- You **must** output `raw_score` or `score` as **exactly one** of these values only:
  **0, 0.5, 1, 1.5, 2, 2.5, … , R** — i.e. start at 0, add **0.5** each step, stop at **R**.
  Examples: R=1 → {0, 0.5, 1}. R=3 → {0, 0.5, 1, 1.5, 2, 2.5, 3}. R=4 → {0, 0.5, …, 4}.
- **Do not** output any other decimals (no 2.33, no 3.7, no integers not on this ladder unless they equal a valid step).
- **Server correction (if you slip):** values not on that ladder are adjusted **upward** to the **next** valid half-step on ``[0, R]``, and never above **R** (so the corrected score is always one of the allowed values above). You should still output a valid value yourself — do not rely on correction.
- Use a **half-point** when the submission clearly falls **between** two adjacent rubric descriptors; otherwise use a whole number on the same ladder.
- **0** = no defensible evidence for this criterion (blank/off-topic **for that dimension**). If there is a relevant attempt (even flawed, informal, or brief), prefer **≥ 0.5** over 0. Use **0** sparingly — not as a default when the chunk clearly contains student prose that relates to the assignment.

STUDENT TEXT SOURCES (read in this order):
1. If ``chunk.evidence.trio.student_response`` is long enough to quote (roughly **≥ ~40 chars** of substantive text), use it for ``evidence`` quotes.
2. If it is **empty or much shorter** than ``chunk.extracted_text``, treat ``chunk.extracted_text`` as the student-visible work for this chunk: quote from there (you may skip boilerplate lines that repeat the prompt). This is common for **PDF / journal / free-response** units where structure extraction lags behind the flattened text.
3. If ``trio.question`` is non-empty and ``extracted_text`` begins with that question, you may treat the **remainder** of ``extracted_text`` after the question as the student answer for quoting.

PDF / WRITTEN PROSE — **anti-harshness**:
- Extract **substantive** student sentences; ignore repeated assignment boilerplate when judging.
- **Do not** assign **all zeros** on every criterion unless ``chunk.extracted_text`` is effectively empty or wholly unrelated to the rubric dimensions.
- If several sentences show genuine engagement with the topic, **at least one** criterion should usually be **> 0** on the half-step ladder (unless rubric text forbids it).

RUBRIC FIDELITY:
- **EXACT NAMES ONLY:** `criterion_scores[].name` MUST match `rubric.rows[].name` **exactly**. No `criterion_1`, no extra criteria.
- One object per rubric row; same order as `rubric.rows` when possible.
- Put the raw level in **`raw_score`** (preferred) or **`score`**. Copy `max_points` from the rubric row.
- Include `evidence`, `reasoning`, `justification` per criterion. **`evidence` must be a verbatim substring** (direct quote) from the student's response in this chunk — not a paraphrase, not a rubric phrase, not a grader summary, **not** text copied only from the answer key. Use quotation marks around the excerpt when helpful. Leave empty only if the submission is truly blank for that criterion.
- **`reasoning` and `justification`** should tie scores to both the rubric and (when provided) the answer-key fields above, while keeping ``evidence`` student-only.
- Also output `criterion_justifications` (one string per criterion, same order).
- Optional `total_score` / `normalized_score`: the **server recomputes** the official score from your raw levels; do not trust self-computed linear ratios.
- `review_flag`: true only if evidence is genuinely ambiguous or the chunk is too short to grade.

FAIRNESS — avoid **harsh** grading:
- Reward **partial mastery**: if the rubric text leaves room between “missing” and “complete,” prefer the level that **best matches quoted evidence**, not the lowest defensible level.
- **Ambiguity favors the student** when the chunk plausibly supports a higher band; cite the lines that justify the higher score. When unsure between two adjacent half-steps, choose the **higher** one if any quoted line supports it.
- Do **not** double-penalize: if one gap already lowers one criterion, do not use the same gap to justify min scores on unrelated criteria.
- If level descriptors are vague, interpret them **charitably** in favor of evident learning, while still requiring **some** quoted support for non-zero scores.
- **Short answers:** brevity alone is **not** “no evidence”; a few clear sentences can justify mid-ladder scores when rubric levels allow.

RUBRIC QUALITY (helps models grade less harshly and more consistently — apply when reading `rubric.rows`):
- Prefer **observable** level descriptors (“states X”, “shows Y”) over vague terms (“insightful”, “weak”) without anchors.
- Separate **dimensions** (e.g. correctness vs. communication) so one weakness does not collapse every score.
- Add **explicit partial-credit** bullets for mid levels (what “adequate” vs “strong” looks like).
- Keep **R** modest and aligned to real distinctions; very large R without rich descriptors invites score compression at the bottom.
- If the rubric has only high bars, treat “mostly meets” as **mid-scale**, not failure, when evidence supports it.

MODALITY GUIDANCE:
- **Code:** use outputs/tests shown in the chunk, not assumptions.
- **Visualization:** judge only what is visible in charts/tables provided.
- **Oral/interview:** transcript/summary only — do not invent delivery traits.

ORAL / MOCK INTERVIEW — **Bias & limitations** (when rubric rows include that theme):
- Oral transcripts are noisy: disfluencies and interviewer overlap must not lower unrelated scores.
- For **Bias & Limitations Awareness** (match the rubric row name exactly): treat **implicit** signals as on-topic — hedging, uncertainty, caveats, "I don't know," missing data, alternative explanations, trade-offs, or what the analysis cannot prove. **Do not** reserve credit only for explicit words "bias" or "limitation."
- Use **0** on that row only when nothing in the quoted student text plausibly reflects uncertainty, risk, or scope limits for the question asked. When any such signal appears, prefer **≥ 0.5** on the half-step ladder over 0.
- Behavioral or storytelling questions may not invite deep technical limitation talk; calibrate that row to what the prompt reasonably expects.

When ``chunk.evidence.trio`` is present, use **question** / **student_response** / **answer_key_segment**
as structured hints; if ``student_response`` is empty or too short, you **must** still use
``chunk.extracted_text`` per **STUDENT TEXT SOURCES** (flattened PDF prose often lands there).

REFERENCE ANSWER / ANSWER KEY (when provided in the user payload as ``reference_answer_key``):
- Use it to **calibrate** expectations for **conceptual correctness**, **depth**, and **evidence quality** — not as a template for verbatim matching.
- The **student's response does not need to match** the reference wording, structure, length, or examples. Different valid approaches, notation, or ordering should receive **fair credit** when they satisfy the rubric.
- Prefer the rubric + **quoted student evidence** as the primary basis for scores; use the reference to resolve ambiguity about what “adequate” or “complete” looks like for this assignment.
- If the reference covers content not present in this chunk, **ignore** that part for this chunk. If the chunk has no reference section, grade from the rubric and student text alone.

PER-CHUNK REFERENCE (``matched_answer_key_for_question`` / ``trio_reference_answer_for_this_chunk``):
- When present, these are the instructor / answer-key excerpts **scoped to this question only**. Prefer them over the global ``reference_answer_key`` when they conflict or when the global key is truncated.
- If they show a **minimal** expected solution (e.g. a single import line, one expression) and the student’s submission **matches** that solution (allowing trivial whitespace or harmless comments), treat the work as **complete for that scope**: award the **top** rubric level for **conceptual correctness** (and for “evidence” / “depth” rows when the prompt for that item only required that minimal output—do **not** punish brevity). Quote the student’s matching line as ``evidence``.

PROGRAMMING / SCAFFOLDED RUBRIC — **MANDATORY** WHEN ``exact_scaffolded_code_matches_reference`` IS TRUE:
- The user payload may set ``exact_scaffolded_code_matches_reference``: true when the student’s executable code **matches** the instructor reference for this chunk (same non-comment lines, or a one-line submission that appears as a required line in the reference).
- When that field is **true**, you **must** output **raw_score = max_points** (the top of the 0…R ladder, i.e. **R**) for **every** criterion in ``rubric.rows`` for this chunk — including **Functional Correctness**, **Logical Implementation**, **Code Quality**, and **Edge Case Awareness**. At this scope, “no extra edge-case code” means **not applicable**: award **full** Edge Case Awareness. Do **not** deduct for missing setup, runtime hooks, or commentary that the reference does not show.
- ``reasoning`` / ``justification`` must state explicitly that the student code matches the reference for the required lines; ``evidence`` must still quote only the student’s submission.

Ignore other questions; grade **only** this chunk.

Return **only** one JSON object (no markdown fences, no prose outside JSON)."""


SYSTEM_CHUNK_GRADER_ORAL_INTERVIEW_SUPPLEMENT = """\

ORAL / MOCK INTERVIEW — duplicate calibration (same chunk; rubric_type is oral):
- **Bias & Limitations Awareness:** implicit caveats, hedging, acknowledged gaps, and "what we cannot conclude" language count as evidence. Prefer mid-scale partial credit over an all-zero sweep when the student shows any reflective awareness of limits or uncertainty.
"""


def chunk_multimodal_grading_system_prompt(chunk: GradingChunk) -> str:
    """System prompt for per-chunk multimodal grading (oral mock interviews get extra calibration)."""
    if chunk.rubric_type == RubricType.ORAL_INTERVIEW or chunk.task_type == TaskType.ORAL_INTERVIEW:
        return SYSTEM_CHUNK_GRADER + "\n\n" + SYSTEM_CHUNK_GRADER_ORAL_INTERVIEW_SUPPLEMENT.strip()
    return SYSTEM_CHUNK_GRADER


def _is_programming_scaffolded_rubric(chunk: GradingChunk) -> bool:
    if chunk.rubric_type == RubricType.PROGRAMMING_SCAFFOLDED:
        return True
    for row in chunk.rubric_rows or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("name") or "").strip() == "Functional Correctness":
            return True
    return False


OUTPUT_SCHEMA_HINT = {
    "rubric_type": "string — must match provided rubric_type",
    "criterion_scores": [
        {
            "name": "string — must equal rubric.rows[].name (same as criterion_name)",
            "raw_score": "number — raw rubric level on 0..max_points in steps of 0.5 only (alias: score)",
            "max_points": "number — max ordinal R, copied from rubric",
            "evidence": "REQUIRED string — verbatim quote from the student's work (``trio.student_response`` or ``chunk.extracted_text`` per system prompt; never answer-key-only text)",
            "reasoning": "REQUIRED string — evidence-grounded; when an answer key is in the payload, say how the student lines up with it (comparison belongs here, not in evidence)",
            "justification": "string — short summary tied to evidence",
        }
    ],
    "criterion_justifications": [
        "string — one per criterion, same order as criterion_scores",
    ],
    "total_score": "optional — sum of raw scores (server may ignore)",
    "normalized_score": "optional — server recomputes as sum(raw_score)/sum(max_points)",
    "confidence_note": "string — brief note if uncertain",
    "review_flag": "boolean — true only if evidence is genuinely ambiguous",
    "note": (
        "Server validates half-step raw scores and recomputes totals; criterion ``score`` in "
        "exported JSON is the snapped raw rubric level on each row’s scale."
    ),
}


def _inject_trio_student_response_fallback(chunk_dict: dict[str, Any]) -> None:
    """
    If ``trio.student_response`` is missing/short but ``extracted_text`` has body text, copy the
    visible student tail into ``trio`` so small models do not treat the chunk as empty.
    """
    et = str(chunk_dict.get("extracted_text") or "").strip()
    if not et:
        return
    ev = chunk_dict.get("evidence")
    if not isinstance(ev, dict):
        ev = {}
    trio = ev.get("trio")
    if not isinstance(trio, dict):
        trio = {}
    tsr = str(trio.get("student_response") or "").strip()
    qt = str(trio.get("question") or "").strip()
    min_sr = 40
    if len(tsr) >= min_sr or len(et) < min_sr:
        return
    if len(tsr) >= len(et) - 10:
        return
    fill = et
    if qt and et.startswith(qt):
        tail = et[len(qt) :].lstrip("\n").strip()
        if tail:
            fill = tail
    if not str(fill).strip():
        return
    trio2 = dict(trio)
    trio2["student_response"] = str(fill).strip()
    ev2 = dict(ev)
    ev2["trio"] = trio2
    chunk_dict["evidence"] = ev2


def build_chunk_grading_prompt(
    chunk: GradingChunk,
    *,
    task_description: str = "",
    answer_key_text: str = "",
    dataset_context_text: str = "",
) -> str:
    """Construct user message: task + optional answer key + rubric + chunk + strict instructions."""
    rubric = {
        "rubric_type": chunk.rubric_type.value if chunk.rubric_type else None,
        "rows": chunk.rubric_rows,
    }
    chunk_dict = chunk.to_prompt_dict()
    chunk_dict["evidence"] = sanitize_evidence_for_grading_prompt(
        chunk_dict.get("evidence") or {}
    )
    _inject_trio_student_response_fallback(chunk_dict)
    max_chunk = _optional_positive_int_env("MULTIMODAL_CHUNK_PROMPT_MAX_CHARS")
    et = chunk_dict.get("extracted_text") or ""
    if max_chunk is not None and isinstance(et, str) and len(et) > max_chunk:
        chunk_dict["extracted_text"] = et[:max_chunk]
        chunk_dict["extracted_text_prompt_capped"] = True
    ak = (answer_key_text or "").strip()
    ds = (dataset_context_text or "").strip()
    instr_parts = [
        "Grade this single chunk. Output one JSON object.\n",
        "Keys: rubric_type, criterion_scores, criterion_justifications, confidence_note, ",
        "review_flag; optional total_score, normalized_score (server may override).\n",
        "Rubric fidelity: criterion_scores MUST list only rubric.rows names, exact spelling.\n",
        "Each criterion row MUST include: evidence, reasoning, raw_score (or score), ",
        "name, max_points, justification.\n",
        "RAW SCORES — critical: use **only** the ladder 0, 0.5, 1, 1.5, …, max_points for ",
        "each row. No other values. If you output an invalid decimal, the server will ",
        "round **up** to the next valid half-step (capped at max_points); you should ",
        "still emit a correct value yourself. Grade fairly: reward quoted partial work; ",
        "do not default to the lowest band when evidence fits a mid level. ",
        "For PDF/journal chunks, if ``trio.student_response`` looks empty, quote from ",
        "``chunk.extracted_text`` per the system prompt. ",
        "Avoid assigning **all** criteria to 0 unless the chunk truly has no on-topic student text.\n",
        "Evidence: use a verbatim substring from the student's answer in this chunk (quote), ",
        "not a grader paraphrase.",
    ]
    matched_ak = ""
    ev0 = chunk.evidence or {}
    trio = ev0.get("trio") if isinstance(ev0.get("trio"), dict) else {}
    trio_ak = str(trio.get("answer_key_segment") or "").strip()
    aku = ev0.get("answer_key_unit")
    if isinstance(aku, dict):
        matched_ak = str(aku.get("snippet") or "").strip()
    if not matched_ak and trio_ak:
        matched_ak = trio_ak

    student_code = grading_student_code_blob(chunk)
    exact_scaffolded = False
    if _is_programming_scaffolded_rubric(chunk) and student_code.strip():
        for ref in (trio_ak, matched_ak):
            if ref and code_reference_matches_student(
                student=student_code, reference=ref
            ):
                exact_scaffolded = True
                break
        if not exact_scaffolded and ak:
            cap_cmp = min(len(ak), 12_000)
            if code_reference_matches_student(student=student_code, reference=ak[:cap_cmp]):
                exact_scaffolded = True

    if ak:
        instr_parts.append(
            "\nA reference answer key is provided under reference_answer_key. "
            "Use it to judge correctness and depth as described in the system prompt; "
            "the student need not match it exactly."
        )
    if matched_ak:
        instr_parts.append(
            "\n**matched_answer_key_for_question** is the instructor / reference material "
            "segment aligned to **this** question (same numbering as chunk.question_id when possible). "
            "Prefer it over the global key when both appear; it may omit unrelated parts of the key."
        )
    if trio_ak:
        instr_parts.append(
            "\n**trio_reference_answer_for_this_chunk** (when present) is the answer-key line(s) "
            "paired with this chunk’s trio extraction. If the student response satisfies it "
            "(including one-line / minimal keys), award full applicable credit for correctness "
            "and do not treat brevity as missing depth or evidence when the task only required that output."
        )
    if ds:
        instr_parts.append(
            "\nA matched dataset preview is provided under matched_dataset_preview. "
            "Use it only as factual context for interpreting the student’s outputs; "
            "it is not part of the student’s submission."
        )
    if ak or matched_ak or trio_ak:
        instr_parts.append(
            "\nAnswer-key grounding: for **each** criterion, both **reasoning** and "
            "**justification** must explicitly relate the student’s evidence to the "
            "reference material in this payload (``reference_answer_key``, "
            "``matched_answer_key_for_question``, and/or ``trio_reference_answer_for_this_chunk``), "
            "e.g. where the submission agrees or diverges. Do not leave the reference unused "
            "when it applies to that criterion."
        )
    if exact_scaffolded:
        instr_parts.append(
            "\n``exact_scaffolded_code_matches_reference`` is **true**: the student’s code "
            "matches the instructor reference for this chunk. Follow the system prompt: "
            "output **raw_score = max_points** for **every** row in ``rubric.rows`` (all four "
            "scaffolded criteria including Edge Case Awareness)."
        )
    if chunk.rubric_type == RubricType.ORAL_INTERVIEW:
        instr_parts.append(
            "\nOral / mock interview: for **Bias & Limitations Awareness** (exact rubric name), "
            "score hedging, uncertainty, caveats, and acknowledged gaps as relevant evidence; "
            "avoid giving 0 when the transcript shows any plausible reflection on limits or what "
            "is unknown, unless the rubric level text truly excludes that interpretation."
        )
    payload: dict[str, Any] = {
        "instructions": "".join(instr_parts),
        "task_description": task_description or "(see assignment brief in LMS)",
        "chunk": chunk_dict,
        "rubric": rubric,
        "output_schema_hint": OUTPUT_SCHEMA_HINT,
    }
    max_chars = 24_000
    if ak:
        payload["reference_answer_key"] = ak[:max_chars] if len(ak) > max_chars else ak
        if len(ak) > max_chars:
            payload["reference_answer_key_truncated"] = True
    if matched_ak:
        cap_q = min(max_chars, 16_000)
        payload["matched_answer_key_for_question"] = (
            matched_ak[:cap_q] if len(matched_ak) > cap_q else matched_ak
        )
        if len(matched_ak) > cap_q:
            payload["matched_answer_key_for_question_truncated"] = True
    if trio_ak:
        cap_t = min(max_chars, 16_000)
        payload["trio_reference_answer_for_this_chunk"] = (
            trio_ak[:cap_t] if len(trio_ak) > cap_t else trio_ak
        )
        if len(trio_ak) > cap_t:
            payload["trio_reference_answer_for_this_chunk_truncated"] = True
    if ds:
        payload["matched_dataset_preview"] = ds[:max_chars] if len(ds) > max_chars else ds
        if len(ds) > max_chars:
            payload["matched_dataset_preview_truncated"] = True
    if exact_scaffolded:
        payload["exact_scaffolded_code_matches_reference"] = True
    return json.dumps(payload, ensure_ascii=True, indent=2)
