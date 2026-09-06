import dataclasses
import threading
import time

import pytest

from Ammeters.client import AmmeterConnectionError, AmmeterError
from src.testing.ammeter import FaultInjector, Measure, Retrying
from src.testing.framework import AmmeterTestFramework
from src.utils.config import AmmeterSpec, Config, ConfigError
from tests.helpers import free_port


def test_run_test_samples_evaluates_and_archives(config: Config) -> None:
    framework = AmmeterTestFramework(config)
    result = framework.run_test("greenlee", label="unit")

    assert len(result.samples) == 5 and result.failed_count == 0
    assert result.statistics is not None and result.statistics.minimum >= 0.01
    assert result.verdict.passed
    assert result.metadata["label"] == "unit"
    assert framework.archive.load(result.run_id) == result


def test_run_all_covers_every_configured_ammeter(config: Config) -> None:
    results = AmmeterTestFramework(config).run_all()
    assert [r.ammeter.name for r in results] == ["greenlee", "entes", "circutor"]
    assert all(r.verdict.passed for r in results)


def recording_framework(config: Config, threads: dict[str, str]) -> AmmeterTestFramework:
    """A framework whose transport records which thread each ammeter was sampled on."""

    class Recording(AmmeterTestFramework):
        def make_measure(self, spec: AmmeterSpec) -> Measure:
            def measure() -> float:
                threads[spec.name] = threading.current_thread().name
                return 1.0

            return measure

    return Recording(config)


def test_run_all_samples_the_ammeters_over_the_same_window(config: Config) -> None:
    started = time.monotonic()
    results = AmmeterTestFramework(config).run_all()
    elapsed = time.monotonic() - started

    longest = max(r.timing.actual_span_s for r in results)
    assert elapsed < longest * 1.5  # three sequential windows would take at least 3x the longest


def test_every_ammeter_gets_its_own_worker(config: Config) -> None:
    threads: dict[str, str] = {}
    recording_framework(config, threads).run_all()
    assert len(set(threads.values())) == 3
    assert all(name.startswith("ammeter") for name in threads.values())


def test_a_single_ammeter_stays_on_the_calling_thread(config: Config) -> None:
    threads: dict[str, str] = {}
    recording_framework(config, threads).run_selected(["greenlee"])
    assert threads == {"greenlee": threading.current_thread().name}


def test_run_selected_returns_results_in_the_order_given(config: Config) -> None:
    results = AmmeterTestFramework(config).run_selected(["circutor", "greenlee"])
    assert [r.ammeter.name for r in results] == ["circutor", "greenlee"]


def test_run_selected_rejects_an_unknown_name_before_starting_any_thread(config: Config) -> None:
    with pytest.raises(ConfigError, match="unknown ammeter 'fluke'"):
        AmmeterTestFramework(config).run_selected(["greenlee", "fluke"])
    assert not config.results_dir.exists()


def test_an_unreachable_ammeter_fails_before_any_sampling_starts(config: Config) -> None:
    """The pool would otherwise hide the error behind a full sampling window of the healthy devices."""
    dead = dataclasses.replace(config.ammeter("circutor"), port=free_port())  # last in config order
    config = dataclasses.replace(config, ammeters={**config.ammeters, "circutor": dead})

    with pytest.raises(AmmeterConnectionError):
        AmmeterTestFramework(config).run_selected(["greenlee", "entes", "circutor"])
    assert not config.results_dir.exists()


def test_unknown_ammeter(config: Config) -> None:
    with pytest.raises(ConfigError, match="unknown ammeter"):
        AmmeterTestFramework(config).run_test("fluke")


def test_unreachable_ammeter_fails_fast(config: Config) -> None:
    dead = dataclasses.replace(config.ammeter("greenlee"), port=free_port())
    config = dataclasses.replace(config, ammeters={"greenlee": dead})
    with pytest.raises(AmmeterConnectionError):
        AmmeterTestFramework(config).run_test("greenlee")
    assert not config.results_dir.exists()


def test_simulated_failures_are_reported(config: Config) -> None:
    config = dataclasses.replace(config, simulated_failure_rate=1.0, simulation_seed=7)
    result = AmmeterTestFramework(config).run_test("greenlee")

    assert result.failure_rate == 1.0 and result.statistics is None
    assert not result.verdict.passed
    assert result.verdict.reasons == ["5 of 5 samples failed (100%, limit 5%)"]
    assert result.metadata["simulated_failure_rate"] == 1.0


def test_ammeter_reference_overrides_the_global_one(config: Config) -> None:
    calibrated = dataclasses.replace(config.ammeter("greenlee"), reference_current_a=1.0)
    config = dataclasses.replace(config, ammeters={**config.ammeters, "greenlee": calibrated}, reference_current_a=2.0)
    framework = AmmeterTestFramework(config)

    assert framework.run_test("greenlee").accuracy.reference_a == 1.0  # type: ignore[union-attr]
    assert framework.run_test("entes").accuracy.reference_a == 2.0  # type: ignore[union-attr]


def test_run_metadata_records_the_criteria(config: Config) -> None:
    metadata = AmmeterTestFramework(config).run_test("greenlee").metadata
    assert metadata["max_failure_rate"] == 0.05 and metadata["max_schedule_error_ms"] == 10


def test_retrying_wraps_the_transport_only_when_it_is_enabled(config: Config) -> None:
    greenlee = config.ammeter("greenlee")
    retried = dataclasses.replace(config, retry_attempts=3, retry_backoff_s=0.0)
    assert isinstance(AmmeterTestFramework(retried).make_measure(greenlee), Retrying)

    off = dataclasses.replace(config, retry_attempts=1)
    assert not isinstance(AmmeterTestFramework(off).make_measure(greenlee), Retrying)


def test_run_metadata_records_the_retry_settings(config: Config) -> None:
    """A retried run has different failure semantics, so an archived one has to say which it was."""
    config = dataclasses.replace(config, retry_attempts=3, retry_backoff_s=0.0)
    metadata = AmmeterTestFramework(config).run_test("greenlee").metadata
    assert metadata["retry_attempts"] == 3 and metadata["retry_backoff_s"] == 0.0


def test_an_oversized_retry_budget_is_rejected_before_any_device_is_touched(config: Config) -> None:
    """A budget wider than the interval would push every later sample late and fail the timing criterion."""
    config = dataclasses.replace(config, retry_attempts=4, retry_backoff_s=1.0)
    with pytest.raises(ConfigError, match="retry budget"):
        AmmeterTestFramework(config).run_test("greenlee")
    assert not config.results_dir.exists()


def test_make_measure_hook_swaps_the_transport(config: Config) -> None:
    class ConstantFramework(AmmeterTestFramework):
        def make_measure(self, spec: AmmeterSpec) -> Measure:
            return lambda: 4.2

    result = ConstantFramework(config).run_test("greenlee")
    assert result.statistics is not None and result.statistics.mean == 4.2
    assert result.verdict.passed


def test_fault_injector_is_reproducible_with_a_seed() -> None:
    def outcomes(seed: int) -> list[bool]:
        injector = FaultInjector(lambda: 1.0, failure_rate=0.5, seed=seed)
        results = []
        for _ in range(20):
            try:
                injector()
                results.append(True)
            except AmmeterError:
                results.append(False)
        return results

    assert outcomes(1) == outcomes(1)
    assert True in outcomes(1) and False in outcomes(1)
    with pytest.raises(ValueError):
        FaultInjector(lambda: 1.0, failure_rate=1.5)
