# Ammeter Test Framework

Emulators for three ammeters (Greenlee, ENTES, CIRCUTOR) and a test framework that samples them on a
precise schedule, analyses the readings, judges each run against pass/fail criteria, archives the results
and compares ammeters against each other.

Each emulator is a small TCP server: send its command, get one current reading back as text.
The framework treats the emulators as the devices under test and never imports their internals.

## Requirements

Python 3.9 or newer and two packages:

```sh
pip install -r requirements.txt
```

| Library    | Used for                                    |
|------------|---------------------------------------------|
| PyYAML     | reading `config/config.yaml`                |
| matplotlib | optional, the PNG plots (skipped if absent) |

The commands below use `python`; on macOS and most Linux systems the interpreter is called `python3`
(and pip is `python3 -m pip`).

## Quick start

Start the emulators in one terminal. `main.py` starts every ammeter listed in the config on its port,
reads one value from each as a smoke test and keeps serving until Ctrl+C:

```sh
python main.py
```

Run the framework in a second terminal:

```sh
python run_tests.py run                          # sample every configured ammeter
python run_tests.py run greenlee --count 100 --frequency 20
python run_tests.py list                         # archived runs
python run_tests.py show 20260904_191148_entes_b5f5e2ef
python run_tests.py compare --latest             # latest run of every ammeter, side by side
```

Single-terminal alternative: `python run_tests.py run --start-emulators` starts `main.py` in the
background for the duration of the run.

Progress goes to stderr as `[INFO]` lines; the report goes to stdout:

```
Run 20260904_191148_entes_b5f5e2ef  [PASS]
  ammeter   entes @ localhost:5001  label: baseline
  samples   50/50 ok, 10 Hz, 4.9 s (scheduled 4.9 s)
  timing    max schedule error 0.01 ms, latency mean 1.68 ms / max 2.34 ms
  current   mean 73.7 A, median 74.07 A, stdev 39.78 A, min 8.064 A, max 165.3 A, CV 53.97%
  plot      results/20260904_191148_entes_b5f5e2ef.png
```

`compare` after a full run:

```
Comparison of 3 runs
ammeter   run_id                             mean [A]  median [A]  stdev [A]  CV %   failed/total  latency [ms]
--------  ---------------------------------  --------  ----------  ---------  -----  ------------  ------------
circutor  20260904_191153_circutor_06d39bb0  0.03054   0.02926     0.01499    49.1   0/50          1.74
entes     20260904_191148_entes_b5f5e2ef     73.7      74.07       39.78      53.97  0/50          1.68
greenlee  20260904_191142_greenlee_20796978  0.1649    0.1144      0.153      92.8   0/50          1.67
Most consistent (lowest CV): circutor at 49.1%
```

## Commands

| Command                         | What it does                                                          |
|---------------------------------|-----------------------------------------------------------------------|
| `run [AMMETER ...]`             | Sample the named ammeters (default: all), report, archive, plot        |
| `list`                          | Table of archived runs                                                 |
| `show RUN_ID`                   | Report of one archived run                                             |
| `compare RUN_ID ... / --latest` | Precision ranking of several runs, plus a comparison plot              |

`run` options: `--count N`, `--duration SECONDS`, `--frequency HZ` (any two), `--label TEXT`,
`--reference AMPERES` (known reference current, enables accuracy metrics), `--simulate-errors RATE` with
`--seed N` for a reproducible failure pattern, `--no-plot`, `--start-emulators`.
Options shared by every command go before the command: `--config PATH`, `--results-dir PATH`, `--verbose`.

Exit code: 0 when every run passed, 1 when a run failed or an ammeter was unreachable, 2 for usage
and configuration errors.

## Configuration

Everything lives in `config/config.yaml`; command line flags override it for one invocation.

```yaml
ammeters:
  greenlee:
    host: localhost
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
one sample every 100 ms. Count alone means "as fast as possible".

A run passes when at most `max_failure_rate` of its samples failed, every reading is inside the
ammeter's expected range and no sample was taken later than `max_schedule_error_ms` after its schedule.

Adding an ammeter is a config entry: name, host, port, command and optionally its expected range and
reference current.

## Results

Every run is archived as `results/<run_id>.json` with a matching `<run_id>.png` plot. The run id embeds the
start time and the ammeter name plus a random suffix, so ids are unique and sort chronologically:
`20260904_191148_entes_b5f5e2ef`.

The JSON holds the ammeter spec, the sampling plan, metadata (label, Python version, platform, the
criteria and the simulated failure rate), every sample (scheduled and actual time, latency, value or
error), the statistics (mean, median, sample standard deviation, min, max, coefficient of variation),
timing metrics, optional accuracy metrics and the verdict with its reasons.

`compare` ranks runs by coefficient of variation (standard deviation divided by mean), the unit-free
precision measure that can be compared across ammeters with very different current ranges. With a
reference current it also ranks by mean absolute error.

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
Ammeters/                 the emulators (three lines changed) and the TCP client
src/testing/
  ammeter.py              unified Ammeter API and FaultInjector (error simulation)
  sampling.py             SamplingPlan and scheduled sample collection
  analysis.py             statistics, timing, accuracy, verdict
  results.py              RunResult and the JSON archive
  framework.py            AmmeterTestFramework: run_test / run_all
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
pip install -r requirements-dev.txt
python -m pytest            # 90 tests, about 2 seconds; starts emulators on free ports by itself
python -m ruff check .
python -m mypy main.py run_tests.py Ammeters src tests examples
```
