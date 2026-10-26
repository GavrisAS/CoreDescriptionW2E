"""Diagnostics never repair geological facts. Tolerance: 0.01 m."""

from decimal import Decimal

from .domain import DepthInterval, ParseIssue, ParseResult, SourceRef

TOLERANCE = Decimal("0.01")


def validate(result: ParseResult) -> list[ParseIssue]:
    issues: list[ParseIssue] = []

    def issue(code: str, text: str, source: SourceRef | None, error: bool = False) -> None:
        issues.append(ParseIssue(code, text, "error" if error else "warning", source))

    def check(interval: DepthInterval | None, label: str, source: SourceRef) -> None:
        if interval is None:
            issue("missing_interval", f"Отсутствует интервал: {label}.", source)
        elif interval.top < 0 or interval.top >= interval.bottom:
            issue("invalid_interval", f"Некорректный интервал: {label}.", source, True)

    if not result.layers:
        issue("no_layers", "Слои не найдены.", None, True)
    if not result.metadata.well:
        issue("missing_well", "Номер скважины отсутствует; поле оставлено пустым.", None)
    if not result.metadata.field_name:
        issue("missing_field", "Месторождение не определено по явному полю Word.", None)
    issue(
        "unconfirmed_metadata",
        "Площадь, код площади, LAS, UWI и стратиграфия ГИС не подтверждены; поля оставлены пустыми.",
        None,
    )
    seen_runs: set[int] = set()
    previous_run: DepthInterval | None = None
    for run in result.runs:
        if run.number in seen_runs:
            issue("duplicate_run", "Повторяется номер долбления.", run.source, True)
        seen_runs.add(run.number)
        check(run.drilling, "долбление/бурение", run.source)
        check(run.gis, "долбление/ГИС", run.source)
        if run.drilling:
            if previous_run and run.drilling.top < previous_run.bottom - TOLERANCE:
                issue(
                    "run_overlap",
                    "Интервалы долблений перекрываются или идут назад.",
                    run.source,
                    True,
                )
            previous_run = run.drilling
        if run.drilled is None or run.recovered is None:
            issue(
                "missing_recovery",
                "Проходка или линейный вынос не распознаны; процент не вычисляется.",
                run.source,
            )
        for value in (run.drilled, run.recovered):
            if value is not None and value <= 0:
                issue("invalid_recovery", "Проходка или вынос неположительны.", run.source, True)
        if (
            run.drilled is not None
            and run.recovered is not None
            and run.recovered > run.drilled + TOLERANCE
        ):
            issue("recovery_exceeds_drilled", "Вынос превышает проходку.", run.source, True)
        if (
            run.drilled is not None
            and run.drilling
            and abs(run.drilled - run.drilling.thickness) > TOLERANCE
        ):
            issue(
                "run_thickness_mismatch", "Проходка расходится с интервалом долбления.", run.source
            )
        seen: set[int] = set()
        previous = None
        previous_gis: DepthInterval | None = None
        previous_number = 0
        for layer in run.layers:
            if layer.number in seen:
                issue(
                    "duplicate_layer",
                    "Повторяется номер слоя внутри долбления.",
                    layer.source,
                    True,
                )
            elif layer.number != previous_number + 1:
                issue(
                    "layer_number_gap",
                    "Нарушена последовательность номеров слоёв; исходные номера сохранены.",
                    layer.source,
                )
            seen.add(layer.number)
            previous_number = layer.number
            check(layer.drilling, "слой/бурение", layer.source)
            check(layer.gis, "слой/ГИС", layer.source)
            if layer.gis:
                if previous_gis and layer.gis.top < previous_gis.bottom - TOLERANCE:
                    issue(
                        "gis_overlap",
                        "Интервалы слоёв по ГИС перекрываются или идут назад.",
                        layer.source,
                        True,
                    )
                previous_gis = layer.gis
            if not layer.stratum:
                issue(
                    "missing_stratum", "Нет предшествующего явного заголовка пласта.", layer.source
                )
            if layer.drilling:
                if previous and layer.drilling.top < previous.bottom - TOLERANCE:
                    issue(
                        "layer_overlap",
                        "Интервалы слоёв перекрываются или идут назад.",
                        layer.source,
                        True,
                    )
                if run.drilling and (
                    layer.drilling.top < run.drilling.top - TOLERANCE
                    or layer.drilling.bottom > run.drilling.bottom + TOLERANCE
                ):
                    issue(
                        "layer_outside_run",
                        "Слой выходит за интервал долбления.",
                        layer.source,
                        True,
                    )
                if (
                    layer.declared_thickness is not None
                    and abs(layer.drilling.thickness - layer.declared_thickness) > TOLERANCE
                ):
                    issue(
                        "thickness_mismatch",
                        "Мощность описания расходится с интервалом слоя.",
                        layer.source,
                    )
                previous = layer.drilling
        # Recovery can be less than drilling; no false coverage equality gate.
    return issues
