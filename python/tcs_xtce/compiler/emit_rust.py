"""Emit a Rust module of packet codecs (uses ``tcs_xtce::packet``)."""

from __future__ import annotations

import json
import math
import textwrap
from typing import Optional

from .common import (HOOK_DOCS, doc_header, written, checks_hooks, items_hooks,
                     terms_hooks, notes, stream_capacity, stream_doc, MATH_OPS,
                     Ref, fast_order, field_range, flt, header_lines, inputs,
                     int_enc, invertible, math_value, size_args, str_cap,
                     binary_cap, stream_fn, string_spec, wire_region)
from .model import (
    term_indexes, term_locals, walk, Index, AggregateEnc, AllOf, AnyOf,
    ArrayEnc, BinaryEnc, Cond, ContextCal, Criterion, DynSize, Fixed, FloatEnc,
    IntEnc, Kind, Local, Mark, MathOp, Mission, Node, Pad, Polynomial, Seek,
    Span, Spline, StringEnc, Value, VType, Require, Fill, Segment, Verify,
    Custom, NARROW_MAX_BYTES, TailPad, TailSeek, LengthEnd, Transform, XformEnc)


def rstr(s: str) -> str:
    """Quotes text as a Rust string literal.

    Args:
        s: The text.

    Returns:
        The literal, with quotes and backslashes escaped and control
        characters written as Rust Unicode escapes.
    """
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
    """Names the Rust type of a value's generated field.

    Args:
        vt:       The value's type.
        alloc:    Whether the code may allocate; without it, arrays, strings
                  and binary values have fixed capacities, so they need no
                  heap.
        optional: Whether the field is present only when its include
                  condition holds, which makes it an ``Option``.

    Returns:
        The Rust type.
    """
    if optional:
        return f"Option<{field_type(vt, alloc)}>"
    k = vt.kind
    if k == "array":
        elem = field_type(vt.elem, alloc)
        return (f"Vec<{elem}>" if alloc
                else f"pk::FixedVec<{elem}, {vt.enc.max_count}>")
    if k == "aggregate":
        return vt.ident
    if k == "int" and vt.width > 128:
        return wide_type(vt)
    if k == "int":
        return f"{'i' if vt.signed else 'u'}{vt.width}"
    if k == "string":
        return "String" if alloc else f"pk::FixedString<{str_cap(vt)}>"
    if k == "binary":
        return "Vec<u8>" if alloc else f"pk::FixedBytes<{binary_cap(vt)}>"
    return {"float": "f64", "bool": "bool"}.get(k) or vt.ident


