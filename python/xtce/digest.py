"""An MD5 digest of what an XTCE document means.

Two programs that exchange packets were built from the same packet
definitions if their XTCE documents have the same digest, so each can
send the other its digest and compare, rather than describing the data in
every message.  The Rust (``tcs_xtce::digest``) and C/C++
(``xtce/digest.h``) runtimes compute the same digest.

The digest covers the document's elements, attributes and text, but not
what leaves its meaning the same: comments and processing instructions,
whitespace between elements and at the start and end of text, the order
of attributes, the namespace prefixes used, and the order of the children
of elements that hold unordered collections (``UNORDERED``), such as the
parameters of a ``ParameterSet``.

The document is read as XML 1.0 in UTF-8.  It is an error for it to be
malformed, to declare another encoding, to have a DOCTYPE, or to have a
root element other than ``SpaceSystem``.  It is the MD5 digest of this
encoding of the root element:

* an element is ``E`` followed by three fields: its name, its attributes
  and its content;
* a field is the number of bytes in it, in decimal, a colon, and the
  bytes;
* a name is ``{namespace}local`` for a name in a namespace, otherwise
  ``local``, in UTF-8; namespace declarations are not attributes;
* the attributes are each attribute's name then its value, as fields, in
  order of name (by their UTF-8 bytes);
* the content is the encodings of the child elements and of the text
  around them, in document order, or sorted by their bytes when the
  element's local name is in ``UNORDERED``;
* the text between two child elements (or before the first or after the
  last), with references and CDATA sections expanded and comments and
  processing instructions left out, has spaces, tabs, carriage returns
  and line feeds trimmed from both ends; if anything is left it is
  encoded as ``T`` followed by it as a field.

As XML requires, line ends are normalized to line feeds before anything
else, and tabs and line feeds in attribute values become spaces.
"""

from __future__ import annotations

import hashlib
import os
import xml.parsers.expat
from pathlib import Path
from typing import Union

from ._xml import XtceError

# Elements whose children may appear in any order without changing what the
# document means: collections of named things that are referred to by name,
# and lists whose items combine in an order-independent way.  Lists in which
# position matters (EntryList, MemberList, DimensionList, ArgumentList,
# SplineCalibrator points, and the first-match DiscreteLookupList,
# ContextCalibratorList and ContextAlarmList) are not here.  Every runtime
# has the same list.
UNORDERED = frozenset({
    # A SpaceSystem's nested SpaceSystems.
    "SpaceSystem",
    # Sets.
    "AlgorithmSet",
    "AliasSet",
    "AncillaryDataSet",
    "ArgumentTypeSet",
    "AuthorSet",
    "CommandContainerSet",
    "ContainerRefSet",
    "ContainerSet",
    "ExternalAlgorithmSet",
    "HistorySet",
    "MessageRefSet",
    "MessageSet",
    "MetaCommandSet",
    "NoteSet",
    "ParameterSet",
    "ParameterTypeSet",
    "ParametersToSuspendAlarmsOnSet",
    "RateInStreamSet",
    "ServiceSet",
    "StreamSet",
    "TriggerSet",
    "UnitSet",
    "ValidRangeSet",
    "VerifierSet",
    # Lists whose items combine without regard to order.
    "ANDedConditions",
    "ArgumentAssignmentList",
    "ComparisonList",
    "EnumerationAlarmList",
    "EnumerationList",
    "ORedConditions",
    "PolynomialCalibrator"})

SPACE = " \t\r\n"


def field(data: bytes) -> bytes:
    """``data`` prefixed with its length."""
    return b"%d:" % len(data) + data


def clark(name: str) -> str:
    """Expat's ``namespace}local`` as ``{namespace}local``."""
    return "{" + name if "}" in name else name


class _Element:
    def __init__(self, name: str, attrs: dict) -> None:
        self.name = name
        self.attrs = attrs
        self.items: list[bytes] = []
        self.text: list[str] = []

    def flush(self) -> None:
        t = "".join(self.text).strip(SPACE)
        if t:
            self.items.append(b"T" + field(t.encode()))
        self.text = []

    def encode(self) -> bytes:
        self.flush()
        attrs = sorted((clark(k).encode(), v.encode())
                       for k, v in self.attrs.items())
        a = b"".join(field(k) + field(v) for k, v in attrs)
        items = self.items
        if self.name.rsplit("}", 1)[-1] in UNORDERED:
            items = sorted(items)
        return (b"E" + field(clark(self.name).encode()) + field(a)
                + field(b"".join(items)))


def encoding(data: bytes) -> bytes:
    """The canonical encoding of the XTCE document ``data``."""
    p = xml.parsers.expat.ParserCreate("UTF-8", namespace_separator="}")
    p.buffer_text = True
    stack: list[_Element] = []
    out: list[bytes] = []

    def decl(version, enc, standalone) -> None:
        if enc is not None and enc.lower() not in ("utf-8", "utf8"):
            raise XtceError(f"encoding {enc} is not UTF-8")

    def doctype(*args) -> None:
        raise XtceError("documents with a DOCTYPE are not supported")

    def start(name, attrs) -> None:
        if not stack and name.rsplit("}", 1)[-1] != "SpaceSystem":
            raise XtceError("the root element is not SpaceSystem")
        if stack:
            stack[-1].flush()
        stack.append(_Element(name, attrs))

    def end(name) -> None:
        e = stack.pop()
        if stack:
            stack[-1].items.append(e.encode())
        else:
            out.append(e.encode())

    def text(data) -> None:
        if stack:
            stack[-1].text.append(data)

    p.XmlDeclHandler = decl
    p.StartDoctypeDeclHandler = doctype
    p.StartElementHandler = start
    p.EndElementHandler = end
    p.CharacterDataHandler = text
    try:
        p.Parse(data, True)
    except xml.parsers.expat.ExpatError as e:
        raise XtceError(xml.parsers.expat.errors.messages[e.code],
                        e.lineno) from None
    return out[0]


def digest_bytes(data: Union[str, bytes]) -> bytes:
    """The MD5 digest of the XTCE document ``data`` (the XML itself, as a
    string or as UTF-8 bytes), as 16 bytes: what programs send each other to
    compare.  Raises ``XtceError`` if it can't be read (see the module
    description)."""
    if isinstance(data, str):
        data = data.encode()
    return hashlib.md5(encoding(bytes(data)), usedforsecurity=False).digest()


def digest(data: Union[str, bytes]) -> str:
    """The digest of the XTCE document ``data``, in lowercase hex."""
    return digest_bytes(data).hex()


def digest_string(text: Union[str, bytes]) -> str:
    """The same as ``digest``: the digest of the document ``text``."""
    return digest(text)


def digest_file_bytes(path: Union[str, os.PathLike]) -> bytes:
    """The digest of the XTCE document in the file ``path``, as 16 bytes.
    Raises ``OSError`` if the file can't be read, and ``XtceError`` if the
    document can't."""
    return digest_bytes(Path(path).read_bytes())


def digest_file(path: Union[str, os.PathLike]) -> str:
    """The digest of the XTCE document in the file ``path``, in lowercase
    hex."""
    return digest_file_bytes(path).hex()
