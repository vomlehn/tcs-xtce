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
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional


class Epoch(enum.Enum):
    """The epoch a time code counts from."""

    CCSDS = 0  # 1958-01-01T00:00:00 TAI
    AGENCY = 1  # one that the agency defines, such as the Unix epoch


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
        n = self.t_len()
        if len(data) < n:
            return None
        whole = int.from_bytes(bytes(data[:self.coarse]), "big")
        # The fraction, least significant octet first, as Rust does.
        frac = 0.0
        for b in reversed(data[self.coarse:n]):
            frac = (frac + b) / 256.0
        return n, float(whole) + frac

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
    found = Cuc.from_pfield(data)
    if found is None:
        return None
    layout, n = found
    t = layout.decode_t(data[n:])
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
        n = self.t_len()
        if len(data) < n:
            return None
        d = 3 if self.day24 else 2
        day = int.from_bytes(bytes(data[:d]), "big")
        ms = int.from_bytes(bytes(data[d:d + 4]), "big")
        if ms >= 86_401_000:
            return None
        t = float(day) * 86400.0 + float(ms) / 1000.0
        sub = int.from_bytes(bytes(data[d + 4:n]), "big")
        if self.sub == SubMs.MICRO:
            if sub >= 1000:
                return None
            t += float(sub) / 1e6
        elif self.sub == SubMs.PICO:
            if sub >= 1_000_000_000:
                return None
            t += float(sub) / 1e12
        return n, t

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
