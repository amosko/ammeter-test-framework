# Implementation brief: five changes to the ammeter test framework

You are working in an existing, working repository. The test suite passes, `ruff`
and `mypy --strict` are clean, and the README and `docs/DESIGN.md` accurately
describe the code. **All three of those things must still be true when you
finish.** A change that adds a feature but leaves the docs describing the old
behaviour is not done.

Work through the five changes in order and commit each one separately. Change 3
depends on change 2. Change 5 is the only one with a real chance of breaking
existing tests, and it is last for that reason.

---

## Ground rules

**Do not touch:**

- `Exam/` — the assignment text, kept as received.
- `Ammeters/Greenlee_Ammeter.py`, `Ammeters/Entes_Ammeter.py`,
  `Ammeters/Circutor_Ammeter.py` — the measurement logic is the device under
  test. The whole design rests on not editing it.
- The archived-run JSON schema, beyond adding keys inside `metadata`. Adding
  keys there is safe because `RunResult.from_dict` reads `metadata` as an opaque
  dict, so the five sample runs already in `results/` still load.

**Match the existing style.** It is consistent and deliberate:

- Python 3.9 target. Builtin generics (`dict[str, int]`, `list[Sample]`) are
  fine — PEP 585 landed in 3.9. `X | Y` unions are **not**; use
  `Optional[X]`.
- `mypy --strict`, so every function needs full annotations including
  `-> None` on `__init__` and on test functions.
- `ruff` with line-length 120 and the `E,F,I,B,UP,SIM` rule set. Imports are
  sorted; unused imports are errors.
- Frozen dataclasses for data, plain classes for behaviour.
- `logger = logging.getLogger(__name__)` at module top.
- Docstrings explain *why*, not what. One-line where one line does.

**Verify after every change:**

```sh
python -m pytest
python -m ruff check .
python -m mypy main.py run_tests.py Ammeters src tests examples
```

And once, manually, at the end:

```sh
python main.py                    # terminal 1
python run_tests.py run           # terminal 2 — run this twice in a row
```

Twice in a row matters: it is what proves the `SO_REUSEADDR` fix holds and that
nothing leaks a socket between runs.

---

## Assess the risk before each change, and be willing to decline one

**This is a standing instruction, not a preamble.** Do not start any of the five
changes below until you have thought through what it can break and decided the
requirement it serves is worth that exposure. If it is not, say so and skip it.
Skipping a change with a stated reason is a correct outcome here. Silently
half-doing one, or degrading something that already works in order to force one
through, is not.

### What is actually being graded

This repository is a submission for a hiring exercise. The spec asks for five
things — a unified measurement API, configurable sampling with precise timing,
statistical analysis, result management with retrieval and comparison, and
(bonus) accuracy assessment — and states five evaluation criteria: code quality
and structure, flexibility, comprehensive error handling, clear result
reporting, and potential for extension and reuse.

There is a sixth, unwritten requirement that outranks several of the others: a
reviewer clones the repository, follows the README, and it works on the first
try. A framework that fails on a reviewer's machine scores nothing for elegance.

### The asymmetry that should drive every judgement

**The repository already satisfies every core requirement.** None of the five
changes below fixes a gap; all five are improvements to something that already
works. That makes the risk profile lopsided: the upside of any single change is
a marginally better answer to a criterion already being met, while the downside
is breaking something currently demonstrated with evidence.

Concretely: change 5 buys a more defensible cross-ammeter comparison. Getting it
wrong damages the sub-millisecond schedule accuracy that `docs/DESIGN.md` proves
with a measured table, and that `max_schedule_error_ms` enforces as a pass
criterion. Those are not equal stakes. Weigh them that way.

### Four questions to answer before touching a file

1. **Which requirement does this serve, and how central is it?** A spec item, an
   evaluation criterion, or a bonus. Bonuses do not justify risk to spec items.
2. **What existing behaviour can it break, and would the break be loud or
   silent?** Loud is a failing test, a mypy error, a ruff error. Silent is wrong
   statistics, timing that degrades only under load, interleaved log lines, a
   flaky test that passes today. Silent failures deserve far more caution,
   because the reviewer finds them and you do not.
3. **Can it be verified by something that fails automatically?** If the only
   verification is "I read it and it looks right", either add a test that would
   fail without the change, or treat the change as higher risk than it looks.
4. **Is reverting clean?** One commit per change exists specifically so the
   answer is yes. Do not batch two changes into one commit; it destroys the
   cheapest available safety mechanism.

### Hard stops — do not work around these, report them

Stop and report back rather than proceeding if making a change work requires
any of:

