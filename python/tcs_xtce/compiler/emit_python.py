"""Emit a Python module of packet codecs (uses ``tcs_xtce.packet``)."""

from __future__ import annotations

import textwrap

from .common import (doc_header, HOOK_DOCS, written, checks_hooks, items_hooks,
                     terms_hooks, notes, stream_doc, stream_fn, Ref, fast_order,
                     field_range, flt, header_lines, inputs, invertible,
                     math_value, size_args, string_spec)
from .model import (term_indexes, term_locals, walk, Index, AggregateEnc, AllOf,
                    AnyOf, ArrayEnc, BinaryEnc, Cond, ContextCal, Criterion,
                    DynSize, Fixed, FloatEnc, IntEnc, Kind, Local, Mark, MathOp,
                    Mission, Node, Pad, Polynomial, Seek, Span, Spline, Value,
                    VType, Require, Fill, Segment, Verify, Custom, TailPad,
                    TailSeek, LengthEnd, Transform, XformEnc, StringEnc)


def pstr(s: str) -> str:
    """Quotes a string as a Python string literal.

    Args:
        s: The string.

    Returns:
        A double-quoted literal, with quotes, backslashes and control
        characters escaped.
    """
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
    """Gives the Python type annotation of a value's generated field.

    Args:
        vt: The value's type.

    Returns:
        ``list[...]`` for an array, the class name for an aggregate or an
        enumeration, else the builtin type.
    """
    if vt.kind == "array":
        return f"list[{field_type(vt.elem)}]"
    if vt.kind == "aggregate":
        return vt.ident
    builtin = {"int": "int", "float": "float", "bool": "bool", "string": "str",
               "binary": "bytes"}
    return builtin.get(vt.kind) or vt.ident


def default(vt: VType) -> str:
    """Gives the default value of a value's generated field.

    Args:
        vt: The value's type.

    Returns:
        The expression: zero, empty or the enumeration's value 0, and a
        ``dataclasses.field`` factory for an array or an aggregate.
    """
    if vt.kind == "array":
        return "dataclasses.field(default_factory=list)"
    if vt.kind == "aggregate":
        return f"dataclasses.field(default_factory={vt.ident})"
    zero = {"int": "0", "float": "0.0", "bool": "False", "string": '""',
            "binary": 'b""'}
    return zero.get(vt.kind) or f"pk.enum_value({vt.ident}, 0)"


def literal(vt: VType, v) -> str:
    """Gives the Python literal of a constant engineering value.

    Args:
        vt: The value's type.
        v:  The value: a number, a bool, a string or bytes.

    Returns:
        The literal; an enumeration's value is wrapped in
        ``pk.enum_value``.
    """
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
    """Gives the expression for a MathOperation tree.

    Args:
        e:     The tree: ``("const", v)``, ``("raw",)`` for the value being
               calibrated, ``("value", ref)`` for another value, or an
               operator and its operands' trees.
        x:     The expression for the value being calibrated.
        place: Gives the expression for a Local the tree uses.

    Returns:
        The expression, a float.
    """
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
    """Gives the expression calibrating a raw value.

    Args:
        cal:   The calibrator, or None.
        x:     The expression for the raw value, a float.
        f:     The name of the field, as a Python string literal, that errors
               name.
        place: Gives the expression for a Local that the calibrator uses.

    Returns:
        The expression for the engineering value; ``x`` itself without a
        calibrator.
    """
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
    """Gives the expression inverting a calibrator.

    Args:
        cal:   The calibrator, or None.
        y:     The expression for the engineering value.
        f:     The name of the field, as a Python string literal, that errors
               name.
        place: Gives the expression for a Local that the calibrator uses.

    Returns:
        The expression for the raw value; ``y`` itself without a
        calibrator.  For a calibrator that can't be inverted, the
        expression raises NotInvertible when it is evaluated.
    """
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
    """Gives a conditional expression over a context calibrator's cases.

    Args:
        cal:   The context calibrator.
        body:  Gives the expression for one of its calibrators.
        place: Gives the expression for a Local that the cases' criteria
               use.

    Returns:
        The expression: ``body`` of the calibrator of the first case
        whose criteria hold, else of the default.
    """
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
    """Gives the expression for the value a bound DynSize adjusts.

    Args:
        d:     The DynSize.
        place: Gives the expression for a Local.

    Returns:
        The expression: a field (or a member or element of one), or the
        first matching value of a DiscreteLookupList, else its default.
    """
    if d.lookup is None:
        return reach(place(d.local), d.path, pstr(d.local.name))
    x = str(d.default)
    for criteria, v in reversed(d.lookup):
        x = (f"({v} if {' and '.join(f'({term(c, place)})' for c in criteria)} "
             f"else {x})")
    return x


def offset(s: Seek, ref, f: str) -> str:
    """Gives the expression for the offset of a Seek.

    Args:
        s:   The Seek.
        ref: Gives the expression for the value that a size, count or offset
             uses (a DynSize), and through ``place`` for a Local.
        f:   The name of the field, as a Python string literal, that errors
             name.

    Returns:
        The expression for the offset in bits, less the bits the Seek
        steps back.
    """
    if isinstance(s.offset, int):
        x = str(s.offset - s.back)
    else:
        x = size(s.offset, ref(s.offset), f)
        x = f"{x} - {s.back}" if s.back else x
    return f"{x} - {span(s.span, ref, f)}" if s.span else x


def span(sp: Span, ref, f: str) -> str:
    """Gives the expression for the bits of a Span.

    Args:
        sp:  The Span.
        ref: Gives the expression for the value that a count or gap uses.
        f:   The name of the field, as a Python string literal, that errors
             name.

    Returns:
        The expression, in bits.
    """
    count, gap = (str(d) if isinstance(d, int) else size(d, ref(d), f)
                  for d in (sp.count, sp.gap))
    return f"pk.span_bits({count}, {sp.each}, {gap})"


