//! The interpreter: decodes and encodes a packet by running its node's
//! items, as the code tcs-xtce generates for Rust runs them, through the
//! same tcsxtce helpers with the same arguments, so that the two give
//! the same values and the same bytes.
//!
//! What it can run so far is checked before it runs anything:
//! [`unsupported`] names the first construct a node uses that it can't,
//! and nothing is decoded or encoded for such a node.

use std::collections::HashMap;
use std::fmt::Write as _;
use std::rc::Rc;

use num_traits::ToPrimitive;
use tcs_xtce::buffer::Buffer;
use tcs_xtce::packet::{
    self as pk, BitReader, BitWriter, ByteOrder, Charset, FloatFormat,
    IntEncoding, PacketError, StringSpec,
};
use tcs_xtce_model::dump::Shared;
use tcs_xtce_model::model::{
    walk, CalCase, Calibrator, Check, Count, Criterion, DynSize, Encoding,
    Expr, Hook, Input, Item, Lit, Local, Member, Node, Num, Order, PathStep,
    Spec, Term, Transform, VType,
};

/// The name the interpreter gives the runtime's helpers for a field: the
/// runtime takes only names fixed when it is compiled, so the
/// interpreter names the field in its own error instead.
const F: &str = "field";

/// A function the application supplies for one of a mission's
/// algorithms, of the kind the algorithm's use needs: the same as the
/// code generated for the mission takes in its `hooks` module.
#[derive(Debug, Clone, Copy)]
pub enum Algorithm {
    /// A custom algorithm in a criterion: whether it holds, from its
    /// inputs.
    Criterion(fn(&[f64]) -> bool),
    /// A CustomAlgorithm's outputs from its inputs; false if it gives
    /// none.
    Outputs(fn(&[f64], &mut [f64]) -> bool),
    /// An alarm's level from its inputs.
    Alarm(fn(&[f64]) -> u8),
    /// A checksum of the bits from `start` to `end` of the data.
    Checksum(fn(&[u8], usize, usize) -> u64),
    /// Decodes a value's bytes: the bytes it took and those it made.
    BinaryFrom(pk::BinaryFromFn),
    /// Encodes a value's bytes: the bytes it made.
    BinaryTo(pk::BinaryToFn),
    /// Decodes a time: the bytes it took and the seconds.
    TimeFrom(pk::TimeFromFn),
    /// Encodes a time: the bytes it made.
    TimeTo(pk::TimeToFn),
    /// A CustomStream's DecodingAlgorithm: gives the function the bytes
    /// it decodes from those it is given.
    StreamDecode(pk::StreamDecode),
    /// A stream's InvertAlgorithm: what a bit is once AutoInvert has
    /// inverted the stream.
    BitInvert(pk::BitInvert),
}

impl Algorithm {
    /// Gives the kind of use the function is for, as the model names it.
    ///
    /// # Returns
    ///
    /// `criterion`, `algorithm`, `alarm`, `checksum`, `binary_from`,
    /// `binary_to`, `time_from`, `time_to`, `stream_decode` or
    /// `bit_invert`.
    pub fn kind(&self) -> &'static str {
        match self {
            Algorithm::Criterion(_) => "criterion",
            Algorithm::Outputs(_) => "algorithm",
            Algorithm::Alarm(_) => "alarm",
            Algorithm::Checksum(_) => "checksum",
            Algorithm::BinaryFrom(_) => "binary_from",
            Algorithm::BinaryTo(_) => "binary_to",
            Algorithm::TimeFrom(_) => "time_from",
            Algorithm::TimeTo(_) => "time_to",
            Algorithm::StreamDecode(_) => "stream_decode",
            Algorithm::BitInvert(_) => "bit_invert",
        }
    }
}

/// The functions an application has supplied for a mission's
/// algorithms, by the name each has in the generated code.
#[derive(Debug, Clone, Default)]
pub struct HookTable {
    /// The place of each hook, by its name in the generated code.
    index: Rc<HashMap<String, usize>>,
    /// Each hook's function, if it is set.
    fns: Vec<Option<Algorithm>>,
}

impl HookTable {
    /// Gives the function set for a hook.
    ///
    /// # Arguments
    ///
    /// * `h` - The hook.
    ///
    /// # Returns
    ///
    /// The function.
    ///
    /// # Errors
    ///
    /// NoAlgorithm, if none is set.
    pub(crate) fn get(&self, h: &Hook) -> pk::Result<Algorithm> {
        self.index
            .get(h.ident.as_str())
            .and_then(|&i| self.fns[i])
            .ok_or(PacketError::NoAlgorithm { name: F })
    }

    /// Copies the functions of another table of the same mission,
    /// without allocating.
    ///
    /// # Arguments
    ///
    /// * `o` - The other table.
    fn copy(&mut self, o: &HookTable) {
        if self.fns.len() == o.fns.len() {
            self.fns.copy_from_slice(&o.fns);
        } else {
            *self = o.clone();
        }
    }
}

/// A decoded value, as the interpreter holds it.
#[derive(Debug, Clone, PartialEq)]
pub enum Val {
    /// An integer.
    Int(i128),
    /// An integer, or an enumeration's value, of more than 64 bits.
    Big(Box<crate::wide::WideInt>),
    /// The raw value of an integer encoding, to be written as it is: not
    /// calibrated, and checked against its encoding only.
    RawInt(i128),
    /// The raw value of a float encoding, to be written as it is.
    RawFloat(f64),
    /// A float.
    Float(f64),
    /// An enumeration's value.
    Enum(i64),
    /// A boolean.
    Bool(bool),
    /// A string.
    Str(String),
    /// Binary data.
    Bytes(Vec<u8>),
    /// An aggregate's members, in order.
    Agg(Vec<Val>),
    /// An array's elements, and how many of them it holds: the storage
    /// past them is kept for a later array with more.
    Arr(Vec<Val>, usize),
}

/// Why a packet couldn't be decoded or encoded: the runtime's error,
/// and the field or container it is about.
#[derive(Debug, Clone, PartialEq)]
pub struct CodecError {
    /// What went wrong, as the runtime says it.
    pub error: PacketError,
    /// The field or container it is about.
    pub field: String,
}

impl CodecError {
    /// Gives the kind of error, as `PacketError::kind` does.
    ///
    /// # Returns
    ///
    /// The kind, such as `BadLength`.
    pub fn kind(&self) -> &'static str {
        self.error.kind()
    }
}

/// A result of decoding or encoding.
pub type CodecResult<T> = std::result::Result<T, CodecError>;

/// What classifying a packet gives: the decoding, or what the packet
/// leads to that the interpreter can't run yet.
pub type Classified = std::result::Result<CodecResult<Decoded>, String>;

/// Names the field of a runtime error.
///
/// # Arguments
///
/// * `field` - The field or container.
///
/// # Returns
///
/// A function turning the runtime's error into the interpreter's.
fn at(field: &str) -> impl Fn(PacketError) -> CodecError + '_ {
    move |error| CodecError {
        error,
        field: field.to_string(),
    }
}

// --- what can be run ---------------------------------------------------

/// Gives the decoding function of a transform.
///
/// # Arguments
///
/// * `h`    - The hook, or `None` where the document gives only the other
///            way.
/// * `vals` - The values of the packet, which carry the functions set.
///
/// # Returns
///
/// The function, or `None` for none, which the runtime refuses as
/// NotInvertible.
///
/// # Errors
///
/// NoAlgorithm, if the application hasn't set it.
fn binary_from(
    h: &Option<Shared<Hook>>,
    vals: &dyn Look,
) -> pk::Result<Option<pk::BinaryFromFn>> {
    let Some(h) = h else { return Ok(None) };
    match vals.hooks().get(&h.get())? {
        Algorithm::BinaryFrom(f) => Ok(Some(f)),
        _ => Err(PacketError::NoAlgorithm { name: F }),
    }
}

/// Gives the encoding function of a transform.
///
/// # Arguments
///
/// * `h`    - The hook, or `None` where the document gives only the other
///            way.
/// * `vals` - The values of the packet, which carry the functions set.
///
/// # Returns
///
/// The function, or `None` for none.
///
/// # Errors
///
/// NoAlgorithm, if the application hasn't set it.
fn binary_to(
    h: &Option<Shared<Hook>>,
    vals: &dyn Look,
) -> pk::Result<Option<pk::BinaryToFn>> {
    let Some(h) = h else { return Ok(None) };
    match vals.hooks().get(&h.get())? {
        Algorithm::BinaryTo(f) => Ok(Some(f)),
        _ => Err(PacketError::NoAlgorithm { name: F }),
    }
}

/// Gives the decoding function of a time.
///
/// # Arguments
///
/// * `h`    - The hook, or `None` where the document gives only the other
///            way.
/// * `vals` - The values of the packet, which carry the functions set.
///
/// # Returns
///
/// The function, or `None` for none.
///
/// # Errors
///
/// NoAlgorithm, if the application hasn't set it.
fn time_from(
    h: &Option<Shared<Hook>>,
    vals: &dyn Look,
) -> pk::Result<Option<pk::TimeFromFn>> {
    let Some(h) = h else { return Ok(None) };
    match vals.hooks().get(&h.get())? {
        Algorithm::TimeFrom(f) => Ok(Some(f)),
        _ => Err(PacketError::NoAlgorithm { name: F }),
    }
}

/// Gives the encoding function of a time.
///
/// # Arguments
///
/// * `h`    - The hook, or `None` where the document gives only the other
///            way.
/// * `vals` - The values of the packet, which carry the functions set.
///
/// # Returns
///
/// The function, or `None` for none.
///
/// # Errors
///
/// NoAlgorithm, if the application hasn't set it.
fn time_to(
    h: &Option<Shared<Hook>>,
    vals: &dyn Look,
) -> pk::Result<Option<pk::TimeToFn>> {
    let Some(h) = h else { return Ok(None) };
    match vals.hooks().get(&h.get())? {
        Algorithm::TimeTo(f) => Ok(Some(f)),
        _ => Err(PacketError::NoAlgorithm { name: F }),
    }
}

/// The most inputs of a custom algorithm the interpreter gathers on the
/// stack; one with more gathers them on the heap.
const INPUTS: usize = 32;

/// Gathers a custom algorithm's inputs and calls a function with them.
///
/// # Arguments
///
/// * `inputs` - The inputs.
/// * `vals`   - The values of the packet, which they read.
/// * `f`      - What is called with them.
///
/// # Returns
///
/// What it gives.
///
/// # Errors
///
/// Missing, if a value it reads isn't there; BadSize, if an element it
/// reads isn't.
pub(crate) fn with_inputs<T>(
    inputs: &[Input],
    vals: &dyn Look,
    f: impl FnOnce(&[f64]) -> T,
) -> pk::Result<T> {
    let one = |i: &Input| -> pk::Result<f64> {
        match i {
            Input::Const(x) => Ok(lit_f64(x)),
            Input::Value(l, path, _) => {
                let l = l.get();
                let v =
                    vals.val(l.id).ok_or(PacketError::Missing { field: F })?;
                let (v, _) = reach(v, &l.vt(), path)?;
                math_value(v)
            }
        }
    };
    if inputs.len() <= INPUTS {
        let mut a = [0.0; INPUTS];
        for (d, i) in a.iter_mut().zip(inputs) {
            *d = one(i)?;
        }
        return Ok(f(&a[..inputs.len()]));
    }
    let a = inputs.iter().map(one).collect::<pk::Result<Vec<f64>>>()?;
    Ok(f(&a))
}

/// Gives a literal as a float, as an algorithm's constant input.
///
/// # Arguments
///
/// * `x` - The literal.
///
/// # Returns
///
/// The float; a boolean is 1 or 0, and anything else not a number.
fn lit_f64(x: &Lit) -> f64 {
    match x {
        Lit::Int(i) => i.to_f64().unwrap_or(f64::NAN),
        Lit::Float(f) => *f,
        Lit::Bool(b) => {
            if *b {
                1.0
            } else {
                0.0
            }
        }
        _ => f64::NAN,
    }
}

/// Finds the first construct of a type that the interpreter can't run.
///
/// # Arguments
///
/// * `vt` - The type.
///
/// # Returns
///
/// What it is, or `None` if it can be run.
fn type_unsupported(vt: &VType) -> Option<String> {
    let what = |s: &str| Some(format!("{s} in type {}", vt.key));
    match &vt.calibrator {
        Some(Calibrator::Context(c)) if !c.bound => {
            return what("a context calibrator not bound to the packet")
        }
        _ => {}
    }
    match &vt.enc {
        Encoding::Int(e) => {
            if e.bits > 64 {
                let bytes = if vt.kind == "bool" {
                    (e.bits + 8) / 8
                } else {
                    vt.width() / 8
                };
                if vt.kind == "int" && vt.calibrator.is_some() && vt.wide() {
                    return what("a calibrated integer of more than 64 bits");
                }
                if bytes as usize > crate::wide::MAX_BYTES {
                    return what("an integer of more than 8192 bits");
                }
            }
        }
        Encoding::Float(_) => {}
        Encoding::String(_) => {}
        Encoding::Binary(_) => {}
        Encoding::Array(e) => return type_unsupported(&e.elem),
        Encoding::Aggregate(e) => {
            for r in &e.checks {
                if let Some(w) = r.criteria.iter().find_map(term_unsupported) {
                    return Some(w);
                }
            }
            for m in &e.members {
                if let Some(w) = type_unsupported(&m.vtype) {
                    return Some(w);
                }
            }
        }
        Encoding::Xform(_) => {}
    }
    None
}

/// Finds the first construct of a term that the interpreter can't run.
///
/// # Arguments
///
/// * `t` - The term.
///
/// # Returns
///
/// What it is, or `None` if it can be run.
fn term_unsupported(t: &Term) -> Option<String> {
    match t {
        Term::Custom(_) => None,
        Term::AllOf(a) => a.get().terms.iter().find_map(term_unsupported),
        Term::AnyOf(a) => a.get().terms.iter().find_map(term_unsupported),
        Term::Criterion(c) => {
            let c = c.get();
            let _ = c;
            None
        }
    }
}

/// Finds the first construct of some items that the interpreter can't run.
///
/// # Arguments
///
/// * `items` - The items.
///
/// # Returns
///
/// What it is, or `None` if they can be run.
fn items_unsupported(items: &[Item]) -> Option<String> {
    for it in items {
        let bad = match it {
            Item::Seek(s) => {
                let _ = s;
                None
            }
            Item::Mark(_) | Item::Pad(_) | Item::Fixed(_) => None,
            Item::Require(r) => r.criteria.iter().find_map(term_unsupported),
            Item::Cond(c) => c
                .criteria
                .iter()
                .find_map(term_unsupported)
                .or_else(|| items_unsupported(&c.items)),
            Item::Fill(_) => None,
            Item::Value(v) => {
                let l = v.local.get();
                type_unsupported(&l.vt())
            }
            Item::TailSeek(_) | Item::TailPad(_) => None,
            Item::LengthEnd(_) => None,
            Item::Transform(t) => items_unsupported(&t.get().items),
            Item::Verify(_) => None,
            Item::Segment(_) => None,
            Item::EndSeek(_) | Item::NextSeek(_) => {
                Some("a location from another entry".into())
            }
        };
        if bad.is_some() {
            return bad;
        }
    }
    None
}

/// Finds the first construct of a packet that the interpreter can't run:
/// in its own items and criteria and its bases'.
///
/// # Arguments
///
/// * `n` - The packet.
///
/// # Returns
///
/// What it is, or `None` if the packet can be decoded and encoded.
pub fn unsupported(n: &Shared<Node>) -> Option<String> {
    let found = n.chain().iter().find_map(|m| {
        let m = m.get();
        m.criteria
            .iter()
            .find_map(term_unsupported)
            .or_else(|| items_unsupported(&m.items))
    });
    found.map(|w| format!("{}: {w}", n.get().key))
}

// --- encodings ---------------------------------------------------------

/// Gives the runtime's integer encoding of an integer type.
///
/// # Arguments
///
/// * `vt` - The type, whose encoding is an IntEnc.
///
/// # Returns
///
/// The encoding; BCD's raw value is unsigned.
fn int_enc(vt: &VType) -> IntEncoding {
    let Encoding::Int(e) = &vt.enc else {
        unreachable!("an integer")
    };
    match e.encoding.as_str() {
        "twosComplement" => IntEncoding::TwosComplement,
        "signMagnitude" => IntEncoding::SignMagnitude,
        "onesComplement" => IntEncoding::OnesComplement,
        _ => IntEncoding::Unsigned,
    }
}

