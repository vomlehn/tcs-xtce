//! Changes to a document, each carrying what is needed to undo it.
//!
//! Every change the editor makes to a document is one of three [`Op`]s:
//! replacing some of an element's children, setting an attribute, or
//! renaming an element.  Each holds both what was there and what is put
//! there, so its inverse is the same change the other way round, read off
//! rather than worked out again from the document as it is by then.
//!
//! The functions below make the ops for the edits a person makes --
//! putting an element in, taking one out, moving one -- and decide how the
//! file around the edit is laid out: a new element is indented as its
//! siblings are, and the comments written just before an element belong to
//! it, so they go where it goes and are taken out with it.

use crate::tree::{is_xml_space, Attr, Comment, Document, Element, Id, Node};

/// One change to a document.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Op {
    /// Replaces some of an element's children, or of the document's
    /// top-level nodes.
    Splice {
        /// The element whose children change, or
        /// [`crate::tree::DOCUMENT`].
        parent: Id,
        /// The index among the children where the change is.
        at: usize,
        /// The children that were there.
        removed: Vec<Node>,
        /// The children put there.
        inserted: Vec<Node>,
        /// Whether the parent was written `<a/>` before, and after.
        empty: (bool, bool),
    },
    /// Adds, changes or takes out an attribute.
    Attr {
        /// The element.
        el: Id,
        /// The attribute's index among the element's attributes.
        index: usize,
        /// The attribute as it was, or `None` if it was not there.
        old: Option<Attr>,
        /// The attribute as it is made, or `None` to take it out.
        new: Option<Attr>,
    },
    /// Gives an element another name, in its start and end tags.
    Rename {
        /// The element.
        el: Id,
        /// Its name as it was, with its prefix.
        old: String,
        /// Its new name, with its prefix.
        new: String,
    },
}

impl Op {
    /// The change that undoes this one.
    ///
    /// # Returns
    ///
    /// The inverse, which applied after this one leaves the document as it
    /// was.
    pub fn inverse(&self) -> Op {
        match self.clone() {
            Op::Splice {
                parent,
                at,
                removed,
                inserted,
                empty,
            } => Op::Splice {
                parent,
                at,
                removed: inserted,
                inserted: removed,
                empty: (empty.1, empty.0),
            },
            Op::Attr {
                el,
                index,
                old,
                new,
            } => Op::Attr {
                el,
                index,
                old: new,
                new: old,
            },
            Op::Rename { el, old, new } => Op::Rename {
                el,
                old: new,
                new: old,
            },
        }
    }

    /// Makes this change to a document.
    ///
    /// # Arguments
    ///
    /// * `doc` - The document.
    ///
    /// # Errors
    ///
    /// Fails, changing nothing, if the document is not as the change
    /// expects it to be, which means the change was made for another
    /// state of it: a mistake in the editor, which is reported rather
    /// than made worse.
    pub fn apply(&self, doc: &mut Document) -> Result<(), String> {
        match self {
            Op::Splice {
                parent,
                at,
                removed,
                inserted,
                empty,
            } => {
                let is_empty = if *parent == crate::tree::DOCUMENT {
                    false
                } else {
                    doc.element(*parent).ok_or("no such parent")?.empty
                };
                if is_empty != empty.0 {
                    return Err("the parent is not closed as expected".into());
                }
                let kids = doc.children_mut(*parent).ok_or("no such parent")?;
                let end = at + removed.len();
                if end > kids.len() || kids[*at..end] != removed[..] {
                    return Err("the children are not as expected".into());
                }
                kids.splice(*at..end, inserted.iter().cloned());
                if *parent != crate::tree::DOCUMENT {
                    let p = doc.element_mut(*parent).expect("found above");
                    p.empty = empty.1;
                    if p.empty && !p.children.is_empty() {
                        return Err(
                            "an element closed as empty has children".into()
                        );
                    }
                }
                Ok(())
            }
            Op::Attr {
                el,
                index,
                old,
                new,
            } => {
                let e = doc.element_mut(*el).ok_or("no such element")?;
                match (old, new) {
                    (Some(o), n) => {
                        if e.attrs.get(*index) != Some(o) {
                            return Err(
                                "the attribute is not as expected".into()
                            );
                        }
                        match n {
                            Some(n) => e.attrs[*index] = n.clone(),
                            None => {
                                e.attrs.remove(*index);
                            }
                        }
                    }
                    (None, Some(n)) => {
                        if *index > e.attrs.len() {
                            return Err("no such attribute place".into());
                        }
                        e.attrs.insert(*index, n.clone());
                    }
                    (None, None) => {}
                }
                Ok(())
            }
            Op::Rename { el, old, new } => {
                let e = doc.element_mut(*el).ok_or("no such element")?;
                if &e.name != old {
                    return Err("the element is not named as expected".into());
                }
                e.name = new.clone();
                Ok(())
            }
        }
    }
}