- **Loosening a pass criterion** to make tests green — raising
  `max_schedule_error_ms`, raising `max_failure_rate`, widening an
  `expected_range_a`. The criteria are the product; relaxing one to accommodate
  new code inverts the purpose of the framework.
- **Deleting, skipping, or weakening an existing test**, or adding a
  `time.sleep` to stabilise a timing assertion.
- **Editing `Ammeters/*_Ammeter.py`** or changing the archived-run JSON schema
  beyond adding `metadata` keys.
- **A new `# type: ignore` or `# noqa`.** The repository has exactly one
  justified `per-file-ignores` entry and a few `# noqa: E402` in an example
  where the import order is forced by a path shim. Anything beyond that is a
  signal the change does not fit the design.
- **Softening a documentation claim** rather than updating it. If a change makes
  a DESIGN.md statement less true, the change is wrong, not the statement.

### Risk ranking of the five changes, with the call already made

| Change | Risk | Failure mode | Verdict |
|---|---|---|---|
| 1 — atomic writes | Very low | Loud: a broken write path fails its own new test immediately | Do it |
| 2 — remove RNG reseed | Low, but touches the device under test | Loud, but if an *existing* test starts failing, something depended on the seeding — stop and report rather than adapting the test | Do it, smallest possible diff |
| 3 — pin config to emulators | Very low, pure test addition | If it fails on the first run, that is a **real finding about the config**, not a broken test. Report the mismatch; do not edit the assertion to match | Do it |
| 4 — retry | Moderate | **Silent**: retries can mask a genuine device fault and make the framework report health the device does not have | Do it, with the protocol-error exclusion and the metadata provenance, both of which exist to contain exactly that |
| 5 — concurrency | Highest | **Silent and load-dependent**: schedule error degrades under GIL contention, possibly only on a slower machine than yours | Attempt it; abandon it if the measurements say so — see below |

### Specific abandon trigger for change 5

If, after applying mitigation option 1 (shortening the GIL switch interval),
the measured max schedule error under concurrent sampling is not comfortably
below the 10 ms criterion — call it 3 ms or better on an idle machine, since a
reviewer's machine may be busier than yours — **revert the commit.**

Then update the "Not done, on purpose" bullet in `docs/DESIGN.md` rather than
deleting it: sequential sampling is a deliberate choice, concurrency was
implemented and measured, and the schedule accuracy it cost was worth more than
the shared measurement window it bought. Include the numbers.

That is a better artifact than shipping a degraded scheduler. It demonstrates
exactly the judgement the evaluation criteria are looking for, and it is the
kind of thing an interviewer asks about.

### Report back

When you finish, include a short risk log alongside the summary of what changed:
which changes you judged risky and why, what you measured to resolve each one,
anything you declined or abandoned with the evidence behind it, and anything you
noticed but did not act on. If a hard stop fired, that goes at the top.

---

## Change 1 — Atomic archive writes

### Why

`ResultsArchive.save` writes JSON directly to its final path:

```python
path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
```

If the process dies mid-write — Ctrl+C, full disk, OOM — the run file is left
truncated. `load_all` already tolerates this by catching the parse error and
logging `skipping ...`, so the archive degrades rather than crashes. But the
corrupt file stays there forever, and `load(run_id)` on it fails for good.

Writing to a temp file and moving it into place means a run file is either
absent or complete. There is no intermediate state to tolerate.

### How

In `src/testing/results.py`:

1. Add `import os` (alphabetical: `json`, `logging`, `os`, `uuid`).
2. Rewrite `save`:

```python
def save(self, result: RunResult) -> Path:
    """Write the run atomically: readers see a complete file or none at all."""
    self.directory.mkdir(parents=True, exist_ok=True)
    path = self.path_for(result.run_id)
    tmp = path.parent / f"{path.name}.tmp"
    try:
        tmp.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
        os.replace(tmp, path)  # atomic within one filesystem, on POSIX and Windows
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path
```

Three details that matter:

- **`os.replace`, not `os.rename` or `Path.rename`.** On Windows, `rename`
  raises if the destination exists; `os.replace` overwrites. The framework
  never overwrites a run today (ids carry a uuid4 suffix), but a caller
  re-saving a loaded result would hit it, and the asymmetry is a trap.
- **The temp name is `<run_id>.json.tmp`, not `<run_id>.tmp`.** `load_all`
  globs `*.json`, and `foo.json.tmp` does not match that glob, so a leftover
  temp file can never be picked up as a run. Do not use
  `path.with_suffix(".tmp")` — that produces `<run_id>.tmp`, which is fine for
  the glob but loses the association if you ever debug a leftover.