def tail_back(t: TailSeek, ref, f: str) -> str:
    """Gives the expression for the bits a TailSeek goes back from the end.

    Args:
        t:   The TailSeek.
        ref: Gives the expression for the value that a span uses.
        f:   The name of the field, as a Python string literal, that errors
             name.

    Returns:
        The expression, in bits.
    """
    return f"{t.back} + {span(t.span, ref, f)}" if t.span else str(t.back)


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
    """Gives the expression reading one value with BitReader ``r``.

    Args:
        vt:    The value's type.
        f:     The name of the field, as a Python string literal, that errors
               name.
        ref:   Gives the expression for the value that a size, count or offset
               uses (a DynSize), and through ``place`` for a Local.
        depth: How deeply aggregates nest here, which names the locals
               that keep the members giving a later member's size.

    Returns:
        The expression, which reads the value and gives it; one least
        significant bit first switches the reader's bit order around it.
    """
    x = read_value_expr(vt, f, ref, depth)
    if lsb_bytes(vt):
        # A tuple's items are evaluated in order.
        return f"(r.set_lsb(True), {x}, r.set_lsb(False))[1]"
    return x


def lsb_bytes(vt: VType) -> bool:
    """Whether ``vt`` is a string or binary value whose bytes are least
    significant bit first."""
    return isinstance(vt.enc, (StringEnc, BinaryEnc)) and vt.enc.lsb


def read_value_expr(vt: VType, f: str, ref, depth: int) -> str:
    """Gives ``read_expr``'s expression, in the reader's usual bit order.

    Args:
        vt:    The value's type.
        f:     The name of the field, as a Python string literal, that errors
               name.
        ref:   Gives the expression for the value that a size, count or offset
               uses (a DynSize), and through ``place`` for a Local.
        depth: How deeply aggregates nest here, which names the locals
               that keep the members giving a later member's size.

    Returns:
        The expression, which reads the value and gives it.
    """
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
        agg = f"({agg}, r.pad({e.pad}, {f}))[0]" if e.pad else agg
        for c in e.checks:
            # A repeated container's own criteria, on each copy.
            cond = " and ".join(f"({term(t, lambda lc: '_c')})"
                                for t in c.criteria)
            agg = (f"pk.checked({agg}, lambda _c: {cond}, {pstr(c.container)}, "
                   f"{pstr(c.text)})")
        x = e.transform
        if x is not None:
            # Read the members from what the transform makes of its bytes.
            agg = (f"pk.within(r.binary_from({transform_hook(x.decode)}, "
                   f"{x.raw}, {x.cap}, {f}), lambda r: {agg})")
        return agg
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
            if e.bits > 64:
                # An enumeration holds a 64-bit value, as in the others.
                v = f"pk.fits_i64({v}, {f})"
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
    if isinstance(e, XformEnc):
        h = transform_hook(e.decode)
        if e.time:
            return f"r.time_from({h}, {e.raw}, {f})"
        return f"r.binary_from({h}, {e.raw}, {e.cap}, {f})"
    if isinstance(e, BinaryEnc):
        if e.rest:
            return f"r.bytes(r.rest_bytes({e.bits // 8}, {e.tail}, {f}), {f})"
        if e.embedded:
            return f"r.bytes(r.embedded_bytes({e.bits // 8}, {f}), {f})"
        n = size(e.dyn, ref(e.dyn), f) if e.dyn else e.bits // 8
        b = f"r.bytes({n}, {f})"
        return f"pk.string_from_bytes({b}, {f})" if vt.kind == "string" else b
    s = f"pk.read_string(r, {spec(vt, ref, f)}, {f})"
    if vt.kind == "bool":
        return (f"pk.bool_from_string({s}, {pstr(vt.one_string)}, "
                f"{pstr(vt.zero_string)}, {f})")
    return s


def write_stmt(vt: VType, x: str, f: str, ref) -> str:
    """Gives the statement writing one value with BitWriter ``w``.

    Not for arrays, aggregates or binary values whose size another value
    gives, which ``PythonEmitter.write_bytes`` writes.

    Args:
        vt:  The value's type.
        x:   The expression for the value.
        f:   The name of the field, as a Python string literal, that errors
             name.
        ref: Gives the expression for the value that a size, count or offset
             uses (a DynSize), and through ``place`` for a Local.

    Returns:
        The statement; for a calibrator that can't be inverted, one that
        raises NotInvertible.
    """
    e = vt.enc
    if isinstance(e, IntEnc):
        bits, enc = e.bits, int_encoding(e)
        # Past 64 bits, the raw field is not bounded by a 64-bit integer.
        to_int = "float_to_wide" if bits > 64 else "float_to_int"
        if vt.kind == "int" and vt.calibrator is not None:
            if not invertible(vt):
                return f'raise pk.PacketError("NotInvertible", {f})'
            y = uncalibrate(vt, f"float({x})", f, ref.place)
            raw = f"pk.{to_int}({y}, {bits}, {enc}, {f})"
        elif vt.kind == "int":
            raw = f"pk.from_signed({x}, {bits}, {enc}, {f})"
        elif vt.kind == "enum":
            raw = f"pk.from_signed(int({x}), {bits}, {enc}, {f})"
        elif vt.kind == "bool":
            raw = f"1 if {x} else 0"
        else:
            if not invertible(vt):
                return f'raise pk.PacketError("NotInvertible", {f})'
            raw = (f"pk.{to_int}({uncalibrate(vt, x, f, ref.place)}, {bits}, "
                   f"{enc}, {f})")
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
    if isinstance(e, XformEnc):
        h = transform_hook(e.encode)
        way = "time_to" if e.time else "binary_to"
        return f"w.{way}({h}, {x}, {e.raw}, {e.bits // 8}, {f})"
    if isinstance(e, BinaryEnc) and e.rest:
        return f"pk.write_rest(w, {x}, {e.bits // 8}, {f})"
    if isinstance(e, BinaryEnc) and e.embedded:
        return f"pk.write_embedded(w, {x}, {e.bits // 8}, {f})"
    if isinstance(e, BinaryEnc):
        data = f"{x}.encode()" if vt.kind == "string" else x
        return f"pk.write_binary(w, {data}, {e.bits // 8}, {f})"
    if vt.kind == "bool":
        s = f"({pstr(vt.one_string)} if {x} else {pstr(vt.zero_string)})"
        return f"pk.write_string(w, {spec(vt, ref, f)}, {s}, {f})"
    return f"pk.write_string(w, {spec(vt, ref, f)}, {x}, {f})"


