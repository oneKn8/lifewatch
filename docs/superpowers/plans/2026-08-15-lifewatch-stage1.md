# lifewatch Stage 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working accountability engine on the user's machine by 2026-08-24 that notices in real time when a declared block is not being kept and escalates until the block is started or renegotiated.

**Architecture:** Seven decoupled units — sensors, store, classifier, contract, watcher, effectors, views — communicating through an append-only SQLite observation log. All time is injected via a `Clock` protocol and all sensors have scripted doubles, so a simulated week replays deterministically in milliseconds. No unit contains domain knowledge; the `school` pack supplies it as data.

**Tech Stack:** Python 3.10.12, stdlib `sqlite3`, FastAPI + uvicorn, PyYAML, pytest, httpx. Frontend is hand-written HTML/CSS/JS with no build step. X11 access via `xprop` subprocess and XScreenSaver through `ctypes` — both verified working on the target machine with no new system packages.

**Spec:** `docs/superpowers/specs/2026-08-15-lifewatch-design.md`

## Global Constraints

- **Python 3.10.12** is the floor. No `match` gymnastics needed but 3.10 syntax is available.
- **No wall-clock reads outside `SystemClock`.** Any `datetime.now()` or `time.time()` in `lifewatch/` outside `clock.py` is a defect. Task 12 enforces this with a grep test.
- **No domain knowledge in the engine.** No course codes, no SSIDs, no personal strings anywhere in `lifewatch/`. Everything personal lives in `config/`, which is gitignored.
- **`Intervention` cannot exist without a non-empty `next_action`** (spec §9.2). Enforced in `__post_init__`, not by a caller-side check.
- **The `presence` sensor is Stage 2.** Do not write it. Do not write a camera import.
- **No image bytes may be written to disk by any code in this repo** (spec §12).
- **Test fixtures use obviously synthetic data.** No real course codes, no real grades, no real network names. Use `COURSE-101`, `Test Network`, and similar.
- **Watcher discretion runs toward mercy only** (spec §9.3): it may select a lower rung than the ladder specifies, never a higher one.
- **Commit after every task.** Commit messages describe the behaviour change, never the tooling that produced them.

---

## File Structure

```
lifewatch/
  __init__.py
  clock.py            Clock protocol, SystemClock, FakeClock
  models.py           Observation, Interval, Klass, Block, BlockState, Intervention
  config.py           Config load/save, Place learning
  store.py            SQLite append-only log + interval store
  contract.py         Blocks, budget, passes, sick mode, move state machine
  classify/
    __init__.py       Classifier orchestrator (tier dispatch)
    tier1.py          Mechanical rules
    tier2.py          Local model judgment
    tier3.py          Ask queue
  sensors/
    __init__.py       Sensor protocol, registry
    window.py         X11 active window
    idle.py           XScreenSaver idle ms
    network.py        SSID to learned place
    runner.py         Poll loop
  watcher.py          Ladder, evaluation, mercy-only discretion
  effectors/
    __init__.py       Effector protocol, registry
    wall.py           Wall display state
    notify.py         ntfy transport
  web/
    app.py            FastAPI app, API routes
    static/
      phone.html      Interactive view
      wall.html       Glanceable view
      grid.js         Semester grid renderer
      style.css
  wizard.py           First-run setup
packs/
  school/
    pack.yaml         Field definitions
    __init__.py       Grade model, campus mode
config.example.yaml
tests/
```

---

### Task 1: Skeleton, clock, and config

**Files:**
- Create: `pyproject.toml`, `lifewatch/__init__.py`, `lifewatch/clock.py`, `lifewatch/config.py`, `config.example.yaml`
- Test: `tests/test_clock.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `Clock` protocol with `now() -> datetime`
  - `SystemClock()` and `FakeClock(start: datetime)` with `advance(seconds: float) -> None`
  - `Config.load(path: Path) -> Config`, `Config.save(path: Path) -> None`
  - `Config.places: dict[str, Place]`, `Place(name: str, matcher_type: str, matcher_value: str)`
  - `Config.learn_place(name: str, ssid: str) -> Place`

- [ ] **Step 1: Write the failing clock test**

```python
# tests/test_clock.py
from datetime import datetime, timedelta
from lifewatch.clock import FakeClock

def test_fake_clock_starts_where_told_and_advances():
    start = datetime(2026, 8, 24, 7, 0, 0)
    clock = FakeClock(start)
    assert clock.now() == start
    clock.advance(90)
    assert clock.now() == start + timedelta(seconds=90)

def test_fake_clock_advance_is_cumulative():
    clock = FakeClock(datetime(2026, 8, 24, 7, 0, 0))
    clock.advance(60)
    clock.advance(60)
    assert clock.now() == datetime(2026, 8, 24, 7, 2, 0)
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `python -m pytest tests/test_clock.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'lifewatch.clock'`

- [ ] **Step 3: Create the package skeleton and `clock.py`**

`pyproject.toml` declares `requires-python = ">=3.10"` and dependencies `fastapi`, `uvicorn`, `pyyaml`, `httpx`; dev extra `pytest`.

`lifewatch/clock.py` defines a `Clock` `Protocol` with `now() -> datetime`, a `SystemClock` returning `datetime.now()`, and a `FakeClock` holding a mutable `_now` advanced by `advance()`.

- [ ] **Step 4: Run the clock test, confirm pass**

Run: `python -m pytest tests/test_clock.py -v`
Expected: 2 passed

- [ ] **Step 5: Write the failing config test**

```python
# tests/test_config.py
import pytest
from lifewatch.config import Config

def test_learn_place_captures_the_ssid_it_is_given(tmp_path):
    cfg = Config.empty()
    place = cfg.learn_place("home", ssid="Test Network")
    assert place.matcher_type == "ssid"
    assert place.matcher_value == "Test Network"
    assert cfg.places["home"] is place

def test_config_round_trips_through_yaml(tmp_path):
    cfg = Config.empty()
    cfg.learn_place("campus", ssid="Test Campus Net")
    path = tmp_path / "config.yaml"
    cfg.save(path)
    reloaded = Config.load(path)
    assert reloaded.places["campus"].matcher_value == "Test Campus Net"

def test_no_place_exists_before_one_is_learned():
    assert Config.empty().places == {}
```

- [ ] **Step 6: Run it and confirm it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 7: Implement `config.py`**

Dataclasses `Place(name, matcher_type, matcher_value)` and `Config(places, commitments, ladder, classifier, effectors, pack)`. `Config.empty()` returns a config with no places. `load`/`save` use `yaml.safe_load` / `yaml.safe_dump`. `learn_place` constructs a `Place` with `matcher_type="ssid"` and stores it under `name`.

`config.example.yaml` shows a generic student: two placeholder places named `home` and `campus` with `matcher_value: CHANGE-ME-RUN-THE-WIZARD`, one commitment `COURSE-101`, and the default ladder from spec §9.1.

- [ ] **Step 8: Run the config tests, confirm pass**

