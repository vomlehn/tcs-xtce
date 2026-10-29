//! Minimal, allocation-conscious XML writer and pull reader.
//!
//! The writer appends directly into a managed [`Buffer`].  The reader works
//! in place over the bytes of a buffer: tag names, attribute values and text
//! are borrowed slices of the input and are only copied when they are stored
//! into a generated data structure.

use std::borrow::Cow;
use std::fmt;

use crate::buffer::Buffer;
use crate::value::Value;

/// Error raised while reading or writing XML.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Error {
    pub message: String,
    /// 1-based line in the input, or 0 when not tied to an input position.
    pub line: usize,
    pub column: usize,
}

impl Error {
    /// Makes an error not tied to a position in the input.
    ///
    /// # Arguments
    ///
    /// * `message` - What is wrong.
    ///
    /// # Returns
    ///
    /// The error, with line and column 0.
    pub fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            line: 0,
            column: 0,
        }
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        if self.line > 0 {
            write!(f, "{}:{}: {}", self.line, self.column, self.message)
        } else {
            f.write_str(&self.message)
        }
    }
}

impl std::error::Error for Error {}

pub type Result<T> = std::result::Result<T, Error>;

/// A type that maps to an XML element.
pub trait XmlElement: Sized {
    /// Writes `self` as an element named `tag`.
    ///
    /// # Arguments
    ///
    /// * `w`   - The writer the element goes to.
    /// * `tag` - The element's name.
    ///
    /// # Errors
    ///
    /// Fails if `self` breaks a constraint of the schema, such as the
    /// number of times an element may occur.
    fn write_xml(&self, w: &mut Writer<'_>, tag: &str) -> Result<()>;
    /// Reads the element whose start tag `tag` has just been consumed.
    ///
    /// # Arguments
    ///
    /// * `r`   - The reader, positioned just after the start tag; the
    ///           element's end tag is consumed too.
    /// * `tag` - The element's start tag.
    ///
    /// # Returns
    ///
    /// The value the element holds.
    ///
    /// # Errors
    ///
    /// Fails if the XML is malformed or the element is not a valid `Self`.
    fn read_xml<'a>(r: &mut Reader<'a>, tag: &Tag<'a>) -> Result<Self>;
}

impl<T: XmlElement> XmlElement for Box<T> {
    fn write_xml(&self, w: &mut Writer<'_>, tag: &str) -> Result<()> {
        (**self).write_xml(w, tag)
    }

    fn read_xml<'a>(r: &mut Reader<'a>, tag: &Tag<'a>) -> Result<Self> {
        T::read_xml(r, tag).map(Box::new)
    }
}

