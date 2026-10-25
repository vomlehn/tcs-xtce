"""Record a rover's PUS telemetry, described by dt.xml, pus.xml and
rover.xml, in one stream, and decode it again, using the code tcs-xtce
generated into rover.py from the three documents together.

The stream starts with the digest of the set of documents, which doesn't
depend on the order they are listed in, so that whatever reads it can
check that it was built from the same definitions.  The Rust, C and C++
versions of this program print identical output.  Run it in the directory
that holds the documents.
"""

import sys

import rover
from xtce.digest import digest_files_bytes
from xtce.packet import PacketError

# The mission's documents: those the others build on first.
DOCUMENTS = ["dt.xml", "pus.xml", "rover.xml"]


def fail(msg: str) -> None:
    print(f"split-mission: {msg}", file=sys.stderr)
    sys.exit(1)


def hk(count: int, battery: float, motor_temp: float, turns: tuple) -> rover.Hk:
    """A housekeeping packet: PUS service 3, subservice 25."""
    return rover.Hk(ccsds_apid=100, ccsds_seq_flags=3, ccsds_seq_count=count,
                    pus_version=2, pus_counter=count, battery=battery,
                    motor_temp=motor_temp, left_turns=turns[0],
                    right_turns=turns[1])


def main() -> None:
    # The digest of the set is the same in any order.
    digest = digest_files_bytes(DOCUMENTS)
    print(f"set digest: {digest.hex()} ({', '.join(DOCUMENTS)})")
    shuffled = ["rover.xml", "dt.xml", "pus.xml"]
    if digest_files_bytes(shuffled) != digest:
        fail("the digest depends on the order of the documents")
    print(f"set digest: the same for {', '.join(shuffled)}")

    # The stream: the digest, then the packets, one after another.
    packets = [hk(1, 28.5, 41.25, (120, 118)),
               rover.Event(ccsds_apid=100, ccsds_seq_flags=3, ccsds_seq_count=2,
                           pus_version=2, pus_counter=2,
                           event=rover.EventType.OVERHEAT, event_data=85),
               hk(3, 27.75, 38.5, (0, 3))]
    stream = bytearray(digest)
    for p in packets:
        b = p.to_bytes()
        print(f"encode {p.describe()} -> {b.hex()}")
        stream += b
    print(f"stream: the set digest, then {len(packets)} packets, {len(stream)} "
          "bytes")

    # Reading it back: check the digest, then split the stream by each
    # packet's length (the bytes after its 6-byte header, minus one).
    if stream[:16] != digest_files_bytes(DOCUMENTS):
        fail("the stream's digest isn't that of these documents")
    print("set digest: the stream's matches")
    at = 16
    for p in packets:
        n = int.from_bytes(stream[at + 4:at + 6], "big") + 7
        try:
            got = rover.CcsdsKind.decode(bytes(stream[at:at + n]))
        except PacketError as e:
            fail(f"decoding at {at}: {e}")
        print(f"decode at {at}: {n} bytes -> {got.describe()}")
        if got != p:
            fail(f"{got.describe()} isn't {p.describe()}")
        at += n

    # A wheel can't turn more than 1000 times between reports.
    p = hk(4, 27.5, 30.0, (1001, 0))
    try:
        b = p.to_bytes()
    except PacketError as e:
        print(f"encode {p.describe()} -> error:{e.kind} ({e})")
    else:
        fail(f"encoding {p.describe()} gave {b.hex()}")


if __name__ == "__main__":
    main()