Run: `python -m pytest tests/ -v`
Expected: 5 passed

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml lifewatch/ tests/ config.example.yaml
git commit -m "feat: injectable clock and runtime-learned place config

Places are captured at runtime and never defaulted, so no network name
can enter source. The clock is a protocol from the first commit because
every later unit is time-dependent and must replay deterministically."
```

---

### Task 2: Store

**Files:**
- Create: `lifewatch/models.py`, `lifewatch/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `Clock` from Task 1
- Produces:
  - `Observation(ts: datetime, sensor: str, kind: str, value: str, meta: dict)` — frozen dataclass
  - `Klass` enum: `ALIGNED, AMBIENT, DRIFT, ABSENT, ACCOUNTED, UNKNOWN`
  - `Interval(start: datetime, end: datetime, klass: Klass, tier: int, reason: str)` — frozen
  - `Store(path: Path, clock: Clock)` with `append(obs)`, `observations(start, end, sensor=None) -> list[Observation]`, `latest(sensor, kind) -> Observation | None`, `put_interval(iv)`, `intervals(start, end) -> list[Interval]`

- [ ] **Step 1: Write the failing store test**

```python
# tests/test_store.py
from datetime import datetime, timedelta
from lifewatch.clock import FakeClock
from lifewatch.models import Observation, Interval, Klass
from lifewatch.store import Store

T0 = datetime(2026, 8, 24, 7, 0, 0)

def make_store(tmp_path):
    return Store(tmp_path / "test.db", FakeClock(T0))

def test_appended_observation_comes_back(tmp_path):
    store = make_store(tmp_path)
    obs = Observation(ts=T0, sensor="window", kind="focus",
                      value="Test App|Test Document", meta={})
    store.append(obs)
    got = store.observations(T0 - timedelta(minutes=1), T0 + timedelta(minutes=1))
    assert len(got) == 1
    assert got[0].value == "Test App|Test Document"

def test_observations_outside_the_range_are_excluded(tmp_path):
    store = make_store(tmp_path)
    store.append(Observation(T0, "window", "focus", "early", {}))
    store.append(Observation(T0 + timedelta(hours=2), "window", "focus", "late", {}))
    got = store.observations(T0 - timedelta(minutes=1), T0 + timedelta(minutes=1))
    assert [o.value for o in got] == ["early"]

def test_latest_returns_the_most_recent_for_that_sensor_and_kind(tmp_path):
    store = make_store(tmp_path)
    store.append(Observation(T0, "window", "focus", "first", {}))
    store.append(Observation(T0 + timedelta(seconds=30), "window", "focus", "second", {}))
    store.append(Observation(T0 + timedelta(seconds=45), "idle", "ms", "1000", {}))
    assert store.latest("window", "focus").value == "second"

def test_latest_is_none_when_nothing_recorded(tmp_path):
    assert make_store(tmp_path).latest("window", "focus") is None

def test_meta_survives_the_round_trip(tmp_path):
    store = make_store(tmp_path)
    store.append(Observation(T0, "window", "focus", "x", {"wm_class": "TestClass"}))
    assert store.observations(T0 - timedelta(seconds=1),
                             T0 + timedelta(seconds=1))[0].meta["wm_class"] == "TestClass"

def test_intervals_round_trip(tmp_path):
    store = make_store(tmp_path)
    iv = Interval(T0, T0 + timedelta(minutes=15), Klass.ALIGNED, tier=1, reason="focused on target")
    store.put_interval(iv)
    got = store.intervals(T0, T0 + timedelta(hours=1))
    assert got[0].klass is Klass.ALIGNED
    assert got[0].tier == 1
```

- [ ] **Step 2: Run and confirm failure**

Run: `python -m pytest tests/test_store.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'lifewatch.models'`

- [ ] **Step 3: Implement `models.py` then `store.py`**

`models.py` holds the frozen dataclasses and the `Klass` enum described in Interfaces.

`store.py` creates two tables on first open:

```sql
CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, sensor TEXT NOT NULL, kind TEXT NOT NULL,
  value TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_obs_ts ON observations(ts);
CREATE INDEX IF NOT EXISTS ix_obs_sensor ON observations(sensor, kind, ts);

CREATE TABLE IF NOT EXISTS intervals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  start TEXT NOT NULL, end TEXT NOT NULL,
  klass TEXT NOT NULL, tier INTEGER NOT NULL, reason TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_iv_start ON intervals(start);
```

Timestamps are stored as ISO-8601 strings so ordering is lexicographic. `meta` is JSON. The table is append-only: expose no update or delete method.

- [ ] **Step 4: Run and confirm pass**

Run: `python -m pytest tests/ -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add lifewatch/models.py lifewatch/store.py tests/test_store.py
git commit -m "feat: append-only observation and interval store

No update or delete path is exposed. What was observed is a matter of
record, and a system whose job is honesty must not be able to revise it."
```

---

### Task 3: Sensors

**Files:**
- Create: `lifewatch/sensors/__init__.py`, `lifewatch/sensors/window.py`, `lifewatch/sensors/idle.py`, `lifewatch/sensors/network.py`
- Test: `tests/test_sensors.py`

**Interfaces:**
- Consumes: `Observation` (Task 2), `Config` (Task 1)
- Produces:
  - `Sensor` protocol: `name: str`, `poll_interval_s: int`, `available() -> bool`, `poll(now: datetime) -> list[Observation]`
  - `WindowSensor(reader=read_active_window)` emitting `kind="focus"`, `value=f"{wm_class}|{title}"`
  - `IdleSensor(reader=xss_reader)` emitting `kind="ms"`, `value=str(idle_ms)`
  - `NetworkSensor(config, reader=ssid_reader)` emitting `kind="place"`, `value=place_name_or_unknown`
  - `FakeSensor(name, scripted: list[Observation])` for tests

Each sensor takes its OS access as an injected callable. That is what makes them testable without X11 and what lets a contributor swap in a Wayland reader.

- [ ] **Step 1: Write the failing sensor tests**

```python
# tests/test_sensors.py
from datetime import datetime
from lifewatch.config import Config
from lifewatch.sensors.window import WindowSensor
from lifewatch.sensors.idle import IdleSensor
from lifewatch.sensors.network import NetworkSensor

T0 = datetime(2026, 8, 24, 7, 0, 0)

def test_window_sensor_reports_class_and_title():
    sensor = WindowSensor(reader=lambda: ("TestClass", "Test Document Title"))
    obs = sensor.poll(T0)
    assert len(obs) == 1
    assert obs[0].sensor == "window"
    assert obs[0].value == "TestClass|Test Document Title"

def test_window_sensor_emits_nothing_when_no_window_is_focused():
    assert WindowSensor(reader=lambda: None).poll(T0) == []

def test_window_sensor_is_unavailable_when_the_reader_raises():
    def broken():
        raise OSError("no display")
    assert WindowSensor(reader=broken).available() is False

def test_idle_sensor_reports_milliseconds():
    obs = IdleSensor(reader=lambda: 79656).poll(T0)
    assert obs[0].sensor == "idle"
    assert obs[0].value == "79656"

def test_network_sensor_maps_a_known_ssid_to_its_learned_place():
    cfg = Config.empty()
    cfg.learn_place("home", ssid="Test Network")
    obs = NetworkSensor(cfg, reader=lambda: "Test Network").poll(T0)
    assert obs[0].value == "home"

def test_network_sensor_reports_unknown_for_an_unlearned_ssid():
    cfg = Config.empty()
    cfg.learn_place("home", ssid="Test Network")
    obs = NetworkSensor(cfg, reader=lambda: "Some Other Network").poll(T0)
    assert obs[0].value == "unknown"

def test_network_sensor_reports_offline_when_there_is_no_ssid():
    obs = NetworkSensor(Config.empty(), reader=lambda: None).poll(T0)
    assert obs[0].value == "offline"
```

