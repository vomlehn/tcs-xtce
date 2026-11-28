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
    walk, Calibrator, Count, Criterion, DynSize, Encoding, Item, Lit, Local,
    Node, Num, Order, PathStep, Term, VType,
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
        None | Some(Calibrator::Poly(_)) | Some(Calibrator::Spline(_)) => {}
        Some(_) => return what("a math or context calibrator"),
    }
    match &vt.enc {
        Encoding::Int(e) => {
            if e.bits > 64 {
                return what("an integer of more than 64 bits");
            }
        }
        Encoding::Float(_) => {}
        Encoding::String(_) => {}
        Encoding::Binary(_) => {}
        Encoding::Array(e) => return type_unsupported(&e.elem),
        Encoding::Aggregate(e) => {
            if e.transform.is_some() || !e.checks.is_empty() {
                return what(
                    "a repeated container with a transform or criteria",
                );
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
            let steps = c.path.iter().chain(c.other_path.iter());
            for s in steps {
                if let PathStep::Index(i) = s {
                    if i.stride != 0 {
                        return Some(
                            "a criterion on an element of a varying array"
                                .into(),
                        );
                    }
                }
            }
            if c.local.get().vt().wide() {
                return Some(
                    "a criterion on an integer of more than 64 bits".into(),
                );
            }
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
                if s.span.is_some() {
                    Some(
                        "a location from the end, past values of varying size"
                            .to_string(),
                    )
                } else {
                    None
                }
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
                if v.assembled != 0 {
                    Some("a value assembled from segments".into())
                } else if l.checksum {
                    Some("a checksum".into())
                } else if l.length.is_some() && l.measures {
                    Some("the length field of an included container".into())
                } else {
                    type_unsupported(&l.vt())
                }
            }
            Item::TailSeek(_) | Item::TailPad(_) => {
                Some("a location from the end".into())
            }
            Item::LengthEnd(_) => {
                Some("the length field of an included container".into())
            }
            Item::Transform(_) => Some("a transform".into()),
            Item::Verify(_) => Some("a checksum".into()),
            Item::Segment(_) => Some("a segment".into()),
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
        .or_else(|| (!n.get().checks.is_empty()).then(|| "a checksum".into()));
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

/// Calibrates a raw value.
///
/// # Arguments
///
/// * `vt` - The type.
/// * `x`  - The raw value.
///
/// # Returns
///
/// The engineering value.
///
/// # Errors
///
/// The runtime's, if a spline doesn't cover the value.
fn calibrate(vt: &VType, x: f64) -> pk::Result<f64> {
    Ok(match &vt.calibrator {
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
        Some(_) => unreachable!("a supported calibrator"),
    })
}

/// Inverts a type's calibrator.
///
/// # Arguments
///
/// * `vt` - The type.
/// * `y`  - The engineering value.
///
/// # Returns
///
/// The raw value.
///
/// # Errors
///
/// NotInvertible, if the calibrator can't be inverted; the runtime's, if
/// a spline doesn't cover the value.
fn uncalibrate(vt: &VType, y: f64) -> pk::Result<f64> {
    match &vt.calibrator {
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
        Some(_) => unreachable!("a supported calibrator"),
    }
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
            Ok(Val::Agg(got.into_iter().map(|g| g.1).collect()))
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
        Encoding::Int(e) => {
            let bits = e.bits as u32;
            let mut raw = raw_read(r, e.bits, &e.order, e.lsb)? as u64;
            if let Some(packed) = bcd(&e.encoding) {
                raw = pk::bcd_decode(raw, bits, packed, F)?;
            }
            let enc = int_enc(vt);
            Ok(match vt.kind.as_str() {
                "int" if vt.calibrator.is_some() => {
                    let y = calibrate(vt, pk::int_to_f64(raw, bits, enc))?;
                    let w = vt.width();
                    let (lo, lim) = if vt.signed() {
                        (-(2f64.powi(w as i32 - 1)), 2f64.powi(w as i32 - 1))
                    } else {
                        (0.0, 2f64.powi(w as i32))
                    };
                    let v = pk::round_to_int(y, lo, lim, F)?;
                    Val::Int(if vt.signed() {
                        v as i64 as i128
                    } else {
                        v as u64 as i128
                    })
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
                _ => Val::Float(calibrate(vt, pk::int_to_f64(raw, bits, enc))?),
            })
        }
        Encoding::Float(e) => {
            let raw = raw_read(r, e.bits, &e.order, e.lsb)?;
            let x = if e.fmt == "IEEE754" && (e.bits == 32 || e.bits == 64) {
                pk::float_from_raw(raw as u64, e.bits as u32)
            } else {
                pk::float_decode(float_format(&e.fmt), e.bits as u32, raw, F)?
            };
            Ok(Val::Float(calibrate(vt, x)?))
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
                v = es
                    .get(ix.i as usize)
                    .ok_or(PacketError::BadSize { field: F })?;
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

/// The state of a decoding.
struct Dec<'d> {
    /// The reader.
    r: BitReader<'d>,
    /// The values decoded so far.
    vals: Values,
    /// The position of each Mark.
    marks: HashMap<i64, usize>,
}

impl Dec<'_> {
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
                    let off = count_of(&s.offset, &self.vals, &[])
                        .map_err(at(where_))?
                        - s.back;
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
                Item::Value(v) => {
                    let l = v.local.get();
                    let vt = l.vt();
                    let x = read_val(&mut self.r, &vt, &self.vals, &[])
                        .map_err(at(&l.name))?;
                    if let (Some(adj), Val::Int(n)) = (l.length, &x) {
                        self.r.limit(*n as u64, adj, F).map_err(at(&l.name))?;
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
    Decoded {
        node: n.clone(),
        fields,
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

/// Decodes a packet of an inheritance tree: the root's entries, then the
/// first child (in document order) whose criteria match, recursively.
///
/// # Arguments
///
/// * `root` - The tree's root, each of whose nodes [`unsupported`] accepts.
/// * `data` - The bytes.
///
/// # Returns
///
/// The decoded packet, of the concrete node it matched, or the error
/// that decoding gave: NoMatch, if it matches no concrete node, or the
/// runtime's; or, outside both, what the packet leads to that the
/// interpreter can't run yet.
pub fn classify(root: &Shared<Node>, data: &[u8]) -> Classified {
    if let Some(w) = node_unsupported(root) {
        return Err(w);
    }
    let mut d = Dec {
        r: BitReader::new(data),
        vals: Values::new(),
        marks: HashMap::new(),
    };
    let key = root.get().key.clone();
    if let Err(e) = d.items(&root.get().items, &key) {
        return Ok(Err(e));
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
            (!m.abstract_ && !m.checks.is_empty()).then(|| "a checksum".into())
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
        if let Some(w) = node_unsupported(c) {
            return Err(w);
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
fn check_valid(vt: &VType, v: &Val) -> pk::Result<()> {
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
    let ok = vt.valid.iter().any(|b| match v {
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
    check_valid(vt, v)?;
    write_unchecked(w, vt, v, vals, inner)
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
        (Encoding::Int(e), _) => {
            let bits = e.bits as u32;
            let enc = int_enc(vt);
            let mut raw = match (vt.kind.as_str(), v) {
                ("int", Val::Int(x)) if vt.calibrator.is_some() => {
                    let y = uncalibrate(vt, *x as f64)?;
                    pk::f64_to_int(y, bits, enc, F)?
                }
                ("int", Val::Int(x)) if vt.signed() => {
                    pk::from_signed(*x as i64, bits, enc, F)?
                }
                ("int", Val::Int(x)) => pk::from_unsigned(*x as u64, bits, F)?,
                ("enum", Val::Enum(x)) => pk::from_signed(*x, bits, enc, F)?,
                ("bool", Val::Bool(b)) => *b as u64,
                (_, Val::Float(x)) => {
                    let y = uncalibrate(vt, *x)?;
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
            let y = uncalibrate(vt, *x)?;
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
}

impl Enc<'_, '_> {
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
                    let off = count_of(&s.offset, &self.vals, &[])
                        .map_err(at(where_))?
                        - s.back;
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
        (_, Val::Bool(b)) => {
            let _ = write!(out, "{b}");
        }
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
