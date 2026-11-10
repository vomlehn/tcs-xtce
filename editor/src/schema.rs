//! The XTCE schemas, read into what the editor checks a document against.
//!
//! Each XTCE version the editor knows has its own schema, as OMG published
//! it, built into the program from `references/`, and its own namespace, by
//! which a document says which version it is written in.  A document is
//! held to the schema of its version and no other: that is what "strictly
//! enforced" means, and why nothing here is written by hand -- the rules are
//! the schema's, read from it.
//!
//! The schemas use a small part of XML Schema: global and local element
//! declarations, named and anonymous complex types made of sequences and
//! choices, derivation by extension and by restriction, simple content,
//! attributes, simple types restricted by enumerations, patterns and ranges,
//! unions, and keys.  That part is read here; anything else in a schema is
//! refused when the schema is read, so that a new schema using more of the
//! language cannot be held to less than it says.
//!
//! XTCE 1.0's schema was never published in machine-readable form, only
//! printed in its specification, so 1.0 is not one of the versions.

use std::collections::HashMap;
use std::fmt;
use std::sync::OnceLock;

use regex::Regex;

use crate::content::Automaton;
use crate::tree::{local_name, Document, Element};

/// An XTCE version the editor knows.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Version {
    /// XTCE 1.1, of 2006.
    V1_1,
    /// XTCE 1.2, of 2018.
    V1_2,
    /// XTCE 1.3, of 2025.
    V1_3,
}

impl Version {
    /// Every version, oldest first.
    pub const ALL: [Version; 3] = [Version::V1_1, Version::V1_2, Version::V1_3];

    /// The newest version.
    pub const NEWEST: Version = Version::V1_3;

    /// Gives the namespace documents of this version are in.
    ///
    /// # Returns
    ///
    /// The namespace's URI.
    pub fn namespace(self) -> &'static str {
        match self {
            Version::V1_1 => "http://www.omg.org/space/xtce",
            Version::V1_2 => "http://www.omg.org/spec/XTCE/20180204",
            Version::V1_3 => "http://www.omg.org/spec/XTCE/20250214",
        }
    }

    /// Gives where OMG publishes the schema, which a document may give as
    /// its `xsi:schemaLocation`.
    ///
    /// # Returns
    ///
    /// The schema's URL.
    pub fn schema_url(self) -> &'static str {
        match self {
            Version::V1_1 => {
                "https://www.omg.org/spec/XTCE/20061101/06-11-06.xsd"
            }
            Version::V1_2 => {
                "https://www.omg.org/spec/XTCE/20180204/SpaceSystem.xsd"
            }
            Version::V1_3 => {
                "https://www.omg.org/spec/XTCE/20250214/SpaceSystem.xsd"
            }
        }
    }

    /// The version a namespace is of.
    pub fn of_namespace(ns: &str) -> Option<Version> {
        Version::ALL.into_iter().find(|v| v.namespace() == ns)
    }

    /// The version a name such as `1.2` is.
    pub fn parse(name: &str) -> Option<Version> {
        Version::ALL.into_iter().find(|v| v.to_string() == name)
    }

    /// Finds the version a document is written in, from its root's
    /// namespace.
    ///
    /// # Arguments
    ///
    /// * `doc` - The document.
    ///
    /// # Returns
    ///
    /// The version.
    ///
    /// # Errors
    ///
    /// Says what the root's namespace is and which are known, if it is
    /// none of them.
    pub fn of_document(doc: &Document) -> Result<Version, String> {
        let root = doc.root();
        if root.local() != "SpaceSystem" {
            return Err(format!(
                "the root element is {}; an XTCE document's is SpaceSystem",
                root.name
            ));
        }
        match doc.namespace_of(root.id) {
            Some(ns) => Version::of_namespace(&ns).ok_or_else(|| {
                if ns == "http://www.omg.org/space/xtce/1.0" {
                    "this is XTCE 1.0, whose schema OMG never published in a \
                     form a program can read; the editor reads 1.1 and later"
                        .to_string()
                } else {
                    format!(
                        "the SpaceSystem is in the namespace {ns}, which is \
                         no XTCE version the editor knows; it knows {}",
                        known_namespaces()
                    )
                }
            }),
            None => Err(format!(
                "the SpaceSystem is in no namespace, so it does not say \
                 which XTCE version it is; give it xmlns=\"...\" with one \
                 of {}",
                known_namespaces()
            )),
        }
    }

    /// Gives the schema's text, built into the program.
    ///
    /// # Returns
    ///
    /// The text of the XSD file.
    fn text(self) -> &'static str {
        match self {
            Version::V1_1 => include_str!("../../references/xtce-1.1.xsd"),
            Version::V1_2 => include_str!("../../references/xtce-1.2.xsd"),
            Version::V1_3 => include_str!("../../references/xtce-1.3.xsd"),
        }
    }

    /// Gives the schema, read the first time it is asked for.
    ///
    /// # Returns
    ///
    /// The schema, which lasts as long as the program.
    ///
    /// # Panics
    ///
    /// Panics if the built-in schema cannot be read, which the tests
    /// rule out for every version before the program is shipped.
    pub fn schema(self) -> &'static Schema {
        static SCHEMAS: [OnceLock<Schema>; 3] =
            [OnceLock::new(), OnceLock::new(), OnceLock::new()];
        SCHEMAS[self as usize].get_or_init(|| {
            Schema::read(self, self.text()).unwrap_or_else(|e| {
                panic!("the built-in XTCE {self} schema cannot be read: {e}")
            })
        })
    }
}

impl fmt::Display for Version {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Version::V1_1 => "1.1",
            Version::V1_2 => "1.2",
            Version::V1_3 => "1.3",
        })
    }
}

/// Lists the namespaces of the versions the editor knows, for messages.
///
/// # Returns
///
/// Each namespace with its version, separated by commas.
fn known_namespaces() -> String {
    Version::ALL
        .iter()
        .map(|v| format!("{} (XTCE {v})", v.namespace()))
        .collect::<Vec<_>>()
        .join(", ")
}

/// How many times something may occur.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Occurs {
    /// The fewest times it may occur.
    pub min: u32,
    /// `None` is unbounded.
    pub max: Option<u32>,
}

impl Occurs {
    /// Exactly once, as XML Schema has it when neither `minOccurs` nor
    /// `maxOccurs` is given.
    pub const ONCE: Occurs = Occurs {
        min: 1,
        max: Some(1),
    };
}