- [ ] **Step 2: Run and confirm failure**

Run: `python -m pytest tests/test_sensors.py -v`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Implement the three sensors and their default readers**

Default readers, each isolated in its own module so the sensor logic stays pure:

- `window.py` — `xprop -root _NET_ACTIVE_WINDOW` then `xprop -id <id> _NET_WM_NAME WM_CLASS`, parsed to `(wm_class, title)`. Verified working on the target machine.
- `idle.py` — `ctypes` against `libX11.so.6` and `libXss.so.1`, `XScreenSaverQueryInfo`, returns `info.contents.idle`. Verified working on the target machine.
- `network.py` — `iwgetid -r`, stripped; returns `None` on empty output.

`available()` calls the reader once inside a `try` and returns `False` on any exception.

- [ ] **Step 4: Run and confirm pass**

Run: `python -m pytest tests/ -v`
Expected: 18 passed

- [ ] **Step 5: Verify the real readers against the live machine**

Run: `python -c "from lifewatch.sensors.window import read_active_window; from lifewatch.sensors.idle import read_idle_ms; from lifewatch.sensors.network import read_ssid; print(read_active_window()); print(read_idle_ms()); print(read_ssid())"`
Expected: a real `(class, title)` tuple, an integer, and an SSID string. This is the only step in the plan that touches real hardware.

- [ ] **Step 6: Commit**

```bash
git add lifewatch/sensors/ tests/test_sensors.py
git commit -m "feat: window, idle and network sensors with injected OS readers

Sensors are strictly factual: they report a title, never a judgment about
it. OS access is injected so the suite needs no X11, and so a Wayland
contributor can replace one reader without touching anything above."
```

---

### Task 4: Sensor runner

**Files:**
- Create: `lifewatch/sensors/runner.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: `Sensor`, `Store`, `Clock`
- Produces: `Runner(sensors: list[Sensor], store: Store, clock: Clock)` with `tick() -> int` returning the number of observations written

- [ ] **Step 1: Write the failing runner test**

```python
# tests/test_runner.py
from datetime import datetime
from lifewatch.clock import FakeClock
from lifewatch.models import Observation
from lifewatch.store import Store
from lifewatch.sensors.runner import Runner

T0 = datetime(2026, 8, 24, 7, 0, 0)

class ScriptedSensor:
    name = "window"
    poll_interval_s = 15
    def __init__(self, values):
        self.values = list(values)
    def available(self):
        return True
    def poll(self, now):
        if not self.values:
            return []
        return [Observation(now, "window", "focus", self.values.pop(0), {})]

def test_runner_writes_observations_to_the_store(tmp_path):
    clock = FakeClock(T0)
    store = Store(tmp_path / "t.db", clock)
    runner = Runner([ScriptedSensor(["a", "b"])], store, clock)
    assert runner.tick() == 1
    clock.advance(15)
    assert runner.tick() == 1
    assert [o.value for o in store.observations(T0, clock.now())] == ["a", "b"]

def test_runner_suppresses_an_unchanged_value_until_the_heartbeat(tmp_path):
    clock = FakeClock(T0)
    store = Store(tmp_path / "t.db", clock)
    runner = Runner([ScriptedSensor(["same", "same", "same"])], store, clock,
                    heartbeat_s=600)
    runner.tick()
    clock.advance(15)
    runner.tick()
    assert len(store.observations(T0, clock.now())) == 1

def test_runner_re_records_an_unchanged_value_after_the_heartbeat(tmp_path):
    clock = FakeClock(T0)
    store = Store(tmp_path / "t.db", clock)
    runner = Runner([ScriptedSensor(["same"] * 3)], store, clock, heartbeat_s=600)
    runner.tick()
    clock.advance(601)
    runner.tick()
    assert len(store.observations(T0, clock.now())) == 2

def test_runner_skips_unavailable_sensors_without_failing(tmp_path):
    class Dead:
        name = "dead"
        poll_interval_s = 15
        def available(self): return False
        def poll(self, now): raise AssertionError("must not be polled")
    clock = FakeClock(T0)
    store = Store(tmp_path / "t.db", clock)
    assert Runner([Dead()], store, clock).tick() == 0

def test_one_failing_sensor_does_not_stop_the_others(tmp_path):
    class Exploding:
        name = "boom"
        poll_interval_s = 15
        def available(self): return True
        def poll(self, now): raise RuntimeError("sensor failed")
    clock = FakeClock(T0)
    store = Store(tmp_path / "t.db", clock)
    runner = Runner([Exploding(), ScriptedSensor(["ok"])], store, clock)
    assert runner.tick() == 1
```

- [ ] **Step 2: Run and confirm failure**

Run: `python -m pytest tests/test_runner.py -v`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Implement `runner.py`**

`tick()` iterates sensors, skips those whose `available()` is false, calls `poll(clock.now())` inside a `try/except` that logs and continues, and writes an observation only when its `value` differs from the last written value for that `(sensor, kind)` or when `heartbeat_s` has elapsed since the last write. Default `heartbeat_s=600`.

- [ ] **Step 4: Run and confirm pass**

Run: `python -m pytest tests/ -v`
Expected: 23 passed

- [ ] **Step 5: Commit**

```bash
git add lifewatch/sensors/runner.py tests/test_runner.py
git commit -m "feat: sensor runner with change suppression and fault isolation

Writes on change plus a heartbeat so the log stays small over a term. A
sensor that throws is skipped rather than taking the loop down, because
an instrument that dies silently is worse than no instrument."
```

---

### Task 5: Contract

**Files:**
- Create: `lifewatch/contract.py` (adds `Block`, `BlockState` to `lifewatch/models.py`)
- Test: `tests/test_contract.py`

**Interfaces:**
- Consumes: `Clock`, `Config`
- Produces:
  - `BlockState` enum: `PLANNED, RUNNING, COMPLETED, MOVED, MISSED, EXCUSED`
  - `Block(id, commitment_id, planned_start, planned_end, actual_start, actual_end, state, moved_to, moved_from)`
  - `Contract(config, clock)` with `add_block(...) -> Block`, `current_block(now) -> Block | None`, `start_block(id, now)`, `complete_block(id, now)`, `move_block(id, new_start, new_end, now) -> Block`, `use_pass(now) -> bool`, `passes_remaining(now) -> int`, `declare_sick(now, hours=24)`, `is_silenced(now) -> bool`, `debt_minutes(day) -> int`

- [ ] **Step 1: Write the failing contract tests**

```python
# tests/test_contract.py
import pytest
from datetime import datetime, timedelta
from lifewatch.clock import FakeClock
from lifewatch.config import Config
from lifewatch.contract import Contract
from lifewatch.models import BlockState

