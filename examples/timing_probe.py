"""Measure the framework's wait against a single sleep: rows 2 and 3 of the timing table in docs/DESIGN.md.

Run: python examples/timing_probe.py [frequency_hz] [count]
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.testing import sampling  # noqa: E402
from src.testing.analysis import TimingStats  # noqa: E402
from src.testing.sampling import SamplingPlan, collect_samples  # noqa: E402


def single_sleep(deadline: float) -> None:
    remaining = deadline - time.perf_counter() - sampling.SPIN_WINDOW_S
    if remaining > 0:
        time.sleep(remaining)
    while time.perf_counter() < deadline:
        pass


def fake_measure() -> float:
    time.sleep(0.0015)  # a typical emulator round trip
    return 1.0


frequency = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
count = int(sys.argv[2]) if len(sys.argv) > 2 else 50
plan = SamplingPlan.resolve(count=count, frequency_hz=frequency)
framework_wait = sampling._wait_until

for name, wait in (("single sleep then spin", single_sleep), ("halving sleeps then spin", framework_wait)):
    sampling._wait_until = wait
    timing = TimingStats.from_samples(collect_samples(fake_measure, plan, "probe"))
    print(f"{name:26s} {frequency:g} Hz x {count}: max schedule error {timing.max_schedule_error_ms:.3f} ms")
sampling._wait_until = framework_wait