/// Makes the op that sets an attribute, keeping how it was written if it
/// was there, and writing it as the element's other attributes are if not.
///
/// # Arguments
///
/// * `doc`   - The document.
/// * `el`    - The element.
/// * `name`  - The attribute's name.
/// * `value` - Its new value.
///
/// # Returns
///
/// The op, or `None` if the element is not in the document or the
/// attribute already has that value.
pub fn set_attr(doc: &Document, el: Id, name: &str, value: &str) -> Option<Op> {
    let e = doc.element(el)?;
    if let Some(i) = e.attrs.iter().position(|a| a.name == name) {
        let old = e.attrs[i].clone();
        if old.value() == value {
            return None;
        }
        let new = old.with_value(value);
        return Some(Op::Attr {
            el,
            index: i,
            old: Some(old),
            new: Some(new),
        });
    }
    let mut new = Attr::new(name, value);
    // An element whose attributes are each on a line of their own gets the
    // new one on a line of its own too, indented as the last.
    if let Some(last) = e.attrs.last() {
        if last.lead.contains('\n') {
            new.lead = last.lead.clone();
        }
        new.quote = last.quote;
        new.raw = crate::tree::escape(value, new.quote);
    }
    Some(Op::Attr {
        el,
        index: e.attrs.len(),
        old: None,
        new: Some(new),
    })
}

/// Makes the op that takes an attribute out.
///
/// # Arguments
///
/// * `doc`  - The document.
/// * `el`   - The element.
/// * `name` - The attribute's name.
///
/// # Returns
///
/// The op, or `None` if there is no such attribute.
pub fn remove_attr(doc: &Document, el: Id, name: &str) -> Option<Op> {
    let e = doc.element(el)?;
    let i = e.attrs.iter().position(|a| a.name == name)?;
    Some(Op::Attr {
        el,
        index: i,
        old: Some(e.attrs[i].clone()),
        new: None,
    })
}

/// The indentation a document is written with: what one level adds.
///
/// # Arguments
///
/// * `doc` - The document.
///
/// # Returns
///
/// The first step of indentation found between an element and its first
/// child, or a tab if the document is written on one line.
pub fn indent_unit(doc: &Document) -> String {
    /// Finds the first step of indentation below an element, looking
    /// deeper until one is found.
    ///
    /// # Arguments
    ///
    /// * `e`   - The element.
    /// * `own` - The indentation of the element's own line.
    ///
    /// # Returns
    ///
    /// What a child's indentation adds to `own`, or `None` if nothing
    /// below the element is on a line of its own, deeper than its parent.
    fn find(e: &Element, own: &str) -> Option<String> {
        let mut prev_space: Option<&str> = None;
        for c in &e.children {
            match c {
                Node::Text(t) if c.is_space() => prev_space = Some(t),
                Node::Element(k) => {
                    if let Some(ind) = prev_space.and_then(line_indent) {
                        if let Some(step) = ind.strip_prefix(own) {
                            if !step.is_empty() {
                                return Some(step.to_string());
                            }
                        }
                    }
                    let kid_indent =
                        prev_space.and_then(line_indent).unwrap_or_default();
                    if let Some(u) = find(k, &kid_indent) {
                        return Some(u);
                    }
                    prev_space = None;
                }
                _ => prev_space = None,
            }
        }
        None
    }
    find(doc.root(), "").unwrap_or_else(|| "\t".to_string())
}

