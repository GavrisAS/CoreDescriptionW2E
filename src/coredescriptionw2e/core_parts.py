"""Conservative paragraph boundaries and reproducible full-duplicate suppression."""

import re

from .domain import CoreInterval, CorePart, ParseIssue, ParseResult, SuppressedDuplicate
from .semantics import ROCKS, ambiguous_hydrocarbons, hydrocarbons, lithology

POSITION = re.compile(r"^(?:Верх|Низ)\s*:\s*\S", re.I)
POWER = re.compile(r"^\d+(?:[.,]\d+)?\s*м\s*[-–—:]\s*(.+)", re.I)


def safe_key(text: str) -> str:
    # No case folding, number/punctuation removal, or reference expansion.
    return re.sub(r"\s+", " ", text).strip()


def boundary(text: str) -> bool:
    if POSITION.match(text):
        return True
    if m := POWER.match(text):
        return bool(
            re.match(
                r"(?:"
                + "|".join(p for p, _ in ROCKS)
                + r"|переслаиван\w*|чередован\w*|кора\s+выветривания)\b",
                m[1],
                re.I,
            )
        )
    return False


def split_core(core: CoreInterval) -> tuple[list[CorePart], bool]:
    text = core.full_text
    starts = []
    offset = 0
    for block in core.body_blocks:
        if boundary(block.text):
            starts.append(offset)
        offset += len(block.text) + 1
    # An unclassified preamble or an isolated marker cannot justify a split.
    uncertain_power = any(POWER.match(b.text) and not boundary(b.text) for b in core.body_blocks)
    proven = len(starts) >= 2 and starts[0] == 0 and not uncertain_power
    ambiguous = bool(starts) and not proven and (len(starts) > 1 or starts[0] != 0)
    ambiguous = ambiguous or uncertain_power
    # Multiple explicit markers inside a paragraph are retained, never split mid-sentence.
    if re.search(r"\S[^\n]*\b(?:Верх|Низ)\s*:", text, re.I) and not proven:
        ambiguous = True
    spans = starts if proven else [0]
    parts = []
    for ordinal, start in enumerate(spans, 1):
        end = spans[ordinal] - 1 if ordinal < len(spans) else len(text)
        fragment = text[start:end]
        blocks = []
        offset = 0
        for block in core.body_blocks:
            if start <= offset < end:
                blocks.append(block)
            offset += len(block.text) + 1
        parts.append(
            CorePart(
                core.source,
                blocks[0].source,
                ordinal,
                tuple(blocks),
                "\n".join(b.raw for b in blocks),
                fragment,
                start,
                end,
                proven,
                lithology(fragment) if not ambiguous else None,
                hydrocarbons(fragment),
            )
        )
    # Only the inter-paragraph newline may lie between adjacent spans.
    assert "\n".join(p.full_text for p in parts) == text
    assert [b for p in parts for b in p.body_blocks] == core.body_blocks
    return parts, ambiguous


def prepare_cores(result: ParseResult) -> None:
    retained: list[CoreInterval] = []
    for core in result.cores:
        core.parts, ambiguous = split_core(core)
        for part in core.parts:
            if ambiguous_hydrocarbons(part.full_text):
                result.issues.append(
                    ParseIssue(
                        "ambiguous_hydrocarbons",
                        "Неоднозначный УВ-признак: исходное выражение сохранено в W, V по этому фрагменту пустая.",
                        source=part.source,
                    )
                )
            if part.lithology is None:
                result.issues.append(
                    ParseIssue(
                        "ambiguous_lithology",
                        "Краткая литология не определена однозначно; W сохранена полностью.",
                        source=part.source,
                    )
                )
        if ambiguous:
            result.issues.append(
                ParseIssue(
                    "split_ambiguous",
                    "Границы частей неоднозначны; описание сохранено целиком.",
                    source=core.source,
                )
            )
        context = safe_key(
            "\n".join(
                b.text
                for b in core.source_fields
                if not re.fullmatch(r"Керн\s*(?:№\s*)?\d+", b.text, re.I)
            )
        )
        duplicate = next(
            (
                other
                for other in retained
                if (
                    core.drilling == other.drilling
                    and core.drilled == other.drilled
                    and core.recovered == other.recovered
                    and safe_key(core.full_text) == safe_key(other.full_text)
                    and {k: m.value for k, m in core.metrics.items()}
                    == {k: m.value for k, m in other.metrics.items()}
                    and context
                    == safe_key(
                        "\n".join(
                            b.text
                            for b in other.source_fields
                            if not re.fullmatch(r"Керн\s*(?:№\s*)?\d+", b.text, re.I)
                        )
                    )
                )
            ),
            None,
        )
        if duplicate:
            result.suppressed_duplicates.append(
                SuppressedDuplicate(
                    core.source,
                    duplicate.source,
                    core.number,
                    duplicate.number,
                    ("drilling", "drilled", "recovered", "description", "metrics", "context"),
                    core.drilled,
                    core.recovered,
                )
            )
            result.issues.append(
                ParseIssue(
                    "suppressed_duplicate",
                    "Исключено как полный дубль; исходная запись сохранена в реестре.",
                    source=core.source,
                )
            )
        else:
            retained.append(core)
