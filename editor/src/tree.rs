//! An XML document held so that writing it out gives back what was read,
//! byte for byte, except where it was edited.
//!
//! The XML binding in the `tcs-xtce` crate reads a document into typed
//! structures and writes it out again in its own hand, and in between the
//! comments, the blank lines, the quoting and the order of the attributes
//! are gone.  An editor that did that would turn a one-attribute change into
//! a rewrite of the whole file, and lose every comment its author wrote, so
//! the editor holds a document as this tree instead.  Every piece of the
//! file is kept as it was written: the whitespace inside a tag, the quote
//! character of each attribute, a value's character references, the text
//! between elements.  A comment is a node among its siblings, so it stays
//! where it was put when the elements around it are edited.
//!
//! The tree is checked as well-formed XML 1.0 in UTF-8 as it is read: tags
//! that match, attributes that are not repeated, the five predefined
//! entities and character references only, and one root element.  Nothing
//! here knows about XTCE; [`crate::schema`] and [`crate::validate`] do.
//!
//! Elements and comments carry an [`Id`] that does not change while the
//! document is open, so that an edit, and its undoing, can name what it
//! changes without depending on where it is.

use std::fmt;

/// What names an element or a comment for as long as the document is open.
/// Ids are never reused within a document.  Id 0 is the document itself, the
/// parent of the root element and of the comments around it.
pub type Id = u64;

/// The id of the document, as the parent of its top-level nodes.
pub const DOCUMENT: Id = 0;

/// A place in the source text: lines and columns count from 1, columns in
/// characters.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Place {
    /// The line, counting from 1.
    pub line: usize,
    /// The column, counting characters from 1, not bytes, so that it is
    /// the column an editor shows.
    pub column: usize,
}

impl fmt::Display for Place {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}:{}", self.line, self.column)
    }
}

/// Why a text is not a document the editor can hold.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParseError {
    /// Where in the text the problem was found.
    pub place: Place,
    /// What is wrong, and what to do about it, in the author's terms.
    pub message: String,
}

impl fmt::Display for ParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "line {}: {}", self.place, self.message)
    }
}

impl std::error::Error for ParseError {}

/// One attribute, as it was written.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Attr {
    /// The whitespace before the name.
    pub lead: String,
    /// The name, with its prefix if it has one.
    pub name: String,
    /// Everything between the name and the opening quote: `=`, with any
    /// whitespace around it.
    pub eq: String,
    /// `"` or `'`.
    pub quote: char,
    /// The value as written, with its references unexpanded.
    pub raw: String,
}

impl Attr {
    /// Makes an attribute written the usual way: one space before it, `=`
    /// with none around it, double quotes.
    ///
    /// # Arguments
    ///
    /// * `name`  - The name, with its prefix if it has one.
    /// * `value` - The value, unescaped; it is escaped here.
    ///
    /// # Returns
    ///
    /// The attribute.
    pub fn new(name: &str, value: &str) -> Attr {
        Attr {
            lead: " ".to_string(),
            name: name.to_string(),
            eq: "=".to_string(),
            quote: '"',
            raw: escape(value, '"'),
        }
    }

    /// The value, with its references expanded.
    pub fn value(&self) -> String {
        // The raw value was checked as the document was read, and a value
        // the editor writes is escaped by it, so it always expands.
        unescape(&self.raw).unwrap_or_else(|_| self.raw.clone())
    }

    /// Makes the same attribute, written as this one is, with another
    /// value, so that changing a value keeps its quotes and spacing.
    ///
    /// # Arguments
    ///
    /// * `value` - The new value, unescaped; it is escaped here for the
    ///             attribute's quote.
    ///
    /// # Returns
    ///
    /// The attribute with the new value.
    pub fn with_value(&self, value: &str) -> Attr {
        Attr {
            raw: escape(value, self.quote),
            ..self.clone()
        }
    }
}

/// An element, and everything inside it, as it was written.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Element {
    /// What names the element while the document is open.
    pub id: Id,
    /// The name as written, with its prefix if it has one.
    pub name: String,
    /// The attributes, in the order they were written.
    pub attrs: Vec<Attr>,
    /// The whitespace between the last attribute and `>` or `/>`.
    pub tail: String,
    /// Written as `<a/>`.  An element so written has no children; giving it
    /// one writes it with an end tag.
    pub empty: bool,
    /// What is between the start tag and the end tag, in order: elements,
    /// comments and text, whitespace included.
    pub children: Vec<Node>,
    /// The whitespace between the name in the end tag and its `>`.
    pub end_tail: String,
}

/// A comment.  Its text is what is between `<!--` and `-->`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Comment {
    /// What names the comment while the document is open.
    pub id: Id,
    /// The text between `<!--` and `-->`, as written.
    pub text: String,
}

/// One piece of a document.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Node {
    /// An element, with everything inside it.
    Element(Element),
    /// A comment.
    Comment(Comment),
    /// Character data as written, with its references unexpanded.
    Text(String),
    /// A CDATA section's contents.
    CData(String),
    /// A processing instruction, the XML declaration among them, as written
    /// between `<?` and `?>`.
    Pi(String),
    /// A document type declaration, as written between `<!DOCTYPE` and its
    /// `>`.
    Doctype(String),
}

