"""Helpers shared by the code emitters."""

from __future__ import annotations

import dataclasses
import math
import re
import textwrap

from tcs_xtce.binding import CRATE, REPOSITORY

from .model import (AggregateEnc, BinaryEnc, Cond, ContextCal, Criterion,
                    DynSize, Fill, FloatEnc, IntEnc, MathOp, Node, Polynomial,
                    Require, Seek, Spline, StringEnc, Value, Verify, VType,
                    term_hooks, walk, TailSeek, Transform, XformEnc)

# The runtimes' integer encodings.  BCD values are decoded to unsigned ones.
ENCODINGS = {"unsigned": "Unsigned", "twosComplement": "TwosComplement",
             "signMagnitude": "SignMagnitude",
             "onesComplement": "OnesComplement", "BCD": "Unsigned",
             "packedBCD": "Unsigned"}


def flt(x: float) -> str:
    """Writes a float literal valid in Rust, C++ and Python.

    Args:
        x: The value, which must be finite.

    Returns:
        The literal, with a decimal point or exponent so that it is a
        float in every language.

    Raises:
        ValueError: ``x`` is infinite or NaN.
    """
    if not math.isfinite(x):
        raise ValueError(f"non-finite constant {x}")
    s = repr(float(x))
    return s if any(c in s for c in ".en") else s + ".0"


def int_enc(vt: VType) -> str:
    return ENCODINGS[vt.enc.encoding]


def le(vt: VType) -> bool:
    return bool(getattr(vt.enc, "little_endian", False))


class Ref:
    """Gives the value a size or location uses (``ref(d)`` for a DynSize),
    and how generated code reaches a Local (``place``), for calibrators and
    for size code that checks first that the elements it reads are
    there."""

    def __init__(self, fn, place):
        self.fn = fn
        self.place = place

    def __call__(self, d):
        return self.fn(d)


# MathOperationCalibrator operators: the Rust runtime's MathOperator and
# the C runtime's XTCE_PK_OP_ names.  +, - and * are plain arithmetic.
MATH_OPS = {"/": "Div", "%": "Mod", "^": "Pow", "y^x": "RevPow", "ln": "Ln",
            "log": "Log10", "e^x": "Exp", "1/x": "Inv", "x!": "Factorial",
            "tan": "Tan", "cos": "Cos", "sin": "Sin", "atan": "Atan",
            "atan2": "Atan2", "acos": "Acos", "asin": "Asin", "tanh": "Tanh",
            "cosh": "Cosh", "sinh": "Sinh", "atanh": "Atanh", "acosh": "Acosh",
            "asinh": "Asinh", "<<": "Shl", ">>": "Shr", "&": "BitAnd",
            "|": "BitOr", "xor": "BitXor", "~": "BitNot", "&&": "And",
            "||": "Or", "!": "Not", "abs": "Abs", "div": "EuclidDiv",
            "int": "Trunc", ">": "Gt", ">=": "Ge", "<": "Lt", "<=": "Le",
            "==": "Eq", "!=": "Ne", "min": "Min", "max": "Max"}


def c_op(op: str) -> str:
    """The C runtime's name of a MathOperationCalibrator operator."""
    return "XTCE_PK_OP_" + re.sub(r"(?<!^)(?=[A-Z])", "_", MATH_OPS[op]).upper()


def leaf_calibrators(cal) -> list:
    """Finds the polynomials and splines in a calibrator.

    Args:
        cal: The calibrator: a Polynomial, a Spline, a ContextCal or
             another kind.

    Returns:
        The polynomials and splines it uses, a context calibrator's cases'
        first and its default's last; none for another kind.
    """
    if isinstance(cal, ContextCal):
        out = [x for _, c in cal.cases for x in leaf_calibrators(c)]
        return out + leaf_calibrators(cal.default)
    return [cal] if isinstance(cal, (Polynomial, Spline)) else []


