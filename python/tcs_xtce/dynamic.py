"""The tcs-xtce run-time codec: reads XTCE documents while a program runs
and packs and unpacks their packets, with no code generated for them.

This is the Rust codec, tcs-xtce-dynamic, through the extension module
``tcs_xtce._native``; it does nothing the Rust codec doesn't.  See
docs/runtime-codec-design.rst.

    m = Mission([open("demo.xtce", "rb").read()])
    root = m.container(m.top, "/Demo/CCSDSPacket")
    scratch, store = m.scratch(), m.store()
    node = m.unpack(root, packet, scratch)
    store.commit(scratch)
    store.get(m.parameter(m.top, "/Demo/SeqCount"))     # 42
    m.pack(node, store)                                 # the bytes again

Values are Python's own: ``int`` (an enumeration's value, and integers of
any width), ``float`` (a time is its seconds), ``bool``, ``str`` and
``bytes``; ``Store.set`` also takes an enumeration's label.

``Mission.set_algorithm`` supplies a function for one of the algorithms
``Mission.algorithms`` lists, of the kind it gives:

- ``criterion``: ``f(inputs) -> bool``
- ``algorithm``: ``f(inputs) -> list[float] | None``
- ``alarm``: ``f(inputs) -> int``, 0 (normal) to 5 (severe)
- ``checksum``: ``f(data, start_bit, end_bit) -> int``
- ``binary_from``: ``f(data) -> (bytes_taken, decoded) | None``
- ``binary_to``: ``f(data) -> encoded | None``
- ``time_from``: ``f(data) -> (bytes_taken, seconds) | None``
- ``time_to``: ``f(seconds) -> encoded | None``
- ``stream_decode``: ``f(data) -> bytes | list[bytes] | None``
- ``bit_invert``: ``f(bit) -> int``

where ``inputs`` is a list of floats and ``data`` bytes.  ``None`` means
the data doesn't decode or encode; an exception a function raises is
raised from the call of the codec that called it.
"""

from ._native import dynamic as _dynamic

Mission = _dynamic.Mission
Store = _dynamic.Store
Scratch = _dynamic.Scratch
Monitor = _dynamic.Monitor
Deframer = _dynamic.Deframer
Space = _dynamic.Space
Node = _dynamic.Node
Slot = _dynamic.Slot
PacketError = _dynamic.PacketError
UnsupportedError = _dynamic.UnsupportedError
SetError = _dynamic.SetError

__all__ = ["Deframer", "Mission", "Monitor", "Node", "PacketError", "Scratch",
           "SetError", "Slot", "Space", "Store", "UnsupportedError"]