- **`except BaseException`, not `except Exception`.** The case worth cleaning
  up after is Ctrl+C during a write, and `KeyboardInterrupt` is not an
  `Exception`.

### Tests

Add to `tests/test_results.py`:

```python
def test_a_failed_write_leaves_no_file_behind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = ResultsArchive(tmp_path)
    result = make_result([1.0])
    monkeypatch.setattr("src.testing.results.json.dumps", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        archive.save(result)
    assert list(tmp_path.iterdir()) == []


def test_saving_twice_replaces_the_file(tmp_path: Path) -> None:
    archive = ResultsArchive(tmp_path)
    result = make_result([1.0])
    archive.save(result)
    path = archive.save(result)
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert archive.load(path.stem) == result
```

Write the `monkeypatch` lambda as a proper local function if the generator-throw
trick trips ruff; readability wins.

### Docs

In `docs/DESIGN.md`, under **Results archive**, extend the existing sentence
about self-contained files with one clause: written via a temp file and
`os.replace`, so an interrupted write cannot leave a half-parsed run in the
archive.

### Commit

`Write archived runs atomically`

---

## Change 2 — Remove the emulator's global RNG reseed

### Why

`Ammeters/base_ammeter.py` still contains, from the original exercise:

```python
def __init__(self, port: int):
    self.port = port
    random.seed(time.time())  # Seed the random number generator for each instance
```

This is a planted defect and the current solution does not address it. Three
separate problems in one line:

1. **It seeds the global `random` module, not a per-instance generator.** All
   three emulators draw through `src/utils/Utils.generate_random_float`, which
   uses module-level `random`. So constructing an emulator resets the stream
   that the *other two* are already using.
2. **The seed is low-entropy and shared.** `main.py` constructs all three
   emulators inside a few microseconds. `time.time()` has millisecond
   resolution or worse on some platforms, so all three can receive the same
   seed and restart the same stream — the opposite of three independent
   devices, which is the premise the whole comparison rests on.
3. **It destroys better randomness.** CPython seeds `random` from
   `os.urandom` at import. Overwriting that with a wall-clock value is a
   downgrade, and it makes emulator output weakly predictable from the start
   time.

Deleting the line restores import-time entropy seeding, which is what you want
by default.

### How

In `Ammeters/base_ammeter.py`:

1. Delete the `random.seed(time.time())` line, leaving:

```python
def __init__(self, port: int):
    self.port = port
```

2. Delete `import random` and `import time` — after step 1 nothing in that file
   uses either, and ruff will flag them as F401. Keep `import os` and
   `import socket`; both are still used by `start_server`.

**Do not add an injectable `seed` parameter.** It is tempting (the other
solution did it), but it is an API change to the device under test that no
framework code would call, and it does not actually fix the problem — a seed
passed to `random.seed()` still lands on the global module and still stomps the
other emulators. If reproducible measurement streams are ever wanted, the
framework already has two clean routes that do not touch the emulators: the
`make_measure` override hook, and `FaultInjector`'s `seed`. Say this in the
DESIGN.md row so a reviewer sees the option was considered and rejected for a
reason.

### Tests

New file `tests/test_emulators.py`:

```python
import random

import pytest

from main import EMULATORS


@pytest.mark.parametrize("name", sorted(EMULATORS))
def test_constructing_an_emulator_does_not_disturb_the_global_rng(name: str) -> None:
    """The emulators share the module-level random; construction must not reseed it."""
    random.seed(1234)
    expected = [random.random() for _ in range(3)]

    random.seed(1234)
    EMULATORS[name](0)
    assert [random.random() for _ in range(3)] == expected


def test_emulators_produce_readings_in_their_documented_range() -> None:
    greenlee = EMULATORS["greenlee"](0)
    values = [greenlee.measure_current() for _ in range(200)]
    assert all(0.01 <= v <= 100 for v in values)
```

Constructing with port `0` binds nothing — `__init__` only stores the port.

The second test is optional but cheap, and it pins the `expected_range_a`
values in `config/config.yaml` to the actual emulator behaviour, which is the
same class of protection as change 3. Note it prints to stdout (the emulators
print every measurement); that is fine under pytest's capture.

### Docs

Add a row to the fixes table in `docs/DESIGN.md`:

| `Ammeters/base_ammeter.py` | `random.seed(time.time())` per instance reseeded the *global* RNG that all three emulators draw from, with a low-entropy value shared by emulators constructed in the same millisecond, so the three devices could produce correlated streams | Removed; CPython's `os.urandom` seeding at import is both stronger and per-process. An injectable seed was considered and rejected: it would still write to the global module. Reproducibility is available without touching the device, via `make_measure` or `FaultInjector(seed=...)` |

