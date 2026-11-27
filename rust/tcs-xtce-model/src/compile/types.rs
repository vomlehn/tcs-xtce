//! Parameter and argument types: their encodings, calibrators, sizes,
//! arrays and aggregates, as the Python compiler's `vtype` and what it
//! calls make them.

use std::rc::Rc;

use num_bigint::BigInt;
use num_traits::{FromPrimitive, ToPrimitive, Zero};
use tcs_xtce::generated as x;
use x::ArgumentDynamicValueChoice as ADV;
use x::ArgumentIntegerValueChoice as AIV;
use x::ArgumentStringDataEncodingChoice as ASC;
use x::ArgumentVariableStringChoice1 as AV1;
use x::ArgumentVariableStringChoice2 as AV2;
use x::MathOperationCalibratorChoice as M;
use x::StringDataEncodingChoice as SC;
use x::VariableStringChoice1 as V1;
use x::VariableStringChoice2 as V2;

use super::xtype::{EncRef, XType};
use super::{
    ancillary_str, qname, Compiler, TypeTable, CCSDS_MAX_BYTES,
    EMBEDDED_TC_TAG, YAMCS_IGNORE,
};
use crate::dump::{Elem, Shared};
use crate::model::{
    unsupported, AggregateEnc, ArrayEnc, Band, BinaryEnc, CalCase, Calibrator,
    ContextCal, Count, DynSize, Encoding, EnumLabel, Error, Expr, FloatEnc,
    IntEnc, Key, LookupList, MathOp, Member, Num, Order, Polynomial, Result,
    Spline, Step, StringEnc, VType, ValueRef, XformEnc,
};
use crate::names::{snake, upper, Namer};
use crate::pyfmt::{g, repr};
use crate::text::{code_unit, encode_text, find_unit, wire_cap};

/// Gives the seconds in one of XTCE's time units.
///
/// # Arguments
///
/// * `units` - The units, as the schema spells them.
///
/// # Returns
///
/// The seconds in one of them.
///
/// # Panics
///
/// For units the schema doesn't name.
pub fn time_unit(units: &str) -> f64 {
    match units {
        "seconds" => 1.0,
        "milliseconds" => 1e-3,
        "microseconds" => 1e-6,
        "nanoseconds" => 1e-9,
        "picoseconds" => 1e-12,
        "minutes" => 60.0,
        "hours" => 3600.0,
        "days" => 86400.0,
        "months" => 2629746.0,
        "years" => 31556952.0,
        _ => panic!("an XTCE time unit"),
    }
}

/// The operators of a MathOperationCalibrator that take one operand.
pub const MATH_UNARY: &[&str] = &[
    "ln", "log", "e^x", "1/x", "x!", "tan", "cos", "sin", "atan", "acos",
    "asin", "tanh", "cosh", "sinh", "atanh", "acosh", "asinh", "abs", "int",
    "!", "~",
];

/// Says whether a name can be a member's after `/`, as the Python's
/// `[A-Za-z_][\w-]*` says.
///
/// # Arguments
///
/// * `s` - The name.
///
/// # Returns
///
/// Whether it can.
pub fn is_member_name(s: &str) -> bool {
    let mut cs = s.chars();
    match cs.next() {
        Some(c) if c.is_ascii_alphabetic() || c == '_' => {}
        _ => return false,
    }
    cs.all(|c| c.is_alphanumeric() || c == '_' || c == '-')
}

/// Splits a reference to an aggregate member or array element (`P.a.b`,
/// `P[2]`, `P[1].a`) into the reference to its parameter or argument and
/// the steps. Only the last path segment can have steps, and `.` and `..`
/// are path segments, not members.
///
/// # Arguments
///
/// * `ref_` - The reference, as written.
///
/// # Returns
///
/// The reference without its steps, and the steps.
///
/// # Errors
///
/// Unsupported, if the steps are malformed.
pub fn member_path(ref_: &str) -> Result<(String, Vec<Step>)> {
    let r = ref_.trim();
    let (head, sep, last) = match r.rfind('/') {
        Some(i) => (&r[..i], "/", &r[i + 1..]),
        None => ("", "", r),
    };
    let name_len = last
        .find(|c| c == '.' || c == '[' || c == ']')
        .unwrap_or(last.len());
    let name = &last[..name_len];
    if last == "." || last == ".." || name.is_empty() || name == last {
        return Ok((r.to_string(), Vec::new()));
    }
    let rest = &last[name_len..];
    let mut steps = Vec::new();
    let mut s = rest;
    while !s.is_empty() {
        if let Some(t) = s.strip_prefix('.') {
            let n = t
                .find(|c: char| {
                    matches!(c, '.' | '[' | ']' | ':' | '/' | ' ' | '\t')
                })
                .unwrap_or(t.len());
            if n == 0 {
                break;
            }
            steps.push(Step::Name(t[..n].to_string()));
            s = &t[n..];
        } else if let Some(t) = s.strip_prefix('[') {
            let n = t.find(|c: char| !c.is_ascii_digit()).unwrap_or(t.len());
            if n == 0 || !t[n..].starts_with(']') {
                break;
            }
            match t[..n].parse::<i64>() {
                Ok(i) => steps.push(Step::Index(i)),
                Err(_) => break,
            }
            s = &t[n + 1..];
        } else {
            break;
        }
    }
    if !s.is_empty() {
        return unsupported(format!("malformed reference {}", repr(r)));
    }
    Ok((format!("{head}{sep}{name}"), steps))
}

/// Gives the smallest value, and one more than the largest, that an
/// integer's generated field holds.
///
/// # Arguments
///
/// * `vt` - The integer's type.
///
/// # Returns
///
/// The limits, for its width and signedness.
pub fn eng_limits(vt: &VType) -> (BigInt, BigInt) {
    let bits = vt.width() as u32;
    if vt.signed() {
        let h = BigInt::from(1) << (bits - 1);
        (-h.clone(), h)
    } else {
        (BigInt::zero(), BigInt::from(1) << bits)
    }
}

/// Finds the inclusive range of an integer type's ValidRange, or of the
/// union of an argument type's ValidRangeSet.
///
/// # Arguments
///
/// * `t`          - The XTCE type.
/// * `calibrated` - Whether the type has a calibrator; the range must then
///                  apply to the calibrated value.
///
/// # Returns
///
/// (min, max), or `None` unless every bound is given inclusively.
pub fn integer_range(t: &XType, calibrated: bool) -> Option<(i64, i64)> {
    let ranges = t.valid_ranges()?.ranges();
    if ranges.is_empty()
        || ranges.iter().any(|r| {
            r.min_inclusive_int.is_none() || r.max_inclusive_int.is_none()
        })
    {
        return None;
    }
    if calibrated
        && ranges
            .iter()
            .any(|r| r.applies_to_calibrated == Some(false))
    {
        return None;
    }
    Some((
        ranges.iter().map(|r| r.min_inclusive_int.unwrap()).min()?,
        ranges.iter().map(|r| r.max_inclusive_int.unwrap()).max()?,
    ))
}

/// Converts a type's ValidRange, or an argument type's ValidRangeSet, to
/// bands a value must lie in (any of them).
///
/// # Arguments
///
/// * `t`          - The XTCE type.
/// * `vt`         - Its compiled type; integer bands are narrowed to whole
///                  numbers.
/// * `calibrated` - Whether the type has a calibrator.
///
/// # Returns
///
/// The bands; empty when there is no valid range, or when a calibrated
/// type's range applies to the raw value.
pub fn valid_bands(t: &XType, vt: &VType, calibrated: bool) -> Vec<Band> {
    let Some(vr) = t.valid_ranges() else {
        return Vec::new();
    };
    let ranges = vr.ranges();
    if calibrated
        && ranges
            .iter()
            .any(|r| r.applies_to_calibrated == Some(false))
    {
        return Vec::new();
    }
    let num = |i: Option<i64>, f: Option<f64>| -> Option<Num> {
        if vr.integer() {
            i.map(|v| Num::Int(BigInt::from(v)))
        } else {
            f.map(Num::Float)
        }
    };
    let mut out = Vec::new();
    for r in ranges {
        let lo_in = num(r.min_inclusive_int, r.min_inclusive);
        let hi_in = num(r.max_inclusive_int, r.max_inclusive);
        let lo_ex = r.min_exclusive.map(Num::Float);
        let hi_ex = r.max_exclusive.map(Num::Float);
        let b = Band {
            level: 0,
            lo_incl: lo_in.is_some() || lo_ex.is_none(),
            lo: lo_in.or(lo_ex),
            hi_incl: hi_in.is_some() || hi_ex.is_none(),
            hi: hi_in.or(hi_ex),
            inside: true,
        };
        out.push(if vt.kind == "int" {
            int_band(&b, vt)
        } else {
            b
        });
    }
    out
}

/// Rounds a number up, as Python's `math.ceil` does.
///
/// # Arguments
///
/// * `n` - The number, finite.
///
/// # Returns
///
/// The least integer at least `n`.
fn ceil(n: &Num) -> BigInt {
    match n {
        Num::Int(i) => i.clone(),
        Num::Float(x) => BigInt::from_f64(x.ceil()).expect("a finite float"),
    }
}

/// Rounds a number down, as Python's `math.floor` does.
///
/// # Arguments
///
/// * `n` - The number, finite.
///
/// # Returns
///
/// The greatest integer at most `n`.
fn floor(n: &Num) -> BigInt {
    match n {
        Num::Int(i) => i.clone(),
        Num::Float(x) => BigInt::from_f64(x.floor()).expect("a finite float"),
    }
}

/// Says whether a number is finite.
///
/// # Arguments
///
/// * `n` - The number.
///
/// # Returns
///
/// True for every int, and for floats that are neither infinite nor NaN.
fn finite(n: &Num) -> bool {
    match n {
        Num::Int(_) => true,
        Num::Float(x) => x.is_finite(),
    }
}

