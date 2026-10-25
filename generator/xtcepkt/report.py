"""Size report: the bits each packet field uses on the wire, and the bits it
needs.

The report reads only the mission's XTCE document.  A field's need comes
from what the document says about its values:

* an integer needs the fewest bits that hold its ``ValidRange`` (for an
  argument, the union of its ``ValidRangeSet``) in the same encoding, or as
  unsigned when the range has no negative values;
* an enumeration needs the bits for its smallest and largest listed values;
* a boolean needs one bit;
* an array or repeated entry needs its most elements times the need of
  one element;
* an aggregate needs the sum of its members' needs.

Little-endian encodings must be whole bytes, so their need is rounded up.
Integers without a ``ValidRange``, floats, and binary fields have no stated
need.  Fixed-size strings are flagged because shorter strings are padded, and
bits skipped by ``LocationInContainerInBits`` are counted separately.  The
``sizeInBits`` and ``signed`` attributes of engineering types are only hints
in XTCE, so they are not used.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .compile import item_bits, max_bits
from .model import (AggregateEnc, ArrayEnc, BinaryEnc, Fixed, IntEnc, Mark,
                    Mission, Node, Pad, Seek, StringEnc, Value, VType, walk,
                    Fill)


def nbytes(n: int) -> str:
    return f"{n} byte" if n == 1 else f"{n} bytes"


def bits_for(lo: int, hi: int, encoding: str) -> Optional[int]:
    """Bits that hold every value in ``lo..hi`` in an integer encoding, or
    None if the encoding cannot hold ``lo`` (an unsigned one, below 0)."""
    if encoding == "unsigned":
        return max(hi.bit_length(), 1) if lo >= 0 else None
    if encoding in ("BCD", "packedBCD"):  # a decimal digit in 8 or 4 bits
        width = 8 if encoding == "BCD" else 4
        return width * max(len(str(hi)), 1) if lo >= 0 else None
    if encoding == "twosComplement":
        n = (max(hi.bit_length() if hi >= 0 else 0,
                 (-lo - 1).bit_length() if lo < 0 else 0) + 1)
    else:  # sign-magnitude and ones' complement: the same range either way
        n = max(abs(lo), abs(hi)).bit_length() + 1
    return max(n, 2)  # the compiler does not support 1-bit signed integers


@dataclass
class Row:
    name: str
    vtype: str
    bits: int
    need: Optional[int] = None
    note: str = ""
    # a constant: fixed by a criterion, or a FixedValueEntry
    fixed: bool = False

    @property
    def saving(self) -> int:
        return self.bits - self.need if self.need is not None else 0


def need(vt: VType) -> tuple[Optional[int], str]:
    """(bits the field needs, or None if the document doesn't say; why)."""
    e = vt.enc
    if isinstance(e, AggregateEnc):
        needs = [need(m.vtype)[0] for m in e.members]
        what = f"aggregate of {len(needs)} members"
        if None in needs:
            unknown = [m.name for m, n in zip(e.members, needs) if n is None]
            return None, f"{what}; no stated need for {', '.join(unknown)}"
        return sum(needs), what
    if isinstance(e, ArrayEnc):
        n, why = need(e.elem)
        count = e.max_count
        what = (f"{count} values" if isinstance(e.count, int)
                else f"up to {count} values (count from {e.count.source})")
        if n is None:
            return None, "; ".join(x for x in (what, why) if x)
        return n * count + e.max_gap * max(count - 1, 0), f"{what}, each {why}"
    if vt.dyn is not None:
        return None, f"size from {vt.dyn.source}, at most {nbytes(vt.dyn.max)}"
    if isinstance(e, BinaryEnc) and e.rest:
        return None, f"rest of the packet, at most {nbytes(e.bits // 8)}"
    if vt.kind == "int" and vt.calibrator is not None:
        return None, "calibrated, so its range does not bound the encoding"
    if vt.kind == "bool" and isinstance(e, StringEnc):
        return 1, "boolean, encoded as a string"
    if vt.kind == "bool":
        n, why = 1, "boolean"
    elif vt.kind in ("int", "enum"):
        if vt.kind == "int":
            if vt.valid_range is None:
                return None, "no ValidRange"
            lo, hi = vt.valid_range
            why = f"ValidRange {lo}..{hi}"
        else:
            if not vt.labels:
                return None, "no enumeration values"
            lo = min(lab.value for lab in vt.labels)
            hi = max(lab.max_value for lab in vt.labels)
            why = f"values {lo}..{hi}"
        n = bits_for(lo, hi, e.encoding)
        if n is None or n > e.bits:
            return None, f"{why} does not fit the encoding"
        if e.encoding not in ("unsigned", "BCD", "packedBCD") and lo >= 0:
            u = bits_for(lo, hi, "unsigned")
            if u < n:
                n, why = u, f"{why}, as unsigned"
    elif isinstance(e, StringEnc) and e.fixed_bits is not None:
        return None, (f"fixed size: strings shorter than {e.fixed_bits // 8} "
                      "bytes are padded")
    else:
        return None, ""
    if isinstance(e, IntEnc) and isinstance(e.order, tuple):
        n = (n + 7) // 8 * 8
        why += ", whole bytes (byte order list)"
    return min(n, e.bits), why


def rows(n: Node) -> list[Row]:
    out = []
    for it in walk(n.all_items()):
        if isinstance(it, Value):
            lc = it.local
            bits, (needs, why) = item_bits(lc.vtype), need(lc.vtype)
            if needs is not None and needs > bits:
                needs = bits
            fixed = lc in n.fixed
            if lc.optional:
                why = "; ".join(
                    x for x in (why, "only when its include condition holds")
                    if x)
            out.append(Row(lc.name, lc.vtype.key, bits, needs, why, fixed))
        elif isinstance(it, Fixed):
            out.append(Row("(FixedValueEntry)", "", it.bits, it.bits,
                           f"0x{it.value:x}", True))
    return out


def gap_bits(n: Node) -> int:
    """Bits inside the packet that no entry covers (left by location offsets
    and container padding)."""
    pos, marks, spans = 0, {0: 0}, []
    for it in walk(n.all_items()):
        if isinstance(it, Seek):
            pos = ((marks[it.from_start] if it.from_start is not None else pos)
                   + it.max_offset)
        elif isinstance(it, Mark):
            marks[it.id] = pos
        elif isinstance(it, Pad):
            pos += it.bits  # padding is not covered by an entry
        elif isinstance(it, Fill):
            pos = max(pos, marks[it.start] + it.max_size)
        else:
            size = (it.bits if not isinstance(it, Value) else 0 if it.assembled
                    else item_bits(it.local.vtype))
            spans.append((pos, pos + size))
            pos += size
    covered, end = 0, 0
    for a, b in sorted(spans):
        a = max(a, end)
        if b > a:
            covered += b - a
            end = b
    return max_bits(n.all_items()) - covered


def table(rs: list[Row]) -> list[str]:
    head = ("field", "type", "bits", "needs", "saves", "")
    body = [(r.name + (" =" if r.fixed else ""), r.vtype, str(r.bits),
             str(r.need) if r.need is not None else "-",
             str(r.saving) if r.saving else "", r.note) for r in rs]
    widths = [max(len(row[i]) for row in [head] + body) for i in range(5)]
    out = []
    for row in [head] + body:
        cells = [row[0].ljust(widths[0]), row[1].ljust(widths[1])]
        cells += [row[i].rjust(widths[i]) for i in (2, 3, 4)]
        note = "  " + row[5] if row[5] else ""
        out.append(("  " + "  ".join(cells) + note).rstrip())
    return out


def node_report(n: Node) -> list[str]:
    what = "container" if n.kind == "tm" else "meta-command"
    total = max_bits(n.all_items())
    rs = rows(n)
    saving = sum(r.saving for r in rs)
    gaps = gap_bits(n)
    out = [f"{n.key} ({what} {n.ident}): at most {total} bits, "
           f"{nbytes(n.max_bytes)}"]
    out += table(rs)
    if saving:
        smaller = total - saving
        out.append(f"  right-sized fields save {saving} bits: at most "
                   f"{smaller} bits, {nbytes((smaller + 7) // 8)}")
    else:
        out.append("  no field is larger than the document says it needs")
    if gaps:
        out.append(f"  {gaps} bits are skipped by location offsets or padding "
                   "and carry nothing")
    if any(isinstance(it, Seek) for it in walk(n.all_items())):
        out.append("  entries are placed with LocationInContainerInBits, so "
                   "savings assume the layout is repacked")
    return out


def summary(mission: Mission) -> list[str]:
    out = ["Summary"]
    shrink, unchecked = [], set()
    for n in mission.nodes:
        rs = rows(n)
        total = max_bits(n.all_items())
        smaller = (total - sum(r.saving for r in rs) + 7) // 8
        if smaller < n.max_bytes:
            shrink.append(f"  {n.key}: {n.max_bytes} -> {nbytes(smaller)}")
        unchecked |= {r.vtype for r in rs if r.note == "no ValidRange"}
    if shrink:
        out.append(f"{len(shrink)} of {len(mission.nodes)} packets would be "
                   "whole bytes smaller with right-sized fields:")
        out += shrink
    else:
        out.append(f"None of the {len(mission.nodes)} packets would be a whole "
                   "byte smaller with right-sized fields.")
    if unchecked:
        out.append(f"{len(unchecked)} integer types have no ValidRange, so the "
                   "report can't check them; giving them one would let it:")
        out.append("  " + ", ".join(sorted(unchecked)))
    return out


def report(mission: Mission) -> str:
    plural = "s" if len(mission.spaces) > 1 else ""
    spaces = ", ".join(repr(s) for s in mission.spaces)
    out = [f"Size report for SpaceSystem{plural} {spaces}", "",
           "bits: on the wire now; needs: what the document says the values "
           "need;",
           "'=' marks constants (fixed by a criterion or a FixedValueEntry)."]
    for n in mission.nodes:
        out.append("")
        out += node_report(n)
    out.append("")
    out += summary(mission)
    for msg in mission.skipped:
        out.append(f"not analysed (unsupported): {msg}")
    return "\n".join(out) + "\n"
