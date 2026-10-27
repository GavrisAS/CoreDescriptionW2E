"""Anchored deterministic Russian core-description grammar (see ARCHITECTURE)."""

import re
from decimal import Decimal

from .domain import (
    CoringRun,
    DepthInterval,
    DocumentMetadata,
    ImportedDocument,
    Layer,
    ParseIssue,
    ParseResult,
    SourceRef,
)

NUMBER = r"\d+(?:[.,]\d+)?"
DASH = r"[-–—−‑]"
INTERVAL = re.compile(rf"(?<![\d.,])({NUMBER})\s*{DASH}\s*({NUMBER})\s*м?", re.I)
RUN = re.compile(r"^Долбление\s*№?\s*(\d+)\b", re.I)
LAYER = re.compile(r"^Слой\s*№?\s*(\d+)\b[.:]?", re.I)
# Only the complete, numbered two-range heading is an equivalent layer boundary.
# A range mentioned in prose, or an unnumbered interval, remains ordinary text.
INTERVAL_LAYER = re.compile(
    rf"^Интервал\s*№?\s*(\d+)\.\s*"
    rf"(?={NUMBER}\s*{DASH}\s*{NUMBER}\s*м\s*"
    rf"\(\s*{NUMBER}\s*{DASH}\s*{NUMBER}\s*м\s*\)\s*$)",
    re.I,
)
CORE_HEADING = re.compile(r"^Керн\s*(?:№\s*)?\d+\s*$", re.I)
STRATUM = re.compile(r"^(?:Пласт\s+\S+|Доюрский\s+комплекс)\b", re.I)
SUMMARY = re.compile(r"^Всего\b", re.I)
SIGNATURE = re.compile(
    r"^(?:Описание\s+керна\s+(?:составил|выполнил)|геолог\b|Привязк\w*\s+керна|"
    r"Начальник\b|Зав\.|Подпись\b|Отбор\s+керна\s+произв)",
    re.I,
)
WELL = re.compile(r"\bСкв(?:ажина)?\.?\s*(?:№\s*)?([\w-]+)", re.I)
LITH = re.compile(
    r"^(?:[=+@-]\s*)?(?:\d+(?:[.,]\d+)?\s*м\s*[-–—:]?\s*)?"
    r"(?P<rock>песчаник(?:и)?|алевролит(?:ы)?|аргиллит(?:ы)?|глина|глины|известняк(?:и)?|доломит(?:ы)?|мергель|"
    r"конгломерат|брекчия|гравелит|туф|базальт|диабаз|уголь)\b",
    re.I,
)
MIXED = re.compile(r"\b(?:переслаиван\w*|чередован\w*|перемеж\w*|смешан\w*)\b", re.I)
UV = re.compile(
    r"(?:[\w -]{0,35}запах\w*\s+нефти|[\w ,()-]{0,70}пропитк\w*\s+нефтью|"
    r"нефтенасыщенн\w*)",
    re.I,
)


def decimal(text: str) -> Decimal:
    return Decimal(text.replace(",", "."))


def intervals(text: str) -> list[DepthInterval]:
    return [DepthInterval(decimal(m[1]), decimal(m[2])) for m in INTERVAL.finditer(text)]


def _finish(layer: Layer, issues: list[ParseIssue]) -> None:
    layer.raw_text = "\n".join(b.raw for b in layer.body_blocks)
    layer.full_text = "\n".join(b.text for b in layer.body_blocks)
    if not layer.full_text:
        issues.append(ParseIssue("empty_body", "У слоя нет описания.", "error", layer.source))
        return
    text = re.sub(r"\s+", " ", layer.full_text)
    thickness = re.match(rf"^({NUMBER})\s*м\b", text, re.I)
    if thickness:
        value = decimal(thickness[1])
        if layer.declared_thickness is not None and value != layer.declared_thickness:
            issues.append(
                ParseIssue(
                    "thickness_conflict",
                    "Мощности заголовка и текста расходятся.",
                    "error",
                    layer.source,
                )
            )
        layer.declared_thickness = value
    else:
        issues.append(
            ParseIssue("missing_thickness", "Начальная мощность не найдена.", source=layer.source)
        )
    match = LITH.match(text)
    if match and not MIXED.search(text[:250]):
        # Multiple named lithologies in the opening phrase are ambiguous.
        opening = re.split(r"(?<!\d)[.;]|\.(?!\d)", text, maxsplit=1)[0]
        rocks = re.findall(
            r"\b(песчаник|алевролит|аргиллит|глин|известняк|доломит|мергел|угол)\w*",
            opening,
            re.I,
        )
        if len({r.casefold() for r in rocks}) <= 1:
            layer.lithology = match["rock"].capitalize()
    if layer.lithology is None:
        issues.append(
            ParseIssue(
                "ambiguous_lithology",
                "Краткая литология не определена однозначно.",
                source=layer.source,
            )
        )
    # Keep literal evidence only, including negation; never infer saturation.
    evidence = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if UV.search(sentence):
            evidence.append(sentence)
    layer.hydrocarbons = " ".join(evidence) or None
    if not re.search(r"от\s+верха\s*\.?$", text, re.I):
        issues.append(
            ParseIssue(
                "missing_end_marker",
                "Не найдена заключительная отметка «от верха».",
                source=layer.source,
            )
        )