/// Makes a band for integer values: whole, inclusive bounds within the
/// range of the field's type (`None` when the type's own limit is as
/// far), so that every language compares integers exactly. An empty range
/// is 1 to 0.
///
/// # Arguments
///
/// * `b`  - The band.
/// * `vt` - The integer field's type.
///
/// # Returns
///
/// The integer band.
pub fn int_band(b: &Band, vt: &VType) -> Band {
    let (lo_lim, hi_lim) = eng_limits(vt);
    let hi_lim = hi_lim - 1;
    let mut lo: Option<BigInt> = None;
    let mut hi: Option<BigInt> = None;
    if let Some(l) = &b.lo {
        if !finite(l) {
            lo = if l.as_f64() < 0.0 {
                None
            } else {
                Some(&hi_lim + 1)
            };
        } else {
            lo = Some(if b.lo_incl { ceil(l) } else { floor(l) + 1 });
        }
    }
    if let Some(h) = &b.hi {
        if !finite(h) {
            hi = if h.as_f64() > 0.0 {
                None
            } else {
                Some(&lo_lim - 1)
            };
        } else {
            hi = Some(if b.hi_incl { floor(h) } else { ceil(h) - 1 });
        }
    }
    let empty = lo.as_ref().is_some_and(|l| *l > hi_lim)
        || hi.as_ref().is_some_and(|h| *h < lo_lim)
        || matches!((&lo, &hi), (Some(l), Some(h)) if l > h);
    if empty {
        return Band {
            level: b.level,
            lo: Some(Num::Int(1.into())),
            lo_incl: true,
            hi: Some(Num::Int(0.into())),
            hi_incl: true,
            inside: b.inside,
        };
    }
    let lo = lo.filter(|l| *l > lo_lim);
    let hi = hi.filter(|h| *h < hi_lim);
    Band {
        level: b.level,
        lo: lo.map(Num::Int),
        lo_incl: true,
        hi: hi.map(Num::Int),
        hi_incl: true,
        inside: b.inside,
    }
}

/// Finds the smallest and largest value an integer encoding holds.
///
/// # Arguments
///
/// * `e` - The encoding.
///
/// # Returns
///
/// (smallest, largest).
pub fn int_range(e: &IntEnc) -> (BigInt, BigInt) {
    let b = e.bits as u32;
    let one = BigInt::from(1);
    match e.encoding.as_str() {
        "unsigned" => (BigInt::zero(), (&one << b) - 1),
        "BCD" => (BigInt::zero(), BigInt::from(10).pow(b / 8) - 1),
        "packedBCD" => (BigInt::zero(), BigInt::from(10).pow(b / 4) - 1),
        "twosComplement" => (-(&one << (b - 1)), (&one << (b - 1)) - 1),
        _ => {
            let h: BigInt = &one << (b - 1);
            let lo: BigInt = -(&h - BigInt::from(1));
            (lo, h - 1)
        }
    }
}

/// Finds the smallest and largest value of an integer type, from its
/// encoding and calibrator.
///
/// # Arguments
///
/// * `vt` - The type.
///
/// # Returns
///
/// (smallest, largest); for a calibrator whose range isn't known, those
/// of the type's own size.
pub fn value_range(vt: &VType) -> (BigInt, BigInt) {
    let Encoding::Int(e) = &vt.enc else {
        panic!("value_range of a type that is not an integer");
    };
    let (lo, hi) = int_range(e);
    let Some(cal) = &vt.calibrator else {
        return (lo, hi);
    };
    let (elo, elim) = eng_limits(vt);
    let top = &elim - 1;
    let ends: Vec<f64> = match cal {
        Calibrator::Poly(p) if p.invertible() => [&lo, &hi]
            .iter()
            .map(|x| {
                p.slope() * x.to_f64().unwrap_or(f64::INFINITY) + p.intercept()
            })
            .collect(),
        Calibrator::Spline(s) if !s.extrapolate && s.orders.is_empty() => {
            s.points.iter().map(|p| p.1).collect()
        }
        _ => return (elo, top),
    };
    if !ends.iter().all(|x| x.is_finite()) {
        return (elo, top);
    }
    let mn = ends.iter().cloned().fold(f64::INFINITY, f64::min);
    let mx = ends.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    let a = BigInt::from_f64(mn.floor()).expect("finite");
    let b = BigInt::from_f64(mx.ceil()).expect("finite");
    (a.max(elo), b.min(top))
}

/// Gives the smallest and largest value of an integer type.
///
/// # Arguments
///
/// * `vt` - The type.
///
/// # Returns
///
/// Its valid range, or else its value range.
pub fn int_bounds(vt: &VType) -> (BigInt, BigInt) {
    match vt.valid_range {
        Some((a, b)) => (a.into(), b.into()),
        None => value_range(vt),
    }
}

/// Finds the largest size or count a DynSize gives.
///
/// # Arguments
///
/// * `b`   - The DynSize.
/// * `rng` - The range (lo, hi) of the value it adjusts.
/// * `cap` - The most it may be.
///
/// # Returns
///
/// The largest `(slope * x + intercept) / unit` for x in `rng`, at least
/// 0 and at most `cap`.
pub fn dyn_units(b: &DynSize, rng: &(BigInt, BigInt), cap: i64) -> i64 {
    let f = |v: &BigInt| v.to_f64().unwrap_or(f64::INFINITY);
    let top = (b.slope * f(&rng.0)).max(b.slope * f(&rng.1)) + b.intercept;
    if !top.is_finite() {
        return cap;
    }
    let fl = top.floor().max(0.0);
    // floor(top) // unit, at most cap.
    if fl >= 9.0e18 {
        return cap;
    }
    ((fl as i64) / b.unit).min(cap)
}

/// Finds the most bits a value of a type occupies.
///
/// # Arguments
///
/// * `vt` - The type.
///
/// # Returns
///
/// The bits.
pub fn item_bits(vt: &VType) -> i64 {
    match &vt.enc {
        Encoding::Array(e) => {
            let n = e.count.max_total();
            n * item_bits(&e.elem) + (n - 1).max(0) * e.gap.max_total()
        }
        Encoding::Aggregate(e) => match &e.transform {
            Some(t) => t.bits(),
            None => {
                e.members
                    .iter()
                    .map(|m| m.skip + item_bits(&m.vtype))
                    .sum::<i64>()
                    + e.pad
            }
        },
        Encoding::String(e) => e.max_bits,
        e => e.bits(),
    }
}

/// Says whether a value takes the rest of the packet.
///
/// # Arguments
///
/// * `vt` - The value's type.
///
/// # Returns
///
/// Whether it does.
pub fn is_rest(vt: &VType) -> bool {
    match &vt.enc {
        Encoding::Binary(e) => e.rest,
        Encoding::Xform(e) => e.raw.is_none() && !e.sized,
        _ => false,
    }
}

/// Makes a type's size or count a bound DynSize, and sets the most it may
/// be.
///
/// # Arguments
///
/// * `vt`  - The type: an array, string or binary data.
/// * `b`   - The bound DynSize; its `max` is set.
/// * `rng` - The range (lo, hi) of the value it adjusts.
/// * `cap` - The most bits the field may take.
///
/// # Returns
///
/// A copy of `vt` whose size or count is `b`.
pub fn bounded(
    vt: &VType,
    mut b: DynSize,
    rng: &(BigInt, BigInt),
    cap: i64,
) -> VType {
    let units = dyn_units(&b, rng, cap);
    let enc = match &vt.enc {
        Encoding::Array(e) => {
            let gap = e.gap.max_total();
            let step = (item_bits(&e.elem) + gap).max(1);
            b.max =
                units.min((cap + gap).div_euclid(step).div_euclid(b.factor));
            Encoding::Array(ArrayEnc {
                count: Count::Dyn(b),
                ..e.clone()
            })
        }
        Encoding::String(e) => {
            b.max = units.min(e.max_bits.div_euclid(8)).min(cap.div_euclid(8));
            Encoding::String(StringEnc {
                max_bits: b.max * 8,
                dyn_: Some(b),
                ..e.clone()
            })
        }
        Encoding::Binary(e) => {
            b.max = units.min(cap.div_euclid(8));
            Encoding::Binary(BinaryEnc {
                bits: b.max * 8,
                dyn_: Some(b),
                ..e.clone()
            })
        }
        _ => panic!("bounded of a type without a size"),
    };
    VType { enc, ..vt.clone() }
}

/// Composes a calibrator with an affine function.
///
/// # Arguments
///
/// * `cal` - The calibrator, or `None` for the identity.
/// * `a`   - The factor.
/// * `b`   - The term added.
///
/// # Returns
///
/// A calibrator giving `a * cal(x) + b`, or `cal` itself when `a` is 1 and
/// `b` 0.
pub fn affine(cal: Option<Calibrator>, a: f64, b: f64) -> Option<Calibrator> {
    if a == 1.0 && b == 0.0 {
        return cal;
    }
    Some(match cal {
        None => Calibrator::Poly(Polynomial {
            terms: vec![(b, 0), (a, 1)],
        }),
        Some(Calibrator::Poly(p)) => {
            let mut terms: Vec<(f64, i64)> =
                p.terms.iter().map(|&(c, e)| (a * c, e)).collect();
            if b != 0.0 {
                terms.push((b, 0));
            }
            Calibrator::Poly(Polynomial { terms })
        }
        Some(Calibrator::Spline(s)) => Calibrator::Spline(Spline {
            points: s.points.iter().map(|&(r, c)| (r, a * c + b)).collect(),
            ..s
        }),
        Some(Calibrator::Math(m)) => Calibrator::Math(MathOp {
            expr: Expr::Op(
                "+".into(),
                vec![
                    Expr::Op("*".into(), vec![m.expr, Expr::Const(a)]),
                    Expr::Const(b),
                ],
            ),
        }),
        Some(Calibrator::Context(c)) => Calibrator::Context(ContextCal {
            cases: c
                .cases
                .into_iter()
                .map(|case| match case {
                    CalCase::Unbound(p, m, k) => {
                        CalCase::Unbound(p, m, affine(Some(k), a, b).unwrap())
                    }
                    CalCase::Bound(t, k) => {
                        CalCase::Bound(t, affine(Some(k), a, b).unwrap())
                    }
                })
                .collect(),
            default: affine(c.default.map(|d| *d), a, b).map(Box::new),
            bound: c.bound,
        }),
    })
}

/// The bytes of an `xs:hexBinary` value; an odd number of digits is read
/// as if it had a leading 0.
///
/// # Arguments
///
/// * `text`   - The digits.
/// * `where_` - Where they are, for error messages.
///
/// # Returns
///
/// The bytes.
///
/// # Errors
///
/// Unsupported, if the text isn't hexadecimal.
pub fn hex_bytes(text: &str, where_: &str) -> Result<Vec<u8>> {
    if !text.chars().all(|c| c.is_ascii_hexdigit()) {
        return unsupported(format!(
            "{where_}: {} is not hexBinary",
            repr(text)
        ));
    }
    let padded = if text.len() % 2 == 1 {
        format!("0{text}")
    } else {
        text.to_string()
    };
    Ok((0..padded.len() / 2)
        .map(|i| u8::from_str_radix(&padded[2 * i..2 * i + 2], 16).unwrap())
        .collect())
}

