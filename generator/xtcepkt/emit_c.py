"""Emit a header of packet codecs that compiles both as C11 and as C++.

The header uses only ``xtce/packet.h``, which is itself plain C.  C has no
namespaces, so every name the header defines starts with a prefix (by default
the root SpaceSystem name in snake_case followed by ``_``).  Fallible
functions return 0 or an ``XTCE_PK_*`` error code instead of throwing, and
nothing is allocated: callers supply the buffers.
"""

from __future__ import annotations

import math
import textwrap
from typing import Optional

from .common import (notes, stream_capacity, stream_doc, Ref, c_op, fast_order,
                     field_range, flt, header_lines, inputs, int_enc,
                     invertible, leaf_calibrators, math_value, size_args,
                     str_cap, string_spec)
from .compile import snake
from .model import (
    term_indexes, term_locals, term_paths, walk, Index, AggregateEnc, AllOf,
    AnyOf, ArrayEnc, BinaryEnc, Cond, ContextCal, DynSize, Criterion, Fixed,
    FloatEnc, IntEnc, Kind, Mark, MathOp, Mission, Node, Pad, Polynomial, Seek,
    Spline, StringEnc, Value, VType, Require, Fill, Segment, Verify)

# One level of indentation in the generated C.
INDENT = " " * 8

ENC = {"Unsigned": "XTCE_PK_UNSIGNED",
       "TwosComplement": "XTCE_PK_TWOS_COMPLEMENT",
       "SignMagnitude": "XTCE_PK_SIGN_MAGNITUDE",
       "OnesComplement": "XTCE_PK_ONES_COMPLEMENT"}

CHARSETS = {"UTF-8": "XTCE_PK_UTF8", "US-ASCII": "XTCE_PK_ASCII",
            "ISO-8859-1": "XTCE_PK_LATIN1",
            "Windows-1252": "XTCE_PK_WINDOWS1252", "UTF-16": "XTCE_PK_UTF16",
            "UTF-16BE": "XTCE_PK_UTF16BE", "UTF-16LE": "XTCE_PK_UTF16LE",
            "UTF-32": "XTCE_PK_UTF32", "UTF-32BE": "XTCE_PK_UTF32BE",
            "UTF-32LE": "XTCE_PK_UTF32LE"}
FLOATS = {"IEEE754": "XTCE_PK_IEEE", "MILSTD_1750A": "XTCE_PK_MIL1750A",
          "DEC": "XTCE_PK_DEC", "IBM": "XTCE_PK_IBM", "TI": "XTCE_PK_TI"}

I64_MIN, I64_MAX, U64_MAX = -(2**63), 2**63 - 1, 2**64 - 1


def checksum_spec(spec: tuple) -> str:
    """The initializer of an xtce_pk_checksum_spec: kind, bits, polynomial,
    init, final XOR, reflect data, reflect remainder and odd."""
    kind = spec[0]
    if kind == "crc":
        width, poly, init, final, ref_in, ref_out = spec[1:]
        return (f"{{XTCE_PK_CRC, {width}, UINT64_C(0x{poly:x}), "
                f"UINT64_C(0x{init:x}), UINT64_C(0x{final:x}), "
                f"{bool_lit(ref_in)}, {bool_lit(ref_out)}, false}}")
    bits = spec[1] if len(spec) > 1 and kind != "parity" else 0
    odd = kind == "parity" and spec[1]
    return (f"{{XTCE_PK_{kind.upper()}, {bits}, 0, 0, 0, false, false, "
            f"{bool_lit(odd)}}}")

# What `<empty optional> op <value>` gives, as with std::optional in C++.
EMPTY = {"==": False, "!=": True, "<": True, "<=": True, ">": False,
         ">=": False}


def cstr(s) -> str:
    """A C string literal of the UTF-8 bytes of `s` (a str or bytes)."""
    out = []
    for b in s.encode("utf-8") if isinstance(s, str) else s:
        c = chr(b)
        if c in '"\\':
            out.append("\\" + c)
        elif b < 0x20 or b >= 0x7F:
            out.append(f'\\x{b:02x}""')
        else:
            out.append(c)
    return '"' + "".join(out) + '"'


def blen(s) -> int:
    return len(s.encode("utf-8") if isinstance(s, str) else s)


def slit(v: int) -> str:
    return ("(-INT64_C(9223372036854775807) - 1)" if v == I64_MIN
            else f"INT64_C({v})")


def ulit(v: int) -> str:
    return f"UINT64_C({v})"


def cflt(x: float) -> str:
    """A double literal, infinities included."""
    if math.isinf(x):
        return "HUGE_VAL" if x > 0 else "-HUGE_VAL"
    return flt(x)


def bool_lit(b: bool) -> str:
    return "true" if b else "false"


def access(path) -> str:
    """The expression suffix for a member and element path."""
    return "".join(f".data[{p.i}]" if isinstance(p, Index) else "." + p
                   for p in path)


class Acc:
    """How generated code reaches a value: an expression, plus the expression
    telling whether it is present when it is optional."""

    def __init__(self, expr: str, present: Optional[str] = None):
        self.expr = expr
        self.present = present


