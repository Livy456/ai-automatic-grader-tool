from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Modality(str, Enum):
    NOTEBOOK = "notebook"
    DOCX = "docx"
    PDF = "pdf"
    MARKDOWN = "markdown"
    HTML = "html"
    PLAIN_TEXT = "plain_text"
    VIDEO_TRANSCRIPT = "video_transcript"
    UNKNOWN = "unknown"


class TaskType(str, Enum):
    CODE_TODO = "code_todo"
    FIND_THE_MISTAKE = "find_the_mistake"
    WRITTEN_RESPONSE = "written_response"
    SELF_RATING = "self_rating"
    CRITIQUE = "critique"
    REFLECTION = "reflection"
    DISCUSSION = "discussion"
    READING = "reading"
    PROVIDED_EXAMPLE = "provided_example"
    INSTRUCTION = "instruction"
    UNKNOWN = "unknown"


@dataclass
class SourceLocation:
    cell_index: int | None = None
    page: int | None = None
    paragraph_index: int | None = None
    char_offset: int | None = None
    start_seconds: float | None = None
    end_seconds: float | None = None

    def describe(self) -> str:
        parts: list[str] = []
        if self.cell_index is not None:
            parts.append(f"cell {self.cell_index}")
        if self.page is not None:
            parts.append(f"page {self.page}")
        if self.paragraph_index is not None:
            parts.append(f"paragraph {self.paragraph_index}")
        if self.char_offset is not None:
            parts.append(f"offset {self.char_offset}")
        if self.start_seconds is not None or self.end_seconds is not None:
            s = self.start_seconds if self.start_seconds is not None else "?"
            e = self.end_seconds if self.end_seconds is not None else "?"
            parts.append(f"{s}s–{e}s")
        return ", ".join(parts) if parts else "unknown"


@dataclass
class Block:
    text: str
    location: SourceLocation
    kind: str = "text"
    level: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Document:
    blocks: list[Block]
    modality: Modality
    source_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Section:
    title: str
    blocks: list[Block]
    level: int = 1
    children: list[Section] = field(default_factory=list)
    location: SourceLocation = field(default_factory=SourceLocation)


@dataclass
class Task:
    task_type: TaskType
    description: str
    location: SourceLocation
    section_title: str | None = None
    raw_text: str | None = None
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


_RAW_TEXT_MAX = 400


def excerpt_raw_text(text: str, max_len: int = _RAW_TEXT_MAX) -> str:
    t = text.strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 3].rstrip() + "..."


@dataclass
class ParsedAssignment:
    title: str
    modality: Modality
    sections: list[Section]
    tasks: list[Task]
    source_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        import json

        def _serialize(obj: Any) -> Any:
            if isinstance(obj, Enum):
                return obj.value
            if isinstance(obj, SourceLocation):
                return asdict(obj)
            if isinstance(obj, Block):
                return {
                    "text": obj.text,
                    "location": asdict(obj.location),
                    "kind": obj.kind,
                    "level": obj.level,
                    "metadata": obj.metadata,
                }
            if isinstance(obj, Section):
                return {
                    "title": obj.title,
                    "blocks": [_serialize(b) for b in obj.blocks],
                    "level": obj.level,
                    "children": [_serialize(c) for c in obj.children],
                    "location": asdict(obj.location),
                }
            if isinstance(obj, Task):
                return {
                    "task_type": obj.task_type.value,
                    "description": obj.description,
                    "location": asdict(obj.location),
                    "section_title": obj.section_title,
                    "raw_text": obj.raw_text,
                    "confidence": obj.confidence,
                    "metadata": obj.metadata,
                }
            if isinstance(obj, ParsedAssignment):
                return {
                    "title": obj.title,
                    "modality": obj.modality.value,
                    "sections": [_serialize(s) for s in obj.sections],
                    "tasks": [_serialize(t) for t in obj.tasks],
                    "source_path": obj.source_path,
                    "metadata": obj.metadata,
                }
            raise TypeError(f"Unsupported type for JSON: {type(obj)}")

        return json.dumps(_serialize(self), indent=2, ensure_ascii=False)