impl Node {
    /// Gives the id of an element or a comment.
    ///
    /// # Returns
    ///
    /// The id, or `None` for text and the other nodes, which have none.
    pub fn id(&self) -> Option<Id> {
        match self {
            Node::Element(e) => Some(e.id),
            Node::Comment(c) => Some(c.id),
            _ => None,
        }
    }

    /// Says whether this is text of nothing but whitespace.
    ///
    /// # Returns
    ///
    /// `true` if it is text and all whitespace, `false` otherwise.
    pub fn is_space(&self) -> bool {
        match self {
            Node::Text(t) => t.chars().all(is_xml_space),
            _ => false,
        }
    }

    /// Gives every element and comment in this node, this one included, a
    /// new id from `next`, so that a copy of a node can go into a document
    /// beside the original.
    ///
    /// # Arguments
    ///
    /// * `next` - The last id given out, which is advanced past each id
    ///            given here.
    pub fn renumber(&mut self, next: &mut Id) {
        match self {
            Node::Element(e) => {
                *next += 1;
                e.id = *next;
                for c in &mut e.children {
                    c.renumber(next);
                }
            }
            Node::Comment(c) => {
                *next += 1;
                c.id = *next;
            }
            _ => {}
        }
    }
}

impl Element {
    /// Makes an empty element with no attributes, written as `<name/>`.
    ///
    /// # Arguments
    ///
    /// * `id`   - The id it is to have.
    /// * `name` - The name, with its prefix if it has one.
    ///
    /// # Returns
    ///
    /// The element.
    pub fn new(id: Id, name: &str) -> Element {
        Element {
            id,
            name: name.to_string(),
            attrs: Vec::new(),
            tail: String::new(),
            empty: true,
            children: Vec::new(),
            end_tail: String::new(),
        }
    }

    /// The name without its prefix.
    pub fn local(&self) -> &str {
        local_name(&self.name)
    }

    /// The prefix of the name, if it has one.
    pub fn prefix(&self) -> Option<&str> {
        self.name.split_once(':').map(|(p, _)| p)
    }

    /// The attribute of a name, written with its prefix if it has one.
    pub fn attr(&self, name: &str) -> Option<&Attr> {
        self.attrs.iter().find(|a| a.name == name)
    }

    /// An attribute's value.
    pub fn get(&self, name: &str) -> Option<String> {
        self.attr(name).map(Attr::value)
    }

    /// Gives the child elements, in order.
    ///
    /// # Returns
    ///
    /// An iterator over the children that are elements, passing over
    /// comments and text.
    pub fn elements(&self) -> impl Iterator<Item = &Element> {
        self.children.iter().filter_map(|n| match n {
            Node::Element(e) => Some(e),
            _ => None,
        })
    }

    /// Gives the text this element holds, with its references expanded and
    /// its CDATA sections included; comments are not text.
    ///
    /// # Returns
    ///
    /// The text, empty if it holds none.
    pub fn text(&self) -> String {
        let mut out = String::new();
        for c in &self.children {
            match c {
                Node::Text(t) => {
                    out.push_str(&unescape(t).unwrap_or_else(|_| t.clone()))
                }
                Node::CData(t) => out.push_str(t),
                _ => {}
            }
        }
        out
    }

    /// Says whether any child is character data that is not whitespace.
    ///
    /// # Returns
    ///
    /// `true` if one is, `false` if the element holds only elements,
    /// comments and whitespace.
    pub fn has_text(&self) -> bool {
        self.children.iter().any(|c| match c {
            Node::Text(_) => !c.is_space(),
            Node::CData(_) => true,
            _ => false,
        })
    }
}

/// The part of a name after its prefix.
pub fn local_name(name: &str) -> &str {
    name.rsplit_once(':').map_or(name, |(_, l)| l)
}

/// A whole document: its top-level nodes, the root element among them.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Document {
    /// Whether the text began with a byte-order mark, which is written back.
    pub bom: bool,
    /// The XML declaration, comments, whitespace and the root element.
    pub top: Vec<Node>,
    /// The last id given out.
    pub last_id: Id,
}

impl Document {
    /// Reads a document.
    ///
    /// # Errors
    ///
    /// Fails, saying where, if the text is not well-formed XML 1.0, or
    /// declares an encoding other than UTF-8.
    pub fn parse(text: &str) -> Result<Document, ParseError> {
        Parser::new(text).document()
    }

    /// Reads a document from bytes, which must be UTF-8.
    ///
    /// # Arguments
    ///
    /// * `bytes` - The file's contents.
    ///
    /// # Returns
    ///
    /// The document.
    ///
    /// # Errors
    ///
    /// As [`Document::parse`], and if the bytes are not UTF-8.
    pub fn from_bytes(bytes: &[u8]) -> Result<Document, ParseError> {
        match std::str::from_utf8(bytes) {
            Ok(text) => Document::parse(text),
            Err(e) => {
                let good = &bytes[..e.valid_up_to()];
                let text = String::from_utf8_lossy(good);
                Err(ParseError {
                    place: place_of(&text, text.len()),
                    message: "this is not UTF-8; save the file as UTF-8"
                        .to_string(),
                })
            }
        }
    }

