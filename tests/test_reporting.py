from datetime import datetime
from typing import Optional

from src.testing.reporting import format_comparison, format_listing, format_run
from src.testing.results import RunResult
from src.testing.sampling import Sample, SamplingPlan
from src.utils.config import AmmeterSpec


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
    assert "samples   3/3 ok, 10 Hz, 0.20 s (scheduled 0.20 s)" in report
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
