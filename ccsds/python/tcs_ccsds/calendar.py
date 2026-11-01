"""The calendar time codes of CCSDS 301.0-B-4, *Time Code Formats*: CCS,
the CCSDS Calendar Segmented time code, and the ASCII time codes A and B.

All three give a time as a date and a time of day, usually UTC, held here
exactly as a ``CalendarTime``: year, month, day, hour, minute, second (60
in a leap second) and picoseconds.  Turning one into a count of seconds
needs to know which days had leap seconds, which is :mod:`tcs_ccsds.utc`'s
business.

* **CCS** is an optional P-field, then a T-field of BCD digits: the year in
  two octets, the month and day of the month in one each or the day of the
  year in two, then the hour, minute and second in one each, then 0 to 6
  octets of two decimal digits each, hundredths of a second for the first.
* **ASCII code A** is ``YYYY-MM-DDThh:mm:ss.d...dZ``, and **code B**
  ``YYYY-DDDThh:mm:ss.d...dZ``, with the day of the year.  The fraction of a
  second, any number of digits, and the closing ``Z`` are optional; here a
  fraction holds at most twelve digits, a picosecond.

The codes check what they decode: a field must be a digit or BCD digits, a
date must be in the Gregorian calendar from year 1 to 9999, and a second of
60 only ends a day, at 23:59.  Encoding rounds the picoseconds to the
layout's unit, halves up, carrying into the second and on through the
calendar.  A carry out of second 59 goes to the next minute: the codes do
not know which days had leap seconds.

The same as the Rust crate's ``calendar`` module and the C header's
``ccsds_calendar`` functions, which give the same results.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace
from typing import Optional

# Picoseconds in a second.
PICOS = 10**12

# The most digits of a fraction of a second the ASCII codes hold here.
ASCII_MAX_DIGITS = 12


def is_leap_year(year: int) -> bool:
    """Says whether a year of the Gregorian calendar is a leap year.

    Args:
        year: The year.

    Returns:
        Whether February has 29 days in it.
    """
    return (year % 4 == 0 and year % 100 != 0) or year % 400 == 0


def days_in_month(year: int, month: int) -> int:
    """Gives the days of a month.

    Args:
        year:  The year.
        month: The month, 1 to 12.

    Returns:
        Its days; 0 for a month that is not 1 to 12.
    """
    if not 1 <= month <= 12:
        return 0
    if month == 2:
        return 29 if is_leap_year(year) else 28
    return 30 if month in (4, 6, 9, 11) else 31


def days_from_civil(year: int, month: int, day: int) -> int:
    """Counts the days from 1970-01-01 to a date, as the Rust crate does.

    Args:
        year:  The year.
        month: The month, 1 to 12.
        day:   The day of the month.

    Returns:
        The days, negative before 1970.
    """
    y = year - (1 if month <= 2 else 0)
    era = y // 400
    yoe = y - era * 400
    doy = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146_097 + doe - 719_468


def civil_from_days(days: int) -> Optional[tuple[int, int, int]]:
    """Gives the date a count of days from 1970-01-01 falls on.

    Args:
        days: The days, negative before 1970.

    Returns:
        The year, month and day; None outside years 1 to 9999.
    """
    z = days + 719_468
    era = z // 146_097
    doe = z - era * 146_097
    yoe = (doe - doe // 1460 + doe // 36_524 - doe // 146_096) // 365
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    day = doy - (153 * mp + 2) // 5 + 1
    month = mp + 3 if mp < 10 else mp - 9
    year = yoe + era * 400 + (1 if month <= 2 else 0)
    if not 1 <= year <= 9999:
        return None
    return year, month, day


@dataclass(frozen=True)
class CalendarTime:
    """A date and a time of day, exactly: year 1 to 9999, month 1 to 12,
    day of the month, hour 0 to 23, minute 0 to 59, second 0 to 59 or 60 at
    23:59 in a leap second, and picoseconds of the second, less than
    10**12."""

    year: int
    month: int = 1
    day: int = 1
    hour: int = 0
    minute: int = 0
    second: int = 0
    picos: int = 0

    def is_valid(self) -> bool:
        """Says whether every field is in its range.

        Returns:
            Whether it is a time the calendar codes can hold.
        """
        return (1 <= self.year <= 9999 and 1 <= self.month <= 12
                and 1 <= self.day <= days_in_month(self.year, self.month)
                and 0 <= self.hour <= 23 and 0 <= self.minute <= 59
                and (0 <= self.second <= 59
                     or (self.second == 60 and self.hour == 23
                         and self.minute == 59)) and 0 <= self.picos < PICOS)

    def day_of_year(self) -> int:
        """Gives the day of the year.

        Returns:
            1 for the first of January, up to 366.
        """
        return (days_from_civil(self.year, self.month, self.day)
                - days_from_civil(self.year, 1, 1) + 1)

    @staticmethod
    def from_day_of_year(year: int, doy: int) -> Optional[CalendarTime]:
        """Makes midnight at the start of a day of the year.

        Args:
            year: The year, 1 to 9999.
            doy:  The day of the year, 1 to 365, or 366 in a leap year.

        Returns:
            The time; None for a year or day out of range.
        """
        days = 366 if is_leap_year(year) else 365
        if not (1 <= year <= 9999 and 1 <= doy <= days):
            return None
        found = civil_from_days(days_from_civil(year, 1, 1) + doy - 1)
        if found is None:
            return None
        return CalendarTime(*found)

    def rounded(self, unit: int) -> Optional[CalendarTime]:
        """Rounds the picoseconds to a unit, halves up, carrying into the
        second and on through the calendar.  A carry out of second 60 ends
        the day; out of second 59 it goes to the next minute.

        Args:
            unit: The unit, in picoseconds: a power of ten up to 10**12.

        Returns:
            The rounded time; None if it is not valid or the carry runs past
            year 9999.
        """
        if not self.is_valid():
            return None
        n = (self.picos + unit // 2) // unit
        if n * unit < PICOS:
            return replace(self, picos=n * unit)
        if self.second < 59:
            return replace(self, second=self.second + 1, picos=0)
        if self.minute < 59:
            return replace(self, minute=self.minute + 1, second=0, picos=0)
        if self.hour < 23:
            return replace(self, hour=self.hour + 1, minute=0, second=0,
                           picos=0)
        found = civil_from_days(days_from_civil(self.year, self.month, self.day)
                                + 1)
        if found is None:
            return None
        return CalendarTime(*found)


def _bcd(b: int) -> Optional[int]:
    """Reads one octet of two BCD digits, or None if a nibble is not a
    digit."""
    hi, lo = b >> 4, b & 0x0F
    return hi * 10 + lo if hi <= 9 and lo <= 9 else None


def _to_bcd(v: int) -> int:
    """Writes a number of up to two decimal digits as one octet of BCD."""
    return ((v // 10) << 4) | (v % 10)


@dataclass(frozen=True)
class Ccs:
    """The layout of a CCS time: the date as a month and day of the month or
    (``day_of_year``) a day of the year, then ``sub`` octets (0 to 6) of two
    decimal digits of a second each."""

    day_of_year: bool
    sub: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.sub <= 6:
            raise ValueError("a CCS time has 0 to 6 sub-second octets, not "
                             f"{self.sub}")

    def t_len(self) -> int:
        """Gives the octets of the layout's T-field: 7 to 13."""
        return 7 + self.sub

    def unit(self) -> int:
        """Gives the picoseconds of the layout's last digit."""
        return PICOS // 100**self.sub

    def pfield(self) -> bytes:
        """Gives the layout's one-octet P-field."""
        return bytes([(0b101 << 4) | (int(self.day_of_year) << 3) | self.sub])

    @staticmethod
    def from_pfield(data) -> Optional[Ccs]:
        """Reads a CCS P-field.

        Args:
            data: The octets from the P-field on.

        Returns:
            The layout; None if there are none or it isn't a CCS P-field (an
            extension, or the reserved resolution 7).
        """
        if not data:
            return None
        p = data[0]
        if p & 0x80 or (p >> 4) & 7 != 0b101 or p & 7 == 7:
            return None
        return Ccs(bool(p & 8), p & 7)

    def decode_t(self, data) -> Optional[tuple[int, CalendarTime]]:
        """Decodes a T-field in this layout.

        Args:
            data: The octets from the T-field on.

        Returns:
            The octets read and the time; None if ``data`` is too short, an
            octet is not two BCD digits, or the time is not valid.
        """
        n = self.t_len()
        if len(data) < n:
            return None
        fields = [_bcd(b) for b in data[:n]]
        hi, lo = fields[0], fields[1]
        if hi is None or lo is None:
            return None
        year = hi * 100 + lo
        if self.day_of_year:
            if data[2] >> 4 or data[2] & 15 > 9 or fields[3] is None:
                return None
            t = CalendarTime.from_day_of_year(year,
                                              (data[2] & 15) * 100 + fields[3])
            if t is None:
                return None
        else:
            if fields[2] is None or fields[3] is None:
                return None
            t = CalendarTime(year, fields[2], fields[3])
        if any(f is None for f in fields[4:]):
            return None
        picos = 0
        for f in fields[7:]:
            picos = picos * 100 + f
        t = replace(t, hour=fields[4], minute=fields[5], second=fields[6],
                    picos=picos * self.unit())
        return (n, t) if t.is_valid() else None

    def encode_t(self, time: CalendarTime) -> Optional[bytes]:
        """Encodes a time as a T-field in this layout, rounding the
        picoseconds to the layout's unit, halves up, carrying through the
        calendar.

        Args:
            time: The time.

        Returns:
            The T-field; None if the time is not valid or rounds past year
            9999.
        """
        t = time.rounded(self.unit())
        if t is None:
            return None
        out = bytearray([_to_bcd(t.year // 100), _to_bcd(t.year % 100)])
        if self.day_of_year:
            doy = t.day_of_year()
            out += bytes([doy // 100, _to_bcd(doy % 100)])
        else:
            out += bytes([_to_bcd(t.month), _to_bcd(t.day)])
        out += bytes([_to_bcd(t.hour), _to_bcd(t.minute), _to_bcd(t.second)])
        n = t.picos // self.unit()
        sub = bytearray()
        for _ in range(self.sub):
            sub.insert(0, _to_bcd(n % 100))
            n //= 100
        return bytes(out + sub)

    def encode(self, time: CalendarTime) -> Optional[bytes]:
        """Encodes a time with this layout's P-field first."""
        t = self.encode_t(time)
        return None if t is None else self.pfield() + t


def decode_ccs(data) -> Optional[tuple[int, CalendarTime]]:
    """Decodes a CCS time that starts with its P-field.

    Args:
        data: The octets from the P-field on.

    Returns:
        The octets read and the time; None if the P-field isn't a CCS one
        or the T-field can't be decoded.
    """
    layout = Ccs.from_pfield(data)
    if layout is None:
        return None
    t = layout.decode_t(data[1:])
    return None if t is None else (1 + t[0], t[1])


class AsciiForm(enum.Enum):
    """Which ASCII time code: A, with a month and day, or B, with a day of
    the year."""

    A = 0
    B = 1


def _digits(text: str) -> Optional[int]:
    """Reads decimal digits, or None if one is not a digit."""
    if not text or not all("0" <= c <= "9" for c in text):
        return None
    return int(text)


def parse_ascii(text: str) -> Optional[tuple[int, AsciiForm, CalendarTime]]:
    """Parses an ASCII time code, A or B.

    Args:
        text: Characters that start with the time code; any after it are
              left.

    Returns:
        The characters read, which code it is, and the time; None if
        ``text`` does not start with one, its fraction has more than
        ``ASCII_MAX_DIGITS`` digits, or the time is not valid.
    """
    if len(text) < 8 or text[4] != "-":
        return None
    year = _digits(text[0:4])
    if year is None or len(text[0:4]) != 4:
        return None
    if text[7] == "-":
        month, day = _digits(text[5:7]), _digits(text[8:10])
        if month is None or day is None or len(text) < 10:
            return None
        form, t, i = AsciiForm.A, CalendarTime(year, month, day), 10
    else:
        doy = _digits(text[5:8])
        t = None if doy is None else CalendarTime.from_day_of_year(year, doy)
        if t is None:
            return None
        form, i = AsciiForm.B, 8
    if (len(text) < i + 9 or text[i] != "T" or text[i + 3] != ":"
            or text[i + 6] != ":"):
        return None
    hms = [_digits(text[i + 1:i + 3]), _digits(text[i + 4:i + 6]),
           _digits(text[i + 7:i + 9])]
    if any(f is None for f in hms):
        return None
    i += 9
    picos = 0
    if i < len(text) and text[i] == ".":
        n = 0
        while i + 1 + n < len(text) and "0" <= text[i + 1 + n] <= "9":
            n += 1
        if n == 0 or n > ASCII_MAX_DIGITS:
            return None
        picos = int(text[i + 1:i + 1 + n]) * 10**(12 - n)
        i += 1 + n
    if i < len(text) and text[i] == "Z":
        i += 1
    t = replace(t, hour=hms[0], minute=hms[1], second=hms[2], picos=picos)
    return (i, form, t) if t.is_valid() else None


def format_ascii(time: CalendarTime, form: AsciiForm, digits: int,
                 z: bool) -> Optional[str]:
    """Formats a time as an ASCII time code, rounding the picoseconds to the
    digits asked for, halves up, carrying through the calendar.

    Args:
        time:   The time.
        form:   Code A or code B.
        digits: The digits of a fraction of a second, 0 to
                ``ASCII_MAX_DIGITS``; with none, no ``.`` is written.
        z:      Whether to end it with ``Z``.

    Returns:
        The time code; None if ``digits`` is too many, the time is not
        valid, or it rounds past year 9999.
    """
    if not 0 <= digits <= ASCII_MAX_DIGITS:
        return None
    unit = 10**(12 - digits)
    t = time.rounded(unit)
    if t is None:
        return None
    if form == AsciiForm.A:
        date = f"{t.year:04d}-{t.month:02d}-{t.day:02d}"
    else:
        date = f"{t.year:04d}-{t.day_of_year():03d}"
    out = f"{date}T{t.hour:02d}:{t.minute:02d}:{t.second:02d}"
    if digits:
        out += f".{t.picos // unit:0{digits}d}"
    return out + ("Z" if z else "")