    /// Writes the document out.
    ///
    /// # Returns
    ///
    /// The text of the document, the same as was read except where it was
    /// edited.
    pub fn write(&self) -> String {
        let mut out = String::new();
        if self.bom {
            out.push('\u{feff}');
        }
        for n in &self.top {
            write_node(n, &mut out);
        }
        out
    }

    /// Gives the root element.
    ///
    /// # Returns
    ///
    /// The root element.
    ///
    /// # Panics
    ///
    /// Panics if the document has no root element, which cannot happen:
    /// one is refused at reading, and no edit takes it out.
    pub fn root(&self) -> &Element {
        self.top
            .iter()
            .find_map(|n| match n {
                Node::Element(e) => Some(e),
                _ => None,
            })
            .expect("a document always has a root element")
    }

    /// Gives out a new id.
    pub fn next_id(&mut self) -> Id {
        self.last_id += 1;
        self.last_id
    }

    /// Finds the element of an id.
    ///
    /// # Arguments
    ///
    /// * `id` - The element's id.
    ///
    /// # Returns
    ///
    /// The element, or `None` if no element in the document has that id.
    pub fn element(&self, id: Id) -> Option<&Element> {
        find_in(&self.top, id).and_then(|n| match n {
            Node::Element(e) => Some(e),
            _ => None,
        })
    }

    /// Finds the element of an id, to change.
    ///
    /// # Arguments
    ///
    /// * `id` - The element's id.
    ///
    /// # Returns
    ///
    /// The element, or `None` if no element in the document has that id.
    pub fn element_mut(&mut self, id: Id) -> Option<&mut Element> {
        find_in_mut(&mut self.top, id).and_then(|n| match n {
            Node::Element(e) => Some(e),
            _ => None,
        })
    }

    /// The node of an id.
    pub fn node(&self, id: Id) -> Option<&Node> {
        find_in(&self.top, id)
    }

    /// Finds the children of an element, or the top-level nodes for
    /// [`DOCUMENT`].
    ///
    /// # Arguments
    ///
    /// * `parent` - The element's id, or [`DOCUMENT`].
    ///
    /// # Returns
    ///
    /// The children, or `None` if no element has that id.
    pub fn children(&self, parent: Id) -> Option<&Vec<Node>> {
        if parent == DOCUMENT {
            Some(&self.top)
        } else {
            self.element(parent).map(|e| &e.children)
        }
    }

    /// Finds the children of an element, or the top-level nodes, to
    /// change.
    ///
    /// # Arguments
    ///
    /// * `parent` - The element's id, or [`DOCUMENT`].
    ///
    /// # Returns
    ///
    /// The children, or `None` if no element has that id.
    pub fn children_mut(&mut self, parent: Id) -> Option<&mut Vec<Node>> {
        if parent == DOCUMENT {
            Some(&mut self.top)
        } else {
            self.element_mut(parent).map(|e| &mut e.children)
        }
    }

    /// The parent of a node and the node's index among its children.
    pub fn parent_of(&self, id: Id) -> Option<(Id, usize)> {
        parent_in(DOCUMENT, &self.top, id)
    }

    /// Lists the ids of the elements from the root down to an element, both
    /// included.
    ///
    /// # Arguments
    ///
    /// * `id` - The element.
    ///
    /// # Returns
    ///
    /// The ids, the root's first and `id` last.
    pub fn ancestry(&self, id: Id) -> Vec<Id> {
        let mut out = vec![id];
        let mut at = id;
        while let Some((p, _)) = self.parent_of(at) {
            if p == DOCUMENT {
                break;
            }
            out.push(p);
            at = p;
        }
        out.reverse();
        out
    }

    /// Finds the namespace an element's name is in, from the `xmlns`
    /// declarations on it and around it.
    ///
    /// # Arguments
    ///
    /// * `id` - The element.
    ///
    /// # Returns
    ///
    /// The namespace, or `None` if the name is in no namespace or there is
    /// no such element.
    pub fn namespace_of(&self, id: Id) -> Option<String> {
        let chain = self.ancestry(id);
        let el = self.element(id)?;
        let prefix = el.prefix();
        let wanted = match prefix {
            Some(p) => format!("xmlns:{p}"),
            None => "xmlns".to_string(),
        };
        for at in chain.iter().rev() {
            if let Some(v) = self.element(*at).and_then(|e| e.get(&wanted)) {
                return if v.is_empty() { None } else { Some(v) };
            }
        }
        None
    }

    /// Finds the prefix the namespace `ns` is declared with where `id` is,
    /// so that an element added there can be named in it.
    ///
    /// # Arguments
    ///
    /// * `id` - The element where the prefix is to be used.
    /// * `ns` - The namespace.
    ///
    /// # Returns
    ///
    /// The prefix, `""` for the default namespace, or `None` if `ns` is not
    /// declared there.
    pub fn prefix_for(&self, id: Id, ns: &str) -> Option<String> {
        for at in self.ancestry(id).iter().rev() {
            let el = self.element(*at)?;
            for a in &el.attrs {
                if a.value() != ns {
                    continue;
                }
                if a.name == "xmlns" {
                    return Some(String::new());
                }
                if let Some(p) = a.name.strip_prefix("xmlns:") {
                    return Some(p.to_string());
                }
            }
        }
        None
    }

