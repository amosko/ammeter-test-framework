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
src/testing/framework.py AmmeterTestFramework: run_test(name) wires the pipeline together,
                         run_selected() samples several devices over one window
src/testing/reporting.py, visualization.py, cli.py   presentation only
```

The sampler only needs a `Callable[[], float]`, so anything that returns amperes (a serial device, a
mock, a fault injector wrapping a real ammeter) plugs in without touching the rest.

### Concurrency

`run_selected` gives each ammeter its own worker, so a three-ammeter run covers **one** wall clock window
instead of three consecutive ones. That is what makes `compare` defensible: readings taken in the same
window are commensurable, whereas three sequential windows silently absorb anything about the host that
changed between them, and the CV ranking then partly measures the host. It is also three times faster —
the shipped 50 samples at 10 Hz take about 5 s in total rather than 15.

It is safe *because* each ammeter is its own single-threaded server on its own port, so three workers
hitting three servers contend for nothing. Concurrency *within* one ammeter would serialise on that
server's accept loop and is not what this does. Results come back in the order given, not in completion
order, so `.result()` re-raises the first failure deterministically. A single-ammeter run skips the pool
entirely and behaves exactly as before. Plotting stays on the main thread after every worker has
finished: `pyplot` is a global state machine and is not thread-safe.

If one ammeter is unreachable the exception surfaces before anything is printed, so the console shows
only the error — but **no data is lost**. `run_test` archives each result before returning, so the runs
that did complete are on disk and `run_tests.py list` shows them.

**The scheduler interaction, measured.** `_wait_until` sleeps in halving steps and then busy-spins for
the last `SPIN_WINDOW_S` (2 ms off Windows). `time.sleep` releases the GIL; the spin does not. The three
workers do start together — their `collect_samples` start times were measured 0.12 ms apart — so their
deadlines coincide and their spin windows genuinely overlap. The cost is nevertheless small, because a
worker spins only until *its own* deadline and then blocks on the socket, releasing the GIL: the measured
spin is 0.118 ms at the median and never exceeded 2.4 ms, and a worker whose deadline has already passed
exits its spin immediately rather than adding to the queue. The worst case is therefore bounded by
(N − 1) × `SPIN_WINDOW_S`, which is 4 ms for three ammeters, still inside the 10 ms criterion.

Measured on macOS 26 / Python 3.9, shipped config (10 Hz, 50 samples), fresh process per run:

| Sampling                                     | Max schedule error | Runs |
|----------------------------------------------|--------------------|------|
| One ammeter alone (no pool)                  | 0.01 – 0.12 ms     | 5    |
| Three concurrently                           | 0.15 – 0.90 ms     | 15   |
| Three concurrently, all 12 cores saturated   | 0.01 – 0.80 ms     | 18   |

So concurrency costs roughly half a millisecond of schedule accuracy against a 10 ms criterion, and
holds up with the machine fully loaded. The GIL switch interval was left alone: shortening it to 0.5 ms
(the obvious mitigation) moved the median from 0.711 ms to 0.643 ms and made the maximum *worse*
(2.934 → 3.502 ms), so a process-global mutation was not worth its complexity.

The caveat is that the spin window is per thread, so N concurrent ammeters spin N times as much. That is
negligible at the shipped 10 Hz and worth knowing above roughly 100 Hz or with many more devices. At
100 Hz the tail grows for both modes — sequential reached 6.5 ms and concurrent 3.6 ms over 15 runs each
in one long-lived process — but that is dominated by GC pauses landing inside a 10 ms interval, which is
a pre-existing property of the sampler and not something concurrency introduced.

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

**Retries, and what must not be retried.** A single dropped TCP handshake would otherwise become a failed
sample, count against `max_failure_rate` and fail an otherwise healthy run — a test framework reporting a
transient blip as a device fault is producing false failures, the worst thing it can do. `Retrying` wraps
any `Measure` and retries `AmmeterConnectionError` and `AmmeterTimeoutError` with linear backoff
(attempt *n* waits *n* × backoff; exponential buys nothing inside an interval measured in tens of
milliseconds). `AmmeterProtocolError` is deliberately *not* retried: an unanswered or non-numeric reply
means a wrong command or a wrong port, which is deterministic and identical on every attempt, so retrying
burns the sampling budget and delays the moment the operator learns their config is wrong. Having three
typed transport errors rather than one generic client error is what makes that distinction expressible.

The layering is retry *inside* the transport, fault injection *outside* it: `make_measure` wraps
`Ammeter.measure` in `Retrying`, and `run_test` wraps the result in `FaultInjector`. `FaultInjector`
raises bare `AmmeterError`, which is not in `TRANSIENT_ERRORS`, so a simulated fault would not be retried
away even if the layers were reversed — but keeping the injector outermost makes the simulated failure
rate in the archived metadata mean exactly what it says, rather than "the rate before retries absorbed
some of it". Both retry settings go into the metadata too, because a run with retries enabled has
different failure semantics from one without and the two cannot otherwise be compared honestly.

Backoff sleeps *inside* a sample's slot, so `sampling_plan` rejects a retry budget that does not fit in
the sampling interval, before any device is touched: otherwise one failing sample pushes every later
sample late, the schedule collapses and `max_schedule_error_ms` fails the run for a reason that has
nothing to do with the device. The budget counts only the deliberate sleeping, not the socket timeout —
worst case per sample is `attempts × timeout + backoffs`, which at the shipped `timeout_seconds: 2.0`
would be 4 s and would reject every sane config; a device that burns its timeout on every attempt is
dead, and the connectivity pre-check already fails fast on that. A device that dies *mid-run* still
overruns its schedule, and that is correct: the run is reported FAIL with a reason, which is the
framework doing its job, so there is no cap.

The shipped `backoff_seconds` is 5 ms, not the 50 ms that retry conventions borrowed from remote services
would suggest. The failure being recovered from is a dropped handshake on localhost, where the whole
request round trip measures about 1.7 ms; 50 ms would consume half of the 100 ms shipped interval and cap
sampling just under 20 Hz, which would reject this repository's own documented
`--count 100 --frequency 20` example. At 5 ms the budget is 5% of the shipped slot and the check only
bites above 200 Hz.

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
- CV is a fair precision measure for the two well-behaved emulators but is dominated by outliers for
  Greenlee; a robust alternative (median absolute deviation) would be a one-line addition to
  `Statistics`.
