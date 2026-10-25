"""Emit Rust data structures and (de)serialization code."""

from __future__ import annotations

from . import header_lines
from .binding import (MANY, ONE, OPT, Binding, ChoiceField, ChoiceType,
                      ElemField, Record)
from .model import (BOOL, DOUBLE, INT, LONG, STRING, USHORT, UNBOUNDED,
                    ComplexRef, EnumRef, ScalarRef, TypeRef)
from .binding import pascal_ident

SCALARS = {STRING: "String", BOOL: "bool", INT: "i32", LONG: "i64",
           USHORT: "u16", DOUBLE: "f64"}

KEYWORDS = set("as async await break const continue crate dyn else enum extern "
               "false fn for if impl in let loop match mod move mut pub ref "
               "return self static struct super trait true type unsafe use "
               "where while abstract become box do final gen macro override "
               "priv try typeof unsized virtual yield".split())


def ident(name: str) -> str:
    return name + "_" if name in KEYWORDS else name


def lit(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def doc(text: str, indent: str = "") -> list[str]:
    if not text:
        return []
    return [f"{indent}/// {text}"]


def base_type(t: TypeRef) -> str:
    if isinstance(t, ScalarRef):
        return SCALARS[t.kind]
    return t.name


def field_type(t: str, card: str, boxed: bool) -> str:
    if boxed:
        t = f"Box<{t}>"
    if card == OPT:
        return f"Option<{t}>"
    if card == MANY:
        return f"Vec<{t}>"
    return t


def is_complex(t: TypeRef) -> bool:
    return isinstance(t, ComplexRef)


class RustEmitter:
    def __init__(self, b: Binding):
        self.b = b
        self.out: list[str] = []

    def w(self, line: str = "") -> None:
        self.out.append(line)

    def emit(self) -> str:
        s = self.b.schema
        for line in header_lines():
            self.w("// " + line)
        self.w("")
        self.w(f"//! Data structures for XTCE {s.version} (`{s.namespace}`).")
        self.w("")
        self.w("#![allow(clippy::all, non_camel_case_types, "
               "clippy::upper_case_acronyms)]")
        self.w("")
        self.w("use crate::value::Value;")
        self.w("use crate::xml::{check_count, Reader, Result, Tag, Writer, "
               "XmlElement};")
        self.w("")
        self.w("/// XML namespace of the XTCE schema these types were "
               "generated from.")
        self.w(f"pub const NAMESPACE: &str = {lit(s.namespace)};")
        self.w("/// XTCE version of the schema these types were generated "
               "from.")
        self.w(f"pub const SCHEMA_VERSION: &str = {lit(s.version)};")
        self.w("/// Name of the document root element.")
        root = next(iter(s.roots))
        self.w(f"pub const ROOT_ELEMENT: &str = {lit(root)};")
        self.w("")
        for name in sorted(s.enums):
            self.enum(name)
        for name in sorted(self.b.records):
            self.record(self.b.records[name])
        for name in sorted(self.b.choices):
            self.choice(self.b.choices[name])
        return "\n".join(self.out) + "\n"

    # -- enumerations -------------------------------------------------------

    def enum(self, name: str) -> None:
        e = self.b.schema.enums[name]
        idents, used = [], set()
        for v in e.values:
            i, n = pascal_ident(v), 2
            base = i
            while i in used:
                i = f"{base}{n}"
                n += 1
            used.add(i)
            idents.append(i)
        self.out += doc(e.doc)
        self.w("#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default)]")
        self.w(f"pub enum {name} {{")
        for k, (i, v) in enumerate(zip(idents, e.values)):
            self.w(f"    /// `{v}`")
            if k == 0:
                self.w("    #[default]")
            self.w(f"    {i},")
        self.w("}")
        self.w("")
        self.w(f"impl {name} {{")
        self.w("    pub const ALL: &'static [Self] = "
               f"&[{', '.join('Self::' + i for i in idents)}];")
        self.w("")
        self.w("    /// The XML lexical value.")
        self.w("    pub fn as_str(self) -> &'static str {")
        self.w("        match self {")
        for i, v in zip(idents, e.values):
            self.w(f"            Self::{i} => {lit(v)},")
        self.w("        }")
        self.w("    }")
        self.w("}")
        self.w("")
        self.w(f"impl Value for {name} {{")
        self.w("    fn parse_xml(s: &str) -> std::result::Result<Self, String> "
               "{")
        self.w("        Ok(match s.trim() {")
        for i, v in zip(idents, e.values):
            self.w(f"            {lit(v.strip())} => Self::{i},")
        self.w(f'            other => return Err(format!("invalid {name} '
               '{other:?}")),')
        self.w("        })")
        self.w("    }")
        self.w("")
        self.w("    fn fmt_xml(&self, out: &mut String) {")
        self.w("        out.push_str(self.as_str());")
        self.w("    }")
        self.w("}")
        self.w("")
        self.w(f"impl std::fmt::Display for {name} {{")
        self.w("    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> "
               "std::fmt::Result {")
        self.w("        f.write_str(self.as_str())")
        self.w("    }")
        self.w("}")
        self.w("")

    # -- records ------------------------------------------------------------

    def record(self, r: Record) -> None:
        self.out += doc(r.doc)
        self.w("#[derive(Debug, Clone, PartialEq, Default)]")
        self.w(f"pub struct {r.name} {{")
        for a in r.attrs:
            self.out += doc(a.doc or f"Attribute `{a.xml}`.", "    ")
            if a.default is not None:
                self.w(f"    ///")
                self.w(f"    /// Schema default: `{a.default}`.")
            t = base_type(a.type)
            self.w(f"    pub {ident(a.ident)}: "
                   f"{t if a.required else f'Option<{t}>'},")
        if r.text is not None:
            self.w("    /// Text content.")
            self.w(f"    pub {ident(r.text.ident)}: {base_type(r.text.type)},")
        for f in r.content:
            if isinstance(f, ElemField):
                self.out += doc(f.doc or f"Element `{f.xml}`.", "    ")
                self.w(f"    pub {ident(f.ident)}: "
                       f"{field_type(base_type(f.type), f.card, f.boxed)},")
            else:
                self.w("    /// One of: "
                       f"{', '.join(f'`{v.xml}`' for v in f.choice.variants)}.")
                self.w(f"    pub {ident(f.ident)}: "
                       f"{field_type(f.choice.name, f.card, f.boxed)},")
        self.w("}")
        self.w("")
        self.w(f"impl XmlElement for {r.name} {{")
        self.write_fn(r)
        self.w("")
        self.read_fn(r)
        self.w("}")
        self.w("")

    def write_value(self, xml: str, t: TypeRef, expr: str, ind: str) -> None:
        if is_complex(t):
            self.w(f"{ind}{expr}.write_xml(w, {lit(xml)})?;")
        else:
            self.w(f"{ind}w.leaf({lit(xml)}, {expr});")

    def write_fn(self, r: Record) -> None:
        self.w("    fn write_xml(&self, w: &mut Writer<'_>, tag: &str) -> "
               "Result<()> {")
        self.w("        w.start(tag);")
        for a in r.attrs:
            f = ident(a.ident)
            if a.required:
                self.w(f"        w.attr({lit(a.xml)}, &self.{f});")
            else:
                self.w(f"        if let Some(v) = &self.{f} {{")
                self.w(f"            w.attr({lit(a.xml)}, v);")
                self.w("        }")
        if r.text is not None:
            self.w(f"        w.text(&self.{ident(r.text.ident)});")
        for f in r.content:
            name = ident(f.ident)
            if isinstance(f, ElemField):
                if f.card == ONE:
                    expr = (f"self.{name}" if is_complex(f.type)
                            else f"&self.{name}")
                    self.write_value(f.xml, f.type, expr, "        ")
                elif f.card == OPT:
                    self.w(f"        if let Some(v) = &self.{name} {{")
                    self.write_value(f.xml, f.type, "v", "            ")
                    self.w("        }")
                else:
                    self.count_check(r, f, f.xml)
                    self.w(f"        for v in &self.{name} {{")
                    self.write_value(f.xml, f.type, "v", "            ")
                    self.w("        }")
            else:
                if f.card == ONE:
                    self.w(f"        self.{name}.write_xml(w)?;")
                elif f.card == OPT:
                    self.w(f"        if let Some(v) = &self.{name} {{")
                    self.w("            v.write_xml(w)?;")
                    self.w("        }")
                else:
                    self.count_check(r, f, f.choice.name)
                    self.w(f"        for v in &self.{name} {{")
                    self.w("            v.write_xml(w)?;")
                    self.w("        }")
        self.w("        w.end(tag);")
        self.w("        Ok(())")
        self.w("    }")

    def count_check(self, r: Record, f, what: str) -> None:
        if f.min == 0 and f.max == UNBOUNDED:
            return
        mx = "None" if f.max == UNBOUNDED else f"Some({f.max})"
        self.w(f"        check_count(self.{ident(f.ident)}.len(), {f.min}, "
               f"{mx}, {lit(r.name + '.' + what)})?;")

    def read_value(self, t: TypeRef) -> str:
        return "r.read_child()?" if is_complex(t) else "r.read_leaf()?"

    def read_fn(self, r: Record) -> None:
        has_fields = bool(r.attrs or r.text or r.content)
        self.w("    fn read_xml<'a>(r: &mut Reader<'a>, tag: &Tag<'a>) -> "
               "Result<Self> {")
        self.w(f"        let {'mut ' if has_fields else ''}v = "
               "Self::default();")
        required = [a for a in r.attrs if a.required]
        for a in required:
            self.w(f"        let mut seen_{a.ident} = false;")
        self.w("        for a in tag.attrs() {")
        if r.attrs:
            self.w("            let (k, raw) = a?;")
            self.w("            match k {")
            for a in r.attrs:
                f = ident(a.ident)
                if a.required:
                    self.w(f"                {lit(a.xml)} => {{")
                    self.w(f"                    v.{f} = r.attr_value(tag, "
                           "raw)?;")
                    self.w(f"                    seen_{a.ident} = true;")
                    self.w("                }")
                else:
                    self.w(f"                {lit(a.xml)} => v.{f} = "
                           "Some(r.attr_value(tag, raw)?),")
            self.w("                _ => r.unknown_attr(tag, k)?,")
            self.w("            }")
        else:
            self.w("            let (k, _) = a?;")
            self.w("            r.unknown_attr(tag, k)?;")
        self.w("        }")
        for a in required:
            self.w(f"        if !seen_{a.ident} {{")
            self.w("            return Err(r.missing_attr(tag, "
                   f"{lit(a.xml)}));")
            self.w("        }")
        if r.text is not None:
            self.w(f"        v.{ident(r.text.ident)} = r.read_value()?;")
            self.w("        Ok(v)")
            self.w("    }")
            return
        for f in r.content:
            name = ident(f.ident)
            if isinstance(f, ElemField):
                x = lit(f.xml)
                if f.card == ONE:
                    self.w(f"        if r.peek_child()? != Some({x}) {{")
                    self.w(f"            return Err(r.expected({x}));")
                    self.w("        }")
                    self.w(f"        v.{name} = {self.read_value(f.type)};")
                elif f.card == OPT:
                    self.w(f"        if r.peek_child()? == Some({x}) {{")
                    self.w(f"            v.{name} = "
                           f"Some({self.read_value(f.type)});")
                    self.w("        }")
                else:
                    cond = f"r.peek_child()? == Some({x})"
                    if f.max != UNBOUNDED:
                        cond = f"v.{name}.len() < {f.max} && {cond}"
                    self.w(f"        while {cond} {{")
                    self.w("            "
                           f"v.{name}.push({self.read_value(f.type)});")
                    self.w("        }")
                    if f.min > 0:
                        self.w(f"        if v.{name}.len() < {f.min} {{")
                        self.w(f"            let n = v.{name}.len();")
                        self.w(f"            return Err(r.too_few({x}, "
                               f"{f.min}, n));")
                        self.w("        }")
            else:
                c = f.choice.name
                rd = f"{c}::read_xml(r)?"
                if f.boxed:
                    rd = f"Box::new({rd})"
                if f.card == ONE:
                    self.w(f"        v.{name} = {rd};")
                elif f.card == OPT:
                    self.w("        if "
                           f"r.peek_child()?.is_some_and({c}::accepts) {{")
                    self.w(f"            v.{name} = Some({rd});")
                    self.w("        }")
                else:
                    cond = f"r.peek_child()?.is_some_and({c}::accepts)"
                    if f.max != UNBOUNDED:
                        cond = f"v.{name}.len() < {f.max} && {cond}"
                    self.w(f"        while {cond} {{")
                    self.w(f"            v.{name}.push({rd});")
                    self.w("        }")
                    if f.min > 0:
                        what = lit("|".join(v.xml for v in f.choice.variants))
                        self.w(f"        if v.{name}.len() < {f.min} {{")
                        self.w(f"            let n = v.{name}.len();")
                        self.w(f"            return Err(r.too_few({what}, "
                               f"{f.min}, n));")
                        self.w("        }")
        self.w("        r.close()?;")
        self.w("        Ok(v)")
        self.w("    }")

    # -- choices ------------------------------------------------------------

    def choice(self, c: ChoiceType) -> None:
        self.w(f"/// Alternatives of a `<choice>` in [`{c.owner}`].")
        self.w("#[derive(Debug, Clone, PartialEq)]")
        self.w(f"pub enum {c.name} {{")
        for v in c.variants:
            self.out += doc(v.doc, "    ")
            t = base_type(v.type)
            self.w(f"    {v.ident}({f'Box<{t}>' if v.boxed else t}),")
        self.w("}")
        self.w("")
        d = c.variants[c.default_variant]
        self.w(f"impl Default for {c.name} {{")
        self.w("    fn default() -> Self {")
        self.w(f"        Self::{d.ident}(Default::default())")
        self.w("    }")
        self.w("}")
        self.w("")
        names = " | ".join(lit(v.xml) for v in c.variants)
        self.w(f"impl {c.name} {{")
        self.w("    /// Whether `name` starts one of the alternatives.")
        self.w("    pub fn accepts(name: &str) -> bool {")
        self.w(f"        matches!(name, {names})")
        self.w("    }")
        self.w("")
        self.w("    /// The element name of the selected alternative.")
        self.w("    pub fn xml_name(&self) -> &'static str {")
        self.w("        match self {")
        for v in c.variants:
            self.w(f"            Self::{v.ident}(_) => {lit(v.xml)},")
        self.w("        }")
        self.w("    }")
        self.w("")
        self.w("    pub fn write_xml(&self, w: &mut Writer<'_>) -> Result<()> "
               "{")
        self.w("        match self {")
        for v in c.variants:
            if is_complex(v.type):
                self.w(f"            Self::{v.ident}(v) => v.write_xml(w, "
                       f"{lit(v.xml)})?,")
            else:
                self.w(f"            Self::{v.ident}(v) => "
                       f"w.leaf({lit(v.xml)}, v),")
        self.w("        }")
        self.w("        Ok(())")
        self.w("    }")
        self.w("")
        self.w("    pub fn read_xml(r: &mut Reader<'_>) -> Result<Self> {")
        self.w("        Ok(match r.peek_child()? {")
        for v in c.variants:
            self.w(f"            Some({lit(v.xml)}) => "
                   f"Self::{v.ident}({self.read_value(v.type)}),")
        self.w("            _ => return "
               f"Err(r.expected({lit('|'.join(v.xml for v in c.variants))})),")
        self.w("        })")
        self.w("    }")
        self.w("}")
        self.w("")


def emit(b: Binding) -> str:
    return RustEmitter(b).emit()