    /// Finds the source line an element starts on, in the document as it
    /// would be written now.
    ///
    /// # Arguments
    ///
    /// * `id` - The element.
    ///
    /// # Returns
    ///
    /// The line, counting from 1, or `None` if there is no such element.
    pub fn line_of(&self, id: Id) -> Option<usize> {
        let mut out = String::new();
        if self.bom {
            out.push('\u{feff}');
        }
        for n in &self.top {
            if let Some(line) = line_in(n, id, &mut out) {
                return Some(line);
            }
        }
        None
    }
}

/// Finds the line a node starts on, writing out what comes before it, for
/// [`Document::line_of`].
///
/// # Arguments
///
/// * `n`   - The node to look in.
/// * `id`  - The node being looked for.
/// * `out` - The text written so far, which is added to.
///
/// # Returns
///
/// The line, counting from 1, or `None` if `id` is not in `n`.
fn line_in(n: &Node, id: Id, out: &mut String) -> Option<usize> {
    if n.id() == Some(id) {
        return Some(out.matches('\n').count() + 1);
    }
    if let Node::Element(e) = n {
        write_start(e, out);
        if e.empty {
            return None;
        }
        for c in &e.children {
            if let Some(line) = line_in(c, id, out) {
                return Some(line);
            }
        }
        write_end(e, out);
    } else {
        write_node(n, out);
    }
    None
}

/// Finds a node by its id among some nodes and everything inside them.
///
/// # Arguments
///
/// * `nodes` - The nodes to look in.
/// * `id`    - The id.
///
/// # Returns
///
/// The node, or `None` if it is not there.
fn find_in(nodes: &[Node], id: Id) -> Option<&Node> {
    for n in nodes {
        if n.id() == Some(id) {
            return Some(n);
        }
        if let Node::Element(e) = n {
            if let Some(found) = find_in(&e.children, id) {
                return Some(found);
            }
        }
    }
    None
}

/// Finds a node by its id among some nodes and everything inside them, to
/// change.
///
/// # Arguments
///
/// * `nodes` - The nodes to look in.
/// * `id`    - The id.
///
/// # Returns
///
/// The node, or `None` if it is not there.
fn find_in_mut(nodes: &mut [Node], id: Id) -> Option<&mut Node> {
    for n in nodes {
        if n.id() == Some(id) {
            return Some(n);
        }
        if let Node::Element(e) = n {
            if let Some(found) = find_in_mut(&mut e.children, id) {
                return Some(found);
            }
        }
    }
    None
}

/// Finds the parent of a node among some nodes and everything inside them.
///
/// # Arguments
///
/// * `parent` - The id of the nodes' own parent.
/// * `nodes`  - The nodes to look in.
/// * `id`     - The node.
///
/// # Returns
///
/// The parent's id and the node's index among its children, or `None` if
/// it is not there.
fn parent_in(parent: Id, nodes: &[Node], id: Id) -> Option<(Id, usize)> {
    for (i, n) in nodes.iter().enumerate() {
        if n.id() == Some(id) {
            return Some((parent, i));
        }
        if let Node::Element(e) = n {
            if let Some(found) = parent_in(e.id, &e.children, id) {
                return Some(found);
            }
        }
    }
    None
}

/// Writes an element's start tag, or the whole of an empty element, as it
/// was written.
///
/// # Arguments
///
/// * `e`   - The element.
/// * `out` - The text it is added to.
fn write_start(e: &Element, out: &mut String) {
    out.push('<');
    out.push_str(&e.name);
    for a in &e.attrs {
        out.push_str(&a.lead);
        out.push_str(&a.name);
        out.push_str(&a.eq);
        out.push(a.quote);
        out.push_str(&a.raw);
        out.push(a.quote);
    }
    out.push_str(&e.tail);
    out.push_str(if e.empty { "/>" } else { ">" });
}

/// Writes an element's end tag, as it was written.
///
/// # Arguments
///
/// * `e`   - The element.
/// * `out` - The text it is added to.
fn write_end(e: &Element, out: &mut String) {
    out.push_str("</");
    out.push_str(&e.name);
    out.push_str(&e.end_tail);
    out.push('>');
}

/// Writes one node out, as [`Document::write`] does.
///
/// # Arguments
///
/// * `n`   - The node, with everything inside it.
/// * `out` - The text it is added to.
pub fn write_node(n: &Node, out: &mut String) {
    match n {
        Node::Element(e) => {
            write_start(e, out);
            if !e.empty {
                for c in &e.children {
                    write_node(c, out);
                }
                write_end(e, out);
            }
        }
        Node::Comment(c) => {
            out.push_str("<!--");
            out.push_str(&c.text);
            out.push_str("-->");
        }
        Node::Text(t) => out.push_str(t),
        Node::CData(t) => {
            out.push_str("<![CDATA[");
            out.push_str(t);
            out.push_str("]]>");
        }
        Node::Pi(t) => {
            out.push_str("<?");
            out.push_str(t);
            out.push_str("?>");
        }
        Node::Doctype(t) => {
            out.push_str("<!DOCTYPE");
            out.push_str(t);
            out.push('>');
        }
    }
}

/// Whether a character is XML whitespace.
pub fn is_xml_space(c: char) -> bool {
    matches!(c, ' ' | '\t' | '\n' | '\r')
}

