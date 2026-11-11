//! Checking a document against the schema of its XTCE version.
//!
//! A document says its version by its namespace, and is checked against
//! that version's schema alone: every element is one the schema allows
//! where it is, every attribute one its element has, every value of the
//! kind its type says, and every key's values present and different.  What
//! is found is a list of [`Problem`]s, each tied to the element it is about,
//! with a message that says what to change.
//!
//! The same walk gives each element the declaration it matched
//! ([`Report::decls`]), which is how the editor knows, for any element,
//! what it may hold and what the specification says about it.

use std::collections::{HashMap, HashSet};

use crate::content::States;
use crate::schema::{
    ComplexType, ElementDecl, Schema, SimpleRef, Step, TypeRef, Version,
};
use crate::tree::{local_name, Document, Element, Id, DOCUMENT};

/// The namespace of the attributes XML Schema gives every document, of
/// which `xsi:schemaLocation` is the one XTCE documents use.
pub const XSI: &str = "http://www.w3.org/2001/XMLSchema-instance";

/// The namespace the `xml` prefix is bound to without being declared.
pub const XML: &str = "http://www.w3.org/XML/1998/namespace";

/// How much a problem matters.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Severity {
    /// The document breaks a rule of its version's schema.
    Error,
    /// The schema allows it, but it is very likely a mistake, such as a
    /// reference that names nothing.
    Warning,
}

/// One thing wrong with a document.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct Problem {
    /// The element the problem is in, or [`DOCUMENT`] for the document as a
    /// whole.
    pub id: Id,
    /// The attribute the problem is in, if it is in one.
    pub attr: Option<String>,
    /// How much it matters.
    pub severity: Severity,
    /// What is wrong, and what to change.
    pub message: String,
}

/// What checking a document found.
#[derive(Debug, Clone, Default)]
pub struct Report {
    /// The version the document is in, or why it is in none.
    pub version: Option<Version>,
    /// Everything wrong, in document order.
    pub problems: Vec<Problem>,
    /// The declaration, by its index in [`Schema::elements`], each element
    /// matched; an element the schema does not allow where it is has none.
    pub decls: HashMap<Id, usize>,
    /// For each element with a complex type, the automaton states before
    /// each of its child elements and after the last, so that what may be
    /// put in among them can be asked without reading them again.
    pub states: HashMap<Id, Vec<States>>,
}

impl Report {
    /// Whether any problem is an error.
    ///
    /// # Returns
    ///
    /// True if the document breaks a rule of its schema.
    pub fn has_errors(&self) -> bool {
        self.problems.iter().any(|p| p.severity == Severity::Error)
    }
}

/// Checks a document against the schema of the version it is in.
///
/// # Arguments
///
/// * `doc` - The document.
///
/// # Returns
///
/// What was found.  A document whose version is not known, or whose
/// version's schema is not on this machine, has one problem, which says
/// so, and nothing else is checked.
pub fn check(doc: &Document) -> Report {
    let mut r = Report::default();
    // A schema not on this machine is one the person has not agreed to
    // download yet; the document is then checked against nothing, and
    // says why, rather than stopping the editor.
    match Version::of_document(doc).and_then(|v| Ok((v, v.load()?))) {
        Ok((v, schema)) => {
            r.version = Some(v);
            let mut w = Walk {
                schema,
                ns: v.namespace(),
                report: &mut r,
            };
            let mut scope = Scope::default();
            w.element(doc.root(), w.schema.root, &mut scope);
        }
        Err(message) => r.problems.push(Problem {
            id: doc.root().id,
            attr: None,
            severity: Severity::Error,
            message,
        }),
    }
    r
}

/// The namespace prefixes in force at an element: each binding is pushed
/// as its declaration is entered and popped as it is left.
#[derive(Debug, Default)]
struct Scope {
    /// The prefix, `""` for the default namespace, and what it is bound to.
    bindings: Vec<(String, String)>,
}

