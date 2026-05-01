from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from assignment_parser.classifiers.llm import LLMClassifier
from assignment_parser.pipeline import parse, render
from assignment_parser.registry import registry


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="assignment-parser",
        description="Parse student assignments into structured tasks.",
    )
    parser.add_argument("file", type=Path, help="Path to assignment file")
    parser.add_argument(
        "--format",
        default="markdown",
        choices=registry.available_renderers(),
        help="Output format",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Write output to this path (default: stdout)",
    )
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="Use LLMClassifier (requires ASSIGNMENT_PARSER_LLM_PROVIDER=module:callable)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)

    classifier = None
    if args.use_llm:
        classifier = LLMClassifier()

    parsed = parse(args.file, classifier=classifier)
    text = render(parsed, format=args.format)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
