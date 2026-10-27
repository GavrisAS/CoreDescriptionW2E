"""Versioned 23-column template schema and atomic, formula-safe Excel output."""

import os
import re
import tempfile
import unicodedata
import zipfile
from copy import copy
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.cell.cell import Cell
from openpyxl.styles import Alignment
from openpyxl.utils import get_column_letter
from openpyxl.workbook.properties import CalcProperties

from coredescriptionw2e.domain import ConversionError, ParseIssue, ParseResult
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
    if parsed.has_errors:
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
        row = 2
        for run in parsed.runs:
            for layer in run.layers:
                for column, style in styles.items():
                    sheet.cell(row, column)._style = copy(style)
                values: dict[int, str | int | float | None] = {
                    0: parsed.metadata.field_name,
                    3: parsed.metadata.well,
                    6: os.path.relpath(source.resolve(), output.parent.resolve()),
                    9: float(run.drilled) if run.drilled is not None else None,
                    10: float(run.recovered) if run.recovered is not None else None,
                    18: layer.stratum,
                    19: layer.number,
                    20: layer.lithology,
                    21: layer.hydrocarbons,
                    22: layer.full_text,
                }
                for key, interval in ((7, run.drilling), (13, layer.drilling), (15, layer.gis)):
                    if interval:
                        values[key] = float(interval.top)
                        values[key + 1] = float(interval.bottom)
                for key, value in values.items():
                    cell = sheet.cell(row, mapping[key])
                    if isinstance(value, str):
                        set_text(cell, value)
                    else:
                        cell.value = value

                def address(key: int, row: int = row) -> str:
                    return f"{get_column_letter(mapping[key])}{row}"

                if run.drilled is not None and run.recovered is not None:
                    sheet.cell(row, mapping[11], f'=IFERROR({address(10)}/{address(9)},"")')
                if layer.drilling is not None:
                    sheet.cell(row, mapping[12], f"={address(14)}-{address(13)}")
                for key in range(7, 17):
                    sheet.cell(row, mapping[key]).number_format = "0.00%" if key == 11 else "0.00"
                sheet.cell(row, mapping[22]).alignment = Alignment(vertical="top", wrap_text=True)
                sheet.row_dimensions[row].height = min(
                    120, max(30, 15 * (len(layer.full_text) // 100 + 1))
                )
                row += 1
        sheet.column_dimensions[get_column_letter(mapping[22])].width = max(
            70, min(100, sheet.column_dimensions[get_column_letter(mapping[22])].width or 70)
        )
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