def math_value(vt: VType, x: str, lang: str) -> str:
    """Expression converting a value to a double, for a calibrator, an
    algorithm or an alarm.

    Args:
        vt:   The value's type.
        x:    The expression for the value.
        lang: The language: "rust", "c" or "python".

    Returns:
        The expression.  An integer of more than 64 bits is rounded to the
        nearest double, ties to even, as bnum converts, and is infinite past
        the largest.
    """
    k = vt.kind
    if vt.wide:
        if lang == "rust":
            return f"pk::wide_f64({x})"
        if lang == "c":
            return (f"xtce_bn_to_f64(({x}).bytes, {vt.width // 8}, "
                    f"{'true' if vt.signed else 'false'})")
        return f"pk.int_float({x})"
    if lang == "rust":
        return {"float": f"({x})", "enum": f"(({x}).0 as f64)",
                "bool": f"(if {x} {{ 1.0 }} else {{ 0.0 }})"}.get(
            k, f"(({x}) as f64)")
    if lang == "c":
        return {"bool": f"(({x}) ? 1.0 : 0.0)"}.get(k, f"((double)({x}))")
    return {"bool": f"(1.0 if {x} else 0.0)"}.get(k, f"float({x})")


def fast_order(e) -> bool:
    """Whether the runtimes' plain reader and writer (``uint_order``)
    handle an integer or float encoding: most significant bit first, big
    endian or little endian in whole bytes, at most 64 bits."""
    return (not e.lsb and e.bits <= 64
            and (e.order == "be" or (e.order == "le" and e.bits % 8 == 0)))


def raw_is_int(vt: VType) -> bool:
    return isinstance(vt.enc, IntEnc)


def invertible(vt: VType) -> bool:
    return vt.calibrator is None or vt.calibrator.invertible


# Python codecs of the XTCE string encodings.
CODECS = {"UTF-8": "utf-8", "US-ASCII": "ascii", "ISO-8859-1": "latin-1",
          "Windows-1252": "cp1252", "UTF-16": "utf-16-be",
          "UTF-16BE": "utf-16-be", "UTF-16LE": "utf-16-le",
          "UTF-32": "utf-32-be", "UTF-32BE": "utf-32-be",
          "UTF-32LE": "utf-32-le"}


def code_unit(charset: str) -> int:
    """The bytes in one code unit of the XTCE string encoding ``charset``."""
    return 2 if "16" in charset else 4 if "32" in charset else 1


def encode_text(s: str, charset: str) -> bytes:
    """The bytes of ``s`` in ``charset``; raises ValueError if it has
    characters that ``charset`` can't hold."""
    return s.encode(CODECS[charset])


def find_unit(b: bytes, term: bytes, unit: int) -> int:
    """Finds a terminator in a string's bytes.

    Args:
        b:    The string's bytes.
        term: The terminator's bytes.
        unit: The bytes in one code unit of the string's encoding.

    Returns:
        The first position of ``term`` in ``b`` that starts a code unit,
        or -1 if there is none.
    """
    i = b.find(term)
    while i >= 0 and i % unit:
        i = b.find(term, i + 1)
    return i


def string_spec(vt: VType) -> tuple:
    e: StringEnc = vt.enc
    return e.fixed_bits, e.max_bits, e.terminator, e.size_tag_bits or 0


def wire_region(vt: VType) -> int:
    """The most bytes a string field occupies on the wire."""
    fixed, mx, _, _ = string_spec(vt)
    return (fixed if fixed is not None and vt.enc.dyn is None else mx) // 8


def binary_cap(vt: VType) -> int:
    """The most bytes a binary value holds."""
    e = vt.enc
    return e.cap if isinstance(e, XformEnc) else e.bits // 8