/// Escapes text for an attribute value in `quote`, or for character data
/// with `quote` `'\0'`.
///
/// # Arguments
///
/// * `text`  - The text, unescaped.
/// * `quote` - The quote the value is written in, or `'\0'` for
///             character data.
///
/// # Returns
///
/// The text as it is to be written.
pub fn escape(text: &str, quote: char) -> String {
    let mut out = String::with_capacity(text.len());
    for c in text.chars() {
        match c {
            '<' => out.push_str("&lt;"),
            '&' => out.push_str("&amp;"),
            '>' if quote == '\0' => out.push_str("&gt;"),
            '"' if quote == '"' => out.push_str("&quot;"),
            '\'' if quote == '\'' => out.push_str("&apos;"),
            // A literal newline or tab in an attribute is read back as a
            // space (attribute value normalisation), so it is written as a
            // reference to survive the trip.
            '\n' if quote != '\0' => out.push_str("&#10;"),
            '\t' if quote != '\0' => out.push_str("&#9;"),
            '\r' => out.push_str("&#13;"),
            _ => out.push(c),
        }
    }
    out
}

/// Expands the predefined entities and character references in raw text.
///
/// # Arguments
///
/// * `raw` - The text as written.
///
/// # Returns
///
/// The text with its references expanded.
///
/// # Errors
///
/// Says which reference is not one, as a message.
pub fn unescape(raw: &str) -> Result<String, String> {
    if !raw.contains('&') {
        return Ok(raw.to_string());
    }
    let mut out = String::with_capacity(raw.len());
    let mut rest = raw;
    while let Some(i) = rest.find('&') {
        out.push_str(&rest[..i]);
        let after = &rest[i + 1..];
        let Some(end) = after.find(';') else {
            return Err(
                "an & that starts no reference; write it as &amp;".to_string()
            );
        };
        let name = &after[..end];
        let c = match name {
            "lt" => '<',
            "gt" => '>',
            "amp" => '&',
            "quot" => '"',
            "apos" => '\'',
            _ => char_ref(name).ok_or_else(|| {
                format!(
                    "&{name}; is not a reference XML defines; write the \
                     character itself, or &#NNN;"
                )
            })?,
        };
        out.push(c);
        rest = &after[end + 1..];
    }
    out.push_str(rest);
    Ok(out)
}

/// Reads a character reference.
///
/// # Arguments
///
/// * `name` - What is between `&` and `;`: `#` and a decimal number, or
///            `#x` and a hexadecimal one.
///
/// # Returns
///
/// The character, or `None` if `name` is not a character reference or
/// names a character XML does not allow.
fn char_ref(name: &str) -> Option<char> {
    let n = if let Some(hex) = name.strip_prefix("#x") {
        u32::from_str_radix(hex, 16).ok()?
    } else {
        let dec = name.strip_prefix('#')?;
        dec.parse::<u32>().ok()?
    };
    char::from_u32(n).filter(|c| is_xml_char(*c))
}

/// Whether XML 1.0 allows a character in a document.
fn is_xml_char(c: char) -> bool {
    matches!(c, '\t' | '\n' | '\r' | '\u{20}'..='\u{d7ff}'
        | '\u{e000}'..='\u{fffd}' | '\u{10000}'..='\u{10ffff}')
}

/// Whether a character may begin an XML name.
fn is_name_start(c: char) -> bool {
    c.is_alphabetic() || c == '_' || c == ':'
}

/// Says whether a character may be in an XML name after its first.
///
/// # Arguments
///
/// * `c` - The character.
///
/// # Returns
///
/// `true` if it may be.
fn is_name_char(c: char) -> bool {
    is_name_start(c)
        || c.is_alphanumeric()
        || matches!(c, '-' | '.')
        || c == '\u{b7}'
}

/// Finds the place of a byte offset in a text.
///
/// # Arguments
///
/// * `text`   - The text.
/// * `offset` - The offset, in bytes; one past the end is taken as the
///              end.
///
/// # Returns
///
/// The line and column.
pub fn place_of(text: &str, offset: usize) -> Place {
    let before = &text[..offset.min(text.len())];
    let line = before.matches('\n').count() + 1;
    let start = before.rfind('\n').map_or(0, |i| i + 1);
    Place {
        line,
        column: before[start..].chars().count() + 1,
    }
}

/// Reads a document's text, keeping every piece of it as it was written.
struct Parser<'a> {
    /// The whole text.
    src: &'a str,
    /// The byte offset reading has reached.
    pos: usize,
    /// The last id given out.
    last_id: Id,
}

impl<'a> Parser<'a> {
    /// Makes a parser that reads from the start of a text.
    ///
    /// # Arguments
    ///
    /// * `src` - The text.
    ///
    /// # Returns
    ///
    /// The parser.
    fn new(src: &'a str) -> Parser<'a> {
        Parser {
            src,
            pos: 0,
            last_id: DOCUMENT,
        }
    }

    /// Makes an error that says where in the text it is.
    ///
    /// # Arguments
    ///
    /// * `at`      - The byte offset of the problem.
    /// * `message` - What is wrong, and what to do about it.
    ///
    /// # Returns
    ///
    /// Never `Ok`; being generic in `T` lets the caller return the error
    /// from whatever it reads.
    ///
    /// # Errors
    ///
    /// Always fails, with `message` at the place of `at`.
    fn error<T>(
        &self,
        at: usize,
        message: impl Into<String>,
    ) -> Result<T, ParseError> {
        Err(ParseError {
            place: place_of(self.src, at),
            message: message.into(),
        })
    }

    /// What is left of the text to read.
    fn rest(&self) -> &'a str {
        &self.src[self.pos..]
    }

