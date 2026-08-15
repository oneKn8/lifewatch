import pytest

from lifewatch.config import Config
from lifewatch.wizard import Wizard


def a_wizard(ssid="Test Network", config=None):
    return Wizard(config or Config.empty(), ssid_reader=lambda: ssid)


# -- learning places ------------------------------------------------------


def test_learn_place_now_captures_whatever_ssid_is_live():
    w = Wizard(Config.empty(), ssid_reader=lambda: "Test Network")
    place = w.learn_place_now("home")
    assert place.matcher_value == "Test Network"


def test_learning_a_place_while_offline_fails_loudly():
    w = Wizard(Config.empty(), ssid_reader=lambda: None)
    with pytest.raises(ValueError, match="no network"):
        w.learn_place_now("home")


def test_a_blank_ssid_is_refused_rather_than_matching_everywhere():
    w = Wizard(Config.empty(), ssid_reader=lambda: "   ")
    with pytest.raises(ValueError, match="no network"):
        w.learn_place_now("home")


def test_a_reader_that_cannot_reach_the_os_reports_no_network():
    def broken():
        raise FileNotFoundError("iwgetid: command not found")

    with pytest.raises(ValueError, match="no network"):
        Wizard(Config.empty(), ssid_reader=broken).learn_place_now("home")


def test_a_refused_place_is_not_half_written():
    w = Wizard(Config.empty(), ssid_reader=lambda: None)
    with pytest.raises(ValueError):
        w.learn_place_now("home")
    assert w.config.places == {}


def test_the_wizard_invents_no_places_of_its_own():
    assert a_wizard().config.places == {}


def test_a_learned_place_is_reachable_by_ssid_lookup():
    w = a_wizard()
    w.learn_place_now("home")
    assert w.config.place_for_ssid("Test Network") == "home"


def test_surrounding_whitespace_is_stripped_from_a_live_ssid():
    w = Wizard(Config.empty(), ssid_reader=lambda: "  Test Network\n")
    assert w.learn_place_now("home").matcher_value == "Test Network"


def test_relearning_a_place_replaces_the_old_matcher():
    cfg = Config.empty()
    Wizard(cfg, ssid_reader=lambda: "Old Network").learn_place_now("home")
    Wizard(cfg, ssid_reader=lambda: "New Network").learn_place_now("home")
    assert cfg.places["home"].matcher_value == "New Network"
    assert cfg.place_for_ssid("Old Network") is None


def test_a_place_needs_a_name():
    with pytest.raises(ValueError):
        a_wizard().learn_place_now("  ")


# -- commitments ----------------------------------------------------------


def test_add_commitment_records_the_pack_fields_it_is_given():
    w = a_wizard()
    w.add_commitment(
        "COURSE-101",
        label="Example Course",
        weekly_target_minutes=600,
        section="001",
        instructor="Example Instructor",
    )
    commitment = w.config.commitments[0]
    assert commitment["id"] == "COURSE-101"
    assert commitment["weekly_target_minutes"] == 600
    assert commitment["section"] == "001"
    assert commitment["instructor"] == "Example Instructor"


def test_the_wizard_does_not_validate_pack_fields_it_cannot_know_about():
    w = a_wizard()
    w.add_commitment(
        "COURSE-102",
        label="Another Example",
        weekly_target_minutes=120,
        some_future_pack_field=["anything"],
    )
    assert w.config.commitments[0]["some_future_pack_field"] == ["anything"]


def test_re_adding_a_commitment_replaces_it_instead_of_duplicating_it():
    w = a_wizard()
    w.add_commitment("COURSE-101", label="First", weekly_target_minutes=600)
    w.add_commitment("COURSE-101", label="Corrected", weekly_target_minutes=300)
    assert len(w.config.commitments) == 1
    assert w.config.commitments[0]["label"] == "Corrected"


def test_replacing_a_commitment_keeps_its_position():
    w = a_wizard()
    w.add_commitment("COURSE-101", label="One", weekly_target_minutes=60)
    w.add_commitment("COURSE-102", label="Two", weekly_target_minutes=60)
    w.add_commitment("COURSE-101", label="One Corrected", weekly_target_minutes=60)
    assert [c["id"] for c in w.config.commitments] == ["COURSE-101", "COURSE-102"]
    assert w.config.commitments[0]["label"] == "One Corrected"


def test_a_commitment_with_no_weekly_target_is_refused():
    with pytest.raises(ValueError):
        a_wizard().add_commitment("COURSE-101", label="Example", weekly_target_minutes=0)


def test_a_commitment_with_a_negative_target_is_refused():
    with pytest.raises(ValueError):
        a_wizard().add_commitment("COURSE-101", label="Example", weekly_target_minutes=-60)


