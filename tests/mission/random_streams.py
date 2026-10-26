"""Random input for tests/stream_check.sh: lines of ``<stream> <hex>`` that
feed the demo mission's streams frames among noise, with bit errors and
slips, missing and inverted frames, and HDLC frames with stuffed bits,
aborts and NRZ-M coding, cut at random places.

Usage: python3 random_streams.py SEED COUNT
"""

import random
import sys

ASM = [int(b) for b in f"{0x1ACFFC1D:032b}"]
DEEP = [int(b) for b in f"{0xFAF320034ACFFC1D5A5A:080b}"]
BEACON = [int(b) for b in f"{0xC3A55A3CF00FF0E1D2:072b}"]
CLEAR = [int(b) for b in f"{0xC3A5965A:032b}"]


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


def fixed_frames(rng, sync: list, frame_bits: int, unmasked=()) -> list:
    """Bits of frames led by ``sync``, whose bits at ``unmasked`` aren't
    compared, with bit errors, slips and noise."""
    out = [rng.randrange(2) for _ in range(rng.randrange(200))]
    for count in range(rng.randrange(1, 20)):
        frame = (sync
                 + bits_of(bytes([count & 255])
                           + rng.randbytes((frame_bits - len(sync)) // 8 - 1)))
        for i in unmasked:
            frame[i] = rng.randrange(2)
        r = rng.random()
        if r < 0.1:
            for _ in range(rng.choice([1, 2, 3])):
                frame[rng.randrange(len(sync))] ^= 1
        elif r < 0.15:
            for _ in range(rng.choice([1, 2, 3])):
                if rng.random() < 0.5:
                    out.append(rng.randrange(2))
                elif out:
                    out.pop()
        elif r < 0.18:
            frame = [rng.randrange(2) for _ in frame]
        out += frame
    return out


def biphase(bits: list, code: str) -> list:
    """Bi-phase-L, -M or -S levels for ``bits``, two a bit."""
    out, level = [], 0
    for b in bits:
        if code == "L":
            out += [b, 1 - b]
            continue
        level ^= 1  # every bit starts with a change of level
        first = level
        if b == (code == "M"):
            level ^= 1  # and a one (M) or zero (S) changes in mid bit
        out += [first, level]
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


def hdlc(rng) -> list:
    """Bits of HDLC frames among noise, with aborts."""
    flag = [0, 1, 1, 1, 1, 1, 1, 0]
    out = [rng.randrange(2) for _ in range(rng.randrange(40))]
    for _ in range(rng.randrange(1, 8)):
        out += flag
        body = stuff(bits_of(rng.randbytes(rng.randrange(1, 40))))
        if rng.random() < 0.1:  # an abort
            body[rng.randrange(len(body)):] = [1] * 7
        out += body
    out += flag
    return out


def uplink(rng) -> list:
    return nrzm(hdlc(rng))


def deep(rng) -> list:
    return biphase(fixed_frames(rng, DEEP, 160, range(64, 72)), "L")


def beacon(rng) -> list:
    return [1 - b for b in biphase(fixed_frames(rng, BEACON, 128), "S")]


def relay(rng) -> list:
    return biphase(hdlc(rng), "M")


def scramble(bits: list) -> list:
    """``bits`` as the Scrambled stream sends them: each byte XOR 0x55."""
    bits = bits + [0] * (-len(bits) % 8)
    return [b ^ ((0x55 >> (7 - i % 8)) & 1) for i, b in enumerate(bits)]


def scrambled(rng) -> list:
    return [rng.randrange(2) for _ in range(8 * rng.randrange(1, 40))]


def clear_frames(rng, payload: list) -> list:
    """Clear's 64-bit frames, carrying ``payload`` 32 bits a frame, with
    bit errors, noise and inversions, among noise."""
    out = [rng.randrange(2) for _ in range(rng.randrange(100))]
    invert = False
    for k in range(0, len(payload), 32):
        frame = CLEAR + (payload[k:k + 32] + [0] * 32)[:32]
        r = rng.random()
        if r < 0.05:
            frame[rng.randrange(32)] ^= 1
        elif r < 0.08:
            frame = [rng.randrange(2) for _ in frame]
        elif r < 0.1:
            invert = not invert
        out += [b ^ invert for b in frame]
    return out


def clear(rng) -> list:
    payload = [rng.randrange(2) for _ in range(32 * rng.randrange(1, 12))]
    return scramble(clear_frames(rng, payload))


def nested(rng) -> list:
    return scramble(clear_frames(rng, hdlc(rng)))


def main() -> None:
    rng = random.Random(int(sys.argv[1]))
    for _ in range(int(sys.argv[2])):
        name = rng.choice(["downlink", "uplink", "deep", "beacon", "relay",
                           "scrambled", "clear", "nested"])
        bits = {"downlink": downlink, "uplink": uplink, "deep": deep,
                "beacon": beacon, "relay": relay, "scrambled": scrambled,
                "clear": clear, "nested": nested}[name](rng)
        hexes = to_hex(bits)
        at = 0
        while at < len(hexes):
            n = 2 * rng.randrange(1, 40)
            print(name, hexes[at:at + n])
            at += n


if __name__ == "__main__":
    main()
