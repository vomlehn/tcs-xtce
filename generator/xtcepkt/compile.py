"""Compile a mission's XTCE document into the packet model.

The document is loaded with the ``xtce`` Python package (the XML binding
generated from the XTCE schema), so anything that package accepts can be
compiled.  The compiler resolves names, fills in schema defaults, flattens
included containers and records which XTCE constructs it does not support.
"""

from __future__ import annotations

import dataclasses
import keyword
import math
import re
from typing import Callable, Optional

from .common import str_cap
from .model import (
    MAX_FIELD_BITS,
    AggregateEnc,
    ArrayEnc,
    BinaryEnc,
    Cond,
    Criterion,
    DynSize,
    EnumLabel,
    FloatEnc,
    Fixed,
    IntEnc,
    Item,
    Kind,
    Local,
    Mark,
    Member,
    Mission,
    Node,
    Pad,
    Polynomial,
    Seek,
    Spline,
    StringEnc,
    Unsupported,
    Value,
    VType,
    walk,
)

Key = tuple  # path of SpaceSystem names followed by the item name


def qname(key: Key) -> str:
    return "/" + "/".join(key)


# --- identifiers --------------------------------------------------------------

# Words reserved in any of the target languages, plus names the runtimes and
# generated code use themselves.  Names are made identical in every language,
# so the union is avoided everywhere.
RESERVED = set(keyword.kwlist) | {
    # Rust
    "as", "async", "await", "box", "const", "crate", "dyn", "enum", "extern",
    "fn", "impl", "let", "loop", "match", "mod", "move", "mut", "pub", "ref",
    "self", "static", "struct", "super", "trait", "type", "unsafe", "use",
    "where", "abstract", "become", "do", "final", "macro", "override", "priv",
    "typeof", "unsized", "virtual", "yield", "try", "gen",
    # C++
    "alignas", "alignof", "and_eq", "asm", "auto", "bitand", "bitor", "bool",
    "case", "catch", "char", "char8_t", "char16_t", "char32_t", "class",
    "compl", "concept", "consteval", "constexpr", "constinit", "const_cast",
    "co_await", "co_return", "co_yield", "decltype", "default", "delete",
    "double", "dynamic_cast", "explicit", "export", "float", "friend", "goto",
    "inline", "int", "long", "mutable", "namespace", "new", "noexcept", "not_eq",
    "nullptr", "operator", "or_eq", "private", "protected", "public", "register",
    "reinterpret_cast", "requires", "short", "signed", "sizeof", "static_assert",
    "static_cast", "switch", "template", "this", "thread_local", "throw",
    "typedef", "typeid", "typename", "union", "unsigned", "using", "void",
    "volatile", "wchar_t", "xor", "xor_eq", "main",
    # C keywords and standard macros a struct member cannot be named
    "restrict", "errno", "assert", "stdin", "stdout", "stderr", "offsetof",
    "true", "false", "NULL", "EOF",
    # Python builtins that would shadow useful names in dataclasses, and the
    # modules generated Python uses in class bodies
    "bytes", "int", "float", "str", "list", "dict", "type", "id", "len",
    "pk", "dataclasses",
    # generated members
    "decode", "encode", "encode_into", "describe", "label", "value", "raw",
    "name", "max_bytes", "data", "buf", "r", "w", "init", "to_bytes",
}


def hex_bytes(text: str, where: str) -> bytes:
    """The bytes of an ``xs:hexBinary`` value; an odd number of digits is
    read as if it had a leading 0."""
    if not re.fullmatch(r"[0-9A-Fa-f]*", text):
        raise Unsupported(f"{where}: {text!r} is not hexBinary")
    return bytes.fromhex(text.zfill(len(text) + len(text) % 2))


def member_path(ref: str) -> tuple[str, tuple]:
    """Splits a reference to an aggregate member (``P.a.b``, XTCE's dot
    syntax) into the reference to its parameter or argument and the
    member names.  Names can't contain dots, so only ``.`` and ``..``
    path segments are not members."""
    head, sep, last = ref.strip().rpartition("/")
    if last in (".", "..") or "." not in last:
        return ref.strip(), ()
    name, *members = last.split(".")
    return head + sep + name, tuple(members)


def walk_members(vt: VType, members: tuple, what: str) -> tuple[tuple, VType]:
    """The field names of ``members`` inside type ``vt``, and the type of
    the last one."""
    path = []
    for name in members:
        if vt.kind != "aggregate":
            raise Unsupported(f"{what}: {vt.kind} type {vt.key} has no member {name!r}")
        m = next((m for m in vt.enc.members if m.name == name), None)
        if m is None:
            raise Unsupported(f"{what}: aggregate type {vt.key} has no member {name!r}")
        path.append(m.ident)
        vt = m.vtype
    return tuple(path), vt


def integer_range(t, calibrated: bool = False) -> Optional[tuple[int, int]]:
    """(min, max) of an integer type's ValidRange, or of the union of an
    argument type's ValidRangeSet; None unless every bound is given.  For a
    type with a calibrator (``calibrated``) the range must apply to the
    calibrated value, which is the value of the generated field."""
    if getattr(t, "valid_range", None) is not None:
        ranges = [t.valid_range]
    elif getattr(t, "valid_range_set", None) is not None:
        ranges = t.valid_range_set.valid_range
    else:
        return None
    if not ranges or any(r.min_inclusive is None or r.max_inclusive is None for r in ranges):
        return None
    if calibrated and any(getattr(r, "valid_range_applies_to_calibrated", None) is False for r in ranges):
        return None
    return min(r.min_inclusive for r in ranges), max(r.max_inclusive for r in ranges)


