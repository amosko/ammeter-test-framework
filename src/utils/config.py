"""Typed, validated access to config/config.yaml."""
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

import yaml

DEFAULT_CONFIG_PATH = Path("config/config.yaml")

T = TypeVar("T")


class ConfigError(Exception):
    """The configuration file is missing, malformed or incomplete."""


@dataclass(frozen=True)
class AmmeterSpec:
    """How to reach one ammeter and which readings it is expected to produce."""

    name: str
    host: str
    port: int
    command: str
    timeout_s: float = 2.0
    expected_min_a: Optional[float] = None
    expected_max_a: Optional[float] = None


@dataclass(frozen=True)
class Config:
    ammeters: dict[str, AmmeterSpec]
    sample_count: Optional[int]
    duration_s: Optional[float]
    frequency_hz: Optional[float]
    max_failure_rate: float
    simulated_failure_rate: float
    simulation_seed: Optional[int]
    reference_a: Optional[float]
    plots_enabled: bool
    results_dir: Path

    def ammeter(self, name: str) -> AmmeterSpec:
        try:
            return self.ammeters[name]
        except KeyError:
            raise ConfigError(f"unknown ammeter '{name}'; configured: {', '.join(self.ammeters)}") from None

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG_PATH) -> "Config":
        return cls.from_dict(load_config(path))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        testing = _section(data, "testing")
        sampling = _section(testing, "sampling")
        simulation = testing.get("error_simulation") or {}
        analysis = data.get("analysis") or {}
        visualization = analysis.get("visualization") or {}
        results = data.get("result_management") or {}

        timeout_s = _value(testing, "connection_timeout_seconds", float, 2.0)
        ammeters = _section(data, "ammeters")
        if not ammeters:
            raise ConfigError("'ammeters' section is empty")

        return cls(
            ammeters={name: _parse_ammeter(name, entry, timeout_s) for name, entry in ammeters.items()},
            sample_count=_optional(sampling, "measurements_count", int),
            duration_s=_optional(sampling, "total_duration_seconds", float),
            frequency_hz=_optional(sampling, "sampling_frequency_hz", float),
            max_failure_rate=_value(testing, "max_failure_rate", float, 0.0),
            simulated_failure_rate=_value(simulation, "failure_rate", float, 0.0),
            simulation_seed=_optional(simulation, "seed", int),
            reference_a=_optional(analysis, "reference_current_a", float),
            plots_enabled=_value(visualization, "enabled", bool, True),
            results_dir=Path(_value(results, "directory", str, "results")),
        )


def load_config(path: Path) -> dict[str, Any]:
    """Load the raw YAML mapping."""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    return data


def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    section = data.get(key)
    if not isinstance(section, dict):
        raise ConfigError(f"'{key}' section is missing or not a mapping")
    return section


def _optional(section: dict[str, Any], key: str, convert: Callable[[Any], T]) -> Optional[T]:
    raw = section.get(key)
    return None if raw is None else _convert(key, raw, convert)


def _value(section: dict[str, Any], key: str, convert: Callable[[Any], T], default: T) -> T:
    raw = section.get(key)
    return default if raw is None else _convert(key, raw, convert)


def _convert(key: str, raw: Any, convert: Callable[[Any], T]) -> T:
    try:
        return convert(raw)
    except (TypeError, ValueError):
        raise ConfigError(f"'{key}' must be a {convert.__name__}, got {raw!r}") from None


def _parse_ammeter(name: str, entry: Any, timeout_s: float) -> AmmeterSpec:
    if not isinstance(entry, dict):
        raise ConfigError(f"ammeter '{name}' must be a mapping with 'port' and 'command'")
    port = _optional(entry, "port", int)
    command = _optional(entry, "command", str)
    if port is None or command is None:
        raise ConfigError(f"ammeter '{name}' needs both 'port' and 'command'")

    expected_range = entry.get("expected_range_a")
    if expected_range is None:
        expected_min = expected_max = None
    elif isinstance(expected_range, list) and len(expected_range) == 2:
        expected_min, expected_max = (_convert("expected_range_a", v, float) for v in expected_range)
    else:
        raise ConfigError(f"ammeter '{name}': 'expected_range_a' must be a [min, max] pair")

    return AmmeterSpec(
        name=name,
        host=str(entry.get("host", "localhost")),
        port=port,
        command=command,
        timeout_s=timeout_s,
        expected_min_a=expected_min,
        expected_max_a=expected_max,
    )
