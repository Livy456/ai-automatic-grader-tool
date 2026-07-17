"""
Rubric routing: map assignment/chunk signals to the right rubric type and criteria, with
deterministic + LLM-assisted routing, partial-credit calibration, generic-rubric loading, and
fallback rows when no course rubric is attached.
"""

from __future__ import annotations
