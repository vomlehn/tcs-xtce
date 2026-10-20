"""Build the Status record described by bit_packing.xml, using the code
tcsxtce --bit-packing generated into bit_packing.py, and show its packed
storage, a rejected value, and a serialize/deserialize round trip.

The Rust and C/C++ versions of this program print identical output.
"""

from xtce.packet import Codec, PacketError

from bit_packing import ModeType, Status


def main() -> None:
    # Packed fields are properties whose setters check that each value fits
    # its encoding; the constructor goes through them.
    status = Status(mode=ModeType.SCIENCE, level=19, enabled=True, offset=-3, count=600)
    print(f"value: {status.describe()}")

    # The five fields share 3 bytes, in the order XTCE packs encoded data.
    packed = status.packed
    print(f"storage: {len(packed)} bytes")
    for i, b in enumerate(packed):
        print(f"storage byte {i}: {b:08b}")

    # A value too large for its 5 bits is rejected and nothing changes.
    try:
        status.level = 32
        print("set level 32 -> accepted")
    except PacketError as e:
        print(f"set level 32 -> error:{e.kind} ({e}); level is still {status.level}")

    # The wire format is the XTCE one, most significant bit first.
    codec = Codec.for_packet(Status)
    with codec.encode(status) as view:
        data = bytes(view)
    print(f"serialize -> {data.hex()}")
    codec.load(data)
    back = codec.decode(Status)
    print(f"deserialize -> {back.describe()}")
    print(f"storage after deserializing matches: {str(back.packed == status.packed).lower()}")


if __name__ == "__main__":
    main()
