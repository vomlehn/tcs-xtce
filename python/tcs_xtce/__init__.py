"""Serialization and deserialization of OMG XTCE documents: part of tcs-xtce,
which is flight software.

The data classes in :mod:`tcs_xtce.generated` are produced from the XTCE
schema (``references/SpaceSystem.xsd``) by :mod:`tcs_xtce.binding`; do not
edit them by hand.  Serialization uses managed buffers that are preallocated
and reused for as long as their owner (usually a :class:`Document`) lives.

The package also holds the compiler that turns a mission's XTCE into packet
codecs (:mod:`tcs_xtce.compiler`, run as ``python3 -m tcs_xtce`` or the
``tcs-xtce`` command), the packet runtime that the Python it writes uses
(:mod:`tcs_xtce.packet`), and the digest (:mod:`tcs_xtce.digest`, with the
``xtce-digest`` command in :mod:`tcs_xtce.digest_cli`).

The XML binding is loaded the first time one of its names is used, so that
the packet runtime and the generators need neither its 1.4 MB nor, before
``make generate`` has written it, its being there.

>>> from tcs_xtce import Document, SpaceSystemType
>>> doc = Document(SpaceSystemType(name="Demo"))
>>> data = doc.to_bytes()
>>> Document.from_bytes(data).root == doc.root
True
"""

from ._buffer import DEFAULT_CAPACITY, ManagedBuffer
from ._xml import XtceError

# What the package itself gives, beside the XML binding's names.
_OWN = ["DEFAULT_CAPACITY", "Document", "ManagedBuffer", "XtceError",
        "from_xml", "to_xml"]

# The package's modules and subpackages, which an import loads rather than
# the XML binding: `from tcs_xtce import packet` asks for one of these by
# name before importing it.
_MODULES = {"_buffer", "_xml", "binding", "compiler", "digest", "digest_cli",
            "document", "generated", "packet", "__main__"}


def __getattr__(name: str):
    """Gives a name of the XML binding, loading it the first time.

    Args:
        name: The name.

    Returns:
        What it names: ``Document``, ``from_xml`` or ``to_xml``, a data
        class or constant of the binding, or the list of every public name
        for ``__all__``.

    Raises:
        AttributeError: The package has no such name, or the name is one of
                        its modules, not yet imported.
        ImportError:    The binding has not been generated.
    """
    if name in _MODULES or name.startswith("__") and name != "__all__":
        raise AttributeError(f"module 'tcs_xtce' has no attribute {name!r}")
    try:
        from . import document, generated
    except ModuleNotFoundError as e:
        if e.name != f"{__name__}.generated":
            raise
        raise ImportError(f"tcs_xtce.{name} is in the XML binding, "
                          "tcs_xtce/generated.py, which has not been "
                          "generated: run make generate", name=__name__) from e
    if name == "__all__":
        return _OWN + list(generated.__all__)
    if name in ("Document", "from_xml", "to_xml"):
        return getattr(document, name)
    if name in generated.__all__:
        return getattr(generated, name)
    raise AttributeError(f"module 'tcs_xtce' has no attribute {name!r}")


def __dir__() -> list:
    """Lists the package's names, the XML binding's included.

    Returns:
        The names, sorted.
    """
    return sorted(set(globals()) | set(__getattr__("__all__")))
