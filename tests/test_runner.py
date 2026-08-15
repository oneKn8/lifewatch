"""Sensor runner tests.

The runner is the only unit that touches every sensor, so it is the one place
a single broken instrument could take the whole log down. Most of what follows
is about that: fault isolation, and not paying for a sensor twice.
"""

from datetime import datetime, timedelta

from lifewatch.clock import FakeClock
from lifewatch.models import Observation
from lifewatch.sensors.runner import Runner
from lifewatch.store import Store

T0 = datetime(2026, 8, 24, 7, 0, 0)


class ScriptedSensor:
    """Emits one prepared value per poll and counts what was asked of it."""

    name = "window"
    poll_interval_s = 15

    def __init__(self, values, meta=None):
        self.values = list(values)
        self.meta = meta
        self.available_calls = 0
        self.poll_calls = 0
        self.polled_at = []

    def available(self):
        self.available_calls += 1
        return True

    def poll(self, now):
        self.poll_calls += 1
        self.polled_at.append(now)
        if not self.values:
            return []
        return [Observation(now, "window", "focus", self.values.pop(0), self.meta or {})]


class Dead:
    """A sensor this machine cannot answer with at all."""

    name = "dead"
    poll_interval_s = 15

    def __init__(self):
        self.available_calls = 0

    def available(self):
        self.available_calls += 1
        return False

    def poll(self, now):
        raise AssertionError("an unavailable sensor must not be polled")


class Exploding:
    """A sensor that is present and broken, which is the interesting case."""

    name = "boom"
    poll_interval_s = 15

    def __init__(self, fail_times=None):
        self.available_calls = 0
        self.poll_calls = 0
        self.fail_times = fail_times

    def available(self):
        self.available_calls += 1
        return True

    def poll(self, now):
        self.poll_calls += 1
        if self.fail_times is None or self.poll_calls <= self.fail_times:
            raise RuntimeError("sensor failed")
        return [Observation(now, "boom", "state", f"ok-{self.poll_calls}", {})]


def make(tmp_path, sensors, **kwargs):
    clock = FakeClock(T0)
    store = Store(tmp_path / "t.db", clock)
    return Runner(sensors, store, clock, **kwargs), store, clock


# -- writing -----------------------------------------------------------------


def test_runner_writes_observations_to_the_store(tmp_path):
    runner, store, clock = make(tmp_path, [ScriptedSensor(["a", "b"])])
    assert runner.tick() == 1
    clock.advance(15)
    assert runner.tick() == 1
    assert [o.value for o in store.observations(T0, clock.now())] == ["a", "b"]


def test_a_sensor_with_nothing_to_say_writes_nothing(tmp_path):
    runner, store, clock = make(tmp_path, [ScriptedSensor([])])
    assert runner.tick() == 0
    assert store.observations(T0, clock.now()) == []


def test_the_sensor_is_polled_with_the_clock_time(tmp_path):
    sensor = ScriptedSensor(["a", "b"])
    runner, _store, clock = make(tmp_path, [sensor])
    runner.tick()
    clock.advance(15)
    runner.tick()
    assert sensor.polled_at == [T0, T0 + timedelta(seconds=15)]


# -- change suppression ------------------------------------------------------


def test_runner_suppresses_an_unchanged_value_until_the_heartbeat(tmp_path):
    runner, store, clock = make(
        tmp_path, [ScriptedSensor(["same", "same", "same"])], heartbeat_s=600
    )
    runner.tick()
    clock.advance(15)
    runner.tick()
    assert len(store.observations(T0, clock.now())) == 1


def test_runner_re_records_an_unchanged_value_after_the_heartbeat(tmp_path):
    runner, store, clock = make(
        tmp_path, [ScriptedSensor(["same"] * 3)], heartbeat_s=600
    )
    runner.tick()
    clock.advance(601)
    runner.tick()
    assert len(store.observations(T0, clock.now())) == 2


def test_the_heartbeat_is_measured_from_the_last_write_not_the_last_change(tmp_path):
    runner, store, clock = make(
        tmp_path, [ScriptedSensor(["same"] * 4)], heartbeat_s=600
    )
    runner.tick()
    clock.advance(601)
    runner.tick()  # heartbeat write
    clock.advance(15)
    runner.tick()  # too soon for another
    assert len(store.observations(T0, clock.now())) == 2


