"""Versioned 23-column template schema and atomic, formula-safe Excel output."""

import math
import os
import re
import tempfile
import textwrap
import unicodedata
import zipfile
from copy import copy
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.cell.cell import Cell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.workbook.properties import CalcProperties

from coredescriptionw2e.domain import ConversionError, ParseIssue, ParseResult
from coredescriptionw2e.validation import REVIEW_CODES
from coredescriptionw2e.word.common import MAX_FILE_BYTES, check_file

# core23-v1: semantic identifiers, validated by header names, including reordered columns.
HEADERS = (
    "MECTR",
    "PLOSHAD",
    "AREA_CODE",
    "N_SKV",
    "LAS",
    "UWI",
    "ссылка на источник информации",
    "Инт-л долбления Кровля(глуб, м)",
    "Инт-л долбления Подошва(глуб, м)",
    "Проходка, м",
    "линейный Вынос, м",
    "Вынос, %",
    "толщина слоя, м",
    "Кровля слоя по бурению, м",
    "подошва слоя по бурению, м",
    "кровля слоя по ГИС, м",
    "подошва слоя по ГИС, м",
    "Стратиграфическая единица по ГИС",
    "Стратиграфическая единица по керну",
    "Слой-номер",
    "Краткое описание породы",
    "Характер УВ насыщения",
    "Описание слоя",
)


def header_key(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("ё", "е")
    return re.sub(r"[^a-zа-я0-9]", "", text)


def schema(sheet) -> dict[int, int]:
    if sheet.max_column != 23:
        raise ConversionError("Шаблон должен содержать ровно 23 столбца схемы core23-v1.")
    expected = {header_key(value): i for i, value in enumerate(HEADERS)}
    result: dict[int, int] = {}
    for cell in sheet[1]:
        key = header_key(cell.value)
        # The legacy template adds a parenthetical explanation to this header.
        if key.startswith(header_key(HEADERS[18])):
            key = header_key(HEADERS[18])
        if key not in expected or expected[key] in result:
            raise ConversionError("Неизвестный или повторяющийся заголовок шаблона core23-v1.")
        result[expected[key]] = cell.column
    if len(result) != 23:
        raise ConversionError("В шаблоне отсутствуют обязательные заголовки.")
    return result


def set_text(cell: Cell, value: str | None) -> None:
    if value is None:
        cell.value = None
        return
    if len(value) > 32767:
        raise ConversionError("Текст превышает лимит ячейки Excel; усечение запрещено.")
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value):
        raise ConversionError("В тексте есть недопустимые для Excel управляющие символы.")
    cell.value = value
    cell.data_type = "s"
    cell.hyperlink = None
    cell.quotePrefix = value.lstrip().startswith(("=", "+", "-", "@"))


def publish(temp: Path, target: Path, overwrite: bool) -> None:
    if overwrite:
        os.replace(temp, target)
    elif os.name == "nt":
        # Windows rename fails if the destination already exists.
        os.rename(temp, target)
    else:
        # replace() would race with a concurrent producer. Atomic no-clobber link.
        os.link(temp, target)
        temp.unlink()