/// Says whether an integer encoding is BCD, and if so packed.
///
/// # Arguments
///
/// * `enc` - The encoding's name.
///
/// # Returns
///
/// `None` if it isn't BCD; whether it is packed if it is.
fn bcd(enc: &str) -> Option<bool> {
    match enc {
        "BCD" => Some(false),
        "packedBCD" => Some(true),
        _ => None,
    }
}

/// Gives bytes that live as long as the program, each distinct value
/// once: the runtime takes a string's terminator and a byte order list
/// only as `'static`, which the code generated for a mission has as
/// constants. Each distinct value is kept once for the whole program,
/// however many missions use it, so what is kept doesn't grow with the
/// missions read.
///
/// # Arguments
///
/// * `b` - The bytes.
///
/// # Returns
///
/// The kept copy.
pub(crate) fn interned(b: &[u8]) -> &'static [u8] {
    use std::sync::{Mutex, OnceLock};
    static KEPT: OnceLock<Mutex<Vec<&'static [u8]>>> = OnceLock::new();
    let mut kept = KEPT.get_or_init(|| Mutex::new(Vec::new())).lock().unwrap();
    if let Some(k) = kept.iter().find(|k| **k == b) {
        return k;
    }
    let k: &'static [u8] = Box::leak(b.to_vec().into_boxed_slice());
    kept.push(k);
    k
}

/// Gives a byte order list that lives as long as the program, as
/// [`interned`] does, looking for a copy kept before without making one.
///
/// # Arguments
///
/// * `l` - The significance of each byte.
///
/// # Returns
///
/// The kept copy.
fn interned_list(l: &[i64]) -> &'static [u8] {
    use std::sync::{Mutex, OnceLock};
    static KEPT: OnceLock<Mutex<Vec<&'static [u8]>>> = OnceLock::new();
    let mut kept = KEPT.get_or_init(|| Mutex::new(Vec::new())).lock().unwrap();
    let same = |k: &[u8]| {
        k.len() == l.len() && k.iter().zip(l).all(|(a, b)| *a as i64 == *b)
    };
    if let Some(k) = kept.iter().find(|k| same(k)) {
        return k;
    }
    let b: Vec<u8> = l.iter().map(|x| *x as u8).collect();
    let k: &'static [u8] = Box::leak(b.into_boxed_slice());
    kept.push(k);
    k
}

/// Gives the runtime's byte order of an encoding.
///
/// # Arguments
///
/// * `o` - The order.
///
/// # Returns
///
/// The order.
fn byte_order(o: &Order) -> ByteOrder {
    match o {
        Order::Be => ByteOrder::Big,
        Order::Le => ByteOrder::Little,
        Order::List(l) => ByteOrder::List(interned_list(l)),
    }
}

/// Says whether the runtime's plain reader and writer handle an encoding:
/// most significant bit first, big endian or whole-byte little endian, at
/// most 64 bits.
///
/// # Arguments
///
/// * `bits`  - The encoding's size.
/// * `order` - Its byte order.
/// * `lsb`   - Whether its bits are least significant first.
///
/// # Returns
///
/// Whether they do.
fn fast(bits: i64, order: &Order, lsb: bool) -> bool {
    !lsb && bits <= 64
        && (matches!(order, Order::Be)
            || (matches!(order, Order::Le) && bits % 8 == 0))
}

/// Reads the raw value of an integer or float encoding.
///
/// # Arguments
///
/// * `r`     - The reader.
/// * `bits`  - The encoding's size.
/// * `order` - Its byte order.
/// * `lsb`   - Whether its bits are least significant first.
///
/// # Returns
///
/// The raw value.
///
/// # Errors
///
/// The runtime's, if the packet ends first.
fn raw_read(
    r: &mut BitReader<'_>,
    bits: i64,
    order: &Order,
    lsb: bool,
) -> pk::Result<u128> {
    if fast(bits, order, lsb) {
        Ok(r.uint_order(bits as u32, matches!(order, Order::Le), F)? as u128)
    } else {
        r.uint_bits(bits as u32, lsb, byte_order(order), F)
    }
}

/// Writes the raw value of an integer or float encoding.
///
/// # Arguments
///
/// * `w`     - The writer.
/// * `bits`  - The encoding's size.
/// * `order` - Its byte order.
/// * `lsb`   - Whether its bits are least significant first.
/// * `v`     - The raw value.
fn raw_write(
    w: &mut BitWriter<'_>,
    bits: i64,
    order: &Order,
    lsb: bool,
    v: u128,
) {
    if fast(bits, order, lsb) {
        w.uint_order(bits as u32, matches!(order, Order::Le), v as u64);
    } else {
        w.uint_bits(bits as u32, lsb, byte_order(order), v);
    }
}

/// The most polynomial terms or spline points the interpreter converts
/// on the stack; a calibrator with more is converted on the heap.
const ON_STACK: usize = 32;

/// Calibrates a raw value with a polynomial, as the runtime does.
///
/// # Arguments
///
/// * `c` - The polynomial.
/// * `x` - The raw value.
///
/// # Returns
///
/// The engineering value.
fn polynomial(c: &tcs_xtce_model::model::Polynomial, x: f64) -> f64 {
    if c.terms.len() <= ON_STACK {
        let mut t = [(0.0, 0i32); ON_STACK];
        for (d, (a, e)) in t.iter_mut().zip(&c.terms) {
            *d = (*a, *e as i32);
        }
        return pk::polynomial(x, &t[..c.terms.len()]);
    }
    let t: Vec<(f64, i32)> =
        c.terms.iter().map(|(a, e)| (*a, *e as i32)).collect();
    pk::polynomial(x, &t)
}

/// Calibrates a raw value with a spline whose segments have orders of
/// their own, as the runtime does.
///
/// # Arguments
///
/// * `s` - The spline.
/// * `x` - The raw value.
///
/// # Returns
///
/// The engineering value.
///
/// # Errors
///
/// The runtime's, if the spline doesn't cover the value.
fn spline_orders(s: &tcs_xtce_model::model::Spline, x: f64) -> pk::Result<f64> {
    if s.orders.len() <= ON_STACK {
        let mut o = [0u32; ON_STACK];
        for (d, v) in o.iter_mut().zip(&s.orders) {
            *d = *v as u32;
        }
        return pk::spline_orders(
            x,
            &s.points,
            &o[..s.orders.len()],
            s.extrapolate,
            F,
        );
    }
    let o: Vec<u32> = s.orders.iter().map(|v| *v as u32).collect();
    pk::spline_orders(x, &s.points, &o, s.extrapolate, F)
}
/// Converts a value of the packet to a float, as a calibrator or
/// algorithm takes it.
///
/// # Arguments
///
/// * `v` - The value.
///
/// # Returns
///
/// It as a float: a boolean is 1 or 0.
///
/// # Errors
///
/// OutOfRange, if it isn't a number.
pub(crate) fn math_value(v: &Val) -> pk::Result<f64> {
    Ok(match v {
        Val::Int(i) => *i as f64,
        Val::Big(i) => crate::wide::to_f64(i),
        Val::Float(x) => *x,
        Val::Enum(i) => *i as f64,
        Val::Bool(b) => {
            if *b {
                1.0
            } else {
                0.0
            }
        }
        _ => return Err(PacketError::OutOfRange { field: F }),
    })
}

/// Gives the runtime's operator for a MathOperation's.
///
/// # Arguments
///
/// * `op` - The operator, as XTCE writes it.
///
/// # Returns
///
/// The runtime's.
fn math_operator(op: &str) -> pk::MathOperator {
    use pk::MathOperator as M;
    match op {
        "/" => M::Div,
        "%" => M::Mod,
        "^" => M::Pow,
        "y^x" => M::RevPow,
        "ln" => M::Ln,
        "log" => M::Log10,
        "e^x" => M::Exp,
        "1/x" => M::Inv,
        "x!" => M::Factorial,
        "tan" => M::Tan,
        "cos" => M::Cos,
        "sin" => M::Sin,
        "atan" => M::Atan,
        "atan2" => M::Atan2,
        "acos" => M::Acos,
        "asin" => M::Asin,
        "tanh" => M::Tanh,
        "cosh" => M::Cosh,
        "sinh" => M::Sinh,
        "atanh" => M::Atanh,
        "acosh" => M::Acosh,
        "asinh" => M::Asinh,
        "<<" => M::Shl,
        ">>" => M::Shr,
        "&" => M::BitAnd,
        "|" => M::BitOr,
        "xor" => M::BitXor,
        "~" => M::BitNot,
        "&&" => M::And,
        "||" => M::Or,
        "!" => M::Not,
        "abs" => M::Abs,
        "div" => M::EuclidDiv,
        "int" => M::Trunc,
        ">" => M::Gt,
        ">=" => M::Ge,
        "<" => M::Lt,
        "<=" => M::Le,
        "==" => M::Eq,
        "!=" => M::Ne,
        "min" => M::Min,
        _ => M::Max,
    }
}

/// Evaluates a MathOperation's expression tree.
///
/// # Arguments
///
/// * `e`    - The tree.
/// * `x`    - The value being calibrated, which `ThisParameterOperand` stands
///            for.
/// * `vals` - The values of the packet, which other operands read.
///
/// # Returns
///
/// The value.
///
/// # Errors
///
/// Missing, if a value it reads isn't there; BadSize, if an element it
/// reads isn't.
pub(crate) fn math(e: &Expr, x: f64, vals: &dyn Look) -> pk::Result<f64> {
    Ok(match e {
        Expr::Const(c) => *c,
        Expr::Raw => x,
        Expr::Value(r) => {
            let r = r.get();
            let l = r.local.as_ref().expect("a bound ValueRef");
            let v = vals
                .val(l.get().id)
                .ok_or(PacketError::Missing { field: F })?;
            let (v, _) = reach(v, &l.get().vt(), &r.member)?;
            math_value(v)?
        }
        Expr::Op(op, args) => {
            let a = math(&args[0], x, vals)?;
            let b = match args.get(1) {
                Some(b) => math(b, x, vals)?,
                None => 0.0,
            };
            match op.as_str() {
                "+" => a + b,
                "-" => a - b,
                "*" => a * b,
                _ => pk::math(math_operator(op), a, b),
            }
        }
    })
}

/// Picks the calibrator of a context calibrator: that of the first case
/// whose criteria hold, or the default.
///
/// # Arguments
///
/// * `c`    - The context calibrator, bound to the packet.
/// * `vals` - The values of the packet, which the criteria read.
///
/// # Returns
///
/// The calibrator, or `None` for none.
///
/// # Errors
///
/// BadSize, if a criterion reads an element that isn't there.
fn choose<'c>(
    c: &'c tcs_xtce_model::model::ContextCal,
    vals: &dyn Look,
) -> pk::Result<Option<&'c Calibrator>> {
    for case in &c.cases {
        let CalCase::Bound(terms, cal) = case else {
            unreachable!("a bound context calibrator")
        };
        let mut ok = true;
        for t in terms {
            if !term(t, vals)? {
                ok = false;
                break;
            }
        }
        if ok {
            return Ok(Some(cal));
        }
    }
    Ok(c.default.as_deref())
}

/// Calibrates a raw value.
///
/// # Arguments
///
/// * `cal`  - The calibrator, or `None` for none.
/// * `x`    - The raw value.
/// * `vals` - The values of the packet, which a math or context calibrator
///            reads.
///
/// # Returns
///
/// The engineering value.
///
/// # Errors
///
/// The runtime's, if a spline doesn't cover the value; Missing, if a value
/// a calibrator reads isn't there.
fn apply(cal: Option<&Calibrator>, x: f64, vals: &dyn Look) -> pk::Result<f64> {
    Ok(match cal {
        None => x,
        Some(Calibrator::Poly(p)) => polynomial(p, x),
        Some(Calibrator::Spline(s)) => {
            if s.orders.is_empty() {
                pk::spline(x, &s.points, s.order as u32, s.extrapolate, F)?
            } else {
                spline_orders(s, x)?
            }
        }
        Some(Calibrator::Math(m)) => math(&m.expr, x, vals)?,
        Some(Calibrator::Context(c)) => apply(choose(c, vals)?, x, vals)?,
    })
}

/// Inverts a calibrator.
///
/// # Arguments
///
/// * `cal`  - The calibrator, or `None` for none.
/// * `y`    - The engineering value.
/// * `vals` - The values of the packet, which a context calibrator's
///            criteria read.
///
/// # Returns
///
/// The raw value.
///
/// # Errors
///
/// NotInvertible, if the calibrator can't be inverted; the runtime's, if
/// a spline doesn't cover the value.
fn invert(
    cal: Option<&Calibrator>,
    y: f64,
    vals: &dyn Look,
) -> pk::Result<f64> {
    match cal {
        None => Ok(y),
        Some(Calibrator::Poly(p)) => {
            let slope: f64 =
                p.terms.iter().filter(|t| t.1 == 1).map(|t| t.0).sum();
            let intercept: f64 =
                p.terms.iter().filter(|t| t.1 == 0).map(|t| t.0).sum();
            if p.terms.iter().any(|t| t.1 > 1) || slope == 0.0 {
                return Err(PacketError::NotInvertible { field: F });
            }
            Ok(pk::polynomial_inverse(y, intercept, slope))
        }
        Some(Calibrator::Spline(s)) => {
            let inc = s.points.windows(2).all(|w| w[0].1 < w[1].1);
            let dec = s.points.windows(2).all(|w| w[0].1 > w[1].1);
            if s.order != 1 || !s.orders.is_empty() || !(inc || dec) {
                return Err(PacketError::NotInvertible { field: F });
            }
            pk::spline_inverse(y, &s.points, s.extrapolate, F)
        }
        Some(Calibrator::Math(_)) => {
            Err(PacketError::NotInvertible { field: F })
        }
        Some(Calibrator::Context(c)) => invert(choose(c, vals)?, y, vals),
    }
}

/// Calibrates a raw value of a type.
///
/// # Arguments
///
/// * `vt`   - The type.
/// * `x`    - The raw value.
/// * `vals` - The values of the packet.
///
/// # Returns
///
/// The engineering value.
///
/// # Errors
///
/// As [`apply`]'s.
fn calibrate(vt: &VType, x: f64, vals: &dyn Look) -> pk::Result<f64> {
    apply(vt.calibrator.as_ref(), x, vals)
}

/// Inverts a type's calibrator.
///
/// # Arguments
///
/// * `vt`   - The type.
/// * `y`    - The engineering value.
/// * `vals` - The values of the packet.
///
/// # Returns
///
/// The raw value.
///
/// # Errors
///
/// As [`invert`]'s.
fn uncalibrate(vt: &VType, y: f64, vals: &dyn Look) -> pk::Result<f64> {
    invert(vt.calibrator.as_ref(), y, vals)
}

/// Gives a string type's StringSpec.
///
/// # Arguments
///
/// * `e` - The string encoding.
///
/// # Returns
///
/// The spec.
fn spec(e: &tcs_xtce_model::model::StringEnc) -> StringSpec {
    let charset = match e.charset.as_str() {
        "US-ASCII" => Charset::Ascii,
        "ISO-8859-1" => Charset::Latin1,
        "Windows-1252" => Charset::Windows1252,
        "UTF-16" => Charset::Utf16,
        "UTF-16BE" => Charset::Utf16Be,
        "UTF-16LE" => Charset::Utf16Le,
        "UTF-32" => Charset::Utf32,
        "UTF-32BE" => Charset::Utf32Be,
        "UTF-32LE" => Charset::Utf32Le,
        _ => Charset::Utf8,
    };
    StringSpec {
        fixed_bits: e.fixed_bits.map(|b| b as usize),
        max_bits: e.max_bits as usize,
        terminator: e.terminator.as_deref().map(interned),
        size_tag_bits: e.size_tag_bits.unwrap_or(0) as u32,
        charset,
    }
}

/// Gives the runtime's float format.
///
/// # Arguments
///
/// * `fmt` - The XTCE format's name.
///
/// # Returns
///
/// The format.
fn float_format(fmt: &str) -> FloatFormat {
    match fmt {
        "MILSTD_1750A" => FloatFormat::Mil1750a,
        "DEC" => FloatFormat::Dec,
        "IBM" => FloatFormat::Ibm,
        "TI" => FloatFormat::Ti,
        _ => FloatFormat::Ieee,
    }
}

