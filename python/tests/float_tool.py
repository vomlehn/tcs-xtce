"""Converts floats as ``tests/float_check.sh`` asks, one case per line of
standard input: ``d FORMAT BITS RAW`` decodes the raw value (hex) and prints
the double's bits, and ``e FORMAT BITS DOUBLE`` encodes the double (its bits
in hex) and prints the raw value; an error prints its kind."""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xtce.packet import PacketError, float_decode, float_encode  # noqa: E402

for line in sys.stdin:
    op, fmt, bits, v = line.split()
    bits, v = int(bits), int(v, 16)
    try:
        if op == "d":
            x = float_decode(fmt, bits, v, "x")
            out = struct.pack(">d", x).hex().lstrip("0") or "0"
            if x != x:  # every runtime gives the same NaN
                out = "7ff8000000000000"
        else:
            x = struct.unpack(">d", v.to_bytes(8, "big"))[0]
            out = f"{float_encode(fmt, bits, x, 'x'):x}"
    except PacketError as e:
        out = f"error:{e.kind}"
    print(out)