def snake(name: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    s = re.sub(r"[^0-9A-Za-z]+", "_", s).strip("_").lower() or "x"
    if s[0].isdigit():
        s = "f_" + s
    if s in RESERVED:
        s += "_"
    return s


def camel(name: str) -> str:
    parts = [p for p in re.split(r"[^0-9A-Za-z]+", name) if p]
    s = "".join(p[0].upper() + p[1:] for p in parts) or "X"
    if s[0].isdigit():
        s = "T" + s
    return s


def upper(label: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", label)
    s = re.sub(r"[^0-9A-Za-z]+", "_", s).strip("_").upper() or "V"
    if s[0].isdigit():
        s = "V_" + s
    return s


class Namer:
    """Hands out unique identifiers within one namespace."""

    def __init__(self, reserved=()):
        self.used = set(reserved)

    def take(self, *candidates: str) -> str:
        for c in candidates:
            if c not in self.used:
                self.used.add(c)
                return c
        base, n = candidates[-1], 2
        while f"{base}{n}" in self.used:
            n += 1
        self.used.add(f"{base}{n}")
        return f"{base}{n}"


# Type names the runtimes or the generated modules define.
RUNTIME_NAMES = {
    "Packet", "PacketError", "Codec", "BitReader", "BitWriter", "Buffer",
    "Result", "Error", "ManagedBuffer", "Optional", "Union", "Any", "String",
    "Vec", "Self", "Option", "Box", "Ok", "Err", "Some", "None",
}


# --- the compiler ---------------------------------------------------------------


class Compiler:
    def __init__(self, root, skip_unsupported: bool = False):
        self.root = root
        self.skip_unsupported = skip_unsupported
        self.ptypes: dict[Key, tuple] = {}
        self.params: dict[Key, tuple] = {}
        self.atypes: dict[Key, tuple] = {}
        self.containers: dict[Key, tuple] = {}
        self.mcs: dict[Key, tuple] = {}
        self.mc_containers: dict[Key, Key] = {}  # CommandContainer -> MetaCommand
        # Parameter and argument types are separate namespaces, so the cache
        # key says which one a type comes from: (is_argument_type, key).
        self.vtypes: dict[tuple[bool, Key], VType] = {}
        self.nodes: dict[Key, Node] = {}
        self.order: list[Node] = []
        self._ids = 0
        self._aggregating: set = set()  # aggregates being compiled, to find cycles
        self.ctypes: dict = {}  # container key -> aggregate of a repeated container
        self._index(root, ())

    # -- indexing and name resolution --

    def _index(self, ss, parent: Key) -> None:
        path = parent + (ss.name,)
        tm, tc = ss.telemetry_meta_data, ss.command_meta_data
        for md in (tm, tc):
            if md is None:
                continue
            if md.parameter_type_set:
                for ch in md.parameter_type_set.items:
                    self.ptypes[path + (ch.value.name,)] = (path, ch.name, ch.value)
            if md.parameter_set:
                for ch in md.parameter_set.items:
                    if ch.name == "Parameter":
                        self.params[path + (ch.value.name,)] = (path, ch.value)
        if tm and tm.container_set:
            for sc in tm.container_set.sequence_container:
                self.containers[path + (sc.name,)] = (path, sc)
        if tc:
            if tc.argument_type_set:
                for ch in tc.argument_type_set.items:
                    self.atypes[path + (ch.value.name,)] = (path, ch.name, ch.value)
            if tc.meta_command_set:
                for ch in tc.meta_command_set.items:
                    if ch.name == "MetaCommand":
                        key = path + (ch.value.name,)
                        self.mcs[key] = (path, ch.value)
                        if ch.value.command_container is not None:
                            self.mc_containers[path + (ch.value.command_container.name,)] = key
        for child in ss.space_system:
            self._index(child, path)

    def resolve(self, ref: str, cur: Key, table: dict, what: str) -> Key:
        """Resolve an XTCE name reference relative to SpaceSystem ``cur``.

        Absolute paths start with ``/`` and the root SpaceSystem's name.
        Relative paths may use ``.`` and ``..``.  An unqualified name is looked
        up in ``cur`` and then in each enclosing SpaceSystem.
        """
        ref = ref.strip()
        parts = ref.split("/")
        if ref.startswith("/"):
            key = tuple(p for p in parts[1:] if p)
            if key in table:
                return key
        elif len(parts) == 1:
            for i in range(len(cur), 0, -1):
                key = cur[:i] + (ref,)
                if key in table:
                    return key
        else:
            base = list(cur)
            for p in parts[:-1]:
                if p == "..":
                    if base:
                        base.pop()
                elif p and p != ".":
                    base.append(p)
            key = tuple(base) + (parts[-1],)
            if key in table:
                return key
        raise Unsupported(f"unresolved {what} reference {ref!r} in {qname(cur)}")

    def _id(self) -> int:
        self._ids += 1
        return self._ids

    # -- types --

    def vtype(self, key: Key, table: dict) -> VType:
        cache = (table is self.atypes, key)
        if cache in self.vtypes:
            return self.vtypes[cache]
        path, kind, t = table[key]
        where = f"type {qname(key)}"
        if getattr(t, "base_type", None):
            raise Unsupported(f"{where}: baseType inheritance is not supported")
        base = kind.replace("ParameterType", "").replace("ArgumentType", "")
        if base in ("AbsoluteTime", "RelativeTime"):
            vt = self.time_type(key, base, t, path, where)
            self.vtypes[cache] = vt
            return vt
        if base == "Array":
            vt = self.array_type(key, table, path, t, where)
            self.vtypes[cache] = vt
            return vt
        if base == "Aggregate":
            vt = self.aggregate_type(key, table, path, t, where, cache)
            self.vtypes[cache] = vt
            return vt
        if base not in ("Integer", "Float", "Enumerated", "Boolean", "String", "Binary"):
            raise Unsupported(f"{where}: {kind} is not supported")
        if t.choice is None:
            raise Unsupported(f"{where}: no data encoding")
        enc_name, e = t.choice.name, t.choice.value
        enc, cal = self.encoding(enc_name, e, path, where)
        vt = VType(key=qname(key), name=t.name, kind="", enc=enc, calibrator=cal)
        if base == "Float":
            if not isinstance(enc, (IntEnc, FloatEnc)):
                raise Unsupported(f"{where}: float with {enc_name}")
            vt.kind = "float"
        elif base in ("Integer", "Enumerated", "Boolean"):
            # A boolean may also be one of two strings.
            if not isinstance(enc, IntEnc) and not (base == "Boolean" and isinstance(enc, StringEnc)):
                raise Unsupported(f"{where}: {base.lower()} with {enc_name}")
            if cal is not None and base != "Integer":
                article = "an" if base[0] in "AEIOU" else "a"
                raise Unsupported(f"{where}: calibrator on {article} {base.lower()} type")
            vt.kind = {"Integer": "int", "Enumerated": "enum", "Boolean": "bool"}[base]
            if base == "Integer":
                if cal is not None:
                    # The calibrated value is rounded to an integer of the
                    # type's own size, not the encoding's.
                    bits = t.size_in_bits if t.size_in_bits is not None else 32
                    if not 1 <= bits <= 64:
                        raise Unsupported(f"{where}: {bits}-bit integers")
                    vt.eng = (bits, t.signed is not False)
                vt.valid_range = integer_range(t, cal is not None)
        elif base == "String":
            # A string encoded as binary is its UTF-8 bytes, filling the field.
            if not isinstance(enc, (StringEnc, BinaryEnc)):
                raise Unsupported(f"{where}: string with {enc_name}")
            if isinstance(enc, BinaryEnc) and enc.rest:
                raise Unsupported(f"{where}: string with binary size -1")
            vt.kind = "string"
        else:
            if not isinstance(enc, BinaryEnc):
                raise Unsupported(f"{where}: binary with {enc_name}")
            vt.kind = "binary"
        if vt.kind == "enum":
            names = Namer()
            for ev in t.enumeration_list.enumeration:
                hi = ev.max_value if ev.max_value is not None else ev.value
                vt.labels.append(EnumLabel(ev.label, names.take(upper(ev.label)), ev.value, hi))
        if vt.kind == "bool":
            vt.one_string = t.one_string_value if t.one_string_value is not None else "True"
            vt.zero_string = t.zero_string_value if t.zero_string_value is not None else "False"
            if isinstance(enc, StringEnc):
                self.check_bool_strings(vt, where)
        self.vtypes[cache] = vt
        return vt

    @staticmethod
    def check_bool_strings(vt: VType, where: str) -> None:
        """A boolean encoded as a string: its two strings must differ and
        fit the encoding."""
        if vt.one_string == vt.zero_string:
            raise Unsupported(f"{where}: oneStringValue and zeroStringValue are both {vt.one_string!r}")
        e = vt.enc
        for s in (vt.one_string, vt.zero_string):
            b = s.encode("utf-8")
            if e.terminator is not None and e.terminator in b:
                raise Unsupported(f"{where}: {s!r} contains the termination character")
            if e.dyn is None and len(b) > str_cap(vt):
                raise Unsupported(f"{where}: {s!r} does not fit its encoding")

    def time_type(self, key: Key, base: str, t, path: Key, where: str) -> VType:
        """Times are seconds: ``scale * raw + offset`` from the reference time."""
        enc = t.encoding
        if enc is None:
            raise Unsupported(f"{where}: time type without Encoding")
        units = enc.units.value if enc.units else "seconds"
        if units != "seconds":
            raise Unsupported(f"{where}: time units {units}")
        e, cal = self.encoding(enc.choice.name, enc.choice.value, path, where)
        if not isinstance(e, (IntEnc, FloatEnc)):
            raise Unsupported(f"{where}: time with {enc.choice.name}")
        if cal is not None:
            raise Unsupported(f"{where}: calibrator inside a time encoding")
        scale = enc.scale if enc.scale is not None else 1.0
        offset = enc.offset if enc.offset is not None else 0.0
        if scale != 1.0 or offset != 0.0:
            cal = Polynomial([(offset, 0), (scale, 1)])
        doc = "seconds"
        ref = t.reference_time
        if ref is not None:
            if ref.choice.name == "Epoch":
                doc = f"seconds since epoch {ref.choice.value}"
            elif ref.choice.name == "OffsetFrom":
                doc = f"seconds after {ref.choice.value.parameter_ref}"
        what = "absolute" if base == "AbsoluteTime" else "relative"
        return VType(key=qname(key), name=t.name, kind="float", enc=e, calibrator=cal, doc=f"{what} time, {doc}")

    def array_type(self, key: Key, table: dict, path: Key, t, where: str) -> VType:
        """An array type: its elements in the order they are encoded, which
        for several dimensions is the order of the DimensionList."""
        ekey = self.resolve(t.array_type_ref, path, table, "array element type")
        if table[ekey][1].startswith("Array"):
            raise Unsupported(f"{where}: arrays of arrays")
        elem = self.vtype(ekey, table)
        if elem.dyn is not None:
            raise Unsupported(f"{where}: elements whose size another value gives")
        if is_rest(elem):
            raise Unsupported(f"{where}: elements that take the rest of the packet")
        dims = t.dimension_list.dimension
        if not dims:
            raise Unsupported(f"{where}: array without dimensions")
        fixed, dyn = 1, None
        for d in dims:
            start = self.int_value(d.starting_index, path, "array StartingIndex", where)
            end = self.int_value(d.ending_index, path, "array EndingIndex", where)
            if isinstance(start, DynSize):
                raise Unsupported(f"{where}: StartingIndex from DynamicValue")
            if isinstance(end, int):
                if end < start - 1:
                    raise Unsupported(f"{where}: EndingIndex {end} is before StartingIndex {start}")
                fixed *= end - start + 1
            elif dyn is not None:
                raise Unsupported(f"{where}: more than one dimension from DynamicValue")
            else:
                end.intercept += 1 - start  # the count is end - start + 1
                dyn = end
        if dyn is not None:
            dyn.factor = fixed
        count = dyn if dyn is not None else fixed
        return VType(key=qname(key), name=t.name, kind="array", enc=ArrayEnc(elem, count))

    def aggregate_type(self, key: Key, table: dict, path: Key, t, where: str, cache) -> VType:
        """An aggregate type: its members, encoded one after another."""
        if cache in self._aggregating:
            raise Unsupported(f"{where}: aggregate contains itself")
        if not t.member_list.member:
            raise Unsupported(f"{where}: aggregate without members")
        self._aggregating.add(cache)
        try:
            names = Namer()
            members = []
            for m in t.member_list.member:
                mkey = self.resolve(m.type_ref, path, table, "member type")
                mt = self.vtype(mkey, table)
                d = mt.dyn
                if d is not None and d.table is not None:
                    raise Unsupported(f"{where}: member {m.name} has a size from a DiscreteLookupList")
                if d is not None:
                    # Like Yamcs, a reference to an earlier member's name
                    # means that member of the same aggregate value.
                    sib = next((x for x in members if x.name == d.name), None)
                    if sib is None:
                        raise Unsupported(
                            f"{where}: member {m.name} has a size that another value gives, "
                            f"and {d.name} is not an earlier member"
                        )
                    if sib.vtype.kind != "int":
                        raise Unsupported(f"{where}: size or count from {sib.vtype.kind} member {sib.name}")
                    mt = bounded(mt, dataclasses.replace(d, member=sib.ident), int_bounds(sib.vtype))
                if is_rest(mt):
                    raise Unsupported(f"{where}: member {m.name} takes the rest of the packet")
                members.append(Member(m.name, names.take(snake(m.name)), mt))
        finally:
            self._aggregating.discard(cache)
        return VType(key=qname(key), name=t.name, kind="aggregate", enc=AggregateEnc(members))

    def int_value(self, iv, path: Key, what: str, where: str):
        """An IntegerValueType: a number, or an unbound DynSize."""
        c = iv.choice
        if c.name == "FixedValue":
            return int(c.value)
        if c.name == "DynamicValue":
            return self.dyn_size(c.value, path, what, where)
        if c.name == "DiscreteLookupList":
            return DynSize(table=(path, c.value))
        raise Unsupported(f"{where}: {what} from {c.name}")

    def dyn_size(self, dv, path: Key, what: str, where: str) -> DynSize:
        """A DynamicValue (or argument DynamicValue), unbound."""
        if hasattr(dv, "parameter_instance_ref"):
            ref, kind = dv.parameter_instance_ref, "ParameterInstanceRef"
        else:
            ref, kind = dv.choice.value, dv.choice.name
        if getattr(ref, "instance", None):
            raise Unsupported(f"{where}: {what} from instance {ref.instance} of a parameter")
        adj = dv.linear_adjustment
        slope = adj.slope if adj is not None and adj.slope is not None else 1.0
        intercept = adj.intercept if adj is not None and adj.intercept is not None else 0.0
        if not (math.isfinite(slope) and math.isfinite(intercept)):
            raise Unsupported(f"{where}: {what} with a non-finite LinearAdjustment")
        if kind == "ParameterInstanceRef":
            name = ref.parameter_ref.strip()
            base, members = member_path(name)
            try:
                key = qname(self.resolve(base, path, self.params, "parameter"))
            except Unsupported:
                key = None  # an error unless it names an aggregate member
            return DynSize(key=key, name=name, members=members, slope=slope, intercept=intercept)
        name = ref.argument_ref.strip()
        base, members = member_path(name)
        return DynSize(arg=base, name=name, members=members, slope=slope, intercept=intercept)

    def encoding(self, name: str, e, path: Key, where: str):
        if getattr(e, "bit_order", None) and e.bit_order.value != "mostSignificantBitFirst":
            raise Unsupported(f"{where}: bitOrder {e.bit_order.value}")
        if getattr(e, "error_detect_correct", None):
            raise Unsupported(f"{where}: ErrorDetectCorrect")
        bo = (e.byte_order or "mostSignificantByteFirst").strip()
        if bo not in ("mostSignificantByteFirst", "leastSignificantByteFirst"):
            raise Unsupported(f"{where}: byteOrder {bo!r}")
        le = bo == "leastSignificantByteFirst"
        if name == "IntegerDataEncoding":
            bits = e.size_in_bits if e.size_in_bits is not None else 8
            kind = e.encoding.value if e.encoding else "unsigned"
            if kind not in ("unsigned", "twosComplement", "signMagnitude", "onesComplement"):
                raise Unsupported(f"{where}: integer encoding {kind}")
            if not 1 <= bits <= 64:
                raise Unsupported(f"{where}: {bits}-bit integers")
            if le and bits % 8:
                raise Unsupported(f"{where}: little-endian size {bits} is not whole bytes")
            if kind != "unsigned" and bits < 2:
                raise Unsupported(f"{where}: 1-bit signed integer")
            return IntEnc(bits, kind, le), self.calibrator(e, where)
        if name == "FloatDataEncoding":
            kind = e.encoding.value if e.encoding else "IEEE754_1985"
            bits = int(e.size_in_bits.value) if e.size_in_bits else 32
            if kind not in ("IEEE754_1985", "IEEE754"):
                raise Unsupported(f"{where}: float encoding {kind}")
            if bits not in (32, 64):
                raise Unsupported(f"{where}: {bits}-bit floats")
            return FloatEnc(bits, le), self.calibrator(e, where)
        if name == "StringDataEncoding":
            cs = e.encoding.value if e.encoding else "UTF-8"
            if cs not in ("UTF-8", "US-ASCII"):
                raise Unsupported(f"{where}: string encoding {cs}")
            c = e.choice
            if c.name == "SizeInBits":
                bits = self._size(c.value.fixed.fixed_value, "string", where)
                if bits % 8:
                    raise Unsupported(f"{where}: string size {bits} is not whole bytes")
                if c.value.leading_size is not None:
                    tag = c.value.leading_size.size_in_bits_of_size_tag or 16
                    return StringEnc(bits, bits, None, self._tag(tag, where)), None
                return StringEnc(bits, bits, self._term(c.value.termination_char, where)), None
            v = c.value
            dyn = None
            if v.choice1 is not None:
                if v.choice1.name == "DynamicValue":
                    dyn = self.dyn_size(v.choice1.value, path, "string size", where)
                elif v.choice1.name == "DiscreteLookupList":
                    dyn = DynSize(table=(path, v.choice1.value))
                else:
                    raise Unsupported(f"{where}: string size from {v.choice1.name}")
                dyn.unit = 8
            mx = self._size(v.max_size_in_bits, "string", where)
            if v.choice2.name == "LeadingSize":
                tag = v.choice2.value.size_in_bits_of_size_tag or 16
                return StringEnc(None, mx, None, self._tag(tag, where), dyn), None
            return StringEnc(None, mx, self._term(v.choice2.value, where), dyn=dyn), None
        if name == "BinaryDataEncoding":
            if e.from_binary_transform_algorithm or e.to_binary_transform_algorithm:
                raise Unsupported(f"{where}: binary transform algorithms")
            c = e.size_in_bits.choice
            if c.name in ("DynamicValue", "DiscreteLookupList"):
                dyn = self.int_value(e.size_in_bits, path, "binary size", where)
                dyn.unit = 8
                return BinaryEnc(MAX_FIELD_BITS, dyn), None
            if c.name != "FixedValue":
                raise Unsupported(f"{where}: binary size from {c.name}")
            if c.value == -1:
                # A Yamcs convention: the size of the value given.  Only the
                # end of the packet can bound it when decoding.
                return BinaryEnc(MAX_FIELD_BITS, rest=True), None
            bits = self._size(c.value, "binary", where)
            if bits % 8:
                raise Unsupported(f"{where}: binary size {bits} is not whole bytes")
            return BinaryEnc(bits), None
        raise Unsupported(f"{where}: {name}")

    @staticmethod
    def _size(bits: int, what: str, where: str) -> int:
        """A size in bits from the document; the schema allows negative ones."""
        if bits < 0:
            raise Unsupported(f"{where}: {what} size {bits} is negative")
        return bits

    @staticmethod
    def _tag(bits: int, where: str) -> int:
        if not 1 <= bits <= 32:
            raise Unsupported(f"{where}: {bits}-bit string size tag")
        return bits

    @staticmethod
    def _term(hexchar: Optional[str], where: str) -> int:
        h = (hexchar or "").strip() or "00"
        b = hex_bytes(h, where)
        if len(b) != 1:
            raise Unsupported(f"{where}: multi-byte termination character {h}")
        return b[0]

    def calibrator(self, e, where: str):
        if getattr(e, "context_calibrator_list", None):
            raise Unsupported(f"{where}: ContextCalibratorList")
        c = e.default_calibrator
        if c is None:
            return None
        if c.choice.name == "PolynomialCalibrator":
            return Polynomial([(t.coefficient, t.exponent) for t in c.choice.value.term])
        if c.choice.name == "SplineCalibrator":
            s = c.choice.value
            order = s.order if s.order is not None else 1
            if order not in (0, 1):
                raise Unsupported(f"{where}: spline of order {order}")
            pts = sorted((p.raw, p.calibrated) for p in s.spline_point)
            if len(pts) < 2:
                raise Unsupported(f"{where}: spline with fewer than two points")
            return Spline(pts, order, bool(s.extrapolate))
        raise Unsupported(f"{where}: {c.choice.name}")

    # -- literals --

    def literal(self, vt: VType, text: str, raw: bool, where: str):
        s = text.strip()
        if vt.kind in ("array", "aggregate"):
            raise Unsupported(f"{where}: value {text!r} for {vt.kind} type {vt.key}")
        try:
            if raw or vt.kind == "int":
                v = int(s, 0) if s.lower().startswith(("0x", "0b", "0o")) else int(s)
                return bool(v) if vt.kind == "bool" else v
            if vt.kind == "float":
                return float(s)
            if vt.kind == "enum":
                for lab in vt.labels:
                    if lab.label == s:
                        return lab.value
                return int(s)
            if vt.kind == "bool":
                if s in (vt.one_string, "true", "1"):
                    return True
                if s in (vt.zero_string, "false", "0"):
                    return False
                raise ValueError(s)
            if vt.kind == "string":
                return text
            return bytes.fromhex(s)
        except ValueError:
            raise Unsupported(f"{where}: value {text!r} does not fit {vt.kind} type {vt.key}") from None

    # -- containers --

    def local(self, name: str, key: str, vt: VType) -> Local:
        return Local(self._id(), name, key, vt)

    def location(self, loc, start: int, path: Key, scope: list, where: str, arg_key=None) -> list[Item]:
        """The Seek for an entry's location; one from other values is bound
        to ``scope`` and must not move backwards."""
        if loc is None:
            return []
        ref = loc.reference_location.value if loc.reference_location else "previousEntry"
        c = loc.choice
        if c.name == "FixedValue":
            off = c.value
        else:
            if c.name == "DynamicValue":
                d = self.dyn_size(c.value, path, "location", where)
            elif c.name == "DiscreteLookupList":
                d = DynSize(table=(path, c.value))
            else:
                raise Unsupported(f"{where}: location from {c.name}")
            off, rng = self.bind_dyn(d, scope, where, arg_key)
            off.max = min(dyn_units(off, rng), MAX_FIELD_BITS)
        if ref == "containerStart":
            return [Seek(start, off)]
        if ref == "previousEntry":
            return [Seek(None, off)] if off else []
        raise Unsupported(f"{where}: location relative to {ref}")

    def repeat(self, local: Local, e, path: Key, where: str) -> Local:
        """Turns the value of an entry with a RepeatEntry into an array."""
        rp = getattr(e, "repeat_entry", None)
        if rp is None:
            return local
        vt = local.vtype
        if vt.kind == "array":
            raise Unsupported(f"{where}: RepeatEntry on an array")
        if vt.dyn is not None:
            raise Unsupported(f"{where}: RepeatEntry on a value whose size another value gives")
        if is_rest(vt):
            raise Unsupported(f"{where}: RepeatEntry on a value that takes the rest of the packet")
        count = self.int_value(rp.count, path, "RepeatEntry Count", where)
        gap = 0
        if rp.offset is not None:
            gap = self.int_value(rp.offset, path, "RepeatEntry Offset", where)
            if isinstance(gap, DynSize):
                raise Unsupported(f"{where}: RepeatEntry Offset from DynamicValue")
            if gap < 0:
                raise Unsupported(f"{where}: RepeatEntry Offset {gap} is negative")
        if isinstance(count, int):
            if count < 1:
                raise Unsupported(f"{where}: RepeatEntry Count {count} is not positive")
            if count == 1:
                return local
        local.vtype = VType(key=vt.key, name=vt.name, kind="array", enc=ArrayEnc(vt, count, gap), doc=vt.doc)
        return local

    def bind(self, local: Local, scope: list, where: str, arg_key: Optional[Callable] = None) -> Local:
        """Binds the size or count of a value to what gives it, which must
        be decoded before it, and bounds it."""
        vt = local.vtype
        if vt.dyn is None:
            return local
        b, rng = self.bind_dyn(vt.dyn, scope, where, arg_key)
        local.vtype = bounded(vt, b, rng)
        return local

    def bind_dyn(self, d: DynSize, scope: list, where: str, arg_key: Optional[Callable] = None):
        """A copy of ``d`` bound to the values in ``scope``, and the range
        (lo, hi) of the value it adjusts."""
        if d.table is not None:
            path, table = d.table
            lookup = [(self.match(lk, path, scope, where, arg_key), lk.value) for lk in table.discrete_lookup]
            values = [v for _, v in lookup] + [table.default_value]
            return dataclasses.replace(d, lookup=lookup, default=table.default_value), (min(values), max(values))
        if d.arg is not None:
            if arg_key is None:
                raise Unsupported(f"{where}: size from argument {d.arg!r} outside a command")
            key, name = arg_key(d.arg), d.arg
        elif d.key is None:
            raise Unsupported(f"{where}: unresolved parameter reference {d.name!r}")
        else:
            key = name = d.key
        ref = next((lc for lc in reversed(scope) if lc.key == key), None)
        if ref is None:
            raise Unsupported(f"{where}: size or count from {name}, which is not decoded before it")
        if ref.optional:
            raise Unsupported(f"{where}: size or count from {name}, which has an IncludeCondition")
        member, vt = walk_members(ref.vtype, d.members, f"{where}: size or count from {d.name}")
        if vt.kind != "int":
            raise Unsupported(f"{where}: size or count from {vt.kind} value {d.name or name}")
        return dataclasses.replace(d, local=ref, path=member), int_bounds(vt)

    def param_local(self, ref: str, path: Key) -> Local:
        pkey = self.resolve(ref, path, self.params, "parameter")
        ppath, p = self.params[pkey]
        tkey = self.resolve(p.parameter_type_ref, ppath, self.ptypes, "parameter type")
        return self.local(p.name, qname(pkey), self.vtype(tkey, self.ptypes))

    def tm_items(self, sc, path: Key, stack: list, start: int, scope: list) -> list[Item]:
        """Items for a container's entries.  ``scope`` lists the values read
        so far (base containers first) and grows as entries are added."""
        items: list[Item] = []
        for ch in sc.entry_list.items:
            e = ch.value
            where = f"container {qname(stack[-1])}"
            if ch.name not in ("ParameterRefEntry", "ContainerRefEntry", "ArrayParameterRefEntry"):
                raise Unsupported(f"{where}: {ch.name}")
            if getattr(e, "dimension_list", None) is not None:
                raise Unsupported(f"{where}: array subsets (DimensionList in an array entry)")
            cond = None
            if e.include_condition is not None:
                cond = self.match(e.include_condition, path, scope, where)
            out = items if cond is None else []
            out += self.location(e.location_in_container_in_bits, start, path, scope, where)
            if ch.name != "ContainerRefEntry":
                local = self.param_local(e.parameter_ref, path)
                local = self.bind(self.repeat(local, e, path, where), scope, where)
                out.append(Value(local))
                scope.append(local)
            else:
                ckey = self.resolve(e.container_ref, path, self.containers, "container")
                if ckey in stack:
                    raise Unsupported(f"{where}: container includes itself")
                cpath, inc = self.containers[ckey]
                if inc.base_container is not None:
                    raise Unsupported(f"{where}: included container {qname(ckey)} has a BaseContainer")
                if e.repeat_entry is not None:
                    local = self.local(inc.name, qname(ckey), self.container_type(ckey, stack, where))
                    local = self.bind(self.repeat(local, e, path, where), scope, where)
                    out.append(Value(local))
                    scope.append(local)
                else:
                    m = self._id()
                    out.append(Mark(m))
                    inner = self.tm_items(inc, cpath, stack + [ckey], m, scope)
                    out += inner + self.size_pad(inc, inner, m, where)
            if cond is not None:
                for it in walk(out):
                    if isinstance(it, Value):
                        it.local.optional = True
                items.append(Cond(cond, out))
        return items

    def size_pad(self, sc, items: list, start: int, where: str) -> list[Item]:
        """The padding that makes a container with a BinaryEncoding occupy
        its SizeInBits; ``items`` are its entries, from mark ``start``."""
        be = sc.binary_encoding
        if be is None:
            return []
        if be.error_detect_correct is not None:
            raise Unsupported(f"{where}: ErrorDetectCorrect")
        if be.from_binary_transform_algorithm or be.to_binary_transform_algorithm:
            raise Unsupported(f"{where}: container transform algorithms")
        if be.size_in_bits is None:
            raise Unsupported(f"{where}: container BinaryEncoding without SizeInBits")
        size = self.int_value(be.size_in_bits, (), "container SizeInBits", where)
        if isinstance(size, DynSize):
            raise Unsupported(f"{where}: container SizeInBits from DynamicValue")
        size = self._size(size, "container", where)
        end = static_end(items, start)
        if end is None:
            raise Unsupported(f"{where}: a container size with entries whose size or position varies")
        pos, extent = end
        if extent > size:
            raise Unsupported(f"{where}: entries take {extent} bits, more than the container size {size}")
        if pos != extent:
            raise Unsupported(f"{where}: a container size after entries that overlap")
        return [Pad(size - pos)] if size > pos else []

    def container_type(self, ckey: Key, stack: list, where: str) -> VType:
        """The aggregate holding one copy of a repeated container's entries,
        with skips and padding from their locations and the container size."""
        if ckey in self.ctypes:
            return self.ctypes[ckey]
        cpath, inc = self.containers[ckey]
        what = f"{where}: repeated container {qname(ckey)}"
        m = self._id()
        # Its entries are values of each copy, so sizes can't come from
        # values outside it; ones from values inside are rejected below.
        items = self.tm_items(inc, cpath, stack + [ckey], m, [])
        items += self.size_pad(inc, items, m, where)
        members: list[Member] = []
        names = Namer()
        pos: Optional[int] = 0  # None once a value of varying size is passed
        marks = {m: 0}
        skip = 0
        for it in items:
            if isinstance(it, Cond):
                raise Unsupported(f"{what} has an IncludeCondition")
            if isinstance(it, Seek):
                if isinstance(it.offset, DynSize):
                    raise Unsupported(f"{what} has a location that other values give")
                if it.from_start is not None:
                    if it.from_start not in marks:
                        raise Unsupported(f"{what} has a location relative to the start of another container")
                    if marks[it.from_start] is None or pos is None:
                        raise Unsupported(f"{what} has a location after a value whose size varies")
                    delta = marks[it.from_start] + it.offset - pos
                else:
                    delta = it.offset
                if delta < 0:
                    raise Unsupported(f"{what} has entries that overlap")
                skip += delta
                pos = pos + delta if pos is not None else None
            elif isinstance(it, Mark):
                marks[it.id] = pos
            elif isinstance(it, Pad):
                skip += it.bits
                pos = pos + it.bits if pos is not None else None
            else:
                vt = it.local.vtype
                if vt.dyn is not None:
                    raise Unsupported(f"{what}: {it.local.name} has a size that another value gives")
                members.append(Member(it.local.name, names.take(snake(it.local.name)), vt, skip))
                skip = 0
                bits = fixed_bits(vt)
                pos = pos + bits if pos is not None and bits is not None else None
        if not members:
            raise Unsupported(f"{what} has no entries")
        vt = VType(key=qname(ckey), name=inc.name, kind="aggregate", enc=AggregateEnc(members, skip))
        vt.doc = "one copy of a repeated container"
        self.ctypes[ckey] = vt
        return vt

    def tm_node(self, key: Key) -> Node:
        if key in self.nodes:
            n = self.nodes[key]
            if getattr(n, "_building", False):
                raise Unsupported(f"container inheritance cycle through {qname(key)}")
            return n
        path, sc = self.containers[key]
        node = Node("tm", qname(key), sc.name, bool(sc.abstract))
        node._building = True
        self.nodes[key] = node
        try:
            base = None
            scope: list[Local] = []
            if sc.base_container is not None:
                bkey = self.resolve(sc.base_container.container_ref, path, self.containers, "container")
                base = self.tm_node(bkey)
                if base.error:
                    raise Unsupported(f"base container {base.key} is not supported")
                node.base = base
                scope = self.scope_of(base)
                rc = sc.base_container.restriction_criteria
                if rc is not None:
                    if rc.next_container is not None:
                        raise Unsupported(f"container {node.key}: NextContainer")
                    node.criteria = self.match(rc, path, scope, f"container {node.key}")
            if sc.binary_encoding is not None and base is not None:
                raise Unsupported(f"container {node.key}: BinaryEncoding with a BaseContainer")
            node.items = self.tm_items(sc, path, [key], 0, scope)
            node.items += self.size_pad(sc, node.items, 0, f"container {node.key}")
            self.check_rest(node, f"container {node.key}")
            if base is not None:
                base.children.append(node)
        except Unsupported as e:
            node.error = str(e)
            node.base = None
        node._building = False
        self.order.append(node)
        return node

    @staticmethod
    def check_rest(node: Node, where: str) -> None:
        """A value that takes the rest of the packet must come last."""
        items = walk(node.all_items())
        for i, it in enumerate(items[:-1]):
            if isinstance(it, Value) and is_rest(it.local.vtype):
                raise Unsupported(f"{where}: {it.local.name} takes the rest of the packet but is not last")

    @staticmethod
    def scope_of(node: Node) -> list[Local]:
        return [it.local for it in walk(node.all_items()) if isinstance(it, Value)]

    def match(self, mc, path: Key, scope: list, where: str, arg_key: Optional[Callable] = None) -> list[Criterion]:
        """Criteria of a RestrictionCriteria, IncludeCondition or
        DiscreteLookup, on values in ``scope``; ``arg_key`` resolves
        argument names in commands."""
        c = mc.choice
        if c.name == "Comparison":
            comps = [c.value]
        elif c.name == "ComparisonList":
            comps = c.value.comparison
        else:
            raise Unsupported(f"{where}: {c.name} criteria")
        out = []
        for cmp in comps:
            instance, raw = None, False
            if hasattr(cmp, "choice"):  # an ArgumentComparison
                ref = cmp.choice.value
                if cmp.choice.name == "ArgumentInstanceRef":
                    if arg_key is None:
                        raise Unsupported(f"{where}: comparison of argument {ref.argument_ref!r} outside a command")
                    base, members = member_path(ref.argument_ref)
                    pkey = arg_key(base)
                else:
                    base, members = member_path(ref.parameter_ref)
                    pkey = qname(self.resolve(base, path, self.params, "parameter"))
                    instance = ref.instance
            else:
                base, members = member_path(cmp.parameter_ref)
                pkey = qname(self.resolve(base, path, self.params, "parameter"))
                instance, raw = cmp.instance, cmp.use_calibrated_value is False
            local = next((lc for lc in reversed(scope) if lc.key == pkey), None)
            if local is None:
                raise Unsupported(f"{where}: criterion on {pkey}, which is not decoded before it")
            if local.optional:
                raise Unsupported(f"{where}: criterion on {pkey}, which has an IncludeCondition")
            member, vt = walk_members(local.vtype, members, f"{where}: criterion on {pkey}")
            pkey = ".".join((pkey,) + members)
            if vt.kind in ("array", "aggregate"):
                raise Unsupported(f"{where}: criterion on {pkey}, which is an {vt.kind}")
            if instance:
                raise Unsupported(f"{where}: comparison of instance {instance}")
            if raw and (vt.kind not in ("int", "enum", "bool") or vt.calibrator is not None or isinstance(vt.enc, StringEnc)):
                raise Unsupported(f"{where}: raw comparison on {vt.kind} parameter {pkey}")
            op = cmp.comparison_operator.value if cmp.comparison_operator else "=="
            if op not in ("==", "!=") and vt.kind in ("bool", "string", "binary"):
                raise Unsupported(f"{where}: {op} on {vt.kind} parameter {pkey}")
            value = self.literal(vt, cmp.value, raw, where)
            target = vt if members else None
            out.append(Criterion(local, op, value, f"{pkey} {op} {cmp.value}", member, target))
        return out

    # -- commands --

    def arg_scope(self, key: Key, seen: tuple = ()) -> dict:
        """Argument name -> (MetaCommand key, Argument) visible in a MetaCommand."""
        if key in seen:
            raise Unsupported(f"meta-command inheritance cycle through {qname(key)}")
        path, mc = self.mcs[key]
        scope = {}
        if mc.base_meta_command is not None:
            bkey = self.resolve(mc.base_meta_command.meta_command_ref, path, self.mcs, "meta-command")
            scope.update(self.arg_scope(bkey, seen + (key,)))
        if mc.argument_list is not None:
            for a in mc.argument_list.argument:
                scope[a.name] = (key, a)
        return scope

    def arg_local(self, scope: dict, name: str, where: str) -> Local:
        if name not in scope:
            raise Unsupported(f"{where}: unknown argument {name!r}")
        owner, a = scope[name]
        tkey = self.resolve(a.argument_type_ref, self.mcs[owner][0], self.atypes, "argument type")
        return self.local(a.name, f"{qname(owner)}#{a.name}", self.vtype(tkey, self.atypes))

    def tc_node(self, key: Key) -> Node:
        if key in self.nodes:
            n = self.nodes[key]
            if getattr(n, "_building", False):
                raise Unsupported(f"meta-command inheritance cycle through {qname(key)}")
            return n
        path, mc = self.mcs[key]
        node = Node("tc", qname(key), mc.name, bool(mc.abstract))
        node._building = True
        self.nodes[key] = node
        where = f"meta-command {node.key}"
        try:
            base = None
            scope = self.arg_scope(key)
            if mc.base_meta_command is not None:
                bkey = self.resolve(mc.base_meta_command.meta_command_ref, path, self.mcs, "meta-command")
                base = self.tc_node(bkey)
                if base.error:
                    raise Unsupported(f"base meta-command {base.key} is not supported")
                node.base = base
                al = mc.base_meta_command.argument_assignment_list
                for asg in al.argument_assignment if al else []:
                    if asg.argument_name not in scope:
                        raise Unsupported(f"{where}: assignment to unknown argument {asg.argument_name!r}")
                    akey = f"{qname(scope[asg.argument_name][0])}#{asg.argument_name}"
                    local = next((lc for lc in reversed(self.scope_of(base)) if lc.key == akey), None)
                    if local is None:
                        continue  # the argument is not encoded in the packet
                    value = self.literal(local.vtype, asg.argument_value, False, where)
                    node.criteria.append(Criterion(local, "==", value, f"{asg.argument_name} = {asg.argument_value}"))
            cc = mc.command_container
            if cc is not None:
                if cc.binary_encoding is not None:
                    raise Unsupported(f"{where}: container BinaryEncoding")
                if cc.base_container is not None:
                    bc = self.resolve(cc.base_container.container_ref, path, self.mc_containers, "command container")
                    owner = self.mc_containers[bc]
                    if base is None or self.nodes.get(owner) is not base:
                        raise Unsupported(
                            f"{where}: CommandContainer extends {qname(bc)}, "
                            "which is not the container of its BaseMetaCommand"
                        )
                    if cc.base_container.restriction_criteria is not None:
                        raise Unsupported(f"{where}: RestrictionCriteria on a CommandContainer")
                elif base is not None and base.all_items():
                    raise Unsupported(f"{where}: CommandContainer does not extend its base's container")
                node.items = self.tc_items(cc, scope, path, where, self.scope_of(base) if base else [])
            self.check_rest(node, where)
            if base is not None:
                base.children.append(node)
        except Unsupported as e:
            node.error = str(e)
            node.base = None
        node._building = False
        self.order.append(node)
        return node

    def tc_items(self, cc, scope: dict, path: Key, where: str, values: list) -> list[Item]:
        """Items for a CommandContainer's entries.  ``values`` lists the
        values encoded so far (base meta-commands first) and grows."""

        def arg_key(name: str) -> str:
            if name not in scope:
                raise Unsupported(f"{where}: unknown argument {name!r}")
            return f"{qname(scope[name][0])}#{name}"

        items: list[Item] = []
        for ch in cc.entry_list.items:
            e = ch.value
            entries = ("ArgumentRefEntry", "ArrayArgumentRefEntry", "ParameterRefEntry", "ArrayParameterRefEntry")
            if ch.name not in entries + ("FixedValueEntry",):
                raise Unsupported(f"{where}: {ch.name} in a CommandContainer")
            if e.include_condition is not None:
                raise Unsupported(f"{where}: IncludeCondition in a CommandContainer")
            if getattr(e, "dimension_list", None) is not None:
                raise Unsupported(f"{where}: array subsets (DimensionList in an array entry)")
            items += self.location(e.location_in_container_in_bits, 0, path, values, where, arg_key)
            if ch.name in entries:
                if "Argument" in ch.name:
                    local = self.arg_local(scope, e.argument_ref, where)
                else:
                    local = self.param_local(e.parameter_ref, path)
                local = self.bind(self.repeat(local, e, path, where), values, where, arg_key)
                items.append(Value(local))
                values.append(local)
            else:
                if e.repeat_entry is not None:
                    raise Unsupported(f"{where}: RepeatEntry on a FixedValueEntry")
                bits = e.size_in_bits
                value = int.from_bytes(hex_bytes(e.binary_value.strip() or "0", where), "big")
                if not 1 <= bits <= 64 or value >> bits:
                    raise Unsupported(f"{where}: FixedValueEntry {e.binary_value} in {bits} bits")
                items.append(Fixed(bits, value))
        return items

    # -- whole mission --

    def compile(self) -> Mission:
        for key in self.containers:
            self.tm_node(key)
        for key in self.mcs:
            self.tc_node(key)
        # A node whose base failed fails too.  Errors list the root causes
        # only; skipping names every node left out, root causes first.
        roots, derived = [], []
        for n in self.order:
            if n.error:
                (derived if n.error.startswith("base ") else roots).append(f"{n.key}: {n.error}")
        msgs = roots + derived
        if msgs and not self.skip_unsupported:
            if derived:
                roots.append(f"and {len(derived)} containers or meta-commands derived from them")
            raise Unsupported("unsupported XTCE constructs:\n  " + "\n  ".join(roots))

        ok = [n for n in self.order if not n.error]
        pos = {id(n): i for i, n in enumerate(self.nodes.values())}
        ok.sort(key=lambda n: (n.kind != "tm", pos[id(n)]))
        names = Namer(RUNTIME_NAMES)
        for n in ok:
            if not n.abstract:
                n.ident = names.take(camel(n.name), camel(n.key))
        roots = [n for n in ok if n.base is None and n.children]
        kinds = []
        for r in roots:
            members = [m for m in [r] + r.descendants() if not m.abstract]
            if members:
                kinds.append(Kind(names.take(camel(r.name) + "Kind", camel(r.key) + "Kind"), r, members, 0))
        concrete = [n for n in ok if not n.abstract]
        used, aggregates = {}, {}
        for n in concrete:
            self._finish(n)
            for it in walk(n.all_items()):
                if isinstance(it, Value):
                    for vt in it.local.vtype.tree():
                        if vt.kind == "enum":
                            used[id(vt)] = vt
                        elif vt.kind == "aggregate":
                            aggregates[id(vt)] = vt
        for vt in used.values():
            vt.ident = names.take(camel(vt.name), camel(vt.key))
        for vt in aggregates.values():
            # A repeated container is often a packet too.
            vt.ident = names.take(camel(vt.name), camel(vt.name) + "Item", camel(vt.key))
        for k in kinds:
            k.max_bytes = max(m.max_bytes for m in k.members)
        return Mission(
            name=self.root.name,
            source="",
            types=list(used.values()),
            aggregates=list(aggregates.values()),
            nodes=concrete,
            kinds=kinds,
            roots=roots,
            skipped=msgs,
        )

    def _finish(self, n: Node) -> None:
        fixed = {}
        for c in n.all_criteria():
            if c.op == "==" and not c.path and c.local not in fixed:
                fixed[c.local] = c.value
        n.fixed = fixed
        names = Namer()
        n.fields = [
            (names.take(snake(it.local.name)), it.local)
            for it in walk(n.all_items())
            if isinstance(it, Value) and it.local not in fixed
        ]
        n.max_bytes = (max_bits(n.all_items()) + 7) // 8


def int_range(e: IntEnc) -> tuple[int, int]:
    """The smallest and largest value an integer encoding holds."""
    b = e.bits
    if e.encoding == "unsigned":
        return 0, (1 << b) - 1
    if e.encoding == "twosComplement":
        return -(1 << (b - 1)), (1 << (b - 1)) - 1
    return -((1 << (b - 1)) - 1), (1 << (b - 1)) - 1


def value_range(vt: VType) -> tuple[int, int]:
    """The smallest and largest value of an integer type, from its encoding
    and calibrator."""
    lo, hi = int_range(vt.enc)
    if vt.calibrator is None:
        return lo, hi
    elo, elim = vt.eng_limits
    cal = vt.calibrator
    if isinstance(cal, Polynomial) and cal.invertible:
        ends = [cal.slope * x + cal.intercept for x in (lo, hi)]
    elif isinstance(cal, Spline) and not cal.extrapolate:
        ends = [c for _, c in cal.points]
    else:
        return elo, elim - 1
    if not all(math.isfinite(x) for x in ends):
        return elo, elim - 1
    return max(math.floor(min(ends)), elo), min(math.ceil(max(ends)), elim - 1)


def int_bounds(vt: VType) -> tuple[int, int]:
    """The smallest and largest value of an integer type."""
    return vt.valid_range or value_range(vt)


def dyn_units(b: DynSize, rng: tuple[int, int]) -> int:
    """The largest ``(slope * x + intercept) / unit`` for x in ``rng``."""
    lo, hi = rng
    top = max(b.slope * lo, b.slope * hi) + b.intercept
    return max(math.floor(top), 0) // b.unit if math.isfinite(top) else MAX_FIELD_BITS


def bounded(vt: VType, b: DynSize, rng: tuple[int, int]) -> VType:
    """A copy of ``vt`` whose size or count is the bound ``b``, with
    ``b.max`` set from the range ``rng`` of the value it adjusts."""
    units = dyn_units(b, rng)
    e = vt.enc
    if isinstance(e, ArrayEnc):
        step = max(item_bits(e.elem) + e.gap, 1)
        b.max = min(units, (MAX_FIELD_BITS + e.gap) // step // b.factor)
        enc = dataclasses.replace(e, count=b)
    elif isinstance(e, StringEnc):
        b.max = min(units, e.max_bits // 8, MAX_FIELD_BITS // 8)
        enc = dataclasses.replace(e, max_bits=b.max * 8, dyn=b)
    else:
        b.max = min(units, MAX_FIELD_BITS // 8)
        enc = dataclasses.replace(e, bits=b.max * 8, dyn=b)
    return dataclasses.replace(vt, enc=enc)


def fixed_bits(vt: VType) -> Optional[int]:
    """The bits a value of this type always occupies, or None if it varies."""
    e = vt.enc
    if isinstance(e, ArrayEnc):
        b = fixed_bits(e.elem)
        if not isinstance(e.count, int) or b is None:
            return None
        return e.count * b + max(e.count - 1, 0) * e.gap
    if isinstance(e, AggregateEnc):
        bs = [fixed_bits(m.vtype) for m in e.members]
        return None if None in bs else sum(bs) + sum(m.skip for m in e.members) + e.pad
    if isinstance(e, StringEnc):
        return e.fixed_bits if e.dyn is None else None
    if isinstance(e, BinaryEnc):
        return None if e.variable else e.bits
    return e.bits


def static_end(items: list[Item], start: int) -> Optional[tuple[int, int]]:
    """(position, furthest bit) after ``items``, in bits from mark
    ``start``, or None when they depend on the data."""
    pos = extent = 0
    marks = {start: 0}
    for it in items:
        if isinstance(it, Cond):
            return None
        if isinstance(it, Seek):
            if isinstance(it.offset, DynSize):
                return None
            if it.from_start is not None and it.from_start not in marks:
                return None
            pos = (marks[it.from_start] if it.from_start is not None else pos) + it.offset
        elif isinstance(it, Mark):
            marks[it.id] = pos
        else:
            bits = fixed_bits(it.local.vtype) if isinstance(it, Value) else it.bits
            if bits is None:
                return None
            pos += bits
        extent = max(extent, pos)
    return pos, extent


def is_rest(vt: VType) -> bool:
    """Whether the value takes the rest of the packet."""
    return isinstance(vt.enc, BinaryEnc) and vt.enc.rest


def item_bits(vt: VType) -> int:
    """The most bits a value of this type occupies."""
    e = vt.enc
    if isinstance(e, ArrayEnc):
        n = e.max_count
        return n * item_bits(e.elem) + max(n - 1, 0) * e.gap
    if isinstance(e, AggregateEnc):
        return sum(m.skip + item_bits(m.vtype) for m in e.members) + e.pad
    if isinstance(e, StringEnc):
        return e.max_bits
    return e.bits


def max_bits(items: list[Item]) -> int:
    """Upper bound on the bits a packet with these items occupies."""
    pos = extent = 0
    marks = {0: 0}
    for it in walk(items):
        if isinstance(it, Seek):
            pos = (marks[it.from_start] if it.from_start is not None else pos) + it.max_offset
        elif isinstance(it, Mark):
            marks[it.id] = pos
        elif isinstance(it, Value):
            pos += item_bits(it.local.vtype)
        else:
            pos += it.bits
        extent = max(extent, pos)
    return extent


def compile_document(root, skip_unsupported: bool = False) -> Mission:
    return Compiler(root, skip_unsupported).compile()
