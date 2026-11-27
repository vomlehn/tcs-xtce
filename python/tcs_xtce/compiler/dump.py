"""The resolved model of a mission, as canonical text.

``python3 -m tcs_xtce INPUT... OUTPUT --dump-model`` writes it.  The Rust
model, ``tcs-xtce-model``, writes the same text for the same documents, so
that the two can be compared line for line while the compiler moves to
Rust: see docs/rust-migration-design.rst.

The text has one value to a line, indented two spaces for each level:

- ``none``, ``true`` and ``false``; integers in decimal; floats as Python's
  ``repr`` writes them (``1.0``, ``1e+16``, ``nan``, ``inf``); strings in
  double quotes, with ``\\``, ``"``, newline, carriage return and tab
  escaped as in C and any other control character as ``\\xHH``.
- A record (a model class) is its class name and ``{``, then each field
  as ``name: value`` in the order the class declares them, then ``}``.
- A list or tuple is ``[``, each element, then ``]``; ``[]`` when empty.
- A dictionary is ``{``, each entry as ``key => value`` in its order, then
  ``}``; ``{}`` when empty.
- A record reached more than once is numbered where it is first written,
  ``Node #3 {``, and written as ``Node #3`` wherever it is reached again,
  so that sharing is shown and the links between nodes don't loop.

Numbers are given in the order records are first written, walking the
mission's fields in order and each record's fields depth first.
"""

from __future__ import annotations

import dataclasses
import math

from tcs_xtce.compiler.model import Mission


def string(s: str) -> str:
    """Writes a string as the canonical text does.

    Args:
        s: The string.

    Returns:
        It in double quotes, escaped.
    """
    out = ['"']
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\x{ord(ch):02x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def scalar(v) -> str | None:
    """Writes a value that takes no lines of its own.

    Args:
        v: The value.

    Returns:
        Its text, or None if it is a record, list, tuple or dictionary.
    """
    if v is None:
        return "none"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v):
            return "nan"
        return repr(v)
    if isinstance(v, str):
        return string(v)
    if isinstance(v, (bytes, bytearray)):
        return "bytes " + (v.hex() if v else "(empty)")
    return None


class Dumper:
    """Writes a mission's model as canonical text.

    Attributes:
        seen:   How many times each record is reached, by its id(), from
                the first pass.
        number: The number given to each shared record written so far,
                by its id().
        lines:  The text so far, one line to an element.
    """

    def __init__(self) -> None:
        """Makes a dumper with nothing counted or written yet."""
        self.seen: dict[int, int] = {}
        self.number: dict[int, int] = {}
        self.lines: list[str] = []

    def count(self, v) -> None:
        """Counts how often each record is reached from a value.

        Args:
            v: The value, walked as dumping walks it.
        """
        if dataclasses.is_dataclass(v) and not isinstance(v, type):
            n = self.seen.get(id(v), 0)
            self.seen[id(v)] = n + 1
            if n:
                return
            for f in dataclasses.fields(v):
                self.count(getattr(v, f.name))
        elif isinstance(v, (list, tuple)):
            for x in v:
                self.count(x)
        elif isinstance(v, dict):
            for k, x in v.items():
                self.count(k)
                self.count(x)

    def put(self, depth: int, head: str, v) -> None:
        """Writes a value on lines of its own.

        Args:
            depth: Its indentation, in levels.
            head:  What goes before it on its first line: a field name and
                   ``: ``, a key and `` => ``, or nothing.
            v:     The value.

        Raises:
            TypeError: If the value is of a kind the text has no form for,
                       so that nothing in the model is left out unseen.
        """
        pad = "  " * depth
        s = scalar(v)
        if s is not None:
            self.lines.append(f"{pad}{head}{s}")
            return
        if dataclasses.is_dataclass(v) and not isinstance(v, type):
            name = type(v).__name__
            if id(v) in self.number:
                self.lines.append(f"{pad}{head}{name} #{self.number[id(v)]}")
                return
            if self.seen.get(id(v), 0) > 1:
                self.number[id(v)] = len(self.number) + 1
                name += f" #{self.number[id(v)]}"
            self.lines.append(f"{pad}{head}{name} {{")
            for f in dataclasses.fields(v):
                self.put(depth + 1, f"{f.name}: ", getattr(v, f.name))
            self.lines.append(f"{pad}}}")
        elif isinstance(v, (list, tuple)):
            if not v:
                self.lines.append(f"{pad}{head}[]")
                return
            self.lines.append(f"{pad}{head}[")
            for x in v:
                self.put(depth + 1, "", x)
            self.lines.append(f"{pad}]")
        elif isinstance(v, dict):
            if not v:
                self.lines.append(f"{pad}{head}{{}}")
                return
            self.lines.append(f"{pad}{head}{{")
            for k, x in v.items():
                self.put(depth + 1, f"{self.key(k)} => ", x)
            self.lines.append(f"{pad}}}")
        else:
            raise TypeError("the model dump has no form for "
                            f"{type(v).__name__}: {v!r}")

    def key(self, k) -> str:
        """Writes a dictionary key, which must fit on one line.

        Args:
            k: The key: a scalar, or a record reached elsewhere first.

        Returns:
            Its text.

        Raises:
            TypeError: If the key is a record not yet written, or of a
                       kind the text has no one-line form for.
        """
        s = scalar(k)
        if s is not None:
            return s
        if dataclasses.is_dataclass(k) and id(k) in self.number:
            return f"{type(k).__name__} #{self.number[id(k)]}"
        if isinstance(k, tuple) and all(scalar(x) is not None for x in k):
            return "(" + ", ".join(scalar(x) for x in k) + ")"
        raise TypeError("the model dump has no one-line form for the key "
                        f"{type(k).__name__}: {k!r}")


def dump(mission: Mission) -> str:
    """Writes a mission's resolved model as canonical text.

    Args:
        mission: The model, as the compiler resolved it.

    Returns:
        The text, ending in a newline.
    """
    d = Dumper()
    d.count(mission)
    d.put(0, "", mission)
    return "\n".join(d.lines) + "\n"
