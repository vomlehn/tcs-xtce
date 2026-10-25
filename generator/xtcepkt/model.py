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


# How the groups of 8 bits of a field combine: "be" (the first group most
# significant), "le" (least significant), or a tuple giving each whole
# byte's significance (an XTCE byte order list).
Order = Union[str, tuple]


@dataclass
class IntEnc:
    """Integer on the wire.

    ``lsb`` (XTCE ``bitOrder="leastSignificantBitFirst"``) and ``order``
    give the bit and byte order; see ``BitReader.uint_bits`` in the
    runtimes.  BCD and packedBCD values are unsigned decimal digits."""

    bits: int
    # unsigned | twosComplement | signMagnitude | onesComplement | BCD |
    # packedBCD
    encoding: str = "unsigned"
    order: Order = "be"
    lsb: bool = False

    @property
    def signed(self) -> bool:
        return (self.encoding in ("twosComplement", "signMagnitude",
                                  "onesComplement"))

    @property
    def bcd(self) -> bool:
        return self.encoding in ("BCD", "packedBCD")

    @property
    def little_endian(self) -> bool:
        return self.order == "le"


@dataclass
class FloatEnc:
    """Float on the wire: ``fmt`` is IEEE754, MILSTD_1750A, DEC, IBM or TI,
    with the sizes each allows; bit and byte order as for IntEnc."""

    bits: int
    fmt: str = "IEEE754"
    order: Order = "be"
    lsb: bool = False

    @property
    def little_endian(self) -> bool:
        return self.order == "le"


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
    instance: int = 0  # of the value: 0 the last decoded, -1 the one before
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
    the ``terminator`` bytes (where a code unit of the ``charset`` starts)
    or is preceded by a ``size_tag_bits`` length in bytes.  With ``dyn`` the
    field occupies the bits another value gives, at most ``max_bits``, and
    the content ends inside it as for a fixed size.
    """

    fixed_bits: Optional[int]
    max_bits: int
    terminator: Optional[bytes] = b"\x00"
    size_tag_bits: Optional[int] = None
    dyn: Optional[DynSize] = None
    charset: str = "UTF-8"
    lsb: bool = False  # bitOrder leastSignificantBitFirst


@dataclass
class BinaryEnc:
    """Bytes on the wire: exactly ``bits``, or with ``dyn`` the bits another
    value gives, at most ``bits``.  With ``rest`` (an XTCE size of -1) the
    value is the rest of the packet, at most ``bits``; nothing may follow it.
    """

    bits: int
    dyn: Optional[DynSize] = None
    rest: bool = False
    lsb: bool = False  # bitOrder leastSignificantBitFirst

    @property
    def variable(self) -> bool:
        """Whether the number of bytes varies from packet to packet."""
        return self.dyn is not None or self.rest


@dataclass
class ArrayEnc:
    """``count`` values of type ``elem`` one after another, with ``gap`` bits
    between them (from a RepeatEntry Offset).  ``count`` and ``gap`` are
    numbers, or DynSizes when another value gives them."""

    elem: "VType"
    count: Union[int, DynSize]
    gap: Union[int, DynSize] = 0
    dims: int = 1  # dimensions of an array type, flattened into ``count``
    # Each dimension's first index, and its size when every size is fixed
    # (empty otherwise): what element references and subsets index.
    starts: tuple = (0,)
    shape: tuple = ()
    # Each dimension's size, None for the one another value gives.
    sizes: tuple = ()

    @property
    def max_count(self) -> int:
        return (self.count if isinstance(self.count, int)
                else self.count.max_total)

    @property
    def max_gap(self) -> int:
        return self.gap if isinstance(self.gap, int) else self.gap.max_total


@dataclass(frozen=True)
class Index:
    """A step into element ``i`` of a one-dimensional array of ``count``
    elements, in a reference such as ``P[2]``.  ``count`` is None when
    another value gives it: the element may then be missing, which reading
    it reports."""

    i: int
    count: Optional[int]


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
    order: int  # the order of every segment, when ``orders`` is empty
    extrapolate: bool
    # The order of the segment from each point, when the points' own orders
    # differ from 0 or 1 everywhere; a segment of order k interpolates
    # through k + 1 points.
    orders: tuple = ()

    @property
    def invertible(self) -> bool:
        if self.order != 1 or self.orders:
            return False
        cal = [c for _, c in self.points]
        inc = all(a < b for a, b in zip(cal, cal[1:]))
        dec = all(a > b for a, b in zip(cal, cal[1:]))
        return inc or dec


@dataclass(eq=False)
class ValueRef:
    """Another value of the packet that a calibrator uses (a
    ParameterInstanceRefOperand).  Unbound, ``ref`` is the XTCE reference
    and ``path`` the SpaceSystem it is in; bound, ``local`` is the value,
    ``member`` the path into it, and ``vtype`` the type there."""

    ref: object
    path: tuple
    local: Optional["Local"] = None
    member: tuple = ()
    vtype: Optional["VType"] = None


# The operators of a MathOperationCalibrator, by the number of operands they
# take.  swap, drop, dup and over rearrange the stack, so the compiler
# applies them.
MATH_UNARY = ("ln", "log", "e^x", "1/x", "x!", "tan", "cos", "sin", "atan",
              "acos", "asin", "tanh", "cosh", "sinh", "atanh", "acosh", "asinh",
              "abs", "int", "!", "~")
MATH_BINARY = ("+", "-", "*", "/", "%", "^", "y^x", "atan2", "<<", ">>", "&",
               "|", "&&", "||", ">", ">=", "<", "<=", "==", "!=", "min", "max",
               "xor", "div")


@dataclass
class MathOp:
    """A MathOperationCalibrator as an expression tree: ``("const", x)``,
    ``("raw",)`` for the value being calibrated, ``("value", ValueRef)``
    for another value of the packet, or ``(operator, operand, ...)``."""

    expr: tuple
    invertible = False

    def refs(self) -> list:
        """The ValueRefs it uses."""
        out = []

        def visit(e):
            if e[0] == "value":
                out.append(e[1])
            elif e[0] not in ("const", "raw"):
                for a in e[1:]:
                    visit(a)
        visit(self.expr)
        return out


@dataclass
class ContextCal:
    """A ContextCalibratorList and the default calibrator (or None): the
    first case whose criteria hold picks its calibrator.  Unbound, a case
    is (path, ContextMatch, calibrator); bound, (terms, calibrator).
    Encoding inverts the calibrator the criteria pick, failing with
    NotInvertible if that one can't be inverted."""

    cases: list
    default: Optional[object]
    bound: bool = False
    invertible = True


