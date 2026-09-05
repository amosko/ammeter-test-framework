"""Unified measurement interface over the emulator protocol, plus fault injection for error simulation."""

import logging
import random
import time
from collections.abc import Callable
from typing import Optional

from Ammeters.client import AmmeterConnectionError, AmmeterError, AmmeterTimeoutError, read_current
from src.utils.config import AmmeterSpec

logger = logging.getLogger(__name__)

Measure = Callable[[], float]  # returns amperes; raises AmmeterError for a failed reading

TRANSIENT_ERRORS = (AmmeterConnectionError, AmmeterTimeoutError)  # a protocol error is deterministic, never these


class Ammeter:
    """One measurement device. Any callable returning amperes can stand in for Ammeter.measure."""

    def __init__(self, spec: AmmeterSpec):
        self.spec = spec

    @property
    def name(self) -> str:
        return self.spec.name

    def measure(self) -> float:
        """Take one reading in amperes; raises AmmeterError on failure."""
        return read_current(self.spec.host, self.spec.port, self.spec.command, self.spec.timeout_s)


class FaultInjector:
    """Wraps a measure function and fails a random fraction of the calls."""

    def __init__(self, measure: Measure, failure_rate: float, seed: Optional[int] = None):
        if not 0.0 <= failure_rate <= 1.0:
            raise ValueError(f"failure_rate must be between 0 and 1, got {failure_rate}")
        self._measure = measure
        self._failure_rate = failure_rate
        self._random = random.Random(seed)

    def __call__(self) -> float:
        if self._random.random() < self._failure_rate:
            raise AmmeterError("simulated fault")
        return self._measure()


class Retrying:
    """Retries transient transport failures.

    A protocol error is not retried: an unanswered or non-numeric reply means the wrong command or port,
    which is deterministic, so retrying only burns the sampling budget and delays the report of a
    configuration fault. Backoff is linear rather than exponential because the whole budget has to fit
    inside a sampling interval measured in tens of milliseconds, where doubling buys nothing.
    """

    def __init__(self, measure: Measure, attempts: int = 2, backoff_s: float = 0.005) -> None:
        if attempts < 1:
            raise ValueError(f"attempts must be at least 1, got {attempts}")
        if backoff_s < 0:
            raise ValueError(f"backoff must not be negative, got {backoff_s}")
        self._measure = measure
        self._attempts = attempts
        self._backoff_s = backoff_s

    def __call__(self) -> float:
        for attempt in range(1, self._attempts):
            try:
                return self._measure()
            except TRANSIENT_ERRORS as exc:
                logger.debug("attempt %d/%d failed (%s), retrying", attempt, self._attempts, exc)
                time.sleep(self._backoff_s * attempt)
        return self._measure()  # the last attempt is not retried, so its error reaches the caller unchanged