def write_excel(
    parsed: ParseResult, template: Path, source: Path, output: Path, *, overwrite: bool = False
) -> list[ParseIssue]:
    check_file(template)
    if template.suffix.lower() != ".xlsx":
        raise ConversionError("Нужен шаблон XLSX.")
    if output.resolve() in {source.resolve(), template.resolve()}:
        raise ConversionError("Результат совпадает с исходным файлом.")
    if output.is_symlink() or (output.exists() and not overwrite):
        raise ConversionError("Результат уже существует; перезапись не разрешена.")
    if not parsed.exportable:
        raise ConversionError("Критические ошибки разбора; Excel не опубликован.")
    try:
        with zipfile.ZipFile(template) as archive:
            if sum(i.file_size for i in archive.infolist()) > MAX_FILE_BYTES:
                raise ConversionError("Слишком большой распакованный шаблон.")
        workbook = load_workbook(template, keep_links=False)
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise ConversionError("Некорректный XLSX-шаблон.") from exc
    temp: Path | None = None
    try:
        if len(workbook.worksheets) != 1:
            raise ConversionError("Схема core23-v1 поддерживает шаблон с одним листом.")
        sheet = workbook["Лист1"] if "Лист1" in workbook.sheetnames else workbook.active
        if sheet is None:
            raise ConversionError("В шаблоне нет листа.")
        mapping = schema(sheet)
        if sheet.title in {"Контроль", "Описание"}:
            sheet.title = "Данные core23-v1"
        issues: list[ParseIssue] = []
        example_wells = {
            str(sheet.cell(r, mapping[3]).value).strip()
            for r in (2, 3)
            if sheet.cell(r, mapping[3]).value is not None
        }
        if parsed.metadata.well and example_wells and example_wells != {parsed.metadata.well}:
            issues.append(
                ParseIssue(
                    "template_well_conflict",
                    "Номер скважины Word отличается от примера Excel; использован только Word.",
                )
            )
        styles = {column: copy(sheet.cell(2, column)._style) for column in mapping.values()}
        for merged in list(sheet.merged_cells.ranges):
            if merged.max_row > 1:
                sheet.unmerge_cells(str(merged))
        sheet.delete_rows(2, max(0, sheet.max_row - 1))
        for row in list(sheet.row_dimensions):
            if row > 1:
                del sheet.row_dimensions[row]
        # Remove stale demonstration table ranges and validation on deleted rows.
        sheet.tables.clear()
        sheet.data_validations.dataValidation.clear()
        sheet.conditional_formatting._cf_rules.clear()
        width = max(
            70, min(100, sheet.column_dimensions[get_column_letter(mapping[22])].width or 70)
        )
        sheet.column_dimensions[get_column_letter(mapping[22])].width = width
        row = 2
        entries = (
            [(run, layer) for run in parsed.runs for layer in run.layers]
            if parsed.mode == "LAYERED_DESCRIPTION"
            else [(core, core) for core in parsed.cores]
        )
        affected = {i.source for i in parsed.issues if i.source and i.code in REVIEW_CODES}
        for core in parsed.cores:
            if any(block.source in affected for block in core.source_fields):
                affected.add(core.source)
        for index, (_, entry) in enumerate(entries):
            if (
                entry.source in affected
                and index
                and any(
                    i.code == "layer_overlap" and i.source == entry.source for i in parsed.issues
                )
            ):
                affected.add(entries[index - 1][1].source)
        long_texts = []
        for run, entry in entries:
            for column, style in styles.items():
                sheet.cell(row, column)._style = copy(style)
            values: dict[int, str | int | float | None] = {
                0: parsed.metadata.field_name,
                3: parsed.metadata.well,
                6: os.path.relpath(source.resolve(), output.parent.resolve()),
                9: float(run.drilled) if run.drilled is not None else None,
                10: float(run.recovered) if run.recovered is not None else None,
                20: entry.lithology,
                21: entry.hydrocarbons,
                22: entry.full_text,
            }
            ranges = [(7, run.drilling)]
            if parsed.mode == "LAYERED_DESCRIPTION":
                # The branch is structural: core IDs never become layer numbers.
                from coredescriptionw2e.domain import Layer

                assert isinstance(entry, Layer)
                values.update({18: entry.stratum, 19: entry.number})
                ranges.extend([(13, entry.drilling), (15, entry.gis)])
            for key, interval in ranges:
                if interval:
                    values[key] = float(interval.top)
                    values[key + 1] = float(interval.bottom)
            for key, value in values.items():
                c = sheet.cell(row, mapping[key])
                if isinstance(value, str):
                    set_text(c, value)
                else:
                    c.value = value

            def address(key: int, row: int = row) -> str:
                return f"{get_column_letter(mapping[key])}{row}"

            if run.drilled is not None and run.recovered is not None:
                sheet.cell(row, mapping[11], f'=IFERROR({address(10)}/{address(9)},"")')
            if parsed.mode == "LAYERED_DESCRIPTION" and entry.drilling is not None:
                sheet.cell(row, mapping[12], f"={address(14)}-{address(13)}")
            for key in range(7, 17):
                sheet.cell(row, mapping[key]).number_format = "0.00%" if key == 11 else "0.00"
            sheet.cell(row, mapping[22]).alignment = Alignment(vertical="top", wrap_text=True)
            # Conservative wrapping estimate, including real newlines; Excel's
            # maximum height is 409 points. W is never changed for presentation.
            lines = sum(
                max(1, math.ceil(len(line.expandtabs(4)) / max(1, int(width * 0.85))))
                for line in entry.full_text.split("\n")
            )
            height = max(30, 16 * lines + 8)
            sheet.row_dimensions[row].height = min(409, height)
            if height > 409:
                long_texts.append((row, entry.full_text))
                issues.append(
                    ParseIssue(
                        "excel_height_limit",
                        "Текст W сохранён целиком; для просмотра см. лист «Описание».",
                        source=entry.source,
                    )
                )
            if entry.source in affected:
                for c in sheet[row]:
                    c.fill = PatternFill("solid", fgColor="FFF2CC")
            row += 1
        add_control(workbook, parsed, sheet.title, entries, long_texts, issues)
        sheet.auto_filter.ref = f"A1:W{row - 1}"
        sheet.print_area = f"A1:W{row - 1}"
        sheet.freeze_panes = "A2"
        workbook.calculation = CalcProperties(
            calcId=191029, fullCalcOnLoad=True, forceFullCalc=True
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".core-w2e-", suffix=".xlsx", dir=output.parent)
        os.close(fd)
        temp = Path(name)
        workbook.save(temp)
        with zipfile.ZipFile(temp) as archive:
            if archive.testzip() is not None:
                raise ConversionError("Не прошла проверка записанного XLSX.")
        with temp.open("r+b") as stream:
            os.fsync(stream.fileno())
        publish(temp, output, overwrite)
        return issues
    finally:
        workbook.close()
        if temp is not None:
            temp.unlink(missing_ok=True)