impl Scope {
    /// The namespace a prefix is bound to.
    ///
    /// # Arguments
    ///
    /// * `prefix` - The prefix, `""` for the default namespace.
    ///
    /// # Returns
    ///
    /// The namespace, or `None` for no namespace.
    fn lookup(&self, prefix: &str) -> Option<&str> {
        if prefix == "xml" {
            return Some(XML);
        }
        self.bindings
            .iter()
            .rev()
            .find(|(p, _)| p == prefix)
            .map(|(_, n)| n.as_str())
            .filter(|n| !n.is_empty())
    }
}

/// The state of one check of a document.
struct Walk<'a> {
    /// The schema of the document's version.
    schema: &'static Schema,
    /// That version's namespace.
    ns: &'static str,
    /// What is found, as it is found.
    report: &'a mut Report,
}

impl Walk<'_> {
    /// Records a problem.
    ///
    /// # Arguments
    ///
    /// * `id`      - The element it is in.
    /// * `attr`    - The attribute it is in, if any.
    /// * `message` - What is wrong and what to change.
    fn error(&mut self, id: Id, attr: Option<&str>, message: String) {
        self.report.problems.push(Problem {
            id,
            attr: attr.map(str::to_string),
            severity: Severity::Error,
            message,
        });
    }

    /// Checks an element, and everything in it, against a declaration.
    ///
    /// # Arguments
    ///
    /// * `e`     - The element.
    /// * `decl`  - The declaration it matched, by its index in
    ///             [`Schema::elements`].
    /// * `scope` - The namespace bindings in force around it.
    fn element(&mut self, e: &Element, decl: usize, scope: &mut Scope) {
        let pushed = push_bindings(e, scope);
        self.report.decls.insert(e.id, decl);
        let d: &ElementDecl = &self.schema.elements[decl];
        let prefix = e.prefix().unwrap_or("");
        let ns = scope.lookup(prefix).map(str::to_string);
        if ns.as_deref() != Some(self.ns) {
            self.error(
                e.id,
                None,
                format!(
                    "{} is not in XTCE {}'s namespace, {}; write it with \
                     the prefix that namespace is declared with",
                    e.name, self.schema.version, self.ns
                ),
            );
        }
        match d.ty {
            TypeRef::Complex(ci) => {
                let ct = &self.schema.complex[ci];
                if ct.is_abstract {
                    self.error(
                        e.id,
                        None,
                        format!(
                            "{} is of the abstract type {}, which no \
                             element may be; use one of the types derived \
                             from it",
                            e.local(),
                            ct.name.as_deref().unwrap_or("?")
                        ),
                    );
                }
                self.attributes(e, ct, scope);
                if let Some(text) = ct.text {
                    self.no_children(e);
                    self.value(e.id, None, text, &e.text());
                    self.fixed(e, d);
                } else {
                    if !ct.mixed && e.has_text() {
                        self.error(
                            e.id,
                            None,
                            format!(
                                "{} holds text, which it may not; take the \
                                 text out or put it in a comment",
                                e.local()
                            ),
                        );
                    }
                    self.children(e, ci, scope);
                }
            }
            TypeRef::Simple(sr) => {
                for a in &e.attrs {
                    if !is_namespace_attr(&a.name) {
                        self.error(
                            e.id,
                            Some(&a.name),
                            format!(
                                "{} has no attributes; take {} out",
                                e.local(),
                                a.name
                            ),
                        );
                    }
                }
                self.no_children(e);
                self.value(e.id, None, sr, &e.text());
                self.fixed(e, d);
            }
        }
        if !d.keys.is_empty() {
            self.keys(e, d, scope);
        }
        scope.bindings.truncate(scope.bindings.len() - pushed);
    }

    /// Checks an element's text against the value its declaration fixes,
    /// if it fixes one.
    ///
    /// # Arguments
    ///
    /// * `e` - The element.
    /// * `d` - Its declaration.
    fn fixed(&mut self, e: &Element, d: &ElementDecl) {
        if let Some(f) = &d.fixed {
            let text = e.text();
            if &text != f {
                self.error(
                    e.id,
                    None,
                    if f.is_empty() {
                        format!(
                            "{} must be empty; take its text out",
                            e.local()
                        )
                    } else {
                        format!(
                            "{} must hold {f:?} and nothing else",
                            e.local()
                        )
                    },
                );
            }
        }
    }

    /// Checks that an element of simple content has no child elements.
    ///
    /// # Arguments
    ///
    /// * `e` - The element.
    fn no_children(&mut self, e: &Element) {
        for c in e.elements() {
            self.error(
                c.id,
                None,
                format!(
                    "{} holds only a value, not elements; take {} out",
                    e.local(),
                    c.local()
                ),
            );
        }
    }

    /// Checks a value against its type.
    ///
    /// # Arguments
    ///
    /// * `id`    - The element the value is in.
    /// * `attr`  - The attribute it is the value of, or `None` for the
    ///             element's text.
    /// * `ty`    - Its type.
    /// * `value` - The value.
    fn value(
        &mut self,
        id: Id,
        attr: Option<&str>,
        ty: SimpleRef,
        value: &str,
    ) {
        if let Err(why) = self.schema.check_value(ty, value) {
            let what = attr.map_or("The text".to_string(), |a| a.to_string());
            self.error(id, attr, format!("{what}: {why}"));
        }
    }

    /// Checks an element's attributes against its type's.
    ///
    /// # Arguments
    ///
    /// * `e`     - The element.
    /// * `ct`    - Its type.
    /// * `scope` - The namespace bindings in force at it.
    fn attributes(&mut self, e: &Element, ct: &ComplexType, scope: &Scope) {
        for a in &e.attrs {
            if is_namespace_attr(&a.name) {
                continue;
            }
            if let Some((p, l)) = a.name.split_once(':') {
                match scope.lookup(p) {
                    Some(XSI)
                        if matches!(
                            l,
                            "schemaLocation" | "noNamespaceSchemaLocation"
                        ) =>
                    {
                        continue
                    }
                    Some(XSI) if l == "nil" => continue,
                    Some(XML) if ct.attrs.iter().any(|d| d.name == a.name) => {
                        continue;
                    }
                    _ => {}
                }
                self.error(
                    e.id,
                    Some(&a.name),
                    format!(
                        "{} has no attribute {}; take it out",
                        e.local(),
                        a.name
                    ),
                );
                continue;
            }
            match ct.attrs.iter().find(|d| d.name == a.name) {
                Some(d) => {
                    let v = a.value();
                    self.value(e.id, Some(&a.name), d.ty, &v);
                    if let Some(f) = &d.fixed {
                        if &v != f {
                            self.error(
                                e.id,
                                Some(&a.name),
                                format!("{} must be {f:?}", a.name),
                            );
                        }
                    }
                }
                None => {
                    let names: Vec<&str> =
                        ct.attrs.iter().map(|d| d.name.as_str()).collect();
                    self.error(
                        e.id,
                        Some(&a.name),
                        format!(
                            "{} has no attribute {}; it may have {}",
                            e.local(),
                            a.name,
                            if names.is_empty() {
                                "none".to_string()
                            } else {
                                names.join(", ")
                            }
                        ),
                    );
                }
            }
        }
        for d in &ct.attrs {
            if d.required && e.attr(&d.name).is_none() {
                self.error(
                    e.id,
                    Some(&d.name),
                    format!("{} must have {}; give it one", e.local(), d.name),
                );
            }
        }
    }

    /// Checks an element's children against its type's content, and each
    /// child against the declaration it matched.
    ///
    /// # Arguments
    ///
    /// * `e`     - The element.
    /// * `ci`    - Its type, by its index in [`Schema::complex`].
    /// * `scope` - The namespace bindings in force at it.
    fn children(&mut self, e: &Element, ci: usize, scope: &mut Scope) {
        let a = self.schema.automaton(ci);
        let mut at = a.initial();
        let mut states = Vec::new();
        let mut lost = false;
        for c in e.elements() {
            states.push(at.clone());
            if lost {
                continue;
            }
            let (next, decl) = a.step(self.schema, &at, c.local());
            match decl {
                Some(d) if !next.is_empty() => {
                    at = next;
                    self.element(c, d, scope);
                }
                _ => {
                    let allowed = self.names(&a.next_decls(&at));
                    self.error(
                        c.id,
                        None,
                        format!(
                            "{} may not be here in {}; {}",
                            c.local(),
                            e.local(),
                            if allowed.is_empty() {
                                "nothing more may be here".to_string()
                            } else {
                                format!("what may be here is {allowed}")
                            }
                        ),
                    );
                    // What follows cannot be typed once a child is not
                    // allowed, so the rest of the children are left
                    // unchecked rather than reported as wrong too.
                    lost = true;
                }
            }
        }
        if !lost {
            states.push(at.clone());
            if !a.accepts(&at) {
                let need = a.shortest(&at).unwrap_or_default();
                self.error(
                    e.id,
                    None,
                    format!(
                        "{} is not complete: it still needs {}",
                        e.local(),
                        self.names(&need)
                    ),
                );
            }
        }
        self.report.states.insert(e.id, states);
    }

    /// Names declarations, for a message.
    ///
    /// # Arguments
    ///
    /// * `decls` - The declarations, by their indexes.
    ///
    /// # Returns
    ///
    /// Their names, joined with commas.
    fn names(&self, decls: &[usize]) -> String {
        decls
            .iter()
            .map(|&d| self.schema.elements[d].name.as_str())
            .collect::<Vec<_>>()
            .join(", ")
    }

    /// Checks the keys declared on an element: the elements each picks out
    /// must each have its field, and no two the same value.
    ///
    /// # Arguments
    ///
    /// * `e`     - The element.
    /// * `d`     - Its declaration, which has the keys.
    /// * `scope` - The namespace bindings in force at it.
    fn keys(&mut self, e: &Element, d: &ElementDecl, scope: &Scope) {
        for key in &d.keys {
            let mut picked: Vec<&Element> = Vec::new();
            for alt in &key.selector {
                let mut at = vec![e];
                for step in alt {
                    let mut next = Vec::new();
                    for x in at {
                        for c in x.elements() {
                            if self.step_matches(step, c, scope) {
                                next.push(c);
                            }
                        }
                    }
                    at = next;
                }
                for x in at {
                    if !picked.iter().any(|p| p.id == x.id) {
                        picked.push(x);
                    }
                }
            }
            let mut seen: HashSet<String> = HashSet::new();
            for x in picked {
                let value = match &key.field {
                    Step::Attribute(a) => x.get(a),
                    Step::Element(n) | Step::Unqualified(n) => {
                        x.elements().find(|c| c.local() == n).map(|c| c.text())
                    }
                    Step::Any => None,
                };
                let Some(value) = value else {
                    let field = match &key.field {
                        Step::Attribute(a) => a.clone(),
                        Step::Element(n) | Step::Unqualified(n) => n.clone(),
                        Step::Any => "*".to_string(),
                    };
                    self.error(
                        x.id,
                        None,
                        format!(
                            "{} needs {field}, which {} requires",
                            x.local(),
                            key.name
                        ),
                    );
                    continue;
                };
                if seen.contains(&value) {
                    self.error(
                        x.id,
                        None,
                        format!(
                            "{} {value:?} has the name of another in the \
                             same {}; rename one of them ({})",
                            x.local(),
                            e.local(),
                            key.name
                        ),
                    );
                } else {
                    seen.insert(value);
                }
            }
        }
    }

    /// Whether an element is one a key's step picks out.
    ///
    /// # Arguments
    ///
    /// * `step`  - The step.
    /// * `c`     - The element.
    /// * `scope` - The namespace bindings in force at its parent.
    ///
    /// # Returns
    ///
    /// True if the step picks it out.
    fn step_matches(&self, step: &Step, c: &Element, scope: &Scope) -> bool {
        let ns = element_namespace(c, scope);
        match step {
            Step::Any => true,
            Step::Element(n) => {
                c.local() == n && ns.as_deref() == Some(self.ns)
            }
            Step::Unqualified(n) => c.local() == n && ns.is_none(),
            Step::Attribute(_) => false,
        }
    }
}