/// Says whether a type is a string or binary value whose bytes are least
/// significant bit first.
///
/// # Arguments
///
/// * `vt` - The type.
///
/// # Returns
///
/// Whether it is.
fn lsb_bytes(vt: &VType) -> bool {
    match &vt.enc {
        Encoding::String(e) => e.lsb,
        Encoding::Binary(e) => e.lsb,
        _ => false,
    }
}

// --- sizes -------------------------------------------------------------

/// Gives the value that a bound DynSize adjusts: the value it reads, or
/// for a DiscreteLookupList the value of the first entry whose criteria
/// hold, or its default.
///
/// # Arguments
///
/// * `d`     - The DynSize.
/// * `vals`  - The values of the packet so far.
/// * `inner` - The members of the aggregate the value is in, which a member's
///             size may come from.
///
/// # Returns
///
/// The value, as a float.
///
/// # Errors
///
/// BadSize, if the value isn't there, or an element it reads isn't.
fn source(d: &DynSize, vals: &dyn Look, inner: Inner<'_>) -> pk::Result<f64> {
    if let Some(m) = &d.member {
        // A member's size from an earlier member of its aggregate.
        let i = inner.0.iter().position(|x| &x.ident == m);
        return match i.and_then(|i| inner.1.get(i)) {
            Some(Val::Int(i)) => Ok(*i as f64),
            _ => Err(PacketError::BadSize { field: F }),
        };
    }
    if let Some(lookup) = &d.lookup {
        for (crit, v) in lookup {
            let mut ok = true;
            for t in crit {
                if !term(t, vals)? {
                    ok = false;
                    break;
                }
            }
            if ok {
                return Ok(*v as f64);
            }
        }
        return Ok(d.default as f64);
    }
    let l = d.local.as_ref().expect("a bound DynSize");
    let v = vals
        .val(l.get().id)
        .ok_or(PacketError::BadSize { field: F })?;
    let (x, _) = reach(v, &l.get().vt(), &d.path)?;
    Ok(match x {
        Val::Int(i) => *i as f64,
        Val::Big(i) => crate::wide::to_f64(i),
        Val::Enum(i) => *i as f64,
        Val::Float(f) => *f,
        Val::Bool(b) => *b as u8 as f64,
        _ => return Err(PacketError::BadSize { field: F }),
    })
}

/// Gives the size or count a DynSize gives.
///
/// # Arguments
///
/// * `d`     - The DynSize.
/// * `vals`  - The values of the packet so far.
/// * `inner` - The members of the aggregate the value is in, which a member's
///             size may come from.
///
/// # Returns
///
/// The size, scaled by its factor.
///
/// # Errors
///
/// BadSize, if it isn't a size the DynSize allows.
fn size_of(
    d: &DynSize,
    vals: &dyn Look,
    inner: Inner<'_>,
) -> pk::Result<usize> {
    let x = source(d, vals, inner)?;
    let n = pk::dynamic_size(
        x,
        d.slope,
        d.intercept,
        d.unit as u32,
        d.max as usize,
        F,
    )?;
    Ok(n * d.factor as usize)
}

/// Gives a count, fixed or from another value.
///
/// # Arguments
///
/// * `c`     - The count.
/// * `vals`  - The values of the packet so far.
/// * `inner` - The members of the aggregate the value is in, which a member's
///             size may come from.
///
/// # Returns
///
/// The count.
///
/// # Errors
///
/// As [`size_of`]'s.
fn count_of(c: &Count, vals: &dyn Look, inner: Inner<'_>) -> pk::Result<i64> {
    match c {
        Count::Int(n) => Ok(*n),
        Count::Dyn(d) => Ok(size_of(d, vals, inner)? as i64),
    }
}

/// Gives the bits a Span covers.
///
/// # Arguments
///
/// * `sp`   - The span.
/// * `vals` - The values of the packet so far.
///
/// # Returns
///
/// The bits.
///
/// # Errors
///
/// As [`size_of`]'s.
fn span(
    sp: &tcs_xtce_model::model::Span,
    vals: &dyn Look,
) -> pk::Result<usize> {
    let count = count_of(&sp.count, vals, OUTER)? as usize;
    let gap = count_of(&sp.gap, vals, OUTER)? as usize;
    Ok(pk::span_bits(count, sp.each as usize, gap))
}

/// Gives the offset of a Seek: its bits, less those it backs up and
/// those of the entry it places from the end.
///
/// # Arguments
///
/// * `s`    - The Seek.
/// * `vals` - The values of the packet so far.
///
/// # Returns
///
/// The offset in bits.
///
/// # Errors
///
/// As [`size_of`]'s.
fn seek_offset(
    s: &tcs_xtce_model::model::Seek,
    vals: &dyn Look,
) -> pk::Result<i64> {
    let mut off = count_of(&s.offset, vals, OUTER)? - s.back;
    if let Some(sp) = &s.span {
        off -= span(sp, vals)? as i64;
    }
    Ok(off)
}

/// Gives the bits a TailSeek goes back from the end.
///
/// # Arguments
///
/// * `t`    - The TailSeek.
/// * `vals` - The values of the packet so far.
///
/// # Returns
///
/// The bits.
///
/// # Errors
///
/// As [`size_of`]'s.
fn tail_back(
    t: &tcs_xtce_model::model::TailSeek,
    vals: &dyn Look,
) -> pk::Result<usize> {
    let mut back = t.back as usize;
    if let Some(sp) = &t.span {
        back += span(sp, vals)?;
    }
    Ok(back)
}

/// Rounds a calibrated integer's engineering value to the integer its
/// generated field holds.
///
/// # Arguments
///
/// * `vt` - The type.
/// * `y`  - The engineering value.
///
/// # Returns
///
/// The integer.
///
/// # Errors
///
/// OutOfRange, if the field can't hold it.
fn round_int(vt: &VType, y: f64) -> pk::Result<i128> {
    let w = vt.width() as i32;
    let (lo, lim) = if vt.signed() {
        (-(2f64.powi(w - 1)), 2f64.powi(w - 1))
    } else {
        (0.0, 2f64.powi(w))
    };
    let v = pk::round_to_int(y, lo, lim, F)?;
    Ok(if vt.signed() {
        v as i64 as i128
    } else {
        v as u64 as i128
    })
}

// --- decoding ----------------------------------------------------------

/// Storage a decoding reuses: a string's bytes as they are on the wire,
/// and the text of a boolean encoded as a string.
#[derive(Debug, Clone, Default)]
pub struct Bufs {
    /// A string's bytes as they are on the wire.
    pub wire: Vec<u8>,
    /// The text of a boolean encoded as a string.
    pub text: String,
    /// Buffers for the transformed data of repeated containers, one for
    /// each held inside another.
    pub xforms: Vec<Vec<u8>>,
}

/// Reads one value into its storage, which keeps the room it has.
///
/// # Arguments
///
/// * `r`     - The reader.
/// * `vt`    - Its type.
/// * `dst`   - Its storage: a value of its type, as [`template`] makes it.
/// * `vals`  - The values of the packet so far, which sizes come from.
/// * `inner` - The members of the aggregate the value is in, which a member's
///             size may come from.
/// * `bufs`  - Storage the decoding reuses.
///
/// # Errors
///
/// The runtime's.
fn read_into(
    r: &mut BitReader<'_>,
    vt: &VType,
    dst: &mut Val,
    vals: &dyn Look,
    inner: Inner<'_>,
    bufs: &mut Bufs,
) -> pk::Result<()> {
    if lsb_bytes(vt) {
        r.set_lsb(true);
        let v = read_plain(r, vt, dst, vals, inner, bufs);
        r.set_lsb(false);
        return v;
    }
    read_plain(r, vt, dst, vals, inner, bufs)
}

/// Reads one value into its storage, in the reader's usual bit order.
///
/// # Arguments
///
/// * `r`     - The reader.
/// * `vt`    - Its type.
/// * `dst`   - Its storage.
/// * `vals`  - The values of the packet so far, which sizes come from.
/// * `inner` - The members of the aggregate the value is in, which a member's
///             size may come from.
/// * `bufs`  - Storage the decoding reuses.
///
/// # Errors
///
/// The runtime's.
fn read_plain(
    r: &mut BitReader<'_>,
    vt: &VType,
    dst: &mut Val,
    vals: &dyn Look,
    inner: Inner<'_>,
    bufs: &mut Bufs,
) -> pk::Result<()> {
    match &vt.enc {
        Encoding::Aggregate(e) => {
            if !matches!(dst, Val::Agg(_)) {
                *dst = template(vt);
            }
            let Some(x) = &e.transform else {
                return read_members(r, e, dst, vals, bufs);
            };
            // The members are read from what the transform makes of its
            // bytes.
            let mut buf = bufs.xforms.pop().unwrap_or_default();
            buf.clear();
            buf.resize(x.cap.max(0) as usize, 0);
            let got = (|| {
                let f = binary_from(&x.decode, vals)?;
                let raw = x.raw.map(|r| r as usize);
                let n = r.binary_from(f, raw, &mut buf[..], F)?;
                let mut t = BitReader::new(&buf[..n]);
                read_members(&mut t, e, dst, vals, bufs)
            })();
            bufs.xforms.push(buf);
            got
        }
        Encoding::Array(e) => {
            let n = count_of(&e.count, vals, inner)? as usize;
            if !matches!(dst, Val::Arr(..)) {
                *dst = template(vt);
            }
            let Val::Arr(es, used) = dst else {
                unreachable!("an array")
            };
            // Room for the most elements its count allows was made with the
            // storage; more is made only if the count is past them.
            while es.len() < n {
                es.push(template(&e.elem));
            }
            *used = n;
            for (i, x) in es.iter_mut().take(n).enumerate() {
                if i > 0 && !matches!(e.gap, Count::Int(0)) {
                    r.skip(count_of(&e.gap, vals, inner)?, F)?;
                }
                read_into(r, &e.elem, x, vals, inner, bufs)?;
            }
            Ok(())
        }
        Encoding::Int(e) if e.bits > 64 => {
            let w = crate::wide::Wide {
                bits: e.bits as u32,
                lsb: e.lsb,
                order: byte_order(&e.order),
                enc: int_enc(vt),
                bcd: bcd(&e.encoding),
                signed: vt.signed(),
            };
            *dst = if vt.kind == "float" {
                let x = crate::wide::read_f64(r, w)?;
                Val::Float(calibrate(vt, x, vals)?)
            } else if vt.kind == "int" && vt.calibrator.is_some() {
                let y = calibrate(vt, crate::wide::read_f64(r, w)?, vals)?;
                Val::Int(round_int(vt, y)?)
            } else if vt.kind == "bool" {
                // A bit more than the field, so that it stays positive.
                let n = ((e.bits + 8) / 8) as usize;
                let w = crate::wide::Wide { signed: false, ..w };
                let x = crate::wide::read(r, w, n)?;
                Val::Bool(x != crate::wide::from_i128(0))
            } else {
                let x = crate::wide::read(r, w, (vt.width() / 8) as usize)?;
                put_big(dst, x);
                return Ok(());
            };
            Ok(())
        }
        Encoding::Int(e) => {
            let bits = e.bits as u32;
            let mut raw = raw_read(r, e.bits, &e.order, e.lsb)? as u64;
            if let Some(packed) = bcd(&e.encoding) {
                raw = pk::bcd_decode(raw, bits, packed, F)?;
            }
            let enc = int_enc(vt);
            *dst = match vt.kind.as_str() {
                "int" if vt.calibrator.is_some() => {
                    let y =
                        calibrate(vt, pk::int_to_f64(raw, bits, enc), vals)?;
                    Val::Int(round_int(vt, y)?)
                }
                "int" if vt.signed() => {
                    Val::Int(pk::to_signed(raw, bits, enc) as i128)
                }
                "int" => Val::Int(raw as i128),
                "enum" if vt.signed() => {
                    Val::Enum(pk::to_signed(raw, bits, enc))
                }
                "enum" => Val::Enum(raw as i64),
                "bool" => Val::Bool(raw != 0),
                _ => Val::Float(calibrate(
                    vt,
                    pk::int_to_f64(raw, bits, enc),
                    vals,
                )?),
            };
            Ok(())
        }
        Encoding::Float(e) => {
            let raw = raw_read(r, e.bits, &e.order, e.lsb)?;
            let x = if e.fmt == "IEEE754" && (e.bits == 32 || e.bits == 64) {
                pk::float_from_raw(raw as u64, e.bits as u32)
            } else {
                pk::float_decode(float_format(&e.fmt), e.bits as u32, raw, F)?
            };
            *dst = Val::Float(calibrate(vt, x, vals)?);
            Ok(())
        }
        Encoding::Binary(e) => {
            let most = (e.bits / 8) as usize;
            let n = if e.rest {
                r.rest_bytes(most, e.tail as usize, F)?
            } else if e.embedded {
                r.embedded_bytes(most, F)?
            } else {
                match &e.dyn_ {
                    Some(d) => size_of(d, vals, inner)?,
                    None => most,
                }
            };
            if vt.kind == "string" {
                r.bytes_reuse(n, &mut bufs.wire, F)?;
                let text = std::str::from_utf8(&bufs.wire)
                    .map_err(|_| PacketError::InvalidString { field: F })?;
                if !matches!(dst, Val::Str(_)) {
                    *dst = template(vt);
                }
                let Val::Str(s) = dst else {
                    unreachable!("a string")
                };
                s.clear();
                s.push_str(text);
            } else {
                if !matches!(dst, Val::Bytes(_)) {
                    *dst = template(vt);
                }
                let Val::Bytes(b) = dst else {
                    unreachable!("binary")
                };
                r.bytes_reuse(n, b, F)?;
            }
            Ok(())
        }
        Encoding::String(e) => {
            let sp = match &e.dyn_ {
                Some(d) => spec(e).sized(size_of(d, vals, inner)?),
                None => spec(e),
            };
            if vt.kind == "bool" {
                pk::read_string_into(
                    r,
                    &sp,
                    &mut bufs.wire,
                    &mut bufs.text,
                    F,
                )?;
                *dst = if bufs.text == vt.one_string {
                    Val::Bool(true)
                } else if bufs.text == vt.zero_string {
                    Val::Bool(false)
                } else {
                    return Err(PacketError::OutOfRange { field: F });
                };
                return Ok(());
            }
            if !matches!(dst, Val::Str(_)) {
                *dst = template(vt);
            }
            let Val::Str(s) = dst else {
                unreachable!("a string")
            };
            pk::read_string_into(r, &sp, &mut bufs.wire, s, F)
        }
        Encoding::Xform(e) => {
            let raw = e.raw.map(|r| r as usize);
            if e.time {
                let f = time_from(&e.decode, vals)?;
                *dst = Val::Float(r.time_from(f, raw, F)?);
                return Ok(());
            }
            if !matches!(dst, Val::Bytes(_)) {
                *dst = template(vt);
            }
            let Val::Bytes(b) = dst else {
                unreachable!("binary")
            };
            let f = binary_from(&e.decode, vals)?;
            b.clear();
            b.resize(e.cap.max(0) as usize, 0);
            match r.binary_from(f, raw, &mut b[..], F) {
                Ok(n) => {
                    b.truncate(n);
                    Ok(())
                }
                Err(x) => {
                    b.clear();
                    Err(x)
                }
            }
        }
    }
}

/// Reads an aggregate's members into its storage.
///
/// # Arguments
///
/// * `r`    - The reader.
/// * `e`    - The aggregate's encoding.
/// * `dst`  - Its storage, an aggregate of its type.
/// * `vals` - The values of the packet so far, which sizes come from.
/// * `bufs` - Storage the decoding reuses.
///
/// # Errors
///
/// The runtime's, or Mismatch if a repeated container's own criteria
/// don't hold.
fn read_members(
    r: &mut BitReader<'_>,
    e: &tcs_xtce_model::model::AggregateEnc,
    dst: &mut Val,
    vals: &dyn Look,
    bufs: &mut Bufs,
) -> pk::Result<()> {
    let Val::Agg(ms) = dst else {
        unreachable!("an aggregate")
    };
    // Each member's size may come from those before it.
    for (i, m) in e.members.iter().enumerate() {
        if m.skip != 0 {
            r.pad(m.skip as usize, F)?;
        }
        let (done, rest) = ms.split_at_mut(i);
        read_into(r, &m.vtype, &mut rest[0], vals, (&e.members, done), bufs)?;
    }
    if e.pad != 0 {
        r.pad(e.pad as usize, F)?;
    }
    copy_checks(e, dst, vals)
}

