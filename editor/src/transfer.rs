//! Taking out, moving and copying several elements and comments at once.
//!
//! What is acted on is the rows selected together, less any inside another
//! of them: an element takes everything in it, and the comments written
//! just before it (see [`crate::edit`]).  Each act is one change, so one
//! Undo takes it back whole.
//!
//! Rows are copied as XTCE text, so that they can be pasted in another
//! document, or into a text editor and back.  The text is the elements as
//! they are written, each declaring the namespaces it uses that are
//! declared outside it.  Pasted text is put in the document's own
//! namespace prefix; text that declares no namespace, as a piece cut from
//! a file in a text editor does, is taken to be in the document's.
//!
//! A move keeps references whole: a reference that named a definition
//! before the move and would name another, or nothing, after it is written
//! again to name the same one, as Rename everywhere writes references
//! again.

use std::collections::{HashMap, HashSet};

use crate::edit::{self, Op};
use crate::refs::{base_of, Index, Kind, Loc, Ref};
use crate::schema::Version;
use crate::session::{Change, Session};
use crate::tree::{Attr, Document, Element, Id, Node, DOCUMENT};
use crate::validate::{insertable, Report};

/// Where rows are put, beside or in the row they are put at.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Place {
    /// Before it, under the same parent.
    Before,
    /// After it, under the same parent.
    After,
    /// In it, after what it holds.
    Into,
}

impl Place {
    /// The place a number from the windows stands for.
    ///
    /// # Arguments
    ///
    /// * `n` - 0 before, 1 after, 2 into.
    ///
    /// # Returns
    ///
    /// The place, or `None` for another number.
    pub fn of(n: i32) -> Option<Place> {
        match n {
            0 => Some(Place::Before),
            1 => Some(Place::After),
            2 => Some(Place::Into),
            _ => None,
        }
    }

    /// The word for the place in messages.
    ///
    /// # Returns
    ///
    /// The word.
    pub fn word(self) -> &'static str {
        match self {
            Place::Before => "before",
            Place::After => "after",
            Place::Into => "into",
        }
    }
}

/// What a move does, ready for [`Session::apply`].
#[derive(Debug, Clone)]
pub struct Moved {
    /// The changes, document by document.
    pub changes: Vec<Change>,
    /// The ids the rows have where they are put, in order.
    pub ids: Vec<Id>,
    /// The last id given to them in the document they are put in.
    pub last: Id,
    /// How many references were written again to keep naming what they
    /// named.
    pub rewritten: usize,
    /// How many references would name something else, or nothing, and no
    /// way of writing them names what they named.
    pub unkept: usize,
}

/// What a paste does, ready for [`Session::apply`].
#[derive(Debug, Clone)]
pub struct Pasted {
    /// The change.
    pub changes: Vec<Change>,
    /// The ids of what was put in, in order.
    pub ids: Vec<Id>,
    /// The last id given to it.
    pub last: Id,
    /// How many references in it were written again to keep naming what
    /// they named.
    pub rewritten: usize,
    /// How many would name something else, or nothing, and no way of
    /// writing them names what they named.
    pub unkept: usize,
}

/// What a reference in copied rows named when they were copied.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Named {
    /// A definition copied with it: the definition's place among the
    /// copied elements and comments, in document order.
    Inside(usize),
    /// A definition outside the copy, by its kind and its path from the
    /// root: its SpaceSystems' names, then its own.
    Outside(Kind, Vec<String>),
}

/// A reference in copied rows, and what it named.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RefNote {
    /// The place among the copied elements and comments, in document
    /// order, of the element it is written on.
    pub at: usize,
    /// The attribute it is written in, or `None` for the element's text.
    pub attr: Option<String>,
    /// What it named.
    pub named: Named,
}

/// The ids of a document's elements and comments in document order.
///
/// # Arguments
///
/// * `doc` - The document.
///
/// # Returns
///
/// Each id's place in document order.
fn order(doc: &Document) -> HashMap<Id, usize> {
    let mut all = Vec::new();
    for n in &doc.top {
        ids_in(n, &mut |id| all.push(id));
    }
    all.into_iter().enumerate().map(|(i, id)| (id, i)).collect()
}

/// Gives each element and comment in a node, the node included, to a
/// function, in document order.
///
/// # Arguments
///
/// * `n`   - The node.
/// * `out` - Called with each id.
fn ids_in(n: &Node, out: &mut impl FnMut(Id)) {
    match n {
        Node::Element(e) => {
            out(e.id);
            for c in &e.children {
                ids_in(c, out);
            }
        }
        Node::Comment(c) => out(c.id),
        _ => {}
    }
}

/// The ids of every element and comment in some nodes, in document order.
///
/// # Arguments
///
/// * `nodes` - The nodes.
///
/// # Returns
///
/// The ids.
fn all_ids(nodes: &[Node]) -> Vec<Id> {
    let mut out = Vec::new();
    for n in nodes {
        ids_in(n, &mut |id| out.push(id));
    }
    out
}

/// The rows to act on: the elements and comments among some ids that are
/// not inside another of them, in document order.
///
/// # Arguments
///
/// * `doc` - The document.
/// * `ids` - The ids of the rows selected.
///
/// # Returns
///
/// The outermost of them.
pub fn roots(doc: &Document, ids: &[Id]) -> Vec<Id> {
    let set: HashSet<Id> = ids
        .iter()
        .copied()
        .filter(|id| {
            matches!(doc.node(*id), Some(Node::Element(_) | Node::Comment(_)))
        })
        .collect();
    let places = order(doc);
    let mut out: Vec<Id> = set
        .iter()
        .copied()
        .filter(|id| {
            let chain = doc.ancestry(*id);
            !chain[..chain.len() - 1].iter().any(|a| set.contains(a))
        })
        .collect();
    out.sort_by_key(|id| places.get(id).copied().unwrap_or(usize::MAX));
    out
}

/// What a row takes with it: an element with the comments written just
/// before it, or a comment alone.
///
/// # Arguments
///
/// * `doc` - The document.
/// * `id`  - The element or comment.
///
/// # Returns
///
/// The nodes, as they are in the document, without the whitespace
/// between them.
fn span(doc: &Document, id: Id) -> Vec<Node> {
    if let Some((_, taken)) = edit::remove(doc, id) {
        return taken;
    }
    doc.node(id).cloned().into_iter().collect()
}

/// The nodes some rows take with them, each once, in document order.
///
/// # Arguments
///
/// * `doc`   - The document.
/// * `roots` - The rows, as [`roots`] gives them.
///
/// # Returns
///
/// The nodes.
fn spans(doc: &Document, roots: &[Id]) -> Vec<Node> {
    let mut out: Vec<Node> = Vec::new();
    for id in roots {
        for n in span(doc, *id) {
            if !out.iter().any(|o| o.id() == n.id()) {
                out.push(n);
            }
        }
    }
    // A comment selected on its own and then taken again with the element
    // after it is in once, where the element puts it.
    let places = order(doc);
    out.sort_by_key(|n| {
        n.id().and_then(|i| places.get(&i).copied()).unwrap_or(0)
    });
    out
}

