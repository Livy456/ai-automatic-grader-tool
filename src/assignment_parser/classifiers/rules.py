from __future__ import annotations

import re
from dataclasses import dataclass

from assignment_parser.models.base import ClassifierRule
from assignment_parser.models.schema import Block, Section, SourceLocation, Task, TaskType, excerpt_raw_text
from assignment_parser.registry import register_rule

CONTEXT_WINDOW = 5


@dataclass
class ClassificationContext:
    previous_blocks: list[Block]
    next_blocks: list[Block]
    section: Section


def _section_text_for_keywords(section: Section) -> str:
    parts = [section.title]
    for b in section.blocks:
        parts.append(b.text)
    return " ".join(parts).lower()


_TODO_LINE = re.compile(
    r"^\s*#\s*(TODO|FIXME)\s*:?\s*(.*)$",
    re.IGNORECASE | re.MULTILINE,
)
_PLACEHOLDER_LINE = re.compile(
    r"^\s*#\s*(YOUR CODE HERE|INSERT CODE HERE|ADD CODE HERE)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_LIKERT_SECTION_KW = re.compile(
    r"very\s+confident|not\s+confident|confidence|scale|likert|"
    r"strongly\s+agree|strongly\s+disagree|\bagree\b|\bdisagree\b",
    re.IGNORECASE,
)

_FIND_MISTAKE_KW = re.compile(
    r"find\s+the\s+mistake|fix\s+the\s+issue|debug\s+this|what\s+is\s+wrong|what's\s+wrong",
    re.IGNORECASE,
)

_TRY_KW = re.compile(
    r"try\s+it\s+yourself|now\s+it'?s\s+your\s+turn|your\s+turn",
    re.IGNORECASE,
)

_RESPONSE_SLOT = re.compile(
    r"type\s+your\s+response|write\s+your\s+answer|your\s+response\s*:",
    re.IGNORECASE,
)

_CRITIQUE_KW = re.compile(
    r"critique|evaluate|strengths?\s*/\s*weaknesses?|strengths?\s+and\s+weaknesses?",
    re.IGNORECASE,
)

_IMPERATIVE_VERBS = {
    "remove",
    "compute",
    "plot",
    "lowercase",
    "assign",
    "implement",
    "write",
    "explain",
    "calculate",
    "define",
    "list",
    "describe",
    "analyze",
    "create",
    "build",
    "add",
    "delete",
    "fix",
    "change",
    "convert",
    "run",
    "print",
    "import",
    "load",
}

_ADMIN_VERBS = {"save", "download", "upload", "click", "open", "submit", "rename"}


@register_rule
class TodoCommentRule(ClassifierRule):
    priority = 100

    def apply(
        self,
        block: Block,
        section: Section,
        context: ClassificationContext,
    ) -> Task | None:
        if block.kind != "code":
            return None
        text = block.text
        desc_parts: list[str] = []
        for m in _TODO_LINE.finditer(text):
            rest = (m.group(2) or "").strip()
            if rest:
                desc_parts.append(rest)
        for m in _PLACEHOLDER_LINE.finditer(text):
            desc_parts.append(m.group(1).strip().lower().replace(" ", " "))
        if not desc_parts:
            return None
        description = "; ".join(desc_parts)
        return Task(
            task_type=TaskType.CODE_TODO,
            description=description,
            location=block.location,
            section_title=section.title,
            raw_text=excerpt_raw_text(text),
            confidence=1.0,
            metadata={"rule": "TodoCommentRule"},
        )


@register_rule
class LikertScaleRule(ClassifierRule):
    priority = 80

    def apply(
        self,
        block: Block,
        section: Section,
        context: ClassificationContext,
    ) -> Task | None:
        if block.kind not in ("text", "list_item"):
            return None
        if "____" not in block.text and "___" not in block.text:
            return None
        sec_blob = _section_text_for_keywords(section)
        if not _LIKERT_SECTION_KW.search(sec_blob):
            return None
        desc = block.text.strip().split("\n")[0][:500]
        return Task(
            task_type=TaskType.SELF_RATING,
            description=desc,
            location=block.location,
            section_title=section.title,
            raw_text=excerpt_raw_text(block.text),
            confidence=0.88,
            metadata={"rule": "LikertScaleRule"},
        )


@register_rule
class FindTheMistakeRule(ClassifierRule):
    priority = 70

    def apply(
        self,
        block: Block,
        section: Section,
        context: ClassificationContext,
    ) -> Task | None:
        if block.kind != "code":
            return None
        prev_blob = " ".join(b.text for b in context.previous_blocks[-2:])
        if not _FIND_MISTAKE_KW.search(prev_blob):
            return None
        return Task(
            task_type=TaskType.FIND_THE_MISTAKE,
            description="Find and fix the mistake in the provided code.",
            location=block.location,
            section_title=section.title,
            raw_text=excerpt_raw_text(block.text),
            confidence=0.92,
            metadata={"rule": "FindTheMistakeRule"},
        )


@register_rule
class TryItYourselfRule(ClassifierRule):
    priority = 60

    def apply(
        self,
        block: Block,
        section: Section,
        context: ClassificationContext,
    ) -> Task | None:
        if block.kind != "code":
            return None
        prev_blob = " ".join(b.text for b in context.previous_blocks[-3:])
        if not _TRY_KW.search(prev_blob):
            return None
        if block.text.strip():
            return None
        return Task(
            task_type=TaskType.CODE_TODO,
            description="Complete the code exercise (try it yourself).",
            location=block.location,
            section_title=section.title,
            raw_text=excerpt_raw_text(block.text),
            confidence=0.82,
            metadata={"rule": "TryItYourselfRule"},
        )


@register_rule
class TypeYourResponseRule(ClassifierRule):
    priority = 50

    def apply(
        self,
        block: Block,
        section: Section,
        context: ClassificationContext,
    ) -> Task | None:
        if block.kind not in ("text", "list_item"):
            return None
        if not _RESPONSE_SLOT.search(block.text):
            return None
        prev_blob = " ".join(b.text for b in context.previous_blocks)
        sec_blob = (section.title + " " + prev_blob).lower()
        is_critique = bool(_CRITIQUE_KW.search(sec_blob))
        question = ""
        for prev in reversed(context.previous_blocks):
            t = prev.text.strip()
            if t and not _RESPONSE_SLOT.search(t):
                question = t
                break
        if not question:
            question = block.text.strip()
        ttype = TaskType.CRITIQUE if is_critique else TaskType.WRITTEN_RESPONSE
        desc = (question.split("\n")[0]).strip()[:500]
        return Task(
            task_type=ttype,
            description=desc,
            location=block.location,
            section_title=section.title,
            raw_text=excerpt_raw_text(block.text),
            confidence=0.78,
            metadata={"rule": "TypeYourResponseRule"},
        )


@register_rule
class ImperativeBulletRule(ClassifierRule):
    priority = 20

    def apply(
        self,
        block: Block,
        section: Section,
        context: ClassificationContext,
    ) -> Task | None:
        if block.kind != "list_item":
            return None
        words = re.findall(r"[A-Za-z]+", block.text.lower())
        if not words:
            return None
        first = words[0]
        if first in _ADMIN_VERBS:
            return None
        if first not in _IMPERATIVE_VERBS:
            return None
        return Task(
            task_type=TaskType.WRITTEN_RESPONSE,
            description=block.text.strip()[:500],
            location=block.location,
            section_title=section.title,
            raw_text=excerpt_raw_text(block.text),
            confidence=0.42,
            metadata={"rule": "ImperativeBulletRule", "verb": first},
        )
