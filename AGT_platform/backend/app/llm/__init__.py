"""
LLM layer: provider routing/clients (:mod:`llm_router` — OpenAI + Anthropic) and the prompt
templates sent to those models (:mod:`prompts` — legacy agent-workflow prompts;
:mod:`prompts_chunk` — the multimodal per-chunk grading system/user prompt builders).

Grading logic (chunking, rubric routing, aggregation, confidence) lives in
:mod:`app.grading`; this package only owns "how we talk to a model provider" and "what we say
to it".
"""

from __future__ import annotations
