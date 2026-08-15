from datetime import datetime, timedelta

from lifewatch.clock import FakeClock, SystemClock


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


def test_fake_clock_accepts_fractional_seconds():
    clock = FakeClock(datetime(2026, 8, 24, 7, 0, 0))
    clock.advance(0.5)
    assert clock.now() == datetime(2026, 8, 24, 7, 0, 0, 500000)


def test_system_clock_returns_a_datetime():
    assert isinstance(SystemClock().now(), datetime)
