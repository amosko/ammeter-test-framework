"""Test results and the on-disk archive (one JSON file per run)."""

import json
import logging
import os
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.testing.analysis import AccuracyStats, Statistics, TimingStats, Verdict, evaluate
from src.testing.sampling import Sample, SamplingPlan
from src.utils.config import AmmeterSpec, ConfigError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunResult:
    run_id: str
    created_at: str  # ISO 8601 with UTC offset
    ammeter: AmmeterSpec
    plan: SamplingPlan
    metadata: dict[str, Any]
    samples: list[Sample]
    statistics: Optional[Statistics]  # None when every sample failed
    timing: TimingStats
    accuracy: Optional[AccuracyStats]  # only with a reference current
    verdict: Verdict

    @classmethod
    def from_samples(
        cls,
        spec: AmmeterSpec,
        plan: SamplingPlan,
        samples: Sequence[Sample],
        started: datetime,
        metadata: dict[str, Any],
        max_failure_rate: float = 0.0,
        reference_a: Optional[float] = None,
        max_schedule_error_ms: Optional[float] = None,
    ) -> "RunResult":
        values = [s.value_a for s in samples if s.value_a is not None]
        stats = Statistics.from_values(values) if values else None
        timing = TimingStats.from_samples(samples)
        return cls(
            run_id=f"{started:%Y%m%d_%H%M%S}_{spec.name}_{uuid.uuid4().hex[:8]}",
            created_at=started.isoformat(timespec="milliseconds"),  # concurrent runs share a second
            ammeter=spec,
            plan=plan,
            metadata=metadata,
            samples=list(samples),
            statistics=stats,
            timing=timing,
            accuracy=AccuracyStats.from_values(values, reference_a) if values and reference_a is not None else None,
            verdict=evaluate(
                samples, stats, timing, spec, max_failure_rate,
                max_schedule_error_ms if plan.has_schedule else None,
            ),
        )

    @property
    def values(self) -> list[float]:
        return [s.value_a for s in self.samples if s.value_a is not None]

    @property
    def failed_count(self) -> int:
        return sum(not s.ok for s in self.samples)

    @property
    def failure_rate(self) -> float:
        return self.failed_count / len(self.samples)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunResult":
        return cls(
            run_id=data["run_id"],
            created_at=data["created_at"],
            ammeter=AmmeterSpec(**data["ammeter"]),
            plan=SamplingPlan(**data["plan"]),
            metadata=data["metadata"],
            samples=[Sample(**s) for s in data["samples"]],
            statistics=Statistics(**data["statistics"]) if data["statistics"] else None,
            timing=TimingStats(**data["timing"]),
            accuracy=AccuracyStats(**data["accuracy"]) if data["accuracy"] else None,
            verdict=Verdict(**data["verdict"]),
        )


def _created_at(result: RunResult) -> datetime:
    """Sort key for archived runs; a naive stamp is read as UTC so a mixed archive still orders."""
    stamp = datetime.fromisoformat(result.created_at)
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


class ResultsArchive:
    """Stores runs as <directory>/<run_id>.json. Run ids embed the start time, so names sort chronologically."""

    def __init__(self, directory: Path):
        self.directory = directory

    def path_for(self, run_id: str, suffix: str = ".json") -> Path:
        return self.directory / f"{run_id}{suffix}"

    def save(self, result: RunResult) -> Path:
        """Write the run atomically: readers see a complete file or none at all."""
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.path_for(result.run_id)
        tmp = path.parent / f"{path.name}.tmp"  # .json.tmp, not .tmp: a leftover is still traceable to its run
        try:
            tmp.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
            os.replace(tmp, path)  # atomic within one filesystem, on POSIX and Windows
        except BaseException:  # KeyboardInterrupt mid-write is the case worth cleaning up after
            tmp.unlink(missing_ok=True)
            raise
        return path

    def load(self, run_id: str) -> RunResult:
        path = self.path_for(run_id)
        if not path.is_file():
            raise FileNotFoundError(f"no run '{run_id}' in {self.directory}")
        try:
            result = RunResult.from_dict(json.loads(path.read_text(encoding="utf-8")))
            _created_at(result)  # an unparseable stamp is corruption too, not a crash at sort time
            return result
        except (ValueError, KeyError, TypeError, ConfigError) as exc:
            raise ValueError(f"{path} is not a valid run file: {exc}") from None

    def load_all(self) -> list[RunResult]:
        """All archived runs, oldest first; files that are not run files are skipped with a warning."""
        results = []
        for path in self.directory.glob("*.json"):
            try:
                results.append(self.load(path.stem))
            except ValueError as exc:
                logger.warning("skipping %s", exc)
        return sorted(results, key=_created_at)

    def latest_per_ammeter(self) -> list[RunResult]:
        latest = {result.ammeter.name: result for result in self.load_all()}
        return list(latest.values())
