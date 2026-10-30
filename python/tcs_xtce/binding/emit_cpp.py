"""Emit C++ data structures (header) and (de)serialization code (source)."""

from __future__ import annotations

from . import docs, header_lines
from .binding import (MANY, ONE, OPT, Binding, ChoiceType, ElemField, Record,
                      pascal_ident)
from .model import (BOOL, DOUBLE, INT, LONG, STRING, USHORT, UNBOUNDED,
                    ComplexRef, EnumRef, ScalarRef, TypeRef)

SCALARS = {STRING: "std::string", BOOL: "bool", INT: "std::int32_t",
           LONG: "std::int64_t", USHORT: "std::uint16_t", DOUBLE: "double"}
ZERO = {BOOL: " = false", INT: " = 0", LONG: " = 0", USHORT: " = 0",
        DOUBLE: " = 0.0", STRING: ""}

KEYWORDS = set(
    "alignas alignof and and_eq asm auto bitand bitor bool break case catch "
    "char char8_t char16_t char32_t class compl concept const consteval "
    "constexpr constinit const_cast continue co_await co_return co_yield "
    "decltype default delete do double dynamic_cast else enum explicit export "
    "extern false float for friend goto if inline int long mutable namespace "
    "new noexcept not not_eq nullptr operator or or_eq private protected "
    "public register reinterpret_cast requires return short signed sizeof "
    "static static_assert static_cast struct switch template this thread_local "
    "throw true try typedef typeid typename union unsigned using virtual void "
    "volatile wchar_t while xor xor_eq read_xml write_xml".split())
# Names that commonly collide with C/POSIX macros.
MACROS = {"EOF", "NULL", "TRUE", "FALSE", "DOMAIN", "OVERFLOW", "UNDERFLOW",
          "ERROR", "DEBUG", "IN", "OUT"}


def ident(name: str) -> str:
    """The C++ identifier for a name: the name, with ``_`` after a keyword.
    """
    return name + "_" if name in KEYWORDS else name


def code(s: str) -> str:
    """A name quoted as code in a Doxygen comment: in backticks."""
    return f"`{s}`"


def lit(s: str) -> str:
    """A C++ string literal whose value is ``s``."""
    out = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{out}"'


def comment(text: str, indent: str) -> list[str]:
    """Makes a one-line ``//`` comment.

    Args:
        text:   What the comment says.
        indent: The comment's indentation.

    Returns:
        The comment's line, or no lines if ``text`` is empty.
    """
    if not text:
        return []
    # Keep each comment on one line; avoid accidentally closing anything.
    return [f"{indent}// {text}"]


def base_type(t: TypeRef) -> str:
    """The C++ type of a value of schema type ``t``, before boxing."""
    return SCALARS[t.kind] if isinstance(t, ScalarRef) else t.name


def field_type(t: str, card: str, boxed: bool) -> str:
    """Gives the type of a struct member.

    Args:
        t:     The C++ type of one value.
        card:  The member's cardinality: ONE, OPT or MANY.
        boxed: Whether each value is boxed, to break a type cycle.

    Returns:
        ``t``, boxed and wrapped in ``std::optional`` or ``std::vector``
        as needed.
    """
    if boxed:
        t = f"Box<{t}>"
    if card == OPT:
        return f"std::optional<{t}>"
    if card == MANY:
        return f"std::vector<{t}>"
    return t


def enum_idents(values: list[str]) -> list[str]:
    """Names the enumerators of an enumerated type.

    Args:
        values: The type's values, in schema order.

    Returns:
        An identifier for each value, unique within the type, with ``k``
        before one that would collide with a common C or POSIX macro.
    """
    out, used = [], set()
    for v in values:
        base = pascal_ident(v)
        if (base in MACROS or base.upper() == base and len(base) > 1
                and base.upper() in MACROS):
            base = "k" + base
        i, n = base, 2
        while i in used:
            i = f"{base}{n}"
            n += 1
        used.add(i)
        out.append(i)
    return out