def parse_document(
    document: ImportedDocument, *, percent_tolerance: Decimal = Decimal("0.5")
) -> ParseResult:
    from .domain import ConversionError

    if percent_tolerance < 0 or not percent_tolerance.is_finite():
        raise ConversionError("Некорректный порог сравнения процентов.", code="invalid_config")
    result = ParseResult(
        document, DocumentMetadata(), [], list(document.issues), percent_tolerance=percent_tolerance
    )
    from .validation import validate

    if any(CORE_HEADING.fullmatch(b.text) for b in document.blocks):
        from .core_parser import parse_core_document

        return parse_core_document(document, percent_tolerance=percent_tolerance)
    run: CoringRun | None = None
    layer: Layer | None = None
    stratum: str | None = None
    stratum_source: SourceRef | None = None
    stopped = False
    run_header: list[str] = []
    header_blocks = []

    def finish_layer() -> None:
        nonlocal layer
        if layer is not None:
            _finish(layer, result.issues)
        layer = None

    def run_fields() -> None:
        if run is None:
            return
        text = " ".join(run_header)
        found = intervals(text)
        run.drilling = found[0] if found else None
        run.gis = found[1] if len(found) > 1 else None
        if len(found) == 1 and re.search(r"по\s+бурению\s+и\s+каротажу", text, re.I):
            run.gis = found[0]
        from .metrics import computed, extract, recovery

        run.metrics, metric_issues = extract(text, run.source)
        for key in list(run.metrics):
            for header_block in header_blocks:
                individual, _ = extract(header_block.text, header_block.source)
                if key in individual:
                    run.metrics[key] = individual[key]
                    break
        result.issues.extend(i for i in metric_issues if i not in result.issues)
        run.drilled = (
            run.metrics["declared_drilled"].value if "declared_drilled" in run.metrics else None
        )
        run.recovered = recovery(run.metrics)
        if metric_issues:
            run.recovered = None
        run.no_core = run.recovered == 0 and "boxed_core" in run.metrics and not run.layers
        computed(run.metrics, run.drilled, run.recovered, run.source)

    for block in document.blocks:
        text = block.text
        if m := RUN.match(text):
            finish_layer()
            run_fields()
            run = CoringRun(int(m[1]), block.source)
            result.runs.append(run)
            run_header = [text[m.end() :]]
            header_blocks = [block]
            stopped = False
            # An explicit stratigraphic heading persists across coring runs,
            # until the next heading; no assignment by numerical overlap.
            result.classified.append((block.source, "run"))
        elif m := LAYER.match(text) or INTERVAL_LAYER.match(text):
            finish_layer()
            run_fields()
            result.classified.append((block.source, "layer"))
            if run is None:
                result.issues.append(
                    ParseIssue("orphan_layer", "Слой вне долбления.", "error", block.source)
                )
                continue
            found = intervals(text[m.end() :])
            remaining = INTERVAL.sub("", text[m.end() :])
            power = re.search(rf"(?:мощность|мощн\.)\s*[:=]?\s*({NUMBER})\s*м", remaining, re.I)
            new_layer = Layer(
                int(m[1]),
                run.number,
                found[0] if found else None,
                found[1] if len(found) > 1 else None,
                decimal(power[1]) if power else None,
                text,
                block.source,
                stratum=stratum,
                stratum_source=stratum_source,
            )
            run.layers.append(new_layer)
            layer = new_layer
            stopped = False
        elif STRATUM.match(text):
            finish_layer()
            stratum = text
            stratum_source = block.source
            result.classified.append((block.source, "stratum"))
        elif SUMMARY.match(text) or SIGNATURE.match(text):
            finish_layer()
            stopped = True
            result.classified.append(
                (block.source, "summary" if SUMMARY.match(text) else "signature")
            )
        elif layer is not None and not stopped:
            if layer.source.cell_role == "LAST" and block.source.cell_role == "OTHER":
                result.classified.append((block.source, "auxiliary"))
                result.issues.append(
                    ParseIssue(
                        "ancillary_table_content",
                        "Содержимое соседней служебной колонки сохранено в импорте и исключено из W.",
                        source=block.source,
                    )
                )
                continue
            layer.body_blocks.append(block)
            result.classified.append((block.source, "body"))
        elif run is not None and not stopped and not run.layers:
            run_header.append(text)
            header_blocks.append(block)
            result.classified.append((block.source, "run"))
        else:
            result.classified.append((block.source, "header" if run is None else "summary"))
            if run is None:
                if m := WELL.search(text):
                    if result.metadata.well and result.metadata.well != m[1]:
                        result.issues.append(
                            ParseIssue(
                                "well_conflict",
                                "В Word указаны разные номера скважины.",
                                "error",
                                block.source,
                            )
                        )
                    result.metadata.well = m[1]
                    result.metadata.sources["well"] = block.source
                field = re.search(r"\bМесторождение\s*:\s*(.+?)(?:;|$)", text, re.I)
                if field:
                    result.metadata.field_name = field[1]
                    result.metadata.sources["field_name"] = block.source
    finish_layer()
    run_fields()
    from .metrics import layered_totals

    result.totals, total_issues = layered_totals(document)
    result.issues.extend(total_issues)
    result.issues.extend(validate(result))
    return result