/// A short description of some rows, for menus and messages: an element
/// and its name, "a comment", or how many rows there are.
///
/// # Arguments
///
/// * `doc`   - The document.
/// * `roots` - The rows.
///
/// # Returns
///
/// The description.
pub fn describe(doc: &Document, roots: &[Id]) -> String {
    match roots {
        [id] => describe_one(doc, *id),
        _ if roots.iter().all(|id| doc.element(*id).is_some()) => {
            format!("{} elements", roots.len())
        }
        _ => format!("{} rows", roots.len()),
    }
}

/// A short description of nodes not yet in a document, as [`describe`]
/// gives one of rows.
///
/// # Arguments
///
/// * `nodes` - The nodes.
///
/// # Returns
///
/// The description.
pub fn describe_nodes(nodes: &[Node]) -> String {
    match nodes {
        [Node::Element(e)] => match e.get("name") {
            Some(n) => format!("{} {n}", e.local()),
            None => e.local().to_string(),
        },
        [_] => "a comment".to_string(),
        _ if nodes.iter().all(|n| matches!(n, Node::Element(_))) => {
            format!("{} elements", nodes.len())
        }
        _ => format!("{} rows", nodes.len()),
    }
}

/// A short description of one row: an element and its name, or "a
/// comment".
///
/// # Arguments
///
/// * `doc` - The document.
/// * `id`  - The row's element or comment.
///
/// # Returns
///
/// The description.
pub fn describe_one(doc: &Document, id: Id) -> String {
    match doc.element(id) {
        Some(e) => match e.get("name") {
            Some(n) => format!("{} {n}", e.local()),
            None => e.local().to_string(),
        },
        None => "a comment".to_string(),
    }
}

/// Makes the op that takes one row out.
///
/// # Arguments
///
/// * `doc` - The document.
/// * `id`  - The element or comment.
///
/// # Returns
///
/// The op, and what it took out, without whitespace.
///
/// # Errors
///
/// Says why, for the root.
fn take_out(doc: &Document, id: Id) -> Result<(Op, Vec<Node>), String> {
    match doc.node(id) {
        Some(Node::Comment(c)) => {
            let op = edit::remove_comment(doc, id).ok_or("not found")?;
            Ok((op, vec![Node::Comment(c.clone())]))
        }
        Some(Node::Element(_)) => edit::remove(doc, id)
            .ok_or_else(|| "the root cannot be taken out".to_string()),
        _ => Err("not found".to_string()),
    }
}

/// Takes rows out of a copy of a document, the last first so that each
/// op is made for the document as the ops before it left it.
///
/// # Arguments
///
/// * `doc`   - The copy, changed.
/// * `roots` - The rows, in document order.
///
/// # Returns
///
/// The ops, in the order made, and what they took out, in document order.
///
/// # Errors
///
/// Says why a row cannot be taken out.
fn take_all(
    doc: &mut Document,
    roots: &[Id],
) -> Result<(Vec<Op>, Vec<Node>), String> {
    let mut ops = Vec::new();
    let mut taken: Vec<Node> = Vec::new();
    for id in roots.iter().rev() {
        // A comment that went with the element after it is gone already.
        if doc.node(*id).is_none() {
            continue;
        }
        let (op, nodes) = take_out(doc, *id)?;
        op.apply(doc)?;
        ops.push(op);
        taken.splice(0..0, nodes);
    }
    Ok((ops, taken))
}

/// Makes the change that takes rows out, as one step.
///
/// # Arguments
///
/// * `s`     - The session.
/// * `d`     - The document's index.
/// * `roots` - The rows, as [`roots`] gives them.
///
/// # Returns
///
/// The change.
///
/// # Errors
///
/// Says why, if a row is the root or not in the document.
pub fn delete_changes(
    s: &Session,
    d: usize,
    roots: &[Id],
) -> Result<Vec<Change>, String> {
    if roots.is_empty() {
        return Err("nothing is selected".into());
    }
    let mut doc = s.docs[d].doc.clone();
    let (ops, _) = take_all(&mut doc, roots)?;
    Ok(vec![Change { doc: d, ops }])
}

/// The run of sibling elements some rows are, if they are one: elements
/// next to each other under one parent, and comments that go with them.
///
/// # Arguments
///
/// * `doc`   - The document.
/// * `roots` - The rows, as [`roots`] gives them.
///
/// # Returns
///
/// The parent, the place of the first element among its elements, and how
/// many there are; `None` if the rows are not such a run.
pub fn block(doc: &Document, roots: &[Id]) -> Option<(Id, usize, usize)> {
    let els: Vec<Id> = roots
        .iter()
        .copied()
        .filter(|id| doc.element(*id).is_some())
        .collect();
    let first = *els.first()?;
    let (parent, _) = doc.parent_of(first)?;
    if parent == DOCUMENT {
        return None;
    }
    let p = doc.element(parent)?;
    let all: Vec<Id> = p.elements().map(|e| e.id).collect();
    let at = all.iter().position(|id| *id == first)?;
    if all.get(at..at + els.len())? != els.as_slice() {
        return None;
    }
    // A comment must be one an element of the run takes with it.
    let with: HashSet<Id> = els
        .iter()
        .flat_map(|id| span(doc, *id))
        .filter_map(|n| n.id())
        .collect();
    if roots.iter().any(|id| !with.contains(id)) {
        return None;
    }
    Some((parent, at, els.len()))
}

/// Makes the change that moves a run of sibling elements up past the
/// element before it, or down past the one after it.
///
/// # Arguments
///
/// * `s`     - The session.
/// * `d`     - The document's index.
/// * `roots` - The rows, as [`roots`] gives them.
/// * `up`    - True to move them up.
///
/// # Returns
///
/// The change.
///
/// # Errors
///
/// Says why, if the rows are not one run or are at the end already.
pub fn shift_changes(
    s: &Session,
    d: usize,
    roots: &[Id],
    up: bool,
) -> Result<Vec<Change>, String> {
    let doc = &s.docs[d].doc;
    let (parent, at, n) = block(doc, roots).ok_or(
        "only elements next to each other under one element move up and \
         down together",
    )?;
    let p = doc.element(parent).ok_or("not found")?;
    let count = p.elements().count();
    // The element on the other side moves round the run instead, which
    // leaves the run's own ids, and its selection, as they were.
    let (other, to) = if up {
        let i = at.checked_sub(1).ok_or("it is first already")?;
        (i, i + n)
    } else if at + n < count {
        (at + n, at)
    } else {
        return Err("it is last already".into());
    };
    let id = p.elements().nth(other).map(|e| e.id).ok_or("not found")?;
    let (out, taken) = edit::remove(doc, id).ok_or("not found")?;
    let mut after = doc.clone();
    out.apply(&mut after)?;
    let back = edit::insert(&after, parent, to, edit::unlaid(taken))
        .ok_or("not found")?;
    Ok(vec![Change {
        doc: d,
        ops: vec![out, back],
    }])
}

