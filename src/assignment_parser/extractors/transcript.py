from __future__ import annotations

import re
from pathlib import Path

from assignment_parser.models.base import Extractor
from assignment_parser.models.schema import Block, Document, Modality, SourceLocation
from assignment_parser.registry import register_extractor

_CHAPTER_PREFIX = re.compile(
    r"^\s*(Chapter|Section|Lesson)\s*:\s*(.+)$",
    re.IGNORECASE,
)

_SRT_TIME = re.compile(
    r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})"
)


def _parse_srt_cues(content: str) -> list[tuple[str, float, float, str]]:
    lines = [ln.rstrip("\r") for ln in content.splitlines()]
    cues: list[tuple[str, float, float, str]] = []
    i = 0
    n = len(lines)
    while i < n:
        while i < n and not lines[i].strip():
            i += 1
        if i >= n:
            break
        idx_line = lines[i].strip()
        if not idx_line.isdigit():
            i += 1
            continue
        cue_id = idx_line
        i += 1
        if i >= n:
            break
        time_line = lines[i].strip()
        m = _SRT_TIME.search(time_line)
        if not m:
            i += 1
            continue
        start = _srt_ts_to_seconds(m.group(1))
        end = _srt_ts_to_seconds(m.group(2))
        i += 1
        text_lines: list[str] = []
        while i < n and lines[i].strip():
            text_lines.append(lines[i].strip())
            i += 1
        text = " ".join(text_lines).strip()
        if text:
            cues.append((cue_id, start, end, text))
        while i < n and not lines[i].strip():
            i += 1
    return cues

_VTT_TS = re.compile(
    r"(\d{2}:)?\d{2}:\d{2}\.\d{3}\s*-->\s*(\d{2}:)?\d{2}:\d{2}\.\d{3}",
)


def _srt_ts_to_seconds(ts: str) -> float:
    ts = ts.strip()
    h, m, rest = ts.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def _vtt_ts_to_seconds(ts: str) -> float:
    ts = ts.strip()
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    if len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    return float(ts)


def _parse_vtt_cues(content: str) -> list[tuple[float, float, str]]:
    cues: list[tuple[float, float, str]] = []
    lines = content.splitlines()
    i = 0
    if lines and lines[0].startswith("WEBVTT"):
        i = 1
    while i < len(lines):
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines):
            break
        if "-->" not in lines[i]:
            i += 1
            continue
        m = _VTT_TS.search(lines[i])
        if not m:
            i += 1
            continue
        line = lines[i]
        left, right = line.split("-->", 1)
        start = _vtt_ts_to_seconds(left.strip())
        end_part = right.strip().split()[0]
        end = _vtt_ts_to_seconds(end_part)
        i += 1
        text_lines: list[str] = []
        while i < len(lines) and lines[i].strip() and "-->" not in lines[i]:
            text_lines.append(lines[i].strip())
            i += 1
        text = " ".join(text_lines).strip()
        if text:
            cues.append((start, end, text))
    return cues


@register_extractor
class TranscriptExtractor(Extractor):
    modality = Modality.VIDEO_TRANSCRIPT

    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() in {".srt", ".vtt"}

    def extract(self, path: Path) -> Document:
        raw = path.read_text(encoding="utf-8", errors="replace")
        blocks: list[Block] = []
        suffix = path.suffix.lower()
        if suffix == ".srt":
            for cue_id, start, end, text in _parse_srt_cues(raw):
                loc = SourceLocation(start_seconds=start, end_seconds=end)
                cm = _CHAPTER_PREFIX.match(text)
                if cm:
                    blocks.append(
                        Block(
                            text=text,
                            location=loc,
                            kind="heading",
                            level=1,
                            metadata={"cue_id": cue_id},
                        )
                    )
                else:
                    blocks.append(
                        Block(
                            text=text,
                            location=loc,
                            kind="text",
                            metadata={"cue_id": cue_id},
                        )
                    )
        else:
            for start, end, text in _parse_vtt_cues(raw):
                loc = SourceLocation(start_seconds=start, end_seconds=end)
                cm = _CHAPTER_PREFIX.match(text)
                if cm:
                    blocks.append(
                        Block(
                            text=text,
                            location=loc,
                            kind="heading",
                            level=1,
                            metadata={},
                        )
                    )
                else:
                    blocks.append(
                        Block(
                            text=text,
                            location=loc,
                            kind="text",
                            metadata={},
                        )
                    )
        return Document(
            blocks=blocks,
            modality=self.modality,
            source_path=str(path.resolve()),
            metadata={"format": suffix},
        )
