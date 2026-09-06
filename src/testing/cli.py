"""Command line interface: run sampling tests, then list, show and compare archived runs."""

import argparse
import contextlib
import dataclasses
import logging
import subprocess
import sys
import time
from collections.abc import Iterator, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from Ammeters.client import AmmeterConnectionError, AmmeterError, is_listening
from src.testing.framework import AmmeterTestFramework
from src.testing.reporting import format_comparison, format_listing, format_run, plural
from src.testing.results import RunResult
from src.testing.visualization import plot_comparison, plot_run
from src.utils.config import DEFAULT_CONFIG_PATH, AmmeterSpec, Config, ConfigError
from src.utils.logger import configure_logging

ROOT = Path(__file__).resolve().parents[2]
EMULATOR_START_TIMEOUT_S = 10


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = Config.load(args.config)
        if args.results_dir is not None:
            config = dataclasses.replace(config, results_dir=args.results_dir)
        log_dir = config.results_dir / "logs" if args.command == "run" else None
        configure_logging(log_dir, logging.DEBUG if args.verbose else logging.INFO)
        return int(args.handler(args, config))
    except (ConfigError, ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except AmmeterConnectionError as exc:
        hint = (
            ""
            if getattr(args, "start_emulators", False)
            else " Start the emulators (main.py) or pass --start-emulators."
        )
        print(f"error: {exc}.{hint}", file=sys.stderr)
        return 1
    except AmmeterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_tests.py", description="Ammeter measurement test framework.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="config file (default: %(default)s)")
    parser.add_argument("--results-dir", type=Path, help="results archive (default: from config)")
    parser.add_argument("-v", "--verbose", action="store_true", help="log every sample")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="sample one or more ammeters (default: all configured)")
    run.add_argument("ammeters", nargs="*", metavar="AMMETER", help="ammeter names from the config")
    run.add_argument("--count", type=int, help="number of measurements")
    run.add_argument("--duration", type=float, metavar="SECONDS", help="total test duration")
    run.add_argument("--frequency", type=float, metavar="HZ", help="sampling frequency")
    run.add_argument("--label", help="free text stored with the run")
    run.add_argument(
        "--reference", type=float, metavar="AMPERES", help="known reference current, enables accuracy metrics"
    )
    run.add_argument("--simulate-errors", type=float, metavar="RATE", help="fail this fraction of samples (0-1)")
    run.add_argument("--retry-attempts", type=int, metavar="N", help="attempts per sample (1 disables retrying)")
    run.add_argument("--seed", type=int, help="seed for error simulation")
    run.add_argument("--no-plot", action="store_true", help="skip the PNG plots")
    run.add_argument("--start-emulators", action="store_true", help="run main.py in the background for this run")
    run.set_defaults(handler=cmd_run)

    commands.add_parser("list", help="list archived runs").set_defaults(handler=cmd_list)

    show = commands.add_parser("show", help="print the report of an archived run")
    show.add_argument("run_id")
    show.set_defaults(handler=cmd_show)

    compare = commands.add_parser("compare", help="compare archived runs side by side")
    compare.add_argument("run_ids", nargs="*", metavar="RUN_ID")
    compare.add_argument("--latest", action="store_true", help="compare the latest run of every ammeter")
    compare.add_argument("--no-plot", action="store_true", help="skip the PNG plot")
    compare.set_defaults(handler=cmd_compare)
    return parser


def cmd_run(args: argparse.Namespace, config: Config) -> int:
    overrides = {
        "reference_current_a": args.reference,
        "simulated_failure_rate": args.simulate_errors,
        "retry_attempts": args.retry_attempts,
        "simulation_seed": args.seed,
        "plots_enabled": False if args.no_plot else None,
    }
    config = dataclasses.replace(config, **{k: v for k, v in overrides.items() if v is not None})
    config = dataclasses.replace(config, **_sampling_overrides(args))
    framework = AmmeterTestFramework(config)
    try:
        framework.sampling_plan()  # validate the sampling settings before touching any device
    except ValueError as exc:
        raise ValueError(f"{exc}{_sampling_hint(args)}") from None
    names = args.ammeters or list(config.ammeters)

    with _emulators(config, args.config, names) if args.start_emulators else contextlib.nullcontext():
        results = framework.run_selected(names, args.label)

    for result in results:  # reporting stays on the main thread: pyplot is a global, not thread-safe
        print(format_run(result))
        if config.plots_enabled:
            _print_plot(plot_run(result, framework.archive.path_for(result.run_id, ".png")))
        print()

    if len(results) > 1:
        _print_comparison(results, framework, config.plots_enabled)
    return 0 if all(r.verdict.passed for r in results) else 1


