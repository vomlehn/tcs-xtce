//! What the names in a mission's documents refer to.
//!
//! An XTCE document names its definitions -- parameters, their types,
//! containers, commands -- and refers to them by name from elsewhere, in
//! the same document or in another document of the mission.  A mission
//! split over several documents is read as Yamcs and the `tcs-xtce`
//! compiler read it: each document's root SpaceSystem sits at the top of
//! one tree, so `/PUS/pus-tm` names a container in the document whose root
//! is `PUS`.
//!
//! How a reference finds what it names is the compiler's rule, `resolve` in
//! `python/tcs_xtce/compiler/compile.py`, followed here exactly so that the
//! editor and the compiler agree about what a document means: an absolute
//! path from a root, a relative path with `.` and `..`, or a name looked up
//! in the reference's SpaceSystem and then in each one enclosing it.
//!
//! The schema cannot say whether a reference names anything, so one that
//! names nothing is a warning, not an error: a mission's documents may be
//! opened one at a time.

use std::collections::HashMap;

use crate::tree::{Document, Element, Id};
use crate::validate::{Problem, Severity};

/// What a definition is, as far as references are concerned: each kind is
/// looked up in a table of its own.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum Kind {
    /// A Parameter.
    Parameter,
    /// A parameter type: any element of a ParameterTypeSet.
    ParameterType,
    /// An argument type: any element of an ArgumentTypeSet.
    ArgumentType,
    /// A SequenceContainer, CommandContainer or a MetaCommand's
    /// CommandContainer.
    Container,
    /// A MetaCommand or BlockMetaCommand.
    MetaCommand,
    /// Any element of an AlgorithmSet.
    Algorithm,
    /// Any element of a StreamSet.
    Stream,
    /// A Message.
    Message,
    /// A Service.
    Service,
    /// A SpaceSystem.
    SpaceSystem,
}

impl Kind {
    /// The kind of definition a reference attribute names.
    ///
    /// # Arguments
    ///
    /// * `attr`   - The attribute's name.
    /// * `parent` - The local name of the element it is on, which decides
    ///              the kind of a type reference.
    /// * `in_cmd` - Whether the element is inside CommandMetaData.
    ///
    /// # Returns
    ///
    /// The kind, or `None` for references that are not looked up in the
    /// SpaceSystem tree, such as an argument named within its command.
    pub fn of_attr(attr: &str, parent: &str, in_cmd: bool) -> Option<Kind> {
        Some(match attr {
            "parameterRef" | "outputParameterRef" => Kind::Parameter,
            "parameterTypeRef" => Kind::ParameterType,
            "argumentTypeRef" => Kind::ArgumentType,
            "containerRef" | "triggerContainer" => Kind::Container,
            "metaCommandRef" => Kind::MetaCommand,
            "streamRef" | "decodedStreamRef" | "encodedStreamRef" => {
                Kind::Stream
            }
            "messageRef" => Kind::Message,
            "serviceRef" => Kind::Service,
            "spaceSystemAtRisk" | "scopeToSpaceSystem" => Kind::SpaceSystem,
            // A type reference names a type of the same side as the type
            // it is written in.
            "baseType" | "typeRef" | "arrayTypeRef" => {
                if parent.ends_with("ArgumentType") || in_cmd {
                    Kind::ArgumentType
                } else {
                    Kind::ParameterType
                }
            }
            _ => return None,
        })
    }

    /// The word for the kind in messages.
    ///
    /// # Returns
    ///
    /// The word.
    pub fn word(self) -> &'static str {
        match self {
            Kind::Parameter => "parameter",
            Kind::ParameterType => "parameter type",
            Kind::ArgumentType => "argument type",
            Kind::Container => "container",
            Kind::MetaCommand => "command",
            Kind::Algorithm => "algorithm",
            Kind::Stream => "stream",
            Kind::Message => "message",
            Kind::Service => "service",
            Kind::SpaceSystem => "SpaceSystem",
        }
    }
}

/// Where something is in a mission: which document, and which element.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct Loc {
    /// The document's index among the mission's.
    pub doc: usize,
    /// The element.
    pub id: Id,
}

