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
        """Makes an empty batch, preallocated.

        Args:
            capacity: The most bytes of packets it holds; at most 2**32 - 1.
        """
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
        """Encodes ``packet`` after the packets already in the batch.

        Args:
            packet: The packet.

        Raises:
            PacketError: ``TooLong`` if the batch would pass its capacity,
                         leaving it as it was, or why the packet can't be
                         encoded.
        """
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
        """Replaces the batch with one read from a binary stream.

        Reads its length, then that many bytes.  On an error the batch is
        left empty.

        Args:
            stream: A binary stream; its ``readinto`` is used if it has one,
                    else its ``read``.

        Raises:
            PacketError: ``BadLength`` if the length is above the capacity.
            EOFError:    The stream ends before the batch does.
        """
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
        """Replaces the batch with ``data``, a whole batch received at once.

        Args:
            data: The batch, length first, such as a datagram.

        Raises:
            PacketError: ``BadLength`` unless its length is the number of
                         bytes after it, and at most the capacity.
        """
        if len(data) < BATCH_LENGTH_BYTES:
            raise PacketError("BadLength", "batch")
        n = self._check_length(bytes(data[:BATCH_LENGTH_BYTES]))
        if n != len(data) - BATCH_LENGTH_BYTES:
            raise PacketError("BadLength", "batch")
        self._buf.load(data)
        self._pos = BATCH_LENGTH_BYTES

    def _check_length(self, head: bytes) -> int:
        """Reads a batch's length and checks it against the capacity.

        Args:
            head: The length's bytes, big endian.

        Returns:
            The length: the number of bytes of packets.

        Raises:
            PacketError: ``BadLength`` if the length is above the capacity.
        """
        n = int.from_bytes(head, "big")
        if n > self._capacity:
            raise PacketError("BadLength", "batch")
        return n

    def next(self, packet_class):
        """Decodes the next packet in the batch.

        Args:
            packet_class: The packet class, or kind, it must be.

        Returns:
            The packet.

        Raises:
            PacketError: Why it can't be decoded.
        """
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
    """Reads exactly ``n`` bytes from a binary stream.

    Args:
        stream: The stream.
        n:      How many bytes to read.

    Returns:
        The bytes.

    Raises:
        EOFError: The stream ends first.
    """
    out = bytearray(n)
    with memoryview(out) as v:
        _read_into(stream, v)
    return bytes(out)


def _read_into(stream, view: memoryview) -> None:
    """Fills ``view`` from ``stream``.

    Args:
        stream: A binary stream; its ``readinto`` is used if it has one,
                else its ``read``.
        view:   Where the bytes go; all of it is filled.

    Raises:
        EOFError: The stream ends first.
    """
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
    """Computes the bit position ``offset`` bits from ``base``.

    Args:
        base:   The bit the offset counts from.
        offset: The offset in bits, which may be negative.
        field:  The entry placed there, for errors.

    Returns:
        The position in bits from the packet's start.

    Raises:
        PacketError: ``BadPosition`` if it is before the packet's start.
    """
    p = base + offset
    if p < 0:
        raise PacketError("BadPosition", field)
    return p


