"""Helpers shared by the code emitters."""

from __future__ import annotations

import math
import textwrap

from xtcegen import CRATE, REPOSITORY

from .model import (BinaryEnc, Criterion, FloatEnc, IntEnc, Node, Polynomial,
                    Spline, StringEnc, Value, VType)

# The runtimes' integer encodings.  BCD values are decoded to unsigned ones.
ENCODINGS = {"unsigned": "Unsigned", "twosComplement": "TwosComplement",
             "signMagnitude": "SignMagnitude",
             "onesComplement": "OnesComplement", "BCD": "Unsigned",
             "packedBCD": "Unsigned"}


def flt(x: float) -> str:
    """A float literal valid in Rust, C++ and Python (finite values only)."""
    if not math.isfinite(x):
        raise ValueError(f"non-finite constant {x}")
    s = repr(float(x))
    return s if any(c in s for c in ".en") else s + ".0"


def int_enc(vt: VType) -> str:
    return ENCODINGS[vt.enc.encoding]


def le(vt: VType) -> bool:
    return bool(getattr(vt.enc, "little_endian", False))


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
    """The first position of ``term`` in ``b`` that starts a code unit, or
    -1."""
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


def wire_cap(vt: VType) -> int:
    """The most bytes of content a string field (or a boolean encoded as a
    string) can hold on the wire, in its charset."""
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
    """The most bytes of UTF-8 a string field (or a boolean encoded as a
    string) can hold.  In a charset other than UTF-8 and US-ASCII, the C
    runtime reads the field's bytes into the end of the storage and
    converts them into its start, so the storage holds the most UTF-8 the
    whole field can give."""
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


def values(node: Node) -> list:
    return [it.local for it in node.all_items() if isinstance(it, Value)]


def header_lines(mission, source: str) -> list:
    """The notice at the top of every generated file, without comment markers.
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


__all__ = ["Criterion", "FloatEnc", "IntEnc", "Polynomial", "Spline",
           "StringEnc", "criteria_by_node", "field_range", "size_args",
           "header_lines", "flt", "int_enc", "invertible", "le", "raw_is_int",
           "str_cap", "string_spec", "values", "wire_cap", "wire_region",
           "code_unit", "encode_text", "find_unit", "fast_order"]