/// What may be inside an element of a complex type.
#[derive(Debug, Clone, PartialEq)]
pub enum Particle {
    /// An element, by its index in [`Schema::elements`].
    Element(usize, Occurs),
    /// Particles that occur in order, the whole as many times as allowed.
    Sequence(Vec<Particle>, Occurs),
    /// Particles of which one occurs each time, as many times as allowed.
    Choice(Vec<Particle>, Occurs),
}

/// A declaration of an element.
#[derive(Debug, Clone, PartialEq)]
pub struct ElementDecl {
    /// The element's name, without a prefix.
    pub name: String,
    /// The element's type.
    pub ty: TypeRef,
    /// The value its text has when it is empty, if the schema gives one.
    pub default: Option<String>,
    /// The one value its text may have, if the schema fixes it.
    pub fixed: Option<String>,
    /// The keys declared on it, which hold below it.
    pub keys: Vec<Key>,
    /// The schema's documentation of it, as one paragraph.
    pub doc: String,
}

/// What an element's or attribute's type is.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TypeRef {
    /// A complex type, by its index in [`Schema::complex`].
    Complex(usize),
    /// A simple type: text and nothing else.
    Simple(SimpleRef),
}

/// A simple type.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SimpleRef {
    /// One of XML Schema's own types.
    Builtin(Builtin),
    /// By its index in [`Schema::simple`].
    Defined(usize),
}

/// The XML Schema types the XTCE schemas build on.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Builtin {
    /// `string`: any text, its whitespace kept.
    String,
    /// `normalizedString`: any text.
    NormalizedString,
    /// `token`: any text, its whitespace collapsed.
    Token,
    /// `boolean`: `true`, `false`, `1` or `0`.
    Boolean,
    /// `double`: a floating-point number, `INF` or `NaN`.
    Double,
    /// `decimal`: a number with digits and at most one point.
    Decimal,
    /// `integer`: a whole number of any size.
    Integer,
    /// `long`: a whole number that fits in 64 bits.
    Long,
    /// `int`: a whole number that fits in 32 bits.
    Int,
    /// `nonNegativeInteger`: a whole number, 0 or more.
    NonNegativeInteger,
    /// `positiveInteger`: a whole number, 1 or more.
    PositiveInteger,
    /// `unsignedShort`: a whole number from 0 to 65535.
    UnsignedShort,
    /// `hexBinary`: bytes, as pairs of hexadecimal digits.
    HexBinary,
    /// `date`: a date, such as 2026-11-10.
    Date,
    /// `dateTime`: a date and time, such as 2026-11-10T12:00:00Z.
    DateTime,
    /// `duration`: a length of time, such as PT1.5S.
    Duration,
    /// `anyURI`: a URI, which is not checked.
    AnyUri,
}

impl Builtin {
    /// Finds the type XML Schema gives a name.
    ///
    /// # Arguments
    ///
    /// * `name` - The name, without a prefix.
    ///
    /// # Returns
    ///
    /// The type, or `None` if it is not one the XTCE schemas build on.
    fn of(name: &str) -> Option<Builtin> {
        Some(match name {
            "string" => Builtin::String,
            "normalizedString" => Builtin::NormalizedString,
            "token" => Builtin::Token,
            "boolean" => Builtin::Boolean,
            "double" => Builtin::Double,
            "decimal" => Builtin::Decimal,
            "integer" => Builtin::Integer,
            "long" => Builtin::Long,
            "int" => Builtin::Int,
            "nonNegativeInteger" => Builtin::NonNegativeInteger,
            "positiveInteger" => Builtin::PositiveInteger,
            "unsignedShort" => Builtin::UnsignedShort,
            "hexBinary" => Builtin::HexBinary,
            "date" => Builtin::Date,
            "dateTime" => Builtin::DateTime,
            "duration" => Builtin::Duration,
            "anyURI" => Builtin::AnyUri,
            _ => return None,
        })
    }

    /// Gives the name XML Schema gives it.
    ///
    /// # Returns
    ///
    /// The name, without a prefix.
    pub fn name(self) -> &'static str {
        match self {
            Builtin::String => "string",
            Builtin::NormalizedString => "normalizedString",
            Builtin::Token => "token",
            Builtin::Boolean => "boolean",
            Builtin::Double => "double",
            Builtin::Decimal => "decimal",
            Builtin::Integer => "integer",
            Builtin::Long => "long",
            Builtin::Int => "int",
            Builtin::NonNegativeInteger => "nonNegativeInteger",
            Builtin::PositiveInteger => "positiveInteger",
            Builtin::UnsignedShort => "unsignedShort",
            Builtin::HexBinary => "hexBinary",
            Builtin::Date => "date",
            Builtin::DateTime => "dateTime",
            Builtin::Duration => "duration",
            Builtin::AnyUri => "anyURI",
        }
    }

    /// Whether whitespace in a value is kept as written: only a string's
    /// is.  Every other type's is collapsed before it is checked.
    fn preserves_space(self) -> bool {
        self == Builtin::String
    }
}

/// A simple type the schema defines.
#[derive(Debug, Clone)]
pub struct SimpleType {
    /// Its name, or `None` for a type defined where it is used.
    pub name: Option<String>,
    /// What it restricts, for a restriction.
    pub base: Option<SimpleRef>,
    /// The values it allows, if it is an enumeration; empty otherwise.
    pub enumeration: Vec<String>,
    /// The patterns a value must match, each as the schema writes it and
    /// as the regular expression that checks it.
    pub patterns: Vec<(String, Regex)>,
    /// The least value it allows, as the schema writes it.
    pub min_inclusive: Option<String>,
    /// The greatest value it allows, as the schema writes it.
    pub max_inclusive: Option<String>,
    /// The types a union is of; empty for a restriction.
    pub union: Vec<SimpleRef>,
    /// The schema's documentation of it, as one paragraph.
    pub doc: String,
}

/// A complex type the schema defines, with what it derives from merged in.
#[derive(Debug, Clone, PartialEq)]
pub struct ComplexType {
    /// Its name, or `None` for a type defined where it is used.
    pub name: Option<String>,
    /// Whether it is abstract, so that only a type derived from it may be
    /// used.
    pub is_abstract: bool,
    /// Whether text may be mixed in among its child elements.
    pub mixed: bool,
    /// The type it derives from, by name, for showing.
    pub base: Option<String>,
    /// Its child elements, base's first; `None` for no children.
    pub content: Option<Particle>,
    /// Its attributes, base's first, with any it redeclares replaced.
    pub attrs: Vec<AttrDecl>,
    /// The type of its text, for a type with simple content.
    pub text: Option<SimpleRef>,
    /// The schema's documentation of it, as one paragraph.
    pub doc: String,
}

