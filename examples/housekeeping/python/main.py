"""Encode housekeeping Status packets described by housekeeping.xml into one
stream, then walk the stream and decode them again, using the code tcs-xtce
generated into housekeeping.py.

A Status packet shows three things the generated code does:

* PacketLength holds the number of bytes after it. Encoding computes it,
  and decoding checks it, so packets stored one after another can be split
  apart.
* Temperature, BusVoltage and HeaterPower are calibrated: the structure
  holds degrees, volts and watts, and the packet holds raw counts.
* HeaterPower is in the packet only when the heater is on.

The Rust and C/C++ versions of this program print identical output.
"""

import sys
from typing import NamedTuple, Optional

from xtce.packet import Codec, PacketError

from housekeeping import HeaterModeType, Status

OFF, ON = HeaterModeType.OFF, HeaterModeType.ON


class Reading(NamedTuple):
    """A Status to encode, and the values decoding it must give: the nearest
    the calibrators can represent."""
    status: Status
    temperature: float
    bus_voltage: float
    heater_power: Optional[float]


READINGS = [
    Reading(Status(heater_mode=OFF, temperature=21.53, bus_voltage=28.3),
            21.5, 28.25, None),
    Reading(Status(heater_mode=ON, temperature=-12.25, bus_voltage=27.5,
                   heater_power=4.5),
            -12.25, 27.5, 4.5),
    Reading(Status(heater_mode=ON, temperature=-20.01, bus_voltage=26.9,
                   heater_power=12.74),
            -20.0, 26.875, 12.734375),
    Reading(Status(heater_mode=OFF, temperature=5.0, bus_voltage=28.0),
            5.0, 28.0, None),
]


def fail(msg: str) -> None:
    sys.exit(f"housekeeping: {msg}")


def try_encode(codec: Codec, status: Status) -> None:
    """Encode a Status that must fail, and print why it did."""
    try:
        with codec.encode(status) as view:
            fail(f"encoding {status.describe()} gave {bytes(view).hex()}")
    except PacketError as e:
        print(f"encode {status.describe()} -> error:{e.kind} ({e})")


def try_decode(codec: Codec, data: bytes, why: str) -> None:
    """Decode bytes that must fail, and print why they did."""
    codec.load(data)
    try:
        status = codec.decode(Status)
        fail(f"decoding {data.hex()} gave {status.describe()}")
    except PacketError as e:
        print(f"decode {data.hex()} ({why}) -> error:{e.kind} ({e})")


def main() -> None:
    # The codec owns an input and an output buffer, both preallocated to
    # Status.MAX_BYTES (7) and reused for every packet.
    codec = Codec.for_packet(Status)

    # Encode each reading and append it to the stream, as a recorder would
    # store packets: one after another, with nothing between them.
    stream = bytearray()
    for r in READINGS:
        with codec.encode(r.status) as view:
            data = bytes(view)
        print(f"encode {r.status.describe()} -> {data.hex()}")
        stream += data
    print(f"stream: {len(READINGS)} packets, {len(stream)} bytes")

    # Walk the stream. Each packet starts with PacketLength, the number of
    # bytes after it, so the packet is 2 + PacketLength bytes long.
    at = 0
    for r in READINGS:
        if len(stream) - at < 2:
            fail(f"the stream ends inside a packet at byte {at}")
        size = 2 + int.from_bytes(stream[at:at + 2], "big")
        packet = bytes(stream[at:at + size])
        codec.load(packet)
        status = codec.decode(Status)
        print(f"decode at {at}: {size} bytes -> {status.describe()}")
        want = Status(heater_mode=r.status.heater_mode,
                      temperature=r.temperature, bus_voltage=r.bus_voltage,
                      heater_power=r.heater_power)
        if status != want:
            fail(f"decoded {status.describe()}, expected {want.describe()}")
        with codec.encode(status) as view:
            if bytes(view) != packet:
                fail(f"re-encoding {status.describe()} gave "
                     f"{bytes(view).hex()}, expected {packet.hex()}")
        at += size
    if at != len(stream):
        fail(f"{len(stream) - at} bytes after the last packet")

    # What the generated code rejects.
    try_encode(codec, Status(heater_mode=ON, temperature=20.0,
                             bus_voltage=28.0))
    try_encode(codec, Status(heater_mode=OFF, temperature=300.0,
                             bus_voltage=28.0))
    on = bytes(stream[5:12])
    try_decode(codec, on[:4], "cut short")
    off = bytearray(stream[0:5])
    off[1] += 1
    try_decode(codec, bytes(off) + b"\0", "PacketLength one too many")


if __name__ == "__main__":
    main()
