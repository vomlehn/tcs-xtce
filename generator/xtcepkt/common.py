"""Helpers shared by the code emitters."""

from __future__ import annotations

import math

from xtcegen import CRATE, REPOSITORY

from .model import BinaryEnc, Criterion, FloatEnc, IntEnc, Node, Polynomial, Spline, StringEnc, Value, VType

ENCODINGS = {
    "unsigned": "Unsigned",
    "twosComplement": "TwosComplement",
    "signMagnitude": "SignMagnitude",
    "onesComplement": "OnesComplement",
}


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


def raw_is_int(vt: VType) -> bool:
    return isinstance(vt.enc, IntEnc)


def invertible(vt: VType) -> bool:
    return vt.calibrator is None or vt.calibrator.invertible


def string_spec(vt: VType) -> tuple:
    e: StringEnc = vt.enc
    return e.fixed_bits, e.max_bits, e.terminator, e.size_tag_bits or 0


def str_cap(vt: VType) -> int:
    """The most bytes of content a string field (or a boolean encoded as
    a string) can hold."""
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
        return max(mx // 8 - 1, 0)
    return max(min((mx - tag) // 8, (1 << tag) - 1), 0)


def field_range(vt: VType) -> tuple[int, int]:
    """The smallest and largest value of an integer's generated field."""
    bits = vt.width
    return (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if vt.signed else (0, (1 << bits) - 1)


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
    """The notice at the top of every generated file, without comment markers."""
    return [
        f"@generated automatically by tcs-xtce from {source} (SpaceSystem {mission.name!r}).",
        "Any changes made to this file will be lost when it is regenerated;",
        "change the XTCE document instead and rerun `python3 -m tcs_xtce`.",
        f"tcs-xtce: {REPOSITORY}",
        f"Rust crate: {CRATE}",
    ]


__all__ = [
    "Criterion",
    "FloatEnc",
    "IntEnc",
    "Polynomial",
    "Spline",
    "StringEnc",
    "criteria_by_node",
    "field_range",
    "size_args",
    "header_lines",
    "flt",
    "int_enc",
    "invertible",
    "le",
    "raw_is_int",
    "str_cap",
    "string_spec",
    "values",
]
