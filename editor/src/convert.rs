//! Moving a document from one XTCE version to another.
//!
//! A conversion goes one version at a time -- 1.1 to 1.2, 1.2 to 1.3, or
//! back -- and each step is three things done to a copy of the document:
//!
//! 1. its namespace, and the schema location if it gives one, become the
//!    other version's;
//! 2. the rules of that step are applied: the elements and attributes the
//!    other version renamed, moved or rewrote are changed to its form, by
//!    the rules below, each written from what the two schemas say;
//! 3. the result is checked against the other version's schema.  On the way
//!    down, an element or attribute the older version has no place for is
//!    taken out, and that is reported as a loss.  Anything else still wrong
//!    stops the conversion and is reported as a refusal, saying why.
//!
//! Some things cannot be converted without knowing more than the document
//! says: the sizes of an array's dimensions, which 1.1 does not hold, or a
//! default 1.3 requires and 1.2 has no place for.  Each such thing is a
//! [`Need`], with a key; the conversion is tried again with an answer for
//! each key, and goes through only when every need is met.
//!
//! The document's comments, layout and attribute order are kept: the
//! conversion changes the tree the editor holds, not a reading of it into
//! other structures, so a comment stays beside what it was written beside.
//! A comment inside an element that is taken out goes with it.

use std::collections::BTreeMap;

use crate::content::Automaton;
use crate::schema::Version;
use crate::schema::{Schema, TypeRef};
use crate::tree::{Attr, Document, Element, Id, Node};
use crate::validate::{check, Problem, Report, Severity, XSI};

/// What one conversion did, or would need to do.
#[derive(Debug, Clone, Default)]
pub struct Outcome {
    /// The converted document, if the conversion went through.
    pub doc: Option<Document>,
    /// What was changed, other than the namespace.
    pub changed: Vec<String>,
    /// What the target version cannot say and was taken out.
    pub lost: Vec<String>,
    /// What must be answered before the conversion can go through.
    pub needs: Vec<Need>,
    /// Why the conversion cannot go through at all.
    pub refused: Vec<String>,
}

impl Outcome {
    /// Whether the conversion went through.
    ///
    /// # Returns
    ///
    /// True if there is a converted document.
    pub fn done(&self) -> bool {
        self.doc.is_some()
    }
}

/// A value a conversion needs from the person converting.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Need {
    /// What names this need in the answers: the element's place and what
    /// is wanted, so that the same question asked again has the same key.
    pub key: String,
    /// The question, saying what the value is for and what form it takes.
    pub question: String,
    /// A value that would do, if one can be suggested.
    pub suggestion: Option<String>,
}

/// The answers given to a conversion's needs, by key.
pub type Answers = BTreeMap<String, String>;

/// Converts a document to another version.
///
/// # Arguments
///
/// * `doc`     - The document, which is not changed.
/// * `target`  - The version to convert it to.
/// * `answers` - Answers to needs a previous try reported.
///
/// # Returns
///
/// What the conversion did.  It has a document only if it went through:
/// nothing refused and every need answered.  The losses are what the
/// person must agree to before the result replaces the document.
pub fn convert(doc: &Document, target: Version, answers: &Answers) -> Outcome {
    let mut out = Outcome::default();
    let from = match Version::of_document(doc) {
        Ok(v) => v,
        Err(e) => {
            out.refused.push(e);
            return out;
        }
    };
    let before = check(doc);
    if before.has_errors() {
        out.refused.push(format!(
            "the document is not valid XTCE {from}, so what its parts mean \
             cannot be known for certain; correct its {} problem(s) first",
            before
                .problems
                .iter()
                .filter(|p| p.severity == Severity::Error)
                .count()
        ));
        return out;
    }
    if from == target {
        out.doc = Some(doc.clone());
        return out;
    }
    let mut at = from;
    let mut cur = doc.clone();
    while at != target {
        let next = if target > at { up(at) } else { down(at) };
        let mut step = Step {
            doc: &mut cur,
            answers,
            out: &mut out,
            from: at,
            to: next,
        };
        step.run();
        if !out.refused.is_empty() || !out.needs.is_empty() {
            return out;
        }
        at = next;
    }
    out.doc = Some(cur);
    out
}

/// The version after one.
///
/// # Arguments
///
/// * `v` - The version, which is not the newest.
///
/// # Returns
///
/// The next version up.
fn up(v: Version) -> Version {
    match v {
        Version::V1_1 => Version::V1_2,
        _ => Version::V1_3,
    }
}

/// The version before one.
///
/// # Arguments
///
/// * `v` - The version, which is not the oldest.
///
/// # Returns
///
/// The next version down.
fn down(v: Version) -> Version {
    match v {
        Version::V1_3 => Version::V1_2,
        _ => Version::V1_1,
    }
}

/// One step of a conversion, from one version to the next.
struct Step<'a> {
    /// The document, changed in place.
    doc: &'a mut Document,
    /// The answers to needs.
    answers: &'a Answers,
    /// What the conversion has done so far.
    out: &'a mut Outcome,
    /// The version the document is in.
    from: Version,
    /// The version it is being converted to.
    to: Version,
}

/// Where an element is, for messages and for the keys of needs: the names
/// of it and its ancestors, each with its `name` attribute if it has one.
///
/// # Arguments
///
/// * `path` - The ancestors' places and the element's, outermost first.
///
/// # Returns
///
/// The place, as `SpaceSystem[S]/TelemetryMetaData/...`.
fn place(path: &[String]) -> String {
    path.join("/")
}

/// One element's part of a place.
///
/// # Arguments
///
/// * `e` - The element.
///
/// # Returns
///
/// Its name without prefix, and its `name` attribute in brackets if it has
/// one.
fn label(e: &Element) -> String {
    match e.get("name") {
        Some(n) => format!("{}[{n}]", e.local()),
        None => e.local().to_string(),
    }
}

/// Visits every element below one, parents before children.
///
/// # Arguments
///
/// * `e`    - The element whose descendants are visited; it is visited
///            too.
/// * `path` - The places of its ancestors, which the visit extends.
/// * `f`    - Called with each element and the places of it and its
///            ancestors.
fn visit(
    e: &mut Element,
    path: &mut Vec<String>,
    f: &mut dyn FnMut(&mut Element, &[String]),
) {
    path.push(label(e));
    f(e, path);
    for c in &mut e.children {
        if let Node::Element(k) = c {
            visit(k, path, f);
        }
    }
    path.pop();
}

/// The root element, to change.
///
/// # Arguments
///
/// * `doc` - The document.
///
/// # Returns
///
/// Its root element.
fn root_mut(doc: &mut Document) -> &mut Element {
    doc.top
        .iter_mut()
        .find_map(|n| match n {
            Node::Element(e) => Some(e),
            _ => None,
        })
        .expect("a document always has a root element")
}

/// Whether the places of an element and its ancestors end with names.
///
/// # Arguments
///
/// * `path`  - The places, outermost first.
/// * `names` - The names the last places must have, outermost first.
///
/// # Returns
///
/// True if they do.
fn ends_with(path: &[String], names: &[&str]) -> bool {
    if path.len() < names.len() {
        return false;
    }
    path[path.len() - names.len()..]
        .iter()
        .zip(names)
        .all(|(p, n)| p.split('[').next() == Some(n))
}

/// Renames an element, keeping its prefix.
///
/// # Arguments
///
/// * `e`   - The element.
/// * `new` - Its new name, without prefix.
fn rename(e: &mut Element, new: &str) {
    e.name = match e.prefix() {
        Some(p) => format!("{p}:{new}"),
        None => new.to_string(),
    };
}

/// Renames an attribute in place, keeping how it is written.
///
/// # Arguments
///
/// * `e`   - The element.
/// * `old` - The attribute's name.
/// * `new` - Its new name.
///
/// # Returns
///
/// True if the element had the attribute.
fn rename_attr(e: &mut Element, old: &str, new: &str) -> bool {
    match e.attrs.iter_mut().find(|a| a.name == old) {
        Some(a) => {
            a.name = new.to_string();
            true
        }
        None => false,
    }
}

/// Sets an attribute's value, keeping how it is written if it is there and
/// adding it after the others if not.
///
/// # Arguments
///
/// * `e`     - The element.
/// * `name`  - The attribute.
/// * `value` - Its value.
fn put_attr(e: &mut Element, name: &str, value: &str) {
    match e.attrs.iter_mut().find(|a| a.name == name) {
        Some(a) => *a = a.with_value(value),
        None => e.attrs.push(Attr::new(name, value)),
    }
}

/// Takes an attribute out.
///
/// # Arguments
///
/// * `e`    - The element.
/// * `name` - The attribute.
///
/// # Returns
///
/// Its value, if it was there.
fn take_attr(e: &mut Element, name: &str) -> Option<String> {
    let i = e.attrs.iter().position(|a| a.name == name)?;
    Some(e.attrs.remove(i).value())
}

