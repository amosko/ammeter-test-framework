"""Unified test API: sample any configured ammeter, evaluate the readings and archive the result."""

import logging
import platform
import threading
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional

from src.testing.ammeter import Ammeter, FaultInjector, Measure, Retrying
from src.testing.results import ResultsArchive, RunResult
from src.testing.sampling import SamplingPlan, collect_samples
from src.utils.config import AmmeterSpec, Config, ConfigError

logger = logging.getLogger(__name__)


class AmmeterTestFramework:
    def __init__(self, config: Config):
        self.config = config
        self.archive = ResultsArchive(config.results_dir)
        self._cancelled = threading.Event()

    def sampling_plan(self) -> SamplingPlan:
        """Resolve the plan and reject a retry budget that cannot fit inside one sampling interval."""
        plan = SamplingPlan.resolve(self.config.sample_count, self.config.duration_s, self.config.frequency_hz)
        budget = self.config.retry_budget_s
        if plan.interval_s and budget >= plan.interval_s:
            raise ConfigError(
                f"the retry budget of {budget * 1000:.0f} ms per sample does not fit in the "
                f"{plan.interval_s * 1000:.0f} ms sampling interval; lower testing.retry.attempts or "
                f"backoff_seconds, or sample more slowly"
            )
        return plan

    def make_measure(self, spec: AmmeterSpec) -> Measure:
        """Override to use another transport; the callable must raise AmmeterError for a failed reading."""
        measure: Measure = Ammeter(spec).measure
        if self.config.retry_attempts > 1:
            measure = Retrying(measure, self.config.retry_attempts, self.config.retry_backoff_s)
        return measure

    def run_test(self, ammeter_type: str, label: Optional[str] = None) -> RunResult:
        """Run one sampling test. Raises ConfigError for unknown ammeters and AmmeterError if unreachable."""
        spec = self.config.ammeter(ammeter_type)
        plan = self.sampling_plan()
        measure = self.make_measure(spec)
        measure()  # connectivity check: fail fast instead of producing a run full of failures

        if self.config.simulated_failure_rate:
            measure = FaultInjector(measure, self.config.simulated_failure_rate, self.config.simulation_seed)

        pace = f"at {plan.frequency_hz:g} Hz" if plan.frequency_hz else "as fast as possible"
        logger.info("%s: taking %d samples %s", spec.name, plan.count, pace)
        started = datetime.now().astimezone()
        samples = collect_samples(measure, plan, spec.name, self._cancelled)

        metadata = {
            "label": label,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "simulated_failure_rate": self.config.simulated_failure_rate,
            "max_failure_rate": self.config.max_failure_rate,
            "max_schedule_error_ms": self.config.max_schedule_error_ms,
            "retry_attempts": self.config.retry_attempts,
            "retry_backoff_s": self.config.retry_backoff_s,
        }
        reference = (
            spec.reference_current_a if spec.reference_current_a is not None else self.config.reference_current_a
        )
        result = RunResult.from_samples(
            spec,
            plan,
            samples,
            started,
            metadata,
            self.config.max_failure_rate,
            reference,
            self.config.max_schedule_error_ms,
        )
        path = self.archive.save(result)
        logger.info("%s: %s, saved %s", spec.name, "PASS" if result.verdict.passed else "FAIL", path)
        return result

    def run_selected(self, names: Sequence[str], label: Optional[str] = None) -> list[RunResult]:
        """Sample the named ammeters over the same window, one worker each. Results follow the order given."""
        for name in names:
            self.make_measure(self.config.ammeter(name))()  # unknown or unreachable: fail before the pool
        if len(names) < 2:
            return [self.run_test(name, label) for name in names]
        pool = ThreadPoolExecutor(max_workers=len(names), thread_name_prefix="ammeter")
        try:
            futures = [pool.submit(self.run_test, name, label) for name in names]
            return [future.result() for future in futures]
        except KeyboardInterrupt:
            self._cancelled.set()  # SIGINT reaches only the main thread; the workers have to be told
            raise
        finally:
            pool.shutdown(wait=True)

    def run_all(self, label: Optional[str] = None) -> list[RunResult]:
        return self.run_selected(list(self.config.ammeters), label)
