"""Runtime for packet codecs generated from a mission's XTCE document.

``tcsxtce`` (in ``generator/``) turns the containers and meta-commands of a
mission database into Python dataclasses derived from :class:`Packet`.  The
generated code reads packets with a :class:`BitReader` and writes them with a
:class:`BitWriter` into a :class:`~xtce.ManagedBuffer`; this module provides
those and the integer, float, string and calibration helpers they call.  It
mirrors ``xtce::packet`` in Rust and ``xtce/packet.h`` in C and C++.

A :class:`Codec` owns one input and one output buffer, preallocated for the
largest packet it will see and reused until the codec is discarded.
"""

from __future__ import annotations

import math
import struct
from typing import Optional

from ._buffer import ManagedBuffer
from ._xml import f_double

__all__ = [
    "BitReader",
    "BitWriter",
    "Codec",
    "Packet",
    "PacketError",
    "PacketKind",
]


class PacketError(ValueError):
    """Why a packet could not be decoded or encoded.

    ``kind`` names the error and is the same in every runtime: ``Truncated``,
    ``BadPosition``, ``NoMatch``, ``Mismatch``, ``OutOfRange``,
    ``InvalidString``, ``TooLong``, ``Calibration``, ``NotInvertible``,
    ``Missing`` or ``BadSize``.
    """

    MESSAGES = {
        "Truncated": "packet too short for {0}",
        "BadPosition": "{0} is placed before the packet start",
        "NoMatch": "no container derived from {0} matches",
        "Mismatch": "{0} requires {1}",
        "OutOfRange": "value of {0} does not fit its encoding",
        "InvalidString": "invalid string in {0}",
        "TooLong": "{0} is too long for its encoding",
        "Calibration": "{0} is outside its calibration range",
        "NotInvertible": "the calibrator of {0} cannot be inverted for encoding",
        "Missing": "{0} is required by its include condition",
        "BadSize": "invalid size or count for {0}",
    }

    def __init__(self, kind: str, *what: str) -> None:
        super().__init__(self.MESSAGES[kind].format(*what))
        self.kind = kind
        self.what = what


class Packet:
    """Base of every generated packet class."""

    __slots__ = ()
    NAME = ""
    MAX_BYTES = 0

    def encode_into(self, w: "BitWriter") -> None:  # pragma: no cover - generated
        raise NotImplementedError

    def encode(self, buf: ManagedBuffer) -> None:
        """Replace the contents of ``buf`` with the encoded packet."""
        self.encode_into(BitWriter(buf))

    def to_bytes(self) -> bytes:
        buf = ManagedBuffer(self.MAX_BYTES)
        self.encode(buf)
        return buf.getvalue()

    def __str__(self) -> str:
        return self.describe()


class PacketKind:
    """Base of generated dispatch classes; ``decode`` returns a member."""

    NAME = ""
    MAX_BYTES = 0
    MEMBERS: tuple = ()


class Codec:
    """Owns the managed input and output buffers for one kind of packet."""

    __slots__ = ("_input", "_output")

    def __init__(self, capacity: int = 4096) -> None:
        self._input = ManagedBuffer(capacity)
        self._output = ManagedBuffer(capacity)

    @classmethod
    def for_packet(cls, packet_class) -> "Codec":
        """A codec whose buffers already fit the largest ``packet_class``."""
        return cls(packet_class.MAX_BYTES)

    def load(self, data) -> None:
        """Copy ``data`` into the input buffer."""
        self._input.load(data)

    def read_from(self, stream) -> int:
        """Fill the input buffer from a binary stream until end of file."""
        return self._input.load_from(stream)

    @property
    def input(self) -> ManagedBuffer:
        """The input buffer (as for :attr:`xtce.Document.input`)."""
        return self._input

    @property
    def output(self) -> ManagedBuffer:
        """The output buffer holding the most recent encoding."""
        return self._output

    def decode(self, packet_class):
        """Decode the input buffer as ``packet_class`` (a packet or kind)."""
        with self._input.view() as v:
            return packet_class.decode(v)

    def encode(self, packet) -> memoryview:
        """Encode into the output buffer and return a view of it.

        Release the view before the next ``encode`` if that packet may be
        larger than the buffer's capacity.
        """
        packet.encode(self._output)
        return self._output.view()

    @property
    def input_capacity(self) -> int:
        return self._input.capacity

    @property
    def output_capacity(self) -> int:
        return self._output.capacity


