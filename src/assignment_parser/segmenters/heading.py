from __future__ import annotations

from typing import Iterator

from assignment_parser.models.base import Segmenter
from assignment_parser.models.schema import Block, Document, Section, SourceLocation


def _section_location(blocks: list[Block]) -> SourceLocation:
    if blocks:
        return blocks[0].location
    return SourceLocation()


def _heading_title(block: Block) -> str:
    text = block.text.strip()
    if text.startswith("#"):
        line = text.split("\n", 1)[0].strip()
        return line.lstrip("#").strip() or "Untitled"
    return text.split("\n", 1)[0].strip() or "Untitled"


def walk_blocks_in_order(sections: list[Section]) -> Iterator[tuple[Section, Block]]:
    for sec in sections:
        for b in sec.blocks:
            yield sec, b
        yield from walk_blocks_in_order(sec.children)


def flatten_sections(sections: list[Section]) -> list[Section]:
    out: list[Section] = []

    def rec(nodes: list[Section]) -> None:
        for n in nodes:
            out.append(n)
            rec(n.children)

    rec(sections)
    return out


class HeadingSegmenter(Segmenter):
    def segment(self, document: Document) -> list[Section]:
        blocks = document.blocks
        if not blocks:
            return [
                Section(
                    title="Document",
                    blocks=[],
                    level=1,
                    children=[],
                    location=SourceLocation(),
                )
            ]

        has_heading = any(b.kind == "heading" and b.level is not None for b in blocks)
        if not has_heading:
            return [
                Section(
                    title="Document",
                    blocks=list(blocks),
                    level=1,
                    children=[],
                    location=_section_location(list(blocks)),
                )
            ]

        top_level: list[Section] = []
        stack: list[Section] = []
        pre_blocks: list[Block] = []

        for block in blocks:
            if block.kind == "heading" and block.level is not None:
                if not stack and pre_blocks:
                    top_level.append(
                        Section(
                            title="Preamble",
                            blocks=pre_blocks.copy(),
                            level=0,
                            children=[],
                            location=_section_location(pre_blocks),
                        )
                    )
                    pre_blocks.clear()

                level = block.level
                while stack and stack[-1].level >= level:
                    stack.pop()

                new_sec = Section(
                    title=_heading_title(block),
                    blocks=[block],
                    level=level,
                    children=[],
                    location=block.location,
                )
                if stack:
                    stack[-1].children.append(new_sec)
                else:
                    top_level.append(new_sec)
                stack.append(new_sec)
            else:
                if not stack:
                    pre_blocks.append(block)
                else:
                    stack[-1].blocks.append(block)

        if not top_level and not stack:
            return [
                Section(
                    title="Document",
                    blocks=list(blocks),
                    level=1,
                    children=[],
                    location=_section_location(list(blocks)),
                )
            ]

        return top_level
