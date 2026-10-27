"""Private local audit; reports belong in the ignored data directory, never CI."""

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from coredescriptionw2e.excel.writer import schema
from coredescriptionw2e.parser import parse_document
from coredescriptionw2e.word.common import read_word


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--word", type=Path, required=True)
    parser.add_argument("--xlsx", type=Path, required=True)
    parser.add_argument("--expected", type=Path)
    parser.add_argument("--reference-docx", type=Path)
    parser.add_argument("--template", type=Path)
    args = parser.parse_args()
    parsed = parse_document(read_word(args.word))
    workbook = load_workbook(args.xlsx, data_only=False)
    sheet = workbook.active
    assert sheet is not None
    mapping = schema(sheet)
    descriptions = [sheet.cell(i + 2, mapping[22]).value for i in range(len(parsed.layers))]
    summary = {
        "runs": len(parsed.runs),
        "layers": len(parsed.layers),
        "distribution": [len(run.layers) for run in parsed.runs],
        "run_details": [
            {
                "number": run.number,
                "interval": [str(run.drilling.top), str(run.drilling.bottom)]
                if run.drilling
                else None,
                "drilled": str(run.drilled) if run.drilled is not None else None,
                "recovered": str(run.recovered) if run.recovered is not None else None,
                "layer_numbers": [layer.number for layer in run.layers],
                "layers": [
                    {
                        "number": layer.number,
                        "interval": [str(layer.drilling.top), str(layer.drilling.bottom)]
                        if layer.drilling
                        else None,
                        "gis": [str(layer.gis.top), str(layer.gis.bottom)] if layer.gis else None,
                        "body_blocks": len(layer.body_blocks),
                        "body_chars": len(layer.full_text),
                    }
                    for layer in run.layers
                ],
            }
            for run in parsed.runs
        ],
        "issues": dict(Counter(issue.code for issue in parsed.issues)),
        "errors": sum(i.severity == "error" for i in parsed.issues),
        "undefined_lithology": sum(layer.lithology is None for layer in parsed.layers),
        "source_end_markers": sum(
            bool(re.search(r"от\s+верха", layer.full_text, re.I)) for layer in parsed.layers
        ),
    }
    checks = {
        "rows": sheet.max_row == len(parsed.layers) + 1,
        "columns": sheet.max_column == 23,
        "w_complete": descriptions == [layer.full_text for layer in parsed.layers],
        "numbers": [sheet.cell(i + 2, mapping[19]).value for i in range(len(parsed.layers))]
        == [layer.number for layer in parsed.layers],
        "no_summaries_in_w": all(
            not re.search(r"(?im)^Всего\b|^Геолог\b", str(text)) for text in descriptions
        ),
        "m_formulas": all(
            sheet.cell(i + 2, mapping[12]).data_type == "f"
            for i in range(len(parsed.layers))
            if parsed.layers[i].drilling is not None
        ),
    }
    depths_ok = True
    for index, layer in enumerate(parsed.layers):
        for key, interval in ((13, layer.drilling), (15, layer.gis)):
            if interval:
                depths_ok &= sheet.cell(index + 2, mapping[key]).value == float(interval.top)
                depths_ok &= sheet.cell(index + 2, mapping[key + 1]).value == float(interval.bottom)
    checks["depth_values"] = depths_ok
    checks["unknown_metadata_blank"] = all(
        sheet.cell(row, mapping[key]).value is None
        for row in range(2, sheet.max_row + 1)
        for key in (1, 2, 4, 5, 17)
    )
    checks["source_reference"] = all(
        sheet.cell(row, mapping[6]).value
        == os.path.relpath(args.word.resolve(), args.xlsx.parent.resolve())
        for row in range(2, sheet.max_row + 1)
    )
    first_row = 2
    recovery_ok = True
    for run in parsed.runs:
        for offset, _ in enumerate(run.layers):
            row = first_row + offset
            j = sheet.cell(row, mapping[9]).value
            k = sheet.cell(row, mapping[10]).value
            percentage = sheet.cell(row, mapping[11]).value
            recovery_ok &= j == (float(run.drilled) if run.drilled is not None else None)
            recovery_ok &= k == (float(run.recovered) if run.recovered is not None else None)
            cell = sheet.cell(row, mapping[11])
            if run.drilled is not None and run.recovered is not None:
                drilled_col = get_column_letter(mapping[9])
                recovered_col = get_column_letter(mapping[10])
                recovery_ok &= percentage == f'=IFERROR({recovered_col}{row}/{drilled_col}{row},"")'
                recovery_ok &= cell.data_type == "f"
            else:
                recovery_ok &= percentage is None
            recovery_ok &= cell.number_format == "0.00%"
        first_row += len(run.layers)
    checks["recovery_all_run_rows"] = recovery_ok
    if args.template:
        template = load_workbook(args.template)
        original_sheet = template.active
        assert original_sheet is not None
        checks["header_preserved"] = [c.value for c in sheet[1]] == [
            c.value for c in original_sheet[1]
        ]
        examples = {str(original_sheet.cell(r, mapping[3]).value) for r in (2, 3)}
        if parsed.metadata.well and examples != {parsed.metadata.well}:
            summary["issues"]["template_well_conflict"] = 1
        checks["word_well_preserved"] = all(
            sheet.cell(r, mapping[3]).value == parsed.metadata.well
            for r in range(2, sheet.max_row + 1)
        )
        template.close()
    if args.reference_docx:
        reference = read_word(args.reference_docx)
        original = re.sub(r"\s+", "", "\n".join(b.text for b in parsed.document.blocks))
        independent = re.sub(r"\s+", "", "\n".join(b.text for b in reference.blocks))
        checks["independent_text_match"] = original == independent
    if args.expected:
        expected = json.loads(args.expected.read_text(encoding="utf-8"))
        checks["expected_structure"] = all(summary[key] == value for key, value in expected.items())
    workbook.close()
    summary["checks"] = checks
    summary["status"] = "PASS" if all(checks.values()) and summary["errors"] == 0 else "FAIL"
    report = args.xlsx.parent / "validation-summary.json"
    report.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": summary["status"],
                "runs": summary["runs"],
                "layers": summary["layers"],
                "checks": checks,
            },
            ensure_ascii=False,
        )
    )
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
