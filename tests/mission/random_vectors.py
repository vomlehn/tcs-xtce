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


def main() -> None:
    rng = random.Random(int(sys.argv[1]))
    for _ in range(int(sys.argv[2])):
        if rng.random() < 0.6:
            apid = rng.choice([100, 100, 150, 200, 200, 250, 250, 260, 260, 270,
                               280, 290, 310, 320, 330, 340, 350, 360,
                               rng.randrange(1 << 11)])
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
            elif apid == 330 and rng.random() < 0.8:
                data += route(rng)
            elif apid == 340 and rng.random() < 0.8:
                fixed = [bytes([1, 2]), bytes([0, 0]), bytes([2, 1])]
                data += rng.choice(fixed + [rng.randbytes(2)])
                data += rng.randbytes(rng.choice([0, 1, 2]))
            kind = "tm"
        else:
            data = header(rng, 1, rng.choice([300, 301, 302]))
            opcode = rng.choice([1, 2, 3, 4, 4, 5, 6, 7, 8, 9, 10,
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
            elif opcode == 8 and rng.random() < 0.8:
                n = rng.choice([1, 2, 4, rng.randrange(6)])
                data += (bytes([rng.randrange(3), n])
                         + rng.randbytes(2 * min(n, 4)))
            kind = "tc"
        data += rng.randbytes(rng.randrange(40))
        data = packet_length(rng,
                             demo.CCSDSPacketKind if kind == "tm"
                             else demo.CCSDSCommandKind, data)
        if rng.random() < 0.1:
            data = data[: rng.randrange(len(data) + 1)]
        print(kind, data.hex() or "00")


if __name__ == "__main__":
    main()
