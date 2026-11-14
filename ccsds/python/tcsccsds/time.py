"""The binary time codes of CCSDS 301.0-B-4, *Time Code Formats*: CUC, the
CCSDS Unsegmented time Code, and CDS, the CCSDS Day Segmented time code.

A time code is an optional P-field (preamble), which says which code it is
and how it is laid out, then a T-field holding the time.  A time is given
as seconds since the code's epoch, as a float: either the CCSDS epoch
(1958-01-01, TAI) or one the agency, that is the mission, defines.
Decoding gives how many octets it read and the seconds, or None; encoding
gives the octets, or None for a time the layout can't hold.

Every computation is IEEE double arithmetic that the Rust and C versions
do the same way, so all three give the same octets and seconds.  Encoding
rounds to the nearest unit of the layout, halves up, carrying into the
seconds or days.

A float holds about a quarter of a microsecond at today's times, which is
coarser than a CUC time of more than three fine octets or a CDS time of
microseconds or picoseconds.  So each code also has an exact form, in
integers, that holds every time its layouts can: ``CucTime``, whose
fraction is in units of 2**-80, and ``CdsTime``, in days, milliseconds and
picoseconds.  Its decoders and encoders are the ``_exact`` ones, and the
float decoders are the exact ones then a conversion.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional


class Epoch(enum.Enum):
    """The epoch a time code counts from."""

    CCSDS = 0  # 1958-01-01T00:00:00 TAI
    AGENCY = 1  # one that the agency defines, such as the Unix epoch


# The bits of binary fraction a CUC time can hold: ten fine octets' worth.
CUC_FRACTION_BITS = 80


@dataclass(frozen=True)
class CucTime:
    """A CUC time, exactly: whole seconds since the epoch, and the fraction
    of a second in units of 2**-80 (less than 2**80), which holds every
    layout's fine octets."""

    seconds: int
    fraction: int = 0

    def to_seconds(self) -> float:
        """Gives the time as seconds since the epoch, as the float decoders
        do.

        Returns:
            The seconds.
        """
        frac = 0.0
        # The fraction, least significant octet first, as Rust does.
        for i in range(CUC_FRACTION_BITS // 8):
            frac = (frac + float((self.fraction >> (8 * i)) & 0xFF)) / 256.0
        return float(self.seconds) + frac


@dataclass(frozen=True)
class CdsTime:
    """A CDS time, exactly: days since the epoch, milliseconds of the day
    (less than 86 401 000, a day with a leap second having a thousand
    more), and picoseconds of the millisecond (less than 10**9)."""

    day: int
    ms: int = 0
    picos: int = 0

    def whole_ms(self) -> float:
        """Gives the days and milliseconds as seconds, as every CDS float
        starts from.

        Returns:
            The seconds.
        """
        return float(self.day) * 86400.0 + float(self.ms) / 1000.0

    def to_seconds(self) -> float:
        """Gives the time as seconds since the epoch, as a float: its
        picoseconds divided by 10**12, which is what the float decoder of
        a picosecond layout gives.

        Returns:
            The seconds.
        """
        return self.whole_ms() + float(self.picos) / 1e12


@dataclass(frozen=True)
class Cuc:
    """The layout of a CUC time: ``coarse`` octets (1 to 7) of whole
    seconds, most significant first, then ``fine`` octets (0 to 10) of
    binary fractions of a second, 2**-8 for the first."""

    coarse: int
    fine: int
    epoch: Epoch = Epoch.CCSDS

    def __post_init__(self) -> None:
        if not (1 <= self.coarse <= 7 and 0 <= self.fine <= 10):
            raise ValueError(f"CUC with {self.coarse} coarse and {self.fine} "
                             "fine octets")

    def t_len(self) -> int:
        """The octets of its T-field."""
        return self.coarse + self.fine

    def pfield(self) -> bytes:
        """Makes its P-field.

        Returns:
            One octet, or two when it has more than 4 coarse or 3 fine
            octets.
        """
        ident = 1 if self.epoch == Epoch.CCSDS else 2
        c, f = min(self.coarse, 4), min(self.fine, 3)
        more = self.coarse > 4 or self.fine > 3
        first = (more << 7) | (ident << 4) | ((c - 1) << 2) | f
        if not more:
            return bytes([first])
        return bytes([first, ((self.coarse - c) << 5) | ((self.fine - f) << 2)])

    @staticmethod
    def from_pfield(data) -> Optional[tuple[Cuc, int]]:
        """Reads a CUC P-field.

        Args:
            data: The octets from the P-field on.

        Returns:
            The layout the P-field gives and the P-field's length; None if
            it isn't a CUC P-field.
        """
        if not data:
            return None
        first = data[0]
        ident = (first >> 4) & 7
        if ident not in (1, 2):
            return None
        epoch = Epoch.CCSDS if ident == 1 else Epoch.AGENCY
        coarse, fine = ((first >> 2) & 3) + 1, first & 3
        if not first & 0x80:
            return Cuc(coarse, fine, epoch), 1
        if len(data) < 2 or data[1] & 0x83:
            # A further extension, or the reserved bits, aren't defined.
            return None
        coarse += (data[1] >> 5) & 3
        fine += (data[1] >> 2) & 7
        if coarse > 7 or fine > 10:
            return None
        return Cuc(coarse, fine, epoch), 2

    def decode_t(self, data) -> Optional[tuple[int, float]]:
        """Decodes a T-field in this layout.

        Args:
            data: The octets from the T-field on.

        Returns:
            The octets read and the seconds since the epoch; None if
            ``data`` is too short.
        """
        found = self.decode_t_exact(data)
        return None if found is None else (found[0], found[1].to_seconds())

    def decode_t_exact(self, data) -> Optional[tuple[int, CucTime]]:
        """Decodes a T-field in this layout, exactly.

        Args:
            data: The octets from the T-field on.

        Returns:
            The octets read and the time; None if ``data`` is too short.
        """
        n = self.t_len()
        if len(data) < n:
            return None
        seconds = int.from_bytes(bytes(data[:self.coarse]), "big")
        fine = int.from_bytes(bytes(data[self.coarse:n]), "big")
        fraction = fine << (CUC_FRACTION_BITS - 8 * self.fine)
        return n, CucTime(seconds, fraction)

    def encode_t_exact(self, time: CucTime) -> Optional[bytes]:
        """Encodes a time as a T-field in this layout, exactly where the
        layout has the bits and otherwise rounding the fraction to its fine
        octets, halves up, carrying into the seconds.

        Args:
            time: The time.

        Returns:
            The T-field, or None if a field of the time is out of range or
            the layout can't hold it.
        """
        if (not (0 <= time.fraction < 1 << CUC_FRACTION_BITS
                 and time.seconds >= 0)):
            return None
        drop = CUC_FRACTION_BITS - 8 * self.fine
        m = time.fraction >> drop
        seconds = time.seconds
        if drop and (time.fraction >> (drop - 1)) & 1:
            m += 1
            if m >> (8 * self.fine):
                m, seconds = 0, seconds + 1
        if seconds >> (8 * self.coarse):
            return None
        return (seconds.to_bytes(self.coarse, "big")
                + m.to_bytes(self.fine, "big"))

    def encode_exact(self, time: CucTime) -> Optional[bytes]:
        """Encodes a time exactly, with this layout's P-field first."""
        t = self.encode_t_exact(time)
        return None if t is None else self.pfield() + t

    def encode_t(self, seconds: float) -> Optional[bytes]:
        """Encodes ``seconds`` as a T-field in this layout.

        Args:
            seconds: The time in seconds since the epoch.

        Returns:
            The T-field, or None if the time is negative, not a number, or
            too large for the coarse octets.
        """
        limit = float(1 << (8 * self.coarse))
        if not (seconds >= 0.0 and seconds < limit):
            return None
        whole = int(seconds)
        frac = seconds - float(whole)
        f = self.fine
        if f <= 6:
            # To the nearest 2**-8f, which may carry a whole second.
            m = int(frac * float(1 << (8 * f)) + 0.5)
            if m == 1 << (8 * f):
                m, whole = 0, whole + 1
            fine = m.to_bytes(f, "big")
        else:
            # More fine octets than a float has bits: the fraction is exact.
            out = bytearray()
            x = frac
            for _ in range(f):
                x *= 256.0
                out.append(int(x))
                x -= float(out[-1])
            fine = bytes(out)
        if float(whole) >= limit:
            return None
        return whole.to_bytes(self.coarse, "big") + fine

    def encode(self, seconds: float) -> Optional[bytes]:
        """Encodes ``seconds`` with this layout's P-field first."""
        t = self.encode_t(seconds)
        return None if t is None else self.pfield() + t


def decode_cuc(data) -> Optional[tuple[int, float]]:
    """Decodes a CUC time that starts with its P-field.

    Args:
        data: The octets from the P-field on.

    Returns:
        The octets read and the seconds since the epoch the P-field
        names; None if the P-field isn't a CUC one or ``data`` is too
        short.
    """
    found = decode_cuc_exact(data)
    return None if found is None else (found[0], found[1].to_seconds())


def decode_cuc_exact(data) -> Optional[tuple[int, CucTime]]:
    """Decodes a CUC time that starts with its P-field, exactly.

    Args:
        data: The octets from the P-field on.

    Returns:
        The octets read and the time since the epoch the P-field names;
        None if the P-field isn't a CUC one or ``data`` is too short.
    """
    found = Cuc.from_pfield(data)
    if found is None:
        return None
    layout, n = found
    t = layout.decode_t_exact(data[n:])
    return None if t is None else (n + t[0], t[1])


class SubMs(enum.Enum):
    """The resolution of a CDS time below a millisecond."""

    NONE = 0
    MICRO = 1  # 16 bits of microseconds of the millisecond
    PICO = 2  # 32 bits of picoseconds of the millisecond


_PER_MS = {SubMs.NONE: 1, SubMs.MICRO: 1000, SubMs.PICO: 1_000_000_000}
_SUB_LEN = {SubMs.NONE: 0, SubMs.MICRO: 2, SubMs.PICO: 4}


@dataclass(frozen=True)
class Cds:
    """The layout of a CDS time: days since the epoch, in 16 bits or
    (``day24``) 24, then milliseconds of the day in 32 bits, then ``sub``."""

    day24: bool = False
    sub: SubMs = SubMs.NONE
    epoch: Epoch = Epoch.CCSDS

    def t_len(self) -> int:
        """The octets of its T-field."""
        return (3 if self.day24 else 2) + 4 + _SUB_LEN[self.sub]

    def pfield(self) -> bytes:
        """Its one-octet P-field."""
        return bytes([(4 << 4) | ((self.epoch == Epoch.AGENCY) << 3)
                      | (self.day24 << 2) | self.sub.value])

    @staticmethod
    def from_pfield(data) -> Optional[Cds]:
        """Reads a CDS P-field.

        Args:
            data: The octets from the P-field on.

        Returns:
            The layout the P-field gives; None if it isn't a CDS P-field.
        """
        if not data:
            return None
        p = data[0]
        if p & 0x80 or (p >> 4) & 7 != 4 or p & 3 == 3:
            return None
        return Cds(bool(p & 4), SubMs(p & 3),
                   Epoch.AGENCY if p & 8 else Epoch.CCSDS)

    def decode_t(self, data) -> Optional[tuple[int, float]]:
        """Decodes a T-field in this layout.

        Args:
            data: The octets from the T-field on.

        Returns:
            The octets read and the seconds since the epoch; None if
            ``data`` is too short or a field is beyond its range
            (milliseconds of a day count a leap second's too).
        """
        found = self.decode_t_exact(data)
        if found is None:
            return None
        n, t = found
        # In the layout's own unit, so microseconds are divided as such.
        if self.sub == SubMs.MICRO:
            return n, t.whole_ms() + float(t.picos // 1_000_000) / 1e6
        if self.sub == SubMs.PICO:
            return n, t.to_seconds()
        return n, t.whole_ms()

    def decode_t_exact(self, data) -> Optional[tuple[int, CdsTime]]:
        """Decodes a T-field in this layout, exactly.

        Args:
            data: The octets from the T-field on.

        Returns:
            The octets read and the time; None if ``data`` is too short or
            a field is beyond its range (milliseconds of a day count a leap
            second's too).
        """
        n = self.t_len()
        if len(data) < n:
            return None
        d = 3 if self.day24 else 2
        day = int.from_bytes(bytes(data[:d]), "big")
        ms = int.from_bytes(bytes(data[d:d + 4]), "big")
        if ms >= 86_401_000:
            return None
        sub = int.from_bytes(bytes(data[d + 4:n]), "big")
        if self.sub == SubMs.MICRO:
            if sub >= 1000:
                return None
            sub *= 1_000_000
        elif self.sub == SubMs.PICO and sub >= 1_000_000_000:
            return None
        return n, CdsTime(day, ms, sub)

    def encode_t_exact(self, time: CdsTime) -> Optional[bytes]:
        """Encodes a time as a T-field in this layout, exactly where the
        layout has the resolution and otherwise rounding to its unit, halves
        up, carrying into the milliseconds and the days.  A time in a leap
        second carries into the next day only past the leap second.

        Args:
            time: The time.

        Returns:
            The T-field, or None if a field of the time is out of range or
            the layout can't hold it.
        """
        days = 1 << (24 if self.day24 else 16)
        if (not (0 <= time.ms < 86_401_000 and 0 <= time.picos < 10**9
                 and time.day >= 0)):
            return None
        unit = {SubMs.NONE: 10**9, SubMs.MICRO: 10**6, SubMs.PICO: 1}[self.sub]
        day, ms = time.day, time.ms
        sub = (time.picos + unit // 2) // unit
        if sub * unit == 10**9:
            # The day ends a second later when this millisecond was in its
            # leap second.
            end = 86_401_000 if time.ms >= 86_400_000 else 86_400_000
            sub, ms = 0, ms + 1
            if ms == end:
                ms, day = 0, day + 1
        if day >= days:
            return None
        d = 3 if self.day24 else 2
        out = day.to_bytes(d, "big") + ms.to_bytes(4, "big")
        return out + sub.to_bytes(_SUB_LEN[self.sub], "big")

    def encode_exact(self, time: CdsTime) -> Optional[bytes]:
        """Encodes a time exactly, with this layout's P-field first."""
        t = self.encode_t_exact(time)
        return None if t is None else self.pfield() + t

    def encode_t(self, seconds: float) -> Optional[bytes]:
        """Encodes ``seconds`` as a T-field in this layout.

        Args:
            seconds: The time in seconds since the epoch.

        Returns:
            The T-field, or None if the time is negative, not a number, or
            has more days than the day field holds.
        """
        days = 1 << (24 if self.day24 else 16)
        if not (seconds >= 0.0 and seconds < float(days) * 86400.0):
            return None
        # To the nearest unit, which may carry a whole day.
        per_ms = _PER_MS[self.sub]
        per_day = 86_400_000 * per_ms
        day = int(seconds / 86400.0)
        rem = seconds - float(day) * 86400.0
        m = int(rem * float(1000 * per_ms) + 0.5)
        if m >= per_day:
            m, day = m - per_day, day + 1
        if day >= days:
            return None
        d = 3 if self.day24 else 2
        out = day.to_bytes(d, "big") + (m // per_ms).to_bytes(4, "big")
        return out + (m % per_ms).to_bytes(_SUB_LEN[self.sub], "big")

    def encode(self, seconds: float) -> Optional[bytes]:
        """Encodes ``seconds`` with this layout's P-field first."""
        t = self.encode_t(seconds)
        return None if t is None else self.pfield() + t


def decode_cds(data) -> Optional[tuple[int, float]]:
    """Decodes a CDS time that starts with its P-field.

    Args:
        data: The octets from the P-field on.

    Returns:
        The octets read and the seconds since the epoch the P-field
        names; None if the P-field isn't a CDS one or a T-field can't
        be decoded.
    """
    layout = Cds.from_pfield(data)
    if layout is None:
        return None
    t = layout.decode_t(data[1:])
    return None if t is None else (1 + t[0], t[1])


def decode_cds_exact(data) -> Optional[tuple[int, CdsTime]]:
    """Decodes a CDS time that starts with its P-field, exactly.

    Args:
        data: The octets from the P-field on.

    Returns:
        The octets read and the time since the epoch the P-field names;
        None if the P-field isn't a CDS one or a T-field can't be decoded.
    """
    layout = Cds.from_pfield(data)
    if layout is None:
        return None
    t = layout.decode_t_exact(data[1:])
    return None if t is None else (1 + t[0], t[1])