/// Checks the number of occurrences of a repeated element before writing.
///
/// # Arguments
///
/// * `len`  - The number of occurrences.
/// * `min`  - The fewest allowed.
/// * `max`  - The most allowed, or `None` if unbounded.
/// * `what` - The element's name, for the error message.
///
/// # Errors
///
/// Fails if `len` is below `min` or above `max`.
pub fn check_count(
    len: usize,
    min: usize,
    max: Option<usize>,
    what: &str,
) -> Result<()> {
    if len < min || max.is_some_and(|m| len > m) {
        let max = max.map_or("unbounded".to_owned(), |m| m.to_string());
        return Err(Error::new(format!(
            "{what}: {len} occurrences, expected {min}..{max}"
        )));
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Writer
// ---------------------------------------------------------------------------

#[derive(Clone, Copy, PartialEq, Eq)]
enum Last {
    Nothing,
    Start,
    Text,
    End,
}

/// Streams XML into a [`Buffer`].
pub struct Writer<'b> {
    buf: &'b mut Buffer,
    scratch: String,
    depth: usize,
    open: bool,
    last: Last,
    indent: bool,
    root_ns: Option<&'b str>,
}

impl<'b> Writer<'b> {
    /// Creates a writer that appends to `buf` (which is not cleared).
    ///
    /// # Arguments
    ///
    /// * `buf`    - The buffer the XML is appended to.
    /// * `indent` - Whether to pretty-print, two spaces per level.
    ///
    /// # Returns
    ///
    /// The writer.
    pub fn new(buf: &'b mut Buffer, indent: bool) -> Self {
        Self {
            buf,
            scratch: String::with_capacity(64),
            depth: 0,
            open: false,
            last: Last::Nothing,
            indent,
            root_ns: None,
        }
    }

    /// Writes the XML declaration.
    pub fn declaration(&mut self) {
        self.buf
            .push_str("<?xml version=\"1.0\" encoding=\"UTF-8\"?>");
        self.last = Last::End;
    }

    /// Declares `ns` as the default namespace on the next element written.
    pub fn set_root_namespace(&mut self, ns: &'b str) {
        self.root_ns = Some(ns);
    }

    /// Starts a new line indented to the current depth, when indenting
    /// and something has already been written.
    fn newline(&mut self) {
        if self.indent && self.last != Last::Nothing {
            self.buf.push(b'\n');
            for _ in 0..self.depth {
                self.buf.push_str("  ");
            }
        }
    }

    /// Ends the open start tag with `>`, if one is open.
    fn close_start(&mut self) {
        if self.open {
            self.buf.push(b'>');
            self.open = false;
        }
    }

    /// Writes the start of an element's start tag, which stays open for
    /// attributes until content or the end is written.  The first element
    /// written declares the namespace given to
    /// [`Writer::set_root_namespace`].
    ///
    /// # Arguments
    ///
    /// * `name` - The element's name.
    pub fn start(&mut self, name: &str) {
        self.close_start();
        self.newline();
        self.buf.push(b'<');
        self.buf.push_str(name);
        self.open = true;
        self.depth += 1;
        self.last = Last::Start;
        if let Some(ns) = self.root_ns.take() {
            self.attr_str("xmlns", ns);
        }
    }

    /// Writes an attribute into the open start tag, escaping its value.
    ///
    /// # Arguments
    ///
    /// * `name`  - The attribute's name.
    /// * `value` - Its value, unescaped.
    ///
    /// # Panics
    ///
    /// In debug builds, if no start tag is open.
    pub fn attr_str(&mut self, name: &str, value: &str) {
        debug_assert!(self.open, "attribute written outside a start tag");
        self.buf.push(b' ');
        self.buf.push_str(name);
        self.buf.push_str("=\"");
        escape_into(self.buf, value, true);
        self.buf.push(b'"');
    }

    /// Writes an attribute into the open start tag, its value in XML
    /// lexical form.
    ///
    /// # Arguments
    ///
    /// * `name`  - The attribute's name.
    /// * `value` - Its value.
    ///
    /// # Panics
    ///
    /// In debug builds, if no start tag is open.
    pub fn attr<V: Value>(&mut self, name: &str, value: &V) {
        let mut s = std::mem::take(&mut self.scratch);
        s.clear();
        value.fmt_xml(&mut s);
        self.attr_str(name, &s);
        self.scratch = s;
    }

    pub fn text_str(&mut self, value: &str) {
        self.close_start();
        escape_into(self.buf, value, false);
        self.last = Last::Text;
    }

    /// Writes a value, in XML lexical form, as text content.
    ///
    /// # Arguments
    ///
    /// * `value` - The value.
    pub fn text<V: Value>(&mut self, value: &V) {
        let mut s = std::mem::take(&mut self.scratch);
        s.clear();
        value.fmt_xml(&mut s);
        self.text_str(&s);
        self.scratch = s;
    }

    /// Ends the current element: closes an empty one with `/>`, otherwise
    /// writes its end tag.
    ///
    /// # Arguments
    ///
    /// * `name` - The element's name, as given to [`Writer::start`].
    ///
    /// # Panics
    ///
    /// In debug builds, if no element is open.
    pub fn end(&mut self, name: &str) {
        self.depth -= 1;
        if self.open {
            self.buf.push_str("/>");
            self.open = false;
        } else {
            if self.last == Last::End {
                self.newline();
            }
            self.buf.push_str("</");
            self.buf.push_str(name);
            self.buf.push(b'>');
        }
        self.last = Last::End;
    }

    /// Writes `<tag>value</tag>` for a scalar value.
    pub fn leaf<V: Value>(&mut self, tag: &str, value: &V) {
        self.start(tag);
        self.text(value);
        self.end(tag);
    }

    /// Writes a trailing newline when indenting.
    pub fn finish(&mut self) {
        if self.indent {
            self.buf.push(b'\n');
        }
    }
}

/// Appends `s` to `buf`, replacing `&`, `<`, `>` and carriage returns
/// with references and, in an attribute value, `"`, line feeds and tabs
/// too.
///
/// # Arguments
///
/// * `buf`  - The buffer appended to.
/// * `s`    - The text, unescaped.
/// * `attr` - Whether `s` is an attribute value.
fn escape_into(buf: &mut Buffer, s: &str, attr: bool) {
    let bytes = s.as_bytes();
    let mut start = 0;
    for (i, &b) in bytes.iter().enumerate() {
        let rep: &str = match b {
            b'&' => "&amp;",
            b'<' => "&lt;",
            b'>' => "&gt;",
            b'"' if attr => "&quot;",
            b'\n' if attr => "&#10;",
            b'\r' => "&#13;",
            b'\t' if attr => "&#9;",
            _ => continue,
        };
        buf.extend_from_slice(&bytes[start..i]);
        buf.push_str(rep);
        start = i + 1;
    }
    buf.extend_from_slice(&bytes[start..]);
}

// ---------------------------------------------------------------------------
// Reader
// ---------------------------------------------------------------------------

/// A start tag.  Attributes are parsed lazily from the raw tag text.
#[derive(Debug, Clone, Copy)]
pub struct Tag<'a> {
    pub name: &'a str,
    raw_attrs: &'a str,
    pub empty: bool,
    offset: usize,
}

