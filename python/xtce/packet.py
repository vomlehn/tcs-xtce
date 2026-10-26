"""Runtime for packet codecs generated from a mission's XTCE document.

``tcs-xtce`` (in ``generator/``) turns the containers and meta-commands of a
mission database into Python dataclasses derived from :class:`Packet`.  The
generated code reads packets with a :class:`BitReader` and writes them with a
:class:`BitWriter` into a :class:`~xtce.ManagedBuffer`; this module provides
those and the integer, float, string and calibration helpers they call.  It
mirrors ``tcs_xtce::packet`` in Rust and ``xtce/packet.h`` in C and C++.

A :class:`Codec` owns one input and one output buffer, preallocated for the
largest packet it will see and reused until the codec is discarded.  A
:class:`Batch` carries several packets in one buffer, after their length, so
a receiver gets them all in two reads.
"""

from __future__ import annotations

import enum
import math
import struct
from fractions import Fraction
from typing import Optional

from ._buffer import ManagedBuffer
from ._xml import f_double

__all__ = ["BATCH_LENGTH_BYTES", "Batch", "BitReader", "BitWriter", "Codec",
           "Packet", "PacketError", "PacketKind"]


class PacketError(ValueError):
    """Why a packet could not be decoded or encoded.

    ``kind`` names the error and is the same in every runtime: ``Truncated``,
    ``BadPosition``, ``NoMatch``, ``Mismatch``, ``OutOfRange``,
    ``InvalidString``, ``TooLong``, ``Calibration``, ``NotInvertible``,
    ``Missing``, ``BadSize``, ``BadLength``, ``InvalidValue``,
    ``BadChecksum`` or ``NoAlgorithm``.
    """

    MESSAGES = {
        "Truncated": "packet too short for {0}",
        "BadPosition": "{0} is placed before the packet start",
        "NoMatch": "no container derived from {0} matches",
        "Mismatch": "{0} requires {1}",
        "OutOfRange": ("value of {0} is outside its valid range or encoding"),
        "InvalidString": "invalid string in {0}",
        "TooLong": "{0} is too long for its encoding",
        "Calibration": "{0} is outside its calibration range",
        "NotInvertible": ("the calibrator of {0} cannot be inverted for "
                          "encoding"),
        "Missing": "{0} is required by its include condition",
        "BadSize": "invalid size or count for {0}",
        "BadLength": "{0} does not match the length of the packet",
        "InvalidValue": ("the bits of {0} are not a value of its encoding"),
        "BadChecksum": "{0} does not match the data it checks",
        "NoAlgorithm": ("no function is registered for the custom algorithm "
                        "{0}")}

    def __init__(self, kind: str, *what: str) -> None:
        super().__init__(self.MESSAGES[kind].format(*what))
        self.kind = kind
        self.what = what


class Packet:
    """Base of every generated packet class."""

    __slots__ = ()
    NAME = ""
    MAX_BYTES = 0

    @classmethod
    def decode(cls, data):
        """Decode a packet that starts at the first byte of ``data``.  Bytes
        after the packet are ignored."""
        return cls.decode_prefix(data)[0]

    # Generated packet classes override these.
    @classmethod
    def decode_prefix(cls, data) -> tuple:  # pragma: no cover
        raise NotImplementedError

    def encode_into(self, w: "BitWriter") -> None:  # pragma: no cover
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

    @classmethod
    def decode(cls, data):
        """Decode a packet that starts at the first byte of ``data`` as the
        member whose criteria match."""
        return cls.decode_prefix(data)[0]


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


# The bytes of the length that starts a Batch.
BATCH_LENGTH_BYTES = 4


class Batch:
    """Several packets in one buffer: the number of bytes that follow, as a
    32-bit big-endian integer, then the packets one after another.

    The length is always sent, so a receiver reads it and then the rest of
    the batch: two reads however many packets there are and however their
    sizes vary.  The packets carry no tags, so the receiver decodes them in
    the order the sender added them, with :meth:`next`.  A packet with a
    value that takes the rest of the packet, and no packet length field,
    takes the rest of the batch, so it must come last.

    The capacity bounds the bytes after the length on both sides: adding a
    packet that would pass it raises ``TooLong``, and receiving a longer
    batch raises ``BadLength`` before the batch is read.
    """

    __slots__ = ("_buf", "_scratch", "_capacity", "_pos")

    def __init__(self, capacity: int = 64 * 1024) -> None:
        """An empty batch that holds up to ``capacity`` bytes of packets (at
        most 2**32 - 1), preallocated."""
        self._capacity = min(capacity, 0xFFFFFFFF)
        self._buf = ManagedBuffer(BATCH_LENGTH_BYTES + self._capacity)
        self._scratch = ManagedBuffer()
        self.clear()

    def clear(self) -> None:
        """Remove every packet, keeping the allocation."""
        self._buf.load(bytes(BATCH_LENGTH_BYTES))
        self._pos = BATCH_LENGTH_BYTES

    @property
    def capacity(self) -> int:
        """The most bytes of packets the batch holds."""
        return self._capacity

    def push(self, packet) -> None:
        """Encode ``packet`` after the packets already in the batch.  Raises
        ``TooLong`` if the batch would pass its capacity, leaving it as it
        was."""
        packet.encode(self._scratch)
        n = len(self) + len(self._scratch)
        if n > self._capacity:
            raise PacketError("TooLong", packet.NAME)
        with self._scratch.view() as v:
            self._buf.write(v)
        with self._buf.view() as v:
            v[:BATCH_LENGTH_BYTES] = n.to_bytes(BATCH_LENGTH_BYTES, "big")

    def __len__(self) -> int:
        """The number of bytes of packets, after the length."""
        return len(self._buf) - BATCH_LENGTH_BYTES

    def view(self) -> memoryview:
        """The whole batch, length first, as it is sent; release the view
        before changing the batch."""
        return self._buf.view()

    def getvalue(self) -> bytes:
        """A copy of the whole batch, length first."""
        return self._buf.getvalue()

    def write_to(self, stream) -> None:
        """Send the batch with one write."""
        with self._buf.view() as v:
            stream.write(v)

    def read_from(self, stream) -> None:
        """Replace the batch with one read from a binary stream: its
        length, then that many bytes.  Raises ``BadLength`` if the length
        is above the capacity, and ``EOFError`` if the batch ends early."""
        head = _read_exact(stream, BATCH_LENGTH_BYTES)
        n = self._check_length(head)
        try:
            self._buf.load(head)
            self._buf.resize(BATCH_LENGTH_BYTES + n)
            with self._buf.view() as v:
                _read_into(stream, v[BATCH_LENGTH_BYTES:])
        except BaseException:
            self.clear()
            raise
        self._pos = BATCH_LENGTH_BYTES

    def load(self, data) -> None:
        """Replace the batch with ``data``, a whole batch received at once
        (a datagram, say).  Raises ``BadLength`` unless its length is the
        number of bytes after it, and at most the capacity."""
        if len(data) < BATCH_LENGTH_BYTES:
            raise PacketError("BadLength", "batch")
        n = self._check_length(bytes(data[:BATCH_LENGTH_BYTES]))
        if n != len(data) - BATCH_LENGTH_BYTES:
            raise PacketError("BadLength", "batch")
        self._buf.load(data)
        self._pos = BATCH_LENGTH_BYTES

    def _check_length(self, head: bytes) -> int:
        n = int.from_bytes(head, "big")
        if n > self._capacity:
            raise PacketError("BadLength", "batch")
        return n

    def next(self, packet_class):
        """Decode the next packet, which must be a ``packet_class`` (a
        packet or kind)."""
        with self._buf.view() as v:
            p, used = packet_class.decode_prefix(v[self._pos:])
        self._pos += used
        return p

    @property
    def remaining(self) -> int:
        """The bytes of packets not yet decoded."""
        return len(self._buf) - self._pos

    def finish(self) -> None:
        """Check that every packet has been decoded: raises ``BadLength`` if
        bytes are left over."""
        if self.remaining:
            raise PacketError("BadLength", "batch")