def test_each_sensor_and_kind_keeps_its_own_suppression_state(tmp_path):
    class TwoKinds:
        name = "two"
        poll_interval_s = 15

        def available(self):
            return True

        def poll(self, now):
            return [
                Observation(now, "two", "left", "steady", {}),
                Observation(now, "two", "right", str(now), {}),
            ]

    runner, store, clock = make(tmp_path, [TwoKinds()])
    assert runner.tick() == 2
    clock.advance(15)
    assert runner.tick() == 1  # only the changing kind
    kinds = [o.kind for o in store.observations(T0, clock.now())]
    assert kinds.count("left") == 1
    assert kinds.count("right") == 2


def test_a_changed_meta_is_recorded_even_when_the_value_is_unchanged(tmp_path):
    """The media sensor's state word can hold while its source changes.

    ``playing`` attributed to a background player and ``playing`` attributed to
    the focused window are opposite verdicts in Tier 1, so suppressing the
    second as a repeat would silently keep the wrong one on the record.
    """
    sensor = ScriptedSensor(["playing"], meta={"app": "first"})
    runner, store, clock = make(tmp_path, [sensor], heartbeat_s=600)
    assert runner.tick() == 1
    sensor.values = ["playing"]
    sensor.meta = {"app": "second"}
    clock.advance(15)
    assert runner.tick() == 1
    apps = [o.meta.get("app") for o in store.observations(T0, clock.now())]
    assert apps == ["first", "second"]


# -- availability ------------------------------------------------------------


def test_runner_skips_unavailable_sensors_without_failing(tmp_path):
    runner, _store, _clock = make(tmp_path, [Dead()])
    assert runner.tick() == 0


def test_availability_is_probed_once_across_many_ticks(tmp_path):
    """Probing costs real work: WindowSensor.available() spawns two processes.

    At a 15 second cadence, re-probing every tick would be roughly 23,000
    subprocess spawns a day on the laptop the user is trying to study on.
    """
    sensor = ScriptedSensor(["v"] * 50)
    runner, _store, clock = make(tmp_path, [sensor])
    for _ in range(20):
        runner.tick()
        clock.advance(15)
    assert sensor.available_calls == 1
    assert sensor.poll_calls == 20


def test_an_unavailable_sensor_is_not_probed_again_either(tmp_path):
    dead = Dead()
    runner, _store, clock = make(tmp_path, [dead])
    for _ in range(5):
        runner.tick()
        clock.advance(15)
    assert dead.available_calls == 1


def test_availability_is_rechecked_after_a_poll_failure(tmp_path):
    broken = Exploding()
    runner, _store, clock = make(tmp_path, [broken])
    runner.tick()
    assert broken.available_calls == 1
    clock.advance(15)
    runner.tick()
    assert broken.available_calls == 2


def test_a_sensor_that_recovers_is_recorded_again(tmp_path):
    recovering = Exploding(fail_times=1)
    runner, store, clock = make(tmp_path, [recovering])
    assert runner.tick() == 0
    clock.advance(15)
    assert runner.tick() == 1
    assert [o.value for o in store.observations(T0, clock.now())] == ["ok-2"]


# -- fault isolation ---------------------------------------------------------


def test_one_failing_sensor_does_not_stop_the_others(tmp_path):
    runner, _store, _clock = make(
        tmp_path, [Exploding(), ScriptedSensor(["ok"])]
    )
    assert runner.tick() == 1


def test_a_sensor_failing_first_still_lets_a_later_one_write(tmp_path):
    good = ScriptedSensor(["ok"])
    runner, store, clock = make(tmp_path, [Exploding(), good, Dead()])
    runner.tick()
    assert [o.value for o in store.observations(T0, clock.now())] == ["ok"]


def test_a_sensor_failure_is_logged_rather_than_swallowed(tmp_path, caplog):
    """An instrument that dies quietly is worse than no instrument."""
    runner, _store, _clock = make(tmp_path, [Exploding()])
    with caplog.at_level("ERROR"):
        runner.tick()
    assert "boom" in caplog.text
