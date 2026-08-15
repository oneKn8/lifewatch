from datetime import datetime, timedelta

import importlib

from lifewatch.classify.tier1 import tier1

# The package re-exports the FUNCTION tier1, which shadows the SUBMODULE of the
# same name: `import lifewatch.classify.tier1 as m` resolves the attribute and
# hands back the function. importlib asks for the module by name, which is the
# only form that is not ambiguous here.
tier1_module = importlib.import_module("lifewatch.classify.tier1")
from lifewatch.models import Block, Klass, Observation

T0 = datetime(2026, 8, 24, 7, 0, 0)


FOCUS_PID = 1111
MEDIA_PID = 4750


# Real sensors report the owning process, because Tier 1 attributes a playing
# media source by process identity rather than by name: MPRIS bus names and X11
# window classes are different namespaces and never compare equal as strings.
# The helper supplies distinct default pids so the common case reads as "the
# player is not the focused window"; a test wanting the opposite, or wanting no
# attribution at all, passes meta explicitly.
_DEFAULT_PIDS = {"focus": FOCUS_PID, "media": MEDIA_PID}


def obs(kind, value, sensor="window", offset=0, meta=None):
    if meta is None:
        meta = dict.fromkeys(["pid"], _DEFAULT_PIDS[kind]) if kind in _DEFAULT_PIDS else {}
    return Observation(T0 + timedelta(seconds=offset), sensor, kind, value, meta)


def a_block(start=T0, minutes=90):
    return Block(id="b1", commitment_id="COURSE-101", planned_start=start,
                 planned_end=start + timedelta(minutes=minutes))


# -- the rules the plan specifies -------------------------------------------