### Commit

`Stop the emulators reseeding the shared RNG on construction`

---

## Change 3 — Pin the config's commands against the emulators

### Why

The entire exercise turns on a command-string mismatch: the original `main.py`
sent `b'MEASURE_GREENLEE'`, the emulator compares against
`b'MEASURE_GREENLEE -get_measurement'`, so every request silently returned
nothing. The fix was to source commands from config. But nothing currently stops
`config/config.yaml` drifting away from the devices again.

`tests/test_config.py::test_shipped_config_matches_the_emulators` sounds like it
covers this, but it compares against **hardcoded string literals in the test**.
If someone edited the config and the test together, it passes. It asserts the
config matches the test author's belief, not the device.

The fix is to assert against `get_current_command` on the emulator classes
themselves, which is the actual protocol definition.

### How

Add to `tests/test_config.py` (keep the existing test; it still usefully pins
ports and sampling values):

```python
from main import EMULATORS


def test_shipped_config_commands_match_the_emulator_definitions() -> None:
    """The config is the datasheet; assert it against the devices, not against a literal."""
    config = Config.load(DEFAULT_CONFIG_PATH)
    for name, emulator_class in EMULATORS.items():
        spec = config.ammeter(name)
        assert spec.command.encode() == emulator_class(spec.port).get_current_command, (
            f"config command for {name} does not match {emulator_class.__name__}"
        )
```

`tests/conftest.py` already imports from `main`, so the import path is
established and no `sys.path` work is needed.

**This test is only safe after change 2.** Before it, constructing an emulator
inside a test reseeds the global RNG mid-suite, which is the exact defect change
2 removes — and would make any test that samples real readings after this one
subtly order-dependent. That dependency is worth one sentence in the commit
message; it is a nice illustration of why the seed line mattered.

### Docs

No doc change needed. Optionally, in `docs/DESIGN.md` under **Testing the
framework**, one clause: the shipped config's commands are asserted against the
emulator classes, so config drift fails the suite rather than surfacing as a
silent empty reply.

### Commit

`Assert the shipped config against the emulator definitions`

---

## Change 4 — Retry transient transport failures

### Why

Every sample currently gets exactly one attempt. A single dropped TCP handshake
turns into a failed sample, which counts against `max_failure_rate` and can fail
an otherwise healthy run. Real instrumentation is retried; a test framework that
reports a transient blip as a device fault produces false failures, which is the
worst thing a test framework can do.

The important design point — and the thing to make explicit in the code — is
**what must not be retried**. `AmmeterProtocolError` means the device closed the
connection without replying, or sent something that is not a number. That is a
wrong command string or a wrong port: deterministic, and identical on every
attempt. Retrying it burns the sampling budget and, worse, delays the moment the
operator learns their config is wrong. Only `AmmeterConnectionError` and
`AmmeterTimeoutError` are retried.

The framework's three typed errors already express this cleanly, which is why
this lands better here than in a design with one generic client error.

### How

**4a. `src/testing/ammeter.py`** — add a `Retrying` wrapper next to
`FaultInjector`. It composes over `Measure`, so it stacks with the injector and
with any custom transport.

```python
import logging
import time

from Ammeters.client import AmmeterConnectionError, AmmeterError, AmmeterTimeoutError, read_current

logger = logging.getLogger(__name__)

TRANSIENT_ERRORS = (AmmeterConnectionError, AmmeterTimeoutError)


class Retrying:
    """Retries transient transport failures.

    A protocol error is not retried: an unanswered or non-numeric reply means the wrong command or
    port, which is deterministic, so retrying only delays the report of a configuration fault.
    """

    def __init__(self, measure: Measure, attempts: int = 2, backoff_s: float = 0.05) -> None:
        if attempts < 1:
            raise ValueError(f"attempts must be at least 1, got {attempts}")
        if backoff_s < 0:
            raise ValueError(f"backoff must not be negative, got {backoff_s}")
        self._measure = measure
        self._attempts = attempts
        self._backoff_s = backoff_s

    def __call__(self) -> float:
        for attempt in range(1, self._attempts + 1):
            try:
                return self._measure()
            except TRANSIENT_ERRORS as exc:
                if attempt == self._attempts:
                    raise
                logger.debug("attempt %d/%d failed (%s), retrying", attempt, self._attempts, exc)
                time.sleep(self._backoff_s * attempt)
        raise AssertionError("unreachable")  # mypy: the loop always returns or raises
```

Backoff is linear (`attempt * backoff_s`) rather than exponential, deliberately:
the budget has to stay inside a sampling interval measured in tens of
milliseconds, and exponential backoff at that scale buys nothing.

