"""Emit a Rust module of packet codecs (uses ``tcs_xtce::packet``)."""

from __future__ import annotations

import json
import math
import textwrap

from .common import (HOOK_DOCS, written, checks_hooks, items_hooks, terms_hooks,
                     notes, stream_capacity, stream_doc, MATH_OPS, Ref,
                     fast_order, field_range, flt, header_lines, inputs,
                     int_enc, invertible, math_value, size_args, str_cap,
                     string_spec, wire_region)
from .model import (term_indexes, term_locals, walk, Index, AggregateEnc, AllOf,
                    AnyOf, ArrayEnc, BinaryEnc, Cond, ContextCal, Criterion,
                    DynSize, Fixed, FloatEnc, IntEnc, Kind, Local, Mark, MathOp,
                    Mission, Node, Pad, Polynomial, Seek, Spline, StringEnc,
                    Value, VType, Require, Fill, Segment, Verify, Custom,
                    NARROW_MAX_BYTES)


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
        return "Vec<u8>" if alloc else f"pk::FixedBytes<{vt.enc.bits // 8}>"
    return {"float": "f64", "bool": "bool"}.get(k) or vt.ident


def rflt(x: float) -> str:
    """An f64 literal, infinities included."""
    if math.isinf(x):
        return "f64::INFINITY" if x > 0 else "f64::NEG_INFINITY"
    return flt(x) + "_f64"


# The Rust type of each kind of hook.
HOOK_TYPES = {"criterion": "fn(&[f64]) -> bool",
              "algorithm": "fn(&[f64], &mut [f64]) -> bool",
              "alarm": "fn(&[f64]) -> u8",
              "checksum": "fn(&[u8], usize, usize) -> u64"}


def wide_type(vt: VType) -> str:
    """The bnum type an integer of more than 64 bits is computed in: its
    own type, for more than 128 bits."""
    return f"pk::{'Int' if vt.signed else 'Uint'}<{vt.width // 8}>"


