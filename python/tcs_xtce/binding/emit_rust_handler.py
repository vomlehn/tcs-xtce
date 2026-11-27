"""Emit the Rust callback interface for reading XTCE: the ``handler``
module of the generated binding.

The reader calls a handler once for each element it reads, bottom-up: it
reads the element's attributes and children, calling the handler for each
child first, then calls the handler for the element with its parts, which
hold the attributes as values and what the handler made of each child.
What a handler makes of each element type is its own type, one associated
type of the ``Handler`` trait for each element type.  The ``Tree`` handler
makes the binding's structs, so reading a document into them is reading
it with ``Tree``.
"""

from __future__ import annotations

from . import docs
from .binding import (MANY, ONE, OPT, Binding, ChoiceType, ElemField, Record,
                      snake)
from .model import UNBOUNDED, ComplexRef, ScalarRef, TypeRef


def lit(s: str) -> str:
    """Gives a Rust string literal.

    Args:
        s: The literal's value.

    Returns:
        The literal.
    """
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def reachable(b: Binding) -> list[str]:
    """Lists the records that can occur in a document: the root's type and
    the types of the elements in them, in name order.

    Args:
        b: The binding.

    Returns:
        The records' names.
    """
    root = next(iter(b.schema.roots.values()))
    seen: set[str] = set()
    stack = [root]
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        for f in b.records[n].content:
            ts = ([f.type] if isinstance(f, ElemField)
                  else [v.type for v in f.choice.variants])
            stack += [t.name for t in ts if isinstance(t, ComplexRef)]
    return sorted(seen)


