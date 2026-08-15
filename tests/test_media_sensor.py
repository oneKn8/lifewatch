"""Media sensor tests.

The reader is injected, so this file runs on a machine with no D-Bus session
bus and no media player. Nothing here uses a real track, a real artist or a
real URL, and the privacy tests below assert that no such thing could reach an
observation even if a player were broadcasting one.
"""

from datetime import datetime

import pytest

from lifewatch.sensors import media
from lifewatch.sensors.media import (
    NONE,
    PAUSED,
    PLAYING,
    STOPPED,
    MediaSensor,
    read_media_state,
)

T0 = datetime(2026, 8, 24, 7, 0, 0)

STATE_WORDS = {PLAYING, PAUSED, STOPPED, NONE}

# A synthetic service name shaped exactly like a real MPRIS bus name.
TEST_SERVICE = "org.mpris.MediaPlayer2.testplayer.instance101"
OTHER_SERVICE = "org.mpris.MediaPlayer2.othertestplayer"


class ReaderExploded(Exception):
    """A failure type the sensor has never heard of."""


def synthetic_pid(service):
    """A stable, obviously fake pid per bus name.

    Distinct services get distinct pids, which is what the sensor now uses to
    attribute a reading. Derived rather than hand-assigned so a test that adds a
    service does not have to remember to pick a free number.
    """
    return 900000 + (abs(hash(service)) % 1000)


def scripted(*players):
    """A reader returning a fixed list of (service, PlaybackStatus, pid) triples.

    Accepts 2-tuples and fills in a synthetic pid, so tests that do not care
    about attribution stay readable.
    """
    normalized = [
        player if len(player) == 3 else (player[0], player[1], synthetic_pid(player[0]))
        for player in players
    ]
    return lambda: list(normalized)


def one(sensor):
    obs = sensor.poll(T0)
    assert len(obs) == 1
    return obs[0]


# -- the four state words ----------------------------------------------------


def test_a_playing_player_reads_as_playing():
    obs = one(MediaSensor(reader=scripted((TEST_SERVICE, "Playing"))))
    assert obs.sensor == "media"
    assert obs.kind == "media"
    assert obs.value == PLAYING


def test_a_paused_player_reads_as_paused():
    assert one(MediaSensor(reader=scripted((TEST_SERVICE, "Paused")))).value == PAUSED


def test_a_stopped_player_reads_as_stopped():
    assert one(MediaSensor(reader=scripted((TEST_SERVICE, "Stopped")))).value == STOPPED


def test_no_player_at_all_reads_as_none():
    assert one(MediaSensor(reader=scripted())).value == NONE


def test_the_value_is_always_one_of_the_four_state_words():
    scripts = [
        (),
        ((TEST_SERVICE, "Playing"),),
        ((TEST_SERVICE, "Paused"),),
        ((TEST_SERVICE, "Stopped"),),
        ((TEST_SERVICE, "playing"),),
        ((TEST_SERVICE, "Buffering"),),
        ((TEST_SERVICE, ""),),
        ((TEST_SERVICE, "Paused"), (OTHER_SERVICE, "Playing")),
    ]
    for script in scripts:
        value = one(MediaSensor(reader=scripted(*script))).value
        assert value in STATE_WORDS, f"{script} produced {value!r}"


def test_the_timestamp_is_the_one_the_runner_supplied():
    assert one(MediaSensor(reader=scripted((TEST_SERVICE, "Playing")))).ts == T0


# -- several players ---------------------------------------------------------


def test_one_playing_player_outranks_several_paused_ones():
    sensor = MediaSensor(
        reader=scripted(
            (TEST_SERVICE, "Paused"),
            (OTHER_SERVICE, "Playing"),
        )
    )
    assert one(sensor).value == PLAYING


def test_paused_outranks_stopped():
    sensor = MediaSensor(
        reader=scripted(
            (TEST_SERVICE, "Stopped"),
            (OTHER_SERVICE, "Paused"),
        )
    )
    assert one(sensor).value == PAUSED


def test_an_unrecognised_status_is_never_read_as_playing():
    sensor = MediaSensor(reader=scripted((TEST_SERVICE, "Buffering")))
    assert one(sensor).value == STOPPED


