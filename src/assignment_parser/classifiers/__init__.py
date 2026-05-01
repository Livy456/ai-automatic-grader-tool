from __future__ import annotations

from assignment_parser.classifiers import rules as _rules  # noqa: F401
from assignment_parser.classifiers.llm import LLMClassifier
from assignment_parser.classifiers.rule_based import RuleBasedClassifier

__all__ = ["LLMClassifier", "RuleBasedClassifier"]
