from __future__ import annotations

import re
from pathlib import Path

from assignment_parser.models.base import Extractor
from assignment_parser.models.schema import Block, Document, Modality, SourceLocation
from assignment_parser.registry import register_extractor


_BULLET_PREFIX = re.compile(r"^\s*[-*•]\s+")


def _is_pure_bullet_paragraph(text: str) -> bool:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False
    return all(_BULLET_PREFIX.match(ln) for ln in lines)


def _bullet_lines(text: str) -> list[str]:
    out: list[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        m = re.match(r"^\s*[-*•]\s+(.*)$", s)
        if m:
            out.append(m.group(1).strip())
    return out


@register_extractor
class MarkdownExtractor(Extractor):
    modality = Modality.MARKDOWN

    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() in {".md", ".markdown", ".txt"}

    def extract(self, path: Path) -> Document:
        raw = path.read_text(encoding="utf-8", errors="replace")
        paragraphs = re.split(r"\n\s*\n", raw)
        blocks: list[Block] = []
        char_offset = 0
        para_idx = 0
        for para in paragraphs:
            para_stripped = para.strip()
            if not para_stripped:
                char_offset += len(para) + 2
                continue
            loc = SourceLocation(paragraph_index=para_idx, char_offset=char_offset)
            if _is_pure_bullet_paragraph(para_stripped):
                for item in _bullet_lines(para_stripped):
                    blocks.append(
                        Block(
                            text=item,
                            location=SourceLocation(
                                paragraph_index=para_idx,
                                char_offset=char_offset,
                            ),
                            kind="list_item",
                            metadata={"source": "markdown"},
                        )
                    )
                    char_offset += len(item) + 1
            else:
                blocks.append(
                    Block(
                        text=para_stripped,
                        location=loc,
                        kind="text",
                        metadata={"source": "markdown"},
                    )
                )
                char_offset += len(para_stripped)
            para_idx += 1
            char_offset += len(para) + 2

        return Document(
            blocks=blocks,
            modality=self.modality,
            source_path=str(path.resolve()),
            metadata={},
        )
