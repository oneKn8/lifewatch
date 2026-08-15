"""The contract holds what was promised.

Most of these assert behaviour. The ones that matter most assert absence: there
is no way to dismiss a block, no way to move one into a slot that has already
passed, and no way to move the same block twice and have the hours charged only
once. Each of those is an exit that would quietly make the instrument useless.
"""

import threading
import time
from datetime import datetime, timedelta

import pytest

from lifewatch.clock import FakeClock
from lifewatch.config import Config
from lifewatch.contract import Contract
from lifewatch.models import BlockState

T0 = datetime(2026, 8, 24, 7, 0, 0)


def make(passes=1):
    cfg = Config.empty()
    cfg.passes_per_week = passes
    return Contract(cfg, FakeClock(T0))


def add(contract, start=T0, minutes=90):
    return contract.add_block("COURSE-101", start, start + timedelta(minutes=minutes))


# -- blocks ---------------------------------------------------------------


def test_a_new_block_is_planned():
    assert add(make()).state is BlockState.PLANNED


def test_starting_a_block_records_the_actual_time():
    c = make()
    b = add(c)
    c.start_block(b.id, T0 + timedelta(minutes=3))
    assert b.state is BlockState.RUNNING
    assert b.actual_start == T0 + timedelta(minutes=3)


def test_completing_a_block_records_the_actual_end():
    c = make()
    b = add(c)
    c.start_block(b.id, T0)
    c.complete_block(b.id, T0 + timedelta(minutes=88))
    assert b.state is BlockState.COMPLETED
    assert b.actual_end == T0 + timedelta(minutes=88)


def test_completing_a_block_that_was_never_started_invents_no_start_time():
    c = make()
    b = add(c)
    c.complete_block(b.id, T0 + timedelta(minutes=90))
    assert b.state is BlockState.COMPLETED
    assert b.actual_start is None


def test_restarting_a_running_block_keeps_the_first_actual_start():
    c = make()
    b = add(c)
    c.start_block(b.id, T0 + timedelta(minutes=3))
    c.start_block(b.id, T0 + timedelta(minutes=40))
    assert b.actual_start == T0 + timedelta(minutes=3)


def test_a_block_must_end_after_it_starts():
    c = make()
    with pytest.raises(ValueError):
        c.add_block("COURSE-101", T0, T0)
    with pytest.raises(ValueError):
        c.add_block("COURSE-101", T0, T0 - timedelta(minutes=30))


def test_an_unknown_block_id_is_refused_rather_than_ignored():
    c = make()
    with pytest.raises(KeyError):
        c.start_block("no-such-block", T0)


def test_the_clock_is_exposed_so_callers_need_no_clock_of_their_own():
    assert make().clock.now() == T0


# -- current block --------------------------------------------------------


def test_current_block_finds_the_block_covering_now():
    c = make()
    b = add(c)
    assert c.current_block(T0 + timedelta(minutes=30)).id == b.id


def test_current_block_is_none_outside_any_window():
    c = make()
    add(c)
    assert c.current_block(T0 + timedelta(hours=5)) is None


def test_current_block_is_none_when_no_block_was_ever_declared():
    assert make().current_block(T0) is None


def test_the_window_includes_its_start_and_excludes_its_end():
    c = make()
    b = add(c, minutes=90)
    assert c.current_block(T0).id == b.id
    assert c.current_block(T0 + timedelta(minutes=90)) is None


def test_a_completed_block_still_occupies_its_window():
    c = make()
    b = add(c)
    c.complete_block(b.id, T0 + timedelta(minutes=30))
    assert c.current_block(T0 + timedelta(minutes=45)).id == b.id


def test_a_moved_block_no_longer_occupies_its_old_window():
    c = make()
    b = add(c)
    later = T0 + timedelta(days=1)
    c.move_block(b.id, later, later + timedelta(minutes=90), T0)
    assert c.current_block(T0 + timedelta(minutes=30)) is None


def test_current_block_prefers_the_one_actually_running_when_windows_overlap():
    c = make()
    first = add(c, T0, minutes=120)
    second = c.add_block(
        "COURSE-102", T0 + timedelta(minutes=30), T0 + timedelta(minutes=90)
    )
    assert c.current_block(T0 + timedelta(minutes=45)).id == first.id
    c.start_block(second.id, T0 + timedelta(minutes=30))
    assert c.current_block(T0 + timedelta(minutes=45)).id == second.id


# -- the exit that does not exist -----------------------------------------


def test_there_is_no_dismiss_method():
    assert not hasattr(make(), "dismiss_block")


def test_nothing_on_the_contract_dismisses_anything():
    assert [name for name in dir(make()) if "dismiss" in name.lower()] == []