def _read_exact(stream, n: int) -> bytes:
    out = bytearray(n)
    with memoryview(out) as v:
        _read_into(stream, v)
    return bytes(out)


def _read_into(stream, view: memoryview) -> None:
    """Fill ``view`` from ``stream``, raising EOFError if it ends first."""
    got = 0
    while got < len(view):
        if hasattr(stream, "readinto"):
            n = stream.readinto(view[got:])
        else:
            block = stream.read(len(view) - got)
            n = len(block)
            view[got:got + n] = block
        if not n:
            raise EOFError("a batch ends early")
        got += n


def _position(base: int, offset: int, field: str) -> int:
    p = base + offset
    if p < 0:
        raise PacketError("BadPosition", field)
    return p


class BitReader:
    """Reads big-endian bit fields from a bytes-like object."""

    __slots__ = ("data", "pos", "nbits", "end")

    def __init__(self, data) -> None:
        self.data = data
        self.pos = 0
        self.nbits = len(data) * 8
        self.end = 0  # the furthest bit read

    def position(self) -> int:
        return self.pos

    def used(self) -> int:
        """The bytes of the packet read so far, up to the furthest bit
        read."""
        return (self.end + 7) // 8

    def seek(self, start: int, offset: int, field: str) -> None:
        """Move to ``offset`` bits from ``start``.  Bits skipped over count
        as part of the packet, as the writer fills them."""
        self.pos = _position(start, offset, field)
        self.end = max(self.end, self.pos)

    def skip(self, offset: int, field: str) -> None:
        self.seek(self.pos, offset, field)

    def uint(self, bits: int, field: str) -> int:
        end = self.pos + bits
        if end > self.nbits:
            raise PacketError("Truncated", field)
        first, last = self.pos >> 3, (end + 7) >> 3
        v = int.from_bytes(self.data[first:last], "big") >> (last * 8 - end)
        self.pos = end
        self.end = max(self.end, end)
        return v & ((1 << bits) - 1)

    def uint_order(self, bits: int, le: bool, field: str) -> int:
        v = self.uint(bits, field)
        return (int.from_bytes(v.to_bytes(bits // 8, "big"), "little") if le
                else v)

    def uint_bits(self, bits: int, lsb: bool, order, field: str) -> int:
        """Read an unsigned field of ``bits`` bits in any bit and byte order.

        The field's bits are taken in groups of 8 (the last may be shorter).
        Within a group, ``lsb`` False numbers the bits of each byte from the
        most significant (the usual order), and the group's first bit is its
        most significant; ``lsb`` True numbers them from the least
        significant, and the group's first bit is its least significant.
        ``order`` combines the groups: ``"be"`` puts the first group most
        significant, ``"le"`` least significant, and a tuple gives each
        (whole) byte's significance, as an XTCE byte order list does."""
        if self.pos + bits > self.nbits:
            raise PacketError("Truncated", field)
        groups = []
        q = self.pos
        left = bits
        while left:
            n = min(8, left)
            g = 0
            for i in range(n):
                byte = self.data[(q + i) >> 3]
                if lsb:
                    g |= ((byte >> ((q + i) & 7)) & 1) << i
                else:
                    g = (g << 1) | ((byte >> (7 - ((q + i) & 7))) & 1)
            groups.append((g, n))
            q += n
            left -= n
        self.pos = q
        self.end = max(self.end, q)
        return _combine(groups, order)

    def packet_end(self) -> int:
        """The end of the packet in bits: the end of the last byte read."""
        return (self.end + 7) // 8 * 8

    def checksum(self, spec: tuple, start: int, end: int, field: str) -> int:
        """The checksum ``spec`` (see ``checksum``) of the bits from
        ``start`` to ``end`` of the packet."""
        return checksum(spec, self.data, start, end, field)

    def pad(self, bits: int, field: str) -> None:
        """Skip ``bits`` bits of padding, which must be in the packet."""
        if self.pos + bits > self.nbits:
            raise PacketError("Truncated", field)
        self.pos += bits
        self.end = max(self.end, self.pos)

    def limit(self, v: int, adjust: int, field: str) -> None:
        """End the packet where the packet length field ``field``, whose
        value ``v`` was just read, says: ``v - adjust`` bytes after the
        field.  Raises ``Truncated`` if the data is shorter."""
        after = v - adjust
        if after < 0:
            raise PacketError("BadLength", field)
        total = (self.pos + 7) // 8 + after
        if total * 8 > self.nbits:
            raise PacketError("Truncated", field)
        self.nbits = total * 8

    def finish(self, field: str) -> None:
        """Check that the packet's entries reach the end that the packet
        length field ``field`` gave."""
        if (self.end + 7) // 8 * 8 != self.nbits:
            raise PacketError("BadLength", field)

    def rest_bytes(self, max_: int, field: str) -> int:
        """The bytes from the current position to the end of the packet, for
        a field that takes the rest of it.  Raises ``BadSize`` unless they
        are whole bytes, at most ``max_`` of them."""
        bits = max(self.nbits - self.pos, 0)
        if bits % 8 or bits // 8 > max_:
            raise PacketError("BadSize", field)
        return bits // 8

    def bytes(self, n: int, field: str) -> bytes:
        if self.pos + n * 8 > self.nbits:
            raise PacketError("Truncated", field)
        if self.pos % 8 == 0:
            start = self.pos >> 3
            self.pos += n * 8
            self.end = max(self.end, self.pos)
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

    def uint_bits(self, bits: int, lsb: bool, order, v: int) -> None:
        """Write the low ``bits`` bits of ``v`` in the bit and byte order that
        ``BitReader.uint_bits`` reads."""
        lengths = [8] * (bits // 8) + ([bits % 8] if bits % 8 else [])
        last = (self.pos + bits + 7) >> 3
        if len(self.buf) < last:
            self.buf.resize(last)
        d = self.buf._data
        q = self.pos
        for j, (g, n) in enumerate(_split(v, lengths, order)):
            for i in range(n):
                bit = (g >> i) & 1 if lsb else (g >> (n - 1 - i)) & 1
                shift = (q + i) & 7 if lsb else 7 - ((q + i) & 7)
                k = (q + i) >> 3
                d[k] = (d[k] & ~(1 << shift)) | (bit << shift)
            q += n
        self.pos = q

    def patch_length(self, at: int, bits: int, le: bool, adjust: int,
                     field: str) -> None:
        """Fill in the packet length field ``field`` of ``bits`` bits at bit
        ``at``, once the whole packet is written: the number of bytes after
        the field plus ``adjust``."""
        v = len(self.buf) - (at + bits + 7) // 8 + adjust
        if not 0 <= v < 1 << bits:
            raise PacketError("OutOfRange", field)
        end = self.pos
        self.pos = at
        self.uint_order(bits, le, v)
        self.pos = end

    def zeros(self, bits: int) -> None:
        if bits:
            self.uint(bits, 0)

    def packet_end(self) -> int:
        """The end of the packet written so far, in bits."""
        return len(self.buf) * 8

    def patch_checksum(self, spec: tuple, start: int, end: int, at: int,
                       bits: int, le: bool, field: str) -> None:
        """Fill in the checksum field ``field`` of ``bits`` bits at bit
        ``at``: the checksum ``spec`` of the bits from ``start`` to ``end``,
        its low ``bits`` bits."""
        v = checksum(spec, self.buf._data, start, end, field)
        pos = self.pos
        self.pos = at
        self.uint_order(bits, le, v & ((1 << bits) - 1))
        self.pos = pos

    def bytes(self, data) -> None:
        if data:
            self.uint(len(data) * 8, int.from_bytes(data, "big"))


def segment_reader(raw: int, total: int) -> BitReader:
    """A reader of a value's ``total``-bit encoding, which its segments
    gathered into ``raw`` (the encoding's last bit least significant)."""
    return BitReader((raw << (64 - total)).to_bytes(8, "big"))


def segment_writer() -> BitWriter:
    """A writer for a value of at most 64 bits to split into segments."""
    return BitWriter(ManagedBuffer(8))


def segment_bits(w: BitWriter, total: int) -> int:
    """The ``total``-bit encoding that segment_writer ``w`` holds, as
    segment_reader takes it."""
    data = bytes(w.buf._data[:len(w.buf)]).ljust(8, b"\0")
    return int.from_bytes(data, "big") >> (64 - total)


def _combine(groups, order) -> int:
    """The value of groups of bits ((value, length) pairs, in stream order)
    combined in ``order`` (see ``BitReader.uint_bits``)."""
    v = 0
    if order == "be":
        for g, n in groups:
            v = (v << n) | g
    elif order == "le":
        shift = 0
        for g, n in groups:
            v |= g << shift
            shift += n
    else:
        for (g, _), sig in zip(groups, order):
            v |= g << (8 * sig)
    return v


def _split(v: int, lengths, order) -> list:
    """The groups (value, length) of ``v`` in stream order: the inverse of
    ``_combine``."""
    out = []
    if order == "be":
        shift = sum(lengths)
        for n in lengths:
            shift -= n
            out.append(((v >> shift) & ((1 << n) - 1), n))
    elif order == "le":
        shift = 0
        for n in lengths:
            out.append(((v >> shift) & ((1 << n) - 1), n))
            shift += n
    else:
        out = [((v >> (8 * sig)) & 0xFF, 8) for sig in order]
    return out


# -- error detection
# -------------------------------------------------------------------

_VERHOEFF_D = ("0123456789", "1234067895", "2340178956", "3401289567",
               "4012395678", "5987604321", "6598710432", "7659821043",
               "8765932104", "9876543210")
_VERHOEFF_P = ("0123456789", "1576283094", "5803796142", "8916043527",
               "9453126870", "4286573901", "2793806415", "7046913258")
_VERHOEFF_INV = "0432156789"
_DAMM = ("0317598642", "7092154863", "4206871359", "1750983426", "6123045978",
         "3674209581", "5869720134", "8945362017", "9438617205", "2581436790")


def _words(data, start: int, end: int, width: int) -> list:
    """The ``width``-bit words of the bits from ``start`` to ``end`` of
    ``data``, most significant bit first; a last partial word is padded
    with zero bits."""
    n = end - start
    if n <= 0:
        return []
    first, last = start >> 3, (end + 7) >> 3
    v = int.from_bytes(bytes(data[first:last]), "big") >> (last * 8 - end)
    v &= (1 << n) - 1
    pad = -n % width
    v <<= pad
    n += pad
    return [(v >> (n - width * (i + 1))) & ((1 << width) - 1)
            for i in range(n // width)]


def _reflect(v: int, bits: int) -> int:
    return int(format(v, f"0{bits}b")[::-1], 2) if bits else 0


def _digits(data, start: int, end: int, field: str) -> list:
    """Decimal digits, one in every 4 bits."""
    ds = _words(data, start, end, 4)
    if any(d > 9 for d in ds):
        raise PacketError("InvalidValue", field)
    return ds


def checksum(spec: tuple, data, start: int, end: int, field: str) -> int:
    """An XTCE ErrorDetectCorrect algorithm over the bits from ``start`` to
    ``end`` of ``data``.  ``spec`` is ``("crc", width, polynomial, init,
    final_xor, reflect_data, reflect_remainder)``, ``("sum", bits)``,
    ``("unix_sum",)``, ``("fletcher", bits)``, ``("adler32",)``,
    ``("luhn",)``, ``("verhoeff",)``, ``("damm",)``, ``("xor", bits)`` or
    ``("parity", odd)``; the manual describes each."""
    kind = spec[0]
    if kind == "crc":
        width, poly, init, final, ref_in, ref_out = spec[1:]
        top, mask = 1 << (width - 1), (1 << width) - 1
        reg = init
        n = end - start
        groups = _words(data, start, start + n - n % 8, 8)
        sizes = [8] * len(groups)
        if n % 8:
            groups.append(_words(data, end - n % 8, end, n % 8)[0])
            sizes.append(n % 8)
        for g, size in zip(groups, sizes):
            if ref_in:
                g = _reflect(g, size)
            for i in range(size - 1, -1, -1):
                bit = (g >> i) & 1
                carry = bool(reg & top) != bool(bit)
                reg = (reg << 1) & mask
                if carry:
                    reg ^= poly
        if ref_out:
            reg = _reflect(reg, width)
        return reg ^ final
    if kind == "sum":
        return sum(_words(data, start, end, 8)) & ((1 << spec[1]) - 1)
    if kind == "unix_sum":
        c = 0
        for b in _words(data, start, end, 8):
            c = ((c >> 1) + ((c & 1) << 15) + b) & 0xFFFF
        return c
    if kind == "fletcher":
        half = spec[1] // 2
        m = (1 << half) - 1
        a = b = 0
        for w in _words(data, start, end, half):
            a = (a + w) % m
            b = (b + a) % m
        return (b << half) | a
    if kind == "adler32":
        a, b = 1, 0
        for w in _words(data, start, end, 8):
            a = (a + w) % 65521
            b = (b + a) % 65521
        return (b << 16) | a
    if kind == "luhn":
        total = 0
        for i, d in enumerate(reversed(_digits(data, start, end, field))):
            if i % 2 == 0:
                d *= 2
                d -= 9 if d > 9 else 0
            total += d
        return (10 - total % 10) % 10
    if kind == "verhoeff":
        c = 0
        for i, d in enumerate(reversed(_digits(data, start, end, field))):
            c = int(_VERHOEFF_D[c][int(_VERHOEFF_P[(i + 1) % 8][d])])
        return int(_VERHOEFF_INV[c])
    if kind == "damm":
        c = 0
        for d in _digits(data, start, end, field):
            c = int(_DAMM[c][d])
        return c
    if kind == "xor":
        c = 0
        for w in _words(data, start, end, spec[1]):
            c ^= w
        return c
    if kind == "custom":  # the application's function, or None
        if spec[1] is None:
            raise PacketError("NoAlgorithm", field)
        return int(spec[1](bytes(data), start, end))
    ones = sum(bin(w).count("1") for w in _words(data, start, end, 8))
    return (ones + spec[1]) & 1  # parity: odd adds one


# -- integers
# -------------------------------------------------------------------


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


def bcd_decode(raw: int, bits: int, packed: bool, field: str) -> int:
    """The value of a BCD field: packed BCD has a decimal digit in every 4
    bits, and (unpacked) BCD one in the low 4 bits of every byte, whose high
    4 bits are zero; the first digit is the most significant.  Raises
    ``InvalidValue`` for any other bits."""
    width = 4 if packed else 8
    v = 0
    for shift in range(bits - width, -1, -width):
        d = (raw >> shift) & ((1 << width) - 1)
        if d > 9:
            raise PacketError("InvalidValue", field)
        v = v * 10 + d
    return v


def bcd_encode(v: int, bits: int, packed: bool, field: str) -> int:
    """The BCD field holding ``v``; raises ``OutOfRange`` unless ``v`` is
    from 0 to the most the field's digits hold."""
    v = int(v)
    width = 4 if packed else 8
    digits = bits // width
    if not 0 <= v < 10 ** digits:
        raise PacketError("OutOfRange", field)
    raw = 0
    for i in range(digits):
        raw |= (v % 10) << (i * width)
        v //= 10
    return raw


def _round(x: float) -> int:
    """Round half away from zero, like Rust's ``f64::round`` and C++'s
    ``std::round``."""
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
    if (abs(r) >= 1 << 64
            or (enc != "unsigned" and not -(1 << 63) <= r < 1 << 63)):
        raise PacketError("OutOfRange", field)
    return from_signed(r, bits, enc, field)


def round_to_int(y: float, lo: float, lim: float, field: str) -> int:
    """Round a calibrated value (half away from zero) to an integer from
    ``lo`` up to but not including ``lim``; raises ``OutOfRange`` otherwise."""
    if not math.isfinite(y):
        raise PacketError("OutOfRange", field)
    r = _round(y)
    if not lo <= r < lim:
        raise PacketError("OutOfRange", field)
    return r


# -- floats
# ---------------------------------------------------------------------


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


def _to_float(neg: bool, m: int, e: int) -> float:
    """``(-1)**neg * m * 2**e`` rounded to the nearest double, ties to
    even; infinite when it is too large."""
    try:
        x = float(Fraction(m) * Fraction(2) ** e) if m else 0.0
    except OverflowError:
        x = math.inf
    return -x if neg else x


def _parts(x: float, p: int) -> tuple:
    """(neg, m, e) with ``x == (-1)**neg * m * 2**e`` exactly, ``m`` of
    exactly ``p`` bits (``p`` at least 53), for finite nonzero ``x``."""
    m, e = math.frexp(abs(x))  # abs(x) == m * 2**e, 0.5 <= m < 1
    return x < 0, int(m * (1 << 53)) << (p - 53), e - p


def _round_shift(m: int, shift: int) -> int:
    """``m / 2**shift`` rounded to the nearest integer, ties to even."""
    if shift <= 0:
        return m << -shift
    q, r = divmod(m, 1 << shift)
    half = 1 << (shift - 1)
    return q + (1 if r > half or (r == half and q & 1) else 0)


# IEEE 754 binary formats: (exponent bits, fraction bits, explicit integer
# bit) by size.
_IEEE = {16: (5, 10, False), 32: (8, 23, False), 64: (11, 52, False),
         80: (15, 64, True), 128: (15, 112, False)}


def _ieee_decode(raw: int, bits: int) -> float:
    ebits, fbits, explicit = _IEEE[bits]
    neg = raw >> (bits - 1) & 1
    exp = (raw >> fbits) & ((1 << ebits) - 1)
    frac = raw & ((1 << fbits) - 1)
    bias = (1 << (ebits - 1)) - 1
    if exp == (1 << ebits) - 1:
        if explicit:
            frac &= (1 << 63) - 1
        x = math.nan if frac else math.inf
        return -x if neg and not frac else x
    if explicit:
        # The integer bit is in the significand; an exponent of 0 counts as
        # 1, as for subnormals.
        return _to_float(neg, frac, max(exp, 1) - bias - 63)
    if exp == 0:
        return _to_float(neg, frac, 1 - bias - fbits)
    return _to_float(neg, frac | (1 << fbits), exp - bias - fbits)


def _ieee_encode(x: float, bits: int) -> int:
    """The IEEE 754 binary format of ``bits`` bits nearest ``x``, ties to
    even, overflowing to infinity."""
    ebits, fbits, explicit = _IEEE[bits]
    sign = (1 << (bits - 1)) if math.copysign(1.0, x) < 0 else 0
    top = (1 << ebits) - 1
    integer = (1 << 63) if explicit else 0  # x87's explicit integer bit
    if math.isnan(x):
        # A quiet NaN: the fraction's top bit set.
        return sign | (top << fbits) | integer | (1 << (fbits - 1 - explicit))
    if math.isinf(x):
        return sign | (top << fbits) | integer
    if x == 0:
        return sign
    bias = (1 << (ebits - 1)) - 1
    p = fbits + (not explicit)  # significand bits, with the integer bit
    _, m, e = _parts(x, 53)
    # The exponent of the significand's lowest bit: subnormals share the
    # smallest normal exponent.
    low = max(e + 52, 1 - bias) - (p - 1)
    q = _round_shift(m, low - e)
    if q >> p:  # rounding carried into a new bit
        q >>= 1
        low += 1
    if not q >> (p - 1):  # subnormal
        return sign | q
    biased = low + p - 1 + bias
    if biased >= top:
        return sign | (top << fbits) | integer
    return sign | (biased << fbits) | (q if explicit else q - (1 << fbits))


def _magnitude(x: float) -> tuple:
    """(m, e, power) for finite nonzero ``x``: ``abs(x) == m * 2**e`` with
    ``m`` of 53 bits, and whether ``abs(x)`` is a power of two."""
    _, m, e = _parts(x, 53)
    return m, e, m == 1 << 52


def _mil1750a_decode(raw: int, bits: int) -> float:
    if bits == 32:
        mant, exp = raw >> 8, raw & 0xFF
        mbits = 24
    else:
        mant = ((raw >> 24) << 16) | (raw & 0xFFFF)
        exp = (raw >> 16) & 0xFF
        mbits = 40
    mant = to_signed(mant, mbits, "twosComplement")
    exp = to_signed(exp, 8, "twosComplement")
    return _to_float(mant < 0, abs(mant), exp - (mbits - 1))


def _mil1750a_encode(x: float, bits: int, field: str) -> int:
    mbits = 24 if bits == 32 else 40
    f = mbits - 1  # fraction bits
    if x == 0:
        return 0
    m, e, power = _magnitude(x)
    top = e + 52  # abs(x) is in [2**top, 2**(top + 1))
    if x > 0:
        exp = top + 1
        mag = _round_shift(m, exp - f - e)
        if mag == 1 << f:
            mag >>= 1
            exp += 1
        mant = mag
    else:
        # Negative mantissas are normalized to [-1, -1/2).
        exp = top if power else top + 1
        mag = _round_shift(m, exp - f - e)
        if mag == 1 << (f - 1):
            mag = 1 << f
            exp -= 1
        mant = -mag
    if exp > 127:
        raise PacketError("OutOfRange", field)
    if exp < -128:
        # Too small to normalize: unnormalized at the smallest exponent.
        exp = -128
        mag = _round_shift(m, exp - f - e)
        mant = mag if x > 0 else -mag
    mant &= (1 << mbits) - 1
    exp &= 0xFF
    if bits == 32:
        return (mant << 8) | exp
    return ((mant >> 16) << 24) | (exp << 16) | (mant & 0xFFFF)


def _dec_decode(raw: int, bits: int, field: str) -> float:
    f = 23 if bits == 32 else 55
    neg = raw >> (bits - 1)
    exp = (raw >> f) & 0xFF
    if exp == 0:
        if neg:  # a reserved operand
            raise PacketError("InvalidValue", field)
        return 0.0
    return _to_float(neg, (raw & ((1 << f) - 1)) | (1 << f), exp - 129 - f)


def _dec_encode(x: float, bits: int, field: str) -> int:
    f = 23 if bits == 32 else 55
    if x == 0:
        return 0
    m, e, _ = _magnitude(x)
    top = e + 52
    q = _round_shift(m, top - f - e)
    if q >> (f + 1):
        q >>= 1
        top += 1
    exp = top + 129
    if exp > 255:
        raise PacketError("OutOfRange", field)
    if exp < 1:
        return 0
    return ((x < 0) << (bits - 1)) | (exp << f) | (q - (1 << f))


def _ibm_decode(raw: int, bits: int) -> float:
    neg = raw >> (bits - 1)
    hi = raw >> (bits - 64) if bits == 128 else raw
    exp = (hi >> (min(bits, 64) - 8)) & 0x7F
    if bits == 128:
        frac = ((hi & ((1 << 56) - 1)) << 56) | (raw & ((1 << 56) - 1))
        f = 112
    else:
        f = bits - 8
        frac = raw & ((1 << f) - 1)
    return _to_float(neg, frac, 4 * (exp - 64) - f)


def _ibm_encode(x: float, bits: int, field: str) -> int:
    f = 112 if bits == 128 else bits - 8
    if x == 0:
        return 0
    m, e, _ = _magnitude(x)
    h = (e + 52) // 4 + 1  # abs(x) / 16**h is in [1/16, 1)
    q = _round_shift(m, 4 * h - f - e)
    if q >> f:
        q >>= 4
        h += 1
    exp = h + 64
    if exp > 127:
        raise PacketError("OutOfRange", field)
    if exp < 0:
        # Too small to normalize: unnormalized at the smallest exponent.
        exp = 0
        q = _round_shift(m, -256 - f - e)
    sign = int(x < 0)
    if bits != 128:
        return (sign << (bits - 1)) | (exp << f) | q
    # The low half has the sign, and a characteristic 14 less (modulo 128).
    hi = (sign << 63) | (exp << 56) | (q >> 56)
    lo = (sign << 63) | (((exp - 14) & 0x7F) << 56) | (q & ((1 << 56) - 1))
    return (hi << 64) | lo


# TI TMS320C3x formats: (exponent bits, fraction bits) by size.
_TI = {16: (4, 11), 32: (8, 23), 40: (8, 31)}


def _ti_decode(raw: int, bits: int) -> float:
    ebits, f = _TI[bits]
    exp = to_signed(raw >> (f + 1), ebits, "twosComplement")
    if exp == -(1 << (ebits - 1)):
        return 0.0
    frac = raw & ((1 << f) - 1)
    if raw >> f & 1:  # (-2 + frac) * 2**exp
        return _to_float(True, (2 << f) - frac, exp - f)
    return _to_float(False, (1 << f) | frac, exp - f)


def _ti_encode(x: float, bits: int, field: str) -> int:
    ebits, f = _TI[bits]
    lo = -(1 << (ebits - 1))  # the exponent of zero
    if x == 0:
        return (lo & ((1 << ebits) - 1)) << (f + 1)
    m, e, power = _magnitude(x)
    top = e + 52
    if x > 0:
        exp = top
        q = _round_shift(m, exp - f - e)
        if q >> (f + 1):
            q >>= 1
            exp += 1
        sign, frac = 0, q - (1 << f)
    else:
        # abs(x) / 2**exp is in (1, 2]: x == (-2 + frac) * 2**exp.
        exp = top - 1 if power else top
        q = _round_shift(m, exp - f - e)
        if q == 1 << f:
            exp -= 1
            q = 2 << f
        sign, frac = 1, (2 << f) - q
    if exp > -lo - 1:
        raise PacketError("OutOfRange", field)
    if exp <= lo:
        return (lo & ((1 << ebits) - 1)) << (f + 1)
    return ((exp & ((1 << ebits) - 1)) << (f + 1)) | (sign << f) | frac


def float_decode(fmt: str, bits: int, raw: int, field: str) -> float:
    """The value of a float field of ``bits`` bits in the XTCE encoding
    ``fmt``: ``IEEE754`` (16, 32, 64, 80 or 128 bits), ``MILSTD_1750A`` (32
    or 48), ``DEC`` (32 for F_floating, 64 for D_floating), ``IBM`` (32, 64
    or 128) or ``TI`` (16, 32 or 40), rounded to the nearest double."""
    if fmt == "IEEE754":
        return _ieee_decode(raw, bits)
    if fmt == "MILSTD_1750A":
        return _mil1750a_decode(raw, bits)
    if fmt == "DEC":
        return _dec_decode(raw, bits, field)
    if fmt == "IBM":
        return _ibm_decode(raw, bits)
    return _ti_decode(raw, bits)


def float_encode(fmt: str, bits: int, x: float, field: str) -> int:
    """The float field nearest ``x`` (ties to even), as ``float_decode``
    reads it.  IEEE formats overflow to infinity; the others, which have no
    infinity or NaN, raise ``OutOfRange`` for values too large for them and
    for infinities and NaN, and encode values too small for them as zero."""
    x = float(x)
    if fmt == "IEEE754":
        return _ieee_encode(x, bits)
    if not math.isfinite(x):
        raise PacketError("OutOfRange", field)
    if fmt == "MILSTD_1750A":
        return _mil1750a_encode(x, bits, field)
    if fmt == "DEC":
        return _dec_encode(x, bits, field)
    if fmt == "IBM":
        return _ibm_encode(x, bits, field)
    return _ti_encode(x, bits, field)


# -- calibrators
# -------------------------------------------------------------------


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


def spline(x: float, points, order: int, extrapolate: bool,
           field: str) -> float:
    first, last = points[0], points[-1]
    if x < first[0] or x > last[0]:
        if not extrapolate:
            raise PacketError("Calibration", field)
        if order == 0:
            return first[1] if x < first[0] else last[1]
        return (_lerp(x, first, points[1]) if x < first[0]
                else _lerp(x, points[-2], last))
    for i in range(len(points) - 1):
        if x < points[i + 1][0]:
            return (points[i][1] if order == 0
                    else _lerp(x, points[i], points[i + 1]))
    return last[1]


def spline_orders(x: float, points, orders, extrapolate: bool,
                  field: str) -> float:
    """A spline whose segments have their own orders: the segment from point
    ``i`` has order ``orders[i]``, and one of order k interpolates through
    k + 1 points, starting at point ``i`` or as close after it as the
    points allow.  Outside the points, the first or last segment
    extrapolates."""
    n = len(points)
    if (x < points[0][0] or x > points[-1][0]) and not extrapolate:
        raise PacketError("Calibration", field)
    i = 0
    while i < n - 2 and x >= points[i + 1][0]:
        i += 1
    k = orders[i]
    if k == 0:
        if x >= points[-1][0]:
            return points[-1][1]
        return points[i][1]
    k = min(k, n - 1)
    j = min(i, n - 1 - k)
    y = 0.0
    for m in range(j, j + k + 1):
        t = points[m][1]
        for q in range(j, j + k + 1):
            if q != m:
                t *= (x - points[q][0]) / (points[m][0] - points[q][0])
        y += t
    return y


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


def _int(x: float):
    """``x`` truncated to a 64-bit integer, or None when it has no such
    value."""
    if not math.isfinite(x) or not -2.0**63 <= x < 2.0**63:
        return None
    return int(x)


def _wrap(v: int) -> float:
    """A 64-bit two's complement integer, as a float."""
    v &= (1 << 64) - 1
    return float(v - (1 << 64) if v >> 63 else v)


def _call(f, *args) -> float:
    try:
        return f(*args)
    except (ValueError, OverflowError, ZeroDivisionError):
        return _libm_result(f, *args)


def _libm_result(f, *args) -> float:
    """What C's libm gives where Python's math raises: NaN for domain
    errors, infinity for overflow and poles."""
    if f is math.log or f is math.log10:
        return -math.inf if args[0] == 0 else math.nan
    if f is math.exp or f in (math.cosh, math.sinh):
        return math.copysign(math.inf, args[0]) if f is math.sinh else math.inf
    if f is math.pow:
        a, b = args
        if a == 0 and b < 0:
            odd = b == int(b) and int(b) % 2 == 1
            return math.copysign(math.inf, a) if odd else math.inf
        if math.isfinite(a) and math.isfinite(b) and a < 0 and b != int(b):
            return math.nan
        big = (math.copysign(math.inf, a)
               if (a < 0 and b == int(b) and int(b) % 2) else math.inf)
        return big
    if f is math.atanh:
        return (math.copysign(math.inf, args[0]) if abs(args[0]) == 1
                else math.nan)
    return math.nan


def math_op(op: str, a: float, b: float = 0.0) -> float:
    """The value of a MathOperationCalibrator operator on the operands ``a``
    and ``b`` (``b`` is ignored by unary operators); identical in every
    runtime.  See the manual for each operator."""
    if op == "/":
        if b != 0:
            return a / b
        if a == 0 or math.isnan(a) or math.isnan(b):
            return math.nan
        return math.copysign(math.inf, a) * math.copysign(1.0, b)
    if op == "%":
        m = math.fmod(a, b) if b != 0 and math.isfinite(a) else math.nan
        if m != 0 and (m < 0) != (b < 0):
            m += b
        return m
    if op == "div":
        if b == 0 or not math.isfinite(a) or not math.isfinite(b):
            return math.nan
        r = math.fmod(a, b)
        if r < 0:
            r += abs(b)
        q = (a - r) / b
        if not math.isfinite(q):
            return q
        return math.copysign(float(_round(q)), q)  # keeps -0.0
    if op == "^":
        return _call(math.pow, a, b)
    if op == "y^x":
        return _call(math.pow, b, a)
    unary = {"ln": math.log, "log": math.log10, "e^x": math.exp,
             "tan": math.tan, "cos": math.cos, "sin": math.sin,
             "atan": math.atan, "acos": math.acos, "asin": math.asin,
             "tanh": math.tanh, "cosh": math.cosh, "sinh": math.sinh,
             "atanh": math.atanh, "acosh": math.acosh, "asinh": math.asinh}
    if op in unary:
        if math.isnan(a):
            return math.nan
        if math.isinf(a) and op in ("tan", "cos", "sin"):
            return math.nan
        return _call(unary[op], a)
    if op == "1/x":
        return math.copysign(math.inf, a) if a == 0 else 1.0 / a
    if op == "x!":
        if a == math.inf:
            return math.inf
        if not a >= 0 or a != math.floor(a):
            return math.nan
        if a > 170:
            return math.inf
        r = 1.0
        for i in range(2, int(a) + 1):
            r *= i
        return r
    if op == "atan2":
        return math.atan2(b, a)
    if op == "abs":
        return abs(a)
    if op == "int":
        if not math.isfinite(a):
            return a
        return math.copysign(float(math.trunc(a)), a)  # keeps -0.0
    if op == "!":
        return 1.0 if a == 0 else 0.0
    if op == "&&":
        return 1.0 if a != 0 and b != 0 else 0.0
    if op == "||":
        return 1.0 if a != 0 or b != 0 else 0.0
    if op in (">", ">=", "<", "<=", "==", "!="):
        return 1.0 if {">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b,
                       "==": a == b, "!=": a != b}[op] else 0.0
    if op == "min":
        return b if b < a else a
    if op == "max":
        return b if b > a else a
    i = _int(a)
    if op == "~":
        return math.nan if i is None else _wrap(~i)
    j = _int(b)
    if i is None or j is None:
        return math.nan
    if op in ("<<", ">>"):
        if not 0 <= j < 64:
            return math.nan
        return _wrap(i << j) if op == "<<" else _wrap(i >> j)
    return _wrap({"&": i & j, "|": i | j, "xor": i ^ j}[op])


def not_invertible(field: str):
    """Raises ``NotInvertible`` for ``field``: in generated expressions."""
    raise PacketError("NotInvertible", field)


# -- sizes from other fields
# ----------------------------------------------------------


def dynamic_size(x, slope: float, intercept: float, unit: int, max_: int,
                 field: str) -> int:
    """A size or count that another field gives (an XTCE ``DynamicValue``):
    ``(slope * x + intercept) / unit``, where ``x`` is that field's value and
    ``unit`` is 8 for a size in bits used in whole bytes.  Raises ``BadSize``
    unless the result is a whole number from 0 to ``max_``."""
    y = slope * float(x) + intercept
    if (not (y >= 0.0) or not y.is_integer() or not (y / unit).is_integer()
            or y / unit > max_):
        raise PacketError("BadSize", field)
    return int(y / unit)


def size_source(n: int, slope: float, intercept: float, unit: int, factor: int,
                field: str) -> int:
    """The value of the field that gives a size or count of ``n`` (the
    inverse of ``dynamic_size`` followed by multiplying by ``factor``), for
    encoding a field computed from the length of the value it sizes.  Raises
    ``BadSize``, naming ``field`` (the sized value), when no whole number
    gives exactly ``n``."""
    if n % factor:
        raise PacketError("BadSize", field)
    y = float(n // factor) * unit
    v = (y - intercept) / slope if slope else math.nan
    if not abs(v) < 9.2e18:
        raise PacketError("BadSize", field)
    x = math.floor(v + 0.5)
    # Accept only a value that decoding maps back to exactly ``n``.
    if slope * x + intercept != y:
        raise PacketError("BadSize", field)
    return x


def read_list(r: "BitReader", n: int, gap: int, field: str, read) -> list:
    """``n`` values read by ``read()``, with ``gap`` bits between them."""
    out = []
    for i in range(n):
        if i and gap:
            r.skip(gap, field)
        out.append(read())
    return out


def element(v, i: int, field: str):
    """Element ``i`` of ``v``, an array whose count another field gives;
    raises ``BadSize``, naming ``field``, if ``v`` has no element ``i``."""
    if i >= len(v):
        raise PacketError("BadSize", field)
    return v[i]


def check_size(v, n: int, field: str) -> None:
    """Raises ``BadSize`` unless ``v`` has ``n`` elements or bytes."""
    if len(v) != n:
        raise PacketError("BadSize", field)


def sized(spec: tuple, n: int) -> tuple:
    """The spec of a string whose size another field gives: exactly ``n`` bytes.
    """
    return (n * 8, n * 8) + tuple(spec[2:])


# -- strings and binary
# -------------------------------------------------------------

# Python codecs of the XTCE string encodings.  UTF-16 and UTF-32 without a
# byte order are read big endian unless they start with a byte order mark,
# and written big endian without one.
_CODECS = {"UTF-8": "utf-8", "US-ASCII": "ascii", "ISO-8859-1": "latin-1",
           "Windows-1252": "cp1252", "UTF-16": "utf-16-be",
           "UTF-16BE": "utf-16-be", "UTF-16LE": "utf-16-le",
           "UTF-32": "utf-32-be", "UTF-32BE": "utf-32-be",
           "UTF-32LE": "utf-32-le"}
_BOMS = {"UTF-16": ((b"\xfe\xff", "utf-16-be"), (b"\xff\xfe", "utf-16-le")),
         "UTF-32": ((b"\x00\x00\xfe\xff", "utf-32-be"),
                    (b"\xff\xfe\x00\x00", "utf-32-le"))}


def code_unit(charset: str) -> int:
    """The bytes in one code unit of the XTCE string encoding ``charset``."""
    return 2 if "16" in charset else 4 if "32" in charset else 1


def decode_text(b: bytes, charset: str, field: str) -> str:
    """The string whose bytes in ``charset`` are ``b``; raises
    ``InvalidString`` if they aren't valid."""
    codec = _CODECS[charset]
    for bom, c in _BOMS.get(charset, ()):
        if b.startswith(bom):
            b, codec = b[len(bom):], c
            break
    if len(b) % code_unit(charset):
        raise PacketError("InvalidString", field)
    try:
        return bytes(b).decode(codec)
    except UnicodeDecodeError:
        raise PacketError("InvalidString", field) from None


def encode_text(s: str, charset: str, field: str) -> bytes:
    """The bytes of ``s`` in ``charset``; raises ``InvalidString`` if it
    has characters that ``charset`` can't hold."""
    try:
        return s.encode(_CODECS[charset])
    except UnicodeEncodeError:
        raise PacketError("InvalidString", field) from None


def _find_unit(b: bytes, term: bytes, unit: int) -> int:
    """The first position of ``term`` in ``b`` that starts a code unit, or
    -1."""
    i = b.find(term)
    while i >= 0 and i % unit:
        i = b.find(term, i + 1)
    return i


def read_string(r: BitReader, spec: tuple, field: str) -> str:
    """Read a string; ``spec`` is (fixed_bits, max_bits, terminator,
    size_tag_bits, charset), where the terminator is bytes or None."""
    fixed, max_bits, term, tag, charset = spec
    unit = code_unit(charset)
    start = r.pos
    if fixed is not None and term is not None:
        b = r.bytes(fixed // 8, field)
        i = _find_unit(b, term, unit)
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
            if (len(out) + unit) * 8 > max_bits:
                raise PacketError("TooLong", field)
            out += r.bytes(unit, field)
            k = len(out) - len(term)
            if k >= 0 and k % unit == 0 and out[k:] == term:
                del out[k:]
                break
        b = bytes(out)
    else:
        n = r.uint(tag, field)
        if n * 8 + tag > max_bits:
            raise PacketError("TooLong", field)
        b = r.bytes(n, field)
    return decode_text(b, charset, field)


def write_string(w: BitWriter, spec: tuple, s: str, field: str) -> None:
    fixed, max_bits, term, tag, charset = spec
    b = encode_text(s, charset, field)
    if term is not None:
        if _find_unit(b, term, code_unit(charset)) >= 0:
            raise PacketError("InvalidString", field)
        room = (fixed if fixed is not None else max_bits) // 8
        # A fixed-size string may fill its field; otherwise the terminator
        # follows it.
        need = len(b) + len(term)
        if fixed is not None and len(b) == room:
            need = room
        if need > room:
            raise PacketError("TooLong", field)
        w.bytes(b)
        if len(b) < room:
            w.bytes(term)
        if fixed is not None and need < room:
            w.bytes(bytes(room - need))
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


def string_from_bytes(b: bytes, field: str) -> str:
    """The string a string field encoded as binary holds: all of its bytes,
    which must be UTF-8."""
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        raise PacketError("InvalidString", field) from None


def bool_from_string(s: str, one: str, zero: str, field: str) -> bool:
    """The value of a boolean encoded as one of two strings; raises
    ``OutOfRange`` for any other string."""
    if s == one:
        return True
    if s == zero:
        return False
    raise PacketError("OutOfRange", field)


def write_rest(w: BitWriter, b: bytes, max_: int, field: str) -> None:
    """Write a binary field that takes the rest of the packet: all of ``b``,
    which must be at most ``max_`` bytes."""
    if len(b) > max_:
        raise PacketError("TooLong", field)
    w.bytes(b)


def enum_value(cls, v: int):
    """``cls(v)`` for an IntEnum, keeping values the enumeration does not list.
    """
    try:
        return cls(v)
    except ValueError:
        m = int.__new__(cls, v)
        m._name_ = None
        m._value_ = v
        return m


# -- canonical text (identical in every runtime)
# ---------------------------------------


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


class AlarmLevel(enum.IntEnum):
    """XTCE's concern levels, least to most."""

    NORMAL = 0
    WATCH = 1
    WARNING = 2
    DISTRESS = 3
    CRITICAL = 4
    SEVERE = 5

    def __str__(self) -> str:
        return self.name.lower()


def in_band(x, lo, lo_incl: bool, hi, hi_incl: bool) -> bool:
    """Whether ``x`` lies in an alarm range; a bound of None is infinite."""
    return ((lo is None or (x >= lo if lo_incl else x > lo))
            and (hi is None or (x <= hi if hi_incl else x < hi)))


def enum_alarm(x: int, labels: tuple, default: int) -> int:
    """The level of enumeration value ``x``: that of the first (first
    value, last value, level) in ``labels`` holding it, else ``default``."""
    for lo, hi, level in labels:
        if lo <= x <= hi:
            return level
    return default


def dfa_match(dfa: tuple, data: bytes) -> bool:
    """Whether the automaton ``(starts, trans, accept)`` (see the generator's
    ``model.Dfa``) accepts ``data``."""
    starts, trans, accept = dfa
    q = 0
    for b in data:
        for i in range(starts[q], starts[q + 1]):
            lo, hi, nxt = trans[i]
            if lo <= b <= hi:
                q = nxt
                break
        else:
            return False
    return accept[q]


def pattern_alarm(s: str, patterns: tuple, default: int) -> int:
    """The level of string ``s``: that of the first (automaton, level) in
    ``patterns`` it matches, else ``default``."""
    data = s.encode("utf-8")
    for dfa, level in patterns:
        if dfa_match(dfa, data):
            return level
    return default


class AlarmState:
    """What an alarm monitor keeps for one parameter: whether it is in
    alarm, its runs of samples in and out of alarm, and its last
    ``history`` values and their times, for change ranges."""

    __slots__ = ("violations", "conformances", "level", "values", "times",
                 "history")

    def __init__(self, history: int = 0) -> None:
        self.violations = 0
        self.conformances = 0
        self.level = 0
        self.values: list = []
        self.times: list = []
        self.history = history

    def update(self, level: int, min_violations: int,
               min_conformance: int) -> int:
        """The level to report for a sample whose own level is ``level``:
        an alarm is raised after ``min_violations`` samples in a row are in
        alarm, and cleared after ``min_conformance`` in a row are not; in
        between, the last level raised stands."""
        if level > 0:
            self.violations += 1
            self.conformances = 0
            if self.level > 0 or self.violations >= min_violations:
                self.level = level
        else:
            self.conformances += 1
            self.violations = 0
            if self.level > 0 and self.conformances >= min_conformance:
                self.level = 0
        return self.level

    def change(self, x: float, t: float, k: int, per_second: bool,
               percent: bool) -> Optional[float]:
        """How much ``x`` (at time ``t``) changed since the value ``k``
        samples before it: as a percentage of that value when ``percent``,
        then per second when ``per_second``.  None when there is no such
        sample, or no change can be worked out (no time between them, or a
        percentage of 0)."""
        if len(self.values) < k:
            return None
        then, t_then = self.values[-k], self.times[-k]
        d = x - then
        if percent:
            if then == 0:
                return None
            d = d / abs(then) * 100.0
        if per_second:
            dt = t - t_then
            if not dt > 0:
                return None
            d = d / dt
        return d

    def push(self, x: float, t: float) -> None:
        """Remembers a sample, for change ranges."""
        self.values.append(x)
        self.times.append(t)
        if len(self.values) > self.history:
            del self.values[0]
            del self.times[0]


def _line_decoder(pcm: str, inverted: bool):
    """A function turning the bits of a PCM stream (``pcm`` NRZL, NRZM or
    NRZS, inverted when ``inverted``) into data bits."""
    prev = [0]

    def decode(raw: int) -> int:
        level = raw ^ inverted
        if pcm == "NRZM":  # a one is a change of level
            bit = level ^ prev[0]
        elif pcm == "NRZS":  # a zero is a change of level
            bit = 1 ^ level ^ prev[0]
        else:
            bit = level
        prev[0] = level
        return bit
    return decode


def _pack(bits) -> bytes:
    """Bits, most significant first, as bytes padded with zeros."""
    out = bytearray((len(bits) + 7) // 8)
    for i, b in enumerate(bits):
        out[i >> 3] |= b << (7 - (i & 7))
    return bytes(out)


class FrameSync:
    """Finds the frames of a FixedFrameStream in a stream of bytes, by the
    state machine XTCE describes: in Search, it looks for the sync pattern
    at every bit; in Verify, it needs ``verify`` frames in a row whose
    patterns are where the frame length (give or take ``aperture`` bits)
    says, then it locks; in Lock, it gives each frame whose pattern it
    found, and a missed pattern sends it to Check, where ``check`` good
    patterns in a row lock it again and a missed one sends it back to
    Search.  Searching ``invert_after`` bits (when not 0) without finding a
    pattern inverts the bits.  See the generator's ``model.Stream``."""

    SEARCH, VERIFY, LOCK, CHECK = range(4)

    def __init__(self, frame_bits: int, pattern: int, mask: int,
                 pattern_bits: int, location: int, max_errors: int,
                 aperture: int, verify: int, check: int, invert_after: int,
                 pcm: str = "NRZL", inverted: bool = False) -> None:
        self.frame_bits = frame_bits
        self.pat = [(pattern >> (pattern_bits - 1 - j)) & 1
                    for j in range(pattern_bits)]
        self.mask = [(mask >> (pattern_bits - 1 - j)) & 1
                     for j in range(pattern_bits)]
        self.location = location
        self.max_errors = max_errors
        self.aperture = aperture
        self.verify = verify
        self.check = check
        self.invert_after = invert_after
        self.decode = _line_decoder(pcm, inverted)
        self.bits = bytearray()
        self.base = 0  # the stream position of bits[0]
        self.state = self.SEARCH
        self.pos = 0  # where Search looks next
        self.first = 0  # the pattern that started Verify
        self.cur = 0  # the start of the current frame
        self.cur_good = False  # whether its pattern was found
        self.last = 0  # the last pattern found
        self.good = 0
        self.searched = 0
        self.flip = 0

    def push(self, data) -> list:
        """The frames that ``data``, the next bytes of the stream, completes,
        each as bytes (its bits padded with zeros)."""
        frames: list = []
        for byte in data:
            for k in range(7, -1, -1):
                self.bits.append(self.decode((byte >> k) & 1))
            self._run(frames)
        return frames

    def _bit(self, i: int) -> int:
        return self.bits[i - self.base] ^ self.flip

    def _matches(self, s: int) -> bool:
        errors = 0
        at = s + self.location
        for j, (p, m) in enumerate(zip(self.pat, self.mask)):
            if m and self._bit(at + j) != p:
                errors += 1
                if errors > self.max_errors:
                    return False
        return True

    def _frame(self, s: int) -> bytes:
        return _pack([self._bit(s + i) for i in range(self.frame_bits)])

    def _run(self, frames: list) -> None:
        span = self.location + len(self.pat)
        while True:
            end = self.base + len(self.bits)
            if self.state == self.SEARCH:
                if self.pos + span > end:
                    break
                if self._matches(self.pos):
                    self.first = self.last = self.cur = self.pos
                    self.cur_good = True
                    self.good = 1
                    self.searched = 0
                    self.state = (self.LOCK if self.verify <= 1
                                  else self.VERIFY)
                else:
                    self.pos += 1
                    self.searched += 1
                    if self.invert_after and self.searched >= self.invert_after:
                        self.flip ^= 1
                        self.searched = 0
                continue
            nxt = self.cur + self.frame_bits
            if nxt + self.aperture + span > end:
                break
            found = None
            for d in range(self.aperture + 1):
                for e in ((0,) if d == 0 else (-d, d)):
                    if found is None and self._matches(nxt + e):
                        found = nxt + e
            if found is not None:
                if self.state != self.LOCK:
                    self.good += 1
                    if (self.good >= (self.verify if self.state == self.VERIFY
                                      else self.check)):
                        self.state = self.LOCK
                if self.state == self.LOCK and self.cur_good:
                    frames.append(self._frame(self.cur))
                self.cur = self.last = found
                self.cur_good = True
            elif self.state == self.LOCK:
                if self.cur_good:
                    frames.append(self._frame(self.cur))
                self.state = self.CHECK
                self.good = 0
                self.cur = nxt
                self.cur_good = False
            else:
                self.pos = ((self.first if self.state == self.VERIFY
                             else self.last) + 1)
                self.state = self.SEARCH
                self.searched = 0
        keep = (self.pos if self.state == self.SEARCH else self.first
                if self.state == self.VERIFY else self.last)
        del self.bits[:keep - self.base]
        self.base = keep


class FlagSync:
    """Finds the frames of a VariableFrameStream in a stream of bytes:
    frames lie between flags, each ``flag_bits`` ones (or zeros, unless
    ``flag_ones``) with a bit of the other kind on either side, and a bit
    of the other kind is stuffed into a frame after ``flag_bits - 1`` in a
    row, which this removes.  More than ``flag_bits`` in a row abort the
    frame, as does a frame of more than ``max_bits``; frames are found
    again from the next flag.  See the generator's ``model.Stream``."""

    def __init__(self, flag_bits: int, flag_ones: bool, max_bits: int,
                 pcm: str = "NRZL", inverted: bool = False) -> None:
        self.flag_bits = flag_bits
        self.flag = int(flag_ones)
        self.max_bits = max_bits
        self.decode = _line_decoder(pcm, inverted)
        self.frame: list = []
        self.run = 0
        self.hunting = True

    def push(self, data) -> list:
        """The frames that ``data``, the next bytes of the stream, completes,
        each as bytes (its bits padded with zeros)."""
        frames: list = []
        for byte in data:
            for k in range(7, -1, -1):
                self._bit(self.decode((byte >> k) & 1), frames)
        return frames

    def _bit(self, b: int, frames: list) -> None:
        f = self.flag_bits
        if b == self.flag:
            self.run += 1
            if self.run > f:
                self.hunting = True
            if not self.hunting:
                self.frame.append(b)
                if len(self.frame) > self.max_bits + f + 1:
                    self.hunting = True
            if self.hunting:
                self.frame = []
            return
        if self.run == f:  # a flag
            if not self.hunting:
                body = self.frame[:-f]
                if body and body[-1] != self.flag:
                    body.pop()  # the flag's leading bit
                if body:
                    frames.append(_pack(body))
            self.hunting = False
            self.frame = []
        elif self.run == f - 1 and not self.hunting:
            pass  # a stuffed bit
        elif not self.hunting:
            self.frame.append(b)
            if len(self.frame) > self.max_bits + f + 1:
                self.hunting = True
                self.frame = []
        self.run = 0


def need_hooks(hooks: dict, names: tuple) -> None:
    """Raises NoAlgorithm unless ``hooks`` has a function for each of
    ``names``."""
    for name in names:
        if name not in hooks:
            raise PacketError("NoAlgorithm", name)


def criterion_hook(hooks: dict, name: str, inputs: tuple) -> bool:
    """Whether the CustomAlgorithm criterion ``name`` holds for ``inputs``;
    False without a function for it."""
    f = hooks.get(name)
    return f is not None and bool(f(inputs))


def algorithm_hook(hooks: dict, name: str, inputs: tuple, outputs: int,
                   index: int) -> Optional[float]:
    """Output ``index`` of the CustomAlgorithm ``name``, whose function
    returns its ``outputs`` outputs for ``inputs``, or None; None without a
    function for it."""
    f = hooks.get(name)
    out = f(inputs) if f is not None else None
    return None if out is None else float(out[index])


def alarm_hook(hooks: dict, name: str, inputs: tuple) -> int:
    """The level, 0 to 5, that the CustomAlarm ``name`` gives ``inputs``; 0
    without a function for it."""
    f = hooks.get(name)
    return 0 if f is None else min(max(int(f(inputs)), 0), 5)


def describe_alarms(alarms) -> str:
    """``field=level`` for each (field, level), ``none`` when the field is
    absent, as every runtime writes it."""
    return ", ".join(f"{name}={'none' if level is None else level}"
                     for name, level in alarms)


def describe_outputs(outputs) -> str:
    """``name=value`` for each MathAlgorithm output, ``none`` when it is
    absent, as every runtime writes it."""
    return ", ".join(f"{name}={'none' if v is None else describe_float(v)}"
                     for name, v in outputs)


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
