# Design notes

## Contents

- [Architecture](#architecture)
  - [Concurrency](#concurrency)
- [Decisions](#decisions)
- [Accuracy assessment](#accuracy-assessment)
- [Testing the framework](#testing-the-framework)
- [Fixes to the original code](#fixes-to-the-original-code)
- [Libraries installed](#libraries-installed)
- [Cross-platform notes](#cross-platform-notes)
- [Extending](#extending)
- [Not done, on purpose](#not-done-on-purpose)

## Architecture

The framework is a pipeline of small modules, each with one job, so any of them can be replaced or reused
on its own:

```
Ammeters/client.py       transport: one TCP request, typed errors (connection / timeout / protocol)
src/testing/ammeter.py   unified API: Ammeter.measure() -> amperes, Retrying for transient transport
                         failures, FaultInjector for error simulation
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

`run_selected` gives each ammeter its own worker, so a three-ammeter run covers one wall clock window
instead of three consecutive ones. That is what makes `compare` defensible: readings taken in the same
window are commensurable, whereas three sequential windows silently absorb anything about the host that
changed between them. It is also three times faster — 50 samples at 10 Hz take about 5 s, not 15.

It is safe because each ammeter is its own single-threaded server on its own port, so the workers contend
for nothing; concurrency *within* one ammeter would serialise on that server's accept loop. Results come
back in the order given rather than completion order, so the first failure re-raises deterministically.
Every device is checked for reachability before the pool starts, since the pool would otherwise hide an
unreachable one behind a full sampling window of the healthy devices. Plotting stays on the main thread
after every worker has finished: `pyplot` is a global state machine and is not thread-safe. `SIGINT`
reaches only the main thread, so Ctrl+C sets an event the samplers check; a cancelled run raises before
it reaches the archive, because a cancelled run is not a result.

`_wait_until` busy-spins for the last `SPIN_WINDOW_S` (2 ms off Windows), and a spinning thread
holds the GIL for a whole switch interval at a time, so workers whose deadlines coincide contend. A
worker spins only until its own deadline and then blocks on the socket, which bounds the worst case at
(N − 1) × `SPIN_WINDOW_S`: 4 ms for three ammeters where that window is 2 ms, inside the 10 ms
criterion. The window is ten times larger on Windows, which changes the arithmetic — see cross-platform
notes. Measured on macOS 26 / Python 3.9 with the shipped
config, fresh process per run:

| Sampling                    | Max schedule error | Runs |
|-----------------------------|--------------------|------|
| One ammeter alone (no pool) | 0.01 – 0.12 ms     | 5    |
| Three concurrently          | 0.15 – 0.90 ms     | 15   |

## Decisions

**The emulators are the devices under test.** The framework talks to them over TCP using the commands
from the config and never imports their classes; only `main.py` (the "power on" script) and the tests
instantiate them. The config is the datasheet: when the README and the emulator disagreed on the CIRCUTOR
command, the config was corrected to what the device actually accepts, the emulator was not changed.

**Config driven, CLI overridable.** `config/config.yaml` holds the ammeters, sampling, pass criteria,
retries, error simulation and archive location. `Config` is a frozen dataclass validated on construction, so a
bad value is rejected with a clear message whether it comes from YAML or from a command line override
(`dataclasses.replace`). Bool/number confusion (`port: true`, `enabled: "false"`) is rejected
explicitly, since YAML happily turns `true` into `1`.

**Sampling: any two of count, duration and frequency.** 50 samples at 10 Hz is a 5 s test with one
sample every 100 ms; sample *i* is scheduled at `i * interval`, so the last one is at 4.9 s. Count alone
means unpaced sampling, which is judged on failures and range but not on schedule: every unpaced sample
is scheduled at zero, so a "schedule error" there would just be the elapsed time, and the report omits
it. Giving three inconsistent values is an error rather than a silent choice. On the command line two
flags define the plan and the third is derived, since otherwise the config would supply a third value
and every pair but the config's own would be rejected as an inconsistent triple.

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

**Retries, and what must not be retried.** A single dropped TCP handshake would otherwise become a
failed sample and count against `max_failure_rate`. `Retrying` wraps any `Measure` and retries
`AmmeterConnectionError` and `AmmeterTimeoutError` with linear backoff. `AmmeterProtocolError` is
deliberately *not* retried: an unanswered or non-numeric reply means a wrong command or a wrong port,
which is identical on every attempt, so retrying only delays the report of a configuration fault. Retry
sits inside the transport and fault injection outside it, so the simulated failure rate in the archived
metadata means what it says; both retry settings are archived too, since a retried run has different
failure semantics from one without.

Backoff sleeps inside a sample's slot, so `sampling_plan` rejects a budget that does not fit in the
sampling interval before any device is touched — otherwise one failing sample pushes every later sample
late and fails the timing criterion for a reason unrelated to the device. The shipped `backoff_seconds`
is 5 ms rather than the conventional 50 ms: the failure being recovered from is a dropped handshake on
localhost with a round trip of about 1.7 ms, and 50 ms would consume half the shipped interval and cap
sampling below 20 Hz. At 5 ms the check bites only at 200 Hz and above.

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
| `Ammeters/base_ammeter.py` | `random.seed(time.time())` in `__init__` reseeded the *global* RNG all three emulators draw from, so devices constructed in the same millisecond could share a stream | Removed; CPython seeds `random` from `os.urandom` at import. An injectable seed would still write to the global module; reproducibility is available via `make_measure` or `FaultInjector(seed=...)` |
| `Ammeters/Greenlee_Ammeter.py` | The reading was printed with a Greek capital omega for ohms, which the default Windows console encoding (cp1252) cannot encode, so `measure_current` raised `UnicodeEncodeError` after matching the command but before replying. Every request on Windows hung until the client's timeout; found by CI, not by reading | Prints `ohm`. One string, no change to the measurement |
| `config/config.yaml` | Also this solution's own, not an original defect: the original file had the whole `ammeters:` block commented out and no `host` key. This solution added `host: localhost`, but the emulators bind an `AF_INET` socket and serve `127.0.0.1` only. On Windows `localhost` resolves to `::1` first, so every request burned its full timeout on IPv6 before falling back; a five-sample run took 10 s | The config names the address the device actually serves. Found by the Windows CI job |
| `Ammeters/client.py` | Not an original defect but recorded for honesty: this solution's own first transport rewrite put connecting and reading in one `try`, so a connect that timed out became `AmmeterTimeoutError`. POSIX refuses a dead port and Windows drops it, so one fault had two types depending on the platform, and only one of them earned the CLI's "start the emulators" hint | Connect failures are connection errors whether refused or timed out; only a connected but silent device is a timeout. Found by the Windows CI job, not by reading |
| `Ammeters/base_ammeter.py` | An unhandled `ConnectionResetError` in the accept loop ended the serving thread, so one client that vanished took the ammeter offline for the rest of the run. `is_listening` connects and closes immediately, and Windows answers a close on a backlogged connection with an RST where POSIX sends FIN, so `--start-emulators` failed there intermittently | The per-connection block catches `Exception` and keeps serving, which also covers a `measure_current` that raises -- the Greenlee omega above, handled structurally rather than at the string. Reproducible on any platform with `SO_LINGER 0`, which is what the test uses |
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

Pure standard library networking and timing, `pathlib` paths, `matplotlib` in headless (`Agg`) mode. Run
ids contain no character Windows forbids in a filename, and the archive is moved into place with
`os.replace`, which is atomic on both. The schedule error reported per run shows the real effect on any
host. Every commit runs the suite, ruff and mypy on ubuntu, macOS and Windows against Python 3.9 and
3.13, plus an end-to-end job that starts real emulators and drives the CLI twice on each
(`.github/workflows/ci.yml`).

That matrix was added because "ensure cross-platform compatibility" is a requirement and nothing here had
ever run on Windows. It found three defects that reading the code had not: one original, the omega in the
Greenlee print, and two this solution had introduced itself -- the IPv6 detour below and a connect
timeout reported as a reply timeout. None of them were
in the sockets-and-paths places one thinks to look.

Two things still differ by design:

- `SO_REUSEADDR` is set only on POSIX, because on Windows the flag lets two servers bind one port. The
  cost is that restarting `main.py` while old connections sit in TIME_WAIT binds cleanly on POSIX but can
  fail on Windows, where it surfaces as the emulator not becoming reachable.
- `SPIN_WINDOW_S` is 20 ms on Windows against 2 ms elsewhere, covering a `time.sleep` granularity of
  about 15 ms before Python 3.11. Concurrency multiplies it: the bound above becomes 40 ms for three
  ammeters rather than 4, and above 50 Hz the window exceeds the sampling interval, so every worker spins
  through its whole slot instead of sleeping. Forcing the 20 ms window on macOS only doubled the measured
  worst case, to 1.5 ms — far short of the bound and still inside the criterion — but that isolates the
  GIL contention without reproducing Windows' timer coarseness. Since 3.11 the window is 2 ms on Windows
  too, so this applies only to Python 3.9 and 3.10 there.

Addressing: the emulators create an `AF_INET` socket, so they serve `127.0.0.1` only, and the config
names that address rather than `localhost`. On Windows `localhost` resolves to `::1` first, so every
request spent its whole two-second timeout failing over IPv6 before falling back — a 5-sample run took
10 s instead of 40 ms. Naming what the device actually serves is the same correction the CIRCUTOR command
got.

Timer coarseness before Python 3.11 also runs in both directions: `time.sleep(0.01)` on Windows 3.9
returned after 8.8 ms, so tests that need a known duration spin rather than sleep.

The 10 ms timing criterion is a property of the host as much as the code: a shared CI runner stalls for
tens of milliseconds, and a single-ammeter run on a macOS runner measured 51 ms. That is the framework
reporting the truth about its host, so the criterion is unchanged and the CI jobs that are not about
timing disable it explicitly.

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
