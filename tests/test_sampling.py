import statistics
import time
from typing import Any, Union

import pytest

from Ammeters.client import AmmeterError
from src.testing.sampling import SamplingPlan, collect_samples


def test_count_and_frequency_derive_interval() -> None:
    plan = SamplingPlan.resolve(count=50, frequency_hz=10)
    assert plan == SamplingPlan(count=50, interval_s=0.1)
    assert plan.duration_s == pytest.approx(5.0)


def test_count_and_duration_derive_interval() -> None:
    assert SamplingPlan.resolve(count=4, duration_s=2.0) == SamplingPlan(count=4, interval_s=0.5)


def test_duration_and_frequency_derive_count() -> None:
    assert SamplingPlan.resolve(duration_s=2.5, frequency_hz=4) == SamplingPlan(count=10, interval_s=0.25)


def test_count_alone_means_unpaced() -> None:
    plan = SamplingPlan.resolve(count=3)
    assert plan.interval_s == 0
    assert plan.frequency_hz is None


def test_consistent_triple_is_accepted_and_inconsistent_rejected() -> None:
    assert SamplingPlan.resolve(count=10, duration_s=1.0, frequency_hz=10).count == 10
    with pytest.raises(ValueError, match="give only two"):
        SamplingPlan.resolve(count=10, duration_s=5.0, frequency_hz=10)
    with pytest.raises(ValueError, match="a run of 1 sample at 10 Hz takes"):  # not "1 samples ... take"
        SamplingPlan.resolve(count=1, duration_s=5.0, frequency_hz=10)


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"duration_s": 1.0},
        {"frequency_hz": 5.0},
        {"count": 0, "frequency_hz": 5.0},
        {"count": 5, "frequency_hz": -1.0},
    ],
)
def test_invalid_plans_are_rejected(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        SamplingPlan.resolve(**kwargs)


def test_samples_follow_the_schedule() -> None:
    plan = SamplingPlan(count=20, interval_s=0.01)
    samples = collect_samples(lambda: 1.0, plan, "greenlee")

    assert [s.index for s in samples] == list(range(20))
    assert all(s.ok and s.value_a == 1.0 for s in samples)
    assert samples[-1].measured_at_s >= 19 * 0.01  # no sample is taken early

    # Median, not max: a shared CI host stalls for tens of milliseconds and one stall says nothing about
    # the schedule. It is also the stricter test of the thing this guards against -- cumulative sleep
    # drift would push the median far past 5 ms, where a single outlier bound would not notice.
    deviations = sorted(abs(s.measured_at_s - s.scheduled_s) for s in samples)
    assert statistics.median(deviations) < 0.01  # one interval; cumulative drift lands 2.5x past it
    assert deviations[-1] < 0.15  # and nothing has run away entirely


def test_unpaced_sampling_does_not_wait() -> None:
    started = time.perf_counter()
    samples = collect_samples(lambda: 1.0, SamplingPlan(count=100, interval_s=0), "greenlee")
    assert len(samples) == 100
    assert time.perf_counter() - started < 0.5


def test_failures_are_recorded_not_raised() -> None:
    outcomes: list[Union[float, AmmeterError]] = [1.0, AmmeterError("boom"), 3.0]

    def measure() -> float:
        outcome = outcomes.pop(0)
        if isinstance(outcome, AmmeterError):
            raise outcome
        return outcome

    samples = collect_samples(measure, SamplingPlan(count=3, interval_s=0), "greenlee")
    assert [s.value_a for s in samples] == [1.0, None, 3.0]
    assert [s.error for s in samples] == [None, "boom", None]


def test_latency_is_measured() -> None:
    def slow_measure() -> float:
        deadline = time.perf_counter() + 0.01  # spin, not sleep: sleep undershoots on Windows before 3.11
        while time.perf_counter() < deadline:
            pass
        return 1.0

    (sample,) = collect_samples(slow_measure, SamplingPlan(count=1, interval_s=0), "greenlee")
    assert sample.latency_ms >= 10
