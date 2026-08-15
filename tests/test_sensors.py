"""Sensor tests.

Every reader is injected, so this file runs with no X11 server, no wireless
interface and no ``xprop`` on PATH. That is the whole reason the readers are
parameters rather than calls: the suite has to be runnable on a machine that
looks nothing like the target one, and a Wayland contributor has to be able to
replace one reader without touching anything above it.

Nothing here uses a real network name, a real application or a real document.
"""

from datetime import datetime

import pytest

from lifewatch.config import Config
from lifewatch.models import Observation
from lifewatch.sensors import FakeSensor, Sensor
from lifewatch.sensors import network, window
from lifewatch.sensors.idle import IdleSensor
from lifewatch.sensors.network import NetworkSensor
from lifewatch.sensors.window import WindowSensor

T0 = datetime(2026, 8, 24, 7, 0, 0)


class ReaderExploded(Exception):
    """A failure type the sensors have never heard of."""


def exploding_reader():
    raise ReaderExploded("the OS said no")


# -- window -----------------------------------------------------------------


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


def test_window_sensor_is_unavailable_on_any_exception_type():
    assert WindowSensor(reader=exploding_reader).available() is False


def test_window_sensor_is_available_when_the_reader_answers():
    assert WindowSensor(reader=lambda: ("TestClass", "Test Title")).available() is True


def test_an_unfocused_desktop_is_not_a_broken_sensor():
    """No focused window is an answer, not a failure. X11 replied."""
    assert WindowSensor(reader=lambda: None).available() is True


def test_available_consults_the_reader_exactly_once():
    calls = []

    def counting_reader():
        calls.append(1)
        return ("TestClass", "Test Title")

    WindowSensor(reader=counting_reader).available()
    assert len(calls) == 1


def test_window_sensor_stamps_the_time_it_was_given():
    obs = WindowSensor(reader=lambda: ("TestClass", "Test Title")).poll(T0)
    assert obs[0].ts == T0


def test_window_sensor_keeps_class_and_title_separable_in_meta():
    """A pipe in a title must not cost the classifier the application name."""
    obs = WindowSensor(reader=lambda: ("TestClass", "part one | part two")).poll(T0)
    assert obs[0].value == "TestClass|part one | part two"
    assert obs[0].meta["wm_class"] == "TestClass"
    assert obs[0].meta["title"] == "part one | part two"


def test_window_sensor_kind_is_focus():
    assert WindowSensor(reader=lambda: ("TestClass", "T")).poll(T0)[0].kind == "focus"


def test_window_sensor_passes_a_poll_failure_up_to_the_runner():
    """A sensor reports its own failure honestly; the runner isolates it.

    Swallowing the error here would make a dead sensor indistinguishable from
    an idle desktop, which is the exact silent-instrument failure the design
    refuses.
    """
    with pytest.raises(ReaderExploded):
        WindowSensor(reader=exploding_reader).poll(T0)


# -- the default X11 reader -------------------------------------------------
#
# Injection means this parser is the one part of the unit no other test
# exercises, so it is driven here against captured xprop output shapes rather
# than against a live display.


def fake_xprop(root_output, prop_output):
    def run(args, tolerate_failure=False):
        return root_output if args[0] == "-root" else prop_output
    return run


ACTIVE = "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x4400007\n"


def test_the_reader_parses_a_title_and_the_application_class(monkeypatch):
    monkeypatch.setattr(window, "_xprop", fake_xprop(ACTIVE, (
        '_NET_WM_NAME(UTF8_STRING) = "Test Document Title"\n'
        'WM_NAME(COMPOUND_TEXT) = "Test Document Title"\n'
        'WM_CLASS(STRING) = "test-instance", "TestClass"\n'
    )))
    assert window.read_active_window() == ("TestClass", "Test Document Title", None)


def test_the_reader_prefers_the_class_over_the_instance(monkeypatch):
    """Applications share a class across windows; a rule wants that one."""
    monkeypatch.setattr(window, "_xprop", fake_xprop(ACTIVE, (
        '_NET_WM_NAME(UTF8_STRING) = "Test Title"\n'
        'WM_CLASS(STRING) = "test-instance", "TestClass"\n'
    )))
    assert window.read_active_window()[0] == "TestClass"


def test_the_reader_falls_back_to_wm_name_on_older_applications(monkeypatch):
    monkeypatch.setattr(window, "_xprop", fake_xprop(ACTIVE, (
        "_NET_WM_NAME:  not found.\n"
        'WM_NAME(STRING) = "Old Test Application"\n'
        'WM_CLASS(STRING) = "test-instance", "TestClass"\n'
    )))
    assert window.read_active_window() == ("TestClass", "Old Test Application", None)


