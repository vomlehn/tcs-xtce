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


def main() -> None:
    rng = random.Random(int(sys.argv[1]))
    for _ in range(int(sys.argv[2])):
        if rng.random() < 0.6:
            apid = rng.choice([100, 100, 150, 200, 200, rng.randrange(1 << 11)])
            data = header(rng, 0, apid)
            if apid == 100 and rng.random() < 0.5:
                data += bytes([2, rng.randrange(128)])  # science mode, cool
            elif apid == 200 and rng.random() < 0.7:
                data += rng.randbytes(2) + rng.choice([b"ORBIT\0\0\0", rng.randbytes(8)])
                n = rng.randrange(34)
                data += bytes([n]) + bytes(rng.choice(b"abc \x00\x7f\xc3\xa9") for _ in range(n))
                if rng.random() < 0.8:
                    data += bytes(rng.choice(b"xyz\x01") for _ in range(rng.randrange(17))) + b"\0"
            kind = "tm"
        else:
            data = header(rng, 1, rng.choice([300, 301]))
            opcode = rng.choice([1, 2, 3, rng.randrange(256)])
            data += bytes([opcode])
            if opcode == 2 and rng.random() < 0.7:
                n = rng.randrange(16)
                data += rng.randbytes(3) + n.to_bytes(2, "big") + bytes(rng.choice(b"ab\xc3\xa9") for _ in range(n))
            kind = "tc"
        data += rng.randbytes(rng.randrange(40))
        if rng.random() < 0.1:
            data = data[: rng.randrange(len(data) + 1)]
        print(kind, data.hex() or "00")


if __name__ == "__main__":
    main()