/// The namespace declared for a prefix where an element is.
///
/// # Arguments
///
/// * `doc`    - The document.
/// * `id`     - The element.
/// * `prefix` - The prefix, `""` for the default namespace.
///
/// # Returns
///
/// The namespace, or `None` if none is declared.
fn declared(doc: &Document, id: Id, prefix: &str) -> Option<String> {
    let wanted = xmlns_name(prefix);
    doc.ancestry(id)
        .iter()
        .rev()
        .find_map(|at| doc.element(*at).and_then(|e| e.get(&wanted)))
}

/// The attribute that declares a prefix.
///
/// # Arguments
///
/// * `prefix` - The prefix, `""` for the default namespace.
///
/// # Returns
///
/// `xmlns` or `xmlns:prefix`.
fn xmlns_name(prefix: &str) -> String {
    if prefix.is_empty() {
        "xmlns".to_string()
    } else {
        format!("xmlns:{prefix}")
    }
}

/// The prefix an `xmlns` attribute declares.
///
/// # Arguments
///
/// * `name` - The attribute's name.
///
/// # Returns
///
/// The prefix, `""` for the default namespace, or `None` if the attribute
/// declares none.
fn declares(name: &str) -> Option<&str> {
    if name == "xmlns" {
        Some("")
    } else {
        name.strip_prefix("xmlns:")
    }
}

/// The prefixes used in an element and everything in it that are not
/// declared inside it.
///
/// # Arguments
///
/// * `e`     - The element.
/// * `inner` - The prefixes declared around it, inside what is copied.
/// * `out`   - The prefixes found, added to.
fn undeclared(e: &Element, inner: &HashSet<String>, out: &mut Vec<String>) {
    let mut here = inner.clone();
    for a in &e.attrs {
        if let Some(p) = declares(&a.name) {
            here.insert(p.to_string());
        }
    }
    let mut used = vec![e.prefix().unwrap_or("").to_string()];
    for a in &e.attrs {
        if declares(&a.name).is_none() {
            if let Some((p, _)) = a.name.split_once(':') {
                if p != "xml" {
                    used.push(p.to_string());
                }
            }
        }
    }
    for p in used {
        if !here.contains(&p) && !out.contains(&p) {
            out.push(p);
        }
    }
    for k in e.elements() {
        undeclared(k, &here, out);
    }
}

/// Writes rows out as XTCE text, each element declaring the namespaces it
/// uses that are declared outside it.
///
/// # Arguments
///
/// * `doc`   - The document.
/// * `roots` - The rows, as [`roots`] gives them.
///
/// # Returns
///
/// The text, a row's nodes to a line.
pub fn copy_text(doc: &Document, roots: &[Id]) -> String {
    let mut parts = Vec::new();
    for n in spans(doc, roots) {
        let mut n = n;
        if let Node::Element(e) = &mut n {
            let mut need = Vec::new();
            undeclared(e, &HashSet::new(), &mut need);
            let decls: Vec<Attr> = need
                .iter()
                .filter_map(|p| {
                    declared(doc, e.id, p)
                        .map(|ns| Attr::new(&xmlns_name(p), &ns))
                })
                .collect();
            e.attrs.splice(0..0, decls);
        }
        let mut out = String::new();
        crate::tree::write_node(&n, &mut out);
        parts.push(out);
    }
    let mut text = parts.join("\n");
    text.push('\n');
    text
}

/// Reads XTCE text, as [`copy_text`] writes it or as it is cut from a
/// file, into nodes that can be put in a document.
///
/// # Arguments
///
/// * `text` - The text.
///
/// # Returns
///
/// The elements and comments, with ids of their own, as written.
///
/// # Errors
///
/// Says why, if the text is not elements and comments.
fn read_fragment(text: &str) -> Result<Vec<Node>, String> {
    let trimmed = text.trim_start_matches('\u{feff}').trim();
    if trimmed.is_empty() {
        return Err("the clipboard is empty".into());
    }
    // A whole document is its root element.
    let nodes = if trimmed.starts_with("<?xml") {
        let doc = Document::parse(trimmed)
            .map_err(|e| format!("the clipboard is not XML: {e}"))?;
        vec![Node::Element(doc.root().clone())]
    } else {
        let wrapped = format!("<tcs-xtcee-clip>{trimmed}</tcs-xtcee-clip>");
        let doc = Document::parse(&wrapped).map_err(|e| {
            format!("the clipboard is not XML elements: {}", e.message)
        })?;
        doc.root().children.clone()
    };
    let mut out = Vec::new();
    for n in nodes {
        match n {
            Node::Element(_) | Node::Comment(_) => out.push(n),
            ref t if t.is_space() => {}
            _ => {
                return Err("the clipboard holds text that is not in an \
                            element"
                    .into())
            }
        }
    }
    if out.is_empty() {
        return Err("the clipboard holds no elements or comments".into());
    }
    Ok(edit::unlaid(out))
}

/// The first element of XTCE text and the namespace it declares, so that
/// whether it may be pasted somewhere can be asked without reading it into
/// a document.
///
/// # Arguments
///
/// * `text` - The text.
///
/// # Returns
///
/// The first element's local name and namespace (`None` if it declares
/// none), or `None` if the text holds only comments.
///
/// # Errors
///
/// Says why, if the text is not elements and comments.
pub fn head(text: &str) -> Result<Option<(String, Option<String>)>, String> {
    let nodes = read_fragment(text)?;
    Ok(nodes.iter().find_map(|n| match n {
        Node::Element(e) => {
            let p = e.prefix().unwrap_or("");
            let ns = e.get(&xmlns_name(p));
            Some((e.local().to_string(), ns))
        }
        _ => None,
    }))
}

