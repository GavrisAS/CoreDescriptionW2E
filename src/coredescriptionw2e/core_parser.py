"""Two-cell core interval grammar. Physical rows, never inferred layer depths."""

import re
from decimal import Decimal

from .domain import (
    CoreInterval,
    DocumentMetadata,
    ImportedDocument,
    Metric,
    NoCoreInterval,
    ParseIssue,
    ParseResult,
)
from .metrics import computed, extract, recovery, value
from .parser import CORE_HEADING, LAYER, RUN, WELL, intervals

CORE = re.compile(r"^Керн\s*(?:№\s*)?(\d+)\s*$", re.I)
NO_CORE = re.compile(r"(?:керн\s+не\s+поднят|бурение\s+без\s+(?:отбора\s+)?керна)", re.I)


def parse_core_document(
    document: ImportedDocument, *, percent_tolerance: Decimal = Decimal("0.5")
) -> ParseResult:
    result = ParseResult(
        document,
        DocumentMetadata(),
        [],
        list(document.issues),
        mode="CORE_INTERVAL",
        percent_tolerance=percent_tolerance,
    )
    if any(RUN.match(b.text) or LAYER.match(b.text) for b in document.blocks):
        result.issues.append(
            ParseIssue("ambiguous_boundaries", "Смешанные структурные семейства.", "error")
        )
        return result
    for block in document.blocks:
        if CORE.fullmatch(block.text):
            break
        if m := WELL.search(block.text):
            if result.metadata.well and result.metadata.well != m[1]:
                result.issues.append(
                    ParseIssue(
                        "well_conflict", "Противоречащие номера скважины.", "error", block.source
                    )
                )
            result.metadata.well = m[1]
            result.metadata.sources["well"] = block.source
        if m := re.search(r"\bМесторождение\s*:\s*(.+?)(?:;|$)", block.text, re.I):
            result.metadata.field_name = m[1]
            result.metadata.sources["field_name"] = block.source
    accounted = 0
    used_positions: set[str] = set()
    for row in document.table_rows:
        if not any(CORE_HEADING.fullmatch(b.text) for c in row.cells for b in c.blocks) and not any(
            NO_CORE.search(c.text) for c in row.cells
        ):
            if any(c.text for c in row.cells):
                result.issues.append(
                    ParseIssue(
                        "unclassified_core_row",
                        "Нераспознанная физическая строка таблицы керна.",
                        "error",
                        row.source,
                    )
                )
            continue
        if len(row.cells) != 2:
            result.issues.append(
                ParseIssue(
                    "ambiguous_boundaries",
                    "Требуются две физические ячейки записи.",
                    "error",
                    row.source,
                )
            )
            continue
        left, right = row.cells
        headings = [m for b in left.blocks if (m := CORE.fullmatch(b.text))]
        ranges = intervals(left.text)
        if not ranges and re.search(r"\d,\s*[-–—−‑]", left.text):
            # A trailing punctuation comma is retained verbatim on Control.
            # Numeric interpretation is explicit and always requires review.
            ranges = intervals(re.sub(r"(?<=\d),(?=\s*[-–—−‑])", "", left.text))
            result.issues.append(
                ParseIssue(
                    "interval_punctuation",
                    "Разделитель после целой глубины: числовое чтение требует проверки; исходная ячейка сохранена на Контроль.",
                    source=left.source,
                )
            )
        if len(ranges) != 1 or len(headings) > 1 or row.source.position in used_positions:
            result.issues.append(
                ParseIssue(
                    "ambiguous_boundaries",
                    "Неоднозначные границы записи или интервала.",
                    "error",
                    row.source,
                )
            )
            continue
        used_positions.add(row.source.position)
        if not headings:
            if NO_CORE.search(right.text):
                result.no_core.append(
                    NoCoreInterval(
                        ranges[0],
                        row.source,
                        right.text,
                        "NO_RECOVERY"
                        if re.search(r"керн\s+не\s+поднят", right.text, re.I)
                        else "DRILLED_WITHOUT_CORE",
                    )
                )
            else:
                result.issues.append(
                    ParseIssue("missing_identifier", "Отсутствует ID керна.", "error", row.source)
                )
            continue
        accounted += 1
        if (
            not right.blocks
            or any(CORE.fullmatch(b.text) for b in right.blocks)
            or re.search(r"(?im)^(?:Всего|Итого|Общая проходка|Линейный вынос)\b", right.text)
        ):
            result.issues.append(
                ParseIssue(
                    "missing_text",
                    "Описание отсутствует или содержит соседнюю структуру.",
                    "error",
                    right.source,
                )
            )
            continue
        metrics, issues = extract(left.text, left.source)
        # Per-field provenance names the actual metric paragraph.
        for key in list(metrics):
            for block in left.blocks:
                single, _ = extract(block.text, block.source)
                if key in single:
                    metrics[key] = single[key]
                    break
        result.issues.extend(issues)
        drilled = metrics.get("declared_drilled")
        recovered = recovery(metrics)
        if drilled is None or recovered is None:
            result.issues.append(
                ParseIssue(
                    "missing_metric", "Отсутствует явная проходка или вынос.", "error", left.source
                )
            )
            continue
        computed(metrics, drilled.value, recovered, left.source)
        record = CoreInterval(
            int(headings[0][1]),
            row.source,
            ranges[0],
            drilled.value,
            recovered,
            metrics["declared_percent"].value if "declared_percent" in metrics else None,
            right.blocks,
            metrics,
            source_fields=left.blocks,
        )
        # Composite descriptions remain complete; U/V require separate unambiguous evidence.
        result.cores.append(record)
        result.issues.append(
            ParseIssue(
                "uninterpreted_core_lithology",
                "Литология и УВ не интерпретированы; полное описание в W.",
                source=right.source,
            )
        )
    source_count = sum(bool(CORE_HEADING.fullmatch(b.text)) for b in document.blocks)
    if accounted != source_count or len(result.cores) != source_count or not source_count:
        result.issues.append(
            ParseIssue(
                "unsupported_core_layout",
                "Не все исходные записи имеют доказанные границы ячеек и обязательные поля.",
                "error",
            )
        )
    # Totals must be outside the core metric/description cells. Header paragraphs
    # are retained by the importer and do not get appended to W.
    core_positions = {
        b.source.position for row in document.table_rows for c in row.cells for b in c.blocks
    }
    for block in document.blocks:
        if block.source.position in core_positions or block.source.cell_id is not None:
            continue
        text = block.text
        for key, pattern in (
            ("declared_drilled", r"^Общая\s+проходка\b.*?[-–—:=]\s*(\d+(?:[.,]\s*\d+)?)\s*м"),
            (
                "linear_recovery",
                r"^Линейный\s+вынос\s+керна\b.*?[-–—:=]\s*(\d+(?:[.,]\s*\d+)?)\s*м",
            ),
        ):
            if m := re.search(pattern, text, re.I):
                metric = Metric(value(m[1]), key, block.source)
                if key in result.totals and result.totals[key].value != metric.value:
                    result.issues.append(
                        ParseIssue(
                            "summary_conflict",
                            "Противоречащие итоги источника.",
                            source=block.source,
                        )
                    )
                result.totals[key] = metric
    from .validation import validate_core

    result.issues.extend(validate_core(result))
    return result


def sums(records: list[CoreInterval]) -> dict[str, Decimal]:
    return {
        "drilled": sum((r.drilled for r in records), Decimal(0)),
        "recovered": sum((r.recovered for r in records), Decimal(0)),
    }
