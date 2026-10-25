"""Send two arrays of lorem ipsum lines from one thread to another over one
TCP connection, each as a Paragraph packet described by variable_array.xml,
using the code tcs-xtce generated into variable_array.py.

A Paragraph is a two-dimensional array whose dimensions both vary: an array
of lines, each an array of words. The sender first writes the digest of
variable_array.xml, which the receiver checks against the digest of its own
copy, so that both agree on the packets. Before each packet the sender writes a
Length packet, also described by variable_array.xml, giving its size in
bytes, so the receiver knows how many bytes to read; it then writes the
whole Paragraph in a single write. The receiver checks that every line and
word arrived intact and in order, then prints each line with its words
separated by spaces. The Rust and C/C++ versions of this program print
identical output. Run it in the directory that holds variable_array.xml.
"""

import socket
import sys
import threading

from xtce import XtceError
from xtce.digest import digest_file_bytes
from xtce.packet import Codec, PacketError

from variable_array import Length, Line, Paragraph

# The lorem ipsum paragraph in five lines, as two arrays: three lines, then
# two.
PARAGRAPHS = [["Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed "
               "do eiusmod tempor incididunt ut",
               "labore et dolore magna aliqua. Ut enim ad minim veniam, quis "
               "nostrud exercitation ullamco",
               "laboris nisi ut aliquip ex ea commodo consequat. Duis aute "
               "irure dolor in reprehenderit in"],
              ["voluptate velit esse cillum dolore eu fugiat nulla pariatur. "
               "Excepteur sint occaecat",
               "cupidatat non proident, sunt in culpa qui officia deserunt "
               "mollit anim id est laborum."]]


# The XTCE file that describes the packets, and the size of its digest.
XTCE = "variable_array.xml"
DIGEST_BYTES = 16


class Failed(Exception):
    """An array arrived damaged or incomplete."""


def paragraph(p: int) -> Paragraph:
    """Array ``p`` as a Paragraph: one Line per line, one word per element."""
    lines = []
    for line in PARAGRAPHS[p]:
        words = line.split(" ")
        # WordCount precedes Words and gives their number.
        lines.append(Line(word_count=len(words), words=words))
    return Paragraph(lines=lines)


def difference(got: Paragraph, want: Paragraph):
    """Describe the first difference between ``got`` and ``want``, if any."""
    if len(got.lines) != len(want.lines):
        return f"received {len(got.lines)} lines, expected {len(want.lines)}"
    for i, (g, w) in enumerate(zip(got.lines, want.lines), 1):
        if len(g.words) != len(w.words):
            return f"line {i} has {len(g.words)} words, expected {len(w.words)}"
        for j, (gw, ww) in enumerate(zip(g.words, w.words), 1):
            if gw != ww:
                return f"line {i} word {j} is {gw!r}, expected {ww!r}"
    return None


def send(address, errors: list) -> None:
    """Connect to ``address``, send the digest of the XTCE file, then send
    each array: a Length packet, then the Paragraph packet in a single
    write. Then close the connection."""
    try:
        with socket.create_connection(address) as sock:
            # First the digest, so the receiver can check that it uses the
            # same definitions of Length and Paragraph.
            sock.sendall(digest_file_bytes(XTCE))
            # The codecs' output buffers are preallocated to
            # Paragraph.MAX_BYTES and Length.MAX_BYTES (4) and reused for both
            # arrays.
            codec = Codec.for_packet(Paragraph)
            length_codec = Codec.for_packet(Length)
            for p in range(len(PARAGRAPHS)):
                with codec.encode(paragraph(p)) as view:
                    with length_codec.encode(
                            Length(byte_count=len(view))) as length:
                        sock.sendall(length)
                    n = sock.send(view)
                    if n != len(view):
                        raise Failed(f"one write sent {n} of {len(view)} bytes")
        # Leaving the with block closes the socket, which tells the receiver
        # there are no more arrays.
    except (Failed, OSError, PacketError, XtceError) as e:
        errors.append(e)


def receive(listener: socket.socket) -> None:
    """Accept the connection, check the sender's XTCE digest, and receive
    arrays until it closes: for each, read and decode the Length packet,
    then read that many bytes, decode the Paragraph, check it and print
    it."""
    conn, _ = listener.accept()
    # The codecs' input buffers are preallocated to Paragraph.MAX_BYTES and
    # Length.MAX_BYTES (4) and reused for both arrays.
    codec = Codec.for_packet(Paragraph)
    length_codec = Codec.for_packet(Length)
    p = 0
    with conn, conn.makefile("rb") as stream:
        # The sender's digest must be the digest of this side's XTCE file:
        # then both read and write the packets the same way.
        theirs = stream.read(DIGEST_BYTES)
        if len(theirs) != DIGEST_BYTES:
            raise Failed("stream closed before the sender's XTCE digest")
        ours = digest_file_bytes(XTCE)
        if theirs != ours:
            raise Failed(f"the sender's XTCE digest {theirs.hex()} is not "
                         f"{ours.hex()}, the digest of {XTCE}")
        print(f"XTCE digest: {ours.hex()}, the same on both sides")
        while length := stream.read(Length.MAX_BYTES):
            if len(length) != Length.MAX_BYTES:
                raise Failed("stream closed in the middle of an array")
            length_codec.load(length)
            size = length_codec.decode(Length).byte_count
            if size > Paragraph.MAX_BYTES:
                raise Failed(f"length {size} is more than a Paragraph's "
                             f"{Paragraph.MAX_BYTES}")
            packet = stream.read(size)
            if len(packet) != size:
                raise Failed("stream closed in the middle of an array")
            if p == len(PARAGRAPHS):
                raise Failed("received more arrays than were sent")

            # Deserialize from the managed input buffer.
            codec.load(packet)
            got = codec.decode(Paragraph)
            diff = difference(got, paragraph(p))
            if diff:
                raise Failed(f"array {p + 1}: {diff}")

            for line in got.lines:
                print(" ".join(line.words))
            p += 1

    if p != len(PARAGRAPHS):
        raise Failed(f"stream closed after {p} of {len(PARAGRAPHS)} arrays")


def main() -> None:
    # Listen on a port the system chooses, on the loopback interface only.
    with socket.create_server(("127.0.0.1", 0)) as listener:
        errors: list = []
        sender = threading.Thread(target=send,
                                  args=(listener.getsockname(), errors))
        sender.start()
        try:
            receive(listener)
        except (Failed, OSError, PacketError, XtceError) as e:
            sys.exit(f"variable-array: {e}")
        finally:
            sender.join()
        if errors:
            sys.exit(f"variable-array: sender: {errors[0]}")


if __name__ == "__main__":
    main()