    /// Gives out a new id.
    fn id(&mut self) -> Id {
        self.last_id += 1;
        self.last_id
    }

    /// Reads the whole text as a document.
    ///
    /// # Returns
    ///
    /// The document.
    ///
    /// # Errors
    ///
    /// Fails, saying where, at the first thing that is not well-formed XML
    /// 1.0 in UTF-8, or that an XTCE document cannot hold: a second root,
    /// text outside the root, a DOCTYPE that declares anything.
    fn document(mut self) -> Result<Document, ParseError> {
        let bom = self.rest().starts_with('\u{feff}');
        if bom {
            self.pos += '\u{feff}'.len_utf8();
        }
        if let Some(bad) = self
            .src
            .chars()
            .find(|c| !is_xml_char(*c) && *c != '\u{feff}')
        {
            let at = self.src.find(bad).unwrap_or(0);
            return self.error(
                at,
                format!(
                    "the character U+{:04X} is not allowed in XML; take it \
                     out",
                    bad as u32
                ),
            );
        }
        let mut top = Vec::new();
        let mut seen_root = false;
        loop {
            let rest = self.rest();
            if rest.is_empty() {
                break;
            }
            let start = self.pos;
            if rest.starts_with("<?") {
                let pi = self.pi()?;
                if pi.starts_with("xml") && pi[3..].starts_with(is_xml_space) {
                    if start != usize::from(bom) * 3 {
                        return self.error(
                            start,
                            "the XML declaration must come first in the \
                             file; move it to the top",
                        );
                    }
                    check_declaration(&pi).or_else(|m| self.error(start, m))?;
                }
                top.push(Node::Pi(pi));
            } else if rest.starts_with("<!--") {
                let c = self.comment()?;
                top.push(c);
            } else if rest.starts_with("<!DOCTYPE") {
                if seen_root {
                    return self.error(
                        start,
                        "a DOCTYPE must come before the root element",
                    );
                }
                top.push(self.doctype()?);
            } else if rest.starts_with('<') {
                if seen_root {
                    return self.error(
                        start,
                        "there is a second root element; an XTCE document \
                         has one SpaceSystem at its top",
                    );
                }
                let e = self.element()?;
                top.push(Node::Element(e));
                seen_root = true;
            } else {
                let end = rest.find('<').map_or(self.src.len(), |i| start + i);
                let text = &self.src[start..end];
                if !text.chars().all(is_xml_space) {
                    return self.error(
                        start,
                        "there is text outside the root element; only \
                         comments may be there",
                    );
                }
                top.push(Node::Text(text.to_string()));
                self.pos = end;
            }
        }
        if !seen_root {
            return self.error(self.src.len(), "there is no root element");
        }
        Ok(Document {
            bom,
            top,
            last_id: self.last_id,
        })
    }

    /// Reads a processing instruction, which starts where reading is.
    ///
    /// # Returns
    ///
    /// What is between `<?` and `?>`.
    ///
    /// # Errors
    ///
    /// Fails if it is never closed.
    fn pi(&mut self) -> Result<String, ParseError> {
        let start = self.pos;
        let Some(end) = self.rest().find("?>") else {
            return self.error(start, "a <? that is never closed by ?>");
        };
        let body = &self.src[start + 2..start + end];
        self.pos = start + end + 2;
        Ok(body.to_string())
    }

    /// Reads a comment, which starts where reading is.
    ///
    /// # Returns
    ///
    /// The comment, with a new id.
    ///
    /// # Errors
    ///
    /// Fails if it is never closed, or holds `--`, which XML forbids.
    fn comment(&mut self) -> Result<Node, ParseError> {
        let start = self.pos;
        let Some(end) = self.src[start + 4..].find("--") else {
            return self.error(start, "a comment that is never closed by -->");
        };
        let at = start + 4 + end;
        if !self.src[at..].starts_with("-->") {
            return self.error(
                at,
                "a comment may not hold \"--\"; take one of the dashes out",
            );
        }
        let text = self.src[start + 4..at].to_string();
        self.pos = at + 3;
        let id = self.id();
        Ok(Node::Comment(Comment { id, text }))
    }

    /// Reads a document type declaration, which starts where reading is.
    ///
    /// # Returns
    ///
    /// The declaration.
    ///
    /// # Errors
    ///
    /// Fails if it is never closed, or has an internal subset: entities
    /// it declared would change what the text means, and the editor does
    /// not expand them.
    fn doctype(&mut self) -> Result<Node, ParseError> {
        let start = self.pos;
        let body_start = start + "<!DOCTYPE".len();
        let mut depth = 0usize;
        for (i, c) in self.src[body_start..].char_indices() {
            match c {
                '[' => depth += 1,
                ']' => depth = depth.saturating_sub(1),
                '>' if depth == 0 => {
                    let body = &self.src[body_start..body_start + i];
                    if body.contains('[') {
                        return self.error(
                            start,
                            "this DOCTYPE declares entities or elements, \
                             which an XTCE document has no use for; take \
                             it out",
                        );
                    }
                    self.pos = body_start + i + 1;
                    return Ok(Node::Doctype(body.to_string()));
                }
                _ => {}
            }
        }
        self.error(start, "a DOCTYPE that is never closed by >")
    }