/// A definition a reference can name.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Def {
    /// Where it is.
    pub loc: Loc,
    /// What kind it is.
    pub kind: Kind,
    /// The names of the SpaceSystems from the root down to the one it is
    /// in, then its own name.
    pub key: Vec<String>,
}

/// One reference.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Ref {
    /// Where it is written: the element the attribute is on.
    pub loc: Loc,
    /// The attribute it is written in, or `None` for an element's text.
    pub attr: Option<String>,
    /// The reference as written.
    pub text: String,
    /// What kind of definition it names.
    pub kind: Kind,
    /// The SpaceSystem it is written in, as names from the root.
    pub space: Vec<String>,
    /// The definition it names, by its index in [`Index::defs`], if any.
    pub target: Option<usize>,
}

/// Every definition and reference in a mission.
#[derive(Debug, Clone, Default)]
pub struct Index {
    /// The definitions, in document order.
    pub defs: Vec<Def>,
    /// Each definition's index in `defs`, by its kind and key.
    by_key: HashMap<(Kind, Vec<String>), usize>,
    /// The references, in document order.
    pub refs: Vec<Ref>,
    /// The names of every aggregate member, which a size inside a type
    /// may name, as the compiler resolves it against the values decoded
    /// before it.
    members: std::collections::HashSet<String>,
}

/// The name the compiler takes, in the Yamcs dialect, for a size the
/// schema asks for and a leading size replaces: `YAMCS_IGNORE` in
/// `python/tcs_xtce/compiler/compile.py`.  It names nothing.
pub const YAMCS_IGNORE: &str = "_yamcs_ignore";

impl Index {
    /// Indexes a mission's documents.
    ///
    /// # Arguments
    ///
    /// * `docs` - The documents, in the order the mission lists them.
    ///
    /// # Returns
    ///
    /// The index, every reference resolved where it can be.
    pub fn of(docs: &[&Document]) -> Index {
        let mut ix = Index::default();
        for (d, doc) in docs.iter().enumerate() {
            let root = doc.root();
            ix.space(d, root, &mut Vec::new(), false);
        }
        for i in 0..ix.refs.len() {
            let r = &ix.refs[i];
            let t = ix.resolve(&r.text, &r.space, r.kind);
            ix.refs[i].target = t;
        }
        ix
    }

    /// Indexes a SpaceSystem and everything in it.
    ///
    /// # Arguments
    ///
    /// * `doc`    - The document's index.
    /// * `e`      - The SpaceSystem.
    /// * `path`   - The names of the SpaceSystems enclosing it, which this
    ///              extends while it is indexed.
    /// * `in_cmd` - Whether it is inside CommandMetaData, which it never
    ///              is; passed on to the walk below it.
    fn space(
        &mut self,
        doc: usize,
        e: &Element,
        path: &mut Vec<String>,
        in_cmd: bool,
    ) {
        let name = e.get("name").unwrap_or_default();
        let mut key = path.clone();
        key.push(name.clone());
        self.define(doc, e, Kind::SpaceSystem, key);
        path.push(name);
        self.walk(doc, e, path, in_cmd, 0);
        path.pop();
    }

    /// Records a definition.
    ///
    /// # Arguments
    ///
    /// * `doc`  - The document's index.
    /// * `e`    - The element that defines it.
    /// * `kind` - What kind it is.
    /// * `key`  - Its SpaceSystem's path and its own name.
    fn define(
        &mut self,
        doc: usize,
        e: &Element,
        kind: Kind,
        key: Vec<String>,
    ) {
        let i = self.defs.len();
        // The first definition of a key wins, as the compiler's tables
        // keep the first; a second is reported by `problems`.
        self.by_key.entry((kind, key.clone())).or_insert(i);
        self.defs.push(Def {
            loc: Loc { doc, id: e.id },
            kind,
            key,
        });
    }

