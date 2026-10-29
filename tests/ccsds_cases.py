"""Prints the random time code cases that tests/ccsds_check.sh gives every
version of the time tool: decoding random octets (often valid P-fields) and
encoding random times (often at the edges of a layout), in the format
ccsds/rust/examples/time_tool.rs describes.

Usage: python3 tests/ccsds_cases.py SEED COUNT
"""

import math
import random
import struct
import sys


def bits(x: float) -> str:
    return f"{struct.unpack('>Q', struct.pack('>d', x))[0]:016x}"


def hexs(b: bytes) -> str:
    return b.hex() or "x"


def cuc_pfield(rng) -> bytes:
    """A random CUC P-field, often valid.

    Args:
        rng: The random number generator.

    Returns:
        One octet, or two when the coarse or fine time is too long for one
        or now and then anyway; the second sometimes has reserved bits set.
    """
    coarse, fine = rng.randint(1, 7), rng.randint(0, 10)
    ident = rng.choice([1, 2, 1, 2, rng.randrange(8)])
    c, f = min(coarse, 4), min(fine, 3)
    more = coarse > 4 or fine > 3 or rng.random() < 0.1
    first = (more << 7) | (ident << 4) | ((c - 1) << 2) | f
    if not more:
        return bytes([first])
    second = ((coarse - c) << 5) | ((fine - f) << 2)
    if rng.random() < 0.05:
        second |= rng.choice([0x80, 0x01, 0x02])
    return bytes([first, second])


def seconds(rng, limit: float) -> float:
    """A random time to encode, often at the edge of a layout's range.

    Args:
        rng:   The random number generator.
        limit: The first time, in seconds, too large for the layout.

    Returns:
        The time in seconds; it may be negative, infinite or NaN.
    """
    return rng.choice(
        [0.0, 1.0, 0.5, limit, limit - 1.0, limit - 0.0001, limit * 2, -1.0,
         -0.0, math.inf, math.nan, rng.random(), rng.random() * limit,
         float(rng.randrange(int(min(limit, 2**53)))) + rng.random(),
         rng.randrange(1000) + rng.choice([0.999, 0.9999996, 0.001, 0.5])])


def main() -> None:
    """Prints COUNT random cases, seeded with SEED, as the module docstring
    describes.
    """
    rng = random.Random(int(sys.argv[1]))
    out = []
    for _ in range(int(sys.argv[2])):
        kind = rng.randrange(6)
        cap = rng.choice([0, 3, 7, 12, 32])
        if kind == 0:
            out.append("cuc_decode "
                       + hexs(cuc_pfield(rng)
                              + rng.randbytes(rng.randrange(20))))
        elif kind == 1:
            c, f = rng.randint(1, 7), rng.randint(0, 10)
            out.append(f"cuc_decode_t {c} {f} {rng.randrange(2)} "
                       + hexs(rng.randbytes(rng.randrange(20))))
        elif kind == 2:
            c, f = rng.randint(1, 7), rng.randint(0, 10)
            s = seconds(rng, float(1 << (8 * c)))
            out.append(f"cuc_encode {c} {f} {rng.randrange(2)} {bits(s)} "
                       f"{rng.randrange(2)} {cap}")
        elif kind == 3:
            p = rng.choice([0x40, 0x41, 0x42, 0x44, 0x45, 0x46, 0x48, 0x4d,
                            0x43, 0x80 | 0x40, rng.randrange(256)])
            out.append("cds_decode "
                       + hexs(bytes([p]) + rng.randbytes(rng.randrange(12))))
        elif kind == 4:
            out.append(f"cds_decode_t {rng.randrange(2)} {rng.randrange(3)} "
                       f"{rng.randrange(2)} "
                       + hexs(rng.randbytes(rng.randrange(12))))
        else:
            d24 = rng.randrange(2)
            s = seconds(rng, float(1 << (24 if d24 else 16)) * 86400.0)
            out.append(f"cds_encode {d24} {rng.randrange(3)} "
                       f"{rng.randrange(2)} {bits(s)} {rng.randrange(2)} {cap}")
    print("\n".join(out))


if __name__ == "__main__":
    main()