/// A new element in the same namespace as another, written with the same
/// prefix.
///
/// # Arguments
///
/// * `like` - The element whose prefix it takes.
/// * `name` - Its name, without prefix.
/// * `id`   - Its id.
///
/// # Returns
///
/// The element, empty.
fn sibling(like: &Element, name: &str, id: Id) -> Element {
    let full = match like.prefix() {
        Some(p) => format!("{p}:{name}"),
        None => name.to_string(),
    };
    Element::new(id, &full)
}

/// Puts a node in among an element's children after any whitespace that
/// opens it, laying it out on a line of its own if the children are.
///
/// # Arguments
///
/// * `e`     - The element.
/// * `index` - Where among its child elements: 0 before the first, and
///             their number after the last.
/// * `node`  - What to put in.
fn put_child(e: &mut Element, index: usize, node: Node) {
    let kids: Vec<usize> = e
        .children
        .iter()
        .enumerate()
        .filter(|(_, c)| matches!(c, Node::Element(_)))
        .map(|(i, _)| i)
        .collect();
    let space =
        e.children
            .iter()
            .find(|c| c.is_space())
            .and_then(|c| match c {
                Node::Text(t) => Some(t.clone()),
                _ => None,
            });
    let at = match kids.get(index) {
        Some(&i) => i,
        None => {
            let mut i = e.children.len();
            if i > 0 && e.children[i - 1].is_space() {
                i -= 1;
            }
            i
        }
    };
    e.empty = false;
    match space {
        Some(s) if index < kids.len() => {
            e.children.insert(at, Node::Text(s));
            e.children.insert(at, node);
        }
        Some(s) => {
            e.children.insert(at, node);
            e.children.insert(at, Node::Text(s));
        }
        None => e.children.insert(at, node),
    }
}

/// The index among an element's child elements of the first with a name.
///
/// # Arguments
///
/// * `e`    - The element.
/// * `name` - The child's name, without prefix.
///
/// # Returns
///
/// The index among the child elements, or `None`.
fn child_index(e: &Element, name: &str) -> Option<usize> {
    e.elements().position(|c| c.local() == name)
}

/// The first child element with a name, to change.
///
/// # Arguments
///
/// * `e`    - The element.
/// * `name` - The child's name, without prefix.
///
/// # Returns
///
/// The child, or `None`.
fn child_mut<'a>(e: &'a mut Element, name: &str) -> Option<&'a mut Element> {
    e.children.iter_mut().find_map(|c| match c {
        Node::Element(k) if k.local() == name => Some(k),
        _ => None,
    })
}

/// Takes the child elements with a name out of an element, with the
/// whitespace before each.
///
/// # Arguments
///
/// * `e`    - The element.
/// * `name` - The children's name, without prefix.
///
/// # Returns
///
/// The children taken out.
fn take_children(e: &mut Element, name: &str) -> Vec<Element> {
    let mut out = Vec::new();
    let mut i = 0;
    while i < e.children.len() {
        let hit =
            matches!(&e.children[i], Node::Element(k) if k.local() == name);
        if hit {
            if let Node::Element(k) = e.children.remove(i) {
                out.push(k);
            }
            if i > 0 && e.children[i - 1].is_space() {
                e.children.remove(i - 1);
                i -= 1;
            }
        } else {
            i += 1;
        }
    }
    if e.children.iter().all(Node::is_space) {
        e.children.clear();
        e.empty = true;
    }
    out
}

/// Writes a whole number given in 1.1's hexadecimal, octal or binary forms
/// in decimal, as 1.2 requires.
///
/// # Arguments
///
/// * `v` - The number as written.
///
/// # Returns
///
/// The number in decimal, or `None` if it is not in one of those forms or
/// does not fit in 64 signed bits.
fn to_decimal(v: &str) -> Option<String> {
    let t = v.trim();
    let (digits, radix) = match t.get(..2) {
        Some("0x" | "0X") => (&t[2..], 16),
        Some("0o" | "0O") => (&t[2..], 8),
        Some("0b" | "0B") => (&t[2..], 2),
        _ => return None,
    };
    let n = i128::from_str_radix(digits, radix).ok()?;
    if n > i64::MAX as i128 {
        return None;
    }
    Some(n.to_string())
}

