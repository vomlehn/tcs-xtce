"""Compile a mission's XTCE document into the packet model.

The document is loaded with the ``xtce`` Python package (the XML binding
generated from the XTCE schema), so anything that package accepts can be
compiled.  The compiler resolves names, fills in schema defaults, flattens
included containers and records which XTCE constructs it does not support.
"""

from __future__ import annotations

import keyword
import re
from typing import Callable, Optional

from .model import (
    BinaryEnc,
    Cond,
    Criterion,
    EnumLabel,
    FloatEnc,
    Fixed,
    IntEnc,
    Item,
    Kind,
    Local,
    Mark,
    Mission,
    Node,
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
    # Python builtins that would shadow useful names in dataclasses
    "bytes", "int", "float", "str", "list", "dict", "type", "id", "len",
    # generated members
    "decode", "encode", "encode_into", "describe", "label", "value", "raw",
    "name", "max_bytes", "data", "buf", "r", "w", "init", "to_bytes", "packed",
}


def hex_bytes(text: str, where: str) -> bytes:
    """The bytes of an ``xs:hexBinary`` value; an odd number of digits is
    read as if it had a leading 0."""
    if not re.fullmatch(r"[0-9A-Fa-f]*", text):
        raise Unsupported(f"{where}: {text!r} is not hexBinary")
    return bytes.fromhex(text.zfill(len(text) + len(text) % 2))


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

    def free(self, name: str) -> bool:
        return name not in self.used

    def take(self, *candidates: str) -> str:
        for c in candidates:
            if self.free(c):
                self.used.add(c)
                return c
        base, n = candidates[-1], 2
        while not self.free(f"{base}{n}"):
            n += 1
        self.used.add(f"{base}{n}")
        return f"{base}{n}"


class FieldNamer(Namer):
    """Field names, kept clear of each other's ``set_`` accessors.

    Bit-packed fields get a ``set_<name>`` setter, so no field may be named
    after another's setter.  The rule applies with and without bit packing so
    that names do not depend on it.
    """

    def free(self, name: str) -> bool:
        if name in self.used or f"set_{name}" in self.used:
            return False
        return not (name.startswith("set_") and name[4:] in self.used)


# Type names the runtimes or the generated modules define.
RUNTIME_NAMES = {
    "Packet", "PacketError", "Codec", "BitReader", "BitWriter", "Buffer",
    "Result", "Error", "ManagedBuffer", "Optional", "Union", "Any", "String",
    "Vec", "Self", "Option", "Box", "Ok", "Err", "Some", "None",
}


# --- the compiler ---------------------------------------------------------------


