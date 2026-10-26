"""Print random packet vectors for tests/mission/demo.xml.

Each packet has a valid CCSDS header for one of the demo's packet types (or
an unknown one) followed by random bytes, so decoding exercises valid
packets as well as every kind of failure.  The packet length mostly ends
the packet where the generated Python codec stops decoding it, so the rest
of the random bytes follow the packet; sometimes it covers them all, and
sometimes it is wrong.  The output has the format of
vectors.txt.

Usage: python3 random_vectors.py SEED COUNT

It needs the generated Python codec, which ``make packets`` writes.
"""

import random
import sys
from pathlib import Path

TOP = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(TOP / "python"), str(TOP / "python" / "tests" / "mission")]

import demo  # noqa: E402
from xtce.packet import PacketError  # noqa: E402

# The custom algorithms the packet tools register.
demo.HOOKS.update(is_even=lambda v: v[0] % 2.0 == 0.0,
                  min_max=lambda v: (min(v), max(v)),
                  bands=lambda v: int(v[0] / 50.0),
                  twice=lambda d, start, end: 2 * sum(d[start // 8:end // 8]))


def header(rng, packet_type, apid):
    """A primary header; packet_length() fills in its length."""
    seq = rng.randrange(1 << 14)
    word0 = (packet_type << 12) | (rng.randrange(2) << 11) | apid
    if packet_type:
        word0 = (1 << 12) | apid  # commands carry fixed version and flags
        seq |= 3 << 14
    else:
        seq |= rng.randrange(4) << 14
    return word0.to_bytes(2, "big") + seq.to_bytes(2, "big") + bytes(2)


def with_length(data: bytes, total: int) -> bytes:
    """The first ``total`` bytes of ``data``, with the CCSDS packet length
    (bytes after the header, minus 1) saying so."""
    return (data[:4] + ((total - 7) % (1 << 16)).to_bytes(2, "big")
            + data[6:total])


def decoded_size(kind, data: bytes) -> int:
    """The size at which the codec stops decoding ``data``: the shortest
    packet that doesn't fail for being too short or too long."""
    for total in range(7, len(data) + 1):
        try:
            kind.decode(with_length(data, total))
            return total
        except PacketError as e:
            if e.kind not in ("Truncated", "BadLength"):
                return total
    return len(data)


def packet_length(rng, kind, data: bytes) -> bytes:
    """``data`` with a packet length that ends where decoding stops, covers
    all of it, or is wrong."""
    r = rng.random()
    if r < 0.6:
        n = decoded_size(kind, data)
    elif r < 0.8:
        n = len(data)
    elif r < 0.9:
        n = decoded_size(kind, data) + rng.choice([-2, -1, 1, 2])
    else:
        n = rng.randrange(1 << 16) + 7
    return data[:4] + ((n - 7) % (1 << 16)).to_bytes(2, "big") + data[6:]


def bits(fields) -> bytes:
    """(width, value) pairs packed most significant bit first."""
    s = "".join(format(v & ((1 << n) - 1), f"0{n}b") for n, v in fields)
    s += "0" * (-len(s) % 8)
    return int(s, 2).to_bytes(len(s) // 8, "big")


def samples(rng, sec_hdr):
    """A Samples body: counts and sizes mostly in range, contents random."""
    n = rng.choice([0, 1, 3, 6, rng.randrange(8)])
    f = [(8, n)] + [(16, rng.randrange(1 << 16)) for _ in range(min(n, 7))]
    if sec_hdr:
        f += [(4, rng.randrange(16)) for _ in range(6)]
    for i in range(4):
        n = 1 if i == 0 else 2
        f += [(1, rng.randrange(2)) for _ in range(n)]
    size = rng.choice([0, 2, 5, 10, rng.randrange(12)])
    text = b"".join(rng.choice([b"a", b"z", b"\0", b"\xc3\xa9", b"\xff"])
                    for _ in range(size))
    f += [(8, size)] + [(8, c) for c in text[:size]]
    size = rng.choice([0, 3, 8, rng.randrange(10)])
    f += [(8, size)] + [(8, rng.randrange(256)) for _ in range(min(size, 8))]
    return bits(f)


def targets(rng, sec_hdr):
    """A Targets body: a target count mostly in range, random targets."""
    n = rng.choice([0, 1, 3, rng.randrange(5)])
    f = [(16, rng.randrange(1 << 16)), (16, rng.randrange(1 << 16)), (8, n)]
    for _ in range(min(n, 4)):
        name = bytes(rng.choice(b"ab\xc3\xa9\xff")
                     for _ in range(rng.choice([0, 3, 15, 16])))
        f += [(4, rng.randrange(16)), (8, rng.choice([0, 2, 20, 200]))]
        f += [(8, c) for c in name + b"\0"]
        f += ([(16, rng.randrange(1 << 16)) for _ in range(2)]
              + [(4, rng.randrange(16)) for _ in range(2)])
    if sec_hdr:
        f += [(16, rng.randrange(1 << 16)) for _ in range(2)]
    return bits(f)


def dump(rng) -> bytes:
    """A Dump body: a calibrated record count mostly from 0 to 6."""
    raw = rng.choice([8, 9, 10, 12, 20, rng.randrange(256)])  # -4 + raw / 2
    n = max(round(-4 + raw / 2 + 1e-9), 0)
    body = bytes([rng.randrange(256), raw]) + rng.randbytes(min(n, 8))
    return body + rng.randbytes(rng.choice([0, 0, 1, 5]))


def log(rng) -> bytes:
    """A Log body: a record count mostly in range, 32-bit records."""
    n = rng.choice([0, 1, 3, 6, rng.randrange(8)])
    return bytes([n]) + rng.randbytes(4 * min(n, 7) + rng.choice([0, 1, 2, 2]))


def batches(rng) -> bytes:
    """A Batches body: counts and sizes mostly in range, random values."""
    n = rng.choice([0, 1, 3, 6, rng.randrange(8)])
    f = [(8, n)]
    for _ in range(min(n, 7)):
        size = rng.choice([0, 1, 3, rng.randrange(16)])
        f += ([(4, size)]
              + [(8, rng.randrange(256)) for _ in range(min(size, 4))])
    return bits(f)


def reply(rng) -> bytes:
    """A Reply body: mostly valid answers, codes and notes."""
    answer = rng.choice([b"yes\0", b"yes\0", b"no\0\0", b"no\0z", b"yes!",
                         rng.randbytes(4)])
    code = rng.choice([b"ABC", b"ABC", b"\xc3\xa9a", b"a\xff\0",
                       rng.randbytes(3)])
    size = rng.choice([0, 2, 8, rng.randrange(12)])
    note = b"".join(rng.choice([b"h", b"i", b"\xc3\xa9"])
                    for _ in range(size))[: min(size, 8)]
    if note and rng.random() < 0.2:
        note = note[:-1] + b"\xff"
    return answer + code + bytes([size]) + note


def frame(rng) -> bytes:
    """A Frame body: a kind that picks the body size, and a gap."""
    kind = rng.choice([0, 1, 2, rng.randrange(256)])
    gap = rng.randrange(16)
    body = {1: 2, 2: 4}.get(kind, 0)
    f = [(8, kind), (4, gap)] + [(8, rng.randrange(256)) for _ in range(body)]
    return bits(f + [(gap, 0), (1, rng.randrange(2))])


def route(rng) -> bytes:
    """A Route body: a header whose members give the stop count and
    whether there is a detour."""
    kind, n = (rng.choice([1, 2, rng.randrange(16)]),
               rng.choice([0, 1, 3, rng.randrange(16)]))
    return bytes([kind << 4 | n]) + rng.randbytes(min(n, 4) + (kind == 2))


def probe(rng) -> bytes:
    """A probe body: two probes, the first mostly with Id 1, and data
    sized by the second probe's first gain."""
    ident, size = (rng.choice([1, 1, rng.randrange(16)]),
                   rng.choice([0, 2, rng.randrange(16)]))
    head = bits([(4, ident), (8, rng.randrange(256)), (4, rng.randrange(16)),
                 (4, size), (4, rng.randrange(16))])
    return head + rng.randbytes(size) + rng.randbytes(rng.choice([0, 1]))


def tally(rng) -> bytes:
    """A Tally body: a count mostly in range, then the tallies, data sized
    by the first, an extra byte when the second is 7 and another when there
    are three or more.  Short counts leave those elements missing."""
    n = rng.choice([0, 1, 2, 2, 3, 3, 4, rng.randrange(8)])
    tallies = [rng.choice([0, 1, 2, 7, rng.randrange(256)])
               for _ in range(min(n, 4))]
    if tallies and rng.random() < 0.8:
        tallies[0] = rng.randrange(4)  # mostly data that is there
    body = bytes([n] + tallies)
    if tallies:
        body += rng.randbytes(min(tallies[0], 4))
    if len(tallies) > 1 and tallies[1] == 7:
        body += rng.randbytes(1)
    if n >= 3:
        body += rng.randbytes(1)
    return body


def bcd(rng, digits: int, packed: bool) -> bytes:
    """BCD digits, now and then one above 9."""
    out = []
    for _ in range(digits):
        d = rng.randrange(10) if rng.random() < 0.95 else rng.randrange(16)
        out.append(d if packed else d | (rng.random() < 0.03) << 4)
    if packed:
        return bytes((a << 4) | b for a, b in zip(out[::2], out[1::2]))
    return bytes(out)


def text(rng, chars: str, codec: str, n: int) -> bytes:
    return "".join(rng.choice(chars) for _ in range(n)).encode(codec)


def encodings(rng) -> bytes:
    """An Encodings body: random integers and float bits (every float
    format's special values among them), BCD digits mostly valid, and
    strings in each character set, now and then invalid."""
    body = rng.randbytes(8) + bcd(rng, 4, True) + bcd(rng, 2, False)
    body += rng.randbytes(2 + 10 + 16 + 4 + 6 + 4 + 8 + 4 + 8 + 16 + 2 + 4 + 5)
    n = rng.choice([0, 3, 5, 8])
    body += (text(rng, "aüßÀ", "latin-1", n) + bytes(8))[:8]
    ansi = text(rng, "a€5ž", "cp1252", rng.randrange(7))
    body += (ansi + b"\n") if rng.random() < 0.9 else b"\x81\n"
    wide = text(rng, "aé😀", "utf-16-be", rng.randrange(4))
    if rng.random() < 0.1:
        wide = b"\xff\xfe" + text(rng, "aé", "utf-16-le", 2)
    body += bytes([len(wide)]) + wide
    name = text(rng, "Zoë", "utf-16-le", rng.randrange(5))
    body += (name + bytes(8))[:8]
    body += text(rng, "a😀é", "utf-32-be", rng.randrange(4)) + bytes(4)
    body += text(rng, "hi\r", "utf-8", rng.randrange(6)) + b"\r\n"
    return body


def configure(rng) -> bytes:
    """A Configure body: a TI gain, a UTF-16 label, BCD digits, flags."""
    label = text(rng, "Hié😀", "utf-16-be", rng.randrange(3))
    return (rng.randbytes(4) + bytes([len(label)]) + label + bcd(rng, 4, True)
            + rng.randbytes(2))


def checked(rng, header: bytes):
    """A Checked packet: half the time with every checksum right (encoded
    by the codec, header and all; the result is then the whole packet, and
    True), otherwise a random body, which they mostly reject (and False)."""
    if rng.random() < 0.5:
        return rng.randbytes(11), False
    p = demo.Checked(
        sec_hdr_flag=bool(header[0] & 0x08),
        seq_flags=demo.SeqFlagsType.UNSEGMENTED,
        seq_count=rng.randrange(1 << 14), counter=rng.randrange(256),
        block_data=rng.randrange(1 << 16), spare=rng.randrange(128))
    data = bytearray(p.to_bytes())
    if rng.random() < 0.2:  # one bit wrong
        data[rng.randrange(len(data))] ^= 1 << rng.randrange(8)
    return bytes(data), True


def guarded(rng, header: bytes) -> bytes:
    """A whole Guarded packet, encoded by the codec, sometimes with a bit
    wrong."""
    on = rng.random() < 0.7
    p = demo.Guarded(sec_hdr_flag=bool(header[0] & 0x08),
                     seq_flags=demo.SeqFlagsType.UNSEGMENTED,
                     seq_count=rng.randrange(1 << 14), guard_on=on,
                     block_data=rng.randrange(1 << 16) if on else None,
                     block_data2=rng.randrange(1 << 16) if on else None,
                     guard_end=rng.randrange(256))
    data = bytearray(p.to_bytes())
    if rng.random() < 0.2:  # one bit wrong
        data[rng.randrange(len(data))] ^= 1 << rng.randrange(8)
    return bytes(data)


def nested(rng) -> bytes:
    """A Nested body: rows, a row count and gap, extra rows, and words of a
    size mostly in range."""
    count, gap = rng.choice([0, 1, 2, 3, rng.randrange(16)]), rng.randrange(16)
    size = rng.choice([0, 1, 2, 4, rng.randrange(8)])
    f = [(4, rng.randrange(16)) for _ in range(6)] + [(4, count), (4, gap)]
    for i in range(min(count, 4)):
        if i:
            f.append((gap, 0))
        f += [(4, rng.randrange(16)) for _ in range(3)]
    f.append((8, size))
    f += [(8, rng.choice(b"ab\0")) for _ in range(3 * min(size, 4))]
    return bits([x for x in f if x[0]])


def framed(rng) -> bytes:
    """A Framed body: a section mostly of kind 2, a note, a bag of a size
    mostly at least its item's, and a tray."""
    note = bytes(rng.choice(b"hi") for _ in range(rng.randrange(6)))
    carton = (note + b"\0" + bytes(6))[:6]
    bag = rng.choice([8, 16, 40, rng.randrange(64)])
    f = ([(4, rng.choice([2, 2, rng.randrange(16)])), (4, rng.randrange(16)),
          (8, rng.randrange(256))] + [(8, c) for c in carton])
    f += [(8, bag), (8, rng.randrange(256)), (max(bag - 8, 0), 0)]
    return bits([x for x in f if x[0]]) + rng.randbytes(4)


def upload(rng) -> bytes:
    """An Upload body: word count and tag size mostly in range."""
    n = rng.choice([1, 2, 4, rng.randrange(6)])
    f = [(8, n)] + [(16, rng.randrange(1 << 16)) for _ in range(min(n, 5))]
    f += [(16, rng.randrange(1 << 16)) for _ in range(2)]
    size = rng.choice([1, 4, 8, rng.randrange(10)])
    tag = bytes(rng.choice(b"ab\0") for _ in range(rng.randrange(size + 2)))
    body = bytes([len(tag)]) + tag
    f += [(8, size)] + [(8, c) for c in body[:size]]
    return bits(f)


def wide_value(rng, n: int) -> int:
    """A raw field of ``n`` bits: zero, all ones, its sign bit alone (-0 or
    the most negative value), small, or random."""
    return rng.choice([0, (1 << n) - 1, 1 << (n - 1), (1 << (n - 1)) - 1,
                       rng.randrange(1000), rng.getrandbits(n),
                       rng.getrandbits(n)])


def bcd_digits(rng, digits: int, width: int) -> int:
    """BCD digits in ``width`` bits each; now and then one isn't."""
    bad = rng.randrange(digits) if rng.random() < 0.1 else -1
    v = 0
    for i in range(digits):
        v = (v << width) | (rng.randrange(10) if i != bad
                            else rng.randrange(10, 1 << min(width, 4)))
    return v


def wide(rng) -> bytes:
    """A Wide body: integers of 65 to 200 bits."""
    f = [(16, rng.randrange(1 << 16))]
    f += [(72, rng.choice([wide_value(rng, 72), 10**21, 10**21 + 1]))]
    f += [(n, wide_value(rng, n)) for n in (80, 65, 7, 100)]
    # Mostly within its ValidRange, which encoding checks.
    f += [(128,
           rng.choice([wide_value(rng, 128), 10**18, 10**18 + 1, 0]
                      + [rng.randrange(10**18)] * 4))]
    f += [(96, bcd_digits(rng, 24, 4)), (136, bcd_digits(rng, 17, 8))]
    f += [(n, wide_value(rng, n)) for n in (128, 200, 72, 72)]
    return bits(f)


def repeated(rng) -> bytes:
    """A Repeated body: a count mostly in range, RepWord now and then the
    value that brings RepAfter."""
    n = rng.choice([0, 1, 3, 5, rng.randrange(8)])
    word = rng.choice([0x12345678, rng.getrandbits(32)])
    f = [(8, n), (32, word)] + [(4, rng.randrange(16)) for _ in range(n)]
    f += [(8, rng.randrange(256)), (4, rng.randrange(16)),
          (8, rng.randrange(256))]
    return bits(f + [(8, rng.randrange(256))])


def set_limits(rng) -> bytes:
    """A SetLimits body: Both mostly 0 or 1, LimitKind mostly 7."""
    both = rng.choice([0, 1, 1, rng.randrange(256)])
    out = bytes([both])
    for _ in range(2 if both == 1 else 1):
        out += bytes([rng.choice([7] * 9 + [rng.randrange(256)])])
        out += rng.randbytes(4)
    return out


def main() -> None:
    rng = random.Random(int(sys.argv[1]))
    for _ in range(int(sys.argv[2])):
        keep = False  # whether the packet length is right as it is
        if rng.random() < 0.6:
            apid = rng.choice([100, 100, 150, 200, 200, 250, 250, 260, 260, 270,
                               280, 290, 310, 320, 330, 340, 350, 360, 370, 380,
                               380, 390, 395, 395, 400, 400, 410, 420, 420, 430,
                               430, 440, 450, 460, 470, 480, 490, 500, 510, 520,
                               530, 550, 550, 560, 560, rng.randrange(1 << 11)])
            data = header(rng, 0, apid)
            if apid == 100 and rng.random() < 0.5:
                data += bytes([2, rng.randrange(128)])  # science mode, cool
            elif apid == 200 and rng.random() < 0.7:
                data += (rng.randbytes(2)
                         + rng.choice([b"ORBIT\0\0\0", rng.randbytes(8)]))
                n = rng.randrange(34)
                data += bytes([n]) + bytes(rng.choice(b"abc \x00\x7f\xc3\xa9")
                                           for _ in range(n))
                if rng.random() < 0.8:
                    n = rng.randrange(17)
                    data += bytes(rng.choice(b"xyz\x01") for _ in range(n))
                    data += b"\0"
            elif apid == 250 and rng.random() < 0.8:
                data += samples(rng, data[0] & 0x08)
            elif apid == 260 and rng.random() < 0.8:
                data += targets(rng, data[0] & 0x08)
            elif apid == 270 and rng.random() < 0.8:
                data += dump(rng)
            elif apid == 280 and rng.random() < 0.8:
                data += log(rng)
            elif apid == 290 and rng.random() < 0.8:
                data += batches(rng)
            elif apid == 310 and rng.random() < 0.8:
                data += reply(rng)
            elif apid == 320 and rng.random() < 0.8:
                data += frame(rng)
            elif apid == 360 and rng.random() < 0.8:
                data += bits([(4, rng.choice([7, 7, rng.randrange(16)]))
                              if i == 1 else (4, rng.randrange(16))
                              for i in range(4)])
                data += rng.randbytes(rng.choice([0, 1]))
            elif apid == 350 and rng.random() < 0.8:
                data += probe(rng)
            elif apid == 370 and rng.random() < 0.8:
                data += tally(rng)
            elif apid == 380 and rng.random() < 0.9:
                data += encodings(rng)
            elif apid == 410 and rng.random() < 0.9:
                # Times: the calibrated one mostly inside its spline, text
                # mostly ASCII.
                cal = rng.choice([rng.randrange(1001), rng.randrange(1 << 16)])
                text = bytes(rng.choice(b"0123456789-T:Z\xe9")
                             for _ in range(rng.randrange(21)))
                data += (rng.randbytes(7) + cal.to_bytes(2, "big")
                         + rng.randbytes(8) + text.ljust(20, b"\0")
                         + rng.randbytes(6))
            elif apid == 420 and rng.random() < 0.9:
                data += nested(rng)
            elif apid == 430 and rng.random() < 0.9:
                data += framed(rng)
            elif apid == 480 and rng.random() < 0.9:
                data, keep = guarded(rng, data), True
            elif apid == 400 and rng.random() < 0.9:
                body, whole = checked(rng, data)
                data = body if whole else data + body
                keep = whole
            elif apid == 390 and rng.random() < 0.9:
                # A calibration context mode, mostly 1 or 2, and the flag.
                data += bits([(8, rng.choice([1, 2, 2, rng.randrange(256)])),
                              (1, rng.randrange(2)),
                              (16, rng.randrange(1 << 16))])
            elif apid == 395 and rng.random() < 0.9:
                # Steps beyond 40 fail without extrapolation, now and then.
                steps = rng.choice([rng.randrange(41), rng.randrange(256)])
                data += bits(
                    [(8, rng.randrange(256)), (8, rng.randrange(4)),
                     (1, rng.randrange(2))]
                    + [(8, rng.randrange(256)) for _ in range(3)] + [(8, steps)]
                    + [(8, rng.randrange(256)) for _ in range(2)])
            elif apid == 330 and rng.random() < 0.8:
                data += route(rng)
            elif apid == 560 and rng.random() < 0.9:
                data += repeated(rng)
            elif apid == 550 and rng.random() < 0.9:
                data += wide(rng)
            elif apid == 340 and rng.random() < 0.8:
                fixed = [bytes([1, 2]), bytes([0, 0]), bytes([2, 1])]
                data += rng.choice(fixed + [rng.randbytes(2)])
                data += rng.randbytes(rng.choice([0, 1, 2]))
            kind = "tm"
        else:
            data = header(rng, 1, rng.choice([300, 301, 302]))
            opcode = rng.choice([1, 2, 3, 4, 4, 5, 6, 7, 8, 9, 10, 11, 11, 12,
                                 12, 13, 13, 14, 15, 16, 17, 17, 18, 18,
                                 rng.randrange(256)])
            data += bytes([opcode])
            if opcode == 2 and rng.random() < 0.7:
                n = rng.randrange(16)
                data += (rng.randbytes(3) + n.to_bytes(2, "big")
                         + bytes(rng.choice(b"ab\xc3\xa9") for _ in range(n)))
            elif opcode == 4 and rng.random() < 0.8:
                data += upload(rng)
            elif opcode == 7 and rng.random() < 0.8:
                mode = rng.choice([0, 1, 2, rng.randrange(256)])
                data += bytes([mode]) + rng.randbytes({0: 0, 2: 3}.get(mode, 1))
            elif opcode == 10 and rng.random() < 0.8:
                last = rng.choice([1, 2, 3, 4, rng.randrange(8)])
                data += bytes([last]) + rng.randbytes(max(min(last, 4) - 1, 0))
            elif opcode == 9 and rng.random() < 0.8:
                lo, hi = rng.randrange(4), rng.randrange(4)
                data += bytes([lo, hi]) + rng.randbytes(2 if lo < hi else 0)
            elif opcode == 11 and rng.random() < 0.9:
                data += configure(rng)
            elif opcode == 13 and rng.random() < 0.9:
                # Arm: Force mostly 0 or 1, then Level when forced, then the
                # pattern, now and then wrong (decoding doesn't check it).
                force = rng.choice([0, 1, 1, rng.randrange(256)])
                data += bytes([force])
                if force == 1:
                    data += bytes([rng.randrange(256)])
                data += rng.choice([bytes.fromhex("a5" * 9 + "0a" + "5a" * 8
                                                  + "50"), rng.randbytes(19)])
            elif opcode == 12 and rng.random() < 0.9:
                # Eight digits and a Luhn check digit, mostly right.
                digits = "".join(rng.choice("0123456789") for _ in range(8))
                p = demo.Send(seq_count=1, digits=int(digits))
                body = p.to_bytes()[7:]
                if rng.random() < 0.3:
                    body = body[:4] + bytes([rng.randrange(256)])
                data += body
            elif opcode == 18 and rng.random() < 0.9:
                data += set_limits(rng)
            elif opcode == 17 and rng.random() < 0.9:
                # SetWide: a 96-bit offset, mostly in its valid range when
                # re-encoded, and a 136-bit key.
                offset = rng.choice([wide_value(rng, 96), 10**18,
                                     (1 << 96) - 10**18, rng.randrange(100)])
                data += bits([(96, offset), (136, wide_value(rng, 136))])
            elif opcode == 8 and rng.random() < 0.8:
                n = rng.choice([1, 2, 4, rng.randrange(6)])
                data += (bytes([rng.randrange(3), n])
                         + rng.randbytes(2 * min(n, 4)))
            kind = "tc"
        data += rng.randbytes(rng.randrange(40))
        if not keep:
            data = packet_length(rng,
                                 demo.CCSDSPacketKind if kind == "tm"
                                 else demo.CCSDSCommandKind, data)
        if rng.random() < 0.1:
            data = data[: rng.randrange(len(data) + 1)]
        print(kind, data.hex() or "00")


if __name__ == "__main__":
    main()
