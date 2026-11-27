//! The language-neutral model of a mission's packets, as the Python
//! compiler's `model.py` has it. Everything here is resolved: references
//! point at what they name, defaults are filled in and sizes are in bits.
//!
//! Each record has the fields of its Python class, in the same order, so
//! that both write the same canonical text (see [`crate::dump`]). Records
//! that the Python model compares by identity are [`Shared`]; the rest are
//! values, copied where the Python copies them.

use std::rc::Rc;

use num_bigint::BigInt;
use tcs_xtce::generated as x;

use crate::dump::{Cx, Elem, Shared, ToTree, Tree};
use crate::{shared_record, value_record};

/// The path of SpaceSystem names to a definition, followed by its name.
pub type Key = Vec<String>;

/// An integer of any size, as Python's are.
pub type Int = BigInt;

/// An error: a construct the compiler does not support, or a loop in what
/// it follows.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Error {
    /// An XTCE construct the compiler does not handle, or a document that
    /// is wrong in a way it reports as one; `--skip-unsupported` skips the
    /// containers and meta-commands that have one.
    Unsupported(String),
    /// A loop in something the compiler follows: a chain of base types,
    /// containers or meta-commands that inherits from itself, or streams
    /// that feed one another. Always fatal.
    Cycle(String),
}

impl Error {
    /// Gives what the error says.
    ///
    /// # Returns
    ///
    /// Its message.
    pub fn message(&self) -> &str {
        match self {
            Error::Unsupported(m) | Error::Cycle(m) => m,
        }
    }

    /// Gives the name of the Python exception the error is.
    ///
    /// # Returns
    ///
    /// `Unsupported` or `Cycle`.
    pub fn class(&self) -> &'static str {
        match self {
            Error::Unsupported(_) => "Unsupported",
            Error::Cycle(_) => "Cycle",
        }
    }
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.message())
    }
}

impl std::error::Error for Error {}

/// What the compiler's functions return.
pub type Result<T> = std::result::Result<T, Error>;

/// Makes an [`Error::Unsupported`].
///
/// # Arguments
///
/// * `msg` - What it says.
///
/// # Returns
///
/// The error, to return.
pub fn unsupported<T>(msg: impl Into<String>) -> Result<T> {
    Err(Error::Unsupported(msg.into()))
}

/// A number that Python holds as either an int or a float, which the text
/// writes differently (`5` and `5.0`).
#[derive(Debug, Clone, PartialEq)]
pub enum Num {
    /// An int.
    Int(Int),
    /// A float.
    Float(f64),
}

impl Num {
    /// Gives the number as a float.
    ///
    /// # Returns
    ///
    /// It, rounded to the nearest float if it is an int too large.
    pub fn as_f64(&self) -> f64 {
        match self {
            Num::Int(i) => {
                num_traits::ToPrimitive::to_f64(i).unwrap_or(f64::INFINITY)
            }
            Num::Float(x) => *x,
        }
    }
}

impl ToTree for Num {
    fn tree(&self, cx: &mut Cx) -> Tree {
        match self {
            Num::Int(i) => i.tree(cx),
            Num::Float(x) => x.tree(cx),
        }
    }
}

// --- encodings ----------------------------------------------------------

/// How the groups of 8 bits of a field combine.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Order {
    /// The first group is the most significant: `"be"`.
    Be,
    /// The first group is the least significant: `"le"`.
    Le,
    /// Each whole byte's significance, from an XTCE byte order list.
    List(Vec<i64>),
}

impl ToTree for Order {
    fn tree(&self, cx: &mut Cx) -> Tree {
        match self {
            Order::Be => Tree::Str("be".into()),
            Order::Le => Tree::Str("le".into()),
            Order::List(v) => v.tree(cx),
        }
    }
}

/// An integer on the wire.
#[derive(Debug, Clone, PartialEq)]
pub struct IntEnc {
    /// Its size in bits.
    pub bits: i64,
    /// `unsigned`, `twosComplement`, `signMagnitude`, `onesComplement`,
    /// `BCD` or `packedBCD`.
    pub encoding: String,
    /// Its byte order.
    pub order: Order,
    /// Whether its least significant bit is first.
    pub lsb: bool,
}
value_record!(IntEnc, "IntEnc", [bits, encoding, order, lsb]);

impl IntEnc {
    /// Says whether the encoding is of signed values.
    ///
    /// # Returns
    ///
    /// True for two's complement, sign-magnitude and ones' complement.
    pub fn signed(&self) -> bool {
        matches!(
            self.encoding.as_str(),
            "twosComplement" | "signMagnitude" | "onesComplement"
        )
    }

    /// Says whether the encoding is of decimal digits.
    ///
    /// # Returns
    ///
    /// True for BCD and packed BCD.
    pub fn bcd(&self) -> bool {
        matches!(self.encoding.as_str(), "BCD" | "packedBCD")
    }
}