    /// Reads an XML name, which starts where reading is.
    ///
    /// # Returns
    ///
    /// The name, with its prefix if it has one.
    ///
    /// # Errors
    ///
    /// Fails if no name starts there.
    fn name(&mut self) -> Result<&'a str, ParseError> {
        let start = self.pos;
        let rest = self.rest();
        let mut end = 0;
        for (i, c) in rest.char_indices() {
            let ok = if i == 0 {
                is_name_start(c)
            } else {
                is_name_char(c)
            };
            if !ok {
                break;
            }
            end = i + c.len_utf8();
        }
        if end == 0 {
            return self.error(start, "a name was expected here");
        }
        self.pos += end;
        Ok(&rest[..end])
    }

    /// Reads whatever whitespace starts where reading is.
    ///
    /// # Returns
    ///
    /// The whitespace, which may be empty.
    fn space(&mut self) -> &'a str {
        let rest = self.rest();
        let end = rest
            .char_indices()
            .find(|(_, c)| !is_xml_space(*c))
            .map_or(rest.len(), |(i, _)| i);
        self.pos += end;
        &rest[..end]
    }

    /// Reads an element and everything inside it, which starts where
    /// reading is.
    ///
    /// # Returns
    ///
    /// The element, with new ids for it and everything inside it.
    ///
    /// # Errors
    ///
    /// Fails, saying where, at the first thing in it that is not
    /// well-formed: a tag never closed or not matched, an attribute
    /// repeated or unquoted, a reference XML does not define.
    fn element(&mut self) -> Result<Element, ParseError> {
        let open = self.pos;
        self.pos += 1;
        let name = self.name()?.to_string();
        let id = self.id();
        let mut attrs: Vec<Attr> = Vec::new();
        let tail;
        let empty;
        loop {
            let lead = self.space().to_string();
            let rest = self.rest();
            if rest.starts_with("/>") {
                self.pos += 2;
                tail = lead;
                empty = true;
                break;
            }
            if rest.starts_with('>') {
                self.pos += 1;
                tail = lead;
                empty = false;
                break;
            }
            if rest.is_empty() {
                return self.error(open, format!("<{name} is never closed"));
            }
            if lead.is_empty() {
                return self.error(
                    self.pos,
                    "attributes must be separated by whitespace",
                );
            }
            let at = self.pos;
            let aname = self.name()?.to_string();
            let mut eq = self.space().to_string();
            if !self.rest().starts_with('=') {
                return self.error(
                    self.pos,
                    format!("{aname} needs = and a quoted value"),
                );
            }
            self.pos += 1;
            eq.push('=');
            eq.push_str(self.space());
            let quote = match self.rest().chars().next() {
                Some(q @ ('"' | '\'')) => q,
                _ => {
                    return self.error(
                        self.pos,
                        format!("the value of {aname} must be in quotes"),
                    )
                }
            };
            self.pos += 1;
            let Some(end) = self.rest().find(quote) else {
                return self.error(
                    at,
                    format!("the value of {aname} is never closed"),
                );
            };
            let raw = &self.rest()[..end];
            if raw.contains('<') {
                return self.error(
                    self.pos,
                    format!(
                        "the value of {aname} holds <, which must be \
                         written &lt;"
                    ),
                );
            }
            unescape(raw).or_else(|m| self.error(self.pos, m))?;
            self.pos += end + 1;
            if attrs.iter().any(|a| a.name == aname) {
                return self.error(
                    at,
                    format!("{aname} is given twice; take one out"),
                );
            }
            attrs.push(Attr {
                lead,
                name: aname,
                eq,
                quote,
                raw: raw.to_string(),
            });
        }
        let mut el = Element {
            id,
            name,
            attrs,
            tail,
            empty,
            children: Vec::new(),
            end_tail: String::new(),
        };
        if empty {
            return Ok(el);
        }
        loop {
            let rest = self.rest();
            let start = self.pos;
            if rest.is_empty() {
                return self.error(
                    open,
                    format!("<{}> is never closed by </{}>", el.name, el.name),
                );
            }
            if rest.starts_with("</") {
                self.pos += 2;
                let end_name = self.name()?;
                if end_name != el.name {
                    return self.error(
                        start,
                        format!(
                            "</{end_name}> closes <{}>, which it does not \
                             match",
                            el.name
                        ),
                    );
                }
                el.end_tail = self.space().to_string();
                if !self.rest().starts_with('>') {
                    return self.error(self.pos, "> was expected here");
                }
                self.pos += 1;
                return Ok(el);
            }
            if rest.starts_with("<!--") {
                el.children.push(self.comment()?);
            } else if rest.starts_with("<![CDATA[") {
                let Some(end) = rest.find("]]>") else {
                    return self.error(start, "a CDATA section never closed");
                };
                el.children.push(Node::CData(rest[9..end].to_string()));
                self.pos += end + 3;
            } else if rest.starts_with("<?") {
                el.children.push(Node::Pi(self.pi()?));
            } else if rest.starts_with("<!") {
                return self.error(start, "<! here starts nothing XML allows");
            } else if rest.starts_with('<') {
                el.children.push(Node::Element(self.element()?));
            } else {
                let end = rest.find('<').unwrap_or(rest.len());
                let text = &rest[..end];
                if let Some(i) = text.find("]]>") {
                    return self.error(
                        start + i,
                        "]]> may not appear in text; write ]]&gt;",
                    );
                }
                unescape(text).or_else(|m| self.error(start, m))?;
                el.children.push(Node::Text(text.to_string()));
                self.pos += end;
            }
        }
    }
}

