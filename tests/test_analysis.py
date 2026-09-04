from typing import Optional

import pytest

from src.testing.analysis import AccuracyStats, Statistics, TimingStats, evaluate
from src.testing.sampling import Sample, SamplingPlan
from src.utils.config import AmmeterSpec

SPEC = AmmeterSpec("test", "localhost", 1, "CMD", expected_min_a=0.0, expected_max_a=10.0)


def sample(index: int, value: Optional[float] = 1.0, error: Optional[str] = None, late_ms: float = 0.0) -> Sample:
    return Sample(index, index * 0.1, index * 0.1 + late_ms / 1000, latency_ms=1.0, value_a=value, error=error)


def test_statistics_of_known_values() -> None:
    stats = Statistics.from_values([1.0, 2.0, 3.0, 4.0])
    assert stats.count == 4
    assert stats.mean == 2.5
    assert stats.median == 2.5
    assert stats.stdev == pytest.approx(1.2910, abs=1e-4)
    assert (stats.minimum, stats.maximum) == (1.0, 4.0)
    assert stats.cv_percent == pytest.approx(51.64, abs=0.01)


def test_single_value_has_zero_stdev() -> None:
    assert Statistics.from_values([2.5]).stdev == 0.0


def test_zero_mean_has_no_cv() -> None:
    assert Statistics.from_values([-1.0, 1.0]).cv_percent is None


def test_no_values_is_an_error() -> None:
    with pytest.raises(ValueError):
        Statistics.from_values([])


def test_timing_stats() -> None:
    samples = [sample(0), sample(1, late_ms=0.5), sample(2, late_ms=2.0)]
    timing = TimingStats.from_samples(samples, SamplingPlan(count=3, interval_s=0.1))
    assert timing.planned_duration_s == pytest.approx(0.3)
    assert timing.actual_duration_s == pytest.approx(0.2 + 0.002 + 0.001)
    assert timing.max_timing_error_ms == pytest.approx(2.0)
    assert timing.mean_latency_ms == timing.max_latency_ms == 1.0


def test_accuracy_against_reference() -> None:
    accuracy = AccuracyStats.from_values([9.0, 11.0, 12.0], reference_a=10.0)
    assert accuracy.bias_a == pytest.approx(0.6667, abs=1e-4)
    assert accuracy.mean_abs_error_a == pytest.approx(1.3333, abs=1e-4)
    assert accuracy.max_abs_error_a == 2.0
    assert accuracy.mean_abs_error_percent == pytest.approx(13.333, abs=1e-3)
    with pytest.raises(ValueError):
        AccuracyStats.from_values([1.0], reference_a=0.0)


def test_verdict_passes_within_limits() -> None:
    samples = [sample(0), sample(1), sample(2, value=None, error="x")]
    verdict = evaluate(samples, Statistics.from_values([1.0, 1.0]), SPEC, max_failure_rate=0.34)
    assert verdict.passed and verdict.reasons == []


def test_verdict_fails_on_too_many_failures() -> None:
    samples = [sample(0), sample(1, value=None, error="x")]
    verdict = evaluate(samples, Statistics.from_values([1.0]), SPEC, max_failure_rate=0.0)
    assert not verdict.passed
    assert verdict.reasons == ["1 of 2 samples failed (50%, limit 0%)"]


def test_verdict_fails_out_of_range_readings() -> None:
    verdict = evaluate([sample(0, 12.0), sample(1, -1.0)], Statistics.from_values([12.0, -1.0]), SPEC, 0.0)
    assert not verdict.passed
    assert len(verdict.reasons) == 2
    assert "below" in verdict.reasons[0] and "above" in verdict.reasons[1]


def test_verdict_when_every_sample_failed() -> None:
    verdict = evaluate([sample(0, None, "x")], None, SPEC, max_failure_rate=0.05)
    assert not verdict.passed
