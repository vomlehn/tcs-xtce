"""TAI and UTC, and the leap seconds between them.

The binary time codes count seconds of TAI, which has no leap seconds; the
calendar codes give dates and times of UTC, which since 1972 has stayed
within a second of the Earth's rotation by inserting a leap second at the
end of a June or December now and then.  Converting between them takes a
table of when, which the IERS publishes in Bulletin C and as
``leap-seconds.list``.

``LEAP_SECONDS`` is that table as of the list updated through Bulletin C and
expiring on 28 June 2027; a mission can give its own ``LeapSeconds``
instead, as a newer bulletin announces more.  A time is converted only
between 1972-01-01, when UTC took its present form, and the table's expiry:
before it UTC's seconds were not TAI's, and after it nobody yet knows
whether a leap second will come.

A TAI time here is ``TaiTime``: seconds since the CCSDS epoch,
1958-01-01T00:00:00 TAI, and picoseconds, exactly.

The same as the Rust crate's ``utc`` module and the C header's
``ccsds_utc_to_tai`` and ``ccsds_tai_to_utc``, which give the same results.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .calendar import PICOS, CalendarTime, civil_from_days, days_from_civil

# Seconds in a day.
_DAY = 86_400


@dataclass(frozen=True)
class TaiTime:
    """A time of TAI, exactly: seconds since 1958-01-01T00:00:00 TAI, and
    picoseconds of the second, less than 10**12."""

    seconds: int
    picos: int = 0

    def to_seconds(self) -> float:
        """Gives the time as seconds since the CCSDS epoch, as a float,
        which is what the binary time codes' float encoders take.

        Returns:
            The seconds.
        """
        return float(self.seconds) + float(self.picos) / 1e12


@dataclass(frozen=True)
class LeapSecond:
    """A change in TAI - UTC: from 00:00:00 UTC on this date, it is this
    many seconds."""

    year: int
    month: int
    day: int
    tai_minus_utc: int


@dataclass(frozen=True)
class LeapSeconds:
    """A table of leap seconds: every change in TAI - UTC from 1972-01-01
    on, in order, and the first day it does not cover, as (year, month,
    day)."""

    changes: tuple[LeapSecond, ...]
    expires: tuple[int, int, int]

    def on(self, days: int) -> Optional[int]:
        """Gives TAI - UTC on a day.

        Args:
            days: The day, as days since 1958-01-01.

        Returns:
            The seconds; None before the first change or from the table's
            expiry.
        """
        if days >= _epoch_days(*self.expires):
            return None
        for c in reversed(self.changes):
            if _epoch_days(c.year, c.month, c.day) <= days:
                return c.tai_minus_utc
        return None

    def at(self, i: int) -> int:
        """Gives the TAI second a change takes effect at: 00:00:00 UTC on
        its day, in seconds since the CCSDS epoch."""
        c = self.changes[i]
        return _epoch_days(c.year, c.month, c.day) * _DAY + c.tai_minus_utc


def _epoch_days(year: int, month: int, day: int) -> int:
    """Counts the days from 1958-01-01 to a date."""
    return days_from_civil(year, month, day) - days_from_civil(1958, 1, 1)


# The leap seconds of the IERS's leap-seconds.list, updated through
# Bulletin C and expiring on 28 June 2027.
LEAP_SECONDS = LeapSeconds(
    tuple(LeapSecond(y, m, 1, o)
          for y, m, o in (
              (1972, 1, 10), (1972, 7, 11), (1973, 1, 12), (1974, 1, 13),
              (1975, 1, 14), (1976, 1, 15), (1977, 1, 16), (1978, 1, 17),
              (1979, 1, 18), (1980, 1, 19), (1981, 7, 20), (1982, 7, 21),
              (1983, 7, 22), (1985, 7, 23), (1988, 1, 24), (1990, 1, 25),
              (1991, 1, 26), (1992, 7, 27), (1993, 7, 28), (1994, 7, 29),
              (1996, 1, 30), (1997, 7, 31), (1999, 1, 32), (2006, 1, 33),
              (2009, 1, 34), (2012, 7, 35), (2015, 7, 36), (2017, 1, 37))),
    (2027, 6, 28))


def utc_to_tai(utc: CalendarTime,
               table: LeapSeconds = LEAP_SECONDS) -> Optional[TaiTime]:
    """Converts a time of UTC to TAI.

    Args:
        utc:   The time.
        table: The leap seconds.

    Returns:
        The time of TAI; None if the time is not valid, is before the
        table's first change or from its expiry, or has a second of 60 on a
        day that has no leap second.
    """
    if not utc.is_valid():
        return None
    days = _epoch_days(utc.year, utc.month, utc.day)
    offset = table.on(days)
    if offset is None:
        return None
    if utc.second == 60:
        # Only where TAI - UTC goes up at the next midnight.
        nxt = table.on(days + 1)
        if nxt is None or nxt <= offset:
            return None
    seconds = (days * _DAY + utc.hour * 3600 + utc.minute * 60 + utc.second
               + offset)
    return TaiTime(seconds, utc.picos) if seconds >= 0 else None


def tai_to_utc(tai: TaiTime,
               table: LeapSeconds = LEAP_SECONDS) -> Optional[CalendarTime]:
    """Converts a time of TAI to UTC.

    Args:
        tai:   The time.
        table: The leap seconds.

    Returns:
        The time of UTC, with a second of 60 in a leap second; None if its
        picoseconds are not less than 10**12, or it is before the table's
        first change or from its expiry.
    """
    if not 0 <= tai.picos < PICOS or tai.seconds < 0:
        return None
    s = tai.seconds
    found = [i for i in range(len(table.changes)) if table.at(i) <= s]
    if not found:
        return None
    i = found[-1]
    offset = table.changes[i].tai_minus_utc
    # In the inserted second before the next change, it is 23:59:60 of the
    # day before.
    leap = (i + 1 < len(table.changes)
            and table.changes[i + 1].tai_minus_utc > offset
            and s >= table.at(i + 1) - 1)
    u = s - offset - (1 if leap else 0)
    days, of_day = divmod(u, _DAY)
    if table.on(days) is None:
        return None
    date = civil_from_days(days + days_from_civil(1958, 1, 1))
    if date is None:
        return None
    return CalendarTime(*date, of_day // 3600, of_day // 60 % 60,
                        of_day % 60 + (1 if leap else 0), tai.picos)
