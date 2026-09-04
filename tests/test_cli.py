import socket
from collections.abc import Callable
from pathlib import Path

import pytest

from src.testing.cli import main
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


def test_failed_verdict_sets_exit_code(cli: Callable[..., int], capsys: pytest.CaptureFixture[str]) -> None:
    assert cli("run", "greenlee", "--simulate-errors", "1", "--no-plot") == 1
    assert "[FAIL]" in capsys.readouterr().out


def test_usage_errors(cli: Callable[..., int], capsys: pytest.CaptureFixture[str]) -> None:
    assert cli("run", "fluke", "--no-plot") == 2
    assert "unknown ammeter 'fluke'" in capsys.readouterr().err

    assert cli("run", "--duration", "9", "--no-plot") == 2
    err = capsys.readouterr().err
    assert "give only two" in err and "--duration was combined with the config" in err

    assert cli("show", "missing_run") == 2
    assert "no run 'missing_run'" in capsys.readouterr().err

    assert cli("compare") == 2
    assert "nothing to compare" in capsys.readouterr().err


def test_unreachable_ammeter_hint(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "dead.yaml"
    config.write_text(CONFIG_TEMPLATE.format(greenlee=free_port(), entes=free_port()), encoding="utf-8")
    assert main(["--config", str(config), "--results-dir", str(tmp_path), "run", "greenlee", "--no-plot"]) == 1
    assert "python main.py" in capsys.readouterr().err


def test_start_emulators_reports_why_main_py_died(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    taken = socket.socket()
    taken.bind(("localhost", 0))
    taken.listen()
    with taken:
        config = tmp_path / "taken.yaml"
        config.write_text(CONFIG_TEMPLATE.format(greenlee=taken.getsockname()[1], entes=free_port()), encoding="utf-8")
        exit_code = main(["--config", str(config), "--results-dir", str(tmp_path), "run", "--start-emulators"])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "main.py exited" in err and "already in use" in err and "pass --start-emulators" not in err


def test_start_emulators_runs_main_py(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "fresh.yaml"
    config.write_text(CONFIG_TEMPLATE.format(greenlee=free_port(), entes=free_port()), encoding="utf-8")
    args = ["--config", str(config), "--results-dir", str(tmp_path / "results")]
    assert main([*args, "run", "--start-emulators", "--no-plot"]) == 0
    assert capsys.readouterr().out.count("[PASS]") == 2
    assert main([*args, "run", "greenlee", "--no-plot"]) == 1  # the emulators were stopped again
