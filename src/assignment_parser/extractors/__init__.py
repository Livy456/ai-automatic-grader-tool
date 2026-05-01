"""Built-in extractors (registration order: notebook, transcript, docx, markdown)."""

from __future__ import annotations

from assignment_parser.extractors import notebook as _notebook  # noqa: F401
from assignment_parser.extractors import transcript as _transcript  # noqa: F401
from assignment_parser.extractors import docx as _docx  # noqa: F401
from assignment_parser.extractors import markdown as _markdown  # noqa: F401
