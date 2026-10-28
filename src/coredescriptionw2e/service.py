"""Single conversion service for CLI and Qt. Failures are isolated per document."""

import json
import unicodedata
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

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
            and not p.name.startswith("~$")
            and p.parent.resolve() != output_dir.resolve()
        ),
        key=lambda p: p.name.casefold(),
    )


def input_census(input_dir: Path) -> list[tuple[Path, str | None]]:
    """Every top-level Word candidate has an outcome, including excluded locks."""
    if not input_dir.is_dir():
        raise ConversionError("Папка Word недоступна.")
    return [
        (
            p,
            "excluded_symlink"
            if p.is_symlink()
            else "excluded_temporary"
            if p.name.startswith("~$")
            else None,
        )
        for p in sorted(input_dir.iterdir(), key=lambda p: p.name.casefold())
        if (p.is_file() or p.is_symlink()) and p.suffix.casefold() in {".doc", ".docx"}
    ]


def name_key(path: Path) -> str:
    return unicodedata.normalize("NFC", path.stem).casefold().rstrip(" .")


def convert_batch(
    input_dir: Path,
    template: Path,
    output_dir: Path | None = None,
    *,
    overwrite: bool = False,
    progress: Progress | None = None,
    review_dir: Path | None = None,
    percent_tolerance: Decimal = Decimal("0.5"),
) -> list[ConversionResult]:
    output_dir = output_dir or input_dir / "output"
    if not template.is_file() or template.suffix.casefold() != ".xlsx":
        raise ConversionError("Выберите доступный Excel-шаблон XLSX.")
    if output_dir.resolve() == input_dir.resolve():
        raise ConversionError("Выходной каталог должен отличаться от папки Word.")
    census = input_census(input_dir)
    files = [p for p, reason in census if reason is None]
    if not census:
        raise ConversionError("В папке нет документов DOC или DOCX.")
    collisions = Counter(name_key(path) for path in files)
    results = []
    for count, (path, excluded_reason) in enumerate(census, 1):
        if excluded_reason:
            result = ConversionResult(
                path,
                None,
                "skipped",
                [
                    ParseIssue(
                        excluded_reason,
                        "Исключён временный/ссылочный Word-кандидат; исходный файл не открывался.",
                    )
                ],
            )
            results.append(result)
            if progress:
                progress(count, len(census), result)
            continue
        parsed = None
        issues: list[ParseIssue] = []
        output = output_dir / f"{path.stem}.xlsx"
        try:
            if collisions[name_key(path)] > 1:
                raise ConversionError(
                    "Конфликт имён Word: одинаковый stem с учётом регистра и Unicode.",
                    code="output_name_collision",
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
            parsed = parse_document(read_word(path), percent_tolerance=percent_tolerance)
            issues = list(parsed.issues)
            if not parsed.exportable:
                raise ConversionError("Критические ошибки разбора; результат не опубликован.")
            if parsed.status == "REVIEW_REQUIRED":
                output = (review_dir or output_dir / "review") / f"{path.stem}.xlsx"
            if output.parent.resolve() == input_dir.resolve():
                raise ConversionError("Выходной каталог должен отличаться от папки Word.")
            if output.exists() and not overwrite:
                raise ConversionError(
                    "Результат уже существует; используйте отдельную папку или явный --overwrite.",
                    code="target_exists",
                )
            if output.parent.is_dir() and any(
                item.suffix.casefold() == ".xlsx"
                and name_key(item) == name_key(path)
                and item.name != output.name
                for item in output.parent.iterdir()
            ):
                raise ConversionError(
                    "Результат конфликтует с существующим именем по регистру или Unicode."
                )
            issues.extend(write_excel(parsed, template, path, output, overwrite=overwrite))
            result = ConversionResult(
                path,
                output,
                "review_required" if parsed.status == "REVIEW_REQUIRED" else "success",
                issues,
                parsed,
            )
        except ConversionError as exc:
            issues.append(ParseIssue(exc.code, str(exc), "error"))
            status = "error"
            if (parsed is not None and parsed.status == "UNSUPPORTED") or exc.code.startswith(
                "unsupported_doc"
            ):
                status = "unsupported"
            if exc.code == "target_exists":
                status = "skipped"
            result = ConversionResult(
                path,
                None,
                status,
                issues,
                parsed,
            )
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
            progress(count, len(census), result)
    try:
        _write_batch_report(results, census, output_dir)
    except OSError:
        # Never discard per-file outcomes when the final audit cannot be saved.
        for result in results:
            result.issues.append(
                ParseIssue(
                    "batch_report_failed",
                    "Не удалось сохранить приватный отчёт пакета; исходы файлов сохранены в CLI/GUI, повторите в доступный каталог.",
                    "error",
                )
            )
            if result.status in {"success", "review_required"}:
                result.status = "error"

    return results


def _write_batch_report(results, census, output_dir) -> None:
    # A new exclusive report for each run. Private: filenames/paths stay local.
    output_dir.mkdir(parents=True, exist_ok=True)
    report = output_dir / (
        "batch-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8] + ".json"
    )
    with report.open("x", encoding="utf-8") as stream:
        json.dump(
            {
                "summary": batch_summary(results),
                "census": [
                    {"file": p.name, "type": p.suffix.casefold(), "discovery": reason or "queued"}
                    for p, reason in census
                ],
                "results": [result_summary(r) for r in results],
                "private_audit": [asdict(r.parsed) if r.parsed else None for r in results],
            },
            stream,
            ensure_ascii=False,
            indent=2,
            default=str,
        )


def batch_summary(results: list[ConversionResult]) -> dict[str, int]:
    counts = Counter(r.status for r in results)
    summary = {
        "discovered": len(results),
        "queued": sum(not any(i.code.startswith("excluded_") for i in r.issues) for r in results),
        "converted_pass": counts["success"],
        "converted_review": counts["review_required"],
        "unsupported": counts["unsupported"],
        "error": counts["error"],
        "skipped_with_reason": counts["skipped"],
    }
    assert summary["discovered"] == sum(
        summary[k]
        for k in (
            "converted_pass",
            "converted_review",
            "unsupported",
            "error",
            "skipped_with_reason",
        )
    )
    return summary


def result_summary(result: ConversionResult) -> dict[str, object]:
    return {
        "file": result.source.name,
        "status": result.status,
        "document_status": {
            "success": "PASS",
            "review_required": "REVIEW_REQUIRED",
            "unsupported": "UNSUPPORTED",
            "error": "ERROR",
            "skipped": "SKIPPED",
        }[result.status],
        "output": str(result.output) if result.output else None,
        "mode": result.parsed.mode if result.parsed else None,
        "core_records": len(result.parsed.cores) if result.parsed else 0,
        "core_parts": sum(len(c.parts) for c in result.parsed.active_cores) if result.parsed else 0,
        "suppressed_duplicates": len(result.parsed.suppressed_duplicates) if result.parsed else 0,
        "output_schema": "core24-v1" if result.output else None,
        "no_core_intervals": len(result.parsed.no_core) if result.parsed else 0,
        "runs": len(result.parsed.runs) if result.parsed else 0,
        "layers": len(result.parsed.layers) if result.parsed else 0,
        "issues": [
            {
                "code": i.code,
                "severity": i.severity,
                "category": i.category,
                "message": i.message,
                "position": i.source.position if i.source else None,
            }
            for i in result.issues
        ],
    }