impl<'a> Tag<'a> {
    /// The element name without any namespace prefix.
    pub fn local_name(&self) -> &'a str {
        local_name(self.name)
    }

    /// Iterates over `(name, raw value)` pairs.
    pub fn attrs(&self) -> Attrs<'a> {
        Attrs {
            rest: self.raw_attrs,
        }
    }
}

fn local_name(name: &str) -> &str {
    name.rsplit_once(':').map_or(name, |(_, l)| l)
}

/// Iterator over the attributes of a [`Tag`].
pub struct Attrs<'a> {
    rest: &'a str,
}

impl<'a> Iterator for Attrs<'a> {
    type Item = Result<(&'a str, &'a str)>;

    fn next(&mut self) -> Option<Self::Item> {
        let s = self.rest.trim_start();
        if s.is_empty() {
            return None;
        }
        let Some(eq) = s.find('=') else {
            self.rest = "";
            return Some(Err(Error::new(format!("malformed attribute {s:?}"))));
        };
        let name = s[..eq].trim_end();
        let after = s[eq + 1..].trim_start();
        let quote = match after.chars().next() {
            Some(q @ ('"' | '\'')) => q,
            _ => {
                self.rest = "";
                return Some(Err(Error::new(format!(
                    "unquoted attribute {name}"
                ))));
            }
        };
        let Some(close) = after[1..].find(quote) else {
            self.rest = "";
            return Some(Err(Error::new(format!(
                "unterminated attribute {name}"
            ))));
        };
        self.rest = &after[close + 2..];
        Some(Ok((name, &after[1..close + 1])))
    }
}

