from __future__ import annotations

from assignment_parser.models.base import Renderer
from assignment_parser.models.schema import ParsedAssignment, Section, Task, TaskType
from assignment_parser.registry import register_renderer

_TASK_LABELS: dict[TaskType, str] = {
    TaskType.CODE_TODO: "Code (TODO / exercise)",
    TaskType.FIND_THE_MISTAKE: "Find the mistake",
    TaskType.WRITTEN_RESPONSE: "Written response",
    TaskType.SELF_RATING: "Self-rating / scale",
    TaskType.CRITIQUE: "Critique",
    TaskType.REFLECTION: "Reflection",
    TaskType.DISCUSSION: "Discussion",
    TaskType.READING: "Reading",
    TaskType.PROVIDED_EXAMPLE: "Provided example",
    TaskType.INSTRUCTION: "Instruction",
    TaskType.UNKNOWN: "Unknown",
}


def _escape_cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def _render_section_tree(sections: list[Section], indent: int = 0) -> list[str]:
    lines: list[str] = []
    pad = "  " * indent
    for sec in sections:
        lines.append(f"{pad}- **{sec.title}** (level {sec.level})")
        lines.extend(_render_section_tree(sec.children, indent + 1))
    return lines


@register_renderer
class MarkdownRenderer(Renderer):
    name = "markdown"

    def render(self, parsed: ParsedAssignment) -> str:
        out: list[str] = []
        out.append(f"# {parsed.title}\n")
        out.append(f"**Modality:** `{parsed.modality.value}`\n")
        if parsed.source_path:
            out.append(f"**Source:** `{parsed.source_path}`\n")
        out.append("\n## Section outline\n")
        out.extend(_render_section_tree(parsed.sections))
        out.append("\n## Tasks by section\n")
        by_section: dict[str | None, list[Task]] = {}
        for t in parsed.tasks:
            key = t.section_title
            by_section.setdefault(key, []).append(t)
        for sec_title, tasks in by_section.items():
            title = sec_title or "(no section)"
            out.append(f"\n### {title}\n")
            for t in tasks:
                label = _TASK_LABELS.get(t.task_type, t.task_type.value)
                loc = t.location.describe()
                out.append(
                    f"- **{label}** (confidence {t.confidence:.2f}, {loc}): "
                    f"{t.description}\n"
                )
        out.append("\n## Summary\n")
        out.append("\n| Type | Description | Confidence | Section | Location |\n")
        out.append("| --- | --- | --- | --- | --- |\n")
        for t in parsed.tasks:
            typ = _TASK_LABELS.get(t.task_type, t.task_type.value)
            out.append(
                "| "
                + " | ".join(
                    [
                        _escape_cell(typ),
                        _escape_cell(t.description[:120]),
                        f"{t.confidence:.2f}",
                        _escape_cell(t.section_title or ""),
                        _escape_cell(t.location.describe()),
                    ]
                )
                + " |\n"
            )
        return "".join(out)


@register_renderer
class JsonRenderer(Renderer):
    name = "json"

    def render(self, parsed: ParsedAssignment) -> str:
        return parsed.to_json()