**4b. `src/utils/config.py`** — two new fields on `Config`:

```python
retry_attempts: int          # total attempts per sample; 1 disables retrying
retry_backoff_s: float       # linear: attempt n sleeps n * backoff before the next try
```

Validate in `__post_init__` alongside the existing checks:

```python
if self.retry_attempts < 1:
    raise ConfigError(f"'attempts' must be at least 1, got {self.retry_attempts}")
if self.retry_backoff_s < 0:
    raise ConfigError(f"'backoff_seconds' must not be negative, got {self.retry_backoff_s}")
```

Add a helper property, because the budget is needed in two places:

```python
@property
def retry_budget_s(self) -> float:
    """Worst-case time spent sleeping between retries for one sample."""
    return sum(self.retry_backoff_s * n for n in range(1, self.retry_attempts))
```

Parse in `from_dict`, mirroring how `error_simulation` is read:

```python
retry = _section(testing, "retry", required=False)
...
retry_attempts=_value(retry, "attempts", int, 1),
retry_backoff_s=_value(retry, "backoff_seconds", float, 0.05),
```

**Default `attempts` to 1 in code** so any existing config behaves exactly as it
does today, and turn it on explicitly in the shipped config. The default should
never silently change a user's failure semantics.

**4c. `config/config.yaml`** — under `testing:`, after `max_schedule_error_ms`:

```yaml
  retry:
    attempts: 2                        # total attempts per sample, not extra tries; 1 disables retrying
    backoff_seconds: 0.05              # linear: attempt n waits n * backoff. Must fit in the sampling interval
```

**4d. `src/testing/framework.py`** — wire it into `make_measure`, so the retry
sits *inside* the transport and the fault injector stays outside it:

```python
def make_measure(self, spec: AmmeterSpec) -> Measure:
    """Override to use another transport; the callable must raise AmmeterError for a failed reading."""
    measure: Measure = Ammeter(spec).measure
    if self.config.retry_attempts > 1:
        measure = Retrying(measure, self.config.retry_attempts, self.config.retry_backoff_s)
    return measure
```

That ordering is the right one and worth a sentence in DESIGN.md.
`FaultInjector` raises bare `AmmeterError`, which `Retrying` does not catch, so
even if the layering were reversed a simulated fault would not be retried away —
but keeping the injector outermost makes the simulated failure rate mean exactly
what it says in the archived metadata, instead of "the rate before retries
absorbed some of them".

Note the knock-on effect: `run_test`'s connectivity pre-check calls
`measure()`, so it now retries too. On a refused localhost connection that costs
one 50 ms sleep, which does not meaningfully weaken "fail fast". Leave it —
routing the pre-check around the retry would mean duplicating transport
knowledge in the framework.

Add both values to the run metadata dict, next to the other criteria:

```python
"retry_attempts": self.config.retry_attempts,
"retry_backoff_s": self.config.retry_backoff_s,
```

Provenance matters here: a run with retries enabled has different failure
semantics from one without, and an archived result that does not say which it
was cannot be compared honestly against the other.

**4e. The budget check.** This is the part that stops the feature backfiring.

Retry backoff sleeps *inside* a sample's slot. If the budget is larger than the
sampling interval, one failing sample pushes every later sample late, the
schedule collapses, and `max_schedule_error_ms` fails the run — for a reason
that has nothing to do with the device. Catch it before any device is touched,
in `AmmeterTestFramework.sampling_plan`:

```python
def sampling_plan(self) -> SamplingPlan:
    plan = SamplingPlan.resolve(self.config.sample_count, self.config.duration_s, self.config.frequency_hz)
    budget = self.config.retry_budget_s
    if plan.interval_s and budget >= plan.interval_s:
        raise ConfigError(
            f"the retry budget of {budget * 1000:.0f} ms per sample does not fit in the "
            f"{plan.interval_s * 1000:.0f} ms sampling interval; lower testing.retry.attempts or "
            f"backoff_seconds, or sample more slowly"
        )
    return plan
```

Two things to be precise about:

- **The budget covers only the deliberate sleeping, not the socket timeout.**
  Worst case per sample is `attempts * timeout + backoffs`, which at the shipped
  `timeout_seconds: 2.0` would be 4 seconds and would reject every sane config.
  A device that actually burns its timeout on every attempt is dead, and
  `run_test`'s pre-check already fails fast on that before sampling starts.
  What this check bounds is the cost of the *recoverable* case.
- **A device that dies mid-run still overruns, and that is correct.** The
  schedule slips, `max_schedule_error_ms` trips, and the run is reported FAIL
  with a reason. That is the framework doing its job. Do not add a cap.

