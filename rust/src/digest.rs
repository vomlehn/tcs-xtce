//! An MD5 digest of what an XTCE document means.
//!
//! Two programs that exchange packets were built from the same packet
//! definitions if their XTCE documents have the same digest, so each can
//! send the other its digest and compare, rather than describing the data
//! in every message.  The Python (`tcs_xtce.digest`) and C/C++
//! (`xtce-digest.h`) runtimes compute the same digest; the Python module
//! describes exactly what it covers.  In short, it leaves out comments and
//! processing instructions, whitespace between elements and around text,
//! the order of attributes, namespace prefixes, and the order of the
//! children of elements that hold unordered collections ([`UNORDERED`]).
//!
//! The document is read as XML 1.0 in UTF-8.  It is an error for it to be
//! malformed, to declare another encoding, to have a DOCTYPE, or to have a
//! root element other than `SpaceSystem`.  Computing the digest allocates
//! memory, so do it at start-up, not in code that must not allocate.
//!
//! A mission whose definitions are split across several documents has one
//! digest for the set ([`digest_set`], [`digest_files`]): the MD5 digest of
//! the documents' encodings, sorted by their bytes and concatenated.  It
//! doesn't depend on the order the documents are given in, and the digest
//! of a set of one document is that document's digest.

use std::collections::HashMap;
use std::path::Path;

use crate::xml::{Error, Result};

/// Elements whose children may appear in any order without changing what
/// the document means.  Every runtime has the same list.
pub const UNORDERED: &[&str] = &[
    // A SpaceSystem's nested SpaceSystems.
    "SpaceSystem",
    // Sets.
    "AlgorithmSet",
    "AliasSet",
    "AncillaryDataSet",
    "ArgumentTypeSet",
    "AuthorSet",
    "CommandContainerSet",
    "ContainerRefSet",
    "ContainerSet",
    "ExternalAlgorithmSet",
    "HistorySet",
    "MessageRefSet",
    "MessageSet",
    "MetaCommandSet",
    "NoteSet",
    "ParameterSet",
    "ParameterTypeSet",
    "ParametersToSuspendAlarmsOnSet",
    "RateInStreamSet",
    "ServiceSet",
    "StreamSet",
    "TriggerSet",
    "UnitSet",
    "ValidRangeSet",
    "VerifierSet",
    // Lists whose items combine without regard to order.
    "ANDedConditions",
    "ArgumentAssignmentList",
    "ComparisonList",
    "EnumerationAlarmList",
    "EnumerationList",
    "ORedConditions",
    "PolynomialCalibrator",
];

const XML_NS: &str = "http://www.w3.org/XML/1998/namespace";

/// Computes the MD5 digest of the XTCE document `data`, as 16 bytes: what
/// programs send each other to compare.
///
/// # Arguments
///
/// * `data` - The document's bytes, UTF-8.
///
/// # Returns
///
/// The digest.
///
/// # Errors
///
/// Fails, as [`encoding`] does, if the document is malformed or isn't
/// one this module reads (see the module documentation).
pub fn digest_bytes(data: &[u8]) -> Result<[u8; 16]> {
    Ok(md5(&encoding(data)?))
}

/// The MD5 digest of the XTCE document `data` (UTF-8 bytes), in lowercase
/// hex.
pub fn digest(data: &[u8]) -> Result<String> {
    Ok(hex(&digest_bytes(data)?))
}

/// The digest of the XTCE document `text`, in lowercase hex.
pub fn digest_string(text: &str) -> Result<String> {
    digest(text.as_bytes())
}

/// Computes the digest of the XTCE document in the file `path`, as 16
/// bytes.
///
/// # Arguments
///
/// * `path` - The file holding the document.
///
/// # Returns
///
/// The digest.
///
/// # Errors
///
/// Fails with an error naming `path` if the file can't be read, and
/// as [`digest_bytes`] does on its contents.
pub fn digest_file_bytes(path: impl AsRef<Path>) -> Result<[u8; 16]> {
    let path = path.as_ref();
    let data = std::fs::read(path)
        .map_err(|e| Error::new(format!("{}: {e}", path.display())))?;
    digest_bytes(&data)
}

