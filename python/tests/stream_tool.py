"""Find the frames of the demo mission's streams, as tests/stream_check.sh
asks: each line of the file it is given is a stream's name and the next
bytes of that stream in hex, and it prints ``<stream>:`` then the frames
those bytes complete, in hex, or `` -``.  Each stream keeps its state from
line to line.  The Rust and C/C++ versions of this tool print identical
output.

Usage: python3 stream_tool.py STREAMS
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "mission"))

import demo  # noqa: E402


def descramble(data: bytes) -> bytes:
    """The demo's Descramble: each byte XOR 0x55."""
    return bytes(x ^ 0x55 for x in data)


def invert(bit: int) -> int:
    """The demo's Invert: the bit's complement, as AutoInvert does without
    it."""
    return bit ^ 1


def main() -> None:
    """Pushes each line of the file named on the command line into its
    stream and prints the frames those bytes complete.
    """
    streams = {"downlink": demo.DownlinkStream(), "uplink": demo.UplinkStream(),
               "deep": demo.DeepStream(), "beacon": demo.BeaconStream(),
               "relay": demo.RelayStream(),
               "scrambled": demo.ScrambledStream(descramble),
               "clear": demo.ClearStream(descramble, invert),
               "nested": demo.NestedStream(descramble, invert)}
    for line in Path(sys.argv[1]).read_text().splitlines():
        line = line.split("#")[0].split()
        if line:
            frames = streams[line[0]].push(bytes.fromhex(line[1]))
            print(f"{line[0]}: " + (" ".join(f.hex() for f in frames) or "-"))


if __name__ == "__main__":
    main()
