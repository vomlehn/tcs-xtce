"""Emit a header of packet codecs that compiles both as C11 and as C++.

The header uses only ``xtce/packet.h``, which is itself plain C.  C has no
namespaces, so every name the header defines starts with a prefix (by default
the root SpaceSystem name in snake_case followed by ``_``).  Fallible
functions return 0 or an ``XTCE_PK_*`` error code instead of throwing, and
nothing is allocated: callers supply the buffers.
"""

from __future__ import annotations

from typing import Optional

from .common import (field_range, flt, header_lines, int_enc, invertible, le,
                     size_args, str_cap, string_spec)
from .compile import snake
from .model import (term_indexes, term_locals, walk, Index, AggregateEnc, AllOf,
                    AnyOf, ArrayEnc, BinaryEnc, Cond, DynSize, Criterion, Fixed,
                    FloatEnc, IntEnc, Kind, Mark, Mission, Node, Pad,
                    Polynomial, Seek, Spline, StringEnc, Value, VType)

# One level of indentation in the generated C.
INDENT = " " * 8

ENC = {
    "Unsigned": "XTCE_PK_UNSIGNED",
    "TwosComplement": "XTCE_PK_TWOS_COMPLEMENT",
    "SignMagnitude": "XTCE_PK_SIGN_MAGNITUDE",
    "OnesComplement": "XTCE_PK_ONES_COMPLEMENT",
}

