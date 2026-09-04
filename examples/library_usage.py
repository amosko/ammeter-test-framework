"""Use the framework as a library: a custom sampling plan against one ammeter.

Start the emulators first (python main.py), then run: python examples/library_usage.py
"""

import dataclasses
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.testing.framework import AmmeterTestFramework  # noqa: E402
from src.testing.reporting import format_run  # noqa: E402
from src.utils.config import Config  # noqa: E402

config = Config.load(ROOT / "config" / "config.yaml")
config = dataclasses.replace(config, sample_count=20, frequency_hz=20, results_dir=ROOT / "results")

framework = AmmeterTestFramework(config)
result = framework.run_test("greenlee", label="library example")
print(format_run(result))

if result.statistics is not None:
    print(f"\nmean current: {result.statistics.mean:.4f} A over {result.statistics.count} readings")
