"""Emit the Python binding's dataclasses and enumerations.

The tcs-xtce crate reads and writes the XML, through the extension module
``tcs_xtce._native``; this module only defines the classes the objects it
reads are of (see ``emit_pyo3``).
"""

from __future__ import annotations

from . import header_lines
import keyword

from .binding import (MANY, ONE, OPT, Binding, ChoiceType, ElemField, Record,
                      pascal_ident, snake)
from .model import (BOOL, DOUBLE, INT, LONG, STRING, USHORT, ComplexRef,
                    EnumRef, ScalarRef, TypeRef)

PY_TYPES = {STRING: "str", BOOL: "bool", INT: "int", LONG: "int", USHORT: "int",
            DOUBLE: "float"}
ZERO = {STRING: '""', BOOL: "False", INT: "0", LONG: "0", USHORT: "0",
        DOUBLE: "0.0"}


def ident(name: str) -> str:
    """The Python identifier for a name: the name, with ``_`` after a
    keyword, ``self`` or ``cls``."""
    return (name + "_" if keyword.iskeyword(name) or name in ("self", "cls")
            else name)


def code(s: str) -> str:
    """A name quoted as code in a docstring: in double backticks."""
    return f"``{s}``"


def doc(text: str, indent: str) -> list[str]:
    """Makes a one-line docstring.

    Args:
        text:   What the docstring says.
        indent: Its indentation.

    Returns:
        The docstring's line, or no lines if ``text`` is empty.
    """
    if not text:
        return []
    text = text.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    return [f'{indent}"""{text}"""']


def py_type(t: TypeRef) -> str:
    """The Python type of a value of schema type ``t``."""
    return PY_TYPES[t.kind] if isinstance(t, ScalarRef) else t.name


def member_names(values: list[str]) -> list[str]:
    """Names the members of an enumerated type.

    Args:
        values: The type's values, in schema order.

    Returns:
        An upper-case name for each value, unique within the type.
    """
    out, used = [], set()
    for v in values:
        base = snake(pascal_ident(v)).upper() or "EMPTY"
        if base[0].isdigit():
            base = "V" + base
        name, n = base, 2
        while name in used:
            name = f"{base}_{n}"
            n += 1
        used.add(name)
        out.append(name)
    return out