I64_MIN, I64_MAX, U64_MAX = -(2**63), 2**63 - 1, 2**64 - 1

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
        self.consts: dict[int, str] = {}  # vtype -> constant name
        self.counts: dict[int, int] = {}  # vtype -> calibrator table length

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
        seen = []
        for vt in self.vtypes():
            if vt.calibrator is None and not isinstance(vt.enc, StringEnc):
                continue
            name = f"{self.p}k{len(self.consts)}"
            self.consts[id(vt)] = name
            seen.append((name, vt))
        if not seen:
            return
        self.line()
        for name, vt in seen:
            cal = vt.calibrator
            if isinstance(cal, Polynomial):
                terms = ", ".join(f"{{{flt(c)}, {e}}}" for c, e in cal.terms)
                self.counts[id(vt)] = len(cal.terms)
                self.line(f"static const xtce_pk_term {name}[{len(cal.terms)}] "
                          f"= {{{terms}}}; /* {vt.key} */")
            elif isinstance(cal, Spline):
                pts = ", ".join(f"{{{flt(a)}, {flt(b)}}}"
                                for a, b in cal.points)
                self.counts[id(vt)] = len(cal.points)
                self.line("static const xtce_pk_point "
                          f"{name}[{len(cal.points)}] = {{{pts}}}; /* {vt.key} "
                          "*/")
            else:
                fixed, mx, term, tag = string_spec(vt)
                spec = ", ".join(
                    [
                        bool_lit(fixed is not None),
                        str(fixed or 0),
                        str(mx),
                        bool_lit(term is not None),
                        str(term or 0),
                        str(tag),
                    ]
                )
                self.line(f"static const xtce_pk_string_spec {name} = "
                          f"{{{spec}}}; /* {vt.key} */")

    def table(self, vt: VType) -> str:
        return f"{self.consts[id(vt)]}, {self.counts[id(vt)]}"

    # -- reading and writing one value --

    def calibrate(self, vt: VType, x: str, dst: str, f: str,
                  depth: int) -> None:
        cal = vt.calibrator
        if cal is None:
            self.line(f"{dst} = {x};", depth)
        elif isinstance(cal, Polynomial):
            self.line(f"{dst} = xtce_pk_polynomial({x}, {self.table(vt)});",
                      depth)
        else:
            ext = bool_lit(cal.extrapolate)
            self.line(f"XTCE_PK_TRY(xtce_pk_spline({x}, {self.table(vt)}, "
                      f"{cal.order}, {ext}, {f}, r->err, &{dst}));", depth)

    def uncalibrate(self, vt: VType, y: str, f: str, depth: int) -> None:
        cal = vt.calibrator
        if cal is None:
            self.line(f"double y = {y};", depth)
        elif isinstance(cal, Polynomial):
            self.line(f"double y = xtce_pk_polynomial_inverse({y}, "
                      f"{flt(cal.intercept)}, {flt(cal.slope)});", depth)
        else:
            ext = bool_lit(cal.extrapolate)
            self.line("double y;", depth)
            self.line(f"XTCE_PK_TRY(xtce_pk_spline_inverse({y}, "
                      f"{self.table(vt)}, {ext}, {f}, w->err, &y));", depth)

    def size(self, d, err: str, f: str, ref, depth: int) -> None:
        """Declares ``size_t n``: a count, or the size or count that a value
        gives (``ref`` gives the expression for that value)."""
        if not isinstance(d, DynSize):
            self.line(f"size_t n = {d};", depth)
            return
        self.line("size_t n;", depth)
        x = f"(double)({ref(d)})"
        args = (f"{x}, {flt(d.slope)}, {flt(d.intercept)}, {d.unit}, {d.max}, "
                f"{f}, {err}, &n")
        self.line(f"XTCE_PK_TRY(xtce_pk_dynamic_size({args}));", depth)
        if d.factor != 1:
            self.line(f"n *= {d.factor};", depth)

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
            def inner(d):
                return f"{dst}.{d.member}" if d.member else ref(d)
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
                self.line(f"if ({i}) XTCE_PK_TRY(xtce_pk_read_skip(r, {e.gap}, "
                          f"{f}));", d + 1)
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
            self.line("uint64_t raw;", d)
            self.line(f"XTCE_PK_TRY(xtce_pk_read_uint(r, {e.bits}, "
                      f"{bool_lit(le(vt))}, {f}, &raw));", d)
            if isinstance(e, FloatEnc):
                self.calibrate(vt, f"xtce_pk_float_from_raw(raw, {e.bits})",
                               dst, f, d)
            else:
                enc = ENC[int_enc(vt)]
                signed = f"xtce_pk_to_signed(raw, {e.bits}, {enc})"
                if vt.kind == "int" and vt.calibrator is not None:
                    lo, lim = vt.eng_limits
                    self.line("double y, v;", d)
                    self.calibrate(vt,
                                   f"xtce_pk_int_to_double(raw, {e.bits}, "
                                   f"{enc})", "y", f, d)
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
                                   f"{enc})", dst, f, d)
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

    def write_value(self, vt: VType, x: str, f: str, depth: int, ref,
                    fixed=None) -> None:
        """Writes `x`: a field access, or the literal of the value `fixed`;
        ``ref`` gives the expression for a value that gives a size or count."""
        is_lit = fixed is not None
        e = vt.enc
        bad = f"return xtce_pk_fail(w->err, XTCE_PK_BAD_SIZE, {f}, NULL);"
        if isinstance(e, AggregateEnc):
            def inner(d):
                return f"{x}.{d.member}" if d.member else ref(d)
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
                self.line(f"if ({i}) XTCE_PK_TRY(xtce_pk_write_skip(w, "
                          f"{e.gap}, {f}));", d + 1)
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
            self.uncalibrate(vt, x, f, depth + 1)
            raw = f"xtce_pk_float_to_raw(y, {e.bits})"
            self.line(f"XTCE_PK_TRY(xtce_pk_write_uint(w, {e.bits}, "
                      f"{bool_lit(le(vt))}, {raw}));", depth + 1)
            self.line("}", depth)
        elif isinstance(e, IntEnc):
            bits, enc = e.bits, ENC[int_enc(vt)]
            self.line("{", depth)
            d = depth + 1
            self.line("uint64_t raw;", d)
            if vt.kind == "int" and vt.calibrator is not None:
                self.uncalibrate(vt, f"(double)({x})", f, d)
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
                self.uncalibrate(vt, x, f, d)
                self.line(f"XTCE_PK_TRY(xtce_pk_double_to_int(y, {bits}, "
                          f"{enc}, {f}, w->err, &raw));", d)
            self.line(f"XTCE_PK_TRY(xtce_pk_write_uint(w, {bits}, "
                      f"{bool_lit(le(vt))}, raw));", d)
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
                return ("xtce_pk_cmp_"
                        f"{'i' if k == 'enum' or vt.signed else 'u'}({x}, {y}) "
                        f"{op} 0")
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
        for n in m.nodes:
            self.declare(n)
        for k in m.kinds:
            self.declare_kind(k)
        for n in m.nodes:
            self.define(n)
        for k in m.kinds:
            self.define_kind(k)
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

    def read_ref(self, d: DynSize) -> str:
        """The value a size or location uses, when decoding."""
        return self.size_source(d, lambda lc: Acc(f"v{lc.id}"))

    def write_ref(self, n: Node, names: dict):
        """The value a size or location uses, when encoding."""
        return lambda d: self.size_source(d,
                                          lambda lc: self.place(n, lc, names))

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
            elif isinstance(it, Pad):
                self.line(f"XTCE_PK_TRY(xtce_pk_read_pad(r, {it.bits}, "
                          f"{cstr(where)}));", depth)
            elif isinstance(it, Value):
                lc = it.local
                if not nested:
                    self.local(lc.vtype, f"v{lc.id}", depth)
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
                            self.local(v.local.vtype, f"v{v.local.id}", depth)
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
            elif isinstance(it, Pad):
                self.line(f"XTCE_PK_TRY(xtce_pk_write_zeros(w, {it.bits}));",
                          depth)
            elif isinstance(it, Cond):
                cond = " && ".join(
                    f"({self.term(c, lambda lc: self.place(n, lc, names))})"
                    for c in it.criteria)
                self.line(f"if ({cond}) {{", depth)
                self.write_items(n, it.items, depth + 1, where, names)
                self.line("}", depth)
            elif it.local.length is not None:
                lc = it.local
                self.line(f"size_t l{lc.id} = w->pos;", depth)
                self.line("XTCE_PK_TRY(xtce_pk_write_bits(w, "
                          f"{lc.vtype.enc.bits}, 0));", depth)
            else:
                lc = it.local
                f = cstr(lc.name)
                ref = self.write_ref(n, names)
                if lc in names:
                    acc = names[lc]
                    if acc.present:
                        self.line(f"if (!{acc.present}) return "
                                  f"xtce_pk_fail(w->err, XTCE_PK_MISSING, {f}, "
                                  "NULL);", depth)
                    self.write_value(lc.vtype, acc.expr, f, depth, ref)
                elif lc in n.derived:
                    self.write_value(lc.vtype, f"d{lc.id}", f, depth, ref)
                else:
                    self.write_value(lc.vtype,
                                     self.literal(lc.vtype, n.fixed[lc]), f,
                                     depth, ref, n.fixed[lc])

    def place(self, n: Node, lc, names: dict) -> Acc:
        """How encoding code reaches a value: a field, a computed size or
        count, or a fixed constant."""
        if lc in names:
            return names[lc]
        if lc in n.derived:
            return Acc(f"d{lc.id}")
        return Acc(self.literal(lc.vtype, n.fixed[lc]))

    @staticmethod
    def mark(i: int) -> str:
        return "0" if i == 0 else f"m{i}"

    def finish(self, n: Node, depth: int) -> None:
        """Checks the packet length field when decoding ends."""
        lc = n.length_field()
        if lc is not None:
            self.line(f"XTCE_PK_TRY(xtce_pk_read_finish(r, {cstr(lc.name)}));",
                      depth)

    def construct(self, n: Node, out: str, depth: int) -> None:
        for name, lc in n.fields:
            if lc.optional:
                self.line(f"{out}->has_{name} = h{lc.id};", depth)
            self.line(f"{out}->{name} = v{lc.id};", depth)

    def reader(self, depth: int = 1) -> None:
        self.line("xtce_pk_reader r_ = xtce_pk_reader_init(data, len, err);",
                  depth)
        self.line("xtce_pk_reader* r = &r_;", depth)

    def define(self, n: Node) -> None:
        t = self.p + n.ident
        self.line()
        self.line(f"static inline int {t}_decode({t}* out, const uint8_t* "
                  "data, size_t len, xtce_pk_error* err) {")
        self.reader()
        self.line("(void)out;", 1)
        for node in n.chain():
            for c in node.criteria:
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
        names = {lc: Acc(f"p->{name}",
                         f"p->has_{name}" if lc.optional else None)
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

    def dispatch(self, k: Kind, n: Node, depth: int) -> None:
        for c in n.children:
            if c.error:
                continue
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
            self.construct(n, "o", depth + 1)
            self.line("}", depth)
            self.line("return 0;", depth)


def emit(mission: Mission, source: str, prefix: Optional[str] = None) -> str:
    return CEmitter(mission, source, prefix).emit()
