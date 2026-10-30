"""Emit a header of packet codecs that compiles both as C11 and as C++.

The header uses only ``xtce/packet.h``, which is itself plain C.  C has no
namespaces, so every name the header defines starts with a prefix (by default
the root SpaceSystem name in snake_case followed by ``_``).  Fallible
functions return 0 or an ``XTCE_PK_*`` error code instead of throwing, and
nothing is allocated: callers supply the buffers.
"""

from __future__ import annotations

import dataclasses
import math
import textwrap
from typing import Optional

from .common import (doc_header, HOOK_DOCS, written, checks_hooks, items_hooks,
                     terms_hooks, notes, stream_capacity, stream_doc, Ref, c_op,
                     fast_order, field_range, flt, header_lines, inputs,
                     int_enc, stream_fn, invertible, leaf_calibrators,
                     math_value, size_args, str_cap, string_spec, binary_cap)
from .compile import snake
from .model import (
    term_indexes, term_locals, term_paths, walk, Index, AggregateEnc, AllOf,
    AnyOf, ArrayEnc, BinaryEnc, Cond, ContextCal, DynSize, Criterion, Fixed,
    FloatEnc, IntEnc, Kind, Mark, MathOp, Mission, Node, Pad, Polynomial, Seek,
    Span, Spline, StringEnc, Value, VType, Require, Fill, Segment, Verify,
    Custom, NARROW_MAX_BYTES, TailPad, TailSeek, LengthEnd, Transform, XformEnc)

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


def checksum_spec(spec: tuple, prefix: str = "") -> str:
    """Writes the initializer of an xtce_pk_checksum_spec.

    Args:
        spec:   The check's spec: ``("crc", width, poly, init, final,
                ref_in, ref_out)``, ``("custom", hook)``, ``("parity",
                odd)``, or another kind with its bits.
        prefix: The prefix of the generated names, which a custom
                checksum's hook variable starts with.

    Returns:
        The braced initializer: kind, bits, polynomial, init, final XOR,
        reflect data, reflect remainder, odd and custom function.
    """
    kind = spec[0]
    if kind == "crc":
        width, poly, init, final, ref_in, ref_out = spec[1:]
        return (f"{{XTCE_PK_CRC, {width}, UINT64_C(0x{poly:x}), "
                f"UINT64_C(0x{init:x}), UINT64_C(0x{final:x}), "
                f"{bool_lit(ref_in)}, {bool_lit(ref_out)}, false, NULL}}")
    if kind == "custom":
        return ("{XTCE_PK_CUSTOM, 0, 0, 0, 0, false, false, false, "
                f"{prefix}hook_{spec[1].ident}}}")
    bits = spec[1] if len(spec) > 1 and kind != "parity" else 0
    odd = kind == "parity" and spec[1]
    return (f"{{XTCE_PK_{kind.upper()}, {bits}, 0, 0, 0, false, false, "
            f"{bool_lit(odd)}, NULL}}")


def spec_decl(spec: tuple) -> str:
    """The declaration of xtce_pk_checksum_spec ``k``: a constant, but for a
    custom checksum, whose function is the application's."""
    return ("const" if spec[0] == "custom" else "static const")

# What `<empty optional> op <value>` gives, as with std::optional in C++.
EMPTY = {"==": False, "!=": True, "<": True, "<=": True, ">": False,
         ">=": False}


def cstr(s) -> str:
    """Quotes text or bytes as a C string literal.

    Args:
        s: The text, written as its UTF-8 bytes, or the bytes.

    Returns:
        The literal, with quotes and backslashes escaped and other bytes
        outside printable ASCII as hex escapes, each ending its piece of
        the literal so that no following character extends it.
    """
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


# The C type of each kind of hook.
HOOK_TYPES = {
    "criterion": "xtce_pk_criterion_fn", "algorithm": "xtce_pk_algorithm_fn",
    "alarm": "xtce_pk_alarm_fn", "checksum": "xtce_pk_checksum_fn",
    "binary_from": "xtce_pk_binary_from_fn",
    "binary_to": "xtce_pk_binary_to_fn", "time_from": "xtce_pk_time_from_fn",
    "time_to": "xtce_pk_time_to_fn"}


def cflt(x: float) -> str:
    """A double literal, infinities included."""
    if math.isinf(x):
        return "HUGE_VAL" if x > 0 else "-HUGE_VAL"
    return flt(x)


def bool_lit(b: bool) -> str:
    return "true" if b else "false"


def access(x: str, path) -> str:
    """Builds the expression for a member or element inside a value.

    Args:
        x:    The expression for the value.
        path: The steps to take: member names, and Index steps into
              arrays (with a stride, an index counted from each group of
              ``per`` elements).

    Returns:
        The expression for what ``path`` reaches.
    """
    for p in path:
        if not isinstance(p, Index):
            x += "." + p
        elif p.stride:
            x += f".data[{p.i} + {p.stride} * ({x}.len / {p.per})]"
        else:
            x += f".data[{p.i}]"
    return x



def lsb_bytes(vt: VType) -> bool:
    """Whether ``vt`` is a string or binary value whose bytes are least
    significant bit first."""
    return isinstance(vt.enc, (StringEnc, BinaryEnc)) and vt.enc.lsb

class Acc:
    """How generated code reaches a value: an expression, plus the expression
    telling whether it is present when it is optional."""

    def __init__(self, expr: str, present: Optional[str] = None):
        self.expr = expr
        self.present = present


