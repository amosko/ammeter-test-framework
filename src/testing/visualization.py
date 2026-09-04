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
    if failed:
        ax_time.plot([s.measured_at_s for s in failed], [0] * len(failed), "rx", label="failed sample")
    stats = result.statistics
    if stats is not None:
        ax_time.axhline(stats.mean, color="C1", label=f"mean {stats.mean:.3g} A")
        ax_time.axhspan(
            stats.mean - stats.stdev, stats.mean + stats.stdev, color="C1", alpha=0.15, label="mean ± stdev"
        )
        ax_hist.hist(result.values, bins=min(20, max(5, len(ok) // 3)), color="C0")
    ax_time.set(xlabel="time [s]", ylabel="current [A]", title=f"{result.ammeter.name}: {result.run_id}")
    ax_time.legend(loc="upper right", fontsize="small")
    ax_hist.set(xlabel="current [A]", ylabel="samples", title="distribution")

    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def plot_comparison(results: Sequence[RunResult], path: Path) -> Optional[Path]:
    """Reading distribution per ammeter (log scale) and precision as CV."""
    plt = _pyplot()
    results = [r for r in results if r.statistics is not None]
    if plt is None or not results:
        return None
    names = [r.ammeter.name for r in results]
    fig, (ax_box, ax_cv) = plt.subplots(1, 2, figsize=(11, 4))

    ax_box.boxplot([r.values for r in results])
    ax_box.set_xticks(range(1, len(names) + 1), names)
    ax_box.set_yscale("log")
    ax_box.set(ylabel="current [A]", title="reading distribution (log scale)")

    cv = {r.ammeter.name: r.statistics.cv_percent for r in results if r.statistics and r.statistics.cv_percent}
    ax_cv.bar(list(cv), list(cv.values()), color="C1")
    ax_cv.set(ylabel="CV [%]", title="precision: coefficient of variation (lower is better)")

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
