"""Compile a mission's XTCE documents into the packet model.

The documents are loaded with the ``xtce`` Python package (the XML binding
generated from the XTCE schema), so anything that package accepts can be
compiled.  The compiler resolves names, fills in schema defaults, flattens
included containers and records which XTCE constructs it does not support.

A mission may be split across several documents, as Yamcs allows: the root
SpaceSystem of each document sits at the top of one tree, so a reference
such as ``/PUS/pus-tm`` in one document can name a container defined in
another whose root SpaceSystem is ``PUS``.
"""

from __future__ import annotations

import dataclasses
import keyword
import math
import re
from typing import Callable, Optional

from .common import (checks_hooks, code_unit, encode_text, fast_order,
                     find_unit, items_hooks, terms_hooks, wire_cap)
from .regex import compile_pattern
from .model import (
    MAX_FIELD_BITS, MAX_FIELD_BYTES_LIMIT, AggregateEnc, AllOf, AnyOf, ArrayEnc,
    BinaryEnc, Cond, Criterion, DynSize, EnumLabel, FloatEnc, Fixed, Index,
    IntEnc, Item, Kind, Local, Mark, Member, Mission, Node, Pad, Polynomial,
    Seek, Spline, StringEnc, Term, Unsupported, Value, VType, ref_text,
    term_locals, walk, MATH_UNARY, Check, ContextCal, EndSeek, MathOp, NextSeek,
    Require, TailPad, TailSeek, LengthEnd, Transform, ValueRef, needs_binding,
    Fill, Segment, Verify, Alarm, AlarmDef, Band, LEVELS, Change, Algorithm,
    Stream, Hook, Custom, term_hooks, input_locals, XformEnc)

Key = tuple  # path of SpaceSystem names followed by the item name


def qname(key: Key) -> str:
    return "/" + "/".join(key)


# --- identifiers --------------------------------------------------------------

# Words reserved in any of the target languages, plus names the runtimes and
# generated code use themselves.  Names are made identical in every language,
# so the union is avoided everywhere.
RESERVED = set(keyword.kwlist) | {
    # Rust
    "as",
    "async",
    "await",
    "box",
    "const",
    "crate",
    "dyn",
    "enum",
    "extern",
    "fn",
    "impl",
    "let",
    "loop",
    "match",
    "mod",
    "move",
    "mut",
    "pub",
    "ref",
    "self",
    "static",
    "struct",
    "super",
    "trait",
    "type",
    "unsafe",
    "use",
    "where",
    "abstract",
    "become",
    "do",
    "final",
    "macro",
    "override",
    "priv",
    "typeof",
    "unsized",
    "virtual",
    "yield",
    "try",
    "gen",
    # C++
    "alignas",
    "alignof",
    "and_eq",
    "asm",
    "auto",
    "bitand",
    "bitor",
    "bool",
    "case",
    "catch",
    "char",
    "char8_t",
    "char16_t",
    "char32_t",
    "class",
    "compl",
    "concept",
    "consteval",
    "constexpr",
    "constinit",
    "const_cast",
    "co_await",
    "co_return",
    "co_yield",
    "decltype",
    "default",
    "delete",
    "double",
    "dynamic_cast",
    "explicit",
    "export",
    "float",
    "friend",
    "goto",
    "inline",
    "int",
    "long",
    "mutable",
    "namespace",
    "new",
    "noexcept",
    "not_eq",
    "nullptr",
    "operator",
    "or_eq",
    "private",
    "protected",
    "public",
    "register",
    "reinterpret_cast",
    "requires",
    "short",
    "signed",
    "sizeof",
    "static_assert",
    "static_cast",
    "switch",
    "template",
    "this",
    "thread_local",
    "throw",
    "typedef",
    "typeid",
    "typename",
    "union",
    "unsigned",
    "using",
    "void",
    "volatile",
    "wchar_t",
    "xor",
    "xor_eq",
    "main",
    # C keywords and standard macros a struct member cannot be named
    "restrict",
    "errno",
    "assert",
    "stdin",
    "stdout",
    "stderr",
    "offsetof",
    "true",
    "false",
    "NULL",
    "EOF",
    # Python builtins that would shadow useful names in dataclasses, and the
    # modules generated Python uses in class bodies
    "bytes",
    "int",
    "float",
    "str",
    "list",
    "dict",
    "type",
    "id",
    "len",
    "pk",
    "dataclasses",
    # generated members
    "decode",
    "decode_prefix",
    "encode",
    "encode_into",
    "describe",
    "label",
    "value",
    "raw",
    "name",
    "max_bytes",
    "data",
    "buf",
    "r",
    "w",
    "init",
    "to_bytes"}


def hex_bytes(text: str, where: str) -> bytes:
    """The bytes of an ``xs:hexBinary`` value; an odd number of digits is
    read as if it had a leading 0."""
    if not re.fullmatch(r"[0-9A-Fa-f]*", text):
        raise Unsupported(f"{where}: {text!r} is not hexBinary")
    return bytes.fromhex(text.zfill(len(text) + len(text) % 2))


# One step of a reference after the parameter or argument name: ``.member``
# or ``[index]``.  Names can't contain ``.``, ``[`` or ``]``.
STEP = re.compile(r"\.([^.\[\]:/ \t]+)|\[(\d+)\]")


def member_path(ref: str) -> tuple[str, tuple]:
    """Splits a reference to an aggregate member or array element
    (``P.a.b``, ``P[2]``, ``P[1].a``) into the reference to its parameter
    or argument and the steps: member names, and ints for indexes.  Only
    the last path segment can have steps, and ``.`` and ``..`` are path
    segments, not members."""
    head, sep, last = ref.strip().rpartition("/")
    name = re.match(r"[^.\[\]]*", last).group()
    if last in (".", "..") or not name or name == last:
        return ref.strip(), ()
    steps, pos = [], len(name)
    for m in STEP.finditer(last, pos):
        if m.start() != pos:
            break
        steps.append(m.group(1) if m.group(1) is not None else int(m.group(2)))
        pos = m.end()
    if pos != len(last):
        raise Unsupported(f"malformed reference {ref.strip()!r}")
    return head + sep + name, tuple(steps)


def walk_members(vt: VType, members: tuple, what: str) -> tuple[tuple, VType]:
    """The path (field names, and Index for array elements) of the steps
    ``members`` inside type ``vt``, and the type they reach."""
    path = []
    steps = list(members)
    while steps:
        step = steps.pop(0)
        if isinstance(step, int):
            e = vt.enc
            if vt.kind != "array":
                raise Unsupported(f"{what}: {vt.kind} type {vt.key} is not an "
                                  "array")
            # One index per dimension, flattened with the last fastest.
            index = [step]
            while len(index) < e.dims and steps and isinstance(steps[0], int):
                index.append(steps.pop(0))
            if len(index) < e.dims:
                raise Unsupported(f"{what}: {len(index)} indexes for a "
                                  f"{e.dims}-dimensional array")
            varies = isinstance(e.count, DynSize)
            # flat + stride * d, d the size of the dimension that varies
            # (if any), whose index must be below d.
            flat = stride = need = 0
            per = 1
            for i, start, size in zip(index, e.starts, e.sizes):
                if size is None:
                    if i < start:
                        raise Unsupported(f"{what}: index {i} is before "
                                          f"{start}")
                    # Whether the element is there is known only when the
                    # packet is decoded or encoded.
                    need = i - start + 1
                    stride = flat
                    flat = i - start
                    continue
                if not start <= i < start + size:
                    raise Unsupported(f"{what}: index {i} is outside "
                                      f"{start}..{start + size - 1}")
                flat = flat * size + i - start
                stride *= size
                per *= size
            if varies and stride:
                path.append(Index(flat, None, stride, per, need))
                vt = e.elem
                continue
            path.append(Index(flat, None if varies else e.count))
            vt = e.elem
            continue
        if vt.kind != "aggregate":
            raise Unsupported(f"{what}: {vt.kind} type {vt.key} has no member "
                              f"{step!r}")
        m = next((m for m in vt.enc.members if m.name == step), None)
        if m is None:
            raise Unsupported(f"{what}: aggregate type {vt.key} has no member "
                              f"{step!r}")
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
    if (not ranges
            or any(r.min_inclusive is None or r.max_inclusive is None
                   for r in ranges)):
        return None
    if (calibrated
            and any(getattr(r, "valid_range_applies_to_calibrated",
                            None) is False for r in ranges)):
        return None
    return (min(r.min_inclusive for r in ranges),
            max(r.max_inclusive for r in ranges))


def valid_bands(t, vt: "VType", calibrated: bool) -> list:
    """The ranges of type ``t``'s ValidRange, or of an argument type's
    ValidRangeSet, as Bands a value must lie in (any of them).  None when
    a calibrated type's range applies to the raw value, which the generated
    code doesn't keep."""
    if getattr(t, "valid_range", None) is not None:
        ranges = [t.valid_range]
    elif getattr(t, "valid_range_set", None) is not None:
        ranges = list(t.valid_range_set.valid_range)
    else:
        return []
    if calibrated and any(getattr(r, "valid_range_applies_to_calibrated",
                                  None) is False for r in ranges):
        return []
    out = []
    for r in ranges:
        lo_excl = getattr(r, "min_exclusive", None)
        hi_excl = getattr(r, "max_exclusive", None)
        b = Band(0, r.min_inclusive if r.min_inclusive is not None else lo_excl,
                 r.min_inclusive is not None or lo_excl is None,
                 r.max_inclusive if r.max_inclusive is not None else hi_excl,
                 r.max_inclusive is not None or hi_excl is None, True)
        out.append(int_band(b, vt) if vt.kind == "int" else b)
    return out


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
RUNTIME_NAMES = {"Packet", "PacketError", "Codec", "BitReader", "BitWriter",
                 "Buffer", "Result", "Error", "ManagedBuffer", "Optional",
                 "Union", "Any", "String", "Vec", "Self", "Option", "Box", "Ok",
                 "Err", "Some", "None", "AlarmMonitor"}


# The AncillaryData that marks a parameter or argument as a packet length
# field; its value is the number to add to the bytes after the field.
LENGTH_TAG = "tcs-xtce.length"
# The AncillaryData of a root SpaceSystem that gives the most bytes a field
# whose size or count another value gives may take on the wire.
MAX_FIELD_TAG = "tcs-xtce.max-field-bytes"
# The AncillaryData of a root SpaceSystem that names the dialect of XTCE the
# documents follow where the standard leaves something open.
DIALECT_TAG = "tcs-xtce.dialect"
DIALECTS = ("yamcs",)


def ancillary_str(item, tag: str, where: str) -> Optional[str]:
    """The value of ``item``'s AncillaryData ``tag``, without surrounding
    space, or None if it has none."""
    ads = item.ancillary_data_set
    tags = [a for a in (ads.ancillary_data if ads else []) if a.name == tag]
    if not tags:
        return None
    if len(tags) > 1:
        raise Unsupported(f"{where}: more than one {tag}")
    return (tags[0].value or "").strip()


def ancillary_int(item, tag: str, where: str) -> Optional[int]:
    """The integer value of ``item``'s AncillaryData ``tag``, or None if it
    has none."""
    v = ancillary_str(item, tag, where)
    if v is None:
        return None
    try:
        return int(v)
    except ValueError:
        raise Unsupported(f"{where}: {tag} {v!r} is not an integer") from None


def length_adjust(item, vt: VType, where: str) -> Optional[int]:
    """The adjustment of a packet length field, or None if ``item`` (a
    Parameter or Argument) isn't one."""
    adjust = ancillary_int(item, LENGTH_TAG, where)
    if adjust is None:
        return None
    if (vt.kind != "int" or not isinstance(vt.enc, IntEnc) or vt.enc.signed
            or vt.enc.bcd or vt.calibrator is not None):
        raise Unsupported(f"{where}: a packet length field must be an "
                          "uncalibrated unsigned binary integer")
    if vt.wide:
        raise Unsupported(f"{where}: a {vt.enc.bits}-bit packet length field")
    if not fast_order(vt.enc):
        raise Unsupported(f"{where}: a packet length field must be big or "
                          "little endian in whole bytes, most significant bit "
                          "first")
    return adjust


# --- the compiler
# ---------------------------------------------------------------