def reach(x: str, path, f: str) -> str:
    """Gives the expression for a member or element of a value.

    Args:
        x:    The expression for the value.
        path: Member names and Index items, outermost first.
        f:    The name of the field, as a Python string literal, that a
              missing element's ``BadSize`` names.

    Returns:
        The expression.  An element of an array whose count varies may be
        missing, and then the expression raises ``BadSize``.
    """
    for p in path:
        if not isinstance(p, Index):
            x += "." + p
        elif p.stride:
            x = (f"pk.element_at({x}, {p.i}, {p.stride}, {p.per}, {p.need}, "
                 f"{f})")
        elif p.count is None:
            x = f"pk.element({x}, {p.i}, {f})"
        else:
            x += f"[{p.i}]"
    return x


def term(t, place) -> str:
    """Gives the expression for a criterion or a tree of them.

    Args:
        t:     A criterion, an AllOf or AnyOf, or a Custom criterion.
        place: Gives the expression for a Local.

    Returns:
        The expression, a bool.  An absent optional value compares as
        ``std::optional`` does in C++: less than any value.
    """
    if isinstance(t, (AllOf, AnyOf)):
        return (" and " if isinstance(t, AllOf)
                else " or ").join(f"({term(x, place)})" for x in t.terms)
    if isinstance(t, Custom):
        return (f"pk.criterion_hook(HOOKS, {pstr(t.hook.ident)}, "
                f"({hook_args(t.inputs, place)}))")
    if t.local.optional:
        # An absent value compares as std::optional's does: less than any.
        x = place(t.local)
        return f"({EMPTY[t.op]} if {x} is None else {condition(t, x)})"
    return condition(t, place(t.local),
                     place(t.other) if t.other is not None else None)


def valid_test(vt: VType, x: str) -> str:
    """Gives the test that a value lies in one of its type's valid ranges.

    Args:
        vt: The value's type, an integer or float with valid ranges.
        x:  The expression for the value.

    Returns:
        The expression, a bool.
    """
    tests = []
    for b in vt.valid:
        if vt.kind == "int":
            lo = f"{b.lo} <= " if b.lo is not None else ""
            hi = f" <= {b.hi}" if b.hi is not None else ""
            tests.append(f"{lo}{x}{hi}" if lo or hi else "True")
        else:
            tests.append(f"pk.in_band({x}, {b.lo!r}, {b.lo_incl}, {b.hi!r}, "
                         f"{b.hi_incl})")
    return " or ".join(f"({t})" for t in tests)


def pspec(spec: tuple) -> str:
    """The runtime's checksum spec: the tuple, but for a custom checksum,
    whose function is the application's."""
    if spec[0] == "custom":
        return f"(\"custom\", HOOKS.get({pstr(spec[1].ident)}))"
    return repr(spec)


def transform_hook(h) -> str:
    """A transform hook, or None for a way the document doesn't give."""
    return f"HOOKS[{pstr(h.ident)}]" if h else "None"


def hook_args(inputs: list, place) -> str:
    """Gives the inputs of a custom algorithm, as tuple items.

    Args:
        inputs: Each input: ``("const", v)``, or a Local with the path to
                its member or element and its type.
        place:  Gives the expression for a Local.

    Returns:
        The inputs' expressions as floats, each followed by a comma, so
        that they make a tuple even when there is one.
    """
    out = [flt(x[1]) if x[0] == "const"
           else math_value(x[3], reach(place(x[1]), x[2], pstr(x[1].name)),
                           "python") for x in inputs]
    return "".join(f"{a}, " for a in out).rstrip()


# What ``<absent value> op <value>`` gives, as with std::optional in C++.
EMPTY = {"==": False, "!=": True, "<": True, "<=": True, ">": False,
         ">=": False}


def condition(c: Criterion, x: str, y=None) -> str:
    """Gives the comparison of a criterion.

    Args:
        c: The criterion.
        x: The expression for the criterion's Local.
        y: The expression for the Local it compares with, or None if it
           compares with a constant.

    Returns:
        The expression, a bool.
    """
    vt = c.vtype
    x = reach(x, c.path, pstr(c.local.name))
    if y is not None:
        y = reach(y, c.other_path, pstr(c.other.name))
        return f"{x} {c.op} {y}"
    v = c.value if vt.kind in ("int", "enum") else literal(vt, c.value)
    return f"{x} {c.op} {v}"


