//! The resolved model as canonical text, as the Python compiler's
//! `--dump-model` writes it, so that the two can be compared line for line
//! while the compiler moves to Rust (see `docs/rust-migration-design.rst`).
//!
//! The text has one value to a line, indented two spaces for each level:
//!
//! - `none`, `true` and `false`; integers in decimal; floats as Python's
//!   `repr` writes them (`1.0`, `1e+16`, `nan`, `inf`); strings in double
//!   quotes, with `\`, `"`, newline, carriage return and tab escaped as in
//!   C and any other control character as `\xHH`; bytes as `bytes` and
//!   their hexadecimal, or `bytes (empty)`.
//! - A record is its class name and `{`, then each field as `name: value`
//!   in the order the Python class declares them, then `}`.
//! - A list is `[`, each element, then `]`; `[]` when empty.
//! - A dictionary is `{`, each entry as `key => value` in its order, then
//!   `}`; `{}` when empty.
//! - A record that compares by identity (a [`Shared`] one) and is reached
//!   more than once is numbered where it is first written, `Node #3 {`,
//!   and written as `Node #3` wherever it is reached again. Any other
//!   record is a value, written in full wherever it is reached; one
//!   reached again while it is being written is written there as its class
//!   name and `^`.
//! - An element of the XTCE document that the model keeps, to bind later,
//!   is `xtce` and its class name.
//!
//! Numbers are given in the order records are first written.
//!
//! The model is first turned into a [`Tree`], which the two passes of the
//! Python dumper then walk: one counting how often each shared record is
//! reached, one writing.

use std::cell::RefCell;
use std::collections::{HashMap, HashSet};
use std::rc::Rc;

use num_bigint::BigInt;

/// A record that compares by identity: the Python model's dataclasses with
/// `eq=False`, which are shared where the model links one thing to
/// another, and may be changed after they are made.
#[derive(Debug, Default)]
pub struct Shared<T>(pub Rc<RefCell<T>>);

impl<T> Clone for Shared<T> {
    fn clone(&self) -> Self {
        Shared(Rc::clone(&self.0))
    }
}

impl<T> Shared<T> {
    /// Makes a record shareable.
    ///
    /// # Arguments
    ///
    /// * `value` - The record.
    ///
    /// # Returns
    ///
    /// The one reference to it so far.
    pub fn new(value: T) -> Self {
        Shared(Rc::new(RefCell::new(value)))
    }

    /// Borrows the record.
    ///
    /// # Returns
    ///
    /// A reference to it, until the guard is dropped.
    ///
    /// # Panics
    ///
    /// If it is being changed.
    pub fn get(&self) -> std::cell::Ref<'_, T> {
        self.0.borrow()
    }

    /// Borrows the record to change it.
    ///
    /// # Returns
    ///
    /// A mutable reference to it, until the guard is dropped.
    ///
    /// # Panics
    ///
    /// If it is borrowed elsewhere.
    pub fn get_mut(&self) -> std::cell::RefMut<'_, T> {
        self.0.borrow_mut()
    }

    /// Says whether two references are to the one record.
    ///
    /// # Arguments
    ///
    /// * `other` - The other reference.
    ///
    /// # Returns
    ///
    /// Whether they are.
    pub fn same(&self, other: &Self) -> bool {
        Rc::ptr_eq(&self.0, &other.0)
    }

    /// Gives the record's address, which tells it from every other while
    /// it lives.
    ///
    /// # Returns
    ///
    /// The address.
    pub fn addr(&self) -> usize {
        Rc::as_ptr(&self.0) as *const () as usize
    }
}

/// A value record that the compiler changes after placing it, so that
/// what holds it sees the change: written as a value, in full, wherever
/// it is reached.
#[derive(Debug, Default)]
pub struct Mut<T>(pub Rc<RefCell<T>>);

impl<T> Clone for Mut<T> {
    fn clone(&self) -> Self {
        Mut(Rc::clone(&self.0))
    }
}

impl<T> Mut<T> {
    /// Makes a value record shareable.
    ///
    /// # Arguments
    ///
    /// * `value` - The record.
    ///
    /// # Returns
    ///
    /// The one reference to it so far.
    pub fn new(value: T) -> Self {
        Mut(Rc::new(RefCell::new(value)))
    }

