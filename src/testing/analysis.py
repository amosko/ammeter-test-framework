"""Statistics, timing metrics and pass/fail evaluation for a set of samples."""

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Optional

from src.testing.sampling import Sample, SamplingPlan
from src.utils.config import AmmeterSpec


@dataclass(frozen=True)
class Statistics:
    count: int
    mean: float
    median: float
    stdev: float  # sample standard deviation; 0 for a single value
    minimum: float
    maximum: float
    cv_percent: Optional[float]  # stdev / mean: unit-free precision, comparable across ammeter types

    @classmethod
    def from_values(cls, values: Sequence[float]) -> "Statistics":
        if not values:
            raise ValueError("no values to analyse")
        mean = statistics.fmean(values)
        stdev = statistics.stdev(values) if len(values) > 1 else 0.0
        return cls(
            count=len(values),
            mean=mean,
            median=statistics.median(values),
            stdev=stdev,
            minimum=min(values),
            maximum=max(values),
            cv_percent=stdev / abs(mean) * 100 if mean else None,
        )


@dataclass(frozen=True)
class TimingStats:
    planned_duration_s: float
    actual_duration_s: float
    max_timing_error_ms: float  # largest deviation of a request from its schedule
    mean_latency_ms: float
    max_latency_ms: float

    @classmethod
    def from_samples(cls, samples: Sequence[Sample], plan: SamplingPlan) -> "TimingStats":
        latencies = [s.latency_ms for s in samples]
        last = samples[-1]
        return cls(
            planned_duration_s=plan.duration_s,
            actual_duration_s=last.measured_at_s + last.latency_ms / 1000,
            max_timing_error_ms=max(abs(s.measured_at_s - s.scheduled_s) for s in samples) * 1000,
            mean_latency_ms=statistics.fmean(latencies),
            max_latency_ms=max(latencies),
        )


@dataclass(frozen=True)
class AccuracyStats:
    """Error against a known reference current; only meaningful with a calibrated source."""

    reference_a: float
    bias_a: float  # mean - reference
    mean_abs_error_a: float
    max_abs_error_a: float
    mean_abs_error_percent: float

    @classmethod
    def from_values(cls, values: Sequence[float], reference_a: float) -> "AccuracyStats":
        if reference_a == 0:
            raise ValueError("reference current must not be zero")
        errors = [abs(v - reference_a) for v in values]
        mean_abs_error = statistics.fmean(errors)
        return cls(
            reference_a=reference_a,
            bias_a=statistics.fmean(values) - reference_a,
            mean_abs_error_a=mean_abs_error,
            max_abs_error_a=max(errors),
            mean_abs_error_percent=mean_abs_error / abs(reference_a) * 100,
        )


@dataclass(frozen=True)
class Verdict:
    passed: bool
    reasons: list[str]  # empty when passed


def evaluate(
    samples: Sequence[Sample], stats: Optional[Statistics], spec: AmmeterSpec, max_failure_rate: float
) -> Verdict:
    """Fail when too many samples failed or a reading is outside the ammeter's expected range."""
    reasons = []
    failed = sum(not s.ok for s in samples)
    failure_rate = failed / len(samples)
    if failure_rate > max_failure_rate:
        reasons.append(f"{failed} of {len(samples)} samples failed ({failure_rate:.0%}, limit {max_failure_rate:.0%})")
    if stats is not None:
        if spec.expected_min_a is not None and stats.minimum < spec.expected_min_a:
            reasons.append(f"minimum {stats.minimum:.4g} A is below the expected {spec.expected_min_a:g} A")
        if spec.expected_max_a is not None and stats.maximum > spec.expected_max_a:
            reasons.append(f"maximum {stats.maximum:.4g} A is above the expected {spec.expected_max_a:g} A")
    return Verdict(passed=not reasons, reasons=reasons)