/// The namespace an element is in, from its own declarations and those in
/// force around it.
///
/// # Arguments
///
/// * `c`     - The element.
/// * `scope` - The bindings in force at its parent.
///
/// # Returns
///
/// The namespace, or `None` for none.
fn element_namespace(c: &Element, scope: &Scope) -> Option<String> {
    let prefix = c.prefix().unwrap_or("");
    let own = if prefix.is_empty() {
        "xmlns".to_string()
    } else {
        format!("xmlns:{prefix}")
    };
    if let Some(v) = c.get(&own) {
        return if v.is_empty() { None } else { Some(v) };
    }
    scope.lookup(prefix).map(str::to_string)
}

/// Whether an attribute declares a namespace.
///
/// # Arguments
///
/// * `name` - The attribute's name.
///
/// # Returns
///
/// True for `xmlns` and `xmlns:` anything.
pub fn is_namespace_attr(name: &str) -> bool {
    name == "xmlns" || name.starts_with("xmlns:")
}

/// Pushes the namespace declarations on an element.
///
/// # Arguments
///
/// * `e`     - The element.
/// * `scope` - The bindings, which are pushed to.
///
/// # Returns
///
/// How many were pushed, to pop when the element is left.
fn push_bindings(e: &Element, scope: &mut Scope) -> usize {
    let mut n = 0;
    for a in &e.attrs {
        if a.name == "xmlns" {
            scope.bindings.push((String::new(), a.value()));
            n += 1;
        } else if let Some(p) = a.name.strip_prefix("xmlns:") {
            scope.bindings.push((p.to_string(), a.value()));
            n += 1;
        }
    }
    n
}

