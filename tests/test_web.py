"""The API, driven through the real routes.

Everything is injected, so these exercise the actual FastAPI app against a
FakeClock and a temporary store rather than a mock of it.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from lifewatch.classify.tier3 import AskQueue
from lifewatch.clock import FakeClock
from lifewatch.config import Config
from lifewatch.contract import Contract
from lifewatch.models import Interval, Klass
from lifewatch.store import Store
from lifewatch.watcher import Watcher
from lifewatch.web.app import create_app

T0 = datetime(2026, 8, 24, 7, 0, 0)


@pytest.fixture
def rig(tmp_path):
    clock = FakeClock(T0)
    config = Config.empty()
    config.passes_per_week = 1
    config.commitments = [
        {"id": "COURSE-101", "label": "Example Course",
         "next_actions": ["example problem set 2, question 4"]}
    ]
    config.consequence_chain = ["hours not spent", "an example outcome"]
    store = Store(tmp_path / "web.db", clock)
    contract = Contract(config, clock)
    ask_queue = AskQueue(store)
    watcher = Watcher(contract, store, config, clock)
    block = contract.add_block("COURSE-101", T0, T0 + timedelta(minutes=90))
    app = create_app(contract, store, watcher, ask_queue, config, clock)
    return type("Rig", (), {
        "client": TestClient(app), "clock": clock, "contract": contract,
        "store": store, "block": block, "config": config,
    })


def test_state_reports_the_current_block(rig):
    body = rig.client.get("/api/state").json()
    assert body["current_block"]["commitment_id"] == "COURSE-101"


def test_starting_a_block_changes_its_state(rig):
    assert rig.client.post(f"/api/block/{rig.block.id}/start").status_code == 200
    assert rig.client.get("/api/state").json()["current_block"]["state"] == "running"


def test_completing_a_block_changes_its_state(rig):
    rig.client.post(f"/api/block/{rig.block.id}/start")
    assert rig.client.post(f"/api/block/{rig.block.id}/complete").status_code == 200
    assert rig.client.get("/api/state").json()["current_block"]["state"] == "completed"


def test_there_is_no_dismiss_route(rig):
    """The design decision, enforced by the router rather than by discipline."""
    assert rig.client.post(f"/api/block/{rig.block.id}/dismiss").status_code == 404


def test_moving_a_block_requires_a_destination(rig):
    """A move with nowhere to move to would be a dismiss under another name."""
    assert rig.client.post(f"/api/block/{rig.block.id}/move", json={}).status_code == 422


def test_a_move_with_a_destination_succeeds(rig):
    later = T0 + timedelta(days=1)
    response = rig.client.post(
        f"/api/block/{rig.block.id}/move",
        json={"new_start": later.isoformat(), "new_end": (later + timedelta(minutes=90)).isoformat()},
    )
    assert response.status_code == 200
    assert response.json()["state"] == "planned"


def test_pass_decrements_and_excuses_the_current_block(rig):
    before = rig.client.get("/api/state").json()["passes_remaining"]
    assert rig.client.post("/api/pass", json={}).json()["spent"] is True
    after = rig.client.get("/api/state").json()
    assert after["passes_remaining"] == before - 1
    assert after["current_block"]["state"] == "excused"


def test_sick_mode_silences(rig):
    rig.client.post("/api/sick", json={"hours": 24})
    assert rig.client.get("/api/state").json()["silenced"] is True


def test_grid_returns_only_waking_hours(rig):
    rows = rig.client.get("/api/grid", params={
        "start": "2026-08-24T00:00:00", "end": "2026-08-25T00:00:00"}).json()
    hours = {datetime.fromisoformat(r["hour"]).hour for r in rows}
    assert min(hours) >= 6 and max(hours) < 23


def test_a_passed_waking_hour_with_nothing_recorded_reads_as_gone(rig):
    rig.clock.advance(6 * 3600)
    rows = rig.client.get("/api/grid", params={
        "start": "2026-08-24T00:00:00", "end": "2026-08-24T13:00:00"}).json()
    assert any(r["klass"] == "gone" for r in rows), (
        "unrecorded past hours must fill red without the user's cooperation"
    )


def test_a_future_hour_is_pending_not_gone(rig):
    rows = rig.client.get("/api/grid", params={
        "start": "2026-08-24T00:00:00", "end": "2026-08-24T23:00:00"}).json()
    late = [r for r in rows if datetime.fromisoformat(r["hour"]).hour == 22]
    assert late and late[0]["klass"] == "pending"


def test_recorded_aligned_time_shows_in_the_grid_and_the_tally(rig):
    rig.store.put_interval(
        Interval(T0, T0 + timedelta(hours=1), Klass.ALIGNED, tier=1, reason="test")
    )
    rig.clock.advance(2 * 3600)
    body = rig.client.get("/api/state").json()
    assert body["banked_minutes"] == 60

    rows = rig.client.get("/api/grid", params={
        "start": "2026-08-24T00:00:00", "end": "2026-08-24T12:00:00"}).json()
    seven = [r for r in rows if datetime.fromisoformat(r["hour"]).hour == 7]
    assert seven and seven[0]["klass"] == "aligned"


def test_integrity_is_none_when_nothing_was_claimed(rig, tmp_path):
    clock = FakeClock(T0)
    config = Config.empty()
    store = Store(tmp_path / "empty.db", clock)
    contract = Contract(config, clock)
    queue = AskQueue(store)
    app = create_app(contract, store, Watcher(contract, store, config, clock),
                     queue, config, clock)
    assert TestClient(app).get("/api/state").json()["integrity"] is None


def test_an_unknown_class_is_rejected(rig):
    assert rig.client.post("/api/questions/1", json={"klass": "nonsense"}).status_code == 422


def test_both_views_are_served(rig):
    assert rig.client.get("/").status_code == 200
    assert rig.client.get("/wall").status_code == 200


def test_the_wall_view_carries_no_interactive_controls(rig):
    """Spec 13.1: the wall accepts no input. The phone is the control surface."""
    html = rig.client.get("/wall").text
    for control in ["<button", "<input", "<form", "<a href"]:
        assert control not in html, f"wall view contains {control}"