class Compiler:
    def __init__(self, root, skip_unsupported: bool = False, bit_packing: bool = False):
        self.root = root
        self.skip_unsupported = skip_unsupported
        self.bit_packing = bit_packing
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
            vt = self.time_type(key, base, t, where)
            self.vtypes[cache] = vt
            return vt
        if base not in ("Integer", "Float", "Enumerated", "Boolean", "String", "Binary"):
            raise Unsupported(f"{where}: {kind} is not supported")
        if t.choice is None:
            raise Unsupported(f"{where}: no data encoding")
        enc_name, e = t.choice.name, t.choice.value
        enc, cal = self.encoding(enc_name, e, where)
        vt = VType(key=qname(key), name=t.name, kind="", enc=enc, calibrator=cal)
        if base == "Float":
            if not isinstance(enc, (IntEnc, FloatEnc)):
                raise Unsupported(f"{where}: float with {enc_name}")
            vt.kind = "float"
        elif base in ("Integer", "Enumerated", "Boolean"):
            if not isinstance(enc, IntEnc):
                raise Unsupported(f"{where}: {base.lower()} with {enc_name}")
            if cal is not None:
                article = "an" if base[0] in "AEIOU" else "a"
                raise Unsupported(f"{where}: calibrator on {article} {base.lower()} type")
            vt.kind = {"Integer": "int", "Enumerated": "enum", "Boolean": "bool"}[base]
        elif base == "String":
            if not isinstance(enc, StringEnc):
                raise Unsupported(f"{where}: string with {enc_name}")
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
        self.vtypes[cache] = vt
        return vt

    def time_type(self, key: Key, base: str, t, where: str) -> VType:
        """Times are seconds: ``scale * raw + offset`` from the reference time."""
        enc = t.encoding
        if enc is None:
            raise Unsupported(f"{where}: time type without Encoding")
        units = enc.units.value if enc.units else "seconds"
        if units != "seconds":
            raise Unsupported(f"{where}: time units {units}")
        e, cal = self.encoding(enc.choice.name, enc.choice.value, where)
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

    def encoding(self, name: str, e, where: str):
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
            if v.choice1 is not None:
                raise Unsupported(f"{where}: string size from {v.choice1.name}")
            mx = self._size(v.max_size_in_bits, "string", where)
            if v.choice2.name == "LeadingSize":
                tag = v.choice2.value.size_in_bits_of_size_tag or 16
                return StringEnc(None, mx, None, self._tag(tag, where)), None
            return StringEnc(None, mx, self._term(v.choice2.value, where)), None
        if name == "BinaryDataEncoding":
            if e.from_binary_transform_algorithm or e.to_binary_transform_algorithm:
                raise Unsupported(f"{where}: binary transform algorithms")
            c = e.size_in_bits.choice
            if c.name != "FixedValue":
                raise Unsupported(f"{where}: binary size from {c.name}")
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

    def location(self, loc, start: int, where: str) -> list[Item]:
        if loc is None:
            return []
        ref = loc.reference_location.value if loc.reference_location else "previousEntry"
        if loc.choice.name != "FixedValue":
            raise Unsupported(f"{where}: location from {loc.choice.name}")
        off = loc.choice.value
        if ref == "containerStart":
            return [Seek(start, off)]
        if ref == "previousEntry":
            return [Seek(None, off)] if off else []
        raise Unsupported(f"{where}: location relative to {ref}")

    @staticmethod
    def _no_repeat(e, where: str) -> None:
        if e.repeat_entry is not None:
            raise Unsupported(f"{where}: RepeatEntry")

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
            if ch.name not in ("ParameterRefEntry", "ContainerRefEntry"):
                raise Unsupported(f"{where}: {ch.name}")
            self._no_repeat(e, where)
            cond = None
            if e.include_condition is not None:
                cond = self.match(e.include_condition, path, scope, where)
            out = items if cond is None else []
            out += self.location(e.location_in_container_in_bits, start, where)
            if ch.name == "ParameterRefEntry":
                local = self.param_local(e.parameter_ref, path)
                out.append(Value(local))
                scope.append(local)
            else:
                ckey = self.resolve(e.container_ref, path, self.containers, "container")
                if ckey in stack:
                    raise Unsupported(f"{where}: container includes itself")
                cpath, inc = self.containers[ckey]
                if inc.base_container is not None:
                    raise Unsupported(f"{where}: included container {qname(ckey)} has a BaseContainer")
                if inc.binary_encoding is not None:
                    raise Unsupported(f"{where}: container BinaryEncoding")
                m = self._id()
                out.append(Mark(m))
                out += self.tm_items(inc, cpath, stack + [ckey], m, scope)
            if cond is not None:
                for it in walk(out):
                    if isinstance(it, Value):
                        it.local.optional = True
                items.append(Cond(cond, out))
        return items

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
            if sc.binary_encoding is not None:
                raise Unsupported(f"container {node.key}: BinaryEncoding")
            node.items = self.tm_items(sc, path, [key], 0, scope)
            if base is not None:
                base.children.append(node)
        except Unsupported as e:
            node.error = str(e)
            node.base = None
        node._building = False
        self.order.append(node)
        return node

    @staticmethod
    def scope_of(node: Node) -> list[Local]:
        return [it.local for it in walk(node.all_items()) if isinstance(it, Value)]

    def match(self, mc, path: Key, scope: list, where: str) -> list[Criterion]:
        """Criteria of a RestrictionCriteria or IncludeCondition, on values in ``scope``."""
        c = mc.choice
        if c.name == "Comparison":
            comps = [c.value]
        elif c.name == "ComparisonList":
            comps = c.value.comparison
        else:
            raise Unsupported(f"{where}: {c.name} criteria")
        out = []
        for cmp in comps:
            pkey = qname(self.resolve(cmp.parameter_ref, path, self.params, "parameter"))
            local = next((lc for lc in reversed(scope) if lc.key == pkey), None)
            if local is None:
                raise Unsupported(f"{where}: criterion on {pkey}, which is not decoded before it")
            if local.optional:
                raise Unsupported(f"{where}: criterion on {pkey}, which has an IncludeCondition")
            if cmp.instance:
                raise Unsupported(f"{where}: comparison of instance {cmp.instance}")
            raw = cmp.use_calibrated_value is False
            vt = local.vtype
            if raw and (vt.kind not in ("int", "enum", "bool")):
                raise Unsupported(f"{where}: raw comparison on {vt.kind} parameter {pkey}")
            op = cmp.comparison_operator.value if cmp.comparison_operator else "=="
            if op not in ("==", "!=") and vt.kind in ("bool", "string", "binary"):
                raise Unsupported(f"{where}: {op} on {vt.kind} parameter {pkey}")
            value = self.literal(vt, cmp.value, raw, where)
            out.append(Criterion(local, op, value, f"{pkey} {op} {cmp.value}"))
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
                node.items = self.tc_items(cc, scope, path, where)
            if base is not None:
                base.children.append(node)
        except Unsupported as e:
            node.error = str(e)
            node.base = None
        node._building = False
        self.order.append(node)
        return node

    def tc_items(self, cc, scope: dict, path: Key, where: str) -> list[Item]:
        items: list[Item] = []
        for ch in cc.entry_list.items:
            e = ch.value
            if ch.name not in ("ArgumentRefEntry", "ParameterRefEntry", "FixedValueEntry"):
                raise Unsupported(f"{where}: {ch.name} in a CommandContainer")
            self._no_repeat(e, where)
            if e.include_condition is not None:
                raise Unsupported(f"{where}: IncludeCondition in a CommandContainer")
            items += self.location(e.location_in_container_in_bits, 0, where)
            if ch.name == "ArgumentRefEntry":
                items.append(Value(self.arg_local(scope, e.argument_ref, where)))
            elif ch.name == "ParameterRefEntry":
                items.append(Value(self.param_local(e.parameter_ref, path)))
            else:
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
        used = {}
        for n in concrete:
            self._finish(n)
            for it in walk(n.all_items()):
                if isinstance(it, Value) and it.local.vtype.kind == "enum":
                    used[id(it.local.vtype)] = it.local.vtype
        for vt in used.values():
            vt.ident = names.take(camel(vt.name), camel(vt.key))
        for k in kinds:
            k.max_bytes = max(m.max_bytes for m in k.members)
        return Mission(
            name=self.root.name,
            source="",
            types=list(used.values()),
            nodes=concrete,
            kinds=kinds,
            roots=roots,
            skipped=msgs,
        )

    def _finish(self, n: Node) -> None:
        fixed = {}
        for c in n.all_criteria():
            if c.op == "==" and c.local not in fixed:
                fixed[c.local] = c.value
        n.fixed = fixed
        names = FieldNamer()
        n.fields = [
            (names.take(snake(it.local.name)), it.local)
            for it in walk(n.all_items())
            if isinstance(it, Value) and it.local not in fixed
        ]
        n.max_bytes = (max_bits(n.all_items()) + 7) // 8
        if self.bit_packing:
            # Packed fields follow each other with no padding, the first at
            # bit 0, crossing byte boundaries as needed.
            offset = 0
            for _, lc in n.fields:
                width = packed_width(lc)
                if width:
                    n.packed[lc] = (offset, width)
                    offset += width
            n.packed_bytes = (offset + 7) // 8