/// An attribute a complex type has.
#[derive(Debug, Clone, PartialEq)]
pub struct AttrDecl {
    /// The attribute's name, with a prefix only for `xml:base`.
    pub name: String,
    /// The type of its value.
    pub ty: SimpleRef,
    /// Whether it must be given.
    pub required: bool,
    /// The value it has when it is not given, if the schema says.
    pub default: Option<String>,
    /// The one value it may have, if the schema fixes it.
    pub fixed: Option<String>,
    /// The schema's documentation of it, as one paragraph.
    pub doc: String,
}

/// A key: the values `field` has on the elements `selector` picks out
/// below the element it is declared on must be there and differ.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Key {
    /// The key's name, which messages give.
    pub name: String,
    /// The alternatives, each a list of steps; a step is an element name
    /// in the schema's namespace, `*`, or a name in no namespace, which an
    /// XTCE element never is.
    pub selector: Vec<Vec<Step>>,
    /// What on each selected element is the key's value: an attribute,
    /// in every XTCE key.
    pub field: Step,
    /// The schema's documentation of it, as one paragraph.
    pub doc: String,
}

/// One step of a key's path.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Step {
    /// An element in the schema's own namespace.
    Element(String),
    /// Any element.
    Any,
    /// An element in no namespace.  The schemas have some such steps, which
    /// look as if a prefix were forgotten; as written they select nothing,
    /// and that is what a validator does with them, so it is what is done
    /// here.
    Unqualified(String),
    /// An attribute, by its name.
    Attribute(String),
}

/// One XTCE version's schema.
#[derive(Debug)]
pub struct Schema {
    /// The version it is the schema of.
    pub version: Version,
    /// Every element declaration, global and local; the other structures
    /// name one by its index here.
    pub elements: Vec<ElementDecl>,
    /// Every complex type, named and anonymous, by index.
    pub complex: Vec<ComplexType>,
    /// Every simple type the schema defines, named and anonymous, by
    /// index.
    pub simple: Vec<SimpleType>,
    /// The global `SpaceSystem` declaration, by its index in `elements`.
    pub root: usize,
    /// The index in `complex` of each named complex type.
    complex_names: HashMap<String, usize>,
    /// The index in `simple` of each named simple type.
    simple_names: HashMap<String, usize>,
    /// While the schema is read, which named complex types have been read,
    /// by index in `complex`.
    done: Vec<bool>,
    /// While the schema is read, the named complex types being read, to
    /// catch a type deriving from itself.
    busy: Vec<String>,
    /// While the schema is read, the global SpaceSystem's declaration once
    /// it is made.
    global: Option<usize>,
    /// Each complex type's content as an automaton, by the type's index in
    /// `complex`, built the first time one is asked for.
    automata: OnceLock<Vec<Automaton>>,
}

/// Why a schema could not be read.
pub type SchemaError = String;

/// A schema's top-level declarations by name, as written, before they are
/// read, so that one can refer to another written after it.
struct Raw<'a> {
    /// Each named complex type.
    complex: HashMap<&'a str, &'a Element>,
    /// Each named simple type.
    simple: HashMap<&'a str, &'a Element>,
    /// Each global element.
    elements: HashMap<&'a str, &'a Element>,
}

impl Schema {
    /// Reads a schema.
    ///
    /// # Arguments
    ///
    /// * `version` - The version it is the schema of.
    /// * `text`    - The text of the XSD file.
    ///
    /// # Returns
    ///
    /// The schema.
    ///
    /// # Errors
    ///
    /// Says what it holds that the editor does not read.
    pub fn read(version: Version, text: &str) -> Result<Schema, SchemaError> {
        let doc = Document::parse(text).map_err(|e| e.to_string())?;
        let top = doc.root();
        let mut raw = Raw {
            complex: HashMap::new(),
            simple: HashMap::new(),
            elements: HashMap::new(),
        };
        for e in top.elements() {
            let name = e.get("name");
            match (e.local(), name) {
                ("complexType", Some(_)) => {
                    raw.complex.insert(attr_str(e, "name"), e);
                }
                ("simpleType", Some(_)) => {
                    raw.simple.insert(attr_str(e, "name"), e);
                }
                ("element", Some(_)) => {
                    raw.elements.insert(attr_str(e, "name"), e);
                }
                ("annotation" | "import", _) => {}
                (other, _) => {
                    return Err(format!("a top-level {other} is not read"))
                }
            }
        }
        let mut s = Schema {
            version,
            elements: Vec::new(),
            complex: Vec::new(),
            simple: Vec::new(),
            root: 0,
            complex_names: HashMap::new(),
            simple_names: HashMap::new(),
            done: Vec::new(),
            busy: Vec::new(),
            global: None,
            automata: OnceLock::new(),
        };
        // Named types get their places first, so that a type can refer to
        // one defined after it, or to itself.
        let mut names: Vec<&str> = raw.simple.keys().copied().collect();
        names.sort_unstable();
        for name in names {
            s.simple_names.insert(name.to_string(), s.simple.len());
            s.simple.push(SimpleType {
                name: Some(name.to_string()),
                base: None,
                enumeration: Vec::new(),
                patterns: Vec::new(),
                min_inclusive: None,
                max_inclusive: None,
                union: Vec::new(),
                doc: String::new(),
            });
        }
        let mut names: Vec<&str> = raw.complex.keys().copied().collect();
        names.sort_unstable();
        for name in &names {
            s.complex_names.insert(name.to_string(), s.complex.len());
            s.complex.push(ComplexType {
                name: Some(name.to_string()),
                is_abstract: false,
                mixed: false,
                base: None,
                content: None,
                attrs: Vec::new(),
                text: None,
                doc: String::new(),
            });
        }
        let mut snames: Vec<&str> = raw.simple.keys().copied().collect();
        snames.sort_unstable();
        for name in snames {
            let idx = s.simple_names[name];
            let st = s.read_simple(raw.simple[name])?;
            s.simple[idx] = SimpleType {
                name: Some(name.to_string()),
                ..st
            };
        }
        // A derived type's content includes its base's, so bases are read
        // first.
        s.done = vec![false; s.complex.len()];
        for name in &names {
            s.complex_named(&raw, name)?;
        }
        s.root = s.global_space_system(&raw)?;
        s.done = Vec::new();
        Ok(s)
    }

