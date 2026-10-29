"""Prints the integer cases that tests/bnum_check.sh gives the bnum crate
(the reference), xtce/bnum.h and xtce/bnum.hpp: every operation on every
size, with each type's edge values and random ones, in the format
rust/examples/bnum_tool.rs describes.  With --c, only the operations that
the C functions have; the C++ class adds the rest.

Usage: python3 tests/bnum_cases.py SEED COUNT [--c]
"""

import random
import struct
import sys

SIZES = (1, 2, 3, 8, 16, 17, 24, 32)

# Operations by their operands: two integers, one, or one and a u32 (a
# shift, power or radix).
BINARY = ["wrapping_add", "wrapping_sub", "wrapping_mul", "overflowing_add",
          "overflowing_sub", "overflowing_mul", "checked_add", "checked_sub",
          "checked_mul", "checked_div", "checked_rem", "checked_div_euclid",
          "checked_rem_euclid", "saturating_add", "saturating_sub",
          "saturating_mul", "abs_diff", "bitand", "bitor", "bitxor", "cmp",
          "min", "max"]
# These panic in bnum when dividing by zero, so the divisor isn't.
DIVIDE = ["wrapping_div", "wrapping_rem", "wrapping_div_euclid",
          "wrapping_rem_euclid", "overflowing_div", "overflowing_rem"]
UNARY = ["wrapping_neg", "overflowing_neg", "checked_neg", "not", "count_ones",
         "count_zeros", "leading_zeros", "leading_ones", "trailing_zeros",
         "trailing_ones", "swap_bytes", "reverse_bits", "to_be_bytes"]
SIGNED = ["wrapping_abs", "overflowing_abs", "checked_abs", "saturating_abs",
          "saturating_neg", "unsigned_abs", "signum"]
UNSIGNED = ["is_power_of_two", "bit_width"]
SHIFT = ["wrapping_shl", "wrapping_shr", "overflowing_shl", "overflowing_shr",
         "checked_shl", "checked_shr", "unbounded_shl", "unbounded_shr",
         "rotate_left", "rotate_right"]
# Only in the C++ class.
POW = ["wrapping_pow", "overflowing_pow", "checked_pow", "saturating_pow"]
CXX_UNARY = ["isqrt", "checked_ilog2", "checked_ilog10"]