`ConfigError` (not `ValueError`) is deliberate: `cli.main` catches both and
returns exit code 2, but `cmd_run` wraps only `ValueError` in order to append
the `--count/--duration/--frequency` hint, which would be nonsense here.

**4f. CLI flag (optional but do it).** `cmd_run` already builds an overrides
dict, so it is two lines:

```python
run.add_argument("--retry-attempts", type=int, metavar="N", help="attempts per sample (1 disables retrying)")
```

```python
"retry_attempts": args.retry_attempts,
```

### Tests

New `tests/test_retry.py`:

```python
import pytest

from Ammeters.client import AmmeterConnectionError, AmmeterProtocolError, AmmeterTimeoutError
from src.testing.ammeter import Retrying


def flaky(failures: int, error: Exception) -> ...:
    """A measure callable that raises `error` the first `failures` times, then returns 1.0."""
```

Cover:

- succeeds on the second attempt after `AmmeterConnectionError`, and after
  `AmmeterTimeoutError`
- **does not retry `AmmeterProtocolError`** — assert the underlying callable was
  invoked exactly once. This is the important one.
- exhausting the attempts re-raises the last error, with its original type
- `attempts=1` calls through exactly once
- `attempts=0` and a negative backoff both raise `ValueError`
- backoff timing: with `attempts=3, backoff_s=0.01`, total elapsed is at least
  `0.01 + 0.02`. Use a generous lower-bound assertion only, never an upper
  bound — upper bounds on timing are how you get a flaky suite.

In `tests/test_config.py`: defaults are `attempts=1, backoff=0.05`; the shipped
config yields `attempts=2`; invalid values raise `ConfigError` naming the key.

In `tests/test_framework.py`: a config with `retry_attempts=4,
retry_backoff_s=1.0` against the 100 Hz test fixture raises `ConfigError`
mentioning "retry budget".

In `tests/test_cli.py`: that same config through `main([...])` returns 2.

### Docs

- `docs/DESIGN.md`, **Error handling**: transient transport failures are
  retried with linear backoff; protocol errors are not, because they are
  deterministic and retrying only delays the report of a bad command or port.
  State the layering (retry inside, fault injection outside) and why. State the
  budget check and the deliberate decision not to cap a mid-run overrun.
- `README.md`: add the `retry:` block to the configuration sample, and
  `--retry-attempts` to the `run` options line if you added the flag.

### Commit

`Retry transient transport failures, but never protocol errors`

---

## Change 5 — Sample the ammeters concurrently

### Why

`run_all` samples one ammeter after another, so a three-ammeter run at 10 Hz ×
50 samples takes about 15 seconds instead of 5, and — the substantive point —
the three readings are taken in three *different* five-second windows. The
comparison report puts them side by side as though they were commensurable. If
anything about the host changed between windows (load, thermal, another
process), the CV ranking silently absorbs it.

Sampling concurrently means all three devices are measured over the same wall
clock window, which is what makes `compare` defensible. `docs/DESIGN.md`
currently lists this under "Not done, on purpose"; that entry has to go when the
code changes.

This is safe *because* each ammeter is its own single-threaded server on its own
port. Three workers hitting three servers contend for nothing. Concurrency
*within* one ammeter would serialise on that server's accept loop and is not
what this change does.

### How

**5a. `src/testing/framework.py`** — add `run_selected` and reduce `run_all` to
a caller of it. The CLI needs to run an arbitrary subset (`run greenlee entes`),
so the subset form is the real API:

```python
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor

def run_selected(self, names: Sequence[str], label: Optional[str] = None) -> list[RunResult]:
    """Sample the named ammeters over the same window. Results come back in the order given."""
    for name in names:
        self.config.ammeter(name)  # fail on an unknown name before starting any thread
    if len(names) < 2:
        return [self.run_test(name, label) for name in names]
    with ThreadPoolExecutor(max_workers=len(names), thread_name_prefix="ammeter") as pool:
        futures = {name: pool.submit(self.run_test, name, label) for name in names}
    return [futures[name].result() for name in names]

def run_all(self, label: Optional[str] = None) -> list[RunResult]:
    return self.run_selected(list(self.config.ammeters), label)
```

Leaving the `with` block waits for every worker; `.result()` then re-raises the
first failure **in the order given**, not in completion order, so the error a
user sees is deterministic. The single-name path skips the pool entirely so a
one-ammeter run has no thread overhead and identical behaviour to today.

**5b. `src/testing/cli.py`** — `cmd_run` currently prints inside the sampling
loop. Split it: sample first, then report.