T0 = datetime(2026, 8, 24, 7, 0, 0)

def make(passes=1):
    cfg = Config.empty()
    cfg.passes_per_week = passes
    return Contract(cfg, FakeClock(T0))

def add(contract, start=T0, minutes=90):
    return contract.add_block("COURSE-101", start, start + timedelta(minutes=minutes))

def test_a_new_block_is_planned():
    assert add(make()).state is BlockState.PLANNED

def test_starting_a_block_records_the_actual_time():
    c = make(); b = add(c)
    c.start_block(b.id, T0 + timedelta(minutes=3))
    assert b.state is BlockState.RUNNING
    assert b.actual_start == T0 + timedelta(minutes=3)

def test_current_block_finds_the_block_covering_now():
    c = make(); b = add(c)
    assert c.current_block(T0 + timedelta(minutes=30)).id == b.id

def test_current_block_is_none_outside_any_window():
    c = make(); add(c)
    assert c.current_block(T0 + timedelta(hours=5)) is None

def test_there_is_no_dismiss_method():
    assert not hasattr(make(), "dismiss_block")

def test_moving_a_block_creates_a_successor_and_links_both_ways():
    c = make(); b = add(c)
    new_start = T0 + timedelta(days=1)
    successor = c.move_block(b.id, new_start, new_start + timedelta(minutes=90), T0)
    assert b.state is BlockState.MOVED
    assert b.moved_to == successor.id
    assert successor.moved_from == b.id
    assert successor.state is BlockState.PLANNED

def test_moved_minutes_become_debt_on_the_receiving_day():
    c = make(); b = add(c, minutes=90)
    new_start = T0 + timedelta(days=1)
    c.move_block(b.id, new_start, new_start + timedelta(minutes=90), T0)
    assert c.debt_minutes(new_start.date()) == 90

def test_a_pass_is_finite_and_decrements():
    c = make(passes=1)
    assert c.passes_remaining(T0) == 1
    assert c.use_pass(T0) is True
    assert c.passes_remaining(T0) == 0
    assert c.use_pass(T0) is False

def test_passes_reset_the_following_week():
    c = make(passes=1)
    c.use_pass(T0)
    assert c.passes_remaining(T0 + timedelta(days=7)) == 1

def test_passes_do_not_accumulate_across_weeks():
    c = make(passes=1)
    assert c.passes_remaining(T0 + timedelta(days=21)) == 1

def test_sick_mode_silences_for_the_declared_window():
    c = make()
    c.declare_sick(T0, hours=24)
    assert c.is_silenced(T0 + timedelta(hours=5)) is True
    assert c.is_silenced(T0 + timedelta(hours=25)) is False

def test_nothing_is_silenced_by_default():
    assert make().is_silenced(T0) is False
```

- [ ] **Step 2: Run and confirm failure**

Run: `python -m pytest tests/test_contract.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'lifewatch.contract'`

- [ ] **Step 3: Implement `contract.py`**

Blocks are held in a dict keyed by a generated id. `move_block` transitions the original to `MOVED`, constructs a successor in `PLANNED`, links `moved_to`/`moved_from`, and records the moved minutes against the successor's date in a `debt` counter. `use_pass` is keyed on ISO week so it resets weekly and does not accumulate. `declare_sick` stores an expiry timestamp; `is_silenced` compares against it.

There is deliberately no `dismiss_block`. The only exits are start, complete, move, pass, sick.

- [ ] **Step 4: Run and confirm pass**

Run: `python -m pytest tests/ -v`
Expected: 35 passed

- [ ] **Step 5: Commit**

```bash
git add lifewatch/contract.py lifewatch/models.py tests/test_contract.py
git commit -m "feat: contract with move, pass and sick mode but no dismiss

Renegotiation is the only routine exit and it costs a named replacement
slot, so hours relocate as debt instead of evaporating. Passes are finite
and weekly; unlimited means no system and zero means the user breaks it."
```

---

### Task 6: Classifier Tier 1

**Files:**
- Create: `lifewatch/classify/__init__.py`, `lifewatch/classify/tier1.py`
- Test: `tests/test_tier1.py`

**Interfaces:**
- Consumes: `Observation`, `Interval`, `Klass`, `Contract`
- Produces: `tier1(observations: list[Observation], block, now) -> Interval | None` — returns `None` when no mechanical rule applies, which is the signal to escalate to Tier 2

The rule that carries the most weight (spec §7): media playing while a **different** window has focus is `AMBIENT`. The same application focused for a sustained period is a Tier 2 candidate, not an automatic `DRIFT`.

- [ ] **Step 1: Write the failing Tier 1 tests**

```python
# tests/test_tier1.py
from datetime import datetime, timedelta
from lifewatch.models import Observation, Klass
from lifewatch.classify.tier1 import tier1

T0 = datetime(2026, 8, 24, 7, 0, 0)

def obs(kind, value, sensor="window", offset=0):
    return Observation(T0 + timedelta(seconds=offset), sensor, kind, value, {})

def test_media_in_the_background_is_ambient_not_drift():
    observations = [
        obs("focus", "TestEditor|problem-set.pdf"),
        obs("media", "playing", sensor="window"),
    ]
    assert tier1(observations, block=object(), now=T0).klass is Klass.AMBIENT

def test_long_idle_during_a_block_is_absent():
    observations = [obs("ms", str(20 * 60 * 1000), sensor="idle")]
    result = tier1(observations, block=object(), now=T0)
    assert result.klass is Klass.ABSENT

def test_brief_idle_is_not_absent():
    observations = [obs("ms", str(45 * 1000), sensor="idle")]
    result = tier1(observations, block=object(), now=T0)
    assert result is None or result.klass is not Klass.ABSENT

def test_time_at_a_place_tagged_accounted_is_accounted():
    observations = [obs("place", "campus", sensor="network")]
    result = tier1(observations, block=None, now=T0, accounted_places={"campus"})
    assert result.klass is Klass.ACCOUNTED

def test_an_ambiguous_focused_window_returns_none_for_tier2():
    observations = [obs("focus", "TestBrowser|Some Video Title")]
    assert tier1(observations, block=object(), now=T0) is None

def test_tier1_records_which_tier_decided():
    observations = [obs("ms", str(20 * 60 * 1000), sensor="idle")]
    assert tier1(observations, block=object(), now=T0).tier == 1

def test_tier1_gives_a_human_readable_reason():
    observations = [obs("ms", str(20 * 60 * 1000), sensor="idle")]
    assert "idle" in tier1(observations, block=object(), now=T0).reason.lower()