/// Puts a wide integer into a value's storage, in the room it has.
///
/// # Arguments
///
/// * `dst` - The storage.
/// * `x`   - The integer.
pub(crate) fn put_big(dst: &mut Val, x: crate::wide::WideInt) {
    match dst {
        Val::Big(b) => **b = x,
        _ => *dst = Val::Big(Box::new(x)),
    }
}

/// Makes storage for a value of a type, with the room its largest value
/// needs: a string's or binary value's most bytes, and an array's most
/// elements.
///
/// # Arguments
///
/// * `vt` - The type.
///
/// # Returns
///
/// The storage, holding a value of the type.
pub fn template(vt: &VType) -> Val {
    match &vt.enc {
        Encoding::Aggregate(e) => {
            Val::Agg(e.members.iter().map(|m| template(&m.vtype)).collect())
        }
        Encoding::Array(e) => {
            let most = if !e.shape.is_empty() {
                e.shape.iter().product::<i64>() as usize
            } else {
                match &e.count {
                    Count::Dyn(d) => d.max.max(0) as usize,
                    Count::Int(n) => *n as usize,
                }
            };
            Val::Arr((0..most).map(|_| template(&e.elem)).collect(), 0)
        }
        _ => match vt.kind.as_str() {
            "string" => Val::Str(String::with_capacity(str_cap(vt))),
            "binary" => Val::Bytes(Vec::with_capacity(bytes_cap(vt))),
            "float" => Val::Float(0.0),
            "enum" if vt.wide() => {
                Val::Big(Box::new(crate::wide::from_i128(0)))
            }
            "enum" => Val::Enum(0),
            "bool" => Val::Bool(false),
            _ if vt.wide() => Val::Big(Box::new(crate::wide::from_i128(0))),
            _ => Val::Int(0),
        },
    }
}

/// Gives the most bytes a binary value of a type holds.
///
/// # Arguments
///
/// * `vt` - The type.
///
/// # Returns
///
/// The bytes.
pub fn bytes_cap(vt: &VType) -> usize {
    match &vt.enc {
        Encoding::Binary(e) => (e.bits / 8).max(0) as usize,
        Encoding::Xform(e) => e.cap.max(0) as usize,
        _ => 0,
    }
}

/// Gives the most bytes of UTF-8 a string field's storage must hold, as
/// the code generated for a mission gives it room for.
///
/// # Arguments
///
/// * `vt` - The type of a string field, or of a boolean encoded as a
///          string, or of a string encoded as binary.
///
/// # Returns
///
/// The bytes.
pub fn str_cap(vt: &VType) -> usize {
    let e = match &vt.enc {
        Encoding::String(e) => e,
        Encoding::Binary(e) => return (e.bits / 8).max(0) as usize,
        _ => return 0,
    };
    let wire = (match (e.fixed_bits, &e.dyn_) {
        (Some(f), None) => f,
        _ => e.max_bits,
    } / 8)
        .max(0) as usize;
    match e.charset.as_str() {
        "UTF-8" | "US-ASCII" => {
            tcs_xtce_model::text::wire_cap(vt).max(0) as usize
        }
        "ISO-8859-1" => 2 * wire,
        "Windows-1252" => 3 * wire,
        cs if cs.contains("16") => (3 * wire).div_ceil(2),
        _ => wire,
    }
}

/// Gives the values of a packet being decoded or encoded, by the number
/// of the Local that holds each.
pub trait Look {
    /// Gives a value.
    ///
    /// # Arguments
    ///
    /// * `id` - The number of its Local.
    ///
    /// # Returns
    ///
    /// The value, or `None` if it is absent.
    fn val(&self, id: i64) -> Option<&Val>;

    /// Gives the functions the application has supplied.
    ///
    /// # Returns
    ///
    /// The functions.
    fn hooks(&self) -> &HookTable;
}

/// The values of a packet being decoded or encoded, by the number of the
/// Local that holds each, in storage made once for the mission and
/// reused: each value keeps the room its type's largest value needs.
#[derive(Debug, Clone)]
pub struct Values {
    /// Each Local's value, or what it last held.
    v: Vec<Val>,
    /// Whether each Local has a value in this packet.
    here: Vec<bool>,
    /// The functions the application has supplied, which criteria,
    /// checksums and transforms call.
    hooks: HookTable,
}

/// Copies storage, keeping the room each string, binary value and array
/// has: a clone keeps only what it holds.
///
/// # Arguments
///
/// * `v` - The storage.
///
/// # Returns
///
/// A copy with the same room.
pub fn fresh(v: &Val) -> Val {
    match v {
        Val::Str(s) => {
            let mut c = String::with_capacity(s.capacity());
            c.push_str(s);
            Val::Str(c)
        }
        Val::Bytes(b) => {
            let mut c = Vec::with_capacity(b.capacity());
            c.extend_from_slice(b);
            Val::Bytes(c)
        }
        Val::Agg(ms) => Val::Agg(ms.iter().map(fresh).collect()),
        Val::Arr(es, n) => Val::Arr(es.iter().map(fresh).collect(), *n),
        x => x.clone(),
    }
}

impl Values {
    /// Makes storage for values like some others, with the same room.
    ///
    /// # Arguments
    ///
    /// * `v` - Each Local's storage as it starts.
    ///
    /// # Returns
    ///
    /// The storage, every value absent.
    pub fn like(v: &[Val]) -> Self {
        Self::new(v.iter().map(fresh).collect())
    }

    /// Makes storage for values.
    ///
    /// # Arguments
    ///
    /// * `v` - Each Local's value as it starts: one with the room its type's
    ///         largest value needs.
    ///
    /// # Returns
    ///
    /// The storage, every value absent.
    pub fn new(v: Vec<Val>) -> Self {
        let here = vec![false; v.len()];
        Values {
            v,
            here,
            hooks: HookTable::default(),
        }
    }

    /// Takes the functions the application has supplied, without
    /// allocating once they have been taken from the same table.
    ///
    /// # Arguments
    ///
    /// * `h` - The functions.
    pub fn set_hooks(&mut self, h: &HookTable) {
        self.hooks.copy(h);
    }

    /// Takes a value's storage out, to fill it in place.
    ///
    /// # Arguments
    ///
    /// * `id` - The number of its Local.
    ///
    /// # Returns
    ///
    /// The storage, which [`Values::put`] puts back.
    pub fn take(&mut self, id: i64) -> Val {
        std::mem::replace(&mut self.v[id as usize], Val::Bool(false))
    }

    /// Puts a value back, present.
    ///
    /// # Arguments
    ///
    /// * `id` - The number of its Local.
    /// * `x`  - The value.
    pub fn put(&mut self, id: i64, x: Val) {
        self.v[id as usize] = x;
        self.here[id as usize] = true;
    }

    /// Puts a value's storage back, absent, after filling it failed.
    ///
    /// # Arguments
    ///
    /// * `id` - The number of its Local.
    /// * `x`  - The storage.
    pub fn restore(&mut self, id: i64, x: Val) {
        self.v[id as usize] = x;
        self.here[id as usize] = false;
    }

    /// Makes a value absent, keeping its storage.
    ///
    /// # Arguments
    ///
    /// * `id` - The number of its Local.
    pub fn absent(&mut self, id: i64) {
        self.here[id as usize] = false;
    }

    /// Makes every value absent, keeping their storage.
    pub fn clear(&mut self) {
        self.here.fill(false);
    }

    /// Gives a value's storage, whether it is present or not.
    ///
    /// # Arguments
    ///
    /// * `id` - The number of its Local.
    ///
    /// # Returns
    ///
    /// The storage.
    pub fn slot_mut(&mut self, id: i64) -> &mut Val {
        &mut self.v[id as usize]
    }
}

impl Look for Values {
    fn val(&self, id: i64) -> Option<&Val> {
        self.here[id as usize].then(|| &self.v[id as usize])
    }

    fn hooks(&self) -> &HookTable {
        &self.hooks
    }
}

/// Values with one more: the copy of a repeated container that its own
/// criteria read.
struct With<'a> {
    /// The values.
    base: &'a dyn Look,
    /// The number of the copy's Local.
    id: i64,
    /// The copy.
    v: &'a Val,
}

impl Look for With<'_> {
    fn val(&self, id: i64) -> Option<&Val> {
        if id == self.id {
            Some(self.v)
        } else {
            self.base.val(id)
        }
    }

    fn hooks(&self) -> &HookTable {
        self.base.hooks()
    }
}

/// The members of the aggregate being decoded or encoded, and their
/// values, those decoded so far when decoding: a member's size may come
/// from one before it.
type Inner<'a> = (&'a [Member], &'a [Val]);

/// No aggregate: a value of a packet.
const OUTER: Inner<'static> = (&[], &[]);

/// Follows member and element steps into a value.
///
/// # Arguments
///
/// * `v`    - The value.
/// * `vt`   - Its type.
/// * `path` - The steps.
///
/// # Returns
///
/// The member or element, and its type.
///
/// # Errors
///
/// BadSize, if an element of an array whose count varies isn't there.
fn reach<'v>(
    v: &'v Val,
    vt: &Rc<VType>,
    path: &[PathStep],
) -> pk::Result<(&'v Val, Rc<VType>)> {
    let mut v = v;
    let mut vt = vt.clone();
    for s in path {
        match s {
            PathStep::Field(ident) => {
                let Encoding::Aggregate(e) = &vt.enc else {
                    unreachable!("an aggregate")
                };
                let i =
                    e.members.iter().position(|m| &m.ident == ident).unwrap();
                let Val::Agg(ms) = v else {
                    unreachable!("an aggregate value")
                };
                v = &ms[i];
                let next = e.members[i].vtype.clone();
                vt = next;
            }
            PathStep::Index(ix) => {
                let Encoding::Array(e) = &vt.enc else {
                    unreachable!("an array")
                };
                let Val::Arr(all, used) = v else {
                    unreachable!("an array value")
                };
                let es = &all[..*used];
                let bad = PacketError::BadSize { field: F };
                v = if ix.stride != 0 {
                    // An element of an array whose count varies, in a
                    // dimension of a fixed size.
                    let d = es.len() / ix.per as usize;
                    if d < ix.need as usize {
                        return Err(bad);
                    }
                    es.get(ix.i as usize + ix.stride as usize * d).ok_or(bad)?
                } else {
                    es.get(ix.i as usize).ok_or(bad)?
                };
                let next = e.elem.clone();
                vt = next;
            }
        }
    }
    Ok((v, vt))
}

/// Compares two values with an operator.
///
/// # Arguments
///
/// * `op` - The operator, such as `==`.
/// * `o`  - How the first compares with the second.
///
/// # Returns
///
/// Whether the comparison holds; false for values that don't compare.
fn holds(op: &str, o: Option<std::cmp::Ordering>) -> bool {
    use std::cmp::Ordering::*;
    match (op, o) {
        ("==", Some(Equal)) | ("!=", Some(Less | Greater)) => true,
        ("!=", None) => true,
        ("<", Some(Less)) | ("<=", Some(Less | Equal)) => true,
        (">", Some(Greater)) | (">=", Some(Greater | Equal)) => true,
        _ => false,
    }
}

/// Compares a value with a literal of the document.
///
/// # Arguments
///
/// * `v`   - The value.
/// * `vt`  - Its type.
/// * `lit` - The literal.
///
/// # Returns
///
/// How the value compares with it.
fn cmp_lit(v: &Val, vt: &VType, lit: &Lit) -> Option<std::cmp::Ordering> {
    match (v, lit) {
        (Val::Big(a), Lit::Int(b)) => Some(crate::wide::cmp_big(a, b)),
        (Val::Int(a), Lit::Int(b)) => match b.to_i128() {
            Some(b) => a.partial_cmp(&b),
            // Outside what any value holds: every value is on one side.
            None => Some(if b.sign() == num_bigint::Sign::Minus {
                std::cmp::Ordering::Greater
            } else {
                std::cmp::Ordering::Less
            }),
        },
        (Val::Enum(a), Lit::Int(b)) => match b.to_i64() {
            Some(b) => a.partial_cmp(&b),
            None => Some(if b.sign() == num_bigint::Sign::Minus {
                std::cmp::Ordering::Greater
            } else {
                std::cmp::Ordering::Less
            }),
        },
        (Val::Float(a), Lit::Float(b)) => a.partial_cmp(b),
        (Val::Float(a), Lit::Int(b)) => {
            a.partial_cmp(&b.to_f64().unwrap_or(f64::NAN))
        }
        (Val::Bool(a), Lit::Bool(b)) => a.partial_cmp(b),
        (Val::Str(a), Lit::Str(b)) => a.as_str().partial_cmp(b.as_str()),
        (Val::Bytes(a), Lit::Bytes(b)) => a.partial_cmp(b),
        _ => {
            let _ = vt;
            None
        }
    }
}

/// Compares two values of the packet.
///
/// # Arguments
///
/// * `a` - The first.
/// * `b` - The second.
///
/// # Returns
///
/// How the first compares with the second.
fn cmp_vals(a: &Val, b: &Val) -> Option<std::cmp::Ordering> {
    match (a, b) {
        (Val::Int(x), Val::Int(y)) => x.partial_cmp(y),
        (Val::Big(x), Val::Big(y)) => (**x).partial_cmp(&**y),
        (Val::Big(x), Val::Int(y)) => {
            (**x).partial_cmp(&crate::wide::from_i128(*y))
        }
        (Val::Int(x), Val::Big(y)) => crate::wide::from_i128(*x).partial_cmp(y),
        (Val::Enum(x), Val::Enum(y)) => x.partial_cmp(y),
        (Val::Float(x), Val::Float(y)) => x.partial_cmp(y),
        (Val::Bool(x), Val::Bool(y)) => x.partial_cmp(y),
        (Val::Str(x), Val::Str(y)) => x.partial_cmp(y),
        _ => None,
    }
}

/// Evaluates a criterion or a tree of them.
///
/// # Arguments
///
/// * `t`    - The term.
/// * `vals` - The values of the packet so far.
///
/// # Returns
///
/// Whether it holds. An absent value compares as C++'s `std::optional`
/// does: less than any value.
///
/// # Errors
///
/// BadSize, if it reads an element that isn't there.
pub(crate) fn term(t: &Term, vals: &dyn Look) -> pk::Result<bool> {
    match t {
        Term::AllOf(a) => {
            for x in &a.get().terms {
                if !term(x, vals)? {
                    return Ok(false);
                }
            }
            Ok(true)
        }
        Term::AnyOf(a) => {
            for x in &a.get().terms {
                if term(x, vals)? {
                    return Ok(true);
                }
            }
            Ok(false)
        }
        Term::Custom(c) => {
            let c = c.get();
            let f = vals.hooks().get(&c.hook.get())?;
            match f {
                Algorithm::Criterion(f) => with_inputs(&c.inputs, vals, f),
                _ => Err(PacketError::NoAlgorithm { name: F }),
            }
        }
        Term::Criterion(c) => criterion(&c.get(), vals),
    }
}

/// Evaluates one criterion.
///
/// # Arguments
///
/// * `c`    - The criterion.
/// * `vals` - The values of the packet so far.
///
/// # Returns
///
/// Whether it holds.
///
/// # Errors
///
/// BadSize, if it reads an element that isn't there.
fn criterion(c: &Criterion, vals: &dyn Look) -> pk::Result<bool> {
    let lvt = c.local.get().vt();
    let Some(v) = vals.val(c.local.get().id) else {
        // Absent: less than any value.
        return Ok(matches!(c.op.as_str(), "!=" | "<" | "<="));
    };
    let (x, xvt) = reach(v, &lvt, &c.path)?;
    if let Some(o) = &c.other {
        let ovt = o.get().vt();
        let Some(w) = vals.val(o.get().id) else {
            return Ok(false);
        };
        let (y, _) = reach(w, &ovt, &c.other_path)?;
        return Ok(holds(&c.op, cmp_vals(x, y)));
    }
    let lit = c.value.as_ref().expect("a literal");
    Ok(holds(&c.op, cmp_lit(x, &xvt, lit)))
}

/// Checks that one criterion holds.
///
/// # Arguments
///
/// * `t`         - The criterion.
/// * `vals`      - The values of the packet.
/// * `container` - The container it is of, for the error.
///
/// # Errors
///
/// Mismatch, if it doesn't hold.
fn require_term(t: &Term, vals: &dyn Look, container: &str) -> CodecResult<()> {
    if !term(t, vals).map_err(at(container))? {
        // Its text is made only when it fails.
        return Err(CodecError {
            error: PacketError::Mismatch {
                container: F,
                criterion: F,
            },
            field: format!("{container}: {}", t.text()),
        });
    }
    Ok(())
}

