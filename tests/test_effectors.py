from datetime import datetime, timedelta

import pytest

from lifewatch.clock import FakeClock
from lifewatch.config import Config
from lifewatch.effectors import Delivery, Effector, deliver_all
from lifewatch.effectors.notify import NotifyEffector, post_via_httpx
from lifewatch.effectors.wall import WallEffector
from lifewatch.models import Intervention
from lifewatch.store import Store

T0 = datetime(2026, 8, 24, 7, 0, 0)


def an_intervention(rung=2):
    return Intervention(rung=rung, block_id="b1", message="Block is dead.",
                        next_action="COURSE-101 problem set 2, question 4",
                        requires_response=False)


def a_store(tmp_path, clock=None):
    return Store(tmp_path / "t.db", clock or FakeClock(T0))


def a_notify_config(url="http://example.invalid/test-topic"):
    cfg = Config.empty()
    cfg.notify_url = url
    return cfg


# -- the four cases the plan names -----------------------------------------


def test_wall_effector_records_the_escalation_state(tmp_path):
    store = a_store(tmp_path)
    wall = WallEffector(store)
    assert wall.deliver(an_intervention()).ok is True
    assert wall.current_state()["rung"] == 2


def test_notify_effector_sends_the_next_action_in_the_body(tmp_path):
    sent = {}

    def poster(url, body, title):
        sent.update(url=url, body=body, title=title)
        return True

    cfg = a_notify_config()
    assert NotifyEffector(cfg, poster=poster).deliver(an_intervention()).ok is True
    assert "problem set 2, question 4" in sent["body"]


def test_notify_effector_reports_failure_rather_than_raising(tmp_path):
    def broken(url, body, title):
        raise ConnectionError("unreachable")

    cfg = a_notify_config()
    assert NotifyEffector(cfg, poster=broken).deliver(an_intervention()).ok is False


def test_notify_effector_is_unavailable_without_a_configured_url():
    assert NotifyEffector(Config.empty(), poster=lambda *a: True).available() is False


# -- the protocol and its record -------------------------------------------


def test_both_effectors_satisfy_the_effector_protocol(tmp_path):
    assert isinstance(WallEffector(a_store(tmp_path)), Effector)
    assert isinstance(NotifyEffector(a_notify_config(), poster=lambda *a: True), Effector)


def test_a_delivery_names_the_effector_that_produced_it(tmp_path):
    assert WallEffector(a_store(tmp_path)).deliver(an_intervention()).effector == "wall"
    notify = NotifyEffector(a_notify_config(), poster=lambda *a: True)
    assert notify.deliver(an_intervention()).effector == "notify"


def test_a_delivery_needs_no_detail_when_it_succeeded():
    assert Delivery(effector="wall", ok=True).detail == ""


# -- wall ------------------------------------------------------------------


def test_wall_effector_is_always_available(tmp_path):
    assert WallEffector(a_store(tmp_path)).available() is True


def test_wall_state_is_none_before_anything_has_escalated(tmp_path):
    assert WallEffector(a_store(tmp_path)).current_state() is None


def test_wall_state_carries_the_next_action(tmp_path):
    wall = WallEffector(a_store(tmp_path))
    wall.deliver(an_intervention())
    state = wall.current_state()
    assert state["next_action"] == "COURSE-101 problem set 2, question 4"
    assert state["message"] == "Block is dead."
    assert state["block_id"] == "b1"


def test_wall_state_shows_the_most_recent_rung(tmp_path):
    wall = WallEffector(a_store(tmp_path))
    wall.deliver(an_intervention(rung=1))
    wall.deliver(an_intervention(rung=2))
    assert wall.current_state()["rung"] == 2


def test_wall_stamps_the_injected_clock_not_wall_time(tmp_path):
    clock = FakeClock(T0)
    wall = WallEffector(a_store(tmp_path, clock))
    clock.advance(3600)
    wall.deliver(an_intervention())
    assert wall.current_state()["ts"] == T0 + timedelta(hours=1)


def test_wall_keeps_every_escalation_not_just_the_last(tmp_path):
    store = a_store(tmp_path)
    wall = WallEffector(store)
    wall.deliver(an_intervention(rung=1))
    wall.deliver(an_intervention(rung=2))
    rows = store.conn.execute("SELECT COUNT(*) FROM escalation").fetchone()[0]
    assert rows == 2


