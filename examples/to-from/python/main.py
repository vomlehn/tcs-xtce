"""Write or read the words of the lorem ipsum paragraph as Word packets
described by to_from.xml, using the code tcs-xtce generated into to_from.py.

``main.py write`` writes the digest of to_from.xml, then one packet per word,
to standard output.  ``main.py read`` reads the digest from standard input and
checks that it is the digest of its own to_from.xml, so that both programs
agree on what a Word is; it then reads packets and prints the words as they
arrive, separated by spaces, and a newline at the end of the input. It checks
that every word arrives intact and in order.  Run it in the directory that
holds to_from.xml.

The Rust and C/C++ versions of this program write the same bytes and print
the same words, so any of them can read what any other wrote. The to-from
script in the directory above connects a writer to a reader.
"""

import sys

from tcs_xtce import XtceError
from tcs_xtce.digest import digest_file_bytes
from tcs_xtce.packet import Codec, PacketError

from to_from import Word

LOREM_IPSUM = ("Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed "
               "do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
               "Ut enim ad minim veniam, quis nostrud exercitation ullamco "
               "laboris nisi ut aliquip ex ea commodo consequat. Duis aute "
               "irure dolor in reprehenderit in voluptate velit esse cillum "
               "dolore eu fugiat nulla pariatur. Excepteur sint occaecat "
               "cupidatat non proident, sunt in culpa qui officia deserunt "
               "mollit anim id est laborum.")


# The XTCE file that describes a Word, and the size of its digest.
XTCE = "to_from.xml"
DIGEST_BYTES = 16

# The size of a Word's size tag: 32 bits, most significant byte first, as
# to_from.xml gives it.
TAG_BYTES = 4


class Failed(Exception):
    """A word went missing, arrived damaged or arrived out of order."""


def write() -> None:
    """Write every word to standard output, one Word packet each."""
    out = sys.stdout.buffer
    # The codec's output buffer is preallocated to Word.MAX_BYTES (36) and
    # reused for every packet.
    codec = Codec.for_packet(Word)
    # First the digest of the XTCE file, so the reader can check that it
    # uses the same definition of a Word.
    out.write(digest_file_bytes(XTCE))
    for text in LOREM_IPSUM.split():
        with codec.encode(Word(text=text)) as view:
            out.write(view)
    out.flush()


def read() -> None:
    """Read words from standard input until it ends, printing each one and
    checking it against the next expected word."""
    stream = sys.stdin.buffer
    expected = LOREM_IPSUM.split()
    # The codec's input buffer is preallocated to Word.MAX_BYTES and reused
    # for every packet.
    codec = Codec.for_packet(Word)
    count = 0

    # The writer's digest must be the digest of this program's XTCE file:
    # then both read and write a Word the same way.
    theirs = stream.read(DIGEST_BYTES)
    if len(theirs) != DIGEST_BYTES:
        raise Failed("input ended before the writer's XTCE digest")
    ours = digest_file_bytes(XTCE)
    if theirs != ours:
        raise Failed(f"the writer's XTCE digest {theirs.hex()} is not "
                     f"{ours.hex()}, the digest of {XTCE}")
    print(f"XTCE digest: {ours.hex()}, the same on both sides")

    # A packet is its 32-bit size tag followed by that many bytes of text, so
    # the tag says how much more of the input belongs to this word.
    while tag := stream.read(TAG_BYTES):
        if len(tag) != TAG_BYTES:
            raise Failed("input ended in the middle of a word")
        size = int.from_bytes(tag, "big")
        if TAG_BYTES + size > Word.MAX_BYTES:
            raise Failed(f"size tag {size} is too large")
        body = stream.read(size)
        if len(body) != size:
            raise Failed("input ended in the middle of a word")

        # Deserialize from the managed input buffer.
        codec.load(tag + body)
        word = codec.decode(Word)
        if count >= len(expected):
            raise Failed(f"unexpected extra word {word.text!r}")
        if word.text != expected[count]:
            raise Failed(f"word {count + 1} is {word.text!r}, expected "
                         f"{expected[count]!r}")

        print(("" if count == 0 else " ") + word.text, end="", flush=True)
        count += 1

    if count != len(expected):
        raise Failed(f"input ended after {count} words, before "
                     f"{expected[count]!r}")
    print()


def main() -> None:
    """Runs the reader or the writer, as the command line says.

    Exits with status 2 for a bad command line, and with an error if the
    program fails.
    """
    if sys.argv[1:] == ["read"]:
        action = read
    elif sys.argv[1:] == ["write"]:
        action = write
    else:
        sys.stderr.write("usage: main.py read|write\n")
        sys.exit(2)
    try:
        action()
    except (Failed, OSError, PacketError, XtceError) as e:
        sys.exit(f"to-from: {e}")


if __name__ == "__main__":
    main()