enum Token<'a> {
    Start(Tag<'a>),
    End(&'a str),
    Text(&'a str),
    CData(&'a str),
    Eof,
}

/// Pull reader over a complete UTF-8 document held in memory.
pub struct Reader<'a> {
    src: &'a str,
    pos: usize,
    peeked: Option<(usize, Token<'a>)>,
    stack: Vec<&'a str>,
    pending_end: bool,
    text: String,
}

impl<'a> Reader<'a> {
    /// Makes a reader over a whole document, skipping a byte order mark.
    ///
    /// # Arguments
    ///
    /// * `src` - The document's bytes.
    ///
    /// # Returns
    ///
    /// The reader, at the start of the document.
    ///
    /// # Errors
    ///
    /// Fails if `src` is not UTF-8.
    pub fn new(src: &'a [u8]) -> Result<Self> {
        let src = std::str::from_utf8(src)
            .map_err(|e| Error::new(format!("input is not UTF-8: {e}")))?;
        let src = src.strip_prefix('\u{feff}').unwrap_or(src);
        Ok(Self {
            src,
            pos: 0,
            peeked: None,
            stack: Vec::with_capacity(32),
            pending_end: false,
            text: String::with_capacity(256),
        })
    }

    /// Builds an error annotated with the line and column of `offset`.
    ///
    /// # Arguments
    ///
    /// * `offset`  - The byte offset in the input the error is at.
    /// * `message` - What is wrong.
    ///
    /// # Returns
    ///
    /// The error, with the 1-based line and column of `offset`.
    pub fn error_at(&self, offset: usize, message: impl Into<String>) -> Error {
        let before = &self.src[..offset.min(self.src.len())];
        let line = before.bytes().filter(|&b| b == b'\n').count() + 1;
        let column = before.len() - before.rfind('\n').map_or(0, |i| i + 1) + 1;
        Error {
            message: message.into(),
            line,
            column,
        }
    }

    fn error(&self, message: impl Into<String>) -> Error {
        let at = self.peeked.as_ref().map_or(self.pos, |(o, _)| *o);
        self.error_at(at, message)
    }

    /// Finds the end of a construct.
    ///
    /// # Arguments
    ///
    /// * `from` - The byte offset to search from.
    /// * `pat`  - The text that ends the construct.
    ///
    /// # Returns
    ///
    /// The offset just past the first `pat` at or after `from`.
    ///
    /// # Errors
    ///
    /// Fails if `pat` doesn't appear.
    fn skip_past(&mut self, from: usize, pat: &str) -> Result<usize> {
        match self.src[from..].find(pat) {
            Some(i) => Ok(from + i + pat.len()),
            None => Err(self.error_at(
                from,
                format!("unterminated construct, expected {pat:?}"),
            )),
        }
    }

    /// Reads the next token, skipping processing instructions, comments
    /// and DOCTYPE declarations.
    ///
    /// # Returns
    ///
    /// The token's byte offset and the token: a start tag, an end tag,
    /// raw text, a CDATA section, or `Eof` at the end of the input.
    ///
    /// # Errors
    ///
    /// Fails if a construct is unterminated or a start tag has no name.
    fn lex(&mut self) -> Result<(usize, Token<'a>)> {
        loop {
            let start = self.pos;
            let rest = &self.src[start..];
            if rest.is_empty() {
                return Ok((start, Token::Eof));
            }
            if !rest.starts_with('<') {
                let end = rest.find('<').map_or(self.src.len(), |i| start + i);
                self.pos = end;
                return Ok((start, Token::Text(&self.src[start..end])));
            }
            if rest.starts_with("<?") {
                self.pos = self.skip_past(start, "?>")?;
            } else if rest.starts_with("<!--") {
                self.pos = self.skip_past(start + 4, "-->")?;
            } else if rest.starts_with("<![CDATA[") {
                let end = self.skip_past(start, "]]>")?;
                self.pos = end;
                return Ok((
                    start,
                    Token::CData(&self.src[start + 9..end - 3]),
                ));
            } else if rest.starts_with("<!") {
                // DOCTYPE, possibly with an internal subset.
                let mut depth = 0usize;
                let mut end = None;
                for (i, c) in rest.char_indices() {
                    match c {
                        '[' => depth += 1,
                        ']' => depth = depth.saturating_sub(1),
                        '>' if depth == 0 => {
                            end = Some(start + i + 1);
                            break;
                        }
                        _ => {}
                    }
                }
                self.pos =
                    end.ok_or_else(|| self.error_at(start, "unterminated <!"))?;
            } else if let Some(r) = rest.strip_prefix("</") {
                let close = r.find('>').ok_or_else(|| {
                    self.error_at(start, "unterminated end tag")
                })?;
                self.pos = start + 2 + close + 1;
                return Ok((start, Token::End(r[..close].trim_end())));
            } else {
                // Start tag; '>' may appear inside quoted attribute values.
                let bytes = rest.as_bytes();
                let mut quote = 0u8;
                let mut close = None;
                for (i, &b) in bytes.iter().enumerate().skip(1) {
                    if quote != 0 {
                        if b == quote {
                            quote = 0;
                        }
                    } else if b == b'"' || b == b'\'' {
                        quote = b;
                    } else if b == b'>' {
                        close = Some(i);
                        break;
                    }
                }
                let close = close.ok_or_else(|| {
                    self.error_at(start, "unterminated start tag")
                })?;
                let mut inner = &rest[1..close];
                let empty = inner.ends_with('/');
                if empty {
                    inner = &inner[..inner.len() - 1];
                }
                let name_end = inner
                    .find(|c: char| c.is_ascii_whitespace())
                    .unwrap_or(inner.len());
                let name = &inner[..name_end];
                if name.is_empty() {
                    return Err(self.error_at(start, "missing element name"));
                }
                self.pos = start + close + 1;
                return Ok((
                    start,
                    Token::Start(Tag {
                        name,
                        raw_attrs: &inner[name_end..],
                        empty,
                        offset: start,
                    }),
                ));
            }
        }
    }

    /// Looks at the next token without consuming it.
    ///
    /// # Returns
    ///
    /// The token, kept for the next read.
    ///
    /// # Errors
    ///
    /// Fails as [`Reader::lex`] does.
    fn peek_token(&mut self) -> Result<&Token<'a>> {
        if self.peeked.is_none() {
            let t = self.lex()?;
            self.peeked = Some(t);
        }
        Ok(&self.peeked.as_ref().unwrap().1)
    }

    fn take_token(&mut self) -> Result<Token<'a>> {
        self.peek_token()?;
        Ok(self.peeked.take().unwrap().1)
    }

    /// Reads up to and including the root start tag.
    ///
    /// # Returns
    ///
    /// The root's start tag.
    ///
    /// # Errors
    ///
    /// Fails if the input is malformed or anything but whitespace,
    /// comments, processing instructions and a DOCTYPE comes first.
    pub fn root(&mut self) -> Result<Tag<'a>> {
        loop {
            match self.take_token()? {
                Token::Text(t) if t.trim().is_empty() => {}
                Token::Start(tag) => {
                    self.opened(&tag);
                    return Ok(tag);
                }
                _ => return Err(self.error("expected the root element")),
            }
        }
    }

    /// Verifies that only whitespace and comments follow the root element.
    ///
    /// # Errors
    ///
    /// Fails if anything else follows the root element.
    pub fn finish(&mut self) -> Result<()> {
        loop {
            match self.take_token()? {
                Token::Eof => return Ok(()),
                Token::Text(t) if t.trim().is_empty() => {}
                _ => return Err(self.error("content after the root element")),
            }
        }
    }

    /// Records that a start tag was consumed: an empty element's end is
    /// pending, any other element is pushed on the stack of open ones.
    ///
    /// # Arguments
    ///
    /// * `tag` - The start tag.
    fn opened(&mut self, tag: &Tag<'a>) {
        if tag.empty {
            self.pending_end = true;
        } else {
            self.stack.push(tag.name);
        }
    }

    /// Finds the local name of the next child element, skipping
    /// whitespace.
    ///
    /// # Returns
    ///
    /// The name, or `None` at the parent's end.
    ///
    /// # Errors
    ///
    /// Fails on malformed input, the end of the input, or text that isn't
    /// whitespace.
    pub fn peek_child(&mut self) -> Result<Option<&'a str>> {
        if self.pending_end {
            return Ok(None);
        }
        loop {
            match self.peek_token()? {
                Token::Start(tag) => return Ok(Some(local_name(tag.name))),
                Token::End(_) => return Ok(None),
                Token::Text(t) if t.trim().is_empty() => {
                    self.peeked = None;
                }
                Token::Eof => return Err(self.error("unexpected end of input")),
                _ => {
                    return Err(self.error("unexpected text in element content"))
                }
            }
        }
    }

    /// Consumes the next child start tag (after [`Reader::peek_child`]).
    ///
    /// # Returns
    ///
    /// The start tag.
    ///
    /// # Errors
    ///
    /// Fails if the next token is not a start tag.
    pub fn open(&mut self) -> Result<Tag<'a>> {
        match self.take_token()? {
            Token::Start(tag) => {
                self.opened(&tag);
                Ok(tag)
            }
            _ => Err(self.error("expected a start tag")),
        }
    }

    /// Consumes the end tag of the current element.
    ///
    /// # Errors
    ///
    /// Fails if a child element or text comes first, or the end tag
    /// doesn't match the element.
    pub fn close(&mut self) -> Result<()> {
        if self.pending_end {
            self.pending_end = false;
            return Ok(());
        }
        match self.peek_child()? {
            Some(name) => {
                Err(self.error(format!("unexpected element <{name}>")))
            }
            None => self.consume_end(),
        }
    }

    /// Consumes an end tag and pops the element it closes.
    ///
    /// # Errors
    ///
    /// Fails if the next token is not an end tag or doesn't match the
    /// innermost open element.
    fn consume_end(&mut self) -> Result<()> {
        let at = self.peeked.as_ref().map_or(self.pos, |(o, _)| *o);
        match self.take_token()? {
            Token::End(name) => match self.stack.pop() {
                Some(open) if open == name => Ok(()),
                Some(open) => Err(self.error_at(
                    at,
                    format!("</{name}> does not match <{open}>"),
                )),
                None => Err(self.error_at(at, format!("unbalanced </{name}>"))),
            },
            _ => Err(self.error_at(at, "expected an end tag")),
        }
    }

    /// Reads the text content of the current element and its end tag.
    ///
    /// # Returns
    ///
    /// The text, references replaced and CDATA sections included;
    /// borrowed from the input when it is one piece needing no changes.
    ///
    /// # Errors
    ///
    /// Fails on a child element, a bad reference, the end of the input or
    /// a mismatched end tag.
    pub fn read_text(&mut self) -> Result<Cow<'a, str>> {
        if self.pending_end {
            self.pending_end = false;
            return Ok(Cow::Borrowed(""));
        }
        let mut single: Option<Cow<'a, str>> = None;
        let mut many = false;
        loop {
            let at = self.peeked.as_ref().map_or(self.pos, |(o, _)| *o);
            let piece = match self.peek_token()? {
                Token::Text(t) => {
                    unescape(t, false).map_err(|m| self.error_at(at, m))?
                }
                Token::CData(t) => Cow::Borrowed(*t),
                Token::End(_) => break,
                Token::Start(t) => {
                    let msg = format!(
                        "unexpected element <{}> in text content",
                        t.name
                    );
                    return Err(self.error_at(at, msg));
                }
                Token::Eof => {
                    return Err(self.error_at(at, "unexpected end of input"))
                }
            };
            self.peeked = None;
            match single.take() {
                None if !many => single = Some(piece),
                prev => {
                    if !many {
                        self.text.clear();
                        many = true;
                    }
                    if let Some(p) = prev {
                        self.text.push_str(&p);
                    }
                    self.text.push_str(&piece);
                }
            }
        }
        self.consume_end()?;
        Ok(if many {
            Cow::Owned(self.text.clone())
        } else {
            single.unwrap_or(Cow::Borrowed(""))
        })
    }

    /// Reads text content as a typed value.
    pub fn read_value<V: Value>(&mut self) -> Result<V> {
        let at = self.pos;
        let text = self.read_text()?;
        V::parse_xml(&text).map_err(|m| self.error_at(at, m))
    }

    /// Reads a child element holding a scalar value.
    ///
    /// # Returns
    ///
    /// The value.
    ///
    /// # Errors
    ///
    /// Fails if the next token is not a start tag, the element has an
    /// attribute other than a namespace declaration or a prefixed one,
    /// or its content is not a valid `V`.
    pub fn read_leaf<V: Value>(&mut self) -> Result<V> {
        let tag = self.open()?;
        if !tag.raw_attrs.trim().is_empty() {
            for a in tag.attrs() {
                let (k, _) = a?;
                self.unknown_attr(&tag, k)?;
            }
        }
        self.read_value()
    }

    /// Reads a child element holding a complex value.
    pub fn read_child<T: XmlElement>(&mut self) -> Result<T> {
        let tag = self.open()?;
        T::read_xml(self, &tag)
    }

    /// Parses an attribute's raw value.
    pub fn attr_value<V: Value>(
        &self,
        tag: &Tag<'a>,
        raw: &'a str,
    ) -> Result<V> {
        let s =
            unescape(raw, true).map_err(|m| self.error_at(tag.offset, m))?;
        V::parse_xml(&s).map_err(|m| self.error_at(tag.offset, m))
    }

    /// Handles an attribute the schema does not declare.  Namespace
    /// declarations and `xsi:`/`xml:` attributes are accepted and ignored.
    ///
    /// # Arguments
    ///
    /// * `tag`  - The start tag the attribute is on.
    /// * `name` - The attribute's name.
    ///
    /// # Errors
    ///
    /// Fails for any attribute without a prefix other than `xmlns`.
    pub fn unknown_attr(&self, tag: &Tag<'a>, name: &str) -> Result<()> {
        if name == "xmlns" || name.contains(':') {
            return Ok(());
        }
        Err(self.error_at(
            tag.offset,
            format!("unexpected attribute {name:?} on <{}>", tag.name),
        ))
    }

    /// Builds the error for a required attribute that is missing.
    ///
    /// # Arguments
    ///
    /// * `tag`  - The start tag that lacks it.
    /// * `name` - The attribute's name.
    ///
    /// # Returns
    ///
    /// The error, at the start tag's position.
    pub fn missing_attr(&self, tag: &Tag<'a>, name: &str) -> Error {
        self.error_at(
            tag.offset,
            format!("missing required attribute {name:?} on <{}>", tag.name),
        )
    }

    /// Builds the error for a missing or unexpected child element.
    ///
    /// # Arguments
    ///
    /// * `what` - The name of the element that was expected.
    ///
    /// # Returns
    ///
    /// The error, naming the element found instead, if any, or the error
    /// from reading the next token.
    pub fn expected(&mut self, what: &str) -> Error {
        match self.peek_child() {
            Ok(Some(found)) => {
                self.error(format!("expected <{what}>, found <{found}>"))
            }
            Ok(None) => self.error(format!("expected <{what}>")),
            Err(e) => e,
        }
    }

    /// Error for too few occurrences of a repeated element.
    pub fn too_few(&mut self, what: &str, min: usize, found: usize) -> Error {
        self.error(format!(
            "expected at least {min} <{what}> elements, found {found}"
        ))
    }
}