    /// Indexes the elements below one, within one SpaceSystem.
    ///
    /// # Arguments
    ///
    /// * `doc`    - The document's index.
    /// * `e`      - The element.
    /// * `path`   - The names of the SpaceSystems from the root to the one
    ///              `e` is in.
    /// * `in_cmd` - Whether `e` is inside CommandMetaData.
    /// * `depth`  - How far below the SpaceSystem `e` is.
    fn walk(
        &mut self,
        doc: usize,
        e: &Element,
        path: &mut Vec<String>,
        in_cmd: bool,
        depth: usize,
    ) {
        for a in &e.attrs {
            if let Some(kind) = Kind::of_attr(&a.name, e.local(), in_cmd) {
                self.refs.push(Ref {
                    loc: Loc { doc, id: e.id },
                    attr: Some(a.name.clone()),
                    text: a.value(),
                    kind,
                    space: path.clone(),
                    target: None,
                });
            }
        }
        if e.local() == "Member" {
            if let Some(n) = e.get("name") {
                self.members.insert(n);
            }
        }
        if e.local() == "MetaCommandRef" && depth > 0 {
            self.refs.push(Ref {
                loc: Loc { doc, id: e.id },
                attr: None,
                text: e.text().trim().to_string(),
                kind: Kind::MetaCommand,
                space: path.clone(),
                target: None,
            });
        }
        for c in e.elements() {
            if c.local() == "SpaceSystem" {
                self.space(doc, c, path, false);
                continue;
            }
            let set = e.local();
            let kind = match (set, c.local()) {
                ("ParameterTypeSet", _) => Some(Kind::ParameterType),
                ("ParameterSet", "Parameter") => Some(Kind::Parameter),
                ("ArgumentTypeSet", _) => Some(Kind::ArgumentType),
                ("ContainerSet", "SequenceContainer") => Some(Kind::Container),
                ("CommandContainerSet", "CommandContainer") => {
                    Some(Kind::Container)
                }
                ("MetaCommand", "CommandContainer") => Some(Kind::Container),
                ("MetaCommandSet", "MetaCommand" | "BlockMetaCommand") => {
                    Some(Kind::MetaCommand)
                }
                ("AlgorithmSet", _) => Some(Kind::Algorithm),
                ("StreamSet", _) => Some(Kind::Stream),
                ("MessageSet", "Message") => Some(Kind::Message),
                ("ServiceSet", "Service") => Some(Kind::Service),
                _ => None,
            };
            if let (Some(kind), Some(name)) = (kind, c.get("name")) {
                let mut key = path.clone();
                key.push(name);
                self.define(doc, c, kind, key);
            }
            let cmd = in_cmd || c.local() == "CommandMetaData";
            self.walk(doc, c, path, cmd, depth + 1);
        }
    }

    /// Finds what a reference names, by the compiler's rule.
    ///
    /// # Arguments
    ///
    /// * `text`  - The reference.
    /// * `space` - The SpaceSystem it is written in.
    /// * `kind`  - What kind of definition it names.
    ///
    /// # Returns
    ///
    /// The definition's index in [`Index::defs`], or `None`.
    pub fn resolve(
        &self,
        text: &str,
        space: &[String],
        kind: Kind,
    ) -> Option<usize> {
        let text = base_of(text.trim(), kind);
        let parts: Vec<&str> = text.split('/').collect();
        let look = |key: Vec<String>| self.by_key.get(&(kind, key)).copied();
        if text.starts_with('/') {
            let key: Vec<String> = parts[1..]
                .iter()
                .filter(|p| !p.is_empty())
                .map(|p| p.to_string())
                .collect();
            return look(key);
        }
        if parts.len() == 1 {
            for i in (1..=space.len()).rev() {
                let mut key = space[..i].to_vec();
                key.push(text.to_string());
                if let Some(d) = look(key) {
                    return Some(d);
                }
            }
            return None;
        }
        let starts: Vec<&[String]> = if parts[0] == "." || parts[0] == ".." {
            vec![space]
        } else {
            (0..=space.len()).rev().map(|i| &space[..i]).collect()
        };
        for start in starts {
            let mut base: Vec<String> = start.to_vec();
            for p in &parts[..parts.len() - 1] {
                match *p {
                    ".." => {
                        base.pop();
                    }
                    "" | "." => {}
                    other => base.push(other.to_string()),
                }
            }
            base.push(parts[parts.len() - 1].to_string());
            if let Some(d) = look(base) {
                return Some(d);
            }
        }
        None
    }