impl Step<'_> {
    /// Records a change.
    ///
    /// # Arguments
    ///
    /// * `what` - What was changed, and where.
    fn changed(&mut self, what: String) {
        self.out
            .changed
            .push(format!("{}->{}: {what}", self.from, self.to));
    }

    /// Records a loss.
    ///
    /// # Arguments
    ///
    /// * `what` - What was taken out, and where.
    fn lost(&mut self, what: String) {
        self.out
            .lost
            .push(format!("{}->{}: {what}", self.from, self.to));
    }

    /// Records a refusal.
    ///
    /// # Arguments
    ///
    /// * `why` - Why the conversion cannot go through, and what to change.
    fn refuse(&mut self, why: String) {
        self.out
            .refused
            .push(format!("{}->{}: {why}", self.from, self.to));
    }

    /// The answer to a need, or records the need if it has none.
    ///
    /// # Arguments
    ///
    /// * `key`        - What names the need.
    /// * `question`   - What is wanted and in what form.
    /// * `suggestion` - A value that would do, if there is one.
    ///
    /// # Returns
    ///
    /// The answer, or `None` if there is none yet.
    fn need(
        &mut self,
        key: String,
        question: String,
        suggestion: Option<String>,
    ) -> Option<String> {
        if let Some(a) = self.answers.get(&key) {
            return Some(a.clone());
        }
        self.out.needs.push(Need {
            key,
            question,
            suggestion,
        });
        None
    }

    /// Runs the step.
    fn run(&mut self) {
        self.namespace();
        match (self.from, self.to) {
            (Version::V1_1, Version::V1_2) => self.up_11_12(),
            (Version::V1_2, Version::V1_1) => self.down_12_11(),
            (Version::V1_2, Version::V1_3) => self.up_12_13(),
            (Version::V1_3, Version::V1_2) => self.down_13_12(),
            _ => unreachable!("a step is between neighbouring versions"),
        }
        if self.out.refused.is_empty() && self.out.needs.is_empty() {
            self.settle();
        }
    }

    /// Moves every namespace declaration and schema location from the
    /// version the document is in to the one it is going to.
    fn namespace(&mut self) {
        let old_ns = self.from.namespace();
        let new_ns = self.to.namespace();
        let old_url = self.from.schema_url();
        let new_url = self.to.schema_url();
        let mut f = |e: &mut Element, _: &[String]| {
            for a in &mut e.attrs {
                let v = a.value();
                if (a.name == "xmlns" || a.name.starts_with("xmlns:"))
                    && v == old_ns
                {
                    *a = a.with_value(new_ns);
                } else if a.name.ends_with(":schemaLocation")
                    && v.contains(old_ns)
                {
                    // Each pair is a namespace and where its schema is; the
                    // XTCE pair is moved to the new version's, whatever
                    // location it gave.
                    let mut words: Vec<String> =
                        v.split_whitespace().map(str::to_string).collect();
                    for i in (0..words.len()).step_by(2) {
                        if words[i] == old_ns {
                            words[i] = new_ns.to_string();
                            if let Some(loc) = words.get_mut(i + 1) {
                                if loc.ends_with(".xsd") {
                                    *loc = if loc == old_url {
                                        new_url.to_string()
                                    } else {
                                        loc.clone()
                                    };
                                }
                            }
                        }
                    }
                    *a = a.with_value(&words.join(" "));
                }
            }
        };
        visit(root_mut(self.doc), &mut Vec::new(), &mut f);
        let _ = XSI;
    }

    /// Checks the step's result against the target's schema, taking out
    /// on the way down what the older version has no place for, until it is
    /// valid or something is left that cannot be taken out.
    fn settle(&mut self) {
        let downward = self.to < self.from;
        // Each round changes one thing, so a document has at most as many
        // rounds as it has elements and attributes; the bound is a guard
        // against a rule that undoes another.
        for _ in 0..100_000 {
            let report = check(self.doc);
            let errors: Vec<Problem> = report
                .problems
                .iter()
                .filter(|p| p.severity == Severity::Error)
                .cloned()
                .collect();
            if errors.is_empty() {
                return;
            }
            // Supplying what the older version requires comes first: an
            // element is out of place when a required sibling before it
            // is missing, and must not be taken for one with no place.
            if downward
                && (self.complete(&report, &errors)
                    || self.drop_unplaced(&errors)
                    || self.drop_incomplete(&report, &errors))
            {
                continue;
            }
            if !downward && self.drop_empty(&report, &errors) {
                continue;
            }
            for p in errors {
                let path = path_of(self.doc, p.id).unwrap_or_default();
                self.refuse(format!(
                    "{path}: {} in XTCE {}",
                    p.message, self.to
                ));
            }
            return;
        }
        self.refuse(
            "the conversion does not settle; this is a mistake in \
                     the editor, so please report it with the document"
                .to_string(),
        );
    }

    /// Takes out one attribute or element the target version has no place
    /// for, recording it as lost.
    ///
    /// # Arguments
    ///
    /// * `errors` - The problems the target's schema finds.
    ///
    /// # Returns
    ///
    /// True if something was taken out.
    fn drop_unplaced(&mut self, errors: &[Problem]) -> bool {
        // Every one found in a round is taken out at once: they are
        // independent, being each the first misplaced child of its
        // parent or an attribute, and one inside another that went first
        // is simply no longer there.
        let mut took = false;
        for p in errors {
            let unplaced = p.message.contains(" may not be here")
                || p.message.contains("has no attribute");
            if !unplaced {
                continue;
            }
            let Some(path) = path_of(self.doc, p.id) else {
                continue;
            };
            if let Some(attr) = &p.attr {
                let e = self.doc.element_mut(p.id).expect("found above");
                let Some(v) = take_attr(e, attr) else {
                    continue;
                };
                self.lost(format!("{path}: @{attr}=\"{v}\""));
            } else {
                self.take_out(p.id);
                self.lost(format!("{path}, and all it held"));
            }
            took = true;
        }
        took
    }

    /// Takes out one element left incomplete, where its parent does not
    /// need it, recording it as lost.  An element the older version
    /// requires more of than the newer, such as an ErrorDetectCorrect whose
    /// only check was new, is the case this is for.
    ///
    /// # Arguments
    ///
    /// * `report` - What checking the document found.
    /// * `errors` - The problems the target's schema finds.
    ///
    /// # Returns
    ///
    /// True if something was taken out.
    fn drop_incomplete(&mut self, report: &Report, errors: &[Problem]) -> bool {
        for p in errors {
            if !p.message.contains("is not complete") {
                continue;
            }
            let Some((parent, _)) = self.doc.parent_of(p.id) else {
                continue;
            };
            let Some(names) = self.doc.element(parent).map(|e| {
                e.elements()
                    .filter(|c| c.id != p.id)
                    .map(|c| c.local().to_string())
                    .collect::<Vec<_>>()
            }) else {
                continue;
            };
            let Some(a) = self.automaton_of(report, parent) else {
                continue;
            };
            let schema = self.to.schema();
            let end =
                a.run(schema, &a.initial(), names.iter().map(String::as_str));
            if !a.accepts(&end) {
                continue;
            }
            let path = path_of(self.doc, p.id).unwrap_or_default();
            self.take_out(p.id);
            self.lost(format!(
                "{path}, which XTCE {} cannot complete",
                self.to
            ));
            return true;
        }
        false
    }

    /// Takes out one element left incomplete by the newer version that
    /// holds nothing at all, where its parent does not need it: an empty
    /// InputSet, which 1.2 allows and 1.3 does not, says no more than none.
    ///
    /// # Arguments
    ///
    /// * `report` - What checking the document found.
    /// * `errors` - The problems the target's schema finds.
    ///
    /// # Returns
    ///
    /// True if something was taken out.
    fn drop_empty(&mut self, report: &Report, errors: &[Problem]) -> bool {
        for p in errors {
            if !p.message.contains("is not complete") {
                continue;
            }
            let empty = self.doc.element(p.id).is_some_and(|e| {
                e.attrs
                    .iter()
                    .all(|a| crate::validate::is_namespace_attr(&a.name))
                    && e.children.iter().all(Node::is_space)
            });
            if !empty {
                continue;
            }
            let Some((parent, _)) = self.doc.parent_of(p.id) else {
                continue;
            };
            let Some(a) = self.automaton_of(report, parent) else {
                continue;
            };
            let names: Vec<String> = self
                .doc
                .element(parent)
                .map(|e| {
                    e.elements()
                        .filter(|c| c.id != p.id)
                        .map(|c| c.local().to_string())
                        .collect()
                })
                .unwrap_or_default();
            let schema = self.to.schema();
            let end =
                a.run(schema, &a.initial(), names.iter().map(String::as_str));
            if !a.accepts(&end) {
                continue;
            }
            let path = path_of(self.doc, p.id).unwrap_or_default();
            self.take_out(p.id);
            self.changed(format!(
                "{path} taken out: it was empty, which XTCE {} does not allow",
                self.to
            ));
            return true;
        }
        false
    }

    /// Takes an element out of its parent, with the whitespace before it.
    ///
    /// # Arguments
    ///
    /// * `id` - The element.
    fn take_out(&mut self, id: Id) {
        if let Some((parent, i)) = self.doc.parent_of(id) {
            let kids = self.doc.children_mut(parent).expect("found above");
            kids.remove(i);
            if i > 0 && kids[i - 1].is_space() {
                kids.remove(i - 1);
            }
        }
    }

    /// The automaton of an element's content in the target version.
    ///
    /// # Arguments
    ///
    /// * `report` - What checking the document found.
    /// * `id`     - The element.
    ///
    /// # Returns
    ///
    /// The automaton, or `None` if the element matched no declaration or
    /// has simple content.
    fn automaton_of(
        &self,
        report: &Report,
        id: Id,
    ) -> Option<&'static Automaton> {
        let schema = self.to.schema();
        let &decl = report.decls.get(&id)?;
        match schema.elements[decl].ty {
            TypeRef::Complex(ci) if schema.complex[ci].text.is_none() => {
                Some(schema.automaton(ci))
            }
            _ => None,
        }
    }

    /// Gives an element a child the older version requires and it lacks,
    /// where the child may be empty -- 1.1's UnitSet, which 1.2 made
    /// optional, is the case this is for -- at the place that most helps
    /// the element's children become a list the version allows.
    ///
    /// # Arguments
    ///
    /// * `report` - What checking the document found.
    /// * `errors` - The problems the target's schema finds.
    ///
    /// # Returns
    ///
    /// True if a child was put in.
    fn complete(&mut self, report: &Report, errors: &[Problem]) -> bool {
        let schema = self.to.schema();
        let mut parents: Vec<Id> = Vec::new();
        for p in errors {
            let id = if p.message.contains("is not complete") {
                Some(p.id)
            } else if p.attr.is_none() && p.message.contains(" may not be here")
            {
                self.doc.parent_of(p.id).map(|(q, _)| q)
            } else {
                None
            };
            if let Some(id) = id {
                if !parents.contains(&id) {
                    parents.push(id);
                }
            }
        }
        for parent in parents {
            let Some(a) = self.automaton_of(report, parent) else {
                continue;
            };
            let Some(e) = self.doc.element(parent) else {
                continue;
            };
            let names: Vec<String> =
                e.elements().map(|c| c.local().to_string()).collect();
            let was = progress(schema, a, &names);
            for at in 0..=names.len() {
                let before = a.run(
                    schema,
                    &a.initial(),
                    names[..at].iter().map(String::as_str),
                );
                for d in a.next_decls(&before) {
                    if !empty_will_do(schema, d) {
                        continue;
                    }
                    let name = schema.elements[d].name.clone();
                    let mut tried = names.clone();
                    tried.insert(at, name.clone());
                    if progress(schema, a, &tried) <= was {
                        continue;
                    }
                    let path = path_of(self.doc, parent).unwrap_or_default();
                    let id = self.doc.next_id();
                    let e = self.doc.element_mut(parent).expect("found above");
                    let child = sibling(e, &name, id);
                    put_child(e, at, Node::Element(child));
                    self.changed(format!(
                        "{path}: an empty {name}, which XTCE {} requires",
                        self.to
                    ));
                    return true;
                }
            }
        }
        false
    }
}

/// How far a list of children gets towards being one a type allows: how
/// many of them are read before one is not allowed, and then how few more
/// would complete them.  More of the first, then fewer of the second, is
/// further.
///
/// # Arguments
///
/// * `schema` - The schema.
/// * `a`      - The type's automaton.
/// * `names`  - The children's names.
///
/// # Returns
///
/// A measure that is greater the further the children get.
fn progress(schema: &Schema, a: &Automaton, names: &[String]) -> (usize, i64) {
    let mut at = a.initial();
    for (i, n) in names.iter().enumerate() {
        let next = a.step(schema, &at, n).0;
        if next.is_empty() {
            return (i, i64::MIN);
        }
        at = next;
    }
    let need = a.shortest(&at).map_or(i64::MAX, |v| v.len() as i64);
    (names.len(), -need)
}

/// Whether an element of a declaration may be empty: no required
/// attributes, no required children and no text it must have.
///
/// # Arguments
///
/// * `schema` - The schema.
/// * `d`      - The declaration, by its index.
///
/// # Returns
///
/// True if `<Name/>` is a valid element of it.
fn empty_will_do(schema: &crate::schema::Schema, d: usize) -> bool {
    match schema.elements[d].ty {
        crate::schema::TypeRef::Complex(ci) => {
            let ct = &schema.complex[ci];
            if ct.is_abstract || ct.attrs.iter().any(|a| a.required) {
                return false;
            }
            if let Some(t) = ct.text {
                return schema.check_value(t, "").is_ok();
            }
            let a = schema.automaton(ci);
            a.accepts(&a.initial())
        }
        crate::schema::TypeRef::Simple(t) => schema.check_value(t, "").is_ok(),
    }
}