/// Puts an element and everything in it in a document's namespace prefix.
///
/// # Arguments
///
/// * `e`      - The element, changed.
/// * `scope`  - The namespaces declared around it in the text.
/// * `ns`     - The document's XTCE namespace.
/// * `prefix` - Its prefix where the element goes, `""` for the default.
///
/// # Errors
///
/// Says why, for an element of another XTCE version.
fn retarget(
    e: &mut Element,
    scope: &HashMap<String, String>,
    ns: &str,
    prefix: &str,
) -> Result<(), String> {
    let mut here = scope.clone();
    for a in &e.attrs {
        if let Some(p) = declares(&a.name) {
            here.insert(p.to_string(), a.value());
        }
    }
    let p = e.prefix().unwrap_or("").to_string();
    match here.get(&p) {
        Some(u) if u == ns => {}
        Some(u) => {
            if let Some(v) = Version::ALL.iter().find(|v| v.namespace() == u) {
                return Err(format!(
                    "the rows are XTCE {v}; convert them to this document's \
                     version first"
                ));
            }
            // An element of another namespace stays as it is, for the
            // schema to judge.
            for c in &mut e.children {
                if let Node::Element(k) = c {
                    retarget(k, &here, ns, prefix)?;
                }
            }
            return Ok(());
        }
        // Text with no declaration is taken to be in the document's
        // namespace.
        None => {}
    }
    let local = e.local().to_string();
    e.name = if prefix.is_empty() {
        local
    } else {
        format!("{prefix}:{local}")
    };
    for c in &mut e.children {
        if let Node::Element(k) = c {
            retarget(k, &here, ns, prefix)?;
        }
    }
    Ok(())
}

/// Whether an element, or anything in it, uses a prefix.
///
/// # Arguments
///
/// * `e` - The element.
/// * `p` - The prefix, `""` for the default namespace.
///
/// # Returns
///
/// True if an element's or an attribute's name has it.
fn uses(e: &Element, p: &str) -> bool {
    e.prefix().unwrap_or("") == p
        || (!p.is_empty()
            && e.attrs.iter().any(|a| {
                declares(&a.name).is_none()
                    && a.name.split_once(':').is_some_and(|(x, _)| x == p)
            }))
        || e.elements().any(|k| uses(k, p))
}

/// Reads XTCE text into nodes for a place in a document: in the
/// document's namespace prefix, with the declarations the place already
/// has, or no longer needs, taken out.
///
/// # Arguments
///
/// * `text`   - The text.
/// * `doc`    - The document.
/// * `parent` - The element the nodes go in.
/// * `ns`     - The document's XTCE namespace.
///
/// # Returns
///
/// The nodes, with ids of their own, which must be numbered for the
/// document before they go in.
///
/// # Errors
///
/// Says why, if the text is not elements and comments or is of another
/// XTCE version.
pub fn fragment(
    text: &str,
    doc: &Document,
    parent: Id,
    ns: &str,
) -> Result<Vec<Node>, String> {
    let prefix = doc
        .prefix_for(parent, ns)
        .ok_or("the place is not in the document's XTCE namespace")?;
    let mut nodes = read_fragment(text)?;
    for n in &mut nodes {
        let Node::Element(e) = n else {
            continue;
        };
        retarget(e, &HashMap::new(), ns, &prefix)?;
        let mut keep = Vec::new();
        for a in std::mem::take(&mut e.attrs) {
            let gone = declares(&a.name).is_some_and(|p| {
                !uses(e, p)
                    || declared(doc, parent, p).as_deref()
                        == Some(a.value().as_str())
            });
            if !gone {
                keep.push(a);
            }
        }
        e.attrs = keep;
    }
    Ok(nodes)
}

/// The parent and the place among its elements that a place beside or in
/// a row is.
///
/// # Arguments
///
/// * `doc`   - The document.
/// * `id`    - The row's element or comment.
/// * `place` - Where, beside or in it.
///
/// # Returns
///
/// The parent and the place: 0 before its first element.  `None` for a
/// place beside the root or in a comment.
pub fn place_of(doc: &Document, id: Id, place: Place) -> Option<(Id, usize)> {
    if place == Place::Into {
        let e = doc.element(id)?;
        return Some((id, e.elements().count()));
    }
    let (parent, i) = doc.parent_of(id)?;
    if parent == DOCUMENT {
        return None;
    }
    let kids = doc.children(parent)?;
    let before = kids[..i]
        .iter()
        .filter(|n| matches!(n, Node::Element(_)))
        .count();
    let at = match (place, &kids[i]) {
        (Place::After, Node::Element(_)) => before + 1,
        _ => before,
    };
    Some((parent, at))
}

/// Whether the first element of some nodes may go at a place, as Insert
/// asks it.
///
/// # Arguments
///
/// * `doc`    - The document.
/// * `report` - What checking it found.
/// * `parent` - The element they go in.
/// * `at`     - Where among its elements.
/// * `local`  - The first element's local name, or `None` for comments
///              alone, which may go anywhere.
///
/// # Errors
///
/// Says why not.
pub fn fits(
    doc: &Document,
    report: &Report,
    parent: Id,
    at: usize,
    local: Option<&str>,
) -> Result<(), String> {
    let Some(local) = local else {
        return Ok(());
    };
    let version = report.version.ok_or("the document has no version")?;
    let schema = version.schema();
    let ok = insertable(doc, report, parent, at)
        .iter()
        .any(|dc| schema.elements[*dc].name == local);
    if ok {
        Ok(())
    } else {
        let p = doc.element(parent).map_or("", |e| e.local());
        Err(format!(
            "XTCE {version} does not allow {local} there, in {p}"
        ))
    }
}

/// The local name of the first element among some nodes.
///
/// # Arguments
///
/// * `nodes` - The nodes.
///
/// # Returns
///
/// The name, or `None` if there is no element.
pub fn first_local(nodes: &[Node]) -> Option<String> {
    nodes.iter().find_map(|n| match n {
        Node::Element(e) => Some(e.local().to_string()),
        _ => None,
    })
}

/// What the references in rows name, noted as they are copied so that a
/// paste of the copy can keep them naming it.
///
/// # Arguments
///
/// * `s`     - The session.
/// * `d`     - The document's index.
/// * `roots` - The rows, as [`roots`] gives them.
///
/// # Returns
///
/// A note for each reference in the rows that names something.
pub fn copy_notes(s: &Session, d: usize, roots: &[Id]) -> Vec<RefNote> {
    let ids = all_ids(&spans(&s.docs[d].doc, roots));
    let place: HashMap<Id, usize> =
        ids.iter().enumerate().map(|(i, id)| (*id, i)).collect();
    let ix = &s.index;
    ix.refs
        .iter()
        .filter(|r| r.loc.doc == d)
        .filter_map(|r| {
            let at = *place.get(&r.loc.id)?;
            let def = &ix.defs[r.target?];
            let named = match place.get(&def.loc.id) {
                Some(i) if def.loc.doc == d => Named::Inside(*i),
                _ => Named::Outside(def.kind, def.key.clone()),
            };
            Some(RefNote {
                at,
                attr: r.attr.clone(),
                named,
            })
        })
        .collect()
}