# -- moving ---------------------------------------------------------------


def test_moving_a_block_creates_a_successor_and_links_both_ways():
    c = make()
    b = add(c)
    new_start = T0 + timedelta(days=1)
    successor = c.move_block(b.id, new_start, new_start + timedelta(minutes=90), T0)
    assert b.state is BlockState.MOVED
    assert b.moved_to == successor.id
    assert successor.moved_from == b.id
    assert successor.state is BlockState.PLANNED


def test_the_successor_inherits_the_commitment_it_was_promised_to():
    c = make()
    b = add(c)
    new_start = T0 + timedelta(days=1)
    successor = c.move_block(b.id, new_start, new_start + timedelta(minutes=90), T0)
    assert successor.commitment_id == b.commitment_id
    assert successor.id != b.id


def test_moved_minutes_become_debt_on_the_receiving_day():
    c = make()
    b = add(c, minutes=90)
    new_start = T0 + timedelta(days=1)
    c.move_block(b.id, new_start, new_start + timedelta(minutes=90), T0)
    assert c.debt_minutes(new_start.date()) == 90


def test_a_day_nothing_was_moved_to_carries_no_debt():
    c = make()
    add(c)
    assert c.debt_minutes(T0.date()) == 0


def test_debt_minutes_understands_a_datetime_rather_than_silently_reporting_zero():
    c = make()
    b = add(c, minutes=90)
    new_start = T0 + timedelta(days=1)
    c.move_block(b.id, new_start, new_start + timedelta(minutes=90), T0)
    assert c.debt_minutes(new_start) == 90


def test_moving_a_block_into_a_slot_that_has_already_passed_is_refused():
    c = make()
    b = add(c)
    yesterday = T0 - timedelta(days=1)
    with pytest.raises(ValueError):
        c.move_block(b.id, yesterday, yesterday + timedelta(minutes=90), T0)
    assert b.state is BlockState.PLANNED
    assert c.debt_minutes(yesterday.date()) == 0


def test_a_replacement_slot_must_end_after_it_starts():
    c = make()
    b = add(c)
    later = T0 + timedelta(days=1)
    with pytest.raises(ValueError):
        c.move_block(b.id, later, later, T0)


def test_the_same_block_cannot_be_moved_twice():
    c = make()
    b = add(c)
    day1 = T0 + timedelta(days=1)
    c.move_block(b.id, day1, day1 + timedelta(minutes=90), T0)
    day2 = T0 + timedelta(days=2)
    with pytest.raises(ValueError):
        c.move_block(b.id, day2, day2 + timedelta(minutes=90), T0)
    assert c.debt_minutes(day2.date()) == 0


def test_a_completed_block_has_nothing_left_to_move():
    c = make()
    b = add(c)
    c.complete_block(b.id, T0 + timedelta(minutes=90))
    later = T0 + timedelta(days=1)
    with pytest.raises(ValueError):
        c.move_block(b.id, later, later + timedelta(minutes=90), T0)


def test_a_moved_block_cannot_be_started_or_completed():
    c = make()
    b = add(c)
    later = T0 + timedelta(days=1)
    c.move_block(b.id, later, later + timedelta(minutes=90), T0)
    with pytest.raises(ValueError):
        c.start_block(b.id, T0)
    with pytest.raises(ValueError):
        c.complete_block(b.id, T0)


def test_moving_a_successor_carries_the_debt_forward_instead_of_duplicating_it():
    c = make()
    b = add(c, minutes=90)
    day1 = T0 + timedelta(days=1)
    successor = c.move_block(b.id, day1, day1 + timedelta(minutes=90), T0)
    day2 = T0 + timedelta(days=2)
    c.move_block(successor.id, day2, day2 + timedelta(minutes=90), T0)
    assert c.debt_minutes(day1.date()) == 0
    assert c.debt_minutes(day2.date()) == 90


def test_debt_does_not_erode_when_the_replacement_slot_is_shorter():
    c = make()
    b = add(c, minutes=90)
    day1 = T0 + timedelta(days=1)
    successor = c.move_block(b.id, day1, day1 + timedelta(minutes=30), T0)
    assert c.debt_minutes(day1.date()) == 90
    day2 = T0 + timedelta(days=2)
    c.move_block(successor.id, day2, day2 + timedelta(minutes=30), T0)
    assert c.debt_minutes(day2.date()) == 90


