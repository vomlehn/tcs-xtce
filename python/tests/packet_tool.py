"""Decode and re-encode the packet vectors in tests/mission/vectors.txt.

For each vector prints ``<description of the decoded packet> | <hex of the
re-encoded packet>``, with ``error:<kind>`` in place of either on failure,
then `` | alarms: <field=level, ...>`` for a packet with alarms.  The Rust
and C/C++ versions of this tool print identical output.

Usage: python3 packet_tool.py VECTORS
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "mission"))

import demo  # noqa: E402
from xtce.packet import Codec, PacketError, describe_alarms  # noqa: E402

DECODERS = {"tm": demo.CCSDSPacketKind, "tc": demo.CCSDSCommandKind,
            "hk": demo.Housekeeping}


def run(kind: str, data: bytes) -> str:
    cls = DECODERS[kind]
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
    alarms = packet.alarms()
    tail = f" | alarms: {describe_alarms(alarms)}" if alarms else ""
    return f"{packet.describe()} | {again}{tail}"


def main() -> None:
    for line in Path(sys.argv[1]).read_text().splitlines():
        line = line.split("#")[0].split()
        if line:
            print(run(line[0], bytes.fromhex(line[1])))


if __name__ == "__main__":
    main()
