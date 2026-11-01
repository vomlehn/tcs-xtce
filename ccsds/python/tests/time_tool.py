"""Decodes and encodes CCSDS time codes as tests/ccsds_check.sh asks, one
case per line of standard input; ccsds/rust/examples/time_tool.rs describes
the cases."""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tcs_ccsds.time import (  # noqa: E402
    Cds, CdsTime, Cuc, CucTime, Epoch, SubMs, decode_cds, decode_cds_exact,
    decode_cuc, decode_cuc_exact)


def unhex(s: str) -> bytes:
    return bytes.fromhex(s[:len(s) // 2 * 2])


def float_bits(x: float) -> str:
    return f"{struct.unpack('>Q', struct.pack('>d', x))[0]:016x}"


def encoded(r, cap: int) -> str:
    """Formats an encoding's result.

    Args:
        r:   The octets, or None for a time the layout can't hold.
        cap: The octets there was room for.

    Returns:
        The octets in hex (``x`` for none), ``needs N``, or ``none``.
    """
    return ("none" if r is None else f"needs {len(r)}" if len(r) > cap
            else r.hex() or "x")


def decoded(r) -> str:
    if r is None:
        return "none"
    return f"{r[0]} {struct.unpack('>Q', struct.pack('>d', r[1]))[0]:016x}"


def main() -> None:
    """Runs each case on standard input and prints its result.

    Blank lines are skipped.  Each result is ``none`` when the operation
    fails, a decoding's size and value bits in hex, or an encoding's bytes
    in hex (``x`` when there are none) or ``needs N`` when it is longer
    than the case's capacity.
    """
    for line in sys.stdin:
        w = line.split()
        if not w:
            continue
        op = w[0]
        if op == "cuc_decode_x":
            r = decode_cuc_exact(unhex(w[1]))
            print("none" if r is None
                  else f"{r[0]} {r[1].seconds:016x} {r[1].fraction:020x}")
        elif op == "cds_decode_x":
            r = decode_cds_exact(unhex(w[1]))
            print("none" if r is None
                  else f"{r[0]} {r[1].day} {r[1].ms} {r[1].picos}")
        elif op == "cuc_seconds":
            t = CucTime(int(w[1], 16), int(w[2], 16))
            print(float_bits(t.to_seconds()))
        elif op == "cds_seconds":
            t = CdsTime(int(w[1]), int(w[2]), int(w[3]))
            print(float_bits(t.to_seconds()))
        elif op == "cuc_encode_x":
            c = Cuc(int(w[1]), int(w[2]), Epoch(int(w[3])))
            t = CucTime(int(w[4], 16), int(w[5], 16))
            r = c.encode_exact(t) if w[6] == "1" else c.encode_t_exact(t)
            print(encoded(r, int(w[7])))
        elif op == "cds_encode_x":
            c = Cds(w[1] == "1", SubMs(int(w[2])), Epoch(int(w[3])))
            t = CdsTime(int(w[4]), int(w[5]), int(w[6]))
            r = c.encode_exact(t) if w[7] == "1" else c.encode_t_exact(t)
            print(encoded(r, int(w[8])))
        elif op == "cuc_decode":
            print(decoded(decode_cuc(unhex(w[1]))))
        elif op == "cds_decode":
            print(decoded(decode_cds(unhex(w[1]))))
        elif op == "cuc_decode_t":
            c = Cuc(int(w[1]), int(w[2]), Epoch(int(w[3])))
            print(decoded(c.decode_t(unhex(w[4]))))
        elif op == "cds_decode_t":
            c = Cds(w[1] == "1", SubMs(int(w[2])), Epoch(int(w[3])))
            print(decoded(c.decode_t(unhex(w[4]))))
        else:
            if op == "cuc_encode":
                c = Cuc(int(w[1]), int(w[2]), Epoch(int(w[3])))
            else:
                c = Cds(w[1] == "1", SubMs(int(w[2])), Epoch(int(w[3])))
            s = struct.unpack(">d", struct.pack(">Q", int(w[4], 16)))[0]
            r = c.encode(s) if w[5] == "1" else c.encode_t(s)
            cap = int(w[6])
            print("none" if r is None else f"needs {len(r)}" if len(r) > cap
                  else r.hex() or "x")


if __name__ == "__main__":
    main()