def byte_slice(v: int, bits: int) -> str:
    """The bytes of ``v``, of ``bits`` bits, most significant first."""
    b = v.to_bytes((bits + 7) // 8, "big")
    return "&[" + ", ".join(f"0x{x:02x}" for x in b) + "]"


def rflt(x: float) -> str:
    """An f64 literal, infinities included."""
    if math.isinf(x):
        return "f64::INFINITY" if x > 0 else "f64::NEG_INFINITY"
    return flt(x) + "_f64"


# The parameters of the generated monitor methods.
MONITOR_PARAMS = [
    ("m",
     "The state of the alarms that depend on earlier samples, which the method "
     "reads and updates."), ("t", "This sample's time, in seconds.")]

# The Rust type of each kind of hook.
HOOK_TYPES = {"criterion": "fn(&[f64]) -> bool",
              "algorithm": "fn(&[f64], &mut [f64]) -> bool",
              "alarm": "fn(&[f64]) -> u8",
              "checksum": "fn(&[u8], usize, usize) -> u64",
              "binary_from": "fn(&[u8], &mut [u8]) -> Option<(usize, usize)>",
              "binary_to": "fn(&[u8], &mut [u8]) -> Option<usize>",
              "time_from": "fn(&[u8]) -> Option<(usize, f64)>",
              "time_to": "fn(f64, &mut [u8]) -> Option<usize>"}


def wide_type(vt: VType) -> str:
    """The bnum type an integer of more than 64 bits is computed in: its
    own type, for more than 128 bits."""
    return f"pk::{'Int' if vt.signed else 'Uint'}<{vt.width // 8}>"


def wide_literal(vt: VType, v: int) -> str:
    """Writes a Rust literal of an integer of more than 64 bits.

    Args:
        vt: The integer's type.
        v:  The value, which the type holds.

    Returns:
        A ``u128`` or ``i128`` literal for 128 bits, else a bnum value built
        from its little-endian bytes.
    """
    if vt.width == 128:
        return f"{v}{'i' if vt.signed else 'u'}128"
    b = v.to_bytes(vt.width // 8, "little", signed=vt.signed)
    return (f"{wide_type(vt).replace('<', '::<')}::from_le_bytes(["
            + ", ".join(f"0x{x:02x}" for x in b) + "])")


def literal(vt: VType, v) -> str:
    """Writes a Rust literal of a value of a type.

    Args:
        vt: The type.
        v:  The value: an int, float, bool, str or bytes, as the type's kind
            needs; for an enumeration, its raw value.

    Returns:
        The literal; for a binary value, a byte slice.
    """
    k = vt.kind
    if k == "int" and vt.wide:
        return wide_literal(vt, v)
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
    return ("&[" + ", ".join(f"({flt(a)}, {flt(b)})" for a, b in cal.points)
            + "]")


def math_expr(e: tuple, x: str, place, f: str) -> str:
    """Writes the Rust expression of a MathOperationCalibrator
    or MathAlgorithm tree.

    Args:
        e:     The tree: ("const", x), ("raw",), ("value", ref) or an
               operator and its operands.
        x:     The expression for the value being calibrated, which
               ("raw",) stands for.
        place: Gives the expression for a Local whose value the tree uses.
        f:     The field's name, as a Rust string literal, for errors.

    Returns:
        An f64 expression.
    """
    if e[0] == "const":
        return f"({flt(e[1])})"
    if e[0] == "raw":
        return x
    if e[0] == "value":
        r = e[1]
        return math_value(r.vtype,
                          reach(place(r.local), r.member, rstr(r.local.name)),
                          "rust")
    args = [math_expr(a, x, place, f) for a in e[1:]]
    if e[0] in ("+", "-", "*"):
        return f"({args[0]} {e[0]} {args[1]})"
    b = args[1] if len(args) > 1 else "0.0"
    return f"pk::math(pk::MathOperator::{MATH_OPS[e[0]]}, {args[0]}, {b})"


def apply_cal(cal, x: str, f: str, place) -> str:
    """Writes the expression calibrating a raw value.

    Args:
        cal:   The calibrator, or None for none.
        x:     The f64 expression for the raw value.
        f:     The field's name, as a Rust string literal, for errors.
        place: Gives the expression for a Local that the calibrator uses.

    Returns:
        The f64 expression for the engineering value; a spline's fails with
        Calibration when the value is outside it.
    """
    if cal is None:
        return x
    if isinstance(cal, Polynomial):
        terms = ", ".join(f"({flt(c)}, {e})" for c, e in cal.terms)
        return f"pk::polynomial({x}, &[{terms}])"
    if isinstance(cal, Spline):
        ext = str(cal.extrapolate).lower()
        if cal.orders:
            orders = ", ".join(map(str, cal.orders))
            return (f"pk::spline_orders({x}, {points(cal)}, &[{orders}], "
                    f"{ext}, {f})?")
        return f"pk::spline({x}, {points(cal)}, {cal.order}, {ext}, {f})?"
    if isinstance(cal, MathOp):
        return f"{{ let x: f64 = {x}; {math_expr(cal.expr, 'x', place, f)} }}"
    return (f"{{ let x: f64 = {x}; "
            + choose(cal, lambda c: apply_cal(c, "x", f, place), place) + " }")


def invert_cal(cal, y: str, f: str, place) -> str:
    """Writes the expression inverting a calibrator.

    Args:
        cal:   The calibrator, or None for none.
        y:     The f64 expression for the engineering value.
        f:     The field's name, as a Rust string literal, for errors.
        place: Gives the expression for a Local that the calibrator uses.

    Returns:
        The f64 expression for the raw value, or for a calibrator that
        can't be inverted a statement returning NotInvertible.
    """
    if cal is None:
        return y
    if not cal.invertible:
        return f"return Err(PacketError::NotInvertible {{ field: {f} }})"
    if isinstance(cal, Polynomial):
        return (f"pk::polynomial_inverse({y}, {flt(cal.intercept)}, "
                f"{flt(cal.slope)})")
    if isinstance(cal, Spline):
        return (f"pk::spline_inverse({y}, {points(cal)}, "
                f"{str(cal.extrapolate).lower()}, {f})?")
    return (f"{{ let y: f64 = {y}; "
            + choose(cal, lambda c: invert_cal(c, "y", f, place), place) + " }")


def choose(cal: ContextCal, body, place) -> str:
    """Writes an if-else chain over a context calibrator's cases.

    Args:
        cal:   The context calibrator.
        body:  Gives the expression for one of its calibrators.
        place: Gives the expression for a Local that the criteria use.

    Returns:
        The chain: ``body(c)`` for the calibrator of the first case whose
        criteria hold, else for the default.
    """
    out = ""
    for terms, c in cal.cases:
        out += f"if {conjunction([term(t, place) for t in terms])} {{ "
        out += f"{body(c)} }} else "
    return out + f"{{ {body(cal.default)} }}"


def calibrate(vt: VType, x: str, f: str, place) -> str:
    return apply_cal(vt.calibrator, x, f, place)


def uncalibrate(vt: VType, y: str, f: str, place) -> str:
    return invert_cal(vt.calibrator, y, f, place)


def spec(vt: VType, ref, f: str) -> str:
    """Writes the StringSpec of a string type.

    Args:
        vt:  The type, whose encoding is a StringEnc.
        ref: Gives the expression for the value that gives a dynamic size.
        f:   The field's name, as a Rust string literal, for errors.

    Returns:
        A reference to the spec, sized by its dynamic size when it has
        one.
    """
    fixed, mx, term, tag = string_spec(vt)
    fixed = f"Some({fixed})" if fixed is not None else "None"
    term = (f"Some(&[{', '.join(f'0x{b:02x}' for b in term)}])"
            if term is not None else "None")
    cs = CHARSETS[vt.enc.charset]
    s = (f"StringSpec {{ fixed_bits: {fixed}, max_bits: {mx}, terminator: "
         f"{term}, size_tag_bits: {tag}, charset: pk::Charset::{cs} }}")
    d = vt.enc.dyn
    return f"&({s}).sized({size(d, ref(d), f)})" if d is not None else "&" + s


def source(d: DynSize, place) -> str:
    """Writes the expression for the value a bound DynSize
    adjusts.

    Args:
        d:     The DynSize.
        place: Gives the expression for a Local.

    Returns:
        A field (or its member or element), or for a DiscreteLookupList an
        i64 expression giving the first matching value, or the default.
    """
    if d.lookup is None:
        return reach(place(d.local), d.path, rstr(d.local.name))
    x = f"{d.default}i64"
    for criteria, v in reversed(d.lookup):
        x = (f"if {conjunction([term(c, place) for c in criteria])} {{ {v}i64 "
             f"}} else {{ {x} }}")
    return f"({x})"


def offset(s: Seek, ref, f: str) -> str:
    """Writes the expression for the offset of a Seek.

    Args:
        s:   The Seek.
        ref: Gives the value that a dynamic offset uses.
        f:   The name to give errors, as a Rust string literal.

    Returns:
        An i64 expression: the offset in bits, less the bits the Seek
        backs up.
    """
    if isinstance(s.offset, int):
        x = str(s.offset - s.back)
    else:
        n = size(s.offset, ref(s.offset), f)
        x = f"({n}) as i64" if s.offset.factor != 1 else f"{n} as i64"
        x = f"{x} - {s.back}" if s.back else x
    return f"{x} - {span(s.span, ref, f)} as i64" if s.span else x


def span(sp: Span, ref, f: str) -> str:
    """Writes the expression for the bits of a Span.

    Args:
        sp:  The Span.
        ref: Gives the value that a count or gap uses.
        f:   The name to give errors, as a Rust string literal.

    Returns:
        A usize expression.
    """
    count, gap = (str(d) if isinstance(d, int) else size(d, ref(d), f)
                  for d in (sp.count, sp.gap))
    return f"pk::span_bits({count}, {sp.each}, {gap})"


def tail_back(t: TailSeek, ref, f: str) -> str:
    """Writes the expression for the bits a TailSeek goes back from the end.

    Args:
        t:   The TailSeek.
        ref: Gives the value that a span uses.
        f:   The name to give errors, as a Rust string literal.

    Returns:
        A usize expression.
    """
    return f"{t.back} + {span(t.span, ref, f)}" if t.span else str(t.back)


def size(d: DynSize, x: str, f: str) -> str:
    """Writes the expression for a size or count that a value
    gives.

    Args:
        d: The DynSize.
        x: The expression for the value.
        f: The field's name, as a Rust string literal, for errors.

    Returns:
        A usize expression, scaled by the DynSize's factor; it fails with
        BadSize when the result isn't a size the DynSize allows.
    """
    v = f"pk::wide_f64({x})" if d.wide_type is not None else f"{x} as f64"
    n = (f"pk::dynamic_size({v}, {flt(d.slope)}, {flt(d.intercept)}, {d.unit}, "
         f"{d.max}, {f})?")
    return f"{n} * {d.factor}" if d.factor != 1 else n


def gap_expr(e: ArrayEnc, ref, f: str) -> str:
    """The bits between an array's elements, as an i64 expression."""
    if isinstance(e.gap, int):
        return str(e.gap)
    return f"({size(e.gap, ref(e.gap), f)}) as i64"


def count(vt: VType, ref, f: str) -> str:
    c = vt.enc.count
    return size(c, ref(c), f) if isinstance(c, DynSize) else f"{c}usize"


def member_sizes(e: AggregateEnc) -> set:
    """The members of an aggregate that give the size of another."""
    return {m.vtype.dyn.member for m in e.members if m.vtype.dyn is not None}


CHARSETS = {"UTF-8": "Utf8", "US-ASCII": "Ascii", "ISO-8859-1": "Latin1",
            "Windows-1252": "Windows1252", "UTF-16": "Utf16",
            "UTF-16BE": "Utf16Be", "UTF-16LE": "Utf16Le", "UTF-32": "Utf32",
            "UTF-32BE": "Utf32Be", "UTF-32LE": "Utf32Le"}
FLOATS = {"IEEE754": "Ieee", "MILSTD_1750A": "Mil1750a", "DEC": "Dec",
          "IBM": "Ibm", "TI": "Ti"}


def checksum_spec(spec: tuple) -> str:
    """Writes the runtime's Checksum for an
    ErrorDetectCorrect algorithm.

    Args:
        spec: The algorithm, as the model gives it: its kind, then its
              parameters (a CRC's width, polynomial, initial and final
              values and reflections; a custom one's hook).

    Returns:
        The ``pk::Checksum`` expression; a custom one's fails with
        NoAlgorithm while its hook isn't set.
    """
    kind = spec[0]
    if kind == "crc":
        width, poly, init, final, ref_in, ref_out = spec[1:]
        return (f"pk::Checksum::Crc {{ width: {width}, poly: 0x{poly:x}, init: "
                f"0x{init:x}, final_xor: 0x{final:x}, reflect_data: "
                f"{str(ref_in).lower()}, reflect_remainder: "
                f"{str(ref_out).lower()} }}")
    if kind == "custom":
        h = spec[1]
        return (f"pk::Checksum::Custom(*hooks::{h.ident.upper()}"
                ".get().ok_or(PacketError::NoAlgorithm { name: "
                f"{rstr(h.ident)} }})?)")
    names = {"sum": "Sum", "unix_sum": "UnixSum", "fletcher": "Fletcher",
             "adler32": "Adler32", "luhn": "Luhn", "verhoeff": "Verhoeff",
             "damm": "Damm", "xor": "Xor", "parity": "Parity"}
    if len(spec) == 1:
        return f"pk::Checksum::{names[kind]}"
    arg = str(spec[1]).lower()
    return f"pk::Checksum::{names[kind]}({arg})"


def byte_order(e) -> str:
    """Writes the runtime's ByteOrder of an encoding.

    Args:
        e: The integer or float encoding.

    Returns:
        ``pk::ByteOrder::Big``, ``Little``, or a list of the bytes' order.
    """
    if e.order == "be":
        return "pk::ByteOrder::Big"
    if e.order == "le":
        return "pk::ByteOrder::Little"
    return f"pk::ByteOrder::List(&[{', '.join(map(str, e.order))}])"


def raw_read(e, f: str) -> str:
    """Writes the expression reading the raw value of an integer
    or float encoding with BitReader ``r``.

    Args:
        e: The encoding.
        f: The field's name, as a Rust string literal, for errors.

    Returns:
        A u64 expression, or a u128 one for more than 64 bits.
    """
    if fast_order(e):
        return f"r.uint_order({e.bits}, {str(e.little_endian).lower()}, {f})?"
    x = (f"r.uint_bits({e.bits}, {str(e.lsb).lower()}, {byte_order(e)}, {f})?")
    return x if e.bits > 64 else f"({x} as u64)"


def raw_write(e, raw: str) -> str:
    """Writes the statement writing the raw value of an integer
    or float encoding with BitWriter ``w``.

    Args:
        e:   The encoding.
        raw: The expression for the raw value: a u64, or a u128 for more
             than 64 bits.

    Returns:
        The statement.
    """
    if fast_order(e):
        return f"w.uint_order({e.bits}, {str(e.little_endian).lower()}, {raw});"
    wide = raw if e.bits > 64 else f"{raw} as u128"
    return (f"w.uint_bits({e.bits}, {str(e.lsb).lower()}, {byte_order(e)}, "
            f"{wide});")


def read_expr(vt: VType, f: str, ref, alloc: bool, depth: int = 0) -> str:
    """Writes the expression reading one value with BitReader
    ``r``.

    Args:
        vt:    The value's type.
        f:     The field's name, as a Rust string literal, for errors.
        ref:   Gives the expression for the value that gives a size or count
               (a DynSize).
        alloc: Whether the code may allocate.
        depth: Numbers the locals of nested aggregates.

    Returns:
        The expression, which returns the decoding error from the function
        it is in.
    """
    x = read_value_expr(vt, f, ref, alloc, depth)
    if lsb_bytes(vt):
        return f"{{ r.set_lsb(true); let v = {x}; r.set_lsb(false); v }}"
    return x


def lsb_bytes(vt: VType) -> bool:
    """Whether ``vt`` is a string or binary value whose bytes are least
    significant bit first."""
    return isinstance(vt.enc, (StringEnc, BinaryEnc)) and vt.enc.lsb


def read_value_expr(vt: VType, f: str, ref, alloc: bool, depth: int) -> str:
    """Writes read_expr's expression, in the reader's usual
    bit order.

    Args:
        vt:    The value's type.
        f:     The field's name, as a Rust string literal, for errors.
        ref:   Gives the expression for the value that gives a size or count.
        alloc: Whether the code may allocate.
        depth: Numbers the locals of nested aggregates.

    Returns:
        The expression.
    """
    e = vt.enc
    if isinstance(e, AggregateEnc):
        a = f"a{depth}_"
        inner = Ref(lambda d: a + d.member if d.member else ref(d), ref.place)
        reads = []
        for m in e.members:
            x = read_expr(m.vtype, f, inner, alloc, depth + 1)
            reads.append((m.ident,
                          f"{{ r.pad({m.skip}, {f})?; {x} }}" if m.skip else x))
        if member_sizes(e):
            # Read members into locals, in order, so later ones can use them.
            lets = " ".join(f"let {a}{i} = {x};" for i, x in reads)
            pad = f" r.pad({e.pad}, {f})?;" if e.pad else ""
            fields = ", ".join(f"{i}: {a}{i}" for i, _ in reads)
            agg = f"{{ {lets}{pad} {vt.ident} {{ {fields} }} }}"
        else:
            agg = (f"{vt.ident} {{ {', '.join(f'{i}: {x}' for i, x in reads)} "
                   "}")
            if e.pad:
                agg = f"{{ let a = {agg}; r.pad({e.pad}, {f})?; a }}"
        if e.checks:
            # A repeated container's own criteria, on each copy.
            c = f"c{depth}"
            agg = f"{{ let {c} = {agg}; {checks(e, c)} {c} }}"
        x = e.transform
        if x is not None:
            # Read the members from what the transform makes of its bytes.
            h, raw = transform_hook(x.decode), opt_size(x.raw)
            t = (f"pk::read_binary_from(&mut r, {h}, {raw}, {x.cap}, {f})?"
                 if alloc
                 else f"pk::read_fixed_binary_from::<{x.cap}>(&mut r, {h}, "
                      f"{raw}, {f})?")
            agg = (f"{{ let t{depth} = {t}; let mut r = "
                   f"BitReader::new(&t{depth}[..]); {agg} }}")
        return agg
    if isinstance(e, ArrayEnc):
        gap = (f"if i > 0 {{ r.skip({gap_expr(e, ref, f)}, {f})?; }}" if e.gap
               else "")
        elem = read_expr(e.elem, f, ref, alloc, depth + 1)
        if alloc:
            return (f"{{ let n = {count(vt, ref, f)}; let mut a = "
                    f"Vec::with_capacity(n); for i in 0..n {{ {gap} "
                    f"a.push({elem}); }} a }}")
        full = f"return Err(PacketError::BadSize {{ field: {f} }})"
        return (f"{{ let n = {count(vt, ref, f)}; let mut a = pk::FixedVec::"
                f"<{field_type(e.elem, alloc)}, {e.max_count}>::new(); for i "
                f"in 0..n {{ {gap} if a.push({elem}).is_err() {{ {full} }} }} "
                "a }")
    if isinstance(e, XformEnc):
        h, raw = transform_hook(e.decode), opt_size(e.raw)
        if e.time:
            return f"r.time_from({h}, {raw}, {f})?"
        if alloc:
            return (f"pk::read_binary_from(&mut r, {h}, {raw}, {e.cap}, {f})?")
        return (f"pk::read_fixed_binary_from::<{e.cap}>(&mut r, {h}, {raw}, "
                f"{f})?")
    if isinstance(e, IntEnc) and vt.wide:
        return read_wide(vt, f)
    if isinstance(e, IntEnc) and e.bits > 64 and vt.kind in ("enum", "bool"):
        return read_wide_choice(vt, f)
    if isinstance(e, IntEnc):
        raw = raw_read(e, f)
        if e.bcd:
            raw = (f"pk::bcd_decode({raw}, {e.bits}, "
                   f"{str(e.encoding == 'packedBCD').lower()}, {f})?")
        enc = f"IntEncoding::{int_enc(vt)}"
        if vt.kind == "int" and vt.calibrator is not None:
            y = calibrate(vt, int_f64(vt, f), f, ref.place)
            lo, lim = vt.eng_limits
            return (f"pk::round_to_int({y}, {flt(lo)}, {flt(lim)}, {f})? as "
                    f"{field_type(vt, alloc)}")
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
        return calibrate(vt, int_f64(vt, f), f, ref.place)
    if isinstance(e, FloatEnc):
        raw = raw_read(e, f)
        if e.fmt == "IEEE754" and e.bits in (32, 64):
            return calibrate(vt, f"pk::float_from_raw({raw}, {e.bits})", f,
                             ref.place)
        wide = raw if e.bits > 64 else f"{raw} as u128"
        return calibrate(vt,
                         f"pk::float_decode(pk::FloatFormat::{FLOATS[e.fmt]}, "
                         f"{e.bits}, {wide}, {f})?", f, ref.place)
    if isinstance(e, BinaryEnc):
        read = "bytes" if alloc else f"fixed_bytes::<{e.bits // 8}>"
        if e.rest:
            return (f"{{ let n = r.rest_bytes({e.bits // 8}, {e.tail}, {f})?; "
                    f"r.{read}(n, {f})? }}")
        if e.embedded:
            return (f"{{ let n = r.embedded_bytes({e.bits // 8}, {f})?; "
                    f"r.{read}(n, {f})? }}")
        n = size(e.dyn, ref(e.dyn), f) if e.dyn else e.bits // 8
        b = f"r.{read}({n}, {f})?"
        if vt.kind != "string":
            return b
        return f"pk::{'' if alloc else 'fixed_'}string_from_bytes({b}, {f})?"
    read = ("read_string" if alloc
            else f"read_fixed_string::<{str_cap(vt)}, {wire_region(vt)}>")
    s = f"pk::{read}(&mut r, {spec(vt, ref, f)}, {f})?"
    if vt.kind == "bool":
        bad = f"return Err(PacketError::OutOfRange {{ field: {f} }})"
        return (f"match {s}.as_str() {{ {rstr(vt.one_string)} => true, "
                f"{rstr(vt.zero_string)} => false, _ => {bad} }}")
    return s


def wide_bytes(bits: int) -> int:
    """The bytes of the bnum integer that holds a raw field.

    Args:
        bits: The bits of the field, more than 64.

    Returns:
        16 up to 128 bits, as a u128 has, and otherwise the whole bytes
        the bits fill.
    """
    return 16 if bits <= 128 else -(-bits // 8)


def int_f64(vt: VType, f: str) -> str:
    """Expression reading the raw integer encoding of a float or calibrated
    integer as an f64, as its calibrator takes it.

    Args:
        vt: The type, whose encoding is an IntEnc.
        f:  The field's name, as a Rust string literal, for errors.

    Returns:
        The expression: pk::int_to_f64 of the raw value, or for more than
        64 bits pk::wide_int_to_f64 of the bnum integer it is read into.
    """
    e = vt.enc
    enc = f"IntEncoding::{int_enc(vt)}"
    packed = str(e.encoding == "packedBCD").lower()
    if e.bits <= 64:
        raw = raw_read(e, f)
        if e.bcd:
            raw = f"pk::bcd_decode({raw}, {e.bits}, {packed}, {f})?"
        return f"pk::int_to_f64({raw}, {e.bits}, {enc})"
    raw = (f"r.wide::<{wide_bytes(e.bits)}>({e.bits}, {str(e.lsb).lower()}, "
           f"{byte_order(e)}, {f})?")
    if e.bcd:
        raw = f"pk::wide_bcd_decode({raw}, {e.bits}, {packed}, {f})?"
    return f"pk::wide_int_to_f64({raw}, {e.bits}, {enc})"


def f64_int_write(vt: VType, y: str, f: str) -> str:
    """Statement writing the f64 ``y``, rounded, as the raw integer encoding
    of a float or calibrated integer.

    Args:
        vt: The type, whose encoding is an IntEnc.
        y:  The expression for the value, after any inverse calibrator.
        f:  The field's name, as a Rust string literal, for errors.

    Returns:
        The statement, which fails with OutOfRange when the encoding can't
        hold the rounded value.
    """
    e = vt.enc
    enc = f"IntEncoding::{int_enc(vt)}"
    packed = str(e.encoding == "packedBCD").lower()
    if e.bits <= 64:
        raw = f"pk::f64_to_int({y}, {e.bits}, {enc}, {f})?"
        if e.bcd:
            raw = f"pk::bcd_encode({raw}, {e.bits}, {packed}, {f})?"
        return raw_write(e, raw)
    raw = f"pk::f64_to_wide::<{wide_bytes(e.bits)}>({y}, {e.bits}, {enc}, {f})?"
    if e.bcd:
        raw = f"pk::wide_bcd_encode({raw}, {e.bits}, {packed}, {f})?"
    return (f"w.wide({e.bits}, {str(e.lsb).lower()}, {byte_order(e)}, "
            f"&{raw}.to_le_bytes());")


def length_u64(lc, x: str) -> str:
    """Expression for a packet length field's value as a u64.

    Args:
        lc: The field.
        x:  The expression for its value.

    Returns:
        The expression; for a field of more than 64 bits, one that fails
        with BadLength when a u64 can't hold the value.
    """
    if not lc.vtype.wide:
        return f"{x} as u64"
    return (f"pk::fits_u64({x}).ok_or(PacketError::BadLength {{ field: "
            f"{rstr(lc.name)} }})?")


def read_wide_choice(vt: VType, f: str) -> str:
    """Expression reading an enumeration or a boolean whose integer
    encoding has more than 64 bits.

    Args:
        vt: The type, whose encoding is an IntEnc.
        f:  The field's name, as a Rust string literal, for errors.

    Returns:
        The expression: a boolean that is true for any raw value but 0, or
        an enumeration holding the value, which fails with OutOfRange when
        an i64 can't hold it.
    """
    e = vt.enc
    # A bit more than the field, so that an unsigned value stays positive.
    n = (e.bits + 8) // 8
    raw = (f"r.wide::<{n}>({e.bits}, {str(e.lsb).lower()}, {byte_order(e)}, "
           f"{f})?")
    if e.bcd:
        raw = (f"pk::wide_bcd_decode({raw}, {e.bits}, "
               f"{str(e.encoding == 'packedBCD').lower()}, {f})?")
    if vt.kind == "bool":
        return f"!{raw}.is_zero()"
    v = f"pk::wide_to_signed({raw}, {e.bits}, IntEncoding::{int_enc(vt)})"
    return f"{vt.ident}(pk::wide_to_i64({v}, {f})?)"


def write_wide_choice(vt: VType, x: str, f: str) -> str:
    """Statement writing ``x``, an enumeration or a boolean whose integer
    encoding has more than 64 bits.

    Args:
        vt: The type, whose encoding is an IntEnc.
        x:  The value to write.
        f:  The field's name, as a Rust string literal, for errors.

    Returns:
        The statement, which fails with OutOfRange when the encoding can't
        hold an enumeration's value.
    """
    e = vt.enc
    n = (e.bits + 8) // 8
    packed = str(e.encoding == "packedBCD").lower()
    if vt.kind == "bool":
        raw = (f"pk::wide_from_unsigned(pk::widen::<{n}, _>({x} as "
               f"u8).cast_unsigned(), {e.bits}, {f})?")
    elif e.bcd:
        v = (f"pk::wide_from_signed(pk::widen::<{n}, _>({x}.0), {e.bits}, "
             f"IntEncoding::Unsigned, {f})?")
        raw = f"pk::wide_bcd_encode({v}, {e.bits}, {packed}, {f})?"
    else:
        raw = (f"pk::wide_from_signed(pk::widen::<{n}, _>({x}.0), {e.bits}, "
               f"IntEncoding::{int_enc(vt)}, {f})?")
    return (f"w.wide({e.bits}, {str(e.lsb).lower()}, {byte_order(e)}, "
            f"&{raw}.to_le_bytes());")


def read_wide(vt: VType, f: str) -> str:
    """Writes the expression reading an integer of more than 64
    bits.

    Args:
        vt: The integer's type, whose encoding is an IntEnc.
        f:  The field's name, as a Rust string literal, for errors.

    Returns:
        The expression: a ``u128`` or ``i128`` for 128 bits, else the bnum
        integer.
    """
    e = vt.enc
    n = vt.width // 8
    x = (f"r.wide::<{n}>({e.bits}, {str(e.lsb).lower()}, {byte_order(e)}, "
         f"{f})?")
    if e.bcd:
        x = (f"pk::wide_bcd_decode({x}, {e.bits}, "
             f"{str(e.encoding == 'packedBCD').lower()}, {f})?")
    if vt.signed:
        x = f"pk::wide_to_signed({x}, {e.bits}, IntEncoding::{int_enc(vt)})"
    if n == 16:
        x = f"{'i' if vt.signed else 'u'}128::from_le_bytes({x}.to_le_bytes())"
    return x


def write_wide(vt: VType, x: str, f: str) -> str:
    """Writes the statement writing an integer of more than 64
    bits.

    Args:
        vt: The integer's type, whose encoding is an IntEnc.
        x:  The expression for the value.
        f:  The field's name, as a Rust string literal, for errors.

    Returns:
        The statement, which fails with OutOfRange when the encoding can't
        hold the value.
    """
    e = vt.enc
    t = wide_type(vt).replace("<", "::<")
    v = f"{t}::from_le_bytes(({x}).to_le_bytes())"
    if e.bcd:
        raw = (f"pk::wide_bcd_encode({v}, {e.bits}, "
               f"{str(e.encoding == 'packedBCD').lower()}, {f})?")
    elif vt.signed:
        raw = (f"pk::wide_from_signed({v}, {e.bits}, "
               f"IntEncoding::{int_enc(vt)}, {f})?")
    else:
        raw = f"pk::wide_from_unsigned({v}, {e.bits}, {f})?"
    return (f"w.wide({e.bits}, {str(e.lsb).lower()}, {byte_order(e)}, "
            f"&{raw}.to_le_bytes());")


def checks(e: AggregateEnc, x: str) -> str:
    """Writes the checks of a repeated container's criteria on one
    copy of it.

    Args:
        e: The aggregate encoding of the copy, which holds the criteria.
        x: The expression for the copy.

    Returns:
        Statements failing with Mismatch unless the copy meets each
        criterion.
    """
    out = []
    for r in e.checks:
        cond = conjunction([term(t, lambda lc: x) for t in r.criteria])
        out.append(f"if !({cond}) {{ return Err(PacketError::Mismatch {{ "
                   f"container: {rstr(r.container)}, criterion: {rstr(r.text)} "
                   "}); }")
    return " ".join(out)


def write_stmt(vt: VType, x: str, f: str, ref) -> str:
    """Writes the statement writing a value with BitWriter
    ``w``.

    Args:
        vt:  The value's type.
        x:   The expression for the value: a place or a literal.
        f:   The field's name, as a Rust string literal, for errors.
        ref: Gives the expression for a value that gives a size or count.

    Returns:
        The statement; a value outside its type's valid ranges fails with
        OutOfRange.
    """
    stmt = write_value_stmt(vt, x, f, ref)
    if not (vt.valid and vt.kind in ("int", "float")):
        return stmt
    tests = []
    for b in vt.valid:
        if vt.wide:
            parts = ([f"{x} >= {literal(vt, b.lo)}"] if b.lo is not None
                     else []) + ([f"{x} <= {literal(vt, b.hi)}"]
                                 if b.hi is not None else [])
            tests.append(" && ".join(parts) or "true")
        elif vt.kind == "int":
            parts = ([f"{b.lo}i128 <= {x} as i128"] if b.lo is not None
                     else []) + ([f"{x} as i128 <= {b.hi}i128"]
                                 if b.hi is not None else [])
            tests.append(" && ".join(parts) or "true")
        else:
            def opt(v):
                return "None" if v is None else f"Some({rflt(v)})"
            tests.append(f"pk::in_band({x}, {opt(b.lo)}, "
                         f"{str(b.lo_incl).lower()}, {opt(b.hi)}, "
                         f"{str(b.hi_incl).lower()})")
    test = " || ".join(f"({t})" for t in tests)
    return (f"{{ if !({test}) {{ return Err(PacketError::OutOfRange {{ field: "
            f"{f} }}); }} {stmt} }}")


def write_value_stmt(vt: VType, x: str, f: str, ref) -> str:
    """Writes write_stmt's statement, without the check of
    the valid ranges.

    Args:
        vt:  The value's type.
        x:   The expression for the value.
        f:   The field's name, as a Rust string literal, for errors.
        ref: Gives the expression for a value that gives a size or count.

    Returns:
        The statement, with the writer least significant bit first around
        it for such a string or binary value.
    """
    s = write_bytes_stmt(vt, x, f, ref)
    if lsb_bytes(vt):
        return f"{{ w.set_lsb(true); {s} w.set_lsb(false); }}"
    return s


def write_bytes_stmt(vt: VType, x: str, f: str, ref) -> str:
    """Writes write_value_stmt's statement, in the
    writer's usual bit order.

    Args:
        vt:  The value's type.
        x:   The expression for the value.
        f:   The field's name, as a Rust string literal, for errors.
        ref: Gives the expression for a value that gives a size or count.

    Returns:
        The statement; it fails with BadSize when an array or binary value
        doesn't have the size its DynSize gives, and with NotInvertible
        when its calibrator can't be inverted.
    """
    e = vt.enc
    bad = f"return Err(PacketError::BadSize {{ field: {f} }});"
    if isinstance(e, AggregateEnc):
        inner = Ref(lambda d: f"{x}.{d.member}" if d.member else ref(d),
                    ref.place)
        parts = [checks(e, x)] if e.checks else []
        t = e.transform
        if t is not None:
            # Write the members, then what the transform makes of them.
            parts.append("let t = w.position();")
        for m in e.members:
            if m.skip:
                parts.append(f"w.zeros({m.skip});")
            parts.append(write_stmt(m.vtype, f"{x}.{m.ident}", f, inner))
        if e.pad:
            parts.append(f"w.zeros({e.pad});")
        if t is not None:
            parts.append(f"w.transform(t, {transform_hook(t.encode)}, "
                         f"{opt_size(t.raw)}, {t.raw}, {f})?;")
        return "{ " + " ".join(parts) + " }"
    if isinstance(e, ArrayEnc):
        gap = (f"if i > 0 {{ w.skip({gap_expr(e, ref, f)}, {f})?; }}" if e.gap
               else "")
        return (f"{{ let n = {count(vt, ref, f)}; if {x}.len() != n {{ {bad} "
                f"}} for (i, {elem_pattern(e.elem)}) in {x}.iter().enumerate() "
                f"{{ {gap} {write_stmt(e.elem, 'e', f, ref)} }} }}")
    if isinstance(e, XformEnc):
        h, raw = transform_hook(e.encode), opt_size(e.raw)
        cap = e.bits // 8
        if e.time:
            return f"w.time_to({h}, {x}, {raw}, {cap}, {f})?;"
        return f"w.binary_to({h}, &{x}, {raw}, {cap}, {f})?;"
    data = f"{x}.as_bytes()" if vt.kind == "string" else f"&{x}"
    if isinstance(e, BinaryEnc) and e.dyn is not None:
        n = size(e.dyn, ref(e.dyn), f)
        return (f"{{ let n = {n}; if {x}.len() != n {{ {bad} }} "
                f"w.bytes({data}); }}")
    if isinstance(e, IntEnc) and vt.wide:
        return write_wide(vt, x, f)
    if isinstance(e, IntEnc) and e.bits > 64 and vt.kind in ("enum", "bool"):
        return write_wide_choice(vt, x, f)
    if isinstance(e, IntEnc):
        bits = e.bits
        enc = f"IntEncoding::{int_enc(vt)}"
        if vt.kind == "int" and vt.calibrator is not None:
            if not invertible(vt):
                return (f"return Err(PacketError::NotInvertible {{ field: {f} "
                        "});")
            return f64_int_write(
                vt, uncalibrate(vt, f"{x} as f64", f, ref.place), f)
        elif vt.kind == "int":
            raw = (f"pk::from_signed({x} as i64, {bits}, {enc}, {f})?"
                   if vt.signed
                   else f"pk::from_unsigned({x} as u64, {bits}, {f})?")
        elif vt.kind == "enum":
            raw = f"pk::from_signed({x}.0, {bits}, {enc}, {f})?"
        elif vt.kind == "bool":
            raw = f"{x} as u64"
        else:
            if not invertible(vt):
                return (f"return Err(PacketError::NotInvertible {{ field: {f} "
                        "});")
            return f64_int_write(vt, uncalibrate(vt, x, f, ref.place), f)
        if e.bcd:
            raw = (f"pk::bcd_encode({raw}, {bits}, "
                   f"{str(e.encoding == 'packedBCD').lower()}, {f})?")
        return raw_write(e, raw)
    if isinstance(e, FloatEnc):
        if not invertible(vt):
            return f"return Err(PacketError::NotInvertible {{ field: {f} }});"
        y = uncalibrate(vt, x, f, ref.place)
        if e.fmt == "IEEE754" and e.bits in (32, 64):
            return raw_write(e, f"pk::float_to_raw({y}, {e.bits})")
        raw = (f"pk::float_encode(pk::FloatFormat::{FLOATS[e.fmt]}, {e.bits}, "
               f"{y}, {f})?")
        return raw_write(e, raw if e.bits > 64 else f"({raw} as u64)")
    if isinstance(e, BinaryEnc) and e.rest:
        return f"pk::write_rest(w, &{x}, {e.bits // 8}, {f})?;"
    if isinstance(e, BinaryEnc) and e.embedded:
        return f"pk::write_embedded(w, &{x}, {e.bits // 8}, {f})?;"
    if isinstance(e, BinaryEnc):
        return f"pk::write_binary(w, {data}, {e.bits // 8}, {f})?;"
    if vt.kind == "bool":
        s = (f"if {x} {{ {rstr(vt.one_string)} }} else {{ "
             f"{rstr(vt.zero_string)} }}")
        return f"pk::write_string(w, {spec(vt, ref, f)}, {s}, {f})?;"
    return f"pk::write_string(w, {spec(vt, ref, f)}, &{x}, {f})?;"


def elem_pattern(vt: VType) -> str:
    """Pattern binding ``e`` to an element of a slice: by value when it is
    Copy, so that ``e`` can be used like a field."""
    return ("e" if vt.kind in ("string", "binary", "aggregate", "array")
            else "&e")


def describe(vt: VType, x: str) -> str:
    """Writes the expression writing a value's canonical text to
    the formatter ``f``, without allocating.

    Args:
        vt: The value's type.
        x:  The expression for the value.

    Returns:
        The expression, which returns a formatting error from the function
        it is in.
    """
    k = vt.kind
    if k == "aggregate":
        parts = []
        for i, m in enumerate(vt.enc.members):
            parts.append("f.write_str("
                         f"{rstr((', ' if i else '') + m.ident + '=')})?;")
            parts.append(describe(m.vtype, f"({x}).{m.ident}") + ";")
        return ("{ f.write_char('{')?; " + " ".join(parts)
                + " f.write_char('}')? }")
    if k == "array":
        el = describe(vt.elem, "e")
        pat = elem_pattern(vt.elem)
        return (f"{{ f.write_char('[')?; for (i, {pat}) in "
                f"({x}).iter().enumerate() {{ if i > 0 {{ f.write_str(\", "
                f"\")?; }} {el}; }} f.write_char(']')? }}")
    if k == "float":
        return f"pk::describe_f64({x}, f)?"
    if k == "string":
        return f"pk::describe_str(&{x}, f)?"
    if k == "binary":
        return f"pk::describe_bytes(&{x}, f)?"
    if k == "int" and vt.width > 128:
        return f"pk::write_{'int' if vt.signed else 'uint'}({x}, f)?"
    return f"write!(f, \"{{}}\", {x})?"


def conjunction(conds: list) -> str:
    """Joins conditions with ``&&``.

    Args:
        conds: The Rust boolean expressions.

    Returns:
        ``true`` for none, the one for one, else each in parentheses joined
        with ``&&``.
    """
    if not conds:
        return "true"
    return conds[0] if len(conds) == 1 else " && ".join(f"({c})" for c in conds)


def reach(x: str, path, f: str) -> str:
    """Writes the expression for a member or element of a value.

    Args:
        x:    The expression for the value.
        path: The member names and Indexes leading to it.
        f:    The name to give BadSize, as a Rust string literal.

    Returns:
        The expression; an element of an array whose count varies may be
        missing, which fails with BadSize.
    """
    for p in path:
        if not isinstance(p, Index):
            x += "." + p
        elif p.stride:
            x = (f"(*pk::element_at(&{x}, {p.i}, {p.stride}, {p.per}, "
                 f"{p.need}, {f})?)")
        elif p.count is None:
            x = f"(*pk::element(&{x}, {p.i}, {f})?)"
        else:
            x += f"[{p.i}]"
    return x


def term(t, place) -> str:
    """Writes the expression for a criterion or a tree of them.

    Args:
        t:     The Criterion, AllOf, AnyOf or Custom.
        place: Gives the expression for a Local.

    Returns:
        A boolean expression.  An absent value compares as C++'s
        std::optional does: less than any value.
    """
    if isinstance(t, (AllOf, AnyOf)):
        return (" && " if isinstance(t, AllOf)
                else " || ").join(f"({term(x, place)})" for x in t.terms)
    if isinstance(t, Custom):
        return (f"hooks::{t.hook.ident.upper()}.get().is_some_and(|f| "
                f"f(&[{hook_args(t.inputs, place)}]))")
    if t.local.optional:
        # An absent value compares as std::optional's does: less than any.
        return (f"{place(t.local)}.as_ref().map_or({EMPTY[t.op]}, |o| "
                f"{condition(t, '(*o)')})")
    return condition(t, place(t.local),
                     place(t.other) if t.other is not None else None)


def transform_hook(h) -> str:
    """A transform hook, or None for a way the document doesn't give."""
    return f"hooks::{h.ident.upper()}.get().copied()" if h else "None"


def opt_size(n) -> str:
    return "None" if n is None else f"Some({n})"


def hook_args(inputs: list, place) -> str:
    """Writes the items of a slice of a custom algorithm's
    inputs.

    Args:
        inputs: Each input: ("const", x), or a Local, its member path and
                its type.
        place:  Gives the expression for a Local.

    Returns:
        The f64 expressions, separated by commas.
    """
    return ", ".join(rflt(x[1]) if x[0] == "const"
                     else math_value(x[3],
                                     reach(place(x[1]), x[2], rstr(x[1].name)),
                                     "rust") for x in inputs)


# What ``<absent value> op <value>`` gives, as with std::optional in C++.
EMPTY = {"==": "false", "!=": "true", "<": "true", "<=": "true", ">": "false",
         ">=": "false"}


def condition(c: Criterion, x: str, y=None) -> str:
    """Writes the comparison of one criterion.

    Args:
        c: The criterion.
        x: The expression for the criterion's Local.
        y: The expression for the Local it compares with, or None to
           compare with its constant.

    Returns:
        A boolean expression; one that every value of the field gives the
        same answer to is ``true`` or ``false``.
    """
    vt = c.vtype
    x = reach(x, c.path, rstr(c.local.name))
    if y is not None:
        y = reach(y, c.other_path, rstr(c.other.name))
        ovt = c.other_target or c.other.vtype
        if vt.kind == "int" and (vt.wide or ovt.wide):
            # In a type that holds both, signed or not.
            m = max(vt.width, ovt.width) // 8 + 1
            return (f"pk::widen::<{m}, _>({x}) {c.op} pk::widen::<{m}, _>({y})")
        if vt.kind == "int":
            return f"({x} as i128) {c.op} ({y} as i128)"
        if vt.kind == "enum":
            return f"{x}.0 {c.op} {y}.0"
        return f"{x} {c.op} {y}"
    if vt.kind == "int" and vt.wide:
        lo, hi = field_range(vt)
        if not lo <= c.value <= hi:
            # Every value of the field compares the same way.
            below = c.value < lo
            return str({"==": False, "!=": True, "<": not below,
                        "<=": not below, ">": below, ">=": below}[c.op]).lower()
        return f"{x} {c.op} {literal(vt, c.value)}"
    if vt.kind == "int":
        return f"({x} as i128) {c.op} {c.value}"
    if vt.kind == "enum":
        return f"{x}.0 {c.op} {c.value}"
    if vt.kind == "binary":
        return f"{x}[..] {c.op} *{literal(vt, c.value)}"
    return f"{x} {c.op} {literal(vt, c.value)}"


class RustEmitter:
    def __init__(self, mission: Mission, source: str, alloc: bool):
        """Starts a Rust module.

        Args:
            mission: The compiled mission.
            source:  The name of the XTCE documents, for the notice at the top.
            alloc:   Whether the code may allocate.
        """
        self.m = mission
        self.source = source
        self.alloc = alloc
        self.dfas: list = []
        self.out: list[str] = []

    def line(self, s: str = "", depth: int = 0) -> None:
        self.out.append("    " * depth + s if s else "")

    def doc(self, depth: int, summary: str, params: list[tuple[str, str]] = (),
            returns: str | None = None, errors: str | None = None) -> None:
        """Writes the rustdoc header of a generated function.

        Args:
            depth:   The function's indentation level.
            summary: What the function does.
            params:  Each parameter but the receiver, with what it means.
            returns: What it returns, or None if it returns nothing.
            errors:  When it returns an error, for one returning a
                     Result.
        """
        for text in doc_header("rust", summary, params, returns, errors,
                               indent=4 * depth):
            self.line(text, depth)

    def emit(self) -> str:
        """Writes the whole module.

        Returns:
            The module's source, before rustfmt.
        """
        m = self.m
        for h in header_lines(m, self.source):
            self.line("// " + h)
        self.line(f"//! Packet codecs for the `{m.name}` mission.")
        if not self.alloc:
            self.line("//!")
            self.line("//! Generated with `--dynamic-allocations false`: "
                      "arrays, strings and binary")
            self.line("//! fields have fixed capacities, and decoding, "
                      "encoding and describing a")
            self.line("//! packet never allocate.")
        self.line("#![allow(dead_code, unused_imports, unused_variables, "
                  "unused_assignments, unused_mut, unreachable_code, "
                  "unused_parens, clippy::all)]")
        self.line()
        self.line("use std::fmt::{self, Write as _};")
        self.line("use tcs_xtce::packet::{self as pk, BitReader, BitWriter, "
                  "IntEncoding, Packet, PacketError, Result, StringSpec};")
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
            self.line(f"/// The automaton matching `{dfa.pattern}`.")
            self.line(f"static {name}: pk::Dfa = pk::Dfa {{")
            self.line("starts: &[" + ", ".join(map(str, dfa.starts)) + "],", 1)
            self.line("trans: &["
                      + ", ".join(f"({a}, {b}, {c})" for a, b, c in dfa.trans)
                      + "],", 1)
            self.line("accept: &["
                      + ", ".join(str(a).lower() for a in dfa.accept) + "],", 1)
            self.line("};")
        return "\n".join(self.out) + "\n"

    def stream(self, st) -> None:
        """Writes a stream's type: its frame
        synchronizer, ``new`` and ``push``.

        Args:
            st: The stream.
        """
        sync = "FrameSync" if st.fixed else "FlagSync"
        pcm = {"NRZL": "Nrzl", "NRZM": "Nrzm", "NRZS": "Nrzs",
               "BiPhaseL": "BiphaseL", "BiPhaseM": "BiphaseM",
               "BiPhaseS": "BiphaseS"}[st.pcm]
        fns = [(k, stream_fn(k, x)) for k, x in st.functions()]
        types = {"decode": "pk::StreamDecode", "invert": "pk::BitInvert"}
        self.line()
        doc = stream_doc(st).replace(st.key, f"`{st.key}`")
        if st.container:
            doc = doc.replace(st.container, f"`{st.container}`")
        if st.feeder is not None:
            doc += (f" Its input is what stream `{st.feeder.key}` gives, from "
                    "the bytes pushed.")
        if fns:
            doc += (" The application gives it its functions: "
                    + ", ".join(f"`{n}`" for _, n in fns) + ".")
        for text in textwrap.wrap(doc, 72):
            self.line(f"/// {text}")
        self.line("#[derive(Debug, Clone)]")
        self.line(f"pub struct {st.ident} {{")
        if st.custom:
            self.line("decode: pk::StreamDecode,", 1)
        else:
            bits, size = stream_capacity(st)
            self.line(f"sync: pk::{sync}<{bits}, {size}>,", 1)
        if st.feeder is not None:
            self.line(f"feeder: {st.feeder.ident},", 1)
        self.line("}")
        self.line()
        if not fns:
            self.line(f"impl Default for {st.ident} {{")
            self.line("fn default() -> Self {", 1)
            self.line("Self::new()", 2)
            self.line("}", 1)
            self.line("}")
            self.line()
        self.line(f"impl {st.ident} {{")
        if not st.custom:
            self.line("/// The container that each frame is.", 1)
            self.line("pub const CONTAINER: &'static str = "
                      f"{rstr(st.container)};", 1)
            if st.fixed:
                self.line("pub const SPEC: pk::FrameSpec = pk::FrameSpec {", 1)
                fields = (
                    ("frame_bits", st.frame_bits),
                    ("pattern", byte_slice(st.pattern, st.pattern_bits)),
                    ("mask", byte_slice(st.mask, st.pattern_bits)),
                    ("pattern_bits", st.pattern_bits),
                    ("location", st.location), ("max_errors", st.max_errors),
                    ("aperture", st.aperture), ("verify", st.verify),
                    ("check", st.check), ("invert_after", st.invert_after),
                    ("pcm", f"pk::Pcm::{pcm}"),
                    ("inverted", str(st.inverted).lower()))
            else:
                self.line("pub const SPEC: pk::FlagSpec = pk::FlagSpec {", 1)
                fields = (("flag_bits", st.flag_bits),
                          ("flag_ones", str(st.flag_ones).lower()),
                          ("max_bits", st.max_bits), ("pcm", f"pk::Pcm::{pcm}"),
                          ("inverted", str(st.inverted).lower()))
            for name, v in fields:
                self.line(f"{name}: {v},", 2)
            self.line("};", 1)
            self.line()
        params = ", ".join(f"{n}: {types[k]}" for k, n in fns)
        what = {"decode": "The DecodingAlgorithm `{}` of stream `{}`, which "
                          "gives the bytes it decodes from those it is given.",
                "invert": "The InvertAlgorithm `{}` of stream `{}`: what a bit "
                          "is once AutoInvert has inverted the stream."}
        self.doc(1, f"Makes a stream `{st.key}` with nothing pushed to it yet.",
                 [(stream_fn(k, x),
                   what[k].format(x.decoder if k == "decode" else x.inverter,
                                  x.key)) for k, x in st.functions()],
                 "The stream, ready for `push`.")
        self.line(f"pub fn new({params}) -> Self {{", 1)
        self.line("Self {", 2)
        if st.custom:
            self.line(f"decode: {stream_fn('decode', st)},", 3)
        elif st.inverter:
            self.line(f"sync: pk::{sync}::new(Self::SPEC).with_invert("
                      f"{stream_fn('invert', st)}),", 3)
        else:
            self.line(f"sync: pk::{sync}::new(Self::SPEC),", 3)
        if st.feeder is not None:
            args = ", ".join(stream_fn(k, x) for k, x in st.feeder.functions())
            self.line(f"feeder: {st.feeder.ident}::new({args}),", 3)
        self.line("}", 2)
        self.line("}", 1)
        self.line()
        what = ("the bytes it decodes" if st.custom
                else "each frame they complete (its bits padded with zeros)")
        self.doc(
            1,
            f"Takes the next bytes of stream `{st.key}`, and gives `f` {what}.",
            [("data", "The stream's next bytes, in the order they arrive."),
             ("f",
              f"Called with each piece of the bytes that `{st.decoder}` "
              "decodes." if st.custom
              else f"Called with the bytes of each frame, a `{st.container}` "
                   "packet.")])
        self.line("pub fn push(&mut self, data: &[u8], mut f: impl "
                  "FnMut(&[u8])) {", 1)
        if st.custom and st.feeder is None:
            self.line("(self.decode)(data, &mut f);", 2)
        elif st.custom:
            self.line("let decode = self.decode;", 2)
            self.line("self.feeder.push(data, |x| decode(x, &mut f));", 2)
        elif st.feeder is None:
            self.line("self.sync.push(data, f);", 2)
        else:
            self.line("let sync = &mut self.sync;", 2)
            self.line("self.feeder.push(data, |x| sync.push(x, &mut f));", 2)
        self.line("}", 1)
        self.line("}")

    def transform_buffers(self, nodes: list, depth: int) -> None:
        """Declares the buffers of the
        transformed data that decoding some containers reads, before the reader,
        so that it can read them.

        Args:
            nodes: The containers.
            depth: The indentation of the declarations.
        """
        for node in nodes:
            for it in walk(node.items):
                if isinstance(it, Transform):
                    self.line(f"let mut t{it.start} = [0u8; {it.cap}];", depth)

    def need_hooks(self, hooks: list, depth: int) -> None:
        """Writes statements failing with NoAlgorithm
        unless each of some hooks is set.

        Args:
            hooks: The hooks.
            depth: The indentation of the statements.
        """
        for h in hooks:
            self.line(f"if hooks::{h.ident.upper()}.get().is_none() {{", depth)
            self.line("return Err(PacketError::NoAlgorithm { name: "
                      f"{rstr(h.ident)} }});", depth + 1)
            self.line("}", depth)

    def hooks(self) -> None:
        """Writes the ``hooks`` module: a static for each
        function the application supplies for the document's custom
        algorithms.
        """
        self.line()
        self.line("/// The functions the application supplies for the "
                  "document's custom")
        self.line("/// algorithms: set them before decoding or encoding a "
                  "packet that needs")
        self.line("/// them, or it fails with `NoAlgorithm`.")
        self.line("pub mod hooks {")
        if self.m.hooks:
            self.line("use std::sync::OnceLock;", 1)
        for h in self.m.hooks:
            self.line()
            for text in textwrap.wrap(f"`{h.name}`, {HOOK_DOCS[h.kind]}", 72):
                self.line(f"/// {text}", 1)
            self.line(f"pub static {h.ident.upper()}: OnceLock"
                      f"<{HOOK_TYPES[h.kind]}> = OnceLock::new();", 1)
        self.line("}")

    def alarm_monitor(self) -> None:
        """Writes ``AlarmMonitor``: the state of each
        parameter whose alarms depend on earlier samples.
        """
        self.line()
        self.line("/// The alarm state of each parameter whose alarms depend "
                  "on earlier")
        self.line("/// samples, which the packets' `monitor()` reads and "
                  "updates.")
        self.line("#[derive(Debug, Clone, Default)]")
        self.line("pub struct AlarmMonitor {")
        for key, ident, history in self.m.monitored:
            self.line(f"/// `{key}`", 1)
            self.line(f"pub {ident}: pk::AlarmState<{history}>,", 1)
        self.line("}")

    def enum_type(self, vt: VType) -> None:
        """Writes an enumeration's type, its constants,
        ``label`` and its Display.

        Args:
            vt: The enumerated type.
        """
        self.line()
        self.line(f"/// Enumeration `{vt.key}`; holds the raw value so "
                  "unlisted values survive decoding.")
        self.line("#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default)]")
        self.line(f"pub struct {vt.ident}(pub i64);")
        self.line()
        self.line(f"impl {vt.ident} {{")
        for lab in vt.labels:
            self.line(f"pub const {lab.ident}: Self = Self({lab.value});", 1)
        self.line()
        self.doc(1, f"Names this value of enumeration `{vt.key}`.",
                 returns="The value's label, or `None` if the enumeration "
                         "doesn't list it.")
        self.line("pub fn label(self) -> Option<&'static str> {", 1)
        self.line("match self.0 {", 2)
        for lab in vt.labels:
            pat = (str(lab.value) if lab.value == lab.max_value
                   else f"{lab.value}..={lab.max_value}")
            self.line(f"{pat} => Some({rstr(lab.label)}),", 3)
        self.line("_ => None,", 3)
        self.line("}", 2)
        self.line("}", 1)
        self.line("}")
        self.line()
        self.line(f"impl fmt::Display for {vt.ident} {{")
        self.line("fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {",
                  1)
        self.line("match self.label() {", 2)
        self.line("Some(l) => f.write_str(l),", 3)
        self.line("None => write!(f, \"{}\", self.0),", 3)
        self.line("}", 2)
        self.line("}", 1)
        self.line("}")

    def aggregate_type(self, vt: VType) -> None:
        """Writes an aggregate's struct.

        Args:
            vt: The aggregate type.
        """
        self.line()
        self.line(f"/// Aggregate `{vt.key}`; its members are encoded in this "
                  "order.")
        self.line("#[derive(Debug, Clone, PartialEq, Default)]")
        self.line(f"pub struct {vt.ident} {{")
        for m in vt.enc.members:
            self.line(f"/// `{m.name}` ({m.vtype.key})", 1)
            self.line(f"pub {m.ident}: {field_type(m.vtype, self.alloc)},", 1)
        self.line("}")

    # -- decoding --

    def read_items(self, items, depth: int, where: str,
                   assign: bool = False) -> None:
        """Writes the statements decoding a container's
        entries with BitReader ``r``, into locals ``v<id>``.

        Args:
            items:  The entries.
            depth:  The indentation of the statements.
            where:  The container's name, for errors.
            assign: Whether the values are inside an include condition, so that
                    their ``Option`` locals are declared already and are
                    assigned.
        """
        for it in items:
            if isinstance(it, Seek):
                off = offset(it, self.read_ref, rstr(where))
                if it.from_start is None:
                    self.line(f"r.skip({off}, {rstr(where)})?;", depth)
                else:
                    seek = "seek_ahead" if it.ahead else "seek"
                    self.line(f"r.{seek}({self.mark(it.from_start)}, {off}, "
                              f"{rstr(where)})?;", depth)
            elif isinstance(it, Mark):
                self.line(f"let m{it.id} = r.position();", depth)
            elif isinstance(it, Pad):
                self.line(f"r.pad({it.bits}, {rstr(where)})?;", depth)
            elif isinstance(it, TailSeek):
                back = tail_back(it, self.read_ref, rstr(where))
                self.line(f"r.seek_end({back}, {rstr(where)})?;", depth)
            elif isinstance(it, LengthEnd):
                self.line(f"r.check_span(e{it.local.id}, "
                          f"{rstr(it.local.name)})?;", depth)
            elif isinstance(it, Transform):
                # Read its items from what the transform makes of its bytes,
                # into the buffer the decoding function begins with.
                t, f = it.start, rstr(it.name)
                self.line(f"let n{t} = "
                          f"r.binary_from({transform_hook(it.decode)}, "
                          f"{opt_size(it.raw)}, &mut t{t}, {f})?;", depth)
                self.line(f"let r{t} = std::mem::replace(&mut r, "
                          f"BitReader::new(&t{t}[..n{t}]));", depth)
                self.read_items(it.items, depth, where, assign)
                if it.length is not None:
                    self.line(f"r.finish({rstr(it.length.name)})?;", depth)
                self.line(f"r = r{t};", depth)
            elif isinstance(it, TailPad):
                # The packet's last bits, which encoding fills with zeros.
                self.line(f"r.skip({it.bits}, {rstr(where)})?;", depth)
            elif isinstance(it, Require):
                self.require(it, lambda lc: f"v{lc.id}", depth)
            elif isinstance(it, Verify):
                lc = it.check.local
                self.verify(it.check,
                            f"v{lc.id}.unwrap_or_default()" if assign
                            else f"v{lc.id}", depth)
            elif isinstance(it, Fill):
                self.fill(it, "r", self.read_ref, depth)
            elif isinstance(it, Segment):
                lc = it.local
                part = (f"(r.uint({it.bits}, {rstr(lc.name)})? << {it.shift})")
                self.line(f"let s{lc.id} = "
                          f"{part if it.first else f's{lc.id} | {part}'};",
                          depth)
            elif isinstance(it, Value):
                lc = it.local
                if lc.checksum:
                    self.line(f"let p{lc.id} = r.position();", depth)
                expr = read_expr(lc.vtype, rstr(lc.name), self.read_ref,
                                 self.alloc)
                if it.assembled:
                    # Decode the value from its segments' bits.
                    self.line(f"let mut b{lc.id} = [0u8; 8];", depth)
                    self.line(f"let v{lc.id} = {{", depth)
                    self.line(f"let mut r = pk::segment_reader(&mut b{lc.id}, "
                              f"s{lc.id}, {it.assembled});", depth + 1)
                    self.line(expr, depth + 1)
                    self.line("};", depth)
                elif assign:
                    self.line(f"v{lc.id} = Some({expr});", depth)
                else:
                    self.line(f"let v{lc.id} = {expr};", depth)
                n = length_u64(lc, f"v{lc.id}")
                if lc.length is not None and lc.measures:
                    self.line(f"let e{lc.id} = r.span_end({n}, {lc.length}, "
                              f"{rstr(lc.name)})?;", depth)
                elif lc.length is not None:
                    self.line(f"r.limit({n}, {lc.length}, {rstr(lc.name)})?;",
                              depth)
            elif isinstance(it, Cond):
                if not assign:
                    for v in walk(it.items):
                        if isinstance(v, Value):
                            self.line(f"let mut v{v.local.id} = None;", depth)
                test = conjunction([term(c, lambda lc: f"v{lc.id}")
                                    for c in it.criteria])
                self.line(f"if {test} {{", depth)
                self.read_items(it.items, depth + 1, where, True)
                self.line("}", depth)
            else:
                self.line(f"r.uint({it.bits}, {rstr(where)})?;", depth)

    def write_items(self, n: Node, items, depth: int, where: str,
                    names: dict) -> None:
        """Writes the statements encoding a
        container's entries with BitWriter ``w``.

        Args:
            n:     The packet being encoded.
            items: The entries.
            depth: The indentation of the statements.
            where: The container's name, for errors.
            names: The field name of each of the packet's Locals.
        """
        for it in items:
            if isinstance(it, Seek):
                off = offset(it, self.write_ref(n, names), rstr(where))
                if it.from_start is None:
                    self.line(f"w.skip({off}, {rstr(where)})?;", depth)
                else:
                    seek = "seek_ahead" if it.ahead else "seek"
                    self.line(f"w.{seek}({self.mark(it.from_start)}, {off}, "
                              f"{rstr(where)})?;", depth)
            elif isinstance(it, Mark):
                self.line(f"let m{it.id} = w.position();", depth)
            elif isinstance(it, Fixed):
                self.line(f"w.uint({it.bits}, 0x{it.value:x});", depth)
            elif isinstance(it, (Pad, TailPad)):
                self.line(f"w.zeros({it.bits});", depth)
            elif isinstance(it, TailSeek):
                back = tail_back(it, self.write_ref(n, names), rstr(where))
                self.line(f"w.seek_tail({back});", depth)
            elif isinstance(it, LengthEnd):
                lc = it.local
                enc = lc.vtype.enc
                self.line(f"w.patch_span(l{lc.id}, {enc.bits}, "
                          f"{str(enc.little_endian).lower()}, {lc.length}, "
                          f"{rstr(lc.name)})?;", depth)
            elif isinstance(it, Transform):
                # Write its items, then what the transform makes of them.
                t = it.start
                self.line(f"let t{t} = w.position();", depth)
                self.write_items(n, it.items, depth, where, names)
                if it.length is not None:
                    self.patch_length(it.length, depth)
                for c in it.patches:
                    self.patch(c, depth, early=True)
                if it.keeps:
                    # For checks of what follows it (see late_patch).
                    self.line(f"let mut p{t} = [0u8; {it.cap}];", depth)
                    self.line(f"let n{t} = w.copy_out(t{t}, &mut p{t}, "
                              f"{rstr(it.name)})?;", depth)
                self.line(f"w.transform(t{t}, {transform_hook(it.encode)}, "
                          f"{opt_size(it.raw)}, {it.out_cap}, "
                          f"{rstr(it.name)})?;", depth)
            elif isinstance(it, Require):
                self.require(it, lambda lc: self.place(n, lc, names), depth)
            elif isinstance(it, Verify):
                self.patch(it.check, depth)
            elif isinstance(it, Fill):
                self.fill(it, "w", self.write_ref(n, names), depth)
            elif isinstance(it, Cond):
                conds = [term(c, lambda lc: self.place(n, lc, names))
                         for c in it.criteria]
                self.line(f"if {conjunction(conds)} {{", depth)
                self.write_items(n, it.items, depth + 1, where, names)
                self.line("}", depth)
            elif isinstance(it, Segment):
                lc = it.local
                if it.first:
                    # Encode the whole value, then write each segment's bits.
                    self.line(f"let t{lc.id} = w.stash({it.total});", depth)
                    self.line(write_stmt(lc.vtype, self.place(n, lc, names),
                                         rstr(lc.name),
                                         self.write_ref(n, names)), depth)
                    self.line(f"let s{lc.id} = w.unstash(t{lc.id});", depth)
                self.line(f"w.uint({it.bits}, (s{lc.id} >> {it.shift}) & "
                          f"0x{(1 << it.bits) - 1:x});", depth)
            elif it.assembled:
                pass
            elif it.local.computed:
                lc = it.local
                self.line(f"let l{lc.id} = w.position();", depth)
                self.line(f"w.zeros({lc.vtype.enc.bits});", depth)
            else:
                lc = it.local
                f = rstr(lc.name)
                if lc.optional:
                    self.line(f"let o{lc.id} = self.{names[lc]}"
                              ".as_ref().ok_or(PacketError::Missing { field: "
                              f"{f} }})?;", depth)
                    x = (f"o{lc.id}"
                         if lc.vtype.kind in ("string", "binary", "array",
                                              "aggregate") else f"(*o{lc.id})")
                else:
                    x = self.place(n, lc, names)
                self.line(write_stmt(written(n, lc), x, f,
                                     self.write_ref(n, names)), depth)

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
        """Writes the expression for a value when encoding.

        Args:
            n:     The packet being encoded.
            lc:    The value.
            names: The field name of each of the packet's Locals.

        Returns:
            The field, the local of a computed size or count, or the literal of
            a value its inheritance fixes.
        """
        if lc in names:
            return f"self.{names[lc]}"
        if lc in n.derived:
            return f"d{lc.id}"
        x = literal(lc.vtype, n.fixed[lc])
        return f"({x})" if lc.vtype.kind == "binary" else x

    def fill(self, it: Fill, rw: str, ref, depth: int) -> None:
        """Writes statements padding to the end of a
        container with a size; they fail with BadSize if its entries go past it.

        Args:
            it:    The Fill.
            rw:    ``r`` when decoding, ``w`` when encoding.
            ref:   Gives the value that a dynamic size uses.
            depth: The indentation of the statements.
        """
        f = rstr(it.container)
        n = (size(it.size, ref(it.size), f) if isinstance(it.size, DynSize)
             else str(it.size))
        self.line("{", depth)
        d = depth + 1
        self.line(f"let e = {self.mark(it.start)} + {n};", d)
        self.line(f"if {rw}.position() > e {{", d)
        self.line(f"return Err(PacketError::BadSize {{ field: {f} }});", d + 1)
        self.line("}", d)
        pad = "pad" if rw == "r" else "zeros"
        tail = f", {f})?;" if rw == "r" else ");"
        self.line(f"{rw}.{pad}(e - {rw}.position(){tail}", d)
        self.line("}", depth)

    def require(self, it: Require, place, depth: int) -> None:
        """Writes a statement failing with Mismatch unless
        an included container's criteria hold.

        Args:
            it:    The Require.
            place: Gives the expression for a Local.
            depth: The indentation of the statement.
        """
        self.line(f"if !({conjunction([term(t, place) for t in it.criteria])}) "
                  "{", depth)
        self.line("return Err(PacketError::Mismatch { container: "
                  f"{rstr(it.container)}, criterion: {rstr(it.text)} }});",
                  depth + 1)
        self.line("}", depth)

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
        self.line(f"w.patch_length(l{lc.id}, {enc.bits}, "
                  f"{str(enc.little_endian).lower()}, {lc.length}, "
                  f"{rstr(lc.name)})?;", depth)

    def finish(self, n: Node, depth: int) -> None:
        """Writes the checks of the packet length field and
        the checksums when decoding ends.

        Args:
            n:     The packet.
            depth: The indentation of the statements.
        """
        lc = n.length_field()
        if lc is not None:
            self.line(f"r.finish({rstr(lc.name)})?;", depth)
        for c in n.checks:
            self.verify(c, f"v{c.local.id}", depth)

    def verify(self, c, value: str, depth: int) -> None:
        """Writes a statement failing with BadChecksum
        unless a check holds.

        Args:
            c:     The check.
            value: The expression for its field's value.
            depth: The indentation of the statement.
        """
        f = rstr(c.local.name)
        start, end = self.region(c, "r")
        bits = c.local.vtype.enc.bits
        x = f"r.checksum(&{checksum_spec(c.spec)}, {start}, {end}, {f})?"
        if c.local.vtype.wide:
            # A wider field holds the value with zeros above it.
            test = f"Some({x}) != pk::fits_u64({value})"
        else:
            test = f"{x} & {hex((1 << bits) - 1)} != {value} as u64"
        self.line(f"if {test} {{", depth)
        self.line(f"return Err(PacketError::BadChecksum {{ field: {f} }});",
                  depth + 1)
        self.line("}", depth)

    def patch(self, c, depth: int, early: bool = False) -> None:
        """Writes the statement putting a check's value into
        its field, unless it is computed elsewhere: before a transform
        when it has a ``stop``, and otherwise once the packet or its
        container is encoded.

        Args:
            c:     The check.
            depth: The indentation of the statement.
            early: Whether this is before the transform of its field.
        """
        if c.late is not None or (c.stop is not None) != early:
            return
        enc = c.local.vtype.enc
        start, end = self.region(c, "w")
        self.line(f"w.patch_checksum(&{checksum_spec(c.spec)}, {start}, {end}, "
                  f"l{c.local.id}, {enc.bits}, "
                  f"{str(enc.little_endian).lower()}, {rstr(c.local.name)})?;",
                  depth)

    def late_patch(self, c, depth: int) -> None:
        """Writes the statement writing a late check's value (see
        model.Check) into its transformed data, which it transforms again.

        Args:
            c:     The check.
            depth: The indentation of the statement.
        """
        enc = c.local.vtype.enc
        start, end = self.region(c, "w")
        t = c.late.start
        self.line(f"w.patch_transformed_checksum(&{checksum_spec(c.spec)}, "
                  f"{start}, {end}, &p{t}[..n{t}], l{c.local.id} - t{t}, "
                  f"{enc.bits}, {str(enc.little_endian).lower()}, t{t}, "
                  f"{transform_hook(c.late.encode)}, {c.late.raw}, "
                  f"{c.late.out_cap}, {rstr(c.local.name)})?;", depth)

    def region(self, c, rw: str) -> tuple:
        """Writes the start and end of the bits a check
        covers.

        Args:
            c:  The check.
            rw: ``r`` when decoding, ``w`` when encoding: on decoding ``p<id>``
                and on encoding ``l<id>`` hold the position of its field.

        Returns:
            The start and end expressions, each in parentheses.
        """
        pos = f"{'p' if rw == 'r' else 'l'}{c.local.id}"
        base = self.mark(c.start)
        start = f"{base} + {c.skip}" if c.skip and not c.from_end else base
        end = f"m{c.end}" if c.end is not None else f"{rw}.packet_end()"
        if c.stop is not None:
            end = f"m{c.stop}.saturating_sub({c.stop_back})"
        elif c.late is not None:
            pass  # the field is before the bits, which run to the end
        elif c.from_end:
            end = f"{end}.saturating_sub({c.skip})"
        elif start == "0":
            end = pos  # the field always follows the packet's start
        else:
            end = f"if {pos} >= {start} {{ {pos} }} else {{ {end} }}"
        return f"({start})", f"({end})"

    def construct(self, n: Node) -> str:
        fields = ", ".join(f"{name}: v{lc.id}" for name, lc in n.fields)
        return f"{n.ident} {{ {fields} }}"

    def node(self, n: Node) -> None:
        """Writes a packet's struct, its alarms and
        algorithms, and its Packet and Display implementations.

        Args:
            n: The packet.
        """
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
            self.line(f"/// `{length.name}` is the packet length: computed "
                      "when encoding, checked when decoding.")
        for lc, (c, _) in n.derived.items():
            self.line("///")
            self.line(f"/// `{lc.name}` gives the size of `{c.name}`: computed "
                      "from its length when encoding.")
        for note in notes(n):
            self.line("///")
            for text in textwrap.wrap(note, 72):
                self.line(f"/// {text}")
        self.line("#[derive(Debug, Clone, PartialEq, Default)]")
        self.line(f"pub struct {n.ident} {{")
        for name, lc in n.fields:
            doc = f"; {lc.vtype.doc}" if lc.vtype.doc else ""
            cond = ("; present only when its include condition holds"
                    if lc.optional else "")
            self.line(f"/// `{lc.key}` ({lc.vtype.key}{doc}{cond})", 1)
            self.line(f"pub {name}: "
                      f"{field_type(lc.vtype, self.alloc, lc.optional)},", 1)
        self.line("}")
        self.line()
        self.alarms(n)
        self.line()
        self.size_check(n.key, n.max_bytes)
        self.line(f"impl Packet for {n.ident} {{")
        self.line(f"const NAME: &'static str = {rstr(n.key)};", 1)
        self.line(f"const MAX_BYTES: usize = {n.max_bytes};", 1)
        self.line()
        self.line("fn decode_prefix(data: &[u8]) -> Result<(Self, usize)> {", 1)
        self.need_hooks(n.hooks, 2)
        # A packet without entries, such as a meta-command without a command
        # container, reads and writes nothing.
        empty = not any(node.items for node in n.chain())
        if empty:
            self.line("let _ = data;", 2)
        else:
            self.transform_buffers(n.chain(), 2)
            self.line("let mut r = BitReader::new(data);", 2)
        for i, node in enumerate(n.chain()):
            for c in node.criteria:
                self.line(f"if !({term(c, lambda lc: f'v{lc.id}')}) {{", 2)
                self.line("return Err(PacketError::Mismatch { container: "
                          f"{rstr(node.key)}, criterion: {rstr(c.text)} }});",
                          3)
                self.line("}", 2)
            self.read_items(node.items, 2, node.key)
        self.finish(n, 2)
        used = "0" if empty else "r.used()"
        self.line(f"Ok(({self.construct(n)}, {used}))", 2)
        self.line("}", 1)
        self.line()
        self.line("fn encode_into(&self, w: &mut BitWriter<'_>) -> Result<()> "
                  "{", 1)
        self.need_hooks(n.hooks, 2)
        names = {lc: name for name, lc in n.fields}
        for lc, (c, d) in n.derived.items():
            # Sizes and counts come from the lengths of what they size.
            x = (f"pk::size_source(self.{names[c]}.len(), {size_args(d)}, "
                 f"{rstr(c.name)})?")
            self.line(f"let d{lc.id} = "
                      f"{field_type(lc.vtype, self.alloc)}::try_from({x})", 2)
            self.line(".map_err(|_| PacketError::OutOfRange { field: "
                      f"{rstr(lc.name)} }})?;", 3)
        guards = {}
        for node in n.chain():
            for c in node.criteria:
                for lc, prefix, count in term_indexes(c):
                    if lc in names:
                        guards[(reach(f"self.{names[lc]}", prefix,
                                      rstr(lc.name)), count)] = (lc.name)
        for (x, count), name in guards.items():
            # Criteria are checked before the arrays they index are written.
            self.line(f"if {x}.len() != {count} {{", 2)
            self.line(f"return Err(PacketError::BadSize {{ field: {rstr(name)} "
                      "});", 3)
            self.line("}", 2)
        for node in n.chain():
            for c in node.criteria:
                if any(lc in names for lc in term_locals(c)):
                    self.line("if !("
                              f"{term(c, lambda lc: self.place(n, lc, names))}"
                              ") {", 2)
                    self.line("return Err(PacketError::Mismatch { container: "
                              f"{rstr(node.key)}, criterion: {rstr(c.text)} "
                              "});", 3)
                    self.line("}", 2)
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
        if empty:
            self.line("let _ = w;", 2)
        self.line("Ok(())", 2)
        self.line("}", 1)
        self.line("}")
        self.line()
        self.line(f"impl fmt::Display for {n.ident} {{")
        self.line("fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {",
                  1)
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

    def alarms(self, n: Node) -> None:
        """Writes ``alarms()`` and ``monitor()``, which give
        each alarmed field's level, and a method computing each one, then
        ``algorithms()``.

        Args:
            n: The packet.
        """
        names = dict((lc, name) for name, lc in n.fields)
        place = lambda lc: self.place(n, lc, names)  # noqa: E731
        monitored = {k: ident for k, ident, _ in self.m.monitored}
        count = len(n.alarms)
        self.line(f"impl {n.ident} {{")
        self.line("/// The number of fields with an alarm.", 1)
        self.line(f"pub const ALARMS: usize = {count};", 1)
        self.line()
        self.doc(1,
                 f"Evaluates the alarms of this `{n.key}` packet's fields.  "
                 "Alarms that depend on earlier samples count only this one "
                 "(see `monitor`).",
                 returns="The alarm level of each field with an alarm, `None` "
                         "when the field is absent.")
        self.line(f"pub fn alarms(&self) -> pk::Alarms<{count}> {{", 1)
        self.line("let mut a = pk::Alarms::new();", 2)
        for name, _, _ in n.alarms:
            self.line(f"a.push({rstr(name)}, self.alarm_{name}());", 2)
        self.line("a", 2)
        self.line("}", 1)
        self.line()
        self.doc(1,
                 f"Evaluates the alarms of this `{n.key}` packet's fields like "
                 "`alarms()`, counting the earlier samples for change ranges, "
                 "minViolations and minConformance.", MONITOR_PARAMS,
                 returns="The alarm level of each field with an alarm, `None` "
                         "when the field is absent.")
        self.line("pub fn monitor(&self, m: &mut AlarmMonitor, t: f64) -> "
                  f"pk::Alarms<{count}> {{", 1)
        self.line("let mut a = pk::Alarms::new();", 2)
        for name, _, a in n.alarms:
            call = (f"self.monitor_{name}(m, t)" if a.stateful
                    else f"self.alarm_{name}()")
            self.line(f"a.push({rstr(name)}, {call});", 2)
        self.line("a", 2)
        self.line("}", 1)
        self.algorithms(n, place)
        for name, lc, a in n.alarms:
            level = (f"The level of `{name}`'s alarm, the worst of its "
                     "elements'" if lc.vtype.kind == "array"
                     else f"The level of `{name}`'s alarm")
            level += (", or `None` when the field is absent." if lc.optional
                      else ".")
            self.line()
            self.doc(1,
                     f"Evaluates the alarm of field `{name}` (`{lc.key}`) on "
                     "this sample alone.", returns=level)
            self.line(f"fn alarm_{name}(&self) -> Option<pk::AlarmLevel> {{", 1)
            self.alarm_body(name, lc, a, place, None)
            if a.stateful:
                self.line()
                self.doc(1,
                         f"Evaluates the alarm of field `{name}` (`{lc.key}`), "
                         "counting the earlier samples whose state `m` keeps "
                         "and recording this one there.", MONITOR_PARAMS,
                         returns=level)
                self.line(f"fn monitor_{name}(&self, m: &mut AlarmMonitor, t: "
                          "f64) -> Option<pk::AlarmLevel> {", 1)
                self.alarm_body(name, lc, a, place, monitored[a.key])
        self.line("}")

    def algorithms(self, n: Node, place) -> None:
        """Writes ``algorithms()``: the output of each
        MathAlgorithm the packet triggers.

        Args:
            n:     The packet.
            place: Gives the expression for a Local.
        """
        count = len(n.algorithms)
        self.line()
        self.line("/// The number of MathAlgorithms this packet triggers.", 1)
        self.line(f"pub const ALGORITHMS: usize = {count};", 1)
        self.line()
        self.doc(1,
                 f"Evaluates the MathAlgorithms that a `{n.key}` packet "
                 "triggers, from this one's values.",
                 returns="The output of each, `None` when an input is absent "
                         "or a custom algorithm's function isn't set or fails.")
        self.line(f"pub fn algorithms(&self) -> pk::Outputs<{count}> {{", 1)
        self.line("let mut o = pk::Outputs::new();", 2)
        for alg in n.algorithms:
            if alg.hook is not None:
                h = alg.hook
                self.line(f"o.push({rstr(alg.name)}, (|| -> Option<f64> {{", 2)
                self.line(f"let f = hooks::{h.ident.upper()}.get()?;", 3)
                self.line(f"let mut out = [0.0f64; {h.outputs}];", 3)
                self.line(f"f(&[{hook_args(alg.inputs, place)}], &mut "
                          f"out).then_some(out[{alg.index}])", 3)
                self.line("})());", 2)
                continue
            opt = [lc for lc in inputs(alg.expr) if lc.optional]

            def at(lc):
                return f"i{lc.id}" if lc.optional else place(lc)
            value = math_expr(alg.expr, "x", at, rstr(alg.name))
            if not opt:
                self.line(f"o.push({rstr(alg.name)}, Some({value}));", 2)
                continue
            self.line(f"o.push({rstr(alg.name)}, (|| -> Option<f64> {{", 2)
            for lc in opt:
                self.line(f"let i{lc.id} = {place(lc)}?;", 3)
            self.line(f"Some({value})", 3)
            self.line("})());", 2)
        self.line("o", 2)
        self.line("}", 1)

    def alarm_body(self, name: str, lc, a, place, state) -> None:
        """Writes the body of a method giving a field's
        alarm level.

        Args:
            name:  The field's name.
            lc:    The field's Local.
            a:     Its alarm, bound to the packet's values.
            place: Gives the expression for a Local.
            state: The AlarmMonitor field that keeps its parameter's earlier
                   samples, or None to count only this one.
        """
        vt = lc.vtype
        kind = vt.elem.kind if vt.kind == "array" else vt.kind
        x = place(lc)
        if lc.optional:
            x = (f"{x}.as_ref()?" if kind == "string" or vt.kind == "array"
                 else f"{x}?")
        if state is not None:
            self.line(f"let s = &mut m.{state};", 2)
            self.line("let mut mv = 1u32;", 2)
            self.line("let mut mc = 1u32;", 2)
        d = 2
        if vt.kind == "array":
            self.line("let mut worst = 0u8;", 2)
            self.line(f"for x in {x}.iter() {{", 2)
            self.line("let x = x.as_str();" if kind == "string"
                      else "let x = *x;", 3)
            d = 3
        else:
            self.line(f"let x = {x}.as_str();" if kind == "string"
                      else f"let x = {x};", 2)
        self.line("let mut lv = 0u8;", d)
        live = state is not None
        et = vt.elem if vt.kind == "array" else vt
        for k, (terms, ad) in enumerate(a.contexts):
            cond = conjunction([term(c, place) for c in terms])
            self.line(f"{'if' if k == 0 else '} else if'} {cond} {{", d)
            self.alarm_def(ad, et, place, d + 1, live)
        if a.contexts:
            if a.default is not None:
                self.line("} else {", d)
                self.alarm_def(a.default, et, place, d + 1, live)
            self.line("}", d)
        elif a.default is not None:
            self.alarm_def(a.default, et, place, d, live)
        if vt.kind == "array":
            self.line("worst = worst.max(lv);", d)
            self.line("}", 2)
            self.line("let lv = worst;", 2)
        if state is None:
            self.line("Some(pk::AlarmLevel::from_index(lv))", 2)
        else:
            if a.history:
                self.line(f"s.push({math_value(et, 'x', 'rust')}, t);", 2)
            self.line("Some(pk::AlarmLevel::from_index(s.update(lv, mv, mc)))",
                      2)
        self.line("}", 1)

    def alarm_def(self, ad, vt: VType, place, depth: int,
                  live: bool = False) -> None:
        """Writes statements raising ``lv`` to the level
        that an alarm definition gives the value ``x``.

        Args:
            ad:    The alarm definition.
            vt:    The value's type (an array's element's).
            place: Gives the expression for a Local, for context criteria.
            depth: The indentation of the statements.
            live:  Whether to check its change ranges, on AlarmState ``s`` at
                   time ``t``, and set ``mv`` and ``mc`` to its minViolations
                   and minConformance.
        """
        kind = vt.kind
        if live and ad.change is not None:
            c = ad.change
            self.line("if let Some(d) = "
                      f"s.change({math_value(vt, 'x', 'rust')}, t, "
                      f"{c.samples}, {str(c.per_second).lower()}, "
                      f"{str(c.percent).lower()}) {{", depth)
            for b in c.bands:
                self.band(b, "d", None, depth + 1)
            self.line("}", depth)
        if live and (ad.min_violations, ad.min_conformance) != (1, 1):
            self.line(f"mv = {ad.min_violations};", depth)
            self.line(f"mc = {ad.min_conformance};", depth)
        for b in ad.bands:
            self.band(b, "x", vt, depth)
        if kind == "enum" and (ad.labels or ad.default):
            labels = ", ".join(f"({lo}, {hi}, {lv})"
                               for lo, hi, lv in ad.labels)
            self.line(f"lv = lv.max(pk::enum_alarm(x.0, &[{labels}], "
                      f"{ad.default}));", depth)
        if kind == "string" and (ad.patterns or ad.default):
            pats = ", ".join(f"(&{self.dfa(dfa)}, {lv})"
                             for dfa, lv in ad.patterns)
            self.line(f"lv = lv.max(pk::pattern_alarm(x, &[{pats}], "
                      f"{ad.default}));", depth)
        if ad.custom is not None:
            h, ins = ad.custom
            self.line(f"lv = lv.max(hooks::{h.ident.upper()}.get().map_or(0, |"
                      f"f| f(&[{hook_args(ins, place)}]).min(5)));", depth)
        for level, terms in ad.conditions:
            self.line(f"if {conjunction([term(c, place) for c in terms])} {{",
                      depth)
            self.line(f"lv = lv.max({level});", depth + 1)
            self.line("}", depth)

    def band(self, b, x: str, vt: Optional[VType], depth: int) -> None:
        """Writes statements raising ``lv`` to a band's level
        if a value violates it.

        Args:
            b:     The band.
            x:     The expression for the value.
            vt:    Its type, or None for a double.
            depth: The indentation of the statements.
        """
        kind = vt.kind if vt is not None else "float"
        if kind == "int" and vt.wide:
            # Exactly, with literals of the field's own type.
            parts = (
                ([f"{x} >= {literal(vt, b.lo)}"] if b.lo is not None else [])
                + ([f"{x} <= {literal(vt, b.hi)}"] if b.hi is not None else []))
            test = " && ".join(parts) or "true"
        elif kind == "int":
            parts = ([f"{b.lo}i128 <= {x} as i128"] if b.lo is not None
                     else []) + ([f"{x} as i128 <= {b.hi}i128"]
                                 if b.hi is not None else [])
            test = " && ".join(parts) or "true"
        else:
            def opt(v):
                return "None" if v is None else f"Some({rflt(v)})"
            test = (f"pk::in_band({x}, {opt(b.lo)}, {str(b.lo_incl).lower()}, "
                    f"{opt(b.hi)}, {str(b.hi_incl).lower()})")
        self.line(f"if {test if b.inside else '!(' + test + ')'} {{", depth)
        self.line(f"lv = lv.max({b.level});", depth + 1)
        self.line("}", depth)

    def dfa(self, dfa) -> str:
        """Names the static holding a pattern's automaton,
        adding it to those the module ends with.

        Args:
            dfa: The automaton.

        Returns:
            The static's name.
        """
        for name, d in self.dfas:
            if d is dfa:
                return name
        name = f"DFA{len(self.dfas)}"
        self.dfas.append((name, dfa))
        return name

    def size_check(self, key: str, n: int) -> None:
        """Writes an assertion that refuses to build
        where a packet's size in bits doesn't fit in a usize, if it may not.

        Args:
            key: The packet's qualified name.
            n:   Its most bytes.
        """
        if n > NARROW_MAX_BYTES:
            self.line(f"const _: () = assert!({n}u64 <= usize::MAX as u64 / 8, "
                      f"{rstr(key + ' is too large for this usize')});")
            self.line()

    def kind(self, k: Kind) -> None:
        """Writes a kind's enum of the concrete packets
        derived from its root, and its Packet, Display, alarms, algorithms and
        From implementations.

        Args:
            k: The kind.
        """
        self.line()
        self.line(f"/// Every concrete packet derived from `{k.root.key}`.")
        self.line("#[derive(Debug, Clone, PartialEq)]")
        self.line(f"pub enum {k.ident} {{")
        for m in k.members:
            self.line(f"{m.ident}({m.ident}),", 1)
        self.line("}")
        self.line()
        self.size_check(k.root.key, k.max_bytes)
        self.line(f"impl Packet for {k.ident} {{")
        self.line(f"const NAME: &'static str = {rstr(k.root.key)};", 1)
        self.line(f"const MAX_BYTES: usize = {k.max_bytes};", 1)
        self.line()
        self.line("/// Decodes the root's entries, then picks the first child "
                  "(in", 1)
        self.line("/// document order) whose criteria match, recursively.", 1)
        self.line("fn decode_prefix(data: &[u8]) -> Result<(Self, usize)> {", 1)
        self.need_hooks(items_hooks(k.root.items), 2)
        self.transform_buffers([k.root] + k.root.descendants(), 2)
        self.line("let mut r = BitReader::new(data);", 2)
        self.read_items(k.root.items, 2, k.root.key)
        self.dispatch(k, k.root, 2)
        self.line("}", 1)
        self.line()
        self.line("fn encode_into(&self, w: &mut BitWriter<'_>) -> Result<()> "
                  "{", 1)
        self.line("match self {", 2)
        for m in k.members:
            self.line(f"Self::{m.ident}(p) => p.encode_into(w),", 3)
        self.line("}", 2)
        self.line("}", 1)
        self.line("}")
        self.line()
        self.line(f"impl fmt::Display for {k.ident} {{")
        self.line("fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {",
                  1)
        self.line("match self {", 2)
        for m in k.members:
            self.line(f"Self::{m.ident}(p) => p.fmt(f),", 3)
        self.line("}", 2)
        self.line("}", 1)
        self.line("}")
        most = max(len(m.alarms) for m in k.members)
        self.line()
        self.line(f"impl {k.ident} {{")
        self.line("/// The most fields with an alarm that a member has.", 1)
        self.line(f"pub const ALARMS: usize = {most};", 1)
        self.line()
        self.doc(1,
                 "Evaluates the alarms of the fields of this packet derived "
                 f"from `{k.root.key}`, as its own `alarms()` does.",
                 returns="The alarm level of each field with an alarm, `None` "
                         "when the field is absent.")
        self.line(f"pub fn alarms(&self) -> pk::Alarms<{most}> {{", 1)
        self.line("let mut out = pk::Alarms::new();", 2)
        self.line("match self {", 2)
        for m in k.members:
            self.line(f"Self::{m.ident}(p) => {{", 3)
            self.line("for a in p.alarms().as_slice() {", 4)
            self.line("out.push(a.field, a.level);", 5)
            self.line("}", 4)
            self.line("}", 3)
        self.line("}", 2)
        self.line("out", 2)
        self.line("}", 1)
        algs = max(len(m.algorithms) for m in k.members)
        self.line()
        self.line("/// The most MathAlgorithms that a member triggers.", 1)
        self.line(f"pub const ALGORITHMS: usize = {algs};", 1)
        self.line()
        self.doc(1,
                 "Evaluates the MathAlgorithms that this packet derived from "
                 f"`{k.root.key}` triggers, as its own `algorithms()` does.",
                 returns="The output of each, `None` when it can't be "
                         "computed.")
        self.line(f"pub fn algorithms(&self) -> pk::Outputs<{algs}> {{", 1)
        self.line("let mut out = pk::Outputs::new();", 2)
        self.line("match self {", 2)
        for m in k.members:
            self.line(f"Self::{m.ident}(p) => {{", 3)
            self.line("for o in p.algorithms().as_slice() {", 4)
            self.line("out.push(o.name, o.value);", 5)
            self.line("}", 4)
            self.line("}", 3)
        self.line("}", 2)
        self.line("out", 2)
        self.line("}", 1)
        self.line()
        self.doc(1,
                 "Evaluates the alarms of the fields of this packet derived "
                 f"from `{k.root.key}`, as its own `monitor()` does.",
                 MONITOR_PARAMS,
                 returns="The alarm level of each field with an alarm, `None` "
                         "when the field is absent.")
        self.line("pub fn monitor(&self, m: &mut AlarmMonitor, t: f64) -> "
                  f"pk::Alarms<{most}> {{", 1)
        self.line("let mut out = pk::Alarms::new();", 2)
        self.line("match self {", 2)
        for m in k.members:
            self.line(f"Self::{m.ident}(p) => {{", 3)
            self.line("for a in p.monitor(m, t).as_slice() {", 4)
            self.line("out.push(a.field, a.level);", 5)
            self.line("}", 4)
            self.line("}", 3)
        self.line("}", 2)
        self.line("out", 2)
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
        """Writes the statements that decode a container's
        children, picking the first whose criteria match, recursively.

        Args:
            k:     The kind being decoded.
            n:     The container whose children to try.
            depth: The indentation of the statements.
        """
        for c in n.children:
            if c.error:
                continue
            test = conjunction([term(x, lambda lc: f"v{lc.id}")
                                for x in c.criteria])
            self.need_hooks(terms_hooks(c.criteria), depth)
            self.line(f"if {test} {{", depth)
            self.need_hooks(items_hooks(c.items), depth + 1)
            self.read_items(c.items, depth + 1, c.key)
            self.dispatch(k, c, depth + 1)
            self.line("}", depth)
        if n.abstract:
            self.line("return Err(PacketError::NoMatch { container: "
                      f"{rstr(n.key)} }});", depth)
        else:
            self.need_hooks(checks_hooks(n.checks), depth)
            self.finish(n, depth)
            self.line(f"return Ok((Self::{n.ident}({self.construct(n)}), "
                      "r.used()));", depth)


def emit(mission: Mission, source: str, alloc: bool = True) -> str:
    """Writes the Rust module of a mission's packet codecs.

    Args:
        mission: The compiled mission.
        source:  The name of the XTCE documents, for the notice at the top.
        alloc:   Whether the code may allocate; without it, it never does.

    Returns:
        The module's source, before rustfmt.
    """
    return RustEmitter(mission, source, alloc).emit()
