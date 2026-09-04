"""Unified measurement interface over the emulator protocol, plus fault injection for error simulation."""

import random
from collections.abc import Callable
from typing import Optional

from Ammeters.client import AmmeterError, read_current
from src.utils.config import AmmeterSpec

Measure = Callable[[], float]


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
