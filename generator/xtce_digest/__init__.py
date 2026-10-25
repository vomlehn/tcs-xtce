"""xtce-digest: an MD5 digest of what an XTCE document means.

The digest covers the document's elements, attributes and text, so that a
change to any of them changes it, but not the changes that leave the
document meaning the same:

* comments and processing instructions;
* whitespace between elements, and at the start and end of text;
* the order of attributes, and the namespace prefixes used;
* the order of the children of elements whose children form an unordered
  collection (``UNORDERED``), such as the parameters of a ``ParameterSet``
  or the comparisons of a ``ComparisonList``.

Unordered children are taken in a fixed order, sorted by their encoding,
so documents that differ only in that order give the same digest.  The
children of every other element, such as the entries of an
``EntryList``, keep their order, as it carries meaning.

The digest is meant to show that the data structures and methods
generated from a document have not changed unexpectedly: it changes
whenever the document says something different.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET

# Elements whose children may appear in any order without changing what the
# document means: collections of named things that are referred to by name,
# and lists whose items combine in an order-independent way.  Lists in which
# position matters (EntryList, MemberList, DimensionList, ArgumentList,
# SplineCalibrator points, and the first-match DiscreteLookupList,
# ContextCalibratorList and ContextAlarmList) are not here.
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


def local_name(tag: str) -> str:
    """An element's name without its namespace."""
    return tag.rsplit("}", 1)[-1]


def field(data: bytes) -> bytes:
    """``data`` prefixed with its length, so that encodings can be
    concatenated without ambiguity."""
    return b"%d:" % len(data) + data


def text_item(text) -> list[bytes]:
    """The encoding of a piece of text content, or nothing if it is only
    whitespace."""
    t = (text or "").strip()
    return [b"T" + field(t.encode())] if t else []


def encode(elem: ET.Element) -> bytes:
    """The canonical encoding of an element and everything inside it."""
    attrs = b"".join(field(k.encode()) + field(v.encode())
                     for k, v in sorted(elem.attrib.items()))
    items = text_item(elem.text)
    for child in elem:
        items.append(encode(child))
        items += text_item(child.tail)
    if local_name(elem.tag) in UNORDERED:
        items.sort()
    body = b"".join(items)
    return b"E" + field(elem.tag.encode()) + field(attrs) + field(body)


def digest(data: bytes) -> str:
    """The MD5 digest, in hex, of the XTCE document ``data``."""
    # ElementTree drops comments and processing instructions, and names
    # elements and attributes by namespace URI rather than prefix.
    root = ET.fromstring(data)
    return hashlib.md5(encode(root), usedforsecurity=False).hexdigest()