/// The declarations of the elements that may be put in among an
/// element's children at one place, so that the children are still a list
/// its type allows.
///
/// # Arguments
///
/// * `doc`    - The document.
/// * `report` - What checking it found.
/// * `parent` - The element.
/// * `at`     - Where among its child elements: 0 before the first, and
///              the number of them after the last.
///
/// # Returns
///
/// The declarations, by their indexes in [`Schema::elements`], in the
/// order the schema gives them.  None if the parent is not an element the
/// schema allows, or its children are already wrong.
pub fn insertable(
    doc: &Document,
    report: &Report,
    parent: Id,
    at: usize,
) -> Vec<usize> {
    let (Some(v), Some(&decl)) = (report.version, report.decls.get(&parent))
    else {
        return Vec::new();
    };
    let schema = v.schema();
    let TypeRef::Complex(ci) = schema.elements[decl].ty else {
        return Vec::new();
    };
    if schema.complex[ci].text.is_some() {
        return Vec::new();
    }
    let Some(states) = report.states.get(&parent) else {
        return Vec::new();
    };
    let Some(before) = states.get(at) else {
        return Vec::new();
    };
    let Some(p) = doc.element(parent) else {
        return Vec::new();
    };
    let a = schema.automaton(ci);
    let rest: Vec<&str> = p.elements().skip(at).map(|c| c.local()).collect();
    a.next_decls(before)
        .into_iter()
        .filter(|&d| {
            let (after, _) = a.step(schema, before, &schema.elements[d].name);
            let end = a.run(schema, &after, rest.iter().copied());
            a.accepts(&end)
        })
        .collect()
}

