import dataclasses
import logging
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from Ammeters.client import wait_for_ammeter
from main import EMULATORS
from src.utils.config import DEFAULT_CONFIG_PATH, AmmeterSpec, Config
from tests.helpers import free_port


@pytest.fixture(scope="session")
def emulator_ports() -> dict[str, int]:
    """Every emulator running on a free port for the whole test session."""
    ports = {}
    for name, emulator_class in EMULATORS.items():
        port = free_port()
        threading.Thread(target=emulator_class(port).start_server, daemon=True).start()
        wait_for_ammeter("localhost", port)
        ports[name] = port
    return ports


@pytest.fixture
def config(emulator_ports: dict[str, int], tmp_path: Path) -> Config:
    """The shipped config, pointed at the session emulators and a temporary results directory."""
    shipped = Config.load(DEFAULT_CONFIG_PATH)
    ammeters = {name: dataclasses.replace(spec, port=emulator_ports[name]) for name, spec in shipped.ammeters.items()}
    return dataclasses.replace(
        shipped, ammeters=ammeters, sample_count=5, duration_s=None, frequency_hz=100, results_dir=tmp_path / "results"
    )


@pytest.fixture
def greenlee(config: Config) -> AmmeterSpec:
    return config.ammeter("greenlee")


@pytest.fixture(autouse=True)
def reset_logging() -> Iterator[None]:
    """CLI tests attach handlers to pytest's capture streams; drop them so later tests do not write to closed files."""
    yield
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()
