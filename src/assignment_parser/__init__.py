"""Parse student assignments into structured tasks (modalities, rules, renderers are plug-in extensible)."""

from __future__ import annotations

from assignment_parser.models import (
    Block,
    Classifier,
    ClassifierRule,
    Document,
    Extractor,
    Modality,
    ParsedAssignment,
    Renderer,
    Section,
    Segmenter,
    SourceLocation,
    Task,
    TaskType,
    excerpt_raw_text,
)
from assignment_parser.pipeline import parse, render
from assignment_parser.registry import (
    register_extractor,
    register_renderer,
    register_rule,
    registry,
)

# Registration side effects (import order matters for extractor precedence).
from assignment_parser import classifiers as _classifiers  # noqa: F401
from assignment_parser import extractors as _extractors  # noqa: F401
from assignment_parser import renderers as _renderers  # noqa: F401

__all__ = [
    "Block",
    "Classifier",
    "ClassifierRule",
    "Document",
    "Extractor",
    "Modality",
    "ParsedAssignment",
    "Renderer",
    "Section",
    "Segmenter",
    "SourceLocation",
    "Task",
    "TaskType",
    "excerpt_raw_text",
    "parse",
    "render",
    "register_extractor",
    "register_renderer",
    "register_rule",
    "registry",
]
