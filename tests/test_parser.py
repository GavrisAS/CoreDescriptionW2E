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
    assert layer.hydrocarbons is not None
    assert layer.drilling.top == Decimal("100")
    assert layer.gis.top == Decimal("100.50")
    assert layer.declared_thickness == Decimal("2.00")
    assert layer.full_text == "\n".join(SYNTHETIC[7:9])
    assert layer.raw_text == layer.full_text
    assert "Запах нефти отсутствует" in layer.hydrocarbons
    assert layer.stratum_source is not None
    assert result.layers[2].stratum == "Доюрский комплекс"
    assert not any(
        "Всего" in item.full_text or "Геолог" in item.full_text for item in result.layers
    )
    assert "layer_number_gap" in [i.code for i in result.issues]
    assert result.layers[1].lithology is None


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