    /// Reads a named complex type, and the types it derives from, unless it
    /// has been read already.
    ///
    /// # Arguments
    ///
    /// * `raw`  - The schema's top-level declarations.
    /// * `name` - The type's name, without a prefix.
    ///
    /// # Returns
    ///
    /// The type's index in [`Schema::complex`].
    ///
    /// # Errors
    ///
    /// Fails if there is no such type, if it derives from itself, or if it
    /// holds what the editor does not read.
    fn complex_named(
        &mut self,
        raw: &Raw,
        name: &str,
    ) -> Result<usize, SchemaError> {
        let Some(&idx) = self.complex_names.get(name) else {
            return Err(format!("no complex type {name}"));
        };
        if self.done[idx] {
            return Ok(idx);
        }
        if self.busy.iter().any(|b| b == name) {
            return Err(format!("{name} derives from itself"));
        }
        self.busy.push(name.to_string());
        let ct = self.read_complex(raw, raw.complex[name])?;
        self.busy.pop();
        self.complex[idx] = ComplexType {
            name: Some(name.to_string()),
            ..ct
        };
        self.done[idx] = true;
        Ok(idx)
    }

    /// Reads a complex type, with what it derives from merged in.
    ///
    /// # Arguments
    ///
    /// * `raw` - The schema's top-level declarations.
    /// * `e`   - The `complexType` element.
    ///
    /// # Returns
    ///
    /// The type, with no name; the caller gives it one if it has one.
    ///
    /// # Errors
    ///
    /// Says what it holds that the editor does not read.
    fn read_complex(
        &mut self,
        raw: &Raw,
        e: &Element,
    ) -> Result<ComplexType, SchemaError> {
        let mut ct = ComplexType {
            name: None,
            is_abstract: e.get("abstract").as_deref() == Some("true"),
            mixed: e.get("mixed").as_deref() == Some("true"),
            base: None,
            content: None,
            attrs: Vec::new(),
            text: None,
            doc: doc_of(e),
        };
        for c in e.elements() {
            match c.local() {
                "annotation" => {}
                "sequence" | "choice" => {
                    ct.content = Some(self.read_particle(raw, c)?);
                }
                "attribute" => ct.attrs.push(self.read_attr(c)?),
                "complexContent" => {
                    if c.get("mixed").as_deref() == Some("true") {
                        ct.mixed = true;
                    }
                    let d = derivation(c)?;
                    let base = attr_str(d, "base");
                    let base_name = local_name(base);
                    let bidx = self.complex_named(raw, base_name)?;
                    let b = self.complex[bidx].clone();
                    ct.base = Some(base_name.to_string());
                    ct.mixed |= b.mixed;
                    let mut own = None;
                    let mut own_attrs = Vec::new();
                    for x in d.elements() {
                        match x.local() {
                            "annotation" => {}
                            "sequence" | "choice" => {
                                own = Some(self.read_particle(raw, x)?)
                            }
                            "attribute" => own_attrs.push(self.read_attr(x)?),
                            other => {
                                return Err(format!(
                                    "{other} in a derivation is not read"
                                ))
                            }
                        }
                    }
                    if d.local() == "extension" {
                        ct.content = match (b.content, own) {
                            (None, o) => o,
                            (b, None) => b,
                            (Some(b), Some(o)) => Some(Particle::Sequence(
                                vec![b, o],
                                Occurs::ONCE,
                            )),
                        };
                        ct.attrs = b.attrs;
                        merge_attrs(&mut ct.attrs, own_attrs);
                    } else {
                        // A restriction says its content again in full.
                        ct.content = own;
                        ct.attrs = b.attrs;
                        merge_attrs(&mut ct.attrs, own_attrs);
                    }
                    ct.text = b.text;
                }
                "simpleContent" => {
                    let d = derivation(c)?;
                    let base = local_name(attr_str(d, "base"));
                    ct.base = Some(base.to_string());
                    if let Some(sr) = self.simple_ref(base) {
                        ct.text = Some(sr);
                    } else {
                        let bidx = self.complex_named(raw, base)?;
                        let b = self.complex[bidx].clone();
                        ct.text = b.text;
                        ct.attrs = b.attrs;
                    }
                    let mut own_attrs = Vec::new();
                    for x in d.elements() {
                        match x.local() {
                            "annotation" => {}
                            "attribute" => own_attrs.push(self.read_attr(x)?),
                            other => {
                                return Err(format!(
                                    "{other} in simple content is not read"
                                ))
                            }
                        }
                    }
                    merge_attrs(&mut ct.attrs, own_attrs);
                }
                other => {
                    return Err(format!(
                        "{other} in a complex type is not read"
                    ))
                }
            }
        }
        Ok(ct)
    }

    /// Reads an element, sequence or choice inside a complex type.
    ///
    /// # Arguments
    ///
    /// * `raw` - The schema's top-level declarations.
    /// * `e`   - The `element`, `sequence` or `choice` element.
    ///
    /// # Returns
    ///
    /// The particle.
    ///
    /// # Errors
    ///
    /// Says what it holds that the editor does not read.
    fn read_particle(
        &mut self,
        raw: &Raw,
        e: &Element,
    ) -> Result<Particle, SchemaError> {
        let occurs = occurs_of(e)?;
        match e.local() {
            "element" => {
                let idx = if let Some(r) = e.get("ref") {
                    // The only global element is SpaceSystem, which holds
                    // itself.
                    if local_name(&r) != "SpaceSystem" {
                        return Err(format!("a reference to {r} is not read"));
                    }
                    self.global_space_system(raw)?
                } else {
                    self.read_element(raw, e)?
                };
                Ok(Particle::Element(idx, occurs))
            }
            "sequence" | "choice" => {
                let mut parts = Vec::new();
                for c in e.elements() {
                    match c.local() {
                        "annotation" => {}
                        "element" | "sequence" | "choice" => {
                            parts.push(self.read_particle(raw, c)?)
                        }
                        other => {
                            return Err(format!(
                                "{other} in a {} is not read",
                                e.local()
                            ))
                        }
                    }
                }
                Ok(if e.local() == "sequence" {
                    Particle::Sequence(parts, occurs)
                } else {
                    Particle::Choice(parts, occurs)
                })
            }
            other => Err(format!("{other} as a particle is not read")),
        }
    }