/// Checks that criteria hold.
///
/// # Arguments
///
/// * `terms`     - The criteria.
/// * `vals`      - The values of the packet.
/// * `container` - The container they are of, for the error.
/// * `text`      - The criteria as written, for the error.
///
/// # Errors
///
/// Mismatch, if one doesn't hold.
fn require(
    terms: &[Term],
    vals: &dyn Look,
    container: &str,
    text: &str,
) -> CodecResult<()> {
    for t in terms {
        if !term(t, vals).map_err(at(container))? {
            return Err(CodecError {
                error: PacketError::Mismatch {
                    container: F,
                    criterion: F,
                },
                field: format!("{container}: {text}"),
            });
        }
    }
    Ok(())
}

// --- checksums ---------------------------------------------------------

/// Gives the runtime's checksum of a check.
///
/// # Arguments
///
/// * `spec` - The check's algorithm.
/// * `vals` - The values of the packet, which carry the functions set.
///
/// # Returns
///
/// The runtime's.
///
/// # Errors
///
/// NoAlgorithm, for a custom one the application hasn't set.
fn checksum_spec(spec: &Spec, vals: &dyn Look) -> pk::Result<pk::Checksum> {
    use pk::Checksum as C;
    Ok(match spec {
        Spec::Crc(width, poly, init, fin, rd, rr) => C::Crc {
            width: *width as u32,
            poly: *poly,
            init: *init,
            final_xor: *fin,
            reflect_data: *rd,
            reflect_remainder: *rr,
        },
        Spec::Xor(bits) => C::Xor(*bits as u32),
        Spec::Parity(odd) => C::Parity(*odd),
        Spec::Named(name, bits) => match (name.as_str(), bits) {
            ("sum", Some(b)) => C::Sum(*b as u32),
            ("fletcher", Some(b)) => C::Fletcher(*b as u32),
            ("unix_sum", _) => C::UnixSum,
            ("adler32", _) => C::Adler32,
            ("luhn", _) => C::Luhn,
            ("verhoeff", _) => C::Verhoeff,
            _ => C::Damm,
        },
        Spec::Custom(h) => match vals.hooks().get(&h.get())? {
            Algorithm::Checksum(f) => C::Custom(f),
            _ => return Err(PacketError::NoAlgorithm { name: F }),
        },
    })
}

/// Gives the bits a check covers, as the generated code works them out.
///
/// # Arguments
///
/// * `c`     - The check.
/// * `marks` - The position of each Mark.
/// * `pos`   - The position of the check's field.
/// * `end`   - The end of the packet.
///
/// # Returns
///
/// The start and end of the bits.
fn region(
    c: &Check,
    marks: &[usize],
    pos: usize,
    end: usize,
) -> (usize, usize) {
    let base = if c.start == 0 {
        0
    } else {
        marks[c.start as usize]
    };
    let shifted = c.skip != 0 && !c.from_end;
    let start = if shifted {
        base + c.skip as usize
    } else {
        base
    };
    let last = match c.end {
        Some(m) => marks[m as usize],
        None => end,
    };
    let last = if let Some(stop) = c.stop {
        // The bits end before the transformed container with the field.
        marks[stop as usize].saturating_sub(c.stop_back as usize)
    } else if c.late.is_some() {
        // The field is before the bits, which run to the end.
        last
    } else if c.from_end {
        last.saturating_sub(c.skip as usize)
    } else if c.start == 0 && !shifted {
        // The field always follows the packet's start.
        pos
    } else if pos >= start {
        pos
    } else {
        last
    };
    (start, last)
}

/// Checks that one copy of a repeated container meets the container's
/// own criteria.
///
/// # Arguments
///
/// * `e`    - The aggregate of the copy, which holds the criteria.
/// * `copy` - The copy.
/// * `vals` - The values of the packet.
///
/// # Errors
///
/// Mismatch, if a criterion doesn't hold.
fn copy_checks(
    e: &tcs_xtce_model::model::AggregateEnc,
    copy: &Val,
    vals: &dyn Look,
) -> pk::Result<()> {
    if e.checks.is_empty() {
        return Ok(());
    }
    let this = e.this.as_ref().expect("the copy's own Local");
    let with = With {
        base: vals,
        id: this.get().id,
        v: copy,
    };
    for r in &e.checks {
        for t in &r.criteria {
            if !term(t, &with)? {
                return Err(PacketError::Mismatch {
                    container: F,
                    criterion: F,
                });
            }
        }
    }
    Ok(())
}

/// What the interpreter knows of a mission before it runs anything, made
/// once: for each node, what it can't run and its length field; for each
/// Local, the storage its values need and its raw type.
#[derive(Debug)]
pub struct Plan {
    /// One more than the largest number of a Local or Mark.
    size: usize,
    /// Each Local's storage, as it starts.
    templates: Vec<Val>,
    /// Each calibrated Local's raw type, by its number.
    raw_types: Vec<Option<Rc<VType>>>,
    /// Each calibrated Local's raw storage, as it starts.
    raw_templates: Vec<Val>,
    /// What each node is, by its address.
    nodes: HashMap<usize, NodeInfo>,
    /// The functions the application has supplied.
    hooks: HookTable,
    /// The Mark each transformed container starts at, and the most bytes
    /// of its items.
    transforms: Vec<(usize, usize)>,
    /// The most bytes of a repeated container's transformed data.
    xform_cap: usize,
    /// The most repeated containers with transforms inside each other.
    xform_depth: usize,
}

/// Gives how many repeated containers with transforms a type holds
/// inside each other, and the most bytes of their transformed data.
///
/// # Arguments
///
/// * `vt` - The type.
///
/// # Returns
///
/// The depth and the bytes.
fn xform_nesting(vt: &VType) -> (usize, usize) {
    match &vt.enc {
        Encoding::Aggregate(e) => {
            let (mut d, mut c) = (0, 0);
            for m in &e.members {
                let (md, mc) = xform_nesting(&m.vtype);
                d = d.max(md);
                c = c.max(mc);
            }
            match &e.transform {
                Some(x) => (d + 1, c.max(x.cap.max(0) as usize)),
                None => (d, c),
            }
        }
        Encoding::Array(e) => xform_nesting(&e.elem),
        _ => (0, 0),
    }
}

/// What the interpreter knows of a node.
#[derive(Debug, Clone)]
struct NodeInfo {
    /// What its own criteria use that the interpreter can't run.
    crit: Option<String>,
    /// What its own criteria and items, and if it is concrete its checks,
    /// use that the interpreter can't run.
    own: Option<String>,
    /// What it or its bases use that the interpreter can't run.
    all: Option<String>,
    /// Its packet length field, for a concrete node.
    length: Option<Shared<Local>>,
}

/// Calls a function on each Value item among some items, those inside
/// conditions and transforms too.
///
/// # Arguments
///
/// * `items` - The items.
/// * `f`     - The function.
fn each_value(items: &[Item], f: &mut dyn FnMut(&Shared<Local>)) {
    for it in items {
        match it {
            Item::Value(v) => f(&v.local),
            Item::Cond(c) => each_value(&c.items, f),
            Item::Transform(t) => each_value(&t.get().items, f),
            _ => {}
        }
    }
}

/// Keeps, once, the string terminators and byte order lists of a type
/// and those inside it, so that running packets keeps nothing new.
///
/// # Arguments
///
/// * `vt` - The type.
fn intern_type(vt: &VType) {
    match &vt.enc {
        Encoding::Int(e) => {
            byte_order(&e.order);
        }
        Encoding::Float(e) => {
            byte_order(&e.order);
        }
        Encoding::String(e) => {
            spec(e);
        }
        Encoding::Aggregate(e) => {
            for m in &e.members {
                intern_type(&m.vtype);
            }
        }
        Encoding::Array(e) => intern_type(&e.elem),
        _ => {}
    }
}

impl Plan {
    /// Makes the plan of a mission's nodes.
    ///
    /// # Arguments
    ///
    /// * `nodes` - Every node of the mission.
    /// * `size`  - One more than the largest number of a Local or Mark.
    /// * `hooks` - The hooks of the mission's custom algorithms.
    ///
    /// # Returns
    ///
    /// The plan.
    pub fn new<'n>(
        nodes: impl Iterator<Item = &'n Shared<Node>>,
        size: usize,
        hooks: &[Shared<Hook>],
    ) -> Plan {
        let mut index = HashMap::new();
        for h in hooks {
            let n = index.len();
            index.entry(h.get().ident.clone()).or_insert(n);
        }
        let mut p = Plan {
            size,
            templates: vec![Val::Bool(false); size],
            raw_types: vec![None; size],
            raw_templates: vec![Val::Bool(false); size],
            nodes: HashMap::new(),
            hooks: HookTable {
                fns: vec![None; index.len()],
                index: Rc::new(index),
            },
            transforms: Vec::new(),
            xform_cap: 0,
            xform_depth: 0,
        };
        for n in nodes {
            let m = n.get();
            for it in walk(&m.items, true) {
                if let Item::Transform(t) = it {
                    let t = t.get();
                    p.transforms
                        .push((t.start as usize, t.cap.max(0) as usize));
                }
            }
            let info = NodeInfo {
                crit: m
                    .criteria
                    .iter()
                    .find_map(term_unsupported)
                    .map(|w| format!("{}: {w}", m.key)),
                own: node_unsupported(n),
                all: unsupported(n),
                length: if m.abstract_ { None } else { length_field(n) },
            };
            p.nodes.insert(n.addr(), info);
            each_value(&m.items, &mut |l| {
                let l = l.get();
                let vt = l.vt();
                let id = l.id as usize;
                intern_type(&vt);
                let (d, c) = xform_nesting(&vt);
                p.xform_depth = p.xform_depth.max(d);
                p.xform_cap = p.xform_cap.max(c);
                p.templates[id] = template(&vt);
                if calibrated(&vt) {
                    let rt = Rc::new(raw_type(&vt));
                    p.raw_templates[id] = template(&rt);
                    p.raw_types[id] = Some(rt);
                }
            });
        }
        p
    }

    /// Gives what the interpreter knows of a node.
    ///
    /// # Arguments
    ///
    /// * `n` - The node, one of the plan's.
    ///
    /// # Returns
    ///
    /// What it knows.
    fn info(&self, n: &Shared<Node>) -> &NodeInfo {
        &self.nodes[&n.addr()]
    }

    /// Gives a calibrated Local's raw type.
    ///
    /// # Arguments
    ///
    /// * `id` - The Local's number.
    ///
    /// # Returns
    ///
    /// The type, or `None` for a Local that isn't calibrated.
    pub fn raw_type(&self, id: i64) -> Option<&Rc<VType>> {
        self.raw_types[id as usize].as_ref()
    }

    /// Adds a hook that no packet calls, such as a stream's.
    ///
    /// # Arguments
    ///
    /// * `ident` - Its name.
    pub fn add_hook(&mut self, ident: &str) {
        let index = Rc::make_mut(&mut self.hooks.index);
        if !index.contains_key(ident) {
            index.insert(ident.to_string(), self.hooks.fns.len());
            self.hooks.fns.push(None);
        }
    }

    /// Gives the function set for a hook.
    ///
    /// # Arguments
    ///
    /// * `ident` - The hook's name.
    ///
    /// # Returns
    ///
    /// The function, or `None` if none is set.
    pub fn hook(&self, ident: &str) -> Option<Algorithm> {
        self.hooks.index.get(ident).and_then(|&i| self.hooks.fns[i])
    }

    /// Sets the function for a hook.
    ///
    /// # Arguments
    ///
    /// * `ident` - The hook's name in the generated code.
    /// * `f`     - The function.
    ///
    /// # Returns
    ///
    /// Whether the plan has the hook.
    pub fn set_hook(&mut self, ident: &str, f: Algorithm) -> bool {
        match self.hooks.index.get(ident) {
            Some(&i) => {
                self.hooks.fns[i] = Some(f);
                true
            }
            None => false,
        }
    }

    /// Says whether a hook's function is set.
    ///
    /// # Arguments
    ///
    /// * `ident` - The hook's name in the generated code.
    ///
    /// # Returns
    ///
    /// Whether it is.
    pub fn hook_set(&self, ident: &str) -> bool {
        self.hooks
            .index
            .get(ident)
            .is_some_and(|&i| self.hooks.fns[i].is_some())
    }

    /// Says what a packet uses that the interpreter can't run.
    ///
    /// # Arguments
    ///
    /// * `n` - The node, one of the plan's.
    ///
    /// # Returns
    ///
    /// What it uses, or `None` if it can be decoded and encoded.
    pub fn unsupported(&self, n: &Shared<Node>) -> Option<&str> {
        self.info(n).all.as_deref()
    }
}

/// Storage that decoding and encoding reuse, made once from a plan: the
/// values of a packet and their raw values, the positions of its marks,
/// checksum fields and computed fields, and buffers for strings.
#[derive(Debug, Clone)]
pub struct Workspace {
    /// The values of the packet, by the number of their Locals.
    pub vals: Values,
    /// The raw values of its calibrated values.
    pub raws: Values,
    /// The position of each Mark.
    marks: Vec<usize>,
    /// The position of each checksum field.
    sums: Vec<usize>,
    /// The bits read so far of each value read in segments, or its
    /// encoding when encoding.
    segs: Vec<u64>,
    /// Where the container each length field of an included container
    /// measures must end.
    spans: Vec<i128>,
    /// The position of each computed field, when encoding.
    computed: Vec<usize>,
    /// Whether each value is a size or count the packet computes from
    /// what it sizes, when encoding.
    derived: Vec<bool>,
    /// Buffers for strings.
    bufs: Bufs,
    /// The data of each transformed container, by the Mark it starts at.
    tbufs: Vec<Vec<u8>>,
    /// Where each transformed container starts, when encoding.
    tstart: Vec<usize>,
    /// The bytes of each transformed container's data kept before it was
    /// transformed, when encoding.
    kept: Vec<usize>,
}

impl Workspace {
    /// Makes the storage for a plan's packets.
    ///
    /// # Arguments
    ///
    /// * `p` - The plan.
    ///
    /// # Returns
    ///
    /// The storage.
    pub fn new(p: &Plan) -> Workspace {
        let wire = p
            .templates
            .iter()
            .map(|t| match t {
                Val::Str(s) => s.capacity(),
                _ => 0,
            })
            .max()
            .unwrap_or(0);
        Workspace {
            vals: {
                let mut v = Values::like(&p.templates);
                v.hooks = p.hooks.clone();
                v
            },
            raws: Values::like(&p.raw_templates),
            marks: vec![0; p.size],
            sums: vec![0; p.size],
            segs: vec![0; p.size],
            spans: vec![0; p.size],
            computed: vec![0; p.size],
            derived: vec![false; p.size],
            bufs: Bufs {
                wire: Vec::with_capacity(4 * wire + 8),
                text: String::with_capacity(4 * wire + 8),
                xforms: (0..p.xform_depth)
                    .map(|_| Vec::with_capacity(p.xform_cap))
                    .collect(),
            },
            tbufs: {
                let mut t = vec![Vec::new(); p.size];
                for &(m, cap) in &p.transforms {
                    t[m] = Vec::with_capacity(cap);
                }
                t
            },
            tstart: vec![0; p.size],
            kept: vec![0; p.size],
        }
    }
}

/// The state of a decoding.
struct Dec<'d, 'w> {
    /// The reader.
    r: BitReader<'d>,
    /// The storage.
    ws: &'w mut Workspace,
    /// The plan.
    plan: &'w Plan,
}

impl Dec<'_, '_> {
    /// Gives the position of a Mark.
    ///
    /// # Arguments
    ///
    /// * `m` - The Mark's number; 0 is the packet's start.
    ///
    /// # Returns
    ///
    /// The position.
    fn mark(&self, m: i64) -> usize {
        if m == 0 {
            0
        } else {
            self.ws.marks[m as usize]
        }
    }