/// The place of an element, as [`place`] writes it.
///
/// # Arguments
///
/// * `doc` - The document.
/// * `id`  - The element.
///
/// # Returns
///
/// The place, or `None` if the element is not in the document.
fn path_of(doc: &Document, id: Id) -> Option<String> {
    let names: Vec<String> = doc
        .ancestry(id)
        .iter()
        .filter_map(|a| doc.element(*a).map(label))
        .collect();
    if names.is_empty() {
        None
    } else {
        Some(place(&names))
    }
}

/// A rename of an element where its place ends with given names.  Both
/// sides are written out in full, the names above the element as each
/// version spells them, since a rename above it is part of its place.
struct Renamed {
    /// The names its place ends with in the older version, its own last.
    older: &'static [&'static str],
    /// The same in the newer version.
    newer: &'static [&'static str],
}

/// The elements 1.2 renamed from 1.1.
const RENAMED_11_12: &[Renamed] = &[
    Renamed {
        older: &["Message", "ContainRef"],
        newer: &["Message", "ContainerRef"],
    },
    Renamed {
        older: &["InputSet", "ParameterInstanceRef"],
        newer: &["InputSet", "InputParameterInstanceRef"],
    },
    Renamed {
        older: &["BinaryParameterType", "ContextAlarmList"],
        newer: &["BinaryParameterType", "BinaryContextAlarmList"],
    },
    Renamed {
        older: &["MetaCommandStep", "ArgumentList"],
        newer: &["MetaCommandStep", "ArgumentAssigmentList"],
    },
    Renamed {
        older: &["MetaCommandStep", "ArgumentList", "Argument"],
        newer: &[
            "MetaCommandStep",
            "ArgumentAssigmentList",
            "ArgumentAssignment",
        ],
    },
];

/// The elements 1.3 renamed from 1.2.
const RENAMED_12_13: &[Renamed] = &[
    Renamed {
        older: &["ArgumentTypeSet", "RelativeTimeAgumentType"],
        newer: &["ArgumentTypeSet", "RelativeTimeArgumentType"],
    },
    Renamed {
        older: &["BinaryParameterType", "BinaryContextAlarmList"],
        newer: &["BinaryParameterType", "ContextAlarmList"],
    },
    Renamed {
        older: &["MetaCommandStep", "ArgumentAssigmentList"],
        newer: &["MetaCommandStep", "ArgumentAssignmentList"],
    },
];

/// Applies renames in one direction.
///
/// # Arguments
///
/// * `doc`    - The document.
/// * `table`  - The renames.
/// * `upward` - True to go from the older names to the newer.
///
/// # Returns
///
/// What was renamed, for the record.
fn apply_renames(
    doc: &mut Document,
    table: &[Renamed],
    upward: bool,
) -> Vec<String> {
    let mut done = Vec::new();
    // Parents are visited before children, and an element's place is taken
    // before it is renamed, so every place below is in the names the
    // document had before the step: the side the renames go from.
    let mut f = |e: &mut Element, path: &[String]| {
        for r in table {
            let (from, to) = if upward {
                (r.older, r.newer)
            } else {
                (r.newer, r.older)
            };
            if ends_with(path, from) {
                let new = to[to.len() - 1];
                done.push(format!("{} -> {new}", place(path)));
                rename(e, new);
                break;
            }
        }
    };
    visit(root_mut(doc), &mut Vec::new(), &mut f);
    done
}

impl Step<'_> {
    /// Applies a rename table, recording what it renamed.
    ///
    /// # Arguments
    ///
    /// * `table`  - The renames.
    /// * `upward` - True to go from the older names to the newer.
    fn renames(&mut self, table: &[Renamed], upward: bool) {
        for d in apply_renames(self.doc, table, upward) {
            self.changed(d);
        }
    }

    /// Collects what a closure finds while visiting every element, so that
    /// the step's records can be written after the visit.
    ///
    /// # Arguments
    ///
    /// * `f` - Called with each element and its place; returns the
    ///         records it makes.
    ///
    /// # Returns
    ///
    /// The records, in document order.
    fn each(
        &mut self,
        mut f: impl FnMut(&mut Element, &[String], &mut Vec<Record>),
    ) -> Vec<Record> {
        let mut out = Vec::new();
        let mut g = |e: &mut Element, p: &[String]| f(e, p, &mut out);
        visit(root_mut(self.doc), &mut Vec::new(), &mut g);
        out
    }

    /// Writes records into the step's outcome.
    ///
    /// # Arguments
    ///
    /// * `records` - The records.
    fn record(&mut self, records: Vec<Record>) {
        for r in records {
            match r {
                Record::Changed(s) => self.changed(s),
                Record::Lost(s) => self.lost(s),
                Record::Refused(s) => self.refuse(s),
                Record::Need(key, q, s) => {
                    self.need(key, q, s);
                }
            }
        }
    }
}

/// Something a rule found, kept until it can be written into the outcome.
enum Record {
    /// A change, and where.
    Changed(String),
    /// A loss, and where.
    Lost(String),
    /// A refusal, and why.
    Refused(String),
    /// A need: its key, its question and a suggested answer.
    Need(String, String, Option<String>),
}

/// Fills in attributes 1.3 requires that 1.2 had optional, or rewrites
/// what 1.3 spells otherwise.  Each closure below is one rule of the
/// 1.2 to 1.3 step, named in its comment as the research named it.
impl Step<'_> {
    /// XTCE 1.2 to 1.3.
    fn up_12_13(&mut self) {
        self.renames(RENAMED_12_13, true);
        let answers = self.answers.clone();
        let records =
            self.each(|e, path, out| rules_up_12_13(e, path, &answers, out));
        self.renumber_new();
        self.record(records);
    }

    /// XTCE 1.3 to 1.2.
    fn down_13_12(&mut self) {
        self.renames(RENAMED_12_13, false);
        let answers = self.answers.clone();
        let records =
            self.each(|e, path, out| rules_down_13_12(e, path, &answers, out));
        self.record(records);
    }

    /// XTCE 1.1 to 1.2.
    fn up_11_12(&mut self) {
        let answers = self.answers.clone();
        // The argument lists of block steps change their attributes'
        // names as well as their own.
        let records = self.each(|e, path, out| {
            if e.local() == "Argument"
                && ends_with(
                    path,
                    &["MetaCommandStep", "ArgumentList", "Argument"],
                )
            {
                rename_attr(e, "name", "argumentName");
                rename_attr(e, "value", "argumentValue");
                let _ = out;
            }
        });
        self.record(records);
        self.renames(RENAMED_11_12, true);
        let records =
            self.each(|e, path, out| rules_up_11_12(e, path, &answers, out));
        self.renumber_new();
        self.record(records);
    }

    /// XTCE 1.2 to 1.1.
    fn down_12_11(&mut self) {
        let answers = self.answers.clone();
        self.renames(RENAMED_11_12, false);
        let records =
            self.each(|e, path, out| rules_down_12_11(e, path, &answers, out));
        self.renumber_new();
        self.record(records);
    }

    /// Gives every element the rules made a real id, since they were made
    /// with id 0 while the document was borrowed.
    fn renumber_new(&mut self) {
        let mut next = self.doc.last_id;
        let mut f = |e: &mut Element, _: &[String]| {
            if e.id == 0 {
                next += 1;
                e.id = next;
            }
        };
        visit(root_mut(self.doc), &mut Vec::new(), &mut f);
        self.doc.last_id = next;
    }
}

/// Puts a 1.1 ByteOrderList first in an encoding.
///
/// # Arguments
///
/// * `e`     - The encoding.
/// * `order` - Each byte's significance, in the order they are sent.
fn put_byte_list(e: &mut Element, order: &[String]) {
    let mut list = sibling(e, "ByteOrderList", 0);
    for s in order {
        let mut b = sibling(e, "Byte", 0);
        put_attr(&mut b, "byteSignificance", s);
        list.empty = false;
        list.children.push(Node::Element(b));
    }
    put_child(e, 0, Node::Element(list));
}

/// Takes out the whitespace among an element's children that only laid
/// them out, so that it can be laid out again where it is put.
///
/// # Arguments
///
/// * `e` - The element.
fn strip_space(e: &mut Element) {
    if !e.has_text() {
        e.children.retain(|c| !c.is_space());
    }
}