    /// The global SpaceSystem declaration, read the first time it is
    /// asked for.  Its type is named, so reading it reads no further and a
    /// SpaceSystem inside a SpaceSystem finds this one.
    ///
    /// # Arguments
    ///
    /// * `raw` - The schema's top-level declarations.
    ///
    /// # Returns
    ///
    /// The declaration's index in [`Schema::elements`].
    ///
    /// # Errors
    ///
    /// Fails if the schema has no global SpaceSystem, or if the declaration
    /// holds what the editor does not read.
    fn global_space_system(&mut self, raw: &Raw) -> Result<usize, SchemaError> {
        if let Some(i) = self.global {
            return Ok(i);
        }
        let Some(g) = raw.elements.get("SpaceSystem") else {
            return Err("there is no global SpaceSystem element".to_string());
        };
        let i = self.read_element(raw, g)?;
        self.global = Some(i);
        Ok(i)
    }

    /// Reads an element declaration, and its type if that is defined
    /// inside it, and adds it to [`Schema::elements`].
    ///
    /// # Arguments
    ///
    /// * `raw` - The schema's top-level declarations.
    /// * `e`   - The `element` element.
    ///
    /// # Returns
    ///
    /// The declaration's index in [`Schema::elements`].
    ///
    /// # Errors
    ///
    /// Fails if its type is not defined, or it holds what the editor does
    /// not read.
    fn read_element(
        &mut self,
        raw: &Raw,
        e: &Element,
    ) -> Result<usize, SchemaError> {
        let name = attr_str(e, "name").to_string();
        let idx = self.elements.len();
        self.elements.push(ElementDecl {
            name: name.clone(),
            ty: TypeRef::Simple(SimpleRef::Builtin(Builtin::String)),
            default: e.get("default"),
            fixed: e.get("fixed"),
            keys: Vec::new(),
            doc: doc_of(e),
        });
        let mut keys = Vec::new();
        for c in e.elements() {
            if c.local() == "key" {
                keys.push(read_key(c)?);
            }
        }
        // Set before the type is read, so that SpaceSystem's own reference
        // to itself finds it.
        self.elements[idx].keys = keys;
        let ty = if let Some(t) = e.get("type") {
            let t = local_name(&t).to_string();
            if let Some(sr) = self.simple_ref(&t) {
                TypeRef::Simple(sr)
            } else if let Some(&ci) = self.complex_names.get(&t) {
                TypeRef::Complex(ci)
            } else {
                return Err(format!("{name} is of a type {t} not defined"));
            }
        } else {
            let mut ty = None;
            for c in e.elements() {
                match c.local() {
                    "complexType" => {
                        let ct = self.read_complex(raw, c)?;
                        self.complex.push(ct);
                        ty = Some(TypeRef::Complex(self.complex.len() - 1));
                    }
                    "simpleType" => {
                        let st = self.read_simple(c)?;
                        self.simple.push(st);
                        ty = Some(TypeRef::Simple(SimpleRef::Defined(
                            self.simple.len() - 1,
                        )));
                    }
                    _ => {}
                }
            }
            ty.unwrap_or(TypeRef::Simple(SimpleRef::Builtin(Builtin::String)))
        };
        self.elements[idx].ty = ty;
        Ok(idx)
    }

    /// Reads an attribute declaration.
    ///
    /// # Arguments
    ///
    /// * `e` - The `attribute` element.
    ///
    /// # Returns
    ///
    /// The declaration.
    ///
    /// # Errors
    ///
    /// Fails if its type is not defined or not simple, or it refers to an
    /// attribute other than `xml:base`, the only one the schemas refer to.
    fn read_attr(&mut self, e: &Element) -> Result<AttrDecl, SchemaError> {
        let (name, ty) = if let Some(r) = e.get("ref") {
            if r != "xml:base" {
                return Err(format!("an attribute ref {r} is not read"));
            }
            (r, SimpleRef::Builtin(Builtin::AnyUri))
        } else {
            let name = attr_str(e, "name").to_string();
            let ty = if let Some(t) = e.get("type") {
                let t = local_name(&t);
                self.simple_ref(t).ok_or_else(|| {
                    format!("attribute {name} is of a type {t} not defined")
                })?
            } else if let Some(st) =
                e.elements().find(|c| c.local() == "simpleType")
            {
                let st = self.read_simple(st)?;
                self.simple.push(st);
                SimpleRef::Defined(self.simple.len() - 1)
            } else {
                SimpleRef::Builtin(Builtin::String)
            };
            (name, ty)
        };
        Ok(AttrDecl {
            name,
            ty,
            required: e.get("use").as_deref() == Some("required"),
            default: e.get("default"),
            fixed: e.get("fixed"),
            doc: doc_of(e),
        })
    }

    /// Reads a simple type, adding to [`Schema::simple`] any defined inside
    /// it.
    ///
    /// # Arguments
    ///
    /// * `e` - The `simpleType` element.
    ///
    /// # Returns
    ///
    /// The type, with no name; the caller gives it one if it has one.
    ///
    /// # Errors
    ///
    /// Fails if it restricts or unites a type that is not simple, has a
    /// facet the editor does not read, or has a pattern it cannot check.
    fn read_simple(&mut self, e: &Element) -> Result<SimpleType, SchemaError> {
        let mut st = SimpleType {
            name: None,
            base: None,
            enumeration: Vec::new(),
            patterns: Vec::new(),
            min_inclusive: None,
            max_inclusive: None,
            union: Vec::new(),
            doc: doc_of(e),
        };
        for c in e.elements() {
            match c.local() {
                "annotation" => {}
                "restriction" => {
                    let base = local_name(attr_str(c, "base"));
                    st.base = Some(self.simple_ref(base).ok_or_else(|| {
                        format!("a restriction of {base}, which is not simple")
                    })?);
                    for f in c.elements() {
                        let v = f.get("value").unwrap_or_default();
                        match f.local() {
                            "annotation" => {}
                            "enumeration" => st.enumeration.push(v),
                            "pattern" => {
                                let re = xsd_regex(&v)?;
                                st.patterns.push((v, re));
                            }
                            "minInclusive" => st.min_inclusive = Some(v),
                            "maxInclusive" => st.max_inclusive = Some(v),
                            other => {
                                return Err(format!(
                                    "the facet {other} is not read"
                                ))
                            }
                        }
                    }
                }
                "union" => {
                    for m in e
                        .get("memberTypes")
                        .or_else(|| c.get("memberTypes"))
                        .unwrap_or_default()
                        .split_whitespace()
                    {
                        let m = local_name(m);
                        st.union.push(self.simple_ref(m).ok_or_else(|| {
                            format!("a union of {m}, which is not simple")
                        })?);
                    }
                    for inner in c.elements() {
                        if inner.local() == "simpleType" {
                            let t = self.read_simple(inner)?;
                            self.simple.push(t);
                            st.union.push(SimpleRef::Defined(
                                self.simple.len() - 1,
                            ));
                        }
                    }
                }
                other => {
                    return Err(format!("{other} in a simple type is not read"))
                }
            }
        }
        Ok(st)
    }

