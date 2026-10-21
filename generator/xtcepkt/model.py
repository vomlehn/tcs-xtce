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
class DynSize:
    """A size or count that another value of the packet gives (an XTCE
    ``DynamicValue``): ``factor * (slope * value + intercept)``.

    Types hold it unbound: ``key`` names the parameter (its qualified name)
    or ``arg`` the argument whose value it uses, and ``name`` is the
    reference as written.  Each packet entry binds a copy to the ``local``
    it reads, decoded before the entry; inside an aggregate, a reference
    to an earlier member binds to that ``member`` (its field name)
    instead.  Binding sets ``max``: the largest value of ``(slope * value
    + intercept) / unit`` that the packet can hold, from the range of the
    value and the ``MAX_FIELD_BITS`` limit.
    """

    key: Optional[str] = None
    arg: Optional[str] = None
    name: str = ""
    member: Optional[str] = None
    # A DiscreteLookupList gives the value instead of a parameter or
    # argument: unbound, ``table`` is (path, DiscreteLookupList); bound,
    # ``lookup`` lists (criteria, value), the first match wins, and
    # ``default`` applies when none matches.
    table: Optional[tuple] = None
    lookup: Optional[list] = None
    default: int = 0
    # A reference to an aggregate member or array element (``P.a[2]``):
    # ``members`` are its steps (names and indexes), and binding sets
    # ``path`` to field names and Index steps.
    members: tuple = ()
    path: tuple = ()
    slope: float = 1.0
    intercept: float = 0.0
    factor: int = 1
    unit: int = 1  # 8 when the value is a size in bits, used in whole bytes
    local: Optional["Local"] = None
    max: int = 0

    @property
    def max_total(self) -> int:
        return self.factor * self.max

    @property
    def source(self) -> str:
        """The name of the value that gives the size, once bound."""
        if self.lookup is not None:
            return "a DiscreteLookupList"
        if self.local is not None:
            return ref_text(self.local.name, self.members)
        return self.name


@dataclass
class StringEnc:
    """Byte string on the wire.

    ``fixed_bits`` is set for strings that always occupy that many bits; for
    variable strings ``max_bits`` bounds the whole field.  The content ends at
    ``terminator`` or is preceded by a ``size_tag_bits`` length in bytes.
    With ``dyn`` the field occupies the bits another value gives, at most
    ``max_bits``, and the content ends inside it as for a fixed size.
    """

    fixed_bits: Optional[int]
    max_bits: int
    terminator: Optional[int] = 0
    size_tag_bits: Optional[int] = None
    dyn: Optional[DynSize] = None


@dataclass
class BinaryEnc:
    """Bytes on the wire: exactly ``bits``, or with ``dyn`` the bits another
    value gives, at most ``bits``.  With ``rest`` (an XTCE size of -1) the
    value is the rest of the packet, at most ``bits``; nothing may follow it.
    """

    bits: int
    dyn: Optional[DynSize] = None
    rest: bool = False

    @property
    def variable(self) -> bool:
        """Whether the number of bytes varies from packet to packet."""
        return self.dyn is not None or self.rest


@dataclass
class ArrayEnc:
    """``count`` values of type ``elem`` one after another, with ``gap`` bits
    between them (from a RepeatEntry Offset).  ``count`` is a number, or a
    DynSize when another value gives it."""

    elem: "VType"
    count: Union[int, DynSize]
    gap: int = 0
    dims: int = 1  # dimensions of an array type, flattened into ``count``
    # Each dimension's first index, and its size when every size is fixed
    # (empty otherwise): what element references and subsets index.
    starts: tuple = (0,)
    shape: tuple = ()

    @property
    def max_count(self) -> int:
        return self.count if isinstance(self.count, int) else self.count.max_total


@dataclass(frozen=True)
class Index:
    """A step into element ``i`` of a one-dimensional array of ``count``
    elements, in a reference such as ``P[2]``."""

    i: int
    count: int


def ref_text(name: str, steps) -> str:
    """A reference as XTCE writes it: ``name`` then ``.member`` and
    ``[index]`` steps (names, or ints and Index for elements)."""
    out = name
    for s in steps:
        if isinstance(s, Index):
            out += f"[{s.i}]"
        elif isinstance(s, int):
            out += f"[{s}]"
        else:
            out += "." + s
    return out


@dataclass
class Member:
    """One member of an aggregate: its XTCE name, its field name in
    generated code, its type, and the bits to skip before it."""

    name: str
    ident: str
    vtype: "VType"
    skip: int = 0


@dataclass
class AggregateEnc:
    """The members of an aggregate, encoded one after another in order,
    then ``pad`` bits.  An aggregate made from a repeated container has
    skips and padding from the container's entry locations and size."""

    members: list[Member]
    pad: int = 0


Encoding = Union[IntEnc, FloatEnc, StringEnc, BinaryEnc, ArrayEnc, AggregateEnc]

