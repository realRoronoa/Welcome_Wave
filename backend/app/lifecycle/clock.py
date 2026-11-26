"""The lifecycle's "today". Program days, due dates, escalation and report windows are all computed from here, never from
date.today(): in demo mode an admin or manager moves the seeded organisation through days 0-90 (POST /api/demo/advance),
which stores a day offset for the organisation. Outside demo mode the offset is always 0. Security decisions (the expiry of
a pre-boarding link) use the real time, never this clock.

Program day 0 is the start date; day 30, 60 and 90 are the check-ins of a 90-day program."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

MAX_OFFSET_DAYS = 365


@dataclass(frozen=True)
class Clock:
    offset_days: int = 0

    def now(self) -> datetime:
        return datetime.now(timezone.utc) + timedelta(days=self.offset_days)

    def today(self) -> date:
        """The server's local date, as onboardings' start dates and the dashboard count days."""
        return date.today() + timedelta(days=self.offset_days)


def for_org(c, org_id: str) -> Clock:
    return Clock(c.lifecycle.org_settings(org_id).demo_day_offset) if c.settings.is_demo else Clock()


def advance(c, org_id: str, days: int) -> Clock:
    """Demo only (the API refuses it elsewhere): move the organisation's clock forward."""
    offset = min(MAX_OFFSET_DAYS, c.lifecycle.org_settings(org_id).demo_day_offset + days)
    c.lifecycle.update_org_settings(org_id, demo_day_offset=offset)
    return Clock(offset)


def _local_date(d: datetime | date) -> date:
    if not isinstance(d, datetime):
        return d
    return d.astimezone().date() if d.tzinfo else d.date()  # stored times are UTC; a naive one is taken as it is


def program_day(start: date, today: date) -> int:
    return (today - start).days


def business_days(since: datetime | date, until: datetime | date) -> int:
    """Weekdays after `since`'s date, up to and including `until`'s date: a blocker opened on Friday has waited one business
    day on Monday. A time's date is the server's local date, as today() is, so blocker ages roll over with program days."""
    a = _local_date(since)
    b = _local_date(until)
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        n += d.weekday() < 5
    return n


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())
