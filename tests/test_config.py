from pathlib import Path
from typing import Any

import pytest

from main import EMULATORS
from src.utils.config import DEFAULT_CONFIG_PATH, Config, ConfigError

MINIMAL: dict[str, Any] = {
    "ammeters": {"greenlee": {"port": 5000, "command": "CMD"}},
    "testing": {"sampling": {"measurements_count": 10}},
}


def with_sections(**sections: Any) -> dict[str, Any]:
    return {**MINIMAL, **sections}


def with_ammeter(**fields: Any) -> dict[str, Any]:
    return with_sections(ammeters={"a": {"port": 1, "command": "x", **fields}})


def test_shipped_config_matches_the_emulators() -> None:
    config = Config.load(DEFAULT_CONFIG_PATH)
    ports = [(spec.name, spec.port) for spec in config.ammeters.values()]
    assert ports == [("greenlee", 5000), ("entes", 5001), ("circutor", 5002)]
    assert config.sample_count == 50 and config.frequency_hz == 10 and config.duration_s is None
    assert config.max_failure_rate == 0.05 and config.max_schedule_error_ms == 10
    assert config.retry_attempts == 2 and config.retry_backoff_s == 0.005
    assert config.retry_budget_s == pytest.approx(0.005)  # comfortably inside the shipped 100 ms interval


def test_shipped_config_commands_match_the_emulator_definitions() -> None:
    """The config is the datasheet; assert it against the devices, not against a literal in this file."""
    config = Config.load(DEFAULT_CONFIG_PATH)
    for name, emulator_class in EMULATORS.items():
        spec = config.ammeter(name)
        assert spec.command.encode() == emulator_class(spec.port).get_current_command, (
            f"config command for {name} does not match {emulator_class.__name__}"
        )


def test_minimal_config_uses_defaults() -> None:
    config = Config.from_dict(MINIMAL)
    spec = config.ammeter("greenlee")
    assert (spec.host, spec.timeout_s, spec.expected_min_a, spec.reference_current_a) == ("localhost", 2.0, None, None)
    assert config.max_schedule_error_ms is None
    assert (config.max_failure_rate, config.simulated_failure_rate, config.plots_enabled) == (0.0, 0.0, True)
    assert config.results_dir == Path("results")
    assert config.retry_attempts == 1 and config.retry_budget_s == 0.0  # retrying is off unless asked for


def test_unknown_ammeter_lists_the_known_ones() -> None:
    with pytest.raises(ConfigError, match="unknown ammeter 'fluke'; configured: greenlee"):
        Config.from_dict(MINIMAL).ammeter("fluke")


@pytest.mark.parametrize(
    "broken, message",
    [
        ({"testing": {"sampling": {}}}, "'ammeters' section is missing"),
        (with_sections(ammeters={}), "'ammeters' section is empty"),
        (with_sections(ammeters={"a": {"command": "x"}}), "needs both 'port' and 'command'"),
        (with_sections(ammeters={"a": "5000"}), "must be a mapping"),
        (with_ammeter(port="abc"), "'port' must be int"),
        (with_ammeter(port=True), "'port' must be int"),
        (with_sections(ammeters={"bad/name": {"port": 1, "command": "x"}}), "may only contain"),
        (with_ammeter(port=70000), "port 70000 is out of range"),
        (with_ammeter(expected_range_a=[1]), "must be a \\[min, max\\] pair"),
        (with_ammeter(expected_range_a=[5, 1]), "expected range is reversed"),
        ({"ammeters": MINIMAL["ammeters"]}, "'testing' section is missing"),
        (with_sections(testing={"sampling": {}, "timeout_seconds": 0}), "timeout must be positive"),
        (with_sections(testing={"sampling": {}, "max_failure_rate": 2}), "must be between 0 and 1"),
        (with_sections(analysis=True), "'analysis' section is missing or not a mapping"),
        (with_sections(analysis={"visualization": {"enabled": "false"}}), "'enabled' must be bool"),
        (with_sections(analysis={"reference_current_a": 0}), "must not be zero"),
        (with_ammeter(reference_current_a=0), "reference current must not be zero"),
        (
            with_sections(testing={"sampling": {}, "max_schedule_error_ms": 0}),
            "'max_schedule_error_ms' must be positive",
        ),
        (with_sections(testing={"sampling": {}, "retry": {"attempts": 0}}), "'attempts' must be at least 1, got 0"),
        (with_sections(testing={"sampling": {}, "retry": {"attempts": "two"}}), "'attempts' must be int"),
        (
            with_sections(testing={"sampling": {}, "retry": {"backoff_seconds": -1}}),
            "'backoff_seconds' must not be negative",
        ),
    ],
)
def test_broken_configs_give_clear_errors(broken: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        Config.from_dict(broken)


def test_missing_and_invalid_files(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read config file"):
        Config.load(tmp_path / "nope.yaml")
    with pytest.raises(ConfigError, match="cannot read config file"):
        Config.load(tmp_path)
    bad = tmp_path / "bad.yaml"
    bad.write_text("ammeters: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid YAML"):
        Config.load(bad)
    bad.write_text("- just a list", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping at the top level"):
        Config.load(bad)