/// A float on the wire.
#[derive(Debug, Clone, PartialEq)]
pub struct FloatEnc {
    /// Its size in bits, one its format allows.
    pub bits: i64,
    /// `IEEE754`, `MILSTD_1750A`, `DEC`, `IBM` or `TI`.
    pub fmt: String,
    /// Its byte order.
    pub order: Order,
    /// Whether its least significant bit is first.
    pub lsb: bool,
}
value_record!(FloatEnc, "FloatEnc", [bits, fmt, order, lsb]);

/// One step of a reference into a value, as written: a member name, or an
/// index.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Step {
    /// `.member`.
    Name(String),
    /// `[index]`.
    Index(i64),
}

impl ToTree for Step {
    fn tree(&self, cx: &mut Cx) -> Tree {
        match self {
            Step::Name(s) => s.tree(cx),
            Step::Index(i) => i.tree(cx),
        }
    }
}

/// A step into element `i` of a one-dimensional array of `count`
/// elements, in a reference such as `P[2]`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Index {
    /// The element, counted from 0.
    pub i: i64,
    /// The elements, or `None` when another value gives them, so that the
    /// element may be missing.
    pub count: Option<i64>,
    /// For an array whose count varies in a dimension after the first:
    /// the element is at `i + stride * d`, `d` that dimension's size.
    pub stride: i64,
    /// What the length is divided by to give `d`.
    pub per: i64,
    /// The least `d` for which the element is there.
    pub need: i64,
}
value_record!(Index, "Index", [i, count, stride, per, need]);

/// A step of a resolved path into a value: a field name, or an [`Index`].
#[derive(Debug, Clone, PartialEq)]
pub enum PathStep {
    /// A member's field name.
    Field(String),
    /// An element.
    Index(Index),
}

impl ToTree for PathStep {
    fn tree(&self, cx: &mut Cx) -> Tree {
        match self {
            PathStep::Field(s) => s.tree(cx),
            PathStep::Index(i) => i.tree(cx),
        }
    }
}

/// A DiscreteLookupList of the document, which a packet binds.
#[derive(Debug, Clone)]
pub enum LookupList {
    /// A parameter's.
    Param(Elem<x::DiscreteLookupListType>),
    /// An argument's.
    Arg(Elem<x::ArgumentDiscreteLookupListType>),
}

impl ToTree for LookupList {
    fn tree(&self, cx: &mut Cx) -> Tree {
        match self {
            LookupList::Param(e) => e.tree(cx),
            LookupList::Arg(e) => e.tree(cx),
        }
    }
}

/// A size or count that another value of the packet gives (an XTCE
/// `DynamicValue`): `factor * (slope * value + intercept)`.
#[derive(Debug, Clone)]
pub struct DynSize {
    /// The qualified name of the parameter whose value it uses, unbound.
    pub key: Option<String>,
    /// The argument whose value it uses, unbound.
    pub arg: Option<String>,
    /// The reference as written.
    pub name: String,
    /// The field name of the earlier member of an aggregate it uses.
    pub member: Option<String>,
    /// Unbound, a DiscreteLookupList that gives the value: the SpaceSystem
    /// it is in, and it.
    pub table: Option<(Key, LookupList)>,
    /// Bound, the lookup's (criteria, value) pairs, the first match
    /// winning.
    pub lookup: Option<Vec<(Vec<Term>, i64)>>,
    /// The lookup's value when none matches.
    pub default: i64,
    /// The member and index steps into the value, as written.
    pub members: Vec<Step>,
    /// The steps, bound to field names and indexes.
    pub path: Vec<PathStep>,
    /// The LinearAdjustment's slope.
    pub slope: f64,
    /// The LinearAdjustment's intercept.
    pub intercept: f64,
    /// The size of the other dimensions of an array, multiplied in.
    pub factor: i64,
    /// 8 when the value is a size in bits, used in whole bytes; else 1.
    pub unit: i64,
    /// Which instance of the value: 0 the last decoded, -1 the one
    /// before.
    pub instance: i64,
    /// Bound, the value it uses.
    pub local: Option<Shared<Local>>,
    /// Bound, the largest value of `(slope * value + intercept) / unit`
    /// that the packet can hold.
    pub max: i64,
    /// Bound to an integer of more than 64 bits: its type.
    pub wide_type: Option<Rc<VType>>,
}
value_record!(
    DynSize,
    "DynSize",
    [
        key, arg, name, member, table, lookup, default, members, path, slope,
        intercept, factor, unit, instance, local, max, wide_type
    ]
);

impl Default for DynSize {
    fn default() -> Self {
        DynSize {
            key: None,
            arg: None,
            name: String::new(),
            member: None,
            table: None,
            lookup: None,
            default: 0,
            members: Vec::new(),
            path: Vec::new(),
            slope: 1.0,
            intercept: 0.0,
            factor: 1,
            unit: 1,
            instance: 0,
            local: None,
            max: 0,
            wide_type: None,
        }
    }
}

