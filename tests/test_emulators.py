import random

import pytest

from main import EMULATORS


@pytest.mark.parametrize("name", sorted(EMULATORS))
def test_constructing_an_emulator_does_not_disturb_the_global_rng(name: str) -> None:
    """All three emulators draw from the module-level random, so construction must not reseed it."""
    random.seed(1234)
    expected = [random.random() for _ in range(3)]

    random.seed(1234)
    EMULATORS[name](0)  # port 0 binds nothing; __init__ only stores it
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


@pytest.mark.parametrize(
    "name, low, high",
    [("greenlee", 0.01, 100), ("entes", 5, 200), ("circutor", 0.001, 0.1)],
)
def test_emulators_produce_readings_in_their_documented_range(name: str, low: float, high: float) -> None:
    """Pins config/config.yaml's expected_range_a to what the devices actually emit."""
    emulator = EMULATORS[name](0)
    assert all(low <= emulator.measure_current() <= high for _ in range(200))
