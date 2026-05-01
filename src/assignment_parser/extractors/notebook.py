from __future__ import annotations

import json
import re
from pathlib import Path

from assignment_parser.models.base import Extractor
from assignment_parser.models.schema import Block, Document, Modality, SourceLocation
from assignment_parser.registry import register_extractor

_ATX = re.compile(r"^(#{1,6})\s+", re.MULTILINE)


@register_extractor
class NotebookExtractor(Extractor):
    modality = Modality.NOTEBOOK

    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".ipynb"

    def extract(self, path: Path) -> Document:
        data = json.loads(path.read_text(encoding="utf-8"))
        cells = data.get("cells", [])
        blocks: list[Block] = []
        for idx, cell in enumerate(cells):
            ctype = cell.get("cell_type", "")
            source = cell.get("source", [])
            if isinstance(source, list):
                text = "".join(source)
            else:
                text = str(source)
            text = text.rstrip("\n")
            loc = SourceLocation(cell_index=idx)
            if ctype == "markdown" and text.lstrip().startswith("#"):
                first_line = text.lstrip().split("\n", 1)[0]
                m = _ATX.match(first_line.lstrip())
                level = len(m.group(1)) if m else 1
                blocks.append(
                    Block(
                        text=text.strip(),
                        location=loc,
                        kind="heading",
                        level=level,
                        metadata={"cell_type": "markdown"},
                    )
                )
            elif ctype == "markdown":
                blocks.append(
                    Block(
                        text=text.strip(),
                        location=loc,
                        kind="text",
                        metadata={"cell_type": "markdown"},
                    )
                )
            elif ctype == "code":
                blocks.append(
                    Block(
                        text=text,
                        location=loc,
                        kind="code",
                        metadata={"cell_type": "code"},
                    )
                )
            else:
                blocks.append(
                    Block(
                        text=text,
                        location=loc,
                        kind="text",
                        metadata={"cell_type": ctype},
                    )
                )
        return Document(
            blocks=blocks,
            modality=self.modality,
            source_path=str(path.resolve()),
            metadata={},
        )