class PythonEmitter:
    """Writes the Python module for a binding.

    Args:
        b: The binding to write.
    """

    def __init__(self, b: Binding):
        self.b = b
        self.out: list[str] = []
        self.enum_members = {n: member_names(e.values)
                             for n, e in b.schema.enums.items()}

    def w(self, line: str = "") -> None:
        """Appends one line to the module."""
        self.out.append(line)

    def emit(self) -> str:
        """Writes the whole module.

        Returns:
            The module's text, before it is wrapped and laid out.
        """
        s = self.b.schema
        root_tag, root_type = next(iter(s.roots.items()))
        for line in header_lines():
            self.w("# " + line)
        self.w(f'"""Data structures for XTCE {s.version} '
               f'(``{s.namespace}``)."""')
        self.w("")
        self.w("from __future__ import annotations")
        self.w("")
        self.w("import enum")
        self.w("from dataclasses import dataclass, field")
        self.w("from typing import Any, ClassVar, Optional")
        self.w("")
        self.w(f"NAMESPACE = {s.namespace!r}")
        self.w(f"SCHEMA_VERSION = {s.version!r}")
        self.w(f"ROOT_ELEMENT = {root_tag!r}")
        self.w(f"ROOT_TYPE_NAME = {root_type!r}")
        self.w("")
        for name in sorted(s.enums):
            self.enum(name)
        for name in sorted(self.b.records):
            self.record(self.b.records[name])
        for name in sorted(self.b.choices):
            self.choice(self.b.choices[name])
        names = (sorted(s.enums) + sorted(self.b.records)
                 + sorted(self.b.choices))
        self.w("__all__ = [")
        fixed = ["NAMESPACE", "SCHEMA_VERSION", "ROOT_ELEMENT"]
        for n in fixed + names:
            self.w(f"    {n!r},")
        self.w("]")
        return "\n".join(self.out) + "\n"

    # -- types --------------------------------------------------------------

    def enum(self, name: str) -> None:
        """Writes the Enum for an enumerated simple type.

        Args:
            name: The simple type's name.
        """
        e = self.b.schema.enums[name]
        self.w("")
        self.w("")
        self.w(f"class {name}(str, enum.Enum):")
        self.out += doc(e.doc, "    ") or ['    """XTCE enumeration."""']
        self.w("")
        for m, v in zip(self.enum_members[name], e.values):
            self.w(f"    {m} = {v!r}")

    def default(self, t: TypeRef, card: str) -> str:
        """Gives a dataclass field's default.

        Args:
            t:    The field's type; unused for OPT and MANY fields.
            card: The field's cardinality: ONE, OPT or MANY.

        Returns:
            The default's expression: an empty list, None, a zero value,
            an enumeration's first member or a new default instance.
        """
        if card == MANY:
            return "field(default_factory=list)"
        if card == OPT:
            return "None"
        if isinstance(t, ScalarRef):
            return ZERO[t.kind]
        if isinstance(t, EnumRef):
            return f"{t.name}.{self.enum_members[t.name][0]}"
        return f"field(default_factory=lambda: {t.name}())"

    def annotation(self, t: str, card: str) -> str:
        """Gives a dataclass field's annotation.

        Args:
            t:    The Python type of one value.
            card: The field's cardinality: ONE, OPT or MANY.

        Returns:
            ``t``, in ``Optional`` or ``list`` as needed.
        """
        if card == OPT:
            return f"Optional[{t}]"
        if card == MANY:
            return f"list[{t}]"
        return t

    def record(self, r: Record) -> None:
        """Writes the dataclass for a complex type.

        Args:
            r: The complex type's record.
        """
        self.w("")
        self.w("")
        self.w("@dataclass(slots=True)")
        self.w(f"class {r.name}:")
        self.out += doc(r.doc, "    ") or ['    """XTCE type."""']
        lines = []
        for a in r.attrs:
            card = ONE if a.required else OPT
            note = (f"  # attribute {a.xml!r}"
                    + (f", schema default {a.default!r}"
                       if a.default is not None else ""))
            lines.append(f"    {ident(a.ident)}: "
                         f"{self.annotation(py_type(a.type), card)} = "
                         f"{self.default(a.type, card)}{note}")
        if r.text is not None:
            lines.append(f"    {ident(r.text.ident)}: {py_type(r.text.type)} = "
                         f"{ZERO[r.text.type.kind]}  # text content")
        for f in r.content:
            if isinstance(f, ElemField):
                lines.append(f"    {ident(f.ident)}: "
                             f"{self.annotation(py_type(f.type), f.card)} = "
                             f"{self.default(f.type, f.card)}  # element "
                             f"{f.xml!r}")
            else:
                c = f.choice
                if f.card == ONE:
                    dflt = f"field(default_factory=lambda: {c.name}())"
                else:
                    dflt = self.default(None, f.card)
                lines.append(f"    {ident(f.ident)}: "
                             f"{self.annotation(c.name, f.card)} = {dflt}")
        if lines:
            self.w("")
            self.out += lines

    def choice(self, c: ChoiceType) -> None:
        """Writes the dataclass for a ``<choice>``, with a constructor for
        each alternative.

        Args:
            c: The choice.
        """
        d = c.variants[c.default_variant]
        self.w("")
        self.w("")
        self.w("@dataclass(slots=True)")
        self.w(f"class {c.name}:")
        self.w('    """One alternative of a ``<choice>`` in '
               f':class:`{c.owner}`.')
        self.w("")
        self.w("    ``name`` is the element name of the alternative and "
               "``value`` its value;")
        self.w("    use the class-method constructors to build one.")
        self.w('    """')
        self.w("")
        self.w(f"    name: str = {d.xml!r}")
        if isinstance(d.type, ComplexRef):
            self.w("    value: Any = field(default_factory=lambda: "
                   f"{d.type.name}())")
        else:
            self.w(f"    value: Any = {self.default(d.type, ONE)}")
        self.w("    NAMES: ClassVar[frozenset[str]] = "
               f"frozenset({{{', '.join(repr(v.xml) for v in c.variants)}}})")
        used = set()
        for v in c.variants:
            m = ident(v.snake)
            if m in used or m in ("name", "value"):
                m += "_"
            used.add(m)
            self.w("")
            self.w("    @classmethod")
            self.w(f"    def {m}(cls, value: {py_type(v.type)}) -> {c.name}:")
            self.w(f'        """Builds the ``{v.xml}`` alternative."""')
            self.w(f"        return cls({v.xml!r}, value)")

def emit(b: Binding) -> str:
    """The Python module for binding ``b``, before it is wrapped."""
    return PythonEmitter(b).emit()
