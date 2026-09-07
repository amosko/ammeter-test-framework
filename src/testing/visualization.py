"""Optional matplotlib plots. matplotlib is imported lazily so everything else works without it."""

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Optional

from src.testing.results import RunResult

logger = logging.getLogger(__name__)


def plot_run(result: RunResult, path: Path) -> Optional[Path]:
    """Time series with mean and one-sigma band, plus a histogram. Returns None if matplotlib is missing."""
    plt = _pyplot()
    if plt is None:
        return None
    fig, (ax_time, ax_hist) = plt.subplots(1, 2, figsize=(11, 4), gridspec_kw={"width_ratios": [2, 1]})

    ok = [s for s in result.samples if s.ok]
    failed = [s for s in result.samples if not s.ok]
    ax_time.plot([s.measured_at_s for s in ok], [s.value_a for s in ok], "o-", markersize=3, label="reading")
    if failed:  # drawn along the bottom edge (axis coordinates) so they never hide real readings
        bottom = ax_time.get_xaxis_transform()
        ax_time.plot(
            [s.measured_at_s for s in failed], [0.03] * len(failed), "rx", transform=bottom, label="failed sample"
        )
    stats = result.statistics
    if stats is not None:
        ax_time.axhline(stats.mean, color="C1", label=f"mean {stats.mean:.3g} A")
        low, high = max(0.0, stats.mean - stats.stdev), stats.mean + stats.stdev
        ax_time.axhspan(low, high, color="C1", alpha=0.15, label="mean ± stdev")
        ax_hist.hist(result.values, bins=min(20, max(5, len(ok) // 3)), color="C0")
    ax_time.set(xlabel="time [s]", ylabel="current [A]", title=f"{result.ammeter.name}: {result.run_id}")
    ax_time.legend(loc="best", fontsize="small")
    ax_hist.set(xlabel="current [A]", ylabel="samples", title="distribution")

    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def cv_bars(results: Sequence[RunResult]) -> list[tuple[str, float]]:
    """One entry per run, not per ammeter: several runs of one device are several bars."""
    return [
        (r.ammeter.name, r.statistics.cv_percent)
        for r in results
        if r.statistics is not None and r.statistics.cv_percent is not None
    ]


def plot_comparison(results: Sequence[RunResult], path: Path) -> Optional[Path]:
    """Reading distribution per ammeter (log scale) and precision as CV."""
    plt = _pyplot()
    results = [r for r in results if r.statistics is not None]
    if plt is None or not results:
        return None
    names = [r.ammeter.name for r in results]
    fig, (ax_box, ax_cv) = plt.subplots(1, 2, figsize=(11, 4.4))

    ax_box.boxplot([r.values for r in results])
    ax_box.set_xticks(range(1, len(names) + 1), names)
    ax_box.set_yscale("log")
    ax_box.set(ylabel="current [A]", title="reading distribution (log scale)")

    charted = cv_bars(results)
    ax_cv.bar(range(len(charted)), [cv for _, cv in charted], color="C1")
    ax_cv.set_xticks(range(len(charted)), [name for name, _ in charted])
    ax_cv.set(ylabel="CV [%]", title="precision: coefficient of variation (lower is better)")
    fig.suptitle("   ".join(r.run_id for r in results), fontsize=8)

    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def _pyplot() -> Any:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib is not installed, skipping plots")
        return None
    return plt
