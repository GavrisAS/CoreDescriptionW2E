"""Single conversion service for CLI and Qt. Failures are isolated per document."""

import unicodedata
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from .domain import ConversionError, ConversionResult, ParseIssue
from .excel.writer import write_excel
from .parser import parse_document
from .word.common import read_word

Progress = Callable[[int, int, ConversionResult], None]


def discover(input_dir: Path, output_dir: Path) -> list[Path]:
    if not input_dir.is_dir():
        raise ConversionError("Папка Word недоступна.")
    return sorted(
        (
            p
            for p in input_dir.iterdir()
            if p.is_file()
            and not p.is_symlink()
            and p.suffix.casefold() in {".doc", ".docx"}
            and not p.name.startswith(("~$", ".", "~"))
            and p.parent.resolve() != output_dir.resolve()
        ),
        key=lambda p: p.name.casefold(),
    )


def name_key(path: Path) -> str:
    return unicodedata.normalize("NFC", path.stem).casefold().rstrip(" .")


def convert_batch(
    input_dir: Path,
    template: Path,
    output_dir: Path | None = None,
    *,
    overwrite: bool = False,
    progress: Progress | None = None,
) -> list[ConversionResult]:
    output_dir = output_dir or input_dir / "output"
    if not template.is_file() or template.suffix.casefold() != ".xlsx":
        raise ConversionError("Выберите доступный Excel-шаблон XLSX.")
    if output_dir.resolve() == input_dir.resolve():
        raise ConversionError("Выходной каталог должен отличаться от папки Word.")
    files = discover(input_dir, output_dir)
    if not files:
        raise ConversionError("В папке нет документов DOC или DOCX.")
    collisions = Counter(name_key(path) for path in files)
    results = []
    for count, path in enumerate(files, 1):
        parsed = None
        issues: list[ParseIssue] = []
        output = output_dir / f"{path.stem}.xlsx"
        try:
            if collisions[name_key(path)] > 1:
                raise ConversionError(
                    "Конфликт имён Word: одинаковый stem с учётом регистра и Unicode."
                )
            if any(c in path.stem for c in '<>:"/\\|?*') or path.stem.endswith((" ", ".")):
                raise ConversionError(
                    "Имя Word несовместимо с безопасным именем Windows-результата."
                )
            if path.stem.split(".")[0].upper() in {
                "CON",
                "PRN",
                "AUX",
                "NUL",
                *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10)),
            }:
                raise ConversionError("Зарезервированное имя Windows.")
            if output.exists() and not overwrite:
                raise ConversionError(
                    "Результат уже существует; используйте отдельную папку или явный --overwrite."
                )
            if output_dir.is_dir() and any(
                item.suffix.casefold() == ".xlsx"
                and name_key(item) == name_key(path)
                and item.name != output.name
                for item in output_dir.iterdir()
            ):
                raise ConversionError(
                    "Результат конфликтует с существующим именем по регистру или Unicode."
                )
            parsed = parse_document(read_word(path))
            issues = list(parsed.issues)
            if parsed.has_errors:
                raise ConversionError("Критические ошибки разбора; результат не опубликован.")
            issues.extend(write_excel(parsed, template, path, output, overwrite=overwrite))
            result = ConversionResult(path, output, "success", issues, parsed)
        except ConversionError as exc:
            issues.append(ParseIssue("conversion_failed", str(exc), "error"))
            result = ConversionResult(path, None, "error", issues, parsed)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # Never include exception text: libraries may quote private source text/paths.
            message = "Файл недоступен, заблокирован или повреждён."
            issues.append(ParseIssue("io_failure", f"{message} ({type(exc).__name__})", "error"))
            result = ConversionResult(path, None, "error", issues, parsed)
        except Exception as exc:
            issues.append(
                ParseIssue(
                    "unexpected_failure",
                    f"Непредвиденная ошибка обработки ({type(exc).__name__}); результат не опубликован.",
                    "error",
                )
            )
            result = ConversionResult(path, None, "error", issues, parsed)
        results.append(result)
        if progress:
            progress(count, len(files), result)
    return results


def result_summary(result: ConversionResult) -> dict[str, object]:
    return {
        "file": result.source.name,
        "status": result.status,
        "runs": len(result.parsed.runs) if result.parsed else 0,
        "layers": len(result.parsed.layers) if result.parsed else 0,
        "issues": [
            {
                "code": i.code,
                "severity": i.severity,
                "message": i.message,
                "position": i.source.position if i.source else None,
            }
            for i in result.issues
        ],
    }
