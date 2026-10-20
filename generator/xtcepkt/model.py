"""Language-neutral model of a mission's packets.

:mod:`xtcepkt.compile` builds this model from a mission's XTCE document and
the ``emit_*`` modules turn it into code.  Everything here is already
resolved: references point at objects, defaults are filled in and sizes are
in bits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Union


class Unsupported(Exception):
    """An XTCE construct the packet compiler does not handle."""


# --- encodings --------------------------------------------------------------


@dataclass
class IntEnc:
    """Integer on the wire."""

    bits: int
    encoding: str = "unsigned"  # unsigned | twosComplement | signMagnitude | onesComplement
    little_endian: bool = False

    @property
    def signed(self) -> bool:
        return self.encoding != "unsigned"


@dataclass
class FloatEnc:
    """IEEE 754 float on the wire (32 or 64 bits)."""

    bits: int
    little_endian: bool = False


@dataclass
class StringEnc:
    """Byte string on the wire.

    ``fixed_bits`` is set for strings that always occupy that many bits; for
    variable strings ``max_bits`` bounds the whole field.  The content ends at
    ``terminator`` or is preceded by a ``size_tag_bits`` length in bytes.
    """

    fixed_bits: Optional[int]
    max_bits: int
    terminator: Optional[int] = 0
    size_tag_bits: Optional[int] = None


@dataclass
class BinaryEnc:
    bits: int


Encoding = Union[IntEnc, FloatEnc, StringEnc, BinaryEnc]


# --- calibrators -------------------------------------------------------------


@dataclass
class Polynomial:
    terms: list[tuple[float, int]]  # (coefficient, exponent)

    @property
    def invertible(self) -> bool:
        return all(e <= 1 for _, e in self.terms) and self.slope != 0.0

    @property
    def slope(self) -> float:
        return sum(c for c, e in self.terms if e == 1)

    @property
    def intercept(self) -> float:
        return sum(c for c, e in self.terms if e == 0)


@dataclass
class Spline:
    points: list[tuple[float, float]]  # (raw, calibrated), sorted by raw
    order: int  # 0 (step) or 1 (linear)
    extrapolate: bool

    @property
    def invertible(self) -> bool:
        if self.order != 1:
            return False
        cal = [c for _, c in self.points]
        inc = all(a < b for a, b in zip(cal, cal[1:]))
        dec = all(a > b for a, b in zip(cal, cal[1:]))
        return inc or dec


Calibrator = Union[Polynomial, Spline]


# --- value types ---------------------------------------------------------------


@dataclass
class EnumLabel:
    label: str
    ident: str  # identifier in generated code
    value: int
    max_value: int


@dataclass
class VType:
    """Engineering type of a parameter or argument plus its encoding.

    ``kind`` is one of ``int``, ``float``, ``enum``, ``bool``, ``string`` and
    ``binary``.  For ``int`` the generated field is ``width`` bits wide and
    signed when the encoding is.
    """

    key: str
    name: str
    kind: str
    enc: Encoding
    calibrator: Optional[Calibrator] = None
    labels: list[EnumLabel] = field(default_factory=list)
    one_string: str = "True"
    zero_string: str = "False"
    ident: str = ""  # generated type name (enums only)
    doc: str = ""  # extra description for generated documentation
    # Integers: the smallest and largest valid value from ValidRange (or the
    # union of an argument's ValidRangeSet), when the document gives both.
    valid_range: Optional[tuple[int, int]] = None

    @property
    def width(self) -> int:
        bits = self.enc.bits
        return next(w for w in (8, 16, 32, 64) if bits <= w)

    @property
    def signed(self) -> bool:
        return isinstance(self.enc, IntEnc) and self.enc.signed


# --- containers --------------------------------------------------------------


@dataclass(eq=False)
class Local:
    """One value read from or written to a packet (a parameter or argument)."""

    id: int
    name: str  # XTCE name
    key: str  # qualified name of the parameter or argument
    vtype: VType
    optional: bool = False  # inside an IncludeCondition


@dataclass
class Seek:
    """Move the bit position before the next entry."""

    from_start: Optional[int]  # start-marker id, or None for previousEntry
    offset: int


@dataclass
class Mark:
    """Remember the current position as the start of an included container."""

    id: int


@dataclass
class Value:
    local: Local


@dataclass
class Fixed:
    """A constant bit pattern (command FixedValueEntry)."""

    bits: int
    value: int


@dataclass
class Cond:
    """Entries present only when all ``criteria`` hold (IncludeCondition)."""

    criteria: list["Criterion"]
    items: list["Item"]


Item = Union[Seek, Mark, Value, Fixed, Cond]


def walk(items: list) -> list:
    """The items, with the contents of conditional blocks spliced in."""
    out = []
    for it in items:
        if isinstance(it, Cond):
            out.extend(walk(it.items))
        else:
            out.append(it)
    return out


@dataclass(eq=False)
class Criterion:
    """``local <op> value``; ``value`` is an engineering value."""

    local: Local
    op: str
    value: object
    text: str  # as written in the XTCE file, for error messages


@dataclass(eq=False)
class Node:
    """A sequence container or meta-command, with its inheritance links."""

    kind: str  # "tm" or "tc"
    key: str  # qualified XTCE name
    name: str
    abstract: bool
    base: Optional["Node"] = None
    criteria: list[Criterion] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    children: list["Node"] = field(default_factory=list)
    ident: str = ""  # generated struct name
    fields: list[tuple[str, Local]] = field(default_factory=list)  # concrete only
    fixed: dict = field(default_factory=dict)  # Local -> engineering value
    max_bytes: int = 0
    error: Optional[str] = None

    def chain(self) -> list["Node"]:
        """This node and its bases, root first."""
        out = []
        n: Optional[Node] = self
        while n is not None:
            out.append(n)
            n = n.base
        return out[::-1]

    def all_items(self) -> list[Item]:
        return [i for n in self.chain() for i in n.items]

    def all_criteria(self) -> list[Criterion]:
        return [c for n in self.chain() for c in n.criteria]

    def descendants(self) -> list["Node"]:
        out = []
        for c in self.children:
            out.append(c)
            out.extend(c.descendants())
        return out


@dataclass(eq=False)
class Kind:
    """Dispatch over every concrete node in one inheritance tree."""

    ident: str
    root: Node
    members: list[Node]
    max_bytes: int


@dataclass
class Mission:
    name: str
    source: str
    types: list[VType]  # enumerations used by generated code
    nodes: list[Node]  # supported concrete nodes, document order
    kinds: list[Kind]
    roots: list[Node]  # roots of the trees behind ``kinds``
    skipped: list[str] = field(default_factory=list)
