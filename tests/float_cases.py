"""Prints the float conversion cases that tests/float_check.sh gives every
runtime: random raw values of every format and size to decode, and random
doubles (and special ones) to encode, in the float tools' input format.

Usage: python3 tests/float_cases.py SEED COUNT
"""

import math
import random
import struct
import sys

SIZES = {"IEEE754": (16, 32, 64, 80, 128), "MILSTD_1750A": (32, 48),
         "DEC": (32, 64), "IBM": (32, 64, 128), "TI": (16, 32, 40)}
SPECIAL = [0.0, -0.0, 1.0, -1.0, 0.5, -0.5, 2.0, -2.0, 1 / 3, -0.1, math.inf,
           -math.inf, math.nan, 5e-324, 2.2250738585072014e-308,
           1.7976931348623157e308, 65504.0, 65520.0, 3.4028235e38, 1e-40,
           7.2e75, 1.7e38, -2.0**127, 2.0**-129, 1e-300, 1e300]


def bits_of(x: float) -> str:
    return struct.pack(">d", x).hex()


def main() -> None:
    rng = random.Random(int(sys.argv[1]))
    count = int(sys.argv[2])
    for fmt, sizes in SIZES.items():
        for bits in sizes:
            for x in SPECIAL:
                print(f"e {fmt} {bits} {bits_of(x)}")
            for _ in range(count):
                print(f"d {fmt} {bits} {rng.getrandbits(bits):x}")
                if rng.random() < 0.5:
                    x = struct.unpack(">d",
                                      rng.getrandbits(64).to_bytes(8, "big"))[0]
                else:
                    x = math.ldexp(rng.random() + 0.5, rng.randrange(-300, 300))
                    x = -x if rng.random() < 0.5 else x
                print(f"e {fmt} {bits} {bits_of(x)}")


if __name__ == "__main__":
    main()