class CppEmitter:
    """Writes the C++ header and source for a binding.

    Args:
        b: The binding to write.
    """

    def __init__(self, b: Binding):
        self.b = b
        self.h: list[str] = []
        self.c: list[str] = []
        self.enum_ids = {n: enum_idents(e.values)
                         for n, e in b.schema.enums.items()}
        # The types whose writers can fail, and why.
        self.fails = docs.write_failures(b, False)
        # The element names the schema gives each complex type.
        self.names = docs.element_names(b)

    def emit(self) -> tuple[str, str]:
        """Writes the header and the source.

        Returns:
            The text of ``generated.hpp`` and of ``generated.cpp``.
        """
        s = self.b.schema
        root_tag, root_type = next(iter(s.roots.items()))
        h = self.h
        header = ["// " + line for line in header_lines()]
        h += header + [
            f"// Data structures for XTCE {s.version} ({s.namespace}).",
            "#pragma once", "", "#include <cstdint>", "#include <optional>",
            "#include <string>", "#include <string_view>", "#include <variant>",
            "#include <vector>", "", '#include "xtce/xml.hpp"', "",
            "namespace xtce {", "",
            "// XML namespace of the XTCE schema these types were generated "
            "from.",
            "inline constexpr std::string_view kNamespace = "
            f"{lit(s.namespace)};",
            "// XTCE version of the schema these types were generated from.",
            "inline constexpr std::string_view kSchemaVersion = "
            f"{lit(s.version)};", "// Name of the document root element.",
            "inline constexpr std::string_view kRootElement = "
            f"{lit(root_tag)};", ""]
        self.c += header + ['#include "xtce/generated.hpp"', "",
                            "namespace xtce {", ""]
        for name in sorted(s.enums):
            self.enum(name)
        h.append("// Forward declarations.")
        for name in sorted(self.b.records) + sorted(self.b.choices):
            h.append(f"struct {name};")
        h.append("")
        for name in self.b.order:
            if name in self.b.records:
                self.record(self.b.records[name])
            else:
                self.choice(self.b.choices[name])
        h += [f"// The type of the document root element <{root_tag}>.",
              f"using RootType = {root_type};", "", "}  // namespace xtce"]
        self.c.append("}  // namespace xtce")
        return "\n".join(self.h) + "\n", "\n".join(self.c) + "\n"

    # -- enumerations -------------------------------------------------------

    def enum(self, name: str) -> None:
        """Writes the enum class for an enumerated simple type, and the
        functions that format and parse its values.

        Args:
            name: The simple type's name.
        """
        e = self.b.schema.enums[name]
        ids = self.enum_ids[name]
        h, c = self.h, self.c
        values = docs.series([code(v) for v in e.values], "or")
        to_string = docs.doxygen(f"Gives the XML lexical value of a `{name}`, "
                                 "spelled as the schema spells it.",
                                 [("v", "The value.")],
                                 f"The value, {values}, as a static string; an "
                                 "empty string if `v` holds no enumerator.")
        parse = docs.doxygen(
            f"Parses the XML lexical value of a `{name}`.",
            [("s", "The text; leading and trailing XML whitespace is ignored."),
             ("v", "Receives the value.  It is left alone if `s` is invalid.")],
            throws=[("std::invalid_argument",
                     "If `s` is none of "
                     f"{docs.series([code(v) for v in e.values])}.")])
        h += comment(e.doc, "")
        h.append(f"enum class {name} {{")
        for i, v in zip(ids, e.values):
            h.append(f"        {i},  // {v}")
        h.append("};")
        h += to_string
        h.append(f"const char* to_string({name} v);")
        h += docs.doxygen(
            f"Appends the XML lexical value of a `{name}` to a string.",
            [("out", "The string to append to."), ("v", "The value.")])
        h.append(f"void format_value(std::string& out, {name} v);")
        h += parse
        h.append(f"void parse_value(std::string_view s, {name}& v);")
        h.append("")
        c.append(f"const char* to_string({name} v) {{")
        c.append("        switch (v) {")
        for i, v in zip(ids, e.values):
            c.append(f"                case {name}::{i}:")
            c.append(f"                        return {lit(v)};")
        c.append("        }")
        c.append('        return "";')
        c.append("}")
        c.append("")
        c.append(f"void format_value(std::string& out, {name} v) {{ out += "
                 "to_string(v); }")
        c.append("")
        c.append(f"void parse_value(std::string_view s, {name}& v) {{")
        c.append("        std::string_view t = trim(s);")
        for i, v in zip(ids, e.values):
            c.append(f"        if (t == {lit(v.strip())}) {{")
            c.append(f"                v = {name}::{i};")
            c.append("                return;")
            c.append("        }")
        c.append(f'        throw std::invalid_argument("invalid {name} \\"" + '
                 'std::string(s) + "\\"");')
        c.append("}")
        c.append("")

    # -- records ------------------------------------------------------------

    def record(self, r: Record) -> None:
        """Writes the struct for a complex type and the definitions of its
        member functions.

        Args:
            r: The complex type's record.
        """
        h = self.h
        h += comment(r.doc, "")
        h.append(f"struct {r.name} {{")
        for a in r.attrs:
            h += comment(a.doc or f"Attribute `{a.xml}`.", "        ")
            if a.default is not None:
                h.append(f"        // Schema default: `{a.default}`.")
            t = base_type(a.type)
            if a.required:
                init = (ZERO[a.type.kind] if isinstance(a.type, ScalarRef)
                        else self.enum_init(a.type))
                h.append(f"        {t} {ident(a.ident)}{init};")
            else:
                h.append(f"        std::optional<{t}> {ident(a.ident)};")
        if r.text is not None:
            h.append("        // Text content.")
            h.append(f"        {base_type(r.text.type)} "
                     f"{ident(r.text.ident)}{ZERO[r.text.type.kind]};")
        for f in r.content:
            if isinstance(f, ElemField):
                h += comment(f.doc or f"Element `{f.xml}`.", "        ")
                init = ""
                if f.card == ONE and not f.boxed:
                    if isinstance(f.type, ScalarRef):
                        init = ZERO[f.type.kind]
                    elif isinstance(f.type, EnumRef):
                        init = self.enum_init(f.type)
                h.append("        "
                         f"{field_type(base_type(f.type), f.card, f.boxed)} "
                         f"{ident(f.ident)}{init};")
            else:
                h.append("        // One of: "
                         f"{', '.join(v.xml for v in f.choice.variants)}.")
                h.append("        "
                         f"{field_type(f.choice.name, f.card, f.boxed)} "
                         f"{ident(f.ident)};")
        h.append("")
        ind = "        "
        names = self.names[r.name]
        what = docs.type_phrase(self.b, r, names, code)
        how = self.fails.get(r.name)
        h += docs.doxygen(
            f"Writes this value, of {what}, as an XML element: its attributes, "
            "then its "
            + ("text" if r.text is not None
               else "child elements in schema order") + ".",
            [("w", "The writer that receives the XML."),
             ("tag", docs.tag_param(names, code))],
            throws=[("Error",
                     docs.sentence(docs.write_error(how, "this value")))] if how
            else (), indent=ind)
        h.append("        void write_xml(Writer& w, std::string_view tag) "
                 "const;")
        err = docs.read_error(self.b, r, code, True)
        h += docs.doxygen(
            f"Reads an XML element of {what}, whose start tag the reader has "
            "just consumed, up to and including its end tag.",
            [("r",
              "The reader, positioned just after the start tag; it is left "
              "after the end tag."),
             ("tag", "The element's start tag, which holds its attributes.")],
            "The element's value.", [("Error", docs.sentence(err))], indent=ind)
        h.append(f"        static {r.name} read_xml(Reader& r, const Tag& "
                 "tag);")
        h.append(f"        bool operator==(const {r.name}&) const = default;")
        h.append("};")
        h.append("")
        self.write_fn(r)
        self.read_fn(r)

    def enum_init(self, t: EnumRef) -> str:
        """The initializer of a member of enumerated type ``t``: its first
        enumerator."""
        return f" = {t.name}::{self.enum_ids[t.name][0]}"

    def write_value(self, t: TypeRef, xml: str, expr: str, ind: str) -> None:
        """Writes the statement that writes one child element.

        Args:
            t:    The element's type.
            xml:  Its name.
            expr: The C++ expression for its value.
            ind:  The statement's indentation.
        """
        if isinstance(t, ComplexRef):
            self.c.append(f"{ind}{expr}.write_xml(w, {lit(xml)});")
        else:
            self.c.append(f"{ind}w.leaf({lit(xml)}, {expr});")

    def count_check(self, r: Record, f, what: str) -> None:
        """Writes the check that a repeated member holds as many items as
        the schema allows, unless it allows any number.

        Args:
            r:    The record.
            f:    The repeated member, an ElemField or a ChoiceField.
            what: What the error message calls the member's items.
        """
        if f.min == 0 and f.max == UNBOUNDED:
            return
        self.c.append(f"        check_count(v.{ident(f.ident)}.size(), "
                      f"{f.min}, {f.max}, {lit(r.name + '.' + what)});")

    def write_fn(self, r: Record) -> None:
        """Writes the definition of a record's ``write_xml``: its
        attributes, its text and its child elements in schema order.

        Args:
            r: The record.
        """
        c = self.c
        c.append(f"void {r.name}::write_xml(Writer& w, std::string_view tag) "
                 "const {")
        if r.content:
            c.append("        const auto& v = *this;")
        c.append("        w.start(tag);")
        for a in r.attrs:
            f = ident(a.ident)
            if a.required:
                c.append(f"        w.attr({lit(a.xml)}, {f});")
            else:
                c.append(f"        if ({f}) w.attr({lit(a.xml)}, *{f});")
        if r.text is not None:
            c.append(f"        w.text({ident(r.text.ident)});")
        for f in r.content:
            name = ident(f.ident)
            unbox = ".get()" if f.boxed else ""
            if isinstance(f, ElemField):
                if f.card == ONE:
                    self.write_value(f.type, f.xml, f"v.{name}{unbox}",
                                     "        ")
                elif f.card == OPT:
                    c.append(f"        if (v.{name}) {{")
                    self.write_value(f.type, f.xml, f"(*v.{name}){unbox}",
                                     "                ")
                    c.append("        }")
                else:
                    self.count_check(r, f, f.xml)
                    c.append(f"        for (const auto& e : v.{name}) {{")
                    self.write_value(f.type, f.xml, "e", "                ")
                    c.append("        }")
            else:
                if f.card == ONE:
                    c.append(f"        v.{name}{unbox}.write_xml(w);")
                elif f.card == OPT:
                    c.append(f"        if (v.{name}) "
                             f"(*v.{name}){unbox}.write_xml(w);")
                else:
                    self.count_check(r, f, f.choice.name)
                    c.append(f"        for (const auto& e : v.{name}) "
                             "e.write_xml(w);")
        c.append("        w.end(tag);")
        c.append("}")
        c.append("")

    def read_expr(self, t: TypeRef) -> str:
        """The expression that reads a child element of type ``t``."""
        if isinstance(t, ComplexRef):
            return f"r.read_child<{t.name}>()"
        return f"r.read_leaf<{base_type(t)}>()"

    def read_fn(self, r: Record) -> None:
        """Writes the definition of a record's ``read_xml``: its
        attributes, then its text or its child elements, checked against
        the content model.

        Args:
            r: The record.
        """
        c = self.c
        c.append(f"{r.name} {r.name}::read_xml(Reader& r, const Tag& tag) {{")
        c.append(f"        {r.name} v;")
        required = [a for a in r.attrs if a.required]
        for a in required:
            c.append(f"        bool seen_{a.ident} = false;")
        c.append("        for (AttrIter it(tag); it.next(r);) {")
        c.append("                std::string_view k = it.name();")
        first = True
        for a in r.attrs:
            kw = "if" if first else "} else if"
            first = False
            f = ident(a.ident)
            c.append(f"                {kw} (k == {lit(a.xml)}) {{")
            if a.required:
                c.append("                        r.attr_value(tag, "
                         f"it.value(), v.{f});")
                c.append(f"                        seen_{a.ident} = true;")
            else:
                c.append("                        r.attr_value(tag, "
                         f"it.value(), v.{f}.emplace());")
        if r.attrs:
            c.append("                } else {")
            c.append("                        r.unknown_attr(tag, k);")
            c.append("                }")
        else:
            c.append("                r.unknown_attr(tag, k);")
        c.append("        }")
        for a in required:
            c.append(f"        if (!seen_{a.ident}) r.missing_attr(tag, "
                     f"{lit(a.xml)});")
        if r.text is not None:
            c.append(f"        v.{ident(r.text.ident)} = r.read_value"
                     f"<{base_type(r.text.type)}>();")
            c.append("        return v;")
            c.append("}")
            c.append("")
            return
        for f in r.content:
            name = ident(f.ident)
            if isinstance(f, ElemField):
                x = lit(f.xml)
                if f.card == ONE:
                    c.append(f"        if (r.peek_child() != {x}) throw "
                             f"r.expected({x});")
                    c.append(f"        v.{name} = {self.read_expr(f.type)};")
                elif f.card == OPT:
                    c.append(f"        if (r.peek_child() == {x}) v.{name} = "
                             f"{self.read_expr(f.type)};")
                else:
                    cond = f"r.peek_child() == {x}"
                    if f.max != UNBOUNDED:
                        cond = f"v.{name}.size() < {f.max} && {cond}"
                    c.append(f"        while ({cond}) "
                             f"v.{name}.push_back({self.read_expr(f.type)});")
                    if f.min > 0:
                        c.append(f"        if (v.{name}.size() < {f.min}) "
                                 f"throw r.too_few({x}, {f.min}, "
                                 f"v.{name}.size());")
            else:
                cn = f.choice.name
                if f.card == ONE:
                    c.append(f"        v.{name} = {cn}::read_xml(r);")
                elif f.card == OPT:
                    c.append("        {")
                    c.append("                auto n = r.peek_child();")
                    c.append(f"                if (n && {cn}::accepts(*n)) "
                             f"v.{name} = {cn}::read_xml(r);")
                    c.append("        }")
                else:
                    c.append("        for (;;) {")
                    if f.max != UNBOUNDED:
                        c.append(f"                if (v.{name}.size() >= "
                                 f"{f.max}) break;")
                    c.append("                auto n = r.peek_child();")
                    c.append(f"                if (!n || !{cn}::accepts(*n)) "
                             "break;")
                    c.append("                "
                             f"v.{name}.push_back({cn}::read_xml(r));")
                    c.append("        }")
                    if f.min > 0:
                        what = lit("|".join(v.xml for v in f.choice.variants))
                        c.append(f"        if (v.{name}.size() < {f.min}) "
                                 f"throw r.too_few({what}, {f.min}, "
                                 f"v.{name}.size());")
        c.append("        r.close();")
        c.append("        return v;")
        c.append("}")
        c.append("")

    # -- choices ------------------------------------------------------------

    def choice(self, ch: ChoiceType) -> None:
        """Writes the struct for a ``<choice>`` and the definitions of its
        member functions.

        Args:
            ch: The choice.
        """
        h, c = self.h, self.c
        alts = []
        for v in ch.variants:
            t = base_type(v.type)
            alts.append(f"Box<{t}>" if v.boxed else t)
        h.append(f"// Alternatives of a <choice> in {ch.owner}; the variant "
                 "index is the Kind.")
        h.append(f"struct {ch.name} {{")
        h.append("        enum class Kind { "
                 f"{', '.join(v.ident for v in ch.variants)} }};")
        init = ("" if ch.default_variant == 0
                else f"{{std::in_place_index<{ch.default_variant}>}}")
        h.append(f"        std::variant<{', '.join(alts)}> value{init};")
        h.append("")
        ind = "        "
        names = docs.choice_names(ch, code)
        h += docs.doxygen("Says which alternative is selected.",
                          returns="The selected alternative's Kind, which is "
                                  "the index of `value`.", indent=ind)
        h.append("        Kind kind() const { return static_cast"
                 "<Kind>(value.index()); }")
        h += docs.doxygen("Gives the element name of the selected alternative.",
                          returns=f"The name: {names}.", indent=ind)
        h.append("        std::string_view xml_name() const;")
        h += docs.doxygen("Says whether an element named `name` starts an "
                          f"alternative of the <choice> in `{ch.owner}`.",
                          [("name", "An element's local name.")],
                          f"Whether `name` is {names}.", indent=ind)
        h.append("        static bool accepts(std::string_view name);")
        for v in ch.variants:
            t = base_type(v.type)
            about = docs.first_sentence(v.doc)
            about = f" {about}" if about else ""
            h += docs.doxygen(f"Builds the `{v.xml}` alternative.{about}",
                              [("v",
                                f"The `{v.xml}` element's value, which is "
                                "moved into the result.")],
                              f"A {ch.name} holding the `{v.xml}` alternative.",
                              indent=ind)
            h.append(f"        static {ch.name} make_{v.ident}({t} v);")
            for const in ("", "const "):
                h += docs.doxygen(f"Gives the value of the `{v.xml}` "
                                  "alternative, if it is the one selected.",
                                  returns=f"A pointer to the {const}value, "
                                          f"owned by this {ch.name}, or "
                                          "nullptr if another alternative is "
                                          "selected.", indent=ind)
                h.append(f"        {const}{t}* as_{v.ident}() {const}".rstrip()
                         + ";")
        how = self.fails.get(ch.name)
        h += docs.doxygen(
            f"Writes the selected alternative as its element, {names}.",
            [("w", "The writer that receives the XML.")],
            throws=[("Error",
                     docs.sentence(docs.write_error(how, "the alternative")))]
            if how else (), indent=ind)
        h.append("        void write_xml(Writer& w) const;")
        h += docs.doxygen(
            "Reads the alternative that the reader's next child element "
            f"starts, which must be {names}.",
            [("r",
              "The reader, positioned before the child element; it is left "
              "after the child's end tag.")], "The alternative read.",
            [("Error",
              "If there is no next child or it is none of "
              f"{docs.choice_names(ch, code, 'and')}, if the alternative's "
              "element is invalid (see the read_xml of its type) or if the "
              "XML is malformed.")], indent=ind)
        h.append(f"        static {ch.name} read_xml(Reader& r);")
        h.append(f"        bool operator==(const {ch.name}&) const = default;")
        h.append("};")
        h.append("")

        c.append(f"std::string_view {ch.name}::xml_name() const {{")
        c.append("        switch (value.index()) {")
        for k, v in enumerate(ch.variants):
            c.append(f"                case {k}:")
            c.append(f"                        return {lit(v.xml)};")
        c.append("        }")
        c.append('        return "";')
        c.append("}")
        c.append("")
        c.append(f"bool {ch.name}::accepts(std::string_view name) {{")
        c.append("        return "
                 f"{' || '.join(f'name == {lit(v.xml)}' for v in ch.variants)}"
                 ";")
        c.append("}")
        c.append("")
        for k, v in enumerate(ch.variants):
            t = base_type(v.type)
            c.append(f"{ch.name} {ch.name}::make_{v.ident}({t} v) {{")
            c.append(f"        {ch.name} c;")
            c.append(f"        c.value.emplace<{k}>(std::move(v));")
            c.append("        return c;")
            c.append("}")
            c.append("")
            get = "&p->get()" if v.boxed else "p"
            for const in ("", "const "):
                c.append(f"{const}{t}* {ch.name}::as_{v.ident}() {const}{{")
                c.append(f"        auto* p = std::get_if<{k}>(&value);")
                c.append(f"        return p ? {get} : nullptr;")
                c.append("}")
                c.append("")
        c.append(f"void {ch.name}::write_xml(Writer& w) const {{")
        c.append("        switch (value.index()) {")
        for k, v in enumerate(ch.variants):
            c.append(f"                case {k}:")
            expr = f"std::get<{k}>(value)" + (".get()" if v.boxed else "")
            self.write_value(v.type, v.xml, expr, "                        ")
            c.append("                        break;")
        c.append("        }")
        c.append("}")
        c.append("")
        c.append(f"{ch.name} {ch.name}::read_xml(Reader& r) {{")
        c.append("        auto n = r.peek_child();")
        c.append(f"        {ch.name} c;")
        for k, v in enumerate(ch.variants):
            kw = "if" if k == 0 else "} else if"
            c.append(f"        {kw} (n == {lit(v.xml)}) {{")
            c.append("                c.value.emplace"
                     f"<{k}>({self.read_expr(v.type)});")
        c.append("        } else {")
        c.append("                throw "
                 f"r.expected({lit('|'.join(v.xml for v in ch.variants))});")
        c.append("        }")
        c.append("        return c;")
        c.append("}")
        c.append("")


def emit(b: Binding) -> tuple[str, str]:
    """The C++ header and source for binding ``b``."""
    return CppEmitter(b).emit()