def wide_literal(vt: VType, v: int) -> str:
    if vt.width == 128:
        return f"{v}{'i' if vt.signed else 'u'}128"
    b = v.to_bytes(vt.width // 8, "little", signed=vt.signed)
    return (f"{wide_type(vt).replace('<', '::<')}::from_le_bytes(["
            + ", ".join(f"0x{x:02x}" for x in b) + "])")


def literal(vt: VType, v) -> str:
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
    """Rust expression for a MathOperationCalibrator tree; ``x`` is the
    value being calibrated."""
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
    """Expression calibrating ``x`` with ``cal``; ``place`` gives the
    expression for a Local that the calibrator uses."""
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
    """Expression inverting ``cal`` for the engineering value ``y``; one
    that can't be inverted returns NotInvertible."""
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
    """An if-else chain over a context calibrator's cases: ``body(c)``
    for the calibrator of the first case that holds, or the default."""
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
    """Expression for the StringSpec of a string type; ``ref`` gives the
    expression for the value that gives a dynamic size."""
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
    """Expression for the value a bound DynSize adjusts: a field, or the
    first matching value of a DiscreteLookupList.  ``place`` gives the
    expression for a Local."""
    if d.lookup is None:
        return reach(place(d.local), d.path, rstr(d.local.name))
    x = f"{d.default}i64"
    for criteria, v in reversed(d.lookup):
        x = (f"if {conjunction([term(c, place) for c in criteria])} {{ {v}i64 "
             f"}} else {{ {x} }}")
    return f"({x})"


def offset(s: Seek, ref, f: str) -> str:
    """Expression for the offset of a Seek."""
    if isinstance(s.offset, int):
        return str(s.offset)
    n = size(s.offset, ref(s.offset), f)
    return f"({n}) as i64" if s.offset.factor != 1 else f"{n} as i64"


def size(d: DynSize, x: str, f: str) -> str:
    """Expression for a size or count that the value ``x`` gives."""
    n = (f"pk::dynamic_size({x} as f64, {flt(d.slope)}, {flt(d.intercept)}, "
         f"{d.unit}, {d.max}, {f})?")
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
    """The runtime's Checksum for an ErrorDetectCorrect algorithm."""
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
    if e.order == "be":
        return "pk::ByteOrder::Big"
    if e.order == "le":
        return "pk::ByteOrder::Little"
    return f"pk::ByteOrder::List(&[{', '.join(map(str, e.order))}])"


def raw_read(e, f: str) -> str:
    """Expression reading the raw value of an integer or float encoding: a
    u64, or a u128 for more than 64 bits."""
    if fast_order(e):
        return f"r.uint_order({e.bits}, {str(e.little_endian).lower()}, {f})?"
    x = (f"r.uint_bits({e.bits}, {str(e.lsb).lower()}, {byte_order(e)}, {f})?")
    return x if e.bits > 64 else f"({x} as u64)"


def raw_write(e, raw: str) -> str:
    """Statement writing the raw value ``raw`` (u64, or u128 for more than
    64 bits) of an integer or float encoding."""
    if fast_order(e):
        return f"w.uint_order({e.bits}, {str(e.little_endian).lower()}, {raw});"
    wide = raw if e.bits > 64 else f"{raw} as u128"
    return (f"w.uint_bits({e.bits}, {str(e.lsb).lower()}, {byte_order(e)}, "
            f"{wide});")


def read_expr(vt: VType, f: str, ref, alloc: bool, depth: int = 0) -> str:
    """Expression reading one value with BitReader ``r``; ``ref`` gives the
    expression for the value that gives a size or count (a DynSize).
    ``alloc`` is whether it may allocate.  ``depth`` numbers the locals of
    nested aggregates."""
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
            return f"{{ {lets}{pad} {vt.ident} {{ {fields} }} }}"
        agg = f"{vt.ident} {{ {', '.join(f'{i}: {x}' for i, x in reads)} }}"
        return f"{{ let a = {agg}; r.pad({e.pad}, {f})?; a }}" if e.pad else agg
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
    if isinstance(e, IntEnc) and vt.wide:
        return read_wide(vt, f)
    if isinstance(e, IntEnc):
        raw = raw_read(e, f)
        if e.bcd:
            raw = (f"pk::bcd_decode({raw}, {e.bits}, "
                   f"{str(e.encoding == 'packedBCD').lower()}, {f})?")
        enc = f"IntEncoding::{int_enc(vt)}"
        if vt.kind == "int" and vt.calibrator is not None:
            y = calibrate(vt, f"pk::int_to_f64({raw}, {e.bits}, {enc})", f,
                          ref.place)
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
        return calibrate(vt, f"pk::int_to_f64({raw}, {e.bits}, {enc})", f,
                         ref.place)
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
            return (f"{{ let n = r.rest_bytes({e.bits // 8}, {f})?; "
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


def read_wide(vt: VType, f: str) -> str:
    """Expression reading an integer of more than 64 bits."""
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
    """Statement writing ``x``, an integer of more than 64 bits."""
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


def write_stmt(vt: VType, x: str, f: str, ref) -> str:
    """Statement writing value ``x`` (a place or literal) with BitWriter ``w``;
    ``ref`` gives the expression for a value that gives a size or count.  A
    value outside its type's valid ranges fails with OutOfRange."""
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
    """write_stmt's statement, without the check of the valid ranges."""
    e = vt.enc
    bad = f"return Err(PacketError::BadSize {{ field: {f} }});"
    if isinstance(e, AggregateEnc):
        inner = Ref(lambda d: f"{x}.{d.member}" if d.member else ref(d),
                    ref.place)
        parts = []
        for m in e.members:
            if m.skip:
                parts.append(f"w.zeros({m.skip});")
            parts.append(write_stmt(m.vtype, f"{x}.{m.ident}", f, inner))
        if e.pad:
            parts.append(f"w.zeros({e.pad});")
        return "{ " + " ".join(parts) + " }"
    if isinstance(e, ArrayEnc):
        gap = (f"if i > 0 {{ w.skip({gap_expr(e, ref, f)}, {f})?; }}" if e.gap
               else "")
        return (f"{{ let n = {count(vt, ref, f)}; if {x}.len() != n {{ {bad} "
                f"}} for (i, {elem_pattern(e.elem)}) in {x}.iter().enumerate() "
                f"{{ {gap} {write_stmt(e.elem, 'e', f, ref)} }} }}")
    data = f"{x}.as_bytes()" if vt.kind == "string" else f"&{x}"
    if isinstance(e, BinaryEnc) and e.dyn is not None:
        n = size(e.dyn, ref(e.dyn), f)
        return (f"{{ let n = {n}; if {x}.len() != n {{ {bad} }} "
                f"w.bytes({data}); }}")
    if isinstance(e, IntEnc) and vt.wide:
        return write_wide(vt, x, f)
    if isinstance(e, IntEnc):
        bits = e.bits
        enc = f"IntEncoding::{int_enc(vt)}"
        if vt.kind == "int" and vt.calibrator is not None:
            if not invertible(vt):
                return (f"return Err(PacketError::NotInvertible {{ field: {f} "
                        "});")
            y = uncalibrate(vt, f"{x} as f64", f, ref.place)
            raw = (f"pk::f64_to_int({y}, {bits}, {enc}, {f})?")
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
            raw = (f"pk::f64_to_int({uncalibrate(vt, x, f, ref.place)}, "
                   f"{bits}, {enc}, {f})?")
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
    """Expression writing the canonical text of ``x`` to the formatter
    ``f``, without allocating."""
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
    if not conds:
        return "true"
    return conds[0] if len(conds) == 1 else " && ".join(f"({c})" for c in conds)


def reach(x: str, path, f: str) -> str:
    """The expression for the member or element ``path`` of ``x``.  An
    element of an array whose count varies may be missing, which fails with
    ``BadSize`` naming ``f``."""
    for p in path:
        if not isinstance(p, Index):
            x += "." + p
        elif p.count is None:
            x = f"(*pk::element(&{x}, {p.i}, {f})?)"
        else:
            x += f"[{p.i}]"
    return x


def term(t, place) -> str:
    """Expression for a criterion or a tree of them; ``place`` gives the
    expression for a Local."""
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


def hook_args(inputs: list, place) -> str:
    """A slice's items: a custom algorithm's inputs, as f64s."""
    return ", ".join(rflt(x[1]) if x[0] == "const"
                     else math_value(x[3],
                                     reach(place(x[1]), x[2], rstr(x[1].name)),
                                     "rust") for x in inputs)


# What ``<absent value> op <value>`` gives, as with std::optional in C++.
EMPTY = {"==": "false", "!=": "true", "<": "true", "<=": "true", ">": "false",
         ">=": "false"}


def condition(c: Criterion, x: str, y=None) -> str:
    """``x`` is the expression for the criterion's local, and ``y`` for
    the value it compares with, if any."""
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
        self.m = mission
        self.source = source
        self.alloc = alloc
        self.dfas: list = []
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
        bits, size = stream_capacity(st)
        sync = "FrameSync" if st.fixed else "FlagSync"
        pcm = {"NRZL": "Nrzl", "NRZM": "Nrzm", "NRZS": "Nrzs"}[st.pcm]
        self.line()
        doc = stream_doc(st).replace(st.key,
                                     f"`{st.key}`").replace(st.container,
                                                            f"`{st.container}`")
        for text in textwrap.wrap(doc, 72):
            self.line(f"/// {text}")
        self.line("#[derive(Debug, Clone)]")
        self.line(f"pub struct {st.ident}(pk::{sync}<{bits}, {size}>);")
        self.line()
        self.line(f"impl Default for {st.ident} {{")
        self.line("fn default() -> Self {", 1)
        self.line("Self::new()", 2)
        self.line("}", 1)
        self.line("}")
        self.line()
        self.line(f"impl {st.ident} {{")
        self.line("/// The container that each frame is.", 1)
        self.line(f"pub const CONTAINER: &'static str = {rstr(st.container)};",
                  1)
        if st.fixed:
            self.line("pub const SPEC: pk::FrameSpec = pk::FrameSpec {", 1)
            fields = (
                ("frame_bits", st.frame_bits), ("pattern", f"0x{st.pattern:x}"),
                ("mask", f"0x{st.mask:x}"), ("pattern_bits", st.pattern_bits),
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
        self.line("pub fn new() -> Self {", 1)
        self.line(f"Self(pk::{sync}::new(Self::SPEC))", 2)
        self.line("}", 1)
        self.line()
        self.line("/// Takes the next bytes of the stream, and gives `f` each "
                  "frame they", 1)
        self.line("/// complete (its bits padded with zeros).", 1)
        self.line("pub fn push(&mut self, data: &[u8], f: impl FnMut(&[u8])) {",
                  1)
        self.line("self.0.push(data, f);", 2)
        self.line("}", 1)
        self.line("}")

    def need_hooks(self, hooks: list, depth: int) -> None:
        for h in hooks:
            self.line(f"if hooks::{h.ident.upper()}.get().is_none() {{", depth)
            self.line("return Err(PacketError::NoAlgorithm { name: "
                      f"{rstr(h.ident)} }});", depth + 1)
            self.line("}", depth)

    def hooks(self) -> None:
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
        self.line("/// The label of this value, if the enumeration lists it.",
                  1)
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
        for it in items:
            if isinstance(it, Seek):
                off = offset(it, self.read_ref, rstr(where))
                if it.from_start is None:
                    self.line(f"r.skip({off}, {rstr(where)})?;", depth)
                else:
                    self.line(f"r.seek({self.mark(it.from_start)}, {off}, "
                              f"{rstr(where)})?;", depth)
            elif isinstance(it, Mark):
                self.line(f"let m{it.id} = r.position();", depth)
            elif isinstance(it, Pad):
                self.line(f"r.pad({it.bits}, {rstr(where)})?;", depth)
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
                if lc.length is not None:
                    self.line(f"r.limit(v{lc.id} as u64, {lc.length}, "
                              f"{rstr(lc.name)})?;", depth)
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
        for it in items:
            if isinstance(it, Seek):
                off = offset(it, self.write_ref(n, names), rstr(where))
                if it.from_start is None:
                    self.line(f"w.skip({off}, {rstr(where)})?;", depth)
                else:
                    self.line(f"w.seek({self.mark(it.from_start)}, {off}, "
                              f"{rstr(where)})?;", depth)
            elif isinstance(it, Mark):
                self.line(f"let m{it.id} = w.position();", depth)
            elif isinstance(it, Fixed):
                self.line(f"w.uint({it.bits}, 0x{it.value:x});", depth)
            elif isinstance(it, Pad):
                self.line(f"w.zeros({it.bits});", depth)
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
                self.line(f"w.uint({lc.vtype.enc.bits}, 0);", depth)
            else:
                lc = it.local
                f = rstr(lc.name)
                if lc.optional:
                    self.line(f"let o{lc.id} = self.{names[lc]}"
                              ".as_ref().ok_or(PacketError::Missing { field: "
                              f"{f} }})?;", depth)
                    x = (f"o{lc.id}"
                         if lc.vtype.kind in ("string", "binary", "array",
                                              "aggregate") else f"*o{lc.id}")
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
        """Expression for a value when encoding: a field, a computed size or
        count, or a fixed constant."""
        if lc in names:
            return f"self.{names[lc]}"
        if lc in n.derived:
            return f"d{lc.id}"
        x = literal(lc.vtype, n.fixed[lc])
        return f"({x})" if lc.vtype.kind == "binary" else x

    def fill(self, it: Fill, rw: str, ref, depth: int) -> None:
        """Pads to the end of a container with a size; fails with BadSize if
        its entries go past it."""
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
        """Fails with Mismatch unless an included container's criteria
        hold."""
        self.line(f"if !({conjunction([term(t, place) for t in it.criteria])}) "
                  "{", depth)
        self.line("return Err(PacketError::Mismatch { container: "
                  f"{rstr(it.container)}, criterion: {rstr(it.text)} }});",
                  depth + 1)
        self.line("}", depth)

    @staticmethod
    def mark(i: int) -> str:
        return "0" if i == 0 else f"m{i}"

    def finish(self, n: Node, depth: int) -> None:
        """Checks the packet length field and the checksums when decoding
        ends."""
        lc = n.length_field()
        if lc is not None:
            self.line(f"r.finish({rstr(lc.name)})?;", depth)
        for c in n.checks:
            self.verify(c, f"v{c.local.id}", depth)

    def verify(self, c, value: str, depth: int) -> None:
        """Fails with BadChecksum unless check ``c`` holds; ``value`` is the
        expression for its field's value."""
        f = rstr(c.local.name)
        start, end = self.region(c, "r")
        self.line(f"if r.checksum(&{checksum_spec(c.spec)}, {start}, {end}, "
                  f"{f})? & {hex((1 << c.local.vtype.enc.bits) - 1)} != "
                  f"{value} as u64 {{", depth)
        self.line(f"return Err(PacketError::BadChecksum {{ field: {f} }});",
                  depth + 1)
        self.line("}", depth)

    def patch(self, c, depth: int) -> None:
        """Writes the value of check ``c`` into its field."""
        enc = c.local.vtype.enc
        start, end = self.region(c, "w")
        self.line(f"w.patch_checksum(&{checksum_spec(c.spec)}, {start}, {end}, "
                  f"l{c.local.id}, {enc.bits}, "
                  f"{str(enc.little_endian).lower()}, {rstr(c.local.name)})?;",
                  depth)

    def region(self, c, rw: str) -> tuple:
        """The start and end of the bits a check covers, with reader or
        writer ``rw``: on decoding ``p`` and on encoding ``l`` hold the
        position of its field."""
        pos = f"{'p' if rw == 'r' else 'l'}{c.local.id}"
        base = self.mark(c.start)
        start = f"{base} + {c.skip}" if c.skip and not c.from_end else base
        end = f"m{c.end}" if c.end is not None else f"{rw}.packet_end()"
        if c.from_end:
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
            enc = length.vtype.enc
            self.line(f"w.patch_length(l{length.id}, {enc.bits}, "
                      f"{str(enc.little_endian).lower()}, {length.length}, "
                      f"{rstr(length.name)})?;", 2)
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
        """``alarms()`` and ``monitor()``, which give each alarmed field's
        level, and a method computing each one."""
        names = dict((lc, name) for name, lc in n.fields)
        place = lambda lc: self.place(n, lc, names)  # noqa: E731
        monitored = {k: ident for k, ident, _ in self.m.monitored}
        count = len(n.alarms)
        self.line(f"impl {n.ident} {{")
        self.line("/// The number of fields with an alarm.", 1)
        self.line(f"pub const ALARMS: usize = {count};", 1)
        self.line()
        self.line("/// The alarm level of each field with an alarm (`None` "
                  "when the", 1)
        self.line("/// field is absent).  Alarms that depend on earlier "
                  "samples count only", 1)
        self.line("/// this one (see `monitor`).", 1)
        self.line(f"pub fn alarms(&self) -> pk::Alarms<{count}> {{", 1)
        self.line("let mut a = pk::Alarms::new();", 2)
        for name, _, _ in n.alarms:
            self.line(f"a.push({rstr(name)}, self.alarm_{name}());", 2)
        self.line("a", 2)
        self.line("}", 1)
        self.line()
        self.line("/// Like `alarms()`, counting the earlier samples whose "
                  "state `m` keeps,", 1)
        self.line("/// for change ranges, minViolations and minConformance; "
                  "`t` is this", 1)
        self.line("/// sample's time in seconds.", 1)
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
            self.line()
            self.line(f"fn alarm_{name}(&self) -> Option<pk::AlarmLevel> {{", 1)
            self.alarm_body(name, lc, a, place, None)
            if a.stateful:
                self.line()
                self.line(f"fn monitor_{name}(&self, m: &mut AlarmMonitor, t: "
                          "f64) -> Option<pk::AlarmLevel> {", 1)
                self.alarm_body(name, lc, a, place, monitored[a.key])
        self.line("}")

    def algorithms(self, n: Node, place) -> None:
        """``algorithms()``: the output of each MathAlgorithm the packet
        triggers."""
        count = len(n.algorithms)
        self.line()
        self.line("/// The number of MathAlgorithms this packet triggers.", 1)
        self.line(f"pub const ALGORITHMS: usize = {count};", 1)
        self.line()
        self.line("/// The output of each MathAlgorithm this packet triggers "
                  "(`None` when", 1)
        self.line("/// an input is absent).", 1)
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
        """The body of a method giving a field's alarm level; with
        ``state``, the AlarmMonitor field that keeps its parameter's
        earlier samples."""
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
        for k, (terms, ad) in enumerate(a.contexts):
            cond = conjunction([term(c, place) for c in terms])
            self.line(f"{'if' if k == 0 else '} else if'} {cond} {{", d)
            self.alarm_def(ad, kind, place, d + 1, live)
        if a.contexts:
            if a.default is not None:
                self.line("} else {", d)
                self.alarm_def(a.default, kind, place, d + 1, live)
            self.line("}", d)
        elif a.default is not None:
            self.alarm_def(a.default, kind, place, d, live)
        if vt.kind == "array":
            self.line("worst = worst.max(lv);", d)
            self.line("}", 2)
            self.line("let lv = worst;", 2)
        if state is None:
            self.line("Some(pk::AlarmLevel::from_index(lv))", 2)
        else:
            if a.history:
                self.line("s.push(x as f64, t);", 2)
            self.line("Some(pk::AlarmLevel::from_index(s.update(lv, mv, mc)))",
                      2)
        self.line("}", 1)

    def alarm_def(self, ad, kind: str, place, depth: int,
                  live: bool = False) -> None:
        """Statements raising ``lv`` to the level that alarm definition
        ``ad`` gives value ``x`` (of kind ``kind``); when ``live``, with
        its change ranges (on AlarmState ``s``, at time ``t``) and setting
        ``mv`` and ``mc`` to its minViolations and minConformance."""
        if live and ad.change is not None:
            c = ad.change
            self.line(f"if let Some(d) = s.change(x as f64, t, {c.samples}, "
                      f"{str(c.per_second).lower()}, {str(c.percent).lower()}) "
                      "{", depth)
            for b in c.bands:
                self.band(b, "d", "float", depth + 1)
            self.line("}", depth)
        if live and (ad.min_violations, ad.min_conformance) != (1, 1):
            self.line(f"mv = {ad.min_violations};", depth)
            self.line(f"mc = {ad.min_conformance};", depth)
        for b in ad.bands:
            self.band(b, "x", kind, depth)
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

    def band(self, b, x: str, kind: str, depth: int) -> None:
        """Raises ``lv`` to the Band's level if value ``x`` (of kind
        ``kind``) violates it."""
        if kind == "int":
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
        """The name of the static holding a pattern's automaton."""
        for name, d in self.dfas:
            if d is dfa:
                return name
        name = f"DFA{len(self.dfas)}"
        self.dfas.append((name, dfa))
        return name

    def size_check(self, key: str, n: int) -> None:
        """Refuse to build where a packet of ``n`` bytes doesn't count its
        bits in a usize."""
        if n > NARROW_MAX_BYTES:
            self.line(f"const _: () = assert!({n}u64 <= usize::MAX as u64 / 8, "
                      f"{rstr(key + ' is too large for this usize')});")
            self.line()

    def kind(self, k: Kind) -> None:
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
        self.line("/// The alarm level of each field with an alarm (`None` "
                  "when the", 1)
        self.line("/// field is absent).", 1)
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
        self.line("/// The output of each MathAlgorithm the packet triggers.",
                  1)
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
        self.line("/// Like `alarms()`, counting the earlier samples whose "
                  "state `m` keeps.", 1)
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
    """The Rust module; without ``alloc`` its code never allocates."""
    return RustEmitter(mission, source, alloc).emit()