impl DynSize {
    /// Gives the most the size or count can be.
    ///
    /// # Returns
    ///
    /// `factor * max`.
    pub fn max_total(&self) -> i64 {
        self.factor * self.max
    }
}

/// A byte string on the wire.
#[derive(Debug, Clone)]
pub struct StringEnc {
    /// The bits it always occupies, for a fixed size.
    pub fixed_bits: Option<i64>,
    /// The most bits the whole field takes.
    pub max_bits: i64,
    /// The bytes that end the content, if any.
    pub terminator: Option<Vec<u8>>,
    /// The bits of the size tag before the content, if it has one.
    pub size_tag_bits: Option<i64>,
    /// The bits another value gives it, at most `max_bits`.
    pub dyn_: Option<DynSize>,
    /// Its character set.
    pub charset: String,
    /// Whether each byte's least significant bit is first.
    pub lsb: bool,
}
value_record!(
    StringEnc,
    "StringEnc",
    [
        fixed_bits,
        max_bits,
        terminator,
        size_tag_bits,
        dyn_ as "dyn",
        charset,
        lsb
    ]
);

/// Bytes on the wire.
#[derive(Debug, Clone)]
pub struct BinaryEnc {
    /// Its size in bits, or the most it may be.
    pub bits: i64,
    /// The bits another value gives it, at most `bits`.
    pub dyn_: Option<DynSize>,
    /// Whether it is the rest of the packet but its last `tail` bits.
    pub rest: bool,
    /// The bits of the entries of a fixed size that follow it.
    pub tail: i64,
    /// Whether each byte's least significant bit is first.
    pub lsb: bool,
    /// Whether it is a complete CCSDS packet, whose packet length field
    /// gives its size (Yamcs:EmbeddedTc).
    pub embedded: bool,
    /// The names of its from-binary and to-binary transform algorithms.
    pub transform: Option<(Option<String>, Option<String>)>,
}
value_record!(
    BinaryEnc,
    "BinaryEnc",
    [bits, dyn_ as "dyn", rest, tail, lsb, embedded, transform]
);

impl BinaryEnc {
    /// Makes a binary encoding of a fixed size, or the most it may be.
    ///
    /// # Arguments
    ///
    /// * `bits` - The size in bits.
    ///
    /// # Returns
    ///
    /// The encoding, with nothing else set.
    pub fn new(bits: i64) -> Self {
        BinaryEnc {
            bits,
            dyn_: None,
            rest: false,
            tail: 0,
            lsb: false,
            embedded: false,
            transform: None,
        }
    }
}

/// A count or gap: a number, or a size another value gives.
#[derive(Debug, Clone)]
pub enum Count {
    /// A number.
    Int(i64),
    /// Another value gives it.
    Dyn(DynSize),
}

impl ToTree for Count {
    fn tree(&self, cx: &mut Cx) -> Tree {
        match self {
            Count::Int(i) => i.tree(cx),
            Count::Dyn(d) => d.tree(cx),
        }
    }
}

impl Count {
    /// Gives the most it can be.
    ///
    /// # Returns
    ///
    /// The number, or the DynSize's `max_total`.
    pub fn max_total(&self) -> i64 {
        match self {
            Count::Int(i) => *i,
            Count::Dyn(d) => d.max_total(),
        }
    }
}

/// `count` values of type `elem` one after another, with `gap` bits
/// between them.
#[derive(Debug, Clone)]
pub struct ArrayEnc {
    /// The elements' type.
    pub elem: Rc<VType>,
    /// How many there are.
    pub count: Count,
    /// The bits between each two.
    pub gap: Count,
    /// The dimensions of an array type, flattened into `count`.
    pub dims: i64,
    /// Each dimension's first index.
    pub starts: Vec<i64>,
    /// Each dimension's size when every size is fixed; empty otherwise.
    pub shape: Vec<i64>,
    /// Each dimension's size, `None` for the one another value gives.
    pub sizes: Vec<Option<i64>>,
}
value_record!(
    ArrayEnc,
    "ArrayEnc",
    [elem, count, gap, dims, starts, shape, sizes]
);

/// One member of an aggregate.
#[derive(Debug, Clone)]
pub struct Member {
    /// Its XTCE name.
    pub name: String,
    /// Its field name in generated code.
    pub ident: String,
    /// Its type.
    pub vtype: Rc<VType>,
    /// The bits to skip before it.
    pub skip: i64,
}
value_record!(Member, "Member", [name, ident, vtype, skip]);