    /// Checks that a check's field holds the checksum of what it covers.
    ///
    /// # Arguments
    ///
    /// * `c` - The check.
    ///
    /// # Errors
    ///
    /// BadChecksum, if it doesn't.
    fn verify(&self, c: &Check) -> CodecResult<()> {
        let l = c.local.get();
        let Encoding::Int(e) = &l.vt().enc else {
            unreachable!("an integer")
        };
        let pos = self.ws.sums[l.id as usize];
        let (start, end) = region(c, &self.ws.marks, pos, self.r.packet_end());
        let x = self
            .r
            .checksum(
                &checksum_spec(&c.spec, &self.ws.vals).map_err(at(&l.name))?,
                start,
                end,
                F,
            )
            .map_err(at(&l.name))?;
        let value = match self.ws.vals.val(l.id) {
            Some(Val::Int(v)) => Some(*v as u64),
            // A wider field holds the value with zeros above it.
            Some(Val::Big(v)) => crate::wide::to_u64(v),
            _ => Some(0),
        };
        if l.vt().wide() {
            if Some(x) != value {
                return Err(at(&l.name)(PacketError::BadChecksum { field: F }));
            }
            return Ok(());
        }
        let value = value.unwrap_or(0);
        let mask = if e.bits >= 64 {
            u64::MAX
        } else {
            (1u64 << e.bits) - 1
        };
        if x & mask != value {
            return Err(at(&l.name)(PacketError::BadChecksum { field: F }));
        }
        Ok(())
    }

    /// Reads one value of the packet into its storage.
    ///
    /// # Arguments
    ///
    /// * `l`         - Its Local.
    /// * `assembled` - The bits of its encoding if it is assembled from
    ///                 segments, or 0.
    ///
    /// # Errors
    ///
    /// The runtime's.
    fn value(&mut self, l: &Local, assembled: i64) -> CodecResult<()> {
        let vt = l.vt();
        let id = l.id;
        if l.checksum {
            self.ws.sums[id as usize] = self.r.position();
        }
        // A calibrated value's raw value too, from the same bits.
        let mut raw_r = self.r.clone();
        let ws = &mut *self.ws;
        let mut x = ws.vals.take(id);
        let got = if assembled != 0 {
            // Decoded from its segments' bits.
            let mut b = [0u8; 8];
            let mut r = pk::segment_reader(
                &mut b,
                ws.segs[id as usize],
                assembled as u32,
            );
            read_into(&mut r, &vt, &mut x, &ws.vals, OUTER, &mut ws.bufs)
        } else {
            read_into(&mut self.r, &vt, &mut x, &ws.vals, OUTER, &mut ws.bufs)
        };
        if let Err(e) = got {
            ws.vals.restore(id, x);
            return Err(at(&l.name)(e));
        }
        if let Some(rt) = &self.plan.raw_types[id as usize] {
            let mut raw = ws.raws.take(id);
            let got = if assembled != 0 {
                let mut b = [0u8; 8];
                let mut r = pk::segment_reader(
                    &mut b,
                    ws.segs[id as usize],
                    assembled as u32,
                );
                read_into(&mut r, rt, &mut raw, &ws.vals, OUTER, &mut ws.bufs)
            } else {
                read_into(
                    &mut raw_r,
                    rt,
                    &mut raw,
                    &ws.vals,
                    OUTER,
                    &mut ws.bufs,
                )
            };
            match got {
                Ok(()) => ws.raws.put(id, raw),
                Err(e) => {
                    ws.raws.restore(id, raw);
                    ws.vals.restore(id, x);
                    return Err(at(&l.name)(e));
                }
            }
        }
        let n = match &x {
            Val::Int(n) => Some(*n as u64),
            // A wider field must hold a length a u64 holds.
            Val::Big(n) => match crate::wide::to_u64(n) {
                Some(n) => Some(n),
                None if l.length.is_some() => {
                    ws.vals.restore(id, x);
                    return Err(at(&l.name)(PacketError::BadLength {
                        field: F,
                    }));
                }
                None => None,
            },
            _ => None,
        };
        ws.vals.put(id, x);
        if let (Some(adj), Some(n)) = (l.length, n) {
            if l.measures {
                let e = self.r.span_end(n, adj, F).map_err(at(&l.name))?;
                self.ws.spans[id as usize] = e;
            } else {
                self.r.limit(n, adj, F).map_err(at(&l.name))?;
            }
        }
        Ok(())
    }

    /// Reads items.
    ///
    /// # Arguments
    ///
    /// * `items`  - The items.
    /// * `where_` - The container they are in, for errors.
    ///
    /// # Errors
    ///
    /// The runtime's, or Mismatch if criteria don't hold.
    fn items(&mut self, items: &[Item], where_: &str) -> CodecResult<()> {
        for it in items {
            match it {
                Item::Seek(s) => {
                    let off =
                        seek_offset(s, &self.ws.vals).map_err(at(where_))?;
                    match s.from_start {
                        None => self.r.skip(off, F),
                        Some(m) => {
                            let at_ = self.mark(m);
                            if s.ahead {
                                self.r.seek_ahead(at_, off, F)
                            } else {
                                self.r.seek(at_, off, F)
                            }
                        }
                    }
                    .map_err(at(where_))?;
                }
                Item::Mark(m) => {
                    self.ws.marks[m.id as usize] = self.r.position()
                }
                Item::Pad(p) => {
                    self.r.pad(p.bits as usize, F).map_err(at(where_))?
                }
                Item::Fixed(f) => {
                    self.r.uint(f.bits as u32, F).map_err(at(where_))?;
                }
                Item::Require(q) => {
                    require(&q.criteria, &self.ws.vals, &q.container, &q.text)?
                }
                Item::Fill(f) => {
                    let n = count_of(&f.size, &self.ws.vals, OUTER)
                        .map_err(at(&f.container))?;
                    let e = self.mark(f.start) + n as usize;
                    if self.r.position() > e {
                        return Err(at(&f.container)(PacketError::BadSize {
                            field: F,
                        }));
                    }
                    let p = e - self.r.position();
                    self.r.pad(p, F).map_err(at(&f.container))?;
                }
                Item::Verify(v) => self.verify(&v.check.get())?,
                Item::TailSeek(t) => {
                    let back =
                        tail_back(t, &self.ws.vals).map_err(at(where_))?;
                    self.r.seek_end(back, F).map_err(at(where_))?;
                }
                // The packet's last bits, which encoding fills with zeros.
                Item::TailPad(t) => {
                    self.r.skip(t.bits, F).map_err(at(where_))?
                }
                Item::LengthEnd(e) => {
                    let l = e.local.get();
                    let end = self.ws.spans[l.id as usize];
                    self.r.check_span(end, F).map_err(at(&l.name))?;
                }
                Item::Segment(g) => {
                    let g = g.get();
                    let l = g.local.get();
                    let bits =
                        self.r.uint(g.bits as u32, F).map_err(at(&l.name))?;
                    let part = bits << g.shift;
                    let i = l.id as usize;
                    self.ws.segs[i] = if g.first {
                        part
                    } else {
                        self.ws.segs[i] | part
                    };
                }
                Item::Value(v) => self.value(&v.local.get(), v.assembled)?,
                Item::Transform(t) => self.transform(&t.get(), where_)?,
                Item::Cond(c) => {
                    let mut ok = true;
                    for t in &c.criteria {
                        if !term(t, &self.ws.vals).map_err(at(where_))? {
                            ok = false;
                            break;
                        }
                    }
                    if ok {
                        self.items(&c.items, where_)?;
                    }
                }
                _ => unreachable!("a supported item"),
            }
        }
        Ok(())
    }

    /// Reads a transformed container's items from what its decoding
    /// algorithm makes of its bytes.
    ///
    /// # Arguments
    ///
    /// * `t`      - The transformed container.
    /// * `where_` - The container it is in, for errors.
    ///
    /// # Errors
    ///
    /// The runtime's, or Mismatch if criteria don't hold.
    fn transform(&mut self, t: &Transform, where_: &str) -> CodecResult<()> {
        let i = t.start as usize;
        let mut buf = std::mem::take(&mut self.ws.tbufs[i]);
        buf.clear();
        buf.resize(t.cap.max(0) as usize, 0);
        let got = self.transformed(t, &mut buf, where_);
        self.ws.tbufs[i] = buf;
        got
    }

    /// Reads a transformed container's items, its data decoded into a
    /// buffer.
    ///
    /// # Arguments
    ///
    /// * `t`      - The transformed container.
    /// * `buf`    - The buffer, as long as the most bytes of its items.
    /// * `where_` - The container it is in, for errors.
    ///
    /// # Errors
    ///
    /// The runtime's, or Mismatch if criteria don't hold.
    fn transformed(
        &mut self,
        t: &Transform,
        buf: &mut [u8],
        where_: &str,
    ) -> CodecResult<()> {
        let f = binary_from(&t.decode, &self.ws.vals).map_err(at(&t.name))?;
        let raw = t.raw.map(|r| r as usize);
        let n = self.r.binary_from(f, raw, buf, F).map_err(at(&t.name))?;
        let mut d = Dec {
            r: BitReader::new(&buf[..n]),
            ws: &mut *self.ws,
            plan: self.plan,
        };
        d.items(&t.items, where_)?;
        if let Some(l) = &t.length {
            d.r.finish(F).map_err(at(&l.get().name))?;
        }
        Ok(())
    }

    /// Ends the decoding of a concrete packet: its length field's check,
    /// then its checks.
    ///
    /// # Arguments
    ///
    /// * `n` - The packet.
    ///
    /// # Errors
    ///
    /// BadLength, if the packet length field disagrees with the packet;
    /// BadChecksum, if a check fails.
    fn finish(&self, n: &Shared<Node>) -> CodecResult<()> {
        if let Some(l) = &self.plan.info(n).length {
            self.r.finish(F).map_err(at(&l.get().name))?;
        }
        for c in &n.get().checks {
            self.verify(&c.get())?;
        }
        Ok(())
    }

    /// Reads the entries of a node's bases and then its own, each node's
    /// criteria checked first.
    ///
    /// # Arguments
    ///
    /// * `n` - The node.
    ///
    /// # Errors
    ///
    /// The runtime's, or Mismatch if criteria don't hold.
    fn chain(&mut self, n: &Shared<Node>) -> CodecResult<()> {
        let m = n.get();
        if let Some(b) = &m.base {
            self.chain(b)?;
        }
        for c in &m.criteria {
            require_term(c, &self.ws.vals, &m.key)?;
        }
        self.items(&m.items, &m.key)
    }
}

/// Finds a packet's length field: not one in transformed data.
///
/// # Arguments
///
/// * `n` - The packet.
///
/// # Returns
///
/// The field, if the packet has one.
fn length_field(n: &Shared<Node>) -> Option<Shared<Local>> {
    walk(&n.all_items(), false)
        .into_iter()
        .find_map(|it| match it {
            Item::Value(v) => {
                let l = v.local.get();
                (l.length.is_some() && !l.measures).then(|| v.local.clone())
            }
            _ => None,
        })
}

/// A decoded packet: its node and the bytes it took. Its values are in
/// the workspace it was decoded into.
#[derive(Debug, Clone)]
pub struct Decoded {
    /// The concrete packet decoded.
    pub node: Shared<Node>,
    /// The bytes it took.
    pub used: usize,
}

/// Gives a calibrated Local's raw type.
///
/// # Arguments
///
/// * `plan` - The plan.
/// * `id`   - The Local's number.
///
/// # Returns
///
/// The type, or `None` for a Local that isn't calibrated.
pub fn raw_type_of(plan: &Plan, id: i64) -> Option<Rc<VType>> {
    plan.raw_type(id).cloned()
}

/// Starts a decoding.
///
/// # Arguments
///
/// * `data` - The bytes.
/// * `plan` - The plan.
/// * `ws`   - The storage, whose values are all made absent.
///
/// # Returns
///
/// The decoding.
fn start<'d, 'w>(
    data: &'d [u8],
    plan: &'w Plan,
    ws: &'w mut Workspace,
) -> Dec<'d, 'w> {
    ws.vals.clear();
    ws.raws.clear();
    ws.vals.set_hooks(&plan.hooks);
    Dec {
        r: BitReader::new(data),
        ws,
        plan,
    }
}

/// Checks that the application has set every function a packet's
/// decoding or encoding calls, as the generated code checks before it
/// starts.
///
/// # Arguments
///
/// * `n`    - The packet.
/// * `plan` - The plan.
///
/// # Errors
///
/// NoAlgorithm, naming the first that isn't set.
fn need_hooks(n: &Node, plan: &Plan) -> CodecResult<()> {
    for h in &n.hooks {
        let h = h.get();
        if !plan.hook_set(&h.ident) {
            return Err(at(&h.name)(PacketError::NoAlgorithm { name: F }));
        }
    }
    Ok(())
}

/// Decodes a concrete packet into a workspace.
///
/// # Arguments
///
/// * `n`    - The packet, which the plan says it can run.
/// * `data` - Its bytes.
/// * `plan` - The plan.
/// * `ws`   - Where its values go.
///
/// # Returns
///
/// The decoded packet.
///
/// # Errors
///
/// The runtime's, or Mismatch if its criteria don't hold.
pub fn decode(
    n: &Shared<Node>,
    data: &[u8],
    plan: &Plan,
    ws: &mut Workspace,
) -> CodecResult<Decoded> {
    need_hooks(&n.get(), plan)?;
    let mut d = start(data, plan, ws);
    d.chain(n)?;
    d.finish(n)?;
    Ok(Decoded {
        node: n.clone(),
        used: d.r.used(),
    })
}

/// Decodes a packet of an inheritance tree into a workspace: the entries
/// of a node and its bases, then the first child (in document order)
/// whose criteria match, recursively.
///
/// # Arguments
///
/// * `root` - The node to start from: a tree's root, or any node in it.
/// * `data` - The bytes.
/// * `plan` - The plan.
/// * `ws`   - Where its values go.
///
/// # Returns
///
/// The decoded packet, of the concrete node it matched, or the error
/// that decoding gave: NoMatch, if it matches no concrete node, or the
/// runtime's; or, outside both, what the packet leads to that the
/// interpreter can't run yet.
pub fn classify(
    root: &Shared<Node>,
    data: &[u8],
    plan: &Plan,
    ws: &mut Workspace,
) -> Classified {
    let mut n = Some(root.clone());
    while let Some(m) = n {
        if let Some(w) = &plan.info(&m).own {
            return Err(w.clone());
        }
        n = m.get().base.clone();
    }
    let mut d = start(data, plan, ws);
    if let Err(e) = d.chain(root) {
        return Ok(Err(e));
    }
    dispatch(root, &mut d)
}

/// Finds the first construct of a node's own criteria and items that the
/// interpreter can't run, and if it is concrete, of its checks.
///
/// # Arguments
///
/// * `n` - The node.
///
/// # Returns
///
/// What it is, or `None` if it can be run.
fn node_unsupported(n: &Shared<Node>) -> Option<String> {
    let m = n.get();
    m.criteria
        .iter()
        .find_map(term_unsupported)
        .or_else(|| items_unsupported(&m.items))
        .map(|w| format!("{}: {w}", m.key))
}

/// Decodes the children of a node, picking the first whose criteria
/// match, recursively.
///
/// # Arguments
///
/// * `n` - The node whose entries are decoded.
/// * `d` - The decoding.
///
/// # Returns
///
/// As [`classify`]'s.
fn dispatch(n: &Shared<Node>, d: &mut Dec<'_, '_>) -> Classified {
    let m = n.get();
    for c in &m.children {
        let cm = c.get();
        if cm.error.is_some() {
            continue;
        }
        // Its criteria are tested if they can be: a packet it doesn't
        // match never needs what else it has.
        let info = d.plan.info(c);
        if let Some(w) = &info.crit {
            return Err(w.clone());
        }
        let mut ok = true;
        for t in &cm.criteria {
            match term(t, &d.ws.vals) {
                Ok(true) => {}
                Ok(false) => {
                    ok = false;
                    break;
                }
                Err(e) => return Ok(Err(at(&cm.key)(e))),
            }
        }
        if ok {
            if let Some(w) = &info.own {
                return Err(w.clone());
            }
            if let Err(e) = d.items(&cm.items, &cm.key) {
                return Ok(Err(e));
            }
            drop(cm);
            return dispatch(c, d);
        }
    }
    if m.abstract_ {
        return Ok(Err(CodecError {
            error: PacketError::NoMatch { container: F },
            field: m.key.clone(),
        }));
    }
    drop(m);
    if let Err(e) = d.finish(n) {
        return Ok(Err(e));
    }
    Ok(Ok(Decoded {
        node: n.clone(),
        used: d.r.used(),
    }))
}

// --- encoding ----------------------------------------------------------

