"""Print random packet vectors for tests/mission/demo.xml.

Each packet has a valid CCSDS header for one of the demo's packet types (or
an unknown one) followed by random bytes, so decoding exercises valid
packets as well as every kind of failure.  The output has the format of
vectors.txt.

Usage: python3 random_vectors.py SEED COUNT
"""

import random
import sys


def header(rng, packet_type, apid):
    seq = rng.randrange(1 << 14)
    length = rng.randrange(1 << 16)
    word0 = (packet_type << 12) | (rng.randrange(2) << 11) | apid
    if packet_type:
        word0 = (1 << 12) | apid  # commands carry fixed version and flags
        seq |= 3 << 14
    else:
        seq |= rng.randrange(4) << 14
    return word0.to_bytes(2, "big") + seq.to_bytes(2, "big") + length.to_bytes(2, "big")


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
        f += ([(1, rng.randrange(2))] if i == 0 else [(1, rng.randrange(2)), (1, rng.randrange(2))])
    size = rng.choice([0, 2, 5, 10, rng.randrange(12)])
    text = b"".join(rng.choice([b"a", b"z", b"\0", b"\xc3\xa9", b"\xff"]) for _ in range(size))
    f += [(8, size)] + [(8, c) for c in text[:size]]
    size = rng.choice([0, 3, 8, rng.randrange(10)])
    f += [(8, size)] + [(8, rng.randrange(256)) for _ in range(min(size, 8))]
    return bits(f)


def targets(rng, sec_hdr):
    """A Targets body: a target count mostly in range, random targets."""
    n = rng.choice([0, 1, 3, rng.randrange(5)])
    f = [(16, rng.randrange(1 << 16)), (16, rng.randrange(1 << 16)), (8, n)]
    for _ in range(min(n, 4)):
        name = bytes(rng.choice(b"ab\xc3\xa9\xff") for _ in range(rng.choice([0, 3, 15, 16])))
        f += [(4, rng.randrange(16)), (8, rng.choice([0, 2, 20, 200]))]
        f += [(8, c) for c in name + b"\0"]
        f += [(16, rng.randrange(1 << 16)) for _ in range(2)] + [(4, rng.randrange(16)) for _ in range(2)]
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
            apid = rng.choice([100, 100, 150, 200, 200, 250, 250, 260, 260, 270, 280, rng.randrange(1 << 11)])
            data = header(rng, 0, apid)
            if apid == 100 and rng.random() < 0.5:
                data += bytes([2, rng.randrange(128)])  # science mode, cool
            elif apid == 200 and rng.random() < 0.7:
                data += rng.randbytes(2) + rng.choice([b"ORBIT\0\0\0", rng.randbytes(8)])
                n = rng.randrange(34)
                data += bytes([n]) + bytes(rng.choice(b"abc \x00\x7f\xc3\xa9") for _ in range(n))
                if rng.random() < 0.8:
                    data += bytes(rng.choice(b"xyz\x01") for _ in range(rng.randrange(17))) + b"\0"
            elif apid == 250 and rng.random() < 0.8:
                data += samples(rng, data[0] & 0x08)
            elif apid == 260 and rng.random() < 0.8:
                data += targets(rng, data[0] & 0x08)
            elif apid == 270 and rng.random() < 0.8:
                data += dump(rng)
            elif apid == 280 and rng.random() < 0.8:
                data += log(rng)
            kind = "tm"
        else:
            data = header(rng, 1, rng.choice([300, 301, 302]))
            opcode = rng.choice([1, 2, 3, 4, 4, 5, 6, rng.randrange(256)])
            data += bytes([opcode])
            if opcode == 2 and rng.random() < 0.7:
                n = rng.randrange(16)
                data += rng.randbytes(3) + n.to_bytes(2, "big") + bytes(rng.choice(b"ab\xc3\xa9") for _ in range(n))
            elif opcode == 4 and rng.random() < 0.8:
                data += upload(rng)
            kind = "tc"
        data += rng.randbytes(rng.randrange(40))
        if rng.random() < 0.1:
            data = data[: rng.randrange(len(data) + 1)]
        print(kind, data.hex() or "00")


if __name__ == "__main__":
    main()
