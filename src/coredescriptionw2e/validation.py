"""Diagnostics never repair geological facts. Tolerance: 0.01 m."""

from decimal import Decimal

from .domain import DepthInterval, ParseIssue, ParseResult, SourceRef

TOLERANCE = Decimal("0.01")
PERCENT_TOLERANCE = Decimal("0.5")  # percentage points, exact Decimal comparison
# Export policy is a closed allowlist. Unknown errors always block publication.
BLOCKING_CODES = frozenset(
    {
        "parse_failure",
        "reader_incomplete",
        "invalid_xml",
        "missing_text",
        "ambiguous_boundaries",
        "missing_identifier",
        "empty_body",
    }
)
REVIEW_CODES = frozenset(
    {
        "interval_punctuation",
        "layer_overlap",
        "possible_duplicate_core",
        "duplicate_core_number",
        "core_overlap",
        "percent_mismatch",
        "summary_mismatch",
        "summary_conflict",
        "core_recovery_exceeds_drilled",
        "core_drilled_mismatch",
        "split_ambiguous",
        "stratum_ambiguous",
        "suppressed_duplicate",
        "recovery_exceeds_drilled",
        "summary_scope_incomplete",
    }
)


def validate(result: ParseResult) -> list[ParseIssue]:
    issues: list[ParseIssue] = []

    def issue(code: str, text: str, source: SourceRef | None, error: bool = False) -> None:
        issues.append(ParseIssue(code, text, "error" if error else "warning", source))

    def check(interval: DepthInterval | None, label: str, source: SourceRef) -> None:
        if interval is None:
            issue("missing_interval", f"Отсутствует интервал: {label}.", source)
        elif interval.top < 0 or interval.top >= interval.bottom:
            issue("invalid_interval", f"Некорректный интервал: {label}.", source, True)

    if not result.layers and not any(run.no_core for run in result.runs):
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
        if not run.layers and not run.no_core:
            issue(
                "empty_run",
                "В долблении не найдены слои; неполная книга не создаётся.",
                run.source,
                True,
            )
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
        if (run.drilled is not None and run.drilled <= 0) or (
            run.recovered is not None
            and (run.recovered < 0 or (run.recovered == 0 and not run.no_core))
        ):
            issue(
                "invalid_recovery",
                "Неположительная проходка или неподтверждённый нулевой вынос.",
                run.source,
                True,
            )
        if (
            run.drilled is not None
            and run.recovered is not None
            and run.recovered > run.drilled + TOLERANCE
        ):
            issue("recovery_exceeds_drilled", "Вынос превышает проходку.", run.source, True)
        if "computed_percent" in run.metrics and "declared_percent" in run.metrics:
            if (
                abs(run.metrics["computed_percent"].value - run.metrics["declared_percent"].value)
                > result.percent_tolerance
            ):
                issue(
                    "percent_mismatch",
                    "Исходный процент Word расходится с K/J; в L используется формула.",
                    run.source,
                )
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
            if layer.declared_thickness is not None and layer.declared_thickness < 0:
                issue(
                    "invalid_thickness", "Отрицательная исходная мощность слоя.", layer.source, True
                )
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
            if layer.drilling is None:
                issue(
                    "ambiguous_boundaries",
                    "У слоя нет доказанного бурового интервала.",
                    layer.source,
                    True,
                )
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
                if previous and layer.drilling.top < previous.top:
                    issue(
                        "layer_order",
                        "Обратный порядок слоёв; требуется проверка структуры.",
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
    for run in result.runs:
        for index, layer in enumerate(run.layers):
            for earlier in run.layers[:index]:
                if (
                    layer.drilling
                    and earlier.drilling
                    and max(layer.drilling.top, earlier.drilling.top)
                    < min(layer.drilling.bottom, earlier.drilling.bottom) - TOLERANCE
                ):
                    issues.append(
                        ParseIssue(
                            "layer_overlap",
                            "Перекрывающиеся исходные интервалы: проверьте обе строки; значения сохранены.",
                            "warning",
                            layer.source,
                            (earlier.source,),
                        )
                    )
    for key, attr in (("declared_drilled", "drilled"), ("linear_recovery", "recovered")):
        if key == "linear_recovery" and key in result.totals:
            result.reconciliation_notes[key] = (
                "Итог «поднято керна» не доказывает семантику линейного выноса; возможен учёт коробок. Сопоставимость и объём учёта проверить вручную."
            )
        known = [getattr(run, attr) for run in result.runs]
        if key in result.totals and all(v is not None for v in known):
            total = sum(known, Decimal(0))
            result.reconciliation.setdefault("all_runs", {})[key] = total
            result.reconciliation.setdefault("source_declared", {})[key] = result.totals[key].value
            if abs(total - result.totals[key].value) > TOLERANCE:
                issue(
                    "summary_mismatch",
                    "Сумма исходных метрик долблений расходится с итогом Word; проверьте сопоставимость (коробки/линейный вынос) и объём учёта. Исходные записи имеют приоритет.",
                    result.totals[key].source,
                )
        elif key in result.totals:
            issue(
                "summary_scope_incomplete",
                "Не все метрики долблений известны; сверка соответствующего итога ограничена.",
                result.totals[key].source,
            )
    return issues


def validate_core(result: ParseResult) -> list[ParseIssue]:
    from .core_parser import sums

    issues = []
    seen = {}
    for index, record in enumerate(result.active_cores):

        def issue(
            code: str, message: str, error: bool = False, source: SourceRef = record.source
        ) -> None:
            issues.append(ParseIssue(code, message, "error" if error else "warning", source))

        if record.drilling.top < 0 or record.drilling.top >= record.drilling.bottom:
            issue("invalid_interval", "Некорректный интервал отбора.", True)
        if record.drilled <= 0 or record.recovered <= 0:
            issue("invalid_recovery", "Неположительные метрики продуктивной записи.", True)
        if record.number in seen:
            issue("duplicate_core_number", "Повтор номера керна; все записи сохранены.")
        seen[record.number] = index
        if abs(record.drilling.thickness - record.drilled) > TOLERANCE:
            issue("core_drilled_mismatch", "Проходка расходится с интервалом отбора.")
        if record.recovered > record.drilled:
            issue("core_recovery_exceeds_drilled", "Вынос превышает проходку.")
        if (
            record.drilled > 0
            and record.declared_percent is not None
            and (
                abs(record.recovered / record.drilled * 100 - record.declared_percent)
                > result.percent_tolerance
                or record.declared_percent < 0
                or record.declared_percent > 100
            )
        ):
            issue(
                "percent_mismatch",
                "Исходный процент расходится с расчётным; исходные значения сохранены.",
            )
        for earlier in result.active_cores[:index]:
            if (
                max(record.drilling.top, earlier.drilling.top)
                < min(record.drilling.bottom, earlier.drilling.bottom) - TOLERANCE
            ):
                issues.append(
                    ParseIssue(
                        "core_overlap",
                        "Перекрытие интервалов отбора; проверьте обе исходные записи.",
                        source=record.source,
                        related_sources=(earlier.source,),
                    )
                )
            if (
                earlier.drilling == record.drilling
                and earlier.drilled == record.drilled
                and earlier.recovered == record.recovered
            ):
                issue(
                    "possible_duplicate_core",
                    "Совпали интервал и метрики, но полный дубль не доказан: запись сохранена.",
                )
                issues.append(
                    ParseIssue(
                        "possible_duplicate_core",
                        "Вторая исходная запись кандидата на дубль.",
                        source=earlier.source,
                    )
                )
                break
    for interval in result.no_core:
        if interval.drilled is not None and interval.drilled <= 0:
            issues.append(
                ParseIssue(
                    "invalid_recovery",
                    "Неположительная объявленная проходка нулевого интервала.",
                    "error",
                    interval.source,
                )
            )
        if interval.drilling.top < 0 or interval.drilling.top >= interval.drilling.bottom:
            issues.append(
                ParseIssue(
                    "invalid_interval", "Некорректный интервал без керна.", "error", interval.source
                )
            )
    result.reconciliation["all_records"] = sums(result.cores)
    result.reconciliation["exported_unique"] = sums(result.active_cores)
    zero_intervals = [r for r in result.no_core if r.kind == "NO_RECOVERY"]
    known_zero_j = sum((r.drilled for r in zero_intervals if r.drilled is not None), Decimal(0))
    result.reconciliation["exported_unique"]["drilled"] += known_zero_j
    result.reconciliation["zero_recovery_intervals"] = {
        "known_declared_drilled": known_zero_j,
        "unknown_drilled_count": Decimal(sum(r.drilled is None for r in zero_intervals)),
        "sampling_interval_span_not_J": sum(
            (r.drilling.thickness for r in zero_intervals), Decimal(0)
        ),
    }
    result.reconciliation["source_declared"] = {k: m.value for k, m in result.totals.items()}
    for key, sum_key in (("declared_drilled", "drilled"), ("linear_recovery", "recovered")):
        if key == "declared_drilled" and any(r.drilled is None for r in zero_intervals):
            issues.append(
                ParseIssue(
                    "summary_scope_incomplete",
                    "Проходка нулевого интервала не объявлена; сумма J неполная. Длина интервала показана отдельно, без подстановки в J.",
                )
            )
        if (
            key in result.totals
            and abs(result.totals[key].value - result.reconciliation["exported_unique"][sum_key])
            > TOLERANCE
        ):
            issues.append(
                ParseIssue(
                    "summary_mismatch",
                    "Сумма уникальных исходных записей расходится с итогом Word; исходные записи имеют приоритет. Проверьте объём и семантику итогов.",
                    source=result.totals[key].source,
                )
            )
    return issues