def limits(signed: bool, n: int) -> tuple[int, int]:
    bits = 8 * n
    return ((-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if signed
            else (0, (1 << bits) - 1))


def values(rng: random.Random, signed: bool, n: int) -> list[int]:
    """Edge values of a type, and random ones of every magnitude.

    Args:
        rng:    The random number generator.
        signed: Whether the type is signed.
        n:      The type's size in bytes.

    Returns:
        The edge values, sorted, then 40 or fewer random ones, all in the
        type's range.
    """
    lo, hi = limits(signed, n)
    edges = {0, 1, 2, 3, 7, 10, hi, hi - 1, lo, lo + 1, hi // 2, hi // 3}
    if signed:
        edges |= {-1, -2, -3, -7, -10, lo // 2}
    for k in range(0, 8 * n, 7):
        edges |= {1 << k, (1 << k) - 1, (1 << k) + 1}
        if signed:
            edges |= {-(1 << k), -(1 << k) - 1}
    edges = {v for v in edges if lo <= v <= hi}
    out = sorted(edges)
    for _ in range(40):
        bits = rng.randint(1, 8 * n)
        v = rng.getrandbits(bits)
        if signed and rng.random() < 0.5:
            v = -v
        if lo <= v <= hi:
            out.append(v)
    return out


def slice_hex(rng: random.Random, signed: bool, n: int) -> str:
    """Bytes for from_le_slice and from_be_slice.

    They are shorter than, as long as or longer than the type, and the
    bytes past its size are often a sign or zero extension.

    Args:
        rng:    The random number generator.
        signed: Whether the type is signed.
        n:      The type's size in bytes.

    Returns:
        The bytes in hex, or "x" when there are none.
    """
    length = rng.randint(0, n + 3)
    b = bytearray(rng.getrandbits(8) for _ in range(length))
    if length > n and rng.random() < 0.7:
        fill = rng.choice([0, 0xff])
        for i in range(n, length):
            b[i] = fill
    # Spelt "x" when empty, so that the case keeps its argument.
    return b.hex() or "x"


def bnum_slice_bug(h: str, signed: bool, n: int, le: bool) -> bool:
    """Tells whether bnum 0.14 reads a slice wrongly.

    It does for a negative slice longer than the type, whose extra bytes
    are all 0xff but whose kept top byte isn't negative: it returns the
    truncated bytes instead of None.

    Args:
        h:      The slice in hex, as slice_hex gives it.
        signed: Whether the type is signed.
        n:      The type's size in bytes.
        le:     Whether the slice is little endian.

    Returns:
        True if bnum reads the slice wrongly.
    """
    b = bytes.fromhex(h.strip("x"))
    if not signed or len(b) <= n:
        return False
    if not le:
        b = b[::-1]
    return (b[-1] & 0x80 and all(x == 0xff for x in b[n:])
            and not b[n - 1] & 0x80)


def double_bits(rng: random.Random, bits: int) -> str:
    """A double's bits for from_f64.

    The double is a whole number near a power of two up to past the
    type's size, of either sign, a half or other fraction, or a special
    value.

    Args:
        rng:  The random number generator.
        bits: The type's size in bits.

    Returns:
        The double's bits in hex, big endian.
    """
    kind = rng.random()
    if kind < 0.1:
        x = rng.choice([0.0, -0.0, 0.5, -0.5, 0.75, 1.5, 2.5, -2.5, 1e-310,
                        float("inf"), -float("inf"), float("nan"),
                        -float("nan"), 1.7976931348623157e308])
    elif kind < 0.8:
        k = rng.randint(0, bits + 2)
        x = float(rng.choice([1 << k, (1 << k) - 1, (1 << k) + 1,
                              rng.getrandbits(k + 1)]))
    else:
        x = rng.uniform(-4, 4) * 2.0 ** rng.randint(-3, bits + 2)
    if rng.random() < 0.5:
        x = -x
    return struct.pack(">d", x).hex()


def radix_text(rng: random.Random, signed: bool, n: int, radix: int) -> str:
    """Text for from_str_radix to parse.

    It is mostly digits of ``radix``, of either case, sometimes signed,
    and now and then empty, a bad sign or ending in a bad character.

    Args:
        rng:    The random number generator.
        signed: Whether the type is signed (unused).
        n:      The type's size in bytes, which bounds the digit count.
        radix:  The radix, 2 to 36.

    Returns:
        The text.
    """
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"[:radix]
    kind = rng.random()
    if kind < 0.05:
        return ""  # nothing after the radix
    if kind < 0.1:
        return rng.choice(["+", "-", "-+1", "1-", "_1"])
    s = "".join(rng.choice(digits) for _ in range(rng.randint(1, 9 * n)))
    if rng.random() < 0.2:
        s = s.upper()
    if rng.random() < 0.05:
        s += rng.choice("!z#")
    if rng.random() < 0.3:
        s = rng.choice("+-") + s
    return s


def main() -> None:
    """Prints COUNT random cases of each operation's type for every size and
    signedness, seeded with SEED, as the module docstring describes.
    """
    rng = random.Random(int(sys.argv[1]))
    count = int(sys.argv[2])
    c_only = "--c" in sys.argv[3:]
    out = []
    for n in SIZES:
        for signed in (False, True):
            t = f"{'i' if signed else 'u'}{n}"
            vs = values(rng, signed, n)
            bits = 8 * n

            def pick():
                return rng.choice(vs)
            ops = BINARY + DIVIDE + UNARY + SHIFT
            ops += SIGNED if signed else UNSIGNED
            if not c_only:
                ops += POW + CXX_UNARY
            ops += ["to_str_radix", "from_str_radix", "cast", "from_le_slice",
                    "from_be_slice", "to_f64", "from_f64"]
            for _ in range(count):
                op = rng.choice(ops)
                if op in BINARY:
                    out.append(f"{op} {t} {pick()} {pick()}")
                elif op in DIVIDE:
                    d = pick() or 1
                    out.append(f"{op} {t} {pick()} {d}")
                elif op in SHIFT:
                    out.append(f"{op} {t} {pick()} "
                               f"{rng.randint(0, 2 * bits + 1)}")
                elif op in POW:
                    base = rng.choice([pick(),
                                       rng.randint(-3, 3) if signed
                                       else rng.randint(0, 3)])
                    out.append(f"{op} {t} {base} {rng.randint(0, 2 * bits)}")
                elif op == "isqrt":
                    # bnum panics on a negative value.
                    v = min(abs(pick()), limits(signed, n)[1])
                    out.append(f"{op} {t} {v}")
                elif op == "to_str_radix":
                    out.append(f"{op} {t} {pick()} {rng.randint(2, 36)}")
                elif op == "from_str_radix":
                    radix = rng.randint(2, 36)
                    out.append(f"{op} {t} {radix} "
                               f"{radix_text(rng, signed, n, radix)}".rstrip())
                elif op == "cast":
                    to = (f"{rng.choice('ui')}{rng.choice(SIZES)}")
                    out.append(f"{op} {t} {pick()} {to}")
                elif op == "from_f64":
                    out.append(f"{op} {t} {double_bits(rng, bits)}")
                elif op in ("from_le_slice", "from_be_slice"):
                    h = slice_hex(rng, signed, n)
                    if bnum_slice_bug(h, signed, n, op == "from_le_slice"):
                        continue
                    out.append(f"{op} {t} {h}")
                else:
                    out.append(f"{op} {t} {pick()}")
    print("\n".join(out))


if __name__ == "__main__":
    main()
