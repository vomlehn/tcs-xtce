"""Emit a Rust module of packet codecs (uses ``xtce::packet``)."""

from __future__ import annotations

import json

from .common import flt, header_lines, int_enc, invertible, le, str_cap, string_spec
from .model import term_indexes, term_locals, walk, Index, AggregateEnc, AllOf, AnyOf, ArrayEnc, BinaryEnc, Cond, Criterion, DynSize, Fixed, FloatEnc, IntEnc, Kind, Local, Mark, Mission, Node, Pad, Polynomial, Seek, Spline, StringEnc, Value, VType


def rstr(s: str) -> str:
    out = []
    for c in s:
        if c in '"\\':
            out.append("\\" + c)
        elif ord(c) < 0x20 or ord(c) == 0x7F:
            out.append(f"\\u{{{ord(c):x}}}")
        else:
            out.append(c)
    return '"' + "".join(out) + '"'


def field_type(vt: VType, alloc: bool, optional: bool = False) -> str:
    """The Rust type of a value; without ``alloc``, arrays, strings and
    binary values have fixed capacities, so they need no heap."""
    if optional:
        return f"Option<{field_type(vt, alloc)}>"
    k = vt.kind
    if k == "array":
        elem = field_type(vt.elem, alloc)
        return f"Vec<{elem}>" if alloc else f"pk::FixedVec<{elem}, {vt.enc.max_count}>"
    if k == "aggregate":
        return vt.ident
    if k == "int":
        return f"{'i' if vt.signed else 'u'}{vt.width}"
    if k == "string":
        return "String" if alloc else f"pk::FixedString<{str_cap(vt)}>"
    if k == "binary":
        return "Vec<u8>" if alloc else f"pk::FixedBytes<{vt.enc.bits // 8}>"
    return {"float": "f64", "bool": "bool"}.get(k) or vt.ident


def literal(vt: VType, v) -> str:
    k = vt.kind
    if k == "int":
        return str(v)
    if k == "float":
        return flt(v) + "_f64"
    if k == "enum":
        return f"{vt.ident}({v})"
    if k == "bool":
        return "true" if v else "false"
    if k == "string":
        return rstr(v)
    return "&[" + ", ".join(f"0x{b:02x}" for b in v) + "][..]"


def points(cal: Spline) -> str:
    return "&[" + ", ".join(f"({flt(a)}, {flt(b)})" for a, b in cal.points) + "]"


def calibrate(vt: VType, x: str, f: str) -> str:
    cal = vt.calibrator
    if cal is None:
        return x
    if isinstance(cal, Polynomial):
        terms = ", ".join(f"({flt(c)}, {e})" for c, e in cal.terms)
        return f"pk::polynomial({x}, &[{terms}])"
    return f"pk::spline({x}, {points(cal)}, {cal.order}, {str(cal.extrapolate).lower()}, {f})?"


def uncalibrate(vt: VType, y: str, f: str) -> str:
    cal = vt.calibrator
    if cal is None:
        return y
    if isinstance(cal, Polynomial):
        return f"pk::polynomial_inverse({y}, {flt(cal.intercept)}, {flt(cal.slope)})"
    return f"pk::spline_inverse({y}, {points(cal)}, {str(cal.extrapolate).lower()}, {f})?"


def spec(vt: VType, ref, f: str) -> str:
    """Expression for the StringSpec of a string type; ``ref`` gives the
    expression for the value that gives a dynamic size."""
    fixed, mx, term, tag = string_spec(vt)
    fixed = f"Some({fixed})" if fixed is not None else "None"
    term = f"Some({term})" if term is not None else "None"
    s = f"StringSpec {{ fixed_bits: {fixed}, max_bits: {mx}, terminator: {term}, size_tag_bits: {tag} }}"
    d = vt.enc.dyn
    return f"&({s}).sized({size(d, ref(d), f)})" if d is not None else "&" + s