class Compiler:
    def __init__(self, roots: list, skip_unsupported: bool = False,
                 dialect: Optional[str] = None):
        """``roots`` is the root SpaceSystem of each of the mission's
        documents; ``dialect`` is the one they follow when they don't say
        (see DIALECT_TAG)."""
        self.roots = list(roots)
        self.skip_unsupported = skip_unsupported
        if dialect is not None and dialect not in DIALECTS:
            raise Unsupported(f"dialect {dialect!r} is not one of "
                              f"{', '.join(DIALECTS)}")
        self.dialect = dialect
        self.ptypes: dict[Key, tuple] = {}
        self.params: dict[Key, tuple] = {}
        self.atypes: dict[Key, tuple] = {}
        self.containers: dict[Key, tuple] = {}
        self.messages: dict[Key, tuple] = {}
        self.algorithms: dict[Key, tuple] = {}
        self.streams: dict[Key, tuple] = {}
        self.services: dict[Key, tuple] = {}
        # (kind, name) -> Hook, for the custom algorithms used
        self.hooks: dict[tuple, Hook] = {}
        self.mcs: dict[Key, tuple] = {}
        # CommandContainer -> MetaCommand
        self.mc_containers: dict[Key, Key] = {}
        # The containers a CommandContainer may include or extend: those of
        # CommandContainerSets, then telemetry's.
        self.tc_containers: dict[Key, tuple] = {}
        # Parameter and argument types are separate namespaces, so the cache
        # key says which one a type comes from: (is_argument_type, key).
        self.vtypes: dict[tuple[bool, Key], VType] = {}
        self.nodes: dict[Key, Node] = {}
        self.order: list[Node] = []
        self._ids = 0
        # aggregates being compiled, to find cycles
        self._aggregating: set = set()
        # container key -> aggregate of a repeated container
        self.ctypes: dict = {}
        # ErrorDetectCorrect of the containers included in the node being
        # compiled: (container, path, start mark, end mark, scope, name)
        self.included_checks: list = []
        seen = set()
        for root in self.roots:
            if root.name in seen:
                raise Unsupported("two documents have the root SpaceSystem "
                                  f"{root.name!r}")
            seen.add(root.name)
            self._index(root, ())
        self.max_field_bits = 8 * self.max_field_bytes()
        self.dialect = self.root_dialect(self.dialect)
        for k, v in self.containers.items():
            self.tc_containers.setdefault(k, v)

    def max_field_bytes(self) -> int:
        """The roots' tcs-xtce.max-field-bytes, which all that give one must
        agree on, or the default."""
        given = {}
        for root in self.roots:
            n = ancillary_int(root, MAX_FIELD_TAG, f"SpaceSystem {root.name}")
            if n is None:
                continue
            if not 1 <= n <= MAX_FIELD_BYTES_LIMIT:
                raise Unsupported(f"SpaceSystem {root.name}: {MAX_FIELD_TAG} "
                                  f"{n} is not from 1 to "
                                  f"{MAX_FIELD_BYTES_LIMIT}")
            given[root.name] = n
        if len(set(given.values())) > 1:
            names = ", ".join(f"{k} ({v})" for k, v in given.items())
            raise Unsupported(f"the documents' {MAX_FIELD_TAG} differ: {names}")
        return next(iter(given.values()), MAX_FIELD_BITS // 8)

    def root_dialect(self, default: Optional[str]) -> Optional[str]:
        """The roots' tcs-xtce.dialect, which all that give one must agree
        on, and ``default``, if given, too; or ``default``."""
        given = {} if default is None else {"(the dialect given)": default}
        for root in self.roots:
            d = ancillary_str(root, DIALECT_TAG, f"SpaceSystem {root.name}")
            if d is None:
                continue
            if d not in DIALECTS:
                raise Unsupported(f"SpaceSystem {root.name}: {DIALECT_TAG} "
                                  f"{d!r} is not one of {', '.join(DIALECTS)}")
            given[root.name] = d
        if len(set(given.values())) > 1:
            names = ", ".join(f"{k} ({v})" for k, v in given.items())
            raise Unsupported(f"the documents' {DIALECT_TAG} differ: {names}")
        return next(iter(given.values()), None)

    # -- indexing and name resolution --

    def _index(self, ss, parent: Key) -> None:
        path = parent + (ss.name,)
        if parent and ancillary_int(ss, MAX_FIELD_TAG, qname(path)) is not None:
            raise Unsupported(f"SpaceSystem {qname(path)}: {MAX_FIELD_TAG} is "
                              "only read on a document's root SpaceSystem")
        if parent and ancillary_str(ss, DIALECT_TAG, qname(path)) is not None:
            raise Unsupported(f"SpaceSystem {qname(path)}: {DIALECT_TAG} is "
                              "only read on a document's root SpaceSystem")
        if ss.service_set:
            for sv in ss.service_set.service:
                self.services[path + (sv.name,)] = (path, sv)
        tm, tc = ss.telemetry_meta_data, ss.command_meta_data
        for md in (tm, tc):
            if md is None:
                continue
            if md.algorithm_set:
                for ch in md.algorithm_set.items:
                    self.algorithms[path + (ch.value.name,)] = (path, ch.value)
            if md.parameter_type_set:
                for ch in md.parameter_type_set.items:
                    self.ptypes[path + (ch.value.name,)] = (path, ch.name,
                                                            ch.value)
            if md.parameter_set:
                for ch in md.parameter_set.items:
                    if ch.name == "Parameter":
                        self.params[path + (ch.value.name,)] = (path, ch.value)
        if tm and tm.stream_set:
            for ch in tm.stream_set.items:
                self.streams[path + (ch.value.name,)] = (path, ch.name,
                                                         ch.value)
        if tm and tm.message_set:
            for m in tm.message_set.message:
                self.messages[path + (m.name,)] = (path, m)
        if tm and tm.container_set:
            for sc in tm.container_set.sequence_container:
                self.containers[path + (sc.name,)] = (path, sc)
        if tc:
            if tc.argument_type_set:
                for ch in tc.argument_type_set.items:
                    self.atypes[path + (ch.value.name,)] = (path, ch.name,
                                                            ch.value)
            if tc.command_container_set:
                for c in tc.command_container_set.command_container:
                    self.tc_containers[path + (c.name,)] = (path, c)
            if tc.meta_command_set:
                for ch in tc.meta_command_set.items:
                    if ch.name == "MetaCommand":
                        key = path + (ch.value.name,)
                        self.mcs[key] = (path, ch.value)
                        if ch.value.command_container is not None:
                            name = ch.value.command_container.name
                            self.mc_containers[path + (name,)] = key
        for child in ss.space_system:
            self._index(child, path)

    def resolve(self, ref: str, cur: Key, table: dict, what: str) -> Key:
        """Resolve an XTCE name reference relative to SpaceSystem ``cur``.

        Absolute paths start with ``/`` and the name of a document's root
        SpaceSystem.  Relative paths may use ``.`` and ``..``.  An unqualified
        name, or a relative path that doesn't start with ``.`` or ``..``, is
        looked up from ``cur`` and then from each enclosing SpaceSystem, as
        Yamcs does, so ``cfs-ccsds/cfs-cmd`` can name a meta-command in
        another document whose root SpaceSystem is ``cfs-ccsds``.
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
            starts = [cur]
            if parts[0] not in (".", ".."):
                starts = [cur[:i] for i in range(len(cur), -1, -1)]
            for start in starts:
                base = list(start)
                for p in parts[:-1]:
                    if p == "..":
                        if base:
                            base.pop()
                    elif p and p != ".":
                        base.append(p)
                key = tuple(base) + (parts[-1],)
                if key in table:
                    return key
        raise Unsupported(f"unresolved {what} reference {ref!r} in "
                          f"{qname(cur)}")

    def _id(self) -> int:
        self._ids += 1
        return self._ids

    # -- types --

    def vtype(self, key: Key, table: dict) -> VType:
        cache = (table is self.atypes, key)
        if cache in self.vtypes:
            return self.vtypes[cache]
        vt = self._vtype(key, table, cache)
        if table is self.ptypes and vt.alarm is None:
            path, _, _ = table[key]
            vt.alarm = self.alarm(self.inherited(key, table), vt, path,
                                  f"type {qname(key)}")
        return vt

    # -- alarms --

    def alarm(self, t, vt: VType, path: Key, where: str) -> Optional[Alarm]:
        """The DefaultAlarm and ContextAlarmList of parameter type ``t``.
        One that can't be evaluated is kept with a ``note`` saying why, so
        that the packets still compile."""
        da = getattr(t, "default_alarm", None)
        cl = getattr(t, "context_alarm_list", None)
        if da is None and cl is None:
            return None
        try:
            return Alarm(
                path, self.alarm_def(da, vt, path, where) if da else None,
                [(ca.context_match, self.alarm_def(ca, vt, path, where))
                 for ca in (cl.context_alarm if cl else [])])
        except Unsupported as e:
            return Alarm(path, None, note=str(e))

    def alarm_def(self, a, vt: VType, path: Key, where: str) -> AlarmDef:
        d = AlarmDef(min_violations=a.min_violations or 1,
                     min_conformance=a.min_conformance or 1)
        if a.disabled:
            return d
        if vt.wide:
            raise Unsupported(f"{where}: an alarm on a {vt.enc.bits}-bit "
                              "integer")
        if a.choice is not None and a.choice.name == "CustomAlarm":
            alg = a.choice.value.input_algorithm
            d.custom = (self.hook("alarm", qname(path + (alg.name,))), alg)
        elif a.choice is not None:
            if a.choice.name != "AlarmConditions":
                raise Unsupported(f"{where}: a {a.choice.name}")
            ac = a.choice.value
            for level, name in enumerate(LEVELS):
                mc = getattr(ac, f"{name}_alarm", None)
                if mc is not None:
                    d.conditions.append((level, mc))
        cr = getattr(a, "change_alarm_ranges", None)
        if cr is not None:
            if vt.kind not in ("int", "float"):
                raise Unsupported(f"{where}: ChangeAlarmRanges on a {vt.kind} "
                                  "value")
            if cr.span_of_interest_in_seconds:
                raise Unsupported(f"{where}: ChangeAlarmRanges with a "
                                  "spanOfInterestInSeconds")
            d.change = Change(range_bands(cr, 1.0),
                              cr.change_type is None
                              or cr.change_type.value == "changePerSecond",
                              cr.change_basis is not None
                              and cr.change_basis.value == "percentageChange",
                              cr.span_of_interest_in_samples or 1)
        cr = getattr(a, "change_per_second_alarm_ranges", None)
        if cr is not None:
            if vt.kind != "float":
                raise Unsupported(f"{where}: ChangePerSecondAlarmRanges on a "
                                  "time held as text or bytes")
            tu = cr.time_units
            d.change = Change(
                range_bands(cr, TIME_UNITS[tu.value if tu else "seconds"]))
        ranges = getattr(a, "static_alarm_ranges", None)
        if ranges is not None:
            tu = getattr(ranges, "time_units", None)
            d.bands += range_bands(ranges,
                                   TIME_UNITS[tu.value if tu else "seconds"])
        multi = getattr(a, "alarm_multi_ranges", None)
        for r in multi.range if multi is not None else []:
            level = LEVELS.index(r.level.value) if r.level else 0
            inside = r.range_form is not None and r.range_form.value == "inside"
            d.bands.append(band(r, level, inside, 1.0))
        if getattr(a, "default_alarm_level", None) is not None:
            d.default = LEVELS.index(a.default_alarm_level.value)
        el = getattr(a, "enumeration_alarm_list", None)
        for ea in el.enumeration_alarm if el is not None else []:
            lab = next((lab for lab in vt.labels
                        if lab.label == ea.enumeration_label), None)
            if lab is None:
                raise Unsupported(f"{where}: an alarm on unknown label "
                                  f"{ea.enumeration_label!r}")
            d.labels.append((lab.value, lab.max_value,
                             LEVELS.index(ea.alarm_level.value)))
        sl = getattr(a, "string_alarm_list", None)
        for sa in sl.string_alarm if sl is not None else []:
            d.patterns.append((compile_pattern(sa.match_pattern),
                               LEVELS.index(sa.alarm_level.value)))
        if d.bands and vt.kind not in ("int", "float", "time"):
            raise Unsupported(f"{where}: alarm ranges on a {vt.kind} value")
        if vt.kind == "int":
            d.bands = [int_band(b, vt) for b in d.bands]
        return d

    def _vtype(self, key: Key, table: dict, cache: tuple) -> VType:
        path, kind, _ = table[key]
        where = f"type {qname(key)}"
        t = self.inherited(key, table)
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
        if (base not in ("Integer", "Float", "Enumerated", "Boolean", "String",
                         "Binary")):
            raise Unsupported(f"{where}: {kind} is not supported")
        if t.choice is None:
            raise Unsupported(f"{where}: no data encoding")
        enc_name, e = t.choice.name, t.choice.value
        enc, cal = self.encoding(enc_name, e, path, where)
        if (isinstance(enc, IntEnc) and enc.bits > 64
                and (base != "Integer" or cal is not None)):
            # Only its integer value is kept; nothing computes with it.
            what = ("calibrated integer" if base == "Integer" else base.lower())
            raise Unsupported(f"{where}: {what} in a {enc.bits}-bit integer "
                              "encoding")
        if isinstance(enc, BinaryEnc) and enc.transform is not None:
            if base != "Binary":
                raise Unsupported(f"{where}: {base.lower()} with transform "
                                  "algorithms")
            enc = self.xform(enc, False, where)
        vt = VType(key=qname(key), name=t.name, kind="", enc=enc,
                   calibrator=cal)
        if base == "Float":
            if not isinstance(enc, (IntEnc, FloatEnc)):
                raise Unsupported(f"{where}: float with {enc_name}")
            vt.kind = "float"
            vt.valid = valid_bands(t, vt, cal is not None)
        elif base in ("Integer", "Enumerated", "Boolean"):
            # A boolean may also be one of two strings.
            if (not isinstance(enc, IntEnc)
                    and not (base == "Boolean" and isinstance(enc, StringEnc))):
                raise Unsupported(f"{where}: {base.lower()} with {enc_name}")
            if cal is not None and base != "Integer":
                article = "an" if base[0] in "AEIOU" else "a"
                raise Unsupported(f"{where}: calibrator on {article} "
                                  f"{base.lower()} type")
            vt.kind = {"Integer": "int", "Enumerated": "enum",
                       "Boolean": "bool"}[base]
            if base == "Integer":
                if cal is not None:
                    # The calibrated value is rounded to an integer of the
                    # type's own size, not the encoding's.
                    bits = t.size_in_bits if t.size_in_bits is not None else 32
                    if not 1 <= bits <= 64:
                        raise Unsupported(f"{where}: {bits}-bit integers")
                    vt.eng = (bits, t.signed is not False)
                vt.valid_range = integer_range(t, cal is not None)
                vt.valid = valid_bands(t, vt, cal is not None)
        elif base == "String":
            # A string encoded as binary is its UTF-8 bytes, filling the field.
            if not isinstance(enc, (StringEnc, BinaryEnc)):
                raise Unsupported(f"{where}: string with {enc_name}")
            if isinstance(enc, BinaryEnc) and enc.rest:
                raise Unsupported(f"{where}: string with binary size -1")
            vt.kind = "string"
        else:
            if not isinstance(enc, (BinaryEnc, XformEnc)):
                raise Unsupported(f"{where}: binary with {enc_name}")
            vt.kind = "binary"
        if vt.kind == "enum":
            names = Namer()
            for ev in t.enumeration_list.enumeration:
                hi = ev.max_value if ev.max_value is not None else ev.value
                vt.labels.append(EnumLabel(ev.label,
                                           names.take(upper(ev.label)),
                                           ev.value, hi))
        if vt.kind == "bool":
            vt.one_string = (t.one_string_value
                             if t.one_string_value is not None else "True")
            vt.zero_string = (t.zero_string_value
                              if t.zero_string_value is not None else "False")
            if isinstance(enc, StringEnc):
                self.check_bool_strings(vt, where)
        self.vtypes[cache] = vt
        return vt

    def inherited(self, key: Key, table: dict, seen: tuple = ()):
        """The type ``key`` with what it inherits from its ``baseType``, if
        it has one, filled in: every attribute and child element it leaves
        out comes from its base type, which must be the same kind of type,
        and those it gives replace the base's.  (References in what it
        inherits are resolved from its own SpaceSystem.)"""
        path, kind, t = table[key]
        if not getattr(t, "base_type", None):
            return t
        where = f"type {qname(key)}"
        if key in seen:
            raise Unsupported(f"{where}: its baseType chain is a cycle")
        bkey = self.resolve(t.base_type, path, table, "base type")
        if table[bkey][1] != kind:
            raise Unsupported(f"{where}: its baseType {qname(bkey)} is another "
                              f"kind of type, {table[bkey][1]}")
        base = self.inherited(bkey, table, seen + (key,))
        own = {f.name: getattr(t, f.name) for f in dataclasses.fields(t)
               if getattr(t, f.name) is not None}
        own["base_type"] = None
        return dataclasses.replace(base, **own)

    @staticmethod
    def check_bool_strings(vt: VType, where: str) -> None:
        """A boolean encoded as a string: its two strings must differ and
        fit the encoding."""
        if vt.one_string == vt.zero_string:
            raise Unsupported(f"{where}: oneStringValue and zeroStringValue "
                              f"are both {vt.one_string!r}")
        e = vt.enc
        for s in (vt.one_string, vt.zero_string):
            try:
                b = encode_text(s, e.charset)
            except ValueError:
                raise Unsupported(f"{where}: {s!r} is not "
                                  f"{e.charset}") from None
            if (e.terminator is not None
                    and find_unit(b, e.terminator, code_unit(e.charset)) >= 0):
                raise Unsupported(f"{where}: {s!r} contains the termination "
                                  "character")
            if e.dyn is None and len(b) > wire_cap(vt):
                raise Unsupported(f"{where}: {s!r} does not fit its encoding")

    def time_type(self, key: Key, base: str, t, path: Key, where: str) -> VType:
        """Times are seconds from the reference time: the encoding's raw
        value, calibrated, then ``scale * value + offset`` in its units.  A
        time encoded as a string or as binary data keeps that text or those
        bytes."""
        enc = t.encoding
        if enc is None:
            raise Unsupported(f"{where}: time type without Encoding")
        units = enc.units.value if enc.units else "seconds"
        scale = enc.scale if enc.scale is not None else 1.0
        offset = enc.offset if enc.offset is not None else 0.0
        what = "absolute" if base == "AbsoluteTime" else "relative"
        ref = self.reference_time(t.reference_time, where)
        e, cal = self.encoding(enc.choice.name, enc.choice.value, path, where)
        if isinstance(e, IntEnc) and e.bits > 64:
            raise Unsupported(f"{where}: time in a {e.bits}-bit integer "
                              "encoding")
        if isinstance(e, (StringEnc, BinaryEnc)):
            if (units, scale, offset) != ("seconds", 1.0, 0.0):
                raise Unsupported(f"{where}: a time encoded as "
                                  f"{enc.choice.name} with units, scale or "
                                  "offset")
            if isinstance(e, BinaryEnc) and e.transform is not None:
                # The transform algorithms give the time in seconds.
                return VType(key=qname(key), name=t.name, kind="float",
                             enc=self.xform(e, True, where),
                             doc=f"{what} time, seconds {ref}")
            kind = "string" if isinstance(e, StringEnc) else "binary"
            return VType(key=qname(key), name=t.name, kind=kind, enc=e,
                         doc=f"{what} time as encoded, {ref}")
        a = scale * TIME_UNITS[units]
        cal = affine(cal, a, offset * TIME_UNITS[units])
        return VType(key=qname(key), name=t.name, kind="float", enc=e,
                     calibrator=cal, doc=f"{what} time, seconds {ref}")

    @staticmethod
    def reference_time(ref, where: str) -> str:
        """What a time is reckoned from, for documentation."""
        if ref is None:
            return "from an unstated time"
        if ref.choice.name == "OffsetFrom":
            return f"after {ref.choice.value.parameter_ref}"
        epoch = str(ref.choice.value).strip()
        if (epoch not in ("TAI", "J2000", "UNIX", "GPS")
                and not re.fullmatch(r"-?\d{4,}-\d\d-\d\d(T\d\d:\d\d:\d\d"
                                     r"(\.\d+)?)?(Z|[+-]\d\d:\d\d)?", epoch)):
            raise Unsupported(f"{where}: epoch {epoch!r} is not a date, a date "
                              "and time, or TAI, J2000, UNIX or GPS")
        return f"since epoch {epoch}"

    def array_type(self, key: Key, table: dict, path: Key, t,
                   where: str) -> VType:
        """An array type: its elements in the order they are encoded, which
        for several dimensions is the order of the DimensionList."""
        ekey = self.resolve(t.array_type_ref, path, table, "array element type")
        # An element may itself be an array, and its size may come from
        # another value: each packet entry binds it.
        elem = self.vtype(ekey, table)
        if is_rest(elem):
            raise Unsupported(f"{where}: elements that take the rest of the "
                              "packet")
        return VType(key=qname(key), name=t.name, kind="array",
                     enc=self.dimensions(elem, t.dimension_list, path, where))

    def dimensions(self, elem: VType, dl, path: Key, where: str) -> ArrayEnc:
        """The ArrayEnc of a DimensionList's elements, flattened with the
        last dimension varying fastest."""
        dims = dl.dimension if dl is not None else []
        if not dims:
            raise Unsupported(f"{where}: array without dimensions")
        fixed, dyn, starts, sizes, all_sizes = 1, None, [], [], []
        for d in dims:
            start = self.int_value(d.starting_index, path,
                                   "array StartingIndex", where)
            end = self.int_value(d.ending_index, path, "array EndingIndex",
                                 where)
            if isinstance(start, DynSize):
                raise Unsupported(f"{where}: StartingIndex from DynamicValue")
            starts.append(start)
            if isinstance(end, int):
                if end < start - 1:
                    raise Unsupported(f"{where}: EndingIndex {end} is before "
                                      f"StartingIndex {start}")
                fixed *= end - start + 1
                sizes.append(end - start + 1)
                all_sizes.append(end - start + 1)
            elif dyn is not None:
                raise Unsupported(f"{where}: more than one dimension from "
                                  "DynamicValue")
            else:
                end.intercept += 1 - start  # the count is end - start + 1
                dyn = end
                all_sizes.append(None)
        if dyn is not None:
            dyn.factor = fixed
        count = dyn if dyn is not None else fixed
        shape = tuple(sizes) if dyn is None else ()
        return ArrayEnc(elem, count, dims=len(dims), starts=tuple(starts),
                        shape=shape, sizes=tuple(all_sizes))

    def subset(self, local: Local, e, path: Key, where: str) -> Local:
        """Turns the value of an array entry with a DimensionList into the
        subset it gives: a list of just those elements."""
        dl = getattr(e, "dimension_list", None)
        if dl is None:
            return local
        vt = local.vtype
        if vt.kind != "array":
            raise Unsupported(f"{where}: DimensionList on {vt.kind} value "
                              f"{local.name}")
        full = vt.enc
        enc = self.dimensions(full.elem, dl, path, where)
        if enc.dims != full.dims:
            raise Unsupported(f"{where}: {enc.dims} dimensions for "
                              f"{full.dims}-dimensional {local.name}")
        for k, start in enumerate(enc.starts):
            if start < full.starts[k]:
                raise Unsupported(f"{where}: subset of {local.name} starts "
                                  f"before its index {full.starts[k]}")
            if (full.shape and enc.shape
                    and start + enc.shape[k] > full.starts[k] + full.shape[k]):
                raise Unsupported(f"{where}: subset of {local.name} ends after "
                                  "its index "
                                  f"{full.starts[k] + full.shape[k] - 1}")
        doc = f"a subset of {vt.key}" + (f"; {vt.doc}" if vt.doc else "")
        local.vtype = VType(key=vt.key, name=vt.name, kind="array", enc=enc,
                            doc=doc)
        return local

    def aggregate_type(self, key: Key, table: dict, path: Key, t, where: str,
                       cache) -> VType:
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
                    raise Unsupported(f"{where}: member {m.name} has a size "
                                      "from a DiscreteLookupList")
                if d is not None:
                    # Like Yamcs, a reference to an earlier member's name
                    # means that member of the same aggregate value.
                    sib = next((x for x in members if x.name == d.name), None)
                    if sib is None:
                        raise Unsupported(f"{where}: member {m.name} has a "
                                          "size that another value gives, and "
                                          f"{d.name} is not an earlier member")
                    if sib.vtype.kind != "int":
                        raise Unsupported(f"{where}: size or count from "
                                          f"{sib.vtype.kind} member {sib.name}")
                    mt = bounded(mt, dataclasses.replace(d, member=sib.ident),
                                 int_bounds(sib.vtype), self.max_field_bits)
                if is_rest(mt):
                    raise Unsupported(f"{where}: member {m.name} takes the "
                                      "rest of the packet")
                members.append(Member(m.name, names.take(snake(m.name)), mt))
        finally:
            self._aggregating.discard(cache)
        return VType(key=qname(key), name=t.name, kind="aggregate",
                     enc=AggregateEnc(members))

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
        instance = getattr(ref, "instance", None) or 0
        if instance > 0:
            raise Unsupported(f"{where}: {what} from instance {instance} of a "
                              "parameter, a value yet to come")
        adj = dv.linear_adjustment
        slope = adj.slope if adj is not None and adj.slope is not None else 1.0
        intercept = (adj.intercept if adj is not None
                     and adj.intercept is not None else 0.0)
        if not (math.isfinite(slope) and math.isfinite(intercept)):
            raise Unsupported(f"{where}: {what} with a non-finite "
                              "LinearAdjustment")
        if kind == "ParameterInstanceRef":
            name = ref.parameter_ref.strip()
            base, members = member_path(name)
            try:
                key = qname(self.resolve(base, path, self.params, "parameter"))
            except Unsupported:
                key = None  # an error unless it names an aggregate member
            return DynSize(key=key, name=name, members=members, slope=slope,
                           intercept=intercept, instance=instance)
        name = ref.argument_ref.strip()
        base, members = member_path(name)
        return DynSize(arg=base, name=name, members=members, slope=slope,
                       intercept=intercept)

    @staticmethod
    def byte_order(e, bits: int, where: str):
        """The bit order (``lsb``) and byte order (``order``) of an
        encoding, as IntEnc holds them.  A byte order list must give the
        significance of each of a whole number of bytes once; a list that
        amounts to big or little endian is that."""
        lsb = bool(getattr(e, "bit_order", None)
                   and e.bit_order.value == "leastSignificantBitFirst")
        bo = (e.byte_order or "mostSignificantByteFirst").strip()
        if bo == "mostSignificantByteFirst":
            return lsb, "be"
        if bo == "leastSignificantByteFirst":
            return lsb, "le"
        sig = tuple(int(x) for x in bo.split(","))
        if bits % 8 or sorted(sig) != list(range(bits // 8)):
            raise Unsupported(f"{where}: byteOrder {bo!r} does not give each "
                              f"of the {bits / 8:g} bytes of the value once")
        if sig == tuple(range(bits // 8 - 1, -1, -1)):
            return lsb, "be"
        if sig == tuple(range(bits // 8)):
            return lsb, "le"
        return lsb, sig

    def encoding(self, name: str, e, path: Key, where: str):
        enc, cal = self._encoding(name, e, path, where)
        if (isinstance(enc, (StringEnc, BinaryEnc))
                and getattr(e, "bit_order", None)
                and e.bit_order.value == "leastSignificantBitFirst"):
            # Its bytes read the same either way when they start on a byte
            # boundary, which check_aligned makes sure of.
            enc.lsb = True
        return enc, cal

    def _encoding(self, name: str, e, path: Key, where: str):
        if getattr(e, "error_detect_correct", None):
            # XTCE deprecates it here: it doesn't say where the value goes.
            raise Unsupported(f"{where}: ErrorDetectCorrect in a data encoding "
                              "(deprecated; give it in the container's "
                              "BinaryEncoding)")
        if name == "IntegerDataEncoding":
            bits = e.size_in_bits if e.size_in_bits is not None else 8
            kind = e.encoding.value if e.encoding else "unsigned"
            if bits < 1:
                raise Unsupported(f"{where}: {bits}-bit integers")
            if kind == "BCD" and bits % 8:
                raise Unsupported(f"{where}: BCD in {bits} bits, not whole "
                                  "bytes")
            if kind == "packedBCD" and bits % 4:
                raise Unsupported(f"{where}: packedBCD in {bits} bits, not "
                                  "whole digits")
            if (kind in ("twosComplement", "signMagnitude", "onesComplement")
                    and bits < 2):
                raise Unsupported(f"{where}: 1-bit signed integer")
            lsb, order = self.byte_order(e, bits, where)
            return (IntEnc(bits, kind, order, lsb),
                    self.calibrator(e, path, where))
        if name == "FloatDataEncoding":
            kind = e.encoding.value if e.encoding else "IEEE754_1985"
            kind = "IEEE754" if kind == "IEEE754_1985" else kind
            bits = int(e.size_in_bits.value) if e.size_in_bits else 32
            sizes = {"IEEE754": (16, 32, 64, 80, 128), "MILSTD_1750A": (32, 48),
                     "DEC": (32, 64), "IBM": (32, 64, 128),
                     "TI": (16, 32, 40)}[kind]
            if bits not in sizes:
                raise Unsupported(f"{where}: {kind} has no {bits}-bit format "
                                  f"(it has {', '.join(map(str, sizes))})")
            lsb, order = self.byte_order(e, bits, where)
            return (FloatEnc(bits, kind, order, lsb),
                    self.calibrator(e, path, where))
        if name == "StringDataEncoding":
            cs = e.encoding.value if e.encoding else "UTF-8"
            # A string is bytes, so byte order matters only to the code units
            # of UTF-16 and UTF-32 without a byte order of their own.
            if ((e.byte_order or "").strip() == "leastSignificantByteFirst"
                    and cs in ("UTF-16", "UTF-32")):
                cs += "LE"
            unit = code_unit(cs)
            c = e.choice
            if c.name == "SizeInBits":
                bits = self._size(c.value.fixed.fixed_value, "string", where)
                if bits % (8 * unit):
                    raise Unsupported(f"{where}: string size {bits} is not "
                                      f"whole {cs} code units")
                if c.value.leading_size is not None:
                    tag = c.value.leading_size.size_in_bits_of_size_tag or 16
                    return (StringEnc(bits, bits, None, self._tag(tag, where),
                                      charset=cs), None)
                return (StringEnc(bits, bits,
                                  self._term(c.value.termination_char, cs,
                                             where), charset=cs), None)
            v = c.value
            dyn = None
            if v.choice1 is not None:
                if v.choice1.name == "DynamicValue":
                    dyn = self.dyn_size(v.choice1.value, path, "string size",
                                        where)
                elif v.choice1.name == "DiscreteLookupList":
                    dyn = DynSize(table=(path, v.choice1.value))
                else:
                    raise Unsupported(f"{where}: string size from "
                                      f"{v.choice1.name}")
                dyn.unit = 8
            mx = self._size(v.max_size_in_bits, "string", where)
            if v.choice2.name == "LeadingSize":
                tag = v.choice2.value.size_in_bits_of_size_tag or 16
                return (StringEnc(None, mx, None, self._tag(tag, where), dyn,
                                  cs), None)
            return (StringEnc(None, mx, self._term(v.choice2.value, cs, where),
                              dyn=dyn, charset=cs), None)
        if name == "BinaryDataEncoding":
            # Binary data is bytes, which have no byte order.
            enc = self.binary_encoding(e, path, where)
            enc.transform = self.transform_names(e, where)
            return enc, None
        raise Unsupported(f"{where}: {name}")

    def xform(self, e: BinaryEnc, time: bool, where: str) -> XformEnc:
        """The encoding of binary data with transform algorithms, which
        make bytes of it, or with ``time``, a time in seconds.  A size of
        -1 offers the decoding algorithm the rest of the packet; in the
        Yamcs dialect it says how much it takes, and entries may follow."""
        if e.dyn is not None:
            raise Unsupported(f"{where}: transformed binary data whose size "
                              "another value gives")
        if e.lsb:
            raise Unsupported(f"{where}: transformed binary data with bitOrder "
                              "leastSignificantBitFirst")
        fa, ta = e.transform
        k = "time" if time else "binary"
        return XformEnc(None if e.rest else e.bits // 8,
                        self.hook(f"{k}_from", fa) if fa else None,
                        self.hook(f"{k}_to", ta) if ta else None,
                        self.max_field_bits // 8, time,
                        e.rest and self.dialect == "yamcs")

    def wrap_transform(self, sc, items: list, start: int, where: str, path: Key,
                       name: str, packet: bool) -> list[Item]:
        """A container's items (from mark ``start``, which must be first)
        in a Transform, if its BinaryEncoding has transform algorithms;
        its SizeInBits is then its size on the wire, which only the packet
        itself may leave out, to take the rest of it."""
        be = sc.binary_encoding
        names = self.transform_names(be, where) if be is not None else None
        if names is None:
            return items
        if edc_items(sc):
            raise Unsupported(f"{where}: ErrorDetectCorrect in a container "
                              "with transform algorithms")
        for it in walk(items):
            if isinstance(it, Value) and it.local.computed:
                raise Unsupported(f"{where}: packet length or checksum field "
                                  f"{it.local.name} in a container with "
                                  "transform algorithms")
            if isinstance(it, TailSeek):
                raise Unsupported(f"{where}: a transformed container with an "
                                  "entry located from its end")
        raw = None
        if be.size_in_bits is not None:
            size = self.int_value(be.size_in_bits, path, "container SizeInBits",
                                  where)
            if not isinstance(size, int) or size % 8:
                raise Unsupported(f"{where}: a transformed container whose "
                                  "size isn't a number of whole bytes")
            raw = size // 8
        elif not packet:
            raise Unsupported(f"{where}: an included container with transform "
                              "algorithms and no fixed SizeInBits")
        cap = (max_bits(items) + 7) // 8
        out_cap = raw if raw is not None else self.max_field_bits // 8
        fa, ta = names
        return [Transform(items, self.hook("binary_from", fa) if fa else None,
                          self.hook("binary_to", ta) if ta else None, raw, cap,
                          out_cap, name, start)]

    def has_transform(self, sc, where: str) -> bool:
        be = sc.binary_encoding
        return be is not None and self.transform_names(be, where) is not None

    @staticmethod
    def transform_names(e, where: str) -> Optional[tuple]:
        """The names of the algorithms that transform the bits of ``e``, a
        BinaryDataEncoding or a container's BinaryEncoding, or None."""
        fa = e.from_binary_transform_algorithm
        ta = e.to_binary_transform_algorithm
        if fa is None and ta is None:
            return None
        # One may be missing: that way fails with NotInvertible.
        return (fa.name if fa is not None else None,
                ta.name if ta is not None else None)

    def binary_encoding(self, e, path: Key, where: str) -> BinaryEnc:
        """A BinaryDataEncoding's BinaryEnc."""
        c = e.size_in_bits.choice
        if c.name in ("DynamicValue", "DiscreteLookupList"):
            dyn = self.int_value(e.size_in_bits, path, "binary size", where)
            dyn.unit = 8
            return BinaryEnc(self.max_field_bits, dyn)
        if c.name != "FixedValue":
            raise Unsupported(f"{where}: binary size from {c.name}")
        if c.value == -1:
            # A Yamcs convention: the size of the value given.  Only the end
            # of the packet can bound it when decoding.
            return BinaryEnc(self.max_field_bits, rest=True)
        bits = self._size(c.value, "binary", where)
        if bits % 8:
            raise Unsupported(f"{where}: binary size {bits} is not whole bytes")
        return BinaryEnc(bits)

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
    def _term(hexchar: Optional[str], cs: str, where: str) -> bytes:
        """The bytes that end a string in charset ``cs``: a whole number of
        its code units, and a zero byte stands for a zero code unit."""
        h = (hexchar or "").strip() or "00"
        b = hex_bytes(h, where)
        unit = code_unit(cs)
        if len(b) < unit and not any(b):
            b = bytes(unit)
        if not b or len(b) % unit or len(b) > 16:
            raise Unsupported(f"{where}: termination character {h} is not 1 to "
                              f"{16 // unit} {cs} code units")
        return b

    def calibrator(self, e, path: Key, where: str):
        """The calibrator of an integer or float encoding: its default
        calibrator, and its context calibrators if it has any."""
        default = (self.one_calibrator(e.default_calibrator, path, where)
                   if e.default_calibrator is not None else None)
        ccl = getattr(e, "context_calibrator_list", None)
        if ccl is None or not ccl.context_calibrator:
            return default
        return ContextCal([(path, cc.context_match,
                            self.one_calibrator(cc.calibrator, path, where))
                           for cc in ccl.context_calibrator], default)

    def one_calibrator(self, c, path: Key, where: str):
        if c.choice.name == "PolynomialCalibrator":
            return Polynomial([(t.coefficient, t.exponent)
                               for t in c.choice.value.term])
        if c.choice.name == "SplineCalibrator":
            s = c.choice.value
            order = s.order if s.order is not None else 1
            pts = sorted(((p.raw, p.calibrated,
                           p.order if p.order is not None else order)
                          for p in s.spline_point), key=lambda t: t[0])
            if len(pts) < 2:
                raise Unsupported(f"{where}: spline with fewer than two points")
            if any(a[0] == b[0] for a, b in zip(pts, pts[1:])):
                raise Unsupported(
                    f"{where}: spline with two points at raw value "
                    + next(f"{a[0]:g}" for a, b in zip(pts, pts[1:])
                           if a[0] == b[0]))
            orders = tuple(o for _, _, o in pts)
            uniform = len(set(orders[:-1])) == 1 and orders[0] in (0, 1)
            return Spline([(r, c) for r, c, _ in pts], orders[0],
                          bool(s.extrapolate), () if uniform else orders)
        return self.math_operation(c.choice.value, path, where)

    def math_operation(self, mc, path: Key, where: str) -> MathOp:
        """A MathOperationCalibrator's postfix expression as a tree."""
        stack: list = []

        def pop(n: int, op: str) -> list:
            if len(stack) < n:
                raise Unsupported(f"{where}: MathOperationCalibrator operator "
                                  f"{op!r} has too few operands")
            args = stack[len(stack) - n:]
            del stack[len(stack) - n:]
            return args

        for item in mc.items:
            name, v = item.name, item.value
            if name == "ValueOperand":
                try:
                    stack.append(("const", float(v.strip())))
                except ValueError:
                    raise Unsupported(f"{where}: ValueOperand {v!r} is not a "
                                      "number") from None
            elif name == "ThisParameterOperand":
                stack.append(("raw",))
            elif name == "ParameterInstanceRefOperand":
                stack.append(("value", ValueRef(v, path)))
            else:
                op = v.value
                if op == "swap":
                    a, b = pop(2, op)
                    stack += [b, a]
                elif op == "drop":
                    pop(1, op)
                elif op == "dup":
                    (a,) = pop(1, op)
                    stack += [a, a]
                elif op == "over":
                    a, b = pop(2, op)
                    stack += [a, b, a]
                elif op in MATH_UNARY:
                    stack.append((op, *pop(1, op)))
                else:
                    stack.append((op, *pop(2, op)))
        if len(stack) != 1:
            raise Unsupported(f"{where}: MathOperationCalibrator leaves "
                              f"{len(stack)} values on its stack, not one")
        return MathOp(stack[0])

    # -- literals --

    def literal(self, vt: VType, text: str, raw: bool, where: str):
        s = text.strip()
        if vt.kind in ("array", "aggregate"):
            raise Unsupported(f"{where}: value {text!r} for {vt.kind} type "
                              f"{vt.key}")
        try:
            if raw or vt.kind == "int":
                v = (int(s, 0) if s.lower().startswith(("0x", "0b", "0o"))
                     else int(s))
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
            raise Unsupported(f"{where}: value {text!r} does not fit {vt.kind} "
                              f"type {vt.key}") from None

    # -- containers --

    def local(self, name: str, key: str, vt: VType) -> Local:
        return Local(self._id(), name, key, vt)

    def location(self, loc, start: int, path: Key, scope: list, where: str,
                 arg_key=None) -> list[Item]:
        """The Seek for an entry's location; one from other values is bound
        to ``scope`` and must not move backwards."""
        if loc is None:
            return []
        ref = (loc.reference_location.value if loc.reference_location
               else "previousEntry")
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
            off.max = dyn_units(off, rng, self.max_field_bits)
        if ref == "containerStart":
            return [Seek(start, off)]
        if ref == "previousEntry":
            return [Seek(None, off)] if off else []
        if ref == "containerEnd":
            if not isinstance(off, int):
                raise Unsupported(f"{where}: a location from the container's "
                                  "end that other values give")
            return [EndSeek(start, off)]
        if not isinstance(off, int):
            raise Unsupported(f"{where}: a location from the next entry that "
                              "other values give")
        return [NextSeek(off)]

    @staticmethod
    def no_next(items: list, where: str) -> None:
        """Rejects a nextEntry location with no entry after it in its entry
        list, or inside an IncludeCondition."""
        if any(isinstance(it, NextSeek) for it in walk(items)):
            raise Unsupported(f"{where}: an entry located from the next entry, "
                              "without one after it in its entry list and "
                              "condition")

    @staticmethod
    def next_seek(items: list, loc: list, where: str) -> list:
        """Places an entry with a nextEntry location, which is in ``items``
        (its NextSeek, then its Value), now that the entry after it has the
        location items ``loc``: the earlier entry ends where the later one
        starts, less the offset.  Returns ``loc``, adjusted."""
        at = next((i for i in range(len(items) - 1, -1, -1)
                   if isinstance(items[i], NextSeek)), None)
        if at is None:
            return loc
        k = items[at].offset
        if (len(loc) == 1 and isinstance(loc[0], Seek)
                and loc[0].from_start is not None
                and isinstance(loc[0].offset, int)):
            # The next entry is placed from its container's start, so this
            # one goes back from there.
            body = [it for it in items[at + 1:]]
            bits = sum(item_bits(it.local.vtype) for it in body
                       if isinstance(it, Value)
                       and fixed_bits(it.local.vtype) is not None)
            if (any(isinstance(it, Value) and fixed_bits(it.local.vtype) is None
                    for it in body)):
                raise Unsupported(f"{where}: an entry located from the next "
                                  "entry whose size varies")
            items[at] = Seek(loc[0].from_start, loc[0].offset - k - bits)
            return loc
        if any(isinstance(it, (EndSeek, NextSeek)) for it in loc):
            raise Unsupported(f"{where}: an entry located from the next entry, "
                              "which is located from another")
        # The next entry follows this one, k bits after its end.
        given = sum(it.offset for it in loc if isinstance(it, Seek)
                    and isinstance(it.offset, int))
        if loc and (given != k or len(loc) > 1):
            raise Unsupported(f"{where}: an entry located {k} bits before the "
                              "next one, which says otherwise")
        del items[at]
        return [Seek(None, k)] if k else []

    def repeat(self, local: Local, e, path: Key, where: str) -> Local:
        """Turns the value of an entry with a RepeatEntry into an array."""
        rp = getattr(e, "repeat_entry", None)
        if rp is None:
            return local
        if local.computed:
            raise Unsupported(f"{where}: RepeatEntry on packet length or "
                              f"checksum field {local.name}")
        vt = local.vtype
        if is_rest(vt):
            raise Unsupported(f"{where}: RepeatEntry on a value that takes the "
                              "rest of the packet")
        count = self.int_value(rp.count, path, "RepeatEntry Count", where)
        gap = 0
        if rp.offset is not None:
            gap = self.int_value(rp.offset, path, "RepeatEntry Offset", where)
            if not isinstance(gap, DynSize) and gap < 0:
                raise Unsupported(f"{where}: RepeatEntry Offset {gap} is "
                                  "negative")
        if isinstance(count, int):
            if count < 1:
                raise Unsupported(f"{where}: RepeatEntry Count {count} is not "
                                  "positive")
            if count == 1:
                return local
        shape = (count,) if isinstance(count, int) else ()
        size = count if isinstance(count, int) else None
        local.vtype = VType(key=vt.key, name=vt.name, kind="array",
                            enc=ArrayEnc(vt, count, gap, shape=shape,
                                         sizes=(size,)), doc=vt.doc)
        return local

    def bind(self, local: Local, scope: list, where: str,
             arg_key: Optional[Callable] = None) -> Local:
        """Binds the size or count of a value to what gives it, which must
        be decoded before it, and bounds it."""
        vt = self.bind_sizes(local.vtype, scope, where, arg_key)
        local.vtype = self.bind_calibrators(vt, scope, where, arg_key)
        return local

    def bind_sizes(self, vt: VType, scope: list, where: str, arg_key) -> VType:
        """``vt`` with its sizes, counts and gaps that other values give,
        its array elements' too, bound to the values in ``scope``."""
        e = vt.enc
        if isinstance(e, ArrayEnc):
            elem = self.bind_sizes(e.elem, scope, where, arg_key)
            gap = e.gap
            if isinstance(gap, DynSize) and unbound(gap):
                gap, rng = self.bind_dyn(gap, scope, where, arg_key)
                gap.max = dyn_units(gap, rng, self.max_field_bits)
            if elem is not e.elem or gap is not e.gap:
                vt = dataclasses.replace(
                    vt, enc=dataclasses.replace(e, elem=elem, gap=gap))
        if vt.dyn is not None and unbound(vt.dyn):
            b, rng = self.bind_dyn(vt.dyn, scope, where, arg_key)
            vt = bounded(vt, b, rng, self.max_field_bits)
        return vt

    def bind_calibrators(self, vt: VType, scope: list, where: str,
                         arg_key) -> VType:
        """``vt``, with copies of the calibrators in it that use other
        values of the packet bound to those in ``scope``."""
        e = vt.enc
        if isinstance(e, ArrayEnc):
            elem = self.bind_calibrators(e.elem, scope, where, arg_key)
            if elem is e.elem:
                return vt
            return dataclasses.replace(vt,
                                       enc=dataclasses.replace(e, elem=elem))
        if isinstance(e, AggregateEnc):
            # The same type, with its members' calibrators bound here.
            members = [
                dataclasses.replace(m,
                                    vtype=self.bind_calibrators(m.vtype, scope,
                                                                where, arg_key))
                for m in e.members]
            if all(a.vtype is b.vtype for a, b in zip(members, e.members)):
                return vt
            return dataclasses.replace(
                vt, enc=dataclasses.replace(e, members=members),
                origin=vt.origin or vt)
        if not needs_binding(vt.calibrator):
            return vt
        return dataclasses.replace(
            vt, calibrator=self.bind_cal(vt.calibrator, scope, where, arg_key))

    def bind_cal(self, cal, scope: list, where: str, arg_key):
        """A copy of ``cal`` bound to the values in ``scope``."""
        if isinstance(cal, ContextCal):
            cases = []
            for path, mc, c in cal.cases:
                terms = self.match(mc, path, scope, where, arg_key)
                for lc in (x for t in terms for x in term_locals(t)):
                    if lc.computed:
                        raise Unsupported(f"{where}: a context calibrator on "
                                          "packet length or checksum field "
                                          f"{lc.name}")
                cases.append((terms, self.bind_cal(c, scope, where, arg_key)))
            default = (self.bind_cal(cal.default, scope, where, arg_key)
                       if cal.default is not None else None)
            return ContextCal(cases, default, bound=True)
        if isinstance(cal, MathOp):
            return MathOp(self.bind_expr(cal.expr, scope, where, arg_key))
        return cal

    def bind_expr(self, e: tuple, scope: list, where: str, arg_key) -> tuple:
        if e[0] in ("const", "raw"):
            return e
        if e[0] != "value":
            return (e[0],
                    *(self.bind_expr(a, scope, where, arg_key) for a in e[1:]))
        r = e[1]
        local, member, vt, name = self.value_ref("ParameterInstanceRef", r.ref,
                                                 r.path, scope, where, arg_key)
        if local.optional:
            raise Unsupported(f"{where}: MathOperationCalibrator uses {name}, "
                              "which has an IncludeCondition")
        if vt.kind not in ("int", "float", "enum", "bool"):
            raise Unsupported(f"{where}: MathOperationCalibrator uses "
                              f"{vt.kind} value {name}")
        if vt.wide:
            raise Unsupported(f"{where}: MathOperationCalibrator uses "
                              f"{vt.enc.bits}-bit integer {name}")
        if local.computed:
            raise Unsupported(f"{where}: MathOperationCalibrator uses packet "
                              f"length or checksum field {name}")
        if r.ref.use_calibrated_value is False and vt.calibrator is not None:
            raise Unsupported(f"{where}: MathOperationCalibrator uses the raw "
                              f"value of calibrated {name}")
        return ("value", ValueRef(r.ref, r.path, local, member, vt))

    def bind_dyn(self, d: DynSize, scope: list, where: str,
                 arg_key: Optional[Callable] = None):
        """A copy of ``d`` bound to the values in ``scope``, and the range
        (lo, hi) of the value it adjusts."""
        if d.table is not None:
            path, table = d.table
            lookup = [(self.match(lk, path, scope, where, arg_key), lk.value)
                      for lk in table.discrete_lookup]
            for crit, _ in lookup:
                for lc in (x for c in crit for x in term_locals(c)):
                    if lc.computed:
                        raise Unsupported(f"{where}: size or count from packet "
                                          f"length or checksum field {lc.name}")
            values = [v for _, v in lookup] + [table.default_value]
            return (dataclasses.replace(d, lookup=lookup,
                                        default=table.default_value),
                    (min(values), max(values)))
        if d.arg is not None:
            if arg_key is None:
                raise Unsupported(f"{where}: size from argument {d.arg!r} "
                                  "outside a command")
            key, name = arg_key(d.arg), d.arg
        elif d.key is None:
            raise Unsupported(f"{where}: unresolved parameter reference "
                              f"{d.name!r}")
        else:
            key = name = d.key
        ref = instance_of(scope, key, d.instance, where)
        if ref is None:
            raise Unsupported(f"{where}: size or count from {name}, which is "
                              "not decoded before it")
        if ref.optional:
            raise Unsupported(f"{where}: size or count from {name}, which has "
                              "an IncludeCondition")
        if ref.computed:
            raise Unsupported(f"{where}: size or count from packet length or "
                              f"checksum field {name}")
        member, vt = walk_members(ref.vtype, d.members,
                                  f"{where}: size or count from {d.name}")
        if vt.kind != "int":
            raise Unsupported(f"{where}: size or count from {vt.kind} value "
                              f"{d.name or name}")
        if vt.wide:
            raise Unsupported(f"{where}: size or count from {vt.enc.bits}-bit "
                              f"integer {d.name or name}")
        return dataclasses.replace(d, local=ref, path=member), int_bounds(vt)

    def param_local(self, ref: str, path: Key) -> Local:
        pkey = self.resolve(ref, path, self.params, "parameter")
        ppath, p = self.params[pkey]
        tkey = self.resolve(p.parameter_type_ref, ppath, self.ptypes,
                            "parameter type")
        local = self.local(p.name, qname(pkey), self.vtype(tkey, self.ptypes))
        local.length = length_adjust(p, local.vtype, f"parameter {qname(pkey)}")
        return local

    def tm_items(self, sc, path: Key, stack: list, start: int, scope: list,
                 conditional: bool = False) -> list[Item]:
        """Items for a container's entries.  ``scope`` lists the values read
        so far (base containers first) and grows as entries are added;
        ``conditional`` says whether the container is inside an
        IncludeCondition."""
        items: list[Item] = []
        segs: dict = {}
        where = f"container {qname(stack[-1])}"
        for ch in sc.entry_list.items:
            e = ch.value
            if (ch.name not in ("ParameterRefEntry", "ContainerRefEntry",
                                "ArrayParameterRefEntry",
                                "IndirectParameterRefEntry") + SEGMENTS):
                raise Unsupported(f"{where}: {ch.name}")
            if (ch.name == "ContainerRefEntry"
                    and e.location_in_container_in_bits is not None
                    and e.location_in_container_in_bits.reference_location
                    and e.location_in_container_in_bits.reference_location.value
                    == "containerEnd"):
                raise Unsupported(f"{where}: an included container located "
                                  "from the container's end")
            cond = None
            if e.include_condition is not None:
                cond = self.match(e.include_condition, path, scope, where)
            out = items if cond is None else []
            out += self.next_seek(items,
                                  self.location(e.location_in_container_in_bits,
                                                start, path, scope, where),
                                  where)
            if ch.name in SEGMENTS:
                after = []
                if out and isinstance(out[-1], EndSeek):
                    if e.repeat_entry is not None:
                        raise Unsupported(f"{where}: a repeated {ch.name} "
                                          "located from the container's end")
                    out[-1], after = self.end_seek(out[-1], sc, None, where,
                                                   e.size_in_bits)
                out += self.segment(ch.name, e, path, where, segs,
                                    cond is not None, scope)
                out += after
            elif ch.name == "IndirectParameterRefEntry":
                out += self.indirect(e, path, scope, where)
            elif ch.name != "ContainerRefEntry":
                local = self.subset(self.param_local(e.parameter_ref, path), e,
                                    path, where)
                local = self.bind(self.repeat(local, e, path, where), scope,
                                  where)
                after = []
                if out and isinstance(out[-1], EndSeek):
                    out[-1], after = self.end_seek(out[-1], sc, local, where)
                out.append(Value(local))
                out += after
                scope.append(local)
            else:
                ckey = self.resolve(e.container_ref, path, self.containers,
                                    "container")
                if ckey in stack:
                    raise Unsupported(f"{where}: container includes itself")
                cpath, inc = self.containers[ckey]
                if (inc.base_container is not None
                        and e.repeat_entry is not None):
                    raise Unsupported(f"{where}: repeated container "
                                      f"{qname(ckey)} has a BaseContainer")
                if e.repeat_entry is not None:
                    local = self.local(inc.name, qname(ckey),
                                       self.container_type(ckey, stack, where))
                    local = self.bind(self.repeat(local, e, path, where), scope,
                                      where)
                    out.append(Value(local))
                    scope.append(local)
                else:
                    m = self._id()
                    inner = [Mark(m)]
                    # A derived container is its bases' entries, then its own.
                    for k, (kp, kc, kkey) in enumerate(
                            self.include_chain(ckey, stack, where)):
                        inner += self.tm_items(kc, kp, stack + [kkey], m, scope,
                                               conditional or cond is not None)
                        rc = (kc.base_container.restriction_criteria
                              if kc.base_container is not None else None)
                        if rc is not None:
                            terms = self.match(rc, kp, scope, where)
                            inner.append(Require(terms, qname(kkey),
                                                 " and ".join(t.text
                                                              for t in terms)))
                    inner += self.size_pad(inc, inner, m, where, scope, cpath)
                    out += self.measure(
                        self.wrap_transform(inc, inner, m, where, cpath,
                                            qname(ckey), False), where)
                    if edc_items(inc):
                        end = self._id()
                        out.append(Mark(end))
                        if conditional or cond is not None:
                            # Checked where the container ends, when it is
                            # there.
                            out += [
                                Verify(self.check(name, v, cpath, list(scope),
                                                  m, end, qname(ckey), where))
                                for name, v in edc_items(inc)]
                        else:
                            self.included_checks.append(
                                (inc, cpath, m, end, list(scope), qname(ckey)))
            if cond is not None:
                if any(isinstance(it, Transform) for it in walk(out)):
                    raise Unsupported(f"{where}: transformed data with an "
                                      "IncludeCondition")
                for it in walk(out):
                    if isinstance(it, Value):
                        it.local.optional = True
                items.append(Cond(cond, out))
        self.no_next(items, where)
        self.raw_segments(items, segs, scope)
        return items

    def segment(self, name: str, e, path: Key, where: str, segs: dict,
                cond: bool, scope: list, arg_key=None) -> list[Item]:
        """Items for a segment entry.  The segments of a parameter in one
        entry list that make up its whole encoding (at most 64 bits) are
        assembled into the value, which is in ``scope`` after its last
        segment; ``segs`` keeps those still incomplete, which
        raw_segments makes fields of their own.  Container and stream
        segments, and segments with an IncludeCondition, are always their
        own fields: the rest of the value is in other packets.

        A segment repeated a fixed number of times, one right after
        another, is that many segments.  Repeated otherwise (a count that
        another value gives, or an Offset between the repeats), its
        segments are a list of raw fields."""
        rp = e.repeat_entry
        if rp is None:
            return self.one_segment(name, e, path, where, segs, cond, scope)
        if e.order is not None:
            raise Unsupported(f"{where}: a repeated {name} with an order")
        count = self.int_value(rp.count, path, "RepeatEntry Count", where)
        gap = (self.int_value(rp.offset, path, "RepeatEntry Offset", where)
               if rp.offset is not None else 0)
        if isinstance(count, int) and gap == 0:
            if count < 1:
                raise Unsupported(f"{where}: RepeatEntry Count {count} is not "
                                  "positive")
            out: list[Item] = []
            for _ in range(count):
                out += self.one_segment(name, e, path, where, segs, cond, scope)
            return out
        ref = self.segment_ref(name, e, path)
        counts = segs.setdefault("#counts", {})
        k = counts.get(ref, 0)
        counts[ref] = k + 1
        lc = self.raw_segment(ref, k, e.size_in_bits, where)
        lc = self.bind(self.repeat(lc, e, path, where), scope, where, arg_key)
        scope.append(lc)
        return [Value(lc)]

    def segment_ref(self, name: str, e, path: Key) -> str:
        """What a segment entry is a segment of."""
        if name == "ParameterSegmentRefEntry":
            return qname(self.resolve(e.parameter_ref, path, self.params,
                                      "parameter"))
        if name == "ContainerSegmentRefEntry":
            return qname(self.resolve(e.container_ref, path, self.containers,
                                      "container"))
        return e.stream_ref

    def one_segment(self, name: str, e, path: Key, where: str, segs: dict,
                    cond: bool, scope: list) -> list[Item]:
        bits = e.size_in_bits
        ref = self.segment_ref(name, e, path)
        counts = segs.setdefault("#counts", {})
        k = counts.get(ref, 0)
        counts[ref] = k + 1
        if e.order is not None:
            k = e.order
        if name == "ParameterSegmentRefEntry" and not cond:
            group = segs.get(ref)
            if group is None:
                local = self.param_local(e.parameter_ref, path)
                local.length = None
                total = fixed_bits(local.vtype)
                group = segs[ref] = (local, total, [])
            local, total, parts = group
            seg = Segment(local, bits, total or 0)
            parts.append((seg, e.order, k))
            got = sum(s.bits for s, _, _ in parts)
            if total is not None and total <= 64 and got >= total:
                if got > total:
                    raise Unsupported(f"{where}: the segments of {ref} have "
                                      f"{got} bits, and its encoding {total}")
                self.assemble(parts, where, ref)
                del segs[ref]
                scope.append(local)
                return [seg, Value(local, assembled=total)]
            return [seg]
        lc = self.raw_segment(ref, k, bits, where)
        scope.append(lc)
        return [Value(lc)]

    def indirect(self, e, path: Key, scope: list, where: str,
                 arg_key=None) -> list[Item]:
        """An IndirectParameterRefEntry: the parameter that the value of
        its ParameterInstance names.  Each parameter it can name becomes a
        field, present when the value names it.  The value is an
        enumeration whose labels name parameters, or with an
        aliasNameSpace, an enumeration, integer or string that gives an
        alias in that name space, as Yamcs reads it.  Values that name no
        parameter select nothing."""
        if e.repeat_entry is not None:
            raise Unsupported(f"{where}: a repeated IndirectParameterRefEntry")
        sel = e.parameter_instance
        _, _, vt, pkey = self.value_ref("ParameterInstanceRef", sel, path,
                                        scope, where, arg_key)
        aliases = (self.aliases(e.alias_name_space)
                   if e.alias_name_space is not None else None)
        if vt.kind == "enum":
            choices = []
            for lab in vt.labels:
                if aliases is not None:
                    key = aliases.get(lab.label)
                else:
                    try:
                        key = self.resolve(lab.label, path, self.params,
                                           "parameter")
                    except Unsupported:
                        key = None
                if key is not None:
                    choices.append((lab.label, key))
        elif aliases is not None and vt.kind in ("int", "string"):
            choices = [(a, key) for a, key in aliases.items()
                       if vt.kind == "string" or re.fullmatch(r"-?\d+", a)]
        else:
            why = (" without an aliasNameSpace" if vt.kind in ("int", "string")
                   else "")
            raise Unsupported(f"{where}: an IndirectParameterRefEntry chosen "
                              f"by {vt.kind} value {pkey}{why}")
        out: list[Item] = []
        for text, key in choices:
            crit = self.comparison("ParameterInstanceRef", sel, "==", text,
                                   path, scope, where, arg_key)
            lc = self.bind(self.param_local("/" + "/".join(key), ()), scope,
                           where, arg_key)
            lc.optional = True
            if lc.computed:
                raise Unsupported(f"{where}: an IndirectParameterRefEntry "
                                  f"selects length field {lc.name}")
            out.append(Cond([crit], [Value(lc)]))
            scope.append(lc)
        if not out:
            raise Unsupported(f"{where}: no label of {pkey} names a parameter")
        return out

    def aliases(self, space: str) -> dict:
        """Alias -> parameter, for the aliases in name space ``space``."""
        out = {}
        for key, (_, p) in self.params.items():
            for a in p.alias_set.alias if p.alias_set else []:
                if a.name_space == space:
                    out.setdefault(a.alias, key)
        return out

    @staticmethod
    def assemble(parts: list, where: str, ref: str) -> None:
        """Places each Segment of a value in its encoding: by their order
        attributes, or in document order without them."""
        orders = [o for _, o, _ in parts]
        if all(o is None for o in orders):
            ranked = [s for s, _, _ in parts]
        elif None in orders or len(set(orders)) != len(orders):
            raise Unsupported(f"{where}: segments of {ref} whose order is "
                              "missing or repeated")
        else:
            ranked = [s for s, _, _ in sorted(parts, key=lambda x: x[1])]
        left = parts[0][0].total
        for s in ranked:
            left -= s.bits
            s.shift = left
        parts[0][0].first = True

    def raw_segment(self, ref: str, k: int, bits: int, where: str) -> Local:
        """A field that holds a segment's bits as they are."""
        if bits <= 64:
            vt = VType(key=f"{ref} segment", name=f"segment of {ref}",
                       kind="int", enc=IntEnc(bits))
        elif bits % 8 == 0 and bits <= self.max_field_bits:
            vt = VType(key=f"{ref} segment", name=f"segment of {ref}",
                       kind="binary", enc=BinaryEnc(bits))
        else:
            raise Unsupported(f"{where}: a {bits}-bit segment of {ref}")
        vt.doc = f"segment {k} of {ref}, its bits as they are"
        name = ref.rsplit("/", 1)[-1]
        return self.local(f"{name}Segment{k}", f"{ref}#segment{k}", vt)

    def raw_segments(self, items: list, segs: dict, scope: list) -> None:
        """Makes fields of the segments of values that an entry list holds
        only part of."""
        for ref, group in segs.items():
            if ref == "#counts":
                continue
            for seg, _, k in group[2]:
                lc = self.raw_segment(ref, k, seg.bits, "")
                i = next(i for i, it in enumerate(items) if it is seg)
                items[i] = Value(lc)
                scope.append(lc)

    def include_chain(self, ckey: Key, stack: list, where: str) -> list:
        """(path, container, key) of an included container and its bases,
        the root first."""
        chain = []
        key = ckey
        while True:
            if key in stack or any(k == key for _, _, k in chain):
                raise Unsupported(f"{where}: container includes itself")
            path, c = self.containers[key]
            chain.append((path, c, key))
            if c.base_container is None:
                return chain[::-1]
            key = self.resolve(c.base_container.container_ref, path,
                               self.containers, "container")

    def end_seek(self, es: EndSeek, sc, local: Optional[Local], where: str,
                 bits: int = 0) -> tuple:
        """The Seek that ends ``local`` (or a segment of ``bits``)
        ``es.offset`` bits before the end of container ``sc``, and the items
        to follow it.  In a container with a fixed size, that is a place
        from its start.  Otherwise the container must be the packet itself,
        whose end decoding knows (a TailSeek, and a TailPad after): an
        included container's end would depend on where its entries are."""
        if local is not None:
            vt = local.vtype
            if vt.dyn is not None or is_rest(vt) or fixed_bits(vt) is None:
                raise Unsupported(f"{where}: a location from the container's "
                                  f"end for {local.name}, whose size varies")
            bits = item_bits(vt)
        be = sc.binary_encoding
        size = (
            self.int_value(be.size_in_bits, (), "container SizeInBits", where)
            if be is not None and be.size_in_bits is not None else None)
        if isinstance(size, int):
            return Seek(es.start, size - es.offset - bits), []
        if es.start != 0 or size is not None:
            raise Unsupported(f"{where}: a location from the end of an "
                              "included container, or one whose size another "
                              "value gives")
        return TailSeek(es.offset + bits), [TailPad(es.offset)]

    @staticmethod
    def check_tail(node: Node, where: str) -> None:
        """An entry located from the end of a packet whose size varies is
        its last, and is always there."""
        items = node.all_items()
        for it in items:
            if (isinstance(it, Cond)
                    and any(isinstance(x, TailSeek) for x in walk(it.items))):
                raise Unsupported(f"{where}: an entry located from the "
                                  "packet's end has an IncludeCondition")
        at = [i for i, it in enumerate(items) if isinstance(it, TailSeek)]
        if not at:
            return
        rest = items[at[0] + 3:]
        if (len(at) > 1
                or any(not isinstance(it, (Require, Mark)) for it in rest)):
            raise Unsupported(f"{where}: an entry located from the end of a "
                              "packet whose size varies is not its last")

    def size_pad(self, sc, items: list, start: int, where: str,
                 scope: Optional[list] = None, path: Key = ()) -> list[Item]:
        """The padding that makes a container with a BinaryEncoding occupy
        its SizeInBits; ``items`` are its entries, from mark ``start``, and
        ``scope`` the values decoded before its end, for a size another
        value gives."""
        be = sc.binary_encoding
        if be is None:
            return []
        if (be.from_binary_transform_algorithm
                or be.to_binary_transform_algorithm):
            # Its SizeInBits is its transformed size: see wrap_transform.
            return []
        if be.size_in_bits is None:
            return []
        size = self.int_value(be.size_in_bits, path, "container SizeInBits",
                              where)
        if isinstance(size, DynSize):
            if scope is None:
                raise Unsupported(f"{where}: a repeated container whose size "
                                  "another value gives")
            off, rng = self.bind_dyn(size, scope, where)
            off.max = dyn_units(off, rng, self.max_field_bits)
            return [Fill(start, off, where)]
        size = self._size(size, "container", where)
        end = static_end(items, start)
        if end is None:
            # Where the entries end varies: pad up to the size at run time.
            if scope is None:
                raise Unsupported(f"{where}: a container size with entries "
                                  "whose size or position varies")
            return [Fill(start, size, where)]
        pos, extent = end
        if extent > size:
            raise Unsupported(f"{where}: entries take {extent} bits, more than "
                              f"the container size {size}")
        if pos != extent:
            raise Unsupported(f"{where}: a container size after entries that "
                              "overlap")
        return [Pad(size - pos)] if size > pos else []

    def container_type(self, ckey: Key, stack: list, where: str,
                       table: Optional[dict] = None) -> VType:
        """The aggregate holding one copy of a repeated container's entries,
        with skips and padding from their locations and the container size.
        ``table`` holds the container (telemetry's by default)."""
        if ckey in self.ctypes:
            return self.ctypes[ckey]
        cpath, inc = (self.containers if table is None else table)[ckey]
        what = f"{where}: repeated container {qname(ckey)}"
        m = self._id()
        # Its entries are values of each copy, so sizes can't come from
        # values outside it; ones from values inside are rejected below.
        items = self.tm_items(inc, cpath, stack + [ckey], m, [])
        items += self.size_pad(inc, items, m, where)
        vt = self.aggregate_of(items, m, what, qname(ckey), inc.name)
        self.ctypes[ckey] = vt
        return vt

    def mc_container_type(self, key: Key, where: str, stack: tuple) -> VType:
        """container_type for the CommandContainer of meta-command ``key``,
        with its arguments as members."""
        ckey = ("meta-command",) + key
        if ckey in self.ctypes:
            return self.ctypes[ckey]
        cc = self.mcs[key][1].command_container
        what = f"{where}: repeated CommandContainer of {qname(key)}"
        items = self.mc_include(key, where, [], stack)
        m = items[0].id  # mc_include starts with its Mark
        vt = self.aggregate_of(items, m, what, qname(key),
                               cc.name if cc is not None else key[-1])
        self.ctypes[ckey] = vt
        return vt

    @staticmethod
    def aggregate_of(items: list, m: int, what: str, key: str,
                     name: str) -> VType:
        """The aggregate of a repeated container's items, which start at
        mark ``m``."""
        this = Local(0, name, key, None)
        idents: dict = {}  # id(Local) -> member
        checks: list = []
        members: list[Member] = []
        names = Namer()
        pos: Optional[int] = 0  # None once a value of varying size is passed
        marks = {m: 0}
        skip = 0
        for it in items:
            if isinstance(it, Cond):
                raise Unsupported(f"{what} has an IncludeCondition")
            if isinstance(it, Require):
                crit = []
                for c in it.criteria:
                    if (not isinstance(c, Criterion) or c.other is not None
                            or id(c.local) not in idents):
                        raise Unsupported(f"{what} has criteria on values "
                                          "outside it")
                    mem = idents[id(c.local)]
                    crit.append(Criterion(this, c.op, c.value, c.text,
                                          (mem.ident,) + tuple(c.path),
                                          c.target or mem.vtype))
                checks.append(Require(crit, it.container, it.text))
                continue
            if isinstance(it, Fill):
                raise Unsupported(f"{what} includes a container whose size "
                                  "varies")
            if isinstance(it, Transform):
                raise Unsupported(f"{what} has transformed data")
            if isinstance(it, Seek):
                if isinstance(it.offset, DynSize):
                    raise Unsupported(f"{what} has a location that other "
                                      "values give")
                if it.from_start is not None:
                    if it.from_start not in marks:
                        raise Unsupported(f"{what} has a location relative to "
                                          "the start of another container")
                    if marks[it.from_start] is None or pos is None:
                        raise Unsupported(f"{what} has a location after a "
                                          "value whose size varies")
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
            elif isinstance(it, Segment):
                raise Unsupported(f"{what} has a value in segments")
            else:
                vt = it.local.vtype
                if vt.dyn is not None:
                    raise Unsupported(f"{what}: {it.local.name} has a size "
                                      "that another value gives")
                if it.local.computed:
                    raise Unsupported(f"{what}: {it.local.name} is a packet "
                                      "length or checksum field")
                members.append(Member(it.local.name,
                                      names.take(snake(it.local.name)), vt,
                                      skip))
                idents[id(it.local)] = members[-1]
                skip = 0
                bits = fixed_bits(vt)
                pos = (pos + bits if pos is not None and bits is not None
                       else None)
        if not members:
            raise Unsupported(f"{what} has no entries")
        vt = VType(key=key, name=name, kind="aggregate",
                   enc=AggregateEnc(members, skip, checks, this))
        this.vtype = vt
        vt.doc = "one copy of a repeated container"
        return vt

    def tm_node(self, key: Key) -> Node:
        if key in self.nodes:
            n = self.nodes[key]
            if getattr(n, "_building", False):
                raise Unsupported("container inheritance cycle through "
                                  f"{qname(key)}")
            return n
        path, sc = self.containers[key]
        node = Node("tm", qname(key), sc.name, bool(sc.abstract))
        node._building = True
        self.nodes[key] = node
        try:
            base = None
            scope: list[Local] = []
            if sc.base_container is not None:
                bkey = self.resolve(sc.base_container.container_ref, path,
                                    self.containers, "container")
                base = self.tm_node(bkey)
                if base.error:
                    raise Unsupported(f"base container {base.key} is not "
                                      "supported")
                node.base = base
                scope = self.scope_of(base)
                rc = sc.base_container.restriction_criteria
                if rc is not None:
                    node.criteria = self.match(rc, path, scope,
                                               f"container {node.key}")
                    if rc.next_container is not None:
                        # It also depends on the container that follows it
                        # in a stream, which a packet codec can't see:
                        # decoding its base never picks it.
                        node.next_container = rc.next_container.container_ref
            node._edc = (sc, path)
            self.included_checks = []
            where = f"container {node.key}"
            if self.has_transform(sc, where):
                # Its own entries are transformed: they start at the start
                # of what the transform gives.
                m = self._id()
                node.items = self.wrap_transform(
                    sc, [Mark(m)] + self.tm_items(sc, path, [key], m, scope), m,
                    where, path, node.key, True)
            else:
                node.items = self.tm_items(sc, path, [key], 0, scope)
                # A size covers the whole packet, base containers' entries
                # too.
                node.items += self.size_pad(sc, node.all_items(), 0, where,
                                            scope, path)
            node._included = self.included_checks
            self.check_rest(node, f"container {node.key}")
            self.check_tail(node, f"container {node.key}")
            check_aligned(node.all_items(), f"container {node.key}")
            self.check_length(node, f"container {node.key}")
            if not node.abstract:
                node.checks = self.node_checks(node, f"container {node.key}")
            if base is not None and node.next_container is None:
                base.children.append(node)
        except Unsupported as e:
            node.error = str(e)
            node.base = None
        node._building = False
        self.order.append(node)
        return node

    def node_checks(self, node: Node, where: str) -> list:
        """The ErrorDetectCorrect checks of a concrete packet: those of the
        containers in its inheritance chain, which cover the packet, and
        those of the containers it includes.  Values are computed in the
        order of their fields in the packet."""
        out = []
        chain = node.chain()
        for k, n in enumerate(chain):
            sc, path = getattr(n, "_edc", (None, None))
            # A check's field is in its container or one derived from it,
            # which all share the check.
            below = [it.local for m in chain[k:] for it in walk(m.items)
                     if isinstance(it, Value)]
            for name, v in edc_items(sc):
                out.append(self.check(name, v, path, below, 0, None, n.key,
                                      where))
        for n in chain:
            for inc, path, m, end, scope, ckey in getattr(n, "_included", []):
                for name, v in edc_items(inc):
                    out.append(self.check(name, v, path, scope, m, end, ckey,
                                          where))
        criteria = (node.all_criteria()
                    + [c for it in node.all_items() for c in conds(it)])
        verified = [it.check for it in walk(node.all_items())
                    if isinstance(it, Verify)]
        for c in out + verified:
            if any(x is c.local for t in criteria for x in term_locals(t)):
                raise Unsupported(f"{where}: a criterion or assignment on "
                                  f"checksum field {c.local.name}")
        order = {id(it.local): i for i, it in enumerate(walk(node.all_items()))
                 if isinstance(it, Value)}
        return sorted(out, key=lambda c: order[id(c.local)])

    def check(self, name: str, v, path: Key, scope: list, start: int, end,
              container: str, where: str) -> Check:
        """One ErrorDetectCorrect algorithm, whose value is in a field among
        ``scope``."""
        pkey = qname(self.resolve(v.parameter_ref, path, self.params,
                                  "parameter"))
        local = next((lc for lc in reversed(scope) if lc.key == pkey), None)
        if local is None:
            raise Unsupported(f"{where}: the {name} value {pkey} is not in "
                              f"container {container}")
        vt = local.vtype
        if (vt.kind != "int" or not isinstance(vt.enc, IntEnc) or vt.enc.signed
                or vt.enc.bcd or vt.calibrator is not None or vt.wide
                or not fast_order(vt.enc)):
            raise Unsupported(f"{where}: checksum field {local.name} must be "
                              "an uncalibrated unsigned binary integer, big "
                              "or little endian in whole bytes, most "
                              "significant bit first")
        if local.optional:
            raise Unsupported(f"{where}: checksum field {local.name} has an "
                              "IncludeCondition")
        if local.computed:
            raise Unsupported(f"{where}: checksum field {local.name} is "
                              "already a packet length or checksum field")
        if name == "CRC":
            width = v.width
            if not 1 <= width <= 64:
                raise Unsupported(f"{where}: a {width}-bit CRC")
            m = (1 << width) - 1

            def hexnum(h, default=0):
                return (int.from_bytes(hex_bytes(h, where), "big") if h
                        and h.strip() else default)

            lsb = (v.direction is not None
                   and v.direction.value == "leastSignificantBitFirst")
            spec = ("crc", width, hexnum(v.polynomial) & m,
                    hexnum(v.init_remainder) & m, hexnum(v.final_xor) & m,
                    bool(v.reflect_data) != lsb, bool(v.reflect_remainder))
        elif name == "Checksum":
            algo = v.name.value
            if algo == "custom":
                name = (v.input_algorithm.name if v.input_algorithm
                        else f"{pkey.rsplit('/', 1)[-1]}Checksum")
                spec = ("custom", self.hook("checksum", qname(path + (name,))))
            m = re.fullmatch(r"(sum|fletcher)(\d+)", algo)
            if algo != "custom":
                spec = (m.group(1), int(m.group(2))) if m else (algo,)
        elif name == "XOR":
            spec = ("xor", vt.enc.bits)
        else:
            spec = ("parity", v.type.value == "Odd")
        local.checksum = True
        skip = v.bits_from_reference or 0
        from_end = v.reference is not None and v.reference.value == "end"
        return Check(spec, local, start, end, skip, from_end, container)

    @staticmethod
    def check_rest(node: Node, where: str) -> None:
        """A value that takes the rest of the packet must come last, as
        must a transformed one that does."""
        items = walk(node.all_items(), inner=False)
        for i, it in enumerate(items[:-1]):
            if ((isinstance(it, Value) and is_rest(it.local.vtype))
                    or (isinstance(it, Transform) and it.raw is None)):
                if all(isinstance(x, (Require, Mark)) for x in items[i + 1:]):
                    continue
                what = (it.name if isinstance(it, Transform) else it.local.name)
                raise Unsupported(f"{where}: {what} takes the rest of the "
                                  "packet but is not last")

    @staticmethod
    def check_length(node: Node, where: str) -> None:
        """A packet has at most one length field, which is always present
        and which nothing compares or takes a size from."""
        items = node.all_items()
        lengths = [it.local for it in walk(items) if isinstance(it, Value)
                   and it.local.length is not None and not it.local.measures]
        if len(lengths) > 1:
            raise Unsupported(f"{where}: more than one packet length field")
        if not lengths:
            return
        lc = lengths[0]
        if lc.optional:
            raise Unsupported(f"{where}: packet length field {lc.name} has an "
                              "IncludeCondition")
        criteria = node.all_criteria() + [c for it in items for c in conds(it)]
        if any(x is lc for c in criteria for x in term_locals(c)):
            raise Unsupported(f"{where}: a criterion or assignment on packet "
                              f"length field {lc.name}")

    @staticmethod
    def scope_of(node: Node) -> list[Local]:
        return [it.local for it in walk(node.all_items())
                if isinstance(it, Value)]

    def match(self, mc, path: Key, scope: list, where: str,
              arg_key: Optional[Callable] = None) -> list:
        """Terms of a RestrictionCriteria, IncludeCondition or
        DiscreteLookup, on values in ``scope``; ``arg_key`` resolves
        argument names in commands."""
        c = mc.choice
        if c.name == "BooleanExpression":
            return [self.boolean(c.value.choice, path, scope, where, arg_key)]
        if c.name == "CustomAlgorithm":
            alg = c.value
            hook = self.hook("criterion", qname(path + (alg.name,)))
            inputs = self.hook_inputs(alg, path, scope, where, arg_key)
            return [Custom(hook, inputs, f"{alg.name}(...)")]
        if c.name == "Comparison":
            comps = [c.value]
        elif c.name == "ComparisonList":
            comps = c.value.comparison
        else:
            raise Unsupported(f"{where}: {c.name} criteria")
        out = []
        for cmp in comps:
            op = (cmp.comparison_operator.value if cmp.comparison_operator
                  else "==")
            if hasattr(cmp, "choice"):  # an ArgumentComparison
                ref = cmp.choice.value
                out.append(self.comparison(cmp.choice.name, ref, op, cmp.value,
                                           path, scope, where, arg_key))
            else:
                out.append(self.comparison("ParameterInstanceRef", cmp, op,
                                           cmp.value, path, scope, where,
                                           arg_key))
        return out

    def boolean(self, c, path: Key, scope: list, where: str, arg_key) -> Term:
        """A term from one alternative of a BooleanExpression (or of its
        ANDedConditions or ORedConditions)."""
        if c.name == "Condition":
            cc = c.value
            if hasattr(cc, "parameter_instance_ref"):
                lhs, ref, rhs = ("ParameterInstanceRef",
                                 cc.parameter_instance_ref, cc.choice)
            else:  # an ArgumentComparisonCheck
                lhs, ref, rhs = cc.choice1.name, cc.choice1.value, cc.choice2
            op = cc.comparison_operator.value
            if rhs.name == "Value":
                return self.comparison(lhs, ref, op, rhs.value, path, scope,
                                       where, arg_key)
            return self.comparison(lhs, ref, op, None, path, scope, where,
                                   arg_key, (rhs.name, rhs.value))
        if c.name not in ("ANDedConditions", "ORedConditions"):
            raise Unsupported(f"{where}: {c.name} in a BooleanExpression")
        terms = [self.boolean(x, path, scope, where, arg_key)
                 for x in c.value.items]
        joiner = " and " if c.name == "ANDedConditions" else " or "
        text = "(" + joiner.join(t.text for t in terms) + ")"
        return (AllOf(terms, text) if c.name == "ANDedConditions"
                else AnyOf(terms, text))

    def value_ref(self, kind: str, ref, path: Key, scope: list, where: str,
                  arg_key):
        """(local, member path, type, name) of the value that a
        ParameterInstanceRef, ArgumentInstanceRef or Comparison names."""
        if kind == "ArgumentInstanceRef":
            if arg_key is None:
                raise Unsupported(f"{where}: comparison of argument "
                                  f"{ref.argument_ref!r} outside a command")
            base, members = member_path(ref.argument_ref)
            pkey = arg_key(base)
        else:
            base, members = member_path(ref.parameter_ref)
            pkey = qname(self.resolve(base, path, self.params, "parameter"))
        local = instance_of(scope, pkey, getattr(ref, "instance", None) or 0,
                            where)
        if local is None:
            raise Unsupported(f"{where}: criterion on {pkey}, which is not "
                              "decoded before it")
        member, vt = walk_members(local.vtype, members,
                                  f"{where}: criterion on {pkey}")
        pkey = ref_text(pkey, members)
        if vt.kind in ("array", "aggregate"):
            raise Unsupported(f"{where}: criterion on {pkey}, which is an "
                              f"{vt.kind}")
        return local, member, vt, pkey

    def comparison(self, kind: str, ref, op: str, text, path: Key, scope: list,
                   where: str, arg_key, other=None):
        """``ref <op> text`` (a value as written), or with ``other`` (the
        kind and reference of a second value) ``ref <op> other``."""
        local, member, vt, pkey = self.value_ref(kind, ref, path, scope, where,
                                                 arg_key)
        raw = getattr(ref, "use_calibrated_value", None) is False
        if op not in ("==", "!=") and vt.kind in ("bool", "string", "binary"):
            raise Unsupported(f"{where}: {op} on {vt.kind} parameter {pkey}")
        target = vt if member else None
        if other is not None and local.optional:
            raise Unsupported(f"{where}: comparison of {pkey}, which has an "
                              "IncludeCondition, with another value")
        if other is not None:
            olocal, opath, ovt, okey = self.value_ref(other[0], other[1], path,
                                                      scope, where, arg_key)
            if olocal.optional:
                raise Unsupported(f"{where}: comparison with {okey}, which has "
                                  "an IncludeCondition")
            if raw or getattr(other[1], "use_calibrated_value", None) is False:
                raise Unsupported(f"{where}: raw comparison of {pkey} and "
                                  f"{okey}")
            if (vt.kind != ovt.kind
                    or vt.kind not in ("int", "enum", "float", "bool",
                                       "string")):
                raise Unsupported(f"{where}: comparison of {vt.kind} {pkey} "
                                  f"and {ovt.kind} {okey}")
            return Criterion(local, op, None, f"{pkey} {op} {okey}", member, vt,
                             olocal, opath, ovt)
        if raw and (vt.kind not in ("int", "enum", "bool")
                    or vt.calibrator is not None
                    or isinstance(vt.enc, StringEnc)):
            raise Unsupported(f"{where}: raw comparison on {vt.kind} parameter "
                              f"{pkey}")
        value = self.literal(vt, text, raw, where)
        return Criterion(local, op, value, f"{pkey} {op} {text}", member,
                         target)

    # -- commands --

    def arg_scope(self, key: Key, seen: tuple = ()) -> dict:
        """Argument name -> (MetaCommand key, Argument) visible in a
        MetaCommand."""
        if key in seen:
            raise Unsupported("meta-command inheritance cycle through "
                              f"{qname(key)}")
        path, mc = self.mcs[key]
        scope = {}
        if mc.base_meta_command is not None:
            bkey = self.resolve(mc.base_meta_command.meta_command_ref, path,
                                self.mcs, "meta-command")
            scope.update(self.arg_scope(bkey, seen + (key,)))
        if mc.argument_list is not None:
            for a in mc.argument_list.argument:
                scope[a.name] = (key, a)
        return scope

    def arg_local(self, scope: dict, name: str, where: str) -> Local:
        if name not in scope:
            raise Unsupported(f"{where}: unknown argument {name!r}")
        owner, a = scope[name]
        tkey = self.resolve(a.argument_type_ref, self.mcs[owner][0],
                            self.atypes, "argument type")
        local = self.local(a.name, f"{qname(owner)}#{a.name}",
                           self.vtype(tkey, self.atypes))
        local.length = length_adjust(a, local.vtype, f"argument {local.key}")
        return local

    def msg_node(self, key: Key) -> Node:
        """A Message: the root container it names, chosen by its
        MatchCriteria, like a container derived from it with no entries of
        its own."""
        path, m = self.messages[key]
        node = Node("tm", qname(key), m.name, False)
        # Messages are named apart from containers.
        self.nodes[("message",) + key] = node
        where = f"message {node.key}"
        try:
            ckey = self.resolve(m.container_ref.container_ref, path,
                                self.containers, "container")
            base = self.tm_node(ckey)
            if base.error:
                raise Unsupported(f"base container {base.key} is not supported")
            node.base = base
            node.criteria = self.match(m.match_criteria, path,
                                       self.scope_of(base), where)
            if not node.criteria:
                raise Unsupported(f"{where}: MatchCriteria with no comparisons")
            base.children.append(node)
        except Unsupported as e:
            node.error = str(e)
            node.base = None
        self.order.append(node)
        return node

    def tc_node(self, key: Key) -> Node:
        if key in self.nodes:
            n = self.nodes[key]
            if getattr(n, "_building", False):
                raise Unsupported("meta-command inheritance cycle through "
                                  f"{qname(key)}")
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
                bkey = self.resolve(mc.base_meta_command.meta_command_ref, path,
                                    self.mcs, "meta-command")
                base = self.tc_node(bkey)
                if base.error:
                    raise Unsupported(f"base meta-command {base.key} is not "
                                      "supported")
                node.base = base
                al = mc.base_meta_command.argument_assignment_list
                for asg in al.argument_assignment if al else []:
                    if asg.argument_name not in scope:
                        raise Unsupported(f"{where}: assignment to unknown "
                                          f"argument {asg.argument_name!r}")
                    akey = (f"{qname(scope[asg.argument_name][0])}#"
                            f"{asg.argument_name}")
                    local = next((lc for lc in reversed(self.scope_of(base))
                                  if lc.key == akey), None)
                    if local is None:
                        continue  # the argument is not encoded in the packet
                    value = self.literal(local.vtype, asg.argument_value, False,
                                         where)
                    node.criteria.append(Criterion(local, "==", value,
                                                   f"{asg.argument_name} = "
                                                   f"{asg.argument_value}"))
            cc = mc.command_container
            if cc is not None:
                node._edc = (cc, path)
                values = self.scope_of(base) if base else []
                prefix: list[Item] = []
                shared = None
                if cc.base_container is not None:
                    try:
                        bc = self.resolve(cc.base_container.container_ref, path,
                                          self.mc_containers,
                                          "command container")
                    except Unsupported:
                        # A container of a CommandContainerSet, or of
                        # telemetry: its entries come first.
                        shared = self.resolve(cc.base_container.container_ref,
                                              path, self.tc_containers,
                                              "container")
                if shared is not None:
                    if base is not None:
                        raise Unsupported(f"{where}: a CommandContainer that "
                                          "extends a container other than its "
                                          "BaseMetaCommand's")
                    prefix = self.tc_include(shared, scope, where, values, ())
                    rc = cc.base_container.restriction_criteria
                    if rc is not None:
                        terms = self.match(
                            rc, path, values, where,
                            lambda name: self.arg_key_of(scope, name, where))
                        prefix.append(Require(terms, qname(shared),
                                              " and ".join(t.text
                                                           for t in terms)))
                elif cc.base_container is not None:
                    owner = self.mc_containers[bc]
                    if base is None or self.nodes.get(owner) is not base:
                        raise Unsupported(f"{where}: CommandContainer extends "
                                          f"{qname(bc)}, which is not the "
                                          "container of its BaseMetaCommand")
                    rc = cc.base_container.restriction_criteria
                    if rc is not None and rc.next_container is not None:
                        raise Unsupported(f"{where}: NextContainer on a "
                                          "CommandContainer")
                    if rc is not None:
                        # Like argument assignments: what they fix, encoding
                        # writes, and decoding picks the command by them.
                        node.criteria += self.match(
                            rc, path, self.scope_of(base), where,
                            lambda name: self.arg_key_of(scope, name, where))
                # A CommandContainer without a BaseContainer still extends
                # the container of its BaseMetaCommand, as Yamcs reads it.
                if self.has_transform(cc, where):
                    m = self._id()
                    node.items = prefix + self.wrap_transform(
                        cc,
                        [Mark(m)]
                        + self.tc_items(cc, scope, path, where, values, m), m,
                        where, path, node.key, True)
                else:
                    node.items = (
                        prefix + self.tc_items(cc, scope, path, where, values))
                    # A size covers the whole command, base containers'
                    # entries too.
                    node.items += self.size_pad(cc, node.all_items(), 0, where,
                                                values, path)
            self.check_rest(node, where)
            self.check_tail(node, where)
            check_aligned(node.all_items(), where)
            self.check_length(node, where)
            if not node.abstract:
                node.checks = self.node_checks(node, where)
            if base is not None:
                base.children.append(node)
        except Unsupported as e:
            node.error = str(e)
            node.base = None
        node._building = False
        self.order.append(node)
        return node

    def tc_items(self, cc, scope: dict, path: Key, where: str, values: list,
                 start: int = 0, stack: tuple = ()) -> list[Item]:
        """Items for a CommandContainer's entries, or those of a container
        it includes (from mark ``start``; ``stack`` lists the containers
        being included).  ``values`` lists the values encoded so far (base
        meta-commands first) and grows."""

        def arg_key(name: str) -> str:
            if name not in scope:
                raise Unsupported(f"{where}: unknown argument {name!r}")
            return f"{qname(scope[name][0])}#{name}"

        items: list[Item] = []
        segs: dict = {}
        for ch in cc.entry_list.items:
            e = ch.value
            entries = ("ArgumentRefEntry", "ArrayArgumentRefEntry",
                       "ParameterRefEntry", "ArrayParameterRefEntry")
            if (ch.name not in entries
                    + ("FixedValueEntry", "ContainerRefEntry",
                       "IndirectParameterRefEntry") + SEGMENTS):
                raise Unsupported(f"{where}: {ch.name} in a CommandContainer")
            cond = None
            if e.include_condition is not None:
                cond = self.match(e.include_condition, path, values, where,
                                  arg_key)
            out = items if cond is None else []
            out += self.next_seek(items,
                                  self.location(e.location_in_container_in_bits,
                                                start, path, values, where,
                                                arg_key), where)
            if (out and isinstance(out[-1], EndSeek)
                    and ch.name not in entries + SEGMENTS):
                raise Unsupported(f"{where}: a {ch.name} located from the "
                                  "container's end")
            if ch.name in SEGMENTS:
                after = []
                if out and isinstance(out[-1], EndSeek):
                    if e.repeat_entry is not None:
                        raise Unsupported(f"{where}: a repeated {ch.name} "
                                          "located from the container's end")
                    out[-1], after = self.end_seek(out[-1], cc, None, where,
                                                   e.size_in_bits)
                out += self.segment(ch.name, e, path, where, segs,
                                    cond is not None, values, arg_key)
                out += after
            elif ch.name == "IndirectParameterRefEntry":
                out += self.indirect(e, path, values, where, arg_key)
            elif (ch.name == "ContainerRefEntry"
                  and (owner := self.mc_owner(e.container_ref, path))):
                if e.repeat_entry is None:
                    out += self.measure(self.mc_include(owner, where, values,
                                                        stack), where)
                else:
                    local = self.local(
                        self.mcs[owner][1].command_container.name, qname(owner),
                        self.mc_container_type(owner, where, stack))
                    local = self.bind(self.repeat(local, e, path, where),
                                      values, where, arg_key)
                    out.append(Value(local))
                    values.append(local)
            elif ch.name == "ContainerRefEntry":
                ckey = self.resolve(e.container_ref, path, self.tc_containers,
                                    "container")
                if e.repeat_entry is None:
                    out += self.measure(self.tc_include(ckey, scope, where,
                                                        values, stack), where)
                else:
                    inc = self.tc_containers[ckey][1]
                    if inc.base_container is not None:
                        raise Unsupported(f"{where}: repeated container "
                                          f"{qname(ckey)} has a BaseContainer")
                    vt = self.container_type(ckey, list(stack), where,
                                             self.tc_containers)
                    local = self.local(inc.name, qname(ckey), vt)
                    local = self.bind(self.repeat(local, e, path, where),
                                      values, where, arg_key)
                    out.append(Value(local))
                    values.append(local)
            elif ch.name in entries:
                if "Argument" in ch.name:
                    local = self.arg_local(scope, e.argument_ref, where)
                else:
                    local = self.param_local(e.parameter_ref, path)
                local = self.subset(local, e, path, where)
                local = self.bind(self.repeat(local, e, path, where), values,
                                  where, arg_key)
                after = []
                if out and isinstance(out[-1], EndSeek):
                    out[-1], after = self.end_seek(out[-1], cc, local, where)
                out.append(Value(local))
                out += after
                values.append(local)
            else:
                out += self.fixed_entry(e, path, where)
            if cond is not None:
                if any(isinstance(it, Transform) for it in walk(out)):
                    raise Unsupported(f"{where}: transformed data with an "
                                      "IncludeCondition")
                for it in walk(out):
                    if isinstance(it, Value):
                        it.local.optional = True
                items.append(Cond(cond, out))
        self.no_next(items, where)
        self.raw_segments(items, segs, values)
        return items

    def tc_include(self, ckey: Key, scope: dict, where: str, values: list,
                   stack: tuple) -> list[Item]:
        """Items for a container that a command includes: its bases'
        entries, then its own, each base's RestrictionCriteria checked
        after its entries, and padding up to its size."""
        m = self._id()
        items: list[Item] = [Mark(m)]
        arg_key = lambda name: self.arg_key_of(scope, name, where)  # noqa
        chain, key = [], ckey
        while True:
            if key in stack or key in (k for _, _, k in chain):
                raise Unsupported(f"{where}: container {qname(key)} includes "
                                  "itself")
            kpath, c = self.tc_containers[key]
            chain.append((kpath, c, key))
            if c.base_container is None:
                break
            key = self.resolve(c.base_container.container_ref, kpath,
                               self.tc_containers, "container")
        inner: list[Item] = []
        for kpath, c, key in chain[::-1]:
            if edc_items(c):
                raise Unsupported(f"{where}: ErrorDetectCorrect in container "
                                  f"{qname(key)}, which a command includes")
            inner += self.tc_items(c, scope, kpath, where, values, m,
                                   stack + (key,))
            rc = (c.base_container.restriction_criteria
                  if c.base_container is not None else None)
            if rc is not None:
                terms = self.match(rc, kpath, values, where, arg_key)
                inner.append(Require(terms, qname(key),
                                     " and ".join(t.text for t in terms)))
        cpath, inc = self.tc_containers[ckey]
        for _, c, key in chain[1:]:
            if self.has_transform(c, where):
                raise Unsupported(f"{where}: transform algorithms in base "
                                  f"container {qname(key)}")
        return self.wrap_transform(
            inc,
            items + inner + self.size_pad(inc, inner, m, where, values, cpath),
            m, where, cpath, qname(ckey), False)

    @staticmethod
    def measure(items: list, where: str) -> list[Item]:
        """An included container's items, its length fields (those not
        already measuring a container inside it) made to measure it, with
        a LengthEnd for each where it ends."""
        ends = []
        for it in walk(items):
            if (isinstance(it, Value) and it.local.length is not None
                    and not it.local.measures):
                if it.local.optional:
                    raise Unsupported(f"{where}: length field {it.local.name} "
                                      "of an included container has an "
                                      "IncludeCondition")
                it.local.measures = True
                ends.append(LengthEnd(it.local))
        return items + ends

    def mc_owner(self, ref: str, path: Key) -> Optional[Key]:
        """The meta-command whose CommandContainer ``ref`` names, if it
        names no container of a CommandContainerSet or of telemetry."""
        try:
            self.resolve(ref, path, self.tc_containers, "container")
            return None
        except Unsupported:
            pass
        try:
            return self.mc_containers[self.resolve(ref, path,
                                                   self.mc_containers,
                                                   "command container")]
        except Unsupported:
            return None

    def mc_include(self, key: Key, where: str, values: list,
                   stack: tuple) -> list[Item]:
        """Items for the CommandContainer of meta-command ``key``, which a
        command includes: those of the container it extends (its
        BaseMetaCommand's, included the same way, or another), then its
        own, with its own arguments; its argument assignments, which fix
        what they assign; and padding up to its size."""
        mark = ("meta-command",) + key
        if mark in stack:
            raise Unsupported(f"{where}: the CommandContainer of {qname(key)} "
                              "includes itself")
        stack += (mark,)
        path, mc = self.mcs[key]
        cc = mc.command_container
        if cc is None:
            raise Unsupported(f"{where}: meta-command {qname(key)} has no "
                              "CommandContainer")
        if edc_items(cc):
            raise Unsupported(f"{where}: ErrorDetectCorrect in the container "
                              f"of meta-command {qname(key)}, which a command "
                              "includes")
        scope = self.arg_scope(key)

        def arg_key(name: str) -> str:
            return self.arg_key_of(scope, name, where)

        m = self._id()
        start = len(values)
        inner: list[Item] = []
        bkey = (self.resolve(mc.base_meta_command.meta_command_ref, path,
                             self.mcs, "meta-command")
                if mc.base_meta_command is not None else None)
        bc = shared = None
        if cc.base_container is not None:
            ref = cc.base_container.container_ref
            try:
                bc = self.resolve(ref, path, self.mc_containers,
                                  "command container")
            except Unsupported:
                shared = self.resolve(ref, path, self.tc_containers,
                                      "container")
        if shared is not None:
            if bkey is not None:
                raise Unsupported(f"{where}: the CommandContainer of "
                                  f"{qname(key)} extends a container other "
                                  "than its BaseMetaCommand's")
            inner += self.tc_include(shared, scope, where, values, stack)
        elif bkey is not None:
            if bc is not None and self.mc_containers[bc] != bkey:
                raise Unsupported(f"{where}: the CommandContainer of "
                                  f"{qname(key)} extends {qname(bc)}, which is "
                                  "not the container of its BaseMetaCommand")
            inner += self.mc_include(bkey, where, values, stack)
            crit = []
            al = mc.base_meta_command.argument_assignment_list
            for asg in al.argument_assignment if al else []:
                name = asg.argument_name
                if name not in scope:
                    raise Unsupported(f"{where}: assignment to unknown "
                                      f"argument {name!r}")
                akey = f"{qname(scope[name][0])}#{name}"
                local = next((lc for lc in reversed(values[start:])
                              if lc.key == akey), None)
                if local is not None:
                    value = self.literal(local.vtype, asg.argument_value, False,
                                         where)
                    crit.append(Criterion(local, "==", value,
                                          f"{name} = {asg.argument_value}"))
            if crit:
                inner.append(Require(crit, qname(key),
                                     " and ".join(c.text for c in crit),
                                     fixes=True))
        rc = (cc.base_container.restriction_criteria
              if cc.base_container is not None else None)
        if rc is not None:
            if rc.next_container is not None:
                raise Unsupported(f"{where}: NextContainer on a "
                                  "CommandContainer")
            terms = self.match(rc, path, values, where, arg_key)
            inner.append(Require(terms, qname(key),
                                 " and ".join(t.text for t in terms)))
        inner += self.tc_items(cc, scope, path, where, values, m, stack)
        return self.wrap_transform(cc,
                                   [Mark(m)] + inner
                                   + self.size_pad(cc, inner, m, where, values,
                                                   path), m, where, path,
                                   qname(key), False)

    def arg_key_of(self, scope: dict, name: str, where: str) -> str:
        if name not in scope:
            raise Unsupported(f"{where}: unknown argument {name!r}")
        return f"{qname(scope[name][0])}#{name}"

    def fixed_entry(self, e, path: Key, where: str) -> list[Item]:
        """A FixedValueEntry of any size, repeated as its RepeatEntry says:
        Fixed items of at most 64 bits each."""
        bits = e.size_in_bits
        value = int.from_bytes(hex_bytes(e.binary_value.strip() or "0", where),
                               "big")
        if bits < 1 or value >> bits:
            raise Unsupported(f"{where}: FixedValueEntry {e.binary_value} in "
                              f"{bits} bits")
        # Most significant first: a short first part, then 64 bits each.
        one = []
        left = bits
        while left > 0:
            n = left % 64 or 64
            left -= n
            one.append(Fixed(n, (value >> left) & ((1 << n) - 1)))
        rp = e.repeat_entry
        if rp is None:
            return one
        count = self.int_value(rp.count, path, "RepeatEntry Count", where)
        gap = (self.int_value(rp.offset, path, "RepeatEntry Offset", where)
               if rp.offset is not None else 0)
        if not isinstance(count, int) or not isinstance(gap, int):
            raise Unsupported(f"{where}: a FixedValueEntry repeated as other "
                              "values say")
        if count < 1 or gap < 0:
            raise Unsupported(f"{where}: RepeatEntry Count {count} or Offset "
                              f"{gap}")
        out = []
        for i in range(count):
            if i and gap:
                out.append(Seek(None, gap))
            out += one
        return out

    # -- whole mission --

    def compile(self) -> Mission:
        for key in self.containers:
            self.tm_node(key)
        for key in self.mcs:
            self.tc_node(key)
        for key in self.messages:
            self.msg_node(key)
        # A node whose base failed fails too.  Errors list the root causes
        # only; skipping names every node left out, root causes first.
        # The containers others derive from.
        self.bases = set()
        for key, (path, sc) in self.containers.items():
            if sc.base_container is not None:
                try:
                    self.bases.add(
                        qname(self.resolve(sc.base_container.container_ref,
                                           path, self.containers, "container")))
                except Unsupported:
                    pass
        roots, derived = [], []
        for n in self.order:
            # An abstract container that nothing derives from is only ever
            # included, where it is compiled with what surrounds it.
            if (n.error and n.abstract and n.kind == "tm"
                    and n.key not in self.bases):
                continue
            if n.error:
                (derived if n.error.startswith("base ")
                 else roots).append(f"{n.key}: {n.error}")
        streams = []
        for key in self.streams:
            try:
                streams.append(self.stream(key))
            except Unsupported as e:
                roots.append(str(e))
        streams = self.link_streams(streams, roots)
        msgs = roots + derived
        if msgs and not self.skip_unsupported:
            if derived:
                roots.append(f"and {len(derived)} containers or meta-commands "
                             "derived from them")
            raise Unsupported("unsupported XTCE constructs:\n  "
                              + "\n  ".join(roots))

        ok = [n for n in self.order if not n.error]
        pos = {id(n): i for i, n in enumerate(self.nodes.values())}
        ok.sort(key=lambda n: (n.kind != "tm", pos[id(n)]))
        names = Namer(RUNTIME_NAMES)
        for n in ok:
            if not n.abstract:
                n.ident = names.take(camel(n.name), camel(n.key))
        for st in streams:
            name = st.key.rsplit("/", 1)[-1]
            st.ident = names.take(camel(name) + "Stream",
                                  camel(st.key) + "Stream")
        roots = [n for n in ok if n.base is None and n.children]
        kinds = []
        for r in roots:
            members = [m for m in [r] + r.descendants() if not m.abstract]
            if members:
                kinds.append(
                    Kind(names.take(camel(r.name) + "Kind",
                                    camel(r.key) + "Kind"), r, members, 0))
        concrete = [n for n in ok if not n.abstract]
        used, aggregates, copies = {}, {}, []
        monitored: dict = {}
        for n in concrete:
            self._finish(n)
            self.bind_alarms(n)
            self.bind_algorithms(n)
            for _, _, a in n.alarms:
                if a.stateful:
                    monitored[a.key] = max(monitored.get(a.key, 0), a.history)
            for it in walk(n.all_items()):
                if isinstance(it, Value):
                    for vt in it.local.vtype.tree():
                        if vt.kind == "enum":
                            used[id(vt)] = vt
                        elif vt.kind == "aggregate" and vt.origin:
                            copies.append(vt)
                            aggregates[id(vt.origin)] = vt.origin
                        elif vt.kind == "aggregate":
                            aggregates[id(vt)] = vt
        for vt in used.values():
            vt.ident = names.take(camel(vt.name), camel(vt.key))
        for vt in aggregates.values():
            # A repeated container is often a packet too.
            vt.ident = names.take(camel(vt.name), camel(vt.name) + "Item",
                                  camel(vt.key))
        for vt in copies:
            vt.ident = vt.origin.ident
        for k in kinds:
            k.max_bytes = max(m.max_bytes for m in k.members)
        for st in streams:
            keys = st.service or [st.container]
            sizes = ([k.max_bytes for k in kinds if k.root.key in keys]
                     + [n.max_bytes for n in concrete if n.key in keys])
            st.max_bits = 8 * max(sizes, default=self.max_field_bits // 8)
        mnames = Namer()
        hnames = Namer()
        for h in self.hooks.values():
            h.ident = hnames.take(snake(h.name.rsplit("/", 1)[-1]),
                                  snake(h.name))
        # The mission is named for its last document, which by convention
        # is its own: the documents it builds on come first.
        return Mission(
            name=self.roots[-1].name, spaces=[r.name for r in self.roots],
            source="", types=list(used.values()),
            aggregates=list(aggregates.values()), nodes=concrete, kinds=kinds,
            roots=roots, skipped=msgs,
            monitored=[(k, mnames.take(snake(k.rsplit("/", 1)[-1]), snake(k)),
                        h) for k, h in monitored.items()], streams=streams,
            hooks=list(self.hooks.values()))

    def stream(self, key: Key) -> Stream:
        """A FixedFrameStream or VariableFrameStream."""
        path, kind, st = self.streams[key]
        where = f"stream {qname(key)}"
        pcm = st.pcm_type.value if st.pcm_type else "NRZL"
        if kind == "CustomStream":
            # Its input is bytes of the encoded stream, which the
            # application's DecodingAlgorithm decodes; it has no frames.
            out = Stream(qname(key), "", "", False, pcm, bool(st.inverted),
                         custom=True, decoder=st.decoding_algorithm.name)
            out._refs = (path, None, st.decoded_stream_ref,
                         st.encoded_stream_ref)
            return out
        if (pcm not in ("NRZL", "NRZM", "NRZS", "BiPhaseL", "BiPhaseM",
                        "BiPhaseS")):
            raise Unsupported(f"{where}: pcmType {pcm}")
        service = []
        if st.choice.name == "ServiceRef":
            skey = self.resolve(st.choice.value.service_ref, path,
                                self.services, "service")
            spath, sv = self.services[skey]
            ckey = qname(skey)
            if sv.choice.name == "ContainerRefSet":
                refs = [(spath, c.container_ref)
                        for c in sv.choice.value.container_ref]
            else:
                refs = []
                for m in sv.choice.value.message_ref:
                    mpath, msg = self.messages[
                        self.resolve(m.message_ref, spath, self.messages,
                                     "message")]
                    refs.append((mpath, msg.container_ref.container_ref))
            service = [qname(self.resolve(ref, rpath, self.containers,
                                          "container")) for rpath, ref in refs]
        else:
            ckey = qname(self.resolve(st.choice.value.container_ref, path,
                                      self.containers, "container"))
        ss = st.sync_strategy
        out = Stream(qname(key), "", ckey, kind == "FixedFrameStream", pcm,
                     bool(st.inverted), service=service)
        out._refs = (path, st.stream_ref.stream_ref if st.stream_ref else None,
                     None, None)
        if ss.auto_invert is not None and ss.auto_invert.invert_algorithm:
            if not out.fixed:
                raise Unsupported(f"{where}: an InvertAlgorithm on a variable "
                                  "stream, which doesn't invert")
            out.inverter = ss.auto_invert.invert_algorithm.name
        if not out.fixed:
            flag = ss.flag
            out.flag_bits = flag.flag_size_in_bits or 6
            if out.flag_bits < 2:
                raise Unsupported(f"{where}: a flag of {out.flag_bits} bit")
            out.flag_ones = (flag.flag_bit_type is None
                             or flag.flag_bit_type.value == "ones")
            return out
        out.frame_bits = st.frame_length_in_bits
        sp = ss.sync_pattern
        n = sp.pattern_length_in_bits
        if n < 1:
            raise Unsupported(f"{where}: a {n}-bit sync pattern")
        # The pattern and mask are truncated from the left.
        out.pattern = int(sp.pattern.strip() or "0", 16) & ((1 << n) - 1)
        mask = (1 << n) - 1
        if sp.mask is not None:
            m = sp.mask_length_in_bits or n
            mask = (int(sp.mask.strip() or "0", 16) & ((1 << m) - 1)
                    | (((1 << n) - 1) & ~((1 << m) - 1)))
        out.mask, out.pattern_bits = mask & ((1 << n) - 1), n
        out.location = sp.bit_location_from_start_of_container or 0
        if (out.location < 0 or out.frame_bits < 1
                or out.location + n > out.frame_bits):
            raise Unsupported(f"{where}: a sync pattern outside the frame")
        out.max_errors = ss.max_bit_errors_in_sync_pattern or 0
        out.aperture = st.sync_aperture_in_bits or 0
        if ss.verify_to_lock_good_frames is not None:
            out.verify = ss.verify_to_lock_good_frames
        if ss.check_to_lock_good_frames is not None:
            out.check = ss.check_to_lock_good_frames
        if ss.auto_invert is not None:
            out.invert_after = ((ss.auto_invert.bad_frames_to_auto_invert
                                 or 1024) * out.frame_bits)
        return out

    def link_streams(self, streams: list, errors: list) -> list:
        """Connects each stream to the one that feeds it, and leaves out
        (adding why to ``errors``) those whose chain doesn't work."""
        by_key = {st.key: st for st in streams}

        def find(path, ref, where):
            key = qname(self.resolve(ref, path, self.streams, "stream"))
            if key not in by_key:
                raise Unsupported(f"{where}: stream {key} is not supported")
            return by_key[key]

        bad = {}
        for st in streams:
            path, ref, decoded, encoded = st._refs
            where = f"stream {st.key}"
            try:
                if ref is not None:
                    st.feeder = find(path, ref, where)
                if st.custom:
                    target = find(path, decoded, where)
                    if target.feeder not in (None, st):
                        raise Unsupported(f"{where}: decodes into "
                                          f"{target.key}, which "
                                          f"{target.feeder.key} feeds")
                    if target is st:
                        raise Unsupported(f"{where}: decodes into itself")
                    target.feeder = st
                    source = find(path, encoded, where)
                    if source is not st:
                        st.feeder = source
            except Unsupported as e:
                bad[st.key] = str(e)
        for st in streams:
            seen, s = [], st
            while s is not None:
                if s in seen:
                    bad.setdefault(st.key,
                                   f"stream {st.key}: its streams feed each "
                                   "other in a cycle")
                    break
                if s.key in bad and s is not st:
                    bad.setdefault(st.key,
                                   f"stream {st.key}: fed by {s.key}, which is "
                                   "not supported")
                    break
                seen.append(s)
                s = s.feeder
        errors += bad.values()
        # Each stream after those that feed it, whose types it holds.
        out: list = []
        for st in streams:
            if st.key not in bad:
                out += [s for s in reversed(st.chain()) if s not in out]
        return out

    def hook(self, kind: str, name: str, outputs: int = 0) -> Hook:
        """The hook for custom algorithm ``name``, which computes ``kind``."""
        h = self.hooks.get((kind, name))
        if h is None:
            h = self.hooks[kind, name] = Hook(kind, name, outputs=outputs)
        return h

    def hook_inputs(self, alg, path: Key, scope: list, where: str,
                    arg_key=None) -> list:
        """The inputs of a custom algorithm, in order: values of the packet,
        which must be present, and constants."""
        out: list = []
        items = alg.input_set.items if alg.input_set else []
        if len(items) > MAX_HOOK_INPUTS:
            raise Unsupported(f"{where}: a custom algorithm with more than "
                              f"{MAX_HOOK_INPUTS} inputs")
        for ch in items:
            v = ch.value
            if ch.name == "Constant":
                try:
                    out.append(("const", float(v.value)))
                except ValueError:
                    raise Unsupported(f"{where}: constant {v.constant_name} "
                                      f"{v.value!r} is not a number") from None
                continue
            local, member, vt, name = self.value_ref(
                "ParameterInstanceRef", v, path, scope, where, arg_key)
            if local.optional:
                raise Unsupported(f"{where}: custom algorithm input {name} has "
                                  "an IncludeCondition")
            if vt.kind not in ("int", "float", "enum", "bool"):
                raise Unsupported(f"{where}: custom algorithm input {name} is "
                                  f"a {vt.kind} value")
            if vt.wide:
                raise Unsupported(f"{where}: custom algorithm input {name} is "
                                  f"a {vt.enc.bits}-bit integer")
            if v.use_calibrated_value is False and vt.calibrator is not None:
                raise Unsupported(f"{where}: the raw value of calibrated "
                                  f"{name}")
            out.append(("value", local, member, vt))
        return out

    def bind_algorithms(self, n: Node) -> None:
        """The MathAlgorithms that packet ``n`` triggers, by holding a value
        of a parameter that triggers it or being (or deriving from) a
        container that does.  One whose inputs the packet doesn't hold is
        left out, and ``algorithm_notes`` says why."""
        scope = self.scope_of(n)
        fields = {lc for _, lc in n.fields}
        keys = {lc.key for lc in scope}
        chain = {x.key for x in n.chain()}
        names = Namer()
        for key, (path, alg) in self.algorithms.items():
            custom = not hasattr(alg, "math_operation")
            mo = alg if custom else alg.math_operation
            where = (f"{n.key}: "
                     f"{'CustomAlgorithm' if custom else 'MathAlgorithm'} "
                     f"{qname(key)}")
            triggered = False
            for ch in mo.trigger_set.items if mo.trigger_set else []:
                try:
                    if ch.name == "OnParameterUpdateTrigger":
                        triggered |= (qname(self.resolve(ch.value.parameter_ref,
                                                         path, self.params,
                                                         "parameter")) in keys)
                    elif ch.name == "OnContainerUpdateTrigger":
                        triggered |= (qname(self.resolve(ch.value.container_ref,
                                                         path, self.containers,
                                                         "container")) in chain)
                except Unsupported:
                    pass
            if not triggered:
                continue
            if custom:
                self.custom_algorithm(n, key, path, alg, scope, fields, names,
                                      where)
                continue
            try:
                out = qname(self.resolve(mo.output_parameter_ref, path,
                                         self.params, "parameter"))
                expr = self.math_operation(mo, path, where).expr
                expr = self.bind_inputs(expr, scope, fields, n, where)
            except Unsupported as e:
                n.algorithm_notes.append(str(e))
                continue
            n.algorithms.append(
                Algorithm(out,
                          names.take(snake(out.rsplit("/", 1)[-1]), snake(out)),
                          expr))

    def custom_algorithm(self, n: Node, key: Key, path: Key, alg, scope: list,
                         fields: set, names, where: str) -> None:
        """The outputs of a CustomAlgorithm that packet ``n`` triggers."""
        try:
            inputs = self.hook_inputs(alg, path, scope, where)
            for x in input_locals(inputs):
                if x not in fields and x not in n.fixed:
                    raise Unsupported(f"{where}: input {x.name} is computed by "
                                      "the packet")
            outs = [qname(self.resolve(o.parameter_ref, path, self.params,
                                       "parameter"))
                    for o in (alg.output_set.output_parameter_ref
                              if alg.output_set else [])]
            if not outs:
                raise Unsupported(f"{where}: no outputs")
        except Unsupported as e:
            n.algorithm_notes.append(str(e))
            return
        hook = self.hook("algorithm", qname(key), len(outs))
        for i, out in enumerate(outs):
            n.algorithms.append(
                Algorithm(out,
                          names.take(snake(out.rsplit("/", 1)[-1]), snake(out)),
                          hook=hook, inputs=inputs, index=i))

    def bind_inputs(self, e: tuple, scope: list, fields: set, n: Node,
                    where: str) -> tuple:
        """A MathAlgorithm's expression bound to the fields of packet ``n``;
        an input may be absent, which makes the output absent."""
        if e[0] == "const":
            return e
        if e[0] == "raw":
            raise Unsupported(f"{where}: ThisParameterOperand, the output's "
                              "own earlier value")
        if e[0] != "value":
            return (e[0],
                    *(self.bind_inputs(a, scope, fields, n, where)
                      for a in e[1:]))
        r = e[1]
        local, member, vt, name = self.value_ref("ParameterInstanceRef", r.ref,
                                                 r.path, scope, where, None)
        if local not in fields and local not in n.fixed:
            raise Unsupported(f"{where}: input {name} is computed by the "
                              "packet")
        if vt.kind not in ("int", "float", "enum", "bool"):
            raise Unsupported(f"{where}: input {name} is a {vt.kind} value")
        if vt.wide:
            raise Unsupported(f"{where}: input {name} is a {vt.enc.bits}-bit "
                              "integer")
        if r.ref.use_calibrated_value is False and vt.calibrator is not None:
            raise Unsupported(f"{where}: the raw value of calibrated {name}")
        return ("value", ValueRef(r.ref, r.path, local, member, vt))

    def bind_alarms(self, n: Node) -> None:
        """The alarms of a packet's fields, their criteria bound to its
        values.  An array's alarm is its elements'.  Alarms that can't be
        evaluated are left out, and ``alarm_notes`` says why."""
        fields = {lc for _, lc in n.fields}
        scope = self.scope_of(n)
        # A value its inheritance fixes has an alarm too, of that value.
        names = Namer([name for name, _ in n.fields])
        fixed = [(names.take(snake(lc.name)), lc) for lc in n.fixed
                 if lc.vtype.kind in ("int", "float", "enum", "bool")]
        for name, lc in n.fields + fixed:
            vt = lc.vtype
            target = vt.elem if vt.kind == "array" else vt
            if target.kind in ("array", "aggregate"):
                if any(t.alarm for t in target.tree()):
                    n.alarm_notes.append(f"{n.key}: the alarms inside "
                                         f"{lc.name} are not evaluated")
                continue
            a = target.alarm
            if a is None:
                continue
            if a.note:
                n.alarm_notes.append(f"{n.key}: alarm of {lc.name}: {a.note}")
                continue
            where = f"{n.key}: alarm of {lc.name}"

            def terms(mc):
                ts = self.match(mc, a.path, scope, where)
                for t in ts:
                    for x in term_locals(t):
                        if x not in fields and x not in n.fixed:
                            raise Unsupported(f"{where}: criteria on {x.name}, "
                                              "which the packet computes")
                return ts

            def bound(d):
                custom = None
                if d.custom is not None:
                    custom = (d.custom[0],
                              self.hook_inputs(d.custom[1], a.path, scope,
                                               where))
                    for x in input_locals(custom[1]):
                        if x not in fields and x not in n.fixed:
                            raise Unsupported(f"{where}: input {x.name}, which "
                                              "the packet computes")
                return dataclasses.replace(
                    d, conditions=[(lv, terms(mc)) for lv, mc in d.conditions],
                    custom=custom)

            if vt.kind == "array" and a.history:
                n.alarm_notes.append(f"{where}: change ranges on an array")
                continue
            try:
                n.alarms.append((name, lc,
                                 Alarm(a.path,
                                       bound(a.default) if a.default else None,
                                       [(terms(mc), bound(d))
                                        for mc, d in a.contexts], key=lc.key)))
            except Unsupported as e:
                n.alarm_notes.append(str(e))

    @staticmethod
    def node_hooks(n: Node) -> list:
        """The hooks that decoding or encoding ``n`` calls."""
        out = terms_hooks(n.all_criteria())
        for h in items_hooks(n.all_items()) + checks_hooks(n.checks):
            if h not in out:
                out.append(h)
        return out

    def _finish(self, n: Node) -> None:
        fixed = {}
        for c in n.all_criteria():
            fixes = (isinstance(c, Criterion) and c.op == "==" and not c.path
                     and c.other is None)
            if fixes and c.local not in fixed:
                fixed[c.local] = c.value
        # An included meta-command's argument assignments, unless inside a
        # Cond (which walk would splice in).
        for it in n.all_items():
            if isinstance(it, Require) and it.fixes:
                for c in it.criteria:
                    if c.local not in fixed and not c.local.optional:
                        fixed[c.local] = c.value
        n.fixed = fixed
        n.derived = derived_sizes(n)
        names = Namer()
        n.fields = [(names.take(snake(it.local.name)), it.local)
                    for it in walk(n.all_items()) if isinstance(it, Value)
                    and it.local not in fixed and not it.local.computed
                    and it.local not in n.derived]
        n.max_bytes = (max_bits(n.all_items()) + 7) // 8
        n.hooks = self.node_hooks(n)


def edc_items(container) -> list:
    """The (kind, algorithm) ErrorDetectCorrect items of a container's
    BinaryEncoding that name the parameter holding their value; the others
    only document the container."""
    be = getattr(container, "binary_encoding", None)
    edc = be.error_detect_correct if be is not None else None
    if edc is None:
        return []
    return [(ch.name, ch.value) for ch in edc.items
            if ch.value.parameter_ref is not None]


# Seconds in each XTCE time unit.  XTCE leaves months and years to the
# system: they are the Gregorian calendar's mean month and year (365.2425
# days).
TIME_UNITS = {"seconds": 1.0, "milliseconds": 1e-3, "microseconds": 1e-6,
              "nanoseconds": 1e-9, "picoseconds": 1e-12, "minutes": 60.0,
              "hours": 3600.0, "days": 86400.0, "months": 2629746.0,
              "years": 31556952.0}


def affine(cal, a: float, b: float):
    """``a * cal(x) + b``, as a calibrator (None for the identity)."""
    if (a, b) == (1.0, 0.0):
        return cal
    if cal is None:
        return Polynomial([(b, 0), (a, 1)])
    if isinstance(cal, Polynomial):
        terms = [(a * c, e) for c, e in cal.terms]
        if b:
            terms.append((b, 0))
        return Polynomial(terms)
    if isinstance(cal, Spline):
        return dataclasses.replace(
            cal, points=[(r, a * c + b) for r, c in cal.points])
    if isinstance(cal, MathOp):
        return MathOp(("+", ("*", cal.expr, ("const", a)), ("const", b)))
    return ContextCal([(p, mc, affine(c, a, b)) for p, mc, c in cal.cases],
                      affine(cal.default, a, b))


def unbound(d: DynSize) -> bool:
    """Whether a size is still to be bound to the value that gives it."""
    return d.local is None and d.lookup is None and d.member is None


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
    elif isinstance(cal, Spline) and not cal.extrapolate and not cal.orders:
        # Interpolation of order 0 or 1 stays within the points.
        ends = [c for _, c in cal.points]
    else:
        return elo, elim - 1
    if not all(math.isfinite(x) for x in ends):
        return elo, elim - 1
    return max(math.floor(min(ends)), elo), min(math.ceil(max(ends)), elim - 1)


def int_bounds(vt: VType) -> tuple[int, int]:
    """The smallest and largest value of an integer type."""
    return vt.valid_range or value_range(vt)


def dyn_units(b: DynSize, rng: tuple[int, int], cap: int) -> int:
    """The largest ``(slope * x + intercept) / unit`` for x in ``rng``, at
    most ``cap``."""
    lo, hi = rng
    top = max(b.slope * lo, b.slope * hi) + b.intercept
    return (min(max(math.floor(top), 0) // b.unit, cap) if math.isfinite(top)
            else cap)


def bounded(vt: VType, b: DynSize, rng: tuple[int, int], cap: int) -> VType:
    """A copy of ``vt`` whose size or count is the bound ``b``, with
    ``b.max`` set from the range ``rng`` of the value it adjusts and the
    most bits, ``cap``, the field may take."""
    units = dyn_units(b, rng, cap)
    e = vt.enc
    if isinstance(e, ArrayEnc):
        step = max(item_bits(e.elem) + e.max_gap, 1)
        b.max = min(units, (cap + e.max_gap) // step // b.factor)
        enc = dataclasses.replace(e, count=b)
    elif isinstance(e, StringEnc):
        b.max = min(units, e.max_bits // 8, cap // 8)
        enc = dataclasses.replace(e, max_bits=b.max * 8, dyn=b)
    else:
        b.max = min(units, cap // 8)
        enc = dataclasses.replace(e, bits=b.max * 8, dyn=b)
    return dataclasses.replace(vt, enc=enc)


def range_bands(ranges, unit: float) -> list[Band]:
    """The Bands of an AlarmRanges (in ``unit`` seconds, for a time)."""
    inside = (ranges.range_form is not None
              and ranges.range_form.value == "inside")
    return [band(r, level, inside, unit) for level, name in enumerate(LEVELS)
            if (r := getattr(ranges, f"{name}_range", None)) is not None]


def band(r, level: int, inside: bool, unit: float) -> Band:
    """An alarm range (in ``unit`` seconds, for a time) as a Band."""
    def scaled(x):
        return None if x is None else x * unit
    lo = r.min_inclusive if r.min_inclusive is not None else r.min_exclusive
    hi = r.max_inclusive if r.max_inclusive is not None else r.max_exclusive
    return Band(level, scaled(lo), r.min_exclusive is None, scaled(hi),
                r.max_exclusive is None, inside)


def int_band(b: Band, vt: VType) -> Band:
    """A Band for integer values: whole, inclusive bounds within the range
    of the field's type (None when the type's own limit is as far), so
    that every language compares integers exactly.  An empty range is
    1 to 0."""
    lo_lim, hi_lim = (vt.eng_limits if vt.eng
                      else ((-(1 << (vt.width - 1)), 1 << (vt.width - 1))
                            if vt.signed else (0, 1 << vt.width)))
    hi_lim -= 1
    lo = hi = None
    if b.lo is not None:
        if not math.isfinite(b.lo):
            lo = None if b.lo < 0 else hi_lim + 1
        else:
            lo = math.ceil(b.lo) if b.lo_incl else math.floor(b.lo) + 1
    if b.hi is not None:
        if not math.isfinite(b.hi):
            hi = None if b.hi > 0 else lo_lim - 1
        else:
            hi = math.floor(b.hi) if b.hi_incl else math.ceil(b.hi) - 1
    if ((lo is not None and lo > hi_lim) or (hi is not None and hi < lo_lim)
            or (lo is not None and hi is not None and lo > hi)):
        return Band(b.level, 1, True, 0, True, b.inside)
    lo = None if lo is not None and lo <= lo_lim else lo
    hi = None if hi is not None and hi >= hi_lim else hi
    return Band(b.level, lo, True, hi, True, b.inside)


def instance_of(scope: list, key: str, instance: int,
                where: str) -> Optional[Local]:
    """The value ``key`` of ``scope`` that a ParameterInstanceRef's
    ``instance`` names: 0 is the one decoded last, -1 the one before it,
    and so on, within the packet.  Positive instances are values yet to
    come, and so are other packets' values: they aren't supported."""
    if instance > 0:
        raise Unsupported(f"{where}: instance {instance} of {key}, a value yet "
                          "to come")
    found = [lc for lc in reversed(scope) if lc.key == key]
    if not found:
        return None
    if -instance >= len(found):
        raise Unsupported(f"{where}: instance {instance} of {key}, which the "
                          f"packet holds {len(found)} "
                          f"time{'s' if len(found) > 1 else ''} before it")
    return found[-instance]


def check_aligned(items: list[Item], where: str) -> None:
    """Strings and binary data with bitOrder leastSignificantBitFirst must
    start on a byte boundary, where their bytes read the same as with the
    usual bit order."""
    marks = {0: 0}

    def walk_items(items: list, pos: Optional[int]) -> Optional[int]:
        for it in items:
            if isinstance(it, Cond):
                after = walk_items(it.items, pos)
                pos = pos if after == pos else None
            elif isinstance(it, Mark):
                marks[it.id] = pos
            elif isinstance(it, Seek):
                base = (pos if it.from_start is None
                        else marks.get(it.from_start))
                pos = (None if base is None or isinstance(it.offset, DynSize)
                       else (base + it.offset) % 8)
            elif isinstance(it, Fill):
                base = marks.get(it.start)
                pos = (None if base is None or isinstance(it.size, DynSize)
                       else (base + it.size) % 8)
            elif isinstance(it, Value):
                if not it.assembled:
                    pos = advance(it.local.vtype, pos, it.local.name)
            elif isinstance(it, (EndSeek, TailSeek)):
                pos = None
            elif isinstance(it, Transform):
                if pos != 0:
                    raise Unsupported(f"{where}: transformed data {it.name} "
                                      "may not start on a byte boundary")
                walk_items(it.items, 0)
                pos = 0 if it.raw is not None else None
            else:
                pos = None if pos is None else (pos + it.bits) % 8
        return pos

    def advance(vt: VType, pos: Optional[int], name: str) -> Optional[int]:
        """Checks ``vt`` at ``pos`` (in bits after a byte boundary, None
        when unknown) and returns the position after it."""
        e = vt.enc
        if isinstance(e, (StringEnc, BinaryEnc)) and e.lsb:
            tag = getattr(e, "size_tag_bits", None) or 0
            if pos != 0 or tag % 8:
                raise Unsupported(f"{where}: {name} has bitOrder "
                                  "leastSignificantBitFirst but may not start "
                                  "on a byte boundary")
        if isinstance(e, AggregateEnc):
            for m in e.members:
                pos = None if pos is None else (pos + m.skip) % 8
                pos = advance(m.vtype, pos, name)
            return None if pos is None else (pos + e.pad) % 8
        if isinstance(e, ArrayEnc):
            gap = e.gap if isinstance(e.gap, int) else None
            if isinstance(e.count, int) and gap is not None:
                for _ in range(e.count):
                    pos = advance(e.elem, pos, name)
                    pos = None if pos is None else (pos + gap) % 8
                return (None if pos is None or e.count == 0
                        else (pos - gap) % 8)
            # Elements that keep the alignment keep it however many there are.
            first = advance(e.elem, pos, name)
            step = (None if first is None or gap is None or pos is None
                    else (first - pos + gap) % 8)
            if step == 0:
                return pos
            for t in e.elem.tree():
                if isinstance(t.enc, (StringEnc, BinaryEnc)) and t.enc.lsb:
                    advance(t, None, name)
            return None
        if isinstance(e, XformEnc):
            if pos != 0:
                raise Unsupported(f"{where}: transformed data {name} may not "
                                  "start on a byte boundary")
            return 0
        bits = fixed_bits(vt)
        if bits is None:
            # Variable strings and binary data occupy whole bytes, after a
            # string's size tag.
            bits = getattr(e, "size_tag_bits", None) or 0
        return None if pos is None else (pos + bits) % 8

    walk_items(items, 0)


# The most inputs a custom algorithm may have, which keeps the C runtime's
# xtce_pk_inputs a fixed size.
MAX_HOOK_INPUTS = 32

SEGMENTS = ("ParameterSegmentRefEntry", "ContainerSegmentRefEntry",
            "StreamSegmentEntry")


def fixed_bits(vt: VType) -> Optional[int]:
    """The bits a value of this type always occupies, or None if it varies."""
    e = vt.enc
    if isinstance(e, ArrayEnc):
        b = fixed_bits(e.elem)
        if not isinstance(e.count, int) or b is None:
            return None
        return e.count * b + max(e.count - 1, 0) * e.max_gap
    if isinstance(e, AggregateEnc):
        bs = [fixed_bits(m.vtype) for m in e.members]
        return (None if None in bs
                else sum(bs) + sum(m.skip for m in e.members) + e.pad)
    if isinstance(e, StringEnc):
        return e.fixed_bits if e.dyn is None else None
    if isinstance(e, BinaryEnc):
        return None if e.variable else e.bits
    if isinstance(e, XformEnc):
        return e.bits if e.raw is not None else None
    return e.bits


def static_end(items: list[Item], start: int) -> Optional[tuple[int, int]]:
    """(position, furthest bit) after ``items``, in bits from mark
    ``start``, or None when they depend on the data."""
    pos = extent = 0
    marks = {start: 0}
    for it in items:
        if isinstance(it, (Cond, TailSeek)):
            return None
        if isinstance(it, Transform) and it.raw is None:
            return None
        if isinstance(it, Fill):
            if isinstance(it.size, DynSize) or it.start not in marks:
                return None
            pos = marks[it.start] + it.size
        elif isinstance(it, Seek):
            if isinstance(it.offset, DynSize):
                return None
            if it.from_start is not None and it.from_start not in marks:
                return None
            pos = ((marks[it.from_start] if it.from_start is not None else pos)
                   + it.offset)
        elif isinstance(it, Mark):
            marks[it.id] = pos
        else:
            bits = (it.bits if not isinstance(it, Value) else 0 if it.assembled
                    else fixed_bits(it.local.vtype))
            if bits is None:
                return None
            pos += bits
        extent = max(extent, pos)
    return pos, extent


def derived_sizes(n: Node) -> dict:
    """The values of ``n`` that encoding computes: Local -> (the value whose
    size or count it gives, that DynSize).  Such a value is an uncalibrated
    integer, always present, that gives the count of an array or the size
    of a binary value, always present, so that it can be worked out from
    their length.  Criteria, locations and lookups may also read it:
    encoding computes it first and they read the computed value.  Anything
    else that uses it (a string's size, an aggregate's or element's size,
    or a size or count of a value that may be absent) leaves it a field
    that the application sets."""
    items = walk(n.all_items())
    # Local -> [(consumer, DynSize) for a size or count it gives, READ for a
    # criterion, location or lookup that reads it, or None for any other use]
    uses: dict = {}

    def use(lc, what=None) -> None:
        uses.setdefault(lc, []).append(what)

    def dyn_uses(d: DynSize, consumer=None, read=False) -> None:
        """A size or count, of ``consumer`` when it is a whole value that is
        always present, or a location's offset when ``read``."""
        if d.lookup is not None:
            for crit, _ in d.lookup:
                for lc in (x for c in crit for x in term_locals(c)):
                    use(lc, READ)
        elif d.local is not None:
            if read and not d.path:
                use(d.local, READ)
            else:
                plain = consumer is not None and not d.path
                use(d.local, (consumer, d) if plain else None)

    for c in n.all_criteria() + [c for it in n.all_items() for c in conds(it)]:
        for lc in term_locals(c):
            use(lc, READ)
    for it in items:
        if isinstance(it, Seek) and isinstance(it.offset, DynSize):
            dyn_uses(it.offset, read=True)
        elif isinstance(it, Value):
            vt = it.local.vtype
            for t in vt.tree():
                if t.dyn is None:
                    continue
                top = (t is vt and t.kind in ("array", "binary")
                       and not is_rest(t))
                consumer = (it.local if top and not it.local.optional
                            and it.local not in n.fixed else None)
                dyn_uses(t.dyn, consumer)
    derived = {}
    for it in items:
        if not isinstance(it, Value):
            continue
        lc = it.local
        vt = lc.vtype
        if (lc.optional or lc in n.fixed or lc.computed or vt.kind != "int"
                or vt.calibrator is not None):
            continue
        found = uses.get(lc, [])
        sizes = [u for u in found if u is not None and u is not READ]
        if sizes and None not in found:
            derived[lc] = sizes[0]
    return derived


READ = "read"  # a use of a value that only reads it


def conds(it: Item) -> list[Term]:
    """The criteria of a conditional block and the blocks inside it."""
    if not isinstance(it, Cond):
        return []
    return it.criteria + [c for i in it.items for c in conds(i)]


def is_rest(vt: VType) -> bool:
    """Whether the value takes the rest of the packet."""
    e = vt.enc
    return ((isinstance(e, BinaryEnc) and e.rest)
            or (isinstance(e, XformEnc) and e.raw is None and not e.sized))


def item_bits(vt: VType) -> int:
    """The most bits a value of this type occupies."""
    e = vt.enc
    if isinstance(e, ArrayEnc):
        n = e.max_count
        return n * item_bits(e.elem) + max(n - 1, 0) * e.max_gap
    if isinstance(e, AggregateEnc):
        return sum(m.skip + item_bits(m.vtype) for m in e.members) + e.pad
    if isinstance(e, StringEnc):
        return e.max_bits
    return e.bits


def max_bits(items: list[Item]) -> int:
    """Upper bound on the bits a packet with these items occupies."""
    pos = extent = 0
    marks = {0: 0}
    for it in walk(items, inner=False):
        if isinstance(it, Transform):
            # Encoding writes its items, then what it gives after them.
            extent = max(extent, pos + 8 * (it.cap + it.out_cap))
        if isinstance(it, Seek):
            pos = ((marks[it.from_start] if it.from_start is not None else pos)
                   + it.max_offset)
        elif isinstance(it, Fill):
            pos = max(pos, marks[it.start] + it.max_size)
        elif isinstance(it, Mark):
            marks[it.id] = pos
        elif isinstance(it, Value):
            vt = it.local.vtype
            pos += 0 if it.assembled else item_bits(vt)
            # Encoding transformed bytes writes them, then what the
            # transform gives after them.
            work = [t.enc.cap for t in vt.tree() if isinstance(t.enc, XformEnc)
                    and not t.enc.time]
            if work:
                extent = max(extent, pos + 8 * max(work))
        else:
            pos += it.bits
        extent = max(extent, pos)
    return extent


def compile_document(root, skip_unsupported: bool = False,
                     dialect: Optional[str] = None) -> Mission:
    """Compile the mission whose definitions are all in one document."""
    return Compiler([root], skip_unsupported, dialect).compile()


def compile_documents(roots: list, skip_unsupported: bool = False,
                      dialect: Optional[str] = None) -> Mission:
    """Compile the mission whose definitions are in several documents:
    ``roots`` is the root SpaceSystem of each, in the order given;
    ``dialect`` is the dialect of XTCE they follow when they don't say."""
    return Compiler(roots, skip_unsupported, dialect).compile()
