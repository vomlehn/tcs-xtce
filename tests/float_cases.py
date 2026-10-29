"""Prints the float conversion cases that tests/float_check.sh gives every
runtime: random raw values of every format and size to decode, random
doubles (and special ones) to encode, MathOperationCalibrator operators on
special and random operands, and ErrorDetectCorrect checksums of random
data, in the float tools' input format.

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


OPERATORS = ["/", "%", "^", "y^x", "ln", "log", "e^x", "1/x", "x!", "tan",
             "cos", "sin", "atan", "atan2", "acos", "asin", "tanh", "cosh",
             "sinh", "atanh", "acosh", "asinh", "<<", ">>", "&", "|", "xor",
             "~", "&&", "||", "!", "abs", "div", "int", ">", ">=", "<", "<=",
             "==", "!=", "min", "max"]
OPERANDS = [0.0, -0.0, 1.0, -1.0, 2.0, -3.0, 0.5, 7.0, -7.0, 3.5, 63.0, 64.0,
            170.0, 171.0, 1e300, -1e300, math.inf, -math.inf, math.nan, 9e18,
            -9.3e18, 1e-310]


def bits_of(x: float) -> str:
    return struct.pack(">d", x).hex()


def main() -> None:
    """Prints the decoding and encoding cases for every format and size, the
    calibrator operator cases and COUNT checksum cases, seeded with SEED.
    """
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
    for op in OPERATORS:
        for a in OPERANDS:
            for b in OPERANDS:
                print(f"m {op} {bits_of(a)} {bits_of(b)}")
        for _ in range(count // 10):
            a, b = (rng.uniform(-20, 20) for _ in range(2))
            print(f"m {op} {bits_of(a)} {bits_of(b)}")
    for _ in range(count):
        print(checksum_case(rng))


CHECKSUMS = {"crc": None, "sum": (8, 16, 24, 32), "unix_sum": (16,),
             "fletcher": (4, 8, 16, 32), "adler32": (32,), "luhn": (4,),
             "verhoeff": (4,), "damm": (4,), "xor": (1, 4, 8, 16, 32, 64),
             "parity": (1,)}


def checksum_case(rng) -> str:
    """A checksum case: a random checksum of random data, between random bits
    of it.

    Args:
        rng: The random number generator.

    Returns:
        The case as a ``k`` line of the float tools' input.
    """
    kind = rng.choice(list(CHECKSUMS))
    n = rng.randrange(1, 40)
    if kind in ("luhn", "verhoeff", "damm"):
        # Decimal digits, now and then one above 9.
        data = bytes(rng.randrange(10) << 4 | rng.randrange(10)
                     for _ in range(n))
        if rng.random() < 0.1:
            data = data[:-1] + bytes([0xAB])
    else:
        data = rng.randbytes(n)
    start = rng.randrange(n * 8)
    end = rng.randrange(start, n * 8 + 1)
    bits = (rng.randrange(1, 65) if kind == "crc"
            else rng.choice(CHECKSUMS[kind]))
    poly, init, xor = (rng.getrandbits(bits) for _ in range(3))
    flags = " ".join(str(rng.randrange(2)) for _ in range(3))
    return (f"k {kind} {bits:x} {poly:x} {init:x} {xor:x} {flags} {data.hex()} "
            f"{start:x} {end:x}")


if __name__ == "__main__":
    main()