    /// Borrows the record.
    ///
    /// # Returns
    ///
    /// A reference to it, until the guard is dropped.
    pub fn get(&self) -> std::cell::Ref<'_, T> {
        self.0.borrow()
    }

    /// Borrows the record to change it.
    ///
    /// # Returns
    ///
    /// A mutable reference to it, until the guard is dropped.
    pub fn get_mut(&self) -> std::cell::RefMut<'_, T> {
        self.0.borrow_mut()
    }

    /// Says whether two references are to the one record.
    ///
    /// # Arguments
    ///
    /// * `other` - The other reference.
    ///
    /// # Returns
    ///
    /// Whether they are.
    pub fn same(&self, other: &Self) -> bool {
        Rc::ptr_eq(&self.0, &other.0)
    }
}

impl<T: ToTree> ToTree for Mut<T> {
    fn tree(&self, cx: &mut Cx) -> Tree {
        self.0.borrow().tree(cx)
    }
}

/// An element of an XTCE document, kept in the model where it is bound to
/// a packet's values only later: the text gives its class name alone.
#[derive(Debug, Clone)]
pub struct Elem<T> {
    /// The element, as the document has it.
    pub value: T,
    /// The name of its class in the XML binding.
    pub class: &'static str,
}

/// A value as the canonical text has it, before it is written.
#[derive(Debug, Clone)]
pub enum Tree {
    /// Python's `None`.
    None,
    /// A boolean.
    Bool(bool),
    /// An integer, in decimal.
    Int(String),
    /// A float.
    Float(f64),
    /// A string.
    Str(String),
    /// Bytes.
    Bytes(Vec<u8>),
    /// A list or tuple.
    List(Vec<Tree>),
    /// A dictionary, its entries in order.
    Dict(Vec<(Tree, Tree)>),
    /// A value record: its class name and its fields in order.
    Record(&'static str, Vec<(&'static str, Tree)>),
    /// A shared record, by its address; its contents are in the table of
    /// shared records.
    Shared(usize),
    /// A value record reached again while it is being written: its class
    /// name.
    Above(&'static str),
    /// An element of the document: its class name.
    Elem(&'static str),
}

/// What turning the model into a [`Tree`] keeps track of.
#[derive(Default)]
pub struct Cx {
    /// Each shared record turned so far, by address: its class name and
    /// fields.
    shared: HashMap<usize, (&'static str, Vec<(&'static str, Tree)>)>,
    /// The value records being turned, by address and class, so that one
    /// reached inside itself is written as [`Tree::Above`].
    open: HashSet<(usize, &'static str)>,
}

impl Cx {
    /// Turns a value record into a tree.
    ///
    /// # Arguments
    ///
    /// * `addr`   - Its address.
    /// * `class`  - Its class name.
    /// * `fields` - Turns its fields, in order.
    ///
    /// # Returns
    ///
    /// The record, or [`Tree::Above`] if it is being turned already.
    pub fn record(
        &mut self,
        addr: usize,
        class: &'static str,
        fields: impl FnOnce(&mut Cx) -> Vec<(&'static str, Tree)>,
    ) -> Tree {
        if !self.open.insert((addr, class)) {
            return Tree::Above(class);
        }
        let f = fields(self);
        self.open.remove(&(addr, class));
        Tree::Record(class, f)
    }

    /// Turns a shared record into a tree, the first time it is reached.
    ///
    /// # Arguments
    ///
    /// * `addr`   - Its address.
    /// * `class`  - Its class name.
    /// * `fields` - Turns its fields, in order.
    ///
    /// # Returns
    ///
    /// A reference to it.
    pub fn shared(
        &mut self,
        addr: usize,
        class: &'static str,
        fields: impl FnOnce(&mut Cx) -> Vec<(&'static str, Tree)>,
    ) -> Tree {
        if !self.shared.contains_key(&addr) {
            // Held while its fields are turned, so that a loop back to it
            // ends at it.
            self.shared.insert(addr, (class, Vec::new()));
            let f = fields(self);
            self.shared.insert(addr, (class, f));
        }
        Tree::Shared(addr)
    }
}

/// A part of the model that the canonical text writes.
pub trait ToTree {
    /// Turns it into a tree.
    ///
    /// # Arguments
    ///
    /// * `cx` - What the turning keeps track of.
    ///
    /// # Returns
    ///
    /// Its tree.
    fn tree(&self, cx: &mut Cx) -> Tree;
}

impl ToTree for bool {
    fn tree(&self, _: &mut Cx) -> Tree {
        Tree::Bool(*self)
    }
}

impl ToTree for i64 {
    fn tree(&self, _: &mut Cx) -> Tree {
        Tree::Int(self.to_string())
    }
}

impl ToTree for BigInt {
    fn tree(&self, _: &mut Cx) -> Tree {
        Tree::Int(self.to_string())
    }
}

impl ToTree for f64 {
    fn tree(&self, _: &mut Cx) -> Tree {
        Tree::Float(*self)
    }
}

impl ToTree for String {
    fn tree(&self, _: &mut Cx) -> Tree {
        Tree::Str(self.clone())
    }
}

impl ToTree for Vec<u8> {
    fn tree(&self, _: &mut Cx) -> Tree {
        Tree::Bytes(self.clone())
    }
}

impl<T: ToTree> ToTree for std::cell::RefCell<T> {
    fn tree(&self, cx: &mut Cx) -> Tree {
        self.borrow().tree(cx)
    }
}

impl<T: ToTree> ToTree for Option<T> {
    fn tree(&self, cx: &mut Cx) -> Tree {
        match self {
            Some(v) => v.tree(cx),
            None => Tree::None,
        }
    }
}

impl<T: ToTree> ToTree for Vec<T> {
    fn tree(&self, cx: &mut Cx) -> Tree {
        Tree::List(self.iter().map(|v| v.tree(cx)).collect())
    }
}

impl<T: ToTree + ?Sized> ToTree for Box<T> {
    fn tree(&self, cx: &mut Cx) -> Tree {
        (**self).tree(cx)
    }
}

impl<T: ToTree + ?Sized> ToTree for Rc<T> {
    fn tree(&self, cx: &mut Cx) -> Tree {
        (**self).tree(cx)
    }
}

impl<A: ToTree, B: ToTree> ToTree for (A, B) {
    fn tree(&self, cx: &mut Cx) -> Tree {
        Tree::List(vec![self.0.tree(cx), self.1.tree(cx)])
    }
}

impl<A: ToTree, B: ToTree, C: ToTree> ToTree for (A, B, C) {
    fn tree(&self, cx: &mut Cx) -> Tree {
        Tree::List(vec![self.0.tree(cx), self.1.tree(cx), self.2.tree(cx)])
    }
}

impl<T> ToTree for Elem<T> {
    fn tree(&self, _: &mut Cx) -> Tree {
        Tree::Elem(self.class)
    }
}

/// A dictionary that keeps the order its entries were added in, as
/// Python's does.
#[derive(Debug, Clone)]
pub struct Dict<K, V>(pub Vec<(K, V)>);

impl<K, V> Default for Dict<K, V> {
    fn default() -> Self {
        Dict(Vec::new())
    }
}

impl<K: ToTree, V: ToTree> ToTree for Dict<K, V> {
    fn tree(&self, cx: &mut Cx) -> Tree {
        Tree::Dict(
            self.0
                .iter()
                .map(|(k, v)| (k.tree(cx), v.tree(cx)))
                .collect(),
        )
    }
}

/// Implements [`ToTree`] for a value record, its fields named in the order
/// the Python class declares them.
#[macro_export]
macro_rules! value_record {
    ($t:ty, $class:literal, [$($f:ident $(as $n:literal)?),* $(,)?]) => {
        impl $crate::dump::ToTree for $t {
            fn tree(&self, cx: &mut $crate::dump::Cx) -> $crate::dump::Tree {
                let addr = self as *const Self as *const () as usize;
                cx.record(addr, $class, |cx| {
                    vec![$((
                        $crate::value_record!(@name $f $($n)?),
                        $crate::dump::ToTree::tree(&self.$f, cx),
                    )),*]
                })
            }
        }
    };
    (@name $f:ident $n:literal) => { $n };
    (@name $f:ident) => { stringify!($f) };
}

/// Implements [`ToTree`] for a shared record, as
/// [`value_record`](crate::value_record) does for a value one.
#[macro_export]
macro_rules! shared_record {
    ($t:ty, $class:literal, [$($f:ident $(as $n:literal)?),* $(,)?]) => {
        impl $crate::dump::ToTree for $crate::dump::Shared<$t> {
            fn tree(&self, cx: &mut $crate::dump::Cx) -> $crate::dump::Tree {
                let me = self.get();
                cx.shared(self.addr(), $class, |cx| {
                    vec![$((
                        $crate::value_record!(@name $f $($n)?),
                        $crate::dump::ToTree::tree(&me.$f, cx),
                    )),*]
                })
            }
        }
    };
}

/// Writes a string as the canonical text does.
///
/// # Arguments
///
/// * `s` - The string.
///
/// # Returns
///
/// It in double quotes, escaped.
pub fn string(s: &str) -> String {
    let mut out = String::from("\"");
    for ch in s.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '"' => out.push_str("\\\""),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                out.push_str(&format!("\\x{:02x}", c as u32))
            }
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

/// Writes a float as Python's `repr` does: the shortest digits that read
/// back as it, in positional notation when its decimal exponent is from
/// -4 to 15 and in scientific notation otherwise.
///
/// # Arguments
///
/// * `x` - The float.
///
/// # Returns
///
/// Its text: `1.0`, `0.0001`, `1e-05`, `1.5e+16`, `inf`, `-inf`, `nan`.
pub fn float(x: f64) -> String {
    if x.is_nan() {
        return "nan".into();
    }
    if x.is_infinite() {
        return if x > 0.0 { "inf" } else { "-inf" }.into();
    }
    // Rust's `{:e}` gives the shortest digits that read back exactly:
    // "d.ddde±x".
    let sci = format!("{:e}", x);
    let (mant, exp) = sci.split_once('e').expect("{:e} has an exponent");
    let exp: i32 = exp.parse().expect("{:e} has an integer exponent");
    let neg = mant.starts_with('-');
    let digits: String = mant.chars().filter(|c| c.is_ascii_digit()).collect();
    let sign = if neg { "-" } else { "" };
    if (-4..16).contains(&exp) {
        let n = digits.len() as i32;
        let body = if exp < 0 {
            format!("0.{}{}", "0".repeat((-exp - 1) as usize), digits)
        } else if exp + 1 >= n {
            format!("{}{}.0", digits, "0".repeat((exp + 1 - n) as usize))
        } else {
            let (a, b) = digits.split_at((exp + 1) as usize);
            format!("{a}.{b}")
        };
        return format!("{sign}{body}");
    }
    let m = if digits.len() == 1 {
        digits
    } else {
        format!("{}.{}", &digits[..1], &digits[1..])
    };
    let esign = if exp < 0 { '-' } else { '+' };
    format!("{sign}{m}e{esign}{:02}", exp.abs())
}

/// Writes a tree as canonical text, with the two passes of the Python
/// dumper.
struct Writer<'a> {
    /// The shared records, by address.
    shared: &'a HashMap<usize, (&'static str, Vec<(&'static str, Tree)>)>,
    /// How often each shared record is reached, from the first pass.
    seen: HashMap<usize, usize>,
    /// The number given to each shared record written so far.
    number: HashMap<usize, usize>,
    /// The text so far, a line to an element.
    lines: Vec<String>,
}

impl Writer<'_> {
    /// Counts how often each shared record is reached from a value.
    ///
    /// # Arguments
    ///
    /// * `t` - The value.
    fn count(&mut self, t: &Tree) {
        match t {
            Tree::Shared(a) => {
                let n = self.seen.entry(*a).or_insert(0);
                *n += 1;
                if *n == 1 {
                    for (_, v) in &self.shared[a].1 {
                        self.count(v);
                    }
                }
            }
            Tree::Record(_, fields) => {
                for (_, v) in fields {
                    self.count(v);
                }
            }
            Tree::List(items) => {
                for v in items {
                    self.count(v);
                }
            }
            Tree::Dict(entries) => {
                for (k, v) in entries {
                    self.count(k);
                    self.count(v);
                }
            }
            _ => {}
        }
    }

    /// Writes a value that takes no lines of its own.
    ///
    /// # Arguments
    ///
    /// * `t` - The value.
    ///
    /// # Returns
    ///
    /// Its text, or `None` if it is a record, list or dictionary.
    fn scalar(t: &Tree) -> Option<String> {
        Some(match t {
            Tree::None => "none".into(),
            Tree::Bool(b) => if *b { "true" } else { "false" }.into(),
            Tree::Int(s) => s.clone(),
            Tree::Float(x) => float(*x),
            Tree::Str(s) => string(s),
            Tree::Bytes(b) if b.is_empty() => "bytes (empty)".into(),
            Tree::Bytes(b) => format!(
                "bytes {}",
                b.iter().map(|x| format!("{x:02x}")).collect::<String>()
            ),
            Tree::Elem(class) => format!("xtce {class}"),
            Tree::Above(class) => format!("{class} ^"),
            _ => return None,
        })
    }

    /// Writes a dictionary key, which must fit on one line.
    ///
    /// # Arguments
    ///
    /// * `k` - The key: a scalar, a shared record written already, or a list of
    ///         scalars.
    ///
    /// # Returns
    ///
    /// Its text.
    ///
    /// # Panics
    ///
    /// If the key has no one-line form, which the Python dumper refuses
    /// too.
    fn key(&self, k: &Tree) -> String {
        if let Some(s) = Self::scalar(k) {
            return s;
        }
        match k {
            Tree::Shared(a) if self.number.contains_key(a) => {
                format!("{} #{}", self.shared[a].0, self.number[a])
            }
            Tree::List(items) => {
                let parts: Option<Vec<String>> =
                    items.iter().map(Self::scalar).collect();
                format!("({})", parts.expect("a key of scalars").join(", "))
            }
            _ => panic!("the model dump has no one-line form for {k:?}"),
        }
    }

    /// Writes a value on lines of its own.
    ///
    /// # Arguments
    ///
    /// * `depth` - Its indentation, in levels.
    /// * `head`  - What goes before it on its first line.
    /// * `t`     - The value.
    fn put(&mut self, depth: usize, head: &str, t: &Tree) {
        let pad = "  ".repeat(depth);
        if let Some(s) = Self::scalar(t) {
            self.lines.push(format!("{pad}{head}{s}"));
            return;
        }
        match t {
            Tree::Shared(a) => {
                let (class, fields) = &self.shared[a];
                if let Some(n) = self.number.get(a) {
                    self.lines.push(format!("{pad}{head}{class} #{n}"));
                    return;
                }
                let mut name = class.to_string();
                if self.seen.get(a).copied().unwrap_or(0) > 1 {
                    let n = self.number.len() + 1;
                    self.number.insert(*a, n);
                    name = format!("{name} #{n}");
                }
                self.fields(depth, head, &name, fields);
            }
            Tree::Record(class, fields) => {
                self.fields(depth, head, class, fields)
            }
            Tree::List(items) if items.is_empty() => {
                self.lines.push(format!("{pad}{head}[]"))
            }
            Tree::List(items) => {
                self.lines.push(format!("{pad}{head}["));
                for v in items {
                    self.put(depth + 1, "", v);
                }
                self.lines.push(format!("{pad}]"));
            }
            Tree::Dict(entries) if entries.is_empty() => {
                self.lines.push(format!("{pad}{head}{{}}"))
            }
            Tree::Dict(entries) => {
                self.lines.push(format!("{pad}{head}{{"));
                for (k, v) in entries {
                    let key = self.key(k);
                    self.put(depth + 1, &format!("{key} => "), v);
                }
                self.lines.push(format!("{pad}}}"));
            }
            _ => unreachable!("scalars are written above"),
        }
    }

    /// Writes a record's fields between its name and its closing brace.
    ///
    /// # Arguments
    ///
    /// * `depth`  - Its indentation, in levels.
    /// * `head`   - What goes before it on its first line.
    /// * `name`   - Its class name, numbered if it is shared.
    /// * `fields` - Its fields, in order.
    fn fields(
        &mut self,
        depth: usize,
        head: &str,
        name: &str,
        fields: &[(&'static str, Tree)],
    ) {
        let pad = "  ".repeat(depth);
        self.lines.push(format!("{pad}{head}{name} {{"));
        for (f, v) in fields {
            self.put(depth + 1, &format!("{f}: "), v);
        }
        self.lines.push(format!("{pad}}}"));
    }
}

/// Writes a part of the model as canonical text.
///
/// # Arguments
///
/// * `v` - The part: the mission, or anything inside it.
///
/// # Returns
///
/// The text, ending in a newline.
pub fn dump(v: &dyn ToTree) -> String {
    let mut cx = Cx::default();
    let tree = v.tree(&mut cx);
    let mut w = Writer {
        shared: &cx.shared,
        seen: HashMap::new(),
        number: HashMap::new(),
        lines: Vec::new(),
    };
    w.count(&tree);
    w.put(0, "", &tree);
    w.lines.join("\n") + "\n"
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn floats_are_written_as_python_does() {
        let cases = [
            (1.0, "1.0"),
            (1e16, "1e+16"),
            (1e-5, "1e-05"),
            (0.0001, "0.0001"),
            (1.5e16, "1.5e+16"),
            (123.456, "123.456"),
            (1e15, "1000000000000000.0"),
            (-0.0, "-0.0"),
            (0.1, "0.1"),
            (2.5e-7, "2.5e-07"),
            (1.2345678901234568e17, "1.2345678901234568e+17"),
            (f64::INFINITY, "inf"),
            (f64::NEG_INFINITY, "-inf"),
            (f64::NAN, "nan"),
            (1e300, "1e+300"),
        ];
        for (x, want) in cases {
            assert_eq!(float(x), want, "{x:?}");
        }
    }

    #[test]
    fn strings_are_quoted_and_escaped() {
        assert_eq!(string("a\"b\\c"), "\"a\\\"b\\\\c\"");
        assert_eq!(string("\n\r\t\x01\x7f"), "\"\\n\\r\\t\\x01\\x7f\"");
        assert_eq!(string("é"), "\"é\"");
    }
}