def test_wall_reports_failure_rather_than_raising_when_the_store_is_dead(tmp_path):
    store = a_store(tmp_path)
    wall = WallEffector(store)
    store.close()
    result = wall.deliver(an_intervention())
    assert result.ok is False
    assert result.detail != ""


# -- notify ----------------------------------------------------------------


def test_notify_effector_is_available_once_a_url_is_configured():
    assert NotifyEffector(a_notify_config(), poster=lambda *a: True).available() is True


def test_notify_posts_to_the_configured_url():
    sent = {}

    def poster(url, body, title):
        sent.update(url=url, body=body, title=title)
        return True

    NotifyEffector(a_notify_config("http://example.invalid/topic-x"),
                   poster=poster).deliver(an_intervention())
    assert sent["url"] == "http://example.invalid/topic-x"


def test_notify_body_carries_the_message_as_well_as_the_next_action():
    sent = {}

    def poster(url, body, title):
        sent.update(body=body)
        return True

    NotifyEffector(a_notify_config(), poster=poster).deliver(an_intervention())
    assert "Block is dead." in sent["body"]
    assert "COURSE-101 problem set 2, question 4" in sent["body"]


def test_notify_title_is_never_empty():
    sent = {}

    def poster(url, body, title):
        sent.update(title=title)
        return True

    NotifyEffector(a_notify_config(), poster=poster).deliver(an_intervention())
    assert sent["title"].strip() != ""


def test_notify_reports_failure_when_the_poster_says_it_did_not_send():
    result = NotifyEffector(a_notify_config(),
                            poster=lambda *a: False).deliver(an_intervention())
    assert result.ok is False


def test_notify_does_not_post_when_no_url_is_configured():
    calls = []

    def poster(url, body, title):
        calls.append(url)
        return True

    result = NotifyEffector(Config.empty(), poster=poster).deliver(an_intervention())
    assert result.ok is False
    assert calls == []


def test_notify_explains_why_a_delivery_failed():
    def broken(url, body, title):
        raise TimeoutError("read timed out")

    result = NotifyEffector(a_notify_config(), poster=broken).deliver(an_intervention())
    assert "read timed out" in result.detail


def test_notify_treats_a_poster_that_returns_nothing_as_a_failure():
    result = NotifyEffector(a_notify_config(),
                            poster=lambda *a: None).deliver(an_intervention())
    assert result.ok is False


def test_the_default_poster_is_the_http_one():
    assert NotifyEffector(a_notify_config()).poster is post_via_httpx


# -- delivering to several channels at once --------------------------------


def test_deliver_all_skips_effectors_that_are_unavailable(tmp_path):
    wall = WallEffector(a_store(tmp_path))
    dead = NotifyEffector(Config.empty(), poster=lambda *a: True)
    results = deliver_all([wall, dead], an_intervention())
    assert [d.effector for d in results] == ["wall"]


def test_one_exploding_effector_does_not_stop_the_others(tmp_path):
    class Exploding:
        name = "boom"

        def available(self):
            return True

        def deliver(self, iv):
            raise RuntimeError("channel failed")

    wall = WallEffector(a_store(tmp_path))
    results = deliver_all([Exploding(), wall], an_intervention())
    assert [(d.effector, d.ok) for d in results] == [("boom", False), ("wall", True)]


def test_an_effector_whose_availability_check_explodes_is_skipped(tmp_path):
    class Cursed:
        name = "cursed"

        def available(self):
            raise OSError("cannot tell")

        def deliver(self, iv):  # pragma: no cover - must never be reached
            raise AssertionError("must not be delivered to")

    wall = WallEffector(a_store(tmp_path))
    results = deliver_all([Cursed(), wall], an_intervention())
    assert [d.effector for d in results] == ["wall"]


def test_every_channel_receives_the_next_action(tmp_path):
    """The point of the whole unit, asserted across every channel at once."""
    sent = {}

    def poster(url, body, title):
        sent.update(body=body)
        return True

    store = a_store(tmp_path)
    wall = WallEffector(store)
    notify = NotifyEffector(a_notify_config(), poster=poster)
    deliver_all([wall, notify], an_intervention())
    action = "COURSE-101 problem set 2, question 4"
    assert action in sent["body"]
    assert wall.current_state()["next_action"] == action


@pytest.mark.parametrize("rung", [1, 2])
def test_wall_records_whichever_rung_it_is_handed(tmp_path, rung):
    wall = WallEffector(a_store(tmp_path))
    wall.deliver(an_intervention(rung=rung))
    assert wall.current_state()["rung"] == rung
