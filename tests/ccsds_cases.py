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


def cuc_time(rng, coarse: int) -> str:
    """A random exact CUC time, often at an edge: a fraction just under,
    at or over half a unit of some number of fine octets, or all ones.

    Args:
        rng:    The random number generator.
        coarse: The coarse octets of the layout it is for.

    Returns:
        Its seconds and fraction in hex, as the tool reads them.
    """
    top = (1 << (8 * coarse)) - 1
    secs = rng.choice([0, 1, top, top - 1, rng.randrange(top + 1),
                       rng.randrange(1 << 64)])
    f = rng.randint(1, 10)
    half = 1 << (80 - 8 * f - 1) if f < 10 else 1
    frac = rng.choice([0, (1 << 80) - 1, half, half - 1, half + 1,
                       rng.randrange(1 << 80),
                       (rng.randrange(1 << (8 * f)) << (80 - 8 * f))
                       | rng.choice([0, half, half - 1])])
    return f"{secs:016x} {frac:020x}"


def cds_time(rng, d24: int) -> str:
    """A random exact CDS time, often at an edge of a field, a rounding or a
    leap second, and now and then out of range.

    Args:
        rng: The random number generator.
        d24: Whether the layout it is for has 24-bit days.

    Returns:
        Its day, milliseconds and picoseconds in decimal.
    """
    top = (1 << (24 if d24 else 16)) - 1
    day = rng.choice([0, 1, top, top + 1, rng.randrange(top + 1)])
    ms = rng.choice([0, 86_399_999, 86_400_000, 86_400_999, 86_401_000,
                     rng.randrange(86_401_000)])
    picos = rng.choice([0, 499_999, 500_000, 499_999_999, 500_000_000,
                        999_999_999, 999_500_000, 1_000_000_000,
                        rng.randrange(10**9), rng.randrange(1000) * 10**6])
    return f"{day} {ms} {picos}"


def calendar(rng) -> str:
    """A random calendar time, often at an edge: a leap day or a day that
    does not exist, a leap second or a second of 60 that is not one, the
    last picosecond before a carry, the ends of the years and of the
    leap-second table, and now and then a field out of range.

    Args:
        rng: The random number generator.

    Returns:
        Its year, month, day, hour, minute, second and picoseconds.
    """
    year = rng.choice([1, 1958, 1971, 1972, 1999, 2000, 2016, 2024, 2027, 9999,
                       0, 10000, rng.randint(1972, 2026), rng.randint(1, 9999)])
    month = rng.choice([1, 2, 6, 7, 12, 0, 13, rng.randint(1, 12)])
    day = rng.choice([1, 28, 29, 30, 31, 0, 32, rng.randint(1, 31)])
    hour = rng.choice([0, 23, 24, rng.randrange(24)])
    minute = rng.choice([0, 59, 60, rng.randrange(60)])
    second = rng.choice([0, 59, 60, 61, rng.randrange(60)])
    if rng.random() < 0.3:
        # The end of a June or December, where leap seconds go.
        month, day, hour, minute = rng.choice([(6, 30), (12, 31)]) + (23, 59)
        second = rng.choice([59, 60])
    picos = rng.choice([0, 999_999_999_999, 500_000_000_000, 499_999_999_999,
                        10**12, rng.randrange(10**12),
                        rng.randrange(10**6) * 10**6])
    return f"{year} {month} {day} {hour} {minute} {second} {picos}"


def ascii_text(rng) -> str:
    """A random ASCII time code, often valid, now and then cut short, given
    too many fraction digits, or with a character out of place.

    Args:
        rng: The random number generator.

    Returns:
        The text, which has no white space.
    """
    year = rng.choice([2016, 2024, 1, 9999, rng.randint(1, 9999)])
    if rng.randrange(2):
        date = f"{year:04d}-{rng.randint(0, 13):02d}-{rng.randint(0, 32):02d}"
    else:
        date = f"{year:04d}-{rng.randint(0, 367):03d}"
    text = (f"{date}T{rng.choice([23, rng.randrange(25)]):02d}:"
            f"{rng.choice([59, rng.randrange(61)]):02d}:"
            f"{rng.choice([59, 60, rng.randrange(62)]):02d}")
    if rng.randrange(2):
        text += "." + "".join(str(rng.randrange(10))
                              for _ in range(rng.randint(0, 14)))
    text += rng.choice(["", "Z", "Zx", "x"])
    if rng.random() < 0.1:
        text = text[:rng.randrange(len(text))] or "x"
    if rng.random() < 0.05:
        i = rng.randrange(len(text))
        text = text[:i] + rng.choice("-T:.Za") + text[i + 1:]
    return text or "x"


