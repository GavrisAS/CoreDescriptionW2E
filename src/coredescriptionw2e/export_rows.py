"""One ordered export plan shared by writer, control and private validation."""

from .domain import CoreInterval, CorePart, CoringRun, Layer, NoCoreInterval, ParseResult

Owner = CoringRun | CoreInterval | NoCoreInterval
Entry = Layer | CorePart | None


def export_rows(parsed: ParseResult) -> list[tuple[Owner, Entry]]:
    entries: list[tuple[Owner, Entry]] = []
    for run in parsed.runs:
        entries.extend((run, layer) for layer in run.layers)
        if run.no_core and not run.layers:
            entries.append((run, None))
    for core in parsed.active_cores:
        entries.extend((core, part) for part in core.parts)
    entries.extend((item, None) for item in parsed.no_core if item.kind == "NO_RECOVERY")
    return sorted(
        entries,
        key=lambda pair: (
            pair[0].source.order,
            pair[1].ordinal if isinstance(pair[1], CorePart) else 0,
        ),
    )
