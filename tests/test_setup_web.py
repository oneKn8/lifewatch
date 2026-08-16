"""First-run setup, driven through the real routes.

The point under test is not that a form works. It is that the only way a place
enters the config is by someone standing in it and pressing a button, because
that is what structurally keeps network names out of the source tree.
"""

import pytest
from fastapi.testclient import TestClient

from lifewatch.config import Config
from lifewatch.web.setup import create_setup_app


@pytest.fixture
def rig(tmp_path):
    path = tmp_path / "config" / "config.yaml"
    app = create_setup_app(path, ssid_reader=lambda: "Test Network")
    return TestClient(app), path


@pytest.fixture
def offline_rig(tmp_path):
    path = tmp_path / "config" / "config.yaml"
    app = create_setup_app(path, ssid_reader=lambda: None)
    return TestClient(app), path


def test_state_reports_the_network_currently_visible(rig):
    client, _ = rig
    assert client.get("/api/setup/state").json()["visible_network"] == "Test Network"


def test_a_place_captures_the_live_network_rather_than_a_typed_one(rig):
    client, _ = rig
    body = client.post("/api/setup/place", json={"name": "home"}).json()
    assert body["matcher_value"] == "Test Network"


def test_learning_a_place_while_offline_is_refused(offline_rig):
    """An empty matcher would be a place that matches everywhere."""
    client, _ = offline_rig
    response = client.post("/api/setup/place", json={"name": "home"})
    assert response.status_code == 409
    assert "network" in response.json()["detail"].lower()


def test_a_commitment_carries_the_packs_own_fields(rig):
    client, _ = rig
    body = client.post("/api/setup/commitment", json={
        "id": "COURSE-101", "label": "Example Course",
        "weekly_target_minutes": 360,
        "fields": {"course_code": "COURSE-101", "section": "001"},
    }).json()
    assert body["section"] == "001"


def test_a_commitment_with_no_target_is_refused(rig):
    client, _ = rig
    response = client.post("/api/setup/commitment", json={
        "id": "COURSE-101", "label": "Example Course", "weekly_target_minutes": 0})
    assert response.status_code == 422


def test_an_empty_ladder_is_refused(rig):
    """A system that never escalates is the passive dashboard this replaces."""
    client, _ = rig
    assert client.post("/api/setup/ladder", json={"rungs": []}).status_code == 422


def test_the_pack_declares_the_fields_rather_than_the_engine(rig):
    client, _ = rig
    keys = {f["key"] for f in client.get("/api/setup/state").json()["pack_fields"]}
    assert "course_code" in keys


def test_finish_writes_a_config_that_loads_back(rig):
    client, path = rig
    client.post("/api/setup/place", json={"name": "home"})
    client.post("/api/setup/commitment", json={
        "id": "COURSE-101", "label": "Example Course", "weekly_target_minutes": 360})
    client.post("/api/setup/ladder", json={"rungs": [
        {"rung": 1, "after_minutes": 0, "effector": "wall"}]})
    client.post("/api/setup/settings", json={"passes_per_week": 1})

    assert client.post("/api/setup/finish").status_code == 200
    assert path.exists()

    reloaded = Config.load(path)
    assert reloaded.places["home"].matcher_value == "Test Network"
    assert reloaded.commitments[0]["id"] == "COURSE-101"


def test_the_written_config_is_owner_only(rig):
    client, path = rig
    client.post("/api/setup/place", json={"name": "home"})
    client.post("/api/setup/ladder", json={"rungs": [
        {"rung": 1, "after_minutes": 0, "effector": "wall"}]})
    client.post("/api/setup/finish")
    assert (path.stat().st_mode & 0o077) == 0, (
        "the one file holding places, courses and the consequence chain must "
        "not be readable by other accounts"
    )


def test_the_setup_page_is_served(rig):
    client, _ = rig
    assert client.get("/").status_code == 200