def describer(vt: VType) -> str:
    """Gives a function that makes the canonical text of a value.

    Args:
        vt: The value's type.

    Returns:
        The expression for the function: a runtime or module function, or
        a lambda for an array or an aggregate.
    """
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
        """Starts a module of packet codecs.

        Args:
            mission: The compiled mission.
            source:  The name of the XTCE documents, for the header comment.
        """
        self.m = mission
        self.source = source
        self.out: list[str] = []
        self.dfas: list = []
        self.transforms = 0  # numbers the start of each transformed copy

    def line(self, s: str = "", depth: int = 0) -> None:
        self.out.append("    " * depth + s if s else "")

    def doc(self, depth: int, summary: str, params: list[tuple[str, str]] = (),
            returns: str | None = None,
            raises: list[tuple[str, str]] = ()) -> None:
        """Writes the docstring of a generated function.

        Args:
            depth:   The indentation level of the function's body.
            summary: What the function does.
            params:  Each parameter but ``self`` and ``cls``, with what it
                     means.
            returns: What it returns, or None if it returns nothing.
            raises:  Each exception it raises, with when.
        """
        for text in doc_header("python", summary, params, returns,
                               raises=raises, indent=4 * depth):
            self.line(text, depth)

    def emit(self) -> str:
        """Writes the module.

        Returns:
            The module's source text.
        """
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
        self.line("from tcs_xtce import packet as pk")
        self.line()
        self.line()
        self.line("def _describe_bool(v: bool) -> str:")
        self.line('return "true" if v else "false"', 1)
        self.line()
        names = ([vt.ident for vt in m.types + m.aggregates] + ["AlarmMonitor"]
                 + [n.ident for n in m.nodes] + [k.ident for k in m.kinds]
                 + [st.ident for st in m.streams])
        self.line()
        self.line("__all__ = [" + ", ".join(pstr(n) for n in names) + "]")
        for vt in m.types:
            self.enum_type(vt)
        for vt in m.aggregates:
            self.aggregate_type(vt)
        self.hooks()
        self.alarm_monitor()
        for n in m.nodes:
            self.node(n)
        for k in m.kinds:
            self.kind(k)
        for st in m.streams:
            self.stream(st)
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

    def stream(self, st) -> None:
        """Writes the class of a stream, which finds its frames or decodes it.

        Args:
            st: The stream.
        """
        self.line()
        self.line()
        params = [stream_fn(k, x) for k, x in st.functions()]
        if st.custom:
            self.line(f"class {st.ident}:")
        else:
            base = "FrameSync" if st.fixed else "FlagSync"
            self.line(f"class {st.ident}(pk.{base}):")
        doc = stream_doc(st).replace(st.key, f"``{st.key}``")
        if st.container:
            doc = doc.replace(st.container, f"``{st.container}``")
        if st.feeder is not None:
            doc += (f" Its input is what stream ``{st.feeder.key}`` gives, "
                    "from the bytes pushed.")
        if params:
            doc += (" The application gives it its functions: "
                    + ", ".join(params) + ".")
        for k, text in enumerate(textwrap.wrap(doc, 70)):
            self.line(('"""' if k == 0 else "") + text, 1)
        self.line('"""', 1)
        self.line()
        if not st.custom:
            self.line(f"CONTAINER: ClassVar[str] = {pstr(st.container)}", 1)
            self.line()
        self.line(f"def __init__(self{''.join(', ' + p for p in params)}) -> "
                  "None:", 1)
        if params:
            what = {
                "decode": "The DecodingAlgorithm ``{}`` of stream ``{}``, "
                          "which gives the bytes it decodes from those it is "
                          "given.",
                "invert": "The InvertAlgorithm ``{}`` of stream ``{}``: what a "
                          "bit is once AutoInvert has inverted the stream."}
            self.doc(
                2,
                f"Makes a stream ``{st.key}`` with nothing pushed to it yet.",
                [(stream_fn(k, x),
                  what[k].format(x.decoder if k == "decode" else x.inverter,
                                 x.key)) for k, x in st.functions()])
        if st.custom:
            self.line(f"self._decode = {stream_fn('decode', st)}", 2)
        elif st.fixed:
            self.line(f"super().__init__({st.frame_bits}, 0x{st.pattern:x}, "
                      f"0x{st.mask:x}, {st.pattern_bits}, {st.location}, "
                      f"{st.max_errors}, {st.aperture}, {st.verify}, "
                      f"{st.check}, {st.invert_after}, {pstr(st.pcm)}, "
                      f"{st.inverted})", 2)
        else:
            self.line(f"super().__init__({st.flag_bits}, {st.flag_ones}, "
                      f"{st.max_bits}, {pstr(st.pcm)}, {st.inverted})", 2)
        if st.inverter:
            self.line(f"self.invert = {stream_fn('invert', st)}", 2)
        if st.feeder is not None:
            args = ", ".join(f"{p}={p}"
                             for p in (stream_fn(k, x)
                                       for k, x in st.feeder.functions()))
            self.line(f"self._feeder = {st.feeder.ident}({args})", 2)
        if not (st.custom or st.feeder is not None):
            return
        self.line()
        self.line("def push(self, data) -> list:", 1)
        what = "decoded bytes" if st.custom else "frames"
        self.doc(2, f"Takes the next bytes of stream ``{st.key}``.",
                 [("data",
                   "The stream's next bytes, in the order they arrive.")],
                 f"The pieces of the bytes that ``{st.decoder}`` decodes from "
                 "them." if st.custom
                 else "The bytes of each frame they complete, each a "
                      f"``{st.container}`` packet.")
        ins = "self._feeder.push(data)" if st.feeder is not None else "[data]"
        if st.custom:
            self.line(f"return [y for y in (self._decode(x) for x in {ins}) if "
                      "y]", 2)
        else:
            self.line(f"return [f for x in {ins} for f in super().push(x)]", 2)

    def wrapped(self, items: list, depth: int) -> None:
        """Writes a tuple of items, wrapped to fit 80 columns.

        Args:
            items: The items, written with ``repr``.
            depth: The indentation level of the tuple's brackets.
        """
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

    def need_hooks(self, hooks: list, depth: int) -> None:
        if hooks:
            names = "".join(f"{pstr(h.ident)}, " for h in hooks).rstrip()
            self.line(f"pk.need_hooks(HOOKS, ({names}))", depth)

    def hooks(self) -> None:
        """Writes ``HOOKS``, the dictionary of the application's functions for
        the document's custom algorithms, with a comment naming each.
        """
        self.line()
        self.line()
        self.line("# The functions the application supplies for the document's "
                  "custom")
        self.line("# algorithms, by name: set them before decoding or encoding "
                  "a packet")
        self.line("# that needs them, or it raises NoAlgorithm.")
        for h in self.m.hooks:
            for text in textwrap.wrap(f"{h.ident}: {h.name}, "
                                      f"{HOOK_DOCS[h.kind]}", 76):
                self.line(f"# {text}")
        self.line("HOOKS: dict = {}")

    def alarm_monitor(self) -> None:
        """Writes the AlarmMonitor class, the state of each parameter whose
        alarms depend on earlier samples.
        """
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
        """Writes the class of an enumeration, its labels and its describer.

        Args:
            vt: The enumeration's type.
        """
        self.line()
        self.line()
        self.line(f"class {vt.ident}(enum.IntEnum):")
        self.line(f'"""Enumeration ``{vt.key}``.', 1)
        self.line()
        self.line("Decoding keeps values the enumeration does not list (see", 1)
        self.line(':func:`tcs_xtce.packet.enum_value`)."""', 1)
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
        self.line("@staticmethod", 1)
        self.line("def label_of(value: int) -> Optional[str]:", 1)
        self.doc(2, f"Names a value of enumeration ``{vt.key}``.",
                 [("value",
                   "The value, which needn't be a member: a value in a label's "
                   "range, or one the enumeration doesn't list.")],
                 "The value's label, or None if the enumeration doesn't list "
                 "it.")
        self.line(f"return pk.find_label(int(value), _LABELS_{vt.ident})", 2)
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
        """Writes the dataclass of an aggregate type.

        Args:
            vt: The aggregate's type.
        """
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
        """Writes the statements decoding entries with BitReader ``r``.

        Each value is read into ``v<id>``.

        Args:
            items:  The entries.
            depth:  The statements' indentation level.
            where:  The qualified name of the container, which errors name.
            nested: Whether the entries are inside a conditional include,
                    whose values were already set to None.
        """
        for it in items:
            if isinstance(it, Seek):
                off = offset(it, self.read_ref, pstr(where))
                if it.from_start is None:
                    self.line(f"r.skip({off}, {pstr(where)})", depth)
                else:
                    seek = "seek_ahead" if it.ahead else "seek"
                    self.line(f"r.{seek}({self.mark(it.from_start)}, {off}, "
                              f"{pstr(where)})", depth)
            elif isinstance(it, Mark):
                self.line(f"m{it.id} = r.pos", depth)
            elif isinstance(it, Pad):
                self.line(f"r.pad({it.bits}, {pstr(where)})", depth)
            elif isinstance(it, TailSeek):
                back = tail_back(it, self.read_ref, pstr(where))
                self.line(f"r.seek_end({back}, {pstr(where)})", depth)
            elif isinstance(it, LengthEnd):
                self.line(f"r.check_span(e{it.local.id}, "
                          f"{pstr(it.local.name)})", depth)
            elif isinstance(it, Transform):
                # Read its items from what the transform makes of its bytes.
                f = pstr(it.name)
                self.line(f"_t{it.start} = "
                          f"r.binary_from({transform_hook(it.decode)}, "
                          f"{it.raw}, {it.cap}, {f})", depth)
                self.line(f"r, _r{it.start} = pk.BitReader(_t{it.start}), r",
                          depth)
                self.read_items(it.items, depth, where, nested)
                if it.length is not None:
                    self.line(f"r.finish({pstr(it.length.name)})", depth)
                self.line(f"r = _r{it.start}", depth)
            elif isinstance(it, TailPad):
                # The packet's last bits, which encoding fills with zeros.
                self.line(f"r.skip({it.bits}, {pstr(where)})", depth)
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
                n = f"v{lc.id}"
                if lc.length is not None and lc.vtype.wide:
                    # A 64-bit value, as in the other runtimes.
                    n = f"pk.length_u64(v{lc.id}, {pstr(lc.name)})"
                if lc.length is not None and lc.measures:
                    self.line(f"e{lc.id} = r.span_end({n}, {lc.length}, "
                              f"{pstr(lc.name)})", depth)
                elif lc.length is not None:
                    self.line(f"r.limit({n}, {lc.length}, {pstr(lc.name)})",
                              depth)
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
        """Writes the statements encoding entries with BitWriter ``w``.

        Args:
            n:     The packet being encoded.
            items: The entries.
            depth: The statements' indentation level.
            where: The qualified name of the container, which errors name.
            names: The name of each Local's field in the packet.
        """
        for it in items:
            if isinstance(it, Seek):
                off = offset(it, self.write_ref(n, names), pstr(where))
                if it.from_start is None:
                    self.line(f"w.skip({off}, {pstr(where)})", depth)
                else:
                    seek = "seek_ahead" if it.ahead else "seek"
                    self.line(f"w.{seek}({self.mark(it.from_start)}, {off}, "
                              f"{pstr(where)})", depth)
            elif isinstance(it, Mark):
                self.line(f"m{it.id} = w.pos", depth)
            elif isinstance(it, Fixed):
                self.line(f"w.uint({it.bits}, 0x{it.value:x})", depth)
            elif isinstance(it, (Pad, TailPad)):
                self.line(f"w.zeros({it.bits})", depth)
            elif isinstance(it, TailSeek):
                back = tail_back(it, self.write_ref(n, names), pstr(where))
                self.line(f"w.seek_tail({back})", depth)
            elif isinstance(it, LengthEnd):
                lc = it.local
                enc = lc.vtype.enc
                self.line(f"w.patch_span(l{lc.id}, {enc.bits}, "
                          f"{enc.little_endian}, {lc.length}, {pstr(lc.name)})",
                          depth)
            elif isinstance(it, Transform):
                # Write its items, then what the transform makes of them.
                self.line(f"_t{it.start} = w.pos", depth)
                self.write_items(n, it.items, depth, where, names)
                if it.length is not None:
                    self.patch_length(it.length, depth)
                for c in it.patches:
                    self.patch(c, depth, early=True)
                if it.keeps:
                    # For checks of what follows it (see late_patch).
                    self.line(f"_p{it.start} = w.copy_out(_t{it.start})", depth)
                self.line(f"w.transform(_t{it.start}, "
                          f"{transform_hook(it.encode)}, {it.raw}, "
                          f"{it.out_cap}, {pstr(it.name)})", depth)
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
                self.write_value(written(n, lc), x, pstr(lc.name),
                                 self.write_ref(n, names), depth)

    def write_value(self, vt: VType, x: str, f: str, ref, depth: int) -> None:
        """Writes the statements encoding one value.

        Args:
            vt:    The value's type.
            x:     The expression for the value.
            f:     The name of the field, as a Python string literal, that
                   errors name.
            ref:   Gives the expression for the value that a size, count or
                   offset uses (a DynSize), and through ``place`` for a Local.
            depth: The statements' indentation level.
        """
        if lsb_bytes(vt):
            self.line("w.set_lsb(True)", depth)
            self.write_bytes(vt, x, f, ref, depth)
            self.line("w.set_lsb(False)", depth)
        else:
            self.write_bytes(vt, x, f, ref, depth)

    def write_bytes(self, vt: VType, x: str, f: str, ref, depth: int) -> None:
        """Writes ``write_value``'s statements, in the writer's usual bit order.

        Args:
            vt:    The value's type.
            x:     The expression for the value.
            f:     The name of the field, as a Python string literal, that
                   errors name.
            ref:   Gives the expression for the value that a size, count or
                   offset uses (a DynSize), and through ``place`` for a Local.
            depth: The statements' indentation level.
        """
        e = vt.enc
        if isinstance(e, AggregateEnc):
            inner = Ref(lambda d: f"{x}.{d.member}" if d.member else ref(d),
                        ref.place)
            for c in e.checks:
                self.require(c, lambda lc: x, depth)
            t = e.transform
            if t is not None:
                # Write the members, then what the transform makes of them.
                self.transforms += 1
                start = f"_x{self.transforms}"
                self.line(f"{start} = w.pos", depth)
            for m in e.members:
                if m.skip:
                    self.line(f"w.zeros({m.skip})", depth)
                self.write_value(m.vtype, f"{x}.{m.ident}", f, inner, depth)
            if e.pad:
                self.line(f"w.zeros({e.pad})", depth)
            if t is not None:
                self.line(f"w.transform({start}, {transform_hook(t.encode)}, "
                          f"{t.raw}, {t.raw}, {f})", depth)
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
            if vt.valid and vt.kind in ("int", "float"):
                self.line(f"if not ({valid_test(vt, x)}):", depth)
                self.line(f'raise pk.PacketError("OutOfRange", {f})', depth + 1)
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
        """Gives the expression for a value when encoding.

        Args:
            n:     The packet being encoded.
            lc:    The value's Local.
            names: The name of each Local's field in the packet.

        Returns:
            The expression: a field, a computed size or count, or a fixed
            constant.
        """
        if lc in names:
            return f"self.{names[lc]}"
        if lc in n.derived:
            return f"d{lc.id}"
        return literal(lc.vtype, n.fixed[lc])

    def fill(self, it: Fill, rw: str, ref, depth: int) -> None:
        """Writes the statements padding to the end of a container with a size.

        The statements raise BadSize if the container's entries go past it.

        Args:
            it:    The Fill.
            rw:    ``"r"`` when decoding, ``"w"`` when encoding.
            ref:   Gives the expression for the value that a size, count or
                   offset uses (a DynSize), and through ``place`` for a Local.
            depth: The statements' indentation level.
        """
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
        """Writes the statement raising Mismatch unless an included
        container's criteria hold.

        Args:
            it:    The Require.
            place: Gives the expression for a Local.
            depth: The statement's indentation level.
        """
        cond = " and ".join(f"({term(t, place)})" for t in it.criteria)
        self.line(f"if not ({cond}):", depth)
        self.line(f'raise pk.PacketError("Mismatch", {pstr(it.container)}, '
                  f'{pstr(it.text)})', depth + 1)

    @staticmethod
    def mark(i: int) -> str:
        return "0" if i == 0 else f"m{i}"

    def patch_length(self, lc: Local, depth: int) -> None:
        """Writes the statement filling in a packet length field from what
        has been written.

        Args:
            lc:    The field.
            depth: The statement's indentation level.
        """
        enc = lc.vtype.enc
        self.line(f"w.patch_length(l{lc.id}, {enc.bits}, {enc.little_endian}, "
                  f"{lc.length}, {pstr(lc.name)})", depth)

    def finish(self, n: Node, depth: int) -> None:
        """Writes the statements checking the packet length field and the
        checksums when decoding ends.

        Args:
            n:     The packet.
            depth: The statements' indentation level.
        """
        lc = n.length_field()
        if lc is not None:
            self.line(f"r.finish({pstr(lc.name)})", depth)
        for c in n.checks:
            self.verify(c, depth)

    def verify(self, c, depth: int) -> None:
        """Writes the statement raising BadChecksum unless a check holds.

        Args:
            c:     The check.
            depth: The statement's indentation level.
        """
        f = pstr(c.local.name)
        start, end = self.region(c, "r")
        mask = hex((1 << c.local.vtype.enc.bits) - 1)
        self.line(f"if r.checksum({pspec(c.spec)}, {start}, {end}, {f}) & "
                  f"{mask} != v{c.local.id}:", depth)
        self.line(f'raise pk.PacketError("BadChecksum", {f})', depth + 1)

    def patch(self, c, depth: int, early: bool = False) -> None:
        """Writes the statement writing a check's value into its field,
        unless it is computed elsewhere: before a transform when it has a
        ``stop``, and otherwise where the packet or container ends.

        Args:
            c:     The check.
            depth: The statement's indentation level.
            early: Whether this is before the transform of its field.
        """
        if c.late is not None or (c.stop is not None) != early:
            return
        enc = c.local.vtype.enc
        start, end = self.region(c, "w")
        self.line(f"w.patch_checksum({pspec(c.spec)}, {start}, {end}, "
                  f"l{c.local.id}, {enc.bits}, {enc.little_endian}, "
                  f"{pstr(c.local.name)})", depth)

    def late_patch(self, c, depth: int) -> None:
        """Writes the statement writing a late check's value (see
        model.Check) into its transformed data, which it transforms again.

        Args:
            c:     The check.
            depth: The statement's indentation level.
        """
        enc = c.local.vtype.enc
        start, end = self.region(c, "w")
        t = c.late
        self.line(f"w.patch_transformed_checksum({pspec(c.spec)}, {start}, "
                  f"{end}, _p{t.start}, l{c.local.id} - _t{t.start}, "
                  f"{enc.bits}, {enc.little_endian}, _t{t.start}, "
                  f"{transform_hook(t.encode)}, {t.raw}, {t.out_cap}, "
                  f"{pstr(c.local.name)})", depth)

    def region(self, c, rw: str) -> tuple:
        """Gives the start and end of the bits a check covers.

        Args:
            c:  The check.
            rw: ``"r"`` when decoding, where ``p<id>`` holds the position of
                the check's field, or ``"w"`` when encoding, where ``l<id>``
                does.

        Returns:
            The expressions for the start and the end, in bits.
        """
        pos = f"{'p' if rw == 'r' else 'l'}{c.local.id}"
        base = self.mark(c.start)
        start = f"{base} + {c.skip}" if c.skip and not c.from_end else base
        end = f"m{c.end}" if c.end is not None else f"{rw}.packet_end()"
        if c.stop is not None:
            end = f"max(m{c.stop} - {c.stop_back}, 0)"
        elif c.late is not None:
            pass  # the field is before the bits, which run to the end
        elif c.from_end:
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
        """Writes the dataclass of a packet, with its codec and its alarm,
        algorithm and describe methods.

        Args:
            n: The packet's container or meta-command.
        """
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
        for note in notes(n):
            self.line()
            for text in textwrap.wrap(note, 72):
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
        self.line(f"def decode_prefix(cls, data) -> tuple[{n.ident}, int]:", 1)
        self.doc(2,
                 f"Decodes a ``{n.key}`` packet that starts at the first byte "
                 "of ``data``.",
                 [("data", "The packet's bytes, and any after it.")],
                 "The packet and the number of bytes it takes.",
                 [("pk.PacketError",
                   "The bytes aren't such a packet, or it can't be decoded; "
                   "its ``kind`` says why.")])
        self.need_hooks(n.hooks, 2)
        self.line("r = pk.BitReader(data)", 2)
        for node in n.chain():
            for c in node.criteria:
                self.line(f"if not ({term(c, lambda lc: f'v{lc.id}')}):", 2)
                self.line(f'raise pk.PacketError("Mismatch", {pstr(node.key)}, '
                          f'{pstr(c.text)})', 3)
            self.read_items(node.items, 2, node.key)
        self.finish(n, 2)
        self.line(f"return {self.construct(n)}, r.used()", 2)
        self.line()
        self.line("def encode_into(self, w: pk.BitWriter) -> None:", 1)
        self.doc(2,
                 f"Encodes this ``{n.key}`` packet with a writer, after what "
                 "it has written.", [("w", "The writer.")],
                 raises=[("pk.PacketError",
                          "A field's value can't be encoded; its ``kind`` says "
                          "why.")])
        body = len(self.out)
        self.need_hooks(n.hooks, 2)
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
            self.patch_length(length, 2)
        # Late checks first: transforming their data again changes what
        # the others cover.
        for c in n.checks:
            if c.late is not None:
                self.late_patch(c, 2)
        for c in n.checks:
            self.patch(c, 2)
        if len(self.out) == body:
            # A packet without entries, such as a meta-command without a
            # command container, writes nothing.
            self.line("pass", 2)
        self.line()
        self.alarms(n)
        self.algorithms(n)
        self.line("def describe(self) -> str:", 1)
        self.doc(2, f"Describes this ``{n.key}`` packet.",
                 returns="Its canonical text form, identical in every "
                         "generated language.")
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
        """Writes ``alarms()`` and ``monitor()``, which give each alarmed
        field's level, and a method computing each one.

        Args:
            n: The packet.
        """
        names = dict((lc, name) for name, lc in n.fields)
        place = lambda lc: self.place(n, lc, names)  # noqa: E731
        monitored = {k: ident for k, ident, _ in self.m.monitored}
        self.line("def alarms(self) -> list:", 1)
        self.doc(2,
                 f"Evaluates the alarms of this ``{n.key}`` packet's fields.  "
                 "Alarms that depend on earlier samples count only this one "
                 "(see ``monitor``).",
                 returns="(field, alarm level) for each field with an alarm; "
                         "the level is None when the field is absent.")
        self.line("return [", 2)
        for name, _, _ in n.alarms:
            self.line(f"({pstr(name)}, self._alarm_{name}()),", 3)
        self.line("]", 2)
        self.line()
        self.line("def monitor(self, m: AlarmMonitor, t: float = 0.0) -> list:",
                  1)
        self.doc(2,
                 f"Evaluates the alarms of this ``{n.key}`` packet's fields "
                 "like ``alarms()``, counting the earlier samples for change "
                 "ranges, minViolations and minConformance.", MONITOR_PARAMS,
                 returns="(field, alarm level) for each field with an alarm; "
                         "the level is None when the field is absent.")
        self.line("return [", 2)
        for name, _, a in n.alarms:
            call = (f"self._monitor_{name}(m, t)" if a.stateful
                    else f"self._alarm_{name}()")
            self.line(f"({pstr(name)}, {call}),", 3)
        self.line("]", 2)
        self.line()
        for name, lc, a in n.alarms:
            level = (f"The level of ``{name}``'s alarm, the worst of its "
                     "elements'" if lc.vtype.kind == "array"
                     else f"The level of ``{name}``'s alarm")
            level += (", or None when the field is absent." if lc.optional
                      else ".")
            self.line(f"def _alarm_{name}(self) -> Optional[pk.AlarmLevel]:", 1)
            self.doc(2,
                     f"Evaluates the alarm of field ``{name}`` (``{lc.key}``) "
                     "on this sample alone.", returns=level)
            self.alarm_body(name, lc, a, place, None)
            if a.stateful:
                self.line(f"def _monitor_{name}(self, m: AlarmMonitor, t: "
                          "float) -> Optional[pk.AlarmLevel]:", 1)
                self.doc(2,
                         f"Evaluates the alarm of field ``{name}`` "
                         f"(``{lc.key}``), counting the earlier samples whose "
                         "state ``m`` keeps and recording this one there.",
                         MONITOR_PARAMS, returns=level)
                self.alarm_body(name, lc, a, place, monitored[a.key])

    def algorithms(self, n: Node) -> None:
        """Writes ``algorithms()``, the output of each MathAlgorithm the packet
        triggers.

        Args:
            n: The packet.
        """
        names = dict((lc, name) for name, lc in n.fields)
        place = lambda lc: self.place(n, lc, names)  # noqa: E731
        self.line("def algorithms(self) -> list:", 1)
        self.doc(2,
                 f"Evaluates the MathAlgorithms that a ``{n.key}`` packet "
                 "triggers, from this one's values.",
                 returns="(output, value) for each; the value is None when an "
                         "input is absent or a custom algorithm's function "
                         "isn't set or fails.")
        self.line("return [", 2)
        for alg in n.algorithms:
            if alg.hook is not None:
                h = alg.hook
                self.line(f"({pstr(alg.name)}, pk.algorithm_hook(HOOKS, "
                          f"{pstr(h.ident)}, ({hook_args(alg.inputs, place)}), "
                          f"{h.outputs}, {alg.index})),", 3)
                continue
            value = math_expr(alg.expr, "x", place)
            absent = [f"{place(lc)} is None" for lc in inputs(alg.expr)
                      if lc.optional]
            if absent:
                value = f"None if {' or '.join(absent)} else {value}"
            self.line(f"({pstr(alg.name)}, {value}),", 3)
        self.line("]", 2)
        self.line()

    def alarm_body(self, name: str, lc, a, place, state) -> None:
        """Writes the body of a method giving a field's alarm level.

        Args:
            name:  The field's name.
            lc:    The field's Local.
            a:     The field's alarm.
            place: Gives the expression for a Local.
            state: The AlarmMonitor attribute that keeps the parameter's
                   earlier samples, or None to count this sample alone.
        """
        self.line(f"x = {place(lc)}", 2)
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
                self.line("s.push(pk.int_float(x), t)", 2)
            self.line("return pk.AlarmLevel(s.update(lv, mv, mc))", 2)
        self.line()

    def alarm_def(self, ad, kind: str, place, depth: int,
                  live: bool = False) -> None:
        """Writes the statements raising ``lv`` to the level that an alarm
        definition gives value ``x``.

        Args:
            ad:    The alarm definition.
            kind:  The kind of value ``x`` is.
            place: Gives the expression for a Local.
            depth: The statements' indentation level.
            live:  Whether to apply its change ranges (on AlarmState ``s``, at
                   time ``t``) and set ``mv`` and ``mc`` to its minViolations
                   and minConformance.
        """
        start = len(self.out)
        if live and ad.change is not None:
            c = ad.change
            self.line(f"d = s.change(pk.int_float(x), t, {c.samples}, "
                      f"{c.per_second}, {c.percent})", depth)
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
        if ad.custom is not None:
            h, ins = ad.custom
            self.line(f"lv = max(lv, pk.alarm_hook(HOOKS, {pstr(h.ident)}, "
                      f"({hook_args(ins, place)})))", depth)
        for level, terms in ad.conditions:
            cond = " and ".join(f"({term(c, place)})" for c in terms)
            self.line(f"if {cond}:", depth)
            self.line(f"lv = max(lv, {level})", depth + 1)
        if len(self.out) == start:
            self.line("pass", depth)

    def dfa(self, dfa) -> str:
        """Names the module constant holding a pattern's automaton, which
        ``emit`` writes at the end.

        Args:
            dfa: The automaton.

        Returns:
            The constant's name.
        """
        for name, d in self.dfas:
            if d is dfa:
                return name
        name = f"_DFA{len(self.dfas)}"
        self.dfas.append((name, dfa))
        return name

    def kind(self, k: Kind) -> None:
        """Writes the class that decodes any concrete packet derived from a
        root container.

        Args:
            k: The kind.
        """
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
        self.line(f"def decode_prefix(data) -> tuple[Union[{members}], int]:",
                  1)
        self.doc(2,
                 f"Decodes a packet derived from ``{k.root.key}`` that starts "
                 "at the first byte of ``data``: reads the root's entries, "
                 "then picks the first child (in document order) whose "
                 "criteria match, recursively.",
                 [("data", "The packet's bytes, and any after it.")],
                 "The packet, an instance of the member that matched, and the "
                 "number of bytes it takes.",
                 [("pk.PacketError",
                   "``NoMatch`` if no member matches, or another ``kind`` if "
                   "the packet can't be decoded.")])
        self.need_hooks(items_hooks(k.root.items), 2)
        self.line("r = pk.BitReader(data)", 2)
        self.read_items(k.root.items, 2, k.root.key)
        self.dispatch(k.root, 2)

    def dispatch(self, n: Node, depth: int) -> None:
        """Writes the statements that pick the packet a kind decodes.

        Each child whose criteria hold has its entries read, recursively; a
        concrete container's packet is returned, and an abstract one raises
        NoMatch.

        Args:
            n:     The container whose children are tried.
            depth: The statements' indentation level.
        """
        for c in n.children:
            if c.error:
                continue
            cond = (" and ".join(f"({term(x, lambda lc: f'v{lc.id}')})"
                                 for x in c.criteria) or "True")
            self.need_hooks(terms_hooks(c.criteria), depth)
            self.line(f"if {cond}:", depth)
            self.need_hooks(items_hooks(c.items), depth + 1)
            self.read_items(c.items, depth + 1, c.key)
            self.dispatch(c, depth + 1)
        if n.abstract:
            self.line(f'raise pk.PacketError("NoMatch", {pstr(n.key)})', depth)
        else:
            self.need_hooks(checks_hooks(n.checks), depth)
            self.finish(n, depth)
            self.line(f"return {self.construct(n)}, r.used()", depth)


# The parameters of the generated monitor methods.
MONITOR_PARAMS = [
    ("m",
     "The state of the alarms that depend on earlier samples, which the method "
     "reads and updates."), ("t", "This sample's time, in seconds.")]


def emit(mission: Mission, source: str) -> str:
    return PythonEmitter(mission, source).emit()