def append_safe(sheet, values) -> None:
    row = sheet.max_row + 1 if sheet.cell(1, 1).value is not None else 1
    for column, value in enumerate(values, 1):
        c = sheet.cell(row, column)
        if isinstance(value, str):
            set_text(c, value)
        else:
            c.value = float(value) if hasattr(value, "as_tuple") else value
        c.alignment = Alignment(vertical="top", wrap_text=True)


def add_control(workbook, parsed, main_title, entries, long_texts, writer_issues) -> None:
    control = workbook.create_sheet("Контроль")
    review = parsed.status == "REVIEW_REQUIRED"
    title = (
        "CORE_INTERVAL / НЕ ПОСЛОЙНО / ТРЕБУЕТ ПРОВЕРКИ"
        if parsed.mode == "CORE_INTERVAL"
        else "LAYERED_DESCRIPTION / ТРЕБУЕТ ПРОВЕРКИ"
        if review
        else "LAYERED_DESCRIPTION / PASS"
    )
    append_safe(control, [title, parsed.status])
    control["A1"].font = Font(bold=True, color="9C0006" if review else "006100", size=16)
    control.row_dimensions[1].height = 50
    append_safe(
        control, ["Визуальная и геологическая приёмка: PENDING. Исходные значения не исправлялись."]
    )
    append_safe(
        control,
        [
            "Тип",
            "ID",
            "Строка",
            "Кровля",
            "Подошва",
            "J",
            "K",
            "Исходный %",
            "Расчётный %",
            "SourceRef",
        ],
    )
    for row, (owner, entry) in enumerate(entries, 2):
        metrics = owner.metrics
        append_safe(
            control,
            [
                parsed.mode,
                f"{owner.number}/{entry.number}"
                if parsed.mode == "LAYERED_DESCRIPTION"
                else str(entry.number),
                row,
                owner.drilling.top if owner.drilling else None,
                owner.drilling.bottom if owner.drilling else None,
                owner.drilled,
                owner.recovered,
                metrics["declared_percent"].value if "declared_percent" in metrics else None,
                metrics["computed_percent"].value if "computed_percent" in metrics else None,
                entry.source.position,
            ],
        )
    for control_row, main_row in enumerate(range(2, len(entries) + 2), 4):
        target = main_title.replace("'", "''")
        control.cell(control_row, 3).hyperlink = f"#'{target}'!A{main_row}"
    for run in parsed.runs:
        if not run.layers:
            append_safe(
                control,
                [
                    "NO_CORE_RUN",
                    str(run.number),
                    None,
                    run.drilling.top if run.drilling else None,
                    run.drilling.bottom if run.drilling else None,
                    run.drilled,
                    run.recovered,
                    None,
                    None,
                    run.source.position,
                ],
            )
    for record in parsed.cores:
        for block in record.source_fields:
            append_safe(
                control, ["SOURCE_FIELD", str(record.number), block.text, block.source.position]
            )
    for key, metric in parsed.totals.items():
        append_safe(
            control,
            [
                "SOURCE_DECLARED_TOTAL",
                key,
                metric.value,
                metric.status,
                metric.confidence,
                metric.source.position,
            ],
        )
    append_safe(control, ["percent_tolerance_pp", parsed.percent_tolerance])
    for interval in parsed.no_core:
        append_safe(
            control,
            [
                "NO_CORE_INTERVAL",
                None,
                None,
                interval.drilling.top,
                interval.drilling.bottom,
                None,
                None,
                None,
                None,
                interval.source.position,
                interval.evidence,
                interval.kind,
            ],
        )
    append_safe(control, ["Метрики", "Вариант", "Значение", "Status", "Confidence", "SourceRef"])
    owners = parsed.runs if parsed.mode == "LAYERED_DESCRIPTION" else parsed.cores
    for owner in owners:
        for metric in owner.metrics.values():
            append_safe(
                control,
                [
                    str(owner.number),
                    metric.variant,
                    metric.value,
                    metric.status,
                    metric.confidence,
                    metric.source.position,
                ],
            )
    ancillary = {ref for ref, kind in parsed.classified if kind == "auxiliary"}
    for block in parsed.document.blocks:
        if block.source in ancillary:
            append_safe(control, ["ANCILLARY_SOURCE", block.source.position, block.text])
    for method, values in parsed.reconciliation.items():
        for key, value in values.items():
            append_safe(control, [method, key, value, "Диагностика; строки не исключены"])
    append_safe(control, ["Код", "Категория", "Severity", "SourceRef", "Диагностика"])
    for issue in [*parsed.issues, *writer_issues]:
        append_safe(
            control,
            [
                issue.code,
                issue.category,
                issue.severity,
                issue.source.position if issue.source else None,
                issue.message,
            ],
        )
    for col in "ABCDEFGHIJK":
        control.column_dimensions[col].width = 25 if col != "J" else 60
    control.freeze_panes = "A4"
    if long_texts:
        detail = workbook.create_sheet("Описание")
        append_safe(detail, ["Строка основного листа", "Фрагмент полного W (в исходном порядке)"])
        detail.column_dimensions["A"].width = 25
        detail.column_dimensions["B"].width = 100
        for main_row, text in long_texts:
            for paragraph in text.split("\n"):
                for piece in textwrap.wrap(
                    paragraph, width=85, replace_whitespace=False, drop_whitespace=False
                ) or [""]:
                    append_safe(detail, [main_row, piece])
                    detail.row_dimensions[detail.max_row].height = 32
        append_safe(
            control,
            [
                "excel_height_limit",
                "Полный W сохранён; лист Описание показывает связанные фрагменты.",
            ],
        )
    if review:
        workbook.active = workbook.index(control)
