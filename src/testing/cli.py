"""Command line interface: run sampling tests, then list, show and compare archived runs."""

import argparse
import contextlib
import dataclasses
import logging
import subprocess
import sys
from collections.abc import Iterator, Sequence
from datetime import datetime
from pathlib import Path
from typing import Optional

from Ammeters.client import AmmeterConnectionError, AmmeterError, wait_for_ammeter
from src.testing.framework import AmmeterTestFramework
from src.testing.reporting import format_comparison, format_listing, format_run
from src.testing.results import RunResult
from src.testing.visualization import plot_comparison, plot_run
from src.utils.config import DEFAULT_CONFIG_PATH, Config, ConfigError
from src.utils.logger import configure_logging

ROOT = Path(__file__).resolve().parents[2]


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = Config.load(args.config)
        if args.results_dir is not None:
            config = dataclasses.replace(config, results_dir=args.results_dir)
        configure_logging(config.results_dir / "logs", logging.DEBUG if args.verbose else logging.INFO)
        return int(args.handler(args, config))
    except (ConfigError, ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except AmmeterConnectionError as exc:
        print(f"error: {exc}\nStart the emulators with 'python main.py' or pass --start-emulators.", file=sys.stderr)
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
        "sample_count": args.count,
        "duration_s": args.duration,
        "frequency_hz": args.frequency,
        "reference_current_a": args.reference,
        "simulated_failure_rate": args.simulate_errors,
        "simulation_seed": args.seed,
        "plots_enabled": False if args.no_plot else None,
    }
    config = dataclasses.replace(config, **{k: v for k, v in overrides.items() if v is not None})
    framework = AmmeterTestFramework(config)
    framework.sampling_plan()  # validate the sampling settings before touching any device
    names = args.ammeters or list(config.ammeters)
    for name in names:
        config.ammeter(name)

    results: list[RunResult] = []
    with _emulators(config, args.config) if args.start_emulators else contextlib.nullcontext():
        for name in names:
            result = framework.run_test(name, args.label)
            results.append(result)
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


def _print_comparison(results: Sequence[RunResult], framework: AmmeterTestFramework, plot: bool) -> None:
    print(f"Comparison of {len(results)} runs")
    print(format_comparison(results))
    if plot:
        path = framework.archive.path_for(f"{datetime.now():%Y%m%d_%H%M%S}_comparison", ".png")
        _print_plot(plot_comparison(results, path))


def _print_plot(path: Optional[Path]) -> None:
    if path is not None:
        print(f"  plot      {path}")


@contextlib.contextmanager
def _emulators(config: Config, config_path: Path) -> Iterator[None]:
    """Run main.py quietly in the background until the block ends."""
    command = [sys.executable, str(ROOT / "main.py"), "--config", str(config_path.resolve())]
    process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for spec in config.ammeters.values():
            wait_for_ammeter(spec.host, spec.port, timeout_s=10)
        yield
    finally:
        process.terminate()
        process.wait(timeout=5)
