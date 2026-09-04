import dataclasses
import socket
import threading
from pathlib import Path

import pytest

from Ammeters.base_ammeter import AmmeterEmulatorBase
from Ammeters.Circutor_Ammeter import CircutorAmmeter
from Ammeters.client import wait_for_ammeter
from Ammeters.Entes_Ammeter import EntesAmmeter
from Ammeters.Greenlee_Ammeter import GreenleeAmmeter
from src.utils.config import DEFAULT_CONFIG_PATH, AmmeterSpec, Config

EMULATORS: dict[str, type[AmmeterEmulatorBase]] = {
    "greenlee": GreenleeAmmeter,
    "entes": EntesAmmeter,
    "circutor": CircutorAmmeter,
}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("localhost", 0))
        return int(sock.getsockname()[1])


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
