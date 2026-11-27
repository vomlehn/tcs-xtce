"""XTCE documents bound to managed input and output buffers."""

from __future__ import annotations

import os
from typing import Any, BinaryIO, Optional

from . import _xml as X
from . import generated
from tcsxtce import DEFAULT_CAPACITY, ManagedBuffer
from .generated import (NAMESPACE, READERS, ROOT_ELEMENT, WRITERS,
                        SpaceSystemType)


def _backend():
    """Chooses what reads and writes the XML: the tcs-xtce crate, through
    the extension module ``tcs_xtce._native``, or this package's own
    Python, which is kept to compare the two until it is retired.

    ``TCS_XTCE_BACKEND`` chooses: ``rust`` needs the extension module,
    ``python`` doesn't use it, and anything else, or nothing, uses the
    module where it is built, as in every wheel, and the Python where it
    isn't, as in a checkout before ``make`` has built it.

    Returns:
        The extension module, or None for the Python.

    Raises:
        ImportError: ``TCS_XTCE_BACKEND`` is ``rust`` and the extension
                     module isn't built.
    """
    choice = os.environ.get("TCS_XTCE_BACKEND", "")
    if choice == "python":
        return None
    try:
        from . import _native
    except ImportError:
        if choice == "rust":
            raise
        return None
    return _native


_NATIVE = _backend()

# What reads and writes the XML: "rust" or "python".
BACKEND = "python" if _NATIVE is None else "rust"


def _record_name(value: Any) -> str:
    """Names the binding class of an object, for the extension module.

    Args:
        value: The object.

    Returns:
        Its class's name in :mod:`tcs_xtce.generated`.

    Raises:
        KeyError: Its class is not one of the binding's records, as the
                  Python writers' table raises.
    """
    cls = type(value)
    if getattr(generated, cls.__name__, None) is not cls:
        raise KeyError(cls)
    return cls.__name__


def to_xml(value: Any, tag: str, buf: ManagedBuffer, indent: bool = True,
           writer: Optional[X.Writer] = None) -> None:
    """Serializes ``value`` as the element ``tag`` into ``buf``, replacing it.

    Args:
        value:  The XTCE object to serialize.
        tag:    The name of its element.
        buf:    The buffer the XML goes into.
        indent: Whether to indent the XML.
        writer: A writer to reuse, or None to make a new one.
    """
    if _NATIVE is not None:
        buf.load(_NATIVE.to_xml(value, _record_name(value), tag, indent))
        return
    w = writer if writer is not None else X.Writer(indent)
    w.reset()
    w.indent = indent
    w.declaration()
    w.root_ns = NAMESPACE
    WRITERS[type(value)](w, value, tag)
    w.flush(buf)


def from_xml(data, cls: type, tag: str):
    """Deserializes an element ``tag`` of type ``cls`` from ``data``.

    Args:
        data: The bytes-like XML.
        cls:  The class of the object the element holds.
        tag:  The name the root element must have.

    Returns:
        The object read.

    Raises:
        XtceError: The XML is bad or its root isn't ``tag``.
    """
    if _NATIVE is not None:
        if getattr(generated, cls.__name__, None) is not cls:
            raise KeyError(cls)
        return _NATIVE.from_xml(bytes(data), cls.__name__, tag)
    root = X.parse(data)
    if root.name != tag:
        raise X.XtceError(f"root element is <{root.name}>, expected <{tag}>",
                          root.line)
    return READERS[cls](root)


class Document:
    """An XTCE ``SpaceSystem`` with the buffers used to (de)serialize it.

    Both buffers are preallocated when the document is created and reused by
    every :meth:`load` and :meth:`serialize`; they are released together with
    the document (or explicitly with :meth:`close` / a ``with`` block).

    Args:
        root:            The space system, or None for an empty one.
        input_capacity:  Bytes to preallocate for the input buffer.
        output_capacity: Bytes to preallocate for the output buffer.
        indent:          Whether :meth:`serialize` indents the XML.
    """

    def __init__(self, root: Optional[SpaceSystemType] = None, *,
                 input_capacity: int = DEFAULT_CAPACITY,
                 output_capacity: int = DEFAULT_CAPACITY,
                 indent: bool = True) -> None:
        self.root = root if root is not None else SpaceSystemType()
        self.indent = indent
        self._input = ManagedBuffer(input_capacity)
        self._output = ManagedBuffer(output_capacity)
        self._writer = X.Writer(indent)

    @classmethod
    def from_bytes(cls, data) -> "Document":
        """Deserializes a document; the input buffer is sized to fit ``data``.

        Args:
            data: The bytes-like XML.

        Returns:
            The new document.
        """
        size = max(len(data), DEFAULT_CAPACITY)
        doc = cls(input_capacity=size, output_capacity=size)
        doc.load(data)
        return doc

    @classmethod
    def from_file(cls, path) -> "Document":
        """Deserializes a document from a file.

        Args:
            path: The file's path.

        Returns:
            The new document.
        """
        doc = cls()
        with open(path, "rb") as f:
            doc.read_from(f)
        return doc

    def load(self, data) -> None:
        """Replace the root by deserializing ``data`` (copied into the input
        buffer)."""
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
