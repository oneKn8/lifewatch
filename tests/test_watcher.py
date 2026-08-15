"""The watcher: when to interrupt someone, how hard, and when not to.

Two invariants carry most of these tests.

The first is spec section 9.2: no escalation may be delivered without a
resolvable next action. It is enforced twice over, once in the type and once in
the watcher, and both halves are asserted here. The type half means a bare
reproach cannot be constructed at all. The watcher half means that when the
contract cannot say what to do now, the ladder stays silent instead of inventing
something vague to say.

The second is spec section 9.3: discretion runs toward mercy only. A judge may
lower a rung and may never raise one, and it decides on derived counts and
durations with no window title, no URL and no raw observation anywhere in what
it is given. That payload is what makes an optional cloud judge safe, so it is
tested structurally rather than by inspection.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pytest

from lifewatch.models import BlockState, Intervention, Klass, Interval, Observation
from lifewatch.watcher import Watcher

T0 = datetime(2026, 8, 24, 7, 0, 0)


# -- the type-level half of the section 9.2 invariant ------------------------


def test_an_intervention_cannot_exist_without_a_next_action():
    with pytest.raises(ValueError):
        Intervention(rung=2, block_id="b1", message="You are behind.",
                     next_action="", requires_response=False)


def test_whitespace_does_not_satisfy_the_next_action_requirement():
    with pytest.raises(ValueError):
        Intervention(rung=2, block_id="b1", message="You are behind.",
                     next_action="   ", requires_response=False)


def test_a_valid_intervention_constructs():
    iv = Intervention(rung=2, block_id="b1", message="Block is dead.",
                      next_action="COURSE-101 problem set 2, question 4",
                      requires_response=False)
    assert iv.rung == 2


# -- when the watcher stays quiet -------------------------------------------


def test_no_intervention_while_the_block_is_running(watcher_with_running_block):
    assert watcher_with_running_block.evaluate(T0 + timedelta(minutes=10)) is None


def test_no_intervention_when_no_block_covers_now(built_system):
    assert built_system.watcher.evaluate(T0 + timedelta(hours=5)) is None


def test_no_intervention_once_the_block_is_completed(built_system):
    built_system.contract.complete_block(built_system.block.id, T0 + timedelta(minutes=5))
    assert built_system.watcher.evaluate(T0 + timedelta(minutes=30)) is None


def test_sick_mode_suppresses_all_interventions(watcher_with_dead_block):
    watcher_with_dead_block.contract.declare_sick(T0, hours=24)
    assert watcher_with_dead_block.evaluate(T0 + timedelta(minutes=30)) is None


def test_moving_the_block_stops_escalation(watcher_with_dead_block):
    w = watcher_with_dead_block
    block = w.contract.current_block(T0)
    later = T0 + timedelta(days=1)
    w.contract.move_block(block.id, later, later + timedelta(minutes=90), T0)
    assert w.evaluate(T0 + timedelta(minutes=30)) is None


def test_spending_a_pass_during_the_block_stops_escalation(watcher_with_dead_block):
    """Spec section 9.1: escalation cancels on pass used.

    A pass is the finite, no-questions-asked exit. It would not be
    no-questions-asked if the ladder kept climbing after it was spent.
    """
    w = watcher_with_dead_block
    assert w.evaluate(T0 + timedelta(minutes=6)) is not None
    assert w.contract.use_pass(T0 + timedelta(minutes=7)) is True
    assert w.evaluate(T0 + timedelta(minutes=8)) is None


def test_a_pass_spent_before_the_block_does_not_excuse_it(built_system):
    """Passes are spent against the block that is dead, not banked in advance."""
    system = built_system
    earlier = T0 - timedelta(hours=2)
    assert system.contract.use_pass(earlier) is True
    assert system.watcher.evaluate(T0 + timedelta(minutes=6)) is not None


# -- the ladder --------------------------------------------------------------


def test_rung_1_fires_at_block_start_with_nothing_running(watcher_with_dead_block):
    iv = watcher_with_dead_block.evaluate(T0)
    assert iv.rung == 1


def test_rung_2_fires_after_five_minutes(watcher_with_dead_block):
    iv = watcher_with_dead_block.evaluate(T0 + timedelta(minutes=6))
    assert iv.rung == 2


def test_rung_2_does_not_fire_early(watcher_with_dead_block):
    assert watcher_with_dead_block.evaluate(T0 + timedelta(minutes=4)).rung == 1


def test_no_rung_is_emitted_above_what_the_ladder_configures(make_system):
    """Stage 1 ships rungs 1 and 2. A one-rung ladder must stay a one-rung ladder.

    The rungs above 2 have no effector yet, so inventing one would produce an
    escalation that is recorded and never delivered, which is the worst possible
    failure for an instrument whose credibility is that it always notices.
    """
    system = make_system(
        ladder=[{"rung": 1, "after_minutes": 0, "effector": "wall",
                 "requires_response": False}]
    )
    assert system.watcher.evaluate(T0 + timedelta(minutes=60)).rung == 1


def test_requires_response_comes_from_the_ladder_entry(make_system):
    system = make_system(
        ladder=[{"rung": 1, "after_minutes": 0, "effector": "wall",
                 "requires_response": True}]
    )
    assert system.watcher.evaluate(T0).requires_response is True


def test_the_intervention_names_the_block_it_is_about(built_system):
    iv = built_system.watcher.evaluate(T0 + timedelta(minutes=6))
    assert iv.block_id == built_system.block.id


# -- the section 9.2 invariant, in the watcher ------------------------------


def test_no_escalation_when_no_next_action_can_be_resolved(watcher_without_next_action):
    assert watcher_without_next_action.evaluate(T0 + timedelta(minutes=30)) is None


def test_no_escalation_when_the_commitment_is_not_in_the_config(make_system):
    """A block against a commitment nobody declared resolves to nothing at all."""
    system = make_system(commitments=[])
    assert system.watcher.evaluate(T0 + timedelta(minutes=30)) is None


def test_no_escalation_when_every_next_action_is_already_done(make_system):
    system = make_system(
        commitments=[{
            "id": "COURSE-101",
            "label": "COURSE-101",
            "next_actions": [{"text": "problem set 2, question 4", "done": True}],
        }]
    )
    assert system.watcher.evaluate(T0 + timedelta(minutes=30)) is None


def test_a_blank_next_action_does_not_count_as_resolvable(make_system):
    system = make_system(
        commitments=[{"id": "COURSE-101", "label": "COURSE-101", "next_action": "   "}]
    )
    assert system.watcher.evaluate(T0 + timedelta(minutes=30)) is None


def test_the_next_action_is_the_concrete_task_from_the_commitment(built_system):
    iv = built_system.watcher.evaluate(T0 + timedelta(minutes=6))
    assert "problem set 2, question 4" in iv.next_action


def test_the_next_action_is_the_first_undone_item(make_system):
    system = make_system(
        commitments=[{
            "id": "COURSE-101",
            "label": "COURSE-101",
            "next_actions": [
                {"text": "problem set 1", "done": True},
                {"text": "problem set 2, question 4"},
            ],
        }]
    )
    iv = system.watcher.evaluate(T0)
    assert "problem set 2, question 4" in iv.next_action
    assert "problem set 1" not in iv.next_action


def test_a_singular_next_action_field_also_resolves(make_system):
    system = make_system(
        commitments=[{"id": "COURSE-101", "label": "COURSE-101",
                      "next_action": "problem set 2, question 4"}]
    )
    assert "problem set 2, question 4" in system.watcher.evaluate(T0).next_action


def test_the_message_does_not_shame(built_system):
    """Spec section 9.2: shame produces avoidance, and avoidance is the disease.

    Not an exhaustive filter, and it is not meant to be. It is a tripwire on the
    register of the copy, so that a later edit toward scolding fails the build
    rather than shipping.
    """
    iv = built_system.watcher.evaluate(T0 + timedelta(minutes=6))
    blob = iv.message.lower()
    for word in ("lazy", "failed", "failure", "pathetic", "disappointed", "shame",
                 "wasted", "useless"):
        assert word not in blob, f"escalation copy should not scold: {iv.message!r}"


# -- discretion: mercy only --------------------------------------------------


def test_discretion_may_lower_a_rung(watcher_with_dead_block):
    w = watcher_with_dead_block
    w.judge = lambda state: 1
    assert w.evaluate(T0 + timedelta(minutes=6)).rung == 1


def test_discretion_may_never_raise_a_rung(watcher_with_dead_block):
    w = watcher_with_dead_block
    w.judge = lambda state: 4
    assert w.evaluate(T0 + timedelta(minutes=6)).rung == 2


def test_discretion_may_silence_an_escalation_entirely(watcher_with_dead_block):
    """Mercy taken to its limit is silence, which is still mercy and still legal."""
    w = watcher_with_dead_block
    w.judge = lambda state: 0
    assert w.evaluate(T0 + timedelta(minutes=6)) is None


def test_a_judge_that_fails_leaves_the_ladder_where_it_was(watcher_with_dead_block):
    """A broken judge must not be able to escalate, and must not be able to excuse.

    Both failures are real. A judge whose exception silenced the ladder would be
    the easiest possible way to disable the system, and a judge whose exception
    raised it would be an escalation nobody decided on.
    """
    def broken(state):
        raise ConnectionError("no judge reachable")

    watcher_with_dead_block.judge = broken
    assert watcher_with_dead_block.evaluate(T0 + timedelta(minutes=6)).rung == 2


def test_an_unparseable_verdict_leaves_the_ladder_where_it_was(watcher_with_dead_block):
    watcher_with_dead_block.judge = lambda state: "banana"
    assert watcher_with_dead_block.evaluate(T0 + timedelta(minutes=6)).rung == 2


def test_a_verdict_between_configured_rungs_rounds_down(make_system):
    """Mercy resolves downward when a judge names a rung the ladder does not have."""
    system = make_system(
        ladder=[
            {"rung": 1, "after_minutes": 0, "effector": "wall",
             "requires_response": False},
            {"rung": 3, "after_minutes": 5, "effector": "wall",
             "requires_response": False},
        ]
    )
    system.watcher.judge = lambda state: 2
    assert system.watcher.evaluate(T0 + timedelta(minutes=6)).rung == 1


def test_a_lowered_rung_is_justified_in_the_log(watcher_with_dead_block, caplog):
    """Spec section 9.3: the model must justify a lowered rung in the log."""
    watcher_with_dead_block.judge = lambda state: {
        "rung": 1,
        "justification": "no input all night, this looks like sleep",
    }
    with caplog.at_level(logging.INFO, logger="lifewatch.watcher"):
        assert watcher_with_dead_block.evaluate(T0 + timedelta(minutes=6)).rung == 1
    assert "no input all night" in caplog.text


def test_the_judge_never_receives_a_window_title(watcher_with_dead_block):
    seen = {}
    def judge(state):
        seen["state"] = state
        return 2
    watcher_with_dead_block.judge = judge
    watcher_with_dead_block.evaluate(T0 + timedelta(minutes=6))
    blob = repr(seen["state"]).lower()
    assert "title" not in blob and "http" not in blob


def test_the_judge_payload_is_numbers_even_when_the_log_is_full_of_titles(built_system):
    """The structural version of the test above, and the one that actually binds.

    The store is seeded with exactly the material that must never leave the
    machine, and then the payload is checked for strings of any kind rather than
    for two suspicious substrings. A payload that cannot carry a string cannot
    carry a document name or a URL, which is what makes the optional cloud judge
    in spec section 7 safe to offer at all.
    """
    system = built_system
    system.store.append(Observation(
        ts=T0 + timedelta(minutes=1), sensor="window", kind="focus",
        value="TestBrowser|Some Video Title https://example.invalid/watch",
        meta={"wm_class": "TestBrowser"},
    ))
    system.store.put_interval(Interval(
        start=T0, end=T0 + timedelta(minutes=5), klass=Klass.DRIFT, tier=2,
        reason="judged against the title Some Video Title",
    ))

    seen = {}
    def judge(state):
        seen["state"] = state
        return 2

    system.watcher.judge = judge
    system.watcher.evaluate(T0 + timedelta(minutes=6))

    payload = seen["state"]
    assert isinstance(payload, dict)
    for key, value in payload.items():
        assert isinstance(key, str)
        assert isinstance(value, (int, float, type(None))), (
            f"{key} is {value!r}: the judge payload carries counts and durations only"
        )


def test_the_judge_payload_carries_the_state_the_spec_names(built_system):
    """Spec section 7: dead block counts, idle minutes, passes, integrity ratio."""
    system = built_system
    system.store.append(Observation(
        ts=T0 + timedelta(minutes=5), sensor="idle", kind="ms",
        value=str(20 * 60 * 1000), meta={},
    ))

    seen = {}
    def judge(state):
        seen["state"] = state
        return 2

    system.watcher.judge = judge
    system.watcher.evaluate(T0 + timedelta(minutes=6))

    payload = seen["state"]
    for field in ("dead_blocks_today", "minutes_elapsed", "idle_minutes",
                  "passes_remaining", "integrity", "ladder_rung"):
        assert field in payload
    assert payload["ladder_rung"] == 2
    assert payload["passes_remaining"] == 1
    assert payload["idle_minutes"] == pytest.approx(20.0)
    assert payload["minutes_elapsed"] == pytest.approx(6.0)


def test_the_judge_is_not_consulted_when_nothing_would_be_delivered(built_system):
    """No block, no escalation, and therefore nothing to be merciful about.

    Worth pinning: a judge that is called on every tick regardless is a paid API
    call every fifteen seconds for a system that is behaving perfectly.
    """
    calls = []
    built_system.watcher.judge = lambda state: calls.append(state) or 1
    assert built_system.watcher.evaluate(T0 + timedelta(hours=5)) is None
    assert calls == []


# -- purity ------------------------------------------------------------------


def test_evaluate_is_idempotent(built_system):
    """Two callers, one loop and one view, must not change each other's answer."""
    first = built_system.watcher.evaluate(T0 + timedelta(minutes=6))
    second = built_system.watcher.evaluate(T0 + timedelta(minutes=6))
    assert first == second


