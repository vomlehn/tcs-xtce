"""Decode and re-encode the packet vectors in tests/mission/vectors.txt.

For each vector prints ``<description of the decoded packet> | <hex of the
re-encoded packet>``, with ``error:<kind>`` in place of either on failure.
The Rust and C/C++ versions of this tool print identical output.  With
``--packed`` it uses the codecs generated with ``--bit-packing``, which must
print the same.

Usage: python3 packet_tool.py [--packed] VECTORS
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "mission"))

import demo  # noqa: E402
import demo_packed  # noqa: E402
from xtce.packet import Codec, PacketError  # noqa: E402


def decoders(m) -> dict:
    return {"tm": m.CCSDSPacketKind, "tc": m.CCSDSCommandKind, "hk": m.Housekeeping}


def run(cls, data: bytes) -> str:
    codec = Codec.for_packet(cls)
    codec.load(data)
    try:
        packet = codec.decode(cls)
    except PacketError as e:
        return f"error:{e.kind} | -"
    try:
        with codec.encode(packet) as out:
            again = out.hex()
    except PacketError as e:
        again = f"error:{e.kind}"
    return f"{packet.describe()} | {again}"


def main() -> None:
    args = sys.argv[1:]
    packed = args[:1] == ["--packed"]
    table = decoders(demo_packed if packed else demo)
    for line in Path(args[-1]).read_text().splitlines():
        line = line.split("#")[0].split()
        if line:
            print(run(table[line[0]], bytes.fromhex(line[1])))


if __name__ == "__main__":
    main()
