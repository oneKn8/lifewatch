from lifewatch.config import Config


def test_learn_place_captures_the_ssid_it_is_given():
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


def test_place_for_ssid_finds_the_learned_name():
    cfg = Config.empty()
    cfg.learn_place("home", ssid="Test Network")
    assert cfg.place_for_ssid("Test Network") == "home"


def test_place_for_an_unlearned_ssid_is_none():
    assert Config.empty().place_for_ssid("Test Network") is None


def test_relearning_a_place_replaces_the_old_ssid():
    cfg = Config.empty()
    cfg.learn_place("home", ssid="Old Network")
    cfg.learn_place("home", ssid="New Network")
    assert cfg.places["home"].matcher_value == "New Network"
    assert cfg.place_for_ssid("Old Network") is None


def test_ladder_and_passes_survive_the_round_trip(tmp_path):
    cfg = Config.empty()
    cfg.passes_per_week = 3
    cfg.ladder = [{"rung": 1, "after_minutes": 0, "effector": "wall"}]
    path = tmp_path / "config.yaml"
    cfg.save(path)
    reloaded = Config.load(path)
    assert reloaded.passes_per_week == 3
    assert reloaded.ladder[0]["effector"] == "wall"


def test_the_shipped_example_config_contains_no_real_network_name():
    from pathlib import Path

    example = (Path(__file__).parent.parent / "config.example.yaml").read_text()
    assert "CHANGE-ME-RUN-THE-WIZARD" in example