def test_evaluate_writes_nothing_to_the_store(built_system):
    """Deciding is not delivering. The escalation record belongs to the effector."""
    built_system.watcher.evaluate(T0 + timedelta(minutes=6))
    rows = built_system.store.conn.execute(
        "SELECT COUNT(*) AS n FROM escalation"
    ).fetchone()["n"]
    assert rows == 0


def test_escalate_returns_each_rung_once_per_block(built_system):
    """The delivery-side helper: a decision repeats, a notification must not.

    ``evaluate`` answers "what is the correct escalation state now" and is safe
    to call every second. ``escalate`` answers "is there something new to send",
    which is the question a loop that owns a phone needs answered.
    """
    watcher = built_system.watcher
    fired = []
    for minute in range(0, 12):
        iv = watcher.escalate(T0 + timedelta(minutes=minute))
        if iv is not None:
            fired.append(iv.rung)
    assert fired == [1, 2]


def test_escalate_starts_over_for_a_different_block(built_system):
    """A new block is a new obligation and gets the whole ladder again."""
    watcher = built_system.watcher
    assert watcher.escalate(T0).rung == 1
    later = T0 + timedelta(days=1)
    built_system.contract.add_block(built_system.commitment_id, later,
                                    later + timedelta(minutes=90))
    assert watcher.escalate(later).rung == 1


def test_a_dead_block_stays_dead_in_the_contract(built_system):
    """The watcher observes; it does not quietly rewrite the block it judged."""
    built_system.watcher.evaluate(T0 + timedelta(minutes=6))
    assert built_system.contract.block(built_system.block.id).state is BlockState.PLANNED