/// The indentation at the end of whitespace that ends a line.
///
/// # Arguments
///
/// * `space` - The whitespace.
///
/// # Returns
///
/// What follows its last newline, or `None` if it has none.
fn line_indent(space: &str) -> Option<String> {
    space.rfind('\n').map(|i| space[i + 1..].to_string())
}

/// The indentation an element is written at.
///
/// # Arguments
///
/// * `doc` - The document.
/// * `id`  - The element.
///
/// # Returns
///
/// The indentation of the line it starts on, or `""` if it does not start
/// a line.
pub fn indent_of(doc: &Document, id: Id) -> String {
    let Some((parent, i)) = doc.parent_of(id) else {
        return String::new();
    };
    let kids = doc.children(parent).expect("the parent is in the document");
    if i > 0 {
        if let Node::Text(t) = &kids[i - 1] {
            if kids[i - 1].is_space() {
                return line_indent(t).unwrap_or_default();
            }
        }
    }
    String::new()
}

/// Lays out a new element to be put in at an indentation: each child on a
/// line of its own, one step in, and the end tag back at `indent`.
///
/// # Arguments
///
/// * `e`      - The element, whose children are laid out again.
/// * `indent` - The indentation it is at.
/// * `unit`   - One step of indentation.
pub fn lay_out(e: &mut Element, indent: &str, unit: &str) {
    if e.children.iter().all(|c| !matches!(c, Node::Element(_))) {
        return;
    }
    let inner = format!("{indent}{unit}");
    let mut out = Vec::new();
    for c in e.children.drain(..) {
        if c.is_space() {
            continue;
        }
        out.push(Node::Text(format!("\n{inner}")));
        let mut c = c;
        if let Node::Element(k) = &mut c {
            lay_out(k, &inner, unit);
        }
        out.push(c);
    }
    out.push(Node::Text(format!("\n{indent}")));
    e.children = out;
    e.empty = false;
}

/// The child index at which to put an element in, given its place among
/// the parent's child elements.
///
/// # Arguments
///
/// * `parent` - The parent.
/// * `at`     - Where among the child elements: 0 before the first.
///
/// # Returns
///
/// The index among all the children: before the comments that belong to
/// the child element it goes before, or after the last child element, its
/// trailing comments included, but before the whitespace that closes the
/// parent.
fn child_index(parent: &Element, at: usize) -> usize {
    let elements: Vec<usize> = parent
        .children
        .iter()
        .enumerate()
        .filter(|(_, c)| matches!(c, Node::Element(_)))
        .map(|(i, _)| i)
        .collect();
    if let Some(&i) = elements.get(at) {
        let (start, _) = attached_span(&parent.children, i);
        start
    } else {
        // After the last element and anything after it but the closing
        // whitespace.
        let mut i = parent.children.len();
        if i > 0 && parent.children[i - 1].is_space() {
            i -= 1;
        }
        i
    }
}

/// The span of children an element at an index takes with it: itself and
/// the comments just before it, each separated from the next by
/// whitespace with no blank line in it.  A blank line before a comment
/// shows it is about what follows in general, not this element alone.
///
/// # Arguments
///
/// * `kids` - The children.
/// * `i`    - The element's index among them.
///
/// # Returns
///
/// The index of the first child the element takes with it, at the start
/// of the whitespace before its first comment if any, and the index just
/// past the element.
fn attached_span(kids: &[Node], i: usize) -> (usize, usize) {
    let mut start = i;
    loop {
        // Whitespace before `start`, then a comment before that.
        let ws = start.checked_sub(1);
        let (space, before) = match ws.map(|w| &kids[w]) {
            Some(Node::Text(t)) if kids[ws.unwrap()].is_space() => {
                (t.as_str(), ws.unwrap().checked_sub(1))
            }
            _ => ("", ws),
        };
        let blank = space.matches('\n').count() > 1;
        match before.map(|b| &kids[b]) {
            Some(Node::Comment(_)) if !blank => start = before.unwrap(),
            _ => break,
        }
    }
    (start, i + 1)
}

