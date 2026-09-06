import random
import socket
import struct
import threading

import pytest

from Ammeters.client import read_current, wait_for_ammeter
from main import EMULATORS
from src.utils.config import DEFAULT_CONFIG_PATH, Config
from tests.helpers import free_port


def test_constructing_an_emulator_does_not_disturb_the_global_rng() -> None:
    """All three emulators draw from the module-level random, so construction must not reseed it."""
    random.seed(1234)
    expected = [random.random() for _ in range(3)]

    random.seed(1234)
    for emulator_class in EMULATORS.values():
        emulator_class(0)  # port 0 binds nothing; __init__ only stores it
    assert [random.random() for _ in range(3)] == expected


def test_emulators_are_independent_of_each_other() -> None:
    """Constructing the other two mid-stream must not shift the readings of the first."""
    greenlee = EMULATORS["greenlee"](0)
    random.seed(99)
    undisturbed = [greenlee.measure_current() for _ in range(5)]

    random.seed(99)
    EMULATORS["entes"](0)
    EMULATORS["circutor"](0)
    assert [greenlee.measure_current() for _ in range(5)] == undisturbed


@pytest.mark.parametrize("name", sorted(EMULATORS))
def test_shipped_expected_ranges_match_what_the_emulators_emit(name: str) -> None:
    """The other half of the config-against-the-device check: ranges asserted from the config, not literals."""
    spec = Config.load(DEFAULT_CONFIG_PATH).ammeter(name)
    assert spec.expected_min_a is not None and spec.expected_max_a is not None
    emulator = EMULATORS[name](0)
    assert all(spec.expected_min_a <= emulator.measure_current() <= spec.expected_max_a for _ in range(200))


def test_a_client_that_vanishes_does_not_kill_the_server() -> None:
    """is_listening connects and closes at once; Windows answers a backlogged close with an RST, and an
    unhandled reset used to end the serving thread, so the ammeter went silent for the rest of the run."""
    port = free_port()
    threading.Thread(target=EMULATORS["greenlee"](port).start_server, daemon=True).start()
    wait_for_ammeter("127.0.0.1", port)
    command = Config.load(DEFAULT_CONFIG_PATH).ammeter("greenlee").command

    rude = socket.socket()
    rude.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))  # close() sends RST
    rude.connect(("127.0.0.1", port))
    rude.close()

    assert read_current("127.0.0.1", port, command) > 0  # still serving