/// The digest of the XTCE document in the file `path`, in lowercase hex.
pub fn digest_file(path: impl AsRef<Path>) -> Result<String> {
    Ok(hex(&digest_file_bytes(path)?))
}

/// Computes the MD5 digest of the set of XTCE documents `documents` (each
/// UTF-8 bytes), as 16 bytes.
///
/// # Arguments
///
/// * `documents` - The documents' bytes, in any order.
///
/// # Returns
///
/// The digest of the set.
///
/// # Errors
///
/// Fails, as [`encoding`] does, on the first document it can't read.
pub fn digest_set_bytes<D: AsRef<[u8]>>(documents: &[D]) -> Result<[u8; 16]> {
    let mut codes = documents
        .iter()
        .map(|d| encoding(d.as_ref()))
        .collect::<Result<Vec<_>>>()?;
    codes.sort();
    Ok(md5(&codes.concat()))
}

/// The digest of the set of XTCE documents `documents`, in lowercase hex.
pub fn digest_set<D: AsRef<[u8]>>(documents: &[D]) -> Result<String> {
    Ok(hex(&digest_set_bytes(documents)?))
}

/// Computes the digest of the set of XTCE documents in the files `paths`,
/// as 16 bytes.
///
/// # Arguments
///
/// * `paths` - The files holding the documents, in any order.
///
/// # Returns
///
/// The digest of the set.
///
/// # Errors
///
/// Fails with an error naming the first file that can't be read, and
/// as [`digest_set_bytes`] does on their contents.
pub fn digest_files_bytes<P: AsRef<Path>>(paths: &[P]) -> Result<[u8; 16]> {
    let documents = paths
        .iter()
        .map(|p| {
            let p = p.as_ref();
            std::fs::read(p)
                .map_err(|e| Error::new(format!("{}: {e}", p.display())))
        })
        .collect::<Result<Vec<_>>>()?;
    digest_set_bytes(&documents)
}

/// The digest of the set of XTCE documents in the files `paths`, in
/// lowercase hex.
pub fn digest_files<P: AsRef<Path>>(paths: &[P]) -> Result<String> {
    Ok(hex(&digest_files_bytes(paths)?))
}

/// 16 bytes as lowercase hex.
pub fn hex(d: &[u8; 16]) -> String {
    d.iter().map(|b| format!("{b:02x}")).collect()
}

/// Computes the canonical encoding of the XTCE document `data`, whose MD5
/// digest is its digest.
///
/// # Arguments
///
/// * `data` - The document's bytes, UTF-8 with or without a byte order
///            mark.
///
/// # Returns
///
/// The encoding.
///
/// # Errors
///
/// Fails if the document isn't UTF-8, holds a character XML forbids, is
/// malformed, declares another encoding, has a DOCTYPE or has a root
/// element other than `SpaceSystem`.
pub fn encoding(data: &[u8]) -> Result<Vec<u8>> {
    let data = data.strip_prefix(b"\xef\xbb\xbf").unwrap_or(data);
    let text = std::str::from_utf8(data)
        .map_err(|_| Error::new("the document is not UTF-8"))?;
    if let Some(c) = text.chars().find(|&c| !is_xml_char(c)) {
        return Err(Error::new(format!(
            "character U+{:04X} is not allowed in XML",
            c as u32
        )));
    }
    // XML normalizes every line end to a line feed before anything else.
    let text = text.replace("\r\n", "\n").replace('\r', "\n");
    let mut p = Parser {
        s: text.as_bytes(),
        pos: 0,
        scopes: Vec::new(),
    };
    p.prolog()?;
    if !p.s[p.pos..].starts_with(b"<") {
        return Err(p.error("no root element"));
    }
    let root = p.element(true)?;
    p.misc()?;
    if p.pos != p.s.len() {
        return Err(p.error("content after the root element"));
    }
    Ok(root)
}

fn field(out: &mut Vec<u8>, data: &[u8]) {
    out.extend_from_slice(data.len().to_string().as_bytes());
    out.push(b':');
    out.extend_from_slice(data);
}