/// Whether an element may be taken out of its parent, leaving the
/// parent's children a list its type allows.
///
/// # Arguments
///
/// * `doc`    - The document.
/// * `report` - What checking it found.
/// * `id`     - The element.
///
/// # Errors
///
/// Fails, saying what taking it out would leave wrong, if it may not be
/// taken out: it is the root, it is not in the document, or its parent's
/// type needs it.
pub fn removable(
    doc: &Document,
    report: &Report,
    id: Id,
) -> Result<(), String> {
    let Some((parent, _)) = doc.parent_of(id) else {
        return Err("it is not in the document".to_string());
    };
    if parent == DOCUMENT {
        return Err("it is the root, which a document must have".to_string());
    }
    let (Some(v), Some(&decl)) = (report.version, report.decls.get(&parent))
    else {
        return Ok(());
    };
    let schema = v.schema();
    let TypeRef::Complex(ci) = schema.elements[decl].ty else {
        return Ok(());
    };
    let p = doc.element(parent).expect("the parent is in the document");
    let a = schema.automaton(ci);
    let names: Vec<&str> = p
        .elements()
        .filter(|c| c.id != id)
        .map(|c| c.local())
        .collect();
    let end = a.run(schema, &a.initial(), names);
    if a.accepts(&end) {
        Ok(())
    } else {
        let gone = doc.element(id).map_or("it", |e| e.local());
        Err(format!(
            "{} must have {gone} where it is, so it may not be taken out; \
             put another in its place first",
            local_name(&p.name)
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Wraps the inside of a SpaceSystem in a 1.3 document.
    ///
    /// # Arguments
    ///
    /// * `inside` - What goes in the SpaceSystem.
    ///
    /// # Returns
    ///
    /// The document.
    fn doc13(inside: &str) -> Document {
        Document::parse(&format!(
            "<xtce:SpaceSystem xmlns:xtce=\"{}\" name=\"S\">{inside}\
             </xtce:SpaceSystem>",
            Version::V1_3.namespace()
        ))
        .expect("well-formed")
    }

    /// The messages of a document's problems.
    ///
    /// # Arguments
    ///
    /// * `doc` - The document.
    ///
    /// # Returns
    ///
    /// The messages, in order.
    fn messages(doc: &Document) -> Vec<String> {
        check(doc).problems.into_iter().map(|p| p.message).collect()
    }

    #[test]
    fn a_small_document_is_valid() {
        let doc = doc13(
            "<xtce:TelemetryMetaData><xtce:ParameterTypeSet>\
             <xtce:IntegerParameterType name=\"u8\" signed=\"false\">\
             <xtce:IntegerDataEncoding sizeInBits=\"8\"/>\
             </xtce:IntegerParameterType></xtce:ParameterTypeSet>\
             <xtce:ParameterSet><xtce:Parameter name=\"p\" \
             parameterTypeRef=\"u8\"/></xtce:ParameterSet>\
             </xtce:TelemetryMetaData>",
        );
        assert_eq!(messages(&doc), Vec::<String>::new());
    }

    #[test]
    fn finds_what_the_schema_does_not_allow() {
        let doc = doc13(
            "<xtce:TelemetryMetaData><xtce:ParameterSet>\
             <xtce:Parameter name=\"p\" bogus=\"1\" parameterTypeRef=\"t\"/>\
             <xtce:Parameter name=\"p\" parameterTypeRef=\"t\"/>\
             <xtce:Parameter parameterTypeRef=\"t\"/>\
             </xtce:ParameterSet></xtce:TelemetryMetaData>\
             <xtce:Header/>",
        );
        let m = messages(&doc).join("\n");
        assert!(m.contains("has no attribute bogus"), "{m}");
        assert!(m.contains("must have name"), "{m}");
        assert!(m.contains("Header may not be here"), "{m}");
    }

    #[test]
    fn holds_an_element_to_its_fixed_value() {
        let doc = doc13(
            "<xtce:TelemetryMetaData><xtce:AlgorithmSet>\
             <xtce:MathAlgorithm name=\"m\"><xtce:MathOperation \
             outputParameterRef=\"p\"><xtce:ThisParameterOperand>1\
             </xtce:ThisParameterOperand></xtce:MathOperation>\
             </xtce:MathAlgorithm></xtce:AlgorithmSet>\
             </xtce:TelemetryMetaData>",
        );
        let m = messages(&doc).join("\n");
        assert!(m.contains("ThisParameterOperand must be empty"), "{m}");
    }

    #[test]
    fn finds_duplicate_names() {
        let doc = doc13(
            "<xtce:TelemetryMetaData><xtce:ParameterSet>\
             <xtce:Parameter name=\"p\" parameterTypeRef=\"t\"/>\
             <xtce:Parameter name=\"p\" parameterTypeRef=\"t\"/>\
             </xtce:ParameterSet></xtce:TelemetryMetaData>",
        );
        let m = messages(&doc).join("\n");
        assert!(m.contains("has the name of another"), "{m}");
    }

    #[test]
    fn says_what_is_missing() {
        let doc = doc13(
            "<xtce:TelemetryMetaData><xtce:ContainerSet>\
             <xtce:SequenceContainer name=\"c\"/>\
             </xtce:ContainerSet></xtce:TelemetryMetaData>",
        );
        let m = messages(&doc).join("\n");
        assert!(m.contains("still needs EntryList"), "{m}");
    }

    #[test]
    fn holds_a_document_to_its_own_version() {
        // A 1.3 element in a 1.1 document: 1.1 has no such thing.
        let doc = Document::parse(&format!(
            "<SpaceSystem xmlns=\"{}\" name=\"S\"><TelemetryMetaData>\
             <ParameterTypeSet><BinaryParameterType name=\"b\">\
             <BinaryDataEncoding><SizeInBits><FixedValue>8</FixedValue>\
             </SizeInBits></BinaryDataEncoding></BinaryParameterType>\
             </ParameterTypeSet></TelemetryMetaData></SpaceSystem>",
            Version::V1_1.namespace()
        ))
        .unwrap();
        let r = check(&doc);
        assert_eq!(r.version, Some(Version::V1_1));
    }

    #[test]
    fn offers_only_what_may_go_in() {
        let doc = doc13("<xtce:TelemetryMetaData/>");
        let r = check(&doc);
        let root = doc.root().id;
        let schema = Version::V1_3.schema();
        let names = |ds: Vec<usize>| -> Vec<String> {
            ds.iter()
                .map(|&d| schema.elements[d].name.clone())
                .collect()
        };
        let before = names(insertable(&doc, &r, root, 0));
        assert!(before.contains(&"Header".to_string()), "{before:?}");
        assert!(!before.contains(&"CommandMetaData".to_string()));
        let after = names(insertable(&doc, &r, root, 1));
        assert!(after.contains(&"CommandMetaData".to_string()), "{after:?}");
        assert!(!after.contains(&"Header".to_string()));
    }

    #[test]
    fn refuses_to_take_out_what_must_be_there() {
        let doc = doc13(
            "<xtce:TelemetryMetaData><xtce:ContainerSet>\
             <xtce:SequenceContainer name=\"c\"><xtce:EntryList/>\
             </xtce:SequenceContainer></xtce:ContainerSet>\
             </xtce:TelemetryMetaData>",
        );
        let r = check(&doc);
        assert!(!r.has_errors(), "{:?}", r.problems);
        let tm = doc.root().elements().next().unwrap();
        let cs = tm.elements().next().unwrap();
        let sc = cs.elements().next().unwrap();
        let el = sc.elements().next().unwrap();
        assert!(removable(&doc, &r, el.id).is_err());
        assert!(removable(&doc, &r, cs.id).is_ok());
    }
}