def test_the_reader_survives_a_title_containing_quotes(monkeypatch):
    monkeypatch.setattr(window, "_xprop", fake_xprop(ACTIVE, (
        '_NET_WM_NAME(UTF8_STRING) = "A \\"quoted\\" test title"\n'
        'WM_CLASS(STRING) = "test-instance", "TestClass"\n'
    )))
    assert window.read_active_window()[1] == 'A "quoted" test title'


def test_the_reader_restores_an_escaped_newline_rather_than_the_letter_n(monkeypatch):
    monkeypatch.setattr(window, "_xprop", fake_xprop(ACTIVE, (
        '_NET_WM_NAME(UTF8_STRING) = "line one\\nline two"\n'
        'WM_CLASS(STRING) = "test-instance", "TestClass"\n'
    )))
    assert window.read_active_window()[1] == "line one\nline two"


def test_the_reader_reports_nothing_focused_as_none(monkeypatch):
    """X11 answers 0x0 on the bare desktop. That is an answer, not a failure."""
    monkeypatch.setattr(window, "_xprop", fake_xprop(
        "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x0\n", ""))
    assert window.read_active_window() is None


def test_the_reader_reports_none_when_the_root_property_is_absent(monkeypatch):
    monkeypatch.setattr(window, "_xprop", fake_xprop(
        "_NET_ACTIVE_WINDOW:  not found.\n", ""))
    assert window.read_active_window() is None


def test_a_window_that_vanishes_mid_read_is_a_race_not_a_failure(monkeypatch):
    monkeypatch.setattr(window, "_xprop", fake_xprop(ACTIVE, None))
    assert window.read_active_window() is None


def test_a_window_with_neither_name_nor_class_is_not_reported(monkeypatch):
    monkeypatch.setattr(window, "_xprop", fake_xprop(ACTIVE, (
        "_NET_WM_NAME:  not found.\n"
        "WM_CLASS:  not found.\n"
    )))
    assert window.read_active_window() is None


class FailedRun:
    returncode = 1
    stdout = ""
    stderr = "xprop:  unable to open display ':99'"


def test_a_broken_display_raises_rather_than_reading_as_an_empty_desk(monkeypatch):
    """available() rests on this: silence and failure must not look alike."""
    monkeypatch.setattr(window.subprocess, "run", lambda *a, **k: FailedRun())
    with pytest.raises(OSError):
        window._xprop(["-root", "_NET_ACTIVE_WINDOW"])


def test_a_tolerated_failure_answers_none_instead_of_raising(monkeypatch):
    monkeypatch.setattr(window.subprocess, "run", lambda *a, **k: FailedRun())
    assert window._xprop(["-id", "0x1", "WM_CLASS"], tolerate_failure=True) is None


# -- idle -------------------------------------------------------------------


def test_idle_sensor_reports_milliseconds():
    obs = IdleSensor(reader=lambda: 79656).poll(T0)
    assert obs[0].sensor == "idle"
    assert obs[0].value == "79656"


def test_idle_sensor_kind_is_ms():
    assert IdleSensor(reader=lambda: 1).poll(T0)[0].kind == "ms"


def test_idle_sensor_reports_zero_rather_than_nothing():
    """Active input is a fact worth recording, not an absence of one."""
    assert IdleSensor(reader=lambda: 0).poll(T0)[0].value == "0"


def test_idle_sensor_does_not_decide_whether_that_is_a_lot():
    """The sensor holds no threshold. Judgment belongs to the classifier."""
    long_idle = IdleSensor(reader=lambda: 60 * 60 * 1000).poll(T0)[0]
    assert long_idle.value == "3600000"
    assert long_idle.kind == "ms"


def test_idle_sensor_emits_an_integer_string_the_classifier_can_parse():
    assert IdleSensor(reader=lambda: 1234.7).poll(T0)[0].value == "1234"


def test_idle_sensor_stamps_the_time_it_was_given():
    assert IdleSensor(reader=lambda: 5).poll(T0)[0].ts == T0


def test_idle_sensor_is_unavailable_when_the_reader_raises():
    assert IdleSensor(reader=exploding_reader).available() is False


def test_idle_sensor_is_available_when_the_reader_answers():
    assert IdleSensor(reader=lambda: 0).available() is True


# -- network ----------------------------------------------------------------


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


def test_network_sensor_kind_and_name():
    obs = NetworkSensor(Config.empty(), reader=lambda: None).poll(T0)
    assert obs[0].sensor == "network"
    assert obs[0].kind == "place"


