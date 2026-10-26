"""Offline batch CLI, including JSON status without geological descriptions."""

import argparse
import json
from pathlib import Path

from . import __version__
from .domain import ConversionError
from .service import convert_batch, result_summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Локальное заполнение Excel по DOC/DOCX.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--input-dir", type=Path, required=True, help="Папка Word; без рекурсии")
    parser.add_argument("--template", type=Path, required=True, help="Шаблон core23-v1 XLSX")
    parser.add_argument("--output-dir", type=Path, help="По умолчанию <input-dir>/output")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Явно разрешить атомарную замену существующих результатов",
    )
    parser.add_argument(
        "--json", action="store_true", help="Машиночитаемая сводка без описаний слоёв"
    )
    args = parser.parse_args(argv)
    try:
        results = convert_batch(
            args.input_dir, args.template, args.output_dir, overwrite=args.overwrite
        )
    except (ConversionError, OSError) as exc:
        message = str(exc) if isinstance(exc, ConversionError) else "Недоступна папка или файл."
        print(json.dumps({"error": message}, ensure_ascii=False) if args.json else message)
        return 2
    if args.json:
        print(json.dumps([result_summary(r) for r in results], ensure_ascii=False))
    else:
        for r in results:
            print(
                f"{r.source.name}: {'ГОТОВО' if r.status == 'success' else 'ОШИБКА'}; предупреждений: {sum(i.severity == 'warning' for i in r.issues)}"
            )
            for issue in r.issues:
                print(f"  [{issue.code}] {issue.message}")
        print(
            f"Готово: {sum(r.status == 'success' for r in results)}; ошибок файлов: {sum(r.status == 'error' for r in results)}."
        )
    return 1 if any(r.status == "error" for r in results) else 0