class HandlerEmitter:
    """Writes the ``handler`` module of the Rust binding.

    Args:
        b:      The binding.
        ident:  Gives the Rust identifier for a field name.
        scalar: Gives the Rust type of a scalar or enumerated value.
    """

    def __init__(self, b: Binding, ident, scalar):
        self.b = b
        self.ident = ident
        self.scalar = scalar
        self.out: list[str] = []
        self.records = reachable(b)
        names = [snake(r) for r in self.records]
        if len(set(names)) != len(names):
            raise ValueError("two element types have the same snake-case name, "
                             "so their callbacks would clash")
        # The choices that the reachable records hold.
        self.choices: list[str] = sorted({f.choice.name for r in self.records
                                          for f in b.records[r].content
                                          if not isinstance(f, ElemField)})
        # A choice's parts depend on the handler if an alternative is an
        # element of a complex type; a record's if it holds one, or such a
        # choice.
        self.generic_choices = {c for c in self.choices
                                if any(isinstance(v.type, ComplexRef)
                                       for v in b.choices[c].variants)}
        self.generic = {r for r in self.records
                        if any(self.generic_field(f)
                               for f in b.records[r].content)}

    def w(self, line: str = "") -> None:
        """Appends one line.

        Args:
            line: The line.
        """
        self.out.append(line)

    def generic_field(self, f) -> bool:
        """Says whether a field's part depends on the handler.

        Args:
            f: An ElemField or ChoiceField.

        Returns:
            Whether it does.
        """
        if isinstance(f, ElemField):
            return isinstance(f.type, ComplexRef)
        return f.choice.name in self.generic_choices

    def parts(self, name: str, generic: bool, h: str = "H") -> str:
        """Gives the type of the parts of a record or choice.

        Args:
            name:    The record's or choice's name.
            generic: Whether the parts depend on the handler.
            h:       The handler type.

        Returns:
            The Rust type.
        """
        return f"{name}Parts<{h}>" if generic else f"{name}Parts"

    def value(self, t: TypeRef, h: str = "H") -> str:
        """Gives the type of what the reader passes for one child element.

        Args:
            t: The element's type.
            h: The handler type.

        Returns:
            What the handler makes of it, for a complex type; the value,
            for a scalar or enumerated one.
        """
        if isinstance(t, ComplexRef):
            return f"{h}::{t.name}"
        return self.scalar(t)

    def emit(self) -> list[str]:
        """Writes the module.

        Returns:
            Its lines.
        """
        root = next(iter(self.b.schema.roots.values()))
        rtag = next(iter(self.b.schema.roots))
        self.w("/// Reading XTCE with callbacks. [`parse`] reads a document "
               "and calls a")
        self.w("/// [`Handler`] once for each element, bottom-up: each child "
               "before its")
        self.w("/// parent, whose callback gets the element's attributes and "
               "what the")
        self.w("/// handler made of each child. A handler chooses what it "
               "makes of each")
        self.w("/// element type, as the associated types of `Handler`; "
               "[`Tree`] makes")
        self.w("/// the binding's own structs.")
        self.w("pub mod handler {")
        self.w("    use super::*;")
        self.w("    use crate::xml::{Reader, Result, Tag};")
        self.w("")
        self.trait()
        self.parse(root, rtag)
        for name in self.records:
            self.parts_struct(self.b.records[name])
            self.read_fn(self.b.records[name])
        for name in self.choices:
            self.choice_parts(self.b.choices[name])
            self.choice_read(self.b.choices[name])
        self.tree()
        self.observer()
        self.w("}")
        return self.out

    # -- the trait ----------------------------------------------------------

    def trait(self) -> None:
        """Writes the ``Handler`` trait."""
        self.w("    /// What a reader calls for each element it reads. Each "
               "associated type")
        self.w("    /// is what the handler makes of one element type, and "
               "each method")
        self.w("    /// makes it from the element's parts.")
        self.w("    pub trait Handler {")
        for name in self.records:
            self.w("        /// What the handler makes of an element of type "
                   f"`{name}`.")
            self.w(f"        type {name};")
        for name in self.records:
            r = self.b.records[name]
            self.w("")
            self.out += docs.rust("Makes what the handler makes of an element "
                                  f"of type `{name}`, from its parts.",
                                  [("parts",
                                    "The element's attributes, text and what "
                                    "the handler made of each of its "
                                    "children.")],
                                  "What the handler makes of the element.",
                                  "An error the handler gives; reading the "
                                  "document stops with it.", indent="        ")
            self.w(f"        fn {snake(name)}(&mut self, parts: "
                   f"{self.parts(name, name in self.generic, 'Self')}) -> "
                   f"Result<Self::{name}>;")
        self.w("    }")
        self.w("")

    def parse(self, root: str, tag: str) -> None:
        """Writes ``parse``, which reads a document with a handler.

        Args:
            root: The root element's type.
            tag:  The root element's name.
        """
        self.out += docs.rust(
            "Reads an XTCE document, calling a handler for each element, and "
            f"gives what the handler makes of its root, the `{tag}` element.",
            [("bytes", "The XML text of the whole document."),
             ("h", "The handler.")], "What the handler makes of the root.",
            "An error if the XML is malformed, the root element is not "
            f"`{tag}`, an element breaks the schema, anything follows the "
            "root, or the handler gives one.", indent="    ")
        self.w("    pub fn parse<H: Handler + ?Sized>(bytes: &[u8], h: &mut "
               f"H) -> Result<H::{root}> {{")
        self.w("        let mut r = Reader::new(bytes)?;")
        self.w("        let root = r.root()?;")
        self.w(f"        if root.local_name() != {lit(tag)} {{")
        self.w("            return Err(crate::xml::Error::new(format!(")
        self.w(f"                \"root element is <{{}}>, expected <{tag}>\",")
        self.w("                root.name")
        self.w("            )));")
        self.w("        }")
        self.w(f"        let v = read_{snake(root)}(&mut r, &root, h)?;")
        self.w("        r.finish()?;")
        self.w("        Ok(v)")
        self.w("    }")
        self.w("")

    # -- records ------------------------------------------------------------

    def field_type(self, f, h: str = "H") -> str:
        """Gives the type of a field of a record's parts.

        Args:
            f: An ElemField or ChoiceField.
            h: The handler type.

        Returns:
            The Rust type: what the reader passes for one item, in an
            ``Option`` or ``Vec`` as the field's cardinality needs.
        """
        if isinstance(f, ElemField):
            t = self.value(f.type, h)
        else:
            t = self.parts(f.choice.name, f.choice.name in self.generic_choices,
                           h)
        if f.card == OPT:
            return f"Option<{t}>"
        if f.card == MANY:
            return f"Vec<{t}>"
        return t

    def parts_struct(self, r: Record) -> None:
        """Writes the parts of a record, what its callback gets.

        Args:
            r: The record.
        """
        g = r.name in self.generic
        self.w(f"    /// The parts of an element of type [`super::{r.name}`]: "
               "its attributes,")
        self.w("    /// its text, and what the handler made of each of its "
               "children.")
        if not g:
            self.w("    #[derive(Debug, Clone, PartialEq)]")
        bound = "<H: Handler + ?Sized>" if g else ""
        self.w(f"    pub struct {r.name}Parts{bound} {{")
        for a in r.attrs:
            self.w(f"        /// The `{a.xml}` "
                   f"attribute{'' if a.required else ', if given'}.")
            t = self.scalar(a.type)
            self.w(f"        pub {self.ident(a.ident)}: "
                   f"{t if a.required else f'Option<{t}>'},")
        if r.text is not None:
            self.w("        /// The text content.")
            self.w(f"        pub {self.ident(r.text.ident)}: "
                   f"{self.scalar(r.text.type)},")
        for f in r.content:
            if isinstance(f, ElemField):
                what = (f"what the handler made of each `{f.xml}` element"
                        if isinstance(f.type, ComplexRef)
                        else f"the value of each `{f.xml}` element")
            else:
                alts = ", ".join(f"`{v.xml}`" for v in f.choice.variants)
                what = f"each alternative of: {alts}"
            self.w(f"        /// {what[0].upper() + what[1:]}.")
            self.w(f"        pub {self.ident(f.ident)}: {self.field_type(f)},")
        self.w("    }")
        self.w("")

    def read_item(self, t: TypeRef) -> str:
        """Gives the expression that reads one child element.

        Args:
            t: The element's type.

        Returns:
            The expression, which calls the handler for a complex type.
        """
        if isinstance(t, ComplexRef):
            return (f"{{ let t = r.open()?; read_{snake(t.name)}(r, &t, h)? }}")
        return "r.read_leaf()?"

    def read_fn(self, r: Record) -> None:
        """Writes the function that reads an element of a record's type
        and calls the handler for it.

        Args:
            r: The record.
        """
        s = snake(r.name)
        self.out += docs.rust(
            f"Reads an element of type [`super::{r.name}`] whose start tag has "
            "just been read: its attributes, then its text or its children, "
            "calling the handler for each child and then for the element.",
            [("r",
              "The reader, just after the start tag; the end tag is read too."),
             ("tag", "The start tag."), ("h", "The handler.")],
            "What the handler makes of the element.",
            "An error if the XML is malformed, the element breaks the schema, "
            "or the handler gives one.", indent="    ")
        self.w(f"    pub fn read_{s}<'a, H: Handler + ?Sized>(")
        self.w("        r: &mut Reader<'a>,")
        self.w("        tag: &Tag<'a>,")
        self.w("        h: &mut H,")
        self.w(f"    ) -> Result<H::{r.name}> {{")
        for a in r.attrs:
            self.w(f"        let mut a_{a.ident}: Option"
                   f"<{self.scalar(a.type)}> = None;")
        self.w("        for a in tag.attrs() {")
        if r.attrs:
            self.w("            let (k, raw) = a?;")
            self.w("            match k {")
            for a in r.attrs:
                self.w(f"                {lit(a.xml)} => a_{a.ident} = "
                       "Some(r.attr_value(tag, raw)?),")
            self.w("                _ => r.unknown_attr(tag, k)?,")
            self.w("            }")
        else:
            self.w("            let (k, _) = a?;")
            self.w("            r.unknown_attr(tag, k)?;")
        self.w("        }")
        for a in r.attrs:
            if a.required:
                self.w(f"        let Some(a_{a.ident}) = a_{a.ident} else {{")
                self.w("            return Err(r.missing_attr(tag, "
                       f"{lit(a.xml)}));")
                self.w("        };")
        fields = [f"{self.ident(a.ident)}: a_{a.ident}" for a in r.attrs]
        if r.text is not None:
            self.w("        let text = r.read_value()?;")
            fields.append(f"{self.ident(r.text.ident)}: text")
        else:
            for f in r.content:
                self.read_field(f)
                fields.append(f"{self.ident(f.ident)}: f_{f.ident}")
            self.w("        r.close()?;")
        self.w(f"        h.{s}({r.name}Parts {{")
        for f in fields:
            self.w(f"            {f},")
        self.w("        })")
        self.w("    }")
        self.w("")

    def read_field(self, f) -> None:
        """Writes the statements that read the children of one field of a
        record, into the variable ``f_<field>``.

        Args:
            f: The ElemField or ChoiceField.
        """
        v = f"f_{f.ident}"
        if isinstance(f, ElemField):
            x = lit(f.xml)
            item = self.read_item(f.type)
            starts = f"r.peek_child()? == Some({x})"
            what = x
        else:
            c = f.choice.name
            item = f"read_{snake(c)}(r, h)?"
            starts = f"r.peek_child()?.is_some_and(super::{c}::accepts)"
            what = lit("|".join(a.xml for a in f.choice.variants))
        if f.card == ONE:
            if isinstance(f, ElemField):
                self.w(f"        if r.peek_child()? != Some({x}) {{")
                self.w(f"            return Err(r.expected({x}));")
                self.w("        }")
            self.w(f"        let {v} = {item};")
        elif f.card == OPT:
            self.w(f"        let {v} = if {starts} {{")
            self.w(f"            Some({item})")
            self.w("        } else {")
            self.w("            None")
            self.w("        };")
        else:
            self.w(f"        let mut {v} = Vec::new();")
            cond = starts
            if f.max != UNBOUNDED:
                cond = f"{v}.len() < {f.max} && {cond}"
            self.w(f"        while {cond} {{")
            self.w(f"            {v}.push({item});")
            self.w("        }")
            if f.min > 0:
                self.w(f"        if {v}.len() < {f.min} {{")
                self.w(f"            let n = {v}.len();")
                self.w(f"            return Err(r.too_few({what}, {f.min}, "
                       "n));")
                self.w("        }")

    # -- choices ------------------------------------------------------------

    def choice_parts(self, c: ChoiceType) -> None:
        """Writes the parts of a choice: the alternative read, with what
        the handler made of it.

        Args:
            c: The choice.
        """
        g = c.name in self.generic_choices
        self.w("    /// The alternative of a `<choice>` in "
               f"[`super::{c.owner}`] that was read,")
        self.w("    /// with what the handler made of its element.")
        if not g:
            self.w("    #[derive(Debug, Clone, PartialEq)]")
        bound = "<H: Handler + ?Sized>" if g else ""
        self.w(f"    pub enum {c.name}Parts{bound} {{")
        for v in c.variants:
            self.w(f"        /// A `{v.xml}` element.")
            self.w(f"        {v.ident}({self.value(v.type)}),")
        self.w("    }")
        self.w("")

    def choice_read(self, c: ChoiceType) -> None:
        """Writes the function that reads the alternative of a choice that
        the next child element starts.

        Args:
            c: The choice.
        """
        g = c.name in self.generic_choices
        alts = docs.choice_names(c, lambda s: f"`{s}`")
        self.out += docs.rust(
            f"Reads the alternative of a `<choice>` in [`super::{c.owner}`] "
            f"that the next child element starts, which must be {alts}, "
            "calling the handler for it.",
            [("r",
              "The reader, before the child element; it is left after the "
              "child's end tag."), ("h", "The handler.")],
            "The alternative read.",
            "An error if the next child is none of the alternatives, the XML "
            "is malformed, the element breaks the schema, or the handler gives "
            "one.", indent="    ")
        unused = "" if g else "_"
        self.w(f"    pub fn read_{snake(c.name)}<H: Handler + ?Sized>(")
        self.w("        r: &mut Reader<'_>,")
        self.w(f"        {unused}h: &mut H,")
        self.w(f"    ) -> Result<{self.parts(c.name, g)}> {{")
        self.w(f"        use {c.name}Parts as P;")
        self.w("        Ok(match r.peek_child()? {")
        for v in c.variants:
            self.w(f"            Some({lit(v.xml)}) => "
                   f"P::{v.ident}({self.read_item(v.type)}),")
        self.w("            _ => return "
               f"Err(r.expected({lit('|'.join(v.xml for v in c.variants))})),")
        self.w("        })")
        self.w("    }")
        self.w("")

    # -- the tree -----------------------------------------------------------

    def tree(self) -> None:
        """Writes ``Tree``, the handler that makes the binding's structs,
        and the conversions from parts to structs that it and
        ``Observing`` share."""
        eqs = ", ".join(f"{n} = super::{n}" for n in self.records)
        self.w("    /// A handler whose types are the binding's own structs, "
               "as those of")
        self.w("    /// [`Tree`] and [`Observing`] are.")
        self.w(f"    pub trait TreeTypes: Handler<{eqs}> {{}}")
        self.w("")
        self.w(f"    impl<H: Handler<{eqs}> + ?Sized> TreeTypes for H {{}}")
        self.w("")
        for name in self.records:
            self.tree_fn(self.b.records[name])
        for name in self.choices:
            self.choice_from(self.b.choices[name])
        self.w("    /// The handler that makes the binding's own structs: "
               "reading a")
        self.w("    /// document with it gives the tree that "
               "`XmlElement::read_xml` gives.")
        self.w("    #[derive(Debug, Clone, Copy, Default)]")
        self.w("    pub struct Tree;")
        self.w("")
        self.w("    impl Handler for Tree {")
        for name in self.records:
            self.w(f"        type {name} = super::{name};")
        for name in self.records:
            p = self.parts(name, name in self.generic, "Self")
            self.w("")
            self.w(f"        fn {snake(name)}(&mut self, parts: {p}) -> Result"
                   f"<super::{name}> {{")
            self.w(f"            Ok(tree_{snake(name)}(parts))")
            self.w("        }")
        self.w("    }")
        self.w("")

    def tree_fn(self, r: Record) -> None:
        """Writes the function that makes a record's struct from its parts,
        read with a handler whose types are the binding's structs.

        Args:
            r: The record.
        """
        g = r.name in self.generic
        self.out += docs.rust(f"Makes a [`super::{r.name}`] from its parts.",
                              [("parts",
                                "The parts, read with a handler whose types "
                                "are the binding's structs.")], "The struct.",
                              indent="    ")
        bound = "<H: TreeTypes + ?Sized>" if g else ""
        self.w(f"    pub fn tree_{snake(r.name)}{bound}(parts: "
               f"{self.parts(r.name, g)}) -> super::{r.name} {{")
        self.w(f"        super::{r.name} {{")
        for a in r.attrs:
            n = self.ident(a.ident)
            self.w(f"            {n}: parts.{n},")
        if r.text is not None:
            n = self.ident(r.text.ident)
            self.w(f"            {n}: parts.{n},")
        for f in r.content:
            n = self.ident(f.ident)
            self.w(f"            {n}: {self.convert(f, 'parts.' + n)},")
        self.w("        }")
        self.w("    }")
        self.w("")

    def convert(self, f, expr: str) -> str:
        """Gives the expression that turns a field of a record's parts,
        read with ``Tree``, into the record's field.

        Args:
            f:    The ElemField or ChoiceField.
            expr: The part's expression.

        Returns:
            The expression: boxed where the field is, and a choice's parts
            turned into the choice.
        """
        if isinstance(f, ElemField):
            if not f.boxed:
                return expr
            one, many = "Box::new({})", "Box::new"
        else:
            c = f"super::{f.choice.name}::from"
            if f.boxed:
                one, many = f"Box::new({c}({{}}))", f"|c| Box::new({c}(c))"
            else:
                one, many = f"{c}({{}})", c
        if f.card == ONE:
            return one.format(expr)
        if f.card == OPT:
            return f"{expr}.map({many})"
        return f"{expr}.into_iter().map({many}).collect()"

    def choice_from(self, c: ChoiceType) -> None:
        """Writes the conversion from a choice's parts, read with ``Tree``,
        to the choice.

        Args:
            c: The choice.
        """
        g = c.name in self.generic_choices
        p = self.parts(c.name, g)
        bound = "<H: TreeTypes + ?Sized>" if g else ""
        self.w(f"    impl{bound} From<{p}> for super::{c.name} {{")
        self.out += docs.rust("Turns the alternative read into the choice.",
                              [("p",
                                "The alternative, read with a handler whose "
                                "types are the binding's structs.")],
                              "The choice.", indent="        ")
        self.w(f"        fn from(p: {p}) -> Self {{")
        self.w(f"            use {c.name}Parts as P;")
        self.w("            match p {")
        for v in c.variants:
            val = "Box::new(v)" if v.boxed else "v"
            self.w(f"                P::{v.ident}(v) => "
                   f"Self::{v.ident}({val}),")
        self.w("            }")
        self.w("        }")
        self.w("    }")
        self.w("")

    # -- observing the tree -------------------------------------------------

    def observer(self) -> None:
        """Writes ``Observer``, callbacks with defaults that see each
        element of the tree as it is made, and ``Observing``, the handler
        that calls them."""
        self.w("    /// Callbacks that see each element as [`Tree`] makes it, "
               "children before")
        self.w("    /// their parents. Each does nothing unless overridden, so "
               "an observer")
        self.w("    /// implements only those it needs; [`Observing`] reads "
               "with one.")
        self.w("    pub trait Observer {")
        for name in self.records:
            self.w("")
            self.out += docs.rust(f"Sees an element of type `{name}`, just "
                                  "made.", [("v", "The element.")], None,
                                  "An error the observer gives; reading the "
                                  "document stops with it.", indent="        ")
            self.w(f"        fn {snake(name)}(&mut self, v: &super::{name}) -> "
                   "Result<()> {")
            self.w("            let _ = v;")
            self.w("            Ok(())")
            self.w("        }")
        self.w("    }")
        self.w("")
        self.w("    /// The handler that makes the tree, as [`Tree`] does, and "
               "shows each")
        self.w("    /// element to an [`Observer`] as it is made.")
        self.w("    #[derive(Debug, Clone, Default)]")
        self.w("    pub struct Observing<O: Observer>(")
        self.w("        /// The observer.")
        self.w("        pub O,")
        self.w("    );")
        self.w("")
        self.w("    impl<O: Observer> Handler for Observing<O> {")
        for name in self.records:
            self.w(f"        type {name} = super::{name};")
        for name in self.records:
            s = snake(name)
            p = self.parts(name, name in self.generic, "Self")
            self.w("")
            self.w(f"        fn {s}(&mut self, parts: {p}) -> Result"
                   f"<super::{name}> {{")
            self.w(f"            let v = tree_{s}(parts);")
            self.w(f"            self.0.{s}(&v)?;")
            self.w("            Ok(v)")
            self.w("        }")
        self.w("    }")
        self.w("")