def test_media_in_the_background_is_ambient_not_drift():
    observations = [
        obs("focus", "TestEditor|problem-set.pdf"),
        obs("media", "playing", sensor="window", meta={"pid": 4750}),
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


# -- the background-media rule, in both directions ---------------------------


def test_the_media_source_is_identified_in_the_reason_when_the_sensor_knows_it():
    observations = [
        obs("focus", "TestEditor|problem-set.pdf", meta={"pid": FOCUS_PID}),
        obs("media", "playing", meta={"app": "TestPlayer", "pid": MEDIA_PID}),
    ]
    result = tier1(observations, block=object(), now=T0)
    assert result.klass is Klass.AMBIENT
    assert str(MEDIA_PID) in result.reason


def test_the_same_process_playing_and_focused_is_left_for_tier2():
    """A video in the focused window is not background listening.

    Tier 1 declines and Tier 2 reads the title. Identity is the process, not the
    name: this browser publishes MPRIS as "chromium" while setting WM_CLASS to
    "Google-chrome", so a string compare never matched and every full-screen
    video was wrongly credited as ambient.
    """
    observations = [
        obs("focus", "Google-chrome|Some Video Title", meta={"pid": 4750}),
        obs("media", "playing", meta={"app": "chromium", "pid": 4750}),
    ]
    assert tier1(observations, block=object(), now=T0) is None


def test_a_player_running_as_a_child_of_the_focused_window_is_left_for_tier2(monkeypatch):
    """Browsers publish MPRIS from a renderer child of the window's process.

    Equality alone would call those two different applications and credit the
    video as background listening, so the check walks the process ancestry.
    """
    monkeypatch.setattr(tier1_module, "_parent_pid", {4751: 4750}.get)
    observations = [
        obs("focus", "Google-chrome|Some Video Title", meta={"pid": 4750}),
        obs("media", "playing", meta={"app": "chromium", "pid": 4751}),
    ]
    assert tier1(observations, block=object(), now=T0) is None


def test_an_unrelated_process_playing_is_still_ambient(monkeypatch):
    """The ancestry walk must not swallow genuinely separate applications."""
    monkeypatch.setattr(tier1_module, "_parent_pid", {}.get)
    observations = [
        obs("focus", "TestEditor|problem-set.pdf", meta={"pid": 1111}),
        obs("media", "playing", meta={"app": "testplayer", "pid": 4750}),
    ]
    assert tier1(observations, block=object(), now=T0).klass is Klass.AMBIENT


def test_media_with_no_attributable_process_is_declined_not_credited():
    """An unproven ambient claim is the same mistake as the name comparison."""
    observations = [
        obs("focus", "TestEditor|problem-set.pdf", meta={"pid": 1111}),
        obs("media", "playing", meta={"app": "testplayer"}),
    ]
    assert tier1(observations, block=object(), now=T0) is None


def test_media_playing_with_nothing_focused_decides_nothing():
    assert tier1([obs("media", "playing")], block=object(), now=T0) is None


def test_paused_media_does_not_make_the_hour_ambient():
    observations = [
        obs("focus", "TestEditor|problem-set.pdf"),
        obs("media", "paused"),
    ]
    assert tier1(observations, block=object(), now=T0) is None


def test_media_that_has_stopped_playing_no_longer_counts():
    observations = [
        obs("focus", "TestEditor|problem-set.pdf"),
        obs("media", "playing", offset=10, meta={"pid": 4750}),
        obs("media", "stopped", offset=600),
    ]
    assert tier1(observations, block=object(), now=T0 + timedelta(seconds=900)) is None


def test_the_ambient_span_starts_when_both_facts_hold():
    observations = [
        obs("focus", "TestEditor|problem-set.pdf", offset=60),
        obs("media", "playing", offset=300, meta={"pid": 4750}),
    ]
    now = T0 + timedelta(seconds=900)
    result = tier1(observations, block=object(), now=now)
    assert result.start == T0 + timedelta(seconds=300)
    assert result.end == now


# -- what this tier refuses to do -------------------------------------------


def test_tier1_never_accuses_anyone_of_drift():
    cases = [
        [obs("focus", "TestBrowser|Some Video Title")],
        [obs("focus", "TestPlayer|Some Video Title"),
         obs("media", "playing", meta={"app": "TestPlayer", "pid": 4750})],
        [obs("focus", "TestGame|Test Game Session"),
         obs("ms", "0", sensor="idle")],
        [obs("place", "unknown", sensor="network")],
    ]
    for observations in cases:
        result = tier1(observations, block=object(), now=T0)
        assert result is None or result.klass is not Klass.DRIFT


def test_the_reason_never_carries_a_window_title():
    observations = [
        obs("focus", "TestEditor|problem-set.pdf"),
        obs("media", "playing"),
    ]
    reason = tier1(observations, block=object(), now=T0).reason
    assert "TestEditor" in reason
    assert "problem-set.pdf" not in reason


def test_no_observations_at_all_decides_nothing():
    assert tier1([], block=object(), now=T0) is None


def test_observations_from_the_future_are_ignored():
    future = [obs("place", "campus", sensor="network", offset=3600)]
    assert tier1(future, block=None, now=T0, accounted_places={"campus"}) is None
    later = tier1(future, block=None, now=T0 + timedelta(seconds=3600),
                  accounted_places={"campus"})
    assert later.klass is Klass.ACCOUNTED


def test_an_unparseable_idle_value_does_not_crash_the_tier():
    assert tier1([obs("ms", "not-a-number", sensor="idle")],
                 block=object(), now=T0) is None


# -- the accounted-place rule ------------------------------------------------


def test_a_place_that_is_not_tagged_accounted_decides_nothing():
    observations = [obs("place", "home", sensor="network")]
    assert tier1(observations, block=object(), now=T0,
                 accounted_places={"campus"}) is None


def test_leaving_an_accounted_place_ends_the_accounting():
    observations = [
        obs("place", "campus", sensor="network", offset=0),
        obs("place", "home", sensor="network", offset=600),
    ]
    result = tier1(observations, block=object(), now=T0 + timedelta(seconds=900),
                   accounted_places={"campus"})
    assert result is None


def test_an_accounted_place_outranks_a_long_idle():
    observations = [
        obs("place", "campus", sensor="network"),
        obs("ms", str(20 * 60 * 1000), sensor="idle"),
    ]
    result = tier1(observations, block=a_block(), now=T0, accounted_places={"campus"})
    assert result.klass is Klass.ACCOUNTED


def test_a_single_accounted_place_given_as_a_bare_string_is_not_matched_by_prefix():
    observations = [obs("place", "cam", sensor="network")]
    assert tier1(observations, block=object(), now=T0,
                 accounted_places="campus") is None


# -- the idle rule -----------------------------------------------------------


def test_idle_outside_any_declared_block_is_not_absence():
    observations = [obs("ms", str(20 * 60 * 1000), sensor="idle")]
    assert tier1(observations, block=None, now=T0) is None


def test_the_absent_span_covers_the_time_actually_spent_idle():
    observations = [obs("ms", str(20 * 60 * 1000), sensor="idle")]
    result = tier1(observations, block=object(), now=T0)
    assert result.start == T0 - timedelta(minutes=20)
    assert result.end == T0


def test_an_interval_never_starts_before_the_block_it_judges():
    block = a_block(start=T0)
    now = T0 + timedelta(minutes=5)
    observations = [obs("ms", str(20 * 60 * 1000), sensor="idle", offset=5 * 60)]
    result = tier1(observations, block=block, now=now)
    assert result.klass is Klass.ABSENT
    assert result.start == T0


def test_the_threshold_is_configurable():
    observations = [obs("ms", str(120 * 1000), sensor="idle")]
    assert tier1(observations, block=object(), now=T0, idle_threshold_s=60).klass \
        is Klass.ABSENT
    assert tier1(observations, block=object(), now=T0, idle_threshold_s=900) is None


def test_idle_exactly_at_the_threshold_counts_as_absent():
    observations = [obs("ms", str(900 * 1000), sensor="idle")]
    assert tier1(observations, block=object(), now=T0).klass is Klass.ABSENT


def test_a_milliseconds_reading_from_another_sensor_is_not_read_as_idleness():
    observations = [obs("ms", str(20 * 60 * 1000), sensor="latency")]
    assert tier1(observations, block=object(), now=T0) is None


# -- interval shape ----------------------------------------------------------


def test_every_returned_interval_is_well_formed():
    cases = [
        ([obs("place", "campus", sensor="network")], {"accounted_places": {"campus"}}),
        ([obs("focus", "TestEditor|problem-set.pdf"), obs("media", "playing")], {}),
        ([obs("ms", str(20 * 60 * 1000), sensor="idle")], {}),
    ]
    for observations, kwargs in cases:
        result = tier1(observations, block=object(), now=T0, **kwargs)
        assert result is not None
        assert result.tier == 1
        assert result.start <= result.end == T0
        assert result.reason.strip() != ""