```python
    with _emulators(config, args.config, names) if args.start_emulators else contextlib.nullcontext():
        results = framework.run_selected(names, args.label)

    for result in results:
        print(format_run(result))
        if config.plots_enabled:
            _print_plot(plot_run(result, framework.archive.path_for(result.run_id, ".png")))
        print()
```

Delete the now-redundant `for name in names: config.ammeter(name)` loop above it
— `run_selected` does that check.

**Plotting must stay on the main thread.** matplotlib's `pyplot` interface is a
global state machine and is not thread-safe; calling `plot_run` from inside a
worker will eventually produce corrupted or interleaved figures. The structure
above keeps it out of the pool. Do not "optimise" it back in.

Two behaviour changes to accept and document:

- Reports now appear together at the end rather than streaming per ammeter.
  Progress is still visible: `run_test` logs `[INFO] entes: taking 50 samples at
  10 Hz` to stderr as each worker starts.
- If one ammeter is unreachable, `.result()` raises before anything is printed,
  so the console shows only the error. **No data is lost** — `run_test` archives
  each result before returning, so the runs that completed are on disk and
  `run_tests.py list` shows them. Say exactly that in DESIGN.md; it turns an
  apparent regression into a documented property.

**5c. `src/testing/sampling.py`** — `collect_samples` logs
`"sample %d failed: %s"` with no device name. Interleaved across three threads
that is unreadable. Add the name:

```python
def collect_samples(measure: Measure, plan: SamplingPlan, name: str) -> list[Sample]:
```

and use it in both log calls (`"%s sample %d failed: %s"`,
`"%s sample %d: %.4g A in %.2f ms"`). Update the call in `framework.run_test` to
pass `spec.name`, and update the direct calls in `tests/test_sampling.py`. Make
it a required parameter rather than defaulting it — a default would let a future
call site silently reintroduce anonymous log lines.

### 5d. The scheduler interaction — read this before you run the suite

This is the one thing likely to break, and the reason this change is last.

`_wait_until` sleeps in halving steps and then **busy-spins** for the final
`SPIN_WINDOW_S` (2 ms off Windows). `time.sleep` releases the GIL; the spin loop
does not. Three workers running the same plan share the same start time and the
same interval, so **their deadlines coincide exactly** — all three enter the
spin at the same moment and fight for the GIL. CPython switches threads every
`sys.getswitchinterval()`, 5 ms by default, which is larger than the 2 ms spin
window. A thread can therefore be descheduled at its deadline and resume several
milliseconds late.

`max_schedule_error_ms: 10` is a pass criterion, and `tests/conftest.py` runs
the fixture at **100 Hz — a 10 ms interval**. So
`test_run_all_covers_every_configured_ammeter`, which asserts every run passes,
is exactly the test that will expose this.

**Measure before changing anything.** Run the suite; run
`python run_tests.py run` against the real config and read the reported
`max schedule error` line for each ammeter. Compare against the numbers in the
DESIGN.md timing table.

If the error has regressed, in order of preference:

1. **Shorten the GIL switch interval for the duration of a run.** Cheap,
   local, reversible. In `collect_samples`, or a small context manager beside
   it:

   ```python
   @contextlib.contextmanager
   def _fine_grained_switching(interval_s: float = 0.0005) -> Iterator[None]:
       """Concurrent samplers spin on coinciding deadlines; the default 5 ms GIL slice is coarser
       than the spin window, so a worker can wake late through no fault of the schedule."""
       previous = sys.getswitchinterval()
       sys.setswitchinterval(interval_s)
       try:
           yield
       finally:
           sys.setswitchinterval(previous)
   ```

   Set it once in `run_selected` around the pool rather than per worker —
   it is a process-global setting and nested set/restore across threads would
   race.

2. **Shrink `SPIN_WINDOW_S`.** Trades a little accuracy for less contention.
   Measure the cost with `examples/timing_probe.py` before accepting it.

3. **Raise `max_schedule_error_ms` in the config,** and say in DESIGN.md that
   the looser bound is the price of concurrent sampling. Honest, but it is
   giving up something the current design earned; prefer 1.

Do **not** stagger the workers' start times to avoid the collision. It would fix
the contention and destroy the reason for the change — simultaneous measurement.

Whichever you land on, **put the real measured numbers in DESIGN.md.** The
existing timing table (cumulative sleep vs. absolute deadline vs. halving sleeps)
is one of the strongest things in the document precisely because it reports
measurements rather than assertions. A concurrent row belongs in it: max
schedule error for one ammeter alone versus three concurrently, on the same host.

### 5e. Thread-safety audit

