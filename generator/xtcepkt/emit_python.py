"""Emit a Python module of packet codecs (uses ``xtce.packet``)."""

from __future__ import annotations

from .common import flt, invertible, le, string_spec
from .model import walk, AggregateEnc, ArrayEnc, BinaryEnc, Cond, Criterion, DynSize, Fixed, FloatEnc, IntEnc, Kind, Mark, Mission, Node, Polynomial, Seek, Spline, Value, VType


def pstr(s: str) -> str:
    out = []
    for c in s:
        if c in '"\\':
            out.append("\\" + c)
        elif ord(c) < 0x20 or ord(c) == 0x7F:
            out.append(f"\\x{ord(c):02x}")
        else:
            out.append(c)
    return '"' + "".join(out) + '"'


def field_type(vt: VType) -> str:
    if vt.kind == "array":
        return f"list[{field_type(vt.elem)}]"
    if vt.kind == "aggregate":
        return vt.ident
    return {"int": "int", "float": "float", "bool": "bool", "string": "str", "binary": "bytes"}.get(vt.kind) or vt.ident


def default(vt: VType) -> str:
    if vt.kind == "array":
        return "dataclasses.field(default_factory=list)"
    if vt.kind == "aggregate":
        return f"dataclasses.field(default_factory={vt.ident})"
    return {"int": "0", "float": "0.0", "bool": "False", "string": '""', "binary": 'b""'}.get(vt.kind) or f"pk.enum_value({vt.ident}, 0)"


def literal(vt: VType, v) -> str:
    k = vt.kind
    if k == "float":
        return flt(v)
    if k == "enum":
        return f"pk.enum_value({vt.ident}, {v})"
    if k == "bool":
        return "True" if v else "False"
    if k == "string":
        return pstr(v)
    if k == "binary":
        return repr(bytes(v))
    return str(v)


def points(cal: Spline) -> str:
    return "(" + ", ".join(f"({flt(a)}, {flt(b)})" for a, b in cal.points) + ",)"


def calibrate(vt: VType, x: str, f: str) -> str:
    cal = vt.calibrator
    if cal is None:
        return x
    if isinstance(cal, Polynomial):
        terms = ", ".join(f"({flt(c)}, {e})" for c, e in cal.terms)
        return f"pk.polynomial({x}, ({terms},))"
    return f"pk.spline({x}, {points(cal)}, {cal.order}, {cal.extrapolate}, {f})"


def uncalibrate(vt: VType, y: str, f: str) -> str:
    cal = vt.calibrator
    if cal is None:
        return y
    if isinstance(cal, Polynomial):
        return f"pk.polynomial_inverse({y}, {flt(cal.intercept)}, {flt(cal.slope)})"
    return f"pk.spline_inverse({y}, {points(cal)}, {cal.extrapolate}, {f})"


def size(d: DynSize, x: str, f: str) -> str:
    """Expression for a size or count that the value ``x`` gives."""
    n = f"pk.dynamic_size({x}, {flt(d.slope)}, {flt(d.intercept)}, {d.unit}, {d.max}, {f})"
    return f"{n} * {d.factor}" if d.factor != 1 else n


def count(vt: VType, ref, f: str) -> str:
    c = vt.enc.count
    return size(c, ref(c.local), f) if isinstance(c, DynSize) else str(c)


def spec(vt: VType, ref, f: str) -> str:
    d = vt.enc.dyn
    s = repr(string_spec(vt))
    return f"pk.sized({s}, {size(d, ref(d.local), f)})" if d is not None else s