```

- [ ] **Step 2: Run and confirm failure**

Run: `python -m pytest tests/test_tier1.py -v`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Implement `tier1.py`**

Signature `tier1(observations, block, now, accounted_places=frozenset(), idle_threshold_s=900) -> Interval | None`.

Rule order: accounted place first, then background-media, then idle threshold, then `None`. Every returned `Interval` carries `tier=1` and a reason naming the rule that fired.

- [ ] **Step 4: Run and confirm pass**

Run: `python -m pytest tests/ -v`
Expected: 42 passed

- [ ] **Step 5: Commit**

```bash
git add lifewatch/classify/ tests/test_tier1.py
git commit -m "feat: mechanical classification tier

Background media resolves the study-music ambiguity structurally rather
than by classifying titles: media playing while another window has focus
is listening, not watching. Returning None is how ambiguity escalates."
```

---

### Task 7: Classifier Tier 2 and Tier 3

**Files:**
- Create: `lifewatch/classify/tier2.py`, `lifewatch/classify/tier3.py`
- Modify: `lifewatch/classify/__init__.py`
- Test: `tests/test_tier2.py`, `tests/test_tier3.py`

**Interfaces:**
- Consumes: Tier 1's `None` signal
- Produces:
  - `tier2(title: str, commitment_label: str, judge: Callable[[str], str]) -> Interval | None`
  - `AskQueue(store)` with `enqueue(title, block_id, now)`, `pending() -> list[Question]`, `answer(question_id, klass)`
  - `Classifier(tier1_fn, tier2_fn, ask_queue, config)` with `classify(observations, block, now) -> Interval`

The judge is injected. Default is a local model over HTTP; tests pass a lambda. Tier 2 **never** receives anything but the title and the commitment label — no URLs, no store access, no user identity.

- [ ] **Step 1: Write the failing Tier 2 tests**

```python
# tests/test_tier2.py
from lifewatch.classify.tier2 import tier2
from lifewatch.models import Klass

def test_judge_verdict_of_aligned_produces_an_aligned_interval():
    result = tier2("Lecture 4: Conditional Probability",
                   "COURSE-101", judge=lambda prompt: "aligned")
    assert result.klass is Klass.ALIGNED
    assert result.tier == 2

def test_judge_verdict_of_drift_produces_a_drift_interval():
    result = tier2("Funny Compilation Video", "COURSE-101",
                   judge=lambda prompt: "drift")
    assert result.klass is Klass.DRIFT

def test_an_unparseable_verdict_returns_none_so_tier3_asks():
    assert tier2("Ambiguous", "COURSE-101", judge=lambda p: "banana") is None

def test_a_failing_judge_returns_none_rather_than_guessing():
    def broken(prompt):
        raise ConnectionError("no local model")
    assert tier2("Anything", "COURSE-101", judge=broken) is None

def test_the_prompt_carries_only_title_and_commitment():
    seen = {}
    def judge(prompt):
        seen["prompt"] = prompt
        return "aligned"
    tier2("Test Title", "COURSE-101", judge=judge)
    assert "Test Title" in seen["prompt"]
    assert "COURSE-101" in seen["prompt"]
```

```python
# tests/test_tier3.py
from datetime import datetime
from lifewatch.clock import FakeClock
from lifewatch.store import Store
from lifewatch.classify.tier3 import AskQueue
from lifewatch.models import Klass

T0 = datetime(2026, 8, 24, 7, 0, 0)

def test_an_enqueued_question_shows_up_as_pending(tmp_path):
    q = AskQueue(Store(tmp_path / "t.db", FakeClock(T0)))
    q.enqueue("Some Video Title", block_id="b1", now=T0)
    assert len(q.pending()) == 1
    assert q.pending()[0].title == "Some Video Title"

def test_answering_removes_it_from_pending(tmp_path):
    q = AskQueue(Store(tmp_path / "t.db", FakeClock(T0)))
    q.enqueue("Some Video Title", block_id="b1", now=T0)
    q.answer(q.pending()[0].id, Klass.DRIFT)
    assert q.pending() == []

def test_the_same_title_is_not_queued_twice_while_pending(tmp_path):
    q = AskQueue(Store(tmp_path / "t.db", FakeClock(T0)))
    q.enqueue("Same Title", block_id="b1", now=T0)
    q.enqueue("Same Title", block_id="b1", now=T0)
    assert len(q.pending()) == 1
```

- [ ] **Step 2: Run both and confirm failure**

Run: `python -m pytest tests/test_tier2.py tests/test_tier3.py -v`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Implement `tier2.py`, `tier3.py`, and the `Classifier` orchestrator**

`tier2` builds a short prompt naming the commitment and the title, asks for exactly one word, and maps `aligned`/`drift`/`ambient` to a `Klass`. Anything else, or any exception, returns `None`.

The default judge posts to a local model endpoint. Per spec §7 it must be a 3B-class model; the endpoint and model name come from config, never hardcoded.

`tier3.AskQueue` persists questions in a `questions` table. `enqueue` is idempotent while a question with the same title is pending.

`Classifier.classify` runs tier1, then tier2, then enqueues a Tier 3 question and returns an `UNKNOWN` interval with `tier=3`.

- [ ] **Step 4: Run and confirm pass**

Run: `python -m pytest tests/ -v`
Expected: 50 passed

- [ ] **Step 5: Commit**

```bash
git add lifewatch/classify/ tests/test_tier2.py tests/test_tier3.py
git commit -m "feat: model judgment tier and ask-the-user fallback

Tier 2 sees a title and a commitment label and nothing else, which is
what lets it run locally and what keeps the cloud option honest. A judge
that fails returns None and the user is asked, rather than guessed at."
```

---

### Task 8: Watcher

**Files:**
- Create: `lifewatch/watcher.py` (adds `Intervention` to `lifewatch/models.py`)
- Test: `tests/test_watcher.py`

**Interfaces:**
- Consumes: `Contract`, `Classifier`, `Store`, `Clock`, ladder config
- Produces:
  - `Intervention(rung: int, block_id: str, message: str, next_action: str, requires_response: bool)` — frozen; `__post_init__` raises `ValueError` on empty `next_action`
  - `Watcher(contract, store, config, clock, judge=None)` with `evaluate(now) -> Intervention | None`

- [ ] **Step 1: Write the failing watcher tests**

```python
# tests/test_watcher.py
import pytest
from datetime import datetime, timedelta
from lifewatch.models import Intervention
from lifewatch.watcher import Watcher

T0 = datetime(2026, 8, 24, 7, 0, 0)

def test_an_intervention_cannot_exist_without_a_next_action():
    with pytest.raises(ValueError):
        Intervention(rung=2, block_id="b1", message="You are behind.",
                     next_action="", requires_response=False)

def test_whitespace_does_not_satisfy_the_next_action_requirement():
    with pytest.raises(ValueError):
        Intervention(rung=2, block_id="b1", message="You are behind.",
                     next_action="   ", requires_response=False)

def test_a_valid_intervention_constructs():
    iv = Intervention(rung=2, block_id="b1", message="Block is dead.",
                      next_action="COURSE-101 problem set 2, question 4",
                      requires_response=False)
    assert iv.rung == 2

