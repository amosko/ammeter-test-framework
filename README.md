# Ammeter Test Framework

Emulators for three ammeters (Greenlee, ENTES, CIRCUTOR) and a test framework that samples them on a
precise schedule, analyses the readings, judges each run against pass/fail criteria, archives the results
and compares ammeters against each other.

Each emulator is a small TCP server: send its command, get one current reading back as text.
The framework treats the emulators as the devices under test and never imports their internals.

## Requirements

Python 3.9 or newer. Standard library only, plus:

```sh
pip install -r requirements.txt
```

| Library    | Used for                                   |
|------------|--------------------------------------------|
| PyYAML     | reading `config/config.yaml`               |
| matplotlib | optional, the PNG plots (skipped if absent) |

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
python run_tests.py show 20260904_183657_greenlee_3abf843d
python run_tests.py compare --latest             # latest run of every ammeter, side by side
```

Single-terminal alternative: `python run_tests.py run --start-emulators` starts `main.py` in the
background for the duration of the run.

Sample output of `run` for one ammeter:

```
Run 20260904_190213_entes_9f1c2a7b  [PASS]
  ammeter   entes @ localhost:5001  label: baseline
  samples   50/50 ok, 10 Hz, 4.90 s (scheduled 4.90 s)
  timing    max schedule error 0.03 ms, latency mean 1.55 ms / max 1.90 ms
  current   mean 75.02 A, median 65.02 A, stdev 44.11 A, min 15.32 A, max 181.7 A, CV 58.8%
  plot      results/20260904_190213_entes_9f1c2a7b.png
```

## Commands

| Command                         | What it does                                                          |
|---------------------------------|-----------------------------------------------------------------------|
| `run [AMMETER ...]`             | Sample the named ammeters (default: all), report, archive, plot        |
| `list`                          | Table of archived runs                                                 |
| `show RUN_ID`                   | Report of one archived run                                             |
| `compare RUN_ID ... / --latest` | Precision ranking of several runs, plus a comparison plot              |

`run` options: `--count N`, `--duration SECONDS`, `--frequency HZ` (give any two, see below),
`--label TEXT`, `--reference AMPERES` (enables accuracy metrics), `--simulate-errors RATE`,
`--seed N`, `--no-plot`, `--start-emulators`. Global options: `--config PATH`, `--results-dir PATH`,
`--verbose`.

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

testing:
  sampling:
    measurements_count: 50
    total_duration_seconds: null    # any two of the three; the third is derived
    sampling_frequency_hz: 10
  timeout_seconds: 2.0              # per connect and per reply
  max_failure_rate: 0.05            # a run fails if more samples than this fail
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

Adding an ammeter is a config entry: name, host, port, command and optionally its expected range.

## Results

Every run is archived as `results/<run_id>.json` with a matching `<run_id>.png` plot. The run id embeds the
start time and the ammeter name plus a random suffix, so ids are unique and sort chronologically:
`20260904_183657_greenlee_3abf843d`.

The JSON holds the ammeter spec, the sampling plan, metadata (label, Python version, platform, simulated
failure rate), every sample (scheduled and actual time, latency, value or error), the statistics (mean,
median, sample standard deviation, min, max, coefficient of variation), timing metrics, optional accuracy
metrics and the verdict with its reasons.

A run passes when at most `max_failure_rate` of its samples failed and every reading is inside the
ammeter's expected range.

`compare` ranks runs by coefficient of variation (standard deviation divided by mean), the unit-free
precision measure that can be compared across ammeters with very different current ranges. With
`--reference`, it also ranks by mean absolute error against the reference current.

The `results/` directory in this repository contains sample runs; `results/logs/` holds one log file per
`run` invocation and is not committed.

## The emulators

| Ammeter  | Port | Command                                      | Method                        | Range        |
|----------|------|----------------------------------------------|-------------------------------|--------------|
| Greenlee | 5000 | `MEASURE_GREENLEE -get_measurement`          | Ohm's law, I = V / R          | 0.01-100 A   |
| ENTES    | 5001 | `MEASURE_ENTES -get_data`                    | Hall effect, I = B * K        | 5-200 A      |
| CIRCUTOR | 5002 | `MEASURE_CIRCUTOR -get_measurement -current` | Rogowski coil, I = integral V dt | 0.001-0.1 A |

The CIRCUTOR emulator requires the `-current` argument; the original README omitted it. An emulator that
receives an unknown command closes the connection without replying, which the framework reports as a
protocol error. On macOS, port 5000 may be taken by AirPlay Receiver; change the port in the config.

## Project structure

```
main.py                   starts the emulators (config driven) and reads one value from each
run_tests.py              framework command line entry point
config/config.yaml        ammeters, sampling, criteria, archive location
Ammeters/                 the emulators (unchanged apart from two small fixes) and the TCP client
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
examples/library_usage.py the framework used as a library
tests/                    pytest suite (unit, emulator integration and CLI tests)
results/                  sample results
docs/DESIGN.md            design decisions and the fixes made to the original code
```

## Development

```sh
pip install -r requirements-dev.txt
python -m pytest            # 75 tests, about 2 seconds; starts emulators on free ports by itself
python -m ruff check .
python -m mypy main.py run_tests.py Ammeters src tests examples
```
