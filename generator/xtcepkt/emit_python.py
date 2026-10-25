"""Emit a Python module of packet codecs (uses ``xtce.packet``)."""

from __future__ import annotations

import textwrap

from .common import (Ref, fast_order, field_range, flt, header_lines,
                     invertible, math_value, size_args, string_spec)
from .model import (term_indexes, term_locals, walk, Index, AggregateEnc, AllOf,
                    AnyOf, ArrayEnc, BinaryEnc, Cond, ContextCal, Criterion,
                    DynSize, Fixed, FloatEnc, IntEnc, Kind, Mark, MathOp,
                    Mission, Node, Pad, Polynomial, Seek, Spline, Value, VType,
                    Require, Fill, Segment, Verify)


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
    builtin = {"int": "int", "float": "float", "bool": "bool", "string": "str",
               "binary": "bytes"}
    return builtin.get(vt.kind) or vt.ident


def default(vt: VType) -> str:
    if vt.kind == "array":
        return "dataclasses.field(default_factory=list)"
    if vt.kind == "aggregate":
        return f"dataclasses.field(default_factory={vt.ident})"
    zero = {"int": "0", "float": "0.0", "bool": "False", "string": '""',
            "binary": 'b""'}
    return zero.get(vt.kind) or f"pk.enum_value({vt.ident}, 0)"


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
    return ("(" + ", ".join(f"({flt(a)}, {flt(b)})" for a, b in cal.points)
            + ",)")


def math_expr(e: tuple, x: str, place) -> str:
    """Python expression for a MathOperationCalibrator tree; ``x`` is the
    value being calibrated."""
    if e[0] == "const":
        return f"({flt(e[1])})"
    if e[0] == "raw":
        return x
    if e[0] == "value":
        r = e[1]
        return math_value(r.vtype,
                          reach(place(r.local), r.member, pstr(r.local.name)),
                          "python")
    args = [math_expr(a, x, place) for a in e[1:]]
    if e[0] in ("+", "-", "*"):
        return f"({args[0]} {e[0]} {args[1]})"
    b = args[1] if len(args) > 1 else "0.0"
    return f"pk.math_op({e[0]!r}, {args[0]}, {b})"


def apply_cal(cal, x: str, f: str, place) -> str:
    """Expression calibrating ``x`` with ``cal``; ``place`` gives the
    expression for a Local that the calibrator uses."""
    if cal is None:
        return x
    if isinstance(cal, Polynomial):
        terms = ", ".join(f"({flt(c)}, {e})" for c, e in cal.terms)
        return f"pk.polynomial({x}, ({terms},))"
    if isinstance(cal, Spline):
        if cal.orders:
            return (f"pk.spline_orders({x}, {points(cal)}, {cal.orders!r}, "
                    f"{cal.extrapolate}, {f})")
        return (f"pk.spline({x}, {points(cal)}, {cal.order}, "
                f"{cal.extrapolate}, {f})")
    if isinstance(cal, MathOp):
        return f"(lambda x: {math_expr(cal.expr, 'x', place)})({x})"
    body = choose(cal, lambda c: apply_cal(c, "x", f, place), place)
    return f"(lambda x: {body})({x})"


def invert_cal(cal, y: str, f: str, place) -> str:
    """Expression inverting ``cal`` for the engineering value ``y``; one
    that can't be inverted raises NotInvertible."""
    if cal is None:
        return y
    if not cal.invertible:
        return f"pk.not_invertible({f})"
    if isinstance(cal, Polynomial):
        return (f"pk.polynomial_inverse({y}, {flt(cal.intercept)}, "
                f"{flt(cal.slope)})")
    if isinstance(cal, Spline):
        return f"pk.spline_inverse({y}, {points(cal)}, {cal.extrapolate}, {f})"
    body = choose(cal, lambda c: invert_cal(c, "y", f, place), place)
    return f"(lambda y: {body})({y})"


def choose(cal: ContextCal, body, place) -> str:
    """A conditional expression over a context calibrator's cases:
    ``body(c)`` for the calibrator of the first case that holds, or the
    default."""
    out = ""
    for terms, c in cal.cases:
        test = " and ".join(f"({term(t, place)})" for t in terms) or "True"
        out += f"{body(c)} if {test} else "
    return f"({out}{body(cal.default)})"


def calibrate(vt: VType, x: str, f: str, place) -> str:
    return apply_cal(vt.calibrator, x, f, place)


def uncalibrate(vt: VType, y: str, f: str, place) -> str:
    return invert_cal(vt.calibrator, y, f, place)


def source(d: DynSize, place) -> str:
    """Expression for the value a bound DynSize adjusts: a field, or the
    first matching value of a DiscreteLookupList.  ``place`` gives the
    expression for a Local."""
    if d.lookup is None:
        return reach(place(d.local), d.path, pstr(d.local.name))
    x = str(d.default)
    for criteria, v in reversed(d.lookup):
        x = (f"({v} if {' and '.join(f'({term(c, place)})' for c in criteria)} "
             f"else {x})")
    return x


def offset(s: Seek, ref, f: str) -> str:
    """Expression for the offset of a Seek."""
    return (str(s.offset) if isinstance(s.offset, int)
            else size(s.offset, ref(s.offset), f))


