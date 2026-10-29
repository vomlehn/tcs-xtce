"""Decodes and encodes CCSDS time codes as tests/ccsds_check.sh asks, one
case per line of standard input; ccsds/rust/examples/time_tool.rs describes
the cases."""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tcs_ccsds.time import (  # noqa: E402
    Cds, Cuc, Epoch, SubMs, decode_cds, decode_cuc)


def unhex(s: str) -> bytes:
    return bytes.fromhex(s[:len(s) // 2 * 2])


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
        if op == "cuc_decode":
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