class CEmitter:
    def __init__(self, mission: Mission, source: str, prefix: Optional[str]):
        self.m = mission
        self.source = source
        self.p = prefix if prefix is not None else snake(mission.name) + "_"
        self.out: list[str] = []
        # Keyed by id(VType): a parameter type and an argument type may share
        # a qualified name.
        # id of a string type or calibrator -> constant name
        self.consts: dict[int, str] = {}
        self.counts: dict[int, int] = {}  # calibrator -> table length
        self.orders: dict[int, str] = {}  # spline -> its segments' orders

    def line(self, s: str = "", depth: int = 0) -> None:
        self.out.append(INDENT * depth + s if s else "")

    # -- types and constants --

    def ctype(self, vt: VType) -> str:
        k = vt.kind
        if k == "array":
            return f"{self.p}{self.tname(vt.elem)}_list{vt.enc.max_count}"
        if k == "aggregate":
            return self.p + vt.ident
        if k == "binary" and vt.enc.variable:
            return f"{self.p}varbytes{vt.enc.bits // 8}"
        if k == "int":
            return f"{'' if vt.signed else 'u'}int{vt.width}_t"
        if k == "float":
            return "double"
        if k == "bool":
            return "bool"
        if k == "string":
            return f"{self.p}str{str_cap(vt)}"
        if k == "binary":
            return f"{self.p}bytes{vt.enc.bits // 8}"
        return self.p + vt.ident

    def tname(self, vt: VType) -> str:
        """The name of an element type inside the names of list types."""
        t = self.ctype(vt)
        t = t[len(self.p) :] if t.startswith(self.p) else t
        return t[:-2] if t.endswith("_t") else t

    def zero(self, vt: VType) -> str:
        return ("XTCE_PK_ZERO"
                if vt.kind in ("string", "binary", "array", "aggregate")
                else "0")

    def literal(self, vt: VType, v) -> str:
        k = vt.kind
        if k == "int":
            return slit(v) if vt.signed else ulit(v)
        if k == "float":
            return flt(v)
        if k == "enum":
            return slit(v)
        if k == "bool":
            return bool_lit(v)
        return cstr(v if k == "string" else bytes(v))

    def vtypes(self) -> list[VType]:
        """Every type of a value and the types inside it, inner ones first."""
        seen: dict[int, VType] = {}
        for n in self.m.nodes:
            for it in walk(n.all_items()):
                if isinstance(it, Value):
                    for vt in it.local.vtype.tree():
                        seen.setdefault(id(vt), vt)
        return list(seen.values())

    def declare_buffers(self) -> None:
        strs = sorted({str_cap(vt) for vt in self.vtypes()
                       if vt.kind == "string"})
        bins = sorted({vt.enc.bits // 8 for vt in self.vtypes()
                       if vt.kind == "binary" and not vt.enc.variable})
        vbins = sorted({vt.enc.bits // 8 for vt in self.vtypes()
                        if vt.kind == "binary" and vt.enc.variable})
        if strs:
            self.line()
            self.line("/* Strings of at most N bytes of UTF-8; `data` is "
                      "always NUL-terminated. */")
            for n in strs:
                self.line(f"typedef struct {self.p}str{n} {{")
                self.line("size_t len;", 1)
                self.line(f"char data[{n + 1}];", 1)
                self.line(f"}} {self.p}str{n};")
        if bins:
            self.line()
            self.line("/* Binary fields of exactly N bytes. */")
            for n in bins:
                self.line(f"typedef struct {self.p}bytes{n} {{")
                self.line(f"uint8_t data[{max(n, 1)}];", 1)
                self.line(f"}} {self.p}bytes{n};")
        if vbins:
            self.line()
            self.line("/* Binary fields of at most N bytes, whose size another "
                      "field gives. */")
            for n in vbins:
                self.line(f"typedef struct {self.p}varbytes{n} {{")
                self.line("size_t len;", 1)
                self.line(f"uint8_t data[{max(n, 1)}];", 1)
                self.line(f"}} {self.p}varbytes{n};")

    def declare_composites(self) -> None:
        """Array and aggregate types, each after the types inside it."""
        done = set()
        for vt in self.vtypes():
            name = self.ctype(vt)
            if vt.kind not in ("array", "aggregate") or name in done:
                continue
            done.add(name)
            self.line()
            if vt.kind == "array":
                self.line(f"/* Arrays of {self.ctype(vt.elem)}: `len` values "
                          "of `data`, in encoding order. */")
                self.line(f"typedef struct {name} {{")
                self.line("size_t len;", 1)
                self.line(f"{self.ctype(vt.elem)} "
                          f"data[{max(vt.enc.max_count, 1)}];", 1)
            else:
                self.line(f"/* Aggregate {vt.key}; its members are encoded in "
                          "this order. */")
                self.line(f"typedef struct {name} {{")
                for m in vt.enc.members:
                    self.line(f"{self.ctype(m.vtype)} {m.ident}; /* {m.name} "
                              f"({m.vtype.key}) */", 1)
            self.line(f"}} {name};")

    def declare_consts(self) -> None:
        strings, cals = [], []
        for vt in self.vtypes():
            if isinstance(vt.enc, StringEnc):
                name = f"{self.p}k{len(self.consts)}"
                self.consts[id(vt)] = name
                strings.append((name, vt))
            for cal in leaf_calibrators(vt.calibrator):
                if id(cal) not in self.consts:
                    name = f"{self.p}k{len(self.consts)}"
                    self.consts[id(cal)] = name
                    cals.append((name, cal, vt))
        if not strings and not cals:
            return
        self.line()
        for name, cal, vt in cals:
            if isinstance(cal, Polynomial):
                terms = ", ".join(f"{{{flt(c)}, {e}}}" for c, e in cal.terms)
                self.counts[id(cal)] = len(cal.terms)
                self.line(f"static const xtce_pk_term {name}[{len(cal.terms)}] "
                          f"= {{{terms}}}; /* {vt.key} */")
            else:
                pts = ", ".join(f"{{{flt(a)}, {flt(b)}}}"
                                for a, b in cal.points)
                self.counts[id(cal)] = len(cal.points)
                self.line("static const xtce_pk_point "
                          f"{name}[{len(cal.points)}] = {{{pts}}}; /* {vt.key} "
                          "*/")
                if cal.orders:
                    self.orders[id(cal)] = f"{name}_orders"
                    self.line("static const unsigned "
                              f"{name}_orders[{len(cal.orders)}] = "
                              f"{{{', '.join(map(str, cal.orders))}}};")
        for name, vt in strings:
            fixed, mx, term, tag = string_spec(vt)
            lit = (f"(const uint8_t*){cstr(term)}" if term is not None
                   else "NULL")
            spec = ", ".join([bool_lit(fixed is not None), str(fixed or 0),
                              str(mx), bool_lit(term is not None), lit,
                              str(len(term or b"")), str(tag),
                              CHARSETS[vt.enc.charset]])
            self.line(f"static const xtce_pk_string_spec {name} = {{{spec}}}; "
                      f"/* {vt.key} */")

    def table(self, cal) -> str:
        return f"{self.consts[id(cal)]}, {self.counts[id(cal)]}"

    # -- reading and writing one value --

    def calibrate(self, vt: VType, x: str, dst: str, f: str, depth: int,
                  ref) -> None:
        """Sets ``dst`` to ``x`` calibrated; ``ref.place`` gives the Acc of
        a Local the calibrator uses."""
        self.apply_cal(vt.calibrator, x, dst, f, depth, ref.place, "r->err")

    def uncalibrate(self, vt: VType, y: str, f: str, depth: int, ref) -> None:
        """Declares ``double y``, the raw value whose calibration is ``y``."""
        self.line("double y;", depth)
        self.invert_cal(vt.calibrator, y, "y", f, depth, ref.place)

    def math_expr(self, e: tuple, x: str, place) -> str:
        """C expression for a MathOperationCalibrator tree; ``x`` is the
        value being calibrated."""
        if e[0] == "const":
            return f"({flt(e[1])})"
        if e[0] == "raw":
            return x
        if e[0] == "value":
            r = e[1]
            v = place(r.local).expr
            for p in r.member:
                v += f".data[{p.i}]" if isinstance(p, Index) else "." + p
            return math_value(r.vtype, v, "c")
        args = [self.math_expr(a, x, place) for a in e[1:]]
        if e[0] in ("+", "-", "*"):
            return f"({args[0]} {e[0]} {args[1]})"
        b = args[1] if len(args) > 1 else "0.0"
        return f"xtce_pk_math({c_op(e[0])}, {args[0]}, {b})"

    def cases(self, cal: ContextCal, x: str, depth: int, place, err: str,
              body) -> None:
        """An if-else chain over a context calibrator's cases, on the value
        ``x``: ``body(c, x, depth)`` for the calibrator of the first case
        that holds, or the default."""
        v = f"c{depth}"
        self.line("{", depth)
        self.line(f"double {v} = {x};", depth + 1)
        self.line(f"(void){v};", depth + 1)
        refs = [p for terms, _ in cal.cases for t in terms
                for p in term_paths(t)]
        self.element_guards(refs, place, err, depth + 1)
        for k, (terms, c) in enumerate(cal.cases):
            cond = " && ".join(f"({self.term(t, place)})" for t in terms)
            keyword = "if" if k == 0 else "} else if"
            self.line(f"{keyword} ({cond or 'true'}) {{", depth + 1)
            body(c, v, depth + 2)
        self.line("} else {", depth + 1)
        body(cal.default, v, depth + 2)
        self.line("}", depth + 1)
        self.line("}", depth)

    def apply_cal(self, cal, x: str, dst: str, f: str, depth: int, place,
                  err: str) -> None:
        if cal is None:
            self.line(f"{dst} = {x};", depth)
        elif isinstance(cal, Polynomial):
            self.line(f"{dst} = xtce_pk_polynomial({x}, {self.table(cal)});",
                      depth)
        elif isinstance(cal, Spline) and cal.orders:
            ext = bool_lit(cal.extrapolate)
            self.line(f"XTCE_PK_TRY(xtce_pk_spline_orders({x}, "
                      f"{self.table(cal)}, {self.orders[id(cal)]}, {ext}, {f}, "
                      f"{err}, &{dst}));", depth)
        elif isinstance(cal, Spline):
            ext = bool_lit(cal.extrapolate)
            self.line(f"XTCE_PK_TRY(xtce_pk_spline({x}, {self.table(cal)}, "
                      f"{cal.order}, {ext}, {f}, {err}, &{dst}));", depth)
        elif isinstance(cal, MathOp):
            refs = [(r.local, r.member) for r in cal.refs()]
            self.element_guards(refs, place, err, depth)
            self.line(f"{dst} = {self.math_expr(cal.expr, x, place)};", depth)
        else:
            self.cases(cal, x, depth, place, err,
                       lambda c, v, d: self.apply_cal(c, v, dst, f, d, place,
                                                      err))

    def invert_cal(self, cal, y: str, dst: str, f: str, depth: int,
                   place) -> None:
        if cal is None:
            self.line(f"{dst} = {y};", depth)
        elif not cal.invertible:
            self.line("return xtce_pk_fail(w->err, XTCE_PK_NOT_INVERTIBLE, "
                      f"{f}, NULL);", depth)
        elif isinstance(cal, Polynomial):
            self.line(f"{dst} = xtce_pk_polynomial_inverse({y}, "
                      f"{flt(cal.intercept)}, {flt(cal.slope)});", depth)
        elif isinstance(cal, Spline):
            ext = bool_lit(cal.extrapolate)
            self.line(f"XTCE_PK_TRY(xtce_pk_spline_inverse({y}, "
                      f"{self.table(cal)}, {ext}, {f}, w->err, &{dst}));",
                      depth)
        else:
            self.cases(cal, y, depth, place, "w->err",
                       lambda c, v, d: self.invert_cal(c, v, dst, f, d, place))

    def size(self, d, err: str, f: str, ref, depth: int) -> None:
        """Declares ``size_t n``: a count, or the size or count that a value
        gives (``ref`` gives the expression for that value)."""
        if not isinstance(d, DynSize):
            self.line(f"size_t n = {d};", depth)
            return
        if d.member is None:
            refs = ([(d.local, d.path)] if d.lookup is None
                    else [p for criteria, _ in d.lookup for c in criteria
                          for p in term_paths(c)])
            self.element_guards(refs, ref.place, err, depth)
        self.line("size_t n;", depth)
        x = f"(double)({ref(d)})"
        args = (f"{x}, {flt(d.slope)}, {flt(d.intercept)}, {d.unit}, {d.max}, "
                f"{f}, {err}, &n")
        self.line(f"XTCE_PK_TRY(xtce_pk_dynamic_size({args}));", depth)
        if d.factor != 1:
            self.line(f"n *= {d.factor};", depth)

    def element_guards(self, refs, place, err: str, depth: int) -> None:
        """Checks that the elements of arrays whose count varies that
        ``refs`` (local and path pairs) read are there.  ``place`` gives the
        Acc of a Local."""
        done = set()
        for lc, path in refs:
            x = place(lc).expr
            for p in path:
                if not isinstance(p, Index):
                    x += "." + p
                    continue
                if p.count is None and (x, p.i) not in done:
                    done.add((x, p.i))
                    self.line(f"XTCE_PK_TRY(xtce_pk_element({x}.len, {p.i}, "
                              f"{cstr(lc.name)}, {err}));", depth)
                x += f".data[{p.i}]"

    def string_spec(self, vt: VType, err: str, f: str, ref, depth: int) -> str:
        """The address of the spec of a string type, declaring a sized copy
        when another value gives its size."""
        k = self.consts[id(vt)]
        if vt.enc.dyn is None:
            return f"&{k}"
        self.size(vt.enc.dyn, err, f, ref, depth)
        self.line(f"xtce_pk_string_spec s = xtce_pk_string_spec_sized(&{k}, "
                  "n);", depth)
        return "&s"

    def read_value(self, vt: VType, dst: str, f: str, depth: int, ref) -> None:
        """Reads into ``dst``; ``ref`` gives the expression for a value that
        gives a size or count."""
        e = vt.enc
        if isinstance(e, AggregateEnc):
            inner = Ref(lambda d: f"{dst}.{d.member}" if d.member else ref(d),
                        ref.place)
            for m in e.members:
                if m.skip:
                    self.line(f"XTCE_PK_TRY(xtce_pk_read_pad(r, {m.skip}, "
                              f"{f}));", depth)
                self.read_value(m.vtype, f"{dst}.{m.ident}", f, depth, inner)
            if e.pad:
                self.line(f"XTCE_PK_TRY(xtce_pk_read_pad(r, {e.pad}, {f}));",
                          depth)
        elif isinstance(e, ArrayEnc):
            # Indexes are numbered by depth: an aggregate element may hold
            # another array.
            i = f"i{depth}"
            self.line("{", depth)
            d = depth + 1
            self.size(e.count, "r->err", f, ref, d)
            self.line(f"{dst}.len = n;", d)
            self.line(f"for (size_t {i} = 0; {i} < n; ++{i}) {{", d)
            if e.gap:
                self.gap(e, "read", f, ref, i, d + 1)
            self.read_value(e.elem, f"{dst}.data[{i}]", f, d + 1, ref)
            self.line("}", d)
            self.line("}", depth)
        elif isinstance(e, BinaryEnc) and e.variable:
            self.line("{", depth)
            if e.rest:
                self.line("size_t n;", depth + 1)
                self.line(f"XTCE_PK_TRY(xtce_pk_read_rest(r, {e.bits // 8}, "
                          f"{f}, &n));", depth + 1)
            else:
                self.size(e.dyn, "r->err", f, ref, depth + 1)
            if vt.kind == "string":
                self.line(f"XTCE_PK_TRY(xtce_pk_read_utf8(r, n, {f}, "
                          f"{dst}.data, &{dst}.len));", depth + 1)
            else:
                self.line(f"XTCE_PK_TRY(xtce_pk_read_bytes(r, n, {f}, "
                          f"{dst}.data));", depth + 1)
                self.line(f"{dst}.len = n;", depth + 1)
            self.line("}", depth)
        elif isinstance(e, (IntEnc, FloatEnc)):
            self.line("{", depth)
            d = depth + 1
            self.read_raw(e, f, d)
            if (isinstance(e, FloatEnc) and e.fmt == "IEEE754"
                    and e.bits in (32, 64)):
                self.calibrate(vt, f"xtce_pk_float_from_raw(raw, {e.bits})",
                               dst, f, d, ref)
            elif isinstance(e, FloatEnc):
                self.line("double x;", d)
                self.line(f"XTCE_PK_TRY(xtce_pk_float_decode({FLOATS[e.fmt]}, "
                          f"{e.bits}, hi, raw, {f}, r->err, &x));", d)
                self.calibrate(vt, "x", dst, f, d, ref)
            else:
                if e.bcd:
                    self.line(f"XTCE_PK_TRY(xtce_pk_bcd_decode(raw, {e.bits}, "
                              f"{bool_lit(e.encoding == 'packedBCD')}, {f}, "
                              "r->err, &raw));", d)
                enc = ENC[int_enc(vt)]
                signed = f"xtce_pk_to_signed(raw, {e.bits}, {enc})"
                if vt.kind == "int" and vt.calibrator is not None:
                    lo, lim = vt.eng_limits
                    self.line("double y, v;", d)
                    self.calibrate(vt,
                                   f"xtce_pk_int_to_double(raw, {e.bits}, "
                                   f"{enc})", "y", f, d, ref)
                    self.line(f"XTCE_PK_TRY(xtce_pk_round_to_int(y, {flt(lo)}, "
                              f"{flt(lim)}, {f}, r->err, &v));", d)
                    self.line(f"{dst} = ({self.ctype(vt)})v;", d)
                elif vt.kind == "int":
                    self.line(f"{dst} = ({self.ctype(vt)})("
                              f"{signed if vt.signed else 'raw'});", d)
                elif vt.kind == "enum":
                    self.line(f"{dst} = "
                              f"{signed if vt.signed else '(int64_t)raw'};", d)
                elif vt.kind == "bool":
                    self.line(f"{dst} = raw != 0;", d)
                else:
                    self.calibrate(vt,
                                   f"xtce_pk_int_to_double(raw, {e.bits}, "
                                   f"{enc})", dst, f, d, ref)
            self.line("}", depth)
        elif isinstance(e, BinaryEnc) and vt.kind == "string":
            self.line(f"XTCE_PK_TRY(xtce_pk_read_utf8(r, {e.bits // 8}, {f}, "
                      f"{dst}.data, &{dst}.len));", depth)
        elif isinstance(e, BinaryEnc):
            self.line(f"XTCE_PK_TRY(xtce_pk_read_bytes(r, {e.bits // 8}, {f}, "
                      f"{dst}.data));", depth)
        elif vt.kind == "bool":
            # One of two strings.
            cap = str_cap(vt)
            self.line("{", depth)
            d = depth + 1
            self.line(f"char s[{cap + 1}];", d)
            self.line("size_t n;", d)
            spec = self.string_spec(vt, "r->err", f, ref, d)
            self.line(f"XTCE_PK_TRY(xtce_pk_read_string(r, {spec}, {f}, s, "
                      f"{cap}, &n));", d)
            one, zero = vt.one_string, vt.zero_string
            self.line(f"if (n == {blen(one)} && memcmp(s, {cstr(one)}, n) == "
                      f"0) {dst} = true;", d)
            self.line(f"else if (n == {blen(zero)} && memcmp(s, {cstr(zero)}, "
                      f"n) == 0) {dst} = false;", d)
            self.line("else return xtce_pk_fail(r->err, XTCE_PK_OUT_OF_RANGE, "
                      f"{f}, NULL);", d)
            self.line("}", depth)
        else:
            self.line("{", depth)
            spec = self.string_spec(vt, "r->err", f, ref, depth + 1)
            args = f"r, {spec}, {f}, {dst}.data, {str_cap(vt)}, &{dst}.len"
            self.line(f"XTCE_PK_TRY(xtce_pk_read_string({args}));", depth + 1)
            self.line("}", depth)

    def order_args(self, e, depth: int) -> str:
        """The bit order, byte order and order list arguments of the
        general reader and writer, declaring the list when there is one."""
        if isinstance(e.order, tuple):
            self.line("static const uint8_t order[] = "
                      f"{{{', '.join(map(str, e.order))}}};", depth)
            order, lst = "XTCE_PK_LIST", "order"
        else:
            order = "XTCE_PK_BIG" if e.order == "be" else "XTCE_PK_LITTLE"
            lst = "NULL"
        return f"{bool_lit(e.lsb)}, {order}, {lst}"

    def read_raw(self, e, f: str, depth: int) -> None:
        """Declares ``raw`` (and ``hi``, for floats) and reads the raw value
        of an integer or float encoding into them."""
        wide = isinstance(e, FloatEnc)
        self.line("uint64_t raw, hi = 0;" if wide else "uint64_t raw;", depth)
        if wide:
            self.line("(void)hi;", depth)
        if fast_order(e):
            self.line(f"XTCE_PK_TRY(xtce_pk_read_uint(r, {e.bits}, "
                      f"{bool_lit(e.little_endian)}, {f}, &raw));", depth)
            return
        args = self.order_args(e, depth)
        if wide:
            self.line(f"XTCE_PK_TRY(xtce_pk_read_ordered(r, {e.bits}, {args}, "
                      f"{f}, &hi, &raw));", depth)
        else:
            self.line(f"XTCE_PK_TRY(xtce_pk_read_ordered64(r, {e.bits}, "
                      f"{args}, {f}, &raw));", depth)

    def write_raw(self, e, depth: int, hi: str = "0") -> None:
        """Writes ``raw`` (with ``hi``) as the raw value of an integer or
        float encoding."""
        if fast_order(e):
            self.line(f"XTCE_PK_TRY(xtce_pk_write_uint(w, {e.bits}, "
                      f"{bool_lit(e.little_endian)}, raw));", depth)
            return
        args = self.order_args(e, depth)
        self.line(f"XTCE_PK_TRY(xtce_pk_write_ordered(w, {e.bits}, {args}, "
                  f"{hi}, raw));", depth)

    def write_value(self, vt: VType, x: str, f: str, depth: int, ref,
                    fixed=None) -> None:
        """Writes `x`: a field access, or the literal of the value `fixed`;
        ``ref`` gives the expression for a value that gives a size or count."""
        is_lit = fixed is not None
        e = vt.enc
        bad = f"return xtce_pk_fail(w->err, XTCE_PK_BAD_SIZE, {f}, NULL);"
        if isinstance(e, AggregateEnc):
            inner = Ref(lambda d: f"{x}.{d.member}" if d.member else ref(d),
                        ref.place)
            for m in e.members:
                if m.skip:
                    self.line(f"XTCE_PK_TRY(xtce_pk_write_zeros(w, {m.skip}));",
                              depth)
                self.write_value(m.vtype, f"{x}.{m.ident}", f, depth, inner)
            if e.pad:
                self.line(f"XTCE_PK_TRY(xtce_pk_write_zeros(w, {e.pad}));",
                          depth)
            return
        if isinstance(e, ArrayEnc):
            i = f"i{depth}"
            self.line("{", depth)
            d = depth + 1
            self.size(e.count, "w->err", f, ref, d)
            self.line(f"if ({x}.len != n) {bad}", d)
            self.line(f"for (size_t {i} = 0; {i} < n; ++{i}) {{", d)
            if e.gap:
                self.gap(e, "write", f, ref, i, d + 1)
            self.write_value(e.elem, f"{x}.data[{i}]", f, d + 1, ref)
            self.line("}", d)
            self.line("}", depth)
            return
        if isinstance(e, BinaryEnc) and e.rest:
            data, n = ((f"(const uint8_t*){x}", str(len(fixed))) if is_lit
                       else (f"{x}.data", f"{x}.len"))
            self.line(f"if ({n} > {e.bits // 8}) return xtce_pk_fail(w->err, "
                      f"XTCE_PK_TOO_LONG, {f}, NULL);", depth)
            self.line(f"XTCE_PK_TRY(xtce_pk_write_bytes(w, {data}, {n}));",
                      depth)
            return
        if isinstance(e, BinaryEnc) and e.dyn is not None:
            if vt.kind == "string":
                data, n = ((x, str(blen(fixed))) if is_lit
                           else (f"{x}.data", f"{x}.len"))
                put = f"xtce_pk_write_utf8(w, {data}, n, {f})"
            else:
                data, n = ((f"(const uint8_t*){x}", str(len(fixed))) if is_lit
                           else (f"{x}.data", f"{x}.len"))
                put = f"xtce_pk_write_bytes(w, {data}, n)"
            self.line("{", depth)
            self.size(e.dyn, "w->err", f, ref, depth + 1)
            self.line(f"if ({n} != n) {bad}", depth + 1)
            self.line(f"XTCE_PK_TRY({put});", depth + 1)
            self.line("}", depth)
            return
        if (isinstance(e, (IntEnc, FloatEnc)) and vt.kind in ("float", "int")
                and not invertible(vt)):
            self.line("return xtce_pk_fail(w->err, XTCE_PK_NOT_INVERTIBLE, "
                      f"{f}, NULL);", depth)
            return
        if isinstance(e, FloatEnc):
            self.line("{", depth)
            d = depth + 1
            self.uncalibrate(vt, x, f, d, ref)
            if e.fmt == "IEEE754" and e.bits in (32, 64):
                self.line(f"uint64_t raw = xtce_pk_float_to_raw(y, {e.bits});",
                          d)
                self.write_raw(e, d)
            else:
                self.line("uint64_t raw, hi;", d)
                self.line(f"XTCE_PK_TRY(xtce_pk_float_encode({FLOATS[e.fmt]}, "
                          f"{e.bits}, y, {f}, w->err, &hi, &raw));", d)
                self.write_raw(e, d, "hi")
            self.line("}", depth)
        elif isinstance(e, IntEnc):
            bits, enc = e.bits, ENC[int_enc(vt)]
            self.line("{", depth)
            d = depth + 1
            self.line("uint64_t raw;", d)
            if vt.kind == "int" and vt.calibrator is not None:
                self.uncalibrate(vt, f"(double)({x})", f, d, ref)
                self.line(f"XTCE_PK_TRY(xtce_pk_double_to_int(y, {bits}, "
                          f"{enc}, {f}, w->err, &raw));", d)
            elif vt.kind == "int" and vt.signed:
                self.line(f"XTCE_PK_TRY(xtce_pk_from_signed((int64_t)({x}), "
                          f"{bits}, {enc}, {f}, w->err, &raw));", d)
            elif vt.kind == "int":
                self.line(f"XTCE_PK_TRY(xtce_pk_from_unsigned((uint64_t)({x}), "
                          f"{bits}, {f}, w->err, &raw));", d)
            elif vt.kind == "enum":
                self.line(f"XTCE_PK_TRY(xtce_pk_from_signed({x}, {bits}, "
                          f"{enc}, {f}, w->err, &raw));", d)
            elif vt.kind == "bool":
                self.line(f"raw = ({x}) ? 1u : 0u;", d)
            else:
                self.uncalibrate(vt, x, f, d, ref)
                self.line(f"XTCE_PK_TRY(xtce_pk_double_to_int(y, {bits}, "
                          f"{enc}, {f}, w->err, &raw));", d)
            if e.bcd:
                self.line(f"XTCE_PK_TRY(xtce_pk_bcd_encode(raw, {bits}, "
                          f"{bool_lit(e.encoding == 'packedBCD')}, {f}, "
                          "w->err, &raw));", d)
            self.write_raw(e, d)
            self.line("}", depth)
        elif isinstance(e, BinaryEnc) and vt.kind == "string":
            # Exactly the field's bytes, like binary values.
            data, n = ((x, str(blen(fixed))) if is_lit
                       else (f"{x}.data", f"{x}.len"))
            size = e.bits // 8
            self.line(f"if ({n} > {size}) return xtce_pk_fail(w->err, "
                      f"XTCE_PK_TOO_LONG, {f}, NULL);", depth)
            self.line(f"if ({n} < {size}) return xtce_pk_fail(w->err, "
                      f"XTCE_PK_OUT_OF_RANGE, {f}, NULL);", depth)
            self.line(f"XTCE_PK_TRY(xtce_pk_write_utf8(w, {data}, {size}, "
                      f"{f}));", depth)
        elif isinstance(e, BinaryEnc):
            data = f"(const uint8_t*){x}" if is_lit else f"{x}.data"
            self.line(f"XTCE_PK_TRY(xtce_pk_write_bytes(w, {data}, "
                      f"{e.bits // 8}));", depth)
        elif vt.kind == "bool":
            one, zero = vt.one_string, vt.zero_string
            self.line("{", depth)
            spec = self.string_spec(vt, "w->err", f, ref, depth + 1)
            args = (f"({x}) ? {cstr(one)} : {cstr(zero)}, ({x}) ? {blen(one)} "
                    f": {blen(zero)}")
            self.line(f"XTCE_PK_TRY(xtce_pk_write_string(w, {spec}, {args}, "
                      f"{f}));", depth + 1)
            self.line("}", depth)
        else:
            data, n = ((x, str(blen(fixed))) if is_lit
                       else (f"{x}.data", f"{x}.len"))
            self.line("{", depth)
            spec = self.string_spec(vt, "w->err", f, ref, depth + 1)
            self.line(f"XTCE_PK_TRY(xtce_pk_write_string(w, {spec}, {data}, "
                      f"{n}, {f}));", depth + 1)
            self.line("}", depth)

    def term(self, t, place) -> str:
        """Expression for a criterion or a tree of them; ``place`` gives
        the Acc of a Local."""
        if isinstance(t, (AllOf, AnyOf)):
            return (" && " if isinstance(t, AllOf)
                    else " || ").join(f"({self.term(x, place)})"
                                      for x in t.terms)
        return self.condition(t, place(t.local),
                              place(t.other) if t.other is not None else None)

    def condition(self, c: Criterion, acc: Acc,
                  other: Optional[Acc] = None) -> str:
        vt, x, op, v = c.vtype, acc.expr + access(c.path), c.op, c.value
        k = vt.kind
        if other is not None:
            # Both values are present: the compiler rejects optional ones.
            y = other.expr + access(c.other_path)
            if k in ("int", "enum"):
                ovt = c.other_target or c.other.vtype
                a = "i" if k == "enum" or vt.signed else "u"
                b = "i" if k == "enum" or ovt.signed else "u"
                return (f"xtce_pk_cmp_{a if a == b else a + b}({x}, {y}) {op} "
                        "0")
            if k == "string":
                return (f"xtce_pk_bytes_cmp({x}.data, {x}.len, {y}.data, "
                        f"{y}.len) {op} 0")
            return f"{x} {op} {y}"
        if k in ("int", "enum"):
            signed = k == "enum" or vt.signed
            lo, hi = (I64_MIN, I64_MAX) if signed else (0, U64_MAX)
            if v < lo or v > hi:
                # Every value of the field compares the same way.
                below = v < lo
                holds = {"==": False, "!=": True, "<": not below,
                         "<=": not below, ">": below, ">=": below}[op]
                cond = f"((void)({x}), {int(holds)})"
            elif signed:
                cond = f"xtce_pk_cmp_i({x}, {slit(v)}) {op} 0"
            else:
                cond = f"xtce_pk_cmp_u({x}, {ulit(v)}) {op} 0"
        elif k == "string":
            cond = (f"xtce_pk_bytes_cmp({x}.data, {x}.len, {cstr(v)}, "
                    f"{blen(v)}) {op} 0")
        elif k == "binary":
            n = vt.enc.bits // 8
            cond = (f"xtce_pk_bytes_cmp({x}.data, {n}, {cstr(bytes(v))}, "
                    f"{len(v)}) {op} 0")
        else:
            cond = f"{x} {op} {self.literal(vt, v)}"
        if acc.present is None:
            return cond
        if EMPTY[op]:
            return f"!{acc.present} || ({cond})"
        return f"{acc.present} && ({cond})"

    # -- output --

    def emit(self) -> str:
        m, p = self.m, self.p
        guard = f"{p.upper()}XTCE_H" if p else "XTCE_PACKETS_H"
        for i, h in enumerate(header_lines(m, self.source)):
            self.line(("/* " if i == 0 else " * ") + h)
        self.line(" *")
        self.line(f" * Packet codecs for the {m.name} mission, for C11 and "
                  "C++. */")
        self.line(f"#ifndef {guard}")
        self.line(f"#define {guard}")
        self.line()
        for h in ("stdbool.h", "stddef.h", "stdint.h", "string.h"):
            self.line(f"#include <{h}>")
        self.line()
        self.line('#include "xtce/packet.h"')
        self.line()
        self.line("#ifdef __cplusplus")
        self.line('extern "C" {')
        self.line("#endif")
        for vt in m.types:
            self.enum_type(vt)
        self.declare_buffers()
        self.declare_composites()
        self.declare_consts()
        self.declare_monitor()
        for n in m.nodes:
            self.declare(n)
        for k in m.kinds:
            self.declare_kind(k)
        self.declare_dfas()
        for n in m.nodes:
            self.define(n)
        for k in m.kinds:
            self.define_kind(k)
        for st in m.streams:
            self.stream(st)
        self.line()
        self.line("#ifdef __cplusplus")
        self.line("}")
        self.line("#endif")
        self.line()
        self.line(f"#endif /* {guard} */")
        return "\n".join(self.out) + "\n"

    def enum_type(self, vt: VType) -> None:
        t = self.p + vt.ident
        self.line()
        self.line(f"/* Enumeration {vt.key}.  Holds the raw value, so values "
                  "the enumeration")
        self.line(" * does not list survive decoding. */")
        self.line(f"typedef int64_t {t};")
        for lab in vt.labels:
            self.line(f"#define {t}_{lab.ident} {slit(lab.value)}")
        self.line()
        self.line("/* The label of a value, or NULL if the enumeration does "
                  "not list it. */")
        self.line(f"static inline const char* {t}_label({t} x) {{")
        for lab in vt.labels:
            if lab.value == lab.max_value:
                self.line(f"if (x == {slit(lab.value)}) return "
                          f"{cstr(lab.label)};", 1)
            else:
                self.line(f"if (x >= {slit(lab.value)} && x <= "
                          f"{slit(lab.max_value)}) return {cstr(lab.label)};",
                          1)
        self.line("return NULL;", 1)
        self.line("}")
        self.line()
        self.line(f"static inline void {t}_describe(xtce_pk_text* t, {t} x) {{")
        self.line(f"const char* l = {t}_label(x);", 1)
        self.line("if (l) {", 1)
        self.line("xtce_pk_text_puts(t, l);", 2)
        self.line("} else {", 1)
        self.line("xtce_pk_text_int(t, x);", 2)
        self.line("}", 1)
        self.line("}")

    def declare(self, n: Node) -> None:
        t = self.p + n.ident
        what = "Sequence container" if n.kind == "tm" else "Meta-command"
        self.line()
        self.line(f"/* {what} {n.key}.")
        if n.fixed:
            fixed = ", ".join(f"{lc.name} = {v!r}" for lc, v in n.fixed.items())
            self.line(f" * Fixed by its inheritance: {fixed}.")
        length = n.length_field()
        if length is not None:
            self.line(f" * {length.name} is the packet length: computed when "
                      "encoding, checked when decoding.")
        for lc, (c, _) in n.derived.items():
            self.line(f" * {lc.name} gives the size of {c.name}: computed from "
                      "its length when encoding.")
        for note in notes(n):
            for text in textwrap.wrap(note, 72):
                self.line(f" * {text.replace('*/', '* /')}")
        self.line(" */")
        self.line(f"#define {t}_NAME {cstr(n.key)}")
        self.line(f"#define {t}_MAX_BYTES {n.max_bytes}")
        self.line(f"typedef struct {t} {{")
        for name, lc in n.fields:
            doc = f"; {lc.vtype.doc}" if lc.vtype.doc else ""
            if lc.optional:
                self.line(f"bool has_{name}; /* whether {name} is present (its "
                          "include condition holds) */", 1)
            self.line(f"{self.ctype(lc.vtype)} {name}; /* {lc.key} "
                      f"({lc.vtype.key}{doc}) */", 1)
        if not n.fields:
            self.line("char unused_; /* C structs need a member */", 1)
        self.line(f"}} {t};")
        self.line(f"/* The number of fields with an alarm. */")
        self.line(f"#define {t}_ALARMS {len(n.alarms)}")
        self.line("/* The number of MathAlgorithms it triggers. */")
        self.line(f"#define {t}_ALGORITHMS {len(n.algorithms)}")
        self.packet_prototypes(t)

    def packet_prototypes(self, t: str, kind: bool = False) -> None:
        self.line()
        self.line("/* Zeroes every field. */")
        self.line(f"static inline void {t}_init({t}* p) {{ memset(p, 0, sizeof "
                  "*p); }")
        self.line("/* Decodes a packet that starts at the first byte of "
                  "`data`.")
        if kind:
            self.line(" * Reads the root's entries, then picks the first child "
                      "(in document order)")
            self.line(" * whose criteria match, recursively.")
        self.line(" */")
        self.line(f"static inline int {t}_decode({t}* out, const uint8_t* "
                  "data, size_t len, xtce_pk_error* err);")
        self.line(f"static inline int {t}_encode_into(const {t}* p, "
                  "xtce_pk_writer* w);")
        self.line("/* Encodes into the `cap` bytes at `buf` and stores the "
                  "packet's size in `*len`. */")
        self.line(f"static inline int {t}_encode(const {t}* p, uint8_t* buf, "
                  "size_t cap, size_t* len, xtce_pk_error* err);")
        self.line("/* Writes the canonical text form, identical in every "
                  "generated language, like")
        self.line(" * snprintf: returns its length and truncates it to fit "
                  "`cap` bytes. */")
        self.line(f"static inline size_t {t}_describe(const {t}* p, char* buf, "
                  "size_t cap);")
        self.line("/* Stores the alarm level of each field with an alarm in "
                  f"`out` ({t}_ALARMS")
        self.line(" * of them at most) and returns how many there are. */")
        self.line(f"static inline size_t {t}_alarms(const {t}* p, "
                  "xtce_pk_alarm* out);")
        self.line("/* Like _alarms, counting the earlier samples whose state "
                  "`m` keeps, for")
        self.line(" * change ranges, minViolations and minConformance; `t` is "
                  "this sample's")
        self.line(" * time in seconds. */")
        self.line(f"static inline size_t {t}_monitor(const {t}* p, "
                  f"{self.p}AlarmMonitor* m, double t, xtce_pk_alarm* out);")
        self.line("/* Stores the output of each MathAlgorithm the packet "
                  f"triggers in `out` ({t}_ALGORITHMS")
        self.line(" * of them at most) and returns how many there are. */")
        self.line(f"static inline size_t {t}_algorithms(const {t}* p, "
                  "xtce_pk_output* out);")

    def declare_kind(self, k: Kind) -> None:
        t = self.p + k.ident
        self.line()
        self.line(f"/* Every concrete packet derived from {k.root.key}. */")
        self.line(f"#define {t}_NAME {cstr(k.root.key)}")
        self.line(f"#define {t}_MAX_BYTES {k.max_bytes}")
        self.line(f"typedef enum {t}_tag {{")
        for mem in k.members:
            self.line(f"{t}_{mem.ident}_tag,", 1)
        self.line(f"}} {t}_tag;")
        self.line(f"typedef struct {t} {{")
        self.line(f"{t}_tag tag;", 1)
        self.line("union {", 1)
        for mem in k.members:
            self.line(f"{self.p}{mem.ident} {mem.ident};", 2)
        self.line("} u;", 1)
        self.line(f"}} {t};")
        self.line(f"/* The most fields with an alarm that a member has. */")
        self.line(f"#define {t}_ALARMS "
                  f"{max(len(mem.alarms) for mem in k.members)}")
        self.line("/* The most MathAlgorithms that a member triggers. */")
        self.line(f"#define {t}_ALGORITHMS "
                  f"{max(len(mem.algorithms) for mem in k.members)}")
        self.packet_prototypes(t, kind=True)

    def size_source(self, d: DynSize, place) -> str:
        """Expression for the value a bound DynSize adjusts: a field, or the
        first matching value of a DiscreteLookupList.  ``place`` gives the
        Acc of a Local."""
        if d.lookup is None:
            return place(d.local).expr + access(d.path)
        x = slit(d.default)
        for criteria, v in reversed(d.lookup):
            cond = " && ".join(f"({self.term(c, place)})" for c in criteria)
            x = f"({cond}) ? {slit(v)} : {x}"
        return f"({x})"

    @property
    def read_ref(self) -> Ref:
        """The value a size or location uses, when decoding."""
        return Ref(lambda d: self.size_source(d, self.read_acc), self.read_acc)

    def write_ref(self, n: Node, names: dict) -> Ref:
        """The value a size or location uses, when encoding."""
        def place(lc):
            return self.place(n, lc, names)
        return Ref(lambda d: self.size_source(d, place), place)

    def seek(self, it: Seek, side: str, ref, where: str, depth: int) -> None:
        """A Seek, by the reader (``side`` "read") or writer ("write")."""
        obj = "r" if side == "read" else "w"
        f = cstr(where)
        off = str(it.offset)
        if isinstance(it.offset, DynSize):
            self.line("{", depth)
            depth += 1
            self.size(it.offset, f"{obj}->err", f, ref, depth)
            off = "(int64_t)n"
        if it.from_start is None:
            self.line(f"XTCE_PK_TRY(xtce_pk_{side}_skip({obj}, {off}, {f}));",
                      depth)
        else:
            self.line(f"XTCE_PK_TRY(xtce_pk_{side}_seek({obj}, "
                      f"{self.mark(it.from_start)}, {off}, {f}));", depth)
        if isinstance(it.offset, DynSize):
            self.line("}", depth - 1)

    def read_items(self, items, depth: int, where: str,
                   nested: bool = False) -> None:
        for it in items:
            if isinstance(it, Seek):
                self.seek(it, "read", self.read_ref, where, depth)
            elif isinstance(it, Mark):
                self.line(f"size_t m{it.id} = r->pos;", depth)
                self.line(f"(void)m{it.id};", depth)
            elif isinstance(it, Require):
                self.require(it, self.read_acc, "r->err", depth)
            elif isinstance(it, Verify):
                self.verify(it.check, depth)
            elif isinstance(it, Fill):
                self.fill(it, "r", self.read_ref, depth)
            elif isinstance(it, Pad):
                self.line(f"XTCE_PK_TRY(xtce_pk_read_pad(r, {it.bits}, "
                          f"{cstr(where)}));", depth)
            elif isinstance(it, Segment):
                lc = it.local
                if it.first:
                    self.line(f"uint64_t s{lc.id} = 0;", depth)
                self.line("{", depth)
                self.line("uint64_t b;", depth + 1)
                self.line(f"XTCE_PK_TRY(xtce_pk_read_bits(r, {it.bits}, "
                          f"{cstr(lc.name)}, &b));", depth + 1)
                self.line(f"s{lc.id} |= b << {it.shift};", depth + 1)
                self.line("}", depth)
            elif isinstance(it, Value):
                lc = it.local
                if lc.checksum:
                    self.line(f"size_t p{lc.id} = r->pos;", depth)
                    self.line(f"(void)p{lc.id};", depth)
                if not nested:
                    self.local(lc.vtype, f"v{lc.id}", depth)
                if it.assembled:
                    # Decode the value from its segments' bits.
                    self.line("{", depth)
                    d = depth + 1
                    self.line("uint8_t b[8];", d)
                    self.line("xtce_pk_reader sub;", d)
                    self.line(f"xtce_pk_segment_bytes(b, s{lc.id}, "
                              f"{it.assembled});", d)
                    self.line("sub = xtce_pk_reader_init(b, 8, r->err);", d)
                    self.line("{", d)
                    self.line("xtce_pk_reader* r = &sub;", d + 1)
                    self.read_value(lc.vtype, f"v{lc.id}", cstr(lc.name), d + 1,
                                    self.read_ref)
                    self.line("}", d)
                    self.line("}", depth)
                    continue
                self.read_value(lc.vtype, f"v{lc.id}", cstr(lc.name), depth,
                                self.read_ref)
                if lc.length is not None:
                    self.line(f"XTCE_PK_TRY(xtce_pk_read_limit(r, v{lc.id}, "
                              f"INT64_C({lc.length}), {cstr(lc.name)}));",
                              depth)
                if nested:
                    self.line(f"h{lc.id} = true;", depth)
            elif isinstance(it, Cond):
                if not nested:
                    for v in walk(it.items):
                        if isinstance(v, Value):
                            self.line(f"bool h{v.local.id} = false;", depth)
                            self.line(f"(void)h{v.local.id};", depth)
                            self.local(v.local.vtype, f"v{v.local.id}", depth)
                self.element_guards(
                    [p for c in it.criteria for p in term_paths(c)],
                    self.read_acc, "r->err", depth)
                cond = " && ".join(f"({self.term(c, self.read_acc)})"
                                   for c in it.criteria)
                self.line(f"if ({cond}) {{", depth)
                self.read_items(it.items, depth + 1, where, True)
                self.line("}", depth)
            else:
                self.line("{", depth)
                self.line("uint64_t pad;", depth + 1)
                self.line(f"XTCE_PK_TRY(xtce_pk_read_bits(r, {it.bits}, "
                          f"{cstr(where)}, &pad));", depth + 1)
                self.line("}", depth)

    def local(self, vt: VType, name: str, depth: int) -> None:
        self.line(f"{self.ctype(vt)} {name} = {self.zero(vt)};", depth)
        self.line(f"(void){name};", depth)

    @staticmethod
    def read_acc(lc) -> Acc:
        """How decoding code reaches a value."""
        return Acc(f"v{lc.id}", f"h{lc.id}" if lc.optional else None)

    def write_items(self, n: Node, items, depth: int, where: str,
                    names: dict) -> None:
        for it in items:
            if isinstance(it, Seek):
                self.seek(it, "write", self.write_ref(n, names), where, depth)
            elif isinstance(it, Mark):
                self.line(f"size_t m{it.id} = w->pos;", depth)
                self.line(f"(void)m{it.id};", depth)
            elif isinstance(it, Fixed):
                self.line(f"XTCE_PK_TRY(xtce_pk_write_bits(w, {it.bits}, "
                          f"UINT64_C(0x{it.value:x})));", depth)
            elif isinstance(it, Require):
                self.require(it, self.write_ref(n, names).place, "w->err",
                             depth)
            elif isinstance(it, Verify):
                self.patch(it.check, depth)
            elif isinstance(it, Fill):
                self.fill(it, "w", self.write_ref(n, names), depth)
            elif isinstance(it, Pad):
                self.line(f"XTCE_PK_TRY(xtce_pk_write_zeros(w, {it.bits}));",
                          depth)
            elif isinstance(it, Cond):
                self.element_guards(
                    [p for c in it.criteria for p in term_paths(c)],
                    lambda lc: self.place(n, lc, names), "w->err", depth)
                cond = " && ".join(
                    f"({self.term(c, lambda lc: self.place(n, lc, names))})"
                    for c in it.criteria)
                self.line(f"if ({cond}) {{", depth)
                self.write_items(n, it.items, depth + 1, where, names)
                self.line("}", depth)
            elif isinstance(it, Segment):
                lc = it.local
                if it.first:
                    # Encode the whole value, then write each segment's bits.
                    self.line(f"uint64_t s{lc.id};", depth)
                    self.line("{", depth)
                    d = depth + 1
                    self.line("uint8_t b[8] = {0};", d)
                    self.line("xtce_pk_writer sub = xtce_pk_writer_init(b, 8, "
                              "w->err);", d)
                    self.line("{", d)
                    self.line("xtce_pk_writer* w = &sub;", d + 1)
                    self.write_place(n, lc, names, d + 1)
                    self.line("}", d)
                    self.line(f"s{lc.id} = xtce_pk_segment_bits(b, "
                              f"{it.total});", d)
                    self.line("}", depth)
                self.line(f"XTCE_PK_TRY(xtce_pk_write_bits(w, {it.bits}, "
                          f"(s{lc.id} >> {it.shift}) & "
                          f"UINT64_C(0x{(1 << it.bits) - 1:x})));", depth)
            elif it.assembled:
                pass
            elif it.local.computed:
                lc = it.local
                self.line(f"size_t l{lc.id} = w->pos;", depth)
                self.line("XTCE_PK_TRY(xtce_pk_write_bits(w, "
                          f"{lc.vtype.enc.bits}, 0));", depth)
            else:
                self.write_place(n, it.local, names, depth)

    def write_place(self, n: Node, lc, names: dict, depth: int) -> None:
        """Writes a value: a field, a computed size or count, or a fixed
        constant."""
        f = cstr(lc.name)
        ref = self.write_ref(n, names)
        if lc in names:
            acc = names[lc]
            if acc.present:
                self.line(f"if (!{acc.present}) return xtce_pk_fail(w->err, "
                          f"XTCE_PK_MISSING, {f}, NULL);", depth)
            self.write_value(lc.vtype, acc.expr, f, depth, ref)
        elif lc in n.derived:
            self.write_value(lc.vtype, f"d{lc.id}", f, depth, ref)
        else:
            self.write_value(lc.vtype, self.literal(lc.vtype, n.fixed[lc]), f,
                             depth, ref, n.fixed[lc])

    def place(self, n: Node, lc, names: dict) -> Acc:
        """How encoding code reaches a value: a field, a computed size or
        count, or a fixed constant."""
        if lc in names:
            return names[lc]
        if lc in n.derived:
            return Acc(f"d{lc.id}")
        return Acc(self.literal(lc.vtype, n.fixed[lc]))

    def fill(self, it: Fill, rw: str, ref, depth: int) -> None:
        """Pads to the end of a container with a size; fails with BadSize if
        its entries go past it."""
        f = cstr(it.container)
        self.line("{", depth)
        d = depth + 1
        if isinstance(it.size, DynSize):
            self.size(it.size, f"{rw}->err", f, ref, d)
        else:
            self.line(f"size_t n = {it.size};", d)
        self.line(f"size_t e = (size_t){self.mark(it.start)} + n;", d)
        self.line(f"if ({rw}->pos > e)", d)
        self.line(f"return xtce_pk_fail({rw}->err, XTCE_PK_BAD_SIZE, {f}, "
                  "NULL);", d + 1)
        if rw == "r":
            self.line(f"XTCE_PK_TRY(xtce_pk_read_pad(r, e - r->pos, {f}));", d)
        else:
            self.line("XTCE_PK_TRY(xtce_pk_write_zeros(w, e - w->pos));", d)
        self.line("}", depth)

    def gap(self, e: ArrayEnc, side: str, f: str, ref, i: str,
            depth: int) -> None:
        """Skips the bits between an array's elements, before each but the
        first (index ``i``)."""
        obj = "r" if side == "read" else "w"
        if isinstance(e.gap, int):
            self.line(f"if ({i}) XTCE_PK_TRY(xtce_pk_{side}_skip({obj}, "
                      f"{e.gap}, {f}));", depth)
            return
        self.line(f"if ({i}) {{", depth)
        self.size(e.gap, f"{obj}->err", f, ref, depth + 1)
        self.line(f"XTCE_PK_TRY(xtce_pk_{side}_skip({obj}, (int64_t)n, {f}));",
                  depth + 1)
        self.line("}", depth)

    def require(self, it: Require, place, err: str, depth: int) -> None:
        """Fails with Mismatch unless an included container's criteria
        hold."""
        refs = [p for t in it.criteria for p in term_paths(t)]
        self.element_guards(refs, place, err, depth)
        cond = " && ".join(f"({self.term(t, place)})" for t in it.criteria)
        self.line(f"if (!({cond}))", depth)
        self.line(f"return xtce_pk_fail({err}, XTCE_PK_MISMATCH, "
                  f"{cstr(it.container)}, {cstr(it.text)});", depth + 1)

    @staticmethod
    def mark(i: int) -> str:
        return "0" if i == 0 else f"m{i}"

    def finish(self, n: Node, depth: int) -> None:
        """Checks the packet length field and the checksums when decoding
        ends."""
        lc = n.length_field()
        if lc is not None:
            self.line(f"XTCE_PK_TRY(xtce_pk_read_finish(r, {cstr(lc.name)}));",
                      depth)
        for c in n.checks:
            self.verify(c, depth)

    def verify(self, c, depth: int) -> None:
        """Fails with BadChecksum unless check ``c`` holds."""
        f = cstr(c.local.name)
        self.line("{", depth)
        d = depth + 1
        self.line("static const xtce_pk_checksum_spec k = "
                  f"{checksum_spec(c.spec)};", d)
        start, end = self.region(c, "r")
        self.line("uint64_t c;", d)
        self.line(f"XTCE_PK_TRY(xtce_pk_read_checksum(r, &k, {start}, {end}, "
                  f"{f}, &c));", d)
        mask = f"UINT64_C(0x{(1 << c.local.vtype.enc.bits) - 1:x})"
        self.line(f"if ((c & {mask}) != (uint64_t)v{c.local.id})", d)
        self.line(f"return xtce_pk_fail(r->err, XTCE_PK_BAD_CHECKSUM, {f}, "
                  "NULL);", d + 1)
        self.line("}", depth)

    def patch(self, c, depth: int) -> None:
        """Writes the value of check ``c`` into its field."""
        enc = c.local.vtype.enc
        self.line("{", depth)
        self.line("static const xtce_pk_checksum_spec k = "
                  f"{checksum_spec(c.spec)};", depth + 1)
        start, end = self.region(c, "w")
        self.line(f"XTCE_PK_TRY(xtce_pk_patch_checksum(w, &k, {start}, {end}, "
                  f"l{c.local.id}, {enc.bits}, {bool_lit(enc.little_endian)}, "
                  f"{cstr(c.local.name)}));", depth + 1)
        self.line("}", depth)

    def region(self, c, rw: str) -> tuple:
        """The start and end of the bits a check covers, with reader or
        writer ``rw``: on decoding ``p`` and on encoding ``l`` hold the
        position of its field."""
        pos = f"{'p' if rw == 'r' else 'l'}{c.local.id}"
        base = self.mark(c.start)
        start = (f"(size_t){base} + {c.skip}" if c.skip and not c.from_end
                 else f"(size_t){base}")
        end = (f"m{c.end}" if c.end is not None
               else f"xtce_pk_{'read' if rw == 'r' else 'write'}_packet_end("
                    f"{rw})")
        if c.from_end:
            end = f"({end} > {c.skip} ? {end} - {c.skip} : 0)"
        elif start == "(size_t)0":
            end = pos  # the field always follows the packet's start
        else:
            end = f"({pos} >= {start} ? {pos} : {end})"
        return f"({start})", end

    def construct(self, n: Node, out: str, depth: int) -> None:
        for name, lc in n.fields:
            if lc.optional:
                self.line(f"{out}->has_{name} = h{lc.id};", depth)
            self.line(f"{out}->{name} = v{lc.id};", depth)

    def reader(self, depth: int = 1) -> None:
        self.line("xtce_pk_reader r_ = xtce_pk_reader_init(data, len, err);",
                  depth)
        self.line("xtce_pk_reader* r = &r_;", depth)
        self.line("(void)r;", depth)

    def define(self, n: Node) -> None:
        t = self.p + n.ident
        self.line()
        self.line(f"static inline int {t}_decode({t}* out, const uint8_t* "
                  "data, size_t len, xtce_pk_error* err) {")
        self.reader()
        self.line("(void)out;", 1)
        for node in n.chain():
            for c in node.criteria:
                self.element_guards(term_paths(c), self.read_acc, "r->err", 1)
                self.line(f"if (!({self.term(c, self.read_acc)})) {{", 1)
                self.line("return xtce_pk_fail(err, XTCE_PK_MISMATCH, "
                          f"{cstr(node.key)}, {cstr(c.text)});", 2)
                self.line("}", 1)
            self.read_items(node.items, 1, node.key)
        self.finish(n, 1)
        self.construct(n, "out", 1)
        self.line("return 0;", 1)
        self.line("}")
        self.line()
        self.line(f"static inline int {t}_encode_into(const {t}* p, "
                  "xtce_pk_writer* w) {")
        self.line("(void)p;", 1)
        self.line("(void)w;", 1)
        names = {
            lc: Acc(f"p->{name}", f"p->has_{name}" if lc.optional else None)
            for name, lc in n.fields}
        for lc, (c, d) in n.derived.items():
            # Sizes and counts come from the lengths of what they size.
            s, f = f"s{lc.id}", cstr(lc.name)
            self.line(f"int64_t {s};", 1)
            args = (f"{names[c].expr}.len, {size_args(d)}, {cstr(c.name)}, "
                    f"w->err, &{s}")
            self.line(f"XTCE_PK_TRY(xtce_pk_size_source({args}));", 1)
            lo, hi = field_range(lc.vtype)
            checks = (([f"{s} < {slit(lo)}"] if lo > I64_MIN else [])
                      + ([f"{s} > {slit(hi)}"] if hi < I64_MAX else []))
            if checks:
                self.line(f"if ({' || '.join(checks)}) return "
                          f"xtce_pk_fail(w->err, XTCE_PK_OUT_OF_RANGE, {f}, "
                          "NULL);", 1)
            self.line(f"{self.ctype(lc.vtype)} d{lc.id} = "
                      f"({self.ctype(lc.vtype)}){s};", 1)
        for node in n.chain():
            for c in node.criteria:
                self.element_guards(term_paths(c),
                                    lambda lc: self.place(n, lc, names),
                                    "w->err", 1)
        guards = {}
        for node in n.chain():
            for c in node.criteria:
                for lc, prefix, count in term_indexes(c):
                    if lc in names:
                        guards[(names[lc].expr + access(prefix),
                                count)] = (lc.name)
        for (x, count), name in guards.items():
            # Criteria are checked before the arrays they index are written.
            self.line(f"if ({x}.len != {count}) return xtce_pk_fail(w->err, "
                      f"XTCE_PK_BAD_SIZE, {cstr(name)}, NULL);", 1)
        for node in n.chain():
            for c in node.criteria:
                if any(lc in names for lc in term_locals(c)):
                    test = self.term(c, lambda lc: self.place(n, lc, names))
                    self.line(f"if (!({test})) {{", 1)
                    self.line("return xtce_pk_fail(w->err, XTCE_PK_MISMATCH, "
                              f"{cstr(node.key)}, {cstr(c.text)});", 2)
                    self.line("}", 1)
        for node in n.chain():
            self.write_items(n, node.items, 1, node.key, names)
        length = n.length_field()
        if length is not None:
            enc = length.vtype.enc
            le = "true" if enc.little_endian else "false"
            self.line(f"XTCE_PK_TRY(xtce_pk_patch_length(w, l{length.id}, "
                      f"{enc.bits}, {le}, INT64_C({length.length}), "
                      f"{cstr(length.name)}));", 1)
        for c in n.checks:
            self.patch(c, 1)
        self.line("return 0;", 1)
        self.line("}")
        self.encode(t)
        self.line()
        self.line(f"static inline size_t {t}_describe(const {t}* p, char* buf, "
                  "size_t cap) {")
        self.line("xtce_pk_text t_ = xtce_pk_text_init(buf, cap);", 1)
        self.line("xtce_pk_text* t = &t_;", 1)
        self.line("(void)p;", 1)
        self.line(f"xtce_pk_text_puts(t, {cstr(n.ident + '{')});", 1)
        for i, (name, lc) in enumerate(n.fields):
            self.line("xtce_pk_text_puts(t, "
                      f"{cstr((', ' if i else '') + name + '=')});", 1)
            if lc.optional:
                self.line(f"if (p->has_{name}) {{", 1)
                self.describe_value(lc.vtype, f"p->{name}", 2)
                self.line("} else {", 1)
                self.line('xtce_pk_text_puts(t, "none");', 2)
                self.line("}", 1)
            else:
                self.describe_value(lc.vtype, f"p->{name}", 1)
        self.line("xtce_pk_text_putc(t, '}');", 1)
        self.line("return t->len;", 1)
        self.line("}")
        self.alarms(n, t)

    def alarms(self, n: Node, t: str) -> None:
        names = {
            lc: Acc(f"p->{name}", f"p->has_{name}" if lc.optional else None)
            for name, lc in n.fields}
        place = lambda lc: self.place(n, lc, names)  # noqa: E731
        monitored = {k: ident for k, ident, _ in self.m.monitored}
        self.line()
        self.line(f"static inline size_t {t}_algorithms(const {t}* p, "
                  "xtce_pk_output* out) {")
        self.line("(void)p;", 1)
        self.line("(void)out;", 1)
        for i, alg in enumerate(n.algorithms):
            present = [place(lc).present for lc in inputs(alg.expr)
                       if lc.optional]
            self.line(f"out[{i}].name = {cstr(alg.name)};", 1)
            self.line(f"out[{i}].present = "
                      f"{' && '.join(present) if present else 'true'};", 1)
            value = self.math_expr(alg.expr, "x", place)
            if present:
                self.line(f"out[{i}].value = out[{i}].present ? {value} : 0.0;",
                          1)
            else:
                self.line(f"out[{i}].value = {value};", 1)
        self.line(f"return {len(n.algorithms)};", 1)
        self.line("}")
        for live in (False, True):
            self.line()
            if live:
                self.line(f"static inline size_t {t}_monitor(const {t}* p, "
                          f"{self.p}AlarmMonitor* m, double t, xtce_pk_alarm* "
                          "out) {")
                self.line("(void)m;", 1)
                self.line("(void)t;", 1)
            else:
                self.line(f"static inline size_t {t}_alarms(const {t}* p, "
                          "xtce_pk_alarm* out) {")
            self.line("(void)p;", 1)
            self.line("(void)out;", 1)
            for i, (name, lc, a) in enumerate(n.alarms):
                state = monitored[a.key] if live and a.stateful else None
                self.alarm_body(i, name, lc, a, place, state)
            self.line(f"return {len(n.alarms)};", 1)
            self.line("}")

    def alarm_body(self, i: int, name: str, lc, a, place, state) -> None:
        """Stores field ``name``'s alarm in ``out[i]``; with ``state``, the
        AlarmMonitor field that keeps its parameter's earlier samples."""
        vt = lc.vtype
        elem = vt.elem if vt.kind == "array" else vt
        self.line("{", 1)
        d = 2
        self.line(f"out[{i}].field = {cstr(name)};", d)
        if lc.optional:
            self.line(f"if (!p->has_{name}) {{", d)
            self.line(f"out[{i}].level = XTCE_PK_ABSENT;", d + 1)
            self.line("} else {", d)
            d += 1
        if state is not None:
            self.line(f"xtce_pk_alarm_state* s = &m->{state};", d)
            self.line("uint32_t mv = 1, mc = 1;", d)
        if vt.kind == "array":
            self.line("int worst = 0;", d)
            self.line("size_t k;", d)
            self.line(f"for (k = 0; k < p->{name}.len; ++k) {{", d)
            d += 1
            self.line(f"{self.ctype(elem)} x = p->{name}.data[k];", d)
        else:
            self.line(f"{self.ctype(elem)} x = p->{name};", d)
        self.line("int lv = 0;", d)
        self.line("(void)x;", d)
        live = None if state is None else (state, a.history)
        for k, (terms, ad) in enumerate(a.contexts):
            cond = " && ".join(f"({self.term(c, place)})" for c in terms)
            self.line(f"{'if' if k == 0 else '} else if'} ({cond}) {{", d)
            self.alarm_def(ad, elem, place, d + 1, live)
        if a.contexts:
            if a.default is not None:
                self.line("} else {", d)
                self.alarm_def(a.default, elem, place, d + 1, live)
            self.line("}", d)
        elif a.default is not None:
            self.alarm_def(a.default, elem, place, d, live)
        if vt.kind == "array":
            self.line("if (lv > worst) worst = lv;", d)
            d -= 1
            self.line("}", d)
            self.line("{", d)
            self.line("int lv = worst;", d + 1)
            self.level(i, state, a, d + 1)
            self.line("}", d)
        else:
            self.level(i, state, a, d)
        if lc.optional:
            d -= 1
            self.line("}", d)
        self.line("}", 1)

    def level(self, i: int, state, a, depth: int) -> None:
        """Stores ``lv`` (through the monitor's state, if ``state``) in
        ``out[i]``."""
        if state is None:
            self.line(f"out[{i}].level = lv;", depth)
            return
        if a.history:
            self.line(f"xtce_pk_alarm_push(s, m->{state}_values, "
                      f"m->{state}_times, {a.history}, (double)x, t);", depth)
        self.line(f"out[{i}].level = xtce_pk_alarm_update(s, lv, mv, mc);",
                  depth)

    def alarm_def(self, ad, vt: VType, place, depth: int, live=None) -> None:
        """Statements raising ``lv`` to the level that alarm definition
        ``ad`` gives value ``x`` (of type ``vt``); with ``live`` (the
        AlarmMonitor field and its history), with its change ranges (on
        AlarmState ``s``, at time ``t``) and setting ``mv`` and ``mc`` to
        its minViolations and minConformance."""
        kind = vt.kind
        if live is not None and ad.change is not None:
            c = ad.change
            state, history = live
            self.line("{", depth)
            self.line("double d;", depth + 1)
            self.line(f"if (xtce_pk_alarm_change(s, m->{state}_values, "
                      f"m->{state}_times, {history}, (double)x, t, "
                      f"{c.samples}, {bool_lit(c.per_second)}, "
                      f"{bool_lit(c.percent)}, &d)) {{", depth + 1)
            for b in c.bands:
                self.band(b, "d", vt, depth + 2, float_value=True)
            self.line("}", depth + 1)
            self.line("}", depth)
        if (live is not None
                and (ad.min_violations, ad.min_conformance) != (1, 1)):
            self.line(f"mv = {ad.min_violations};", depth)
            self.line(f"mc = {ad.min_conformance};", depth)
        for b in ad.bands:
            self.band(b, "x", vt, depth)
        if kind == "enum" and (ad.labels or ad.default):
            self.line("{", depth)
            if ad.labels:
                self.line("static const xtce_pk_label_alarm labels[] = {",
                          depth + 1)
                for lo, hi, lv in ad.labels:
                    self.line(f"{{{slit(lo)}, {slit(hi)}, {lv}}},", depth + 2)
                self.line("};", depth + 1)
            args = (f"labels, {len(ad.labels)}" if ad.labels else "NULL, 0")
            self.line(f"int l = xtce_pk_enum_alarm(x, {args}, {ad.default});",
                      depth + 1)
            self.line("if (l > lv) lv = l;", depth + 1)
            self.line("}", depth)
        if kind == "string" and (ad.patterns or ad.default):
            self.line("{", depth)
            if ad.patterns:
                self.line("static const xtce_pk_pattern patterns[] = {",
                          depth + 1)
                for dfa, lv in ad.patterns:
                    self.line(f"{{&{self.dfa(dfa)}, {lv}}},", depth + 2)
                self.line("};", depth + 1)
            args = (f"patterns, {len(ad.patterns)}" if ad.patterns
                    else "NULL, 0")
            self.line(f"int l = xtce_pk_pattern_alarm(x.data, x.len, {args}, "
                      f"{ad.default});", depth + 1)
            self.line("if (l > lv) lv = l;", depth + 1)
            self.line("}", depth)
        for level, terms in ad.conditions:
            cond = " && ".join(f"({self.term(c, place)})" for c in terms)
            self.line(f"if ({cond}) {{", depth)
            self.line(f"if (lv < {level}) lv = {level};", depth + 1)
            self.line("}", depth)

    def band(self, b, x: str, vt: VType, depth: int,
             float_value: bool = False) -> None:
        """Raises ``lv`` to the Band's level if value ``x`` (of type ``vt``,
        or a double when ``float_value``) violates it."""
        if vt.kind == "int" and not float_value:
            lit = slit if vt.signed else ulit
            parts = (([f"{x} >= {lit(b.lo)}"] if b.lo is not None else [])
                     + ([f"{x} <= {lit(b.hi)}"] if b.hi is not None else []))
            test = "(" + (" && ".join(parts) or "true") + ")"
        else:
            def bound(v):
                return "false, 0.0" if v is None else f"true, {cflt(v)}"
            test = (f"xtce_pk_in_band({x}, {bound(b.lo)}, "
                    f"{bool_lit(b.lo_incl)}, {bound(b.hi)}, "
                    f"{bool_lit(b.hi_incl)})")
        self.line(f"if ({test if b.inside else '!' + test}) {{", depth)
        self.line(f"if (lv < {b.level}) lv = {b.level};", depth + 1)
        self.line("}", depth)

    def dfa(self, dfa) -> str:
        """The name of the constant holding a pattern's automaton."""
        return self.dfa_names[id(dfa)]

    def stream(self, st) -> None:
        t = self.p + st.ident
        bits, size = stream_capacity(st)
        sync = "frame" if st.fixed else "flag"
        pcm = f"XTCE_PK_{st.pcm}"
        self.line()
        for k, text in enumerate(textwrap.wrap(stream_doc(st)
                                               + " Initialize it with "
                                                 f"{t}_init, and don't copy it "
                                                 "after.", 72)):
            self.line(("/* " if k == 0 else " * ") + text)
        self.line(" */")
        self.line(f"#define {t}_CONTAINER {cstr(st.container)}")
        self.line(f"typedef struct {t} {{")
        self.line(f"xtce_pk_{sync}_sync sync;", 1)
        self.line(f"uint8_t bits[{bits}];", 1)
        self.line(f"uint8_t frame[{size}];", 1)
        self.line(f"}} {t};")
        self.line()
        if st.fixed:
            self.line(f"static const xtce_pk_frame_spec {t}_spec = "
                      f"{{{st.frame_bits}, UINT64_C(0x{st.pattern:x}), "
                      f"UINT64_C(0x{st.mask:x}), {st.pattern_bits}, "
                      f"{st.location}, {st.max_errors}, {st.aperture}, "
                      f"{st.verify}, {st.check}, UINT64_C({st.invert_after}), "
                      f"{pcm}, {bool_lit(st.inverted)}}};")
        else:
            self.line(f"static const xtce_pk_flag_spec {t}_spec = "
                      f"{{{st.flag_bits}, {bool_lit(st.flag_ones)}, "
                      f"{st.max_bits}, {pcm}, {bool_lit(st.inverted)}}};")
        self.line()
        self.line(f"static inline void {t}_init({t}* s) {{")
        self.line(f"xtce_pk_{sync}_sync_init(&s->sync, &{t}_spec, s->bits, "
                  f"{bits}, s->frame);", 1)
        self.line("}")
        self.line()
        self.line("/* Takes the next `len` bytes of the stream, and gives `f` "
                  "each frame they")
        self.line(" * complete. */")
        self.line(f"static inline void {t}_push({t}* s, const uint8_t* data, "
                  "size_t len, xtce_pk_frame_fn f, void* ctx) {")
        self.line(f"xtce_pk_{sync}_sync_push(&s->sync, data, len, f, ctx);", 1)
        self.line("}")

    def declare_monitor(self) -> None:
        t = self.p + "AlarmMonitor"
        self.line()
        self.line("/* The alarm state of each parameter whose alarms depend on "
                  "earlier samples,")
        self.line(" * which the packets' _monitor functions read and update; "
                  f"{t}_init")
        self.line(" * clears it. */")
        self.line(f"typedef struct {t} {{")
        for key, ident, history in self.m.monitored:
            self.line(f"xtce_pk_alarm_state {ident}; /* {key} */", 1)
            if history:
                self.line(f"double {ident}_values[{history}];", 1)
                self.line(f"double {ident}_times[{history}];", 1)
        if not self.m.monitored:
            self.line("char unused_; /* C structs need a member */", 1)
        self.line(f"}} {t};")
        self.line()
        self.line(f"static inline void {t}_init({t}* m) {{")
        self.line("memset(m, 0, sizeof *m);", 1)
        self.line("}")

    def declare_dfas(self) -> None:
        """The automata of the string alarms' patterns."""
        self.dfa_names = {}
        for n in self.m.nodes:
            for _, _, a in n.alarms:
                defs = ([d for _, d in a.contexts]
                        + ([a.default] if a.default is not None else []))
                for d in defs:
                    for dfa, _ in d.patterns:
                        if id(dfa) in self.dfa_names:
                            continue
                        name = f"{self.p}dfa{len(self.dfa_names)}"
                        self.dfa_names[id(dfa)] = name
                        self.line()
                        self.line("/* The automaton matching "
                                  f"{dfa.pattern.replace('*/', '* /')}. */")
                        self.line(f"static const uint16_t {name}_starts[] = {{"
                                  + ", ".join(map(str, dfa.starts)) + "};")
                        trans = dfa.trans or [(0, 0, 0)]
                        self.line("static const xtce_pk_dfa_trans "
                                  f"{name}_trans[] = {{"
                                  + ", ".join(f"{{{a}, {b}, {c}}}"
                                              for a, b, c in trans) + "};")
                        self.line(f"static const bool {name}_accept[] = {{"
                                  + ", ".join(bool_lit(x) for x in dfa.accept)
                                  + "};")
                        self.line(f"static const xtce_pk_dfa {name} = "
                                  f"{{{name}_starts, {name}_trans, "
                                  f"{name}_accept}};")

    def encode(self, t: str) -> None:
        self.line()
        self.line(f"static inline int {t}_encode(const {t}* p, uint8_t* buf, "
                  "size_t cap, size_t* len, xtce_pk_error* err) {")
        self.line("xtce_pk_writer w = xtce_pk_writer_init(buf, cap, err);", 1)
        self.line(f"XTCE_PK_TRY({t}_encode_into(p, &w));", 1)
        self.line("*len = w.len;", 1)
        self.line("return 0;", 1)
        self.line("}")

    def describe_value(self, vt: VType, x: str, depth: int) -> None:
        k = vt.kind
        if k == "aggregate":
            self.line("xtce_pk_text_putc(t, '{');", depth)
            for j, m in enumerate(vt.enc.members):
                self.line("xtce_pk_text_puts(t, "
                          f"{cstr((', ' if j else '') + m.ident + '=')});",
                          depth)
                self.describe_value(m.vtype, f"{x}.{m.ident}", depth)
            self.line("xtce_pk_text_putc(t, '}');", depth)
        elif k == "array":
            i = f"i{depth}"
            self.line("xtce_pk_text_putc(t, '[');", depth)
            # `len` is checked against the storage, as callers may set it.
            cap = f"sizeof {x}.data / sizeof {x}.data[0]"
            self.line(f"for (size_t {i} = 0; {i} < {x}.len && {i} < {cap}; "
                      f"++{i}) {{", depth)
            self.line(f'if ({i}) xtce_pk_text_puts(t, ", ");', depth + 1)
            self.describe_value(vt.elem, f"{x}.data[{i}]", depth + 1)
            self.line("}", depth)
            self.line("xtce_pk_text_putc(t, ']');", depth)
        elif k == "binary" and vt.enc.variable:
            n = f"({x}.len < sizeof {x}.data ? {x}.len : sizeof {x}.data)"
            self.line(f"xtce_pk_text_bytes(t, {x}.data, {n});", depth)
        elif k == "float":
            self.line(f"xtce_pk_text_double(t, {x});", depth)
        elif k == "string":
            self.line(f"xtce_pk_text_str(t, {x}.data, {x}.len);", depth)
        elif k == "binary":
            self.line(f"xtce_pk_text_bytes(t, {x}.data, {vt.enc.bits // 8});",
                      depth)
        elif k == "enum":
            self.line(f"{self.p}{vt.ident}_describe(t, {x});", depth)
        elif k == "bool":
            self.line(f'xtce_pk_text_puts(t, {x} ? "true" : "false");', depth)
        elif vt.signed:
            self.line(f"xtce_pk_text_int(t, {x});", depth)
        else:
            self.line(f"xtce_pk_text_uint(t, {x});", depth)

    def define_kind(self, k: Kind) -> None:
        t = self.p + k.ident
        self.line()
        self.line(f"static inline int {t}_decode({t}* out, const uint8_t* "
                  "data, size_t len, xtce_pk_error* err) {")
        self.reader()
        self.read_items(k.root.items, 1, k.root.key)
        self.dispatch(k, k.root, 1)
        self.line("}")
        self.line()
        self.line(f"static inline int {t}_encode_into(const {t}* p, "
                  "xtce_pk_writer* w) {")
        self.line("switch (p->tag) {", 1)
        for mem in k.members:
            self.line(f"case {t}_{mem.ident}_tag:", 2)
            self.line("return "
                      f"{self.p}{mem.ident}_encode_into(&p->u.{mem.ident}, w);",
                      3)
        self.line("}", 1)
        self.line(f"return xtce_pk_fail(w->err, XTCE_PK_NO_MATCH, {t}_NAME, "
                  "NULL);", 1)
        self.line("}")
        self.encode(t)
        self.line()
        self.line(f"static inline size_t {t}_describe(const {t}* p, char* buf, "
                  "size_t cap) {")
        self.line("switch (p->tag) {", 1)
        for mem in k.members:
            self.line(f"case {t}_{mem.ident}_tag:", 2)
            self.line(f"return {self.p}{mem.ident}_describe(&p->u.{mem.ident}, "
                      "buf, cap);", 3)
        self.line("}", 1)
        self.line("if (cap) buf[0] = 0;", 1)
        self.line("return 0;", 1)
        self.line("}")
        self.line()
        self.line(f"static inline size_t {t}_alarms(const {t}* p, "
                  "xtce_pk_alarm* out) {")
        self.line("switch (p->tag) {", 1)
        for mem in k.members:
            self.line(f"case {t}_{mem.ident}_tag:", 2)
            self.line(f"return {self.p}{mem.ident}_alarms(&p->u.{mem.ident}, "
                      "out);", 3)
        self.line("}", 1)
        self.line("(void)out;", 1)
        self.line("return 0;", 1)
        self.line("}")
        self.line()
        self.line(f"static inline size_t {t}_algorithms(const {t}* p, "
                  "xtce_pk_output* out) {")
        self.line("switch (p->tag) {", 1)
        for mem in k.members:
            self.line(f"case {t}_{mem.ident}_tag:", 2)
            self.line("return "
                      f"{self.p}{mem.ident}_algorithms(&p->u.{mem.ident}, "
                      "out);", 3)
        self.line("}", 1)
        self.line("(void)out;", 1)
        self.line("return 0;", 1)
        self.line("}")
        self.line()
        self.line(f"static inline size_t {t}_monitor(const {t}* p, "
                  f"{self.p}AlarmMonitor* m, double t, xtce_pk_alarm* out) {{")
        self.line("switch (p->tag) {", 1)
        for mem in k.members:
            self.line(f"case {t}_{mem.ident}_tag:", 2)
            self.line(f"return {self.p}{mem.ident}_monitor(&p->u.{mem.ident}, "
                      "m, t, out);", 3)
        self.line("}", 1)
        self.line("(void)m;", 1)
        self.line("(void)t;", 1)
        self.line("(void)out;", 1)
        self.line("return 0;", 1)
        self.line("}")

    def dispatch(self, k: Kind, n: Node, depth: int) -> None:
        for c in n.children:
            if c.error:
                continue
            self.element_guards([p for x in c.criteria for p in term_paths(x)],
                                self.read_acc, "r->err", depth)
            conds = [self.term(x, self.read_acc) for x in c.criteria]
            cond = (" && ".join(f"({x})" for x in conds) if len(conds) > 1
                    else (conds or ["1"])[0])
            self.line(f"if ({cond}) {{", depth)
            self.read_items(c.items, depth + 1, c.key)
            self.dispatch(k, c, depth + 1)
            self.line("}", depth)
        if n.abstract:
            self.line("return xtce_pk_fail(err, XTCE_PK_NO_MATCH, "
                      f"{cstr(n.key)}, NULL);", depth)
        else:
            t = self.p + k.ident
            self.finish(n, depth)
            self.line(f"out->tag = {t}_{n.ident}_tag;", depth)
            self.line("{", depth)
            self.line(f"{self.p}{n.ident}* o = &out->u.{n.ident};", depth + 1)
            # A packet without fields leaves o unused.
            self.line("(void)o;", depth + 1)
            self.construct(n, "o", depth + 1)
            self.line("}", depth)
            self.line("return 0;", depth)


def emit(mission: Mission, source: str, prefix: Optional[str] = None) -> str:
    return CEmitter(mission, source, prefix).emit()