def _position(base: int, offset: int, field: str) -> int:
    p = base + offset
    if p < 0:
        raise PacketError("BadPosition", field)
    return p


class BitReader:
    """Reads big-endian bit fields from a bytes-like object."""

    __slots__ = ("data", "pos", "nbits")

    def __init__(self, data) -> None:
        self.data = data
        self.pos = 0
        self.nbits = len(data) * 8

    def position(self) -> int:
        return self.pos

    def seek(self, start: int, offset: int, field: str) -> None:
        self.pos = _position(start, offset, field)

    def skip(self, offset: int, field: str) -> None:
        self.pos = _position(self.pos, offset, field)

    def uint(self, bits: int, field: str) -> int:
        end = self.pos + bits
        if end > self.nbits:
            raise PacketError("Truncated", field)
        first, last = self.pos >> 3, (end + 7) >> 3
        v = int.from_bytes(self.data[first:last], "big") >> (last * 8 - end)
        self.pos = end
        return v & ((1 << bits) - 1)

    def uint_order(self, bits: int, le: bool, field: str) -> int:
        v = self.uint(bits, field)
        return int.from_bytes(v.to_bytes(bits // 8, "big"), "little") if le else v

    def bytes(self, n: int, field: str) -> bytes:
        if self.pos + n * 8 > self.nbits:
            raise PacketError("Truncated", field)
        if self.pos % 8 == 0:
            start = self.pos >> 3
            self.pos += n * 8
            return bytes(self.data[start : start + n])
        return self.uint(n * 8, field).to_bytes(n, "big") if n else b""


class BitWriter:
    """Writes big-endian bit fields into a :class:`~xtce.ManagedBuffer`.

    The buffer is cleared when the writer is created and grows (zero filled)
    to cover the furthest bit written; its allocation is reused.
    """

    __slots__ = ("buf", "pos")

    def __init__(self, buf: ManagedBuffer) -> None:
        buf.clear()
        self.buf = buf
        self.pos = 0

    def position(self) -> int:
        return self.pos

    def seek(self, start: int, offset: int, field: str) -> None:
        self.pos = _position(start, offset, field)

    def skip(self, offset: int, field: str) -> None:
        self.pos = _position(self.pos, offset, field)

    def uint(self, bits: int, v: int) -> None:
        end = self.pos + bits
        first, last = self.pos >> 3, (end + 7) >> 3
        if len(self.buf) < last:
            self.buf.resize(last)
        d = self.buf._data
        shift = last * 8 - end
        m = ((1 << bits) - 1) << shift
        cur = int.from_bytes(d[first:last], "big")
        cur = (cur & ~m) | ((v << shift) & m)
        d[first:last] = cur.to_bytes(last - first, "big")
        self.pos = end

    def uint_order(self, bits: int, le: bool, v: int) -> None:
        if le:
            v = int.from_bytes(v.to_bytes(bits // 8, "big"), "little")
        self.uint(bits, v)

    def bytes(self, data) -> None:
        if data:
            self.uint(len(data) * 8, int.from_bytes(data, "big"))


# -- integers -------------------------------------------------------------------


def to_signed(raw: int, bits: int, enc: str) -> int:
    """Convert a raw field to its value (``enc`` as in the XTCE schema)."""
    sign = (raw >> (bits - 1)) & 1
    low = (1 << (bits - 1)) - 1
    if enc == "unsigned" or not sign:
        return raw if enc == "unsigned" else raw & low
    if enc == "twosComplement":
        return raw - (1 << bits)
    if enc == "signMagnitude":
        return -(raw & low)
    return -(~raw & low)


def from_signed(v: int, bits: int, enc: str, field: str) -> int:
    """Convert a value to its raw field, checking that it fits."""
    v = int(v)
    if enc == "unsigned":
        return from_unsigned(v, bits, field)
    limit = (1 << (bits - 1)) - 1
    if enc == "twosComplement":
        if not -limit - 1 <= v <= limit:
            raise PacketError("OutOfRange", field)
        return v & ((1 << bits) - 1)
    m = abs(v)
    if m > limit:
        raise PacketError("OutOfRange", field)
    if v >= 0:
        return m
    if enc == "signMagnitude":
        return (1 << (bits - 1)) | m
    return ~m & ((1 << bits) - 1)


def from_unsigned(v: int, bits: int, field: str) -> int:
    v = int(v)
    if not 0 <= v < (1 << bits):
        raise PacketError("OutOfRange", field)
    return v


def int_to_float(raw: int, bits: int, enc: str) -> float:
    return float(to_signed(raw, bits, enc))


def _round(x: float) -> int:
    """Round half away from zero, like Rust's ``f64::round`` and C++'s ``std::round``."""
    i = math.trunc(x)
    frac = x - i
    if frac >= 0.5:
        i += 1
    elif frac <= -0.5:
        i -= 1
    return i


def float_to_int(x: float, bits: int, enc: str, field: str) -> int:
    """Round an engineering value to a raw integer field."""
    if not math.isfinite(x):
        raise PacketError("OutOfRange", field)
    r = _round(x)
    if abs(r) >= 1 << 64 or (enc != "unsigned" and not -(1 << 63) <= r < 1 << 63):
        raise PacketError("OutOfRange", field)
    return from_signed(r, bits, enc, field)


# -- floats ---------------------------------------------------------------------


def float_from_raw(raw: int, bits: int) -> float:
    if bits == 32:
        return struct.unpack(">f", raw.to_bytes(4, "big"))[0]
    return struct.unpack(">d", raw.to_bytes(8, "big"))[0]


def float_to_raw(v: float, bits: int) -> int:
    if bits == 32:
        try:
            b = struct.pack(">f", v)
        except OverflowError:  # Rust and C++ round to infinity
            b = struct.pack(">f", math.copysign(math.inf, v))
        return int.from_bytes(b, "big")
    return int.from_bytes(struct.pack(">d", v), "big")


# -- calibrators -------------------------------------------------------------------


def polynomial(x: float, terms) -> float:
    y = 0.0
    for c, e in terms:
        y += c if e == 0 else c * x if e == 1 else c * math.pow(x, e)
    return y


def polynomial_inverse(y: float, intercept: float, slope: float) -> float:
    return (y - intercept) / slope


def _lerp(x: float, p0, p1) -> float:
    (x0, y0), (x1, y1) = p0, p1
    return y0 + (x - x0) * (y1 - y0) / (x1 - x0)


def spline(x: float, points, order: int, extrapolate: bool, field: str) -> float:
    first, last = points[0], points[-1]
    if x < first[0] or x > last[0]:
        if not extrapolate:
            raise PacketError("Calibration", field)
        if order == 0:
            return first[1] if x < first[0] else last[1]
        return _lerp(x, first, points[1]) if x < first[0] else _lerp(x, points[-2], last)
    for i in range(len(points) - 1):
        if x < points[i + 1][0]:
            return points[i][1] if order == 0 else _lerp(x, points[i], points[i + 1])
    return last[1]


def spline_inverse(y: float, points, extrapolate: bool, field: str) -> float:
    flip = [(c, r) for r, c in points]
    for i in range(len(points) - 1):
        a, b = points[i][1], points[i + 1][1]
        if a <= y <= b or b <= y <= a:
            return _lerp(y, flip[i], flip[i + 1])
    if not extrapolate:
        raise PacketError("Calibration", field)
    increasing = points[-1][1] > points[0][1]
    if (y < points[0][1]) == increasing:
        return _lerp(y, flip[0], flip[1])
    return _lerp(y, flip[-2], flip[-1])


# -- sizes from other fields ----------------------------------------------------------


def dynamic_size(x, slope: float, intercept: float, unit: int, max_: int, field: str) -> int:
    """A size or count that another field gives (an XTCE ``DynamicValue``):
    ``(slope * x + intercept) / unit``, where ``x`` is that field's value and
    ``unit`` is 8 for a size in bits used in whole bytes.  Raises ``BadSize``
    unless the result is a whole number from 0 to ``max_``."""
    y = slope * float(x) + intercept
    if not (y >= 0.0) or not y.is_integer() or not (y / unit).is_integer() or y / unit > max_:
        raise PacketError("BadSize", field)
    return int(y / unit)


def read_list(r: "BitReader", n: int, gap: int, field: str, read) -> list:
    """``n`` values read by ``read()``, with ``gap`` bits between them."""
    out = []
    for i in range(n):
        if i and gap:
            r.skip(gap, field)
        out.append(read())
    return out


def check_size(v, n: int, field: str) -> None:
    """Raises ``BadSize`` unless ``v`` has ``n`` elements or bytes."""
    if len(v) != n:
        raise PacketError("BadSize", field)


def sized(spec: tuple, n: int) -> tuple:
    """The spec of a string whose size another field gives: exactly ``n`` bytes."""
    return (n * 8, n * 8, spec[2], spec[3])


# -- strings and binary -------------------------------------------------------------


def read_string(r: BitReader, spec: tuple, field: str) -> str:
    """Read a string; ``spec`` is (fixed_bits, max_bits, terminator, size_tag_bits)."""
    fixed, max_bits, term, tag = spec
    start = r.pos
    if fixed is not None and term is not None:
        b = r.bytes(fixed // 8, field)
        i = b.find(bytes([term]))
        if i >= 0:
            b = b[:i]
    elif fixed is not None:
        n = r.uint(tag, field)
        if n * 8 + tag > fixed:
            raise PacketError("TooLong", field)
        b = r.bytes(n, field)
        r.seek(start, fixed, field)
    elif term is not None:
        out = bytearray()
        while True:
            if (len(out) + 1) * 8 > max_bits:
                raise PacketError("TooLong", field)
            c = r.uint(8, field)
            if c == term:
                break
            out.append(c)
        b = bytes(out)
    else:
        n = r.uint(tag, field)
        if n * 8 + tag > max_bits:
            raise PacketError("TooLong", field)
        b = r.bytes(n, field)
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        raise PacketError("InvalidString", field) from None


def write_string(w: BitWriter, spec: tuple, s: str, field: str) -> None:
    fixed, max_bits, term, tag = spec
    try:
        b = s.encode("utf-8")
    except UnicodeEncodeError:
        raise PacketError("InvalidString", field) from None
    if term is not None:
        if term in b:
            raise PacketError("InvalidString", field)
        room = (fixed if fixed is not None else max_bits) // 8
        need = len(b) if fixed is not None else len(b) + 1
        if need > room:
            raise PacketError("TooLong", field)
        w.bytes(b)
        if len(b) < room:
            w.uint(8, term)
        if fixed is not None and len(b) + 1 < fixed // 8:
            w.bytes(bytes(fixed // 8 - len(b) - 1))
    else:
        room = fixed if fixed is not None else max_bits
        if len(b) * 8 + tag > room or len(b) >= 1 << tag:
            raise PacketError("TooLong", field)
        w.uint(tag, len(b))
        w.bytes(b)
        if fixed is not None and fixed - tag - len(b) * 8:
            w.uint(fixed - tag - len(b) * 8, 0)


def write_binary(w: BitWriter, b: bytes, n: int, field: str) -> None:
    if len(b) != n:
        raise PacketError("TooLong" if len(b) > n else "OutOfRange", field)
    w.bytes(b)


def enum_value(cls, v: int):
    """``cls(v)`` for an IntEnum, keeping values the enumeration does not list."""
    try:
        return cls(v)
    except ValueError:
        m = int.__new__(cls, v)
        m._name_ = None
        m._value_ = v
        return m


# -- canonical text (identical in every runtime) ---------------------------------------


def describe_float(x: float) -> str:
    return f_double(x)


def describe_str(s: str) -> str:
    out = ['"']
    for c in s:
        if c == '"':
            out.append('\\"')
        elif c == "\\":
            out.append("\\\\")
        elif ord(c) < 0x20 or ord(c) == 0x7F:
            out.append(f"\\x{ord(c):02x}")
        else:
            out.append(c)
    out.append('"')
    return "".join(out)


def describe_optional(v, describe) -> str:
    return "none" if v is None else describe(v)


def describe_list(v, describe) -> str:
    return "[" + ", ".join(describe(x) for x in v) + "]"


def describe_bytes(b: bytes) -> str:
    return "0x" + bytes(b).hex()


def describe_enum(v, labels) -> str:
    label = find_label(int(v), labels)
    return label if label is not None else str(int(v))


def find_label(v: int, labels) -> Optional[str]:
    for label, lo, hi in labels:
        if lo <= v <= hi:
            return label
    return None