def size(d: DynSize, x: str, f: str) -> str:
    """Expression for a size or count that the value ``x`` gives."""
    n = (f"pk.dynamic_size({x}, {flt(d.slope)}, {flt(d.intercept)}, {d.unit}, "
         f"{d.max}, {f})")
    return f"{n} * {d.factor}" if d.factor != 1 else n


def gap_expr(e: ArrayEnc, ref, f: str) -> str:
    """The bits between an array's elements."""
    return (str(e.gap) if isinstance(e.gap, int)
            else size(e.gap, ref(e.gap), f))


def count(vt: VType, ref, f: str) -> str:
    c = vt.enc.count
    return size(c, ref(c), f) if isinstance(c, DynSize) else str(c)


def spec(vt: VType, ref, f: str) -> str:
    d = vt.enc.dyn
    s = repr(string_spec(vt) + (vt.enc.charset,))
    return f"pk.sized({s}, {size(d, ref(d), f)})" if d is not None else s


def order(e) -> str:
    return repr(e.order)


def raw_read(e, f: str) -> str:
    """Expression reading the raw value of an integer or float encoding."""
    if fast_order(e):
        return f"r.uint_order({e.bits}, {e.little_endian}, {f})"
    return f"r.uint_bits({e.bits}, {e.lsb}, {order(e)}, {f})"


def raw_write(e, raw: str) -> str:
    """Statement writing the raw value of an integer or float encoding."""
    if fast_order(e):
        return f"w.uint_order({e.bits}, {e.little_endian}, {raw})"
    return f"w.uint_bits({e.bits}, {e.lsb}, {order(e)}, {raw})"


def int_encoding(e: IntEnc) -> str:
    """The runtime's name of an integer encoding: BCD is decoded to
    unsigned."""
    return repr("unsigned" if e.bcd else e.encoding)


def read_expr(vt: VType, f: str, ref, depth: int = 0) -> str:
    """Expression reading one value with BitReader ``r``; ``ref`` gives the
    expression for a value that gives a size or count."""
    e = vt.enc
    if isinstance(e, AggregateEnc):
        # Arguments and tuple items are evaluated in order.
        # Members that give the size of a later one are kept in locals.
        a = f"_a{depth}_"
        sizes = {m.vtype.dyn.member for m in e.members
                 if m.vtype.dyn is not None}
        inner = Ref(lambda d: a + d.member if d.member else ref(d), ref.place)
        parts = []
        for m in e.members:
            x = read_expr(m.vtype, f, inner, depth + 1)
            if m.ident in sizes:
                x = f"({a}{m.ident} := {x})"
            if m.skip:
                x = f"(r.pad({m.skip}, {f}), {x})[1]"
            parts.append(f"{m.ident}={x}")
        agg = f"{vt.ident}({', '.join(parts)})"
        return f"({agg}, r.pad({e.pad}, {f}))[0]" if e.pad else agg
    if isinstance(e, ArrayEnc):
        return (f"pk.read_list(r, {count(vt, ref, f)}, {gap_expr(e, ref, f)}, "
                f"{f}, lambda: {read_expr(e.elem, f, ref, depth + 1)})")
    if isinstance(e, IntEnc):
        raw = raw_read(e, f)
        if e.bcd:
            raw = (f"pk.bcd_decode({raw}, {e.bits}, "
                   f"{e.encoding == 'packedBCD'}, {f})")
        enc = int_encoding(e)
        if vt.kind == "int" and vt.calibrator is not None:
            y = calibrate(vt, f"pk.int_to_float({raw}, {e.bits}, {enc})", f,
                          ref.place)
            lo, lim = vt.eng_limits
            return f"pk.round_to_int({y}, {flt(lo)}, {flt(lim)}, {f})"
        if vt.kind == "int":
            return f"pk.to_signed({raw}, {e.bits}, {enc})" if vt.signed else raw
        if vt.kind == "enum":
            v = f"pk.to_signed({raw}, {e.bits}, {enc})" if vt.signed else raw
            return f"pk.enum_value({vt.ident}, {v})"
        if vt.kind == "bool":
            return f"{raw} != 0"
        return calibrate(vt, f"pk.int_to_float({raw}, {e.bits}, {enc})", f,
                         ref.place)
    if isinstance(e, FloatEnc):
        if e.fmt == "IEEE754" and e.bits in (32, 64):
            return calibrate(vt,
                             f"pk.float_from_raw({raw_read(e, f)}, {e.bits})",
                             f, ref.place)
        return calibrate(vt,
                         f"pk.float_decode({e.fmt!r}, {e.bits}, "
                         f"{raw_read(e, f)}, {f})", f, ref.place)
    if isinstance(e, BinaryEnc):
        if e.rest:
            return f"r.bytes(r.rest_bytes({e.bits // 8}, {f}), {f})"
        n = size(e.dyn, ref(e.dyn), f) if e.dyn else e.bits // 8
        b = f"r.bytes({n}, {f})"
        return f"pk.string_from_bytes({b}, {f})" if vt.kind == "string" else b
    s = f"pk.read_string(r, {spec(vt, ref, f)}, {f})"
    if vt.kind == "bool":
        return (f"pk.bool_from_string({s}, {pstr(vt.one_string)}, "
                f"{pstr(vt.zero_string)}, {f})")
    return s