def source(d: DynSize, place) -> str:
    """Expression for the value a bound DynSize adjusts: a field, or the
    first matching value of a DiscreteLookupList.  ``place`` gives the
    expression for a Local."""
    if d.lookup is None:
        return place(d.local) + access(d.path)
    x = f"{d.default}i64"
    for criteria, v in reversed(d.lookup):
        x = f"if {conjunction([term(c, place) for c in criteria])} {{ {v}i64 }} else {{ {x} }}"
    return f"({x})"


def offset(s: Seek, ref, f: str) -> str:
    """Expression for the offset of a Seek."""
    if isinstance(s.offset, int):
        return str(s.offset)
    n = size(s.offset, ref(s.offset), f)
    return f"({n}) as i64" if s.offset.factor != 1 else f"{n} as i64"


def size(d: DynSize, x: str, f: str) -> str:
    """Expression for a size or count that the value ``x`` gives."""
    n = f"pk::dynamic_size({x} as f64, {flt(d.slope)}, {flt(d.intercept)}, {d.unit}, {d.max}, {f})?"
    return f"{n} * {d.factor}" if d.factor != 1 else n


def count(vt: VType, ref, f: str) -> str:
    c = vt.enc.count
    return size(c, ref(c), f) if isinstance(c, DynSize) else f"{c}usize"


def member_sizes(e: AggregateEnc) -> set:
    """The members of an aggregate that give the size of another."""
    return {m.vtype.dyn.member for m in e.members if m.vtype.dyn is not None}


def read_expr(vt: VType, f: str, ref, alloc: bool, depth: int = 0) -> str:
    """Expression reading one value with BitReader ``r``; ``ref`` gives the
    expression for the value that gives a size or count (a DynSize).
    ``alloc`` is whether it may allocate.  ``depth`` numbers the locals of
    nested aggregates."""
    e = vt.enc
    if isinstance(e, AggregateEnc):
        a = f"a{depth}_"
        inner = lambda d: a + d.member if d.member else ref(d)  # noqa: E731
        reads = []
        for m in e.members:
            x = read_expr(m.vtype, f, inner, alloc, depth + 1)
            reads.append((m.ident, f"{{ r.pad({m.skip}, {f})?; {x} }}" if m.skip else x))
        if member_sizes(e):
            # Read members into locals, in order, so later ones can use them.
            lets = " ".join(f"let {a}{i} = {x};" for i, x in reads)
            pad = f" r.pad({e.pad}, {f})?;" if e.pad else ""
            fields = ", ".join(f"{i}: {a}{i}" for i, _ in reads)
            return f"{{ {lets}{pad} {vt.ident} {{ {fields} }} }}"
        agg = f"{vt.ident} {{ {', '.join(f'{i}: {x}' for i, x in reads)} }}"
        return f"{{ let a = {agg}; r.pad({e.pad}, {f})?; a }}" if e.pad else agg
    if isinstance(e, ArrayEnc):
        gap = f"if i > 0 {{ r.skip({e.gap}, {f})?; }}" if e.gap else ""
        elem = read_expr(e.elem, f, ref, alloc, depth + 1)
        if alloc:
            return f"{{ let n = {count(vt, ref, f)}; let mut a = Vec::with_capacity(n); for i in 0..n {{ {gap} a.push({elem}); }} a }}"
        full = f"return Err(PacketError::BadSize {{ field: {f} }})"
        return (
            f"{{ let n = {count(vt, ref, f)}; let mut a = pk::FixedVec::<{field_type(e.elem, alloc)}, {e.max_count}>::new(); "
            f"for i in 0..n {{ {gap} if a.push({elem}).is_err() {{ {full} }} }} a }}"
        )
    if isinstance(e, IntEnc):
        raw = f"r.uint_order({e.bits}, {str(le(vt)).lower()}, {f})?"
        enc = f"IntEncoding::{int_enc(vt)}"
        if vt.kind == "int" and vt.calibrator is not None:
            y = calibrate(vt, f"pk::int_to_f64({raw}, {e.bits}, {enc})", f)
            lo, lim = vt.eng_limits
            return f"pk::round_to_int({y}, {flt(lo)}, {flt(lim)}, {f})? as {field_type(vt, alloc)}"
        if vt.kind == "int":
            if vt.signed:
                return f"pk::to_signed({raw}, {e.bits}, {enc}) as i{vt.width}"
            return f"{raw} as u{vt.width}"
        if vt.kind == "enum":
            if vt.signed:
                return f"{vt.ident}(pk::to_signed({raw}, {e.bits}, {enc}))"
            return f"{vt.ident}({raw} as i64)"
        if vt.kind == "bool":
            return f"{raw} != 0"
        return calibrate(vt, f"pk::int_to_f64({raw}, {e.bits}, {enc})", f)
    if isinstance(e, FloatEnc):
        raw = f"r.uint_order({e.bits}, {str(le(vt)).lower()}, {f})?"
        return calibrate(vt, f"pk::float_from_raw({raw}, {e.bits})", f)
    if isinstance(e, BinaryEnc):
        read = "bytes" if alloc else f"fixed_bytes::<{e.bits // 8}>"
        if e.rest:
            return f"{{ let n = r.rest_bytes({e.bits // 8}, {f})?; r.{read}(n, {f})? }}"
        n = size(e.dyn, ref(e.dyn), f) if e.dyn else e.bits // 8
        b = f"r.{read}({n}, {f})?"
        if vt.kind != "string":
            return b
        return f"pk::{'' if alloc else 'fixed_'}string_from_bytes({b}, {f})?"
    read = "read_string" if alloc else f"read_fixed_string::<{str_cap(vt)}>"
    s = f"pk::{read}(&mut r, {spec(vt, ref, f)}, {f})?"
    if vt.kind == "bool":
        bad = f"return Err(PacketError::OutOfRange {{ field: {f} }})"
        return f"match {s}.as_str() {{ {rstr(vt.one_string)} => true, {rstr(vt.zero_string)} => false, _ => {bad} }}"
    return s


