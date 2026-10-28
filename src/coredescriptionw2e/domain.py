"""Shared, source-aware domain objects. No GUI or spreadsheet dependencies."""

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Literal

Severity = Literal["warning", "error"]
DescriptionMode = Literal["LAYERED_DESCRIPTION", "CORE_INTERVAL"]
DocumentStatus = Literal["PASS", "REVIEW_REQUIRED", "UNSUPPORTED", "ERROR"]
BlockKind = Literal[
    "header", "run", "stratum", "layer", "body", "summary", "signature", "auxiliary"
]


@dataclass(frozen=True)
class SourceRef:
    file_id: str
    position: str
    order: int
    cell_id: str | None = None
    cell_role: Literal["LAST", "OTHER"] | None = None


@dataclass(frozen=True)
class TextBlock:
    raw: str
    text: str
    source: SourceRef


@dataclass(frozen=True)
class ParseIssue:
    code: str
    message: str
    severity: Severity = "warning"
    source: SourceRef | None = None
    related_sources: tuple[SourceRef, ...] = ()

    @property
    def category(self) -> str:
        from .validation import REVIEW_CODES

        if (
            self.code in REVIEW_CODES
            or self.code
            in {
                "recovery_exceeds_drilled",
                "run_metric_conflict",
                "well_conflict",
                "thickness_conflict",
            }
            or self.code.startswith("invalid_")
        ):
            return "INVALID_SOURCE_DATA"
        if self.code.startswith(("unsupported_doc", "reader_")):
            return "READER_LIMITATION"
        if self.severity == "error":
            return "UNSUPPORTED_STRUCTURE"
        return "INTERPRETATION_LIMITATION"


@dataclass(frozen=True)
class SourceCell:
    source: SourceRef
    blocks: list[TextBlock]

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks)


@dataclass(frozen=True)
class SourceRow:
    source: SourceRef
    cells: list[SourceCell]


@dataclass(frozen=True)
class Metric:
    value: Decimal
    variant: str
    source: SourceRef
    status: Literal["DECLARED", "COMPUTED"] = "DECLARED"
    confidence: Literal["EXPLICIT", "PAIRED", "DERIVED"] = "EXPLICIT"


@dataclass
class ImportedDocument:
    file_id: str
    raw_text: str
    blocks: list[TextBlock]
    issues: list[ParseIssue] = field(default_factory=list)
    table_rows: list[SourceRow] = field(default_factory=list)
    reader_facts: dict[str, str | int] = field(default_factory=dict)


@dataclass(frozen=True)
class DepthInterval:
    top: Decimal
    bottom: Decimal

    @property
    def thickness(self) -> Decimal:
        return self.bottom - self.top


@dataclass
class DocumentMetadata:
    well: str | None = None
    field_name: str | None = None
    sources: dict[str, SourceRef] = field(default_factory=dict)


@dataclass
class Layer:
    number: int
    run_number: int
    drilling: DepthInterval | None
    gis: DepthInterval | None
    declared_thickness: Decimal | None
    header: str
    source: SourceRef
    body_blocks: list[TextBlock] = field(default_factory=list)
    full_text: str = ""
    raw_text: str = ""
    lithology: str | None = None
    hydrocarbons: str | None = None
    stratum: str | None = None
    stratum_source: SourceRef | None = None


@dataclass
class CoringRun:
    number: int
    source: SourceRef
    drilling: DepthInterval | None = None
    gis: DepthInterval | None = None
    drilled: Decimal | None = None
    recovered: Decimal | None = None
    layers: list[Layer] = field(default_factory=list)
    metrics: dict[str, Metric] = field(default_factory=dict)
    no_core: bool = False