Calibrator = Union[Polynomial, Spline, MathOp, ContextCal]


def needs_binding(cal) -> bool:
    """Whether a calibrator uses other values of the packet."""
    if isinstance(cal, ContextCal):
        return not cal.bound
    return isinstance(cal, MathOp) and any(r.local is None for r in cal.refs())


# --- value types
# ---------------------------------------------------------------


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
        return ((-(1 << (bits - 1)), 1 << (bits - 1)) if self.signed
                else (0, 1 << bits))

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
        inner = ([e.elem] if isinstance(e, ArrayEnc)
                 else [m.vtype for m in e.members]
                 if isinstance(e, AggregateEnc) else [])
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
    # A packet length field: the generated code computes it, as the number
    # of bytes after it plus ``length``, and checks it when decoding.
    length: Optional[int] = None
    # A field that holds a checksum (an ErrorDetectCorrect value): the
    # generated code computes it, and checks it when decoding.
    checksum: bool = False

    @property
    def computed(self) -> bool:
        """Whether the generated code computes the value: it is not a field
        of the generated struct."""
        return self.length is not None or self.checksum


@dataclass
class Seek:
    """Move the bit position before the next entry: ``offset`` bits, or the
    bits a bound DynSize gives (at most its ``max_total``)."""

    from_start: Optional[int]  # start-marker id, or None for previousEntry
    offset: Union[int, DynSize]

    @property
    def max_offset(self) -> int:
        return (self.offset.max_total if isinstance(self.offset, DynSize)
                else self.offset)


@dataclass
class EndSeek:
    """While compiling: an entry whose end is ``offset`` bits before the end
    of the container that starts at mark ``start`` (a containerEnd
    location).  The compiler replaces it with a Seek once it knows the
    container's size."""

    start: int
    offset: int


@dataclass
class Mark:
    """Remember the current position as the start of an included container."""

    id: int


@dataclass
class Value:
    """A value on the wire.  An ``assembled`` value occupies no bits
    itself: decoding reads it from the bits of its Segments, which come
    before it, and ``assembled`` is the bits of its encoding."""

    local: Local
    assembled: int = 0