/// Replaces entity and character references and normalizes line endings.
/// In attribute values, literal whitespace characters become spaces.
///
/// # Arguments
///
/// * `raw`  - The text as written.
/// * `attr` - Whether it is an attribute value.
///
/// # Returns
///
/// The text; borrowed from `raw` when nothing needs replacing.
///
/// # Errors
///
/// A message if a reference is unterminated or unknown.
pub fn unescape(
    raw: &str,
    attr: bool,
) -> std::result::Result<Cow<'_, str>, String> {
    let needs =
        |b: u8| b == b'&' || b == b'\r' || (attr && (b == b'\n' || b == b'\t'));
    if !raw.bytes().any(needs) {
        return Ok(Cow::Borrowed(raw));
    }
    let mut out = String::with_capacity(raw.len());
    let mut chars = raw.char_indices().peekable();
    while let Some((i, c)) = chars.next() {
        match c {
            '&' => {
                let semi = raw[i..]
                    .find(';')
                    .ok_or_else(|| format!("unterminated entity in {raw:?}"))?;
                let ent = &raw[i + 1..i + semi];
                match ent {
                    "amp" => out.push('&'),
                    "lt" => out.push('<'),
                    "gt" => out.push('>'),
                    "quot" => out.push('"'),
                    "apos" => out.push('\''),
                    _ => {
                        let code = if let Some(h) = ent.strip_prefix("#x") {
                            u32::from_str_radix(h, 16).ok()
                        } else if let Some(d) = ent.strip_prefix('#') {
                            d.parse().ok()
                        } else {
                            None
                        };
                        let ch = code
                            .and_then(char::from_u32)
                            .ok_or_else(|| format!("unknown entity &{ent};"))?;
                        out.push(ch);
                    }
                }
                while chars.peek().is_some_and(|(j, _)| *j <= i + semi) {
                    chars.next();
                }
            }
            '\r' => {
                if chars.peek().is_some_and(|(_, n)| *n == '\n') {
                    chars.next();
                }
                out.push(if attr { ' ' } else { '\n' });
            }
            '\n' | '\t' if attr => out.push(' '),
            _ => out.push(c),
        }
    }
    Ok(Cow::Owned(out))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unescapes_references() {
        assert_eq!(unescape("a&amp;b&#65;&#x42;", false).unwrap(), "a&bAB");
        assert_eq!(unescape("x\r\ny", false).unwrap(), "x\ny");
        assert_eq!(unescape("x\ny\tz", true).unwrap(), "x y z");
        assert!(unescape("&bogus;", false).is_err());
    }

    #[test]
    fn writer_escapes_and_indents() {
        let mut buf = Buffer::with_capacity(128);
        let mut w = Writer::new(&mut buf, true);
        w.start("A");
        w.attr_str("v", "1<\"2\"\n");
        w.leaf("B", &String::from("x&y"));
        w.start("C");
        w.end("C");
        w.end("A");
        assert_eq!(
            std::str::from_utf8(buf.as_bytes()).unwrap(),
            "<A v=\"1&lt;&quot;2&quot;&#10;\">\n  <B>x&amp;y</B>\n  <C/>\n</A>"
        );
    }

    #[test]
    fn reader_walks_children() {
        let src =
            b"<?xml version='1.0'?><!-- c --><x:A xmlns:x='u' a=\"1 &gt; 0\">\
                    <B>t<![CDATA[<raw>]]></B><C/></x:A>";
        let mut r = Reader::new(src).unwrap();
        let root = r.root().unwrap();
        assert_eq!(root.local_name(), "A");
        let attrs: Vec<_> = root.attrs().map(|a| a.unwrap()).collect();
        assert_eq!(attrs, vec![("xmlns:x", "u"), ("a", "1 &gt; 0")]);
        assert_eq!(r.peek_child().unwrap(), Some("B"));
        let b: String = r.read_leaf().unwrap();
        assert_eq!(b, "t<raw>");
        assert_eq!(r.peek_child().unwrap(), Some("C"));
        r.open().unwrap();
        r.close().unwrap();
        assert_eq!(r.peek_child().unwrap(), None);
        r.close().unwrap();
        r.finish().unwrap();
    }
}