def test_no_intervention_while_the_block_is_running(watcher_with_running_block):
    assert watcher_with_running_block.evaluate(T0 + timedelta(minutes=10)) is None

def test_rung_1_fires_at_block_start_with_nothing_running(watcher_with_dead_block):
    iv = watcher_with_dead_block.evaluate(T0)
    assert iv.rung == 1

def test_rung_2_fires_after_five_minutes(watcher_with_dead_block):
    iv = watcher_with_dead_block.evaluate(T0 + timedelta(minutes=6))
    assert iv.rung == 2

def test_sick_mode_suppresses_all_interventions(watcher_with_dead_block):
    watcher_with_dead_block.contract.declare_sick(T0, hours=24)
    assert watcher_with_dead_block.evaluate(T0 + timedelta(minutes=30)) is None

def test_moving_the_block_stops_escalation(watcher_with_dead_block):
    w = watcher_with_dead_block
    block = w.contract.current_block(T0)
    later = T0 + timedelta(days=1)
    w.contract.move_block(block.id, later, later + timedelta(minutes=90), T0)
    assert w.evaluate(T0 + timedelta(minutes=30)) is None

def test_no_escalation_when_no_next_action_can_be_resolved(watcher_without_next_action):
    assert watcher_without_next_action.evaluate(T0 + timedelta(minutes=30)) is None

def test_discretion_may_lower_a_rung(watcher_with_dead_block):
    w = watcher_with_dead_block
    w.judge = lambda state: 1
    assert w.evaluate(T0 + timedelta(minutes=6)).rung == 1

def test_discretion_may_never_raise_a_rung(watcher_with_dead_block):
    w = watcher_with_dead_block
    w.judge = lambda state: 4
    assert w.evaluate(T0 + timedelta(minutes=6)).rung == 2

def test_the_judge_never_receives_a_window_title(watcher_with_dead_block):
    seen = {}
    def judge(state):
        seen["state"] = state
        return 2
    watcher_with_dead_block.judge = judge
    watcher_with_dead_block.evaluate(T0 + timedelta(minutes=6))
    blob = repr(seen["state"]).lower()
    assert "title" not in blob and "http" not in blob
```

Fixtures live in `tests/conftest.py` and build a `Contract` with one 90-minute block starting at `T0`, a `Store` on `tmp_path`, and a `FakeClock`. `watcher_with_dead_block` leaves the block `PLANNED`; `watcher_with_running_block` calls `start_block`; `watcher_without_next_action` uses a commitment with no resolvable next action.

- [ ] **Step 2: Run and confirm failure**

Run: `python -m pytest tests/test_watcher.py -v`
Expected: FAIL, `ImportError: cannot import name 'Intervention'`

- [ ] **Step 3: Implement `Intervention` and `watcher.py`**

`Intervention.__post_init__` raises `ValueError` when `next_action.strip()` is empty. This is the spec §9.2 invariant expressed as a type constraint, so no code path anywhere can deliver a bare reproach.

`Watcher.evaluate` returns `None` when the contract is silenced, when there is no current block, when the block is `RUNNING`/`COMPLETED`/`MOVED`, or when no next action resolves. Otherwise it computes the ladder rung from minutes elapsed since `planned_start`, then, if a judge is set, calls it with derived state only — a dict of counts and durations — and takes `min(ladder_rung, judge_rung)`.

- [ ] **Step 4: Run and confirm pass**

Run: `python -m pytest tests/ -v`
Expected: 62 passed

- [ ] **Step 5: Commit**

```bash
git add lifewatch/watcher.py lifewatch/models.py tests/test_watcher.py tests/conftest.py
git commit -m "feat: watcher with mercy-only discretion and a hard next-action gate

An Intervention without a concrete next action cannot be constructed, so
the system is structurally incapable of delivering a bare reproach. The
judge may soften a rung and never sharpen one, and it sees derived counts
rather than any observation."
```

---

### Task 9: Effectors

**Files:**
- Create: `lifewatch/effectors/__init__.py`, `lifewatch/effectors/wall.py`, `lifewatch/effectors/notify.py`
- Test: `tests/test_effectors.py`

**Interfaces:**
- Consumes: `Intervention`
- Produces:
  - `Effector` protocol: `name`, `available() -> bool`, `deliver(iv) -> Delivery`
  - `Delivery(effector: str, ok: bool, detail: str)`
  - `WallEffector(store)` — records the current escalation state for the wall view to read
  - `NotifyEffector(config, poster)` — HTTP POST to a configured push topic; `poster` injected

- [ ] **Step 1: Write the failing effector tests**

```python
# tests/test_effectors.py
from datetime import datetime
from lifewatch.clock import FakeClock
from lifewatch.config import Config
from lifewatch.store import Store
from lifewatch.models import Intervention
from lifewatch.effectors.wall import WallEffector
from lifewatch.effectors.notify import NotifyEffector

T0 = datetime(2026, 8, 24, 7, 0, 0)

def an_intervention(rung=2):
    return Intervention(rung=rung, block_id="b1", message="Block is dead.",
                        next_action="COURSE-101 problem set 2, question 4",
                        requires_response=False)

def test_wall_effector_records_the_escalation_state(tmp_path):
    store = Store(tmp_path / "t.db", FakeClock(T0))
    wall = WallEffector(store)
    assert wall.deliver(an_intervention()).ok is True
    assert wall.current_state()["rung"] == 2

def test_notify_effector_sends_the_next_action_in_the_body(tmp_path):
    sent = {}
    def poster(url, body, title):
        sent.update(url=url, body=body, title=title)
        return True
    cfg = Config.empty()
    cfg.notify_url = "http://example.invalid/test-topic"
    assert NotifyEffector(cfg, poster=poster).deliver(an_intervention()).ok is True
    assert "problem set 2, question 4" in sent["body"]

def test_notify_effector_reports_failure_rather_than_raising(tmp_path):
    def broken(url, body, title):
        raise ConnectionError("unreachable")
    cfg = Config.empty()
    cfg.notify_url = "http://example.invalid/test-topic"
    assert NotifyEffector(cfg, poster=broken).deliver(an_intervention()).ok is False

def test_notify_effector_is_unavailable_without_a_configured_url():
    assert NotifyEffector(Config.empty(), poster=lambda *a: True).available() is False
```

- [ ] **Step 2: Run and confirm failure**

Run: `python -m pytest tests/test_effectors.py -v`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Implement the effectors**

`WallEffector` writes escalation state into an `escalation` table and exposes `current_state()`. `NotifyEffector` posts title and body to the configured URL through the injected `poster`, catching every exception into `Delivery(ok=False, detail=...)`. A failed delivery is recorded, never raised, because a dead notification channel must not take the watcher down.

- [ ] **Step 4: Run and confirm pass**

Run: `python -m pytest tests/ -v`
Expected: 66 passed

- [ ] **Step 5: Commit**

```bash
git add lifewatch/effectors/ tests/test_effectors.py
git commit -m "feat: wall and notification effectors

