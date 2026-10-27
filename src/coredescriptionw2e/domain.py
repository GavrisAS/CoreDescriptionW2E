"""Shared, source-aware domain objects. No GUI or spreadsheet dependencies."""

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Literal

Severity = Literal["warning", "error"]
BlockKind = Literal["header", "run", "stratum", "layer", "body", "summary", "signature"]


@dataclass(frozen=True)
class SourceRef:
    file_id: str
    position: str
    order: int


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


@dataclass
class ImportedDocument:
    file_id: str
    raw_text: str
    blocks: list[TextBlock]
    issues: list[ParseIssue] = field(default_factory=list)


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


@dataclass
class ParseResult:
    document: ImportedDocument
    metadata: DocumentMetadata
    runs: list[CoringRun]
    issues: list[ParseIssue]
    classified: list[tuple[SourceRef, BlockKind]] = field(default_factory=list)

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
    status: Literal["success", "error"]
    issues: list[ParseIssue]
    parsed: ParseResult | None = None


class ConversionError(Exception):
    """Expected failure with a safe, path-free diagnostic."""

    def __init__(self, message: str, *, code: str = "conversion_failed") -> None:
        super().__init__(message)
        self.code = code
