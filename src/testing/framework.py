"""Unified test API: sample any configured ammeter, evaluate the readings and archive the result."""

import logging
import platform
from datetime import datetime
from typing import Optional

from src.testing.ammeter import Ammeter, FaultInjector, Measure
from src.testing.results import ResultsArchive, RunResult
from src.testing.sampling import SamplingPlan, collect_samples
from src.utils.config import Config

logger = logging.getLogger(__name__)


class AmmeterTestFramework:
    def __init__(self, config: Config):
        self.config = config
        self.archive = ResultsArchive(config.results_dir)

    def sampling_plan(self) -> SamplingPlan:
        return SamplingPlan.resolve(self.config.sample_count, self.config.duration_s, self.config.frequency_hz)

    def run_test(self, ammeter_type: str, label: Optional[str] = None) -> RunResult:
        """Run one sampling test. Raises ConfigError for unknown ammeters and AmmeterError if unreachable."""
        spec = self.config.ammeter(ammeter_type)
        plan = self.sampling_plan()
        ammeter = Ammeter(spec)
        ammeter.measure()  # connectivity check: fail fast instead of producing a run full of failures

        measure: Measure = ammeter.measure
        if self.config.simulated_failure_rate:
            measure = FaultInjector(measure, self.config.simulated_failure_rate, self.config.simulation_seed)

        pace = f"at {plan.frequency_hz:g} Hz" if plan.frequency_hz else "as fast as possible"
        logger.info("%s: taking %d samples %s", spec.name, plan.count, pace)
        started = datetime.now().astimezone()
        samples = collect_samples(measure, plan)

        metadata = {
            "label": label,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "simulated_failure_rate": self.config.simulated_failure_rate,
        }
        result = RunResult.from_samples(
            spec, plan, samples, started, metadata, self.config.max_failure_rate, self.config.reference_current_a
        )
        path = self.archive.save(result)
        logger.info("%s: %s, saved %s", spec.name, "PASS" if result.verdict.passed else "FAIL", path)
        return result

    def run_all(self, label: Optional[str] = None) -> list[RunResult]:
        return [self.run_test(name, label) for name in self.config.ammeters]
