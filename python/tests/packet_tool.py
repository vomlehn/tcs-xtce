"""Decode and re-encode the packet vectors in tests/mission/vectors.txt.

For each vector prints ``<description of the decoded packet> | <hex of the
re-encoded packet>``, with ``error:<kind>`` in place of either on failure,
then `` | alarms: <field=level, ...>`` for a packet with alarms, and
`` | monitor: <field=level, ...>`` when one alarm monitor, kept across the
vectors (each at the time of its index), gives other levels, then
`` | derived: <output=value, ...>`` for a packet that triggers
MathAlgorithms.  The Rust and C/C++ versions of this tool print identical
output.

Usage: python3 packet_tool.py VECTORS
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "mission"))

import demo  # noqa: E402
from xtce.packet import (  # noqa: E402
    Codec, PacketError, describe_alarms, describe_outputs)

DECODERS = {"tm": demo.CCSDSPacketKind, "tc": demo.CCSDSCommandKind,
            "hk": demo.Housekeeping}


MONITOR = demo.AlarmMonitor()
# The demo mission's custom algorithms, the same in every version of this
# tool.
demo.HOOKS.update(is_even=lambda v: v[0] % 2.0 == 0.0,
                  min_max=lambda v: (min(v), max(v)),
                  bands=lambda v: int(v[0] / 50.0),
                  twice=lambda d, start, end: 2 * sum(d[start // 8:end // 8]))
# The demo's transforms: Expand doubles every byte, Shrink takes every
# other one (and gives nothing when a pair differs); Mask and Unmask are XOR
# 0x5A.
demo.HOOKS.update(expand=lambda b: bytes(x for x in b for _ in range(2)),
                  shrink=lambda b: (bytes(b[::2]) if len(b) % 2 == 0
                                    and b[::2] == b[1::2] else None),
                  mask=lambda b: bytes(x ^ 0x5A for x in b),
                  unmask=lambda b: bytes(x ^ 0x5A for x in b))


def run(kind: str, data: bytes, t: float) -> str:
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
    alarms = describe_alarms(packet.alarms())
    monitored = describe_alarms(packet.monitor(MONITOR, t))
    tail = f" | alarms: {alarms}" if alarms else ""
    if alarms and monitored != alarms:
        tail += f" | monitor: {monitored}"
    outputs = packet.algorithms()
    if outputs:
        tail += f" | derived: {describe_outputs(outputs)}"
    return f"{packet.describe()} | {again}{tail}"


def main() -> None:
    index = 0
    for line in Path(sys.argv[1]).read_text().splitlines():
        line = line.split("#")[0].split()
        if line:
            print(run(line[0], bytes.fromhex(line[1]), float(index)))
            index += 1


if __name__ == "__main__":
    main()
