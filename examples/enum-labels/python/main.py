"""Encode and decode a probe's status, described by modes.xtce, using the
code tcs-xtce generated into probe.py.

The status's mode is an enumeration, and everything that depends on it
names a label: Target is there only in SCIENCE mode and Station only in
DOWNLINK mode, a SafeStatus is a Status in SAFE mode, and the mode's alarm
is a warning in SAFE mode and a watch in TEST mode.  So this program names
labels too, never numbers.  The Rust, C and C++ versions of this program
print identical output.
"""

import sys

import probe
from tcs_xtce.packet import PacketError, describe_alarms


def fail(msg: str) -> None:
    print(f"enum-labels: {msg}", file=sys.stderr)
    sys.exit(1)


def encode(p) -> bytes:
    """Encodes a packet, prints it, and returns the bytes.

    Exits with an error if the packet can't be encoded.

    Args:
        p: The packet.

    Returns:
        The encoded packet.
    """
    try:
        b = p.to_bytes()
    except PacketError as e:
        fail(f"encoding {p.describe()}: {e}")
    print(f"encode {p.describe()} -> {b.hex()}")
    return b


def decode(b: bytes):
    """Decodes a status of either kind and prints it, with its alarms.

    Exits with an error if the bytes can't be decoded.

    Args:
        b: The encoded status.

    Returns:
        The Status or SafeStatus decoded.
    """
    try:
        p = probe.StatusKind.decode(b)
    except PacketError as e:
        fail(f"decoding {b.hex()}: {e}")
    print(f"decode {b.hex()} -> {p.describe()} | alarms: "
          f"{describe_alarms(p.alarms())}")
    return p


def main() -> None:
    """Shows the labels of some modes, then encodes and decodes statuses.

    Exits with an error if any result isn't what it must be.
    """
    # Labels name the values, and one label can name a range of them.
    for v in (0, 2, 20, 40):
        label = probe.ModeType.label_of(v)
        print(f"mode {v} is {label}" if label else f"mode {v} has no label")

    # One status in each mode but SAFE, which is a SafeStatus: its
    # restriction fixes the mode, so the structure has no mode field.
    statuses = [
        probe.Status(mode=probe.ModeType.IDLE, battery=80),
        probe.Status(mode=probe.ModeType.SCIENCE, battery=75, target=1234),
        probe.Status(mode=probe.ModeType.DOWNLINK, battery=70, station=7)]
    safe = probe.SafeStatus(battery=9, reason=probe.ReasonType.LOW_POWER)
    packets = [encode(s) for s in statuses] + [encode(safe)]

    # Decoding picks the SafeStatus by its mode's label, SAFE.
    for p, b in zip(statuses + [safe], packets):
        if decode(b) != p:
            fail(f"{b.hex()} decoded to something else")

    # A value in TEST's range, and one with no label: decoding keeps it.
    for h in ("1463", "2850"):
        if decode(bytes.fromhex(h)).to_bytes().hex() != h:
            fail(f"{h} encodes again as something else")

    # SCIENCE mode needs a Target.
    s = probe.Status(mode=probe.ModeType.SCIENCE, battery=50)
    try:
        b = s.to_bytes()
    except PacketError as e:
        print(f"encode {s.describe()} -> error:{e.kind} ({e})")
    else:
        fail(f"encoding {s.describe()} gave {b.hex()}")


if __name__ == "__main__":
    main()