def main() -> None:
    """Prints COUNT random cases, seeded with SEED, as the module docstring
    describes.
    """
    rng = random.Random(int(sys.argv[1]))
    out = []
    for _ in range(int(sys.argv[2])):
        kind = rng.randrange(16)
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
        elif kind == 5:
            d24 = rng.randrange(2)
            s = seconds(rng, float(1 << (24 if d24 else 16)) * 86400.0)
            out.append(f"cds_encode {d24} {rng.randrange(3)} "
                       f"{rng.randrange(2)} {bits(s)} {rng.randrange(2)} {cap}")
        elif kind == 6:
            # Decoding exactly, from the same octets the others decode.
            if rng.randrange(2):
                data = cuc_pfield(rng) + rng.randbytes(rng.randrange(20))
                out.append("cuc_decode_x " + hexs(data))
            else:
                p = rng.choice([0x40, 0x41, 0x42, 0x44, 0x45, 0x46, 0x48, 0x4d,
                                rng.randrange(256)])
                data = bytes([p]) + rng.randbytes(rng.randrange(12))
                out.append("cds_decode_x " + hexs(data))
        elif kind == 7:
            c, f = rng.randint(1, 7), rng.randint(0, 10)
            out.append(f"cuc_encode_x {c} {f} {rng.randrange(2)} "
                       f"{cuc_time(rng, c)} {rng.randrange(2)} {cap}")
        elif kind == 8:
            d24 = rng.randrange(2)
            out.append(f"cds_encode_x {d24} {rng.randrange(3)} "
                       f"{rng.randrange(2)} {cds_time(rng, d24)} "
                       f"{rng.randrange(2)} {cap}")
        elif kind == 9:
            if rng.randrange(2):
                out.append(f"cuc_seconds {cuc_time(rng, 7)}")
            else:
                d24 = rng.randrange(2)
                out.append(f"cds_seconds {cds_time(rng, d24)}")
        elif kind == 10:
            # A CCS P-field, often valid, then octets that are often BCD.
            p = rng.choice([0x50, 0x52, 0x56, 0x58, 0x5b, 0x5e, 0x57, 0xd0,
                            rng.randrange(256)])
            t = bytes(rng.choice([rng.randrange(256),
                                  (rng.randrange(10) << 4) | rng.randrange(10)])
                      for _ in range(rng.randrange(15)))
            out.append("ccs_decode " + hexs(bytes([p]) + t))
        elif kind == 11:
            out.append(f"ccs_encode {rng.randrange(2)} {rng.randint(0, 6)} "
                       f"{calendar(rng)} {rng.randrange(2)} {cap}")
        elif kind == 12:
            out.append("ascii_parse " + ascii_text(rng))
        elif kind == 13:
            out.append(f"ascii_format {rng.choice('AB')} {rng.randint(0, 13)} "
                       f"{rng.randrange(2)} {calendar(rng)} "
                       f"{rng.choice([0, 17, 24, 40])}")
        elif kind == 14:
            out.append(f"utc_to_tai {calendar(rng)}")
        else:
            # Around the table's first change, a leap second, its expiry,
            # and anywhere.
            s = rng.choice([5113 * 86400 + 10, 5113 * 86400 + 9, 1861920035,
                            1861920036, 1861920037, 2192832037, 2192832036,
                            rng.randrange(2**34)])
            picos = rng.choice([0, 10**12 - 1, 10**12, rng.randrange(10**12)])
            out.append(f"tai_to_utc {s} {picos}")
    print("\n".join(out))


if __name__ == "__main__":
    main()