/// Makes the change that puts copies of rows in a document, writing again
/// each reference in them that would name something else, or nothing,
/// where they go: one that named a definition copied with it names the
/// copy, and one that named a definition outside the copy names that.
/// References elsewhere are left as they are.
///
/// # Arguments
///
/// * `s`      - The session.
/// * `d`      - The document's index.
/// * `parent` - The element they go in.
/// * `at`     - Where among its elements.
/// * `nodes`  - What to put in, as [`fragment`] gives it.
/// * `notes`  - What the references in it named when it was copied, as
///              [`copy_notes`] gives it; empty for text from elsewhere.
///
/// # Returns
///
/// The change, the ids given to what goes in, and how many references
/// were written again.
///
/// # Errors
///
/// Says why, if the first element may not go there.
pub fn paste_changes(
    s: &Session,
    d: usize,
    parent: Id,
    at: usize,
    nodes: Vec<Node>,
    notes: &[RefNote],
) -> Result<Pasted, String> {
    let o = &s.docs[d];
    fits(
        &o.doc,
        &o.report,
        parent,
        at,
        first_local(&nodes).as_deref(),
    )?;
    let mut nodes = nodes;
    let mut next = o.doc.last_id;
    for n in &mut nodes {
        n.renumber(&mut next);
    }
    let ids = nodes.iter().filter_map(Node::id).collect();
    let every = all_ids(&nodes);
    let op = edit::insert(&o.doc, parent, at, nodes).ok_or("not found")?;
    let mut doc = o.doc.clone();
    op.apply(&mut doc)?;
    let mut ops = vec![op];
    // The references in the copy, as they resolve where it is.
    let mut edits: Vec<(Id, Option<String>, String)> = Vec::new();
    let mut unkept = 0;
    {
        let docs: Vec<&Document> = (0..s.docs.len())
            .map(|i| if i == d { &doc } else { &s.docs[i].doc })
            .collect();
        let after = Index::of(&docs);
        for n in notes {
            let Some(id) = every.get(n.at).copied() else {
                continue;
            };
            let loc = Loc { doc: d, id };
            let Some(r) =
                after.refs.iter().find(|r| r.loc == loc && r.attr == n.attr)
            else {
                continue;
            };
            let def = match &n.named {
                Named::Inside(i) => every
                    .get(*i)
                    .and_then(|x| after.def_at(Loc { doc: d, id: *x })),
                Named::Outside(kind, key) => {
                    after.resolve(&format!("/{}", key.join("/")), &[], *kind)
                }
            };
            let Some(def) = def else {
                unkept += 1;
                continue;
            };
            if r.target == Some(def) {
                continue;
            }
            match rewrite(&after, r, def) {
                Some(t) => edits.push((id, n.attr.clone(), t)),
                None => unkept += 1,
            }
        }
    }
    let rewritten = edits.len();
    for (id, attr, text) in edits {
        let op = match &attr {
            Some(a) => edit::set_attr(&doc, id, a, &text),
            None => edit::set_text(&doc, id, &text),
        };
        if let Some(op) = op {
            op.apply(&mut doc)?;
            ops.push(op);
        }
    }
    Ok(Pasted {
        changes: vec![Change { doc: d, ops }],
        ids,
        last: next,
        rewritten,
        unkept,
    })
}

/// The references a session's definitions have, by where the reference
/// is and where what it names is.
///
/// # Arguments
///
/// * `ix` - The index.
///
/// # Returns
///
/// Each resolved reference's place and attribute, and its definition's
/// place.
fn resolved(ix: &Index) -> Vec<(Loc, Option<String>, Loc)> {
    ix.refs
        .iter()
        .filter_map(|r| {
            let t = r.target?;
            Some((r.loc, r.attr.clone(), ix.defs[t].loc))
        })
        .collect()
}

/// The reference written again to name a definition from where it is: by
/// its name alone if that finds it, or else by its path from the root, an
/// absolute reference staying absolute; any member part is kept.
///
/// # Arguments
///
/// * `ix`  - The index after the move.
/// * `r`   - The reference.
/// * `def` - The definition it is to name, by its index in `ix.defs`.
///
/// # Returns
///
/// The text, or `None` if no way of writing it names the definition.
fn rewrite(ix: &Index, r: &Ref, def: usize) -> Option<String> {
    let base = base_of(&r.text, r.kind);
    let rest = &r.text[base.len()..];
    let key = &ix.defs[def].key;
    let abs = format!("/{}", key.join("/"));
    let mut tries = Vec::new();
    if !r.text.trim_start().starts_with('/') {
        tries.push(key.last()?.clone());
    }
    tries.push(abs);
    tries
        .into_iter()
        .map(|t| format!("{t}{rest}"))
        .find(|t| ix.resolve(t, &r.space, r.kind) == Some(def))
}