# -- source attribution ------------------------------------------------------


def test_the_playing_application_is_named_so_a_focused_player_can_be_told_apart():
    obs = one(MediaSensor(reader=scripted((TEST_SERVICE, "Playing"))))
    assert obs.meta["app"] == "testplayer"


def test_the_instance_suffix_is_not_part_of_the_application_name():
    obs = one(MediaSensor(reader=scripted((OTHER_SERVICE, "Playing"))))
    assert obs.meta["app"] == "othertestplayer"


def test_two_windows_of_the_same_application_still_attribute_to_it():
    """Two windows, one process: the reading is attributable.

    Attribution is by pid rather than by name, because MPRIS bus names and X11
    window classes are different namespaces and comparing them as strings never
    matched. Two windows of one application share an owning process.
    """
    sensor = MediaSensor(
        reader=scripted(
            ("org.mpris.MediaPlayer2.testplayer.instance1", "Playing", 4750),
            ("org.mpris.MediaPlayer2.testplayer.instance2", "Playing", 4750),
        )
    )
    meta = one(sensor).meta
    assert meta["pid"] == 4750
    assert meta["app"] == "testplayer"


def test_two_different_applications_playing_leaves_the_source_unattributed():
    sensor = MediaSensor(
        reader=scripted(
            (TEST_SERVICE, "Playing"),
            (OTHER_SERVICE, "Playing"),
        )
    )
    assert "app" not in one(sensor).meta


def test_the_attributed_application_is_the_one_in_the_reported_state():
    sensor = MediaSensor(
        reader=scripted(
            (TEST_SERVICE, "Paused"),
            (OTHER_SERVICE, "Playing"),
        )
    )
    assert one(sensor).meta["app"] == "othertestplayer"


def test_nothing_is_attributed_when_there_is_no_player():
    assert one(MediaSensor(reader=scripted())).meta == {}


def test_a_bus_name_that_is_not_mpris_shaped_is_not_attributed():
    sensor = MediaSensor(reader=scripted(("org.mpris.MediaPlayer2.", "Playing")))
    obs = one(sensor)
    assert obs.value == PLAYING
    assert "app" not in obs.meta


# -- privacy -----------------------------------------------------------------

# Every key MPRIS Metadata would offer if it were ever read. None may appear.
FORBIDDEN_META_KEYS = {
    "title",
    "xesam:title",
    "artist",
    "xesam:artist",
    "album",
    "xesam:album",
    "url",
    "xesam:url",
    "track",
    "trackid",
    "metadata",
    "art",
    "arturl",
    "mpris:arturl",
}


def test_the_observation_carries_no_title_like_key():
    for script in [
        ((TEST_SERVICE, "Playing"),),
        ((TEST_SERVICE, "Paused"),),
        ((TEST_SERVICE, "Stopped"),),
        (),
    ]:
        obs = one(MediaSensor(reader=scripted(*script)))
        keys = {k.lower() for k in obs.meta}
        assert keys & FORBIDDEN_META_KEYS == set()
        # Positive form of the same rule: the owning process and its name are
        # the only things this sensor is allowed to say beyond the state word.
        # Neither identifies WHAT is playing, only which process is playing it.
        assert keys <= {"app", "pid"}


def test_the_state_word_carries_no_free_text_from_the_player():
    # A player is a program on the far side of D-Bus and may answer anything.
    # Whatever it answers, the recorded value stays a state word.
    hostile = "Playing http://example.invalid/watch?v=TEST - Some Track Title"
    obs = one(MediaSensor(reader=scripted((TEST_SERVICE, hostile))))
    assert obs.value in STATE_WORDS
    assert "example.invalid" not in obs.value
    assert "example.invalid" not in repr(obs.meta)