    /// Finds a simple type by name, XML Schema's own or the schema's.
    ///
    /// # Arguments
    ///
    /// * `name` - The name, without a prefix.
    ///
    /// # Returns
    ///
    /// The type, or `None` if no simple type has that name.
    fn simple_ref(&self, name: &str) -> Option<SimpleRef> {
        if let Some(b) = Builtin::of(name) {
            return Some(SimpleRef::Builtin(b));
        }
        self.simple_names.get(name).map(|&i| SimpleRef::Defined(i))
    }

    /// A complex type by name.
    pub fn complex_type(&self, name: &str) -> Option<&ComplexType> {
        self.complex_names.get(name).map(|&i| &self.complex[i])
    }

    /// The automaton of a complex type's content.
    ///
    /// # Arguments
    ///
    /// * `complex` - The type's index in [`Schema::complex`].
    ///
    /// # Returns
    ///
    /// The automaton.
    pub fn automaton(&self, complex: usize) -> &Automaton {
        &self.automata.get_or_init(|| {
            self.complex
                .iter()
                .map(|c| Automaton::of(c.content.as_ref()))
                .collect()
        })[complex]
    }

    /// The global `SpaceSystem` declaration.
    pub fn root(&self) -> &ElementDecl {
        &self.elements[self.root]
    }

    /// Finds the complex type of an element declaration, if it has one.
    ///
    /// # Arguments
    ///
    /// * `decl` - The declaration's index in [`Schema::elements`].
    ///
    /// # Returns
    ///
    /// The type, or `None` if the element's type is simple.
    pub fn complex_of(&self, decl: usize) -> Option<&ComplexType> {
        match self.elements[decl].ty {
            TypeRef::Complex(i) => Some(&self.complex[i]),
            TypeRef::Simple(_) => None,
        }
    }

    /// Names a simple type, for messages.
    ///
    /// # Arguments
    ///
    /// * `r` - The type.
    ///
    /// # Returns
    ///
    /// Its name; for an anonymous type, the name of what it restricts, or
    /// "a union".
    pub fn simple_name(&self, r: SimpleRef) -> String {
        match r {
            SimpleRef::Builtin(b) => b.name().to_string(),
            SimpleRef::Defined(i) => match &self.simple[i].name {
                Some(n) => n.clone(),
                None => match self.simple[i].base {
                    Some(b) => self.simple_name(b),
                    None => "a union".to_string(),
                },
            },
        }
    }

    /// Lists the values a simple type allows, if it is an enumeration, so
    /// that they can be offered to choose from.
    ///
    /// # Arguments
    ///
    /// * `r` - The type.
    ///
    /// # Returns
    ///
    /// The values, `true` and `false` for a boolean, or none if the type is
    /// not an enumeration.
    pub fn enumeration(&self, r: SimpleRef) -> Vec<String> {
        match r {
            SimpleRef::Builtin(Builtin::Boolean) => {
                vec!["true".to_string(), "false".to_string()]
            }
            SimpleRef::Builtin(_) => Vec::new(),
            SimpleRef::Defined(i) => {
                let st = &self.simple[i];
                if !st.enumeration.is_empty() {
                    st.enumeration.clone()
                } else if let Some(b) = st.base {
                    self.enumeration(b)
                } else {
                    Vec::new()
                }
            }
        }
    }

    /// Checks a value against a simple type.
    ///
    /// # Arguments
    ///
    /// * `r`     - The type.
    /// * `value` - The value, with its references expanded.
    ///
    /// # Errors
    ///
    /// Says what about the value the type does not allow, and what it does.
    pub fn check_value(&self, r: SimpleRef, value: &str) -> Result<(), String> {
        match r {
            SimpleRef::Builtin(b) => check_builtin(b, value),
            SimpleRef::Defined(i) => {
                let st = &self.simple[i];
                if !st.union.is_empty() {
                    let mut why = Vec::new();
                    for m in &st.union {
                        match self.check_value(*m, value) {
                            Ok(()) => return Ok(()),
                            Err(e) => why.push(e),
                        }
                    }
                    return Err(format!(
                        "{value:?} is none of the kinds of value {} may be: \
                         {}",
                        self.simple_name(r),
                        why.join("; ")
                    ));
                }
                let base =
                    st.base.unwrap_or(SimpleRef::Builtin(Builtin::String));
                self.check_value(base, value)?;
                let v = if self.preserves_space(base) {
                    value.to_string()
                } else {
                    collapse(value)
                };
                if !st.enumeration.is_empty() && !st.enumeration.contains(&v) {
                    return Err(format!(
                        "{v:?} is not one of the values allowed: {}",
                        st.enumeration.join(", ")
                    ));
                }
                for (text, re) in &st.patterns {
                    if !re.is_match(&v) {
                        return Err(format!(
                            "{v:?} is not of the form {}, which is the \
                             pattern {text}",
                            self.simple_name(r)
                        ));
                    }
                }
                if let Some(min) = &st.min_inclusive {
                    if compare_numbers(&v, min)
                        == Some(std::cmp::Ordering::Less)
                    {
                        return Err(format!("{v} is below the least, {min}"));
                    }
                }
                if let Some(max) = &st.max_inclusive {
                    if compare_numbers(&v, max)
                        == Some(std::cmp::Ordering::Greater)
                    {
                        return Err(format!("{v} is above the most, {max}"));
                    }
                }
                Ok(())
            }
        }
    }

    /// Says whether whitespace in a value of a simple type is kept as
    /// written, as it is for a string and what restricts one.
    ///
    /// # Arguments
    ///
    /// * `r` - The type.
    ///
    /// # Returns
    ///
    /// `true` if it is kept, `false` if it is collapsed.
    fn preserves_space(&self, r: SimpleRef) -> bool {
        match r {
            SimpleRef::Builtin(b) => b.preserves_space(),
            SimpleRef::Defined(i) => match self.simple[i].base {
                Some(b) => self.preserves_space(b),
                None => false,
            },
        }
    }
}