def write_stmt(vt: VType, x: str, f: str, ref) -> str:
    """Statement writing ``x``; not for arrays or dynamic binary values,
    which ``PythonEmitter.write_value`` writes."""
    e = vt.enc
    if isinstance(e, IntEnc):
        bits, enc = e.bits, int_encoding(e)
        if vt.kind == "int" and vt.calibrator is not None:
            if not invertible(vt):
                return f'raise pk.PacketError("NotInvertible", {f})'
            y = uncalibrate(vt, f"float({x})", f, ref.place)
            raw = f"pk.float_to_int({y}, {bits}, {enc}, {f})"
        elif vt.kind == "int":
            raw = f"pk.from_signed({x}, {bits}, {enc}, {f})"
        elif vt.kind == "enum":
            raw = f"pk.from_signed(int({x}), {bits}, {enc}, {f})"
        elif vt.kind == "bool":
            raw = f"1 if {x} else 0"
        else:
            if not invertible(vt):
                return f'raise pk.PacketError("NotInvertible", {f})'
            raw = (f"pk.float_to_int({uncalibrate(vt, x, f, ref.place)}, "
                   f"{bits}, {enc}, {f})")
        if e.bcd:
            raw = (f"pk.bcd_encode({raw}, {bits}, {e.encoding == 'packedBCD'}, "
                   f"{f})")
        return raw_write(e, raw)
    if isinstance(e, FloatEnc):
        if not invertible(vt):
            return f'raise pk.PacketError("NotInvertible", {f})'
        y = uncalibrate(vt, x, f, ref.place)
        if e.fmt == "IEEE754" and e.bits in (32, 64):
            return raw_write(e, f"pk.float_to_raw({y}, {e.bits})")
        return raw_write(e, f"pk.float_encode({e.fmt!r}, {e.bits}, {y}, {f})")
    if isinstance(e, BinaryEnc) and e.rest:
        return f"pk.write_rest(w, {x}, {e.bits // 8}, {f})"
    if isinstance(e, BinaryEnc):
        data = f"{x}.encode()" if vt.kind == "string" else x
        return f"pk.write_binary(w, {data}, {e.bits // 8}, {f})"
    if vt.kind == "bool":
        s = f"({pstr(vt.one_string)} if {x} else {pstr(vt.zero_string)})"
        return f"pk.write_string(w, {spec(vt, ref, f)}, {s}, {f})"
    return f"pk.write_string(w, {spec(vt, ref, f)}, {x}, {f})"


def reach(x: str, path, f: str) -> str:
    """The expression for the member or element ``path`` of ``x``.  An
    element of an array whose count varies may be missing, which raises
    ``BadSize`` naming ``f``."""
    for p in path:
        if not isinstance(p, Index):
            x += "." + p
        elif p.count is None:
            x = f"pk.element({x}, {p.i}, {f})"
        else:
            x += f"[{p.i}]"
    return x


def term(t, place) -> str:
    """Expression for a criterion or a tree of them; ``place`` gives the
    expression for a Local."""
    if isinstance(t, (AllOf, AnyOf)):
        return (" and " if isinstance(t, AllOf)
                else " or ").join(f"({term(x, place)})" for x in t.terms)
    if t.local.optional:
        # An absent value compares as std::optional's does: less than any.
        x = place(t.local)
        return f"({EMPTY[t.op]} if {x} is None else {condition(t, x)})"
    return condition(t, place(t.local),
                     place(t.other) if t.other is not None else None)


# What ``<absent value> op <value>`` gives, as with std::optional in C++.
EMPTY = {"==": False, "!=": True, "<": True, "<=": True, ">": False,
         ">=": False}


def condition(c: Criterion, x: str, y=None) -> str:
    """``x`` is the expression for the criterion's local, and ``y`` for
    the value it compares with, if any."""
    vt = c.vtype
    x = reach(x, c.path, pstr(c.local.name))
    if y is not None:
        y = reach(y, c.other_path, pstr(c.other.name))
        return f"{x} {c.op} {y}"
    v = c.value if vt.kind in ("int", "enum") else literal(vt, c.value)
    return f"{x} {c.op} {v}"


