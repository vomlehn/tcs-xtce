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
    Expr, Item, Lit, Local, Node, Num, Order, PathStep, Spec, Term, VType,
};

/// The name the interpreter gives the runtime's helpers for a field: the
/// runtime takes only names fixed when it is compiled, so the
/// interpreter names the field in its own error instead.
const F: &str = "field";

/// A decoded value, as the interpreter holds it.
#[derive(Debug, Clone, PartialEq)]
pub enum Val {
    /// An integer.
    Int(i128),
    /// An integer, or an enumeration's value, of more than 64 bits.
    Big(num_bigint::BigInt),
    /// A raw value, to be written as it is: not calibrated, and checked
    /// against its encoding only.
    Raw(Box<Val>),
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
    /// An array's elements, in order.
    Arr(Vec<Val>),
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
                    return what("an integer of more than 512 bits");
                }
            }
        }
        Encoding::Float(_) => {}
        Encoding::String(_) => {}
        Encoding::Binary(_) => {}
        Encoding::Array(e) => return type_unsupported(&e.elem),
        Encoding::Aggregate(e) => {
            if e.transform.is_some() {
                return what("a repeated container with a transform");
            }
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
        Encoding::Xform(_) => return what("a transform"),
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
        Term::Custom(_) => Some("a custom algorithm in a criterion".into()),
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
            Item::Transform(_) => Some("a transform".into()),
            Item::Verify(v) => check_unsupported(&v.check.get()),
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
    let found = n
        .chain()
        .iter()
        .find_map(|m| {
            let m = m.get();
            m.criteria
                .iter()
                .find_map(term_unsupported)
                .or_else(|| items_unsupported(&m.items))
        })
        .or_else(|| {
            n.get()
                .checks
                .iter()
                .find_map(|c| check_unsupported(&c.get()))
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
fn interned(b: &[u8]) -> &'static [u8] {
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
        Order::List(l) => {
            let b: Vec<u8> = l.iter().map(|x| *x as u8).collect();
            ByteOrder::List(interned(&b))
        }
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

/// Gives a calibrator's polynomial terms as the runtime takes them.
///
/// # Arguments
///
/// * `c` - The calibrator.
///
/// # Returns
///
/// The terms.
fn terms(c: &tcs_xtce_model::model::Polynomial) -> Vec<(f64, i32)> {
    c.terms.iter().map(|(a, e)| (*a, *e as i32)).collect()
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
fn math_value(v: &Val) -> pk::Result<f64> {
    Ok(match v {
        Val::Int(i) => *i as f64,
        Val::Big(i) => i.to_f64().unwrap_or(f64::NAN),
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
fn math(e: &Expr, x: f64, vals: &Values) -> pk::Result<f64> {
    Ok(match e {
        Expr::Const(c) => *c,
        Expr::Raw => x,
        Expr::Value(r) => {
            let r = r.get();
            let l = r.local.as_ref().expect("a bound ValueRef");
            let v = vals
                .get(&l.get().id)
                .and_then(|v| v.as_ref())
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
    vals: &Values,
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
fn apply(cal: Option<&Calibrator>, x: f64, vals: &Values) -> pk::Result<f64> {
    Ok(match cal {
        None => x,
        Some(Calibrator::Poly(p)) => pk::polynomial(x, &terms(p)),
        Some(Calibrator::Spline(s)) => {
            if s.orders.is_empty() {
                pk::spline(x, &s.points, s.order as u32, s.extrapolate, F)?
            } else {
                let o: Vec<u32> = s.orders.iter().map(|o| *o as u32).collect();
                pk::spline_orders(x, &s.points, &o, s.extrapolate, F)?
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
fn invert(cal: Option<&Calibrator>, y: f64, vals: &Values) -> pk::Result<f64> {
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
            let cal: Vec<f64> = s.points.iter().map(|p| p.1).collect();
            let inc = cal.windows(2).all(|w| w[0] < w[1]);
            let dec = cal.windows(2).all(|w| w[0] > w[1]);
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
fn calibrate(vt: &VType, x: f64, vals: &Values) -> pk::Result<f64> {
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
fn uncalibrate(vt: &VType, y: f64, vals: &Values) -> pk::Result<f64> {
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
fn source(d: &DynSize, vals: &Values, inner: Inner<'_>) -> pk::Result<f64> {
    if let Some(m) = &d.member {
        // A member's size from an earlier member of its aggregate.
        return match inner.iter().find(|x| &x.0 == m) {
            Some((_, Val::Int(i))) => Ok(*i as f64),
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
        .get(&l.get().id)
        .and_then(|v| v.as_ref())
        .ok_or(PacketError::BadSize { field: F })?;
    let (x, _) = reach(v, &l.get().vt(), &d.path)?;
    Ok(match x {
        Val::Int(i) => *i as f64,
        Val::Big(i) => i.to_f64().unwrap_or(f64::NAN),
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
fn size_of(d: &DynSize, vals: &Values, inner: Inner<'_>) -> pk::Result<usize> {
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
fn count_of(c: &Count, vals: &Values, inner: Inner<'_>) -> pk::Result<i64> {
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
fn span(sp: &tcs_xtce_model::model::Span, vals: &Values) -> pk::Result<usize> {
    let count = count_of(&sp.count, vals, &[])? as usize;
    let gap = count_of(&sp.gap, vals, &[])? as usize;
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
    vals: &Values,
) -> pk::Result<i64> {
    let mut off = count_of(&s.offset, vals, &[])? - s.back;
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
    vals: &Values,
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

/// Reads one value.
///
/// # Arguments
///
/// * `r`     - The reader.
/// * `vt`    - Its type.
/// * `vals`  - The values of the packet so far, which sizes come from.
/// * `inner` - The members of the aggregate the value is in, which a member's
///             size may come from.
///
/// # Returns
///
/// The value.
///
/// # Errors
///
/// The runtime's.
fn read_val(
    r: &mut BitReader<'_>,
    vt: &VType,
    vals: &Values,
    inner: Inner<'_>,
) -> pk::Result<Val> {
    if lsb_bytes(vt) {
        r.set_lsb(true);
        let v = read_plain(r, vt, vals, inner);
        r.set_lsb(false);
        return v;
    }
    read_plain(r, vt, vals, inner)
}

/// Reads one value, in the reader's usual bit order.
///
/// # Arguments
///
/// * `r`     - The reader.
/// * `vt`    - Its type.
/// * `vals`  - The values of the packet so far, which sizes come from.
/// * `inner` - The members of the aggregate the value is in, which a member's
///             size may come from.
///
/// # Returns
///
/// The value.
///
/// # Errors
///
/// The runtime's.
fn read_plain(
    r: &mut BitReader<'_>,
    vt: &VType,
    vals: &Values,
    inner: Inner<'_>,
) -> pk::Result<Val> {
    match &vt.enc {
        Encoding::Aggregate(e) => {
            // Each member's size may come from those before it.
            let mut got: Vec<(String, Val)> = Vec::new();
            for m in &e.members {
                if m.skip != 0 {
                    r.pad(m.skip as usize, F)?;
                }
                let x = read_val(r, &m.vtype, vals, &got)?;
                got.push((m.ident.clone(), x));
            }
            if e.pad != 0 {
                r.pad(e.pad as usize, F)?;
            }
            let copy = Val::Agg(got.into_iter().map(|g| g.1).collect());
            copy_checks(e, &copy)?;
            Ok(copy)
        }
        Encoding::Array(e) => {
            let n = count_of(&e.count, vals, inner)?;
            let mut out = Vec::new();
            for i in 0..n {
                if i > 0 && !matches!(e.gap, Count::Int(0)) {
                    r.skip(count_of(&e.gap, vals, inner)?, F)?;
                }
                out.push(read_val(r, &e.elem, vals, inner)?);
            }
            Ok(Val::Arr(out))
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
            if vt.kind == "float" {
                let x = crate::wide::read_f64(r, w)?;
                return Ok(Val::Float(calibrate(vt, x, vals)?));
            }
            if vt.kind == "int" && vt.calibrator.is_some() {
                let y = calibrate(vt, crate::wide::read_f64(r, w)?, vals)?;
                return Ok(Val::Int(round_int(vt, y)?));
            }
            if vt.kind == "bool" {
                // A bit more than the field, so that it stays positive.
                let n = ((e.bits + 8) / 8) as usize;
                let w = crate::wide::Wide { signed: false, ..w };
                let x = crate::wide::read(r, w, n)?;
                return Ok(Val::Bool(x != num_bigint::BigInt::from(0)));
            }
            Ok(Val::Big(crate::wide::read(
                r,
                w,
                (vt.width() / 8) as usize,
            )?))
        }
        Encoding::Int(e) => {
            let bits = e.bits as u32;
            let mut raw = raw_read(r, e.bits, &e.order, e.lsb)? as u64;
            if let Some(packed) = bcd(&e.encoding) {
                raw = pk::bcd_decode(raw, bits, packed, F)?;
            }
            let enc = int_enc(vt);
            Ok(match vt.kind.as_str() {
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
            })
        }
        Encoding::Float(e) => {
            let raw = raw_read(r, e.bits, &e.order, e.lsb)?;
            let x = if e.fmt == "IEEE754" && (e.bits == 32 || e.bits == 64) {
                pk::float_from_raw(raw as u64, e.bits as u32)
            } else {
                pk::float_decode(float_format(&e.fmt), e.bits as u32, raw, F)?
            };
            Ok(Val::Float(calibrate(vt, x, vals)?))
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
            let b = r.bytes(n, F)?;
            if vt.kind == "string" {
                Ok(Val::Str(pk::string_from_bytes(b, F)?))
            } else {
                Ok(Val::Bytes(b))
            }
        }
        Encoding::String(e) => {
            let sp = match &e.dyn_ {
                Some(d) => spec(e).sized(size_of(d, vals, inner)?),
                None => spec(e),
            };
            let s = pk::read_string(r, &sp, F)?;
            if vt.kind == "bool" {
                if s == vt.one_string {
                    Ok(Val::Bool(true))
                } else if s == vt.zero_string {
                    Ok(Val::Bool(false))
                } else {
                    Err(PacketError::OutOfRange { field: F })
                }
            } else {
                Ok(Val::Str(s))
            }
        }
        Encoding::Xform(_) => unreachable!("a supported type"),
    }
}

/// The values of a packet being decoded or encoded, by the number of the
/// Local that holds each; `None` for one that is absent.
type Values = HashMap<i64, Option<Val>>;

/// The members of the aggregate being decoded or encoded, by identifier,
/// those decoded so far when decoding: a member's size may come from one
/// before it.
type Inner<'a> = &'a [(String, Val)];

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
                let Val::Arr(es) = v else {
                    unreachable!("an array value")
                };
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
        (Val::Big(a), Lit::Int(b)) => a.partial_cmp(b),
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
        (Val::Big(x), Val::Big(y)) => x.partial_cmp(y),
        (Val::Big(x), Val::Int(y)) => {
            x.partial_cmp(&num_bigint::BigInt::from(*y))
        }
        (Val::Int(x), Val::Big(y)) => {
            num_bigint::BigInt::from(*x).partial_cmp(y)
        }
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
fn term(t: &Term, vals: &Values) -> pk::Result<bool> {
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
        Term::Custom(_) => unreachable!("a supported term"),
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
fn criterion(c: &Criterion, vals: &Values) -> pk::Result<bool> {
    let lvt = c.local.get().vt();
    let Some(v) = vals.get(&c.local.get().id).and_then(|v| v.as_ref()) else {
        // Absent: less than any value.
        return Ok(matches!(c.op.as_str(), "!=" | "<" | "<="));
    };
    let (x, xvt) = reach(v, &lvt, &c.path)?;
    if let Some(o) = &c.other {
        let ovt = o.get().vt();
        let Some(w) = vals.get(&o.get().id).and_then(|v| v.as_ref()) else {
            return Ok(false);
        };
        let (y, _) = reach(w, &ovt, &c.other_path)?;
        return Ok(holds(&c.op, cmp_vals(x, y)));
    }
    let lit = c.value.as_ref().expect("a literal");
    Ok(holds(&c.op, cmp_lit(x, &xvt, lit)))
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
    vals: &Values,
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

/// Finds what of a check the interpreter can't run.
///
/// # Arguments
///
/// * `c` - The check.
///
/// # Returns
///
/// What it is, or `None` if it can be run.
fn check_unsupported(c: &Check) -> Option<String> {
    if matches!(c.spec, Spec::Custom(_)) {
        Some("a custom checksum".into())
    } else if c.stop.is_some() || c.late.is_some() {
        Some("a checksum in transformed data".into())
    } else {
        None
    }
}

/// Gives the runtime's checksum of a check.
///
/// # Arguments
///
/// * `spec` - The check's algorithm.
///
/// # Returns
///
/// The runtime's.
fn checksum_spec(spec: &Spec) -> pk::Checksum {
    use pk::Checksum as C;
    match spec {
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
        Spec::Custom(_) => unreachable!("a supported checksum"),
    }
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
    marks: &HashMap<i64, usize>,
    pos: usize,
    end: usize,
) -> (usize, usize) {
    let base = if c.start == 0 { 0 } else { marks[&c.start] };
    let shifted = c.skip != 0 && !c.from_end;
    let start = if shifted {
        base + c.skip as usize
    } else {
        base
    };
    let last = match c.end {
        Some(m) => marks[&m],
        None => end,
    };
    let last = if c.from_end {
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
///
/// # Errors
///
/// Mismatch, if a criterion doesn't hold.
fn copy_checks(
    e: &tcs_xtce_model::model::AggregateEnc,
    copy: &Val,
) -> pk::Result<()> {
    if e.checks.is_empty() {
        return Ok(());
    }
    let this = e.this.as_ref().expect("the copy's own Local");
    let mut vals = Values::new();
    vals.insert(this.get().id, Some(copy.clone()));
    for r in &e.checks {
        for t in &r.criteria {
            if !term(t, &vals)? {
                return Err(PacketError::Mismatch {
                    container: F,
                    criterion: F,
                });
            }
        }
    }
    Ok(())
}

/// The state of a decoding.
struct Dec<'d> {
    /// The reader.
    r: BitReader<'d>,
    /// The values decoded so far.
    vals: Values,
    /// The position of each Mark.
    marks: HashMap<i64, usize>,
    /// The position of each checksum field.
    sums: HashMap<i64, usize>,
    /// The bits read so far of each value read in segments.
    segs: HashMap<i64, u64>,
    /// Where the container each length field of an included container
    /// measures must end, by the field's number.
    spans: HashMap<i64, i128>,
    /// The raw value of each calibrated value, by its number.
    raws: HashMap<i64, Val>,
}

impl Dec<'_> {
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
        let name = l.name.clone();
        let Encoding::Int(e) = &l.vt().enc else {
            unreachable!("an integer")
        };
        let (start, end) =
            region(c, &self.marks, self.sums[&l.id], self.r.packet_end());
        let x = self
            .r
            .checksum(&checksum_spec(&c.spec), start, end, F)
            .map_err(at(&name))?;
        let value = match self.vals.get(&l.id).cloned().flatten() {
            Some(Val::Int(v)) => Some(v as u64),
            // A wider field holds the value with zeros above it.
            Some(Val::Big(v)) => v.to_u64(),
            _ => Some(0),
        };
        if l.vt().wide() {
            if Some(x) != value {
                return Err(at(&name)(PacketError::BadChecksum { field: F }));
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
            return Err(at(&name)(PacketError::BadChecksum { field: F }));
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
                    let off = seek_offset(s, &self.vals).map_err(at(where_))?;
                    match s.from_start {
                        None => self.r.skip(off, F),
                        Some(m) => {
                            let at_ = if m == 0 { 0 } else { self.marks[&m] };
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
                    self.marks.insert(m.id, self.r.position());
                }
                Item::Pad(p) => {
                    self.r.pad(p.bits as usize, F).map_err(at(where_))?
                }
                Item::Fixed(f) => {
                    self.r.uint(f.bits as u32, F).map_err(at(where_))?;
                }
                Item::Require(q) => {
                    require(&q.criteria, &self.vals, &q.container, &q.text)?
                }
                Item::Fill(f) => {
                    let n = count_of(&f.size, &self.vals, &[])
                        .map_err(at(&f.container))?;
                    let start = if f.start == 0 {
                        0
                    } else {
                        self.marks[&f.start]
                    };
                    let e = start + n as usize;
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
                    let back = tail_back(t, &self.vals).map_err(at(where_))?;
                    self.r.seek_end(back, F).map_err(at(where_))?;
                }
                // The packet's last bits, which encoding fills with zeros.
                Item::TailPad(t) => {
                    self.r.skip(t.bits, F).map_err(at(where_))?;
                }
                Item::LengthEnd(e) => {
                    let l = e.local.get();
                    self.r
                        .check_span(self.spans[&l.id], F)
                        .map_err(at(&l.name))?;
                }
                Item::Segment(g) => {
                    let g = g.get();
                    let l = g.local.get();
                    let bits =
                        self.r.uint(g.bits as u32, F).map_err(at(&l.name))?;
                    let part = bits << g.shift;
                    let s = if g.first {
                        part
                    } else {
                        self.segs[&l.id] | part
                    };
                    self.segs.insert(l.id, s);
                }
                Item::Value(v) => {
                    let l = v.local.get();
                    let vt = l.vt();
                    if l.checksum {
                        self.sums.insert(l.id, self.r.position());
                    }
                    // A calibrated value's raw value too, from the same
                    // bits.
                    let mut raw_r = self.r.clone();
                    let x = if v.assembled != 0 {
                        // Decoded from its segments' bits.
                        let mut b = [0u8; 8];
                        let mut r = pk::segment_reader(
                            &mut b,
                            self.segs[&l.id],
                            v.assembled as u32,
                        );
                        read_val(&mut r, &vt, &self.vals, &[])
                    } else {
                        read_val(&mut self.r, &vt, &self.vals, &[])
                    }
                    .map_err(at(&l.name))?;
                    if calibrated(&vt) {
                        let rt = raw_type(&vt);
                        let raw = if v.assembled != 0 {
                            let mut b = [0u8; 8];
                            let mut r = pk::segment_reader(
                                &mut b,
                                self.segs[&l.id],
                                v.assembled as u32,
                            );
                            read_val(&mut r, &rt, &self.vals, &[])
                        } else {
                            read_val(&mut raw_r, &rt, &self.vals, &[])
                        }
                        .map_err(at(&l.name))?;
                        self.raws.insert(l.id, raw);
                    }
                    let n = match &x {
                        Val::Int(n) => Some(*n as u64),
                        // A wider field must hold a length a u64 holds.
                        Val::Big(n) => match n.to_u64() {
                            Some(n) => Some(n),
                            None if l.length.is_some() => {
                                return Err(at(&l.name)(
                                    PacketError::BadLength { field: F },
                                ))
                            }
                            None => None,
                        },
                        _ => None,
                    };
                    if let (Some(adj), Some(n)) = (l.length, n) {
                        let n = &(n as i128);
                        if l.measures {
                            let e = self
                                .r
                                .span_end(*n as u64, adj, F)
                                .map_err(at(&l.name))?;
                            self.spans.insert(l.id, e);
                        } else {
                            self.r
                                .limit(*n as u64, adj, F)
                                .map_err(at(&l.name))?;
                        }
                    }
                    self.vals.insert(l.id, Some(x));
                }
                Item::Cond(c) => {
                    for x in walk(&c.items, true) {
                        if let Item::Value(v) = x {
                            self.vals.entry(v.local.get().id).or_insert(None);
                        }
                    }
                    let mut ok = true;
                    for t in &c.criteria {
                        if !term(t, &self.vals).map_err(at(where_))? {
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

    /// Ends the decoding of a concrete packet: its length field's check.
    ///
    /// # Arguments
    ///
    /// * `n` - The packet.
    ///
    /// # Errors
    ///
    /// BadLength, if the packet length field disagrees with the packet.
    fn finish(&self, n: &Shared<Node>) -> CodecResult<()> {
        if let Some(l) = length_field(n) {
            self.r.finish(F).map_err(at(&l.get().name))?;
        }
        for c in &n.get().checks {
            self.verify(&c.get())?;
        }
        Ok(())
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

/// A decoded packet: its node, and the value of each of its fields, in
/// the order of the node's `fields`.
#[derive(Debug, Clone)]
pub struct Decoded {
    /// The concrete packet decoded.
    pub node: Shared<Node>,
    /// Each field's value; `None` for one that is absent.
    pub fields: Vec<Option<Val>>,
    /// Each field's raw value, as its [`raw_type`] reads it, where the
    /// field's type has a calibrator; `None` elsewhere.
    pub raws: Vec<Option<Val>>,
    /// The bytes it took.
    pub used: usize,
}

/// Gives the fields of a decoded packet.
///
/// # Arguments
///
/// * `n`    - The packet.
/// * `d`    - The decoding.
///
/// # Returns
///
/// The decoded packet.
fn decoded(n: &Shared<Node>, d: &Dec<'_>) -> Decoded {
    let fields = n
        .get()
        .fields
        .iter()
        .map(|(_, l)| d.vals.get(&l.get().id).cloned().flatten())
        .collect();
    let raws = n
        .get()
        .fields
        .iter()
        .map(|(_, l)| d.raws.get(&l.get().id).cloned())
        .collect();
    Decoded {
        node: n.clone(),
        fields,
        raws,
        used: d.r.used(),
    }
}

/// Decodes a concrete packet.
///
/// # Arguments
///
/// * `n`    - The packet, which [`unsupported`] accepts.
/// * `data` - Its bytes.
///
/// # Returns
///
/// The decoded packet.
///
/// # Errors
///
/// The runtime's, or Mismatch if its criteria don't hold.
pub fn decode(n: &Shared<Node>, data: &[u8]) -> CodecResult<Decoded> {
    let mut d = Dec {
        r: BitReader::new(data),
        vals: Values::new(),
        marks: HashMap::new(),
        sums: HashMap::new(),
        segs: HashMap::new(),
        spans: HashMap::new(),
        raws: HashMap::new(),
    };
    for m in n.chain() {
        let m = m.get();
        for c in &m.criteria {
            require(std::slice::from_ref(c), &d.vals, &m.key, &c.text())?;
        }
        d.items(&m.items, &m.key)?;
    }
    d.finish(n)?;
    Ok(decoded(n, &d))
}

/// Decodes a packet of an inheritance tree: the entries of a node and its
/// bases, then the first child (in document order) whose criteria match,
/// recursively.
///
/// # Arguments
///
/// * `root` - The node to start from: a tree's root, or any node in it.
/// * `data` - The bytes.
///
/// # Returns
///
/// The decoded packet, of the concrete node it matched, or the error
/// that decoding gave: NoMatch, if it matches no concrete node, or the
/// runtime's; or, outside both, what the packet leads to that the
/// interpreter can't run yet.
pub fn classify(root: &Shared<Node>, data: &[u8]) -> Classified {
    let mut d = Dec {
        r: BitReader::new(data),
        vals: Values::new(),
        marks: HashMap::new(),
        sums: HashMap::new(),
        segs: HashMap::new(),
        spans: HashMap::new(),
        raws: HashMap::new(),
    };
    // The node's bases' entries, then its own, each node's criteria
    // checked first.
    for m in root.chain() {
        if let Some(w) = node_unsupported(&m) {
            return Err(w);
        }
        let (key, criteria, items) = {
            let m = m.get();
            (m.key.clone(), m.criteria.clone(), m.items.clone())
        };
        for c in &criteria {
            if let Err(e) =
                require(std::slice::from_ref(c), &d.vals, &key, &c.text())
            {
                return Ok(Err(e));
            }
        }
        if let Err(e) = d.items(&items, &key) {
            return Ok(Err(e));
        }
    }
    dispatch(root, &mut d)
}

/// Finds the first construct of a node's own criteria and items that the
/// interpreter can't run, and if it is concrete, of its checks and
/// derived sizes.
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
        .or_else(|| {
            if m.abstract_ {
                None
            } else {
                m.checks.iter().find_map(|c| check_unsupported(&c.get()))
            }
        })
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
fn dispatch(n: &Shared<Node>, d: &mut Dec<'_>) -> Classified {
    let children = n.get().children.clone();
    for c in &children {
        if c.get().error.is_some() {
            continue;
        }
        // Its criteria are tested if they can be: a packet it doesn't
        // match never needs what else it has.
        let crit = c.get().criteria.iter().find_map(term_unsupported);
        if let Some(w) = crit {
            return Err(format!("{}: {w}", c.get().key));
        }
        let mut ok = true;
        for t in &c.get().criteria {
            match term(t, &d.vals) {
                Ok(true) => {}
                Ok(false) => {
                    ok = false;
                    break;
                }
                Err(e) => return Ok(Err(at(&c.get().key)(e))),
            }
        }
        if ok {
            if let Some(w) = node_unsupported(c) {
                return Err(w);
            }
            let key = c.get().key.clone();
            let items = c.get().items.clone();
            if let Err(e) = d.items(&items, &key) {
                return Ok(Err(e));
            }
            return dispatch(c, d);
        }
    }
    if n.get().abstract_ {
        return Ok(Err(CodecError {
            error: PacketError::NoMatch { container: F },
            field: n.get().key.clone(),
        }));
    }
    if let Err(e) = d.finish(n) {
        return Ok(Err(e));
    }
    Ok(Ok(decoded(n, d)))
}

// --- encoding ----------------------------------------------------------

/// Converts a literal of the document to a value of a type.
///
/// # Arguments
///
/// * `vt`  - The type.
/// * `lit` - The literal.
///
/// # Returns
///
/// The value.
fn lit_val(vt: &VType, lit: &Lit) -> Val {
    match (vt.kind.as_str(), lit) {
        ("float", Lit::Int(i)) => Val::Float(i.to_f64().unwrap_or(f64::NAN)),
        ("float", Lit::Float(x)) => Val::Float(*x),
        ("int" | "enum", Lit::Int(i)) if vt.wide() => Val::Big(i.clone()),
        ("enum", Lit::Int(i)) => Val::Enum(i.to_i64().unwrap_or(0)),
        (_, Lit::Int(i)) => Val::Int(i.to_i128().unwrap_or(0)),
        (_, Lit::Float(x)) => Val::Float(*x),
        (_, Lit::Bool(b)) => Val::Bool(*b),
        (_, Lit::Str(s)) => Val::Str(s.clone()),
        (_, Lit::Bytes(b)) => Val::Bytes(b.clone()),
    }
}

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
    let big = |n: &Num| match n {
        Num::Int(i) => i.clone(),
        Num::Float(x) => num_bigint::BigInt::from(*x as i128),
    };
    let ok = vt.valid.iter().any(|b| match v {
        Val::Big(x) => {
            b.lo.as_ref().is_none_or(|lo| big(lo) <= *x)
                && b.hi.as_ref().is_none_or(|hi| *x <= big(hi))
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
    vals: &Values,
    inner: Inner<'_>,
) -> pk::Result<()> {
    if let Val::Raw(x) = v {
        // Written as it is, checked against its encoding only.
        return write_unchecked(w, &raw_type(vt), x, vals, inner);
    }
    check_valid(vt, v)?;
    write_unchecked(w, vt, v, vals, inner)
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
    vals: &Values,
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
    vals: &Values,
    inner: Inner<'_>,
) -> pk::Result<()> {
    let bad = PacketError::BadSize { field: F };
    match (&vt.enc, v) {
        (Encoding::Aggregate(e), Val::Agg(ms)) => {
            copy_checks(e, v)?;
            let named: Vec<(String, Val)> = e
                .members
                .iter()
                .zip(ms)
                .map(|(m, x)| (m.ident.clone(), x.clone()))
                .collect();
            for (m, x) in e.members.iter().zip(ms) {
                if m.skip != 0 {
                    w.zeros(m.skip as usize);
                }
                write_val(w, &m.vtype, x, vals, &named)?;
            }
            if e.pad != 0 {
                w.zeros(e.pad as usize);
            }
            Ok(())
        }
        (Encoding::Array(e), Val::Arr(es)) => {
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
                    let x = num_bigint::BigInt::from(*b as u8);
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
    /// The values of the packet, by the number of the Local that holds
    /// each, those its inheritance fixes among them.
    vals: Values,
    /// The position of each Mark.
    marks: HashMap<i64, usize>,
    /// The position of each computed field.
    computed: HashMap<i64, usize>,
    /// The numbers of the sizes and counts the packet computes from what
    /// they size.
    derived: Vec<i64>,
    /// The encoding of each value written in segments.
    segs: HashMap<i64, u64>,
}

impl Enc<'_, '_> {
    /// Puts the checksum of what a check covers into its field.
    ///
    /// # Arguments
    ///
    /// * `c` - The check.
    ///
    /// # Errors
    ///
    /// The runtime's, if the bits it covers aren't all written.
    fn patch(&mut self, c: &Check) -> CodecResult<()> {
        let l = c.local.get();
        let Encoding::Int(e) = &l.vt().enc else {
            unreachable!("an integer")
        };
        let at_ = self.computed[&l.id];
        let (start, end) = region(c, &self.marks, at_, self.w.packet_end());
        self.w
            .patch_checksum(
                &checksum_spec(&c.spec),
                start,
                end,
                at_,
                e.bits as u32,
                matches!(e.order, Order::Le),
                F,
            )
            .map_err(at(&l.name))
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
                    let off = seek_offset(s, &self.vals).map_err(at(where_))?;
                    match s.from_start {
                        None => self.w.skip(off, F),
                        Some(m) => {
                            let at_ = if m == 0 { 0 } else { self.marks[&m] };
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
                    self.marks.insert(m.id, self.w.position());
                }
                Item::Fixed(f) => {
                    let v = f.value.to_u64().unwrap_or(0);
                    self.w.uint(f.bits as u32, v);
                }
                Item::Pad(p) => self.w.zeros(p.bits as usize),
                Item::Require(q) => {
                    require(&q.criteria, &self.vals, &q.container, &q.text)?
                }
                Item::Fill(f) => {
                    let n = count_of(&f.size, &self.vals, &[])
                        .map_err(at(&f.container))?;
                    let start = if f.start == 0 {
                        0
                    } else {
                        self.marks[&f.start]
                    };
                    let e = start + n as usize;
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
                        if !term(t, &self.vals).map_err(at(where_))? {
                            ok = false;
                            break;
                        }
                    }
                    if ok {
                        self.items(&c.items, where_)?;
                    }
                }
                Item::Verify(v) => self.patch(&v.check.get())?,
                Item::TailSeek(t) => {
                    let back = tail_back(t, &self.vals).map_err(at(where_))?;
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
                            self.computed[&l.id],
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
                    if g.first {
                        // Encode the whole value, then write each
                        // segment's bits.
                        let x = self
                            .vals
                            .get(&l.id)
                            .cloned()
                            .flatten()
                            .ok_or_else(|| {
                                at(&l.name)(PacketError::Missing { field: F })
                            })?;
                        let t = self.w.stash(g.total as u32);
                        write_val(self.w, &l.vt(), &x, &self.vals, &[])
                            .map_err(at(&l.name))?;
                        let s = self.w.unstash(t);
                        self.segs.insert(l.id, s);
                    }
                    let mask = if g.bits >= 64 {
                        u64::MAX
                    } else {
                        (1u64 << g.bits) - 1
                    };
                    self.w.uint(
                        g.bits as u32,
                        (self.segs[&l.id] >> g.shift) & mask,
                    );
                }
                Item::Value(v) if v.assembled != 0 => {}
                Item::Value(v) => {
                    let l = v.local.get();
                    let vt = l.vt();
                    if l.computed() {
                        self.computed.insert(l.id, self.w.position());
                        let Encoding::Int(e) = &vt.enc else {
                            unreachable!("an integer")
                        };
                        self.w.zeros(e.bits as usize);
                        continue;
                    }
                    let x = match self.vals.get(&l.id).cloned().flatten() {
                        Some(x) => x,
                        None => {
                            return Err(at(&l.name)(PacketError::Missing {
                                field: F,
                            }));
                        }
                    };
                    let r = if self.derived.contains(&l.id) {
                        write_unchecked(self.w, &vt, &x, &self.vals, &[])
                    } else {
                        write_val(self.w, &vt, &x, &self.vals, &[])
                    };
                    r.map_err(at(&l.name))?;
                }
                _ => unreachable!("a supported item"),
            }
        }
        Ok(())
    }
}

/// Encodes a concrete packet.
///
/// # Arguments
///
/// * `n`      - The packet, which [`unsupported`] accepts.
/// * `fields` - The value of each of its fields, in the order of its
///              `fields`; `None` for one that is absent.
/// * `out`    - The buffer the bytes go to.
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
    fields: &[Option<Val>],
    out: &'b mut Buffer,
) -> CodecResult<&'b [u8]> {
    let mut vals = Values::new();
    for ((_, l), v) in n.get().fields.iter().zip(fields) {
        vals.insert(l.get().id, v.clone());
    }
    for (l, lit) in &n.get().fixed.0 {
        let vt = l.get().vt();
        vals.insert(l.get().id, Some(lit_val(&vt, lit)));
    }
    // Sizes and counts come from the lengths of what they size.
    let mut derived = Vec::new();
    for (l, (c, d)) in &n.get().derived.0 {
        let (l, c) = (l.get(), c.get());
        let len = match vals.get(&c.id).cloned().flatten() {
            Some(Val::Arr(a)) => a.len(),
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
        vals.insert(l.id, Some(Val::Int(x as i128)));
        derived.push(l.id);
    }
    let mut w = BitWriter::new(out);
    {
        let mut e = Enc {
            w: &mut w,
            vals,
            marks: HashMap::new(),
            computed: HashMap::new(),
            derived,
            segs: HashMap::new(),
        };
        for m in n.chain() {
            let m = m.get();
            for c in &m.criteria {
                if c.locals()
                    .iter()
                    .any(|l| n.get().fields.iter().any(|f| f.1.same(l)))
                {
                    require(
                        std::slice::from_ref(c),
                        &e.vals,
                        &m.key,
                        &c.text(),
                    )?;
                }
            }
        }
        for m in n.chain() {
            let (items, key) = {
                let m = m.get();
                (m.items.clone(), m.key.clone())
            };
            e.items(&items, &key)?;
        }
        if let Some(l) = length_field(n) {
            let l = l.get();
            let Encoding::Int(enc) = &l.vt().enc else {
                unreachable!("an integer")
            };
            let le = matches!(enc.order, Order::Le);
            e.w.patch_length(
                e.computed[&l.id],
                enc.bits as u32,
                le,
                l.length.unwrap(),
                F,
            )
            .map_err(at(&l.name))?;
        }
        for c in &n.get().checks {
            e.patch(&c.get())?;
        }
    }
    drop(w);
    Ok(out.as_bytes())
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
        (Encoding::Array(e), Val::Arr(es)) => {
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
            let label = x.to_i64().and_then(|v| {
                vt.labels.iter().find(|l| l.value <= v && v <= l.max_value)
            });
            match label {
                Some(l) if vt.kind == "enum" => out.push_str(&l.label),
                _ => {
                    let _ = write!(out, "{x}");
                }
            }
        }
        (_, Val::Bool(b)) => {
            let _ = write!(out, "{b}");
        }
        (_, Val::Raw(x)) => describe(&raw_type(vt), x, out),
        _ => out.push('?'),
    }
}

/// Writes a decoded packet's canonical text, as generated code's Display
/// does: its name, then each field and its value.
///
/// # Arguments
///
/// * `d` - The decoded packet.
///
/// # Returns
///
/// The text.
pub fn describe_packet(d: &Decoded) -> String {
    let n = d.node.get();
    let mut out = format!("{}{{", n.ident);
    for (i, ((name, l), v)) in n.fields.iter().zip(&d.fields).enumerate() {
        if i > 0 {
            out.push_str(", ");
        }
        out.push_str(name);
        out.push('=');
        match v {
            Some(v) => describe(&l.get().vt(), v, &mut out),
            None => out.push_str(pk::DESCRIBE_NONE),
        }
    }
    out.push('}');
    out
}
