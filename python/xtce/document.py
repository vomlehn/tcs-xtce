"""XTCE documents bound to managed input and output buffers."""

from __future__ import annotations

from typing import Any, BinaryIO, Optional

from . import _xml as X
from ._buffer import DEFAULT_CAPACITY, ManagedBuffer
from .generated import NAMESPACE, READERS, ROOT_ELEMENT, WRITERS, SpaceSystemType


def to_xml(value: Any, tag: str, buf: ManagedBuffer, indent: bool = True, writer: Optional[X.Writer] = None) -> None:
    """Serialize ``value`` as the element ``tag`` into ``buf`` (replacing it)."""
    w = writer if writer is not None else X.Writer(indent)
    w.reset()
    w.indent = indent
    w.declaration()
    w.root_ns = NAMESPACE
    WRITERS[type(value)](w, value, tag)
    w.flush(buf)


def from_xml(data, cls: type, tag: str):
    """Deserialize an element ``tag`` of type ``cls`` from bytes-like ``data``."""
    root = X.parse(data)
    if root.name != tag:
        raise X.XtceError(f"root element is <{root.name}>, expected <{tag}>", root.line)
    return READERS[cls](root)


class Document:
    """An XTCE ``SpaceSystem`` with the buffers used to (de)serialize it.

    Both buffers are preallocated when the document is created and reused by
    every :meth:`load` and :meth:`serialize`; they are released together with
    the document (or explicitly with :meth:`close` / a ``with`` block).
    """

    def __init__(
        self,
        root: Optional[SpaceSystemType] = None,
        *,
        input_capacity: int = DEFAULT_CAPACITY,
        output_capacity: int = DEFAULT_CAPACITY,
        indent: bool = True,
    ) -> None:
        self.root = root if root is not None else SpaceSystemType()
        self.indent = indent
        self._input = ManagedBuffer(input_capacity)
        self._output = ManagedBuffer(output_capacity)
        self._writer = X.Writer(indent)

    @classmethod
    def from_bytes(cls, data) -> "Document":
        """Deserialize a document; the input buffer is sized to fit ``data``."""
        size = max(len(data), DEFAULT_CAPACITY)
        doc = cls(input_capacity=size, output_capacity=size)
        doc.load(data)
        return doc

    @classmethod
    def from_file(cls, path) -> "Document":
        doc = cls()
        with open(path, "rb") as f:
            doc.read_from(f)
        return doc

    def load(self, data) -> None:
        """Replace the root by deserializing ``data`` (copied into the input buffer)."""
        self._input.load(data)
        self._parse_input()

    def read_from(self, stream: BinaryIO) -> None:
        """Replace the root by deserializing everything read from ``stream``."""
        self._input.load_from(stream)
        self._parse_input()

    def _parse_input(self) -> None:
        with self._input.view() as view:
            self.root = from_xml(view, SpaceSystemType, ROOT_ELEMENT)

    def serialize(self) -> memoryview:
        """Serialize into the output buffer and return a zero-copy view of it.

        The view is valid until the next serialization; release it before
        serializing again if the output may need to grow.
        """
        to_xml(self.root, ROOT_ELEMENT, self._output, self.indent, self._writer)
        return self._output.view()

    def to_bytes(self) -> bytes:
        """Serialize and return a copy of the bytes."""
        with self.serialize() as view:
            return bytes(view)

    def write_to(self, stream: BinaryIO) -> None:
        with self.serialize() as view:
            stream.write(view)

    @property
    def input(self) -> ManagedBuffer:
        return self._input

    @property
    def output(self) -> ManagedBuffer:
        return self._output

    def close(self) -> None:
        """Release both buffers immediately."""
        self._input = ManagedBuffer(0)
        self._output = ManagedBuffer(0)

    def __enter__(self) -> "Document":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