/// The members of an aggregate, encoded one after another in order, then
/// `pad` bits.
#[derive(Debug, Clone)]
pub struct AggregateEnc {
    /// The members.
    pub members: Vec<Member>,
    /// The bits after them.
    pub pad: i64,
    /// Requires on the aggregate's own members, on `this`.
    pub checks: Vec<Term>,
    /// The aggregate value itself, which the checks are on.
    pub this: Option<Shared<Local>>,
    /// A repeated container's transform algorithms.
    pub transform: Option<XformEnc>,
}
value_record!(
    AggregateEnc,
    "AggregateEnc",
    [members, pad, checks, this, transform]
);

/// A value that algorithms the application supplies make from its bits on
/// the wire and back.
#[derive(Debug, Clone)]
pub struct XformEnc {
    /// The bytes on the wire, or `None` for what the decoding algorithm
    /// takes of the rest of the packet.
    pub raw: Option<i64>,
    /// The decoding algorithm.
    pub decode: Option<Shared<Hook>>,
    /// The encoding algorithm.
    pub encode: Option<Shared<Hook>>,
    /// The most bytes of the value.
    pub cap: i64,
    /// Whether the value is a time in seconds rather than bytes.
    pub time: bool,
    /// Whether entries may follow a value whose size the decoding
    /// algorithm says (the Yamcs reading of a size of -1).
    pub sized: bool,
}
value_record!(
    XformEnc,
    "XformEnc",
    [raw, decode, encode, cap, time, sized]
);

impl XformEnc {
    /// Gives the most bits it takes on the wire.
    ///
    /// # Returns
    ///
    /// 8 times `raw`, or `cap` when `raw` is not given.
    pub fn bits(&self) -> i64 {
        8 * self.raw.unwrap_or(self.cap)
    }
}

/// How a value is encoded.
#[derive(Debug, Clone)]
pub enum Encoding {
    /// An integer.
    Int(IntEnc),
    /// A float.
    Float(FloatEnc),
    /// A string.
    String(StringEnc),
    /// Bytes.
    Binary(BinaryEnc),
    /// An array.
    Array(ArrayEnc),
    /// An aggregate.
    Aggregate(AggregateEnc),
    /// Transformed bytes or time.
    Xform(XformEnc),
}

impl ToTree for Encoding {
    fn tree(&self, cx: &mut Cx) -> Tree {
        match self {
            Encoding::Int(e) => e.tree(cx),
            Encoding::Float(e) => e.tree(cx),
            Encoding::String(e) => e.tree(cx),
            Encoding::Binary(e) => e.tree(cx),
            Encoding::Array(e) => e.tree(cx),
            Encoding::Aggregate(e) => e.tree(cx),
            Encoding::Xform(e) => e.tree(cx),
        }
    }
}

impl Encoding {
    /// Gives the bits of the encoding, as the Python's `enc.bits` does.
    ///
    /// # Returns
    ///
    /// Its bits.
    ///
    /// # Panics
    ///
    /// For an encoding with no `bits` (strings, arrays and aggregates),
    /// where the Python would fail too.
    pub fn bits(&self) -> i64 {
        match self {
            Encoding::Int(e) => e.bits,
            Encoding::Float(e) => e.bits,
            Encoding::Binary(e) => e.bits,
            Encoding::Xform(e) => e.bits(),
            _ => panic!("the encoding has no bits"),
        }
    }

    /// Gives the size another value gives the encoding, if it has one.
    ///
    /// # Returns
    ///
    /// A string's or binary data's DynSize.
    pub fn dyn_size(&self) -> Option<&DynSize> {
        match self {
            Encoding::String(e) => e.dyn_.as_ref(),
            Encoding::Binary(e) => e.dyn_.as_ref(),
            _ => None,
        }
    }
}

/// The most bits a field whose size or count another value gives may
/// take on the wire, unless the mission sets its own.
pub const MAX_FIELD_BITS: i64 = 65536 * 8;
/// The largest `tcs-xtce.max-field-bytes`.
pub const MAX_FIELD_BYTES_LIMIT: i64 = (1 << 32) - 1;

// --- calibrators --------------------------------------------------------

/// A polynomial calibrator.
#[derive(Debug, Clone)]
pub struct Polynomial {
    /// Its terms, each (coefficient, exponent).
    pub terms: Vec<(f64, i64)>,
}
value_record!(Polynomial, "Polynomial", [terms]);

impl Polynomial {
    /// Gives the coefficient of the linear term.
    ///
    /// # Returns
    ///
    /// The sum of the coefficients of exponent 1.
    pub fn slope(&self) -> f64 {
        self.terms
            .iter()
            .filter(|t| t.1 == 1)
            .fold(0.0, |a, t| a + t.0)
    }

    /// Gives the constant term.
    ///
    /// # Returns
    ///
    /// The sum of the coefficients of exponent 0.
    pub fn intercept(&self) -> f64 {
        self.terms
            .iter()
            .filter(|t| t.1 == 0)
            .fold(0.0, |a, t| a + t.0)
    }