/// Whether XML allows the character `c` (the production Char).
fn is_xml_char(c: char) -> bool {
    matches!(c as u32, 0x9 | 0xA | 0xD | 0x20..=0xD7FF | 0xE000..=0xFFFD
        | 0x10000..=0x10FFFF)
}

fn is_space(c: u8) -> bool {
    matches!(c, b' ' | b'\t' | b'\n' | b'\r')
}

struct Parser<'a> {
    s: &'a [u8],
    pos: usize,
    /// Namespace declarations of the open elements: prefix ("" for the
    /// default namespace) to URI ("" when undeclared).
    scopes: Vec<HashMap<String, String>>,
}

impl<'a> Parser<'a> {
    /// Makes an error at the current position.
    ///
    /// # Arguments
    ///
    /// * `message` - What is wrong.
    ///
    /// # Returns
    ///
    /// The error, with the 1-based line and column of the position.
    fn error(&self, message: &str) -> Error {
        let before = &self.s[..self.pos.min(self.s.len())];
        let line = before.iter().filter(|&&c| c == b'\n').count() + 1;
        let column =
            before.iter().rev().take_while(|&&c| c != b'\n').count() + 1;
        Error {
            message: message.into(),
            line,
            column,
        }
    }

    fn rest(&self) -> &'a [u8] {
        &self.s[self.pos..]
    }

    fn skip_space(&mut self) {
        while self.pos < self.s.len() && is_space(self.s[self.pos]) {
            self.pos += 1;
        }
    }

    /// Skips past `end`, failing with `what` if it never comes.
    ///
    /// # Arguments
    ///
    /// * `end`  - The bytes that end what is skipped.
    /// * `what` - The error message if `end` doesn't appear.
    ///
    /// # Returns
    ///
    /// The bytes skipped, `end` excluded.
    ///
    /// # Errors
    ///
    /// Fails with `what` if `end` doesn't appear.
    fn skip_past(&mut self, end: &[u8], what: &str) -> Result<&'a [u8]> {
        let rest = self.rest();
        match rest.windows(end.len()).position(|w| w == end) {
            Some(i) => {
                self.pos += i + end.len();
                Ok(&rest[..i])
            }
            None => Err(self.error(what)),
        }
    }

    /// Skips the XML declaration, and the comments, processing instructions
    /// and whitespace before the root element.
    ///
    /// # Errors
    ///
    /// Fails if one is unclosed, the declaration names an encoding other
    /// than UTF-8, or a DOCTYPE follows.
    fn prolog(&mut self) -> Result<()> {
        if self.rest().starts_with(b"<?xml")
            && self.rest().get(5).is_some_and(|&c| is_space(c))
        {
            self.pos += 5;
            let decl = self.skip_past(b"?>", "unclosed XML declaration")?;
            let decl = std::str::from_utf8(decl).unwrap_or("");
            if let Some(enc) = pseudo_attr(decl, "encoding") {
                let e = enc.to_ascii_lowercase();
                if e != "utf-8" && e != "utf8" {
                    return Err(Error::new(format!(
                        "encoding {enc} is not UTF-8"
                    )));
                }
            }
        }
        self.misc()?;
        if self.rest().starts_with(b"<!DOCTYPE") {
            return Err(
                self.error("documents with a DOCTYPE are not supported")
            );
        }
        Ok(())
    }

    /// Skips comments, processing instructions and whitespace.
    ///
    /// # Errors
    ///
    /// Fails if a comment or processing instruction is unclosed.
    fn misc(&mut self) -> Result<()> {
        loop {
            self.skip_space();
            if self.rest().starts_with(b"<!--") {
                self.pos += 4;
                self.skip_past(b"-->", "unclosed comment")?;
            } else if self.rest().starts_with(b"<?") {
                self.pos += 2;
                self.skip_past(b"?>", "unclosed processing instruction")?;
            } else {
                return Ok(());
            }
        }
    }

    /// Reads a name: element, attribute or end tag.
    ///
    /// # Returns
    ///
    /// The name.
    ///
    /// # Errors
    ///
    /// Fails if no name is at the current position.
    fn name(&mut self) -> Result<&'a str> {
        let start = self.pos;
        while self.pos < self.s.len()
            && !is_space(self.s[self.pos])
            && !b"/>=<\"'&".contains(&self.s[self.pos])
        {
            self.pos += 1;
        }
        if self.pos == start {
            return Err(self.error("expected a name"));
        }
        // The bytes come from a str and stop at ASCII, so they are UTF-8.
        Ok(std::str::from_utf8(&self.s[start..self.pos]).unwrap_or(""))
    }

    /// Finds the namespace a prefix is bound to in the open elements.
    ///
    /// # Arguments
    ///
    /// * `prefix` - The prefix, "" for the default namespace.
    ///
    /// # Returns
    ///
    /// The URI ("" when undeclared), or `None` if `prefix` is not bound.
    fn lookup(&self, prefix: &str) -> Option<&str> {
        if prefix == "xml" {
            return Some(XML_NS);
        }
        self.scopes
            .iter()
            .rev()
            .find_map(|m| m.get(prefix))
            .map(|s| s.as_str())
    }

    /// A name in Clark notation, `{namespace}local` or `local`.  An
    /// unprefixed attribute is in no namespace.
    ///
    /// # Arguments
    ///
    /// * `qname` - The name as written, perhaps with a prefix.
    /// * `attr`  - Whether it is an attribute's name.
    ///
    /// # Returns
    ///
    /// The expanded name.
    ///
    /// # Errors
    ///
    /// Fails if its prefix is unbound.
    fn expand(&self, qname: &str, attr: bool) -> Result<String> {
        let (prefix, local) = match qname.split_once(':') {
            Some((p, l)) => (p, l),
            None => ("", qname),
        };
        if prefix.is_empty() && attr {
            return Ok(local.to_string());
        }
        match self.lookup(prefix) {
            Some(uri) if !uri.is_empty() => Ok(format!("{{{uri}}}{local}")),
            _ if prefix.is_empty() => Ok(local.to_string()),
            _ => Err(self.error(&format!("unbound prefix {prefix:?}"))),
        }
    }

    /// Expands the references in `raw`; in an attribute value, whitespace
    /// characters become spaces.
    ///
    /// # Arguments
    ///
    /// * `raw`  - The text as written.
    /// * `attr` - Whether it is an attribute value.
    ///
    /// # Returns
    ///
    /// The text.
    ///
    /// # Errors
    ///
    /// Fails on an unterminated or undefined reference or, in an attribute
    /// value, a `<`.
    fn unescape(&self, raw: &[u8], attr: bool) -> Result<String> {
        let mut out = String::new();
        let text = std::str::from_utf8(raw).unwrap_or("");
        let mut rest = text;
        while let Some(i) = rest.find(|c| c == '&' || (attr && c == '<')) {
            push_text(&mut out, &rest[..i], attr);
            if rest.as_bytes()[i] == b'<' {
                return Err(self.error("'<' in an attribute value"));
            }
            let end = rest[i..]
                .find(';')
                .ok_or_else(|| self.error("unterminated reference"))?;
            let r = &rest[i + 1..i + end];
            let c = match r {
                "lt" => '<',
                "gt" => '>',
                "amp" => '&',
                "quot" => '"',
                "apos" => '\'',
                _ => {
                    let code = if let Some(h) =
                        r.strip_prefix("#x").or_else(|| r.strip_prefix("#X"))
                    {
                        u32::from_str_radix(h, 16).ok()
                    } else if let Some(d) = r.strip_prefix('#') {
                        d.parse().ok()
                    } else {
                        None
                    };
                    code.and_then(char::from_u32)
                        .filter(|&c| is_xml_char(c))
                        .ok_or_else(|| {
                            self.error(&format!("undefined entity &{r};"))
                        })?
                }
            };
            out.push(c);
            rest = &rest[i + end + 1..];
        }
        push_text(&mut out, rest, attr);
        Ok(out)
    }

    /// Reads an element, from its `<`, as its encoding.
    ///
    /// # Arguments
    ///
    /// * `root` - Whether it is the root element, which must be
    ///            `SpaceSystem`.
    ///
    /// # Returns
    ///
    /// The encoding.
    ///
    /// # Errors
    ///
    /// Fails if the element or its content is malformed, uses an unbound
    /// prefix or repeats an attribute, or if `root` is set and the element
    /// is not `SpaceSystem`.
    fn element(&mut self, root: bool) -> Result<Vec<u8>> {
        self.pos += 1;
        let qname = self.name()?;
        let mut raw: Vec<(&'a str, String)> = Vec::new();
        let mut decls = HashMap::new();
        let empty = loop {
            let before = self.pos;
            self.skip_space();
            if self.rest().starts_with(b"/>") {
                self.pos += 2;
                break true;
            }
            if self.rest().starts_with(b">") {
                self.pos += 1;
                break false;
            }
            if self.pos == before {
                return Err(self.error("expected whitespace or '>'"));
            }
            let name = self.name()?;
            self.skip_space();
            if !self.rest().starts_with(b"=") {
                return Err(self.error("expected '='"));
            }
            self.pos += 1;
            self.skip_space();
            let q = *self
                .rest()
                .first()
                .ok_or_else(|| self.error("unexpected end"))?;
            if q != b'"' && q != b'\'' {
                return Err(self.error("expected a quoted value"));
            }
            self.pos += 1;
            let value = self.skip_past(&[q], "unterminated attribute value")?;
            let value = self.unescape(value, true)?;
            if name == "xmlns" {
                decls.insert(String::new(), value);
            } else if let Some(p) = name.strip_prefix("xmlns:") {
                decls.insert(p.to_string(), value);
            } else {
                raw.push((name, value));
            }
        };
        self.scopes.push(decls);
        let tag = self.expand(qname, false)?;
        let local = tag.rsplit('}').next().unwrap_or("");
        if root && local != "SpaceSystem" {
            return Err(self.error("the root element is not SpaceSystem"));
        }
        let mut attrs = Vec::new();
        for (name, value) in raw {
            attrs.push((self.expand(name, true)?.into_bytes(), value));
        }
        attrs.sort();
        if attrs.windows(2).any(|w| w[0].0 == w[1].0) {
            return Err(self.error("duplicate attribute"));
        }
        let mut items: Vec<Vec<u8>> = Vec::new();
        if !empty {
            self.content(qname, &mut items)?;
        }
        self.scopes.pop();
        if UNORDERED.contains(&local) {
            items.sort();
        }
        let mut a = Vec::new();
        for (k, v) in &attrs {
            field(&mut a, k);
            field(&mut a, v.as_bytes());
        }
        let mut out = vec![b'E'];
        field(&mut out, tag.as_bytes());
        field(&mut out, &a);
        field(&mut out, &items.concat());
        Ok(out)
    }

    /// Reads an element's content, up to and including its end tag.
    ///
    /// # Arguments
    ///
    /// * `qname` - The element's name as written in its start tag.
    /// * `items` - Receives the encoding of each child element and run of
    ///             text.
    ///
    /// # Errors
    ///
    /// Fails if the content is malformed or the end tag doesn't match.
    fn content(&mut self, qname: &str, items: &mut Vec<Vec<u8>>) -> Result<()> {
        let mut text = String::new();
        loop {
            let rest = self.rest();
            let i = rest
                .iter()
                .position(|&c| c == b'<')
                .ok_or_else(|| self.error("unclosed element"))?;
            let chunk = self.unescape(&rest[..i], false)?;
            text.push_str(&chunk);
            self.pos += i;
            let rest = self.rest();
            if rest.starts_with(b"<!--") {
                self.pos += 4;
                self.skip_past(b"-->", "unclosed comment")?;
            } else if rest.starts_with(b"<![CDATA[") {
                self.pos += 9;
                let data = self.skip_past(b"]]>", "unclosed CDATA section")?;
                text.push_str(std::str::from_utf8(data).unwrap_or(""));
            } else if rest.starts_with(b"<?") {
                self.pos += 2;
                self.skip_past(b"?>", "unclosed processing instruction")?;
            } else if rest.starts_with(b"</") {
                self.pos += 2;
                let end = self.name()?;
                if end != qname {
                    return Err(self.error("mismatched tag"));
                }
                self.skip_space();
                if !self.rest().starts_with(b">") {
                    return Err(self.error("expected '>'"));
                }
                self.pos += 1;
                flush(&mut text, items);
                return Ok(());
            } else if rest.starts_with(b"<!") {
                return Err(self.error("unexpected markup declaration"));
            } else {
                flush(&mut text, items);
                items.push(self.element(false)?);
            }
        }
    }
}

