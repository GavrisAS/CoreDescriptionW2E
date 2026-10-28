from decimal import Decimal
from pathlib import Path

import pytest

from coredescriptionw2e.parser import parse_document
from coredescriptionw2e.smoke import SYNTHETIC
from coredescriptionw2e.word.common import make_document, read_word


def parse_lines(lines):
    return parse_document(
        make_document(
            Path("synthetic.docx"),
            [(str(i), line) for i, line in enumerate(lines)],
            "\n".join(lines),
        )
    )


def test_runs_layers_intervals_and_provenance(sample):
    result = parse_document(read_word(sample[1]))
    assert not result.has_errors
    assert [r.number for r in result.runs] == [1, 2]
    assert [[item.number for item in r.layers] for r in result.runs] == [[1, 3], [1]]
    layer = result.layers[0]
    assert layer.drilling is not None and layer.gis is not None
    assert layer.hydrocarbons is None  # Answer 10: absence is not a positive UV sign.
    assert layer.drilling.top == Decimal("100")
    assert layer.gis.top == Decimal("100.50")
    assert layer.declared_thickness == Decimal("2.00")
    assert layer.full_text == "\n".join(SYNTHETIC[7:9])
    assert layer.raw_text == layer.full_text
    assert "Запах\nнефти отсутствует" in layer.full_text
    assert layer.stratum_source is not None
    assert result.layers[2].stratum == "Доюрский комплекс"
    assert not any(
        "Всего" in item.full_text or "Геолог" in item.full_text for item in result.layers
    )
    assert "layer_number_gap" in [i.code for i in result.issues]
    assert result.layers[1].lithology == "Аргиллит / Песчаник"  # Answer 9.


@pytest.mark.parametrize(
    "header,code",
    [
        ("Слой 1. 102-100м (102-100м)", "invalid_interval"),
        ("Слой 1.", "missing_interval"),
        ("Слой 1. 100-101м", "thickness_mismatch"),
    ],
)
def test_diagnostics_without_correction(header, code):
    result = parse_lines(
        [
            "Скв. TEST",
            "Долбление 1",
            "Инт:100-103м по бурению и каротажу",
            header,
            "2,00м – Песчаник. 2,00м от верха.",
        ]
    )
    assert code in [i.code for i in result.issues]
    assert result.layers[0].header == header
    assert result.layers[0].stratum is None


def test_no_layer_numbers_inferred_from_internal_intervals():
    result = parse_lines(SYNTHETIC)
    assert len(result.layers) == 3
    assert result.layers[0].drilling.top == Decimal(100)


def test_missing_j_not_derived_from_drilling_or_percentage():
    lines = list(SYNTHETIC)
    lines[4] = "вынос керна 3м, 100%"
    result = parse_lines(lines)
    assert result.runs[0].drilled is None
    assert result.runs[0].recovered == Decimal(3)


def test_partial_recovery_is_valid():
    result = parse_lines(
        [
            "Долбление 1",
            "Инт:100-110м по бурению и каротажу",
            "проходка 10м, вынос керна 2м",
            "Слой 1. 100-102м (100-102м)",
            "2м – Песчаник. 2м от верха.",
        ]
    )
    assert not result.has_errors


def test_duplicates_are_errors():
    lines = list(SYNTHETIC)
    lines[9] = lines[9].replace("Слой 3", "Слой 1")
    result = parse_lines(lines)
    assert result.has_errors and "duplicate_layer" in [i.code for i in result.issues]


def test_mixed_inflected_lithology_is_not_reduced_to_one_rock():
    result = parse_lines(
        [
            "Долбление 1",
            "Инт:100-102м по бурению и каротажу",
            "Слой 1. 100-102м (100-102м)",
            "2.00м – Песчаники с прослоями аргиллита. 2м от верха.",
        ]
    )
    assert result.layers[0].lithology is None


def test_gis_overlap_is_critical():
    lines = list(SYNTHETIC)
    lines[9] = "Слой 3. 102-103м (101-102м)"
    result = parse_lines(lines)
    assert result.has_errors and "gis_overlap" in [i.code for i in result.issues]


def test_run_depths_cannot_move_backwards():
    lines = list(SYNTHETIC)
    lines[14] = "Инт:102-105м – по бурению"
    result = parse_lines(lines)
    assert result.has_errors and "run_overlap" in [i.code for i in result.issues]