/// Merges a derived type's own attributes into its base's: one of the same
/// name replaces the base's, and the rest are added.
///
/// # Arguments
///
/// * `into` - The base's attributes, which become the derived type's.
/// * `own`  - The attributes the derived type declares.
fn merge_attrs(into: &mut Vec<AttrDecl>, own: Vec<AttrDecl>) {
    for a in own {
        if let Some(old) = into.iter_mut().find(|o| o.name == a.name) {
            *old = a;
        } else {
            into.push(a);
        }
    }
}

/// Finds the derivation in complex or simple content.
fn derivation(c: &Element) -> Result<&Element, SchemaError> {
    c.elements()
        .find(|d| matches!(d.local(), "extension" | "restriction"))
        .ok_or_else(|| format!("a {} with no derivation", c.local()))
}

/// An attribute's value as written, or `""` if it is not given.
fn attr_str<'a>(e: &'a Element, name: &str) -> &'a str {
    e.attr(name).map_or("", |a| a.raw.as_str())
}

/// Reads how many times a particle may occur.
///
/// # Arguments
///
/// * `e` - The particle's element.
///
/// # Returns
///
/// Its `minOccurs` and `maxOccurs`, each 1 if not given.
///
/// # Errors
///
/// Fails if either is not a whole number, or `unbounded` for the most.
fn occurs_of(e: &Element) -> Result<Occurs, SchemaError> {
    let min = match e.get("minOccurs") {
        Some(v) => v.parse().map_err(|_| format!("minOccurs {v}"))?,
        None => 1,
    };
    let max = match e.get("maxOccurs").as_deref() {
        Some("unbounded") => None,
        Some(v) => Some(v.parse().map_err(|_| format!("maxOccurs {v}"))?),
        None => Some(1),
    };
    Ok(Occurs { min, max })
}

/// Gathers the documentation an XSD declaration gives, as one paragraph.
///
/// # Arguments
///
/// * `e` - The declaration.
///
/// # Returns
///
/// The text of its `documentation` elements, whitespace collapsed, or
/// nothing if it has none.
fn doc_of(e: &Element) -> String {
    let mut out = Vec::new();
    for a in e.elements().filter(|a| a.local() == "annotation") {
        for d in a.elements().filter(|d| d.local() == "documentation") {
            out.push(collapse(&d.text()));
        }
    }
    out.join(" ")
}

/// Reads a key declaration.
///
/// # Arguments
///
/// * `e` - The `key` element.
///
/// # Returns
///
/// The key.
///
/// # Errors
///
/// Fails if it has no field.
fn read_key(e: &Element) -> Result<Key, SchemaError> {
    let mut selector = Vec::new();
    let mut field = None;
    for c in e.elements() {
        match c.local() {
            "selector" => {
                for alt in c.get("xpath").unwrap_or_default().split('|') {
                    selector.push(
                        alt.trim()
                            .split('/')
                            .map(|s| step_of(s.trim()))
                            .collect::<Vec<_>>(),
                    );
                }
            }
            "field" => {
                field = Some(step_of(&c.get("xpath").unwrap_or_default()))
            }
            _ => {}
        }
    }
    Ok(Key {
        name: e.get("name").unwrap_or_default(),
        selector,
        field: field.ok_or("a key with no field")?,
        doc: doc_of(e),
    })
}

/// Reads one step of a key's XPath.
///
/// # Arguments
///
/// * `s` - The step, trimmed.
///
/// # Returns
///
/// The step.
fn step_of(s: &str) -> Step {
    if let Some(a) = s.strip_prefix('@') {
        Step::Attribute(a.to_string())
    } else if s == "*" {
        Step::Any
    } else if let Some(n) = s.strip_prefix("xtce:") {
        Step::Element(n.to_string())
    } else {
        Step::Unqualified(s.to_string())
    }
}

