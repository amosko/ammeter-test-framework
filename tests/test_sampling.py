import statistics
import threading
import time
from typing import Any, Union

import pytest

from Ammeters.client import AmmeterError
from src.testing.analysis import TimingStats, evaluate
from src.testing.sampling import SamplingPlan, collect_samples
from src.utils.config import AmmeterSpec

SPEC = AmmeterSpec("greenlee", "127.0.0.1", 5000, "CMD")


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


def test_a_zero_value_is_rejected_rather_than_silently_clamped() -> None:
    """Without the positivity guard, duration 0 resolves to a one-sample run instead of an error."""
    with pytest.raises(ValueError, match="duration must be positive"):
        SamplingPlan.resolve(duration_s=0.0, frequency_hz=20)
    with pytest.raises(ValueError, match="count must be positive"):
        SamplingPlan.resolve(count=0, frequency_hz=20)


def test_samples_follow_the_schedule() -> None:
    plan = SamplingPlan(count=20, interval_s=0.01)
    samples = collect_samples(lambda: 1.0, plan, "greenlee")

    assert [s.index for s in samples] == list(range(20))
    assert all(s.ok and s.value_a == 1.0 for s in samples)
    assert samples[-1].measured_at_s >= 19 * plan.interval_s  # no sample is taken early

    # The design's claim is that overshoot cannot accumulate, not that a shared host is punctual. A
    # stalling runner shifts every sample alike and passes; cumulative sleeping makes each sample later
    # than the last, so the tail falls a whole interval or more behind the head.
    deviations = [s.measured_at_s - s.scheduled_s for s in samples]

    # Minimum, not median: one stalled sample raises only the samples after it, so the tail minimum stays
    # put, while cumulative drift raises every sample in the tail. A median tail fails on a single 120 ms
    # stall, which a shared runner produces -- DESIGN.md records 51 ms on one.
    head, tail = min(deviations[:10]), min(deviations[10:])
    assert tail < head + plan.interval_s

    # The check above is invariant to a uniform shift by construction, which is what makes it
    # host-independent and also what makes it blind: without this, a sampler late on every sample passes.
    # Five intervals only bounds gross lateness -- it tolerates up to 49 ms, which is five times the
    # shipped criterion. The criterion itself is pinned deterministically below and in test_analysis.py.
    assert statistics.median(deviations) < 5 * plan.interval_s


def test_unpaced_sampling_does_not_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Assert it never sleeps, rather than that it finished quickly: a wall-clock ceiling only says the
    host was not busy at the time."""
    slept: list[float] = []  # recorded, not failed on: this patches the process-wide time.sleep, and
    monkeypatch.setattr("src.testing.sampling.time.sleep", slept.append)  # pytest.fail in a thread kills it
    samples = collect_samples(lambda: 1.0, SamplingPlan(count=100, interval_s=0), "greenlee")
    assert len(samples) == 100 and slept == []


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


def test_the_cancellation_message_pluralises() -> None:
    """Cancel after exactly one sample, which is the only count that can read "1 samples"."""
    cancelled = threading.Event()

    def measure() -> float:
        cancelled.set()  # checked at the top of the next iteration
        return 1.0

    with pytest.raises(KeyboardInterrupt, match=r"cancelled after 1 sample$"):
        collect_samples(measure, SamplingPlan(count=5, interval_s=0), "greenlee", cancelled)


def test_the_sampler_sleeps_toward_the_deadline_rather_than_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The halving wait is what earns the sub-millisecond schedule error in DESIGN.md's timing table: one
    sleep for the whole remainder inherits the platform's oversleep, which is why the table shows 8.1 ms."""
    requested: list[float] = []
    real_sleep = time.sleep

    def recording_sleep(seconds: float) -> None:
        requested.append(seconds)
        real_sleep(seconds)

    monkeypatch.setattr("src.testing.sampling.time.sleep", recording_sleep)

    collect_samples(lambda: 1.0, SamplingPlan(count=3, interval_s=0.05), "greenlee")
    assert len(requested) > 3  # several converging sleeps per sample, not one per deadline
    assert max(requested) < 0.05  # and none of them the whole remaining interval


def test_a_late_sample_fails_the_shipped_criterion(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic, unlike the statistical bound above: no test configuration enables the criterion, so
    without this the 10 ms the config ships is only ever exercised on synthetic samples."""
    monkeypatch.setattr("src.testing.sampling._wait_until", lambda deadline: None)  # every sample fires at once
    plan = SamplingPlan(count=5, interval_s=0.01)
    samples = collect_samples(lambda: 1.0, plan, "greenlee")

    timing = TimingStats.from_samples(samples)
    verdict = evaluate(samples, None, timing, SPEC, max_failure_rate=0.0, max_schedule_error_ms=10.0)
    assert not verdict.passed and "exceeds the 10 ms limit" in verdict.reasons[0]
