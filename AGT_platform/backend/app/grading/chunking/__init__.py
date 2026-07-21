"""
Chunking: decompose a submission (already parsed to plaintext/artifacts by
:mod:`app.grading.parsing`) into the per-question ``(question, student_response, answer)``
trio that grading operates on — heuristic notebook/cell-order chunkers, blank-template
alignment, LLM/Claude-assisted structured chunking, the OpenAI trio+RAG frontload, RAG
chunk-embedding enrichment, per-question answer-key alignment, and chunk-cache
(de)serialization.

Sibling packages: :mod:`app.grading.parsing` (bytes -> plaintext, reference-file resolution,
upstream of chunking), :mod:`app.grading.multimodal` (pipeline orchestration that calls into
this package), :mod:`app.grading.confidence_calculation`, :mod:`app.grading.rubric_routing`,
and :mod:`app.grading.grading_output`.
"""

from __future__ import annotations