/// The rules of the step from XTCE 1.2 to 1.3, applied to one element.
///
/// # Arguments
///
/// * `e`       - The element.
/// * `path`    - The places of it and its ancestors, outermost first.
/// * `answers` - The answers to the conversion's needs.
/// * `out`     - What the rules find, added to.
fn rules_up_12_13(
    e: &mut Element,
    path: &[String],
    answers: &Answers,
    out: &mut Vec<Record>,
) {
    let here = place(path);
    // 12-timeassoc-unit: the units are spelt as TimeUnitsType's.
    if e.local() == "TimeAssociation" {
        if let Some(u) = e.get("unit") {
            let new = match u.as_str() {
                "si_nanosecond" => Some("nanoseconds"),
                "si_microsecond" => Some("microseconds"),
                "si_millsecond" => Some("milliseconds"),
                "si_second" => Some("seconds"),
                "minute" => Some("minutes"),
                "day" => Some("days"),
                _ => None,
            };
            match new {
                Some(n) => {
                    put_attr(e, "unit", n);
                    out.push(Record::Changed(format!(
                        "{here}: unit {u} -> {n}"
                    )));
                }
                None => out.push(Record::Refused(format!(
                    "{here}: the unit {u} has no XTCE 1.3 \
                     equivalent; give the offset in another unit \
                     first"
                ))),
            }
        }
    }
    // 12-timeunits-picoseconds.
    if e.local() == "Encoding"
        && e.get("units").as_deref() == Some("picoSeconds")
    {
        put_attr(e, "units", "picoseconds");
        out.push(Record::Changed(format!(
            "{here}: picoSeconds -> picoseconds"
        )));
    }
    // 12-container-binaryencoding: a container's BinaryEncoding
    // has no bit or byte order in 1.3.
    if e.local() == "BinaryEncoding"
        && path.len() >= 2
        && ends_with(&path[..path.len() - 1], &["SequenceContainer"])
    {
        for (a, default) in [
            ("bitOrder", "mostSignificantBitFirst"),
            ("byteOrder", "mostSignificantByteFirst"),
        ] {
            if let Some(v) = take_attr(e, a) {
                if v != default {
                    out.push(Record::Lost(format!(
                        "{here}: @{a}=\"{v}\", which a container's \
                         encoding cannot have in XTCE 1.3"
                    )));
                }
            }
        }
    }
    // 12-derivation-mathop: a Derivation is no longer a calibrator.
    if e.local() == "Derivation" {
        for a in ["name", "shortDescription"] {
            if let Some(v) = take_attr(e, a) {
                out.push(Record::Lost(format!("{here}: @{a}=\"{v}\"")));
            }
        }
        if !take_children(e, "AncillaryDataSet").is_empty() {
            out.push(Record::Lost(format!("{here}: AncillaryDataSet")));
        }
    }
    // FrameStreamType/ServiceRef: no text in 1.3.
    if e.local() == "ServiceRef" && e.has_text() {
        let t = e.text();
        e.children.clear();
        e.empty = true;
        if Some(t.trim()) != e.get("serviceRef").as_deref() {
            out.push(Record::Lost(format!("{here}: its text {t:?}")));
        }
    }
    // 12-discretelookup-default: required in 1.3.
    if e.local() == "DiscreteLookupList" && e.get("defaultValue").is_none() {
        let key = format!("{here}@defaultValue");
        match answers.get(&key) {
            Some(v) => put_attr(e, "defaultValue", v),
            None => out.push(Record::Need(
                key,
                format!(
                    "{here}: XTCE 1.3 needs a defaultValue, the \
                     value used when no lookup matches; 1.2 did not \
                     say what happens then.  Give a whole number."
                ),
                None,
            )),
        }
    }
    // CRC/@width: derived from the polynomial's hex digits.
    if e.local() == "CRC" && e.get("width").is_none() {
        let digits = e
            .elements()
            .find(|c| c.local() == "Polynomial")
            .map(|p| p.text().trim().len())
            .unwrap_or(0);
        let w = (digits * 4).to_string();
        put_attr(e, "width", &w);
        out.push(Record::Changed(format!(
            "{here}: width {w}, from the polynomial's hex digits"
        )));
    }
    // 12-constant-name.
    if e.local() == "Constant" && e.get("constantName").is_none() {
        let n = format!("constant{}", e.id);
        put_attr(e, "constantName", &n);
        out.push(Record::Changed(format!(
            "{here}: constantName {n}, which 1.3 requires"
        )));
    }
    // minConformance reads as 1 in 1.3 where 1.2 read it as
    // minViolations.
    if e.get("minViolations").is_some_and(|v| v.trim() != "1")
        && e.get("minConformance").is_none()
    {
        let v = e.get("minViolations").unwrap_or_default();
        put_attr(e, "minConformance", &v);
        out.push(Record::Changed(format!(
            "{here}: minConformance {v}, as 1.2 read it"
        )));
    }
    // 12-variable-string.
    if e.local() == "Variable" {
        let has_lead = child_index(e, "LeadingSize").is_some();
        let has_term = child_index(e, "TerminationChar").is_some();
        if has_lead && has_term {
            take_children(e, "TerminationChar");
            out.push(Record::Changed(format!(
                "{here}: TerminationChar taken out, which 1.2 says \
                 has no meaning beside a LeadingSize"
            )));
        } else if !has_lead && !has_term {
            let mut t = sibling(e, "TerminationChar", 0);
            t.empty = false;
            t.children.push(Node::Text("00".to_string()));
            let n = e.elements().count();
            put_child(e, n, Node::Element(t));
            out.push(Record::Changed(format!(
                "{here}: TerminationChar 00, 1.2's default, which \
                 1.3 needs written"
            )));
        }
    }
    // 12-argref-nopath: argument references may not be paths.
    if e.local() == "ArgumentAssignment" {
        if let Some(n) = e.get("argumentName") {
            if n.contains('/') {
                out.push(Record::Refused(format!(
                    "{here}: the argument reference {n} has a path, \
                     which XTCE 1.3 does not allow; name the \
                     argument alone"
                )));
            }
        }
    }
}

/// The rules of the step from XTCE 1.3 to 1.2, applied to one element.
///
/// # Arguments
///
/// * `e`       - The element.
/// * `path`    - The places of it and its ancestors, outermost first.
/// * `answers` - The answers to the conversion's needs.
/// * `out`     - What the rules find, added to.
fn rules_down_13_12(
    e: &mut Element,
    path: &[String],
    answers: &Answers,
    out: &mut Vec<Record>,
) {
    let here = place(path);
    if e.local() == "TimeAssociation" {
        // Hours and picoseconds have no 1.2 unit, but minutes and
        // nanoseconds do, so the offset is given in those instead.
        let scale = match e.get("unit").as_deref() {
            Some("hours") => Some(("minutes", 60.0)),
            Some("picoseconds") => Some(("nanoseconds", 0.001)),
            _ => None,
        };
        if let Some((unit, by)) = scale {
            let old = e.get("unit").unwrap_or_default();
            if let Some(off) = e.get("offset") {
                match off.trim().parse::<f64>() {
                    Ok(v) => put_attr(e, "offset", &(v * by).to_string()),
                    Err(_) => out.push(Record::Refused(format!(
                        "{here}: the offset {off} is not a number"
                    ))),
                }
            }
            put_attr(e, "unit", unit);
            out.push(Record::Changed(format!(
                "{here}: the offset given in {unit} rather than {old}"
            )));
        }
        if let Some(u) = e.get("unit") {
            let old = match u.as_str() {
                "nanoseconds" => Some("si_nanosecond"),
                "microseconds" => Some("si_microsecond"),
                "milliseconds" => Some("si_millsecond"),
                "seconds" => Some("si_second"),
                "minutes" => Some("minute"),
                "days" => Some("day"),
                _ => None,
            };
            match old {
                Some(o) => {
                    put_attr(e, "unit", o);
                    out.push(Record::Changed(format!(
                        "{here}: unit {u} -> {o}"
                    )));
                }
                None => out.push(Record::Refused(format!(
                    "{here}: the unit {u} has no XTCE 1.2 \
                     equivalent; give the offset in another unit \
                     first"
                ))),
            }
        }
    }
    if e.local() == "Encoding"
        && e.get("units").as_deref() == Some("picoseconds")
    {
        put_attr(e, "units", "picoSeconds");
        out.push(Record::Changed(format!(
            "{here}: picoseconds -> picoSeconds"
        )));
    }
    // A Derivation's type, MathOperationType, is abstract in the
    // 1.2 schema, so no 1.2 document can hold one.
    if e.local() == "Derivation"
        && ends_with(path, &["ParameterToSet", "Derivation"])
    {
        out.push(Record::Refused(format!(
            "{here}: XTCE 1.2's schema makes a Derivation's type \
             abstract, so no 1.2 document can have one; take it out \
             first"
        )));
    }
    // FrameStreamType/ServiceRef holds the service's name as its
    // text in 1.2 as well as in serviceRef.
    if e.local() == "ServiceRef" && !e.has_text() {
        if let Some(r) = e.get("serviceRef") {
            e.children = vec![Node::Text(crate::tree::escape(&r, '\0'))];
            e.empty = false;
        }
    }
    // A command container's BinaryEncoding needs a SizeInBits in
    // 1.2, which 1.3 lets it leave out.
    if e.local() == "BinaryEncoding"
        && path.len() >= 2
        && ends_with(&path[..path.len() - 1], &["CommandContainer"])
        && child_index(e, "SizeInBits").is_none()
    {
        let key = format!("{here}/SizeInBits");
        match answers.get(&key) {
            Some(v) => {
                let mut size = sibling(e, "SizeInBits", 0);
                let mut fv = sibling(e, "FixedValue", 0);
                fv.empty = false;
                fv.children.push(Node::Text(v.trim().to_string()));
                size.empty = false;
                size.children.push(Node::Element(fv));
                put_child(e, 0, Node::Element(size));
            }
            None => out.push(Record::Need(
                key,
                format!(
                    "{here}: XTCE 1.2 needs the encoding's size in \
                     bits, which this document leaves out.  Give a \
                     whole number of bits."
                ),
                None,
            )),
        }
    }
    // Parity's bitsFromReference was required in 1.2.
    if e.local() == "Parity" && e.get("bitsFromReference").is_none() {
        put_attr(e, "bitsFromReference", "0");
    }
    // LinearAdjustment's slope had no default in 1.2.
    if e.local() == "LinearAdjustment" && e.get("slope").is_none() {
        put_attr(e, "slope", "1");
        out.push(Record::Changed(format!(
            "{here}: slope 1, the 1.3 default, written out"
        )));
    }
    if e.local() == "DiscreteLookupList" {
        if let Some(v) = take_attr(e, "defaultValue") {
            out.push(Record::Lost(format!("{here}: @defaultValue=\"{v}\"")));
        }
    }
    if e.local() == "ArgumentAssignment" || e.local() == "ParameterRefEntry" {
        for a in ["argumentName", "parameterRef"] {
            if let Some(v) = e.get(a) {
                let parts = v.split('/').next_back().unwrap_or("");
                if parts.contains('[') || parts.contains('.') {
                    out.push(Record::Refused(format!(
                        "{here}: the reference {v} names a member or \
                         an array element, which XTCE 1.2 cannot"
                    )));
                }
            }
        }
    }
    if e.local() == "Variable"
        && child_index(e, "DynamicValue").is_none()
        && child_index(e, "DiscreteLookupList").is_none()
    {
        out.push(Record::Refused(format!(
            "{here}: XTCE 1.2 needs a DynamicValue or \
             DiscreteLookupList in a variable string; add one first"
        )));
    }
    // systemType and assetType are new; unknown is no loss.
    if e.local() == "SpaceSystem" {
        if let Some(v) = take_attr(e, "systemType") {
            if v != "unknown" {
                out.push(Record::Lost(format!("{here}: @systemType=\"{v}\"")));
            }
        }
    }
}