def read_expr(vt: VType, f: str, ref) -> str:
    """Expression reading one value with BitReader ``r``; ``ref`` gives the
    expression for a value that gives a size or count."""
    e = vt.enc
    if isinstance(e, AggregateEnc):
        members = ", ".join(f"{m.ident}={read_expr(m.vtype, f, ref)}" for m in e.members)
        return f"{vt.ident}({members})"
    if isinstance(e, ArrayEnc):
        return f"pk.read_list(r, {count(vt, ref, f)}, {e.gap}, {f}, lambda: {read_expr(e.elem, f, ref)})"
    if isinstance(e, IntEnc):
        raw = f"r.uint_order({e.bits}, {le(vt)}, {f})"
        enc = repr(e.encoding)
        if vt.kind == "int":
            return f"pk.to_signed({raw}, {e.bits}, {enc})" if vt.signed else raw
        if vt.kind == "enum":
            v = f"pk.to_signed({raw}, {e.bits}, {enc})" if vt.signed else raw
            return f"pk.enum_value({vt.ident}, {v})"
        if vt.kind == "bool":
            return f"{raw} != 0"
        return calibrate(vt, f"pk.int_to_float({raw}, {e.bits}, {enc})", f)
    if isinstance(e, FloatEnc):
        return calibrate(vt, f"pk.float_from_raw(r.uint_order({e.bits}, {le(vt)}, {f}), {e.bits})", f)
    if isinstance(e, BinaryEnc):
        n = size(e.dyn, ref(e.dyn.local), f) if e.dyn else e.bits // 8
        return f"r.bytes({n}, {f})"
    return f"pk.read_string(r, {spec(vt, ref, f)}, {f})"


def write_stmt(vt: VType, x: str, f: str, ref) -> str:
    """Statement writing ``x``; not for arrays or dynamic binary values,
    which ``PythonEmitter.write_value`` writes."""
    e = vt.enc
    if isinstance(e, IntEnc):
        bits, enc = e.bits, repr(e.encoding)
        if vt.kind == "int":
            raw = f"pk.from_signed({x}, {bits}, {enc}, {f})"
        elif vt.kind == "enum":
            raw = f"pk.from_signed(int({x}), {bits}, {enc}, {f})"
        elif vt.kind == "bool":
            raw = f"1 if {x} else 0"
        else:
            if not invertible(vt):
                return f'raise pk.PacketError("NotInvertible", {f})'
            raw = f"pk.float_to_int({uncalibrate(vt, x, f)}, {bits}, {enc}, {f})"
        return f"w.uint_order({bits}, {le(vt)}, {raw})"
    if isinstance(e, FloatEnc):
        if not invertible(vt):
            return f'raise pk.PacketError("NotInvertible", {f})'
        return f"w.uint_order({e.bits}, {le(vt)}, pk.float_to_raw({uncalibrate(vt, x, f)}, {e.bits}))"
    if isinstance(e, BinaryEnc):
        return f"pk.write_binary(w, {x}, {e.bits // 8}, {f})"
    return f"pk.write_string(w, {spec(vt, ref, f)}, {x}, {f})"


def condition(c: Criterion, x: str) -> str:
    vt = c.local.vtype
    v = c.value if vt.kind in ("int", "enum") else literal(vt, c.value)
    return f"{x} {c.op} {v}"


def describer(vt: VType) -> str:
    """Expression for a function giving the canonical text of a value."""
    k = vt.kind
    if k == "array":
        return f"lambda v: pk.describe_list(v, {describer(vt.elem)})"
    if k == "aggregate":
        parts = ", ".join(f"{pstr(m.ident + '=')} + ({describer(m.vtype)})(v.{m.ident})" for m in vt.enc.members)
        return f'lambda v: "{{" + ", ".join(({parts},)) + "}}"'
    if k == "float":
        return "pk.describe_float"
    if k == "string":
        return "pk.describe_str"
    if k == "binary":
        return "pk.describe_bytes"
    if k == "enum":
        return f"_DESCRIBE_{vt.ident}"
    if k == "bool":
        return "_describe_bool"
    return "str"