/// Makes the op that puts an element in among a parent's children,
/// indented as its siblings are.
///
/// # Arguments
///
/// * `doc`    - The document.
/// * `parent` - The parent.
/// * `at`     - Where among its child elements: 0 before the first.
/// * `nodes`  - What to put in: the element, after any comments that
///              belong to it.  It is laid out at the place's indentation.
///
/// # Returns
///
/// The op, or `None` if the parent is not in the document.
pub fn insert(
    doc: &Document,
    parent: Id,
    at: usize,
    nodes: Vec<Node>,
) -> Option<Op> {
    let p = doc.element(parent)?;
    let unit = indent_unit(doc);
    let own = indent_of(doc, parent);
    let indent = format!("{own}{unit}");
    let mut nodes = nodes;
    for n in &mut nodes {
        if let Node::Element(e) = n {
            lay_out(e, &indent, &unit);
        }
    }
    let idx = child_index(p, at);
    let count = p.elements().count();
    let space = |s: &str| Node::Text(format!("\n{s}"));
    let mut inserted = Vec::new();
    if p.empty || p.children.is_empty() {
        // An element with nothing in it gets its children on lines of
        // their own and its end tag on a line of its own.
        for n in nodes {
            inserted.push(space(&indent));
            inserted.push(n);
        }
        inserted.push(space(&own));
    } else if at >= count {
        // After the last child element: each new node on a line of its own
        // if the children are laid out on lines, before the whitespace
        // that ends the last line.
        let laid = p.children.get(idx).is_some_and(Node::is_space);
        for n in nodes {
            if laid {
                inserted.push(space(&indent));
            }
            inserted.push(n);
        }
    } else {
        // Before a child element, after the whitespace that starts its
        // line, so each new node is followed by the same.
        let laid = idx > 0 && p.children[idx - 1].is_space();
        for n in nodes {
            inserted.push(n);
            if laid {
                inserted.push(space(&indent));
            }
        }
    }
    Some(Op::Splice {
        parent,
        at: idx,
        removed: Vec::new(),
        inserted,
        empty: (p.empty, false),
    })
}

/// Makes the op that takes an element out, with the comments that belong
/// to it and the whitespace that put it on a line of its own.
///
/// # Arguments
///
/// * `doc` - The document.
/// * `id`  - The element.
///
/// # Returns
///
/// The op, and what was taken out (the comments and the element, without
/// whitespace), or `None` if the element is the root or not in the
/// document.
pub fn remove(doc: &Document, id: Id) -> Option<(Op, Vec<Node>)> {
    let (parent, i) = doc.parent_of(id)?;
    if parent == crate::tree::DOCUMENT {
        return None;
    }
    let kids = doc.children(parent)?;
    let (mut start, end) = attached_span(kids, i);
    // The whitespace before the span goes too, so no empty line is left.
    if start > 0 && kids[start - 1].is_space() {
        start -= 1;
    }
    let taken: Vec<Node> = kids[start..end]
        .iter()
        .filter(|n| !n.is_space())
        .cloned()
        .collect();
    let left_only_space =
        kids[..start].iter().chain(&kids[end..]).all(Node::is_space);
    let op = if left_only_space {
        // Nothing is left: the element is written <a/> again, as it would
        // be had it always been empty.
        Op::Splice {
            parent,
            at: 0,
            removed: kids.to_vec(),
            inserted: Vec::new(),
            empty: (false, true),
        }
    } else {
        Op::Splice {
            parent,
            at: start,
            removed: kids[start..end].to_vec(),
            inserted: Vec::new(),
            empty: (false, false),
        }
    };
    Some((op, taken))
}

/// Makes the op that takes a comment out, with the whitespace that put it
/// on a line of its own.
///
/// # Arguments
///
/// * `doc` - The document.
/// * `id`  - The comment.
///
/// # Returns
///
/// The op, or `None` if there is no such comment.
pub fn remove_comment(doc: &Document, id: Id) -> Option<Op> {
    if !matches!(doc.node(id), Some(Node::Comment(_))) {
        return None;
    }
    let (parent, i) = doc.parent_of(id)?;
    let kids = doc.children(parent)?;
    let start = if i > 0 && kids[i - 1].is_space() {
        i - 1
    } else {
        i
    };
    let empty = doc.element(parent).is_some_and(|p| p.empty);
    Some(Op::Splice {
        parent,
        at: start,
        removed: kids[start..=i].to_vec(),
        inserted: Vec::new(),
        empty: (empty, empty),
    })
}