/// The value a pseudo-attribute of an XML declaration is given.
///
/// # Arguments
///
/// * `pi`   - The declaration, between `<?` and `?>`.
/// * `name` - The pseudo-attribute.
///
/// # Returns
///
/// The value, or `None` if it is not given.
fn declared(pi: &str, name: &str) -> Option<String> {
    let i = pi.find(name)?;
    let rest = &pi[i + name.len()..];
    Some(
        rest.chars()
            .skip_while(|c| *c != '"' && *c != '\'')
            .skip(1)
            .take_while(|c| *c != '"' && *c != '\'')
            .collect(),
    )
}

/// Checks an XML declaration's version and encoding.
///
/// # Arguments
///
/// * `pi` - The declaration, between `<?` and `?>`.
///
/// # Errors
///
/// Fails, saying what to change, unless it declares XML 1.0 and either
/// UTF-8 or no encoding, since the editor reads nothing else.
fn check_declaration(pi: &str) -> Result<(), String> {
    match declared(pi, "version") {
        Some(v) if v == "1.0" => {}
        Some(v) => {
            return Err(format!(
                "the file says it is XML {v}; the editor reads XML 1.0, so \
                 say version=\"1.0\""
            ))
        }
        None => {
            return Err("the XML declaration has no version; give it \
                        version=\"1.0\""
                .to_string())
        }
    }
    if let Some(value) = declared(pi, "encoding") {
        let value = value.to_ascii_lowercase();
        if value != "utf-8" {
            return Err(format!(
                "the file says it is in {value}; the editor reads UTF-8 \
                 only, so convert it and say encoding=\"UTF-8\""
            ));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn round_trip(text: &str) {
        let doc = Document::parse(text).expect("parses");
        assert_eq!(doc.write(), text);
    }

    #[test]
    fn writes_back_what_it_read() {
        round_trip(
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n\
             <!-- before -->\n\
             <xtce:SpaceSystem  name = 'a&amp;b' xmlns:xtce=\"u\"\n\
             \t   b=\"&#10;\" >\n\
             \t<!-- inside -->  <xtce:A/>\r\n\
             \t<xtce:B x=\"1\"  >t &lt; u<![CDATA[<raw>]]></xtce:B >\n\
             </xtce:SpaceSystem>\n<!-- after -->\n",
        );
    }

    #[test]
    fn keeps_a_byte_order_mark() {
        round_trip("\u{feff}<a/>");
    }

    #[test]
    fn refuses_what_is_not_well_formed() {
        for bad in [
            "<a>",
            "<a></b>",
            "<a x='1' x='2'/>",
            "<a/><b/>",
            "<a>&nbsp;</a>",
            "<a><!-- x -- y --></a>",
            "text<a/>",
            "<a x=1/>",
            "<?xml version=\"1.0\" encoding=\"latin1\"?><a/>",
        ] {
            assert!(Document::parse(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn says_where_a_problem_is() {
        let e = Document::parse("<a>\n  <b>\n</a>").unwrap_err();
        assert_eq!(e.place, Place { line: 3, column: 1 });
    }

    #[test]
    fn finds_by_id_and_namespace() {
        let doc = Document::parse(
            "<x:S xmlns:x=\"n\"><x:A><B xmlns=\"m\"/></x:A></x:S>",
        )
        .unwrap();
        let root = doc.root();
        let a = root.elements().next().unwrap();
        let b = a.elements().next().unwrap();
        assert_eq!(doc.namespace_of(a.id).as_deref(), Some("n"));
        assert_eq!(doc.namespace_of(b.id).as_deref(), Some("m"));
        assert_eq!(doc.parent_of(b.id), Some((a.id, 0)));
        assert_eq!(doc.ancestry(b.id), vec![root.id, a.id, b.id]);
        assert_eq!(doc.prefix_for(b.id, "n").as_deref(), Some("x"));
    }

    #[test]
    fn escapes_what_it_writes() {
        let a = Attr::new("n", "a\"<&\n");
        assert_eq!(a.raw, "a&quot;&lt;&amp;&#10;");
        assert_eq!(a.value(), "a\"<&\n");
    }

    #[test]
    fn finds_an_elements_line() {
        let doc = Document::parse("<a>\n<b/>\n<c/></a>").unwrap();
        let c = doc.root().elements().nth(1).unwrap().id;
        assert_eq!(doc.line_of(c), Some(3));
    }
}
