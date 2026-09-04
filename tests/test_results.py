from datetime import datetime
from pathlib import Path
from typing import Optional

import pytest

from src.testing.results import ResultsArchive, RunResult
from src.testing.sampling import Sample, SamplingPlan
from src.utils.config import AmmeterSpec

SPEC = AmmeterSpec("greenlee", "localhost", 5000, "CMD", expected_min_a=0.0, expected_max_a=10.0)
PLAN = SamplingPlan(count=2, interval_s=0.5)


def make_result(values: list[Optional[float]], started: datetime = datetime(2026, 1, 2, 3, 4, 5)) -> RunResult:
    samples = [Sample(i, i * 0.5, i * 0.5, 1.0, v, None if v is not None else "boom") for i, v in enumerate(values)]
    return RunResult.from_samples(SPEC, PLAN, samples, started, {"label": "unit"}, 0.0, reference_a=2.0)


def test_result_is_built_from_samples() -> None:
    result = make_result([1.0, 3.0])
    assert result.run_id.startswith("20260102_030405_greenlee_")
    assert result.created_at == "2026-01-02T03:04:05"
    assert result.statistics is not None and result.statistics.mean == 2.0
    assert result.accuracy is not None and result.accuracy.bias_a == 0.0
    assert result.verdict.passed
    assert result.values == [1.0, 3.0]


def test_all_failed_samples_give_no_statistics() -> None:
    result = make_result([None, None])
    assert result.statistics is None and result.accuracy is None
    assert result.failure_rate == 1.0
    assert not result.verdict.passed


def test_run_ids_are_unique() -> None:
    assert make_result([1.0]).run_id != make_result([1.0]).run_id


def test_archive_round_trip(tmp_path: Path) -> None:
    archive = ResultsArchive(tmp_path / "results")
    for result in (make_result([1.0, 3.0]), make_result([None, None])):
        path = archive.save(result)
        assert path == tmp_path / "results" / f"{result.run_id}.json"
        assert archive.load(result.run_id) == result


def test_archive_lists_runs_oldest_first(tmp_path: Path) -> None:
    archive = ResultsArchive(tmp_path)
    newer = make_result([1.0], datetime(2026, 1, 2))
    older = make_result([1.0], datetime(2025, 1, 2))
    archive.save(newer)
    archive.save(older)
    assert [r.run_id for r in archive.load_all()] == [older.run_id, newer.run_id]
    assert archive.latest_per_ammeter() == [newer]


def test_missing_run_is_reported(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no run 'nope'"):
        ResultsArchive(tmp_path).load("nope")


def test_empty_archive(tmp_path: Path) -> None:
    assert ResultsArchive(tmp_path / "missing").load_all() == []