def wire_cap(vt: VType) -> int:
    """Says how much content a string field can hold on the wire.

    Args:
        vt: The type of a string field, or of a boolean encoded as a
            string, or of a string encoded as binary.

    Returns:
        The most bytes of content, in its charset, that the field can
        hold: what its fixed size, its maximum size, its terminator and
        its size tag leave room for.
    """
    if isinstance(vt.enc, BinaryEnc):
        return vt.enc.bits // 8  # a string encoded as binary fills its field
    fixed, mx, term, tag = string_spec(vt)
    if vt.enc.dyn is not None:
        fixed = mx  # it occupies at most max_bits, like a fixed size
    if fixed is not None and term is not None:
        return fixed // 8
    if fixed is not None:
        return max(min((fixed - tag) // 8, (1 << tag) - 1), 0)
    if term is not None:
        return max(mx // 8 - len(term), 0)
    return max(min((mx - tag) // 8, (1 << tag) - 1), 0)


def str_cap(vt: VType) -> int:
    """Says how much UTF-8 a string field's storage must hold.

    In a charset other than UTF-8 and US-ASCII, the C runtime reads the
    field's bytes into the end of the storage and converts them into its
    start, so the storage holds the most UTF-8 the whole field can give.

    Args:
        vt: The type of a string field, or of a boolean encoded as a
            string, or of a string encoded as binary.

    Returns:
        The most bytes of UTF-8 the field can hold.
    """
    if isinstance(vt.enc, BinaryEnc) or vt.enc.charset in ("UTF-8", "US-ASCII"):
        return wire_cap(vt)
    w = wire_region(vt)
    cs = vt.enc.charset
    if cs == "ISO-8859-1":
        return 2 * w
    if cs == "Windows-1252":
        return 3 * w
    if "16" in cs:
        return (3 * w + 1) // 2
    return w


def field_range(vt: VType) -> tuple[int, int]:
    """The smallest and largest value of an integer's generated field."""
    bits = vt.width
    return ((-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if vt.signed
            else (0, (1 << bits) - 1))


def size_args(d) -> str:
    """The arguments after the length that size_source takes for a
    DynSize, in every language."""
    return f"{flt(d.slope)}, {flt(d.intercept)}, {d.unit}, {d.factor}"


def criteria_by_node(node: Node) -> list[tuple[Node, list[Criterion]]]:
    """(node, criteria) for every node of the chain below the root."""
    return [(n, n.criteria) for n in node.chain()[1:]]


def stream_capacity(st) -> tuple[int, int]:
    """Sizes the buffers of a stream's synchronizer.

    Args:
        st: A FixedFrameStream or VariableFrameStream.

    Returns:
        The most bits its synchronizer keeps, and the most bytes of one
        of its frames.
    """
    if st.fixed:
        bits = ((max(st.verify, 2) + 2) * st.frame_bits + 2 * st.aperture
                + st.location + st.pattern_bits + 16)
        return bits, (st.frame_bits + 7) // 8
    return st.max_bits + st.flag_bits + 2, (st.max_bits + 8) // 8


def stream_fn(kind: str, st) -> str:
    """The name of the parameter that gives a stream's type the
    application's ``kind`` function (decode or invert) for stream ``st``."""
    from .compile import snake  # compile imports this module
    return f"{snake(st.key.rsplit('/', 1)[-1])}_{kind}"


def stream_doc(st) -> str:
    """Describes a stream, for its generated type's documentation.

    Args:
        st: The stream.

    Returns:
        One or two sentences saying what kind of stream it is and what
        it gives: frames of its container or service, or the bytes its
        DecodingAlgorithm decodes.
    """
    if st.custom:
        return (f"Custom stream {st.key}: gives the bytes that the "
                f"application's DecodingAlgorithm {st.decoder} decodes from "
                "its input.")
    what = "Fixed" if st.fixed else "Variable"
    if st.service:
        return (f"{what} frame stream {st.key}: finds its frames, each a "
                f"packet of service {st.container}: {', '.join(st.service)}.")
    return (f"{what} frame stream {st.key}: finds its frames, each a "
            f"{st.container} packet.")


# What each kind of hook computes, for the generated documentation; the
# manual gives each language's signatures.
HOOK_DOCS = {
    "criterion": "a criterion: whether it holds for the inputs.",
    "algorithm": "an algorithm: its outputs from the inputs.",
    "alarm": "an alarm: the level for the inputs, 0 (normal) to 5 (severe).",
    "checksum": "a checksum: the value of a range of the packet's bits.",
    "binary_from": "a transform: how many of the bytes given it takes and the "
                   "bytes it makes of them, or nothing if it can't.",
    "binary_to": "a transform: the bytes it makes of the bytes given, or "
                 "nothing if it can't.",
    "time_from": "a time decoder: how many of the bytes given it takes and the "
                 "time they hold, in seconds, or nothing if it can't.",
    "time_to": "a time encoder: the bytes of the time given, in seconds, or "
               "nothing if it can't."}


def terms_hooks(terms: list) -> list:
    """Lists the hooks that criteria call.

    Args:
        terms: The criteria, each a Criterion or a tree of them.

    Returns:
        Each hook they call, once, in the order they first call it.
    """
    out: list = []
    for t in terms:
        out += [h for h in term_hooks(t) if h not in out]
    return out


def checks_hooks(checks: list) -> list:
    """Lists the hooks of custom checksums.

    Args:
        checks: The checks, whose ``spec`` is ``("custom", hook)`` for a
                custom checksum.

    Returns:
        Each custom checksum's hook, once, in order.
    """
    out: list = []
    for c in checks:
        if c.spec[0] == "custom" and c.spec[1] not in out:
            out.append(c.spec[1])
    return out


def items_hooks(items: list) -> list:
    """Lists the hooks that reading or writing entries calls.

    Args:
        items: The entries of a container or meta-command.

    Returns:
        Each hook they call, once: first those that criteria call
        (include conditions, then restriction criteria and the lookups of
        dynamic sizes), then custom checksums', then transforms'.
    """
    def conds(it) -> list:
        return (it.criteria + [c for x in it.items for c in conds(x)]
                if isinstance(it, Cond) else [])

    terms: list = [c for it in items for c in conds(it)]
    checks: list = []
    transforms: list = []
    for it in walk(items):
        if isinstance(it, Transform):
            transforms += [h for h in (it.decode, it.encode) if h]
        if isinstance(it, Require):
            terms += it.criteria
        if isinstance(it, Verify):
            checks.append(it.check)
        dyns = []
        if isinstance(it, Seek) and isinstance(it.offset, DynSize):
            dyns.append(it.offset)
        if isinstance(it, (Seek, TailSeek)) and it.span is not None:
            dyns += it.span.dyns
        if isinstance(it, Fill) and isinstance(it.size, DynSize):
            dyns.append(it.size)
        if isinstance(it, Value):
            dyns += [t.dyn for t in it.local.vtype.tree() if t.dyn]
            for t in it.local.vtype.tree():
                x = (t.enc.transform if isinstance(t.enc, AggregateEnc)
                     else t.enc)
                if isinstance(x, XformEnc):
                    transforms += [h for h in (x.decode, x.encode) if h]
        for d in dyns:
            terms += [c for crit, _ in d.lookup or [] for c in crit]
    out = terms_hooks(terms)
    out += [h for h in checks_hooks(checks) if h not in out]
    return out + [h for h in dict.fromkeys(transforms) if h not in out]


def written(n: Node, lc) -> VType:
    """The type of a value as packet ``n`` writes it: a size or count it
    computes from what it sizes isn't checked against its valid ranges,
    since what it sizes is, with a better error (BadSize)."""
    if lc in n.derived and lc.vtype.valid:
        return dataclasses.replace(lc.vtype, valid=[])
    return lc.vtype


def notes(n: Node) -> list[str]:
    """What the generated documentation of packet ``n`` says it leaves
    out."""
    return ([f"Alarm left out: {x}." for x in n.alarm_notes]
            + [f"MathAlgorithm left out: {x}." for x in n.algorithm_notes])


def inputs(e: tuple) -> list:
    """Lists the values that a MathOperation uses.

    Args:
        e: The operation's tree: ``("const", x)``, ``("raw",)``,
           ``("value", ref)`` or an operator with its operands.

    Returns:
        The Locals of the values it refers to, each once, in the order
        it first uses them.
    """
    out: list = []
    if e[0] == "value":
        out.append(e[1].local)
    elif e[0] not in ("const", "raw"):
        for a in e[1:]:
            out += [lc for lc in inputs(a) if lc not in out]
    return out


def values(node: Node) -> list:
    return [it.local for it in node.all_items() if isinstance(it, Value)]


def header_lines(mission, source: str) -> list:
    """Writes the notice at the top of every generated file.

    Args:
        mission: The compiled mission, whose SpaceSystems it names.
        source:  The name of the XTCE documents it was generated from.

    Returns:
        The notice's lines, without comment markers; the first sentence,
        which names the documents, is wrapped at 76 columns.
    """
    spaces = ", ".join(repr(s) for s in mission.spaces)
    plural = "s" if len(mission.spaces) > 1 else ""
    # The names of several documents may not fit one line.
    first = textwrap.wrap(f"@generated automatically by tcs-xtce from {source} "
                          f"(SpaceSystem{plural} {spaces}).", 76,
                          break_long_words=False, break_on_hyphens=False)
    rest = ["Any changes made to this file will be lost when it is "
            "regenerated;",
            f"change the XTCE document{plural} instead and rerun `python3 -m "
            "tcs_xtce`.", f"tcs-xtce: {REPOSITORY}", f"Rust crate: {CRATE}"]
    return first + rest


# The widest line of generated code.
WIDTH = 80


def _fill(text: str, first: str, rest: str, width: int) -> list[str]:
    """Wraps text at spaces into lines of at most ``width`` columns.

    Args:
        text:  The text to wrap.
        first: What starts the first line.
        rest:  What starts each later line.
        width: The widest line, in columns.

    Returns:
        The lines; a word longer than a line is left whole.
    """
    return (textwrap.wrap(" ".join(text.split()), width, initial_indent=first,
                          subsequent_indent=rest, break_long_words=False,
                          break_on_hyphens=False) or [first.rstrip()])


def doc_header(lang: str, summary: str, params: list[tuple[str, str]] = (),
               returns: str | None = None, errors: str | None = None,
               raises: list[tuple[str, str]] = (),
               indent: int = 0) -> list[str]:
    """Formats the documentation header of a generated function.

    The header follows the language's convention: rustdoc for Rust,
    Doxygen for C and C++, a Google style docstring for Python.  Each
    parameter's description, continuation lines included, starts in one
    column, and every line fits ``WIDTH`` columns after ``indent``.

    Args:
        lang:    The language: "rust", "c" or "python".
        summary: What the function does, one or more sentences.
        params:  Each parameter, in order, with what it means; for Rust
                 and Python without the receiver.
        returns: What the function returns, or None if it returns
                 nothing.  In C it should say what the error codes are.
        errors:  For Rust, when the function returns an error (its
                 ``# Errors`` section); ignored in the other languages.
        raises:  For Python, each exception the function raises, with
                 when; ignored in the other languages.
        indent:  The columns the lines will be indented by.

    Returns:
        The header's lines without that indentation: for Rust ``///``
        lines, for C one ``/** ... */`` comment, for Python the docstring
        with its quotes.

    Raises:
        ValueError: ``lang`` is none of the three.
    """
    width = WIDTH - indent
    names = [n for n, _ in params]
    if lang == "rust":
        out = _fill(summary, "/// ", "/// ", width)
        if params:
            out += ["///", "/// # Arguments", "///"]
            col = max(len(f"`{n}`") for n in names)
            for n, d in params:
                head = f"/// * {f'`{n}`':<{col}} - "
                out += _fill(d, head, "///" + " " * (len(head) - 3), width)
        for title, text in (("Returns", returns), ("Errors", errors)):
            if text is not None:
                out += ["///", f"/// # {title}", "///"]
                out += _fill(text, "/// ", "/// ", width)
        return out
    if lang == "c":
        out = _fill(summary, "/** ", " * ", width)
        if params or returns is not None:
            out.append(" *")
            col = max([len(n) for n in names] + [1]) + 2
            tag = len(" * @param ") + col
            for n, d in params:
                out += _fill(d, f" * @param {n:<{col}}", " *" + " " * (tag - 2),
                             width)
            if returns is not None:
                head = (f" * {'@return':<{tag - 3}}" if params
                        else " * @return ")
                out += _fill(returns, head, " *" + " " * (len(head) - 2), width)
        # Close the comment on its last line when it fits, else below it.
        if len(out[-1]) + 3 <= width:
            out[-1] += " */"
        else:
            out.append(" */")
        return out
    if lang == "python":
        out = _fill(summary, '"""', "", width)
        if params:
            out += ["", "Args:"]
            col = max(len(n) for n in names) + 2
            for n, d in params:
                out += _fill(d, f"    {n + ':':<{col}}", " " * (4 + col), width)
        if returns is not None:
            out += ["", "Returns:"] + _fill(returns, "    ", "    ", width)
        if raises:
            out += ["", "Raises:"]
            for exc, d in raises:
                out += _fill(d, f"    {exc}: ", "        ", width)
        if len(out) == 1 and len(out[0]) + 3 <= width:
            out[0] += '"""'
        else:
            out.append('"""')
        return out
    raise ValueError(f"unknown language {lang!r}")


__all__ = ["Criterion", "FloatEnc", "IntEnc", "Polynomial", "Spline",
           "StringEnc", "criteria_by_node", "field_range", "size_args",
           "stream_fn", "header_lines", "flt", "int_enc", "invertible", "le",
           "raw_is_int", "str_cap", "string_spec", "values", "wire_cap",
           "wire_region", "code_unit", "encode_text", "find_unit", "fast_order",
           "Ref", "MATH_OPS", "c_op", "leaf_calibrators", "math_value",
           "doc_header"]
