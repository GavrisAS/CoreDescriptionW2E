"""Explicit metric variants; no depth subtraction is used as a recovery source."""

import re
from decimal import Decimal

from .domain import ImportedDocument, Metric, ParseIssue, SourceRef

NUMBER = r"\d+(?:[.,]\s*\d+)?"
SEP = r"\s*[-–—−‑:=]?\s*"
PATTERNS = {
    "declared_drilled": rf"\b(?:проходка|проход)\b{SEP}({NUMBER})\s*м\b",
    "boxed_core": rf"\bв\s+коробках{SEP}({NUMBER})\s*м?\b",
    "linear_recovery": rf"(?:\b(?:линейный\s+)?вынос(?:\s+керна)?|\bл\s*\.\s*в\s*\.\s*к\s*\.){SEP}({NUMBER})\s*м\b",
    "declared_core": rf"\b(?:проходка|проход)\b{SEP}{NUMBER}\s*м\s*,\s*керна{SEP}({NUMBER})\s*м\b",
    "declared_percent": rf"%\s*выноса{SEP}({NUMBER})|({NUMBER})\s*%",
}


def value(text: str) -> Decimal:
    return Decimal(re.sub(r"\s", "", text).replace(",", "."))


def extract(text: str, source: SourceRef) -> tuple[dict[str, Metric], list[ParseIssue]]:
    metrics = {}
    issues = []
    for variant, pattern in PATTERNS.items():
        values = {
            value(next(g for g in m.groups() if g is not None))
            for m in re.finditer(pattern, text, re.I)
        }
        if len(values) > 1:
            issues.append(
                ParseIssue(
                    "run_metric_conflict", "Противоречащие метрики одного типа.", "error", source
                )
            )
        elif values:
            metrics[variant] = Metric(
                next(iter(values)),
                variant,
                source,
                confidence="PAIRED" if variant == "declared_core" else "EXPLICIT",
            )
    # Explicit boxing is a separate observation, not a conflict with declared core.
    # Without boxing, competing explicit/paired recovery must agree.
    if (
        "boxed_core" not in metrics
        and all(k in metrics for k in ("linear_recovery", "declared_core"))
        and metrics["linear_recovery"].value != metrics["declared_core"].value
    ):
        issues.append(
            ParseIssue("run_metric_conflict", "Противоречащие источники выноса.", "error", source)
        )
    return metrics, issues


def recovery(metrics: dict[str, Metric]) -> Decimal | None:
    for variant in ("boxed_core", "linear_recovery", "declared_core"):
        if variant in metrics:
            return metrics[variant].value
    return None


def computed(
    metrics: dict[str, Metric],
    drilled: Decimal | None,
    recovered: Decimal | None,
    source: SourceRef,
) -> None:
    if drilled and drilled > 0 and recovered is not None:
        metrics["computed_percent"] = Metric(
            recovered / drilled * 100, "computed_percent", source, "COMPUTED", "DERIVED"
        )


def layered_totals(document: ImportedDocument) -> tuple[dict[str, Metric], list[ParseIssue]]:
    """Only explicitly scoped whole-well summary rows, not stratigraphic subtotals."""
    totals: dict[str, Metric] = {}
    issues = []
    for row in document.table_rows:
        if len(row.cells) != 2:
            continue
        left, right = row.cells
        if not left.blocks or not re.match(r"^Всего\s+по\s+скв", left.blocks[0].text, re.I):
            continue
        if len(left.blocks) < 2 or not re.fullmatch(
            r"Всего\s+поднято\s+керна\s*:", left.blocks[1].text, re.I
        ):
            continue
        if len(right.blocks) < 2:
            issues.append(
                ParseIssue(
                    "summary_unreadable",
                    "Итог скважины не прочитан по ячейкам; автоматическая сверка ограничена.",
                    source=row.source,
                )
            )
            continue
        for key, block in zip(
            ("declared_drilled", "linear_recovery"), right.blocks[:2], strict=True
        ):
            if match := re.fullmatch(rf"({NUMBER})\s*м", block.text, re.I):
                metric = Metric(value(match[1]), key, block.source)
                if key in totals and totals[key].value != metric.value:
                    issues.append(
                        ParseIssue(
                            "summary_conflict",
                            "Противоречащие итоги скважины.",
                            source=block.source,
                        )
                    )
                totals[key] = metric
            else:
                issues.append(
                    ParseIssue(
                        "summary_unreadable",
                        "Числовой итог скважины не определён однозначно.",
                        source=block.source,
                    )
                )
    return totals, issues
