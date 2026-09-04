"""Test results and the on-disk archive (one JSON file per run)."""

import json
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.testing.analysis import AccuracyStats, Statistics, TimingStats, Verdict, evaluate
from src.testing.sampling import Sample, SamplingPlan
from src.utils.config import AmmeterSpec


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
        max_failure_rate: float,
        reference_a: Optional[float] = None,
    ) -> "RunResult":
        values = [s.value_a for s in samples if s.value_a is not None]
        stats = Statistics.from_values(values) if values else None
        return cls(
            run_id=f"{started:%Y%m%d_%H%M%S}_{spec.name}_{uuid.uuid4().hex[:8]}",
            created_at=started.isoformat(timespec="seconds"),
            ammeter=spec,
            plan=plan,
            metadata=metadata,
            samples=list(samples),
            statistics=stats,
            timing=TimingStats.from_samples(samples),
            accuracy=AccuracyStats.from_values(values, reference_a) if values and reference_a is not None else None,
            verdict=evaluate(samples, stats, spec, max_failure_rate),
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


class ResultsArchive:
    """Stores runs as <directory>/<run_id>.json. Run ids embed the start time, so names sort chronologically."""

    def __init__(self, directory: Path):
        self.directory = directory

    def path_for(self, run_id: str, suffix: str = ".json") -> Path:
        return self.directory / f"{run_id}{suffix}"

    def save(self, result: RunResult) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.path_for(result.run_id)
        path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
        return path

    def load(self, run_id: str) -> RunResult:
        path = self.path_for(run_id)
        if not path.is_file():
            raise FileNotFoundError(f"no run '{run_id}' in {self.directory}")
        return RunResult.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def load_all(self) -> list[RunResult]:
        """All archived runs, oldest first."""
        return sorted((self.load(p.stem) for p in self.directory.glob("*.json")), key=lambda r: r.created_at)

    def latest_per_ammeter(self) -> list[RunResult]:
        latest = {result.ammeter.name: result for result in self.load_all()}
        return list(latest.values())
