"""Converts floats as ``tests/float_check.sh`` asks, one case per line of
standard input: ``d FORMAT BITS RAW`` decodes the raw value (hex) and prints
the double's bits, and ``e FORMAT BITS DOUBLE`` encodes the double (its bits
in hex) and prints the raw value; an error prints its kind.  ``m OP A B``
applies a MathOperationCalibrator operator to two doubles (their bits in
hex) and prints the result's bits."""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xtce.packet import (  # noqa: E402
    PacketError, float_decode, float_encode, math_op)


def bits(x: float) -> str:
    """The bits of ``x``; every runtime prints the same NaN."""
    if x != x:
        return "7ff8000000000000"
    return struct.pack(">d", x).hex().lstrip("0") or "0"


def double(h: str) -> float:
    return struct.unpack(">d", int(h, 16).to_bytes(8, "big"))[0]


for line in sys.stdin:
    words = line.split()
    if words[0] == "m":
        print(bits(math_op(words[1], double(words[2]), double(words[3]))))
        continue
    op, fmt, size, v = words
    size, v = int(size), int(v, 16)
    try:
        if op == "d":
            out = bits(float_decode(fmt, size, v, "x"))
        else:
            x = struct.unpack(">d", v.to_bytes(8, "big"))[0]
            out = f"{float_encode(fmt, size, x, 'x'):x}"
    except PacketError as e:
        out = f"error:{e.kind}"
    print(out)