/// Appends `s` to `out`; in an attribute value, tabs and line feeds become
/// spaces.
///
/// # Arguments
///
/// * `out`  - The string appended to.
/// * `s`    - The text to append.
/// * `attr` - Whether the text is in an attribute value.
fn push_text(out: &mut String, s: &str, attr: bool) {
    if attr {
        out.extend(s.chars().map(|c| if is_space_char(c) { ' ' } else { c }));
    } else {
        out.push_str(s);
    }
}

fn is_space_char(c: char) -> bool {
    matches!(c, ' ' | '\t' | '\n' | '\r')
}

/// Ends a run of text: its encoding, if anything is left once whitespace
/// is trimmed from both ends.
///
/// # Arguments
///
/// * `text`  - The run of text, which is cleared.
/// * `items` - Receives the run's encoding.
fn flush(text: &mut String, items: &mut Vec<Vec<u8>>) {
    let t = text.trim_matches(is_space_char);
    if !t.is_empty() {
        let mut out = vec![b'T'];
        field(&mut out, t.as_bytes());
        items.push(out);
    }
    text.clear();
}

/// Finds the value of `name="..."` in an XML declaration.
///
/// # Arguments
///
/// * `decl` - The declaration's text, between `<?xml` and `?>`.
/// * `name` - The pseudo-attribute's name.
///
/// # Returns
///
/// The value, without its quotes, or `None` if it isn't there.
fn pseudo_attr<'s>(decl: &'s str, name: &str) -> Option<&'s str> {
    let i = decl.find(name)?;
    let rest = decl[i + name.len()..].trim_start();
    let rest = rest.strip_prefix('=')?.trim_start();
    let q = rest.chars().next()?;
    if q != '"' && q != '\'' {
        return None;
    }
    let rest = &rest[1..];
    rest.find(q).map(|j| &rest[..j])
}