class PythonEmitter:
    def __init__(self, mission: Mission, source: str):
        self.m = mission
        self.source = source
        self.out: list[str] = []

    def line(self, s: str = "", depth: int = 0) -> None:
        self.out.append("    " * depth + s if s else "")

    def emit(self) -> str:
        m = self.m
        self.line(f"# @generated by tcsxtce from {self.source} (SpaceSystem {m.name!r}).")
        self.line("# Do not edit by hand; regenerate with `python3 -m tcsxtce`.")
        self.line(f'"""Packet codecs for the ``{m.name}`` mission."""')
        self.line()
        self.line("from __future__ import annotations")
        self.line()
        self.line("import dataclasses")
        self.line("import enum")
        self.line("from dataclasses import dataclass")
        self.line("from typing import ClassVar, Optional, Union")
        self.line()
        self.line("from xtce import packet as pk")
        self.line()
        self.line()
        self.line("def _describe_bool(v: bool) -> str:")
        self.line('return "true" if v else "false"', 1)
        self.line()
        names = [vt.ident for vt in m.types + m.aggregates] + [n.ident for n in m.nodes] + [k.ident for k in m.kinds]
        self.line()
        self.line("__all__ = [" + ", ".join(pstr(n) for n in names) + "]")
        for vt in m.types:
            self.enum_type(vt)
        for vt in m.aggregates:
            self.aggregate_type(vt)
        for n in m.nodes:
            self.node(n)
        for k in m.kinds:
            self.kind(k)
        return "\n".join(self.out) + "\n"

    def enum_type(self, vt: VType) -> None:
        self.line()
        self.line()
        self.line(f"class {vt.ident}(enum.IntEnum):")
        self.line(f'"""Enumeration ``{vt.key}``.', 1)
        self.line()
        self.line("Decoding keeps values the enumeration does not list (see", 1)
        self.line(':func:`xtce.packet.enum_value`)."""', 1)
        self.line()
        for lab in vt.labels:
            self.line(f"{lab.ident} = {lab.value}", 1)
        self.line()
        self.line("@property", 1)
        self.line("def label(self) -> Optional[str]:", 1)
        self.line('"""The label of this value, if the enumeration lists it."""', 2)
        self.line(f"return pk.find_label(int(self), _LABELS_{vt.ident})", 2)
        self.line()
        self.line()
        labels = ", ".join(f"({pstr(lab.label)}, {lab.value}, {lab.max_value})" for lab in vt.labels)
        self.line(f"_LABELS_{vt.ident} = ({labels},)")
        self.line()
        self.line()
        self.line(f"def _DESCRIBE_{vt.ident}(v) -> str:")
        self.line(f"return pk.describe_enum(v, _LABELS_{vt.ident})", 1)

    def aggregate_type(self, vt: VType) -> None:
        self.line()
        self.line()
        self.line("@dataclass(slots=True)")
        self.line(f"class {vt.ident}:")
        self.line(f'"""Aggregate ``{vt.key}``; its members are encoded in this order."""', 1)
        self.line()
        for m in vt.enc.members:
            self.line(f"{m.ident}: {field_type(m.vtype)} = {default(m.vtype)}", 1)
            self.line(f'"""``{m.name}`` ({m.vtype.key})"""', 1)

    def read_items(self, items, depth: int, where: str, nested: bool = False) -> None:
        for it in items:
            if isinstance(it, Seek):
                if it.from_start is None:
                    self.line(f"r.skip({it.offset}, {pstr(where)})", depth)
                else:
                    self.line(f"r.seek({self.mark(it.from_start)}, {it.offset}, {pstr(where)})", depth)
            elif isinstance(it, Mark):
                self.line(f"m{it.id} = r.pos", depth)
            elif isinstance(it, Value):
                lc = it.local
                self.line(f"v{lc.id} = {read_expr(lc.vtype, pstr(lc.name), lambda r: f'v{r.id}')}", depth)
            elif isinstance(it, Cond):
                if not nested:
                    for v in walk(it.items):
                        if isinstance(v, Value):
                            self.line(f"v{v.local.id} = None", depth)
                cond = " and ".join(f"({condition(c, f'v{c.local.id}')})" for c in it.criteria)
                self.line(f"if {cond}:", depth)
                self.read_items(it.items, depth + 1, where, True)
            else:
                self.line(f"r.uint({it.bits}, {pstr(where)})", depth)

    def write_items(self, n: Node, items, depth: int, where: str, names: dict) -> None:
        for it in items:
            if isinstance(it, Seek):
                if it.from_start is None:
                    self.line(f"w.skip({it.offset}, {pstr(where)})", depth)
                else:
                    self.line(f"w.seek({self.mark(it.from_start)}, {it.offset}, {pstr(where)})", depth)
            elif isinstance(it, Mark):
                self.line(f"m{it.id} = w.pos", depth)
            elif isinstance(it, Fixed):
                self.line(f"w.uint({it.bits}, 0x{it.value:x})", depth)
            elif isinstance(it, Cond):
                cond = " and ".join(f"({condition(c, self.place(n, c.local, names))})" for c in it.criteria)
                self.line(f"if {cond}:", depth)
                self.write_items(n, it.items, depth + 1, where, names)
            else:
                lc = it.local
                x = self.place(n, lc, names)
                if lc.optional:
                    self.line(f"if {x} is None:", depth)
                    self.line(f'raise pk.PacketError("Missing", {pstr(lc.name)})', depth + 1)
                self.write_value(lc.vtype, x, pstr(lc.name), lambda r: self.place(n, r, names), depth)

    def write_value(self, vt: VType, x: str, f: str, ref, depth: int) -> None:
        e = vt.enc
        if isinstance(e, AggregateEnc):
            for m in e.members:
                self.write_value(m.vtype, f"{x}.{m.ident}", f, ref, depth)
        elif isinstance(e, ArrayEnc):
            # Loop variables are numbered by depth: an aggregate element may
            # hold another array.
            i, el = f"_i{depth}", f"_e{depth}"
            self.line(f"pk.check_size({x}, {count(vt, ref, f)}, {f})", depth)
            self.line(f"for {i}, {el} in enumerate({x}):", depth)
            if e.gap:
                self.line(f"if {i}:", depth + 1)
                self.line(f"w.skip({e.gap}, {f})", depth + 2)
            self.write_value(e.elem, el, f, ref, depth + 1)
        elif isinstance(e, BinaryEnc) and e.dyn is not None:
            self.line(f"pk.check_size({x}, {size(e.dyn, ref(e.dyn.local), f)}, {f})", depth)
            self.line(f"w.bytes({x})", depth)
        else:
            self.line(write_stmt(vt, x, f, ref), depth)

    @staticmethod
    def place(n: Node, lc, names: dict) -> str:
        return f"self.{names[lc]}" if lc in names else literal(lc.vtype, n.fixed[lc])

    @staticmethod
    def mark(i: int) -> str:
        return "0" if i == 0 else f"m{i}"

    @staticmethod
    def construct(n: Node) -> str:
        return f"{n.ident}(" + ", ".join(f"{name}=v{lc.id}" for name, lc in n.fields) + ")"

    def node(self, n: Node) -> None:
        what = "Sequence container" if n.kind == "tm" else "Meta-command"
        self.line()
        self.line()
        self.line("@dataclass(slots=True)")
        self.line(f"class {n.ident}(pk.Packet):")
        self.line(f'"""{what} ``{n.key}``.', 1)
        if n.fixed:
            self.line()
            fixed = ", ".join(f"``{lc.name}`` = {v!r}" for lc, v in n.fixed.items())
            self.line(f"Fixed by its inheritance: {fixed}.", 1)
        self.line('"""', 1)
        self.line()
        self.line(f"NAME: ClassVar[str] = {pstr(n.key)}", 1)
        self.line(f"MAX_BYTES: ClassVar[int] = {n.max_bytes}", 1)
        if n.fields:
            self.line()
        for name, lc in n.fields:
            if lc.optional:
                self.line(f"{name}: Optional[{field_type(lc.vtype)}] = None", 1)
            else:
                self.line(f"{name}: {field_type(lc.vtype)} = {default(lc.vtype)}", 1)
            doc = f"; {lc.vtype.doc}" if lc.vtype.doc else ""
            cond = "; present only when its include condition holds" if lc.optional else ""
            self.line(f'"""``{lc.key}`` ({lc.vtype.key}{doc}{cond})"""', 1)
        self.line()
        self.line("@classmethod", 1)
        self.line(f"def decode(cls, data) -> {n.ident}:", 1)
        self.line('"""Decode a packet that starts at the first byte of ``data``."""', 2)
        self.line("r = pk.BitReader(data)", 2)
        for node in n.chain():
            for c in node.criteria:
                self.line(f"if not ({condition(c, f'v{c.local.id}')}):", 2)
                self.line(f'raise pk.PacketError("Mismatch", {pstr(node.key)}, {pstr(c.text)})', 3)
            self.read_items(node.items, 2, node.key)
        self.line(f"return {self.construct(n)}", 2)
        self.line()
        self.line("def encode_into(self, w: pk.BitWriter) -> None:", 1)
        names = {lc: name for name, lc in n.fields}
        for node in n.chain():
            for c in node.criteria:
                if c.local in names:
                    self.line(f"if not ({condition(c, 'self.' + names[c.local])}):", 2)
                    self.line(f'raise pk.PacketError("Mismatch", {pstr(node.key)}, {pstr(c.text)})', 3)
        for node in n.chain():
            self.write_items(n, node.items, 2, node.key, names)
        self.line()
        self.line("def describe(self) -> str:", 1)
        self.line('"""Canonical text form, identical in every generated language."""', 2)
        parts = []
        for name, lc in n.fields:
            fn = describer(lc.vtype)
            v = f"self.{name}"
            if lc.optional:
                parts.append(f"pk.describe_optional({v}, {fn})")
            else:
                parts.append(f"({fn})({v})" if fn.startswith("lambda") else f"{fn}({v})")
        if not parts:
            self.line(f"return {pstr(n.ident + '{}')}", 2)
            return
        self.line(f"return {pstr(n.ident + '{')} + \", \".join((", 2)
        for (name, _), p in zip(n.fields, parts):
            self.line(f"{pstr(name + '=')} + {p},", 3)
        self.line(')) + "}"', 2)

    def kind(self, k: Kind) -> None:
        self.line()
        self.line()
        self.line(f"class {k.ident}(pk.PacketKind):")
        self.line(f'"""Every concrete packet derived from ``{k.root.key}``."""', 1)
        self.line()
        self.line(f"NAME: ClassVar[str] = {pstr(k.root.key)}", 1)
        self.line(f"MAX_BYTES: ClassVar[int] = {k.max_bytes}", 1)
        members = ", ".join(m.ident for m in k.members)
        self.line(f"MEMBERS: ClassVar[tuple] = ({members},)", 1)
        self.line()
        self.line("@staticmethod", 1)
        self.line(f"def decode(data) -> Union[{members}]:", 1)
        self.line('"""Decode the root\'s entries, then pick the first child (in', 2)
        self.line('document order) whose criteria match, recursively."""', 2)
        self.line("r = pk.BitReader(data)", 2)
        self.read_items(k.root.items, 2, k.root.key)
        self.dispatch(k.root, 2)

    def dispatch(self, n: Node, depth: int) -> None:
        for c in n.children:
            if c.error:
                continue
            cond = " and ".join(f"({condition(x, f'v{x.local.id}')})" for x in c.criteria) or "True"
            self.line(f"if {cond}:", depth)
            self.read_items(c.items, depth + 1, c.key)
            self.dispatch(c, depth + 1)
        if n.abstract:
            self.line(f'raise pk.PacketError("NoMatch", {pstr(n.key)})', depth)
        else:
            self.line(f"return {self.construct(n)}", depth)


def emit(mission: Mission, source: str) -> str:
    return PythonEmitter(mission, source).emit()
