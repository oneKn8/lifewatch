"""Intervals must tile time, never overlap.

The tiers anchor a verdict at the observation that justifies it. That is the
right answer to "when did this become true" and the wrong answer to "what span
does this record cover", because the sensor runner suppresses unchanged values:
the same observation is the evidence on every tick until the next heartbeat.

Anchoring the recorded span there re-counts the same minutes once per tick. The
bug these tests pin was measured at 55 minutes of recorded intervals for 10
minutes of elapsed time.

That matters because spec section 3.1 defines integrity as
``aligned_minutes / claimed_minutes``. An inflated numerator does not surface as
an obvious defect. It surfaces as a good day, which is the one failure mode this
whole instrument exists to prevent.
"""

from datetime import datetime, timedelta

import pytest

from lifewatch.classify import Classifier
from lifewatch.classify.tier3 import AskQueue
from lifewatch.clock import FakeClock
from lifewatch.config import Config
from lifewatch.models import Block, BlockState, Observation
from lifewatch.store import Store

T0 = datetime(2026, 8, 24, 7, 0, 0)


@pytest.fixture
def classifier(tmp_path):
    store = Store(tmp_path / "tile.db", FakeClock(T0))
    return Classifier(Config.empty(), AskQueue(store), judge=lambda prompt: "aligned")


def a_block():
    return Block(
        id="b1",
        commitment_id="COURSE-101",
        planned_start=T0,
        planned_end=T0 + timedelta(minutes=90),
        state=BlockState.RUNNING,
    )


def steady_evidence():
    """One unchanged reading, exactly as the runner's suppression produces."""
    return [
        Observation(T0, "window", "focus", "TestEditor|problem-set.pdf", {"pid": 1111}),
        Observation(T0, "idle", "ms", "5000", {}),
    ]


def test_recorded_minutes_never_exceed_elapsed_minutes(classifier):
    block, evidence = a_block(), steady_evidence()
    recorded = timedelta()
    for minute in range(1, 11):
        interval = classifier.classify(evidence, block, now=T0 + timedelta(minutes=minute))
        recorded += interval.end - interval.start

    elapsed = timedelta(minutes=10)
    assert recorded <= elapsed, (
        f"recorded {recorded} for {elapsed} elapsed: intervals are overlapping"
    )


def test_consecutive_intervals_do_not_overlap(classifier):
    block, evidence = a_block(), steady_evidence()
    intervals = [
        classifier.classify(evidence, block, now=T0 + timedelta(minutes=minute))
        for minute in range(1, 8)
    ]
    for earlier, later in zip(intervals, intervals[1:]):
        assert later.start >= earlier.end, (
            f"{later.start} starts before {earlier.end} ended"
        )


def test_an_interval_never_starts_after_it_ends(classifier):
    block, evidence = a_block(), steady_evidence()
    for minute in range(1, 8):
        interval = classifier.classify(evidence, block, now=T0 + timedelta(minutes=minute))
        assert interval.start <= interval.end


def test_an_explicit_since_is_honoured(classifier):
    interval = classifier.classify(
        steady_evidence(),
        a_block(),
        now=T0 + timedelta(minutes=30),
        since=T0 + timedelta(minutes=25),
    )
    assert interval.start >= T0 + timedelta(minutes=25)


def test_a_restart_does_not_reopen_a_closed_span(tmp_path):
    """A fresh Classifier recovers where the record left off.

    Without this, every daemon restart would re-count everything back to the
    oldest surviving observation.
    """
    store = Store(tmp_path / "tile.db", FakeClock(T0))
    first = Classifier(Config.empty(), AskQueue(store), judge=lambda prompt: "aligned")
    block, evidence = a_block(), steady_evidence()

    early = first.classify(evidence, block, now=T0 + timedelta(minutes=20))
    store.put_interval(early)

    second = Classifier(Config.empty(), AskQueue(store), judge=lambda prompt: "aligned")
    later = second.classify(evidence, block, now=T0 + timedelta(minutes=25))
    assert later.start >= early.end


def test_a_clock_that_goes_backwards_does_not_produce_a_negative_span(classifier):
    block, evidence = a_block(), steady_evidence()
    classifier.classify(evidence, block, now=T0 + timedelta(minutes=30))
    backwards = classifier.classify(evidence, block, now=T0 + timedelta(minutes=10))
    assert backwards.start <= backwards.end
