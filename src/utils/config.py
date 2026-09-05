"""Typed, validated access to config/config.yaml."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, TypeVar

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"

T = TypeVar("T")


class ConfigError(Exception):
    """The configuration is missing, malformed or inconsistent."""


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
    reference_current_a: Optional[float] = None  # known current for this ammeter, overrides the global one

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", self.name):
            raise ConfigError(f"ammeter name '{self.name}' may only contain letters, digits, '_', '-' and '.'")
        if not 1 <= self.port <= 65535:
            raise ConfigError(f"ammeter '{self.name}': port {self.port} is out of range")
        if self.timeout_s <= 0:
            raise ConfigError(f"ammeter '{self.name}': timeout must be positive")
        low, high = self.expected_min_a, self.expected_max_a
        if low is not None and high is not None and low > high:
            raise ConfigError(f"ammeter '{self.name}': expected range is reversed")
        if self.reference_current_a == 0:
            raise ConfigError(f"ammeter '{self.name}': reference current must not be zero")


@dataclass(frozen=True)
class Config:
    """Where the ammeters are and how to sample, judge and store runs. Validated on construction."""

    ammeters: dict[str, AmmeterSpec]
    sample_count: Optional[int]
    duration_s: Optional[float]
    frequency_hz: Optional[float]
    max_failure_rate: float
    max_schedule_error_ms: Optional[float]  # None disables the timing criterion
    retry_attempts: int  # total attempts per sample, not extra tries; 1 disables retrying
    retry_backoff_s: float  # linear: attempt n sleeps n * backoff before the next try
    simulated_failure_rate: float
    simulation_seed: Optional[int]
    reference_current_a: Optional[float]
    plots_enabled: bool
    results_dir: Path

    def __post_init__(self) -> None:
        for name, rate in (("max_failure_rate", self.max_failure_rate), ("failure_rate", self.simulated_failure_rate)):
            if not 0 <= rate <= 1:
                raise ConfigError(f"'{name}' must be between 0 and 1, got {rate}")
        if self.reference_current_a == 0:
            raise ConfigError("'reference_current_a' must not be zero")
        if self.max_schedule_error_ms is not None and self.max_schedule_error_ms <= 0:
            raise ConfigError("'max_schedule_error_ms' must be positive")
        if self.retry_attempts < 1:
            raise ConfigError(f"'attempts' must be at least 1, got {self.retry_attempts}")
        if self.retry_backoff_s < 0:
            raise ConfigError(f"'backoff_seconds' must not be negative, got {self.retry_backoff_s}")

    @property
    def retry_budget_s(self) -> float:
        """Worst-case time spent sleeping between retries for one sample."""
        return sum(self.retry_backoff_s * n for n in range(1, self.retry_attempts))

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
        simulation = _section(testing, "error_simulation", required=False)
        retry = _section(testing, "retry", required=False)
        analysis = _section(data, "analysis", required=False)
        visualization = _section(analysis, "visualization", required=False)
        results = _section(data, "result_management", required=False)

        ammeters = _section(data, "ammeters")
        if not ammeters:
            raise ConfigError("'ammeters' section is empty")
        timeout_s = _value(testing, "timeout_seconds", float, 2.0)

        return cls(
            ammeters={name: _parse_ammeter(name, entry, timeout_s) for name, entry in ammeters.items()},
            sample_count=_optional(sampling, "measurements_count", int),
            duration_s=_optional(sampling, "total_duration_seconds", float),
            frequency_hz=_optional(sampling, "sampling_frequency_hz", float),
            max_failure_rate=_value(testing, "max_failure_rate", float, 0.0),
            max_schedule_error_ms=_optional(testing, "max_schedule_error_ms", float),
            retry_attempts=_value(retry, "attempts", int, 1),  # 1 by default: never change failure semantics
            retry_backoff_s=_value(retry, "backoff_seconds", float, 0.005),
            simulated_failure_rate=_value(simulation, "failure_rate", float, 0.0),
            simulation_seed=_optional(simulation, "seed", int),
            reference_current_a=_optional(analysis, "reference_current_a", float),
            plots_enabled=_value(visualization, "enabled", bool, True),
            results_dir=Path(_value(results, "directory", str, "results")),
        )


def load_config(path: Path) -> dict[str, Any]:
    """Load the raw YAML mapping."""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except OSError as exc:
        raise ConfigError(f"cannot read config file: {exc}") from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    return data


def _section(data: dict[str, Any], key: str, required: bool = True) -> dict[str, Any]:
    section = data.get(key)
    if section is None and not required:
        return {}
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
    if isinstance(raw, bool) != (convert is bool):  # YAML true/false is not a number, and "false" is not a bool
        raise ConfigError(f"'{key}' must be {convert.__name__}, got {raw!r}")
    try:
        return convert(raw)
    except (TypeError, ValueError):
        raise ConfigError(f"'{key}' must be {convert.__name__}, got {raw!r}") from None


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
        host=_value(entry, "host", str, "localhost"),
        port=port,
        command=command,
        timeout_s=timeout_s,
        expected_min_a=expected_min,
        expected_max_a=expected_max,
        reference_current_a=_optional(entry, "reference_current_a", float),
    )