/// Strips the whitespace that laid nodes out, so they can be laid out
/// again somewhere else.
///
/// # Arguments
///
/// * `nodes` - The nodes, as [`remove`] gives them.
///
/// # Returns
///
/// The same nodes, the elements among them with the whitespace between
/// their children taken out where it only laid them out.
pub fn unlaid(nodes: Vec<Node>) -> Vec<Node> {
    nodes
        .into_iter()
        .map(|n| match n {
            Node::Element(mut e) => {
                strip_layout(&mut e);
                Node::Element(e)
            }
            other => other,
        })
        .collect()
}

/// Takes out the whitespace between an element's children, at every
/// level, where the element holds elements rather than text.
///
/// # Arguments
///
/// * `e` - The element.
fn strip_layout(e: &mut Element) {
    if e.has_text() {
        return;
    }
    e.children.retain(|c| !c.is_space());
    for c in &mut e.children {
        if let Node::Element(k) = c {
            strip_layout(k);
        }
    }
}

/// Makes the op that replaces an element's text, for an element that
/// holds a value rather than other elements.
///
/// # Arguments
///
/// * `doc`  - The document.
/// * `id`   - The element.
/// * `text` - The new text, unescaped.
///
/// # Returns
///
/// The op, or `None` if the element is not in the document, holds
/// elements, or already holds that text.
pub fn set_text(doc: &Document, id: Id, text: &str) -> Option<Op> {
    let e = doc.element(id)?;
    if e.elements().next().is_some() || e.text() == text {
        return None;
    }
    // Comments among the text stay, after it.
    let mut inserted = vec![Node::Text(crate::tree::escape(text, '\0'))];
    inserted.extend(
        e.children
            .iter()
            .filter(|c| matches!(c, Node::Comment(_) | Node::Pi(_)))
            .cloned(),
    );
    if text.is_empty() {
        inserted.remove(0);
    }
    Some(Op::Splice {
        parent: id,
        at: 0,
        removed: e.children.clone(),
        inserted: inserted.clone(),
        empty: (e.empty, e.empty && inserted.is_empty()),
    })
}

/// Makes the op that changes a comment's text.
///
/// # Arguments
///
/// * `doc`  - The document.
/// * `id`   - The comment.
/// * `text` - Its new text, which may not hold `--` or end in `-`.
///
/// # Returns
///
/// The op.
///
/// # Errors
///
/// Says why the text cannot be a comment's, or that there is no such
/// comment.
pub fn set_comment(doc: &Document, id: Id, text: &str) -> Result<Op, String> {
    check_comment(text)?;
    let (parent, i) = doc.parent_of(id).ok_or("there is no such comment")?;
    let old = doc.children(parent).ok_or("no parent")?[i].clone();
    let Node::Comment(c) = &old else {
        return Err("that is not a comment".to_string());
    };
    let empty = doc.element(parent).is_some_and(|p| p.empty);
    Ok(Op::Splice {
        parent,
        at: i,
        removed: vec![old.clone()],
        inserted: vec![Node::Comment(Comment {
            id: c.id,
            text: text.to_string(),
        })],
        empty: (empty, empty),
    })
}

/// Checks that a text can be a comment's.
///
/// # Arguments
///
/// * `text` - The text.
///
/// # Errors
///
/// Says what to change if it holds `--` or ends in `-`, which XML does not
/// allow in a comment.
pub fn check_comment(text: &str) -> Result<(), String> {
    if text.contains("--") {
        return Err("a comment may not hold \"--\"; write it otherwise, \
                    such as with a single dash"
            .to_string());
    }
    if text.ends_with('-') {
        return Err(
            "a comment may not end in \"-\"; add a space after it".to_string()
        );
    }
    Ok(())
}