def write_stmt(vt: VType, x: str, f: str, ref) -> str:
    """Statement writing value ``x`` (a place or literal) with BitWriter ``w``;
    ``ref`` gives the expression for a value that gives a size or count."""
    e = vt.enc
    bad = f"return Err(PacketError::BadSize {{ field: {f} }});"
    if isinstance(e, AggregateEnc):
        inner = lambda d: f"{x}.{d.member}" if d.member else ref(d)  # noqa: E731
        parts = []
        for m in e.members:
            if m.skip:
                parts.append(f"w.zeros({m.skip});")
            parts.append(write_stmt(m.vtype, f"{x}.{m.ident}", f, inner))
        if e.pad:
            parts.append(f"w.zeros({e.pad});")
        return "{ " + " ".join(parts) + " }"
    if isinstance(e, ArrayEnc):
        gap = f"if i > 0 {{ w.skip({e.gap}, {f})?; }}" if e.gap else ""
        return (
            f"{{ let n = {count(vt, ref, f)}; if {x}.len() != n {{ {bad} }} "
            f"for (i, {elem_pattern(e.elem)}) in {x}.iter().enumerate() {{ {gap} {write_stmt(e.elem, 'e', f, ref)} }} }}"
        )
    data = f"{x}.as_bytes()" if vt.kind == "string" else f"&{x}"
    if isinstance(e, BinaryEnc) and e.dyn is not None:
        n = size(e.dyn, ref(e.dyn), f)
        return f"{{ let n = {n}; if {x}.len() != n {{ {bad} }} w.bytes({data}); }}"
    if isinstance(e, IntEnc):
        bits, order = e.bits, str(le(vt)).lower()
        enc = f"IntEncoding::{int_enc(vt)}"
        if vt.kind == "int" and vt.calibrator is not None:
            if not invertible(vt):
                return f"return Err(PacketError::NotInvertible {{ field: {f} }});"
            raw = f"pk::f64_to_int({uncalibrate(vt, f'{x} as f64', f)}, {bits}, {enc}, {f})?"
        elif vt.kind == "int":
            raw = f"pk::from_signed({x} as i64, {bits}, {enc}, {f})?" if vt.signed else f"pk::from_unsigned({x} as u64, {bits}, {f})?"
        elif vt.kind == "enum":
            raw = f"pk::from_signed({x}.0, {bits}, {enc}, {f})?"
        elif vt.kind == "bool":
            raw = f"{x} as u64"
        else:
            if not invertible(vt):
                return f"return Err(PacketError::NotInvertible {{ field: {f} }});"
            raw = f"pk::f64_to_int({uncalibrate(vt, x, f)}, {bits}, {enc}, {f})?"
        return f"w.uint_order({bits}, {order}, {raw});"
    if isinstance(e, FloatEnc):
        if not invertible(vt):
            return f"return Err(PacketError::NotInvertible {{ field: {f} }});"
        return f"w.uint_order({e.bits}, {str(le(vt)).lower()}, pk::float_to_raw({uncalibrate(vt, x, f)}, {e.bits}));"
    if isinstance(e, BinaryEnc) and e.rest:
        return f"pk::write_rest(w, &{x}, {e.bits // 8}, {f})?;"
    if isinstance(e, BinaryEnc):
        return f"pk::write_binary(w, {data}, {e.bits // 8}, {f})?;"
    if vt.kind == "bool":
        s = f"if {x} {{ {rstr(vt.one_string)} }} else {{ {rstr(vt.zero_string)} }}"
        return f"pk::write_string(w, {spec(vt, ref, f)}, {s}, {f})?;"
    return f"pk::write_string(w, {spec(vt, ref, f)}, &{x}, {f})?;"


