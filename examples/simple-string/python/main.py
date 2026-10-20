"""Serialize and deserialize the SimpleString structure described by
simple_string.xml, using the code tcsxtce generated into simple_string.py.

The Rust and C/C++ versions of this program print identical output.
"""

from xtce.packet import Codec, PacketError, describe_str

from simple_string import SimpleString


def main() -> None:
    # The codec owns an input and an output buffer, both preallocated to
    # SimpleString.MAX_BYTES (8) and reused for every packet.
    codec = Codec.for_packet(SimpleString)
    capacity = (codec.input_capacity, codec.output_capacity)

    for text in ["ABCDEFGH", "Hi", "", "héllo", "ABCDEFGHI"]:
        value = SimpleString(text=text)

        # Serialize into the managed output buffer.
        try:
            with codec.encode(value) as view:
                data = bytes(view)
        except PacketError as e:
            print(f"serialize {describe_str(text)} -> error:{e.kind} ({e})")
            continue
        print(f"serialize {describe_str(text)} -> {data.hex()}")

        # Deserialize from the managed input buffer.
        codec.load(data)
        back = codec.decode(SimpleString)
        print(f"deserialize {data.hex()} -> {back.describe()}")
        assert back == value

    unchanged = capacity == (codec.input_capacity, codec.output_capacity)
    print(f"buffers reused without reallocation: {str(unchanged).lower()}")


if __name__ == "__main__":
    main()