class BitReader:
    """Reads big-endian bit fields from a bytes-like object."""

    __slots__ = ("data", "pos", "nbits", "end", "lsb")

    def __init__(self, data) -> None:
        """Starts reading at the first bit of ``data``.

        Args:
            data: The packet, bytes-like; it is not copied.
        """
        self.data = data
        self.pos = 0
        self.nbits = len(data) * 8
        self.end = 0  # the furthest bit read
        self.lsb = False  # strings and binary data least significant first

    def set_lsb(self, lsb: bool) -> None:
        """Reads the bytes of strings and binary data, and their size
        tags, least significant bit first when ``lsb``: each byte is a
        group of 8 bits read as :meth:`uint_bits` reads one.  On a byte
        boundary that is the bytes as they are."""
        self.lsb = lsb

    def tag(self, bits: int, field: str) -> int:
        """A string's size tag of ``bits`` bits, in its bit order."""
        if self.lsb:
            return self.uint_bits(bits, True, "be", field)
        return self.uint(bits, field)

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

    def seek_ahead(self, start: int, offset: int, field: str) -> None:
        """Moves to ``offset`` bits from ``start``, not backwards.

        As :meth:`seek`, but for an entry located from the end of a container
        whose size another value gives, which must not overlap the entries
        before it.

        Args:
            start:  The bit the offset counts from.
            offset: The offset in bits.
            field:  The entry placed there, for errors.

        Raises:
            PacketError: ``BadPosition`` if that is before the packet's start
                         or the current position.
        """
        p = _position(start, offset, field)
        if p < self.pos:
            raise PacketError("BadPosition", field)
        self.pos = p
        self.end = max(self.end, p)

    def skip(self, offset: int, field: str) -> None:
        self.seek(self.pos, offset, field)

    def seek_end(self, back: int, field: str) -> None:
        """Moves to ``back`` bits before the end of the packet.

        Args:
            back:  How many bits before the end.
            field: The entry placed there, for errors.

        Raises:
            PacketError: ``BadPosition`` if that is before the current
                         position.
        """
        p = self.nbits - back
        if p < self.pos:
            raise PacketError("BadPosition", field)
        self.pos = p
        self.end = max(self.end, p)

    def uint(self, bits: int, field: str) -> int:
        """Reads an unsigned big-endian field.

        Args:
            bits:  The field's size in bits.
            field: The field's name, for errors.

        Returns:
            The field's value.

        Raises:
            PacketError: ``Truncated`` if the packet ends first.
        """
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
        """Reads an unsigned field of ``bits`` bits in any bit and byte order.

        The field's bits are taken in groups of 8 (the last may be shorter).
        Within a group, ``lsb`` False numbers the bits of each byte from the
        most significant (the usual order), and the group's first bit is its
        most significant; ``lsb`` True numbers them from the least
        significant, and the group's first bit is its least significant.
        ``order`` combines the groups: ``"be"`` puts the first group most
        significant, ``"le"`` least significant, and a tuple gives each
        (whole) byte's significance, as an XTCE byte order list does.

        Args:
            bits:  The field's size in bits.
            lsb:   Whether bits are numbered from the least significant.
            order: ``"be"``, ``"le"`` or a tuple of byte significances.
            field: The field's name, for errors.

        Returns:
            The field's value.

        Raises:
            PacketError: ``Truncated`` if the packet ends first.
        """
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
        """Skips ``bits`` bits of padding, which must be in the packet.

        Args:
            bits:  How many bits to skip.
            field: The entry the padding belongs to, for errors.

        Raises:
            PacketError: ``Truncated`` if the packet ends first.
        """
        if self.pos + bits > self.nbits:
            raise PacketError("Truncated", field)
        self.pos += bits
        self.end = max(self.end, self.pos)

    def limit(self, v: int, adjust: int, field: str) -> None:
        """Ends the packet where its packet length field says.

        The packet ends ``v - adjust`` bytes after the field.

        Args:
            v:      The value of the packet length field, just read.
            adjust: What the field's value is more than the bytes after it.
            field:  The packet length field's name, for errors.

        Raises:
            PacketError: ``BadLength`` if ``v`` is less than ``adjust``, and
                         ``Truncated`` if the data is shorter.
        """
        after = v - adjust
        if after < 0:
            raise PacketError("BadLength", field)
        total = (self.pos + 7) // 8 + after
        if total * 8 > self.nbits:
            raise PacketError("Truncated", field)
        self.nbits = total * 8

    def _offer(self, n, field: str) -> bytes:
        """Gets the next whole bytes, without reading them.

        Args:
            n:     How many bytes, or None for the rest of the packet.
            field: The field's name, for errors.

        Returns:
            The bytes.

        Raises:
            PacketError: ``BadPosition`` off a byte boundary, ``Truncated``
                         if the packet ends first.
        """
        if self.pos % 8:
            raise PacketError("BadPosition", field)
        if n is None:
            n = max(self.nbits - self.pos, 0) // 8
        end = self.pos + 8 * n
        if end > self.nbits:
            raise PacketError("Truncated", field)
        return bytes(self.data[self.pos // 8:end // 8])

    def _took(self, k, offered: int, raw, field: str) -> None:
        """Moves past the bytes a transform took of those ``_offer`` gave.

        Args:
            k:       How many bytes the transform says it took.
            offered: How many bytes it was offered.
            raw:     How many it must take, or None for any number.
            field:   The field's name, for errors.

        Raises:
            PacketError: ``BadSize`` unless ``k`` is an integer from 0 to
                         ``offered``, equal to ``raw`` when that is given.
        """
        if (not isinstance(k, int) or not 0 <= k <= offered
                or (raw is not None and k != raw)):
            raise PacketError("BadSize", field)
        self.pos += 8 * k
        self.end = max(self.end, self.pos)

    def binary_from(self, f, raw, cap: int, field: str) -> bytes:
        """Reads data that the transform ``f`` decodes.

        Offers it the next ``raw`` bytes, or the rest of the packet, and moves
        past those it says it takes.

        Args:
            f:     The decoding transform: takes bytes, gives the bytes it took
                   and the bytes it makes, or None if they are invalid.  None
                   if the document gives only the encoding one.
            raw:   How many bytes it must take, or None for any number.
            cap:   The most bytes it may make.
            field: The field's name, for errors.

        Returns:
            The bytes it makes.

        Raises:
            PacketError: ``NotInvertible`` with no transform, ``InvalidValue``
                         if it gives None, ``TooLong`` if it makes more than
                         ``cap`` bytes, or as :meth:`_offer` and
                         :meth:`_took` do.
        """
        if f is None:
            # The document gives only the other way.
            raise PacketError("NotInvertible", field)
        data = self._offer(raw, field)
        got = f(data)
        if got is None:
            raise PacketError("InvalidValue", field)
        k, out = got
        if len(out) > cap:
            raise PacketError("TooLong", field)
        self._took(k, len(data), raw, field)
        return bytes(out)

    def time_from(self, f, raw, field: str) -> float:
        """Reads a time that the transform ``f`` decodes, as ``binary_from``
        reads bytes.

        Args:
            f:     The decoding transform: takes bytes, gives the bytes it took
                   and the time, or None if they are invalid.  None if the
                   document gives only the encoding one.
            raw:   How many bytes it must take, or None for any number.
            field: The field's name, for errors.

        Returns:
            The time, in seconds.

        Raises:
            PacketError: As :meth:`binary_from`.
        """
        if f is None:
            raise PacketError("NotInvertible", field)
        data = self._offer(raw, field)
        got = f(data)
        if got is None:
            raise PacketError("InvalidValue", field)
        k, t = got
        self._took(k, len(data), raw, field)
        return float(t)

    def span_end(self, v: int, adjust: int, field: str) -> int:
        """Where the container that the length field ``field``, whose value
        ``v`` was just read, measures ends: ``v - adjust`` bytes after the
        field, in bytes from the packet's start."""
        if v - adjust < 0:
            raise PacketError("BadLength", field)
        return (self.pos + 7) // 8 + v - adjust

    def check_span(self, end: int, field: str) -> None:
        """Raises BadLength unless the container that ``field`` measures
        ends at byte ``end``, here."""
        if (self.pos + 7) // 8 != end:
            raise PacketError("BadLength", field)

    def finish(self, field: str) -> None:
        """Check that the packet's entries reach the end that the packet
        length field ``field`` gave."""
        if (self.end + 7) // 8 * 8 != self.nbits:
            raise PacketError("BadLength", field)

    def rest_bytes(self, max_: int, tail: int, field: str) -> int:
        """Measures a field that takes the rest of the packet but a tail.

        Args:
            max_:  The most bytes the field holds.
            tail:  The bits of the entries after the field.
            field: The field's name, for errors.

        Returns:
            The bytes from the current position to ``tail`` bits before the
            end of the packet.

        Raises:
            PacketError: ``BadSize`` unless they are whole bytes, at most
                         ``max_`` of them.
        """
        bits = max(self.nbits - self.pos - tail, 0)
        if bits % 8 or bits // 8 > max_:
            raise PacketError("BadSize", field)
        return bits // 8

    def embedded_bytes(self, max_: int, field: str) -> int:
        """Measures the CCSDS packet that starts here, for an embedded packet.

        Reads its packet length field (as Yamcs:EmbeddedTc does) without
        moving.

        Args:
            max_:  The most bytes the embedded packet may have.
            field: The field's name, for errors.

        Returns:
            The embedded packet's size in bytes.

        Raises:
            PacketError: ``Truncated`` if its header isn't all here, and
                         ``TooLong`` if it is more than ``max_`` bytes.
        """
        pos, end = self.pos, self.end
        self.pad(32, field)
        n = self.uint(16, field) + 7
        self.pos, self.end = pos, end
        if n > max_:
            raise PacketError("TooLong", field)
        return n

    def bytes(self, n: int, field: str) -> bytes:
        """Reads ``n`` bytes, in the reader's bit order off a byte boundary.

        Args:
            n:     How many bytes.
            field: The field's name, for errors.

        Returns:
            The bytes.

        Raises:
            PacketError: ``Truncated`` if the packet ends first.
        """
        if self.pos + n * 8 > self.nbits:
            raise PacketError("Truncated", field)
        if self.pos % 8 == 0:
            start = self.pos >> 3
            self.pos += n * 8
            self.end = max(self.end, self.pos)
            return bytes(self.data[start : start + n])
        if self.lsb:
            return bytes(self.uint_bits(8, True, "be", field) for _ in range(n))
        return self.uint(n * 8, field).to_bytes(n, "big") if n else b""


class BitWriter:
    """Writes big-endian bit fields into a :class:`~xtce.ManagedBuffer`.

    The buffer is cleared when the writer is created and grows (zero filled)
    to cover the furthest bit written; its allocation is reused.
    """

    __slots__ = ("buf", "pos", "lsb")

    def __init__(self, buf: ManagedBuffer) -> None:
        """Starts writing at the first bit of ``buf``, which is cleared.

        Args:
            buf: The buffer the packet goes into.
        """
        buf.clear()
        self.buf = buf
        self.pos = 0
        self.lsb = False  # strings and binary data least significant first

    def set_lsb(self, lsb: bool) -> None:
        """Writes the bytes of strings and binary data, and their size
        tags, least significant bit first when ``lsb``, as
        :meth:`BitReader.set_lsb` reads them."""
        self.lsb = lsb

    def tag(self, bits: int, v: int) -> None:
        """Writes a string's size tag of ``bits`` bits, in its bit order.

        Args:
            bits: The tag's size in bits.
            v:    Its value.
        """
        if self.lsb:
            self.uint_bits(bits, True, "be", v)
        else:
            self.uint(bits, v)

    def position(self) -> int:
        return self.pos

    def seek(self, start: int, offset: int, field: str) -> None:
        self.pos = _position(start, offset, field)

    def seek_ahead(self, start: int, offset: int, field: str) -> None:
        """Moves to ``offset`` bits from ``start``, not backwards, as
        :meth:`BitReader.seek_ahead` does.

        Args:
            start:  The bit the offset counts from.
            offset: The offset in bits.
            field:  The entry placed there, for errors.

        Raises:
            PacketError: ``BadPosition`` if that is before the packet's start
                         or the current position.
        """
        p = _position(start, offset, field)
        if p < self.pos:
            raise PacketError("BadPosition", field)
        self.pos = p

    def skip(self, offset: int, field: str) -> None:
        self.pos = _position(self.pos, offset, field)

    def seek_tail(self, back: int) -> None:
        """Move forward the fewest bits (zeros) that put the end of the
        packet, ``back`` bits on, on a byte boundary: where decoding finds
        an entry located from the packet's end."""
        self.pos += -(self.pos + back) % 8

    def uint(self, bits: int, v: int) -> None:
        """Writes an unsigned big-endian field, growing the buffer to hold it.

        Args:
            bits: The field's size in bits.
            v:    Its value; only the low ``bits`` bits are written.
        """
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
        """Writes the low ``bits`` bits of ``v`` in the bit and byte order that
        ``BitReader.uint_bits`` reads.

        Args:
            bits:  The field's size in bits.
            lsb:   Whether bits are numbered from the least significant.
            order: ``"be"``, ``"le"`` or a tuple of byte significances.
            v:     The value.
        """
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
        """Fills in a packet length field once the whole packet is written.

        It holds the number of bytes after the field plus ``adjust``.

        Args:
            at:     The field's position in bits.
            bits:   Its size in bits.
            le:     Whether it is little endian.
            adjust: What its value is more than the bytes after it.
            field:  Its name, for errors.

        Raises:
            PacketError: ``OutOfRange`` if the field can't hold the value.
        """
        v = len(self.buf) - (at + bits + 7) // 8 + adjust
        if not 0 <= v < 1 << bits:
            raise PacketError("OutOfRange", field)
        end = self.pos
        self.pos = at
        self.uint_order(bits, le, v)
        self.pos = end

    def transform(self, start: int, f, raw, cap: int, field: str) -> None:
        """Replaces the bytes written from bit ``start`` with what the transform
        ``f`` makes of them.

        Args:
            start: Where the bytes start, on a byte boundary.
            f:     The encoding transform: takes bytes, gives bytes or None if
                   it can't encode them.  None if the document gives only the
                   decoding one.
            raw:   How many bytes it must make, or None for any number.
            cap:   The most bytes it may make.
            field: The field's name, for errors.

        Raises:
            PacketError: ``NotInvertible`` with no transform, ``InvalidValue``
                         if it gives None, ``TooLong`` if it makes more than
                         ``cap`` bytes, and ``BadSize`` if not ``raw``.
        """
        a, b = start // 8, (self.pos + 7) // 8
        if f is None:
            raise PacketError("NotInvertible", field)
        out = f(bytes(self.buf._data[a:b]))
        if out is None:
            raise PacketError("InvalidValue", field)
        if len(out) > cap:
            raise PacketError("TooLong", field)
        if raw is not None and len(out) != raw:
            raise PacketError("BadSize", field)
        self.buf.resize(a)
        self.buf.write(out)
        self.pos = 8 * len(self.buf)

    def binary_to(self, f, data: bytes, raw, cap: int, field: str) -> None:
        """Writes what the transform ``f`` makes of ``data``, as ``transform``
        does, on a byte boundary.

        Args:
            f:     The encoding transform, as for :meth:`transform`.
            data:  The bytes to transform.
            raw:   How many bytes it must make, or None for any number.
            cap:   The most bytes it may make.
            field: The field's name, for errors.

        Raises:
            PacketError: ``BadPosition`` off a byte boundary, or as
                         :meth:`transform` does.
        """
        if self.pos % 8:
            raise PacketError("BadPosition", field)
        start = self.pos
        self.bytes(data)
        self.transform(start, f, raw, cap, field)

    def time_to(self, f, t: float, raw, cap: int, field: str) -> None:
        """Writes what the transform ``f`` makes of the time ``t``, on a byte
        boundary.

        Args:
            f:     The encoding transform: takes seconds, gives bytes or None
                   if it can't encode them.  None if the document gives only
                   the decoding one.
            t:     The time, in seconds.
            raw:   How many bytes it must make, or None for any number.
            cap:   The most bytes it may make.
            field: The field's name, for errors.

        Raises:
            PacketError: ``NotInvertible`` with no transform, ``BadPosition``
                         off a byte boundary, ``InvalidValue`` if it gives
                         None, ``TooLong`` if it makes more than ``cap`` bytes,
                         and ``BadSize`` if not ``raw``.
        """
        if f is None:
            raise PacketError("NotInvertible", field)
        if self.pos % 8:
            raise PacketError("BadPosition", field)
        out = f(t)
        if out is None:
            raise PacketError("InvalidValue", field)
        if len(out) > cap:
            raise PacketError("TooLong", field)
        if raw is not None and len(out) != raw:
            raise PacketError("BadSize", field)
        self.buf.resize(self.pos // 8)
        self.buf.write(out)
        self.pos = 8 * len(self.buf)

    def patch_span(self, at: int, bits: int, le: bool, adjust: int,
                   field: str) -> None:
        """Fills in a length field that measures an included container that ends
        here.

        It holds the bytes after the field plus ``adjust``.

        Args:
            at:     The field's position in bits.
            bits:   Its size in bits.
            le:     Whether it is little endian.
            adjust: What its value is more than the bytes after it.
            field:  Its name, for errors.

        Raises:
            PacketError: ``OutOfRange`` if the field can't hold the value.
        """
        v = (self.pos + 7) // 8 - (at + bits + 7) // 8 + adjust
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
        """Fills in a checksum field.

        Args:
            spec:  The checksum, as :func:`checksum` takes it.
            start: The first bit it covers.
            end:   The bit after the last it covers.
            at:    The field's position in bits.
            bits:  Its size in bits; the checksum's low ``bits`` bits go in it.
            le:    Whether it is little endian.
            field: Its name, for errors.

        Raises:
            PacketError: As :func:`checksum` does.
        """
        v = checksum(spec, self.buf._data, start, end, field)
        pos = self.pos
        self.pos = at
        self.uint_order(bits, le, v & ((1 << bits) - 1))
        self.pos = pos

    def bytes(self, data) -> None:
        """Writes bytes, in the writer's bit order off a byte boundary.

        Args:
            data: The bytes.
        """
        if data and self.lsb and self.pos % 8:
            for b in data:
                self.uint_bits(8, True, "be", b)
        elif data:
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
    """Combines groups of bits into a value.

    Args:
        groups: The groups' (value, length) pairs, in stream order.
        order:  How to combine them, as for ``BitReader.uint_bits``.

    Returns:
        The value.
    """
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
    """Splits a value into groups of bits: the inverse of ``_combine``.

    Args:
        v:       The value.
        lengths: Each group's length in bits, in stream order.
        order:   How the groups combine, as for ``BitReader.uint_bits``.

    Returns:
        The groups' (value, length) pairs, in stream order.
    """
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
    """Splits bits of data into words.

    Args:
        data:  The bytes-like data.
        start: The first bit.
        end:   The bit after the last.
        width: The words' size in bits.

    Returns:
        The words, most significant bit first; a last partial word is
        padded with zero bits.
    """
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
    """Reads decimal digits, one in every 4 bits.

    Args:
        data:  The bytes-like data.
        start: The first bit.
        end:   The bit after the last.
        field: The checksum field's name, for errors.

    Returns:
        The digits, the first most significant.

    Raises:
        PacketError: ``InvalidValue`` for 4 bits that aren't a digit.
    """
    ds = _words(data, start, end, 4)
    if any(d > 9 for d in ds):
        raise PacketError("InvalidValue", field)
    return ds


def checksum(spec: tuple, data, start: int, end: int, field: str) -> int:
    """Computes an XTCE ErrorDetectCorrect algorithm over bits of data.

    Args:
        spec:  ``("crc", width, polynomial, init, final_xor, reflect_data,
               reflect_remainder)``, ``("sum", bits)``, ``("unix_sum",)``,
               ``("fletcher", bits)``, ``("adler32",)``, ``("luhn",)``,
               ``("verhoeff",)``, ``("damm",)``, ``("xor", bits)``,
               ``("parity", odd)`` or ``("custom", function)``, where
               ``function`` takes the data, start and end, or is None; the
               manual describes each.
        data:  The bytes-like data.
        start: The first bit covered.
        end:   The bit after the last.
        field: The checksum field's name, for errors.

    Returns:
        The checksum.

    Raises:
        PacketError: ``InvalidValue`` for a non-digit under a decimal
                     algorithm, ``NoAlgorithm`` for a custom one with no
                     function.
    """
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
    """Converts a raw field to its value.

    Args:
        raw:  The raw field.
        bits: Its size in bits.
        enc:  Its integer encoding, as in the XTCE schema.

    Returns:
        The value.
    """
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
    """Converts a value to its raw field, checking that it fits.

    Args:
        v:     The value.
        bits:  The field's size in bits.
        enc:   Its integer encoding, as in the XTCE schema.
        field: The field's name, for errors.

    Returns:
        The raw field.

    Raises:
        PacketError: ``OutOfRange`` if the encoding can't hold the value.
    """
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
    """Checks that an unsigned field holds a value.

    Args:
        v:     The value.
        bits:  The field's size in bits.
        field: The field's name, for errors.

    Returns:
        The value, as an int.

    Raises:
        PacketError: ``OutOfRange`` unless it is from 0 to 2**bits - 1.
    """
    v = int(v)
    if not 0 <= v < (1 << bits):
        raise PacketError("OutOfRange", field)
    return v


def int_float(v: int) -> float:
    """An integer as a float, as the Rust runtime converts it.

    Args:
        v: The integer, of any size.

    Returns:
        The nearest float, ties to even, or an infinity past the largest.
    """
    try:
        return float(v)
    except OverflowError:
        return math.copysign(math.inf, v)


def length_u64(v: int, field: str) -> int:
    """A packet length field's value, from a field of more than 64 bits.

    Args:
        v:     The field's value.
        field: The field's name, for errors.

    Returns:
        ``v``; raises ``BadLength`` unless an unsigned 64-bit integer holds
        it, as the other runtimes do.
    """
    if v >= 1 << 64:
        raise PacketError("BadLength", field)
    return v


def fits_i64(v: int, field: str) -> int:
    """An enumeration's value from a field of more than 64 bits.

    Args:
        v:     The field's value.
        field: The field's name, for errors.

    Returns:
        ``v``; raises ``OutOfRange`` unless a signed 64-bit integer holds
        it, as the other runtimes' enumerations do.
    """
    if not -(1 << 63) <= v < 1 << 63:
        raise PacketError("OutOfRange", field)
    return v


def int_to_float(raw: int, bits: int, enc: str) -> float:
    """A raw field as a float.

    Args:
        raw:  The raw field.
        bits: The bits of the field.
        enc:  Its integer encoding, as in the XTCE schema.

    Returns:
        Its value, as :func:`int_float` converts it.
    """
    return int_float(to_signed(raw, bits, enc))


def bcd_decode(raw: int, bits: int, packed: bool, field: str) -> int:
    """Decodes a BCD field.

    Packed BCD has a decimal digit in every 4 bits, and (unpacked) BCD one
    in the low 4 bits of every byte, whose high 4 bits are zero; the first
    digit is the most significant.

    Args:
        raw:    The raw field.
        bits:   Its size in bits.
        packed: Whether it is packed BCD.
        field:  Its name, for errors.

    Returns:
        The value.

    Raises:
        PacketError: ``InvalidValue`` for any other bits.
    """
    width = 4 if packed else 8
    v = 0
    for shift in range(bits - width, -1, -width):
        d = (raw >> shift) & ((1 << width) - 1)
        if d > 9:
            raise PacketError("InvalidValue", field)
        v = v * 10 + d
    return v


def bcd_encode(v: int, bits: int, packed: bool, field: str) -> int:
    """Encodes ``v`` as a BCD field, as :func:`bcd_decode` decodes it.

    Args:
        v:      The value.
        bits:   The field's size in bits.
        packed: Whether it is packed BCD.
        field:  Its name, for errors.

    Returns:
        The raw field.

    Raises:
        PacketError: ``OutOfRange`` unless ``v`` is from 0 to the most the
                     field's digits hold.
    """
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
    """Rounds half away from zero, like Rust's ``f64::round`` and C++'s
    ``std::round``.

    Args:
        x: The value, finite.

    Returns:
        The nearest integer.
    """
    i = math.trunc(x)
    frac = x - i
    if frac >= 0.5:
        i += 1
    elif frac <= -0.5:
        i -= 1
    return i


def float_to_int(x: float, bits: int, enc: str, field: str) -> int:
    """Rounds an engineering value to a raw integer field.

    Args:
        x:     The value, after any inverse calibrator.
        bits:  The field's size in bits, at most 64.
        enc:   Its integer encoding, as in the XTCE schema.
        field: Its name, for errors.

    Returns:
        The raw field.

    Raises:
        PacketError: ``OutOfRange`` unless the field holds the value
                     rounded half away from zero.
    """
    if not math.isfinite(x):
        raise PacketError("OutOfRange", field)
    r = _round(x)
    if (abs(r) >= 1 << 64
            or (enc != "unsigned" and not -(1 << 63) <= r < 1 << 63)):
        raise PacketError("OutOfRange", field)
    return from_signed(r, bits, enc, field)


def float_to_wide(x: float, bits: int, enc: str, field: str) -> int:
    """:func:`float_to_int` for fields of more than 64 bits.

    Args:
        x:     The engineering value, after any inverse calibrator.
        bits:  The bits of the field.
        enc:   Its integer encoding, as in the XTCE schema.
        field: The field's name, for errors.

    Returns:
        The raw field; raises ``OutOfRange`` unless it holds the value
        rounded half away from zero.
    """
    if not math.isfinite(x):
        raise PacketError("OutOfRange", field)
    return from_signed(_round(x), bits, enc, field)


def round_to_int(y: float, lo: float, lim: float, field: str) -> int:
    """Rounds a calibrated value half away from zero to an integer in a range.

    Args:
        y:     The value.
        lo:    The smallest integer allowed.
        lim:   The integer after the largest allowed.
        field: The field's name, for errors.

    Returns:
        The integer.

    Raises:
        PacketError: ``OutOfRange`` if it is not finite or out of range.
    """
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
    """Converts a value to the bits of a binary32 or binary64 float.

    A binary32 too large for its range rounds to infinity, as Rust and C++
    do.

    Args:
        v:    The value.
        bits: 32 or 64.

    Returns:
        The raw field.
    """
    if bits == 32:
        try:
            b = struct.pack(">f", v)
        except OverflowError:  # Rust and C++ round to infinity
            b = struct.pack(">f", math.copysign(math.inf, v))
        return int.from_bytes(b, "big")
    return int.from_bytes(struct.pack(">d", v), "big")


def _to_float(neg: bool, m: int, e: int) -> float:
    """Computes ``(-1)**neg * m * 2**e`` rounded to the nearest double, ties
    to even; infinite when it is too large.

    Args:
        neg: Whether it is negative.
        m:   The significand, a non-negative integer.
        e:   The exponent of two.

    Returns:
        The double.
    """
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
    """Computes ``m / 2**shift`` rounded to the nearest integer, ties to even.

    Args:
        m:     The integer, non-negative.
        shift: The bits to shift right; negative shifts left.

    Returns:
        The result.
    """
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
    """Decodes an IEEE 754 binary float.

    Args:
        raw:  The raw field.
        bits: Its size: 16, 32, 64, 80 (x87 extended) or 128.

    Returns:
        The nearest double, ties to even.
    """
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
    """Encodes ``x`` in an IEEE 754 binary format.

    Args:
        x:    The value.
        bits: The format's size: 16, 32, 64, 80 (x87 extended) or 128.

    Returns:
        The raw field nearest ``x``, ties to even, overflowing to
        infinity; a NaN is a quiet NaN.
    """
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
    """Decodes a MIL-STD-1750A float.

    Args:
        raw:  The raw field.
        bits: Its size: 32 or 48.

    Returns:
        The nearest double, ties to even.
    """
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
    """Encodes ``x`` as a MIL-STD-1750A float.

    A value too small for the format is unnormalized at the smallest
    exponent.

    Args:
        x:     The value, finite.
        bits:  The format's size: 32 or 48.
        field: The field's name, for errors.

    Returns:
        The raw field nearest ``x``, ties to even.

    Raises:
        PacketError: ``OutOfRange`` if ``x`` is too large for the format.
    """
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
    """Decodes a DEC F_floating (32 bits) or D_floating (64 bits) float.

    Args:
        raw:   The raw field.
        bits:  Its size: 32 or 64.
        field: The field's name, for errors.

    Returns:
        The nearest double, ties to even.

    Raises:
        PacketError: ``InvalidValue`` for a reserved operand: the sign set
                     with a zero exponent.
    """
    f = 23 if bits == 32 else 55
    neg = raw >> (bits - 1)
    exp = (raw >> f) & 0xFF
    if exp == 0:
        if neg:  # a reserved operand
            raise PacketError("InvalidValue", field)
        return 0.0
    return _to_float(neg, (raw & ((1 << f) - 1)) | (1 << f), exp - 129 - f)


def _dec_encode(x: float, bits: int, field: str) -> int:
    """Encodes ``x`` as a DEC F_floating (32 bits) or D_floating (64 bits)
    float.

    Args:
        x:     The value, finite.
        bits:  The format's size: 32 or 64.
        field: The field's name, for errors.

    Returns:
        The raw field nearest ``x``, ties to even; zero for a value too
        small for the format.

    Raises:
        PacketError: ``OutOfRange`` if ``x`` is too large for the format.
    """
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
    """Decodes an IBM hexadecimal float.

    Args:
        raw:  The raw field.
        bits: Its size: 32, 64 or 128.

    Returns:
        The nearest double, ties to even.
    """
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
    """Encodes ``x`` as an IBM hexadecimal float.

    A value too small for the format is unnormalized at the smallest
    exponent.  In the 128-bit format the low half repeats the sign and has
    a characteristic 14 less.

    Args:
        x:     The value, finite.
        bits:  The format's size: 32, 64 or 128.
        field: The field's name, for errors.

    Returns:
        The raw field nearest ``x``, ties to even.

    Raises:
        PacketError: ``OutOfRange`` if ``x`` is too large for the format.
    """
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
    """Decodes a TI TMS320C3x float.

    Args:
        raw:  The raw field.
        bits: Its size: 16, 32 or 40.

    Returns:
        The nearest double, ties to even; zero for the smallest exponent.
    """
    ebits, f = _TI[bits]
    exp = to_signed(raw >> (f + 1), ebits, "twosComplement")
    if exp == -(1 << (ebits - 1)):
        return 0.0
    frac = raw & ((1 << f) - 1)
    if raw >> f & 1:  # (-2 + frac) * 2**exp
        return _to_float(True, (2 << f) - frac, exp - f)
    return _to_float(False, (1 << f) | frac, exp - f)


def _ti_encode(x: float, bits: int, field: str) -> int:
    """Encodes ``x`` as a TI TMS320C3x float.

    Args:
        x:     The value, finite.
        bits:  The format's size: 16, 32 or 40.
        field: The field's name, for errors.

    Returns:
        The raw field nearest ``x``, ties to even; the format's zero for
        a value too small for it.

    Raises:
        PacketError: ``OutOfRange`` if ``x`` is too large for the format.
    """
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
    """Decodes a float field.

    Args:
        fmt:   The XTCE encoding: ``IEEE754`` (16, 32, 64, 80 or 128 bits),
               ``MILSTD_1750A`` (32 or 48), ``DEC`` (32 for F_floating, 64
               for D_floating), ``IBM`` (32, 64 or 128) or ``TI`` (16, 32
               or 40).
        bits:  The field's size in bits.
        raw:   The raw field.
        field: The field's name, for errors.

    Returns:
        The value, rounded to the nearest double.

    Raises:
        PacketError: ``InvalidValue`` for a DEC reserved operand.
    """
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
    """Encodes a float field, as ``float_decode`` reads it.

    IEEE formats overflow to infinity; the others, which have no infinity
    or NaN, encode values too small for them as zero (or unnormalized).

    Args:
        fmt:   The XTCE encoding, as for :func:`float_decode`.
        bits:  The field's size in bits.
        x:     The value.
        field: The field's name, for errors.

    Returns:
        The raw field nearest ``x``, ties to even.

    Raises:
        PacketError: ``OutOfRange``, for a format other than IEEE, for
                     values too large for it and for infinities and NaN.
    """
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
    """Evaluates a PolynomialCalibrator.

    Args:
        x:     The raw value.
        terms: The terms' (coefficient, exponent) pairs.

    Returns:
        The calibrated value.
    """
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
    """Evaluates a SplineCalibrator of order 0 or 1.

    Args:
        x:           The raw value.
        points:      The (raw, calibrated) points, in order of raw value.
        order:       0 for steps, 1 for straight lines between the points.
        extrapolate: Whether to extend the first and last segments past
                     the points.
        field:       The field's name, for errors.

    Returns:
        The calibrated value.

    Raises:
        PacketError: ``Calibration`` if ``x`` is outside the points and
                     ``extrapolate`` is False.
    """
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
    """Evaluates a spline whose segments have their own orders.

    The segment from point ``i`` has order ``orders[i]``, and one of order
    k interpolates through k + 1 points, starting at point ``i`` or as
    close after it as the points allow.  Outside the points, the first or
    last segment extrapolates.

    Args:
        x:           The raw value.
        points:      The (raw, calibrated) points, in order of raw value.
        orders:      Each segment's order.
        extrapolate: Whether values outside the points are allowed.
        field:       The field's name, for errors.

    Returns:
        The calibrated value.

    Raises:
        PacketError: ``Calibration`` if ``x`` is outside the points and
                     ``extrapolate`` is False.
    """
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
    """Inverts a linear SplineCalibrator, for encoding.

    Interpolates in the first segment whose calibrated values span ``y``.

    Args:
        y:           The calibrated value.
        points:      The (raw, calibrated) points, in order of raw value.
        extrapolate: Whether to extend the first and last segments past
                     the points.
        field:       The field's name, for errors.

    Returns:
        The raw value.

    Raises:
        PacketError: ``Calibration`` if no segment spans ``y`` and
                     ``extrapolate`` is False.
    """
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
    """Calls a math function, giving C libm's result where Python's raises.

    Args:
        f:     The function.
        *args: Its arguments.

    Returns:
        What ``f`` returns, or what libm gives on a domain error, overflow
        or pole.
    """
    try:
        return f(*args)
    except (ValueError, OverflowError, ZeroDivisionError):
        return _libm_result(f, *args)


def _libm_result(f, *args) -> float:
    """Gives what C's libm returns where Python's math raises.

    Args:
        f:     The function that raised.
        *args: Its arguments.

    Returns:
        NaN for domain errors, infinity of the right sign for overflow and
        poles.
    """
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
    """Applies a MathOperationCalibrator operator; identical in every runtime.

    Args:
        op: The operator, as the XTCE schema names it; see the manual for
            each.
        a:  The first operand.
        b:  The second operand, ignored by unary operators.

    Returns:
        The result.
    """
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
    """Computes a size or count that another field gives (an XTCE
    ``DynamicValue``): ``(slope * x + intercept) / unit``.

    Args:
        x:         The value of the field that gives it.
        slope:     The linear adjustment's slope.
        intercept: Its intercept.
        unit:      8 for a size in bits used in whole bytes, else 1.
        max_:      The largest size or count allowed.
        field:     The sized field's name, for errors.

    Returns:
        The size or count.

    Raises:
        PacketError: ``BadSize`` unless the result is a whole number from
                     0 to ``max_``.
    """
    # int_float, so that an integer too large for a float is infinite, as
    # in the other runtimes, rather than an OverflowError.
    y = slope * int_float(x) + intercept
    if (not (y >= 0.0) or not y.is_integer() or not (y / unit).is_integer()
            or y / unit > max_):
        raise PacketError("BadSize", field)
    return int(y / unit)


def size_source(n: int, slope: float, intercept: float, unit: int, factor: int,
                field: str) -> int:
    """Computes the value of the field that gives a size or count of ``n``.

    It is the inverse of ``dynamic_size`` followed by multiplying by
    ``factor``, for encoding a field computed from the length of the
    value it sizes.

    Args:
        n:         The size or count.
        slope:     The linear adjustment's slope.
        intercept: Its intercept.
        unit:      8 for a size in bits used in whole bytes, else 1.
        factor:    What the size or count is multiplied by.
        field:     The sized value's name, for errors.

    Returns:
        The field's value.

    Raises:
        PacketError: ``BadSize`` when no whole number gives exactly ``n``.
    """
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
    """Reads ``n`` values, with ``gap`` bits between them.

    Args:
        r:     The reader.
        n:     How many values.
        gap:   The bits to skip between values.
        field: The array's name, for errors.
        read:  Reads one value, called with no arguments.

    Returns:
        The values.
    """
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


def element_at(v, i: int, stride: int, per: int, need: int, field: str):
    """Gets an element of a multi-dimensional array whose count varies in a
    dimension after the first.

    That dimension has ``d = len(v) / per`` elements.

    Args:
        v:      The array's elements, flattened.
        i:      The part of the element's index that doesn't depend on
                ``d``.
        stride: What ``d`` is multiplied by in the index.
        per:    The number of elements per element of that dimension.
        need:   The least ``d`` that has the element.
        field:  The array's name, for errors.

    Returns:
        Element ``i + stride * d`` of ``v``.

    Raises:
        PacketError: ``BadSize`` unless ``d`` is at least ``need``.
    """
    d = len(v) // per
    if d < need:
        raise PacketError("BadSize", field)
    return v[i + stride * d]


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
    """Decodes a string's bytes.

    A UTF-16 or UTF-32 string with no byte order in its name may start
    with a byte order mark; otherwise it is big endian.

    Args:
        b:       The bytes.
        charset: The XTCE string encoding.
        field:   The field's name, for errors.

    Returns:
        The string.

    Raises:
        PacketError: ``InvalidString`` if the bytes aren't valid.
    """
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
    """Encodes a string's bytes, big endian with no byte order mark for
    UTF-16 and UTF-32 without a byte order.

    Args:
        s:       The string.
        charset: The XTCE string encoding.
        field:   The field's name, for errors.

    Returns:
        The bytes.

    Raises:
        PacketError: ``InvalidString`` if it has characters that
                     ``charset`` can't hold.
    """
    try:
        return s.encode(_CODECS[charset])
    except UnicodeEncodeError:
        raise PacketError("InvalidString", field) from None


def _find_unit(b: bytes, term: bytes, unit: int) -> int:
    """Finds a terminator that starts a code unit.

    Args:
        b:    The string's bytes.
        term: The terminator's bytes.
        unit: The bytes in a code unit.

    Returns:
        The first position of ``term`` in ``b`` that is a multiple of
        ``unit``, or -1.
    """
    i = b.find(term)
    while i >= 0 and i % unit:
        i = b.find(term, i + 1)
    return i


def read_string(r: BitReader, spec: tuple, field: str) -> str:
    """Reads a string.

    Args:
        r:     The reader.
        spec:  (fixed_bits, max_bits, terminator, size_tag_bits, charset):
               the field's size in bits or None if it varies, the most bits
               it may take, the terminator's bytes or None, the size tag's
               bits, and the XTCE string encoding.
        field: The field's name, for errors.

    Returns:
        The string.

    Raises:
        PacketError: ``Truncated`` if the packet ends first, ``TooLong``
                     if the string passes its size, ``InvalidString`` if
                     its bytes aren't valid.
    """
    fixed, max_bits, term, tag, charset = spec
    unit = code_unit(charset)
    start = r.pos
    if fixed is not None and term is not None:
        b = r.bytes(fixed // 8, field)
        i = _find_unit(b, term, unit)
        if i >= 0:
            b = b[:i]
    elif fixed is not None:
        n = r.tag(tag, field)
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
        n = r.tag(tag, field)
        if n * 8 + tag > max_bits:
            raise PacketError("TooLong", field)
        b = r.bytes(n, field)
    return decode_text(b, charset, field)


def write_string(w: BitWriter, spec: tuple, s: str, field: str) -> None:
    """Writes a string, as :func:`read_string` reads it.

    A fixed-size string with a terminator may fill its field; otherwise
    the terminator follows it, and zeros fill the rest of a fixed size.

    Args:
        w:     The writer.
        spec:  The string's layout, as for :func:`read_string`.
        s:     The string.
        field: The field's name, for errors.

    Raises:
        PacketError: ``InvalidString`` if ``charset`` can't hold it or it
                     holds the terminator, ``TooLong`` if it doesn't fit.
    """
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
        w.tag(tag, len(b))
        w.bytes(b)
        if fixed is not None and fixed - tag - len(b) * 8:
            w.uint(fixed - tag - len(b) * 8, 0)


def write_binary(w: BitWriter, b: bytes, n: int, field: str) -> None:
    if len(b) != n:
        raise PacketError("TooLong" if len(b) > n else "OutOfRange", field)
    w.bytes(b)


def string_from_bytes(b: bytes, field: str) -> str:
    """Decodes the string that a string field encoded as binary holds.

    Args:
        b:     All of the field's bytes, which must be UTF-8.
        field: The field's name, for errors.

    Returns:
        The string.

    Raises:
        PacketError: ``InvalidString`` if the bytes aren't UTF-8.
    """
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        raise PacketError("InvalidString", field) from None


def bool_from_string(s: str, one: str, zero: str, field: str) -> bool:
    """Decodes a boolean encoded as one of two strings.

    Args:
        s:     The string read.
        one:   The string for True.
        zero:  The string for False.
        field: The field's name, for errors.

    Returns:
        The boolean.

    Raises:
        PacketError: ``OutOfRange`` for any other string.
    """
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


def write_embedded(w: BitWriter, b: bytes, max_: int, field: str) -> None:
    """Writes an embedded CCSDS packet (Yamcs:EmbeddedTc), with its packet
    length field set to its size.

    Args:
        w:     The writer.
        b:     The packet.
        max_:  The most bytes it may have.
        field: The field's name, for errors.

    Raises:
        PacketError: ``TooLong`` if ``b`` is more than ``max_`` bytes, and
                     ``BadSize`` if it is less than 7.
    """
    if len(b) > max_:
        raise PacketError("TooLong", field)
    if len(b) < 7:
        raise PacketError("BadSize", field)
    w.bytes(bytes(b[:4]) + (len(b) - 7).to_bytes(2, "big") + bytes(b[6:]))


def enum_value(cls, v: int):
    """Makes ``cls(v)`` for an IntEnum, keeping values the enumeration does not
    list.

    Args:
        cls: The IntEnum class.
        v:   The value.

    Returns:
        The member, or for a value not listed a member with no name.
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
    """Writes a string as every runtime describes it.

    Args:
        s: The string.

    Returns:
        ``s`` in double quotes, with double quotes and backslashes
        escaped by a backslash and control characters as a backslash,
        ``x`` and two hex digits.
    """
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
    """Gives the alarm level of an enumeration value.

    Args:
        x:       The value.
        labels:  The (first value, last value, level) of each alarm.
        default: The level when none holds ``x``.

    Returns:
        The level of the first in ``labels`` holding ``x``, else
        ``default``.
    """
    for lo, hi, level in labels:
        if lo <= x <= hi:
            return level
    return default


def dfa_match(dfa: tuple, data: bytes) -> bool:
    """Runs an automaton over bytes.

    Args:
        dfa:  ``(starts, trans, accept)``, as the generator's
              ``model.Dfa`` gives it.
        data: The bytes.

    Returns:
        Whether the automaton accepts ``data``.
    """
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
    """Gives the alarm level of a string.

    Args:
        s:        The string; its UTF-8 bytes are matched.
        patterns: The (automaton, level) of each alarm.
        default:  The level when it matches none.

    Returns:
        The level of the first in ``patterns`` it matches, else
        ``default``.
    """
    data = s.encode("utf-8")
    for dfa, level in patterns:
        if dfa_match(dfa, data):
            return level
    return default


class AlarmState:
    """What an alarm monitor keeps for one parameter.

    Whether it is in alarm, its runs of samples in and out of alarm, and
    its last ``history`` values and their times, for change ranges.

    Args:
        history: How many samples to remember.
    """

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
        """Gives the level to report for a sample.

        An alarm is raised after ``min_violations`` samples in a row are in
        alarm, and cleared after ``min_conformance`` in a row are not; in
        between, the last level raised stands.

        Args:
            level:           The sample's own level, 0 for none.
            min_violations:  The samples in alarm in a row that raise it.
            min_conformance: The samples out of alarm in a row that clear it.

        Returns:
            The level to report.
        """
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
        """Works out how much a sample changed, for a change range.

        Args:
            x:          The sample's value.
            t:          Its time, in seconds.
            k:          How many samples before it to compare with.
            per_second: Whether to give the change per second.
            percent:    Whether to give it as a percentage of the earlier
                        value.

        Returns:
            The change, or None when there is no such sample, or no change can
            be worked out (no time between them, or a percentage of 0).
        """
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
        """Remembers a sample, for change ranges, forgetting the oldest beyond
        ``history``.

        Args:
            x: The sample's value.
            t: Its time, in seconds.
        """
        self.values.append(x)
        self.times.append(t)
        if len(self.values) > self.history:
            del self.values[0]
            del self.times[0]


def _line_decoder(pcm: str, inverted: bool):
    """Makes a decoder from the levels of a PCM stream to data bits.

    Args:
        pcm:      The PCM type: NRZL, NRZM, NRZS, BiPhaseL, BiPhaseM or
                  BiPhaseS.
        inverted: Whether the levels are inverted.

    Returns:
        A function taking each level and giving the bit it completes, or
        None for the first half of a bi-phase bit, the stream starting on
        one.
    """
    prev = [0]
    half = [None]

    def decode(raw: int):
        """Decodes one level.

        Args:
            raw: The level, 0 or 1.

        Returns:
            The bit it completes, or None for the first half of a bi-phase
            bit.
        """
        level = raw ^ inverted
        if pcm.startswith("BiPhase"):
            first = half[0]
            if first is None:
                half[0] = level
                return None
            half[0] = None
            if pcm == "BiPhaseL":  # high then low is a one
                bit = first
            elif pcm == "BiPhaseM":  # a one changes level in mid bit
                bit = first ^ level
            else:  # a zero does
                bit = 1 ^ first ^ level
        elif pcm == "NRZM":  # a one is a change of level
            bit = level ^ prev[0]
        elif pcm == "NRZS":  # a zero is a change of level
            bit = 1 ^ level ^ prev[0]
        else:
            bit = level
        prev[0] = level
        return bit
    return decode


def _pack(bits) -> bytes:
    """Packs bits into bytes.

    Args:
        bits: The bits, most significant first.

    Returns:
        The bytes, the last padded with zeros.
    """
    out = bytearray((len(bits) + 7) // 8)
    for i, b in enumerate(bits):
        out[i >> 3] |= b << (7 - (i & 7))
    return bytes(out)


class FrameSync:
    """Finds the frames of a FixedFrameStream in a stream of bytes.

    It uses the state machine XTCE describes: in Search, it looks for the
    sync pattern at every bit; in Verify, it needs ``verify`` frames in a
    row whose patterns are where the frame length (give or take
    ``aperture`` bits) says, then it locks; in Lock, it gives each frame
    whose pattern it found, and a missed pattern sends it to Check, where
    ``check`` good patterns in a row lock it again and a missed one sends
    it back to Search.  Searching ``invert_after`` bits (when not 0)
    without finding a pattern inverts the bits.  See the generator's
    ``model.Stream``.

    Args:
        frame_bits:   The frame's length in bits.
        pattern:      The sync pattern.
        mask:         The pattern's bits that must match, as set bits.
        pattern_bits: The pattern's length in bits.
        location:     Where the pattern is, in bits from the frame's start.
        max_errors:   The most bits of the pattern that may be wrong.
        aperture:     How many bits a pattern may be from where it should.
        verify:       The patterns in a row that lock from Search.
        check:        The patterns in a row that lock again from Check.
        invert_after: The bits searched without a pattern that invert the
                      stream, or 0 never to invert it.
        pcm:          The stream's PCM type, as for ``_line_decoder``.
        inverted:     Whether its levels are inverted.
    """

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
        # An InvertAlgorithm: what a bit is once AutoInvert has inverted the
        # stream, in place of its complement.
        self.invert = None

    def push(self, data) -> list:
        """Finds the frames that the next bytes of the stream complete.

        Args:
            data: The bytes.

        Returns:
            The frames, each as bytes (its bits padded with zeros).
        """
        frames: list = []
        for byte in data:
            for k in range(7, -1, -1):
                b = self.decode((byte >> k) & 1)
                if b is not None:
                    self.bits.append(b)
            self._run(frames)
        return frames

    def _bit(self, i: int) -> int:
        """Gives a bit of the stream, inverted if the stream has been.

        An inverted stream's bits go through :attr:`invert` when there is
        one, in place of their complement.

        Args:
            i: The bit's position in the stream.

        Returns:
            The bit.
        """
        b = self.bits[i - self.base]
        if self.flip and self.invert is not None:
            return self.invert(b)
        return b ^ self.flip

    def _matches(self, s: int) -> bool:
        """Checks for the sync pattern in the frame at a position.

        Args:
            s: Where the frame starts, in bits in the stream.

        Returns:
            Whether the pattern is there, with at most ``max_errors`` wrong.
        """
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
        """Runs the state machine over the bits held, as far as they go.

        Drops the bits it no longer needs.

        Args:
            frames: Gets each frame found.
        """
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
                    # After the current frame's pattern, which a wide
                    # aperture could reach back to.
                    if (found is None and nxt + e > self.cur
                            and self._matches(nxt + e)):
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
    """Finds the frames of a VariableFrameStream in a stream of bytes.

    Frames lie between flags, each ``flag_bits`` ones (or zeros, unless
    ``flag_ones``) with a bit of the other kind on either side, and a bit
    of the other kind is stuffed into a frame after ``flag_bits - 1`` in a
    row, which this removes.  More than ``flag_bits`` in a row abort the
    frame, as does a frame of more than ``max_bits``; frames are found
    again from the next flag.  See the generator's ``model.Stream``.

    Args:
        flag_bits: The flag's run of bits.
        flag_ones: Whether the run is of ones, else zeros.
        max_bits:  The most bits a frame may have.
        pcm:       The stream's PCM type, as for ``_line_decoder``.
        inverted:  Whether its levels are inverted.
    """

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
        """Finds the frames that the next bytes of the stream complete.

        Args:
            data: The bytes.

        Returns:
            The frames, each as bytes (its bits padded with zeros).
        """
        frames: list = []
        for byte in data:
            for k in range(7, -1, -1):
                b = self.decode((byte >> k) & 1)
                if b is not None:
                    self._bit(b, frames)
        return frames

    def _bit(self, b: int, frames: list) -> None:
        """Takes one data bit: ends a frame at a flag, drops stuffed bits, and
        aborts a frame too long or with too long a run.

        Args:
            b:      The bit.
            frames: Gets the frame a flag ends.
        """
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


def within(data: bytes, read):
    """What ``read`` reads from a BitReader over ``data``: a copy of a
    repeated container, from the bytes its transform gives."""
    return read(BitReader(data))


def checked(value, ok, container: str, text: str):
    """``value``, a copy of a repeated container, if ``ok(value)``; raises
    Mismatch naming ``container`` and the criterion ``text`` otherwise."""
    if not ok(value):
        raise PacketError("Mismatch", container, text)
    return value


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
    """Finds an enumeration value's label.

    Args:
        v:      The value.
        labels: The (label, first value, last value) of each label.

    Returns:
        The first label whose range holds ``v``, or None.
    """
    for label, lo, hi in labels:
        if lo <= v <= hi:
            return label
    return None
