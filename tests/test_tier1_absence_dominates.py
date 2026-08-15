"""Regression tests for the single worst bug this system can have.

Background media is classified as AMBIENT on the theory that the person is
listening while working. That theory holds only while there is evidence of
working. Sustained zero input is evidence of the opposite, and it must win.

The bug these tests pin was real: with the media rule ahead of the idle rule,
forty minutes of zero input with a video playing classified as AMBIENT, which
reads as a productive session with background music. That is precisely the
failure the whole project exists to catch, scored as success.

See design spec section 3.2: absence is the loudest signal, not the quietest.
"""

from datetime import datetime, timedelta

from lifewatch.classify.tier1 import tier1
from lifewatch.models import Klass, Observation

T0 = datetime(2026, 8, 24, 7, 0, 0)
THRESHOLD_S = 900


def obs(kind, value, sensor="window", offset=0, meta=None):
    return Observation(T0 + timedelta(seconds=offset), sensor, kind, value, meta or {})


def test_media_playing_with_no_input_for_forty_minutes_is_absent_not_ambient():
    observations = [
        obs("focus", "TestEditor|problem-set.pdf"),
        obs("media", "playing"),
        obs("ms", str(40 * 60 * 1000), sensor="idle"),
    ]
    result = tier1(observations, block=object(), now=T0 + timedelta(minutes=40),
                   idle_threshold_s=THRESHOLD_S)
    assert result is not None
    assert result.klass is Klass.ABSENT, (
        "media playing to an empty chair is absence, not ambience"
    )


def test_media_playing_with_recent_input_is_still_ambient():
    """The ambient rule must survive the fix, or study music becomes a crime."""
    observations = [
        obs("focus", "TestEditor|problem-set.pdf", meta={"pid": 1111}),
        obs("media", "playing", meta={"pid": 4750}),
        obs("ms", "30000", sensor="idle"),
    ]
    result = tier1(observations, block=object(), now=T0 + timedelta(minutes=5),
                   idle_threshold_s=THRESHOLD_S)
    assert result is not None
    assert result.klass is Klass.AMBIENT


def test_absence_wins_at_exactly_the_threshold_with_media_playing():
    observations = [
        obs("focus", "TestEditor|problem-set.pdf"),
        obs("media", "playing"),
        obs("ms", str(THRESHOLD_S * 1000), sensor="idle"),
    ]
    result = tier1(observations, block=object(), now=T0 + timedelta(seconds=THRESHOLD_S),
                   idle_threshold_s=THRESHOLD_S)
    assert result is not None
    assert result.klass is Klass.ABSENT


def test_absence_still_detected_with_no_media_at_all():
    observations = [obs("ms", str(40 * 60 * 1000), sensor="idle")]
    result = tier1(observations, block=object(), now=T0 + timedelta(minutes=40),
                   idle_threshold_s=THRESHOLD_S)
    assert result is not None
    assert result.klass is Klass.ABSENT


def test_an_accounted_place_still_outranks_absence():
    """Being idle in class is not a failure to study, it is class."""
    observations = [
        obs("place", "campus", sensor="network"),
        obs("ms", str(40 * 60 * 1000), sensor="idle"),
    ]
    result = tier1(observations, block=object(), now=T0 + timedelta(minutes=40),
                   accounted_places={"campus"}, idle_threshold_s=THRESHOLD_S)
    assert result is not None
    assert result.klass is Klass.ACCOUNTED