/// The rules of the step from XTCE 1.1 to 1.2, applied to one element.
///
/// # Arguments
///
/// * `e`       - The element.
/// * `path`    - The places of it and its ancestors, outermost first.
/// * `answers` - The answers to the conversion's needs.
/// * `out`     - What the rules find, added to.
fn rules_up_11_12(
    e: &mut Element,
    path: &[String],
    answers: &Answers,
    out: &mut Vec<Record>,
) {
    let here = place(path);
    if e.local() == "EnumerationAlarm"
        && rename_attr(e, "enumerationValue", "enumerationLabel")
    {
        out.push(Record::Changed(format!(
            "{here}: enumerationValue -> enumerationLabel"
        )));
    }
    if e.local() == "ArrayArgumentRefEntry"
        && rename_attr(e, "parameterRef", "argumentRef")
    {
        out.push(Record::Changed(format!(
            "{here}: parameterRef -> argumentRef"
        )));
    }
    // 11-integer encodings' spelling.
    if let Some(enc) = e.get("encoding") {
        let new = match enc.as_str() {
            "twosCompliment" => Some("twosComplement"),
            "onesCompliment" => Some("onesComplement"),
            _ => None,
        };
        if let Some(n) = new {
            put_attr(e, "encoding", n);
            out.push(Record::Changed(format!("{here}: {enc} -> {n}")));
        }
    }
    // 11-fixedinteger-lexical.
    for a in ["initialValue", "minInclusive", "maxInclusive"] {
        if let Some(v) = e.get(a) {
            if let Some(d) = to_decimal(&v) {
                put_attr(e, a, &d);
                out.push(Record::Changed(format!("{here}: @{a} {v} -> {d}")));
            }
        }
    }
    if e.local() == "FixedValue" {
        let t = e.text();
        if let Some(d) = to_decimal(&t) {
            let comments: Vec<Node> = e
                .children
                .iter()
                .filter(|c| matches!(c, Node::Comment(_)))
                .cloned()
                .collect();
            e.children = vec![Node::Text(d.clone())];
            e.children.extend(comments);
            out.push(Record::Changed(format!("{here}: {} -> {d}", t.trim())));
        }
    }
    // ThisParameterOperand: any content in 1.1, empty in 1.2.
    if e.local() == "ThisParameterOperand" && !e.children.is_empty() {
        let held = e.text();
        e.children.clear();
        e.empty = true;
        if !held.trim().is_empty() || e.elements().next().is_some() {
            out.push(Record::Lost(format!(
                "{here}: what it held, {:?}, which 1.2 requires to \
                 be empty",
                held.trim()
            )));
        }
    }
    // 11-polynomial-exponent.
    if e.local() == "Term" {
        if let Some(x) = e.get("exponent") {
            match x.trim().parse::<f64>() {
                Ok(f) if f >= 0.0 && f.fract() == 0.0 => {
                    let w = (f as u64).to_string();
                    if w != x.trim() {
                        put_attr(e, "exponent", &w);
                    }
                }
                _ => out.push(Record::Refused(format!(
                    "{here}: the exponent {x} is not a whole number \
                     0 or more, which 1.2 requires"
                ))),
            }
        }
    }
    // NumberFormat's thousands grouping: 1.1 defaulted it on.
    if e.local() == "NumberFormat" && e.get("showThousandsGrouping").is_none() {
        put_attr(e, "showThousandsGrouping", "true");
        out.push(Record::Changed(format!(
            "{here}: showThousandsGrouping true, 1.1's default, \
             written out"
        )));
    }
    // 11-linear-slope: an absent 1.1 slope is 0.
    if e.local() == "LinearAdjustment" && e.get("slope").is_none() {
        put_attr(e, "slope", "0");
        out.push(Record::Changed(format!(
            "{here}: slope 0, 1.1's default, written out; check \
             that it is what was meant"
        )));
    }
    // FixedValueEntry's sizeInBits became required.
    if e.local() == "FixedValueEntry" && e.get("sizeInBits").is_none() {
        let n = e.get("binaryValue").map_or(0, |b| b.trim().len()) * 4;
        if n == 0 {
            out.push(Record::Refused(format!(
                "{here}: has neither sizeInBits nor a binaryValue \
                 to take it from"
            )));
        } else {
            put_attr(e, "sizeInBits", &n.to_string());
            out.push(Record::Changed(format!(
                "{here}: sizeInBits {n}, from binaryValue's digits"
            )));
        }
    }
    // lastEntryForThisArrayInstance has no 1.2 form.
    if let Some(v) = take_attr(e, "lastEntryForThisArrayInstance") {
        if v.trim() == "true" || v.trim() == "1" {
            out.push(Record::Lost(format!(
                "{here}: @lastEntryForThisArrayInstance=\"{v}\""
            )));
        }
    }
    // A calibrator's LongDescription and AliasSet.
    if matches!(e.local(), "DefaultCalibrator" | "Calibrator") {
        for n in ["LongDescription", "AliasSet"] {
            if !take_children(e, n).is_empty() {
                out.push(Record::Lost(format!("{here}: {n}")));
            }
        }
    }
    // consequenceLevel's values were replaced.
    if let Some(c) = e.get("consequenceLevel") {
        let key = format!("{here}@consequenceLevel");
        let new = match c.as_str() {
            "none" => Some("normal".to_string()),
            "critical" => Some("critical".to_string()),
            _ => answers.get(&key).cloned(),
        };
        match new {
            Some(n) => {
                if n != c {
                    put_attr(e, "consequenceLevel", &n);
                    out.push(Record::Changed(format!(
                        "{here}: consequenceLevel {c} -> {n}"
                    )));
                }
            }
            None => out.push(Record::Need(
                key,
                format!(
                    "{here}: consequenceLevel {c} has no XTCE 1.2 \
                     equivalent.  Give one of normal, vital, \
                     critical, forbidden, user1, user2."
                ),
                Some("normal".to_string()),
            )),
        }
    }
    // 11-timeassoc-offset: a date, which cannot be a number of
    // seconds.
    if e.local() == "TimeAssociation" && e.get("offset").is_some() {
        out.push(Record::Refused(format!(
            "{here}: its offset is a date in XTCE 1.1 and a number \
             of seconds in 1.2; take it out, then give it again \
             after converting"
        )));
    }
    // 11-validrange-applies: onto the ValidRange.
    if matches!(e.local(), "FloatParameterType" | "IntegerParameterType") {
        if let Some(v) = take_attr(e, "validRangeAppliesToCalibrated") {
            if let Some(r) = child_mut(e, "ValidRange") {
                put_attr(r, "validRangeAppliesToCalibrated", &v);
            }
        }
    }
    // 11-argtype-validrange: into a ValidRangeSet, the one the type
    // has or a new one, first.
    if matches!(e.local(), "FloatArgumentType" | "IntegerArgumentType") {
        let applies = take_attr(e, "validRangeAppliesToCalibrated");
        let ranges = take_children(e, "ValidRange");
        if !ranges.is_empty() {
            if child_index(e, "ValidRangeSet").is_none() {
                let set = sibling(e, "ValidRangeSet", 0);
                // It is the last child 1.2 allows an argument type.
                let at = e.elements().count();
                put_child(e, at, Node::Element(set));
            }
            let set = child_mut(e, "ValidRangeSet").expect("put in above");
            let had = set.get("validRangeAppliesToCalibrated");
            match (&applies, &had) {
                (Some(a), Some(h)) if a.trim() != h.trim() => {
                    out.push(Record::Refused(format!(
                        "{here}: its ValidRange and its ValidRangeSet \
                         say differently whether they apply to the \
                         calibrated value, which 1.2 cannot; make \
                         them agree first"
                    )));
                }
                (Some(a), None) => {
                    put_attr(set, "validRangeAppliesToCalibrated", a)
                }
                _ => {}
            }
            for (i, r) in ranges.into_iter().enumerate() {
                put_child(set, i, Node::Element(r));
            }
            out.push(Record::Changed(format!(
                "{here}: ValidRange put in its ValidRangeSet"
            )));
        }
    }
    // 11-byteorder: the list becomes one attribute.
    if let Some(i) = child_index(e, "ByteOrderList") {
        let list = take_children(e, "ByteOrderList");
        let _ = i;
        let order: Vec<String> = list
            .first()
            .map(|l| {
                l.elements()
                    .filter_map(|b| b.get("byteSignificance"))
                    .map(|s| s.trim().to_string())
                    .collect()
            })
            .unwrap_or_default();
        let n = order.len();
        let msb: Vec<String> = (0..n).rev().map(|i| i.to_string()).collect();
        let lsb: Vec<String> = (0..n).map(|i| i.to_string()).collect();
        let value = if order == msb {
            None
        } else if order == lsb {
            Some("leastSignificantByteFirst".to_string())
        } else if order
            .iter()
            .all(|s| s.parse::<u32>().is_ok_and(|v| v <= 15))
        {
            Some(order.join(","))
        } else {
            out.push(Record::Refused(format!(
                "{here}: the byte order {} names a byte above 15, \
                 which XTCE 1.2 cannot",
                order.join(",")
            )));
            None
        };
        if let Some(v) = value {
            put_attr(e, "byteOrder", &v);
        }
        out.push(Record::Changed(format!(
            "{here}: ByteOrderList -> byteOrder"
        )));
    }
    // 11-crc-polynomial: Terms become hex and a width.
    if e.local() == "CRC" {
        if let Some(p) = child_mut(e, "Polynomial") {
            let terms: Vec<(String, String)> = p
                .elements()
                .map(|t| {
                    (
                        t.get("coefficient").unwrap_or_default(),
                        t.get("exponent").unwrap_or_default(),
                    )
                })
                .collect();
            if !terms.is_empty() {
                let mut width = 0u32;
                let mut bits = 0u128;
                let mut ok = true;
                for (c, x) in &terms {
                    let x: u32 = match x.trim().parse::<f64>() {
                        Ok(f) if f >= 0.0 && f.fract() == 0.0 && f < 128.0 => {
                            f as u32
                        }
                        _ => {
                            ok = false;
                            0
                        }
                    };
                    match c.trim() {
                        "1" => {
                            width = width.max(x);
                            bits |= 1u128 << x;
                        }
                        "0" => {}
                        _ => ok = false,
                    }
                }
                if ok && width > 0 {
                    bits &= !(1u128 << width);
                    let digits = width.div_ceil(8) as usize * 2;
                    let hex = format!("{bits:0digits$X}");
                    p.children = vec![Node::Text(hex.clone())];
                    p.empty = false;
                    put_attr(e, "width", &width.to_string());
                    out.push(Record::Changed(format!(
                        "{here}: polynomial terms -> {hex}, width {width}"
                    )));
                } else {
                    out.push(Record::Refused(format!(
                        "{here}: the polynomial's terms are not each \
                         a coefficient 0 or 1 and a whole exponent"
                    )));
                }
            }
        }
    }
    // 11-customalarm-wrap.
    if e.local() == "CustomAlarm" && child_index(e, "InputAlgorithm").is_none()
    {
        let mut inner = sibling(e, "InputAlgorithm", 0);
        inner.attrs = std::mem::take(&mut e.attrs);
        if let Some(a) = inner.attrs.first_mut() {
            a.lead = " ".to_string();
        }
        inner.children = std::mem::take(&mut e.children);
        inner.empty = inner.children.is_empty();
        strip_space(&mut inner);
        e.children.clear();
        put_child(e, 0, Node::Element(inner));
        out.push(Record::Changed(format!(
            "{here}: wrapped in an InputAlgorithm"
        )));
    }
    // 11-array-dimensions.
    if matches!(e.local(), "ArrayParameterType" | "ArrayArgumentType")
        && child_index(e, "DimensionList").is_none()
    {
        if let Some(n) = take_attr(e, "numberOfDimensions") {
            let dims: usize = n.trim().parse().unwrap_or(0);
            let mut list = sibling(e, "DimensionList", 0);
            for d in 0..dims {
                let key = format!("{here}/Dimension[{d}]@size");
                let Some(size) = answers.get(&key) else {
                    out.push(Record::Need(
                        key,
                        format!(
                            "{here}: XTCE 1.2 needs the size of \
                             dimension {d}, which 1.1 does not \
                             hold.  Give the number of elements."
                        ),
                        None,
                    ));
                    continue;
                };
                let last = size.trim().parse::<i64>().unwrap_or(1) - 1;
                let mut dim = sibling(e, "Dimension", 0);
                let mut start = sibling(e, "StartingIndex", 0);
                let mut fv = sibling(e, "FixedValue", 0);
                fv.empty = false;
                fv.children.push(Node::Text("0".to_string()));
                start.empty = false;
                start.children.push(Node::Element(fv));
                let mut end = sibling(e, "EndingIndex", 0);
                let mut fv = sibling(e, "FixedValue", 0);
                fv.empty = false;
                fv.children.push(Node::Text(last.to_string()));
                end.empty = false;
                end.children.push(Node::Element(fv));
                dim.empty = false;
                dim.children.push(Node::Element(start));
                dim.children.push(Node::Element(end));
                list.empty = false;
                list.children.push(Node::Element(dim));
            }
            let n = e.elements().count();
            put_child(e, n, Node::Element(list));
        }
    }
}