def test_two_blocks_moved_onto_the_same_day_both_land_there():
    c = make()
    first = add(c, T0, minutes=90)
    second = c.add_block(
        "COURSE-101", T0 + timedelta(hours=3), T0 + timedelta(hours=4)
    )
    day1 = T0 + timedelta(days=1)
    c.move_block(first.id, day1, day1 + timedelta(minutes=90), T0)
    c.move_block(second.id, day1 + timedelta(hours=3), day1 + timedelta(hours=4), T0)
    assert c.debt_minutes(day1.date()) == 150


# -- passes ---------------------------------------------------------------


def test_a_pass_is_finite_and_decrements():
    c = make(passes=1)
    assert c.passes_remaining(T0) == 1
    assert c.use_pass(T0) is True
    assert c.passes_remaining(T0) == 0
    assert c.use_pass(T0) is False


def test_passes_reset_the_following_week():
    c = make(passes=1)
    c.use_pass(T0)
    assert c.passes_remaining(T0 + timedelta(days=7)) == 1


def test_passes_do_not_accumulate_across_weeks():
    c = make(passes=1)
    assert c.passes_remaining(T0 + timedelta(days=21)) == 1


def test_a_pass_spent_on_sunday_does_not_touch_mondays_allowance():
    c = make(passes=1)
    sunday = T0 + timedelta(days=6)
    monday = T0 + timedelta(days=7)
    assert c.use_pass(sunday) is True
    assert c.passes_remaining(sunday) == 0
    assert c.use_pass(monday) is True


def test_a_contract_with_no_passes_grants_none():
    c = make(passes=0)
    assert c.passes_remaining(T0) == 0
    assert c.use_pass(T0) is False


def test_the_moment_a_pass_was_spent_is_recorded():
    c = make(passes=2)
    c.use_pass(T0)
    c.use_pass(T0 + timedelta(hours=2))
    assert c.passes_used_at(T0) == [T0, T0 + timedelta(hours=2)]


def test_two_threads_racing_for_the_last_pass_cannot_both_win():
    """A pass is finite, and finite has to survive concurrency to mean anything.

    The web layer serves synchronous handlers from a thread pool, so two taps on
    "use a pass" genuinely can interleave. The stand-in config stalls inside the
    read that sits between the check and the decrement, which is the exact window
    an unguarded implementation would lose.
    """

    class StallingConfig:
        @property
        def passes_per_week(self):
            time.sleep(0.05)
            return 1

    contract = Contract(StallingConfig(), FakeClock(T0))
    barrier = threading.Barrier(2)
    results = []

    def attempt():
        barrier.wait()
        results.append(contract.use_pass(T0))

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [False, True]


# -- sick mode ------------------------------------------------------------


def test_sick_mode_silences_for_the_declared_window():
    c = make()
    c.declare_sick(T0, hours=24)
    assert c.is_silenced(T0 + timedelta(hours=5)) is True
    assert c.is_silenced(T0 + timedelta(hours=25)) is False


def test_nothing_is_silenced_by_default():
    assert make().is_silenced(T0) is False


def test_silence_ends_exactly_when_it_was_declared_to_end():
    c = make()
    c.declare_sick(T0, hours=24)
    assert c.is_silenced(T0 + timedelta(hours=24)) is False


def test_a_second_declaration_extends_the_silence():
    c = make()
    c.declare_sick(T0, hours=24)
    c.declare_sick(T0 + timedelta(hours=20), hours=24)
    assert c.is_silenced(T0 + timedelta(hours=40)) is True


def test_a_shorter_later_declaration_cannot_cut_the_silence_short():
    c = make()
    c.declare_sick(T0, hours=24)
    c.declare_sick(T0 + timedelta(hours=1), hours=1)
    assert c.is_silenced(T0 + timedelta(hours=10)) is True


def test_a_sick_window_must_have_a_length():
    c = make()
    with pytest.raises(ValueError):
        c.declare_sick(T0, hours=0)
    assert c.is_silenced(T0) is False


# -- accessors ------------------------------------------------------------


def test_blocks_come_back_in_the_order_they_were_declared():
    c = make()
    first = add(c, T0)
    second = add(c, T0 + timedelta(hours=4))
    assert [b.id for b in c.blocks()] == [first.id, second.id]


def test_a_block_can_be_looked_up_by_id_and_missing_ones_are_none():
    c = make()
    b = add(c)
    assert c.block(b.id) is b
    assert c.block("no-such-block") is None


def test_block_ids_are_unique_and_do_not_depend_on_the_wall_clock():
    """Ids come from a counter, not a timestamp or a random source.

    A replayed week has to produce the same ids every time or the escalation
    sequence cannot be asserted exactly.
    """
    first = make()
    second = make()
    ids = [add(first).id for _ in range(3)]
    assert len(set(ids)) == 3
    assert ids == [add(second).id for _ in range(3)]