    /// Says whether the polynomial can be inverted for encoding.
    ///
    /// # Returns
    ///
    /// True when it is linear with a slope that is not zero.
    pub fn invertible(&self) -> bool {
        self.terms.iter().all(|t| t.1 <= 1) && self.slope() != 0.0
    }
}

/// A spline calibrator.
#[derive(Debug, Clone)]
pub struct Spline {
    /// Its points, each (raw, calibrated), sorted by raw.
    pub points: Vec<(f64, f64)>,
    /// The order of every segment, when `orders` is empty.
    pub order: i64,
    /// Whether it extrapolates beyond its points.
    pub extrapolate: bool,
    /// The order of the segment from each point, when they differ.
    pub orders: Vec<i64>,
}
value_record!(Spline, "Spline", [points, order, extrapolate, orders]);

/// Another value of the packet that a calibrator uses (a
/// ParameterInstanceRefOperand).
#[derive(Debug)]
pub struct ValueRef {
    /// Unbound, the XTCE reference.
    pub ref_: Elem<x::ParameterInstanceRefType>,
    /// The SpaceSystem it is in.
    pub path: Key,
    /// Bound, the value.
    pub local: Option<Shared<Local>>,
    /// Bound, the path into it.
    pub member: Vec<PathStep>,
    /// Bound, the type there.
    pub vtype: Option<Rc<VType>>,
}
shared_record!(
    ValueRef,
    "ValueRef",
    [ref_ as "ref", path, local, member, vtype]
);

/// A node of a MathOperationCalibrator's expression tree, written as the
/// Python's tuples are.
#[derive(Debug, Clone)]
pub enum Expr {
    /// `("const", x)`.
    Const(f64),
    /// `("raw",)`: the value being calibrated.
    Raw,
    /// `("value", ValueRef)`: another value of the packet.
    Value(Shared<ValueRef>),
    /// `(operator, operand, ...)`.
    Op(String, Vec<Expr>),
}

impl ToTree for Expr {
    fn tree(&self, cx: &mut Cx) -> Tree {
        match self {
            Expr::Const(x) => {
                Tree::List(vec![Tree::Str("const".into()), x.tree(cx)])
            }
            Expr::Raw => Tree::List(vec![Tree::Str("raw".into())]),
            Expr::Value(v) => {
                Tree::List(vec![Tree::Str("value".into()), v.tree(cx)])
            }
            Expr::Op(op, args) => {
                let mut out = vec![Tree::Str(op.clone())];
                out.extend(args.iter().map(|a| a.tree(cx)));
                Tree::List(out)
            }
        }
    }
}

/// A MathOperationCalibrator as an expression tree.
#[derive(Debug, Clone)]
pub struct MathOp {
    /// The expression.
    pub expr: Expr,
}
value_record!(MathOp, "MathOp", [expr]);

/// One case of a ContextCalibratorList.
#[derive(Debug, Clone)]
pub enum CalCase {
    /// Unbound: the SpaceSystem it is in, its ContextMatch and its
    /// calibrator.
    Unbound(Key, Elem<x::ContextMatchType>, Calibrator),
    /// Bound: its criteria and its calibrator.
    Bound(Vec<Term>, Calibrator),
}

impl ToTree for CalCase {
    fn tree(&self, cx: &mut Cx) -> Tree {
        match self {
            CalCase::Unbound(p, m, c) => {
                Tree::List(vec![p.tree(cx), m.tree(cx), c.tree(cx)])
            }
            CalCase::Bound(t, c) => Tree::List(vec![t.tree(cx), c.tree(cx)]),
        }
    }
}

/// A ContextCalibratorList and the default calibrator: the first case
/// whose criteria hold picks its calibrator.
#[derive(Debug, Clone)]
pub struct ContextCal {
    /// The cases, in order.
    pub cases: Vec<CalCase>,
    /// The calibrator when no case holds.
    pub default: Option<Box<Calibrator>>,
    /// Whether the cases are bound to a packet's values.
    pub bound: bool,
}
value_record!(ContextCal, "ContextCal", [cases, default, bound]);

/// A calibrator.
#[derive(Debug, Clone)]
pub enum Calibrator {
    /// A polynomial.
    Poly(Polynomial),
    /// A spline.
    Spline(Spline),
    /// A math operation.
    Math(MathOp),
    /// Context calibrators.
    Context(ContextCal),
}

impl ToTree for Calibrator {
    fn tree(&self, cx: &mut Cx) -> Tree {
        match self {
            Calibrator::Poly(c) => c.tree(cx),
            Calibrator::Spline(c) => c.tree(cx),
            Calibrator::Math(c) => c.tree(cx),
            Calibrator::Context(c) => c.tree(cx),
        }
    }
}

// --- value types --------------------------------------------------------