/// An IntegerValueType, of a parameter or an argument.
#[derive(Debug, Clone, Copy)]
pub enum IntValueRef<'b> {
    /// A parameter's.
    P(&'b x::IntegerValueType),
    /// An argument's.
    A(&'b x::ArgumentIntegerValueType),
}

/// A DynamicValue, of a parameter or an argument.
#[derive(Debug, Clone, Copy)]
pub enum DynRef<'b> {
    /// A parameter's.
    P(&'b x::DynamicValueType),
    /// An argument's.
    A(&'b x::ArgumentDynamicValueType),
}

/// What an IntegerValueType gives: a number, or an unbound DynSize.
#[derive(Debug, Clone)]
pub enum IntValue {
    /// A FixedValue.
    Int(i64),
    /// A DynamicValue or DiscreteLookupList.
    Dyn(DynSize),
}

impl Compiler<'_> {
    /// Compiles a parameter or argument type, once.
    ///
    /// # Arguments
    ///
    /// * `key`   - The type's key.
    /// * `table` - The table it is in.
    ///
    /// # Returns
    ///
    /// The compiled type, with a parameter type's alarm.
    ///
    /// # Errors
    ///
    /// Unsupported, if the type uses something not supported; Cycle, for a
    /// loop of base types or an aggregate that contains itself.
    pub fn vtype(&mut self, key: &Key, table: TypeTable) -> Result<Rc<VType>> {
        let cache = (table, key.clone());
        if let Some(vt) = self.vtypes.get(&cache) {
            return Ok(vt.clone());
        }
        let mut vt = self.vtype_inner(key, table)?;
        if table == TypeTable::Param && vt.alarm.is_none() {
            let path = self.ptypes.get(key).expect("a type's key").0.clone();
            let t = self.inherited(key, table, &[])?;
            vt.alarm =
                self.alarm(&t, &vt, &path, &format!("type {}", qname(key)));
        }
        let vt = Rc::new(vt);
        self.vtypes.insert(cache, vt.clone());
        Ok(vt)
    }

    /// Compiles a parameter or argument type, without its alarm.
    ///
    /// # Arguments
    ///
    /// * `key`   - The type's key.
    /// * `table` - The table it is in.
    ///
    /// # Returns
    ///
    /// The compiled type.
    ///
    /// # Errors
    ///
    /// Unsupported, if the type uses something not supported; Cycle, for a
    /// loop.
    fn vtype_inner(&mut self, key: &Key, table: TypeTable) -> Result<VType> {
        let (path, kind) = {
            let (p, t) = self.types(table).get(key).expect("a type's key");
            (p.clone(), t.kind())
        };
        let where_ = format!("type {}", qname(key));
        let t = self.inherited(key, table, &[])?;
        let base = kind
            .replace("ParameterType", "")
            .replace("ArgumentType", "");
        match base.as_str() {
            "AbsoluteTime" | "RelativeTime" => {
                return self.time_type(key, &base, &t, &path, &where_)
            }
            "Array" => return self.array_type(key, table, &path, &t, &where_),
            "Aggregate" => {
                return self.aggregate_type(key, table, &path, &t, &where_)
            }
            "Integer" | "Float" | "Enumerated" | "Boolean" | "String"
            | "Binary" => {}
            _ => {
                return unsupported(format!(
                    "{where_}: {kind} is not supported"
                ))
            }
        }
        let Some(e) = t.data_encoding() else {
            return unsupported(format!("{where_}: no data encoding"));
        };
        let enc_name = e.name();
        let (mut enc, cal) = self.encoding(e, &path, &where_)?;
        if let Encoding::Binary(b) = &enc {
            if b.transform.is_some() {
                if base != "Binary" {
                    return unsupported(format!(
                        "{where_}: {} with transform algorithms",
                        base.to_lowercase()
                    ));
                }
                enc = Encoding::Xform(self.xform(b, false, &where_)?);
            } else if b.rest
                && base == "Binary"
                && self.dialect.as_deref() == Some("yamcs")
                && ancillary_str(
                    t.ancillary_data_set(),
                    EMBEDDED_TC_TAG,
                    &where_,
                )?
                .is_some()
            {
                // Its own packet length field gives its size.
                let mut emb = BinaryEnc::new(b.bits.min(8 * CCSDS_MAX_BYTES));
                emb.embedded = true;
                enc = Encoding::Binary(emb);
            }
        }
        let mut vt = VType::new(&qname(key), t.name(), "", enc);
        vt.calibrator = cal;
        let calibrated = vt.calibrator.is_some();
        match base.as_str() {
            "Float" => {
                if !matches!(vt.enc, Encoding::Int(_) | Encoding::Float(_)) {
                    return unsupported(format!(
                        "{where_}: float with {enc_name}"
                    ));
                }
                vt.kind = "float".into();
                vt.valid = valid_bands(&t, &vt, calibrated);
            }
            "Integer" | "Enumerated" | "Boolean" => {
                let ok = matches!(vt.enc, Encoding::Int(_))
                    || (base == "Boolean"
                        && matches!(vt.enc, Encoding::String(_)));
                if !ok {
                    return unsupported(format!(
                        "{where_}: {} with {enc_name}",
                        base.to_lowercase()
                    ));
                }
                if calibrated && base != "Integer" {
                    let article = if "AEIOU".contains(&base[..1]) {
                        "an"
                    } else {
                        "a"
                    };
                    return unsupported(format!(
                        "{where_}: calibrator on {article} {} type",
                        base.to_lowercase()
                    ));
                }
                vt.kind = match base.as_str() {
                    "Integer" => "int",
                    "Enumerated" => "enum",
                    _ => "bool",
                }
                .into();
                if base == "Integer" {
                    if calibrated {
                        // The calibrated value is rounded to an integer of
                        // the type's own size, not the encoding's.
                        let (size, signed) = t.int_size();
                        let bits = size.unwrap_or(32);
                        if !(1..=64).contains(&bits) {
                            return unsupported(format!(
                                "{where_}: {bits}-bit integers"
                            ));
                        }
                        vt.eng = Some((bits, signed != Some(false)));
                    }
                    vt.valid_range = integer_range(&t, calibrated);
                    vt.valid = valid_bands(&t, &vt, calibrated);
                }
            }
            "String" => {
                // A string encoded as binary is its UTF-8 bytes.
                match &vt.enc {
                    Encoding::String(_) => {}
                    Encoding::Binary(b) if b.rest => {
                        return unsupported(format!(
                            "{where_}: string with binary size -1"
                        ))
                    }
                    Encoding::Binary(_) => {}
                    _ => {
                        return unsupported(format!(
                            "{where_}: string with {enc_name}"
                        ))
                    }
                }
                vt.kind = "string".into();
            }
            _ => {
                let rest = match &vt.enc {
                    Encoding::Binary(b) => b.rest,
                    Encoding::Xform(x) => x.raw.is_none() && !x.time,
                    _ => {
                        return unsupported(format!(
                            "{where_}: binary with {enc_name}"
                        ))
                    }
                };
                vt.kind = "binary".into();
                // Nothing but the end of the packet bounds it; an
                // EmbeddedTc's own packet length does.
                if rest {
                    self.warn(format!(
                        "{where_}: binary data that takes the rest of the \
                         packet, of no maximum size: the generated code's \
                         buffer is the maximum field size, {} bytes, instead",
                        self.max_field_bits / 8
                    ));
                }
            }
        }
        if vt.kind == "enum" {
            let mut names = Namer::default();
            for ev in &t.enumeration_list().expect("an enumeration").enumeration
            {
                let hi = ev.max_value.unwrap_or(ev.value);
                vt.labels.push(EnumLabel {
                    label: ev.label.clone(),
                    ident: names.take(&[&upper(&ev.label)]),
                    value: ev.value,
                    max_value: hi,
                });
            }
            check_enum_values(&vt, &where_)?;
        }
        if vt.kind == "bool" {
            let (one, zero) = t.bool_strings();
            vt.one_string = one.unwrap_or("True").into();
            vt.zero_string = zero.unwrap_or("False").into();
            if matches!(vt.enc, Encoding::String(_)) {
                check_bool_strings(&vt, &where_)?;
            }
        }
        Ok(vt)
    }

    /// Fills in what a type inherits from its baseType, if it has one:
    /// every attribute and child element it leaves out comes from its base
    /// type, which must be the same kind of type.
    ///
    /// # Arguments
    ///
    /// * `key`   - The type's key.
    /// * `table` - The table it is in.
    /// * `seen`  - The keys of the types that inherit from it, to find cycles.
    ///
    /// # Returns
    ///
    /// The XTCE type, with what it inherits, and no baseType.
    ///
    /// # Errors
    ///
    /// Unsupported, if a base type is another kind of type; Cycle, if the
    /// chain of base types is a loop.
    pub fn inherited(
        &self,
        key: &Key,
        table: TypeTable,
        seen: &[Key],
    ) -> Result<XType> {
        let (path, t) = self.types(table).get(key).expect("a type's key");
        let Some(bt) = t.base_type().filter(|b| !b.is_empty()) else {
            return Ok(t.clone());
        };
        let where_ = format!("type {}", qname(key));
        if seen.contains(key) {
            return Err(Error::Cycle(format!(
                "{where_}: its baseType chain is a loop"
            )));
        }
        let bkey = self.resolve_type(bt, path, table, "base type")?;
        let bkind = self.types(table).get(&bkey).unwrap().1.kind();
        if bkind != t.kind() {
            return unsupported(format!(
                "{where_}: its baseType {} is another kind of type, {bkind}",
                qname(&bkey)
            ));
        }
        let mut seen2 = seen.to_vec();
        seen2.push(key.clone());
        let base = self.inherited(&bkey, table, &seen2)?;
        Ok(t.inherit(&base))
    }

    /// Compiles an absolute or relative time type: seconds from the
    /// reference time, the encoding's raw value, calibrated, then
    /// `scale * value + offset` in its units. A time encoded as a string
    /// or as binary data keeps that text or those bytes.
    ///
    /// # Arguments
    ///
    /// * `key`    - The type's key.
    /// * `base`   - `AbsoluteTime` or `RelativeTime`.
    /// * `t`      - The XTCE type, with what it inherits.
    /// * `path`   - The key of the SpaceSystem it is defined in.
    /// * `where_` - What it is, for error messages.
    ///
    /// # Returns
    ///
    /// The compiled type: a float, or the string or binary data.
    ///
    /// # Errors
    ///
    /// Unsupported, if it has no encoding, its reference time is invalid,
    /// or a string or binary time has units, scale or offset.
    fn time_type(
        &mut self,
        key: &Key,
        base: &str,
        t: &XType,
        path: &Key,
        where_: &str,
    ) -> Result<VType> {
        let (enc, rt) = t.time();
        let Some(enc) = enc else {
            return unsupported(format!(
                "{where_}: time type without Encoding"
            ));
        };
        let units = enc.units.map(|u| u.as_str()).unwrap_or("seconds");
        let scale = enc.scale.unwrap_or(1.0);
        let offset = enc.offset.unwrap_or(0.0);
        let what = if base == "AbsoluteTime" {
            "absolute"
        } else {
            "relative"
        };
        let ref_ = reference_time(rt, where_)?;
        let e = match &enc.choice {
            x::EncodingChoice::BinaryDataEncoding(e) => EncRef::Bin(e),
            x::EncodingChoice::FloatDataEncoding(e) => EncRef::Float(e),
            x::EncodingChoice::IntegerDataEncoding(e) => EncRef::Int(e),
            x::EncodingChoice::StringDataEncoding(e) => EncRef::Str(e),
        };
        let (enc_v, cal) = self.encoding(e, path, where_)?;
        let name = t.name();
        match &enc_v {
            Encoding::String(_) | Encoding::Binary(_) => {
                if (units, scale, offset) != ("seconds", 1.0, 0.0) {
                    return unsupported(format!(
                        "{where_}: a time encoded as {} with units, scale or \
                         offset",
                        e.name()
                    ));
                }
                if let Encoding::Binary(b) = &enc_v {
                    if b.transform.is_some() {
                        // The transform algorithms give the time in
                        // seconds.
                        let xf = self.xform(b, true, where_)?;
                        let mut vt = VType::new(
                            &qname(key),
                            name,
                            "float",
                            Encoding::Xform(xf),
                        );
                        vt.doc = format!("{what} time, seconds {ref_}");
                        return Ok(vt);
                    }
                }
                let kind = if matches!(enc_v, Encoding::String(_)) {
                    "string"
                } else {
                    "binary"
                };
                let mut vt = VType::new(&qname(key), name, kind, enc_v);
                vt.doc = format!("{what} time as encoded, {ref_}");
                Ok(vt)
            }
            _ => {
                let a = scale * time_unit(units);
                let cal = affine(cal, a, offset * time_unit(units));
                let mut vt = VType::new(&qname(key), name, "float", enc_v);
                vt.calibrator = cal;
                vt.doc = format!("{what} time, seconds {ref_}");
                Ok(vt)
            }
        }
    }

    /// Compiles an array type: its elements in the order they are
    /// encoded.
    ///
    /// # Arguments
    ///
    /// * `key`    - The type's key.
    /// * `table`  - The table it is in.
    /// * `path`   - The key of the SpaceSystem it is defined in.
    /// * `t`      - The XTCE type.
    /// * `where_` - What it is, for error messages.
    ///
    /// # Returns
    ///
    /// The compiled type, with an ArrayEnc.
    ///
    /// # Errors
    ///
    /// Unsupported, if its elements take the rest of the packet or its
    /// dimensions are invalid.
    fn array_type(
        &mut self,
        key: &Key,
        table: TypeTable,
        path: &Key,
        t: &XType,
        where_: &str,
    ) -> Result<VType> {
        let a = t.array().expect("an array type");
        let elem_ref = match a {
            super::xtype::ArrayRef::P(p) => &p.array_type_ref,
            super::xtype::ArrayRef::A(p) => &p.array_type_ref,
        };
        let ekey =
            self.resolve_type(elem_ref, path, table, "array element type")?;
        // An element may itself be an array, and its size may come from
        // another value: each packet entry binds it.
        let elem = self.vtype(&ekey, table)?;
        if is_rest(&elem) {
            return unsupported(format!(
                "{where_}: elements that take the rest of the packet"
            ));
        }
        let dims: Vec<(IntValueRef<'_>, IntValueRef<'_>)> = match a {
            super::xtype::ArrayRef::P(p) => p
                .dimension_list
                .dimension
                .iter()
                .map(|d| {
                    (
                        IntValueRef::P(&d.starting_index),
                        IntValueRef::P(&d.ending_index),
                    )
                })
                .collect(),
            super::xtype::ArrayRef::A(p) => p
                .dimension_list
                .dimension
                .iter()
                .map(|d| {
                    (
                        IntValueRef::A(&d.starting_index),
                        IntValueRef::A(&d.ending_index),
                    )
                })
                .collect(),
        };
        let enc = self.dimensions(elem, &dims, path, where_)?;
        Ok(VType::new(
            &qname(key),
            t.name(),
            "array",
            Encoding::Array(enc),
        ))
    }

    /// Compiles a DimensionList, flattened with the last dimension varying
    /// fastest.
    ///
    /// # Arguments
    ///
    /// * `elem`   - The compiled type of the elements.
    /// * `dims`   - Each dimension's StartingIndex and EndingIndex.
    /// * `path`   - The key of the SpaceSystem it is written in.
    /// * `where_` - What it belongs to, for error messages.
    ///
    /// # Returns
    ///
    /// The ArrayEnc of the elements.
    ///
    /// # Errors
    ///
    /// Unsupported, if there are no dimensions, a StartingIndex comes from
    /// another value, an EndingIndex is before its StartingIndex, or more
    /// than one dimension's size comes from another value.
    pub fn dimensions(
        &mut self,
        elem: Rc<VType>,
        dims: &[(IntValueRef<'_>, IntValueRef<'_>)],
        path: &Key,
        where_: &str,
    ) -> Result<ArrayEnc> {
        if dims.is_empty() {
            return unsupported(format!("{where_}: array without dimensions"));
        }
        let mut fixed: i64 = 1;
        let mut dyn_: Option<DynSize> = None;
        let mut starts = Vec::new();
        let mut sizes = Vec::new();
        let mut all_sizes = Vec::new();
        for (s, e) in dims {
            let start =
                self.int_value(*s, path, "array StartingIndex", where_)?;
            let end = self.int_value(*e, path, "array EndingIndex", where_)?;
            let IntValue::Int(start) = start else {
                return unsupported(format!(
                    "{where_}: StartingIndex from DynamicValue"
                ));
            };
            starts.push(start);
            match end {
                IntValue::Int(end) => {
                    if end < start - 1 {
                        return unsupported(format!(
                            "{where_}: EndingIndex {end} is before \
                             StartingIndex {start}"
                        ));
                    }
                    fixed *= end - start + 1;
                    sizes.push(end - start + 1);
                    all_sizes.push(Some(end - start + 1));
                }
                IntValue::Dyn(_) if dyn_.is_some() => {
                    return unsupported(format!(
                        "{where_}: more than one dimension from DynamicValue"
                    ))
                }
                IntValue::Dyn(mut d) => {
                    // The count is end - start + 1.
                    d.intercept += (1 - start) as f64;
                    dyn_ = Some(d);
                    all_sizes.push(None);
                }
            }
        }
        let shape = if dyn_.is_none() { sizes } else { Vec::new() };
        let count = match dyn_ {
            Some(mut d) => {
                d.factor = fixed;
                Count::Dyn(d)
            }
            None => Count::Int(fixed),
        };
        Ok(ArrayEnc {
            elem,
            count,
            gap: Count::Int(0),
            dims: dims.len() as i64,
            starts,
            shape,
            sizes: all_sizes,
        })
    }

    /// Compiles an aggregate type: its members, encoded one after
    /// another.
    ///
    /// # Arguments
    ///
    /// * `key`    - The type's key.
    /// * `table`  - The table it is in.
    /// * `path`   - The key of the SpaceSystem it is defined in.
    /// * `t`      - The XTCE type.
    /// * `where_` - What it is, for error messages.
    ///
    /// # Returns
    ///
    /// The compiled type, with an AggregateEnc.
    ///
    /// # Errors
    ///
    /// Unsupported, if it has no members, or a member takes the rest of
    /// the packet or has a size that isn't from an earlier integer member;
    /// Cycle, if it contains itself.
    fn aggregate_type(
        &mut self,
        key: &Key,
        table: TypeTable,
        path: &Key,
        t: &XType,
        where_: &str,
    ) -> Result<VType> {
        let cache = (table, key.clone());
        if self.aggregating.contains(&cache) {
            return Err(Error::Cycle(format!(
                "{where_}: aggregate contains itself, a loop"
            )));
        }
        let ml = t.member_list().expect("an aggregate type");
        if ml.member.is_empty() {
            return unsupported(format!("{where_}: aggregate without members"));
        }
        self.aggregating.insert(cache.clone());
        let r = self.members(ml, table, path, where_);
        self.aggregating.remove(&cache);
        let members = r?;
        Ok(VType::new(
            &qname(key),
            t.name(),
            "aggregate",
            Encoding::Aggregate(AggregateEnc {
                members,
                pad: 0,
                checks: Vec::new(),
                this: None,
                transform: None,
            }),
        ))
    }

    /// Compiles the members of an aggregate type.
    ///
    /// # Arguments
    ///
    /// * `ml`     - Its MemberList.
    /// * `table`  - The table it is in.
    /// * `path`   - The key of the SpaceSystem it is defined in.
    /// * `where_` - What it is, for error messages.
    ///
    /// # Returns
    ///
    /// The members.
    ///
    /// # Errors
    ///
    /// As [`Compiler::aggregate_type`]'s.
    fn members(
        &mut self,
        ml: &x::MemberListType,
        table: TypeTable,
        path: &Key,
        where_: &str,
    ) -> Result<Vec<Member>> {
        let mut names = Namer::default();
        let mut members: Vec<Member> = Vec::new();
        for m in &ml.member {
            let mkey =
                self.resolve_type(&m.type_ref, path, table, "member type")?;
            let mut mt = self.vtype(&mkey, table)?;
            if let Some(d) = mt.dyn_size().cloned() {
                if d.table.is_some() {
                    return unsupported(format!(
                        "{where_}: member {} has a size from a \
                         DiscreteLookupList",
                        m.name
                    ));
                }
                // Like Yamcs, a reference to an earlier member's name means
                // that member of the same aggregate value.
                let Some(sib) = members.iter().find(|x| x.name == d.name)
                else {
                    return unsupported(format!(
                        "{where_}: member {} has a size that another value \
                         gives, and {} is not an earlier member",
                        m.name, d.name
                    ));
                };
                if sib.vtype.kind != "int" {
                    return unsupported(format!(
                        "{where_}: size or count from {} member {}",
                        sib.vtype.kind, sib.name
                    ));
                }
                let b = DynSize {
                    member: Some(sib.ident.clone()),
                    ..d
                };
                let rng = int_bounds(&sib.vtype);
                mt = Rc::new(self.bound(&mt, b, &rng, where_));
            }
            if is_rest(&mt) {
                return unsupported(format!(
                    "{where_}: member {} takes the rest of the packet",
                    m.name
                ));
            }
            members.push(Member {
                name: m.name.clone(),
                ident: names.take(&[&snake(&m.name)]),
                vtype: mt,
                skip: 0,
            });
        }
        Ok(members)
    }

    /// Compiles an IntegerValueType.
    ///
    /// # Arguments
    ///
    /// * `iv`     - The XTCE IntegerValueType.
    /// * `path`   - The key of the SpaceSystem it is written in.
    /// * `what`   - What it gives, for error messages.
    /// * `where_` - What it belongs to, for error messages.
    ///
    /// # Returns
    ///
    /// A number for a FixedValue, or an unbound DynSize for a
    /// DynamicValue or DiscreteLookupList.
    ///
    /// # Errors
    ///
    /// As [`Compiler::dyn_size`]'s.
    pub fn int_value(
        &mut self,
        iv: IntValueRef<'_>,
        path: &Key,
        what: &str,
        where_: &str,
    ) -> Result<IntValue> {
        match iv {
            IntValueRef::P(v) => match &v.choice {
                x::IntegerValueChoice::FixedValue(n) => Ok(IntValue::Int(*n)),
                x::IntegerValueChoice::DynamicValue(d) => Ok(IntValue::Dyn(
                    self.dyn_size(DynRef::P(d), path, what, where_)?,
                )),
                x::IntegerValueChoice::DiscreteLookupList(l) => {
                    Ok(IntValue::Dyn(lookup_size(
                        path,
                        LookupList::Param(Elem {
                            value: l.clone(),
                            class: "DiscreteLookupListType",
                        }),
                    )))
                }
            },
            IntValueRef::A(v) => match &v.choice {
                AIV::FixedValue(n) => Ok(IntValue::Int(*n)),
                AIV::DynamicValue(d) => Ok(IntValue::Dyn(self.dyn_size(
                    DynRef::A(d),
                    path,
                    what,
                    where_,
                )?)),
                AIV::DiscreteLookupList(l) => Ok(IntValue::Dyn(lookup_size(
                    path,
                    LookupList::Arg(Elem {
                        value: l.clone(),
                        class: "ArgumentDiscreteLookupListType",
                    }),
                ))),
            },
        }
    }

    /// Compiles a DynamicValue (or argument DynamicValue), unbound.
    ///
    /// # Arguments
    ///
    /// * `dv`     - The XTCE DynamicValue.
    /// * `path`   - The key of the SpaceSystem it is written in.
    /// * `what`   - What it gives, for error messages.
    /// * `where_` - What it belongs to, for error messages.
    ///
    /// # Returns
    ///
    /// The DynSize, naming the parameter (or argument) and member steps
    /// and holding the LinearAdjustment.
    ///
    /// # Errors
    ///
    /// Unsupported, if it refers to a later instance of a parameter, its
    /// LinearAdjustment isn't finite, or its reference is malformed.
    pub fn dyn_size(
        &mut self,
        dv: DynRef<'_>,
        path: &Key,
        what: &str,
        where_: &str,
    ) -> Result<DynSize> {
        /// The reference a DynamicValue makes.
        enum R<'c> {
            /// A ParameterInstanceRef.
            P(&'c x::ParameterInstanceRefType),
            /// An ArgumentInstanceRef.
            A(&'c x::ArgumentInstanceRefType),
        }
        let (r, adj) = match dv {
            DynRef::P(d) => {
                (R::P(&d.parameter_instance_ref), &d.linear_adjustment)
            }
            DynRef::A(d) => (
                match &d.choice {
                    ADV::ArgumentInstanceRef(a) => R::A(a),
                    ADV::ParameterInstanceRef(p) => R::P(p),
                },
                &d.linear_adjustment,
            ),
        };
        let instance = match &r {
            R::P(p) => p.instance.unwrap_or(0),
            R::A(_) => 0,
        };
        if instance > 0 {
            return unsupported(format!(
                "{where_}: {what} from instance {instance} of a parameter, a \
                 value yet to come"
            ));
        }
        let slope = adj.as_ref().and_then(|a| a.slope).unwrap_or(1.0);
        let intercept = adj.as_ref().and_then(|a| a.intercept).unwrap_or(0.0);
        if !(slope.is_finite() && intercept.is_finite()) {
            return unsupported(format!(
                "{where_}: {what} with a non-finite LinearAdjustment"
            ));
        }
        match r {
            R::P(p) => {
                let name = p.parameter_ref.trim().to_string();
                let mut members = member_path(&name)?.1;
                let key = match self.param_ref(&name, path) {
                    Ok((k, m)) => {
                        members = m;
                        Some(qname(&k))
                    }
                    // An error unless it names an aggregate member.
                    Err(Error::Unsupported(_)) => None,
                    Err(e) => return Err(e),
                };
                Ok(DynSize {
                    key,
                    name,
                    members,
                    slope,
                    intercept,
                    instance,
                    ..DynSize::default()
                })
            }
            R::A(a) => {
                let name = a.argument_ref.trim().to_string();
                let (base, members) = member_path(&name)?;
                Ok(DynSize {
                    arg: Some(base),
                    name,
                    members,
                    slope,
                    intercept,
                    ..DynSize::default()
                })
            }
        }
    }

    /// Compiles a data encoding.
    ///
    /// # Arguments
    ///
    /// * `e`      - The XTCE encoding.
    /// * `path`   - The key of the SpaceSystem it is written in.
    /// * `where_` - What it belongs to, for error messages.
    ///
    /// # Returns
    ///
    /// The encoding and its calibrator.
    ///
    /// # Errors
    ///
    /// Unsupported, if the encoding uses something not supported.
    pub fn encoding(
        &mut self,
        e: EncRef<'_>,
        path: &Key,
        where_: &str,
    ) -> Result<(Encoding, Option<Calibrator>)> {
        let (mut enc, cal) = self.encoding_inner(e, path, where_)?;
        if e.bit_order() == Some(x::BitOrderType::LeastSignificantBitFirst) {
            // Each byte is a group of 8 bits read in that order.
            match &mut enc {
                Encoding::String(s) => s.lsb = true,
                Encoding::Binary(b) => b.lsb = true,
                _ => {}
            }
        }
        Ok((enc, cal))
    }

    /// Compiles a data encoding, without the bit order of strings and
    /// binary data.
    ///
    /// # Arguments
    ///
    /// * `e`      - The XTCE encoding.
    /// * `path`   - The key of the SpaceSystem it is written in.
    /// * `where_` - What it belongs to, for error messages.
    ///
    /// # Returns
    ///
    /// The encoding and its calibrator.
    ///
    /// # Errors
    ///
    /// Unsupported, if the encoding uses something not supported or its
    /// sizes are invalid.
    fn encoding_inner(
        &mut self,
        e: EncRef<'_>,
        path: &Key,
        where_: &str,
    ) -> Result<(Encoding, Option<Calibrator>)> {
        if e.has_edc() {
            // XTCE deprecates it here: it doesn't say where the value goes.
            return unsupported(format!(
                "{where_}: ErrorDetectCorrect in a data encoding (deprecated; \
                 give it in the container's BinaryEncoding)"
            ));
        }
        match e {
            EncRef::Int(ie) => {
                let bits = ie.size_in_bits.unwrap_or(8);
                let kind =
                    ie.encoding.map(|k| k.as_str()).unwrap_or("unsigned");
                if bits < 1 {
                    return unsupported(format!(
                        "{where_}: {bits}-bit integers"
                    ));
                }
                if kind == "BCD" && bits % 8 != 0 {
                    return unsupported(format!(
                        "{where_}: BCD in {bits} bits, not whole bytes"
                    ));
                }
                if kind == "packedBCD" && bits % 4 != 0 {
                    return unsupported(format!(
                        "{where_}: packedBCD in {bits} bits, not whole digits"
                    ));
                }
                if matches!(
                    kind,
                    "twosComplement" | "signMagnitude" | "onesComplement"
                ) && bits < 2
                {
                    return unsupported(format!(
                        "{where_}: 1-bit signed integer"
                    ));
                }
                let (lsb, order) = byte_order(e, bits, where_)?;
                let cal = self.calibrator(
                    ie.default_calibrator.as_ref(),
                    ie.context_calibrator_list.as_ref(),
                    path,
                    where_,
                )?;
                Ok((
                    Encoding::Int(IntEnc {
                        bits,
                        encoding: kind.into(),
                        order,
                        lsb,
                    }),
                    cal,
                ))
            }
            EncRef::Float(fe) => {
                let kind =
                    fe.encoding.map(|k| k.as_str()).unwrap_or("IEEE754_1985");
                let kind = if kind == "IEEE754_1985" {
                    "IEEE754"
                } else {
                    kind
                };
                let bits: i64 = fe
                    .size_in_bits
                    .map(|s| s.as_str().parse().expect("a size"))
                    .unwrap_or(32);
                let sizes: &[i64] = match kind {
                    "IEEE754" => &[16, 32, 64, 80, 128],
                    "MILSTD_1750A" => &[32, 48],
                    "DEC" => &[32, 64],
                    "IBM" => &[32, 64, 128],
                    _ => &[16, 32, 40],
                };
                if !sizes.contains(&bits) {
                    let has: Vec<String> =
                        sizes.iter().map(|s| s.to_string()).collect();
                    return unsupported(format!(
                        "{where_}: {kind} has no {bits}-bit format (it has {})",
                        has.join(", ")
                    ));
                }
                let (lsb, order) = byte_order(e, bits, where_)?;
                let cal = self.calibrator(
                    fe.default_calibrator.as_ref(),
                    fe.context_calibrator_list.as_ref(),
                    path,
                    where_,
                )?;
                Ok((
                    Encoding::Float(FloatEnc {
                        bits,
                        fmt: kind.into(),
                        order,
                        lsb,
                    }),
                    cal,
                ))
            }
            EncRef::Str(_) | EncRef::ArgStr(_) => Ok((
                Encoding::String(self.string_encoding(e, path, where_)?),
                None,
            )),
            EncRef::Bin(_) | EncRef::ArgBin(_) => {
                // Binary data is bytes, which have no byte order.
                let mut enc = self.binary_encoding(e, path, where_)?;
                enc.transform = transform_names(e);
                Ok((Encoding::Binary(enc), None))
            }
        }
    }

    /// Compiles a StringDataEncoding.
    ///
    /// # Arguments
    ///
    /// * `e`      - The XTCE encoding, a parameter's or an argument's.
    /// * `path`   - The key of the SpaceSystem it is written in.
    /// * `where_` - What it belongs to, for error messages.
    ///
    /// # Returns
    ///
    /// The StringEnc.
    ///
    /// # Errors
    ///
    /// Unsupported, if its sizes, size tag or terminator are invalid.
    fn string_encoding(
        &mut self,
        e: EncRef<'_>,
        path: &Key,
        where_: &str,
    ) -> Result<StringEnc> {
        let (enc, byte_order) = match e {
            EncRef::Str(s) => (s.encoding, s.byte_order.as_deref()),
            EncRef::ArgStr(s) => (s.encoding, s.byte_order.as_deref()),
            _ => unreachable!("a string encoding"),
        };
        let mut cs = enc.map(|c| c.as_str()).unwrap_or("UTF-8").to_string();
        // A string is bytes, so byte order matters only to the code units
        // of UTF-16 and UTF-32 without a byte order of their own.
        if byte_order.unwrap_or("").trim() == "leastSignificantByteFirst"
            && (cs == "UTF-16" || cs == "UTF-32")
        {
            cs.push_str("LE");
        }
        let unit = code_unit(&cs) as i64;
        let fixed = match e {
            EncRef::Str(s) => match &s.choice {
                SC::SizeInBits(f) => Some(f),
                _ => None,
            },
            EncRef::ArgStr(s) => match &s.choice {
                ASC::SizeInBits(f) => Some(f),
                _ => None,
            },
            _ => None,
        };
        let new =
            |fixed_bits, max_bits, terminator, size_tag_bits, dyn_| StringEnc {
                fixed_bits,
                max_bits,
                terminator,
                size_tag_bits,
                dyn_,
                charset: cs.clone(),
                lsb: false,
            };
        if let Some(f) = fixed {
            let bits = size(f.fixed.fixed_value, "string", where_)?;
            if bits % (8 * unit) != 0 {
                return unsupported(format!(
                    "{where_}: string size {bits} is not whole {cs} code units"
                ));
            }
            if let Some(ls) = &f.leading_size {
                let tag = ls
                    .size_in_bits_of_size_tag
                    .filter(|t| *t != 0)
                    .unwrap_or(16);
                return Ok(new(
                    Some(bits),
                    bits,
                    None,
                    Some(size_tag(tag, where_)?),
                    None,
                ));
            }
            let t = term(f.termination_char.as_deref(), &cs, where_)?;
            return Ok(new(Some(bits), bits, Some(t), None, None));
        }
        // A Variable size.
        let (max_size, dyn_choice, leading, term_char): (
            i64,
            Option<StrSize<'_>>,
            Option<&x::LeadingSizeType>,
            Option<&str>,
        ) = match e {
            EncRef::Str(s) => match &s.choice {
                SC::Variable(v) => (
                    v.max_size_in_bits,
                    v.choice1.as_ref().map(|c| match c {
                        V1::DynamicValue(d) => StrSize::Dyn(DynRef::P(d)),
                        V1::DiscreteLookupList(l) => {
                            StrSize::Lookup(LookupList::Param(Elem {
                                value: l.clone(),
                                class: "DiscreteLookupListType",
                            }))
                        }
                    }),
                    match &v.choice2 {
                        V2::LeadingSize(l) => Some(l),
                        _ => None,
                    },
                    match &v.choice2 {
                        V2::TerminationChar(t) => Some(t.as_str()),
                        _ => None,
                    },
                ),
                _ => unreachable!("a Variable size"),
            },
            EncRef::ArgStr(s) => match &s.choice {
                ASC::Variable(v) => (
                    v.max_size_in_bits,
                    v.choice1.as_ref().map(|c| match c {
                        AV1::DynamicValue(d) => StrSize::Dyn(DynRef::A(d)),
                        AV1::DiscreteLookupList(l) => {
                            StrSize::Lookup(LookupList::Arg(Elem {
                                value: l.clone(),
                                class: "ArgumentDiscreteLookupListType",
                            }))
                        }
                    }),
                    match &v.choice2 {
                        AV2::LeadingSize(l) => Some(l),
                        _ => None,
                    },
                    match &v.choice2 {
                        AV2::TerminationChar(t) => Some(t.as_str()),
                        _ => None,
                    },
                ),
                _ => unreachable!("a Variable size"),
            },
            _ => unreachable!("a string encoding"),
        };
        let mut dyn_ = match dyn_choice {
            None => None,
            Some(StrSize::Dyn(d)) => {
                let d = self.dyn_size(d, path, "string size", where_)?;
                if self.dialect.as_deref() == Some("yamcs")
                    && d.name == YAMCS_IGNORE
                    && d.table.is_none()
                {
                    // The schema needs a size; Yamcs ignores this one.
                    None
                } else {
                    Some(d)
                }
            }
            Some(StrSize::Lookup(l)) => Some(lookup_size(path, l)),
        };
        if let Some(d) = &mut dyn_ {
            d.unit = 8;
        }
        let mx = size(max_size, "string", where_)?;
        if let Some(ls) = leading {
            let tag = ls
                .size_in_bits_of_size_tag
                .filter(|t| *t != 0)
                .unwrap_or(16);
            return Ok(new(None, mx, None, Some(size_tag(tag, where_)?), dyn_));
        }
        let t = term(term_char, &cs, where_)?;
        Ok(new(None, mx, Some(t), None, dyn_))
    }

    /// Compiles the encoding of binary data with transform algorithms,
    /// which make bytes of it, or with `time`, a time in seconds.
    ///
    /// # Arguments
    ///
    /// * `e`      - The binary encoding, with the names of its algorithms.
    /// * `time`   - Whether the algorithms give a time in seconds.
    /// * `where_` - What it belongs to, for error messages.
    ///
    /// # Returns
    ///
    /// The XformEnc, with the hooks for its algorithms.
    ///
    /// # Errors
    ///
    /// Unsupported, if its size comes from another value or its bits are
    /// least significant first.
    pub fn xform(
        &mut self,
        e: &BinaryEnc,
        time: bool,
        where_: &str,
    ) -> Result<XformEnc> {
        if e.dyn_.is_some() {
            return unsupported(format!(
                "{where_}: transformed binary data whose size another value \
                 gives"
            ));
        }
        if e.lsb {
            return unsupported(format!(
                "{where_}: transformed binary data with bitOrder \
                 leastSignificantBitFirst"
            ));
        }
        let (fa, ta) = e.transform.clone().expect("transform algorithms");
        let k = if time { "time" } else { "binary" };
        let decode = fa.map(|n| self.hook(&format!("{k}_from"), &n, 0));
        let encode = ta.map(|n| self.hook(&format!("{k}_to"), &n, 0));
        Ok(XformEnc {
            raw: if e.rest { None } else { Some(e.bits / 8) },
            decode,
            encode,
            cap: self.max_field_bits / 8,
            time,
            sized: e.rest && self.dialect.as_deref() == Some("yamcs"),
        })
    }

    /// Compiles a BinaryDataEncoding.
    ///
    /// # Arguments
    ///
    /// * `e`      - The XTCE encoding.
    /// * `path`   - The key of the SpaceSystem it is written in.
    /// * `where_` - What it belongs to, for error messages.
    ///
    /// # Returns
    ///
    /// Its BinaryEnc: of a fixed size, of a size another value gives, or,
    /// for size -1, taking the rest of the packet.
    ///
    /// # Errors
    ///
    /// Unsupported, if its size is negative or not whole bytes.
    fn binary_encoding(
        &mut self,
        e: EncRef<'_>,
        path: &Key,
        where_: &str,
    ) -> Result<BinaryEnc> {
        let iv = match e {
            EncRef::Bin(b) => IntValueRef::P(&b.size_in_bits),
            EncRef::ArgBin(b) => IntValueRef::A(&b.size_in_bits),
            _ => unreachable!("a binary encoding"),
        };
        match self.int_value(iv, path, "binary size", where_)? {
            IntValue::Dyn(mut d) => {
                d.unit = 8;
                let mut b = BinaryEnc::new(self.max_field_bits);
                b.dyn_ = Some(d);
                Ok(b)
            }
            IntValue::Int(-1) => {
                // A Yamcs convention: only the end of the packet can bound
                // it when decoding.
                let mut b = BinaryEnc::new(self.max_field_bits);
                b.rest = true;
                Ok(b)
            }
            IntValue::Int(n) => {
                let bits = size(n, "binary", where_)?;
                if bits % 8 != 0 {
                    return unsupported(format!(
                        "{where_}: binary size {bits} is not whole bytes"
                    ));
                }
                Ok(BinaryEnc::new(bits))
            }
        }
    }

    /// Compiles the calibrator of an integer or float encoding: its
    /// default calibrator, and its context calibrators if it has any.
    ///
    /// # Arguments
    ///
    /// * `default` - The DefaultCalibrator.
    /// * `ccl`     - The ContextCalibratorList.
    /// * `path`    - The key of the SpaceSystem it is written in.
    /// * `where_`  - What it belongs to, for error messages.
    ///
    /// # Returns
    ///
    /// The default calibrator, a ContextCal if there are context
    /// calibrators, or `None` if there are neither.
    ///
    /// # Errors
    ///
    /// As [`Compiler::one_calibrator`]'s.
    fn calibrator(
        &mut self,
        default: Option<&x::CalibratorType>,
        ccl: Option<&x::ContextCalibratorListType>,
        path: &Key,
        where_: &str,
    ) -> Result<Option<Calibrator>> {
        let default = match default {
            Some(c) => Some(self.one_calibrator(c, path, where_)?),
            None => None,
        };
        let Some(ccl) = ccl.filter(|l| !l.context_calibrator.is_empty()) else {
            return Ok(default);
        };
        let mut cases = Vec::new();
        for cc in &ccl.context_calibrator {
            cases.push(CalCase::Unbound(
                path.clone(),
                Elem {
                    value: cc.context_match.clone(),
                    class: "ContextMatchType",
                },
                self.one_calibrator(&cc.calibrator, path, where_)?,
            ));
        }
        Ok(Some(Calibrator::Context(ContextCal {
            cases,
            default: default.map(Box::new),
            bound: false,
        })))
    }

    /// Compiles one polynomial, spline or math operation calibrator.
    ///
    /// # Arguments
    ///
    /// * `c`      - The XTCE calibrator.
    /// * `path`   - The key of the SpaceSystem it is written in.
    /// * `where_` - What it belongs to, for error messages.
    ///
    /// # Returns
    ///
    /// The Polynomial, Spline or MathOp.
    ///
    /// # Errors
    ///
    /// Unsupported, if a spline has fewer than two points or two at the
    /// same raw value, or a math operation is invalid.
    fn one_calibrator(
        &mut self,
        c: &x::CalibratorType,
        path: &Key,
        where_: &str,
    ) -> Result<Calibrator> {
        match &c.choice {
            x::CalibratorChoice::PolynomialCalibrator(p) => {
                Ok(Calibrator::Poly(Polynomial {
                    terms: p
                        .term
                        .iter()
                        .map(|t| (t.coefficient, t.exponent))
                        .collect(),
                }))
            }
            x::CalibratorChoice::SplineCalibrator(s) => {
                let order = s.order.unwrap_or(1);
                let mut pts: Vec<(f64, f64, i64)> = s
                    .spline_point
                    .iter()
                    .map(|p| (p.raw, p.calibrated, p.order.unwrap_or(order)))
                    .collect();
                pts.sort_by(|a, b| {
                    a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal)
                });
                if pts.len() < 2 {
                    return unsupported(format!(
                        "{where_}: spline with fewer than two points"
                    ));
                }
                if let Some(w) = pts.windows(2).find(|w| w[0].0 == w[1].0) {
                    return unsupported(format!(
                        "{where_}: spline with two points at raw value {}",
                        g(w[0].0)
                    ));
                }
                let orders: Vec<i64> = pts.iter().map(|p| p.2).collect();
                let head = &orders[..orders.len() - 1];
                let uniform = head.iter().all(|o| *o == head[0])
                    && (orders[0] == 0 || orders[0] == 1);
                Ok(Calibrator::Spline(Spline {
                    points: pts.iter().map(|p| (p.0, p.1)).collect(),
                    order: orders[0],
                    extrapolate: s.extrapolate.unwrap_or(false),
                    orders: if uniform { Vec::new() } else { orders },
                }))
            }
            x::CalibratorChoice::MathOperationCalibrator(m) => {
                Ok(Calibrator::Math(self.math_operation(
                    &calibrator_items(m),
                    path,
                    where_,
                )?))
            }
        }
    }

    /// Compiles a MathOperationCalibrator's postfix expression into a
    /// tree.
    ///
    /// # Arguments
    ///
    /// * `items`  - The items of the XTCE MathOperationCalibrator, or of a
    ///              MathAlgorithm's MathOperation.
    /// * `path`   - The key of the SpaceSystem it is written in.
    /// * `where_` - What it belongs to, for error messages.
    ///
    /// # Returns
    ///
    /// The MathOp.
    ///
    /// # Errors
    ///
    /// Unsupported, if an operand isn't a number, an operator has too few
    /// operands, or more or fewer than one value is left.
    pub fn math_operation(
        &mut self,
        items: &[MItem<'_>],
        path: &Key,
        where_: &str,
    ) -> Result<MathOp> {
        let mut stack: Vec<Expr> = Vec::new();
        let pop =
            |stack: &mut Vec<Expr>, n: usize, op: &str| -> Result<Vec<Expr>> {
                if stack.len() < n {
                    return unsupported(format!(
                    "{where_}: MathOperationCalibrator operator {} has too few \
                     operands",
                    repr(op)
                ));
                }
                Ok(stack.split_off(stack.len() - n))
            };
        for item in items {
            match *item {
                MItem::Value(v) => match crate::pyparse::float(v) {
                    Some(f) => stack.push(Expr::Const(f)),
                    None => {
                        return unsupported(format!(
                            "{where_}: ValueOperand {} is not a number",
                            repr(v)
                        ))
                    }
                },
                MItem::This => stack.push(Expr::Raw),
                MItem::Ref(v) => {
                    stack.push(Expr::Value(Shared::new(ValueRef {
                        ref_: Elem {
                            value: v.clone(),
                            class: "ParameterInstanceRefType",
                        },
                        path: path.clone(),
                        local: None,
                        member: Vec::new(),
                        vtype: None,
                    })))
                }
                MItem::Op(op) => match op {
                    "swap" => {
                        let mut ab = pop(&mut stack, 2, op)?;
                        let b = ab.pop().unwrap();
                        let a = ab.pop().unwrap();
                        stack.push(b);
                        stack.push(a);
                    }
                    "drop" => {
                        pop(&mut stack, 1, op)?;
                    }
                    "dup" => {
                        let a = pop(&mut stack, 1, op)?.pop().unwrap();
                        stack.push(a.clone());
                        stack.push(a);
                    }
                    "over" => {
                        let mut ab = pop(&mut stack, 2, op)?;
                        let b = ab.pop().unwrap();
                        let a = ab.pop().unwrap();
                        stack.push(a.clone());
                        stack.push(b);
                        stack.push(a);
                    }
                    _ if MATH_UNARY.contains(&op) => {
                        let args = pop(&mut stack, 1, op)?;
                        stack.push(Expr::Op(op.into(), args));
                    }
                    _ => {
                        let args = pop(&mut stack, 2, op)?;
                        stack.push(Expr::Op(op.into(), args));
                    }
                },
            }
        }
        if stack.len() != 1 {
            return unsupported(format!(
                "{where_}: MathOperationCalibrator leaves {} values on its \
                 stack, not one",
                stack.len()
            ));
        }
        Ok(MathOp {
            expr: stack.pop().unwrap(),
        })
    }

    /// Makes a type's size or count a bound DynSize, as [`bounded`] does,
    /// at most the maximum field size, and warns where the document gives
    /// binary data no maximum size of its own.
    ///
    /// # Arguments
    ///
    /// * `vt`     - The type: an array, string or binary data.
    /// * `b`      - The bound DynSize.
    /// * `rng`    - The range (lo, hi) of the value it adjusts.
    /// * `where_` - What it is in, for the warning.
    ///
    /// # Returns
    ///
    /// A copy of `vt` whose size or count is `b`.
    pub fn bound(
        &mut self,
        vt: &VType,
        b: DynSize,
        rng: &(BigInt, BigInt),
        where_: &str,
    ) -> VType {
        let cap = self.max_field_bits;
        if matches!(vt.enc, Encoding::Binary(_))
            && dyn_units(&b, rng, cap) >= cap / 8
        {
            let given = if b.table.is_none() {
                format!("the value of {}", b.name)
            } else {
                "a DiscreteLookupList".into()
            };
            self.warn(format!(
                "{where_}: {} is binary data with no maximum size: its size, \
                 {given}, allows more than the maximum field size, {} bytes, \
                 which the generated code's buffer is instead; give the size a \
                 ValidRange",
                vt.name,
                cap / 8
            ));
        }
        bounded(vt, b, rng, cap)
    }
}

/// Where a Variable string's size comes from.
enum StrSize<'b> {
    /// A DynamicValue.
    Dyn(DynRef<'b>),
    /// A DiscreteLookupList.
    Lookup(LookupList),
}

/// Makes the unbound DynSize of a DiscreteLookupList.
///
/// # Arguments
///
/// * `path` - The SpaceSystem it is in.
/// * `l`    - The list.
///
/// # Returns
///
/// The DynSize, holding the list to bind.
fn lookup_size(path: &Key, l: LookupList) -> DynSize {
    DynSize {
        table: Some((path.clone(), l)),
        ..DynSize::default()
    }
}

/// Reads the bit order and byte order of an encoding. A byte order list
/// must give the significance of each of a whole number of bytes once; a
/// list that amounts to big or little endian is that.
///
/// # Arguments
///
/// * `e`      - The XTCE data encoding.
/// * `bits`   - The size of the value in bits.
/// * `where_` - What it belongs to, for error messages.
///
/// # Returns
///
/// Whether the least significant bit is first, and the byte order.
///
/// # Errors
///
/// Unsupported, if a byte order list doesn't give each byte once.
pub fn byte_order(
    e: EncRef<'_>,
    bits: i64,
    where_: &str,
) -> Result<(bool, Order)> {
    let lsb = e.bit_order() == Some(x::BitOrderType::LeastSignificantBitFirst);
    let bo = e.byte_order().unwrap_or("mostSignificantByteFirst").trim();
    if bo == "mostSignificantByteFirst" {
        return Ok((lsb, Order::Be));
    }
    if bo == "leastSignificantByteFirst" {
        return Ok((lsb, Order::Le));
    }
    let refuse = || {
        unsupported(format!(
            "{where_}: byteOrder {} does not give each of the {} bytes of the \
             value once",
            repr(bo),
            g(bits as f64 / 8.0)
        ))
    };
    let sig: Option<Vec<i64>> =
        bo.split(',').map(crate::pyparse::int).collect();
    let Some(sig) = sig else {
        return refuse();
    };
    let n = bits / 8;
    let mut sorted = sig.clone();
    sorted.sort();
    if bits % 8 != 0 || sorted != (0..n).collect::<Vec<_>>() {
        return refuse();
    }
    if sig == (0..n).rev().collect::<Vec<_>>() {
        return Ok((lsb, Order::Be));
    }
    if sig == (0..n).collect::<Vec<_>>() {
        return Ok((lsb, Order::Le));
    }
    Ok((lsb, Order::List(sig)))
}

/// Reads the names of the algorithms that transform the bits of a binary
/// encoding.
///
/// # Arguments
///
/// * `e` - A BinaryDataEncoding.
///
/// # Returns
///
/// The names of the from-binary and to-binary algorithms, either `None`
/// if missing, or `None` if it has neither.
pub fn transform_names(
    e: EncRef<'_>,
) -> Option<(Option<String>, Option<String>)> {
    let (fa, ta) = match e {
        EncRef::Bin(b) => (
            b.from_binary_transform_algorithm
                .as_ref()
                .map(|a| a.name.clone()),
            b.to_binary_transform_algorithm
                .as_ref()
                .map(|a| a.name.clone()),
        ),
        EncRef::ArgBin(b) => (
            b.from_binary_transform_algorithm
                .as_ref()
                .map(|a| a.name.clone()),
            b.to_binary_transform_algorithm
                .as_ref()
                .map(|a| a.name.clone()),
        ),
        _ => (None, None),
    };
    if fa.is_none() && ta.is_none() {
        None
    } else {
        Some((fa, ta))
    }
}

/// Checks a size in bits from the document; the schema allows negative
/// ones.
///
/// # Arguments
///
/// * `bits`   - The size.
/// * `what`   - What it is the size of.
/// * `where_` - What it belongs to, for error messages.
///
/// # Returns
///
/// The size.
///
/// # Errors
///
/// Unsupported, if it is negative.
fn size(bits: i64, what: &str, where_: &str) -> Result<i64> {
    if bits < 0 {
        return unsupported(format!(
            "{where_}: {what} size {bits} is negative"
        ));
    }
    Ok(bits)
}

/// Checks the size of a string's size tag.
///
/// # Arguments
///
/// * `bits`   - The size.
/// * `where_` - What it belongs to, for error messages.
///
/// # Returns
///
/// The size.
///
/// # Errors
///
/// Unsupported, unless it is 1 to 32 bits.
fn size_tag(bits: i64, where_: &str) -> Result<i64> {
    if !(1..=32).contains(&bits) {
        return unsupported(format!("{where_}: {bits}-bit string size tag"));
    }
    Ok(bits)
}

/// Compiles the bytes that end a string in a charset: a whole number of
/// its code units, and a zero byte stands for a zero code unit.
///
/// # Arguments
///
/// * `hexchar` - The TerminationChar in hexadecimal, or `None` for the default,
///               zero.
/// * `cs`      - The string's character set.
/// * `where_`  - What the string belongs to, for error messages.
///
/// # Returns
///
/// The terminating bytes.
///
/// # Errors
///
/// Unsupported, unless they are 1 to 16 bytes of whole code units.
fn term(hexchar: Option<&str>, cs: &str, where_: &str) -> Result<Vec<u8>> {
    let h = hexchar.unwrap_or("").trim();
    let h = if h.is_empty() { "00" } else { h };
    let mut b = hex_bytes(h, where_)?;
    let unit = code_unit(cs);
    if b.len() < unit && b.iter().all(|x| *x == 0) {
        b = vec![0; unit];
    }
    if b.is_empty() || b.len() % unit != 0 || b.len() > 16 {
        return unsupported(format!(
            "{where_}: termination character {h} is not 1 to {} {cs} code \
             units",
            16 / unit
        ));
    }
    Ok(b)
}

/// Describes what a time is reckoned from, for documentation.
///
/// # Arguments
///
/// * `ref_`   - The XTCE ReferenceTime, or `None`.
/// * `where_` - What the time type is, for error messages.
///
/// # Returns
///
/// The description, such as `since epoch TAI`.
///
/// # Errors
///
/// Unsupported, if the epoch is not a date, a date and time, or TAI,
/// J2000, UNIX or GPS.
fn reference_time(
    ref_: Option<&x::ReferenceTimeType>,
    where_: &str,
) -> Result<String> {
    let Some(r) = ref_ else {
        return Ok("from an unstated time".into());
    };
    match &r.choice {
        x::ReferenceTimeChoice::OffsetFrom(p) => {
            Ok(format!("after {}", p.parameter_ref))
        }
        x::ReferenceTimeChoice::Epoch(e) => {
            let epoch = e.trim();
            if !matches!(epoch, "TAI" | "J2000" | "UNIX" | "GPS")
                && !is_date(epoch)
            {
                return unsupported(format!(
                    "{where_}: epoch {} is not a date, a date and time, or \
                     TAI, J2000, UNIX or GPS",
                    repr(epoch)
                ));
            }
            Ok(format!("since epoch {epoch}"))
        }
    }
}

/// Says whether an epoch is a date, or a date and time, as the Python's
/// `-?\d{4,}-\d\d-\d\d(T\d\d:\d\d:\d\d(\.\d+)?)?(Z|[+-]\d\d:\d\d)?` says.
///
/// # Arguments
///
/// * `s` - The epoch.
///
/// # Returns
///
/// Whether it is.
fn is_date(s: &str) -> bool {
    let b = s.as_bytes();
    let mut i = 0;
    let digits = |i: &mut usize, n: usize| -> bool {
        let start = *i;
        while *i < b.len() && b[*i].is_ascii_digit() && *i - start < n {
            *i += 1;
        }
        *i - start == n
    };
    if b.first() == Some(&b'-') {
        i += 1;
    }
    let y0 = i;
    while i < b.len() && b[i].is_ascii_digit() {
        i += 1;
    }
    if i - y0 < 4 {
        return false;
    }
    let lit = |i: &mut usize, c: u8| -> bool {
        if b.get(*i) == Some(&c) {
            *i += 1;
            true
        } else {
            false
        }
    };
    if !(lit(&mut i, b'-')
        && digits(&mut i, 2)
        && lit(&mut i, b'-')
        && digits(&mut i, 2))
    {
        return false;
    }
    if b.get(i) == Some(&b'T') {
        i += 1;
        if !(digits(&mut i, 2)
            && lit(&mut i, b':')
            && digits(&mut i, 2)
            && lit(&mut i, b':')
            && digits(&mut i, 2))
        {
            return false;
        }
        if b.get(i) == Some(&b'.') {
            i += 1;
            let f0 = i;
            while i < b.len() && b[i].is_ascii_digit() {
                i += 1;
            }
            if i == f0 {
                return false;
            }
        }
    }
    match b.get(i) {
        None => true,
        Some(b'Z') => i + 1 == b.len(),
        Some(b'+') | Some(b'-') => {
            i += 1;
            digits(&mut i, 2)
                && lit(&mut i, b':')
                && digits(&mut i, 2)
                && i == b.len()
        }
        _ => false,
    }
}

/// Checks that an enumeration on an integer encoding of more than 64 bits
/// holds its labels' values in the type a wide integer of that encoding
/// has: for an unsigned encoding, that none is negative.
///
/// # Arguments
///
/// * `vt`     - The enumeration's compiled type, with its labels.
/// * `where_` - What it is, for error messages.
///
/// # Errors
///
/// Unsupported, if a label's value is negative and the encoding unsigned.
fn check_enum_values(vt: &VType, where_: &str) -> Result<()> {
    if !vt.wide() || vt.signed() {
        return Ok(());
    }
    for lab in &vt.labels {
        if lab.value < 0 {
            return unsupported(format!(
                "{where_}: label {} has value {}, which its unsigned encoding \
                 can't hold",
                repr(&lab.label),
                lab.value
            ));
        }
    }
    Ok(())
}

/// Checks a boolean encoded as a string: its two strings must differ and
/// fit the encoding.
///
/// # Arguments
///
/// * `vt`     - The boolean's compiled type.
/// * `where_` - What it is, for error messages.
///
/// # Errors
///
/// Unsupported, if the strings are the same, aren't in the character set,
/// contain the termination character or don't fit.
fn check_bool_strings(vt: &VType, where_: &str) -> Result<()> {
    if vt.one_string == vt.zero_string {
        return unsupported(format!(
            "{where_}: oneStringValue and zeroStringValue are both {}",
            repr(&vt.one_string)
        ));
    }
    let Encoding::String(e) = &vt.enc else {
        unreachable!("a boolean encoded as a string");
    };
    for s in [&vt.one_string, &vt.zero_string] {
        let Some(b) = encode_text(s, &e.charset) else {
            return unsupported(format!(
                "{where_}: {} is not {}",
                repr(s),
                e.charset
            ));
        };
        if let Some(t) = &e.terminator {
            if find_unit(&b, t, code_unit(&e.charset)).is_some() {
                return unsupported(format!(
                    "{where_}: {} contains the termination character",
                    repr(s)
                ));
            }
        }
        if e.dyn_.is_none() && b.len() as i64 > wire_cap(vt) {
            return unsupported(format!(
                "{where_}: {} does not fit its encoding",
                repr(s)
            ));
        }
    }
    Ok(())
}

/// One item of a MathOperation's postfix expression, of a calibrator or
/// of a MathAlgorithm.
#[derive(Debug, Clone, Copy)]
pub enum MItem<'b> {
    /// A ValueOperand.
    Value(&'b str),
    /// A ThisParameterOperand.
    This,
    /// An Operator.
    Op(&'static str),
    /// A ParameterInstanceRefOperand.
    Ref(&'b x::ParameterInstanceRefType),
}

/// Lists the items of a MathOperationCalibrator.
///
/// # Arguments
///
/// * `mc` - The calibrator.
///
/// # Returns
///
/// Its items, in order.
pub fn calibrator_items(mc: &x::MathOperationCalibratorType) -> Vec<MItem<'_>> {
    mc.items
        .iter()
        .map(|i| match i {
            M::ValueOperand(v) => MItem::Value(v),
            M::ThisParameterOperand(_) => MItem::This,
            M::Operator(o) => MItem::Op(o.as_str()),
            M::ParameterInstanceRefOperand(r) => MItem::Ref(r),
        })
        .collect()
}

/// Lists the items of a MathAlgorithm's MathOperation.
///
/// # Arguments
///
/// * `mo` - The MathOperation.
///
/// # Returns
///
/// Its items, in order.
pub fn triggered_items(mo: &x::TriggeredMathOperationType) -> Vec<MItem<'_>> {
    use x::TriggeredMathOperationChoice as T;
    mo.items
        .iter()
        .map(|i| match i {
            T::ValueOperand(v) => MItem::Value(v),
            T::ThisParameterOperand(_) => MItem::This,
            T::Operator(o) => MItem::Op(o.as_str()),
            T::ParameterInstanceRefOperand(r) => MItem::Ref(r),
        })
        .collect()
}