/// Makes the change that moves rows to a place in the same document or
/// another, as one step, writing again every reference the move would
/// otherwise change the meaning of.
///
/// # Arguments
///
/// * `s`      - The session.
/// * `from`   - The index of the document the rows are in.
/// * `roots`  - The rows, as [`roots`] gives them.
/// * `to`     - The index of the document they go to.
/// * `parent` - The element they go in.
/// * `at`     - Where among its elements, as it is before the move.
///
/// # Returns
///
/// The move.
///
/// # Errors
///
/// Says why, if the rows would go in themselves, are the root, or are of
/// another XTCE version than the document they go to.
pub fn move_changes(
    s: &Session,
    from: usize,
    roots: &[Id],
    to: usize,
    parent: Id,
    at: usize,
) -> Result<Moved, String> {
    if roots.is_empty() {
        return Err("nothing is selected".into());
    }
    let src = &s.docs[from];
    let dst = &s.docs[to];
    let version = dst.report.version.ok_or("the document has no version")?;
    if src.report.version != Some(version) {
        return Err(format!(
            "the rows are not XTCE {version}; convert them to this \
             document's version first"
        ));
    }
    let moving: HashSet<Id> = roots.iter().copied().collect();
    if from == to {
        let chain = src.doc.ancestry(parent);
        if chain.iter().any(|a| moving.contains(a)) {
            return Err("rows cannot be moved into themselves".into());
        }
    }
    // Where they go is named by the element they go before, which the
    // move leaves where it is.
    let anchor = dst
        .doc
        .element(parent)
        .ok_or("not found")?
        .elements()
        .skip(at)
        .map(|e| e.id)
        .find(|id| from != to || !moving.contains(id));
    let mut scratch: HashMap<usize, Document> = HashMap::new();
    let mut ops: HashMap<usize, Vec<Op>> = HashMap::new();
    let mut a = src.doc.clone();
    let (out, taken) = take_all(&mut a, roots)?;
    ops.insert(from, out);
    scratch.insert(from, a);
    let old_ids = all_ids(&edit::unlaid(taken.clone()));
    let mut nodes = edit::unlaid(taken);
    let mut map: HashMap<Id, Id> = HashMap::new();
    if from != to {
        // Into another document by way of the text a copy is, which puts
        // the rows in its namespace prefix; their ids are its.
        let text = copy_text(&src.doc, roots);
        nodes = fragment(&text, &dst.doc, parent, version.namespace())?;
        let mut next = dst.doc.last_id;
        for n in &mut nodes {
            n.renumber(&mut next);
        }
        let new_ids = all_ids(&nodes);
        if new_ids.len() != old_ids.len() {
            return Err("the rows could not be copied whole".into());
        }
        map = old_ids.iter().copied().zip(new_ids).collect();
        scratch.insert(to, dst.doc.clone());
    } else {
        map.extend(old_ids.iter().map(|id| (*id, *id)));
    }
    let ids: Vec<Id> = nodes.iter().filter_map(Node::id).collect();
    let b = scratch.get_mut(&to).expect("made above");
    let p = b.element(parent).ok_or("not found")?;
    let at = match anchor {
        Some(x) => p.elements().position(|e| e.id == x).unwrap_or(0),
        None => p.elements().count(),
    };
    let put = edit::insert(b, parent, at, nodes).ok_or("not found")?;
    put.apply(b)?;
    let last = map.values().copied().max().unwrap_or(0).max(b.last_id);
    b.last_id = last;
    ops.entry(to).or_default().push(put);
    // The references, as they resolve after the move.
    let moved = |l: Loc| -> Loc {
        match map.get(&l.id) {
            Some(n) if l.doc == from => Loc { doc: to, id: *n },
            _ => l,
        }
    };
    let mut edits: Vec<(Loc, Option<String>, String)> = Vec::new();
    let mut unkept = 0;
    {
        let docs: Vec<&Document> = (0..s.docs.len())
            .map(|i| scratch.get(&i).unwrap_or(&s.docs[i].doc))
            .collect();
        let after = Index::of(&docs);
        for (rl, attr, dl) in resolved(&s.index) {
            let (rl, dl) = (moved(rl), moved(dl));
            let Some(r) =
                after.refs.iter().find(|r| r.loc == rl && r.attr == attr)
            else {
                continue;
            };
            let Some(def) = after.def_at(dl) else {
                continue;
            };
            if r.target == Some(def) {
                continue;
            }
            match rewrite(&after, r, def) {
                Some(t) => edits.push((rl, attr, t)),
                None => unkept += 1,
            }
        }
    }
    let rewritten = edits.len();
    for (loc, attr, text) in edits {
        let d = scratch
            .entry(loc.doc)
            .or_insert_with(|| s.docs[loc.doc].doc.clone());
        let op = match &attr {
            Some(a) => edit::set_attr(d, loc.id, a, &text),
            None => edit::set_text(d, loc.id, &text),
        };
        if let Some(op) = op {
            op.apply(d)?;
            ops.entry(loc.doc).or_default().push(op);
        }
    }
    let mut changes: Vec<Change> = ops
        .into_iter()
        .map(|(doc, ops)| Change { doc, ops })
        .collect();
    changes.sort_by_key(|c| c.doc);
    Ok(Moved {
        changes,
        ids,
        last,
        rewritten,
        unkept,
    })
}