/// One label of an enumeration.
#[derive(Debug, Clone)]
pub struct EnumLabel {
    /// The label.
    pub label: String,
    /// Its identifier in generated code.
    pub ident: String,
    /// Its value.
    pub value: i64,
    /// The last value of its range.
    pub max_value: i64,
}
value_record!(EnumLabel, "EnumLabel", [label, ident, value, max_value]);

/// The engineering type of a parameter or argument, with its encoding.
#[derive(Debug, Clone)]
pub struct VType {
    /// Its qualified name.
    pub key: String,
    /// Its name.
    pub name: String,
    /// `int`, `float`, `enum`, `bool`, `string`, `binary`, `array` or
    /// `aggregate`.
    pub kind: String,
    /// Its encoding.
    pub enc: Encoding,
    /// Its calibrator.
    pub calibrator: Option<Calibrator>,
    /// An enumeration's labels.
    pub labels: Vec<EnumLabel>,
    /// A boolean's string for true.
    pub one_string: String,
    /// A boolean's string for false.
    pub zero_string: String,
    /// The generated type name of an enumeration or aggregate.
    pub ident: String,
    /// More for its generated documentation.
    pub doc: String,
    /// An integer's smallest and largest valid values.
    pub valid_range: Option<(i64, i64)>,
    /// A calibrated integer's own size and signedness.
    pub eng: Option<(i64, bool)>,
    /// Its alarms.
    pub alarm: Option<Alarm>,
    /// The bands its value must lie in when it is encoded.
    pub valid: Vec<Band>,
    /// The aggregate type a bound copy copies.
    pub origin: Option<Rc<VType>>,
}
value_record!(
    VType,
    "VType",
    [
        key,
        name,
        kind,
        enc,
        calibrator,
        labels,
        one_string,
        zero_string,
        ident,
        doc,
        valid_range,
        eng,
        alarm,
        valid,
        origin
    ]
);

impl VType {
    /// Makes a type with nothing but its names, kind and encoding.
    ///
    /// # Arguments
    ///
    /// * `key`  - Its qualified name.
    /// * `name` - Its name.
    /// * `kind` - Its kind.
    /// * `enc`  - Its encoding.
    ///
    /// # Returns
    ///
    /// The type, with every other field defaulted as the Python's are.
    pub fn new(key: &str, name: &str, kind: &str, enc: Encoding) -> Self {
        VType {
            key: key.into(),
            name: name.into(),
            kind: kind.into(),
            enc,
            calibrator: None,
            labels: Vec::new(),
            one_string: "True".into(),
            zero_string: "False".into(),
            ident: String::new(),
            doc: String::new(),
            valid_range: None,
            eng: None,
            alarm: None,
            valid: Vec::new(),
            origin: None,
        }
    }

    /// Gives the bits of the generated field.
    ///
    /// # Returns
    ///
    /// 8, 16, 32, 64 or 128, or the whole bytes of a wider value.
    pub fn width(&self) -> i64 {
        let bits = match self.eng {
            Some((b, _)) => b,
            None => self.enc.bits(),
        };
        [8, 16, 32, 64, 128]
            .into_iter()
            .find(|w| bits <= *w)
            .unwrap_or((bits + 7) / 8 * 8)
    }

    /// Says whether the value is held as an integer of more than 64 bits.
    ///
    /// # Returns
    ///
    /// True for such an integer, or an enumeration on such an encoding.
    pub fn wide(&self) -> bool {
        if self.kind == "enum" {
            return matches!(&self.enc, Encoding::Int(e) if e.bits > 64);
        }
        self.kind == "int" && self.width() > 64
    }

    /// Says whether the value is signed.
    ///
    /// # Returns
    ///
    /// A calibrated integer's own signedness, or its encoding's.
    pub fn signed(&self) -> bool {
        if let Some((_, s)) = self.eng {
            return s;
        }
        matches!(&self.enc, Encoding::Int(e) if e.signed())
    }

    /// Gives the size or count another value gives this type.
    ///
    /// # Returns
    ///
    /// An array's DynSize count, or the encoding's; `None` if neither.
    pub fn dyn_size(&self) -> Option<&DynSize> {
        match &self.enc {
            Encoding::Array(a) => match &a.count {
                Count::Dyn(d) => Some(d),
                Count::Int(_) => None,
            },
            e => e.dyn_size(),
        }
    }
}

// --- alarms -------------------------------------------------------------

/// XTCE's concern levels, least to most; a level is an index into this.
pub const LEVELS: [&str; 6] = [
    "normal", "watch", "warning", "distress", "critical", "severe",
];