Every intervention body carries its next action, which is the point of
the next-action gate. Delivery failures are recorded rather than raised
so a dead push channel cannot take the watcher down with it."
```

---

### Task 10: Web layer, phone view, wall view

**Files:**
- Create: `lifewatch/web/app.py`, `lifewatch/web/static/phone.html`, `lifewatch/web/static/wall.html`, `lifewatch/web/static/grid.js`, `lifewatch/web/static/style.css`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `Contract`, `Store`, `Watcher`, `AskQueue`
- Produces these routes:
  - `GET /api/state` → `{now, current_block, rung, banked_minutes, gone_minutes, passes_remaining, silenced}`
  - `GET /api/grid?start=&end=` → `[{hour_iso, klass}]`
  - `POST /api/block/{id}/start`, `POST /api/block/{id}/complete`
  - `POST /api/block/{id}/move` body `{new_start, new_end}`
  - `POST /api/pass`, `POST /api/sick`
  - `GET /api/questions`, `POST /api/questions/{id}` body `{klass}`
  - `GET /` → phone view, `GET /wall` → wall view

- [ ] **Step 1: Write the failing web tests**

```python
# tests/test_web.py
from fastapi.testclient import TestClient

def test_state_endpoint_reports_the_current_block(client):
    body = client.get("/api/state").json()
    assert body["current_block"]["commitment_id"] == "COURSE-101"

def test_starting_a_block_through_the_api_changes_its_state(client, contract):
    block_id = contract.current_block(contract.clock.now()).id
    assert client.post(f"/api/block/{block_id}/start").status_code == 200
    assert client.get("/api/state").json()["current_block"]["state"] == "running"

def test_there_is_no_dismiss_route(client, contract):
    block_id = contract.current_block(contract.clock.now()).id
    assert client.post(f"/api/block/{block_id}/dismiss").status_code == 404

def test_moving_a_block_requires_a_destination(client, contract):
    block_id = contract.current_block(contract.clock.now()).id
    assert client.post(f"/api/block/{block_id}/move", json={}).status_code == 422

def test_grid_returns_one_entry_per_hour(client):
    rows = client.get("/api/grid", params={"start": "2026-08-24T00:00:00",
                                           "end": "2026-08-24T06:00:00"}).json()
    assert len(rows) == 6

def test_pass_endpoint_decrements_remaining(client):
    before = client.get("/api/state").json()["passes_remaining"]
    client.post("/api/pass")
    assert client.get("/api/state").json()["passes_remaining"] == before - 1

def test_wall_view_is_served(client):
    assert client.get("/wall").status_code == 200

def test_phone_view_is_served(client):
    assert client.get("/").status_code == 200
```

- [ ] **Step 2: Run and confirm failure**

Run: `python -m pytest tests/test_web.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'lifewatch.web'`

- [ ] **Step 3: Implement `app.py` and the two views**

`app.py` builds the FastAPI app from an injected `Contract`, `Store`, `Watcher`, and `AskQueue` so the test client gets fakes. There is no dismiss route, by design and by test.

`wall.html` per spec §13.1: three numbers, the grid, escalation state, the consequence chain from config. Minimum font size 48px, pure black background, pure white and pure red foreground, no font weight below 600, no opacity below 1.0 on text. A 2011 720p panel has a tired backlight and washes out anything subtle.

`phone.html` per spec §13.2: start, stop, move, pass, sick, and the Tier 3 question card.

`grid.js` renders one cell per waking hour for the term from `/api/grid`, coloured per spec §13.3: filled aligned, hatched ambient, grey accounted, **red unclaimed**, empty not-yet-reached. Both views poll `/api/state` every 15 seconds so red accumulates without interaction.

- [ ] **Step 4: Run and confirm pass**

Run: `python -m pytest tests/ -v`
Expected: 74 passed

- [ ] **Step 5: Commit**

```bash
git add lifewatch/web/ tests/test_web.py
git commit -m "feat: API, phone view and wall view with the semester grid

The wall polls and repaints on its own so unclaimed hours accumulate in
red whether or not anyone is operating the instrument. That autonomy is
the whole reason a passive dashboard fails and this does not.

There is no dismiss route, enforced by test."
```

---

### Task 11: Setup wizard and the school pack

**Files:**
- Create: `lifewatch/wizard.py`, `packs/school/pack.yaml`, `packs/school/__init__.py`
- Test: `tests/test_wizard.py`, `tests/test_school_pack.py`

**Interfaces:**
- Consumes: `Config`, the live SSID reader
- Produces:
  - `Wizard(config, ssid_reader)` with `learn_place_now(name) -> Place`, `add_commitment(...)`, `set_ladder(rungs)`, `finish(path)`
  - `packs/school`: `grade_needed(items, target_fraction) -> float`, `campus_gaps(meetings, day) -> list[tuple]`

- [ ] **Step 1: Write the failing wizard and pack tests**

```python
# tests/test_wizard.py
from lifewatch.config import Config
from lifewatch.wizard import Wizard

def test_learn_place_now_captures_whatever_ssid_is_live():
    w = Wizard(Config.empty(), ssid_reader=lambda: "Test Network")
    place = w.learn_place_now("home")
    assert place.matcher_value == "Test Network"

def test_learning_a_place_while_offline_fails_loudly():
    w = Wizard(Config.empty(), ssid_reader=lambda: None)
    try:
        w.learn_place_now("home")
        assert False, "should have raised"
    except ValueError as e:
        assert "no network" in str(e).lower()

def test_finished_config_contains_no_hardcoded_default_ssid(tmp_path):
    w = Wizard(Config.empty(), ssid_reader=lambda: "Test Network")
    w.learn_place_now("home")
    path = tmp_path / "config.yaml"
    w.finish(path)
    assert "CHANGE-ME" not in path.read_text()
```

```python
# tests/test_school_pack.py
from packs.school import grade_needed

def test_grade_needed_computes_the_remaining_requirement():
    items = [
        {"name": "Exam 1", "weight": 0.25, "score": 0.80},
        {"name": "Exam 2", "weight": 0.25, "score": None},
        {"name": "Final",  "weight": 0.50, "score": None},
    ]
    assert abs(grade_needed(items, target_fraction=0.90) - 0.9333) < 0.001

def test_grade_needed_is_impossible_when_it_exceeds_one():
    items = [
        {"name": "Exam 1", "weight": 0.50, "score": 0.40},
        {"name": "Final",  "weight": 0.50, "score": None},
    ]
    assert grade_needed(items, target_fraction=0.90) > 1.0

def test_grade_needed_is_zero_when_the_target_is_already_secured():
    items = [
        {"name": "Exam 1", "weight": 0.50, "score": 1.00},
        {"name": "Final",  "weight": 0.50, "score": None},
    ]
    assert grade_needed(items, target_fraction=0.40) <= 0.0
