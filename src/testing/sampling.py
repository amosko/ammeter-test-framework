"""Timed collection of measurements."""

import logging
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional

from Ammeters.client import AmmeterError
from src.testing.ammeter import Measure

logger = logging.getLogger(__name__)

# Busy-wait this close to each deadline, sized to the platform's sleep granularity: ~15 ms on Windows
# before 3.11, which switched time.sleep to a high-resolution timer, and ~1 ms everywhere else.
COARSE_SLEEP = sys.platform == "win32" and sys.version_info < (3, 11)
SPIN_WINDOW_S = 0.02 if COARSE_SLEEP else 0.002


@dataclass(frozen=True)
class SamplingPlan:
    """How many samples to take and how far apart. interval_s == 0 means as fast as possible."""

    count: int
    interval_s: float

    def __post_init__(self) -> None:
        if self.count < 1:
            raise ValueError(f"count must be at least 1, got {self.count}")
        if self.interval_s < 0:
            raise ValueError(f"interval must not be negative, got {self.interval_s}")

    @property
    def frequency_hz(self) -> Optional[float]:
        return 1 / self.interval_s if self.interval_s else None

    @property
    def duration_s(self) -> float:
        return self.count * self.interval_s

    @classmethod
    def resolve(
        cls,
        count: Optional[int] = None,
        duration_s: Optional[float] = None,
        frequency_hz: Optional[float] = None,
    ) -> "SamplingPlan":
        """Build a plan from any two of count / duration / frequency. Count alone means unpaced sampling."""
        for name, value in (("count", count), ("duration", duration_s), ("frequency", frequency_hz)):
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive, got {value}")

        if count is not None and frequency_hz is not None:
            plan = cls(count, 1 / frequency_hz)
            if duration_s is not None and abs(plan.duration_s - duration_s) > plan.interval_s / 2:
                raise ValueError(
                    f"{count} samples at {frequency_hz:g} Hz take {plan.duration_s:g} s, not {duration_s:g} s; "
                    "give only two of count, duration and frequency"
                )
            return plan
        if count is not None and duration_s is not None:
            return cls(count, duration_s / count)
        if duration_s is not None and frequency_hz is not None:
            return cls(max(1, round(duration_s * frequency_hz)), 1 / frequency_hz)
        if count is not None:
            return cls(count, 0.0)
        raise ValueError("specify at least two of count, duration and frequency (or count alone)")


@dataclass(frozen=True)
class Sample:
    index: int
    scheduled_s: float  # planned offset from the start of the run
    measured_at_s: float  # actual offset when the request was sent
    latency_ms: float  # request round trip time
    value_a: Optional[float]
    error: Optional[str]

    @property
    def ok(self) -> bool:
        return self.error is None


def collect_samples(
    measure: Measure, plan: SamplingPlan, name: str, cancelled: Optional[threading.Event] = None
) -> list[Sample]:
    """Take plan.count measurements on a fixed schedule. Failed measurements are recorded, not raised.

    Raises KeyboardInterrupt if `cancelled` is set, before the run reaches the archive: a cancelled run
    is not a result. SIGINT only reaches the main thread, so a worker has to be told.
    """
    samples = []
    start = time.perf_counter()
    for index in range(plan.count):
        if cancelled is not None and cancelled.is_set():
            raise KeyboardInterrupt(f"{name}: cancelled after {index} samples")
        scheduled = index * plan.interval_s
        _wait_until(start + scheduled)

        sent_at = time.perf_counter()
        value: Optional[float] = None
        error: Optional[str] = None
        try:
            value = measure()
        except AmmeterError as exc:
            error = str(exc)
            logger.warning("%s sample %d failed: %s", name, index, exc)
        latency_ms = (time.perf_counter() - sent_at) * 1000
        if value is not None:
            logger.debug("%s sample %d: %.4g A in %.2f ms", name, index, value, latency_ms)

        samples.append(Sample(index, scheduled, sent_at - start, latency_ms, value, error))
    return samples


def _wait_until(deadline: float) -> None:
    """Sleep in halving steps, then spin for the last moments.

    Deadlines are absolute, so sleep overshoot cannot accumulate; halving keeps each overshoot inside the
    spin window even on systems that oversleep by up to 50% (macOS timer coalescing).
    """
    while (remaining := deadline - time.perf_counter()) > SPIN_WINDOW_S:
        time.sleep((remaining - SPIN_WINDOW_S) / 2)
    while time.perf_counter() < deadline:
        pass