/// Collapses whitespace as XML Schema does for every type but `string`:
/// runs become one space, and none at either end.
///
/// # Arguments
///
/// * `s` - The value as given.
///
/// # Returns
///
/// The value collapsed.
pub fn collapse(s: &str) -> String {
    s.split(crate::tree::is_xml_space)
        .filter(|w| !w.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

/// Turns an XSD pattern into a regular expression that matches it.  An XSD
/// pattern is anchored at both ends; the escapes the XTCE schemas use mean
/// the same in both languages.
///
/// # Arguments
///
/// * `p` - The pattern, as the schema writes it.
///
/// # Returns
///
/// The regular expression.
///
/// # Errors
///
/// Fails if the pattern uses an escape only XSD has, or is not a regular
/// expression the `regex` crate reads.
fn xsd_regex(p: &str) -> Result<Regex, SchemaError> {
    if p.contains("\\i") || p.contains("\\c") || p.contains("\\p{Is") {
        return Err(format!("the pattern {p} uses XSD's own escapes"));
    }
    Regex::new(&format!("^(?:{p})$")).map_err(|e| format!("pattern {p}: {e}"))
}

/// Compares two numbers, as whole numbers if both are, so that bounds
/// beyond what a float holds exactly compare rightly, and as floats if not.
///
/// # Arguments
///
/// * `a` - The first number.
/// * `b` - The second number.
///
/// # Returns
///
/// How `a` compares with `b`, or `None` if either is not a number.
fn compare_numbers(a: &str, b: &str) -> Option<std::cmp::Ordering> {
    match (a.parse::<i128>(), b.parse::<i128>()) {
        (Ok(x), Ok(y)) => Some(x.cmp(&y)),
        _ => {
            let x: f64 = a.parse().ok()?;
            let y: f64 = b.parse().ok()?;
            x.partial_cmp(&y)
        }
    }
}

/// Checks that a value is a whole number in a range.
///
/// # Arguments
///
/// * `v`    - The value, whitespace collapsed.
/// * `lo`   - The least it may be.
/// * `hi`   - The greatest it may be.
/// * `what` - What the type allows, which a message ends with.
///
/// # Errors
///
/// Fails, saying `what`, if the value is not digits with an optional sign,
/// or is out of the range.
fn integer_in(v: &str, lo: i128, hi: i128, what: &str) -> Result<(), String> {
    let digits = v.strip_prefix(['+', '-']).unwrap_or(v);
    if digits.is_empty() || !digits.bytes().all(|b| b.is_ascii_digit()) {
        return Err(format!("{v:?} is not a whole number; {what}"));
    }
    match v.parse::<i128>() {
        Ok(n) if n >= lo && n <= hi => Ok(()),
        _ => Err(format!("{v} is out of range; {what}")),
    }
}

/// Checks a value against one of XML Schema's own types.
///
/// # Arguments
///
/// * `b`     - The type.
/// * `value` - The value, with its references expanded.
///
/// # Errors
///
/// Fails, saying what the type allows, with an example, if the value is
/// not of it.
fn check_builtin(b: Builtin, value: &str) -> Result<(), String> {
    let v = if b.preserves_space() {
        value.to_string()
    } else {
        collapse(value)
    };
    match b {
        Builtin::String | Builtin::NormalizedString | Builtin::Token => Ok(()),
        Builtin::AnyUri => Ok(()),
        Builtin::Boolean => match v.as_str() {
            "true" | "false" | "1" | "0" => Ok(()),
            _ => Err(format!("{v:?} is not true or false")),
        },
        Builtin::Double => {
            let ok = matches!(v.as_str(), "INF" | "-INF" | "NaN" | "+INF")
                || (!v.is_empty()
                    && v.bytes().all(|c| {
                        c.is_ascii_digit()
                            || matches!(c, b'+' | b'-' | b'.' | b'e' | b'E')
                    })
                    && v.parse::<f64>().is_ok());
            if ok {
                Ok(())
            } else {
                Err(format!("{v:?} is not a number, such as 1.5 or -2E3"))
            }
        }
        Builtin::Decimal => {
            let body = v.strip_prefix(['+', '-']).unwrap_or(&v);
            let ok = !body.is_empty()
                && body.bytes().all(|c| c.is_ascii_digit() || c == b'.')
                && body.matches('.').count() <= 1
                && body != ".";
            if ok {
                Ok(())
            } else {
                Err(format!("{v:?} is not a decimal number, such as 2.5"))
            }
        }
        Builtin::Integer => {
            integer_in(&v, i128::MIN, i128::MAX, "write digits")
        }
        Builtin::Long => integer_in(
            &v,
            i64::MIN as i128,
            i64::MAX as i128,
            "it must fit in 64 bits",
        ),
        Builtin::Int => integer_in(
            &v,
            i32::MIN as i128,
            i32::MAX as i128,
            "it must fit in 32 bits",
        ),
        Builtin::NonNegativeInteger => {
            integer_in(&v, 0, i128::MAX, "it must be 0 or more")
        }
        Builtin::PositiveInteger => {
            integer_in(&v, 1, i128::MAX, "it must be 1 or more")
        }
        Builtin::UnsignedShort => {
            integer_in(&v, 0, 65535, "it must be from 0 to 65535")
        }
        Builtin::HexBinary => {
            if v.len() % 2 == 0 && v.bytes().all(|c| c.is_ascii_hexdigit()) {
                Ok(())
            } else {
                Err(format!(
                    "{v:?} is not hexadecimal bytes: an even number of the \
                     digits 0-9 and A-F, with no 0x"
                ))
            }
        }
        Builtin::Date => check_re(
            &v,
            r"^-?\d{4,}-\d\d-\d\d(Z|[+-]\d\d:\d\d)?$",
            "a date, such as 2026-11-10",
        ),
        Builtin::DateTime => check_re(
            &v,
            concat!(
                r"^-?\d{4,}-\d\d-\d\d",
                r"T\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)?$",
            ),
            "a date and time, such as 2026-11-10T12:00:00Z",
        ),
        Builtin::Duration => check_re(
            &v,
            concat!(
                r"^-?P(?:\d+Y)?(?:\d+M)?(?:\d+D)?",
                r"(?:T(?:\d+H)?(?:\d+M)?(?:\d+(?:\.\d+)?S)?)?$",
            ),
            "a duration, such as PT1.5S",
        )
        .and_then(|()| {
            if v.ends_with('P') || v.ends_with('T') {
                Err(format!("{v:?} is not a duration, such as PT1.5S"))
            } else {
                Ok(())
            }
        }),
    }
}

/// Checks a value against a regular expression.
///
/// # Arguments
///
/// * `v`    - The value.
/// * `re`   - The regular expression, anchored at both ends.
/// * `what` - What a value of the type is, with an example, for the
///            message.
///
/// # Errors
///
/// Fails, saying `what`, if the value does not match.
///
/// # Panics
///
/// Panics if `re` is not a regular expression; every caller gives a fixed
/// one, which the tests run.
fn check_re(v: &str, re: &str, what: &str) -> Result<(), String> {
    if Regex::new(re).expect("a fixed pattern").is_match(v) {
        Ok(())
    } else {
        Err(format!("{v:?} is not {what}"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_schema_is_read() {
        for v in Version::ALL {
            let s = v.schema();
            assert_eq!(s.root().name, "SpaceSystem");
            assert!(!s.root().keys.is_empty(), "{v}");
            assert!(s.complex_type("SpaceSystemType").is_some(), "{v}");
        }
    }

    #[test]
    fn checks_values() {
        let s = Version::V1_3.schema();
        let ct = s.complex_type("IntegerDataEncodingType").unwrap();
        let enc = ct.attrs.iter().find(|a| a.name == "encoding").unwrap();
        assert!(s.check_value(enc.ty, "twosComplement").is_ok());
        assert!(s.check_value(enc.ty, "twos").is_err());
        let size = ct.attrs.iter().find(|a| a.name == "sizeInBits").unwrap();
        assert!(s.check_value(size.ty, "16").is_ok());
        assert!(s.check_value(size.ty, "0").is_err());
        assert!(s.check_value(size.ty, "x").is_err());
    }

    #[test]
    fn checks_builtins() {
        assert!(check_builtin(Builtin::Duration, "PT1.5S").is_ok());
        assert!(check_builtin(Builtin::Duration, "P").is_err());
        assert!(check_builtin(Builtin::HexBinary, "0aFF").is_ok());
        assert!(check_builtin(Builtin::HexBinary, "0x0a").is_err());
        assert!(check_builtin(Builtin::Double, "-2E3").is_ok());
        assert!(check_builtin(Builtin::Double, "two").is_err());
        assert!(check_builtin(Builtin::Boolean, " true ").is_ok());
    }

    #[test]
    fn names_versions_by_namespace() {
        for v in Version::ALL {
            assert_eq!(Version::of_namespace(v.namespace()), Some(v));
            assert_eq!(Version::parse(&v.to_string()), Some(v));
        }
    }
}
