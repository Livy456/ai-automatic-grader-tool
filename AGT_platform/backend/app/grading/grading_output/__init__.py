"""
Grading output structure: LLM grading-response parsing (into ``ParsedChunkGrade``), cheap
deterministic consistency checks, and the final JSON output contract + validation shared by
producers (multimodal pipeline) and consumers (routes, Celery tasks, local tests).
"""

from __future__ import annotations