@dataclass
class CoreInterval:
    number: int
    source: SourceRef
    drilling: DepthInterval
    drilled: Decimal
    recovered: Decimal
    declared_percent: Decimal | None
    body_blocks: list[TextBlock]
    metrics: dict[str, Metric] = field(default_factory=dict)
    source_fields: list[TextBlock] = field(default_factory=list)
    lithology: str | None = None
    hydrocarbons: str | None = None
    parts: list[CorePart] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        return "\n".join(b.text for b in self.body_blocks)


@dataclass(frozen=True)
class CorePart:
    """Source-backed fragment; ordinal is local and is NEVER a geological ID."""

    parent_source: SourceRef
    source: SourceRef
    ordinal: int
    body_blocks: tuple[TextBlock, ...]
    raw_text: str
    full_text: str
    start: int
    end: int
    split_proven: bool
    lithology: str | None = None
    hydrocarbons: str | None = None


@dataclass(frozen=True)
class SuppressedDuplicate:
    source: SourceRef
    retained_source: SourceRef
    source_number: int
    retained_number: int
    compared_fields: tuple[str, ...]
    drilled_effect: Decimal
    recovered_effect: Decimal


@dataclass(frozen=True)
class NoCoreInterval:
    drilling: DepthInterval
    source: SourceRef
    evidence: str
    kind: Literal["NO_RECOVERY", "DRILLED_WITHOUT_CORE"] = "DRILLED_WITHOUT_CORE"
    drilled: Decimal | None = None
    metrics: dict[str, Metric] = field(default_factory=dict)
    number: int | None = None

    @property
    def recovered(self) -> Decimal | None:
        return Decimal(0) if self.kind == "NO_RECOVERY" else None


@dataclass
class ParseResult:
    document: ImportedDocument
    metadata: DocumentMetadata
    runs: list[CoringRun]
    issues: list[ParseIssue]
    classified: list[tuple[SourceRef, BlockKind]] = field(default_factory=list)

    percent_tolerance: Decimal = Decimal("0.5")
    mode: DescriptionMode = "LAYERED_DESCRIPTION"
    cores: list[CoreInterval] = field(default_factory=list)
    no_core: list[NoCoreInterval] = field(default_factory=list)
    totals: dict[str, Metric] = field(default_factory=dict)
    reconciliation: dict[str, dict[str, Decimal]] = field(default_factory=dict)
    reconciliation_notes: dict[str, str] = field(default_factory=dict)
    suppressed_duplicates: list[SuppressedDuplicate] = field(default_factory=list)

    @property
    def active_cores(self) -> list[CoreInterval]:
        suppressed = {d.source for d in self.suppressed_duplicates}
        return [c for c in self.cores if c.source not in suppressed]

    @property
    def status(self) -> DocumentStatus:
        from .validation import BLOCKING_CODES, REVIEW_CODES

        errors = [i for i in self.issues if i.severity == "error" or i.code in BLOCKING_CODES]
        if any(i.code not in REVIEW_CODES for i in errors):
            return (
                "ERROR"
                if any(
                    i.category == "INVALID_SOURCE_DATA" and i.code not in REVIEW_CODES
                    for i in errors
                )
                else "UNSUPPORTED"
            )
        if (
            errors
            or self.mode == "CORE_INTERVAL"
            or any(i.code in REVIEW_CODES for i in self.issues)
        ):
            return "REVIEW_REQUIRED"
        return "PASS"

    @property
    def exportable(self) -> bool:
        return self.status in {"PASS", "REVIEW_REQUIRED"}

    @property
    def layers(self) -> list[Layer]:
        return [layer for run in self.runs for layer in run.layers]

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)


@dataclass
class ConversionResult:
    source: Path
    output: Path | None
    status: Literal["success", "review_required", "unsupported", "error", "skipped"]
    issues: list[ParseIssue]
    parsed: ParseResult | None = None


class ConversionError(Exception):
    """Expected failure with a safe, path-free diagnostic."""

    def __init__(self, message: str, *, code: str = "conversion_failed") -> None:
        super().__init__(message)
        self.code = code