/// Checks a value against its type's valid ranges, as encoding does.
///
/// # Arguments
///
/// * `vt` - The type.
/// * `v`  - The value.
///
/// # Errors
///
/// OutOfRange, if it lies in none of them.
pub(crate) fn check_valid(vt: &VType, v: &Val) -> pk::Result<()> {
    if vt.valid.is_empty() || !(vt.kind == "int" || vt.kind == "float") {
        return Ok(());
    }
    let num = |n: &Num| match n {
        Num::Int(i) => i.to_f64().unwrap_or(f64::NAN),
        Num::Float(x) => *x,
    };
    let int = |n: &Num| match n {
        Num::Int(i) => i.to_i128().unwrap_or(0),
        Num::Float(x) => *x as i128,
    };
    // How a wide value compares with a bound.
    let big = |x: &crate::wide::WideInt, n: &Num| match n {
        Num::Int(i) => crate::wide::cmp_big(x, i),
        Num::Float(f) => x.cmp(&crate::wide::from_i128(*f as i128)),
    };
    let ok = vt.valid.iter().any(|b| match v {
        Val::Big(x) => {
            use std::cmp::Ordering::{Greater, Less};
            b.lo.as_ref().is_none_or(|lo| big(x, lo) != Less)
                && b.hi.as_ref().is_none_or(|hi| big(x, hi) != Greater)
        }
        Val::Int(x) => {
            b.lo.as_ref().is_none_or(|lo| int(lo) <= *x)
                && b.hi.as_ref().is_none_or(|hi| *x <= int(hi))
        }
        Val::Float(x) => pk::in_band(
            *x,
            b.lo.as_ref().map(num),
            b.lo_incl,
            b.hi.as_ref().map(num),
            b.hi_incl,
        ),
        _ => true,
    });
    if ok {
        Ok(())
    } else {
        Err(PacketError::OutOfRange { field: F })
    }
}

/// Writes one value.
///
/// # Arguments
///
/// * `w`     - The writer.
/// * `vt`    - Its type.
/// * `v`     - The value.
/// * `vals`  - The values of the packet so far, which sizes come from.
/// * `inner` - The members of the aggregate the value is in, which a member's
///             size may come from.
///
/// # Errors
///
/// OutOfRange, if it doesn't fit its type; NotInvertible, if its
/// calibrator can't be inverted; BadSize, if an array or binary value
/// isn't its size.
fn write_val(
    w: &mut BitWriter<'_>,
    vt: &VType,
    v: &Val,
    vals: &dyn Look,
    inner: Inner<'_>,
) -> pk::Result<()> {
    if let Val::RawInt(_) | Val::RawFloat(_) = v {
        // Written as it is, checked against its encoding only.
        return write_raw(w, vt, v);
    }
    check_valid(vt, v)?;
    write_unchecked(w, vt, v, vals, inner)
}

/// Writes a raw value as it is, as its encoding holds it: not
/// calibrated, and checked against the encoding only.
///
/// # Arguments
///
/// * `w`  - The writer.
/// * `vt` - The value's type.
/// * `v`  - The raw value: `RawInt` for an integer encoding, `RawFloat` for a
///          float encoding.
///
/// # Errors
///
/// OutOfRange, if the encoding can't hold it.
fn write_raw(w: &mut BitWriter<'_>, vt: &VType, v: &Val) -> pk::Result<()> {
    let bad = PacketError::OutOfRange { field: F };
    match (&vt.enc, v) {
        (Encoding::Int(e), Val::RawInt(x)) if e.bits > 64 => {
            let enc = int_enc(vt);
            let w_ = crate::wide::Wide {
                bits: e.bits as u32,
                lsb: e.lsb,
                order: byte_order(&e.order),
                enc,
                bcd: bcd(&e.encoding),
                signed: enc != IntEncoding::Unsigned,
            };
            let bytes = crate::wide::raw_bytes(e.bits as u32);
            crate::wide::write(w, w_, bytes, &crate::wide::from_i128(*x))
        }
        (Encoding::Int(e), Val::RawInt(x)) => {
            let bits = e.bits as u32;
            let enc = int_enc(vt);
            let mut raw = if enc == IntEncoding::Unsigned {
                pk::from_unsigned(u64::try_from(*x).map_err(|_| bad)?, bits, F)?
            } else {
                pk::from_signed(
                    i64::try_from(*x).map_err(|_| bad)?,
                    bits,
                    enc,
                    F,
                )?
            };
            if let Some(packed) = bcd(&e.encoding) {
                raw = pk::bcd_encode(raw, bits, packed, F)?;
            }
            raw_write(w, e.bits, &e.order, e.lsb, raw as u128);
            Ok(())
        }
        (Encoding::Float(e), Val::RawFloat(x)) => {
            let raw = if e.fmt == "IEEE754" && (e.bits == 32 || e.bits == 64) {
                pk::float_to_raw(*x, e.bits as u32) as u128
            } else {
                let r = pk::float_encode(
                    float_format(&e.fmt),
                    e.bits as u32,
                    *x,
                    F,
                )?;
                if e.bits > 64 {
                    r
                } else {
                    r as u64 as u128
                }
            };
            raw_write(w, e.bits, &e.order, e.lsb, raw);
            Ok(())
        }
        _ => Err(bad),
    }
}

/// Says whether a type, or one inside it, has a calibrator.
///
/// # Arguments
///
/// * `vt` - The type.
///
/// # Returns
///
/// Whether it has.
pub fn calibrated(vt: &VType) -> bool {
    vt.calibrator.is_some()
        || match &vt.enc {
            Encoding::Aggregate(e) => {
                e.members.iter().any(|m| calibrated(&m.vtype))
            }
            Encoding::Array(e) => calibrated(&e.elem),
            _ => false,
        }
}

/// Gives the raw type of a type: the type its raw value has, with no
/// calibrator and no valid ranges, and the kind of its encoding: an
/// integer for an integer encoding and a float for a float encoding.
///
/// # Arguments
///
/// * `vt` - The type.
///
/// # Returns
///
/// The raw type.
pub fn raw_type(vt: &VType) -> VType {
    let mut r = vt.clone();
    r.calibrator = None;
    r.valid.clear();
    r.eng = None;
    match &mut r.enc {
        Encoding::Int(_) => r.kind = "int".into(),
        Encoding::Float(_) => r.kind = "float".into(),
        Encoding::Aggregate(e) => {
            for m in &mut e.members {
                m.vtype = Rc::new(raw_type(&m.vtype));
            }
        }
        Encoding::Array(e) => e.elem = Rc::new(raw_type(&e.elem)),
        _ => {}
    }
    r
}

/// Writes one value without checking it against its type's valid ranges,
/// as encoding does for a size it computes from what it sizes.
///
/// # Arguments
///
/// * `w`     - The writer.
/// * `vt`    - Its type.
/// * `v`     - The value.
/// * `vals`  - The values of the packet, which sizes come from.
/// * `inner` - The members of the aggregate the value is in, which a member's
///             size may come from.
///
/// # Errors
///
/// As [`write_val`]'s.
fn write_unchecked(
    w: &mut BitWriter<'_>,
    vt: &VType,
    v: &Val,
    vals: &dyn Look,
    inner: Inner<'_>,
) -> pk::Result<()> {
    if lsb_bytes(vt) {
        w.set_lsb(true);
        let r = write_plain(w, vt, v, vals, inner);
        w.set_lsb(false);
        return r;
    }
    write_plain(w, vt, v, vals, inner)
}

/// Writes one value, in the writer's usual bit order.
///
/// # Arguments
///
/// * `w`     - The writer.
/// * `vt`    - Its type.
/// * `v`     - The value.
/// * `vals`  - The values of the packet so far, which sizes come from.
/// * `inner` - The members of the aggregate the value is in, which a member's
///             size may come from.
///
/// # Errors
///
/// As [`write_val`]'s.
fn write_plain(
    w: &mut BitWriter<'_>,
    vt: &VType,
    v: &Val,
    vals: &dyn Look,
    inner: Inner<'_>,
) -> pk::Result<()> {
    let bad = PacketError::BadSize { field: F };
    match (&vt.enc, v) {
        (Encoding::Aggregate(e), Val::Agg(ms)) => {
            copy_checks(e, v, vals)?;
            // A transform is of the members written.
            let t = w.position();
            for (m, x) in e.members.iter().zip(ms) {
                if m.skip != 0 {
                    w.zeros(m.skip as usize);
                }
                write_val(w, &m.vtype, x, vals, (&e.members, ms))?;
            }
            if e.pad != 0 {
                w.zeros(e.pad as usize);
            }
            if let Some(x) = &e.transform {
                let f = binary_to(&x.encode, vals)?;
                let raw = x.raw.map(|r| r as usize);
                w.transform(t, f, raw, raw.unwrap_or(x.cap as usize), F)?;
            }
            Ok(())
        }
        (Encoding::Xform(e), _) => {
            let raw = e.raw.map(|r| r as usize);
            let cap = raw.unwrap_or(e.cap.max(0) as usize);
            match v {
                Val::Float(x) if e.time => {
                    w.time_to(time_to(&e.encode, vals)?, *x, raw, cap, F)
                }
                Val::Bytes(b) if !e.time => {
                    w.binary_to(binary_to(&e.encode, vals)?, b, raw, cap, F)
                }
                _ => Err(PacketError::OutOfRange { field: F }),
            }
        }
        (Encoding::Array(e), Val::Arr(all, used)) => {
            let es = &all[..*used];
            let n = count_of(&e.count, vals, inner)?;
            if es.len() != n as usize {
                return Err(bad);
            }
            for (i, x) in es.iter().enumerate() {
                if i > 0 && !matches!(e.gap, Count::Int(0)) {
                    w.skip(count_of(&e.gap, vals, inner)?, F)?;
                }
                write_val(w, &e.elem, x, vals, inner)?;
            }
            Ok(())
        }
        (Encoding::Int(e), _) if e.bits > 64 => {
            let w_ = crate::wide::Wide {
                bits: e.bits as u32,
                lsb: e.lsb,
                order: byte_order(&e.order),
                enc: int_enc(vt),
                bcd: bcd(&e.encoding),
                signed: vt.signed(),
            };
            match v {
                Val::Float(x) => {
                    let y = uncalibrate(vt, *x, vals)?;
                    crate::wide::write_f64(w, w_, y)
                }
                Val::Int(x) if vt.calibrator.is_some() => {
                    let y = uncalibrate(vt, *x as f64, vals)?;
                    crate::wide::write_f64(w, w_, y)
                }
                Val::Bool(b) => {
                    let n = ((e.bits + 8) / 8) as usize;
                    let w_ = crate::wide::Wide {
                        signed: false,
                        bcd: None,
                        ..w_
                    };
                    let x = crate::wide::from_i128(*b as i128);
                    crate::wide::write(w, w_, n, &x)
                }
                Val::Big(x) => {
                    crate::wide::write(w, w_, (vt.width() / 8) as usize, x)
                }
                _ => Err(PacketError::OutOfRange { field: F }),
            }
        }
        (Encoding::Int(e), _) => {
            let bits = e.bits as u32;
            let enc = int_enc(vt);
            let mut raw = match (vt.kind.as_str(), v) {
                ("int", Val::Int(x)) if vt.calibrator.is_some() => {
                    let y = uncalibrate(vt, *x as f64, vals)?;
                    pk::f64_to_int(y, bits, enc, F)?
                }
                ("int", Val::Int(x)) if vt.signed() => {
                    pk::from_signed(*x as i64, bits, enc, F)?
                }
                ("int", Val::Int(x)) => pk::from_unsigned(*x as u64, bits, F)?,
                ("enum", Val::Enum(x)) => pk::from_signed(*x, bits, enc, F)?,
                ("bool", Val::Bool(b)) => *b as u64,
                (_, Val::Float(x)) => {
                    let y = uncalibrate(vt, *x, vals)?;
                    pk::f64_to_int(y, bits, enc, F)?
                }
                _ => return Err(PacketError::OutOfRange { field: F }),
            };
            if let Some(packed) = bcd(&e.encoding) {
                raw = pk::bcd_encode(raw, bits, packed, F)?;
            }
            raw_write(w, e.bits, &e.order, e.lsb, raw as u128);
            Ok(())
        }
        (Encoding::Float(e), Val::Float(x)) => {
            let y = uncalibrate(vt, *x, vals)?;
            let raw = if e.fmt == "IEEE754" && (e.bits == 32 || e.bits == 64) {
                pk::float_to_raw(y, e.bits as u32) as u128
            } else {
                let r = pk::float_encode(
                    float_format(&e.fmt),
                    e.bits as u32,
                    y,
                    F,
                )?;
                if e.bits > 64 {
                    r
                } else {
                    r as u64 as u128
                }
            };
            raw_write(w, e.bits, &e.order, e.lsb, raw);
            Ok(())
        }
        (Encoding::Binary(e), Val::Bytes(_) | Val::Str(_)) => {
            let data = match v {
                Val::Bytes(b) => &b[..],
                Val::Str(s) => s.as_bytes(),
                _ => unreachable!("bytes or a string"),
            };
            let most = (e.bits / 8) as usize;
            if e.rest {
                return pk::write_rest(w, data, most, F);
            }
            if e.embedded {
                return pk::write_embedded(w, data, most, F);
            }
            match &e.dyn_ {
                Some(d) => {
                    if data.len() != size_of(d, vals, inner)? {
                        return Err(bad);
                    }
                    w.bytes(data);
                    Ok(())
                }
                None => pk::write_binary(w, data, (e.bits / 8) as usize, F),
            }
        }
        (Encoding::String(e), Val::Bool(_) | Val::Str(_)) => {
            let sp = match &e.dyn_ {
                Some(d) => spec(e).sized(size_of(d, vals, inner)?),
                None => spec(e),
            };
            let s = match v {
                Val::Bool(true) => &vt.one_string,
                Val::Bool(false) => &vt.zero_string,
                Val::Str(s) => s,
                _ => unreachable!("a string or boolean"),
            };
            pk::write_string(w, &sp, s, F)
        }
        _ => Err(PacketError::OutOfRange { field: F }),
    }
}

/// The state of an encoding.
struct Enc<'w, 'b> {
    /// The writer.
    w: &'w mut BitWriter<'b>,
    /// The storage, holding the packet's values.
    ws: &'w mut Workspace,
}

impl Enc<'_, '_> {
    /// Gives the position of a Mark.
    ///
    /// # Arguments
    ///
    /// * `m` - The Mark's number; 0 is the packet's start.
    ///
    /// # Returns
    ///
    /// The position.
    fn mark(&self, m: i64) -> usize {
        if m == 0 {
            0
        } else {
            self.ws.marks[m as usize]
        }
    }

    /// Puts the checksum of what a check covers into its field, unless it
    /// is computed elsewhere: before a transform when it has a `stop`,
    /// and otherwise once the packet is encoded.
    ///
    /// # Arguments
    ///
    /// * `c`     - The check.
    /// * `early` - Whether this is before the transform of its field.
    ///
    /// # Errors
    ///
    /// The runtime's, if the bits it covers aren't all written.
    fn patch(&mut self, c: &Check, early: bool) -> CodecResult<()> {
        if c.late.is_some() || c.stop.is_some() != early {
            return Ok(());
        }
        let l = c.local.get();
        let Encoding::Int(e) = &l.vt().enc else {
            unreachable!("an integer")
        };
        let at_ = self.ws.computed[l.id as usize];
        let (start, end) = region(c, &self.ws.marks, at_, self.w.packet_end());
        self.w
            .patch_checksum(
                &checksum_spec(&c.spec, &self.ws.vals).map_err(at(&l.name))?,
                start,
                end,
                at_,
                e.bits as u32,
                matches!(e.order, Order::Le),
                F,
            )
            .map_err(at(&l.name))
    }

    /// Puts the checksum of a late check into its transformed data, which
    /// it transforms again.
    ///
    /// # Arguments
    ///
    /// * `c` - The check, which is late.
    ///
    /// # Errors
    ///
    /// The runtime's, if the bits it covers aren't all written or the
    /// transform fails.
    fn late_patch(&mut self, c: &Check) -> CodecResult<()> {
        let l = c.local.get();
        let Encoding::Int(e) = &l.vt().enc else {
            unreachable!("an integer")
        };
        let late = c.late.as_ref().expect("a late check").get();
        let at_ = self.ws.computed[l.id as usize];
        let (start, end) = region(c, &self.ws.marks, at_, self.w.packet_end());
        let t = late.start as usize;
        let ts = self.ws.tstart[t];
        let spec =
            checksum_spec(&c.spec, &self.ws.vals).map_err(at(&l.name))?;
        let f = binary_to(&late.encode, &self.ws.vals).map_err(at(&l.name))?;
        let plain = &self.ws.tbufs[t][..self.ws.kept[t]];
        self.w
            .patch_transformed_checksum(
                &spec,
                start,
                end,
                plain,
                at_ - ts,
                e.bits as u32,
                matches!(e.order, Order::Le),
                ts,
                f,
                late.raw.unwrap_or(0) as usize,
                late.out_cap.max(0) as usize,
                F,
            )
            .map_err(at(&l.name))
    }