def test_the_default_reader_never_asks_for_metadata(monkeypatch):
    """The privacy promise is structural: Metadata is never a command argument."""
    calls = []

    class Result:
        returncode = 0
        stdout = f"{TEST_SERVICE} 1 testplayer user :1.1 - - -\n"
        stderr = ""

    class StatusResult:
        returncode = 0
        stdout = 's "Playing"\n'
        stderr = ""

    def fake_run(args, **kwargs):
        calls.append(list(args))
        return Result() if "list" in args else StatusResult()

    monkeypatch.setattr(media.subprocess, "run", fake_run)
    assert read_media_state() == [(TEST_SERVICE, "Playing", 1)]
    flat = " ".join(" ".join(c) for c in calls)
    assert "Metadata" not in flat
    assert "PlaybackStatus" in flat


# -- availability ------------------------------------------------------------


def test_the_sensor_is_unavailable_when_the_reader_raises():
    def broken():
        raise OSError("no session bus")

    assert MediaSensor(reader=broken).available() is False


def test_the_sensor_is_unavailable_on_any_exception_type():
    def broken():
        raise ReaderExploded("the bus said no")

    assert MediaSensor(reader=broken).available() is False


def test_the_sensor_is_available_when_the_reader_answers():
    assert MediaSensor(reader=scripted()).available() is True


def test_the_reader_is_called_once_by_available():
    calls = []

    def counting():
        calls.append(1)
        return []

    MediaSensor(reader=counting).available()
    assert len(calls) == 1


def test_a_poll_failure_travels_up_to_the_runner():
    def broken():
        raise ReaderExploded("the bus said no")

    with pytest.raises(ReaderExploded):
        MediaSensor(reader=broken).poll(T0)


def test_the_poll_interval_matches_the_window_sensor():
    # Ambience is only meaningful next to what had focus at the same moment,
    # so the two sensors have to sample at the same rate.
    assert MediaSensor(reader=scripted()).poll_interval_s == 15


# -- the default reader's parsing --------------------------------------------

# Captured from `busctl --user list --acquired --no-legend` on the target
# machine, with the service names replaced by synthetic ones.
LISTING = (
    "ca.desrt.dconf                    1923 dconf-service   u :1.41 - - -\n"
    f"{TEST_SERVICE}                   4750 testplayer      u :1.99 - - -\n"
    "org.freedesktop.Notifications     1812 gnome-shell     u :1.37 - - -\n"
)


def test_the_listing_parser_keeps_only_mpris_services():
    assert media._mpris_services(LISTING) == [(TEST_SERVICE, 4750)]


def test_the_listing_parser_survives_an_empty_bus():
    assert media._mpris_services("") == []


def test_the_status_parser_reads_the_quoted_word():
    assert media._playback_status('s "Playing"\n') == "Playing"


def test_the_status_parser_returns_none_for_an_unexpected_answer():
    assert media._playback_status("that is not a property\n") is None


def test_a_player_that_vanishes_between_the_two_calls_is_skipped(monkeypatch):
    """A player quitting mid-read is a race, not a broken bus."""

    class Listing:
        returncode = 0
        stdout = LISTING
        stderr = ""

    class Gone:
        returncode = 1
        stdout = ""
        stderr = "Failed to get property PlaybackStatus: The name is not activatable"

    monkeypatch.setattr(
        media.subprocess,
        "run",
        lambda args, **kw: Listing() if "list" in args else Gone(),
    )
    assert read_media_state() == []


def test_a_missing_bus_tool_raises_so_the_sensor_reports_unavailable(monkeypatch):
    class Failed:
        returncode = 1
        stdout = ""
        stderr = "no session bus"

    monkeypatch.setattr(media.subprocess, "run", lambda args, **kw: Failed())
    with pytest.raises(OSError):
        read_media_state()


# -- registration ------------------------------------------------------------


def test_the_media_sensor_is_one_of_the_default_sensors():
    from lifewatch.config import Config
    from lifewatch.sensors import default_sensors

    names = [s.name for s in default_sensors(Config.empty())]
    assert "media" in names


def test_the_media_sensor_is_exported_from_the_package():
    from lifewatch.sensors import MediaSensor as Exported

    assert Exported is MediaSensor


def test_the_media_sensor_satisfies_the_sensor_protocol():
    from lifewatch.sensors import Sensor

    assert isinstance(MediaSensor(reader=scripted()), Sensor)