def cmd_list(args: argparse.Namespace, config: Config) -> int:
    print(format_listing(AmmeterTestFramework(config).archive.load_all()))
    return 0


def cmd_show(args: argparse.Namespace, config: Config) -> int:
    print(format_run(AmmeterTestFramework(config).archive.load(args.run_id)))
    return 0


def cmd_compare(args: argparse.Namespace, config: Config) -> int:
    framework = AmmeterTestFramework(config)
    if args.latest:
        results = framework.archive.latest_per_ammeter()
    else:
        results = [framework.archive.load(run_id) for run_id in args.run_ids]
    if not results:
        raise ValueError("nothing to compare: give run ids or --latest")
    _print_comparison(results, framework, config.plots_enabled and not args.no_plot)
    return 0


def _sampling_overrides(args: argparse.Namespace) -> dict[str, Any]:
    """Two flags define the plan on their own, so the third comes from the flags rather than the config.

    Without this the config's own value supplies a third, and any pair but the config's own becomes an
    inconsistent triple -- so `--duration 1 --frequency 20` would be rejected against a count nobody asked for.
    """
    given: dict[str, Any] = {"sample_count": args.count, "duration_s": args.duration, "frequency_hz": args.frequency}
    supplied = {name: value for name, value in given.items() if value is not None}
    return {name: supplied.get(name) for name in given} if len(supplied) >= 2 else supplied


def _sampling_hint(args: argparse.Namespace) -> str:
    flags = [
        f"--{n}" for n, v in (("count", args.count), ("duration", args.duration), ("frequency", args.frequency)) if v
    ]
    if len(flags) == 1:
        return f" ({flags[0]} was combined with the config's other sampling values; pass a second flag to replace one)"
    return ""


def _print_comparison(results: Sequence[RunResult], framework: AmmeterTestFramework, plot: bool) -> None:
    print(f"Comparison of {len(results)} {plural(len(results), 'run')}")
    print(format_comparison(results))
    if plot:
        path = framework.archive.path_for(f"{datetime.now():%Y%m%d_%H%M%S}_comparison", ".png")
        _print_plot(plot_comparison(results, path))


def _print_plot(path: Optional[Path]) -> None:
    if path is not None:
        print(f"  plot      {path}")


@contextlib.contextmanager
def _emulators(config: Config, config_path: Path, names: Sequence[str]) -> Iterator[None]:
    """Run main.py in the background until the block ends; its stdout is discarded, its stderr reported."""
    for spec in (config.ammeter(name) for name in names):
        if is_listening(spec.host, spec.port):
            raise AmmeterError(f"{spec.host}:{spec.port} is already served; drop --start-emulators to use it")
    command = [sys.executable, str(ROOT / "main.py"), "--config", str(config_path.resolve())]
    process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        for name in names:
            _wait_for_emulator(process, config.ammeter(name))
        yield
    finally:
        process.terminate()
        process.wait(timeout=5)


def _wait_for_emulator(process: "subprocess.Popen[str]", spec: AmmeterSpec) -> None:
    deadline = time.monotonic() + EMULATOR_START_TIMEOUT_S
    while not is_listening(spec.host, spec.port):
        if process.poll() is not None:
            stderr = process.stderr.read().strip() if process.stderr else ""
            raise AmmeterConnectionError(f"main.py exited with code {process.returncode}: {stderr}")
        if time.monotonic() > deadline:
            raise AmmeterConnectionError(f"{spec.host}:{spec.port} not reachable after {EMULATOR_START_TIMEOUT_S}s")
        time.sleep(0.05)