@dataclass
class Segment:
    """``bits`` bits of ``local``'s encoding (a ParameterSegmentRefEntry).
    The value's whole encoding, ``total`` bits, is the bits of its segments
    in order; this one lies ``shift`` bits above the end of it.  Decoding
    gathers the bits and decodes the value at its assembled Value; encoding
    encodes the value at its ``first`` segment and writes each segment's
    share."""

    local: Local
    bits: int
    total: int
    shift: int = 0
    first: bool = False


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


@dataclass(eq=False)
class Check:
    """An ErrorDetectCorrect algorithm of a container.  With ``from_end``
    (XTCE's reference="end") it covers the bits from mark ``start`` (0 for
    the packet's start) to ``skip`` bits before the container's end (mark
    ``end``, or the packet's end when None).  Otherwise it covers the bits
    from ``skip`` bits after mark ``start`` to ``local``, if that lies after
    them, or else to the container's end.  ``local`` holds the
    value: its low bits when the algorithm gives more.  ``spec`` is the
    algorithm, as the Python runtime's ``checksum`` takes it."""

    spec: tuple
    local: Local
    start: int
    end: Optional[int]
    skip: int
    from_end: bool
    container: str


@dataclass
class Verify:
    """A Check of a container that is included only when its
    IncludeCondition holds.  Its bits all lie in that container, so it is
    verified (or computed, when encoding) where the container ends."""

    check: Check
    bits = 0  # it occupies nothing


@dataclass
class Require:
    """Criteria that must hold where it stands: the RestrictionCriteria of
    an included container with a BaseContainer.  Decoding and encoding fail
    with Mismatch, naming ``container``, when they don't."""

    criteria: list["Term"]
    container: str
    text: str
    bits = 0  # it occupies nothing


@dataclass
class Fill:
    """Padding up to ``size`` bits after mark ``start``, where a container
    with a size ends: a number, or a bound DynSize when another value gives
    it.  Decoding and encoding fail with BadSize, naming ``container``, if
    the entries already go past it."""

    start: int
    size: Union[int, "DynSize"]
    container: str
    bits = 0  # where it ends depends on where it starts

    @property
    def max_size(self) -> int:
        return (self.size.max_total if isinstance(self.size, DynSize)
                else self.size)


Item = Union[Seek, Mark, Value, Segment, Fixed, Pad, Cond, Require, Fill,
             Verify]


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
    part of ``local``, of type ``target``.  With ``other`` (and ``other_path``)
    it compares two
    values of the packet instead, and ``value`` is unused."""

    local: Local
    op: str
    value: object
    text: str  # as written in the XTCE file, for error messages
    path: tuple = ()
    target: Optional["VType"] = None
    other: Optional[Local] = None
    other_path: tuple = ()
    other_target: Optional["VType"] = None  # the other value's type there

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
                out += [(lc, path[:k], s.count) for k, s in enumerate(path)
                        if isinstance(s, Index) and s.count is not None]
        return out
    return [x for u in t.terms for x in term_indexes(u)]


def term_paths(t) -> list:
    """(local, path) for every value a term reads, with the member and
    element steps into it."""
    if isinstance(t, Criterion):
        out = [(t.local, t.path)]
        if t.other is not None:
            out.append((t.other, t.other_path))
        return out
    return [x for u in t.terms for x in term_paths(u)]


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
    # concrete only
    fields: list[tuple[str, Local]] = field(default_factory=list)
    fixed: dict = field(default_factory=dict)  # Local -> engineering value
    # Values that only give the size or count of another value: Local ->
    # (the value whose size it gives, its DynSize).  They are not fields;
    # encoding computes them from the other value's length.
    derived: dict = field(default_factory=dict)
    # ErrorDetectCorrect checks, in the order their values are computed
    checks: list = field(default_factory=list)
    # The container that must follow this one in a stream (its
    # NextContainer restriction), which keeps decoding its base from
    # picking it.
    next_container: Optional[str] = None
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

    def length_field(self) -> Optional[Local]:
        """The packet length field, if the packet has one."""
        return next(
            (it.local for it in walk(self.all_items()) if isinstance(it, Value)
             and it.local.length is not None), None)

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
    # the root SpaceSystem of each document, in the order given
    spaces: list[str]
    source: str
    types: list[VType]  # enumerations used by generated code
    # aggregates used by generated code, inner ones first
    aggregates: list[VType]
    nodes: list[Node]  # supported concrete nodes, document order
    kinds: list[Kind]
    roots: list[Node]  # roots of the trees behind ``kinds``
    skipped: list[str] = field(default_factory=list)
