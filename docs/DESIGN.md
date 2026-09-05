# Design notes

## Architecture

The framework is a pipeline of small modules, each with one job, so any of them can be replaced or reused
on its own:

```
Ammeters/client.py       transport: one TCP request, typed errors (connection / timeout / protocol)
src/testing/ammeter.py   unified API: Ammeter.measure() -> amperes, FaultInjector for error simulation
src/testing/sampling.py  SamplingPlan + collect_samples(): timed collection, failures recorded per sample
src/testing/analysis.py  Statistics, TimingStats, AccuracyStats, evaluate() -> Verdict
src/testing/results.py   RunResult (everything about one run) + ResultsArchive (JSON per run)
src/testing/framework.py AmmeterTestFramework: run_test(name) wires the pipeline together
src/testing/reporting.py, visualization.py, cli.py   presentation only
```

The sampler only needs a `Callable[[], float]`, so anything that returns amperes (a serial device, a
mock, a fault injector wrapping a real ammeter) plugs in without touching the rest.

## Decisions

**The emulators are the devices under test.** The framework talks to them over TCP using the commands
from the config and never imports their classes; only `main.py` (the "power on" script) and the tests
instantiate them. The config is the datasheet: when the README and the emulator disagreed on the CIRCUTOR
command, the config was corrected to what the device actually accepts, the emulator was not changed.

**Config driven, CLI overridable.** `config/config.yaml` holds the ammeters, sampling, pass criteria,
error simulation and archive location. `Config` is a frozen dataclass validated on construction, so a
bad value is rejected with a clear message whether it comes from YAML or from a command line override
(`dataclasses.replace`). Bool/number confusion (`port: true`, `enabled: "false"`) is rejected
explicitly, since YAML happily turns `true` into `1`.

**Sampling: any two of count, duration and frequency.** 50 samples at 10 Hz is a 5 s test with one
sample every 100 ms; sample *i* is scheduled at `i * interval`, so the last one is at 4.9 s. Count alone
means unpaced sampling. Giving three inconsistent values is an error rather than a silent choice.

**Precise timing.** Each sample has an absolute deadline (`start + i * interval`), so sleep overshoot
cannot accumulate. The wait sleeps in halving steps and spins for the last 2 ms, because operating
systems oversleep: macOS lets a 100 ms sleep overshoot by up to 10 ms. Measured with
`examples/timing_probe.py` (macOS 26, Python 3.9) at 10 Hz, 50 samples, 1.5 ms request latency:

| Strategy                                     | Max schedule error      | Median   |
|----------------------------------------------|-------------------------|----------|
| Cumulative `sleep(interval)`                 | drifts 18% over the run | -        |
| Absolute deadline, single sleep, 2 ms spin   | 8.1 ms                  | 5.2 ms   |
| Absolute deadline, halving sleeps, 2 ms spin | 0.01 ms                 | 0.006 ms |

Every run records its own max schedule error and request latencies, so timing quality is part of the
result rather than an assumption, and `max_schedule_error_ms` makes it a pass/fail criterion. Spinning
costs 2 ms of CPU per sample (2% at 10 Hz) on macOS and Linux; on Windows before Python 3.11 the spin
window is 20 ms to cover the 15 ms sleep granularity.

**Error handling.** The transport maps every failure to one of three typed errors with a message that
says what to check. `run_test` takes one reading before the timed run so an unreachable device fails in
milliseconds with a hint, instead of after a full run of failures. During the run, failures are recorded
per sample and sampling continues, because intermittent failures are exactly what a test should
measure. The verdict fails a run when more than `max_failure_rate` of the samples failed, a reading is
outside the ammeter's expected range or a sample was taken too late, and the CLI exit code reflects it
(0 pass, 1 fail, 2 usage).

**Statistics from the standard library.** Mean, median, sample standard deviation (n-1), min, max and
coefficient of variation come from `statistics`; numpy, scipy and pandas were dropped because they
added nothing for a few dozen numbers. matplotlib is imported lazily and is optional.

**Results archive.** One JSON file per run named `<start time>_<ammeter>_<8 hex chars>` so ids are unique,
human readable and chronological when sorted. The file is self-contained: spec, plan, metadata, every
sample, statistics, timing, accuracy and verdict, and round-trips back into `RunResult` for `show` and
`compare`. It is written to `<run_id>.json.tmp` and moved into place with `os.replace`, so an interrupted
write cannot leave a half-parsed run in the archive; the temp name stays outside the `*.json` glob, so a
leftover from a crash can never be read back as a run. Files that are not run files are reported with
their path and skipped. Plots sit next to the JSON under the same id.

**Error simulation.** `FaultInjector` wraps any measure function and raises `AmmeterError` for a random
fraction of calls, seeded for reproducibility. It exercises the whole failure path (per-sample errors,
statistics on the survivors, verdict, exit code) without touching the emulators, and the simulated rate
is stored in the run's metadata so archived results stay honest.

## Accuracy assessment

There is no ground truth: the emulators draw random inputs, so accuracy in the metrological sense cannot
be measured. What can be compared across ammeter types is:

- precision: coefficient of variation, which is unit free and therefore comparable across devices whose
  currents differ by orders of magnitude,
- reliability: failed samples per run,
- responsiveness: request latency.