def packed_width(lc: Local) -> int:
    """Bits a field takes in bit-packed storage, or 0 if it is not packed.

    Integer and enumerated fields are stored as their raw encoding, booleans
    as one bit.  Optional, float, string and binary fields are not packed.
    """
    vt = lc.vtype
    if lc.optional or vt.kind not in ("int", "enum", "bool") or not isinstance(vt.enc, IntEnc):
        return 0
    return 1 if vt.kind == "bool" else vt.enc.bits


def item_bits(vt: VType) -> int:
    e = vt.enc
    if isinstance(e, StringEnc):
        return e.max_bits
    return e.bits


def max_bits(items: list[Item]) -> int:
    """Upper bound on the bits a packet with these items occupies."""
    pos = extent = 0
    marks = {0: 0}
    for it in walk(items):
        if isinstance(it, Seek):
            pos = (marks[it.from_start] if it.from_start is not None else pos) + it.offset
        elif isinstance(it, Mark):
            marks[it.id] = pos
        elif isinstance(it, Value):
            pos += item_bits(it.local.vtype)
        else:
            pos += it.bits
        extent = max(extent, pos)
    return extent


def compile_document(root, skip_unsupported: bool = False, bit_packing: bool = False) -> Mission:
    return Compiler(root, skip_unsupported, bit_packing).compile()
