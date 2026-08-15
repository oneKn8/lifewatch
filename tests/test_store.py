from datetime import datetime, timedelta

from lifewatch.clock import FakeClock
from lifewatch.models import Interval, Klass, Observation
from lifewatch.store import Store

T0 = datetime(2026, 8, 24, 7, 0, 0)


def make_store(tmp_path):
    return Store(tmp_path / "test.db", FakeClock(T0))


def test_appended_observation_comes_back(tmp_path):
    store = make_store(tmp_path)
    store.append(Observation(ts=T0, sensor="window", kind="focus",
                             value="Test App|Test Document", meta={}))
    got = store.observations(T0 - timedelta(minutes=1), T0 + timedelta(minutes=1))
    assert len(got) == 1
    assert got[0].value == "Test App|Test Document"


def test_observations_outside_the_range_are_excluded(tmp_path):
    store = make_store(tmp_path)
    store.append(Observation(T0, "window", "focus", "early", {}))
    store.append(Observation(T0 + timedelta(hours=2), "window", "focus", "late", {}))
    got = store.observations(T0 - timedelta(minutes=1), T0 + timedelta(minutes=1))
    assert [o.value for o in got] == ["early"]


def test_observations_come_back_in_time_order(tmp_path):
    store = make_store(tmp_path)
    store.append(Observation(T0 + timedelta(minutes=5), "window", "focus", "second", {}))
    store.append(Observation(T0, "window", "focus", "first", {}))
    got = store.observations(T0 - timedelta(minutes=1), T0 + timedelta(minutes=10))
    assert [o.value for o in got] == ["first", "second"]


def test_observations_can_be_filtered_by_sensor(tmp_path):
    store = make_store(tmp_path)
    store.append(Observation(T0, "window", "focus", "a window", {}))
    store.append(Observation(T0, "idle", "ms", "1000", {}))
    got = store.observations(T0 - timedelta(minutes=1), T0 + timedelta(minutes=1),
                             sensor="idle")
    assert [o.value for o in got] == ["1000"]


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
    got = store.observations(T0 - timedelta(seconds=1), T0 + timedelta(seconds=1))
    assert got[0].meta["wm_class"] == "TestClass"


def test_intervals_round_trip(tmp_path):
    store = make_store(tmp_path)
    store.put_interval(Interval(T0, T0 + timedelta(minutes=15), Klass.ALIGNED,
                                tier=1, reason="focused on target"))
    got = store.intervals(T0, T0 + timedelta(hours=1))
    assert got[0].klass is Klass.ALIGNED
    assert got[0].tier == 1
    assert got[0].reason == "focused on target"


def test_the_store_exposes_no_way_to_rewrite_history(tmp_path):
    store = make_store(tmp_path)
    for forbidden in ("update", "delete", "edit", "remove", "clear"):
        assert not hasattr(store, forbidden), f"Store must not expose {forbidden}"


def test_a_reopened_store_still_has_its_observations(tmp_path):
    path = tmp_path / "test.db"
    first = Store(path, FakeClock(T0))
    first.append(Observation(T0, "window", "focus", "persisted", {}))
    first.close()
    second = Store(path, FakeClock(T0))
    got = second.observations(T0 - timedelta(minutes=1), T0 + timedelta(minutes=1))
    assert [o.value for o in got] == ["persisted"]
