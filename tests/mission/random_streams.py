"""Random input for tests/stream_check.sh: lines of ``<stream> <hex>`` that
feed the demo mission's streams frames among noise, with bit errors and
slips, missing and inverted frames, and HDLC frames with stuffed bits,
aborts and NRZ-M coding, cut at random places.

Usage: python3 random_streams.py SEED COUNT
"""

import random
import sys

ASM = [int(b) for b in f"{0x1ACFFC1D:032b}"]


def bits_of(data: bytes) -> list:
    return [(b >> (7 - k)) & 1 for b in data for k in range(8)]


def to_hex(bits: list) -> str:
    bits = bits + [0] * (-len(bits) % 8)
    return bytes(int("".join(map(str, bits[i:i + 8])), 2)
                 for i in range(0, len(bits), 8)).hex()


def downlink(rng) -> list:
    """Bits of frames: sync, count and data, with mishaps."""
    out = [rng.randrange(2) for _ in range(rng.randrange(200))]
    invert = False
    for count in range(rng.randrange(1, 30)):
        frame = ASM + bits_of(bytes([count & 255]) + rng.randbytes(11))
        r = rng.random()
        if r < 0.1:  # bit errors in the sync pattern
            for _ in range(rng.choice([1, 1, 2])):
                frame[rng.randrange(32)] ^= 1
        elif r < 0.15:  # a slip
            if rng.random() < 0.5:
                out.append(rng.randrange(2))
            elif out:
                out.pop()
        elif r < 0.18:  # noise in place of a frame
            frame = [rng.randrange(2) for _ in frame]
        elif r < 0.2:
            invert = not invert
        out += [b ^ invert for b in frame]
    return out


def stuff(bits: list) -> list:
    out, run = [], 0
    for b in bits:
        out.append(b)
        run = run + 1 if b else 0
        if run == 5:
            out.append(0)
            run = 0
    return out


def nrzm(bits: list, level: int = 0) -> list:
    out = []
    for b in bits:
        level ^= b
        out.append(level)
    return out


def uplink(rng) -> list:
    flag = [0, 1, 1, 1, 1, 1, 1, 0]
    out = [rng.randrange(2) for _ in range(rng.randrange(40))]
    for _ in range(rng.randrange(1, 8)):
        out += flag
        body = stuff(bits_of(rng.randbytes(rng.randrange(1, 40))))
        if rng.random() < 0.1:  # an abort
            body[rng.randrange(len(body)):] = [1] * 7
        out += body
    out += flag
    return nrzm(out)


def main() -> None:
    rng = random.Random(int(sys.argv[1]))
    for _ in range(int(sys.argv[2])):
        name = rng.choice(["downlink", "uplink"])
        bits = downlink(rng) if name == "downlink" else uplink(rng)
        hexes = to_hex(bits)
        at = 0
        while at < len(hexes):
            n = 2 * rng.randrange(1, 40)
            print(name, hexes[at:at + n])
            at += n


if __name__ == "__main__":
    main()
