"""Plain text reports for the terminal."""

from collections.abc import Sequence
from typing import Optional

from src.testing.results import RunResult


def format_run(result: RunResult) -> str:
    ammeter, timing, plan = result.ammeter, result.timing, result.plan
    pace = f"{plan.frequency_hz:g} Hz" if plan.frequency_hz else "unpaced"
    label = f"  label: {result.metadata['label']}" if result.metadata.get("label") else ""
    # An unpaced run schedules every sample at zero, so both scheduled figures would only be numbers.
    schedule_error = f"max schedule error {timing.max_schedule_error_ms:.2f} ms, " if timing.planned_span_s else ""
    scheduled = f" (scheduled {timing.planned_span_s:.3g} s)" if timing.planned_span_s else ""
    lines = [
        f"Run {result.run_id}  [{_verdict(result)}]",
        f"  ammeter   {ammeter.name} @ {ammeter.host}:{ammeter.port}{label}",
        f"  samples   {len(result.values)}/{len(result.samples)} ok, {pace}, "
        f"{timing.actual_span_s:.3g} s{scheduled}",
        f"  timing    {schedule_error}latency mean {timing.mean_latency_ms:.2f} ms "
        f"/ max {timing.max_latency_ms:.2f} ms",
    ]
    stats = result.statistics
    if stats is None:
        lines.append("  current   no successful samples")
    else:
        lines.append(
            f"  current   mean {stats.mean:.4g} A, median {stats.median:.4g} A, stdev {stats.stdev:.4g} A, "
            f"min {stats.minimum:.4g} A, max {stats.maximum:.4g} A, CV {_num(stats.cv_percent)}%"
        )
    accuracy = result.accuracy
    if accuracy is not None:
        lines.append(
            f"  accuracy  vs {accuracy.reference_a:g} A: bias {accuracy.bias_a:+.4g} A, "
            f"mean abs error {accuracy.mean_abs_error_a:.4g} A ({accuracy.mean_abs_error_percent:.1f}%), "
            f"max abs error {accuracy.max_abs_error_a:.4g} A"
        )
    lines.extend(f"  !! {reason}" for reason in result.verdict.reasons)
    return "\n".join(lines)


def format_listing(results: Sequence[RunResult]) -> str:
    if not results:
        return "no archived runs"
    header = ["run_id", "ammeter", "created", "label", "ok/total", "mean [A]", "CV %", "verdict"]
    rows = [
        [
            r.run_id,
            r.ammeter.name,
            r.created_at[:19].replace("T", " "),
            str(r.metadata.get("label") or "-"),
            f"{len(r.values)}/{len(r.samples)}",
            _num(r.statistics.mean if r.statistics else None),
            _num(r.statistics.cv_percent if r.statistics else None),
            _verdict(r),
        ]
        for r in results
    ]
    return _table(header, rows)


def format_comparison(results: Sequence[RunResult]) -> str:
    """Runs side by side, ranked by precision (CV); accuracy columns appear when every run has a reference."""
    if not results:
        return "nothing to compare"
    with_accuracy = all(r.accuracy is not None for r in results)
    ranked = sorted(results, key=_cv_key)
    header = ["ammeter", "run_id", "mean [A]", "median [A]", "stdev [A]", "CV %", "failed/total", "latency [ms]"]
    if with_accuracy:
        header += ["bias [A]", "abs error %"]
    rows = []
    for r in ranked:
        s = r.statistics
        row = [
            r.ammeter.name,
            r.run_id,
            _num(s.mean if s else None),
            _num(s.median if s else None),
            _num(s.stdev if s else None),
            _num(s.cv_percent if s else None),
            f"{r.failed_count}/{len(r.samples)}",
            f"{r.timing.mean_latency_ms:.2f}",
        ]
        if with_accuracy and r.accuracy is not None:
            row += [f"{r.accuracy.bias_a:+.4g}", _num(r.accuracy.mean_abs_error_percent)]
        rows.append(row)

    lines = [_table(header, rows)]
    best = ranked[0]
    if best.statistics is not None and best.statistics.cv_percent is not None:
        lines.append(f"Most consistent (lowest CV): {best.ammeter.name} at {_num(best.statistics.cv_percent)}%")
    reliable = min(ranked, key=lambda r: (r.failure_rate, _cv_key(r)))
    lines.append(
        f"Most reliable (fewest failed samples): {reliable.ammeter.name} "
        f"at {reliable.failed_count}/{len(reliable.samples)}"
    )
    if with_accuracy:
        accurate = min(results, key=lambda r: r.accuracy.mean_abs_error_percent if r.accuracy else float("inf"))
        if accurate.accuracy is not None:
            lines.append(
                f"Most accurate (lowest mean abs error): {accurate.ammeter.name} "
                f"at {_num(accurate.accuracy.mean_abs_error_percent)}%"
            )
    return "\n".join(lines)


def _cv_key(result: RunResult) -> float:
    stats = result.statistics
    return stats.cv_percent if stats is not None and stats.cv_percent is not None else float("inf")


def _verdict(result: RunResult) -> str:
    return "PASS" if result.verdict.passed else "FAIL"


def _num(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:.4g}"


def _table(header: list[str], rows: list[list[str]]) -> str:
    # zip() would otherwise truncate every line to the shortest row, dropping real columns silently.
    ragged = [row for row in rows if len(row) != len(header)]
    if ragged:
        raise ValueError(f"row has {len(ragged[0])} cells for {len(header)} columns: {ragged[0]}")
    widths = [max(len(str(cell)) for cell in column) for column in zip(header, *rows)]
    lines = [header] + [["-" * w for w in widths]] + rows
    return "\n".join("  ".join(cell.ljust(width) for cell, width in zip(line, widths)).rstrip() for line in lines)