# The most bits a field whose size or count another value gives may take on
# the wire, unless the range of that value bounds it more tightly.  It keeps
# the storage of C structs bounded.
MAX_FIELD_BITS = 65536 * 8


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

    ``kind`` is one of ``int``, ``float``, ``enum``, ``bool``, ``string``,
    ``binary``, ``array`` and ``aggregate``.  For ``int`` the generated
    field is ``width`` bits wide and signed when the encoding is.  An
    ``array`` (an array type or a repeated entry) has an ArrayEnc and is a
    list of its elements, in the order they are encoded.  An ``aggregate``
    has an AggregateEnc and is a generated struct named ``ident``.
    """

    key: str
    name: str
    kind: str
    enc: Encoding
    calibrator: Optional[Calibrator] = None
    labels: list[EnumLabel] = field(default_factory=list)
    one_string: str = "True"
    zero_string: str = "False"
    ident: str = ""  # generated type name (enums and aggregates)
    doc: str = ""  # extra description for generated documentation
    # Integers: the smallest and largest valid value from ValidRange (or the
    # union of an argument's ValidRangeSet), when the document gives both.
    valid_range: Optional[tuple[int, int]] = None
    # Integers with a calibrator: the type's sizeInBits and signed, which
    # give the range of the calibrated value rather than the encoding.
    eng: Optional[tuple[int, bool]] = None

    @property
    def width(self) -> int:
        bits = self.eng[0] if self.eng else self.enc.bits
        return next(w for w in (8, 16, 32, 64) if bits <= w)

    @property
    def signed(self) -> bool:
        if self.eng:
            return self.eng[1]
        return isinstance(self.enc, IntEnc) and self.enc.signed

    @property
    def eng_limits(self) -> tuple[int, int]:
        """For a calibrated integer: the smallest value, and one more than
        the largest, that its generated field holds."""
        bits = self.width
        return (-(1 << (bits - 1)), 1 << (bits - 1)) if self.signed else (0, 1 << bits)

    @property
    def dyn(self) -> Optional[DynSize]:
        """The size or count another value gives this type, if any."""
        e = self.enc
        if isinstance(e, ArrayEnc):
            return e.count if isinstance(e.count, DynSize) else None
        return getattr(e, "dyn", None)

    @property
    def elem(self) -> "VType":
        """The element type of an array, or the type itself."""
        return self.enc.elem if isinstance(self.enc, ArrayEnc) else self

    def tree(self) -> list["VType"]:
        """This type and every type inside it (array elements, aggregate
        members), inner types first."""
        e = self.enc
        inner = [e.elem] if isinstance(e, ArrayEnc) else [m.vtype for m in e.members] if isinstance(e, AggregateEnc) else []
        return [t for i in inner for t in i.tree()] + [self]


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
    """Move the bit position before the next entry: ``offset`` bits, or the
    bits a bound DynSize gives (at most its ``max_total``)."""

    from_start: Optional[int]  # start-marker id, or None for previousEntry
    offset: Union[int, DynSize]

    @property
    def max_offset(self) -> int:
        return self.offset.max_total if isinstance(self.offset, DynSize) else self.offset


@dataclass
class Mark:
    """Remember the current position as the start of an included container."""

    id: int


@dataclass
class Value:
    local: Local


@dataclass
class Pad:
    """``bits`` zero bits, ignored when decoding but required to be there
    (the rest of a container with a fixed size)."""

    bits: int


@dataclass
class Fixed:
    """A constant bit pattern (command FixedValueEntry)."""

    bits: int
    value: int


@dataclass
class Cond:
    """Entries present only when all ``criteria`` hold (IncludeCondition)."""

    criteria: list["Term"]
    items: list["Item"]


Item = Union[Seek, Mark, Value, Fixed, Pad, Cond]


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
    """``local <op> value``; ``value`` is an engineering value.  With a
    ``path`` (field names of aggregate members and Index steps into
    arrays, from a reference such as ``P.a[2]``) the criterion is on that
    part of ``local``, of type ``target``.  With ``other`` (and ``other_path``) it compares two
    values of the packet instead, and ``value`` is unused."""

    local: Local
    op: str
    value: object
    text: str  # as written in the XTCE file, for error messages
    path: tuple = ()
    target: Optional["VType"] = None
    other: Optional[Local] = None
    other_path: tuple = ()

    @property
    def vtype(self) -> "VType":
        return self.target if self.target is not None else self.local.vtype


@dataclass(eq=False)
class AllOf:
    """Holds when every term holds (a BooleanExpression ANDedConditions)."""

    terms: list
    text: str


@dataclass(eq=False)
class AnyOf:
    """Holds when any term holds (a BooleanExpression ORedConditions)."""

    terms: list
    text: str


def term_indexes(t) -> list:
    """(local, path before the step, count) for every array element that
    a term reads, so that encoding can check those arrays' lengths before
    it indexes them."""
    if isinstance(t, Criterion):
        out = []
        for lc, path in ((t.local, t.path), (t.other, t.other_path)):
            if lc is not None:
                out += [(lc, path[:k], s.count) for k, s in enumerate(path) if isinstance(s, Index)]
        return out
    return [x for u in t.terms for x in term_indexes(u)]


# A criterion, or a tree of them.  Lists of terms hold when all of them do.
Term = Union[Criterion, AllOf, AnyOf]


def term_locals(t: Term) -> list:
    """The values a term reads."""
    if isinstance(t, Criterion):
        return [t.local] + ([t.other] if t.other is not None else [])
    return [lc for x in t.terms for lc in term_locals(x)]


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
    aggregates: list[VType]  # aggregates used by generated code, inner ones first
    nodes: list[Node]  # supported concrete nodes, document order
    kinds: list[Kind]
    roots: list[Node]  # roots of the trees behind ``kinds``
    skipped: list[str] = field(default_factory=list)