/// An alarm range: values inside it (`inside`), or else outside it, raise
/// `level`. A missing bound is infinite.
#[derive(Debug, Clone, PartialEq)]
pub struct Band {
    /// The level, an index into [`LEVELS`].
    pub level: i64,
    /// The lower bound.
    pub lo: Option<Num>,
    /// Whether the lower bound is in the range.
    pub lo_incl: bool,
    /// The upper bound.
    pub hi: Option<Num>,
    /// Whether the upper bound is in the range.
    pub hi_incl: bool,
    /// Whether a value inside raises the level, rather than one outside.
    pub inside: bool,
}
value_record!(Band, "Band", [level, lo, lo_incl, hi, hi_incl, inside]);

/// A deterministic automaton over UTF-8 bytes that tells whether a string
/// matches a pattern.
#[derive(Debug, Clone)]
pub struct Dfa {
    /// The pattern.
    pub pattern: String,
    /// Where each state's transitions start in `trans`, and where the
    /// last's end.
    pub starts: Vec<i64>,
    /// The transitions, each (first byte, last byte, next state).
    pub trans: Vec<(i64, i64, i64)>,
    /// Whether each state accepts.
    pub accept: Vec<bool>,
}
value_record!(Dfa, "Dfa", [pattern, starts, trans, accept]);

/// ChangeAlarmRanges: bands on how much the value changed.
#[derive(Debug, Clone)]
pub struct Change {
    /// The bands.
    pub bands: Vec<Band>,
    /// Whether the change is per second.
    pub per_second: bool,
    /// Whether it is a percentage of the earlier value.
    pub percent: bool,
    /// The samples back the earlier value is.
    pub samples: i64,
}
value_record!(Change, "Change", [bands, per_second, percent, samples]);

/// The criteria of an AlarmConditions level.
#[derive(Debug, Clone)]
pub enum AlarmCriteria {
    /// Unbound: the document's.
    Unbound(Elem<x::MatchCriteriaType>),
    /// Bound to a packet's values.
    Bound(Vec<Term>),
}

impl ToTree for AlarmCriteria {
    fn tree(&self, cx: &mut Cx) -> Tree {
        match self {
            AlarmCriteria::Unbound(e) => e.tree(cx),
            AlarmCriteria::Bound(t) => t.tree(cx),
        }
    }
}

/// One alarm definition (a DefaultAlarm, or a ContextAlarm's).
#[derive(Debug, Clone)]
pub struct AlarmDef {
    /// The ranges a value must lie in.
    pub bands: Vec<Band>,
    /// An enumeration's (first value, last value, level) for each label.
    pub labels: Vec<(i64, i64, i64)>,
    /// A string's patterns and their levels.
    pub patterns: Vec<(Dfa, i64)>,
    /// The level when no label or pattern matches.
    pub default: i64,
    /// The AlarmConditions: each (level, criteria).
    pub conditions: Vec<(i64, AlarmCriteria)>,
    /// The violations in a row before the alarm is raised.
    pub min_violations: i64,
    /// The conforming samples in a row before it is cleared.
    pub min_conformance: i64,
    /// Its change ranges.
    pub change: Option<Change>,
    /// A CustomAlarm: its hook and its InputAlgorithm.
    pub custom: Option<(Shared<Hook>, Elem<x::InputAlgorithmType>)>,
}
value_record!(
    AlarmDef,
    "AlarmDef",
    [
        bands,
        labels,
        patterns,
        default,
        conditions,
        min_violations,
        min_conformance,
        change,
        custom
    ]
);

/// One context of a ContextAlarmList: its ContextMatch, unbound, or its
/// criteria, bound.
#[derive(Debug, Clone)]
pub enum AlarmContext {
    /// The document's ContextMatch.
    Unbound(Elem<x::ContextMatchType>),
    /// Bound to a packet's values.
    Bound(Vec<Term>),
}

impl ToTree for AlarmContext {
    fn tree(&self, cx: &mut Cx) -> Tree {
        match self {
            AlarmContext::Unbound(e) => e.tree(cx),
            AlarmContext::Bound(t) => t.tree(cx),
        }
    }
}

/// The alarms of a type.
#[derive(Debug, Clone)]
pub struct Alarm {
    /// The SpaceSystem the type is in.
    pub path: Key,
    /// The definition when no context holds.
    pub default: Option<AlarmDef>,
    /// The contexts, each with its definition.
    pub contexts: Vec<(AlarmContext, AlarmDef)>,
    /// Why it can't be evaluated, if it can't.
    pub note: String,
    /// Bound: the qualified name of the parameter.
    pub key: String,
}
value_record!(Alarm, "Alarm", [path, default, contexts, note, key]);

// --- what packets hold --------------------------------------------------

/// A CustomAlgorithm that the application supplies as a function.
#[derive(Debug, Clone)]
pub struct Hook {
    /// What it computes, such as `checksum` or `binary_from`.
    pub kind: String,
    /// The algorithm's qualified name.
    pub name: String,
    /// Its name in the generated code.
    pub ident: String,
    /// How many values it gives.
    pub outputs: i64,
}
shared_record!(Hook, "Hook", [kind, name, ident, outputs]);