def describer(vt: VType) -> str:
    """Expression for a function giving the canonical text of a value."""
    k = vt.kind
    if k == "array":
        return f"lambda v: pk.describe_list(v, {describer(vt.elem)})"
    if k == "aggregate":
        parts = ", ".join(f"{pstr(m.ident + '=')} + "
                          f"({describer(m.vtype)})(v.{m.ident})"
                          for m in vt.enc.members)
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
        self.dfas: list = []

    def line(self, s: str = "", depth: int = 0) -> None:
        self.out.append("    " * depth + s if s else "")

    def emit(self) -> str:
        m = self.m
        for h in header_lines(m, self.source):
            self.line("# " + h)
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
        names = ([vt.ident for vt in m.types + m.aggregates] + ["AlarmMonitor"]
                 + [n.ident for n in m.nodes] + [k.ident for k in m.kinds])
        self.line()
        self.line("__all__ = [" + ", ".join(pstr(n) for n in names) + "]")
        for vt in m.types:
            self.enum_type(vt)
        for vt in m.aggregates:
            self.aggregate_type(vt)
        self.alarm_monitor()
        for n in m.nodes:
            self.node(n)
        for k in m.kinds:
            self.kind(k)
        for name, dfa in self.dfas:
            self.line()
            self.line()
            self.line(f"# The automaton matching {dfa.pattern!r}.")
            self.line(f"{name} = (")
            self.wrapped(dfa.starts, 1)
            self.wrapped(dfa.trans, 1)
            self.wrapped([bool(a) for a in dfa.accept], 1)
            self.line(")")
        return "\n".join(self.out) + "\n"

    def wrapped(self, items: list, depth: int) -> None:
        """A tuple of ``items``, wrapped to fit 80 columns."""
        self.line("(", depth)
        line = ""
        for x in items:
            text = f"{x!r}, ".replace("True", "True").replace(" ,", ",")
            if len(line) + len(text) + 4 * (depth + 1) > 80:
                self.line(line.rstrip(), depth + 1)
                line = ""
            line += text
        if line:
            self.line(line.rstrip(), depth + 1)
        self.line("),", depth)

    def alarm_monitor(self) -> None:
        self.line()
        self.line()
        self.line("class AlarmMonitor:")
        self.line('"""The alarm state of each parameter whose alarms depend on '
                  'earlier', 1)
        self.line('samples, which the packets\' ``monitor()`` reads and '
                  'updates."""', 1)
        self.line()
        self.line("def __init__(self) -> None:", 1)
        for key, ident, history in self.m.monitored:
            self.line(f"self.{ident} = pk.AlarmState({history})  # {key}", 2)
        if not self.m.monitored:
            self.line("pass", 2)

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
        self.line('"""The label of this value, if the enumeration lists it."""',
                  2)
        self.line(f"return pk.find_label(int(self), _LABELS_{vt.ident})", 2)
        self.line()
        self.line()
        labels = ", ".join(f"({pstr(lab.label)}, {lab.value}, {lab.max_value})"
                           for lab in vt.labels)
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
        self.line(f'"""Aggregate ``{vt.key}``; its members are encoded in this '
                  'order."""', 1)
        self.line()
        for m in vt.enc.members:
            self.line(f"{m.ident}: {field_type(m.vtype)} = {default(m.vtype)}",
                      1)
            self.line(f'"""``{m.name}`` ({m.vtype.key})"""', 1)

    def read_items(self, items, depth: int, where: str,
                   nested: bool = False) -> None:
        for it in items:
            if isinstance(it, Seek):
                off = offset(it, self.read_ref, pstr(where))
                if it.from_start is None:
                    self.line(f"r.skip({off}, {pstr(where)})", depth)
                else:
                    self.line(f"r.seek({self.mark(it.from_start)}, {off}, "
                              f"{pstr(where)})", depth)
            elif isinstance(it, Mark):
                self.line(f"m{it.id} = r.pos", depth)
            elif isinstance(it, Pad):
                self.line(f"r.pad({it.bits}, {pstr(where)})", depth)
            elif isinstance(it, Require):
                self.require(it, lambda lc: f"v{lc.id}", depth)
            elif isinstance(it, Verify):
                self.verify(it.check, depth)
            elif isinstance(it, Fill):
                self.fill(it, "r", self.read_ref, depth)
            elif isinstance(it, Segment):
                lc = it.local
                self.line(f"s{lc.id} {'=' if it.first else '|='} "
                          f"r.uint({it.bits}, {pstr(lc.name)}) << {it.shift}",
                          depth)
            elif isinstance(it, Value):
                lc = it.local
                if it.assembled:
                    # Decode the value from its segments' bits.
                    self.line(f"r, r{lc.id} = (pk.segment_reader(s{lc.id}, "
                              f"{it.assembled}), r)", depth)
                if lc.checksum:
                    self.line(f"p{lc.id} = r.pos", depth)
                self.line(
                    f"v{lc.id} = "
                    f"{read_expr(lc.vtype, pstr(lc.name), self.read_ref)}",
                    depth)
                if lc.length is not None:
                    self.line(f"r.limit(v{lc.id}, {lc.length}, "
                              f"{pstr(lc.name)})", depth)
                if it.assembled:
                    self.line(f"r = r{lc.id}", depth)
            elif isinstance(it, Cond):
                if not nested:
                    for v in walk(it.items):
                        if isinstance(v, Value):
                            self.line(f"v{v.local.id} = None", depth)
                cond = " and ".join(f"({term(c, lambda lc: f'v{lc.id}')})"
                                    for c in it.criteria)
                self.line(f"if {cond}:", depth)
                self.read_items(it.items, depth + 1, where, True)
            else:
                self.line(f"r.uint({it.bits}, {pstr(where)})", depth)

    def write_items(self, n: Node, items, depth: int, where: str,
                    names: dict) -> None:
        for it in items:
            if isinstance(it, Seek):
                off = offset(it, self.write_ref(n, names), pstr(where))
                if it.from_start is None:
                    self.line(f"w.skip({off}, {pstr(where)})", depth)
                else:
                    self.line(f"w.seek({self.mark(it.from_start)}, {off}, "
                              f"{pstr(where)})", depth)
            elif isinstance(it, Mark):
                self.line(f"m{it.id} = w.pos", depth)
            elif isinstance(it, Fixed):
                self.line(f"w.uint({it.bits}, 0x{it.value:x})", depth)
            elif isinstance(it, Pad):
                self.line(f"w.zeros({it.bits})", depth)
            elif isinstance(it, Require):
                self.require(it, lambda lc: self.place(n, lc, names), depth)
            elif isinstance(it, Verify):
                self.patch(it.check, depth)
            elif isinstance(it, Fill):
                self.fill(it, "w", self.write_ref(n, names), depth)
            elif isinstance(it, Cond):
                cond = " and ".join(
                    f"({term(c, lambda lc: self.place(n, lc, names))})"
                    for c in it.criteria)
                self.line(f"if {cond}:", depth)
                self.write_items(n, it.items, depth + 1, where, names)
            elif isinstance(it, Segment):
                lc = it.local
                if it.first:
                    # Encode the whole value, then write each segment's bits.
                    self.line(f"w, w{lc.id} = pk.segment_writer(), w", depth)
                    self.write_value(lc.vtype, self.place(n, lc, names),
                                     pstr(lc.name), self.write_ref(n, names),
                                     depth)
                    self.line(f"s{lc.id}, w = pk.segment_bits(w, {it.total}), "
                              f"w{lc.id}", depth)
                self.line(f"w.uint({it.bits}, (s{lc.id} >> {it.shift}) & "
                          f"0x{(1 << it.bits) - 1:x})", depth)
            elif it.assembled:
                pass
            elif it.local.computed:
                lc = it.local
                self.line(f"l{lc.id} = w.pos", depth)
                self.line(f"w.uint({lc.vtype.enc.bits}, 0)", depth)
            else:
                lc = it.local
                x = self.place(n, lc, names)
                if lc.optional:
                    self.line(f"if {x} is None:", depth)
                    self.line('raise pk.PacketError("Missing", '
                              f'{pstr(lc.name)})', depth + 1)
                self.write_value(lc.vtype, x, pstr(lc.name),
                                 self.write_ref(n, names), depth)

    def write_value(self, vt: VType, x: str, f: str, ref, depth: int) -> None:
        e = vt.enc
        if isinstance(e, AggregateEnc):
            inner = Ref(lambda d: f"{x}.{d.member}" if d.member else ref(d),
                        ref.place)
            for m in e.members:
                if m.skip:
                    self.line(f"w.zeros({m.skip})", depth)
                self.write_value(m.vtype, f"{x}.{m.ident}", f, inner, depth)
            if e.pad:
                self.line(f"w.zeros({e.pad})", depth)
        elif isinstance(e, ArrayEnc):
            # Loop variables are numbered by depth: an aggregate element may
            # hold another array.
            i, el = f"_i{depth}", f"_e{depth}"
            self.line(f"pk.check_size({x}, {count(vt, ref, f)}, {f})", depth)
            self.line(f"for {i}, {el} in enumerate({x}):", depth)
            if e.gap:
                self.line(f"if {i}:", depth + 1)
                self.line(f"w.skip({gap_expr(e, ref, f)}, {f})", depth + 2)
            self.write_value(e.elem, el, f, ref, depth + 1)
        elif isinstance(e, BinaryEnc) and e.dyn is not None:
            data = f"{x}.encode()" if vt.kind == "string" else x
            self.line(f"pk.check_size({data}, {size(e.dyn, ref(e.dyn), f)}, "
                      f"{f})", depth)
            self.line(f"w.bytes({data})", depth)
        else:
            self.line(write_stmt(vt, x, f, ref), depth)

    # The value a size or location uses, and the expression for a Local,
    # when decoding.
    read_ref = Ref(lambda d: source(d, lambda lc: f"v{lc.id}"),
                   lambda lc: f"v{lc.id}")

    def write_ref(self, n: Node, names: dict) -> Ref:
        """The value a size or location uses, and the expression for a
        Local, when encoding."""
        place = lambda lc: self.place(n, lc, names)  # noqa: E731
        return Ref(lambda d: source(d, place), place)

    @staticmethod
    def place(n: Node, lc, names: dict) -> str:
        """Expression for a value when encoding: a field, a computed size or
        count, or a fixed constant."""
        if lc in names:
            return f"self.{names[lc]}"
        if lc in n.derived:
            return f"d{lc.id}"
        return literal(lc.vtype, n.fixed[lc])

    def fill(self, it: Fill, rw: str, ref, depth: int) -> None:
        """Pads to the end of a container with a size; raises BadSize if its
        entries go past it."""
        f = pstr(it.container)
        n = (size(it.size, ref(it.size), f) if isinstance(it.size, DynSize)
             else str(it.size))
        self.line(f"e = {self.mark(it.start)} + {n}", depth)
        self.line(f"if {rw}.pos > e:", depth)
        self.line(f'raise pk.PacketError("BadSize", {f})', depth + 1)
        if rw == "r":
            self.line(f"r.pad(e - r.pos, {f})", depth)
        else:
            self.line("w.zeros(e - w.pos)", depth)

    def require(self, it: Require, place, depth: int) -> None:
        """Raises Mismatch unless an included container's criteria hold."""
        cond = " and ".join(f"({term(t, place)})" for t in it.criteria)
        self.line(f"if not ({cond}):", depth)
        self.line(f'raise pk.PacketError("Mismatch", {pstr(it.container)}, '
                  f'{pstr(it.text)})', depth + 1)

    @staticmethod
    def mark(i: int) -> str:
        return "0" if i == 0 else f"m{i}"

    def finish(self, n: Node, depth: int) -> None:
        """Checks the packet length field and the checksums when decoding
        ends."""
        lc = n.length_field()
        if lc is not None:
            self.line(f"r.finish({pstr(lc.name)})", depth)
        for c in n.checks:
            self.verify(c, depth)

    def verify(self, c, depth: int) -> None:
        """Fails with BadChecksum unless check ``c`` holds."""
        f = pstr(c.local.name)
        start, end = self.region(c, "r")
        mask = hex((1 << c.local.vtype.enc.bits) - 1)
        self.line(f"if r.checksum({c.spec!r}, {start}, {end}, {f}) & {mask} != "
                  f"v{c.local.id}:", depth)
        self.line(f'raise pk.PacketError("BadChecksum", {f})', depth + 1)

    def patch(self, c, depth: int) -> None:
        """Writes the value of check ``c`` into its field."""
        enc = c.local.vtype.enc
        start, end = self.region(c, "w")
        self.line(f"w.patch_checksum({c.spec!r}, {start}, {end}, "
                  f"l{c.local.id}, {enc.bits}, {enc.little_endian}, "
                  f"{pstr(c.local.name)})", depth)

    def region(self, c, rw: str) -> tuple:
        """The start and end of the bits a check covers, with reader or
        writer ``rw``: on decoding ``p`` and on encoding ``l`` hold the
        position of its field."""
        pos = f"{'p' if rw == 'r' else 'l'}{c.local.id}"
        base = self.mark(c.start)
        start = f"{base} + {c.skip}" if c.skip and not c.from_end else base
        end = f"m{c.end}" if c.end is not None else f"{rw}.packet_end()"
        if c.from_end:
            end = f"max({end} - {c.skip}, 0)"
        elif start == "0":
            end = pos  # the field always follows the packet's start
        else:
            end = f"{pos} if {pos} >= {start} else {end}"
        return f"({start})", f"({end})"

    @staticmethod
    def construct(n: Node) -> str:
        return (f"{n.ident}("
                + ", ".join(f"{name}=v{lc.id}" for name, lc in n.fields) + ")")

    def node(self, n: Node) -> None:
        what = "Sequence container" if n.kind == "tm" else "Meta-command"
        self.line()
        self.line()
        self.line("@dataclass(slots=True)")
        self.line(f"class {n.ident}(pk.Packet):")
        self.line(f'"""{what} ``{n.key}``.', 1)
        if n.fixed:
            self.line()
            fixed = ", ".join(f"``{lc.name}`` = {v!r}"
                              for lc, v in n.fixed.items())
            self.line(f"Fixed by its inheritance: {fixed}.", 1)
        length = n.length_field()
        if length is not None:
            self.line()
            self.line(f"``{length.name}`` is the packet length: computed when "
                      "encoding, checked when decoding.", 1)
        for lc, (c, _) in n.derived.items():
            self.line()
            self.line(f"``{lc.name}`` gives the size of ``{c.name}``: computed "
                      "from its length when encoding.", 1)
        for note in n.alarm_notes:
            self.line()
            for text in textwrap.wrap(f"Alarm left out: {note}.", 72):
                self.line(text, 1)
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
                self.line(f"{name}: {field_type(lc.vtype)} = "
                          f"{default(lc.vtype)}", 1)
            doc = f"; {lc.vtype.doc}" if lc.vtype.doc else ""
            cond = ("; present only when its include condition holds"
                    if lc.optional else "")
            self.line(f'"""``{lc.key}`` ({lc.vtype.key}{doc}{cond})"""', 1)
        self.line()
        self.line("@classmethod", 1)
        self.line(f"def decode(cls, data) -> {n.ident}:", 1)
        self.line('"""Decode a packet that starts at the first byte of '
                  '``data``."""', 2)
        self.line("r = pk.BitReader(data)", 2)
        for node in n.chain():
            for c in node.criteria:
                self.line(f"if not ({term(c, lambda lc: f'v{lc.id}')}):", 2)
                self.line(f'raise pk.PacketError("Mismatch", {pstr(node.key)}, '
                          f'{pstr(c.text)})', 3)
            self.read_items(node.items, 2, node.key)
        self.finish(n, 2)
        self.line(f"return {self.construct(n)}", 2)
        self.line()
        self.line("def encode_into(self, w: pk.BitWriter) -> None:", 1)
        body = len(self.out)
        names = {lc: name for name, lc in n.fields}
        for lc, (c, d) in n.derived.items():
            # Sizes and counts come from the lengths of what they size.
            self.line(f"d{lc.id} = pk.size_source(len(self.{names[c]}), "
                      f"{size_args(d)}, {pstr(c.name)})", 2)
            lo, hi = field_range(lc.vtype)
            self.line(f"if not {lo} <= d{lc.id} <= {hi}:", 2)
            self.line(f'raise pk.PacketError("OutOfRange", {pstr(lc.name)})', 3)
        guards = {}
        for node in n.chain():
            for c in node.criteria:
                for lc, prefix, count in term_indexes(c):
                    if lc in names:
                        guards[(reach(f"self.{names[lc]}", prefix,
                                      pstr(lc.name)), count)] = (lc.name)
        for (x, count), name in guards.items():
            # Criteria are checked before the arrays they index are written.
            self.line(f"if len({x}) != {count}:", 2)
            self.line(f'raise pk.PacketError("BadSize", {pstr(name)})', 3)
        for node in n.chain():
            for c in node.criteria:
                if any(lc in names for lc in term_locals(c)):
                    test = term(c, lambda lc: self.place(n, lc, names))
                    self.line(f"if not ({test}):", 2)
                    self.line('raise pk.PacketError("Mismatch", '
                              f'{pstr(node.key)}, {pstr(c.text)})', 3)
        for node in n.chain():
            self.write_items(n, node.items, 2, node.key, names)
        if length is not None:
            enc = length.vtype.enc
            self.line(f"w.patch_length(l{length.id}, {enc.bits}, "
                      f"{enc.little_endian}, {length.length}, "
                      f"{pstr(length.name)})", 2)
        for c in n.checks:
            self.patch(c, 2)
        if len(self.out) == body:
            # A packet without entries, such as a meta-command without a
            # command container, writes nothing.
            self.line("pass", 2)
        self.line()
        self.alarms(n)
        self.line("def describe(self) -> str:", 1)
        self.line('"""Canonical text form, identical in every generated '
                  'language."""', 2)
        parts = []
        for name, lc in n.fields:
            fn = describer(lc.vtype)
            v = f"self.{name}"
            if lc.optional:
                parts.append(f"pk.describe_optional({v}, {fn})")
            else:
                parts.append(f"({fn})({v})" if fn.startswith("lambda")
                             else f"{fn}({v})")
        if not parts:
            self.line(f"return {pstr(n.ident + '{}')}", 2)
            return
        self.line(f"return {pstr(n.ident + '{')} + \", \".join((", 2)
        for (name, _), p in zip(n.fields, parts):
            self.line(f"{pstr(name + '=')} + {p},", 3)
        self.line(')) + "}"', 2)

    def alarms(self, n: Node) -> None:
        """``alarms()`` and ``monitor()``, which give each alarmed field's
        level, and a method computing each one."""
        names = dict((lc, name) for name, lc in n.fields)
        place = lambda lc: self.place(n, lc, names)  # noqa: E731
        monitored = {k: ident for k, ident, _ in self.m.monitored}
        self.line("def alarms(self) -> list:", 1)
        self.line('"""(field, alarm level) for each field with an alarm; the '
                  'level is None', 2)
        self.line("when the field is absent.  Alarms that depend on earlier "
                  "samples count", 2)
        self.line('only this one (see ``monitor``)."""', 2)
        self.line("return [", 2)
        for name, _, _ in n.alarms:
            self.line(f"({pstr(name)}, self._alarm_{name}()),", 3)
        self.line("]", 2)
        self.line()
        self.line("def monitor(self, m: AlarmMonitor, t: float = 0.0) -> list:",
                  1)
        self.line('"""Like ``alarms()``, counting the earlier samples whose '
                  'state ``m`` keeps,', 2)
        self.line("for change ranges, minViolations and minConformance; ``t`` "
                  "is this", 2)
        self.line('sample\'s time in seconds."""', 2)
        self.line("return [", 2)
        for name, _, a in n.alarms:
            call = (f"self._monitor_{name}(m, t)" if a.stateful
                    else f"self._alarm_{name}()")
            self.line(f"({pstr(name)}, {call}),", 3)
        self.line("]", 2)
        self.line()
        for name, lc, a in n.alarms:
            self.line(f"def _alarm_{name}(self) -> Optional[pk.AlarmLevel]:", 1)
            self.alarm_body(name, lc, a, place, None)
            if a.stateful:
                self.line(f"def _monitor_{name}(self, m: AlarmMonitor, t: "
                          "float) -> Optional[pk.AlarmLevel]:", 1)
                self.alarm_body(name, lc, a, place, monitored[a.key])

    def alarm_body(self, name: str, lc, a, place, state) -> None:
        """The body of a method giving a field's alarm level; with
        ``state``, the AlarmMonitor field that keeps its parameter's
        earlier samples."""
        self.line(f"x = self.{name}", 2)
        if lc.optional:
            self.line("if x is None:", 2)
            self.line("return None", 3)
        if state is not None:
            self.line(f"s = m.{state}", 2)
            self.line("mv = mc = 1", 2)
        d = 2
        if lc.vtype.kind == "array":
            self.line("worst = 0", 2)
            self.line("for x in x:", 2)
            d = 3
        self.line("lv = 0", d)
        kind = (lc.vtype.elem.kind if lc.vtype.kind == "array"
                else lc.vtype.kind)
        live = state is not None
        for k, (terms, ad) in enumerate(a.contexts):
            cond = " and ".join(f"({term(c, place)})" for c in terms)
            self.line(f"{'if' if k == 0 else 'elif'} {cond}:", d)
            self.alarm_def(ad, kind, place, d + 1, live)
        if a.contexts:
            if a.default is not None:
                self.line("else:", d)
                self.alarm_def(a.default, kind, place, d + 1, live)
        elif a.default is not None:
            self.alarm_def(a.default, kind, place, d, live)
        if lc.vtype.kind == "array":
            self.line("worst = max(worst, lv)", d)
            self.line("lv = worst", 2)
        if state is None:
            self.line("return pk.AlarmLevel(lv)", 2)
        else:
            if a.history:
                self.line("s.push(float(x), t)", 2)
            self.line("return pk.AlarmLevel(s.update(lv, mv, mc))", 2)
        self.line()

    def alarm_def(self, ad, kind: str, place, depth: int,
                  live: bool = False) -> None:
        """Statements raising ``lv`` to the level that alarm definition
        ``ad`` gives value ``x`` (of kind ``kind``); when ``live``, with
        its change ranges (on AlarmState ``s``, at time ``t``) and setting
        ``mv`` and ``mc`` to its minViolations and minConformance."""
        start = len(self.out)
        if live and ad.change is not None:
            c = ad.change
            self.line(f"d = s.change(float(x), t, {c.samples}, {c.per_second}, "
                      f"{c.percent})", depth)
            self.line("if d is not None:", depth)
            for b in c.bands:
                test = (f"pk.in_band(d, {b.lo!r}, {b.lo_incl}, {b.hi!r}, "
                        f"{b.hi_incl})")
                self.line(f"if {test if b.inside else 'not ' + test}:",
                          depth + 1)
                self.line(f"lv = max(lv, {b.level})", depth + 2)
            if not c.bands:
                self.line("pass", depth + 1)
        if live and (ad.min_violations, ad.min_conformance) != (1, 1):
            self.line(f"mv, mc = {ad.min_violations}, {ad.min_conformance}",
                      depth)
        for b in ad.bands:
            test = (f"pk.in_band(x, {b.lo!r}, {b.lo_incl}, {b.hi!r}, "
                    f"{b.hi_incl})")
            self.line(f"if {test if b.inside else 'not ' + test}:", depth)
            self.line(f"lv = max(lv, {b.level})", depth + 1)
        if kind == "enum" and (ad.labels or ad.default):
            labels = "".join(f"({lo}, {hi}, {lv}), "
                             for lo, hi, lv in ad.labels)
            self.line(f"lv = max(lv, pk.enum_alarm(x, ({labels.rstrip()}), "
                      f"{ad.default}))", depth)
        if kind == "string" and (ad.patterns or ad.default):
            pats = "".join(f"({self.dfa(dfa)}, {lv}), "
                           for dfa, lv in ad.patterns)
            self.line(f"lv = max(lv, pk.pattern_alarm(x, ({pats.rstrip()}), "
                      f"{ad.default}))", depth)
        for level, terms in ad.conditions:
            cond = " and ".join(f"({term(c, place)})" for c in terms)
            self.line(f"if {cond}:", depth)
            self.line(f"lv = max(lv, {level})", depth + 1)
        if len(self.out) == start:
            self.line("pass", depth)

    def dfa(self, dfa) -> str:
        """The name of the module constant holding a pattern's automaton."""
        for name, d in self.dfas:
            if d is dfa:
                return name
        name = f"_DFA{len(self.dfas)}"
        self.dfas.append((name, dfa))
        return name

    def kind(self, k: Kind) -> None:
        self.line()
        self.line()
        self.line(f"class {k.ident}(pk.PacketKind):")
        self.line(f'"""Every concrete packet derived from ``{k.root.key}``."""',
                  1)
        self.line()
        self.line(f"NAME: ClassVar[str] = {pstr(k.root.key)}", 1)
        self.line(f"MAX_BYTES: ClassVar[int] = {k.max_bytes}", 1)
        members = ", ".join(m.ident for m in k.members)
        self.line(f"MEMBERS: ClassVar[tuple] = ({members},)", 1)
        self.line()
        self.line("@staticmethod", 1)
        self.line(f"def decode(data) -> Union[{members}]:", 1)
        self.line('"""Decode the root\'s entries, then pick the first child '
                  '(in', 2)
        self.line('document order) whose criteria match, recursively."""', 2)
        self.line("r = pk.BitReader(data)", 2)
        self.read_items(k.root.items, 2, k.root.key)
        self.dispatch(k.root, 2)

    def dispatch(self, n: Node, depth: int) -> None:
        for c in n.children:
            if c.error:
                continue
            cond = (" and ".join(f"({term(x, lambda lc: f'v{lc.id}')})"
                                 for x in c.criteria) or "True")
            self.line(f"if {cond}:", depth)
            self.read_items(c.items, depth + 1, c.key)
            self.dispatch(c, depth + 1)
        if n.abstract:
            self.line(f'raise pk.PacketError("NoMatch", {pstr(n.key)})', depth)
        else:
            self.finish(n, depth)
            self.line(f"return {self.construct(n)}", depth)


def emit(mission: Mission, source: str) -> str:
    return PythonEmitter(mission, source).emit()
