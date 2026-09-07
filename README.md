# Ammeter Test Framework

[![CI](https://github.com/amosko/ammeter-test-framework/actions/workflows/ci.yml/badge.svg?branch=feature/ammeter-test-framework)](https://github.com/amosko/ammeter-test-framework/actions/workflows/ci.yml)

Emulators for three ammeters (Greenlee, ENTES, CIRCUTOR) and a test framework that samples them on a
precise schedule, analyses the readings, judges each run against pass/fail criteria, archives the results
and compares ammeters against each other.

Each emulator is a small TCP server: send its command, get one current reading back as text.
The framework treats the emulators as the devices under test and never imports their internals.

## Contents

- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Commands](#commands)
- [Configuration](#configuration)
- [Results](#results)
- [The emulators](#the-emulators)
- [Project structure](#project-structure)
- [Development](#development)

## Requirements

Python 3.9 or newer and two packages:

```sh
python3 -m pip install -r requirements.txt
```

| Library    | Used for                                    |
|------------|---------------------------------------------|
| PyYAML     | reading `config/config.yaml`                |
| matplotlib | optional, the PNG plots (skipped if absent) |

The commands below use `python3`, which is what the interpreter is called on macOS and most Linux
systems. On Windows it is usually `python`. Every commit is tested on Linux, macOS and Windows against
Python 3.9 and 3.13; see `.github/workflows/ci.yml`.

## Quick start

Start the emulators in one terminal. `main.py` starts every ammeter listed in the config on its port,
reads one value from each as a smoke test and keeps serving until Ctrl+C:

```sh
python3 main.py
```

Run the framework in a second terminal:

```sh
python3 run_tests.py run                          # sample every configured ammeter
python3 run_tests.py run greenlee --count 100 --frequency 20
python3 run_tests.py list                         # archived runs
python3 run_tests.py show 20260906_164010_entes_41918e70
python3 run_tests.py compare --latest             # latest run of every ammeter, side by side
```

Single-terminal alternative: `python3 run_tests.py run --start-emulators` starts `main.py` in the
background for the duration of the run.

The ammeters are sampled concurrently, one worker each, so a full run covers a single measurement window
rather than three consecutive ones — the shipped 50 samples at 10 Hz take about 5 seconds, not 15.
Reports print together once every ammeter has finished.

Progress goes to stderr as `[INFO]` lines; the report goes to stdout:

```
Run 20260906_164010_entes_41918e70  [PASS]
  ammeter   entes @ 127.0.0.1:5001  label: baseline
  samples   50/50 ok, 10 Hz, 4.9 s (scheduled 4.9 s)
  timing    max schedule error 0.52 ms, latency mean 1.61 ms / max 2.22 ms
  current   mean 76.52 A, median 73.06 A, stdev 40.12 A, min 12.82 A, max 178.2 A, CV 52.44%
  plot      results/20260906_164010_entes_41918e70.png
```

`compare` after a full run:

```
Comparison of 3 runs
ammeter   run_id                             mean [A]  median [A]  stdev [A]  CV %   failed/total  latency [ms]
--------  ---------------------------------  --------  ----------  ---------  -----  ------------  ------------
circutor  20260906_164010_circutor_aa9fba71  0.02907   0.02703     0.01357    46.69  0/50          1.45
entes     20260906_164010_entes_41918e70     76.52     73.06       40.12      52.44  0/50          1.61
greenlee  20260906_164010_greenlee_d74b38ba  0.4325    0.1066      1.266      292.6  0/50          2.04
Most consistent (lowest CV): circutor at 46.69%
Most reliable (lowest failure rate): 3 runs tied at 0% failed
```

## Commands

| Command                         | What it does                                                           |
|---------------------------------|------------------------------------------------------------------------|
| `run [AMMETER ...]`             | Sample the named ammeters (default: all) concurrently, report, archive |
| `list`                          | Table of archived runs                                                 |
| `show RUN_ID`                   | Report of one archived run                                             |
| `compare RUN_ID ... / --latest` | Ranks several runs by precision, reliability and accuracy, plus a plot |

`run` options: `--count N`, `--duration SECONDS`, `--frequency HZ` (any two), `--label TEXT`,
`--reference AMPERES` (known reference current, enables accuracy metrics), `--simulate-errors RATE` with
`--seed N` for a reproducible failure pattern, `--retry-attempts N`, `--no-plot`, `--start-emulators`.
Options shared by every command go before the command: `--config PATH`, `--results-dir PATH`, `--verbose`.

Exit code: 0 when every run passed, 1 when a run failed or an ammeter was unreachable, 2 for usage
and configuration errors, 130 when interrupted.

## Configuration

Everything lives in `config/config.yaml`; command line flags override it for one invocation.

```yaml
ammeters:
  greenlee:
    host: 127.0.0.1
    port: 5000
    command: "MEASURE_GREENLEE -get_measurement"
    expected_range_a: [0.01, 100]   # readings outside this range fail the run
    # reference_current_a: 1.0      # optional per-ammeter reference, overrides the global one

testing:
  sampling:
    measurements_count: 50
    total_duration_seconds: null    # any two of the three; the third is derived
    sampling_frequency_hz: 10
  timeout_seconds: 2.0              # per connect and per reply
  max_failure_rate: 0.05            # a run fails if more samples than this fail
  max_schedule_error_ms: 10         # or if a sample is taken this late; null disables the check
  retry:
    attempts: 2                     # attempts per sample; 1 disables retrying
    backoff_seconds: 0.005          # linear: attempt n waits n * backoff
  error_simulation:
    failure_rate: 0.0               # randomly fail this fraction of samples
    seed: null

analysis:
  reference_current_a: null         # known reference current, enables accuracy metrics
  visualization:
    enabled: true

result_management:
  directory: results
```

Sampling is defined by any two of count, duration and frequency: 50 samples at 10 Hz is a 5 s test with
one sample every 100 ms. Count alone means "as fast as possible". Two of `--count`, `--duration` and
`--frequency` define the plan on their own, so the third is derived rather than taken from the config;
one flag on its own combines with the config's other value.

A run passes when at most `max_failure_rate` of its samples failed, every reading is inside the
ammeter's expected range and no sample was taken later than `max_schedule_error_ms` after its schedule.

Transient transport failures (a refused connection, a timeout) are retried, so a single dropped handshake
does not count as a device fault. Protocol errors are never retried: an unanswered or non-numeric reply
means a wrong command or port, which is identical on every attempt.

Adding an ammeter is a config entry: name, host, port, command and optionally its expected range and
reference current.

## Results

Every run is archived as `results/<run_id>.json` and, unless plotting is off, a matching `<run_id>.png`
plot. The run id embeds the start time and the ammeter name plus a random suffix, so ids are unique
and sort chronologically:
`20260906_164010_entes_41918e70`.

The JSON holds the ammeter spec, the sampling plan, metadata (label, Python version, platform, the
criteria, the retry settings and the simulated failure rate), every sample (scheduled and actual time,
latency, value or error), the statistics (mean, median, sample standard deviation, min, max,
coefficient of variation), timing metrics, optional accuracy metrics and the verdict with its reasons.

`compare` ranks runs three ways: by coefficient of variation (standard deviation divided by mean), the
unit-free precision measure comparable across ammeters with very different current ranges; by failed
samples, which is reliability; and, when every run has a reference current, by mean absolute error.

The `results/` directory in this repository contains five sample runs (baseline for all three ammeters,
an error-simulation run and a reference-current run). `results/logs/` holds the log files of `run`
invocations and is not committed. A relative results directory is resolved from the current working
directory, so run the commands from the repository root.

## The emulators

| Ammeter  | Port | Command                                      | Method                           | Range       |
|----------|------|----------------------------------------------|----------------------------------|-------------|
| Greenlee | 5000 | `MEASURE_GREENLEE -get_measurement`          | Ohm's law, I = V / R             | 0.01-100 A  |
| ENTES    | 5001 | `MEASURE_ENTES -get_data`                    | Hall effect, I = B * K           | 5-200 A     |
| CIRCUTOR | 5002 | `MEASURE_CIRCUTOR -get_measurement -current` | Rogowski coil, I = integral V dt | 0.001-0.1 A |

The CIRCUTOR emulator requires the `-current` argument; the original README omitted it. An emulator that
receives an unknown command closes the connection without replying, which the framework reports as a
protocol error. The emulators print every request to their own terminal; that is original behaviour.
On macOS, port 5000 may be taken by AirPlay Receiver; change the port in the config.

## Project structure

```
main.py                   starts the emulators (config driven) and reads one value from each
run_tests.py              framework command line entry point
config/config.yaml        ammeters, sampling, criteria, archive location
Ammeters/                 the emulators (four small fixes, see docs/DESIGN.md) and the TCP client
src/testing/
  ammeter.py              unified Ammeter API, Retrying, FaultInjector (error simulation)
  sampling.py             SamplingPlan and scheduled sample collection
  analysis.py             statistics, timing, accuracy, verdict
  results.py              RunResult and the JSON archive
  framework.py            AmmeterTestFramework: run_test / run_selected / run_all
  reporting.py            text reports and comparison table
  visualization.py        matplotlib plots (optional)
  cli.py                  argparse commands
src/utils/                config loading and validation, logging setup
examples/                 the framework used as a library; the timing measurement behind the design notes
tests/                    pytest suite (unit, emulator integration and CLI tests)
results/                  sample results
docs/DESIGN.md            design decisions and the fixes made to the original code
```

## Development

```sh
python3 -m pip install -r requirements-dev.txt
python3 -m pytest            # 164 tests, about 6 seconds; starts emulators on free ports by itself
python3 -m ruff check .
python3 -m mypy main.py run_tests.py Ammeters src tests examples   # run on 3.9, the version it targets
```
