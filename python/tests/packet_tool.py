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

import math
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
# 0x5A.  The decoding ones say how many bytes they take: all of them.
demo.HOOKS.update(expand=lambda b: bytes(x for x in b for _ in range(2)),
                  shrink=lambda b: ((len(b), bytes(b[::2])) if len(b) % 2 == 0
                                    and b[::2] == b[1::2] else None),
                  mask=lambda b: bytes(x ^ 0x5A for x in b),
                  unmask=lambda b: (len(b), bytes(x ^ 0x5A for x in b)))


def unstuff(b):
    """A length byte and that many bytes: those bytes."""
    return (1 + b[0], bytes(b[1:1 + b[0]])) if b and len(b) > b[0] else None


def ms(s):
    """Seconds as whole milliseconds, or None outside 0 to 2**32 - 1."""
    v = math.floor(s * 1000.0 + 0.5) if math.isfinite(s) else -1
    return v if 0 <= v < 1 << 32 else None


def var_from(b):
    """Decodes the demo's VarFrom: a LEB128 varint of milliseconds.

    Args:
        b: The bytes from the varint on; at most the first five are read.

    Returns:
        The number of bytes the varint takes and its value in seconds, or
        None if it doesn't end within five bytes.
    """
    v = 0
    for i, x in enumerate(b[:5]):
        v |= (x & 0x7F) << 7 * i
        if not x & 0x80:
            return i + 1, v / 1000.0
    return None


def var_to(s):
    """Encodes the demo's VarTo: seconds as a LEB128 varint of milliseconds.

    Args:
        s: The time in seconds.

    Returns:
        The varint, or None if the milliseconds are outside 0 to
        2**32 - 1.
    """
    v, out = ms(s), bytearray()
    if v is None:
        return None
    while v >= 0x80:
        out.append(v & 0x7F | 0x80)
        v >>= 7
    return bytes(out + bytes([v]))


# Stuff and Unstuff frame bytes with their length; MsFrom and MsTo are
# milliseconds in four bytes, VarFrom and VarTo milliseconds in a varint.
demo.HOOKS.update(stuff=lambda b: bytes([len(b)]) + bytes(b) if len(b) < 256
                  else None, unstuff=unstuff,
                  ms_from=lambda b: (4, int.from_bytes(b[:4], "big") / 1000.0),
                  ms_to=lambda s: None if ms(s) is None
                  else ms(s).to_bytes(4, "big"), var_from=var_from,
                  var_to=var_to)


def run(kind: str, data: bytes, t: float) -> str:
    """Decodes one vector, re-encodes it and describes the result.

    Args:
        kind: The decoder to use: ``tm``, ``tc`` or ``hk``.
        data: The packet.
        t:    The time the alarm monitor sees it at, in seconds.

    Returns:
        The line to print for the vector, as the module description
        gives it.
    """
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
    """Prints the line for each vector in the file named on the command line.

    Blank lines and ``#`` comments are skipped; each vector's index is the
    time the alarm monitor sees it at.
    """
    index = 0
    for line in Path(sys.argv[1]).read_text().splitlines():
        line = line.split("#")[0].split()
        if line:
            print(run(line[0], bytes.fromhex(line[1]), float(index)))
            index += 1


if __name__ == "__main__":
    main()