/// Whether text is only whitespace that lays out nodes.
///
/// # Arguments
///
/// * `t` - The text.
///
/// # Returns
///
/// True if it is all whitespace.
pub fn is_layout(t: &str) -> bool {
    t.chars().all(is_xml_space)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Applies ops to a document, checks that undoing them gives back the
    /// original, and gives the document as edited.
    ///
    /// # Arguments
    ///
    /// * `text` - The document.
    /// * `make` - Makes the ops from the document.
    ///
    /// # Returns
    ///
    /// The edited document, written out.
    fn edited(text: &str, make: impl Fn(&Document) -> Vec<Op>) -> String {
        let mut doc = Document::parse(text).unwrap();
        let ops = make(&doc);
        for op in &ops {
            op.apply(&mut doc).unwrap();
        }
        let out = doc.write();
        for op in ops.iter().rev() {
            op.inverse().apply(&mut doc).unwrap();
        }
        assert_eq!(doc.write(), text, "undo gives back the original");
        out
    }

    /// The id of the n-th child element of the root.
    ///
    /// # Arguments
    ///
    /// * `doc` - The document.
    /// * `n`   - Which child, from 0.
    ///
    /// # Returns
    ///
    /// Its id.
    fn kid(doc: &Document, n: usize) -> Id {
        doc.root().elements().nth(n).unwrap().id
    }

    #[test]
    fn sets_an_attribute_as_it_was_written() {
        let out = edited("<a x = 'v' y=\"w\"/>", |d| {
            vec![set_attr(d, d.root().id, "x", "it's").unwrap()]
        });
        assert_eq!(out, "<a x = 'it&apos;s' y=\"w\"/>");
    }

    #[test]
    fn adds_an_attribute_as_the_others_are_written() {
        let out = edited("<a\n   x='v'/>", |d| {
            vec![set_attr(d, d.root().id, "z", "1").unwrap()]
        });
        assert_eq!(out, "<a\n   x='v'\n   z='1'/>");
    }

    #[test]
    fn inserts_indented_as_the_siblings_are() {
        let text = "<a>\n  <b/>\n  <c/>\n</a>";
        let out = edited(text, |d| {
            let e = Element::new(99, "n");
            vec![insert(d, d.root().id, 1, vec![Node::Element(e)]).unwrap()]
        });
        assert_eq!(out, "<a>\n  <b/>\n  <n/>\n  <c/>\n</a>");
        let out = edited(text, |d| {
            let e = Element::new(99, "n");
            vec![insert(d, d.root().id, 2, vec![Node::Element(e)]).unwrap()]
        });
        assert_eq!(out, "<a>\n  <b/>\n  <c/>\n  <n/>\n</a>");
    }

    #[test]
    fn inserts_into_an_empty_element() {
        let out = edited("<a>\n\t<b/>\n</a>", |d| {
            let b = kid(d, 0);
            let mut e = Element::new(99, "n");
            e.children.push(Node::Element(Element::new(98, "m")));
            vec![insert(d, b, 0, vec![Node::Element(e)]).unwrap()]
        });
        assert_eq!(
            out,
            "<a>\n\t<b>\n\t\t<n>\n\t\t\t<m/>\n\t\t</n>\n\t</b>\n</a>"
        );
    }

    #[test]
    fn removes_an_element_with_its_comments() {
        let text =
            "<a>\n  <!-- about b -->\n  <b/>\n\n  <!-- general -->\n\n  \
                    <!-- about c -->\n  <c/>\n</a>";
        let out = edited(text, |d| vec![remove(d, kid(d, 0)).unwrap().0]);
        assert_eq!(
            out,
            "<a>\n\n  <!-- general -->\n\n  <!-- about c -->\n  <c/>\n</a>"
        );
        let out = edited(text, |d| vec![remove(d, kid(d, 1)).unwrap().0]);
        assert_eq!(
            out,
            "<a>\n  <!-- about b -->\n  <b/>\n\n  <!-- general -->\n</a>"
        );
    }

    #[test]
    fn closes_an_element_left_empty() {
        let out = edited("<a>\n  <b/>\n</a>", |d| {
            vec![remove(d, kid(d, 0)).unwrap().0]
        });
        assert_eq!(out, "<a/>");
    }

    #[test]
    fn sets_text_and_keeps_comments() {
        let out = edited("<a><!-- c -->old</a>", |d| {
            vec![set_text(d, d.root().id, "a<b").unwrap()]
        });
        assert_eq!(out, "<a>a&lt;b<!-- c --></a>");
    }

    #[test]
    fn refuses_a_comment_xml_does_not_allow() {
        assert!(check_comment("a -- b").is_err());
        assert!(check_comment("a -").is_err());
        assert!(check_comment(" fine ").is_ok());
    }
}