`compare` ranks runs by CV and prints all three. When a known reference current is available
(`--reference` for the whole setup, or `reference_current_a` per ammeter for calibrated devices), bias,
mean and max absolute error are computed and a second ranking by error is printed. Two observations
from the sample results: Greenlee's `V / R` with R down to 0.1 ohm is heavy tailed (CV of 100 to 300%
between runs), and CIRCUTOR's "integration" returns volt-seconds without a coil sensitivity, so its
values sit one to three orders of magnitude below the other two; comparing means across these devices
is meaningless without calibration, which is why the ranking uses CV.

## Testing the framework

`tests/` starts every emulator once per session on a free port, so the suite needs no running `main.py`
and never collides with the default ports. Protocol edge cases (silent device, garbage, empty and split
replies, refused connection) use a tiny one-shot fake server. CLI tests drive `main(argv)` directly and
cover `--start-emulators` end to end, including that the subprocess is stopped afterwards.

The shipped config's commands are asserted against `get_current_command` on the emulator classes rather
than against string literals in the test, so config drift fails the suite instead of surfacing as a
silent empty reply — which is exactly how the original defect presented. Its `expected_range_a` values
are pinned the same way, against 200 readings from each emulator.

## Fixes to the original code

| File | Problem | Fix |
|------|---------|-----|
| `main.py` | Ports 5001-5003 contradicted the README and config (5000-5002) | Ports and commands come from the config |
| `main.py` | Request commands lacked their arguments, so the emulators closed the connection without answering; nothing was ever read | Reads one value per ammeter with the configured command, so the smoke run validates the config |
| `main.py` | Startup used a fixed 5 s sleep; a second instance failed with a thread traceback | Waits until each server accepts connections; refuses a port that is already in use; keeps serving until Ctrl+C |
| `Ammeters/base_ammeter.py` | Restarting failed with "Address already in use" while old connections were in TIME_WAIT (the reason for the "increase sleep time" comment) | `SO_REUSEADDR` on POSIX; on Windows the flag would let two servers bind one port. Three lines changed |
| `Ammeters/base_ammeter.py` | `random.seed(time.time())` in `__init__` reseeded the *global* RNG that all three emulators draw from through `generate_random_float`, with a low-entropy value shared by emulators constructed in the same millisecond, so the three "independent" devices could restart the same stream | Removed; CPython seeds `random` from `os.urandom` at import, which is both stronger and per-process. An injectable seed was considered and rejected: it would still write to the global module and still stomp the other two. Reproducibility is available without touching the device, via the `make_measure` hook or `FaultInjector(seed=...)` |
| `Ammeters/client.py` | No timeout, so a silent device hangs forever; printed instead of returning the value; a single `recv` could return a truncated number | Timeout, typed errors, reads until the emulator closes, returns the float |
| `README.md` | CIRCUTOR command missing `-current`; referenced `AmmeterTester.py` and `run_test.py`, which do not exist | Rewritten |
| `config/config.yaml` | Every value null or commented out | Filled in, plus criteria and error simulation |
| `src/testing/test_framework.py` | `Dict` not imported (NameError on import); `run_test` had no body; the example called it without its argument | Replaced by `src/testing/framework.py` |
| `src/utils/logger.py` | Computed a log file name but never attached a handler, so nothing was logged anywhere | `configure_logging()` with console and lazily created file handlers |
| `src/utils/config.py` | Returned a raw dict, no validation | Typed `Config` / `AmmeterSpec` with validation; `load_config` kept |
| `examples/run_tests.py` | Broken (see above); README said not to use it | Replaced by `examples/library_usage.py` |
| `requirements.txt` | numpy, scipy, seaborn, pandas listed but unused | PyYAML and optional matplotlib |
| packages | `Ammeters/` and `src/` had no `__init__.py` | Added, plus `pyproject.toml` for pytest, ruff and mypy |

The original files use CRLF line endings; the ones that were only touched lightly keep them so the diff
shows the real change, new files use LF.

## Libraries installed

Runtime: PyYAML, matplotlib (optional). Development: pytest, mypy, ruff, types-PyYAML.

## Cross-platform notes

Pure standard library networking and timing, `pathlib` paths, `matplotlib` in headless (`Agg`) mode.
`time.sleep` granularity differs per platform (see timing above); the schedule error reported per run
shows the actual effect on any host.

## Extending

- New ammeter of an existing kind: add a config entry.
- New transport (serial, Modbus): subclass `AmmeterTestFramework` and override `make_measure(spec)` to
  return any callable that yields amperes and raises `AmmeterError` for a failed reading. Sampling,
  analysis, verdicts and the archive stay unchanged.
- New criteria or metrics: `analysis.py` is the only place to touch; `RunResult` serialises whatever
  dataclasses it holds.

## Not done, on purpose

- Error simulation has one mode (a failed reading). Simulated timeouts, garbage replies or latency
  spikes would follow the same wrapper pattern.
- Ammeters are sampled one after another; sampling several concurrently would need one thread per
  device and per-thread logging.
- CV is a fair precision measure for the two well-behaved emulators but is dominated by outliers for
  Greenlee; a robust alternative (median absolute deviation) would be a one-line addition to
  `Statistics`.