    /// Writes a packet length field's value, once what it measures is
    /// written.
    ///
    /// # Arguments
    ///
    /// * `l` - The field.
    ///
    /// # Errors
    ///
    /// The runtime's, if the length doesn't fit.
    fn patch_length(&mut self, l: &Local) -> CodecResult<()> {
        let Encoding::Int(enc) = &l.vt().enc else {
            unreachable!("an integer")
        };
        let le = matches!(enc.order, Order::Le);
        let at_ = self.ws.computed[l.id as usize];
        self.w
            .patch_length(at_, enc.bits as u32, le, l.length.unwrap(), F)
            .map_err(at(&l.name))
    }

    /// Writes a transformed container: its items, then what its encoding
    /// algorithm makes of them.
    ///
    /// # Arguments
    ///
    /// * `t`      - The transformed container.
    /// * `where_` - The container it is in, for errors.
    ///
    /// # Errors
    ///
    /// As [`Enc::items`]', or the runtime's if the transform fails.
    fn transform(&mut self, t: &Transform, where_: &str) -> CodecResult<()> {
        let start = self.w.position();
        let i = t.start as usize;
        self.ws.tstart[i] = start;
        self.items(&t.items, where_)?;
        if let Some(l) = &t.length {
            self.patch_length(&l.get())?;
        }
        for c in &t.patches {
            self.patch(&c.get(), true)?;
        }
        if t.keeps {
            // For checks of what follows it, which are late.
            let mut buf = std::mem::take(&mut self.ws.tbufs[i]);
            buf.clear();
            buf.resize(t.cap.max(0) as usize, 0);
            let n = self.w.copy_out(start, &mut buf, F);
            self.ws.tbufs[i] = buf;
            self.ws.kept[i] = n.map_err(at(&t.name))?;
        }
        let f = binary_to(&t.encode, &self.ws.vals).map_err(at(&t.name))?;
        let raw = t.raw.map(|r| r as usize);
        self.w
            .transform(start, f, raw, t.out_cap.max(0) as usize, F)
            .map_err(at(&t.name))
    }

    /// Writes items.
    ///
    /// # Arguments
    ///
    /// * `items`  - The items.
    /// * `where_` - The container they are in, for errors.
    ///
    /// # Errors
    ///
    /// As [`write_val`]'s, Missing for an absent value its condition
    /// requires, or Mismatch if criteria don't hold.
    fn items(&mut self, items: &[Item], where_: &str) -> CodecResult<()> {
        for it in items {
            match it {
                Item::Seek(s) => {
                    let off =
                        seek_offset(s, &self.ws.vals).map_err(at(where_))?;
                    match s.from_start {
                        None => self.w.skip(off, F),
                        Some(m) => {
                            let at_ = self.mark(m);
                            if s.ahead {
                                self.w.seek_ahead(at_, off, F)
                            } else {
                                self.w.seek(at_, off, F)
                            }
                        }
                    }
                    .map_err(at(where_))?;
                }
                Item::Mark(m) => {
                    self.ws.marks[m.id as usize] = self.w.position()
                }
                Item::Fixed(f) => {
                    let v = f.value.to_u64().unwrap_or(0);
                    self.w.uint(f.bits as u32, v);
                }
                Item::Pad(p) => self.w.zeros(p.bits as usize),
                Item::Require(q) => {
                    require(&q.criteria, &self.ws.vals, &q.container, &q.text)?
                }
                Item::Fill(f) => {
                    let n = count_of(&f.size, &self.ws.vals, OUTER)
                        .map_err(at(&f.container))?;
                    let e = self.mark(f.start) + n as usize;
                    if self.w.position() > e {
                        return Err(at(&f.container)(PacketError::BadSize {
                            field: F,
                        }));
                    }
                    let p = e - self.w.position();
                    self.w.zeros(p);
                }
                Item::Cond(c) => {
                    let mut ok = true;
                    for t in &c.criteria {
                        if !term(t, &self.ws.vals).map_err(at(where_))? {
                            ok = false;
                            break;
                        }
                    }
                    if ok {
                        self.items(&c.items, where_)?;
                    }
                }
                Item::Verify(v) => self.patch(&v.check.get(), false)?,
                Item::Transform(t) => self.transform(&t.get(), where_)?,
                Item::TailSeek(t) => {
                    let back =
                        tail_back(t, &self.ws.vals).map_err(at(where_))?;
                    self.w.seek_tail(back);
                }
                Item::TailPad(t) => self.w.zeros(t.bits as usize),
                Item::LengthEnd(e) => {
                    let l = e.local.get();
                    let Encoding::Int(enc) = &l.vt().enc else {
                        unreachable!("an integer")
                    };
                    self.w
                        .patch_span(
                            self.ws.computed[l.id as usize],
                            enc.bits as u32,
                            matches!(enc.order, Order::Le),
                            l.length.unwrap(),
                            F,
                        )
                        .map_err(at(&l.name))?;
                }
                Item::Segment(g) => {
                    let g = g.get();
                    let l = g.local.get();
                    let i = l.id as usize;
                    if g.first {
                        // Encode the whole value, then write each
                        // segment's bits.
                        let Some(x) = self.ws.vals.val(l.id) else {
                            return Err(at(&l.name)(PacketError::Missing {
                                field: F,
                            }));
                        };
                        let t = self.w.stash(g.total as u32);
                        write_val(self.w, &l.vt(), x, &self.ws.vals, OUTER)
                            .map_err(at(&l.name))?;
                        self.ws.segs[i] = self.w.unstash(t);
                    }
                    let mask = if g.bits >= 64 {
                        u64::MAX
                    } else {
                        (1u64 << g.bits) - 1
                    };
                    self.w.uint(
                        g.bits as u32,
                        (self.ws.segs[i] >> g.shift) & mask,
                    );
                }
                Item::Value(v) if v.assembled != 0 => {}
                Item::Value(v) => {
                    let l = v.local.get();
                    let vt = l.vt();
                    let i = l.id as usize;
                    if l.computed() {
                        self.ws.computed[i] = self.w.position();
                        let Encoding::Int(e) = &vt.enc else {
                            unreachable!("an integer")
                        };
                        self.w.zeros(e.bits as usize);
                        continue;
                    }
                    let Some(x) = self.ws.vals.val(l.id) else {
                        return Err(at(&l.name)(PacketError::Missing {
                            field: F,
                        }));
                    };
                    let r = if self.ws.derived[i] {
                        write_unchecked(self.w, &vt, x, &self.ws.vals, OUTER)
                    } else {
                        write_val(self.w, &vt, x, &self.ws.vals, OUTER)
                    };
                    r.map_err(at(&l.name))?;
                }
                _ => unreachable!("a supported item"),
            }
        }
        Ok(())
    }

    /// Writes the entries of a node's bases and then its own.
    ///
    /// # Arguments
    ///
    /// * `n` - The node.
    ///
    /// # Errors
    ///
    /// As [`Enc::items`]'.
    fn chain(&mut self, n: &Shared<Node>) -> CodecResult<()> {
        let m = n.get();
        if let Some(b) = &m.base {
            self.chain(b)?;
        }
        self.items(&m.items, &m.key)
    }
}

/// Says whether a term reads a field of a packet.
///
/// # Arguments
///
/// * `t` - The term.
/// * `n` - The packet.
///
/// # Returns
///
/// Whether it does.
fn reads_field(t: &Term, n: &Node) -> bool {
    let field = |l: &Shared<Local>| n.fields.iter().any(|f| f.1.same(l));
    match t {
        Term::Criterion(c) => {
            let c = c.get();
            field(&c.local) || c.other.as_ref().is_some_and(field)
        }
        Term::Custom(c) => c.get().inputs.iter().any(|i| match i {
            Input::Value(l, _, _) => field(l),
            Input::Const(_) => false,
        }),
        Term::AllOf(a) => a.get().terms.iter().any(|x| reads_field(x, n)),
        Term::AnyOf(a) => a.get().terms.iter().any(|x| reads_field(x, n)),
    }
}

/// Checks the criteria of a node and its bases that read the packet's
/// fields, before anything is written.
///
/// # Arguments
///
/// * `m`    - The node, or one of its bases.
/// * `n`    - The packet.
/// * `vals` - Its values.
///
/// # Errors
///
/// Mismatch, if one doesn't hold.
fn field_criteria(
    m: &Shared<Node>,
    n: &Node,
    vals: &Values,
) -> CodecResult<()> {
    let mm = m.get();
    if let Some(b) = &mm.base {
        field_criteria(b, n, vals)?;
    }
    for c in &mm.criteria {
        if reads_field(c, n) {
            require_term(c, vals, &mm.key)?;
        }
    }
    Ok(())
}

/// Encodes a concrete packet from the values in a workspace: its fields'
/// values must be there; those its inheritance fixes and the sizes it
/// computes are put there.
///
/// # Arguments
///
/// * `n`    - The packet, which the plan says it can run.
/// * `plan` - The plan.
/// * `ws`   - The storage, holding the fields' values.
/// * `out`  - The buffer the bytes go to.
///
/// # Returns
///
/// The bytes.
///
/// # Errors
///
/// As [`write_val`]'s, Missing for an absent value that must be there, or
/// Mismatch if criteria don't hold.
pub fn encode<'b>(
    n: &Shared<Node>,
    plan: &Plan,
    ws: &mut Workspace,
    out: &'b mut Buffer,
) -> CodecResult<&'b [u8]> {
    let nb = n.get();
    ws.vals.set_hooks(&plan.hooks);
    need_hooks(&nb, plan)?;
    for (l, lit) in &nb.fixed.0 {
        let l = l.get();
        let mut x = ws.vals.take(l.id);
        lit_assign(&mut x, &l.vt(), lit);
        ws.vals.put(l.id, x);
    }
    // Sizes and counts come from the lengths of what they size.
    ws.derived.fill(false);
    for (l, (c, d)) in &nb.derived.0 {
        let (l, c) = (l.get(), c.get());
        let len = match ws.vals.val(c.id) {
            Some(Val::Arr(_, used)) => *used,
            Some(Val::Bytes(b)) => b.len(),
            Some(Val::Str(s)) => s.len(),
            _ => return Err(at(&c.name)(PacketError::Missing { field: F })),
        };
        let x = pk::size_source(
            len,
            d.slope,
            d.intercept,
            d.unit as u32,
            d.factor as usize,
            F,
        )
        .map_err(at(&c.name))?;
        let vt = l.vt();
        let w = vt.width() as u32;
        let fits = if vt.signed() {
            w >= 64 || (x >= -(1i64 << (w - 1)) && x < (1i64 << (w - 1)))
        } else {
            x >= 0 && (w >= 64 || x < (1i64 << w))
        };
        if !fits {
            return Err(at(&l.name)(PacketError::OutOfRange { field: F }));
        }
        ws.vals.put(l.id, Val::Int(x as i128));
        ws.derived[l.id as usize] = true;
    }
    field_criteria(n, &nb, &ws.vals)?;
    let mut w = BitWriter::new(out);
    {
        let mut e = Enc { w: &mut w, ws };
        e.chain(n)?;
        if let Some(l) = &plan.info(n).length {
            e.patch_length(&l.get())?;
        }
        // Late checks first: transforming their data again changes what
        // the others cover.
        for c in &nb.checks {
            if c.get().late.is_some() {
                e.late_patch(&c.get())?;
            }
        }
        for c in &nb.checks {
            e.patch(&c.get(), false)?;
        }
    }
    drop(w);
    Ok(out.as_bytes())
}

/// Puts a literal of the document into a value's storage.
///
/// # Arguments
///
/// * `dst` - The storage.
/// * `vt`  - The value's type.
/// * `lit` - The literal.
pub fn lit_assign(dst: &mut Val, vt: &VType, lit: &Lit) {
    match (vt.kind.as_str(), lit) {
        ("float", Lit::Int(i)) => {
            *dst = Val::Float(i.to_f64().unwrap_or(f64::NAN))
        }
        ("float", Lit::Float(x)) => *dst = Val::Float(*x),
        ("int" | "enum", Lit::Int(i)) if vt.wide() => {
            put_big(dst, crate::wide::from_big(i).unwrap_or_default())
        }
        ("enum", Lit::Int(i)) => *dst = Val::Enum(i.to_i64().unwrap_or(0)),
        (_, Lit::Int(i)) => *dst = Val::Int(i.to_i128().unwrap_or(0)),
        (_, Lit::Float(x)) => *dst = Val::Float(*x),
        (_, Lit::Bool(b)) => *dst = Val::Bool(*b),
        (_, Lit::Str(s)) => match dst {
            Val::Str(d) => {
                d.clear();
                d.push_str(s);
            }
            _ => *dst = Val::Str(s.clone()),
        },
        (_, Lit::Bytes(b)) => match dst {
            Val::Bytes(d) => {
                d.clear();
                d.extend_from_slice(b);
            }
            _ => *dst = Val::Bytes(b.clone()),
        },
    }
}

// --- describing --------------------------------------------------------

/// Writes a value's canonical text, as generated code's Display does.
///
/// # Arguments
///
/// * `vt`  - Its type.
/// * `v`   - The value.
/// * `out` - Where the text goes.
pub fn describe(vt: &VType, v: &Val, out: &mut String) {
    match (&vt.enc, v) {
        (Encoding::Aggregate(e), Val::Agg(ms)) => {
            out.push('{');
            for (i, (m, x)) in e.members.iter().zip(ms).enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                out.push_str(&m.ident);
                out.push('=');
                describe(&m.vtype, x, out);
            }
            out.push('}');
        }
        (Encoding::Array(e), Val::Arr(all, used)) => {
            let es = &all[..*used];
            out.push('[');
            for (i, x) in es.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                describe(&e.elem, x, out);
            }
            out.push(']');
        }
        (_, Val::Float(x)) => {
            let _ = pk::describe_f64(*x, out);
        }
        (_, Val::Str(s)) => {
            let _ = pk::describe_str(s, out);
        }
        (_, Val::Bytes(b)) => {
            let _ = pk::describe_bytes(b, out);
        }
        (_, Val::Enum(x)) => match vt
            .labels
            .iter()
            .find(|l| l.value <= *x && *x <= l.max_value)
        {
            Some(l) => out.push_str(&l.label),
            None => {
                let _ = write!(out, "{x}");
            }
        },
        (_, Val::Int(x)) => {
            let _ = write!(out, "{x}");
        }
        (_, Val::Big(x)) => {
            let label = crate::wide::to_i64(x).and_then(|v| {
                vt.labels.iter().find(|l| l.value <= v && v <= l.max_value)
            });
            match label {
                Some(l) if vt.kind == "enum" => out.push_str(&l.label),
                _ => {
                    let _ = write!(out, "{}", crate::wide::to_big(x));
                }
            }
        }
        (_, Val::Bool(b)) => {
            let _ = write!(out, "{b}");
        }
        (_, Val::RawInt(x)) => {
            let _ = write!(out, "{x}");
        }
        (_, Val::RawFloat(x)) => {
            let _ = pk::describe_f64(*x, out);
        }
        _ => out.push('?'),
    }
}

/// Writes a decoded packet's canonical text, as generated code's Display
/// does: its name, then each field and its value.
///
/// # Arguments
///
/// * `d`  - The decoded packet.
/// * `ws` - The workspace it was decoded into.
///
/// # Returns
///
/// The text.
pub fn describe_packet(d: &Decoded, ws: &Workspace) -> String {
    let n = d.node.get();
    let mut out = format!("{}{{", n.ident);
    for (i, (name, l)) in n.fields.iter().enumerate() {
        if i > 0 {
            out.push_str(", ");
        }
        out.push_str(name);
        out.push('=');
        let l = l.get();
        match ws.vals.val(l.id) {
            Some(v) => describe(&l.vt(), v, &mut out),
            None => out.push_str(pk::DESCRIBE_NONE),
        }
    }
    out.push('}');
    out
}