Confirm each of these while you work; they are all currently fine, and the point
is not to break them:

- `ResultsArchive.save` — `mkdir(exist_ok=True)` is safe under concurrency; run
  ids embed `uuid4().hex[:8]`, so two workers cannot collide on a filename.
  With change 1, the temp names are derived from the run id and are unique too.
- `collect_samples` — local state only.
- `FaultInjector` — each instance owns a `random.Random`; no shared state.
- `logging` — thread-safe by design. Only the message content needed fixing.
- `configure_logging(..., force=True)` — called once in `main` before any
  worker starts. Do not call it from a worker.
- The emulators — one server per port, one connection at a time, and each
  worker talks to a different port.

### Tests

- `test_run_all_covers_every_configured_ammeter` should still pass unchanged.
  Strengthen it: assert the runs actually overlap in time, which is the whole
  point of the change.

  ```python
  def test_run_all_samples_the_ammeters_over_the_same_window(config: Config) -> None:
      started = time.monotonic()
      results = AmmeterTestFramework(config).run_all()
      elapsed = time.monotonic() - started
      longest = max(r.timing.actual_span_s for r in results)
      assert elapsed < longest * len(results)  # not merely sequential
  ```

  Keep the assertion loose like that. A tight bound on wall-clock timing in CI
  is a flaky test waiting to happen.

- Add: `run_selected` with an unknown name raises `ConfigError` **before** any
  sampling happens — assert no files were written to `results_dir`.
- Add: one unreachable ammeter among three raises, and the reachable ones are
  still archived. This pins the "no data is lost" claim.
- `tests/test_cli.py::test_run_list_show_compare` asserts
  `out.count("[PASS]") == 2` and should pass unchanged; if it does not, the
  reporting split in 5b is wrong.

### Docs

- `docs/DESIGN.md`: **delete** the bullet under "Not done, on purpose" that
  says ammeters are sampled one after another. Add a **Concurrency** paragraph
  to Architecture: one worker per device, why it is safe (separate servers),
  why it matters (same measurement window makes `compare` meaningful), results
  returned in config order, plotting kept on the main thread and why. Add the
  GIL/spin-window interaction and whatever you measured. Add the caveat that
  the spin window is per thread, so N concurrent ammeters spin N times as much
  — negligible at the shipped 10 Hz, worth knowing above ~100 Hz.
- `README.md`: if the run is now noticeably faster, the quick-start text
  benefits from saying so.

### Commit

`Sample every ammeter over the same window`

---

## Finishing up

1. **Regenerate the sample results.** `results/` holds five runs produced by the
   older code; they lack the new `retry_attempts` / `retry_backoff_s` metadata.
   They still load (metadata is read as an opaque dict), but committed evidence
   should match the code that is committed beside it. Delete them, run
   `python main.py` in one terminal and, in another:

   ```sh
   python run_tests.py run --label baseline
   python run_tests.py run greenlee --simulate-errors 0.2 --seed 42 --label error-simulation
   python run_tests.py run entes --reference 60 --label reference
   python run_tests.py compare --latest
   ```

   Run ids change; update any run id quoted in `README.md` (the `show` example
   and the sample report block both quote one).

2. **Update the test count** in `README.md` under Development — it currently
   says 90.

3. **Re-read `docs/DESIGN.md` end to end** against the code. Every one of these
   five changes touches a claim it makes. In particular the "Not done, on
   purpose" section: after this work, only the error-simulation-modes bullet and
   the MAD bullet should remain, and a doc that still lists a shipped feature as
   deliberately omitted is worse than one that never mentioned it.

4. Final gate:

   ```sh
   python -m pytest
   python -m ruff check .
   python -m mypy main.py run_tests.py Ammeters src tests examples
   python main.py            # terminal 1
   python run_tests.py run   # terminal 2, twice in a row, exit code 0 both times
   ```

## Definition of done

- [ ] Five commits, one per change, each with a message saying what was found or verified, not just what changed
- [ ] `pytest`, `ruff`, and `mypy --strict` all clean
- [ ] `run_tests.py run` exits 0 twice in a row against live emulators
- [ ] Reported max schedule error under concurrency is measured and written into DESIGN.md
- [ ] The DESIGN.md fixes table has a row for the RNG seed
- [ ] "Not done, on purpose" no longer lists sequential sampling
- [ ] `results/` regenerated, and every run id quoted in README.md matches a file that exists
- [ ] No pass criterion was loosened, no test deleted or skipped, no new `# type: ignore` or `# noqa`
- [ ] Any change declined or abandoned is recorded, with the measurement or reasoning that justified it
- [ ] Risk log delivered with the summary