/// The references outside some rows that name a definition among them,
/// which taking the rows out would leave naming nothing.
///
/// # Arguments
///
/// * `s`     - The session.
/// * `d`     - The document's index.
/// * `roots` - The rows, as [`roots`] gives them.
///
/// # Returns
///
/// Each such reference's place, with the name of what it names.
pub fn outside_usages(
    s: &Session,
    d: usize,
    roots: &[Id],
) -> Vec<(String, Loc)> {
    let doc = &s.docs[d].doc;
    let inside = |l: Loc| {
        l.doc == d && doc.ancestry(l.id).iter().any(|a| roots.contains(a))
    };
    let mut out = Vec::new();
    for (i, def) in s.index.defs.iter().enumerate() {
        if !inside(def.loc) {
            continue;
        }
        let name = def.key.last().cloned().unwrap_or_default();
        for r in s.index.usages(i) {
            if !inside(r.loc) {
                out.push((name.clone(), r.loc));
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Opens shipped documents in a session, from copies in a directory of
    /// their own.
    ///
    /// # Arguments
    ///
    /// * `files` - The documents' paths from the top of the repository.
    ///
    /// # Returns
    ///
    /// The session, and the directory, which is removed when it is
    /// dropped.
    fn session(files: &[&str]) -> (Session, tempfile::TempDir) {
        let dir = tempfile::tempdir().unwrap();
        let mut s = Session::new();
        s.state_dir = Some(dir.path().join("state"));
        for f in files {
            let from = format!("{}/../{f}", env!("CARGO_MANIFEST_DIR"));
            let to = dir
                .path()
                .join(std::path::Path::new(f).file_name().unwrap());
            std::fs::copy(from, &to).unwrap();
            s.open(&to).unwrap();
        }
        (s, dir)
    }

    /// Finds an element by its local name and its name attribute.
    ///
    /// # Arguments
    ///
    /// * `doc`   - The document.
    /// * `local` - The element's local name.
    /// * `name`  - Its name attribute, or `None` for the first so named.
    ///
    /// # Returns
    ///
    /// Its id.
    fn find(doc: &Document, local: &str, name: Option<&str>) -> Id {
        /// Looks in an element and everything in it.
        ///
        /// # Arguments
        ///
        /// * `e`     - The element.
        /// * `local` - The local name wanted.
        /// * `name`  - The name attribute wanted, if any.
        ///
        /// # Returns
        ///
        /// The first element found.
        fn look(e: &Element, local: &str, name: Option<&str>) -> Option<Id> {
            if e.local() == local
                && name.is_none_or(|n| e.get("name").as_deref() == Some(n))
            {
                return Some(e.id);
            }
            e.elements().find_map(|k| look(k, local, name))
        }
        look(doc.root(), local, name)
            .unwrap_or_else(|| panic!("no {local} {name:?}"))
    }

    /// The names of a parent's elements, in order.
    ///
    /// # Arguments
    ///
    /// * `doc` - The document.
    /// * `id`  - The parent.
    ///
    /// # Returns
    ///
    /// Their name attributes.
    fn names(doc: &Document, id: Id) -> Vec<String> {
        doc.element(id)
            .unwrap()
            .elements()
            .map(|e| e.get("name").unwrap_or_default())
            .collect()
    }

    #[test]
    fn roots_leave_out_what_is_inside_another() {
        let (s, _dir) = session(&["examples/housekeeping/housekeeping.xtce"]);
        let doc = &s.docs[0].doc;
        let set = find(doc, "ParameterSet", None);
        let p = find(doc, "Parameter", Some("HeaterOn"));
        let t = find(doc, "ParameterTypeSet", None);
        assert_eq!(roots(doc, &[p, set, t]), vec![t, set]);
    }

    #[test]
    fn copies_to_text_and_pastes_it_in_another_prefix() {
        let (mut s, dir) =
            session(&["examples/housekeeping/housekeeping.xtce"]);
        // A document that names XTCE with the prefix xtce, where the
        // housekeeping document uses the default namespace.
        let path = dir.path().join("prefixed.xtce");
        std::fs::write(
            &path,
            "<?xml version=\"1.0\"?>\n<xtce:SpaceSystem \
             xmlns:xtce=\"http://www.omg.org/spec/XTCE/20250214\" \
             name=\"P\">\n  <xtce:TelemetryMetaData>\n    \
             <xtce:ParameterTypeSet>\n      \
             <xtce:BooleanParameterType name=\"HeaterOnType\"/>\n      \
             <xtce:IntegerParameterType name=\"LengthType\"/>\n    \
             </xtce:ParameterTypeSet>\n    <xtce:ParameterSet>\n      \
             <xtce:Parameter name=\"x\" \
             parameterTypeRef=\"LengthType\"/>\n    \
             </xtce:ParameterSet>\n  </xtce:TelemetryMetaData>\n\
             </xtce:SpaceSystem>\n",
        )
        .unwrap();
        s.open(&path).unwrap();
        let doc = &s.docs[0].doc;
        let a = find(doc, "Parameter", Some("PacketLength"));
        let b = find(doc, "Parameter", Some("HeaterOn"));
        let text = copy_text(doc, &[a, b]);
        // The comment inside a row goes with it, and the namespace the
        // rows are in is declared on each.
        assert!(text.contains("The bytes after PacketLength"), "{text}");
        assert_eq!(text.matches("<Parameter xmlns=\"").count(), 2, "{text}");
        let other = &s.docs[1].doc;
        let ns = Version::V1_3.namespace();
        let set = find(other, "ParameterSet", None);
        let nodes = fragment(&text, other, set, ns).unwrap();
        let Node::Element(e) = &nodes[0] else {
            panic!("{nodes:?}")
        };
        assert_eq!(e.name, "xtce:Parameter");
        assert!(e.attrs.iter().all(|a| declares(&a.name).is_none()));
        let p = paste_changes(&s, 1, set, 1, nodes, &[]).unwrap();
        s.apply(p.changes, "paste").unwrap();
        assert_eq!(
            names(&s.docs[1].doc, set),
            ["x", "PacketLength", "HeaterOn"]
        );
        assert!(s.problems(1).is_empty(), "{:?}", s.problems(1));
        let written = s.docs[1].doc.write();
        assert!(written.contains("<xtce:AncillaryData name"), "{written}");
        // Text from a file, with no declaration, is in the document's
        // namespace.
        let plain = "<Parameter name=\"y\" parameterTypeRef=\"LengthType\"/>";
        let nodes = fragment(plain, &s.docs[1].doc, set, ns).unwrap();
        let p = paste_changes(&s, 1, set, 0, nodes, &[]).unwrap();
        s.apply(p.changes, "paste").unwrap();
        assert!(s.docs[1].doc.write().contains("<xtce:Parameter name=\"y\""));
    }

    #[test]
    fn pastes_only_where_the_schema_allows() {
        let (s, _dir) = session(&["examples/housekeeping/housekeeping.xtce"]);
        let doc = &s.docs[0].doc;
        let p = find(doc, "Parameter", Some("HeaterOn"));
        let types = find(doc, "ParameterTypeSet", None);
        let text = copy_text(doc, &[p]);
        let nodes =
            fragment(&text, doc, types, Version::V1_3.namespace()).unwrap();
        let e = paste_changes(&s, 0, types, 0, nodes, &[]).unwrap_err();
        assert!(e.contains("does not allow Parameter"), "{e}");
        // Rows of another version are refused.
        let old =
            text.replace(Version::V1_3.namespace(), Version::V1_2.namespace());
        let set = find(doc, "ParameterSet", None);
        let e =
            fragment(&old, doc, set, Version::V1_3.namespace()).unwrap_err();
        assert!(e.contains("XTCE 1.2"), "{e}");
        assert!(fragment("just words", doc, set, "x").is_err());
    }

    #[test]
    fn deletes_several_rows_as_one_change() {
        let (mut s, _dir) =
            session(&["examples/housekeeping/housekeeping.xtce"]);
        let doc = &s.docs[0].doc;
        let set = find(doc, "ParameterSet", None);
        let a = find(doc, "Parameter", Some("PacketLength"));
        let b = find(doc, "Parameter", Some("BusVoltage"));
        let before = names(doc, set);
        let c = delete_changes(&s, 0, &roots(doc, &[a, b])).unwrap();
        s.apply(c, "delete").unwrap();
        assert_eq!(names(&s.docs[0].doc, set).len(), before.len() - 2);
        s.undo(0).unwrap();
        assert_eq!(names(&s.docs[0].doc, set), before);
        let root = s.docs[0].doc.root().id;
        assert!(delete_changes(&s, 0, &[root]).is_err());
    }

    #[test]
    fn moves_only_a_run_of_siblings_up_and_down() {
        let (mut s, _dir) =
            session(&["examples/housekeeping/housekeeping.xtce"]);
        let doc = &s.docs[0].doc;
        let set = find(doc, "ParameterSet", None);
        let a = find(doc, "Parameter", Some("Temperature"));
        let b = find(doc, "Parameter", Some("BusVoltage"));
        let far = find(doc, "Parameter", Some("PacketLength"));
        let t = find(doc, "FloatParameterType", Some("PowerType"));
        assert!(block(doc, &[a, b]).is_some());
        assert!(block(doc, &[far, b]).is_none(), "not next to each other");
        assert!(block(doc, &[t, b]).is_none(), "under two parents");
        let c = shift_changes(&s, 0, &[a, b], false).unwrap();
        s.apply(c, "move down").unwrap();
        assert_eq!(
            names(&s.docs[0].doc, set),
            [
                "PacketLength",
                "HeaterOn",
                "HeaterPower",
                "Temperature",
                "BusVoltage"
            ]
        );
        assert!(shift_changes(&s, 0, &[a, b], false).is_err());
    }

    #[test]
    fn a_move_into_another_document_keeps_references_whole() {
        let (mut s, _dir) = session(&[
            "examples/split-mission/dt.xtce",
            "examples/split-mission/pus.xtce",
            "examples/split-mission/rover.xtce",
        ]);
        let before: usize = (0..3).map(|d| s.problems(d).len()).sum();
        assert_eq!(before, 0);
        let t = find(&s.docs[0].doc, "IntegerParameterType", Some("uint16"));
        let used = s
            .index
            .usages(s.index.def_at(Loc { doc: 0, id: t }).unwrap());
        let n = used.len();
        assert!(n >= 2, "{n}");
        let types = find(&s.docs[2].doc, "ParameterTypeSet", None);
        let m = move_changes(&s, 0, &[t], 2, types, 0).unwrap();
        assert_eq!((m.rewritten, m.unkept), (n, 0));
        s.apply(m.changes, "move").unwrap();
        // Every reference still names the type, now in Rover.
        let after: usize = (0..3).map(|d| s.problems(d).len()).sum();
        assert_eq!(
            after,
            0,
            "{:?}",
            (0..3).map(|d| s.problems(d)).collect::<Vec<_>>()
        );
        let pus = s.docs[1].doc.write();
        assert!(pus.contains("\"/Rover/uint16\""), "{pus}");
        let rover = s.docs[2].doc.write();
        assert!(rover.contains("<IntegerParameterType name=\"uint16\""));
        assert!(!s.docs[0].doc.write().contains("name=\"uint16\""));
        // One step, undone in every document at once.
        s.undo(0).unwrap();
        assert!(s.docs[1].doc.write().contains("\"/dt/uint16\""));
        assert!(s.docs[0].doc.write().contains("name=\"uint16\""));
    }

    #[test]
    fn a_move_into_a_nested_space_system_keeps_references_whole() {
        let text = "<?xml version=\"1.0\"?>\n\
            <SpaceSystem xmlns=\"http://www.omg.org/spec/XTCE/20250214\" \
            name=\"S\">\n  <TelemetryMetaData>\n    <ParameterTypeSet>\n      \
            <BooleanParameterType name=\"b\"/>\n      \
            <BooleanParameterType name=\"d\"/>\n    </ParameterTypeSet>\n    \
            <ParameterSet>\n      <Parameter name=\"p\" \
            parameterTypeRef=\"b\"/>\n    </ParameterSet>\n  \
            </TelemetryMetaData>\n  <SpaceSystem name=\"In\">\n    \
            <TelemetryMetaData>\n      <ParameterTypeSet>\n        \
            <BooleanParameterType name=\"c\"/>\n      </ParameterTypeSet>\n    \
            </TelemetryMetaData>\n  </SpaceSystem>\n</SpaceSystem>\n";
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("nested.xtce");
        std::fs::write(&path, text).unwrap();
        let mut s = Session::new();
        s.state_dir = Some(dir.path().join("state"));
        s.open(&path).unwrap();
        assert!(s.problems(0).is_empty(), "{:?}", s.problems(0));
        let doc = &s.docs[0].doc;
        let b = find(doc, "BooleanParameterType", Some("b"));
        let c = find(doc, "BooleanParameterType", Some("c"));
        let (inner, _) = doc.parent_of(c).unwrap();
        let m = move_changes(&s, 0, &[b], 0, inner, 1).unwrap();
        assert_eq!((m.rewritten, m.unkept), (1, 0));
        s.apply(m.changes, "move").unwrap();
        assert!(s.problems(0).is_empty(), "{:?}", s.problems(0));
        assert!(s.docs[0]
            .doc
            .write()
            .contains("parameterTypeRef=\"/S/In/b\""));
        // Not into itself.
        let doc = &s.docs[0].doc;
        let space = find(doc, "SpaceSystem", Some("In"));
        let e = move_changes(&s, 0, &[space], 0, space, 0).unwrap_err();
        assert!(e.contains("themselves"), "{e}");
    }

    #[test]
    fn a_copy_keeps_its_references_naming_what_they_named() {
        let ns = Version::V1_3.namespace();
        let a = format!(
            "<?xml version=\"1.0\"?>\n<SpaceSystem xmlns=\"{ns}\" \
             name=\"S\">\n  <TelemetryMetaData>\n    \
             <ParameterTypeSet>\n      <BooleanParameterType \
             name=\"d\"/>\n    </ParameterTypeSet>\n  \
             </TelemetryMetaData>\n  <SpaceSystem name=\"In\">\n    \
             <TelemetryMetaData>\n      <ParameterTypeSet>\n        \
             <BooleanParameterType name=\"c\"/>\n      \
             </ParameterTypeSet>\n      <ParameterSet>\n        \
             <Parameter name=\"q\" parameterTypeRef=\"/S/In/c\"/>\n        \
             <Parameter name=\"r\" parameterTypeRef=\"d\"/>\n      \
             </ParameterSet>\n    </TelemetryMetaData>\n  \
             </SpaceSystem>\n</SpaceSystem>\n"
        );
        let b = format!(
            "<?xml version=\"1.0\"?>\n<SpaceSystem xmlns=\"{ns}\" \
             name=\"T\">\n  <TelemetryMetaData/>\n</SpaceSystem>\n"
        );
        let dir = tempfile::tempdir().unwrap();
        let mut s = Session::new();
        s.state_dir = Some(dir.path().join("state"));
        for (name, text) in [("a.xtce", &a), ("b.xtce", &b)] {
            let path = dir.path().join(name);
            std::fs::write(&path, text).unwrap();
            s.open(&path).unwrap();
        }
        assert!(s.problems(0).is_empty(), "{:?}", s.problems(0));
        let inner = find(&s.docs[0].doc, "SpaceSystem", Some("In"));
        let notes = copy_notes(&s, 0, &[inner]);
        assert_eq!(notes.len(), 2, "{notes:?}");
        let text = copy_text(&s.docs[0].doc, &[inner]);
        let root = s.docs[1].doc.root().id;
        let nodes = fragment(&text, &s.docs[1].doc, root, ns).unwrap();
        let p = paste_changes(&s, 1, root, 1, nodes, &notes).unwrap();
        assert_eq!((p.rewritten, p.unkept), (2, 0));
        s.apply(p.changes, "paste").unwrap();
        // The type copied with it names the copy; the one outside names
        // the original, by its path.
        let b = s.docs[1].doc.write();
        assert!(b.contains("parameterTypeRef=\"/T/In/c\""), "{b}");
        assert!(b.contains("parameterTypeRef=\"/S/d\""), "{b}");
        assert!(s.problems(1).is_empty(), "{:?}", s.problems(1));
        // The original is as it was.
        assert_eq!(s.docs[0].doc.write(), a);
    }

    #[test]
    fn finds_the_references_a_delete_would_leave_naming_nothing() {
        let (s, _dir) = session(&["examples/housekeeping/housekeeping.xtce"]);
        let doc = &s.docs[0].doc;
        let t = find(doc, "FloatParameterType", Some("PowerType"));
        let u = outside_usages(&s, 0, &[t]);
        assert_eq!(u.len(), 1);
        assert_eq!(u[0].0, "PowerType");
        // A reference among what is taken out does not count.
        let types = find(doc, "ParameterTypeSet", None);
        let set = find(doc, "ParameterSet", None);
        let all = outside_usages(&s, 0, &[types, set]);
        assert!(all.iter().all(|(n, _)| n != "PowerType"), "{all:?}");
    }
}
