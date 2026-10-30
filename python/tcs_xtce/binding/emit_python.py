"""Emit Python dataclasses and (de)serialization functions."""

from __future__ import annotations

from . import docs, header_lines
import keyword

from .binding import (MANY, ONE, OPT, Binding, ChoiceType, ElemField, Record,
                      pascal_ident, snake)
from .model import (BOOL, DOUBLE, INT, LONG, STRING, USHORT, UNBOUNDED,
                    ComplexRef, EnumRef, ScalarRef, TypeRef)

PY_TYPES = {STRING: "str", BOOL: "bool", INT: "int", LONG: "int", USHORT: "int",
            DOUBLE: "float"}
PARSE = {STRING: "X.p_str", BOOL: "X.p_bool", INT: "X.p_i32", LONG: "X.p_i64",
         USHORT: "X.p_u16", DOUBLE: "X.p_double"}
FORMAT = {STRING: "", BOOL: "X.f_bool", INT: "X.f_int", LONG: "X.f_int",
          USHORT: "X.f_int", DOUBLE: "X.f_double"}
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


def parse_fn(t: TypeRef) -> str:
    """The function that parses the text of a value of simple type ``t``.
    """
    if isinstance(t, ScalarRef):
        return PARSE[t.kind]
    return f"_p_{t.name}"