@pytest.mark.parametrize("label", ["Проход 19м", "проходка 19 м", "Проход\u00a0−\u202f19м"])
def test_explicit_run_progress(label):
    lines = list(SYNTHETIC)
    lines[4] = label + ", вынос керна – 18,48м"
    result = parse_lines(lines)
    assert result.runs[0].drilled == Decimal(19)
    assert result.runs[0].recovered == Decimal("18.48")


@pytest.mark.parametrize("label", ["непроход 19м", "проходками 19м", "Проход без величины"])
def test_progress_requires_whole_label_and_quantity(label):
    lines = list(SYNTHETIC)
    lines[4] = label
    assert parse_lines(lines).runs[0].drilled is None


def test_layer_progress_cannot_fill_run_field():
    lines = list(SYNTHETIC)
    lines[4] = "вынос керна 3м"
    lines[8] += " Проход 19м."
    result = parse_lines(lines)
    assert result.runs[0].drilled is None
    assert "Проход 19м." in result.layers[0].full_text


@pytest.mark.parametrize(
    "line",
    [
        "Интервал 0,10-0,20м внутри описания.",
        "Интервал 2. Пояснение без абсолютных глубин.",
        "Интервал 2. 101-102м (101-102м) внутри описания.",
        "На интервале 101-102м (101-102м) текст продолжается.",
    ],
)
def test_interval_prose_is_not_a_layer(line):
    result = parse_lines(
        [
            "Долбление 1",
            "Инт:100-103м по бурению и каротажу",
            "Слой 1. 100-103м (100-103м)",
            "3м – Песчаник.",
            line,
        ]
    )
    assert len(result.layers) == 1 and line in result.layers[0].full_text


def test_numbered_interval_keeps_overlap_critical():
    result = parse_lines(
        [
            "Долбление 1",
            "Инт:100-103м по бурению и каротажу",
            "Интервал 1. 100-102м (100-102м)",
            "2м – Песчаник.",
            "Интервал 2. 101-103м (102-104м)",
            "2м – Алевролит.",
        ]
    )
    assert result.status == "REVIEW_REQUIRED"
    assert "layer_overlap" in {i.code for i in result.issues}
    assert [layer.number for layer in result.layers] == [1, 2]


def test_numbered_interval_resumes_after_intermediate_summary():
    result = parse_lines(
        [
            "Долбление 1",
            "Инт:100-103м по бурению и каротажу",
            "Слой 1. 100-102м (100-102м)",
            "2м – Песчаник.",
            "Всего по пласту",
            "Пласт Учебный",
            "Интервал 2. 102-103м (102-103м)",
            "1м – Алевролит.",
        ]
    )
    assert not result.has_errors and [layer.number for layer in result.layers] == [1, 2]
    assert [layer.full_text for layer in result.layers] == ["2м – Песчаник.", "1м – Алевролит."]


@pytest.mark.parametrize("metric", ["керна 2м", "Проходка 4м. керна 2м", "непроходка 4м, керна 2м"])
def test_bare_core_length_does_not_supply_recovery(metric):
    lines = list(SYNTHETIC)
    lines[4] = metric
    assert parse_lines(lines).runs[0].recovered is None


def test_paired_metric_in_body_does_not_supply_run_metadata():
    lines = list(SYNTHETIC)
    lines[4] = "Показатели не указаны"
    lines[8] += " Проходка 4м, керна 2м."
    result = parse_lines(lines)
    assert result.runs[0].drilled is None and result.runs[0].recovered is None
    assert "Проходка 4м, керна 2м." in result.layers[0].full_text


@pytest.mark.parametrize(
    "metric",
    ["Проходка 4м, проходка 5м", "Проходка 4м, керна 2м, вынос керна 3м"],
)
def test_conflicting_run_metrics_are_not_silently_selected(metric):
    lines = list(SYNTHETIC)
    lines[4] = metric
    result = parse_lines(lines)
    assert result.has_errors and "run_metric_conflict" in {i.code for i in result.issues}


def test_empty_run_is_critical_even_if_other_run_has_layers():
    result = parse_lines([*SYNTHETIC, "Долбление 3", "Инт:105-106м по бурению и каротажу"])
    assert result.has_errors and "empty_run" in {i.code for i in result.issues}
