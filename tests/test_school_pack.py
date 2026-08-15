import math
from datetime import date, datetime, time

import pytest

from packs.school import (
    campus_gaps,
    commitment_fields,
    grade_needed,
    load_pack,
    running_grade,
)

# A Monday, so a meeting declared for monday lands on it.
MONDAY = date(2026, 8, 24)


# -- grade_needed ---------------------------------------------------------


def test_grade_needed_computes_the_remaining_requirement():
    items = [
        {"name": "Exam 1", "weight": 0.25, "score": 0.80},
        {"name": "Exam 2", "weight": 0.25, "score": None},
        {"name": "Final", "weight": 0.50, "score": None},
    ]
    assert abs(grade_needed(items, target_fraction=0.90) - 0.9333) < 0.001


def test_grade_needed_is_impossible_when_it_exceeds_one():
    items = [
        {"name": "Exam 1", "weight": 0.50, "score": 0.40},
        {"name": "Final", "weight": 0.50, "score": None},
    ]
    assert grade_needed(items, target_fraction=0.90) > 1.0


def test_grade_needed_is_zero_when_the_target_is_already_secured():
    items = [
        {"name": "Exam 1", "weight": 0.50, "score": 1.00},
        {"name": "Final", "weight": 0.50, "score": None},
    ]
    assert grade_needed(items, target_fraction=0.40) <= 0.0


def test_percentage_style_weights_give_the_same_answer_as_fractions():
    items = [
        {"name": "Exam 1", "weight": 25, "score": 0.80},
        {"name": "Exam 2", "weight": 25, "score": None},
        {"name": "Final", "weight": 50, "score": None},
    ]
    assert abs(grade_needed(items, target_fraction=0.90) - 0.9333) < 0.001


def test_a_missing_score_key_counts_as_ungraded():
    items = [
        {"name": "Exam 1", "weight": 0.50, "score": 0.80},
        {"name": "Final", "weight": 0.50},
    ]
    assert abs(grade_needed(items, target_fraction=0.90) - 1.00) < 1e-9


def test_an_unreachable_target_with_nothing_left_is_infinite():
    items = [{"name": "Final", "weight": 1.0, "score": 0.50}]
    assert math.isinf(grade_needed(items, target_fraction=0.90))


def test_a_secured_target_with_nothing_left_needs_nothing():
    items = [{"name": "Final", "weight": 1.0, "score": 0.95}]
    assert grade_needed(items, target_fraction=0.90) == 0.0


def test_a_gradebook_with_no_weight_has_no_answer():
    with pytest.raises(ValueError):
        grade_needed([], target_fraction=0.90)


def test_a_negative_weight_is_refused():
    with pytest.raises(ValueError):
        grade_needed([{"name": "Exam 1", "weight": -0.5, "score": None}], 0.90)


def test_an_item_without_a_weight_is_refused():
    with pytest.raises(ValueError):
        grade_needed([{"name": "Exam 1", "score": 0.80}], 0.90)


# -- running_grade --------------------------------------------------------


def test_running_grade_ignores_items_that_are_not_graded_yet():
    items = [
        {"name": "Exam 1", "weight": 0.25, "score": 0.80},
        {"name": "Final", "weight": 0.75, "score": None},
    ]
    assert running_grade(items) == pytest.approx(0.80)


def test_running_grade_is_none_before_anything_is_graded():
    assert running_grade([{"name": "Final", "weight": 1.0, "score": None}]) is None


def test_running_grade_weights_the_graded_items_against_each_other():
    items = [
        {"name": "Quiz", "weight": 0.10, "score": 1.00},
        {"name": "Exam 1", "weight": 0.30, "score": 0.60},
        {"name": "Final", "weight": 0.60, "score": None},
    ]
    assert running_grade(items) == pytest.approx((0.10 + 0.18) / 0.40)


# -- campus_gaps ----------------------------------------------------------


def a_meeting(day="monday", start="10:00", end="11:15"):
    return {"day": day, "start": start, "end": end}