def elem_pattern(vt: VType) -> str:
    """Pattern binding ``e`` to an element of a slice: by value when it is
    Copy, so that ``e`` can be used like a field."""
    return "e" if vt.kind in ("string", "binary", "aggregate") else "&e"


def describe(vt: VType, x: str) -> str:
    """Expression writing the canonical text of ``x`` to the formatter
    ``f``, without allocating."""
    k = vt.kind
    if k == "aggregate":
        parts = []
        for i, m in enumerate(vt.enc.members):
            parts.append(f"f.write_str({rstr((', ' if i else '') + m.ident + '=')})?;")
            parts.append(describe(m.vtype, f"({x}).{m.ident}") + ";")
        return "{ f.write_char('{')?; " + " ".join(parts) + " f.write_char('}')? }"
    if k == "array":
        el = describe(vt.elem, "e")
        pat = elem_pattern(vt.elem)
        return f"{{ f.write_char('[')?; for (i, {pat}) in ({x}).iter().enumerate() {{ if i > 0 {{ f.write_str(\", \")?; }} {el}; }} f.write_char(']')? }}"
    if k == "float":
        return f"pk::describe_f64({x}, f)?"
    if k == "string":
        return f"pk::describe_str(&{x}, f)?"
    if k == "binary":
        return f"pk::describe_bytes(&{x}, f)?"
    return f"write!(f, \"{{}}\", {x})?"


def conjunction(conds: list) -> str:
    if not conds:
        return "true"
    return conds[0] if len(conds) == 1 else " && ".join(f"({c})" for c in conds)


def access(path) -> str:
    """The expression suffix for a member and element path."""
    return "".join(f"[{p.i}]" if isinstance(p, Index) else "." + p for p in path)


def term(t, place) -> str:
    """Expression for a criterion or a tree of them; ``place`` gives the
    expression for a Local."""
    if isinstance(t, (AllOf, AnyOf)):
        return (" && " if isinstance(t, AllOf) else " || ").join(f"({term(x, place)})" for x in t.terms)
    return condition(t, place(t.local), place(t.other) if t.other is not None else None)