class CEmitter:
    """Writes the C header of a mission's packet codecs.

    Args:
        mission: The compiled mission.
        source:  The name of the XTCE input, for the notice at the top.
        prefix:  What every generated name starts with, or None for the
                 root SpaceSystem's name in snake_case and ``_``.
    """

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

    def doc(self, summary: str, params: list[tuple[str, str]] = (),
            returns: Optional[str] = None) -> None:
        """Writes the Doxygen header of a generated function, which starts
        a line.

        Args:
            summary: What the function does.
            params:  Each parameter, with what it means.
            returns: What it returns, the error codes included, or None if
                     it returns nothing.
        """
        for text in doc_header("c", summary.replace("*/", "* /"),
                               [(n, d.replace("*/", "* /")) for n, d in params],
                               None if returns is None
                               else returns.replace("*/", "* /")):
            self.line(text)

    # -- types and constants --

    def ctype(self, vt: VType) -> str:
        """Names the C type that holds a value.

        Args:
            vt: The value's type.

        Returns:
            The C type: a fixed-width integer, double or bool, or a
            generated list, string, bytes, wide integer, aggregate or
            enumeration type.
        """
        k = vt.kind
        if k == "array":
            return f"{self.p}{self.tname(vt.elem)}_list{vt.enc.max_count}"
        if k == "aggregate":
            return self.p + vt.ident
        if k == "binary" and vt.enc.variable:
            return f"{self.p}varbytes{binary_cap(vt)}"
        if k == "int" and vt.wide:
            return f"{self.p}{'' if vt.signed else 'u'}int{vt.width}"
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
                or vt.wide else "0")

    def literal(self, vt: VType, v) -> str:
        """Writes a constant of a type as a C expression.

        Args:
            vt: The constant's type.
            v:  Its value: an int, float, bool, str or bytes.

        Returns:
            The C literal; a wide integer's is an XTCE_BN_LIT of its
            bytes, least significant first.
        """
        k = vt.kind
        if k == "int" and vt.wide:
            b = v.to_bytes(vt.width // 8, "little", signed=vt.signed)
            return (f"XTCE_BN_LIT({self.ctype(vt)}, {{{{"
                    + ", ".join(f"0x{x:02x}" for x in b) + "}})")
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
        """Lists the types of the values the mission's packets hold.

        Returns:
            Every type of a value and the types inside it, inner ones
            first, each once.
        """
        seen: dict[int, VType] = {}
        for n in self.m.nodes:
            for it in walk(n.all_items()):
                if isinstance(it, Value):
                    for vt in it.local.vtype.tree():
                        seen.setdefault(id(vt), vt)
        return list(seen.values())

    def declare_buffers(self) -> None:
        """Writes the typedefs of the wide integers, strings and binary
        buffers that the packets' fields use, one for each size."""
        strs = sorted({str_cap(vt) for vt in self.vtypes()
                       if vt.kind == "string"})
        bins = sorted({vt.enc.bits // 8 for vt in self.vtypes()
                       if vt.kind == "binary" and not vt.enc.variable})
        vbins = sorted({binary_cap(vt) for vt in self.vtypes()
                        if vt.kind == "binary" and vt.enc.variable})
        wides = sorted({(vt.width, vt.signed) for vt in self.vtypes()
                        if vt.wide})
        if wides:
            self.line()
            self.line("/** Integers of N bits, xtce/bnum.h's: whole bytes, "
                      "least significant first. */")
            for bits, signed in wides:
                self.line(f"XTCE_BN_TYPEDEF({self.p}{'' if signed else 'u'}int"
                          f"{bits}, {bits // 8});")
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
        """Writes the constant tables of the calibrators' polynomials and
        splines and the specs of the string types, and records their names
        (``consts``), lengths (``counts``) and spline orders (``orders``).
        """
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
        """Builds the C expression for a MathOperation tree.

        Args:
            e:     The tree: ``("const", v)``, ``("raw",)``, ``("value",
                   ref)`` or an operator with its operands.
            x:     The expression for the value being calibrated.
            place: Gives the Acc of a Local that the tree uses.

        Returns:
            The expression, a double.
        """
        if e[0] == "const":
            return f"({flt(e[1])})"
        if e[0] == "raw":
            return x
        if e[0] == "value":
            r = e[1]
            return math_value(r.vtype, access(place(r.local).expr, r.member),
                              "c")
        args = [self.math_expr(a, x, place) for a in e[1:]]
        if e[0] in ("+", "-", "*"):
            return f"({args[0]} {e[0]} {args[1]})"
        b = args[1] if len(args) > 1 else "0.0"
        return f"xtce_pk_math({c_op(e[0])}, {args[0]}, {b})"

    def cases(self, cal: ContextCal, x: str, depth: int, place, err: str,
              body) -> None:
        """Writes an if-else chain over a context calibrator's cases.

        Args:
            cal:   The context calibrator.
            x:     The expression for the value, which the chain copies
                   into a double first.
            depth: The indentation level.
            place: Gives the Acc of a Local that a case's criteria use.
            err:   The C expression for the xtce_pk_error to fail with.
            body:  Writes, as ``body(c, v, depth)``, the statements for
                   calibrator ``c`` of the first case that holds (or the
                   default) on the copy ``v``.
        """
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
        """Writes the statements that calibrate a raw value.

        Args:
            cal:   The calibrator, or None to copy the value.
            x:     The expression for the raw value.
            dst:   What receives the engineering value, a double.
            f:     The C string literal naming the field, for errors.
            depth: The indentation level.
            place: Gives the Acc of a Local that the calibrator uses.
            err:   The C expression for the xtce_pk_error to fail with,
                   for a spline that doesn't cover the value.
        """
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
        """Writes the statements that find the raw value of an engineering
        value, when encoding; a calibrator that can't be inverted fails
        with XTCE_PK_NOT_INVERTIBLE.

        Args:
            cal:   The calibrator, or None to copy the value.
            y:     The expression for the engineering value.
            dst:   What receives the raw value, a double.
            f:     The C string literal naming the field, for errors.
            depth: The indentation level.
            place: Gives the Acc of a Local that the calibrator uses.
        """
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
        gives.

        Args:
            d:     A fixed count, or the DynSize that gives it.
            err:   The C expression for the xtce_pk_error to fail with.
            f:     The C string literal naming the field, for errors.
            ref:   Gives the expression for the value of a DynSize, and
                   (``ref.place``) the Acc of a Local.
            depth: The indentation level.
        """
        if not isinstance(d, DynSize):
            self.line(f"size_t n = {d};", depth)
            return
        if d.member is None:
            refs = ([(d.local, d.path)] if d.lookup is None
                    else [p for criteria, _ in d.lookup for c in criteria
                          for p in term_paths(c)])
            self.element_guards(refs, ref.place, err, depth)
        self.line("size_t n;", depth)
        x = (math_value(d.wide_type, ref(d), "c") if d.wide_type is not None
             else f"(double)({ref(d)})")
        args = (f"{x}, {flt(d.slope)}, {flt(d.intercept)}, {d.unit}, {d.max}, "
                f"{f}, {err}, &n")
        self.line(f"XTCE_PK_TRY(xtce_pk_dynamic_size({args}));", depth)
        if d.factor != 1:
            self.line(f"n *= {d.factor};", depth)

    def element_guards(self, refs, place, err: str, depth: int) -> None:
        """Writes checks that the array elements a set of references reads
        are there, for arrays whose count varies.

        Args:
            refs:  The references: (Local, path) pairs.
            place: Gives the Acc of a Local.
            err:   The C expression for the xtce_pk_error to fail with.
            depth: The indentation level.
        """
        done = set()
        for lc, path in refs:
            x = place(lc).expr
            for p in path:
                if not isinstance(p, Index):
                    x += "." + p
                    continue
                if p.stride and (x, p) not in done:
                    done.add((x, p))
                    self.line(f"XTCE_PK_TRY(xtce_pk_element_at({x}.len, "
                              f"{p.per}, {p.need}, {cstr(lc.name)}, {err}));",
                              depth)
                elif p.count is None and (x, p.i) not in done:
                    done.add((x, p.i))
                    self.line(f"XTCE_PK_TRY(xtce_pk_element({x}.len, {p.i}, "
                              f"{cstr(lc.name)}, {err}));", depth)
                x = access(x, [p])

    def string_spec(self, vt: VType, err: str, f: str, ref, depth: int) -> str:
        """Gives the spec of a string type, declaring a sized copy ``s``
        when another value gives its size.

        Args:
            vt:    The string type.
            err:   The C expression for the xtce_pk_error to fail with.
            f:     The C string literal naming the field, for errors.
            ref:   Gives the expression for the value of a DynSize.
            depth: The indentation level.

        Returns:
            The expression for the spec's address.
        """
        k = self.consts[id(vt)]
        if vt.enc.dyn is None:
            return f"&{k}"
        self.size(vt.enc.dyn, err, f, ref, depth)
        self.line(f"xtce_pk_string_spec s = xtce_pk_string_spec_sized(&{k}, "
                  "n);", depth)
        return "&s"

    def read_value(self, vt: VType, dst: str, f: str, depth: int, ref) -> None:
        """Writes the statements reading one value, with reader ``r``.

        Args:
            vt:    The value's type.
            dst:   What receives the value.
            f:     The C string literal naming the field, for errors.
            depth: The indentation level.
            ref:   Gives the expression for a value that gives a size or
                   count.
        """
        if lsb_bytes(vt):
            self.line("xtce_pk_read_set_lsb(r, true);", depth)
            self.read_bytes(vt, dst, f, depth, ref)
            self.line("xtce_pk_read_set_lsb(r, false);", depth)
        else:
            self.read_bytes(vt, dst, f, depth, ref)

    def read_bytes(self, vt: VType, dst: str, f: str, depth: int, ref) -> None:
        """Writes the statements reading one value, like ``read_value``,
        in the reader's usual bit order.

        Args:
            vt:    The value's type.
            dst:   What receives the value.
            f:     The C string literal naming the field, for errors.
            depth: The indentation level.
            ref:   Gives the expression for a value that gives a size or
                   count.
        """
        e = vt.enc
        if isinstance(e, AggregateEnc) and e.transform is not None:
            # Read the members from what the transform makes of its bytes.
            x, d = e.transform, depth + 1
            self.line("{", depth)
            self.line(f"size_t k{d};", d)
            self.line(f"uint8_t t{d}[{x.cap}];", d)
            self.line("XTCE_PK_TRY(xtce_pk_read_binary_from(r, "
                      f"{self.transform_hook(x.decode)}, true, {x.raw}, t{d}, "
                      f"{x.cap}, {f}, &k{d}));", d)
            self.line(f"xtce_pk_reader s{d} = xtce_pk_reader_init(t{d}, k{d}, "
                      "r->err);", d)
            self.line(f"xtce_pk_reader* rr{d} = r;", d)
            self.line(f"r = &s{d};", d)
            self.read_value(
                dataclasses.replace(vt,
                                    enc=dataclasses.replace(e, transform=None)),
                dst, f, d, ref)
            self.line(f"r = rr{d};", d)
            self.line("}", depth)
        elif isinstance(e, AggregateEnc):
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
            for c in e.checks:
                # A repeated container's own criteria, on each copy.
                self.require(c, lambda lc: Acc(dst), "r->err", depth)
        elif isinstance(e, ArrayEnc):
            # Indexes are numbered by depth: an aggregate element may hold
            # another array.
            i = f"i{depth}"
            # A fixed count needs no block to hold it.
            fixed = isinstance(e.count, int)
            d = depth if fixed else depth + 1
            if not fixed:
                self.line("{", depth)
                self.size(e.count, "r->err", f, ref, d)
            n = e.count if fixed else "n"
            self.line(f"{dst}.len = {n};", d)
            self.line(f"for (size_t {i} = 0; {i} < {n}; ++{i}) {{", d)
            if e.gap:
                self.gap(e, "read", f, ref, i, d + 1)
            self.read_value(e.elem, f"{dst}.data[{i}]", f, d + 1, ref)
            self.line("}", d)
            if not fixed:
                self.line("}", depth)
        elif isinstance(e, XformEnc):
            h = self.transform_hook(e.decode)
            has, raw = bool_lit(e.raw is not None), e.raw or 0
            if e.time:
                self.line(f"XTCE_PK_TRY(xtce_pk_read_time_from(r, {h}, {has}, "
                          f"{raw}, {f}, &{dst}));", depth)
            else:
                self.line(f"XTCE_PK_TRY(xtce_pk_read_binary_from(r, {h}, "
                          f"{has}, {raw}, {dst}.data, {e.cap}, {f}, "
                          f"&{dst}.len));", depth)
        elif isinstance(e, BinaryEnc) and e.variable:
            self.line("{", depth)
            if e.rest:
                self.line("size_t n;", depth + 1)
                self.line(f"XTCE_PK_TRY(xtce_pk_read_rest(r, {e.bits // 8}, "
                          f"{e.tail}, {f}, &n));", depth + 1)
            elif e.embedded:
                self.line("size_t n;", depth + 1)
                self.line("XTCE_PK_TRY(xtce_pk_read_embedded(r, "
                          f"{e.bits // 8}, {f}, &n));", depth + 1)
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
        elif isinstance(e, IntEnc) and vt.wide:
            self.read_wide(vt, dst, f, depth)
        elif (isinstance(e, IntEnc) and e.bits > 64
              and vt.kind in ("enum", "bool")):
            self.read_wide_choice(vt, dst, f, depth)
        elif isinstance(e, IntEnc) and e.bits > 64:
            self.read_wide_f64(vt, dst, f, depth, ref)
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

    def read_wide(self, vt: VType, dst: str, f: str, depth: int) -> None:
        """Writes the statements reading an integer of more than 64 bits.

        Args:
            vt:    The integer's type.
            dst:   What receives the value, an xtce/bnum.h integer.
            f:     The C string literal naming the field, for errors.
            depth: The indentation level.
        """
        e, n, d = vt.enc, vt.width // 8, depth + 1
        self.line("{", depth)
        self.line(f"uint8_t raw[{n}];", d)
        args = self.order_args(e, d)
        self.line(f"XTCE_PK_TRY(xtce_pk_read_wide(r, {e.bits}, {args}, {f}, "
                  f"raw, {n}));", d)
        if e.bcd:
            self.line(f"uint8_t s[{n}];", d)
            self.line(f"XTCE_PK_TRY(xtce_pk_wide_bcd_decode(raw, {n}, "
                      f"{e.bits}, {bool_lit(e.encoding == 'packedBCD')}, {f}, "
                      "r->err, s));", d)
        if vt.signed:
            self.line(f"xtce_pk_wide_to_signed(raw, {n}, {e.bits}, "
                      f"{ENC[int_enc(vt)]});", d)
        self.line(f"memcpy({dst}.bytes, raw, {n});", d)
        self.line("}", depth)

    def read_wide_choice(self, vt: VType, dst: str, f: str, depth: int) -> None:
        """Writes the statements reading an enumeration or a boolean whose
        integer encoding has more than 64 bits.  They fail with
        XTCE_PK_OUT_OF_RANGE when an enumeration's int64_t can't hold the
        value.

        Args:
            vt:    The type, whose encoding is an IntEnc.
            dst:   The place to read into.
            f:     The field's name, as a C string literal, for errors.
            depth: The indentation of the statements.
        """
        e, d = vt.enc, depth + 1
        # A byte more than the field, so that an unsigned value stays
        # positive.
        n = e.bits // 8 + 1
        self.line("{", depth)
        self.line(f"uint8_t raw[{n}], s[{n}];", d)
        self.line("(void)s;", d)
        args = self.order_args(e, d)
        self.line(f"XTCE_PK_TRY(xtce_pk_read_wide(r, {e.bits}, {args}, {f}, "
                  f"raw, {n}));", d)
        if e.bcd:
            self.line(f"XTCE_PK_TRY(xtce_pk_wide_bcd_decode(raw, {n}, "
                      f"{e.bits}, {bool_lit(e.encoding == 'packedBCD')}, {f}, "
                      "r->err, s));", d)
        if vt.kind == "bool":
            self.line(f"{dst} = !xtce_bn_is_zero(raw, {n});", d)
        else:
            self.line(f"xtce_pk_wide_to_signed(raw, {n}, {e.bits}, "
                      f"{ENC[int_enc(vt)]});", d)
            self.line(f"if (!xtce_bn_to_i64(raw, {n}, true, &{dst}))", d)
            self.line(f"return xtce_pk_fail(r->err, XTCE_PK_OUT_OF_RANGE, {f}, "
                      "NULL);", d + 1)
        self.line("}", depth)

    def write_wide_choice(self, vt: VType, x: str, f: str, depth: int) -> None:
        """Writes the statements writing an enumeration or a boolean whose
        integer encoding has more than 64 bits.  They fail with
        XTCE_PK_OUT_OF_RANGE when the encoding can't hold an
        enumeration's value.

        Args:
            vt:    The type, whose encoding is an IntEnc.
            x:     The value to write.
            f:     The field's name, as a C string literal, for errors.
            depth: The indentation of the statements.
        """
        e, d = vt.enc, depth + 1
        n = e.bits // 8 + 1
        self.line("{", depth)
        self.line(f"uint8_t raw[{n}], s[{n}];", d)
        self.line("(void)s;", d)
        if vt.kind == "bool":
            self.line(f"xtce_bn_from_u64(raw, {n}, ({x}) ? 1u : 0u);", d)
        else:
            self.line(f"xtce_bn_from_i64(raw, {n}, {x});", d)
            enc = "XTCE_PK_UNSIGNED" if e.bcd else ENC[int_enc(vt)]
            self.line(f"XTCE_PK_TRY(xtce_pk_wide_from_signed(raw, {n}, "
                      f"{e.bits}, {enc}, {f}, w->err));", d)
            if e.bcd:
                self.line(f"XTCE_PK_TRY(xtce_pk_wide_bcd_encode(raw, {n}, "
                          f"{e.bits}, {bool_lit(e.encoding == 'packedBCD')}, "
                          f"{f}, w->err, s));", d)
        args = self.order_args(e, d)
        self.line(f"XTCE_PK_TRY(xtce_pk_write_wide(w, {e.bits}, {args}, raw));",
                  d)
        self.line("}", depth)

    def read_wide_f64(self, vt: VType, dst: str, f: str, depth: int,
                      ref) -> None:
        """Writes the statements reading a float or calibrated integer whose
        integer encoding has more than 64 bits, through a double.

        Args:
            vt:    The type, whose encoding is an IntEnc.
            dst:   The place to read into.
            f:     The field's name, as a C string literal, for errors.
            depth: The indentation of the statements.
            ref:   The expressions for values its calibrator uses.
        """
        e, d = vt.enc, depth + 1
        n = -(-e.bits // 8)
        enc = ENC[int_enc(vt)]
        self.line("{", depth)
        self.line(f"uint8_t raw[{n}], s[{n}];", d)
        args = self.order_args(e, d)
        self.line(f"XTCE_PK_TRY(xtce_pk_read_wide(r, {e.bits}, {args}, {f}, "
                  f"raw, {n}));", d)
        if e.bcd:
            self.line(f"XTCE_PK_TRY(xtce_pk_wide_bcd_decode(raw, {n}, "
                      f"{e.bits}, {bool_lit(e.encoding == 'packedBCD')}, {f}, "
                      "r->err, s));", d)
        x = f"xtce_pk_wide_to_double(raw, {n}, {e.bits}, {enc}, s)"
        if vt.kind == "int":
            lo, lim = vt.eng_limits
            self.line("double y, v;", d)
            self.calibrate(vt, x, "y", f, d, ref)
            self.line(f"XTCE_PK_TRY(xtce_pk_round_to_int(y, {flt(lo)}, "
                      f"{flt(lim)}, {f}, r->err, &v));", d)
            self.line(f"{dst} = ({self.ctype(vt)})v;", d)
        else:
            self.calibrate(vt, x, dst, f, d, ref)
        self.line("}", depth)

    def write_wide_f64(self, vt: VType, x: str, f: str, depth: int,
                       ref) -> None:
        """Writes the statements writing a float or calibrated integer whose
        integer encoding has more than 64 bits, through a double.  They
        fail with XTCE_PK_OUT_OF_RANGE when the encoding can't hold the
        value.

        Args:
            vt:    The type, whose encoding is an IntEnc.
            x:     The value to write.
            f:     The field's name, as a C string literal, for errors.
            depth: The indentation of the statements.
            ref:   The expressions for values its inverse calibrator uses.
        """
        e, d = vt.enc, depth + 1
        n = -(-e.bits // 8)
        self.line("{", depth)
        self.line(f"uint8_t raw[{n}], s[{n}];", d)
        self.line("(void)s;", d)
        self.uncalibrate(vt, f"(double)({x})" if vt.kind == "int" else x, f, d,
                         ref)
        self.line(f"XTCE_PK_TRY(xtce_pk_double_to_wide(y, {e.bits}, "
                  f"{ENC[int_enc(vt)]}, {f}, w->err, raw, {n}));", d)
        if e.bcd:
            self.line(f"XTCE_PK_TRY(xtce_pk_wide_bcd_encode(raw, {n}, "
                      f"{e.bits}, {bool_lit(e.encoding == 'packedBCD')}, {f}, "
                      "w->err, s));", d)
        args = self.order_args(e, d)
        self.line(f"XTCE_PK_TRY(xtce_pk_write_wide(w, {e.bits}, {args}, raw));",
                  d)
        self.line("}", depth)

    def write_wide(self, vt: VType, x: str, f: str, depth: int) -> None:
        """Writes the statements writing an integer of more than 64 bits,
        which fail when the encoding can't hold it.

        Args:
            vt:    The integer's type.
            x:     The expression for the value, an xtce/bnum.h integer.
            f:     The C string literal naming the field, for errors.
            depth: The indentation level.
        """
        e, n, d = vt.enc, vt.width // 8, depth + 1
        self.line("{", depth)
        self.line(f"uint8_t raw[{n}];", d)
        self.line(f"memcpy(raw, ({x}).bytes, {n});", d)
        if e.bcd:
            self.line(f"uint8_t s[{n}];", d)
            self.line(f"XTCE_PK_TRY(xtce_pk_wide_bcd_encode(raw, {n}, "
                      f"{e.bits}, {bool_lit(e.encoding == 'packedBCD')}, {f}, "
                      "w->err, s));", d)
        elif vt.signed:
            self.line(f"XTCE_PK_TRY(xtce_pk_wide_from_signed(raw, {n}, "
                      f"{e.bits}, {ENC[int_enc(vt)]}, {f}, w->err));", d)
        else:
            self.line(f"XTCE_PK_TRY(xtce_pk_wide_from_unsigned(raw, {n}, "
                      f"{e.bits}, {f}, w->err));", d)
        args = self.order_args(e, d)
        self.line(f"XTCE_PK_TRY(xtce_pk_write_wide(w, {e.bits}, {args}, raw));",
                  d)
        self.line("}", depth)

    def int_bytes(self, vt: VType, x: str) -> str:
        """Gives the bytes of an integer, to compare it with xtce/bnum.h.

        Args:
            vt: The integer's type (or an enumeration's).
            x:  The expression for the value.

        Returns:
            The arguments: the bytes, least significant first, and how
            many there are.
        """
        if vt.wide:
            return f"({x}).bytes, {vt.width // 8}"
        if vt.kind == "enum" or vt.signed:
            return f"xtce_pk_le64_i((int64_t)({x})).bytes, 8"
        return f"xtce_pk_le64_u((uint64_t)({x})).bytes, 8"

    def order_args(self, e, depth: int) -> str:
        """Gives the bit order, byte order and order list arguments of the
        runtime's general reader and writer, declaring the list ``order``
        when the encoding has one.

        Args:
            e:     The integer or float encoding.
            depth: The indentation level of the declaration.

        Returns:
            The three arguments, comma-separated.
        """
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
        of an integer or float encoding into them.

        Args:
            e:     The encoding.
            f:     The C string literal naming the field, for errors.
            depth: The indentation level.
        """
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
        """Writes the statement writing ``raw`` as the raw value of an
        integer or float encoding.

        Args:
            e:     The encoding.
            depth: The indentation level.
            hi:    The expression for the bits above the low 64 of a
                   float's raw value.
        """
        if fast_order(e):
            self.line(f"XTCE_PK_TRY(xtce_pk_write_uint(w, {e.bits}, "
                      f"{bool_lit(e.little_endian)}, raw));", depth)
            return
        args = self.order_args(e, depth)
        self.line(f"XTCE_PK_TRY(xtce_pk_write_ordered(w, {e.bits}, {args}, "
                  f"{hi}, raw));", depth)

    def write_value(self, vt: VType, x: str, f: str, depth: int, ref,
                    fixed=None) -> None:
        """Writes the statements writing one value, with writer ``w``.

        Args:
            vt:    The value's type.
            x:     The expression for the value: a field access, or the
                   literal of ``fixed``.
            f:     The C string literal naming the field, for errors.
            depth: The indentation level.
            ref:   Gives the expression for a value that gives a size or
                   count.
            fixed: The value when ``x`` is its literal, else None.
        """
        if lsb_bytes(vt):
            self.line("xtce_pk_write_set_lsb(w, true);", depth)
            self.write_bytes(vt, x, f, depth, ref, fixed)
            self.line("xtce_pk_write_set_lsb(w, false);", depth)
        else:
            self.write_bytes(vt, x, f, depth, ref, fixed)

    def write_bytes(self, vt: VType, x: str, f: str, depth: int, ref,
                    fixed=None) -> None:
        """Writes the statements writing one value, like ``write_value``,
        in the writer's usual bit order.

        Args:
            vt:    The value's type.
            x:     The expression for the value: a field access, or the
                   literal of ``fixed``.
            f:     The C string literal naming the field, for errors.
            depth: The indentation level.
            ref:   Gives the expression for a value that gives a size or
                   count.
            fixed: The value when ``x`` is its literal, else None.
        """
        is_lit = fixed is not None
        e = vt.enc
        bad = f"return xtce_pk_fail(w->err, XTCE_PK_BAD_SIZE, {f}, NULL);"
        if isinstance(e, AggregateEnc) and e.transform is not None:
            # Write the members, then what the transform makes of them.
            t, d = e.transform, depth + 1
            self.line("{", depth)
            self.line(f"size_t t{d} = w->pos;", d)
            self.write_value(
                dataclasses.replace(vt,
                                    enc=dataclasses.replace(e, transform=None)),
                x, f, d, ref)
            self.line(f"XTCE_PK_TRY(xtce_pk_write_transform(w, t{d}, "
                      f"{self.transform_hook(t.encode)}, true, {t.raw}, "
                      f"{t.raw}, {f}));", d)
            self.line("}", depth)
            return
        if isinstance(e, AggregateEnc):
            inner = Ref(lambda d: f"{x}.{d.member}" if d.member else ref(d),
                        ref.place)
            for c in e.checks:
                self.require(c, lambda lc: Acc(x), "w->err", depth)
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
            # A fixed count needs no block to hold it.
            fixed = isinstance(e.count, int)
            d = depth if fixed else depth + 1
            if not fixed:
                self.line("{", depth)
                self.size(e.count, "w->err", f, ref, d)
            n = e.count if fixed else "n"
            self.line(f"if ({x}.len != {n}) {bad}", d)
            self.line(f"for (size_t {i} = 0; {i} < {n}; ++{i}) {{", d)
            if e.gap:
                self.gap(e, "write", f, ref, i, d + 1)
            self.write_value(e.elem, f"{x}.data[{i}]", f, d + 1, ref)
            self.line("}", d)
            if not fixed:
                self.line("}", depth)
            return
        if isinstance(e, XformEnc):
            h = self.transform_hook(e.encode)
            has, raw = bool_lit(e.raw is not None), e.raw or 0
            if e.time:
                self.line(f"XTCE_PK_TRY(xtce_pk_write_time_to(w, {h}, {x}, "
                          f"{has}, {raw}, {e.bits // 8}, {f}));", depth)
                return
            data, n = ((f"(const uint8_t*){x}", str(len(fixed))) if is_lit
                       else (f"{x}.data", f"{x}.len"))
            self.line(f"if ({n} > {e.cap}) return xtce_pk_fail(w->err, "
                      f"XTCE_PK_TOO_LONG, {f}, NULL);", depth)
            self.line(f"XTCE_PK_TRY(xtce_pk_write_binary_to(w, {h}, {data}, "
                      f"{n}, {has}, {raw}, {e.bits // 8}, {f}));", depth)
            return
        if isinstance(e, BinaryEnc) and e.embedded:
            data, n = ((f"(const uint8_t*){x}", str(len(fixed))) if is_lit
                       else (f"{x}.data", f"{x}.len"))
            self.line(f"XTCE_PK_TRY(xtce_pk_write_embedded(w, {data}, {n}, "
                      f"{e.bits // 8}, {f}));", depth)
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
        if vt.valid and vt.kind in ("int", "float"):
            self.line(f"if (!({self.valid_test(vt, x)}))", depth)
            self.line(f"return xtce_pk_fail(w->err, XTCE_PK_OUT_OF_RANGE, {f}, "
                      "NULL);", depth + 1)
        if (isinstance(e, (IntEnc, FloatEnc)) and vt.kind in ("float", "int")
                and not invertible(vt)):
            self.line("return xtce_pk_fail(w->err, XTCE_PK_NOT_INVERTIBLE, "
                      f"{f}, NULL);", depth)
            return
        if isinstance(e, IntEnc) and vt.wide:
            self.write_wide(vt, x, f, depth)
        elif (isinstance(e, IntEnc) and e.bits > 64
              and vt.kind in ("enum", "bool")):
            self.write_wide_choice(vt, x, f, depth)
        elif isinstance(e, IntEnc) and e.bits > 64:
            self.write_wide_f64(vt, x, f, depth, ref)
        elif isinstance(e, FloatEnc):
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
        """Builds the C expression for a criterion or a tree of them.

        Args:
            t:     The criterion, an AllOf or AnyOf of them, or a Custom
                   criterion (which is false while its hook isn't set).
            place: Gives the Acc of a Local.

        Returns:
            The condition.
        """
        if isinstance(t, (AllOf, AnyOf)):
            return (" && " if isinstance(t, AllOf)
                    else " || ").join(f"({self.term(x, place)})"
                                      for x in t.terms)
        if isinstance(t, Custom):
            f = f"{self.p}hook_{t.hook.ident}"
            return f"({f} && {f}({self.hook_inputs(t.inputs, place)}))"
        return self.condition(t, place(t.local),
                              place(t.other) if t.other is not None else None)

    def condition(self, c: Criterion, acc: Acc,
                  other: Optional[Acc] = None) -> str:
        """Builds the C expression for one comparison criterion.

        Args:
            c:     The criterion: a value compared with a constant or with
                   another value.
            acc:   How to reach the value compared.
            other: How to reach the value it is compared with, or None
                   when that is a constant.

        Returns:
            The condition.  An absent optional value compares as
            std::optional's empty value does (see ``EMPTY``); a constant
            outside the field's range gives a constant result.
        """
        vt, x, op, v = c.vtype, access(acc.expr, c.path), c.op, c.value
        k = vt.kind
        if other is not None:
            # Both values are present: the compiler rejects optional ones.
            y = access(other.expr, c.other_path)
            ovt = c.other_target or c.other.vtype
            if k == "int" and (vt.wide or ovt.wide):
                return (f"xtce_bn_cmp_mixed({self.int_bytes(vt, x)}, "
                        f"{bool_lit(vt.signed)}, {self.int_bytes(ovt, y)}, "
                        f"{bool_lit(ovt.signed)}) {op} 0")
            if k in ("int", "enum"):
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
            lo, hi = (field_range(vt) if vt.wide else (I64_MIN, I64_MAX)
                      if signed else (0, U64_MAX))
            if v < lo or v > hi:
                # Every value of the field compares the same way.
                below = v < lo
                holds = {"==": False, "!=": True, "<": not below,
                         "<=": not below, ">": below, ">=": below}[op]
                cond = f"((void)({x}), {int(holds)})"
            elif vt.wide:
                cond = (f"xtce_bn_cmp(({x}).bytes, "
                        f"({self.literal(vt, v)}).bytes, {vt.width // 8}, "
                        f"{bool_lit(signed)}) {op} 0")
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
        """Writes the whole header.

        Returns:
            The header's text, one statement to a line; the caller wraps
            it to 80 columns.
        """
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
        self.declare_hooks()
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
        """Writes an enumeration's typedef, its labels' macros and its
        _label and _describe functions.

        Args:
            vt: The enumeration type.
        """
        t = self.p + vt.ident
        self.line()
        self.line(f"/* Enumeration {vt.key}.  Holds the raw value, so values "
                  "the enumeration")
        self.line(" * does not list survive decoding. */")
        self.line(f"typedef int64_t {t};")
        for lab in vt.labels:
            self.line(f"#define {t}_{lab.ident} {slit(lab.value)}")
        self.line()
        self.doc(
            f"Names a value of enumeration {vt.key}.", [("x", "The value.")],
            "The value's label, or NULL if the enumeration doesn't list it.")
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
        self.doc(f"Appends a value of enumeration {vt.key} to a packet's "
                 "canonical text form: its label, or its number if the "
                 "enumeration doesn't list it.",
                 [("t", "The text being written."), ("x", "The value.")])
        self.line(f"static inline void {t}_describe(xtce_pk_text* t, {t} x) {{")
        self.line(f"const char* l = {t}_label(x);", 1)
        self.line("if (l) {", 1)
        self.line("xtce_pk_text_puts(t, l);", 2)
        self.line("} else {", 1)
        self.line("xtce_pk_text_int(t, x);", 2)
        self.line("}", 1)
        self.line("}")

    def declare(self, n: Node) -> None:
        """Writes a packet's struct, its NAME, MAX_BYTES, ALARMS and
        ALGORITHMS macros and the prototypes of its functions.

        Args:
            n: The concrete container or meta-command.
        """
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
        self.max_bytes(t, n.key, n.max_bytes)
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
        self.packet_prototypes(t, n.key)

    def packet_prototypes(self, t: str, key: str, kind: bool = False) -> None:
        """Declares the functions of a packet type, with their headers.

        Args:
            t:    The C type of the packet.
            key:  The qualified XTCE name of its container or meta-command,
                  or for a kind the root container's.
            kind: Whether ``t`` is a kind: any concrete packet derived from
                  ``key``.
        """
        what = f"packet derived from {key}" if kind else f"{key} packet"
        fail = ("0 on success, else the XTCE_PK_ code of the error, which is "
                "also stored in `*err`.")
        err = ("Receives the error's code, the field or container it concerns "
               "and its detail on failure; may be NULL.")
        self.line()
        self.doc(f"Zeroes every field of a {what}.", [("p", "The packet.")])
        self.line(f"static inline void {t}_init({t}* p) {{ memset(p, 0, sizeof "
                  "*p); }")
        self.doc(f"Decodes a {what} that starts at the first byte of `data`."
                 + (" Reads the root's entries, then picks the first child (in "
                    "document order) whose criteria match, recursively." if kind
                    else ""),
                 [("out", "Receives the packet."),
                  ("data", "The packet's bytes, and any after it."),
                  ("len", "The number of bytes at `data`."), ("err", err)],
                 fail)
        self.line(f"static inline int {t}_decode({t}* out, const uint8_t* "
                  "data, size_t len, xtce_pk_error* err);")
        self.doc(f"Decodes a {what} that starts at the first byte of `data`, "
                 "like _decode, and says how many bytes it takes.",
                 [("out", "Receives the packet."),
                  ("data", "The packet's bytes, and any after it."),
                  ("len", "The number of bytes at `data`."),
                  ("used",
                   "Receives the number of bytes the packet takes, on "
                   "success."), ("err", err)], fail)
        self.line(f"static inline int {t}_decode_prefix({t}* out, const "
                  "uint8_t* data, size_t len, size_t* used, xtce_pk_error* "
                  "err);")
        self.doc(f"Encodes a {what} with a writer, after what it has written.",
                 [("p", "The packet."),
                  ("w",
                   "The writer, whose `err` receives the error on failure.")],
                 "0 on success, else the XTCE_PK_ code of the error.")
        self.line(f"static inline int {t}_encode_into(const {t}* p, "
                  "xtce_pk_writer* w);")
        self.doc(f"Encodes a {what} into a buffer.",
                 [("p", "The packet."), ("buf", "Where the packet's bytes go."),
                  ("cap",
                   "The number of bytes at `buf`; fewer than the packet needs "
                   "fail with XTCE_PK_NO_SPACE."),
                  ("len", "Receives the packet's size in bytes, on success."),
                  ("err", err)], fail)
        self.line(f"static inline int {t}_encode(const {t}* p, uint8_t* buf, "
                  "size_t cap, size_t* len, xtce_pk_error* err);")
        self.doc(f"Writes the canonical text form of a {what}, identical in "
                 "every generated language, like snprintf.",
                 [("p", "The packet."),
                  ("buf",
                   "Where the text goes, truncated to fit and NUL-terminated "
                   "when `cap` isn't 0."),
                  ("cap", "The number of bytes at `buf`.")],
                 "The length of the whole text, without its NUL.")
        self.line(f"static inline size_t {t}_describe(const {t}* p, char* buf, "
                  "size_t cap);")
        alarms_out = ("Receives the level of each field with an alarm, "
                      f"{t}_ALARMS of them at most.")
        self.doc(f"Evaluates the alarms of a {what}'s fields.  Alarms that "
                 "depend on earlier samples count only this one (see "
                 "_monitor).", [("p", "The packet."), ("out", alarms_out)],
                 "The number of alarms stored in `out`.")
        self.line(f"static inline size_t {t}_alarms(const {t}* p, "
                  "xtce_pk_alarm* out);")
        self.doc(f"Evaluates the alarms of a {what}'s fields like _alarms, "
                 "counting the earlier samples for change ranges, "
                 "minViolations and minConformance.",
                 [("p", "The packet."),
                  ("m",
                   "The state of the alarms that depend on earlier samples, "
                   "which it reads and updates."),
                  ("t", "This sample's time, in seconds."),
                  ("out", alarms_out)], "The number of alarms stored in `out`.")
        self.line(f"static inline size_t {t}_monitor(const {t}* p, "
                  f"{self.p}AlarmMonitor* m, double t, xtce_pk_alarm* out);")
        self.doc(f"Evaluates the MathAlgorithms that a {what} triggers.",
                 [("p", "The packet."),
                  ("out",
                   f"Receives the output of each, {t}_ALGORITHMS of them at "
                   "most; one that can't be computed is marked not present.")],
                 "The number of outputs stored in `out`.")
        self.line(f"static inline size_t {t}_algorithms(const {t}* p, "
                  "xtce_pk_output* out);")

    def max_bytes(self, t: str, key: str, n: int) -> None:
        """Defines a packet type's MAX_BYTES, refusing to build where its
        size in bits doesn't fit in a size_t.

        Args:
            t:   The packet's C type.
            key: The qualified XTCE name of its container, for the error.
            n:   The most bytes the packet takes.
        """
        self.line(f"#define {t}_MAX_BYTES {n}")
        if n > NARROW_MAX_BYTES:
            self.line(f"#if SIZE_MAX / 8 < {t}_MAX_BYTES")
            self.line(f"#error {cstr(key + ' is too large for this size_t')}")
            self.line("#endif")

    def declare_kind(self, k: Kind) -> None:
        """Writes the tagged union of a kind's members, its macros and the
        prototypes of its functions.

        Args:
            k: The kind: every concrete packet derived from a root.
        """
        t = self.p + k.ident
        self.line()
        self.line(f"/* Every concrete packet derived from {k.root.key}. */")
        self.line(f"#define {t}_NAME {cstr(k.root.key)}")
        self.max_bytes(t, k.root.key, k.max_bytes)
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
        self.packet_prototypes(t, k.root.key, kind=True)

    def size_source(self, d: DynSize, place) -> str:
        """Builds the expression for the value a bound DynSize adjusts.

        Args:
            d:     The DynSize.
            place: Gives the Acc of a Local.

        Returns:
            The field's value, or the first matching value of a
            DiscreteLookupList (its default if none matches).
        """
        if d.lookup is None:
            return access(place(d.local).expr, d.path)
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
        """Writes the statements that move the reader or writer for a
        Seek.

        Args:
            it:    The Seek.
            side:  "read" for the reader ``r``, "write" for the writer
                   ``w``.
            ref:   Gives the value of an offset that a DynSize gives.
            where: The qualified name of the container, for errors.
            depth: The indentation level.
        """
        obj = "r" if side == "read" else "w"
        f = cstr(where)
        off = str(it.offset - it.back) if isinstance(it.offset, int) else ""
        if it.varies:
            self.line("{", depth)
            depth += 1
        if it.span is not None:
            self.span(it.span, f"{obj}->err", f, ref, depth)
        if isinstance(it.offset, DynSize):
            self.size(it.offset, f"{obj}->err", f, ref, depth)
            off = f"(int64_t)n - {it.back}" if it.back else "(int64_t)n"
        if it.span is not None:
            off += " - (int64_t)span_bits"
        seek = "seek_ahead" if it.ahead else "seek"
        if it.from_start is None:
            self.line(f"XTCE_PK_TRY(xtce_pk_{side}_skip({obj}, {off}, {f}));",
                      depth)
        else:
            self.line(f"XTCE_PK_TRY(xtce_pk_{side}_{seek}({obj}, "
                      f"{self.mark(it.from_start)}, {off}, {f}));", depth)
        if it.varies:
            self.line("}", depth - 1)

    def span(self, sp: Span, err: str, f: str, ref, depth: int) -> None:
        """Declares ``size_t span_bits``: the bits of a Span.

        Args:
            sp:    The Span.
            err:   The C expression for the xtce_pk_error to fail with.
            f:     The C string literal naming the field, for errors.
            ref:   Gives the expression for the value of a count or gap,
                   and (``ref.place``) the Acc of a Local.
            depth: The indentation level.
        """
        parts = []
        for name, d in (("span_count", sp.count), ("span_gap", sp.gap)):
            if isinstance(d, int):
                parts.append(str(d))
                continue
            self.line(f"size_t {name};", depth)
            self.line("{", depth)
            self.size(d, err, f, ref, depth + 1)
            self.line(f"{name} = n;", depth + 1)
            self.line("}", depth)
            parts.append(name)
        self.line(f"size_t span_bits = xtce_pk_span_bits({parts[0]}, "
                  f"{sp.each}, {parts[1]});", depth)

    def tail_seek(self, it: TailSeek, side: str, ref, where: str,
                  depth: int) -> None:
        """Writes the statements that move the reader, or pad the writer,
        for a TailSeek.

        Args:
            it:    The TailSeek.
            side:  "read" for the reader ``r``, "write" for the writer
                   ``w``.
            ref:   Gives the value of a count or gap that its span uses.
            where: The qualified name of the container, for errors.
            depth: The indentation level.
        """
        obj = "r" if side == "read" else "w"
        f = cstr(where)
        back = str(it.back)
        if it.span is not None:
            self.line("{", depth)
            depth += 1
            self.span(it.span, f"{obj}->err", f, ref, depth)
            back = f"{it.back} + span_bits"
        if side == "read":
            self.line(f"XTCE_PK_TRY(xtce_pk_read_seek_end(r, {back}, {f}));",
                      depth)
        else:
            self.line(f"XTCE_PK_TRY(xtce_pk_write_seek_tail(w, {back}));",
                      depth)
        if it.span is not None:
            self.line("}", depth - 1)

    def read_items(self, items, depth: int, where: str,
                   nested: bool = False) -> None:
        """Writes the statements that decode a container's entries with
        reader ``r``, declaring ``v<id>`` for each value (and ``h<id>``
        saying whether a conditional one is present).

        Args:
            items:  The entries.
            depth:  The indentation level.
            where:  The qualified name of the container, for errors.
            nested: Whether the entries are inside a condition, whose
                    values were declared before it.
        """
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
            elif isinstance(it, TailSeek):
                self.tail_seek(it, "read", self.read_ref, where, depth)
            elif isinstance(it, LengthEnd):
                self.line("XTCE_PK_TRY(xtce_pk_read_check_span(r, "
                          f"e{it.local.id}, {cstr(it.local.name)}));", depth)
            elif isinstance(it, Transform):
                # Read its items from what the transform makes of its bytes.
                t, f = it.start, cstr(it.name)
                self.line(f"size_t k{t};", depth)
                self.line(f"uint8_t t{t}[{it.cap}];", depth)
                self.line("XTCE_PK_TRY(xtce_pk_read_binary_from(r, "
                          f"{self.transform_hook(it.decode)}, "
                          f"{bool_lit(it.raw is not None)}, {it.raw or 0}, "
                          f"t{t}, {it.cap}, {f}, &k{t}));", depth)
                self.line(f"xtce_pk_reader s{t} = xtce_pk_reader_init(t{t}, "
                          f"k{t}, r->err);", depth)
                self.line(f"xtce_pk_reader* rr{t} = r;", depth)
                self.line(f"r = &s{t};", depth)
                self.read_items(it.items, depth, where, nested)
                self.line(f"r = rr{t};", depth)
            elif isinstance(it, TailPad):
                # The packet's last bits, which encoding fills with zeros.
                self.line(f"XTCE_PK_TRY(xtce_pk_read_skip(r, {it.bits}, "
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
                n = f"v{lc.id}"
                if lc.length is not None and lc.vtype.wide:
                    # Its value, which a uint64_t must hold.
                    n = f"n{lc.id}"
                    self.line(f"uint64_t {n} = 0;", depth)
                    self.line(f"if (!xtce_bn_to_u64(v{lc.id}.bytes, "
                              f"{lc.vtype.width // 8}, false, &{n}))", depth)
                    self.line("return xtce_pk_fail(r->err, "
                              f"XTCE_PK_BAD_LENGTH, {cstr(lc.name)}, NULL);",
                              depth + 1)
                if lc.length is not None and lc.measures:
                    self.line(f"int64_t e{lc.id};", depth)
                    self.line(f"XTCE_PK_TRY(xtce_pk_read_span_end(r, {n}, "
                              f"INT64_C({lc.length}), {cstr(lc.name)}, "
                              f"&e{lc.id}));", depth)
                elif lc.length is not None:
                    self.line(f"XTCE_PK_TRY(xtce_pk_read_limit(r, {n}, "
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
        """Writes the statements that encode a container's entries with
        writer ``w``.

        Args:
            n:     The packet being encoded.
            items: The entries.
            depth: The indentation level.
            where: The qualified name of the container, for errors.
            names: The Acc of each of the packet's fields, by Local.
        """
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
            elif isinstance(it, (Pad, TailPad)):
                self.line(f"XTCE_PK_TRY(xtce_pk_write_zeros(w, {it.bits}));",
                          depth)
            elif isinstance(it, TailSeek):
                self.tail_seek(it, "write", self.write_ref(n, names), where,
                               depth)
            elif isinstance(it, LengthEnd):
                lc = it.local
                enc = lc.vtype.enc
                self.line(f"XTCE_PK_TRY(xtce_pk_patch_span(w, l{lc.id}, "
                          f"{enc.bits}, {bool_lit(enc.little_endian)}, "
                          f"INT64_C({lc.length}), {cstr(lc.name)}));", depth)
            elif isinstance(it, Transform):
                # Write its items, then what the transform makes of them.
                t = it.start
                self.line(f"size_t t{t} = w->pos;", depth)
                self.write_items(n, it.items, depth, where, names)
                self.line(f"XTCE_PK_TRY(xtce_pk_write_transform(w, t{t}, "
                          f"{self.transform_hook(it.encode)}, "
                          f"{bool_lit(it.raw is not None)}, {it.raw or 0}, "
                          f"{it.out_cap}, {cstr(it.name)}));", depth)
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
                self.line("XTCE_PK_TRY(xtce_pk_write_zeros(w, "
                          f"{lc.vtype.enc.bits}));", depth)
            else:
                self.write_place(n, it.local, names, depth)

    def write_place(self, n: Node, lc, names: dict, depth: int) -> None:
        """Writes the statements writing a value: a field, a computed size
        or count, or a fixed constant.  An absent optional field fails
        with XTCE_PK_MISSING.

        Args:
            n:     The packet being encoded.
            lc:    The value's Local.
            names: The Acc of each of the packet's fields, by Local.
            depth: The indentation level.
        """
        f = cstr(lc.name)
        ref = self.write_ref(n, names)
        if lc in names:
            acc = names[lc]
            if acc.present:
                self.line(f"if (!{acc.present}) return xtce_pk_fail(w->err, "
                          f"XTCE_PK_MISSING, {f}, NULL);", depth)
            self.write_value(lc.vtype, acc.expr, f, depth, ref)
        elif lc in n.derived:
            self.write_value(written(n, lc), f"d{lc.id}", f, depth, ref)
        else:
            self.write_value(lc.vtype, self.literal(lc.vtype, n.fixed[lc]), f,
                             depth, ref, n.fixed[lc])

    def place(self, n: Node, lc, names: dict) -> Acc:
        """Says how encoding code reaches a value.

        Args:
            n:     The packet being encoded.
            lc:    The value's Local.
            names: The Acc of each of the packet's fields, by Local.

        Returns:
            The Acc of the field, of the computed size or count ``d<id>``,
            or of the fixed constant's literal.
        """
        if lc in names:
            return names[lc]
        if lc in n.derived:
            return Acc(f"d{lc.id}")
        return Acc(self.literal(lc.vtype, n.fixed[lc]))

    def fill(self, it: Fill, rw: str, ref, depth: int) -> None:
        """Writes the statements padding to the end of a container with a
        size, which fail with XTCE_PK_BAD_SIZE if its entries go past it.

        Args:
            it:    The Fill.
            rw:    "r" for the reader, "w" for the writer.
            ref:   Gives the value of a size that a DynSize gives.
            depth: The indentation level.
        """
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
        """Writes the statements skipping the bits between an array's
        elements, before each but the first.

        Args:
            e:     The array's encoding.
            side:  "read" for the reader ``r``, "write" for the writer
                   ``w``.
            f:     The C string literal naming the field, for errors.
            ref:   Gives the value of a gap that a DynSize gives.
            i:     The C variable holding the element's index.
            depth: The indentation level.
        """
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
        """Writes the statements failing with XTCE_PK_MISMATCH unless an
        included container's criteria hold.

        Args:
            it:    The Require.
            place: Gives the Acc of a Local.
            err:   The C expression for the xtce_pk_error to fail with.
            depth: The indentation level.
        """
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
        """Writes the statements checking the packet length field and the
        checksums when decoding ends.

        Args:
            n:     The packet being decoded.
            depth: The indentation level.
        """
        lc = n.length_field()
        if lc is not None:
            self.line(f"XTCE_PK_TRY(xtce_pk_read_finish(r, {cstr(lc.name)}));",
                      depth)
        for c in n.checks:
            self.verify(c, depth)

    def verify(self, c, depth: int) -> None:
        """Writes the statements failing with XTCE_PK_BAD_CHECKSUM unless a
        check holds.

        Args:
            c:     The check: its field's Local, spec and region.
            depth: The indentation level.
        """
        f = cstr(c.local.name)
        self.line("{", depth)
        d = depth + 1
        self.line(f"{spec_decl(c.spec)} xtce_pk_checksum_spec k = "
                  f"{checksum_spec(c.spec, self.p)};", d)
        start, end = self.region(c, "r")
        self.line("uint64_t c;", d)
        self.line(f"XTCE_PK_TRY(xtce_pk_read_checksum(r, &k, {start}, {end}, "
                  f"{f}, &c));", d)
        vt = c.local.vtype
        if vt.wide:
            # A wider field holds the value with zeros above it.
            self.line("uint64_t got = 0;", d)
            self.line(f"if (!xtce_bn_to_u64(v{c.local.id}.bytes, "
                      f"{vt.width // 8}, false, &got) || c != got)", d)
        else:
            mask = f"UINT64_C(0x{(1 << vt.enc.bits) - 1:x})"
            self.line(f"if ((c & {mask}) != (uint64_t)v{c.local.id})", d)
        self.line(f"return xtce_pk_fail(r->err, XTCE_PK_BAD_CHECKSUM, {f}, "
                  "NULL);", d + 1)
        self.line("}", depth)

    def patch(self, c, depth: int) -> None:
        """Writes the statement that computes a check and writes its value
        into its field, which was written as zeros.

        Args:
            c:     The check: its field's Local, spec and region.
            depth: The indentation level.
        """
        enc = c.local.vtype.enc
        self.line("{", depth)
        self.line(f"{spec_decl(c.spec)} xtce_pk_checksum_spec k = "
                  f"{checksum_spec(c.spec, self.p)};", depth + 1)
        start, end = self.region(c, "w")
        self.line(f"XTCE_PK_TRY(xtce_pk_patch_checksum(w, &k, {start}, {end}, "
                  f"l{c.local.id}, {enc.bits}, {bool_lit(enc.little_endian)}, "
                  f"{cstr(c.local.name)}));", depth + 1)
        self.line("}", depth)

    def region(self, c, rw: str) -> tuple:
        """Gives the start and end of the bits a check covers.

        Args:
            c:  The check.
            rw: "r" for the reader, whose ``p<id>`` holds the position of
                the check's field, or "w" for the writer, whose
                ``l<id>`` does.

        Returns:
            The C expressions for the start and end, in bits.
        """
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
        """Writes the statements storing the decoded values in a packet's
        struct.

        Args:
            n:     The packet.
            out:   The C expression for the pointer to the struct.
            depth: The indentation level.
        """
        for name, lc in n.fields:
            if lc.optional:
                self.line(f"{out}->has_{name} = h{lc.id};", depth)
            self.line(f"{out}->{name} = v{lc.id};", depth)

    def reader(self, depth: int = 1) -> None:
        """Declares the reader ``r`` of a decoding function's ``data``,
        ``len`` bytes long, which fails with ``err``.

        Args:
            depth: The indentation level.
        """
        self.line("xtce_pk_reader r_ = xtce_pk_reader_init(data, len, err);",
                  depth)
        self.line("xtce_pk_reader* r = &r_;", depth)
        self.line("(void)r;", depth)

    def define(self, n: Node) -> None:
        """Writes the definitions of a packet's functions: _decode,
        _decode_prefix, _encode_into, _encode, _batch_add, _batch_next,
        _describe, _algorithms, _alarms and _monitor.

        Args:
            n: The concrete container or meta-command.
        """
        t = self.p + n.ident
        self.decode(t)
        self.line()
        self.line(f"static inline int {t}_decode_prefix({t}* out, const "
                  "uint8_t* data, size_t len, size_t* used, xtce_pk_error* "
                  "err) {")
        self.reader()
        self.need_hooks(n.hooks, "err", 1)
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
        self.line("*used = xtce_pk_read_used(r);", 1)
        self.line("return 0;", 1)
        self.line("}")
        self.line()
        self.line(f"static inline int {t}_encode_into(const {t}* p, "
                  "xtce_pk_writer* w) {")
        self.line("(void)p;", 1)
        self.line("(void)w;", 1)
        self.need_hooks(n.hooks, "w->err", 1)
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
                        guards[(access(names[lc].expr, prefix),
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
        self.batch(t, n.key)
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
        """Writes the definitions of a packet's _algorithms, _alarms and
        _monitor functions.

        Args:
            n: The concrete container or meta-command.
            t: Its C type.
        """
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
            self.line(f"out[{i}].name = {cstr(alg.name)};", 1)
            if alg.hook is not None:
                f = f"{self.p}hook_{alg.hook.ident}"
                self.line("{", 1)
                self.line(f"double o[{alg.hook.outputs}];", 2)
                self.line(f"out[{i}].present = {f} && "
                          f"{f}({self.hook_inputs(alg.inputs, place)}, o);", 2)
                self.line(f"out[{i}].value = out[{i}].present ? o[{alg.index}] "
                          ": 0.0;", 2)
                self.line("}", 1)
                continue
            present = [place(lc).present for lc in inputs(alg.expr)
                       if lc.optional]
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
        """Writes the statements storing a field's alarm in ``out[i]``:
        XTCE_PK_ABSENT for an absent field, the worst of the elements' for
        an array.

        Args:
            i:     The alarm's place in ``out``.
            name:  The field's name in the packet's struct.
            lc:    The field's Local.
            a:     The alarm, bound to the packet's values.
            place: Gives the Acc of a Local.
            state: The AlarmMonitor field that keeps the parameter's
                   earlier samples, or None to count only this one.
        """
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
            self.line(f"{self.ctype(elem)} x = {place(lc).expr};", d)
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
            self.level(i, state, a, d + 1, elem)
            self.line("}", d)
        else:
            self.level(i, state, a, d, elem)
        if lc.optional:
            d -= 1
            self.line("}", d)
        self.line("}", 1)

    def level(self, i: int, state, a, depth: int, vt: VType) -> None:
        """Writes the statements storing ``lv`` in ``out[i]``; with
        ``state``, through the alarm's state, after recording ``x`` in its
        history.

        Args:
            i:     The alarm's place in ``out``.
            state: The AlarmMonitor field that keeps the parameter's
                   earlier samples, or None.
            a:     The alarm.
            depth: The indentation of the statements.
            vt:    The type of the value ``x`` (an array's element's).
        """
        if state is None:
            self.line(f"out[{i}].level = lv;", depth)
            return
        if a.history:
            self.line(f"xtce_pk_alarm_push(s, m->{state}_values, "
                      f"m->{state}_times, {a.history}, "
                      f"{math_value(vt, 'x', 'c')}, t);", depth)
        self.line(f"out[{i}].level = xtce_pk_alarm_update(s, lv, mv, mc);",
                  depth)

    def alarm_def(self, ad, vt: VType, place, depth: int, live=None) -> None:
        """Writes the statements raising ``lv`` to the level that an alarm
        definition gives the value ``x``.

        Args:
            ad:    The alarm definition.
            vt:    The type of ``x``.
            place: Gives the Acc of a Local that its conditions use.
            depth: The indentation level.
            live:  The AlarmMonitor field and its history's length, to
                   apply the change ranges too (on AlarmState ``s``, at
                   time ``t``) and set ``mv`` and ``mc`` to the
                   minViolations and minConformance; None for neither.
        """
        kind = vt.kind
        if live is not None and ad.change is not None:
            c = ad.change
            state, history = live
            self.line("{", depth)
            self.line("double d;", depth + 1)
            self.line(f"if (xtce_pk_alarm_change(s, m->{state}_values, "
                      f"m->{state}_times, {history}, "
                      f"{math_value(vt, 'x', 'c')}, t, {c.samples}, "
                      f"{bool_lit(c.per_second)}, {bool_lit(c.percent)}, &d)) "
                      "{", depth + 1)
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
        if ad.custom is not None:
            h, ins = ad.custom
            f = f"{self.p}hook_{h.ident}"
            self.line(f"if ({f}) {{", depth)
            self.line(f"int l = {f}({self.hook_inputs(ins, place)});",
                      depth + 1)
            self.line("if (l > 5) l = 5;", depth + 1)
            self.line("if (l > lv) lv = l;", depth + 1)
            self.line("}", depth)
        for level, terms in ad.conditions:
            cond = " && ".join(f"({self.term(c, place)})" for c in terms)
            self.line(f"if ({cond}) {{", depth)
            self.line(f"if (lv < {level}) lv = {level};", depth + 1)
            self.line("}", depth)

    def band(self, b, x: str, vt: VType, depth: int,
             float_value: bool = False) -> None:
        """Writes the statements raising ``lv`` to a Band's level if a
        value violates it.

        Args:
            b:           The Band.
            x:           The expression for the value.
            vt:          The value's type.
            depth:       The indentation level.
            float_value: Whether ``x`` is a double whatever ``vt`` is, as
                         a change is.
        """
        if vt.kind == "int" and vt.wide and not float_value:
            def cmp(v):
                return (f"xtce_bn_cmp(({x}).bytes, "
                        f"({self.literal(vt, v)}).bytes, {vt.width // 8}, "
                        f"{bool_lit(vt.signed)})")
            parts = (([f"{cmp(b.lo)} >= 0"] if b.lo is not None else [])
                     + ([f"{cmp(b.hi)} <= 0"] if b.hi is not None else []))
            test = "(" + (" && ".join(parts) or "true") + ")"
        elif vt.kind == "int" and not float_value:
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
        """Writes a stream's struct, its sync spec and its _init and _push
        functions (and, for a stream fed by another, the link between
        them).

        Args:
            st: The FixedFrameStream, VariableFrameStream or CustomStream.
        """
        t = self.p + st.ident
        fns = [(k, stream_fn(k, x)) for k, x in st.functions()]
        params = "".join(f", xtce_pk_stream_decode_fn {n}, void* "
                         f"{n[:-len('decode')]}ctx" if k == "decode"
                         else f", uint8_t (*{n})(uint8_t bit)" for k, n in fns)
        self.line()
        doc = stream_doc(st)
        if st.feeder is not None:
            doc += (f" Its input is what stream {st.feeder.key} gives, from "
                    "the bytes pushed.")
        if fns:
            doc += (" The application gives it its functions: "
                    + ", ".join(n for _, n in fns)
                    + " (a decoding function with the context it is called "
                      "with).")
        doc += f" Initialize it with {t}_init, and don't copy it after."
        for k, text in enumerate(textwrap.wrap(doc, 72)):
            self.line(("/* " if k == 0 else " * ") + text)
        self.line(" */")
        if not st.custom:
            self.line(f"#define {t}_CONTAINER {cstr(st.container)}")
        self.line(f"typedef struct {t} {{")
        if st.custom:
            self.line("xtce_pk_stream_decode_fn decode;", 1)
            self.line("void* ctx;", 1)
        else:
            bits, size = stream_capacity(st)
            sync = "frame" if st.fixed else "flag"
            self.line(f"xtce_pk_{sync}_sync sync;", 1)
            self.line(f"uint8_t bits[{bits}];", 1)
            self.line(f"uint8_t frame[{size}];", 1)
        if st.feeder is not None:
            self.line(f"{self.p}{st.feeder.ident} feeder;", 1)
        self.line(f"}} {t};")
        self.line()
        pcm = "XTCE_PK_" + st.pcm.upper().replace("BIPHASE", "BIPHASE_")
        if st.custom:
            pass
        elif st.fixed:
            n = (st.pattern_bits + 7) // 8
            for name, v in (("pattern", st.pattern), ("mask", st.mask)):
                b = ", ".join(f"0x{x:02x}" for x in v.to_bytes(n, "big"))
                self.line(f"static const uint8_t {t}_{name}[{n}] = {{{b}}};")
            self.line(f"static const xtce_pk_frame_spec {t}_spec = "
                      f"{{{st.frame_bits}, {t}_pattern, {t}_mask, "
                      f"{st.pattern_bits}, {st.location}, {st.max_errors}, "
                      f"{st.aperture}, {st.verify}, {st.check}, "
                      f"UINT64_C({st.invert_after}), {pcm}, "
                      f"{bool_lit(st.inverted)}}};")
            self.line()
        else:
            self.line(f"static const xtce_pk_flag_spec {t}_spec = "
                      f"{{{st.flag_bits}, {bool_lit(st.flag_ones)}, "
                      f"{st.max_bits}, {pcm}, {bool_lit(st.inverted)}}};")
            self.line()
        fparams = [("s", "The stream.")]
        for k, x in st.functions():
            n = stream_fn(k, x)
            if k == "decode":
                fparams.append((n,
                                f"The DecodingAlgorithm {x.decoder} of stream "
                                f"{x.key}, which gives the bytes it decodes "
                                "from those it is given."))
                fparams.append((n[:-len("decode")] + "ctx",
                                f"What {n} is called with."))
            else:
                fparams.append((n,
                                f"The InvertAlgorithm {x.inverter} of stream "
                                f"{x.key}: what a bit is once AutoInvert has "
                                "inverted the stream."))
        self.doc(f"Initializes a stream {st.key} with nothing pushed to it "
                 "yet.", fparams)
        self.line(f"static inline void {t}_init({t}* s{params}) {{")
        if st.custom:
            n = stream_fn("decode", st)
            self.line(f"s->decode = {n};", 1)
            self.line(f"s->ctx = {n[:-len('decode')]}ctx;", 1)
        else:
            bits, _ = stream_capacity(st)
            sync = "frame" if st.fixed else "flag"
            self.line(f"xtce_pk_{sync}_sync_init(&s->sync, &{t}_spec, s->bits, "
                      f"{bits}, s->frame);", 1)
            if st.inverter:
                self.line(f"s->sync.invert = {stream_fn('invert', st)};", 1)
        if st.feeder is not None:
            args = "".join(f", {n}, {n[:-len('decode')]}ctx" if k == "decode"
                           else f", {n}"
                           for k, n in ((k, stream_fn(k, x))
                                        for k, x in st.feeder.functions()))
            self.line(f"{self.p}{st.feeder.ident}_init(&s->feeder{args});", 1)
        self.line("}")
        if st.feeder is not None:
            # What the feeder gives goes on to this stream.
            self.line()
            self.line(f"typedef struct {t}_link_ {{")
            self.line(f"{t}* s;", 1)
            self.line("xtce_pk_frame_fn f;", 1)
            self.line("void* ctx;", 1)
            self.line(f"}} {t}_link_;")
            self.line()
            self.doc(f"Passes what stream {st.feeder.key} gives on to stream "
                     f"{st.key}.",
                     [("c",
                       f"The {t}_link_ that says which stream, and where its "
                       "output goes."), ("data", "The bytes given."),
                      ("len", "The number of bytes at `data`.")])
            self.line(f"static inline void {t}_feed_(void* c, const uint8_t* "
                      "data, size_t len) {")
            self.line(f"{t}_link_* k = ({t}_link_*)c;", 1)
            if st.custom:
                self.line("k->s->decode(k->s->ctx, data, len, k->f, k->ctx);",
                          1)
            else:
                sync = "frame" if st.fixed else "flag"
                self.line(f"xtce_pk_{sync}_sync_push(&k->s->sync, data, len, "
                          "k->f, k->ctx);", 1)
            self.line("}")
        self.line()
        what = ("the bytes it decodes" if st.custom
                else "each frame they complete")
        self.doc(
            f"Takes the next bytes of stream {st.key}, and gives `f` {what}.",
            [("s", "The stream."),
             ("data", "The stream's next bytes, in the order they arrive."),
             ("len", "The number of bytes at `data`."),
             ("f",
              f"Called with each piece of the bytes that {st.decoder} decodes."
              if st.custom
              else f"Called with the bytes of each frame, a {st.container} "
                   "packet, padded with zeros."),
             ("ctx", "What `f` is called with.")])
        self.line(f"static inline void {t}_push({t}* s, const uint8_t* data, "
                  "size_t len, xtce_pk_frame_fn f, void* ctx) {")
        if st.feeder is not None:
            self.line(f"{t}_link_ k;", 1)
            self.line("k.s = s;", 1)
            self.line("k.f = f;", 1)
            self.line("k.ctx = ctx;", 1)
            self.line(f"{self.p}{st.feeder.ident}_push(&s->feeder, data, len, "
                      f"{t}_feed_, &k);", 1)
        elif st.custom:
            self.line("s->decode(s->ctx, data, len, f, ctx);", 1)
        else:
            sync = "frame" if st.fixed else "flag"
            self.line(f"xtce_pk_{sync}_sync_push(&s->sync, data, len, f, ctx);",
                      1)
        self.line("}")

    def valid_test(self, vt: VType, x: str) -> str:
        """Builds the C condition that a value lies in one of its type's
        valid ranges.

        Args:
            vt: The value's type, an integer or a float with ranges.
            x:  The expression for the value.

        Returns:
            The condition.
        """
        tests = []
        for b in vt.valid:
            if vt.wide:
                def cmp(v):
                    return (f"xtce_bn_cmp(({x}).bytes, "
                            f"({self.literal(vt, v)}).bytes, {vt.width // 8}, "
                            f"{bool_lit(vt.signed)})")
                parts = (([f"{cmp(b.lo)} >= 0"] if b.lo is not None else [])
                         + ([f"{cmp(b.hi)} <= 0"] if b.hi is not None else []))
                tests.append(" && ".join(parts) or "true")
            elif vt.kind == "int":
                lit = slit if vt.signed else ulit
                parts = (([f"{x} >= {lit(b.lo)}"] if b.lo is not None else [])
                         + ([f"{x} <= {lit(b.hi)}"] if b.hi is not None
                            else []))
                tests.append(" && ".join(parts) or "true")
            else:
                def bound(v):
                    return "false, 0.0" if v is None else f"true, {cflt(v)}"
                tests.append(f"xtce_pk_in_band({x}, {bound(b.lo)}, "
                             f"{bool_lit(b.lo_incl)}, {bound(b.hi)}, "
                             f"{bool_lit(b.hi_incl)})")
        return " || ".join(f"({t})" for t in tests)

    def hook_inputs(self, inputs: list, place) -> str:
        """Builds the xtce_pk_inputs of a custom algorithm's inputs.

        Args:
            inputs: Each input: ``("const", value)``, or a value given by
                    its Local, path and type.
            place:  Gives the Acc of a Local.

        Returns:
            The xtce_pk_inputs_of call, each input as a double.
        """
        args = "".join(f", {flt(x[1])}" if x[0] == "const"
                       else ", (double)"
                       + math_value(x[3], access(place(x[1]).expr, x[2]), "c")
                       for x in inputs)
        return f"xtce_pk_inputs_of({len(inputs)}{args})"

    def transform_hook(self, h) -> str:
        """A transform hook, or NULL for a way the document doesn't give."""
        return f"{self.p}hook_{h.ident}" if h else "NULL"

    def need_hooks(self, hooks: list, err: str, depth: int) -> None:
        """Writes the statements failing with XTCE_PK_NO_ALGORITHM while
        any of the hooks a function calls isn't set.

        Args:
            hooks: The hooks.
            err:   The C expression for the xtce_pk_error to fail with.
            depth: The indentation level.
        """
        for h in hooks:
            self.line(f"if (!{self.p}hook_{h.ident}) return "
                      f"xtce_pk_fail({err}, XTCE_PK_NO_ALGORITHM, "
                      f"{cstr(h.ident)}, NULL);", depth)

    def declare_hooks(self) -> None:
        """Declares the function pointers the application sets for the
        document's custom algorithms, defined in the one file that
        defines the HOOKS macro before including the header."""
        macro = self.p.upper() + "HOOKS"
        self.line()
        self.line("/* The functions the application supplies for the "
                  "document's custom algorithms:")
        self.line(" * set them before decoding or encoding a packet that needs "
                  "them, or it fails")
        self.line(f" * with XTCE_PK_NO_ALGORITHM.  Define {macro} in exactly "
                  "one file before it")
        self.line(" * includes this header, to define them there. */")
        self.line(f"#ifdef {macro}")
        self.line(f"#define {self.p.upper()}HOOK")
        self.line("#else")
        self.line(f"#define {self.p.upper()}HOOK extern")
        self.line("#endif")
        for h in self.m.hooks:
            for k, text in enumerate(textwrap.wrap(f"{h.name}, "
                                                   f"{HOOK_DOCS[h.kind]}", 72)):
                self.line(("/* " if k == 0 else " * ") + text)
            self.line(" */")
            self.line(f"{self.p.upper()}HOOK {HOOK_TYPES[h.kind]} "
                      f"{self.p}hook_{h.ident};")

    def declare_monitor(self) -> None:
        """Writes the AlarmMonitor struct, which keeps the state of each
        parameter whose alarms depend on earlier samples, and its _init
        function."""
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
        self.doc("Clears the state of every alarm that depends on earlier "
                 "samples.", [("m", "The state.")])
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

    def decode(self, t: str) -> None:
        """Writes the definition of a packet type's _decode, which calls
        its _decode_prefix.

        Args:
            t: The packet's C type.
        """
        self.line()
        self.line(f"static inline int {t}_decode({t}* out, const uint8_t* "
                  "data, size_t len, xtce_pk_error* err) {")
        self.line("size_t used;", 1)
        self.line(f"return {t}_decode_prefix(out, data, len, &used, err);", 1)
        self.line("}")

    def batch(self, t: str, key: str) -> None:
        """Writes the definitions of a packet type's _batch_add and
        _batch_next, with their headers.

        Args:
            t:   The packet's C type.
            key: What the headers call the packet: its container's
                 qualified XTCE name, or for a kind the root's followed by
                 "derived".
        """
        self.line()
        err = ("Receives the error's code, the field or container it concerns "
               "and its detail on failure; may be NULL.")
        self.doc(
            f"Encodes a {key} packet after the packets already in a batch.",
            [("p", "The packet."),
             ("b",
              "The batch; it is left as it was on failure, XTCE_PK_NO_SPACE if "
              "the packet doesn't fit."), ("err", err)],
            "0 on success, else the XTCE_PK_ code of the error, which is also "
            "stored in `*err`.")
        self.line(f"static inline int {t}_batch_add(const {t}* p, "
                  "xtce_pk_batch* b, xtce_pk_error* err) {")
        self.line("size_t n;", 1)
        self.line(f"XTCE_PK_TRY({t}_encode(p, xtce_pk_batch_tail(b), "
                  "xtce_pk_batch_room(b), &n, err));", 1)
        self.line("xtce_pk_batch_added(b, n);", 1)
        self.line("return 0;", 1)
        self.line("}")
        self.line()
        self.doc(f"Decodes the next packet of a batch, which must be a {key} "
                 "packet.",
                 [("out", "Receives the packet."),
                  ("b", "The batch, which moves past the packet on success."),
                  ("err", err)],
                 "0 on success, else the XTCE_PK_ code of the error, which is "
                 "also stored in `*err`.")
        self.line(f"static inline int {t}_batch_next({t}* out, xtce_pk_batch* "
                  "b, xtce_pk_error* err) {")
        self.line("size_t used;", 1)
        self.line(f"XTCE_PK_TRY({t}_decode_prefix(out, b->buf + b->pos, "
                  "xtce_pk_batch_remaining(b), &used, err));", 1)
        self.line("b->pos += used;", 1)
        self.line("return 0;", 1)
        self.line("}")

    def encode(self, t: str) -> None:
        """Writes the definition of a packet type's _encode, which calls
        its _encode_into with a writer over the caller's buffer.

        Args:
            t: The packet's C type.
        """
        self.line()
        self.line(f"static inline int {t}_encode(const {t}* p, uint8_t* buf, "
                  "size_t cap, size_t* len, xtce_pk_error* err) {")
        self.line("xtce_pk_writer w = xtce_pk_writer_init(buf, cap, err);", 1)
        self.line(f"XTCE_PK_TRY({t}_encode_into(p, &w));", 1)
        self.line("*len = w.len;", 1)
        self.line("return 0;", 1)
        self.line("}")

    def describe_value(self, vt: VType, x: str, depth: int) -> None:
        """Writes the statements appending a value's canonical text to the
        text ``t``.

        Args:
            vt:    The value's type.
            x:     The expression for the value.
            depth: The indentation level.
        """
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
        elif vt.wide:
            n = vt.width // 8
            # A sign, and fewer than 2.41 digits a byte.
            self.line("{", depth)
            self.line(f"uint8_t s[{n}];", depth + 1)
            self.line(f"char d[{3 * n + 2}];", depth + 1)
            self.line(f"xtce_bn_to_str_radix(({x}).bytes, {n}, "
                      f"{bool_lit(vt.signed)}, 10, s, d, sizeof d);", depth + 1)
            self.line("xtce_pk_text_puts(t, d);", depth + 1)
            self.line("}", depth)
        elif vt.signed:
            self.line(f"xtce_pk_text_int(t, {x});", depth)
        else:
            self.line(f"xtce_pk_text_uint(t, {x});", depth)

    def define_kind(self, k: Kind) -> None:
        """Writes the definitions of a kind's functions, which decode the
        root and its children and hand the rest to the member the tag
        names.

        Args:
            k: The kind: every concrete packet derived from a root.
        """
        t = self.p + k.ident
        self.decode(t)
        self.line()
        self.line(f"static inline int {t}_decode_prefix({t}* out, const "
                  "uint8_t* data, size_t len, size_t* used, xtce_pk_error* "
                  "err) {")
        self.reader()
        self.need_hooks(items_hooks(k.root.items), "err", 1)
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
        self.batch(t, f"{k.root.key} derived")
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
        """Writes the statements that, after a container's entries are
        read, read those of the first child whose criteria hold,
        recursively; at a concrete container they check it and store the
        packet in ``out``, and at an abstract one with no child matching
        they fail with XTCE_PK_NO_MATCH.

        Args:
            k:     The kind being decoded.
            n:     The container whose entries were just read.
            depth: The indentation level.
        """
        for c in n.children:
            if c.error:
                continue
            self.element_guards([p for x in c.criteria for p in term_paths(x)],
                                self.read_acc, "r->err", depth)
            conds = [self.term(x, self.read_acc) for x in c.criteria]
            cond = (" && ".join(f"({x})" for x in conds) if len(conds) > 1
                    else (conds or ["1"])[0])
            self.need_hooks(terms_hooks(c.criteria), "err", depth)
            self.line(f"if ({cond}) {{", depth)
            self.need_hooks(items_hooks(c.items), "err", depth + 1)
            self.read_items(c.items, depth + 1, c.key)
            self.dispatch(k, c, depth + 1)
            self.line("}", depth)
        if n.abstract:
            self.line("return xtce_pk_fail(err, XTCE_PK_NO_MATCH, "
                      f"{cstr(n.key)}, NULL);", depth)
        else:
            t = self.p + k.ident
            self.need_hooks(checks_hooks(n.checks), "err", depth)
            self.finish(n, depth)
            self.line(f"out->tag = {t}_{n.ident}_tag;", depth)
            self.line("{", depth)
            self.line(f"{self.p}{n.ident}* o = &out->u.{n.ident};", depth + 1)
            # A packet without fields leaves o unused.
            self.line("(void)o;", depth + 1)
            self.construct(n, "o", depth + 1)
            self.line("}", depth)
            self.line("*used = xtce_pk_read_used(r);", depth)
            self.line("return 0;", depth)


def emit(mission: Mission, source: str, prefix: Optional[str] = None) -> str:
    return CEmitter(mission, source, prefix).emit()
