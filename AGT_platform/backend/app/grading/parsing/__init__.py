"""
Submission parsing: modality detection, artifact-bytes -> plaintext extraction, question/response
chunking (structured, notebook-aware, LLM/Claude-assisted, blank-template-aligned), and reference
resolution (dataset / answer-key / blank-template file matching) used before grading.
"""

from __future__ import annotations
