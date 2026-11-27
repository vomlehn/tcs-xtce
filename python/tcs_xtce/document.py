"""XTCE documents bound to managed input and output buffers."""

from __future__ import annotations

from typing import Any, BinaryIO, Optional

from . import generated
from tcsxtce import DEFAULT_CAPACITY, ManagedBuffer
from .generated import ROOT_ELEMENT, SpaceSystemType

try:
    from . import _native
except ImportError as e:
    raise ImportError("tcs_xtce reads and writes XTCE with its extension "
                      "module, tcs_xtce._native, which is not built: in a "
                      "tcs-xtce checkout, run make python; a wheel always has "
                      "it", name=__name__) from e


def _record_name(cls: type) -> str:
    """Names a class of the XML binding, for the extension module.

    Args:
        cls: The class.

    Returns:
        Its name in :mod:`tcs_xtce.generated`.

    Raises:
        KeyError: The class is not one of the binding's records.
    """
    if getattr(generated, cls.__name__, None) is not cls:
        raise KeyError(cls)
    return cls.__name__


def to_xml(value: Any, tag: str, buf: ManagedBuffer,
           indent: bool = True) -> None:
    """Serializes ``value`` as the element ``tag`` into ``buf``, replacing it.

    Args:
        value:  The XTCE object to serialize.
        tag:    The name of its element.
        buf:    The buffer the XML goes into.
        indent: Whether to indent the XML.

    Raises:
        XtceError: The object can't be written as XML.
        KeyError:  Its class is not one of the binding's records.
    """
    buf.load(_native.to_xml(value, _record_name(type(value)), tag, indent))


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
        KeyError:  ``cls`` is not one of the binding's records.
    """
    return _native.from_xml(bytes(data), _record_name(cls), tag)


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
        to_xml(self.root, ROOT_ELEMENT, self._output, self.indent)
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
