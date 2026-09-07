import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytest

from src.testing.reporting import _table, format_comparison, format_listing, format_run
from src.testing.results import RunResult
from src.testing.sampling import Sample, SamplingPlan
from src.testing.visualization import cv_bars, plot_comparison, plot_run
from src.utils.config import AmmeterSpec
from src.utils.text import plural


def make_result(
    name: str, values: list[Optional[float]], label: Optional[str] = None, reference_a: Optional[float] = None
) -> RunResult:
    spec = AmmeterSpec(name, "localhost", 5000, "CMD", expected_min_a=0.0, expected_max_a=10.0)
    samples = [Sample(i, i * 0.1, i * 0.1, 1.0, v, None if v is not None else "boom") for i, v in enumerate(values)]
    plan = SamplingPlan(count=len(values), interval_s=0.1)
    return RunResult.from_samples(
        spec, plan, samples, datetime(2026, 1, 2, 3, 4, 5), {"label": label}, 0.0, reference_a
    )


def test_run_report_lists_everything() -> None:
    report = format_run(make_result("entes", [9.0, 11.0, 12.0], label="ref", reference_a=10.0))
    assert report.startswith("Run 20260102_030405_entes_")
    assert "[FAIL]" in report
    assert "label: ref" in report
    assert "samples   3/3 ok, 10 Hz, 0.201 s (scheduled 0.2 s)" in report
    assert "current   mean 10.67 A, median 11 A, stdev 1.528 A, min 9 A, max 12 A, CV 14.32%" in report
    assert "accuracy  vs 10 A: bias +0.6667 A, mean abs error 1.333 A (13.3%), max abs error 2 A" in report
    assert "!! maximum 12 A is above the expected 10 A" in report


def test_run_report_without_successful_samples() -> None:
    report = format_run(make_result("greenlee", [None, None]))
    assert "samples   0/2 ok" in report
    assert "current   no successful samples" in report
    assert "!! 2 of 2 samples failed (100%, limit 0%)" in report


def test_listing() -> None:
    assert format_listing([]) == "no archived runs"
    listing = format_listing([make_result("greenlee", [1.0, 3.0], label="base"), make_result("entes", [None])])
    lines = listing.splitlines()
    assert lines[0].split() == [
        "run_id",
        "ammeter",
        "created",
        "label",
        "ok/total",
        "mean",
        "[A]",
        "CV",
        "%",
        "verdict",
    ]
    assert lines[2].split()[1:] == ["greenlee", "2026-01-02", "03:04:05", "base", "2/2", "2", "70.71", "PASS"]
    assert lines[3].split()[1:] == ["entes", "2026-01-02", "03:04:05", "-", "0/1", "-", "-", "FAIL"]


def test_comparison_ranks_by_precision_and_accuracy() -> None:
    steady = make_result("circutor", [1.0, 1.1, 0.9], reference_a=1.0)
    noisy = make_result("greenlee", [1.0, 5.0, None], reference_a=1.0)
    comparison = format_comparison([noisy, steady])
    lines = comparison.splitlines()
    assert lines[0].split() == [
        "ammeter", "run_id", "mean", "[A]", "median", "[A]", "stdev", "[A]", "CV", "%",
        "failed/total", "latency", "[ms]", "bias", "[A]", "abs", "error", "%",
    ]  # fmt: skip
    assert lines[2].startswith("circutor") and lines[3].startswith("greenlee")
    assert "1/3" in lines[3]
    assert "Most consistent (lowest CV): circutor at 10%" in comparison
    assert "Most accurate (lowest mean abs error): circutor at 6.667%" in comparison
    assert format_comparison([]) == "nothing to compare"


def test_generated_output_pluralises() -> None:
    assert (plural(1, "run"), plural(0, "run"), plural(2, "sample")) == ("run", "runs", "samples")


def test_several_runs_of_one_ammeter_are_several_bars() -> None:
    """Keying the CV chart by ammeter name collapsed them into one bar beside a table listing them all."""
    runs = [
        make_result("greenlee", [1.0, 3.0]),
        make_result("greenlee", [2.0, 9.0]),
        make_result("entes", [5.0, 5.0]),  # stdev 0, so CV is exactly 0.0
    ]
    bars = cv_bars(runs)
    assert [name for name, _ in bars] == ["greenlee", "greenlee", "entes"]
    assert bars[-1][1] == 0.0  # a CV of exactly zero is a bar, not a falsy value to drop


def test_a_single_sample_report_omits_the_empty_scheduled_span() -> None:
    """One paced sample is scheduled at zero, so "(scheduled 0 s)" is a number rather than information."""
    result = make_result("greenlee", [1.0])
    report = format_run(result)
    assert "(scheduled" not in report and "max schedule error" not in report


def test_plots_are_skipped_when_matplotlib_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The README calls matplotlib optional, so its absence must be a skipped plot, not a crash."""
    monkeypatch.setitem(sys.modules, "matplotlib", None)  # makes `import matplotlib` raise ImportError
    result = make_result("greenlee", [1.0, 3.0])
    assert plot_run(result, tmp_path / "x.png") is None
    assert plot_comparison([result], tmp_path / "y.png") is None


def test_accuracy_columns_need_every_run_to_have_a_reference() -> None:
    """Only some runs having a reference must drop the columns for everyone, not build a ragged table:
    the widths come from zip(), which would otherwise truncate every line to the shortest row."""
    with_ref = make_result("greenlee", [1.0, 3.0], reference_a=2.0)
    without = make_result("entes", [1.0, 3.0])
    assert "bias [A]" not in format_comparison([with_ref, without])
    assert "bias [A]" in format_comparison([with_ref])


def test_a_ragged_table_is_an_error_rather_than_silent_truncation() -> None:
    with pytest.raises(ValueError, match="2 cells for 3 columns"):
        _table(["a", "b", "c"], [["1", "2", "3"], ["4", "5"]])


def test_comparison_names_the_most_reliable_ammeter() -> None:
    """The specification asks to identify the most reliable method; CV and accuracy do not measure that."""
    flaky = make_result("greenlee", [1.0, None, None])
    solid = make_result("entes", [5.0, None, 7.0])
    assert "Most reliable (lowest failure rate): entes at 33% failed (1/3)" in format_comparison([flaky, solid])


def test_reliability_ranks_by_rate_not_by_count() -> None:
    """3 failures in 100 is a better device than 2 in 10, so the count alone would name the wrong one."""
    few_samples = make_result("entes", [1.0] * 8 + [None, None])
    many_samples = make_result("greenlee", [1.0] * 97 + [None, None, None])
    assert "greenlee at 3% failed (3/100)" in format_comparison([few_samples, many_samples])


def test_reliability_reports_a_tie_as_a_tie() -> None:
    """Every clean run is 0 failed, so a single winner there would be decided by the sort, not the data."""
    runs = [make_result(name, [1.0, 2.0]) for name in ("greenlee", "entes", "circutor")]
    assert "Most reliable (lowest failure rate): 3 runs tied at 0% failed" in format_comparison(runs)


def test_reliability_is_not_claimed_when_every_run_failed_everything() -> None:
    runs = [make_result("greenlee", [None, None]), make_result("entes", [None, None])]
    assert "Most reliable" not in format_comparison(runs)


def test_a_run_with_no_statistics_ranks_last_not_first() -> None:
    """cv_percent is None when every sample failed; sorting it as 0 would call it the most consistent."""
    failed = make_result("greenlee", [None, None])
    measured = make_result("entes", [1.0, 2.0])
    table = format_comparison([failed, measured])
    assert table.index("entes") < table.index("greenlee")
    assert "Most consistent (lowest CV): entes" in table
