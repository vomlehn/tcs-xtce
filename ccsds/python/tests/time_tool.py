"""Decodes and encodes CCSDS time codes as tests/ccsds_check.sh asks, one
case per line of standard input; ccsds/rust/examples/time_tool.rs describes
the cases."""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tcs_ccsds.calendar import (  # noqa: E402
    AsciiForm, CalendarTime, Ccs, decode_ccs, format_ascii, parse_ascii)
from tcs_ccsds.utc import TaiTime, tai_to_utc, utc_to_tai  # noqa: E402
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


def cal(w) -> CalendarTime:
    """Reads a calendar time's seven fields from words."""
    return CalendarTime(*(int(x) for x in w[:7]))


def show(t: CalendarTime) -> str:
    """Writes a calendar time's seven fields."""
    return (f"{t.year} {t.month} {t.day} {t.hour} {t.minute} {t.second} "
            f"{t.picos}")


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
        if op == "ccs_decode":
            r = decode_ccs(unhex(w[1]))
            print("none" if r is None else f"{r[0]} {show(r[1])}")
        elif op == "ccs_encode":
            c = Ccs(w[1] == "1", int(w[2]))
            t = cal(w[3:10])
            r = c.encode(t) if w[10] == "1" else c.encode_t(t)
            print(encoded(r, int(w[11])))
        elif op == "ascii_parse":
            r = parse_ascii(w[1])
            print("none" if r is None else f"{r[0]} {r[1].name} {show(r[2])}")
        elif op == "ascii_format":
            r = format_ascii(cal(w[4:11]), AsciiForm[w[1]], int(w[2]),
                             w[3] == "1")
            cap = int(w[11])
            print("none" if r is None else f"needs {len(r)}" if len(r) > cap
                  else r)
        elif op == "utc_to_tai":
            r = utc_to_tai(cal(w[1:8]))
            print("none" if r is None else f"{r.seconds} {r.picos}")
        elif op == "tai_to_utc":
            r = tai_to_utc(TaiTime(int(w[1]), int(w[2])))
            print("none" if r is None else show(r))
        elif op == "cuc_decode_x":
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