// --- MD5 (RFC 1321) ----------------------------------------------------------

/// Computes the MD5 digest of `data`.
///
/// # Arguments
///
/// * `data` - The bytes to digest.
///
/// # Returns
///
/// The digest, 16 bytes.
pub fn md5(data: &[u8]) -> [u8; 16] {
    const S: [u32; 64] = [
        7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 5, 9, 14,
        20, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20, 4, 11, 16, 23, 4, 11, 16,
        23, 4, 11, 16, 23, 4, 11, 16, 23, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10,
        15, 21, 6, 10, 15, 21,
    ];
    // K[i] = floor(abs(sin(i + 1)) * 2^32), written out so the result
    // doesn't depend on a math library.
    const K: [u32; 64] = [
        0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee, 0xf57c0faf, 0x4787c62a,
        0xa8304613, 0xfd469501, 0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be,
        0x6b901122, 0xfd987193, 0xa679438e, 0x49b40821, 0xf61e2562, 0xc040b340,
        0x265e5a51, 0xe9b6c7aa, 0xd62f105d, 0x02441453, 0xd8a1e681, 0xe7d3fbc8,
        0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed, 0xa9e3e905, 0xfcefa3f8,
        0x676f02d9, 0x8d2a4c8a, 0xfffa3942, 0x8771f681, 0x6d9d6122, 0xfde5380c,
        0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70, 0x289b7ec6, 0xeaa127fa,
        0xd4ef3085, 0x04881d05, 0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665,
        0xf4292244, 0x432aff97, 0xab9423a7, 0xfc93a039, 0x655b59c3, 0x8f0ccc92,
        0xffeff47d, 0x85845dd1, 0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1,
        0xf7537e82, 0xbd3af235, 0x2ad7d2bb, 0xeb86d391,
    ];
    let mut msg = data.to_vec();
    let bits = (data.len() as u64).wrapping_mul(8);
    msg.push(0x80);
    while msg.len() % 64 != 56 {
        msg.push(0);
    }
    msg.extend_from_slice(&bits.to_le_bytes());
    let (mut a0, mut b0, mut c0, mut d0) =
        (0x67452301u32, 0xefcdab89u32, 0x98badcfeu32, 0x10325476u32);
    for chunk in msg.chunks(64) {
        let m: Vec<u32> = chunk
            .chunks(4)
            .map(|w| u32::from_le_bytes([w[0], w[1], w[2], w[3]]))
            .collect();
        let (mut a, mut b, mut c, mut d) = (a0, b0, c0, d0);
        for i in 0..64 {
            let (f, g) = match i / 16 {
                0 => ((b & c) | (!b & d), i),
                1 => ((d & b) | (!d & c), (5 * i + 1) % 16),
                2 => (b ^ c ^ d, (3 * i + 5) % 16),
                _ => (c ^ (b | !d), (7 * i) % 16),
            };
            let f = f.wrapping_add(a).wrapping_add(K[i]).wrapping_add(m[g]);
            a = d;
            d = c;
            c = b;
            b = b.wrapping_add(f.rotate_left(S[i]));
        }
        a0 = a0.wrapping_add(a);
        b0 = b0.wrapping_add(b);
        c0 = c0.wrapping_add(c);
        d0 = d0.wrapping_add(d);
    }
    let mut out = [0u8; 16];
    for (i, v) in [a0, b0, c0, d0].iter().enumerate() {
        out[4 * i..4 * i + 4].copy_from_slice(&v.to_le_bytes());
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn md5_matches_rfc_1321() {
        assert_eq!(hex(&md5(b"")), "d41d8cd98f00b204e9800998ecf8427e");
        assert_eq!(hex(&md5(b"abc")), "900150983cd24fb0d6963f7d28e17f72");
        assert_eq!(
            hex(&md5(b"12345678901234567890123456789012345678901234567890\
                       123456789012345678901234567890")),
            "57edf4a22be3c955ac49da2e2107b67a"
        );
    }

    #[test]
    fn encoding_ignores_what_does_not_change_the_meaning() {
        let a = "<SpaceSystem xmlns='u' name='S' b='2'><x>hi</x></SpaceSystem>";
        let b = "<?xml version='1.0'?>\n<!-- c -->\n<p:SpaceSystem \
                 xmlns:p='u' b='2' name='S'>\n  <p:x> hi <!-- c --></p:x>\n\
                 </p:SpaceSystem>";
        assert_eq!(digest_string(a).unwrap(), digest_string(b).unwrap());
        assert_eq!(
            encoding(a.as_bytes()).unwrap(),
            b"E14:{u}SpaceSystem15:1:b1:24:name1:S16:E4:{u}x0:5:T2:hi"
        );
    }

    #[test]
    fn set_digest_is_of_the_sorted_encodings() {
        let a = "<SpaceSystem name='A'/>";
        let b = "<SpaceSystem name='B'/>";
        let mut both = encoding(b.as_bytes()).unwrap();
        both.extend(encoding(a.as_bytes()).unwrap());
        assert_eq!(digest_set(&[a]).unwrap(), digest_string(a).unwrap());
        assert_eq!(digest_set(&[b, a]).unwrap(), digest_set(&[a, b]).unwrap());
        assert_ne!(digest_set(&[a, b]).unwrap(), hex(&md5(&both)));
        assert!(digest_set(&[a, "<other/>"]).is_err());
    }

    #[test]
    fn rejects_what_it_cannot_read() {
        for bad in [
            "<SpaceSystem",
            "<other/>",
            "<!DOCTYPE SpaceSystem><SpaceSystem/>",
            "<?xml version='1.0' encoding='ISO-8859-1'?><SpaceSystem/>",
            "<SpaceSystem><a></b></SpaceSystem>",
            "<SpaceSystem p:a='1'/>",
            "<SpaceSystem name='&#1;'/>",
            "<SpaceSystem name='\u{1}'/>",
        ] {
            assert!(digest_string(bad).is_err(), "{bad}");
        }
    }
}
