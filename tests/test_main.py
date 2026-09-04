import dataclasses

import pytest

from Ammeters.client import AmmeterError, read_current
from main import start_emulators
from src.utils.config import Config
from tests.conftest import free_port


def test_start_emulators_serves_every_configured_ammeter(config: Config) -> None:
    fresh_ports = {name: dataclasses.replace(spec, port=free_port()) for name, spec in config.ammeters.items()}
    specs = start_emulators(dataclasses.replace(config, ammeters=fresh_ports))
    assert [spec.name for spec in specs] == ["greenlee", "entes", "circutor"]
    for spec in specs:
        assert read_current(spec.host, spec.port, spec.command, spec.timeout_s) > 0


def test_start_emulators_rejects_ports_in_use(config: Config) -> None:
    with pytest.raises(AmmeterError, match="already in use"):
        start_emulators(config)  # the session emulators already own these ports


def test_start_emulators_needs_an_ammeter_with_an_emulator(config: Config) -> None:
    fluke = dataclasses.replace(config.ammeter("greenlee"), name="fluke")
    with pytest.raises(AmmeterError, match="none of the configured ammeters"):
        start_emulators(dataclasses.replace(config, ammeters={"fluke": fluke}))