def fmt(t: TypeRef, expr: str) -> str:
    """Formats a value as XML text.

    Args:
        t:    The value's simple type.
        expr: The Python expression for the value.

    Returns:
        The expression for its XML lexical form.
    """
    if isinstance(t, EnumRef):
        return f"{expr}.value"
    f = FORMAT[t.kind]
    return f"{f}({expr})" if f else expr


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
        # The types whose writers can fail because of a repeated element,
        # and those that hold a choice, whose writer fails if it names an
        # alternative it doesn't have.
        self.fails = docs.write_failures(b, False)
        self.choice_fails = docs.write_failures(b, True, counts=False)
        # The element names the schema gives each complex type.
        self.names = docs.element_names(b)

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
        self.w("from . import _xml as X")
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
        self.w("")
        self.w("# -- deserialization " + "-" * 58)
        for name in sorted(self.b.records):
            self.reader(self.b.records[name])
        for name in sorted(self.b.choices):
            self.choice_reader(self.b.choices[name])
        self.w("")
        self.w("# -- serialization " + "-" * 60)
        for name in sorted(self.b.records):
            self.writer(self.b.records[name])
        for name in sorted(self.b.choices):
            self.choice_writer(self.b.choices[name])
        self.w("")
        self.w("READERS = {")
        for name in sorted(self.b.records):
            self.w(f"    {name}: _r_{name},")
        self.w("}")
        self.w("WRITERS = {")
        for name in sorted(self.b.records):
            self.w(f"    {name}: _w_{name},")
        self.w("}")
        self.w("")
        names = (sorted(s.enums) + sorted(self.b.records)
                 + sorted(self.b.choices))
        self.w("__all__ = [")
        fixed = ["NAMESPACE", "SCHEMA_VERSION", "ROOT_ELEMENT", "READERS",
                 "WRITERS"]
        for n in fixed + names:
            self.w(f"    {n!r},")
        self.w("]")
        return "\n".join(self.out) + "\n"

    # -- types --------------------------------------------------------------

    def enum(self, name: str) -> None:
        """Writes the Enum for an enumerated simple type and its parser.

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
        self.w("")
        self.w("")
        self.w(f"_E_{name} = {{m.value.strip(): m for m in {name}}}")
        self.w("")
        self.w("")
        self.w(f"def _p_{name}(s: str) -> {name}:")
        self.out += docs.google(
            f"Parses the XML lexical value of a {code(name)}.",
            [("s", "The text; leading and trailing whitespace is ignored.")],
            f"The member whose value is ``s``.",
            [("ValueError",
              "If ``s`` is none of "
              f"{docs.series([code(v) for v in e.values])}.")], indent="    ")
        self.w("    try:")
        self.w(f"        return _E_{name}[s.strip()]")
        self.w("    except KeyError:")
        self.w(f'        raise ValueError(f"invalid {name} {{s!r}}") from None')

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

    # -- readers ------------------------------------------------------------

    def read_expr(self, t: TypeRef, node: str) -> str:
        """Gives the expression that reads a child element.

        Args:
            t:    The element's type.
            node: The expression for the element's node.

        Returns:
            The expression for the element's value.
        """
        if isinstance(t, ComplexRef):
            return f"_r_{t.name}({node})"
        return f"X.leaf({node}, {parse_fn(t)})"

    def reader(self, r: Record) -> None:
        """Writes a record's reader, ``_r_`` and the record's name: its
        attributes, then its text or its child elements, checked against
        the content model.

        Args:
            r: The record.
        """
        self.w("")
        self.w("")
        self.w(f"_A_{r.name} = "
               f"frozenset({{{', '.join(repr(a.xml) for a in r.attrs)}}})")
        self.w("")
        self.w("")
        self.w(f"def _r_{r.name}(n: X.Node) -> {r.name}:")
        what = docs.type_phrase(self.b, r, self.names[r.name], code)
        self.out += docs.google(f"Reads an XML element of {what}.",
                                [("n", "The element's parsed node.")],
                                f"The element's value, a new {code(r.name)}.",
                                [("X.XtceError",
                                  docs.sentence(docs.read_error(self.b, r, code,
                                                                False)))],
                                indent="    ")
        self.w(f"    X.check_attrs(n, _A_{r.name})")
        self.w(f"    v = {r.name}.__new__({r.name})")
        for a in r.attrs:
            fn = "req_attr" if a.required else "opt_attr"
            self.w(f"    v.{ident(a.ident)} = X.{fn}(n, {a.xml!r}, "
                   f"{parse_fn(a.type)})")
        if r.text is not None:
            self.w(f"    v.{ident(r.text.ident)} = X.value(n, X.text_of(n), "
                   f"{parse_fn(r.text.type)})")
            self.w("    return v")
            return
        self.w("    c = X.Cursor(n)")
        for f in r.content:
            name = ident(f.ident)
            if isinstance(f, ElemField):
                x = repr(f.xml)
                if f.card == ONE:
                    self.w(f"    if c.peek() != {x}:")
                    self.w(f"        raise c.expected({x})")
                    self.w(f"    v.{name} = "
                           f"{self.read_expr(f.type, 'c.take()')}")
                elif f.card == OPT:
                    self.w(f"    v.{name} = "
                           f"{self.read_expr(f.type, 'c.take()')} if c.peek() "
                           f"== {x} else None")
                else:
                    cond = f"c.peek() == {x}"
                    if f.max != UNBOUNDED:
                        cond = f"len(items) < {f.max} and {cond}"
                    self.w("    items = []")
                    self.w(f"    while {cond}:")
                    self.w("        items.append("
                           f"{self.read_expr(f.type, 'c.take()')})")
                    if f.min > 0:
                        self.w(f"    if len(items) < {f.min}:")
                        self.w(f"        raise c.too_few({x}, {f.min}, "
                               "len(items))")
                    self.w(f"    v.{name} = items")
            else:
                cn = f.choice.name
                if f.card == ONE:
                    self.w(f"    v.{name} = _r_{cn}(c)")
                elif f.card == OPT:
                    self.w(f"    v.{name} = _r_{cn}(c) if c.peek() in "
                           f"{cn}.NAMES else None")
                else:
                    cond = f"c.peek() in {cn}.NAMES"
                    if f.max != UNBOUNDED:
                        cond = f"len(items) < {f.max} and {cond}"
                    self.w("    items = []")
                    self.w(f"    while {cond}:")
                    self.w(f"        items.append(_r_{cn}(c))")
                    if f.min > 0:
                        what = repr("|".join(v.xml for v in f.choice.variants))
                        self.w(f"    if len(items) < {f.min}:")
                        self.w(f"        raise c.too_few({what}, {f.min}, "
                               "len(items))")
                    self.w(f"    v.{name} = items")
        self.w("    c.end()")
        self.w("    return v")

    def choice_reader(self, c: ChoiceType) -> None:
        """Writes a choice's reader, ``_r_`` and the choice's name, and the
        table of its alternatives' readers.

        Args:
            c: The choice.
        """
        self.w("")
        self.w("")
        self.w(f"_RC_{c.name} = {{")
        for v in c.variants:
            if isinstance(v.type, ComplexRef):
                self.w(f"    {v.xml!r}: _r_{v.type.name},")
            else:
                self.w(f"    {v.xml!r}: lambda n: X.leaf(n, "
                       f"{parse_fn(v.type)}),")
        self.w("}")
        self.w("")
        self.w("")
        self.w(f"def _r_{c.name}(c: X.Cursor) -> {c.name}:")
        names = docs.choice_names(c, code)
        self.out += docs.google("Reads the alternative of the ``<choice>`` in "
                                f"{code(c.owner)} that the cursor's next child "
                                f"element starts, which must be {names}.",
                                [("c",
                                  "The cursor over the parent's children; it "
                                  "moves past the child read.")],
                                f"The alternative read, a new {code(c.name)}.",
                                [("X.XtceError",
                                  "If there is no next child or it is none of "
                                  f"{docs.choice_names(c, code, 'and')}, or if "
                                  "the alternative's element is invalid (see "
                                  "the reader of its type).")], indent="    ")
        self.w("    k = c.peek()")
        self.w(f"    fn = _RC_{c.name}.get(k)")
        self.w("    if fn is None:")
        self.w("        raise "
               f"c.expected({'|'.join(v.xml for v in c.variants)!r})")
        self.w(f"    return {c.name}(k, fn(c.take()))")

    # -- writers ------------------------------------------------------------

    def write_stmt(self, t: TypeRef, xml: str, expr: str, ind: str) -> None:
        """Writes the statement that writes one child element.

        Args:
            t:    The element's type.
            xml:  Its name.
            expr: The Python expression for its value.
            ind:  The statement's indentation.
        """
        if isinstance(t, ComplexRef):
            self.w(f"{ind}_w_{t.name}(w, {expr}, {xml!r})")
        else:
            self.w(f"{ind}w.leaf({xml!r}, {fmt(t, expr)})")

    def count_check(self, r: Record, f, what: str) -> None:
        """Writes the check that a repeated field holds as many items as
        the schema allows, unless it allows any number.

        Args:
            r:    The record.
            f:    The repeated field, an ElemField or a ChoiceField.
            what: What the error message calls the field's items.
        """
        if f.min == 0 and f.max == UNBOUNDED:
            return
        self.w(f"    X.check_count(len(v.{ident(f.ident)}), {f.min}, {f.max}, "
               f"{r.name + '.' + what!r})")

    def write_raises(self, name: str, what: str) -> list[tuple[str, str]]:
        """Says how writing a record or choice fails.

        Args:
            name: The record's or choice's name.
            what: What the text calls the value being written.

        Returns:
            The Raises entries: none if writing it can't fail.
        """
        whys = []
        how = self.fails.get(name)
        if how:
            whys.append(docs.write_error(how, what))
        if name in self.b.choices:
            whys.append(f"if {what} names an alternative the choice doesn't "
                        "have")
        if name in self.b.records:
            inner = [f.type.name if isinstance(f, ElemField) else f.choice.name
                     for f in self.b.records[name].content
                     if not isinstance(f, ElemField)
                     or isinstance(f.type, ComplexRef)]
        else:
            inner = [v.type.name for v in self.b.choices[name].variants
                     if isinstance(v.type, ComplexRef)]
        if any(i in self.choice_fails for i in inner):
            whys.append(f"if a choice nested in {what} names an alternative it "
                        "doesn't have")
        if not whys:
            return []
        return [("X.XtceError", docs.sentence("; or ".join(whys)))]

    def writer(self, r: Record) -> None:
        """Writes a record's writer, ``_w_`` and the record's name: its
        attributes, its text and its child elements in schema order.

        Args:
            r: The record.
        """
        self.w("")
        self.w("")
        self.w(f"def _w_{r.name}(w: X.Writer, v: {r.name}, tag: str) -> None:")
        names = self.names[r.name]
        what = docs.type_phrase(self.b, r, names, code)
        self.out += docs.google(
            f"Writes a value of {what} as an XML element: its attributes, then "
            "its "
            + ("text" if r.text is not None
               else "child elements in schema order") + ".",
            [("w", "The writer that receives the XML."), ("v", "The value."),
             ("tag", docs.tag_param(names, code))],
            raises=self.write_raises(r.name, "``v``"), indent="    ")
        self.w("    w.start(tag)")
        for a in r.attrs:
            f = ident(a.ident)
            if a.required:
                self.w(f"    w.attr({a.xml!r}, {fmt(a.type, 'v.' + f)})")
            else:
                self.w(f"    if v.{f} is not None:")
                self.w(f"        w.attr({a.xml!r}, {fmt(a.type, 'v.' + f)})")
        if r.text is not None:
            self.w("    "
                   f"w.text({fmt(r.text.type, 'v.' + ident(r.text.ident))})")
        for f in r.content:
            name = ident(f.ident)
            if isinstance(f, ElemField):
                if f.card == ONE:
                    self.write_stmt(f.type, f.xml, f"v.{name}", "    ")
                elif f.card == OPT:
                    self.w(f"    if v.{name} is not None:")
                    self.write_stmt(f.type, f.xml, f"v.{name}", "        ")
                else:
                    self.count_check(r, f, f.xml)
                    self.w(f"    for e in v.{name}:")
                    self.write_stmt(f.type, f.xml, "e", "        ")
            else:
                cn = f.choice.name
                if f.card == ONE:
                    self.w(f"    _w_{cn}(w, v.{name})")
                elif f.card == OPT:
                    self.w(f"    if v.{name} is not None:")
                    self.w(f"        _w_{cn}(w, v.{name})")
                else:
                    self.count_check(r, f, cn)
                    self.w(f"    for e in v.{name}:")
                    self.w(f"        _w_{cn}(w, e)")
        self.w("    w.end(tag)")

    def choice_writer(self, c: ChoiceType) -> None:
        """Writes a choice's writer, ``_w_`` and the choice's name, and the
        table of its alternatives' writers.

        Args:
            c: The choice.
        """
        self.w("")
        self.w("")
        self.w(f"_WC_{c.name} = {{")
        for v in c.variants:
            if isinstance(v.type, ComplexRef):
                self.w(f"    {v.xml!r}: _w_{v.type.name},")
            else:
                self.w(f"    {v.xml!r}: lambda w, x, t: w.leaf(t, "
                       f"{fmt(v.type, 'x')}),")
        self.w("}")
        self.w("")
        self.w("")
        self.w(f"def _w_{c.name}(w: X.Writer, v: {c.name}) -> None:")
        self.out += docs.google(
            "Writes the alternative a value of the ``<choice>`` in "
            f"{code(c.owner)} holds as its element, "
            f"{docs.choice_names(c, code)}.",
            [("w", "The writer that receives the XML."),
             ("v", "The value; ``v.name`` names the alternative.")],
            raises=self.write_raises(c.name, "``v``"), indent="    ")
        self.w(f"    fn = _WC_{c.name}.get(v.name)")
        self.w("    if fn is None:")
        self.w(f'        raise X.XtceError(f"{c.name}: unknown alternative '
               '{v.name!r}")')
        self.w("    fn(w, v.value, v.name)")


def emit(b: Binding) -> str:
    """The Python module for binding ``b``, before it is wrapped."""
    return PythonEmitter(b).emit()