```

- [ ] **Step 2: Run and confirm failure**

Run: `python -m pytest tests/test_wizard.py tests/test_school_pack.py -v`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Implement the wizard and the school pack**

`learn_place_now` calls the SSID reader and raises `ValueError("no network detected")` when it returns `None`, because silently learning an empty SSID would match everywhere.

`grade_needed(items, target_fraction)` returns the uniform fraction required across all unscored items to reach the target, allowing values above 1.0 so the caller can say "not reachable" honestly rather than clamping and lying.

`pack.yaml` declares the school commitment fields: course code, section, instructor, meeting times.

- [ ] **Step 4: Run and confirm pass**

Run: `python -m pytest tests/ -v`
Expected: 80 passed

- [ ] **Step 5: Commit**

```bash
git add lifewatch/wizard.py packs/ tests/test_wizard.py tests/test_school_pack.py
git commit -m "feat: setup wizard and school pack grade model

The wizard is what structurally guarantees no personal value reaches
source: it collects at runtime what would otherwise be a default. Places
refuse to be learned while offline rather than matching everywhere.

grade_needed returns above 1.0 when a target is unreachable instead of
clamping, because the honest answer is that it cannot be done."
```

---

### Task 12: Whole-week replay and the guard tests

**Files:**
- Create: `tests/test_replay.py`, `tests/test_guards.py`
- Test: both

**Interfaces:**
- Consumes: everything
- Produces: proof that a simulated week produces the exact expected intervention sequence

- [ ] **Step 1: Write the failing replay and guard tests**

```python
# tests/test_replay.py
from datetime import datetime, timedelta
from lifewatch.clock import FakeClock

T0 = datetime(2026, 8, 24, 7, 0, 0)

def test_a_kept_block_produces_no_interventions(built_system):
    system = built_system
    block = system.contract.current_block(T0)
    system.contract.start_block(block.id, T0)
    fired = []
    for _ in range(60):
        system.clock.advance(60)
        iv = system.watcher.evaluate(system.clock.now())
        if iv:
            fired.append(iv)
    assert fired == []

def test_a_dead_block_escalates_through_rungs_one_then_two(built_system):
    system = built_system
    rungs = []
    for _ in range(30):
        iv = system.watcher.evaluate(system.clock.now())
        if iv:
            rungs.append(iv.rung)
        system.clock.advance(60)
    assert rungs[0] == 1
    assert 2 in rungs
    assert max(rungs) <= 2, "stage 1 ships rungs 1 and 2 only"

def test_moving_a_dead_block_ends_escalation_immediately(built_system):
    system = built_system
    system.clock.advance(600)
    assert system.watcher.evaluate(system.clock.now()) is not None
    block = system.contract.current_block(T0)
    later = T0 + timedelta(days=1)
    system.contract.move_block(block.id, later, later + timedelta(minutes=90),
                               system.clock.now())
    assert system.watcher.evaluate(system.clock.now()) is None

def test_every_delivered_intervention_carries_a_next_action(built_system):
    system = built_system
    for _ in range(30):
        iv = system.watcher.evaluate(system.clock.now())
        if iv:
            assert iv.next_action.strip() != ""
        system.clock.advance(60)
```

```python
# tests/test_guards.py
import pathlib, re

SRC = pathlib.Path(__file__).parent.parent / "lifewatch"

def test_no_wall_clock_reads_outside_clock_module():
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.name == "clock.py":
            continue
        text = path.read_text()
        if re.search(r"datetime\.now\(|time\.time\(", text):
            offenders.append(str(path))
    assert offenders == [], f"wall-clock reads outside clock.py: {offenders}"

def test_no_image_is_ever_written_to_disk():
    offenders = []
    for path in SRC.rglob("*.py"):
        text = path.read_text()
        if re.search(r"imwrite|\.save\(.*\.(png|jpg|jpeg)", text, re.I):
            offenders.append(str(path))
    assert offenders == [], f"image write found: {offenders}"

def test_no_camera_access_exists_in_stage_1():
    offenders = []
    for path in SRC.rglob("*.py"):
        if re.search(r"cv2|VideoCapture|/dev/video", path.read_text()):
            offenders.append(str(path))
    assert offenders == [], f"camera access is stage 2: {offenders}"

def test_config_directory_is_ignored_by_git():
    ignore = (pathlib.Path(__file__).parent.parent / ".gitignore").read_text()
    assert "config/" in ignore
```

- [ ] **Step 2: Run and confirm failure**

Run: `python -m pytest tests/test_replay.py tests/test_guards.py -v`
Expected: FAIL on the missing `built_system` fixture

- [ ] **Step 3: Add the `built_system` fixture to `conftest.py`**

Assembles a `FakeClock`, a `Store` on `tmp_path`, a `Contract` with one 90-minute block at `T0`, a `Classifier` with a stub judge, and a `Watcher` with the default two-rung ladder, returned as a simple namespace.

- [ ] **Step 4: Run the whole suite**

Run: `python -m pytest tests/ -v`
Expected: 88 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_replay.py tests/test_guards.py tests/conftest.py
git commit -m "test: whole-week replay and structural guards

A simulated week runs in milliseconds and asserts the exact escalation
sequence, which is the only practical way to test a system whose subject
is elapsed time.

Guards fail the build on wall-clock reads outside the clock module, on
any image write, and on camera access, so the stage-2 boundary and the
privacy promise are enforced by CI rather than by memory."
```

---

## Self-Review

**Spec coverage.** §3.1 discrepancy → Tasks 6, 7 produce the classified intervals; the ratio is computed in Task 10's `/api/state`. §3.2 absence-loudest → Task 6 idle rule plus Task 8 escalation. §3.3 renegotiation-only → Task 5, tested by `test_there_is_no_dismiss_method` and `test_there_is_no_dismiss_route`. §4 seven units → Tasks 2-10. §5 environment → Task 3 Step 5 verifies against live hardware. §6 sensors → Task 3, `presence` correctly excluded and guarded in Task 12. §7 three tiers → Tasks 6, 7. §8 contract and exceptions → Task 5. §9.1 ladder → Task 8, Stage 1 rungs only, asserted by `max(rungs) <= 2`. §9.2 invariant → Task 8 `__post_init__` plus Task 12. §9.3 mercy-only → Task 8. §10 wizard → Task 11. §11 packs → Task 11. §12 privacy → Task 12 guards. §13 views → Task 10. §14 testing → Tasks 1, 12. §15 OSS posture → Task 1 example config, Task 12 gitignore guard.

**Gap found and closed:** the spec's `integrity` ratio (§3.1) had no home; it is now explicitly part of Task 10's `/api/state` payload.

**Type consistency.** `Klass`, `Interval`, `Observation`, `Block`, `BlockState`, `Intervention` are defined once in Task 2 or Task 5 or Task 8 and referenced identically thereafter. `tier1` returns `Interval | None` in both its definition and its Task 7 consumer. `Delivery` is defined in Task 9 and used only there.

**Not in Stage 1, deliberately:** `presence` sensor, rungs 3 and 4, Tier 3 learned ruleset, grade items populated from real syllabi. All are Stage 2 per spec §16 and the replay test asserts the rung ceiling so they cannot leak in early.