def test_a_commitment_needs_an_id_and_a_label():
    w = a_wizard()
    with pytest.raises(ValueError):
        w.add_commitment("  ", label="Example", weekly_target_minutes=60)
    with pytest.raises(ValueError):
        w.add_commitment("COURSE-101", label="", weekly_target_minutes=60)


# -- ladder ---------------------------------------------------------------


def test_set_ladder_stores_the_rungs():
    w = a_wizard()
    w.set_ladder([{"rung": 1, "after_minutes": 0, "effector": "wall"}])
    assert w.config.ladder == [
        {"rung": 1, "after_minutes": 0, "effector": "wall", "requires_response": False}
    ]


def test_set_ladder_orders_rungs_by_when_they_fire():
    w = a_wizard()
    w.set_ladder(
        [
            {"rung": 2, "after_minutes": 5, "effector": "notify"},
            {"rung": 1, "after_minutes": 0, "effector": "wall"},
        ]
    )
    assert [r["after_minutes"] for r in w.config.ladder] == [0, 5]


def test_an_empty_ladder_is_refused_because_it_would_never_escalate():
    with pytest.raises(ValueError):
        a_wizard().set_ladder([])


def test_a_rung_missing_its_effector_is_refused():
    with pytest.raises(ValueError):
        a_wizard().set_ladder([{"rung": 1, "after_minutes": 0}])


def test_a_rung_that_fires_before_the_block_starts_is_refused():
    with pytest.raises(ValueError):
        a_wizard().set_ladder(
            [{"rung": 1, "after_minutes": -5, "effector": "wall"}]
        )


def test_two_rungs_with_the_same_number_are_refused():
    with pytest.raises(ValueError):
        a_wizard().set_ladder(
            [
                {"rung": 1, "after_minutes": 0, "effector": "wall"},
                {"rung": 1, "after_minutes": 5, "effector": "notify"},
            ]
        )


# -- finishing ------------------------------------------------------------


def test_finished_config_contains_no_hardcoded_default_ssid(tmp_path):
    w = Wizard(Config.empty(), ssid_reader=lambda: "Test Network")
    w.learn_place_now("home")
    path = tmp_path / "config.yaml"
    w.finish(path)
    assert "CHANGE-ME" not in path.read_text()


def test_a_finished_config_loads_back_with_what_was_collected(tmp_path):
    w = a_wizard()
    w.learn_place_now("home")
    w.add_commitment("COURSE-101", label="Example Course", weekly_target_minutes=600)
    path = tmp_path / "config.yaml"
    w.finish(path)

    reloaded = Config.load(path)
    assert reloaded.places["home"].matcher_value == "Test Network"
    assert reloaded.commitments[0]["id"] == "COURSE-101"


def test_finish_refuses_to_write_a_surviving_placeholder(tmp_path):
    cfg = Config.empty()
    cfg.classifier = {"backend": "local", "model": "CHANGE-ME-A-3B-CLASS-MODEL"}
    w = Wizard(cfg, ssid_reader=lambda: "Test Network")
    w.learn_place_now("home")
    with pytest.raises(ValueError, match="CHANGE-ME"):
        w.finish(tmp_path / "config.yaml")


def test_a_refused_finish_writes_no_file(tmp_path):
    cfg = Config.empty()
    cfg.classifier = {"model": "CHANGE-ME-A-3B-CLASS-MODEL"}
    path = tmp_path / "config.yaml"
    with pytest.raises(ValueError):
        Wizard(cfg, ssid_reader=lambda: "Test Network").finish(path)
    assert not path.exists()


def test_finish_refuses_a_place_whose_matcher_would_match_everywhere(tmp_path):
    cfg = Config.empty()
    cfg.learn_place("home", ssid="Test Network")
    cfg.places["home"].matcher_value = "   "
    with pytest.raises(ValueError, match="home"):
        Wizard(cfg, ssid_reader=lambda: "Test Network").finish(tmp_path / "config.yaml")


def test_finish_returns_the_path_it_wrote(tmp_path):
    w = a_wizard()
    w.learn_place_now("home")
    path = tmp_path / "nested" / "config.yaml"
    assert w.finish(path) == path
    assert path.exists()


def test_the_finished_config_is_readable_only_by_its_owner(tmp_path):
    w = a_wizard()
    w.learn_place_now("home")
    path = tmp_path / "config.yaml"
    w.finish(path)
    assert path.stat().st_mode & 0o077 == 0


def test_the_wizard_is_rerunnable_over_an_existing_config(tmp_path):
    path = tmp_path / "config.yaml"
    first = a_wizard()
    first.learn_place_now("home")
    first.finish(path)

    second = Wizard(Config.load(path), ssid_reader=lambda: "Test Campus Net")
    second.learn_place_now("campus")
    second.finish(path)

    reloaded = Config.load(path)
    assert reloaded.places["home"].matcher_value == "Test Network"
    assert reloaded.places["campus"].matcher_value == "Test Campus Net"