/// The rules of the step from XTCE 1.2 to 1.1, applied to one element.
///
/// # Arguments
///
/// * `e`       - The element.
/// * `path`    - The places of it and its ancestors, outermost first.
/// * `answers` - The answers to the conversion's needs.
/// * `out`     - What the rules find, added to.
fn rules_down_12_11(
    e: &mut Element,
    path: &[String],
    answers: &Answers,
    out: &mut Vec<Record>,
) {
    // No rule of this step asks anything, so there is nothing to answer.
    let _ = answers;
    let here = place(path);
    if e.local() == "ArgumentAssignment"
        || (e.local() == "Argument"
            && ends_with(
                path,
                &["MetaCommandStep", "ArgumentList", "Argument"],
            ))
    {
        rename_attr(e, "argumentName", "name");
        rename_attr(e, "argumentValue", "value");
    }
    if e.local() == "EnumerationAlarm"
        && rename_attr(e, "enumerationLabel", "enumerationValue")
    {
        out.push(Record::Changed(format!(
            "{here}: enumerationLabel -> enumerationValue"
        )));
    }
    if e.local() == "ArrayArgumentRefEntry"
        && rename_attr(e, "argumentRef", "parameterRef")
    {
        out.push(Record::Changed(format!(
            "{here}: argumentRef -> parameterRef"
        )));
    }
    if let Some(enc) = e.get("encoding") {
        let old = match enc.as_str() {
            "twosComplement" => Some("twosCompliment"),
            "onesComplement" => Some("onesCompliment"),
            _ => None,
        };
        if let Some(o) = old {
            put_attr(e, "encoding", o);
        }
    }
    if e.local() == "NumberFormat" && e.get("showThousandsGrouping").is_none() {
        put_attr(e, "showThousandsGrouping", "false");
    }
    if e.local() == "LinearAdjustment" && e.get("slope").is_none() {
        put_attr(e, "slope", "1");
    }
    if let Some(c) = e.get("consequenceLevel") {
        match c.as_str() {
            "normal" => put_attr(e, "consequenceLevel", "none"),
            "critical" => {}
            other => out.push(Record::Refused(format!(
                "{here}: consequenceLevel {other} has no XTCE 1.1 \
                 equivalent; make it normal or critical first"
            ))),
        }
    }
    if e.local() == "CustomAlarm" {
        if let Some(inner) = take_children(e, "InputAlgorithm").pop() {
            for a in ["name", "shortDescription"] {
                if let Some(v) = take_attr(e, a) {
                    out.push(Record::Lost(format!("{here}: @{a}=\"{v}\"")));
                }
            }
            if !take_children(e, "AncillaryDataSet").is_empty() {
                out.push(Record::Lost(format!("{here}: AncillaryDataSet")));
            }
            e.attrs = inner.attrs;
            e.children = inner.children;
            e.empty = e.children.is_empty();
        }
    }
    if let Some(order) = take_attr(e, "byteOrder") {
        match order.as_str() {
            "mostSignificantByteFirst" => {}
            "leastSignificantByteFirst" => {
                let size = e
                    .get("sizeInBits")
                    .and_then(|s| s.trim().parse::<usize>().ok());
                match size {
                    Some(bits) if bits % 8 == 0 && bits >= 16 => {
                        let list: Vec<String> =
                            (0..bits / 8).map(|i| i.to_string()).collect();
                        put_byte_list(e, &list);
                    }
                    _ => out.push(Record::Refused(format!(
                        "{here}: least significant byte first needs \
                         the size in bytes, which this encoding \
                         does not give"
                    ))),
                }
            }
            list => {
                let items: Vec<String> =
                    list.split(',').map(|s| s.trim().to_string()).collect();
                put_byte_list(e, &items);
            }
        }
    }
    if e.local() == "CRC" {
        for n in ["InitRemainder", "FinalXOR"] {
            if child_index(e, n).is_some() {
                out.push(Record::Refused(format!(
                    "{here}: XTCE 1.1 cannot give a CRC's {n}"
                )));
            }
        }
        let width = take_attr(e, "width");
        if let Some(p) = child_mut(e, "Polynomial") {
            let hex = p.text().trim().to_string();
            let width: u32 = width
                .and_then(|w| w.trim().parse().ok())
                .unwrap_or(hex.len() as u32 * 4);
            match u128::from_str_radix(&hex, 16) {
                Ok(bits) => {
                    p.children.clear();
                    let mut terms = Vec::new();
                    for x in (0..width).rev() {
                        if x < 128 && bits & (1u128 << x) != 0 {
                            terms.push(x);
                        }
                    }
                    let mut t = sibling(p, "Term", 0);
                    put_attr(&mut t, "coefficient", "1");
                    put_attr(&mut t, "exponent", &width.to_string());
                    p.children.push(Node::Element(t));
                    for x in terms {
                        let mut t = sibling(p, "Term", 0);
                        put_attr(&mut t, "coefficient", "1");
                        put_attr(&mut t, "exponent", &x.to_string());
                        p.children.push(Node::Element(t));
                    }
                    p.empty = false;
                }
                Err(_) => out.push(Record::Refused(format!(
                    "{here}: the polynomial {hex} is not hexadecimal"
                ))),
            }
        }
    }
    if matches!(e.local(), "ArrayParameterType" | "ArrayArgumentType") {
        if let Some(list) = take_children(e, "DimensionList").pop() {
            let n = list.elements().count();
            put_attr(e, "numberOfDimensions", &n.to_string());
            out.push(Record::Lost(format!(
                "{here}: the sizes of its {n} dimension(s), which \
                 XTCE 1.1 cannot give"
            )));
        }
    }
    if matches!(e.local(), "FloatParameterType" | "IntegerParameterType") {
        if let Some(v) = child_mut(e, "ValidRange")
            .and_then(|r| take_attr(r, "validRangeAppliesToCalibrated"))
        {
            put_attr(e, "validRangeAppliesToCalibrated", &v);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A document of a version holding some XML.
    ///
    /// # Arguments
    ///
    /// * `v`      - The version.
    /// * `inside` - What the SpaceSystem holds.
    ///
    /// # Returns
    ///
    /// The document.
    fn doc(v: Version, inside: &str) -> Document {
        Document::parse(&format!(
            "<?xml version=\"1.0\"?>\n<!-- top -->\n\
             <SpaceSystem xmlns=\"{}\" name=\"S\">{inside}</SpaceSystem>\n",
            v.namespace()
        ))
        .unwrap()
    }

    #[test]
    fn moves_the_namespace_and_keeps_comments() {
        let d = doc(
            Version::V1_2,
            "<!-- here --><TelemetryMetaData><!-- in --></TelemetryMetaData>",
        );
        let o = convert(&d, Version::V1_3, &Answers::new());
        assert!(o.refused.is_empty(), "{:?}", o.refused);
        let text = o.doc.unwrap().write();
        assert!(text.contains(Version::V1_3.namespace()));
        assert!(text.contains("<!-- top -->"));
        assert!(text.contains("<!-- here --><TelemetryMetaData><!-- in -->"));
    }

    #[test]
    fn renames_on_the_way_up_and_back() {
        let inside = "<TelemetryMetaData><ParameterTypeSet>\
            <BinaryParameterType name=\"b\"><UnitSet/>\
            <BinaryDataEncoding><SizeInBits><FixedValue>8</FixedValue>\
            </SizeInBits></BinaryDataEncoding>\
            <BinaryContextAlarmList><ContextAlarm><ContextMatch>\
            <Comparison parameterRef=\"p\" value=\"1\"/></ContextMatch>\
            </ContextAlarm></BinaryContextAlarmList>\
            </BinaryParameterType></ParameterTypeSet></TelemetryMetaData>";
        let d = doc(Version::V1_2, inside);
        assert!(!check(&d).has_errors(), "{:?}", check(&d).problems);
        let o = convert(&d, Version::V1_3, &Answers::new());
        assert!(o.refused.is_empty(), "{:?}", o.refused);
        let up = o.doc.unwrap();
        assert!(up.write().contains("<ContextAlarmList>"));
        let back = convert(&up, Version::V1_2, &Answers::new());
        assert!(back.refused.is_empty(), "{:?}", back.refused);
        assert_eq!(back.doc.unwrap().write(), d.write());
    }

    #[test]
    fn asks_for_what_the_document_does_not_say() {
        let inside = "<TelemetryMetaData><ParameterTypeSet>\
            <StringParameterType name=\"s\"><StringDataEncoding>\
            <Variable maxSizeInBits=\"64\"><DiscreteLookupList>\
            <DiscreteLookup value=\"8\"><Comparison parameterRef=\"p\" \
            value=\"1\"/></DiscreteLookup></DiscreteLookupList>\
            <TerminationChar>00</TerminationChar></Variable>\
            </StringDataEncoding></StringParameterType>\
            </ParameterTypeSet></TelemetryMetaData>";
        let d = doc(Version::V1_2, inside);
        assert!(!check(&d).has_errors(), "{:?}", check(&d).problems);
        let o = convert(&d, Version::V1_3, &Answers::new());
        assert!(!o.done());
        assert_eq!(o.needs.len(), 1, "{:?}", o.needs);
        let mut a = Answers::new();
        a.insert(o.needs[0].key.clone(), "0".to_string());
        let o = convert(&d, Version::V1_3, &a);
        assert!(o.done(), "{:?} {:?}", o.refused, o.needs);
        assert!(o.doc.unwrap().write().contains("defaultValue=\"0\""));
    }

    #[test]
    fn reports_what_a_downgrade_loses() {
        let inside = "<TelemetryMetaData><ParameterTypeSet>\
            <IntegerParameterType name=\"i\"><IntegerDataEncoding/>\
            <DefaultAlarm disabled=\"true\"/>\
            </IntegerParameterType></ParameterTypeSet></TelemetryMetaData>";
        let d = doc(Version::V1_3, inside);
        assert!(!check(&d).has_errors(), "{:?}", check(&d).problems);
        let o = convert(&d, Version::V1_2, &Answers::new());
        assert!(o.done(), "{:?}", o.refused);
        assert!(
            o.lost.iter().any(|l| l.contains("@disabled")),
            "{:?}",
            o.lost
        );
    }

    #[test]
    fn converts_random_documents_between_every_pair_of_versions() {
        for from in Version::ALL {
            for seed in 0..12 {
                let d =
                    Document::parse(&crate::make::sample(from, seed)).unwrap();
                for to in Version::ALL {
                    let mut answers = Answers::new();
                    let mut o = convert(&d, to, &answers);
                    for _ in 0..3 {
                        if o.needs.is_empty() {
                            break;
                        }
                        for n in &o.needs {
                            let a = n.suggestion.clone().unwrap_or("8".into());
                            answers.insert(n.key.clone(), a);
                        }
                        o = convert(&d, to, &answers);
                    }
                    assert!(
                        o.refused.is_empty(),
                        "{from}->{to} {seed}: {:?}",
                        o.refused
                    );
                    let out = o.doc.expect("converted");
                    let r = check(&out);
                    assert!(
                        !r.has_errors(),
                        "{from}->{to} {seed}: {:?}",
                        r.problems
                    );
                    assert_eq!(Version::of_document(&out), Ok(to));
                }
            }
        }
    }

    #[test]
    fn writes_11_numbers_in_decimal() {
        assert_eq!(to_decimal("0x1F").as_deref(), Some("31"));
        assert_eq!(to_decimal("0b101").as_deref(), Some("5"));
        assert_eq!(to_decimal("12"), None);
    }
}
