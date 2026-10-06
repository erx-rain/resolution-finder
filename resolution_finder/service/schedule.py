"""Schedule tags — the UI-settable cron for the service.

Owner decision (2026-10-06): scans are triggered on a schedule the admin
sets from the UI as a human-readable tag, e.g. "everyday at 9:00". This
module parses that tag deterministically and computes the next run time.
Unparseable tags are REJECTED with the list of supported forms — never
silently guessed (same no-inventing rule as everywhere else in this repo).

Supported forms (case-insensitive):
- "everyday at 9:00" / "every day at 09:30" / "daily at 21:15"
- "everyday at 9am" / "daily at 9 pm"
- "every 6 hours" / "every 90 minutes"
- "off" / "disabled" / "" (no schedule)

All times are the service host's local time; the /schedule API echoes the
computed next_run so the admin can verify the interpretation.
"""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

SUPPORTED_FORMS = (
    '"everyday at 9:00", "daily at 21:15", "everyday at 9am", '
    '"every 6 hours", "every 90 minutes", "off"'
)

_DAILY_RE = re.compile(
    r"^\s*(?:every\s*day|everyday|daily)\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*$",
    re.IGNORECASE,
)
_INTERVAL_RE = re.compile(
    r"^\s*every\s+(\d{1,3})\s*(hours?|hrs?|h|minutes?|mins?|m)\s*$",
    re.IGNORECASE,
)
_OFF_RE = re.compile(r"^\s*(off|disabled|none)?\s*$", re.IGNORECASE)


class ScheduleParseError(ValueError):
    pass


@dataclass
class Schedule:
    kind: str  # "daily" | "interval" | "off"
    raw: str
    hour: int = 0
    minute: int = 0
    interval_minutes: int = 0

    def describe(self) -> str:
        if self.kind == "daily":
            return f"daily at {self.hour:02d}:{self.minute:02d}"
        if self.kind == "interval":
            return f"every {self.interval_minutes} minutes"
        return "off"


def parse_schedule_tag(tag: Optional[str]) -> Schedule:
    raw = tag or ""
    if _OFF_RE.match(raw):
        return Schedule(kind="off", raw=raw)

    m = _DAILY_RE.match(raw)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ampm = (m.group(3) or "").lower()
        if ampm == "pm" and hour != 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        if hour > 23 or minute > 59:
            raise ScheduleParseError(
                f"Invalid time in schedule tag {raw!r}. Supported forms: {SUPPORTED_FORMS}"
            )
        return Schedule(kind="daily", raw=raw, hour=hour, minute=minute)

    m = _INTERVAL_RE.match(raw)
    if m:
        amount = int(m.group(1))
        unit = m.group(2).lower()
        minutes = amount * 60 if unit.startswith("h") else amount
        if minutes < 15:
            # A full scan takes seconds-to-minutes PER MARKET; anything
            # tighter than 15 minutes would overlap itself and hammer the
            # news sources this repo is careful with.
            raise ScheduleParseError(
                "Minimum interval is 15 minutes (a scan can take minutes per market)"
            )
        return Schedule(kind="interval", raw=raw, interval_minutes=minutes)

    raise ScheduleParseError(
        f"Could not parse schedule tag {raw!r}. Supported forms: {SUPPORTED_FORMS}"
    )


def next_run(schedule: Schedule, now: datetime, last_run: Optional[datetime] = None) -> Optional[datetime]:
    """The next datetime this schedule should fire. None when off."""
    if schedule.kind == "off":
        return None
    if schedule.kind == "daily":
        candidate = now.replace(hour=schedule.hour, minute=schedule.minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        # If we already ran today at/after the slot, next is tomorrow.
        if last_run is not None and last_run.date() == now.date() and candidate.date() == now.date():
            candidate += timedelta(days=1)
        return candidate
    # interval
    if last_run is None:
        return now
    return last_run + timedelta(minutes=schedule.interval_minutes)


def is_due(schedule: Schedule, now: datetime, last_run: Optional[datetime]) -> bool:
    if schedule.kind == "off":
        return False
    if schedule.kind == "daily":
        slot = now.replace(hour=schedule.hour, minute=schedule.minute, second=0, microsecond=0)
        if now < slot:
            return False
        return last_run is None or last_run < slot
    # interval
    if last_run is None:
        return True
    return (now - last_run) >= timedelta(minutes=schedule.interval_minutes)