def test_campus_gaps_finds_the_gap_between_two_meetings():
    meetings = [a_meeting(start="10:00", end="11:15"), a_meeting(start="13:00", end="14:15")]
    assert campus_gaps(meetings, MONDAY) == [
        (datetime(2026, 8, 24, 11, 15), datetime(2026, 8, 24, 13, 0))
    ]


def test_campus_gaps_ignores_meetings_on_another_day():
    meetings = [
        a_meeting(day="monday", start="10:00", end="11:15"),
        a_meeting(day="tuesday", start="13:00", end="14:15"),
    ]
    assert campus_gaps(meetings, MONDAY) == []


def test_campus_gaps_accepts_an_abbreviated_day_name():
    meetings = [a_meeting(day="Mon", start="10:00", end="11:15"), a_meeting(day="MON", start="13:00", end="14:15")]
    assert len(campus_gaps(meetings, MONDAY)) == 1


def test_a_passing_period_is_not_a_study_block():
    meetings = [a_meeting(start="10:00", end="11:15"), a_meeting(start="11:30", end="12:45")]
    assert campus_gaps(meetings, MONDAY) == []


def test_the_passing_period_threshold_can_be_lowered():
    meetings = [a_meeting(start="10:00", end="11:15"), a_meeting(start="11:30", end="12:45")]
    assert len(campus_gaps(meetings, MONDAY, min_minutes=10)) == 1


def test_campus_gaps_sorts_meetings_it_is_given_out_of_order():
    meetings = [a_meeting(start="13:00", end="14:15"), a_meeting(start="10:00", end="11:15")]
    assert campus_gaps(meetings, MONDAY)[0][0] == datetime(2026, 8, 24, 11, 15)


def test_a_single_meeting_has_no_gaps():
    assert campus_gaps([a_meeting()], MONDAY) == []


def test_overlapping_meetings_never_produce_a_backwards_gap():
    meetings = [
        a_meeting(start="10:00", end="12:00"),
        a_meeting(start="11:00", end="11:30"),
        a_meeting(start="14:00", end="15:00"),
    ]
    assert campus_gaps(meetings, MONDAY) == [
        (datetime(2026, 8, 24, 12, 0), datetime(2026, 8, 24, 14, 0))
    ]


def test_campus_gaps_accepts_a_datetime_for_the_day():
    meetings = [a_meeting(start="10:00", end="11:15"), a_meeting(start="13:00", end="14:15")]
    assert len(campus_gaps(meetings, datetime(2026, 8, 24, 19, 30))) == 1


def test_campus_gaps_accepts_time_objects():
    meetings = [
        {"day": "monday", "start": time(10, 0), "end": time(11, 15)},
        {"day": "monday", "start": time(13, 0), "end": time(14, 15)},
    ]
    assert len(campus_gaps(meetings, MONDAY)) == 1


def test_a_malformed_meeting_time_is_refused():
    with pytest.raises(ValueError):
        campus_gaps([a_meeting(start="ten o'clock")], MONDAY)


def test_a_meeting_without_a_day_is_refused():
    with pytest.raises(ValueError):
        campus_gaps([{"start": "10:00", "end": "11:15"}], MONDAY)


def test_a_meeting_that_ends_before_it_starts_is_refused():
    with pytest.raises(ValueError):
        campus_gaps([a_meeting(start="11:00", end="10:00")], MONDAY)


# -- the pack file --------------------------------------------------------


def test_the_pack_declares_the_school_commitment_fields():
    keys = {field["key"] for field in commitment_fields()}
    assert {"course_code", "section", "instructor", "meetings"} <= keys


def test_every_declared_field_has_a_label_and_a_type():
    for field in commitment_fields():
        assert field["label"]
        assert field["type"]


def test_the_pack_names_itself():
    assert load_pack()["name"] == "school"


def test_the_pack_ships_only_synthetic_examples():
    from pathlib import Path

    import packs.school as school

    text = (Path(school.__file__).parent / "pack.yaml").read_text()
    assert "COURSE-101" in text
    assert "Example Instructor" in text


def test_the_campus_gap_threshold_comes_from_the_pack_file():
    assert load_pack()["campus_mode"]["min_gap_minutes"] > 0