    /// The references that name a definition.
    ///
    /// # Arguments
    ///
    /// * `def` - The definition's index in [`Index::defs`].
    ///
    /// # Returns
    ///
    /// The references, in document order.
    pub fn usages(&self, def: usize) -> Vec<&Ref> {
        self.refs.iter().filter(|r| r.target == Some(def)).collect()
    }

    /// The definition an element is, if it is one.
    ///
    /// # Arguments
    ///
    /// * `loc` - The element.
    ///
    /// # Returns
    ///
    /// Its index in [`Index::defs`].
    pub fn def_at(&self, loc: Loc) -> Option<usize> {
        self.defs.iter().position(|d| d.loc == loc)
    }

    /// The warnings about one document's references and definitions:
    /// references that name nothing, and two definitions of one name.
    ///
    /// # Arguments
    ///
    /// * `doc` - The document's index.
    ///
    /// # Returns
    ///
    /// The warnings.
    pub fn problems(&self, doc: usize) -> Vec<Problem> {
        let mut out = Vec::new();
        for r in self.refs.iter().filter(|r| r.loc.doc == doc) {
            let member = r.kind == Kind::Parameter
                && self.members.contains(base_of(&r.text, r.kind));
            let ignored = r.text == YAMCS_IGNORE;
            if r.target.is_none() && !r.text.is_empty() && !member && !ignored {
                out.push(Problem {
                    id: r.loc.id,
                    attr: r.attr.clone(),
                    severity: Severity::Warning,
                    message: format!(
                        "{} names no {} in this mission; correct it, or \
                         open the document that defines it with this one",
                        r.text,
                        r.kind.word()
                    ),
                });
            }
        }
        for (i, d) in self.defs.iter().enumerate() {
            if d.loc.doc != doc {
                continue;
            }
            let first = self.by_key.get(&(d.kind, d.key.clone()));
            if first != Some(&i) {
                out.push(Problem {
                    id: d.loc.id,
                    attr: Some("name".to_string()),
                    severity: Severity::Warning,
                    message: format!(
                        "another {} in this SpaceSystem has the name {}, so \
                         references to it find the other one; rename one",
                        d.kind.word(),
                        d.key.last().map_or("", String::as_str)
                    ),
                });
            }
        }
        out
    }
}

/// The part of a reference that names a definition: a parameter
/// reference may go on to name a member or an element, as `p.x[2]`.
///
/// # Arguments
///
/// * `text` - The reference.
/// * `kind` - What kind of definition it names.
///
/// # Returns
///
/// The reference without the member and index part.
pub fn base_of(text: &str, kind: Kind) -> &str {
    if kind != Kind::Parameter {
        return text;
    }
    let last = text.rfind('/').map_or(0, |i| i + 1);
    let tail = &text[last..];
    let end = tail.find(['.', '[']).unwrap_or(tail.len());
    // `.` and `..` are path steps, not members.
    if tail.starts_with('.') {
        return text;
    }
    &text[..last + end]
}