def condition(c: Criterion, x: str, y=None) -> str:
    """``x`` is the expression for the criterion's local, and ``y`` for
    the value it compares with, if any."""
    vt = c.vtype
    x += access(c.path)
    if y is not None:
        y += access(c.other_path)
        if vt.kind == "int":
            return f"({x} as i128) {c.op} ({y} as i128)"
        if vt.kind == "enum":
            return f"{x}.0 {c.op} {y}.0"
        return f"{x} {c.op} {y}"
    if vt.kind == "int":
        return f"({x} as i128) {c.op} {c.value}"
    if vt.kind == "enum":
        return f"{x}.0 {c.op} {c.value}"
    if vt.kind == "binary":
        return f"{x}[..] {c.op} *{literal(vt, c.value)}"
    return f"{x} {c.op} {literal(vt, c.value)}"


class RustEmitter:
    def __init__(self, mission: Mission, source: str, alloc: bool):
        self.m = mission
        self.source = source
        self.alloc = alloc
        self.out: list[str] = []

    def line(self, s: str = "", depth: int = 0) -> None:
        self.out.append("    " * depth + s if s else "")

    def emit(self) -> str:
        m = self.m
        for h in header_lines(m, self.source):
            self.line("// " + h)
        self.line(f"//! Packet codecs for the `{m.name}` mission.")
        if not self.alloc:
            self.line("//!")
            self.line("//! Generated with `--dynamic-allocations false`: arrays, strings and binary")
            self.line("//! fields have fixed capacities, and decoding, encoding and describing a")
            self.line("//! packet never allocate.")
        self.line("#![allow(dead_code, unused_imports, unused_variables, unreachable_code, clippy::all)]")
        self.line()
        self.line("use std::fmt::{self, Write as _};")
        self.line("use xtce::packet::{self as pk, BitReader, BitWriter, IntEncoding, Packet, PacketError, Result, StringSpec};")
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
        self.line(f"/// Enumeration `{vt.key}`; holds the raw value so unlisted values survive decoding.")
        self.line("#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default)]")
        self.line(f"pub struct {vt.ident}(pub i64);")
        self.line()
        self.line(f"impl {vt.ident} {{")
        for lab in vt.labels:
            self.line(f"pub const {lab.ident}: Self = Self({lab.value});", 1)
        self.line()
        self.line("/// The label of this value, if the enumeration lists it.", 1)
        self.line("pub fn label(self) -> Option<&'static str> {", 1)
        self.line("match self.0 {", 2)
        for lab in vt.labels:
            pat = str(lab.value) if lab.value == lab.max_value else f"{lab.value}..={lab.max_value}"
            self.line(f"{pat} => Some({rstr(lab.label)}),", 3)
        self.line("_ => None,", 3)
        self.line("}", 2)
        self.line("}", 1)
        self.line("}")
        self.line()
        self.line(f"impl fmt::Display for {vt.ident} {{")
        self.line("fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {", 1)
        self.line("match self.label() {", 2)
        self.line("Some(l) => f.write_str(l),", 3)
        self.line("None => write!(f, \"{}\", self.0),", 3)
        self.line("}", 2)
        self.line("}", 1)
        self.line("}")

    def aggregate_type(self, vt: VType) -> None:
        self.line()
        self.line(f"/// Aggregate `{vt.key}`; its members are encoded in this order.")
        self.line("#[derive(Debug, Clone, PartialEq, Default)]")
        self.line(f"pub struct {vt.ident} {{")
        for m in vt.enc.members:
            self.line(f"/// `{m.name}` ({m.vtype.key})", 1)
            self.line(f"pub {m.ident}: {field_type(m.vtype, self.alloc)},", 1)
        self.line("}")

    # -- decoding --

    def read_items(self, items, depth: int, where: str, assign: bool = False) -> None:
        for it in items:
            if isinstance(it, Seek):
                off = offset(it, self.read_ref, rstr(where))
                if it.from_start is None:
                    self.line(f"r.skip({off}, {rstr(where)})?;", depth)
                else:
                    self.line(f"r.seek({self.mark(it.from_start)}, {off}, {rstr(where)})?;", depth)
            elif isinstance(it, Mark):
                self.line(f"let m{it.id} = r.position();", depth)
            elif isinstance(it, Pad):
                self.line(f"r.pad({it.bits}, {rstr(where)})?;", depth)
            elif isinstance(it, Value):
                lc = it.local
                expr = read_expr(lc.vtype, rstr(lc.name), self.read_ref, self.alloc)
                if assign:
                    self.line(f"v{lc.id} = Some({expr});", depth)
                else:
                    self.line(f"let v{lc.id} = {expr};", depth)
                if lc.length is not None:
                    self.line(f"r.limit(v{lc.id} as u64, {lc.length}, {rstr(lc.name)})?;", depth)
            elif isinstance(it, Cond):
                if not assign:
                    for v in walk(it.items):
                        if isinstance(v, Value):
                            self.line(f"let mut v{v.local.id} = None;", depth)
                self.line(f"if {conjunction([term(c, lambda lc: f'v{lc.id}') for c in it.criteria])} {{", depth)
                self.read_items(it.items, depth + 1, where, True)
                self.line("}", depth)
            else:
                self.line(f"r.uint({it.bits}, {rstr(where)})?;", depth)

    def write_items(self, n: Node, items, depth: int, where: str, names: dict) -> None:
        for it in items:
            if isinstance(it, Seek):
                off = offset(it, self.write_ref(n, names), rstr(where))
                if it.from_start is None:
                    self.line(f"w.skip({off}, {rstr(where)})?;", depth)
                else:
                    self.line(f"w.seek({self.mark(it.from_start)}, {off}, {rstr(where)})?;", depth)
            elif isinstance(it, Mark):
                self.line(f"let m{it.id} = w.position();", depth)
            elif isinstance(it, Fixed):
                self.line(f"w.uint({it.bits}, 0x{it.value:x});", depth)
            elif isinstance(it, Pad):
                self.line(f"w.zeros({it.bits});", depth)
            elif isinstance(it, Cond):
                conds = [term(c, lambda lc: self.place(n, lc, names)) for c in it.criteria]
                self.line(f"if {conjunction(conds)} {{", depth)
                self.write_items(n, it.items, depth + 1, where, names)
                self.line("}", depth)
            elif it.local.length is not None:
                lc = it.local
                self.line(f"let l{lc.id} = w.position();", depth)
                self.line(f"w.uint({lc.vtype.enc.bits}, 0);", depth)
            else:
                lc = it.local
                f = rstr(lc.name)
                if lc.optional:
                    self.line(
                        f"let o{lc.id} = self.{names[lc]}.as_ref().ok_or(PacketError::Missing {{ field: {f} }})?;",
                        depth,
                    )
                    x = f"o{lc.id}" if lc.vtype.kind in ("string", "binary", "array", "aggregate") else f"*o{lc.id}"
                else:
                    x = self.place(n, lc, names)
                self.line(write_stmt(lc.vtype, x, f, self.write_ref(n, names)), depth)

    @staticmethod
    def read_ref(d: DynSize) -> str:
        """The value a size or location uses, when decoding."""
        return source(d, lambda lc: f"v{lc.id}")

    def write_ref(self, n: Node, names: dict):
        """The value a size or location uses, when encoding."""
        return lambda d: source(d, lambda lc: self.place(n, lc, names))

    @staticmethod
    def place(n: Node, lc, names: dict) -> str:
        """Expression for a value when encoding: a field or a fixed constant."""
        if lc in names:
            return f"self.{names[lc]}"
        x = literal(lc.vtype, n.fixed[lc])
        return f"({x})" if lc.vtype.kind == "binary" else x

    @staticmethod
    def mark(i: int) -> str:
        return "0" if i == 0 else f"m{i}"

    def finish(self, n: Node, depth: int) -> None:
        """Checks the packet length field when decoding ends."""
        lc = n.length_field()
        if lc is not None:
            self.line(f"r.finish({rstr(lc.name)})?;", depth)

    def construct(self, n: Node) -> str:
        fields = ", ".join(f"{name}: v{lc.id}" for name, lc in n.fields)
        return f"{n.ident} {{ {fields} }}"

    def node(self, n: Node) -> None:
        what = "Sequence container" if n.kind == "tm" else "Meta-command"
        self.line()
        self.line(f"/// {what} `{n.key}`.")
        fixed = [f"`{lc.name}` = {v!r}" for lc, v in n.fixed.items()]
        if fixed:
            self.line("///")
            self.line(f"/// Fixed by its inheritance: {', '.join(fixed)}.")
        length = n.length_field()
        if length is not None:
            self.line("///")
            self.line(f"/// `{length.name}` is the packet length: computed when encoding, checked when decoding.")
        self.line("#[derive(Debug, Clone, PartialEq, Default)]")
        self.line(f"pub struct {n.ident} {{")
        for name, lc in n.fields:
            doc = f"; {lc.vtype.doc}" if lc.vtype.doc else ""
            cond = "; present only when its include condition holds" if lc.optional else ""
            self.line(f"/// `{lc.key}` ({lc.vtype.key}{doc}{cond})", 1)
            self.line(f"pub {name}: {field_type(lc.vtype, self.alloc, lc.optional)},", 1)
        self.line("}")
        self.line()
        self.line(f"impl Packet for {n.ident} {{")
        self.line(f"const NAME: &'static str = {rstr(n.key)};", 1)
        self.line(f"const MAX_BYTES: usize = {n.max_bytes};", 1)
        self.line()
        self.line("fn decode(data: &[u8]) -> Result<Self> {", 1)
        self.line("let mut r = BitReader::new(data);", 2)
        for i, node in enumerate(n.chain()):
            for c in node.criteria:
                self.line(f"if !({term(c, lambda lc: f'v{lc.id}')}) {{", 2)
                self.line(f"return Err(PacketError::Mismatch {{ container: {rstr(node.key)}, criterion: {rstr(c.text)} }});", 3)
                self.line("}", 2)
            self.read_items(node.items, 2, node.key)
        self.finish(n, 2)
        self.line(f"Ok({self.construct(n)})", 2)
        self.line("}", 1)
        self.line()
        self.line("fn encode_into(&self, w: &mut BitWriter<'_>) -> Result<()> {", 1)
        names = {lc: name for name, lc in n.fields}
        guards = {}
        for node in n.chain():
            for c in node.criteria:
                for lc, prefix, count in term_indexes(c):
                    if lc in names:
                        guards[(f"self.{names[lc]}{access(prefix)}", count)] = lc.name
        for (x, count), name in guards.items():
            # Criteria are checked before the arrays they index are written.
            self.line(f"if {x}.len() != {count} {{", 2)
            self.line(f"return Err(PacketError::BadSize {{ field: {rstr(name)} }});", 3)
            self.line("}", 2)
        for node in n.chain():
            for c in node.criteria:
                if any(lc in names for lc in term_locals(c)):
                    self.line(f"if !({term(c, lambda lc: self.place(n, lc, names))}) {{", 2)
                    self.line(f"return Err(PacketError::Mismatch {{ container: {rstr(node.key)}, criterion: {rstr(c.text)} }});", 3)
                    self.line("}", 2)
        for node in n.chain():
            self.write_items(n, node.items, 2, node.key, names)
        if length is not None:
            enc = length.vtype.enc
            self.line(
                f"w.patch_length(l{length.id}, {enc.bits}, {str(enc.little_endian).lower()}, {length.length}, {rstr(length.name)})?;",
                2,
            )
        self.line("Ok(())", 2)
        self.line("}", 1)
        self.line("}")
        self.line()
        self.line(f"impl fmt::Display for {n.ident} {{")
        self.line("fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {", 1)
        self.line(f"f.write_str({rstr(n.ident + '{')})?;", 2)
        for i, (name, lc) in enumerate(n.fields):
            sep = ", " if i else ""
            self.line(f"f.write_str({rstr(sep + name + '=')})?;", 2)
            if lc.optional:
                self.line(f"match &self.{name} {{", 2)
                self.line(f"Some(v) => {describe(lc.vtype, '*v')},", 3)
                self.line("None => f.write_str(pk::DESCRIBE_NONE)?,", 3)
                self.line("}", 2)
            else:
                self.line(describe(lc.vtype, f"self.{name}") + ";", 2)
        self.line("f.write_char('}')", 2)
        self.line("}", 1)
        self.line("}")

    # -- dispatch --

    def kind(self, k: Kind) -> None:
        self.line()
        self.line(f"/// Every concrete packet derived from `{k.root.key}`.")
        self.line("#[derive(Debug, Clone, PartialEq)]")
        self.line(f"pub enum {k.ident} {{")
        for m in k.members:
            self.line(f"{m.ident}({m.ident}),", 1)
        self.line("}")
        self.line()
        self.line(f"impl Packet for {k.ident} {{")
        self.line(f"const NAME: &'static str = {rstr(k.root.key)};", 1)
        self.line(f"const MAX_BYTES: usize = {k.max_bytes};", 1)
        self.line()
        self.line("/// Decodes the root's entries, then picks the first child (in", 1)
        self.line("/// document order) whose criteria match, recursively.", 1)
        self.line("fn decode(data: &[u8]) -> Result<Self> {", 1)
        self.line("let mut r = BitReader::new(data);", 2)
        self.read_items(k.root.items, 2, k.root.key)
        self.dispatch(k, k.root, 2)
        self.line("}", 1)
        self.line()
        self.line("fn encode_into(&self, w: &mut BitWriter<'_>) -> Result<()> {", 1)
        self.line("match self {", 2)
        for m in k.members:
            self.line(f"Self::{m.ident}(p) => p.encode_into(w),", 3)
        self.line("}", 2)
        self.line("}", 1)
        self.line("}")
        self.line()
        self.line(f"impl fmt::Display for {k.ident} {{")
        self.line("fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {", 1)
        self.line("match self {", 2)
        for m in k.members:
            self.line(f"Self::{m.ident}(p) => p.fmt(f),", 3)
        self.line("}", 2)
        self.line("}", 1)
        self.line("}")
        for m in k.members:
            self.line()
            self.line(f"impl From<{m.ident}> for {k.ident} {{")
            self.line(f"fn from(p: {m.ident}) -> Self {{", 1)
            self.line(f"Self::{m.ident}(p)", 2)
            self.line("}", 1)
            self.line("}")

    def dispatch(self, k: Kind, n: Node, depth: int) -> None:
        for c in n.children:
            if c.error:
                continue
            self.line(f"if {conjunction([term(x, lambda lc: f'v{lc.id}') for x in c.criteria])} {{", depth)
            self.read_items(c.items, depth + 1, c.key)
            self.dispatch(k, c, depth + 1)
            self.line("}", depth)
        if n.abstract:
            self.line(f"return Err(PacketError::NoMatch {{ container: {rstr(n.key)} }});", depth)
        else:
            self.finish(n, depth)
            self.line(f"return Ok(Self::{n.ident}({self.construct(n)}));", depth)


def emit(mission: Mission, source: str, alloc: bool = True) -> str:
    """The Rust module; without ``alloc`` its code never allocates."""
    return RustEmitter(mission, source, alloc).emit()
