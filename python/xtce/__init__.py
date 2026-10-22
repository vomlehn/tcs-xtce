"""Serialization and deserialization of OMG XTCE documents: part of tcs-xtce,
which is flight software.

The data classes in :mod:`xtce.generated` are produced from the XTCE schema
(``references/SpaceSystem.xsd``) by the generator in ``generator/``; do not
edit them by hand.  Serialization uses managed buffers that are preallocated
and reused for as long as their owner (usually a :class:`Document`) lives.

>>> from xtce import Document, SpaceSystemType
>>> doc = Document(SpaceSystemType(name="Demo"))
>>> data = doc.to_bytes()
>>> Document.from_bytes(data).root == doc.root
True
"""

from ._buffer import DEFAULT_CAPACITY, ManagedBuffer
from ._xml import XtceError
from .document import Document, from_xml, to_xml
from .generated import *  # noqa: F401,F403
from .generated import __all__ as _generated_all

__all__ = [
    "DEFAULT_CAPACITY",
    "Document",
    "ManagedBuffer",
    "XtceError",
    "from_xml",
    "to_xml",
] + list(_generated_all)