def test_the_observation_log_never_holds_the_network_name_itself():
    """The place is the fact the system needs; the SSID is only how it was found.

    An SSID is a location identifier with no downstream use, and the log is the
    artefact most likely to be handed to someone else while debugging.
    """
    cfg = Config.empty()
    cfg.learn_place("home", ssid="Test Network")
    obs = NetworkSensor(cfg, reader=lambda: "Test Network").poll(T0)
    assert "Test Network" not in repr(obs[0])


def test_an_unlearned_network_name_is_not_logged_either():
    obs = NetworkSensor(Config.empty(), reader=lambda: "Some Other Network").poll(T0)
    assert "Some Other Network" not in repr(obs[0])


def test_network_sensor_sees_places_learned_after_it_was_built():
    """The wizard is re-runnable, so the sensor must hold the config live."""
    cfg = Config.empty()
    sensor = NetworkSensor(cfg, reader=lambda: "Test Network")
    assert sensor.poll(T0)[0].value == "unknown"
    cfg.learn_place("home", ssid="Test Network")
    assert sensor.poll(T0)[0].value == "home"


def test_network_sensor_stamps_the_time_it_was_given():
    assert NetworkSensor(Config.empty(), reader=lambda: None).poll(T0)[0].ts == T0


def test_network_sensor_is_unavailable_when_the_reader_raises():
    assert NetworkSensor(Config.empty(), reader=exploding_reader).available() is False


def test_being_offline_is_not_being_unavailable():
    assert NetworkSensor(Config.empty(), reader=lambda: None).available() is True


def test_the_wireless_tool_is_found_even_when_it_is_off_the_path(monkeypatch, tmp_path):
    """A systemd user service has no sbin on its PATH; the tool lives there."""
    tool = tmp_path / "iwgetid"
    tool.write_text("#!/bin/sh\necho\n")
    tool.chmod(0o755)
    monkeypatch.setattr(network.shutil, "which", lambda name: None)
    monkeypatch.setattr(network, "_SBIN_FALLBACKS", (str(tool),))
    assert network._iwgetid_path() == str(tool)


def test_the_path_wins_when_the_tool_is_on_it(monkeypatch):
    monkeypatch.setattr(network.shutil, "which", lambda name: "/usr/bin/iwgetid")
    monkeypatch.setattr(network, "_SBIN_FALLBACKS", ("/somewhere/else/iwgetid",))
    assert network._iwgetid_path() == "/usr/bin/iwgetid"


def test_a_machine_without_the_tool_falls_through_to_a_loud_failure(monkeypatch):
    """The bare name makes subprocess raise, which is what unavailable means."""
    monkeypatch.setattr(network.shutil, "which", lambda name: None)
    monkeypatch.setattr(network, "_SBIN_FALLBACKS", ("/nonexistent/iwgetid",))
    assert network._iwgetid_path() == "iwgetid"


# -- the protocol and its double --------------------------------------------


def test_poll_intervals_match_the_cost_of_each_question():
    """Focus and input change by the second; a place changes by the hour."""
    assert WindowSensor(reader=lambda: None).poll_interval_s == 15
    assert IdleSensor(reader=lambda: 0).poll_interval_s == 15
    assert NetworkSensor(Config.empty(), reader=lambda: None).poll_interval_s == 60


def test_each_sensor_names_itself_after_the_question_it_answers():
    assert WindowSensor(reader=lambda: None).name == "window"
    assert IdleSensor(reader=lambda: 0).name == "idle"
    assert NetworkSensor(Config.empty(), reader=lambda: None).name == "network"


def test_every_sensor_satisfies_the_protocol():
    sensors = [
        WindowSensor(reader=lambda: None),
        IdleSensor(reader=lambda: 0),
        NetworkSensor(Config.empty(), reader=lambda: None),
        FakeSensor("window", []),
    ]
    for sensor in sensors:
        assert isinstance(sensor, Sensor)


def test_fake_sensor_emits_its_script_one_poll_at_a_time():
    first = Observation(T0, "window", "focus", "TestClass|First", {})
    second = Observation(T0, "window", "focus", "TestClass|Second", {})
    sensor = FakeSensor("window", [first, second])
    assert sensor.poll(T0) == [first]
    assert sensor.poll(T0) == [second]
    assert sensor.poll(T0) == []


def test_fake_sensor_does_not_consume_the_callers_list():
    script = [Observation(T0, "window", "focus", "TestClass|Only", {})]
    FakeSensor("window", script).poll(T0)
    assert len(script) == 1


def test_fake_sensor_takes_the_name_it_is_given():
    assert FakeSensor("idle", []).name == "idle"


def test_fake_sensor_is_available_by_default():
    assert FakeSensor("window", []).available() is True


def test_fake_sensor_can_be_scripted_as_unavailable():
    """So callers can prove they skip a sensor that cannot run here."""
    assert FakeSensor("window", [], is_available=False).available() is False