/// The reference written again to name a renamed definition, keeping its
/// path and any member part.
///
/// # Arguments
///
/// * `text` - The reference as written.
/// * `kind` - What kind of definition it names.
/// * `new`  - The definition's new name.
///
/// # Returns
///
/// The reference with its last name replaced.
pub fn renamed(text: &str, kind: Kind, new: &str) -> String {
    let base = base_of(text, kind);
    let rest = &text[base.len()..];
    let start = base.rfind('/').map_or(0, |i| i + 1);
    format!("{}{new}{rest}", &base[..start])
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Indexes documents given as text.
    ///
    /// # Arguments
    ///
    /// * `texts` - The documents.
    ///
    /// # Returns
    ///
    /// The documents and their index.
    fn index(texts: &[&str]) -> (Vec<Document>, Index) {
        let docs: Vec<Document> =
            texts.iter().map(|t| Document::parse(t).unwrap()).collect();
        let refs: Vec<&Document> = docs.iter().collect();
        let ix = Index::of(&refs);
        (docs, ix)
    }

    #[test]
    fn resolves_as_the_compiler_does() {
        let (_, ix) = index(&[
            "<SpaceSystem name=\"dt\"><TelemetryMetaData><ParameterTypeSet>\
             <IntegerParameterType name=\"u16\"/></ParameterTypeSet>\
             </TelemetryMetaData></SpaceSystem>",
            "<SpaceSystem name=\"R\"><TelemetryMetaData><ParameterSet>\
             <Parameter name=\"a\" parameterTypeRef=\"/dt/u16\"/>\
             <Parameter name=\"b\" parameterTypeRef=\"u16\"/>\
             </ParameterSet></TelemetryMetaData>\
             <SpaceSystem name=\"sub\"><TelemetryMetaData><ParameterSet>\
             <Parameter name=\"c\" parameterTypeRef=\"../../dt/u16\"/>\
             </ParameterSet></TelemetryMetaData></SpaceSystem>\
             </SpaceSystem>",
        ]);
        let found: Vec<bool> =
            ix.refs.iter().map(|r| r.target.is_some()).collect();
        assert_eq!(found, [true, false, true]);
        assert_eq!(ix.problems(1).len(), 1);
    }

    #[test]
    fn finds_usages_and_renames_references() {
        let (_, ix) = index(&[
            "<SpaceSystem name=\"S\"><TelemetryMetaData><ParameterSet>\
             <Parameter name=\"p\" parameterTypeRef=\"t\"/></ParameterSet>\
             <ContainerSet><SequenceContainer name=\"c\"><EntryList>\
             <ParameterRefEntry parameterRef=\"p.x[1]\"/></EntryList>\
             </SequenceContainer></ContainerSet>\
             </TelemetryMetaData></SpaceSystem>",
        ]);
        let p = ix
            .defs
            .iter()
            .position(|d| d.kind == Kind::Parameter)
            .unwrap();
        assert_eq!(ix.usages(p).len(), 1);
        assert_eq!(renamed("p.x[1]", Kind::Parameter, "q"), "q.x[1]");
        assert_eq!(renamed("/S/p", Kind::Parameter, "q"), "/S/q");
        assert_eq!(renamed("../t", Kind::ParameterType, "u"), "../u");
    }

    #[test]
    fn warns_of_two_definitions_of_one_name() {
        let (_, ix) = index(&[
            "<SpaceSystem name=\"S\"><TelemetryMetaData><ParameterSet>\
             <Parameter name=\"p\" parameterTypeRef=\"p\"/></ParameterSet>\
             </TelemetryMetaData><CommandMetaData><ParameterSet>\
             <Parameter name=\"p\" parameterTypeRef=\"p\"/></ParameterSet>\
             </CommandMetaData></SpaceSystem>",
        ]);
        let m: Vec<String> =
            ix.problems(0).into_iter().map(|p| p.message).collect();
        assert!(m.iter().any(|m| m.contains("has the name p")), "{m:?}");
    }

    #[test]
    fn resolves_every_reference_the_compiler_does() {
        // The compiler builds these, so every reference in them names
        // something; the order is the one the example's Makefile gives.
        for set in [
            &[
                "examples/split-mission/dt.xtce",
                "examples/split-mission/pus.xtce",
                "examples/split-mission/rover.xtce",
            ][..],
            &["tests/mission/demo.xtce"][..],
            &["examples/housekeeping/housekeeping.xtce"][..],
        ] {
            let docs: Vec<Document> = set
                .iter()
                .map(|f| {
                    let p = format!("{}/../{f}", env!("CARGO_MANIFEST_DIR"));
                    Document::parse(&std::fs::read_to_string(p).unwrap())
                        .unwrap()
                })
                .collect();
            let refs: Vec<&Document> = docs.iter().collect();
            let ix = Index::of(&refs);
            assert!(!ix.refs.is_empty());
            for (d, name) in set.iter().enumerate() {
                let p = ix.problems(d);
                assert!(p.is_empty(), "{name}: {p:?}");
            }
        }
    }
}