/// One value read from or written to a packet (a parameter or argument).
#[derive(Debug, Clone)]
pub struct Local {
    /// Its number, unique in the mission.
    pub id: i64,
    /// Its XTCE name.
    pub name: String,
    /// The qualified name of the parameter or argument.
    pub key: String,
    /// Its type.
    pub vtype: Option<Rc<VType>>,
    /// Whether it is inside an IncludeCondition.
    pub optional: bool,
    /// For a packet length field, what to add to the bytes after it.
    pub length: Option<i64>,
    /// Whether a length field measures an included container.
    pub measures: bool,
    /// Whether it holds a checksum.
    pub checksum: bool,
}
shared_record!(
    Local,
    "Local",
    [id, name, key, vtype, optional, length, measures, checksum]
);

/// A literal value of the document, as the Python holds it.
#[derive(Debug, Clone, PartialEq)]
pub enum Lit {
    /// An int.
    Int(Int),
    /// A float.
    Float(f64),
    /// A boolean.
    Bool(bool),
    /// A string.
    Str(String),
    /// Bytes.
    Bytes(Vec<u8>),
}

impl ToTree for Lit {
    fn tree(&self, cx: &mut Cx) -> Tree {
        match self {
            Lit::Int(i) => i.tree(cx),
            Lit::Float(x) => x.tree(cx),
            Lit::Bool(b) => b.tree(cx),
            Lit::Str(s) => s.tree(cx),
            Lit::Bytes(b) => b.tree(cx),
        }
    }
}

/// `local <op> value`, or a comparison of two values of the packet.
#[derive(Debug)]
pub struct Criterion {
    /// The value compared.
    pub local: Shared<Local>,
    /// The comparison operator.
    pub op: String,
    /// The engineering value it is compared with.
    pub value: Lit,
    /// As written in the document, for error messages.
    pub text: String,
    /// The path into `local`.
    pub path: Vec<PathStep>,
    /// The type there.
    pub target: Option<Rc<VType>>,
    /// The other value, for a comparison of two.
    pub other: Option<Shared<Local>>,
    /// The path into it.
    pub other_path: Vec<PathStep>,
    /// The type there.
    pub other_target: Option<Rc<VType>>,
}
shared_record!(
    Criterion,
    "Criterion",
    [
        local,
        op,
        value,
        text,
        path,
        target,
        other,
        other_path,
        other_target
    ]
);

/// Holds when every term does (ANDedConditions).
#[derive(Debug)]
pub struct AllOf {
    /// The terms.
    pub terms: Vec<Term>,
    /// As written, for messages.
    pub text: String,
}
shared_record!(AllOf, "AllOf", [terms, text]);

/// Holds when any term does (ORedConditions).
#[derive(Debug)]
pub struct AnyOf {
    /// The terms.
    pub terms: Vec<Term>,
    /// As written, for messages.
    pub text: String,
}
shared_record!(AnyOf, "AnyOf", [terms, text]);

/// One input of a custom algorithm.
#[derive(Debug, Clone)]
pub enum Input {
    /// `("value", local, path, vtype)`.
    Value(Shared<Local>, Vec<PathStep>, Rc<VType>),
    /// `("const", x)`.
    Const(Lit),
}

impl ToTree for Input {
    fn tree(&self, cx: &mut Cx) -> Tree {
        match self {
            Input::Value(l, p, t) => Tree::List(vec![
                Tree::Str("value".into()),
                l.tree(cx),
                p.tree(cx),
                t.tree(cx),
            ]),
            Input::Const(x) => {
                Tree::List(vec![Tree::Str("const".into()), x.tree(cx)])
            }
        }
    }
}

/// A CustomAlgorithm criterion: it holds when its hook says so.
#[derive(Debug)]
pub struct Custom {
    /// The hook.
    pub hook: Shared<Hook>,
    /// Its inputs.
    pub inputs: Vec<Input>,
    /// As written, for messages.
    pub text: String,
}
shared_record!(Custom, "Custom", [hook, inputs, text]);

/// A criterion, or a tree of them.
#[derive(Debug, Clone)]
pub enum Term {
    /// A comparison.
    Criterion(Shared<Criterion>),
    /// A custom algorithm's.
    Custom(Shared<Custom>),
    /// All of some terms.
    AllOf(Shared<AllOf>),
    /// Any of some terms.
    AnyOf(Shared<AnyOf>),
}

impl ToTree for Term {
    fn tree(&self, cx: &mut Cx) -> Tree {
        match self {
            Term::Criterion(t) => t.tree(cx),
            Term::Custom(t) => t.tree(cx),
            Term::AllOf(t) => t.tree(cx),
            Term::AnyOf(t) => t.tree(cx),
        }
    }
}
