from datetime import datetime

import pytest

from resolution_finder.service.schedule import (
    ScheduleParseError,
    is_due,
    next_run,
    parse_schedule_tag,
)


def test_parse_everyday_at():
    s = parse_schedule_tag("everyday at 9:00")
    assert s.kind == "daily" and s.hour == 9 and s.minute == 0


def test_parse_every_day_with_space_and_minutes():
    s = parse_schedule_tag("every day at 21:15")
    assert s.kind == "daily" and s.hour == 21 and s.minute == 15


def test_parse_daily_pm():
    s = parse_schedule_tag("daily at 9 pm")
    assert s.kind == "daily" and s.hour == 21 and s.minute == 0


def test_parse_12am_is_midnight():
    s = parse_schedule_tag("everyday at 12am")
    assert s.hour == 0


def test_parse_interval_hours():
    s = parse_schedule_tag("every 6 hours")
    assert s.kind == "interval" and s.interval_minutes == 360


def test_parse_interval_minutes():
    s = parse_schedule_tag("every 90 minutes")
    assert s.kind == "interval" and s.interval_minutes == 90


def test_interval_below_15_minutes_rejected():
    with pytest.raises(ScheduleParseError):
        parse_schedule_tag("every 5 minutes")


def test_off_and_empty():
    assert parse_schedule_tag("off").kind == "off"
    assert parse_schedule_tag("").kind == "off"
    assert parse_schedule_tag(None).kind == "off"


def test_garbage_rejected_with_supported_forms():
    with pytest.raises(ScheduleParseError) as exc:
        parse_schedule_tag("whenever you feel like it")
    assert "Supported forms" in str(exc.value)


def test_invalid_hour_rejected():
    with pytest.raises(ScheduleParseError):
        parse_schedule_tag("everyday at 25:00")


def test_next_run_daily_before_slot():
    s = parse_schedule_tag("everyday at 9:00")
    now = datetime(2026, 10, 6, 8, 0)
    assert next_run(s, now) == datetime(2026, 10, 6, 9, 0)


def test_next_run_daily_after_slot_is_tomorrow():
    s = parse_schedule_tag("everyday at 9:00")
    now = datetime(2026, 10, 6, 10, 0)
    assert next_run(s, now) == datetime(2026, 10, 7, 9, 0)


def test_is_due_daily():
    s = parse_schedule_tag("everyday at 9:00")
    before = datetime(2026, 10, 6, 8, 59)
    after = datetime(2026, 10, 6, 9, 1)
    assert not is_due(s, before, None)
    assert is_due(s, after, None)
    # Already ran after today's slot -> not due again today
    assert not is_due(s, after, datetime(2026, 10, 6, 9, 0, 30))
    # Ran yesterday -> due today after the slot
    assert is_due(s, after, datetime(2026, 10, 5, 9, 0, 30))


def test_is_due_interval():
    s = parse_schedule_tag("every 60 minutes")
    now = datetime(2026, 10, 6, 12, 0)
    assert is_due(s, now, None)
    assert not is_due(s, now, datetime(2026, 10, 6, 11, 30))
    assert is_due(s, now, datetime(2026, 10, 6, 11, 0))


def test_off_never_due():
    s = parse_schedule_tag("off")
    assert not is_due(s, datetime(2026, 10, 6, 12, 0), None)
    assert next_run(s, datetime(2026, 10, 6, 12, 0)) is None
