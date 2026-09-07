import dataclasses
import io
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Optional

import pytest

from src.testing import cli as cli_module
from src.testing.cli import main
from src.testing.results import ResultsArchive, RunResult
from src.testing.sampling import SamplingPlan
from src.utils.config import Config
from tests.helpers import free_port

CONFIG_TEMPLATE = """
ammeters:
  greenlee:
    port: {greenlee}
    command: "MEASURE_GREENLEE -get_measurement"
    expected_range_a: [0.01, 100]
  entes:
    port: {entes}
    command: "MEASURE_ENTES -get_data"
testing:
  sampling:
    measurements_count: 4
    sampling_frequency_hz: 100
  max_failure_rate: 0.05
"""


@pytest.fixture
def config_file(tmp_path: Path, emulator_ports: dict[str, int]) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG_TEMPLATE.format(**emulator_ports), encoding="utf-8")
    return path


@pytest.fixture
def cli(config_file: Path, tmp_path: Path) -> Callable[..., int]:
    def run(*args: str) -> int:
        return main(["--config", str(config_file), "--results-dir", str(tmp_path / "results"), *args])

    return run


def test_run_list_show_compare(cli: Callable[..., int], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli("run", "--no-plot", "--label", "smoke") == 0
    out = capsys.readouterr().out
    assert out.count("[PASS]") == 2
    assert "Comparison of 2 runs" in out and "Most consistent" in out
    run_files = sorted((tmp_path / "results").glob("*.json"))
    assert len(run_files) == 2

    assert cli("list") == 0
    listing = capsys.readouterr().out
    assert all(path.stem in listing for path in run_files) and "smoke" in listing

    assert cli("show", run_files[0].stem) == 0
    assert f"Run {run_files[0].stem}" in capsys.readouterr().out

    assert cli("compare", "--latest", "--no-plot") == 0
    assert "Comparison of 2 runs" in capsys.readouterr().out


def test_run_writes_plots(cli: Callable[..., int], tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    assert cli("run", "greenlee", "--count", "3", "--frequency", "100") == 0
    assert len(list((tmp_path / "results").glob("*.png"))) == 1


def test_plotting_stays_on_the_main_thread(cli: Callable[..., int], monkeypatch: pytest.MonkeyPatch) -> None:
    """pyplot is a global state machine and is not thread-safe, so it must not run inside a worker."""
    callers: list[str] = []

    def record(result: RunResult, path: Path) -> Path:
        callers.append(threading.current_thread().name)
        return path

    monkeypatch.setattr("src.testing.cli.plot_run", record)
    monkeypatch.setattr("src.testing.cli.plot_comparison", lambda results, path: path)
    assert cli("run") == 0
    assert callers == [threading.main_thread().name] * 2  # one per ammeter, none from a worker


@pytest.mark.parametrize(
    "flags",
    [
        ["--count", "20", "--frequency", "20"],
        ["--count", "20", "--duration", "1"],
        ["--duration", "1", "--frequency", "20"],
    ],
)
def test_any_two_sampling_flags_define_the_plan(
    flags: list[str], cli: Callable[..., int], tmp_path: Path
) -> None:
    """The config supplies the third value otherwise, so every pair but its own became an inconsistent triple."""
    assert cli("run", "greenlee", *flags, "--no-plot") == 0
    (result,) = ResultsArchive(tmp_path / "results").load_all()
    assert result.plan == SamplingPlan(count=20, interval_s=0.05)


def test_one_sampling_flag_still_combines_with_the_config(cli: Callable[..., int], tmp_path: Path) -> None:
    assert cli("run", "greenlee", "--count", "6", "--no-plot") == 0
    (result,) = ResultsArchive(tmp_path / "results").load_all()
    assert result.plan == SamplingPlan(count=6, interval_s=0.01)  # 100 Hz from the config


def test_failed_verdict_sets_exit_code(cli: Callable[..., int], capsys: pytest.CaptureFixture[str]) -> None:
    assert cli("run", "greenlee", "--simulate-errors", "1", "--no-plot") == 1
    assert "[FAIL]" in capsys.readouterr().out


def test_usage_errors(cli: Callable[..., int], capsys: pytest.CaptureFixture[str]) -> None:
    assert cli("run", "fluke", "--no-plot") == 2
    assert "unknown ammeter 'fluke'" in capsys.readouterr().err

    assert cli("run", "--duration", "9", "--no-plot") == 2
    err = capsys.readouterr().err
    assert "give only two" in err and "--duration was combined with the config" in err

    assert cli("run", "--duration", "0", "--frequency", "20", "--no-plot") == 2  # two flags, zero is one
    assert "was combined with the config" not in capsys.readouterr().err

    assert cli("show", "missing_run") == 2
    assert "no run 'missing_run'" in capsys.readouterr().err

    assert cli("compare") == 2
    assert "nothing to compare" in capsys.readouterr().err


def test_unreachable_ammeter_hint(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "dead.yaml"
    config.write_text(CONFIG_TEMPLATE.format(greenlee=free_port(), entes=free_port()), encoding="utf-8")
    assert main(["--config", str(config), "--results-dir", str(tmp_path), "run", "greenlee", "--no-plot"]) == 1
    assert "Start the emulators (main.py) or pass --start-emulators" in capsys.readouterr().err


def test_start_emulators_refuses_ports_that_are_already_served(
    config_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["--config", str(config_file), "--results-dir", str(tmp_path), "run", "greenlee", "--start-emulators"]
    assert main(args) == 1  # the session emulators already serve these ports
    err = capsys.readouterr().err
    assert "already served; drop --start-emulators" in err and "pass --start-emulators" not in err


def test_start_emulators_reports_why_main_py_died(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "fluke.yaml"
    fluke = "ammeters:\n  fluke:\n    port: {port}\n    command: X\ntesting:\n  sampling:\n    measurements_count: 2\n"
    config.write_text(fluke.format(port=free_port()), encoding="utf-8")
    assert main(["--config", str(config), "--results-dir", str(tmp_path), "run", "--start-emulators"]) == 1
    err = capsys.readouterr().err
    assert "main.py exited with code 1" in err and "none of the configured ammeters has an emulator" in err
    assert "pass --start-emulators" not in err  # already passed; suggesting it again would be noise


def test_start_emulators_runs_main_py(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "fresh.yaml"
    config.write_text(CONFIG_TEMPLATE.format(greenlee=free_port(), entes=free_port()), encoding="utf-8")
    args = ["--config", str(config), "--results-dir", str(tmp_path / "results")]
    assert main([*args, "run", "--start-emulators", "--no-plot"]) == 0
    assert capsys.readouterr().out.count("[PASS]") == 2
    assert main([*args, "run", "greenlee", "--no-plot"]) == 1  # the emulators were stopped again


def test_retry_attempts_flag_reaches_the_run(cli: Callable[..., int], tmp_path: Path) -> None:
    assert cli("run", "greenlee", "--retry-attempts", "2", "--no-plot") == 0
    (result,) = ResultsArchive(tmp_path / "results").load_all()
    assert result.metadata["retry_attempts"] == 2


def test_comparing_one_run_pluralises(
    cli: Callable[..., int], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli("run", "greenlee", "--count", "2", "--no-plot") == 0
    capsys.readouterr()
    (result,) = ResultsArchive(tmp_path / "results").load_all()

    assert cli("compare", result.run_id, "--no-plot") == 0
    assert "Comparison of 1 run\n" in capsys.readouterr().out  # not "1 runs"


def test_run_writes_a_log_file(cli: Callable[..., int], tmp_path: Path) -> None:
    """The original defect was a log path computed but never attached to a handler."""
    assert cli("run", "greenlee", "--count", "2", "--no-plot") == 0
    logs = list((tmp_path / "results" / "logs").glob("*.log"))
    assert len(logs) == 1 and "greenlee" in logs[0].read_text(encoding="utf-8")


def test_reference_enables_the_accuracy_metrics(cli: Callable[..., int], tmp_path: Path) -> None:
    assert cli("run", "greenlee", "--count", "2", "--reference", "3", "--no-plot") == 0
    (result,) = ResultsArchive(tmp_path / "results").load_all()
    assert result.accuracy is not None and result.accuracy.reference_a == 3.0


def test_no_plot_suppresses_the_png(cli: Callable[..., int], tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    assert cli("run", "greenlee", "--count", "2", "--no-plot") == 0
    assert list((tmp_path / "results").glob("*.png")) == []


def test_compare_no_plot_suppresses_the_png(cli: Callable[..., int], tmp_path: Path) -> None:
    """The other --no-plot: `run` and `compare` each have one, and only `run`'s was pinned."""
    pytest.importorskip("matplotlib")
    assert cli("run", "greenlee", "--count", "2", "--no-plot") == 0
    (result,) = ResultsArchive(tmp_path / "results").load_all()

    assert cli("compare", result.run_id, "--no-plot") == 0
    assert list((tmp_path / "results").glob("*comparison*.png")) == []


def test_an_interrupt_exits_130(cli: Callable[..., int], monkeypatch: pytest.MonkeyPatch) -> None:
    """README documents 130 in its exit-code list."""

    def interrupted(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("src.testing.cli.AmmeterTestFramework.run_selected", interrupted)
    assert cli("run", "greenlee", "--no-plot") == 130


class _StubbornProcess:
    """A main.py that ignores SIGTERM: every wait() with a timeout expires."""

    def __init__(self) -> None:
        self.killed = False
        self.stderr = io.StringIO()

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: Optional[float] = None) -> int:
        if timeout is not None:
            raise subprocess.TimeoutExpired("main.py", timeout)
        return -9


def test_a_stubborn_emulator_is_killed_without_masking_the_error(
    config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cleanup that raises would replace the caller's exception and leave the child holding the ports."""
    process = _StubbornProcess()
    spec = dataclasses.replace(config.ammeter("greenlee"), port=free_port())  # free: the pre-check must pass
    config = dataclasses.replace(config, ammeters={"greenlee": spec})
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)  # cli.py holds this module
    monkeypatch.setattr(cli_module, "_wait_for_emulator", lambda process, spec: None)

    emulators = cli_module._emulators(config, tmp_path / "config.yaml", ["greenlee"])
    with pytest.raises(RuntimeError, match="the run failed"), emulators:
        raise RuntimeError("the run failed")

    assert process.killed  # otherwise it goes on serving the ports the next run needs
    assert process.stderr.closed
