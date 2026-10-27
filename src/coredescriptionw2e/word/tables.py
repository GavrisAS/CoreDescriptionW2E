"""Source cell boundaries shared by the XML and bounded binary adapters."""

import re

from coredescriptionw2e.domain import SourceCell, SourceRef, SourceRow, TextBlock

from .common import normalize


def cell(file_id: str, position: str, order: int, raw: str) -> SourceCell:
    source = SourceRef(file_id, position, order)
    blocks = [
        TextBlock(line, normalize(line), SourceRef(file_id, f"{position}/p:{n}", order + n))
        for n, line in enumerate(re.split(r"[\r\n\x0b\x0c]", raw))
        if normalize(line)
    ]
    return SourceCell(source, blocks)


def binary_core_rows(file_id: str, raw: str) -> list[SourceRow]:
    """Only the flat two-cell, double-cell-mark row grammar is supported.

    Does not infer arbitrary DOC table layout: nested/merged/other cell counts
    fail the core parser's exhaustive heading and row accounting.
    """
    rows = []
    start = 0
    for n, match in enumerate(re.finditer(r"\x07\x07", raw)):
        segment = raw[start : match.start()]
        parts = segment.split("\x07")
        if len(parts) != 2:
            start = match.end()
            continue
        left, right = parts
        # Ordinary document heading precedes the first table cell in the story.
        heading = re.search(r"(?im)(?:^|(?<=\r))Керн\s*(?:№\s*)?\d+\s*(?:\r|$)", left)
        if heading:
            left_start = start + heading.start()
            left = left[heading.start() :]
        else:
            left_start = start
        pos = f"cp:{left_start}-{match.end()}/row:{n}"
        cells = [
            cell(file_id, f"{pos}/cell:0", left_start, left),
            cell(file_id, f"{pos}/cell:1", start + len(parts[0]) + 1, right),
        ]
        rows.append(SourceRow(SourceRef(file_id, pos, left_start), cells))
        start = match.end()
    return rows
