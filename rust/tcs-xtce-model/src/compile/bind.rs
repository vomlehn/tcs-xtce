//! Entries' values: where they go, their repeats and subsets, and their
//! sizes and calibrators bound to the values a packet decodes before
//! them, as the Python compiler's `location`, `repeat`, `bind` and their
//! kin make them.

use std::rc::Rc;

use num_bigint::BigInt;
use tcs_xtce::generated as x;

use super::criteria::{instance_of, walk_members, ArgKey, CritRef, XRef};
use super::layout::fixed_bits;
use super::types::{
    dyn_units, int_bounds, is_rest, item_bits, IntValue, IntValueRef,
};
use super::{ancillary_int, qname, Compiler, TypeTable, LENGTH_TAG};
use crate::dump::{Elem, Shared};
use crate::model::{
    unsupported, ArrayEnc, CalCase, Calibrator, ContextCal, Count, DynSize,
    Encoding, EndSeek, Expr, IntEnc, Item, Key, Local, LookupList, MathOp,
    NextSeek, Order, Result, Seek, VType, ValueRef,
};
use crate::pyfmt::repr;

/// Says whether an integer encoding is most significant bit first, and
/// big endian, or little endian in whole bytes: what a packet length or
/// checksum field may be.
///
/// # Arguments
///
/// * `e` - The encoding.
///
/// # Returns
///
/// Whether it is.
pub fn plain_order(e: &IntEnc) -> bool {
    !e.lsb
        && (e.order == Order::Be || (e.order == Order::Le && e.bits % 8 == 0))
}

/// Reads the adjustment of a packet length field.
///
/// # Arguments
///
/// * `ads`    - The Parameter's or Argument's AncillaryDataSet.
/// * `vt`     - Its compiled type.
/// * `where_` - What it is, for error messages.
///
/// # Returns
///
/// The number to add to the bytes after the field, or `None` if it isn't
/// a packet length field.
///
/// # Errors
///
/// Unsupported, if it is one but its type isn't an uncalibrated unsigned
/// binary integer, big or little endian in whole bytes, most significant
/// bit first.
pub fn length_adjust(
    ads: Option<&x::AncillaryDataSetType>,
    vt: &VType,
    where_: &str,
) -> Result<Option<i64>> {
    let Some(adjust) = ancillary_int(ads, LENGTH_TAG, where_)? else {
        return Ok(None);
    };
    let plain = match &vt.enc {
        Encoding::Int(e) => {
            vt.kind == "int"
                && !e.signed()
                && !e.bcd()
                && vt.calibrator.is_none()
        }
        _ => false,
    };
    if !plain {
        return unsupported(format!(
            "{where_}: a packet length field must be an uncalibrated \
             unsigned binary integer"
        ));
    }
    let Encoding::Int(e) = &vt.enc else {
        unreachable!("an integer");
    };
    if !plain_order(e) {
        return unsupported(format!(
            "{where_}: a packet length field must be big or little endian in \
             whole bytes, most significant bit first"
        ));
    }
    Ok(Some(adjust))
}

/// Says whether a size is still to be bound to the value that gives it.
///
/// # Arguments
///
/// * `d` - The size.
///
/// # Returns
///
/// Whether it is.
pub fn unbound(d: &DynSize) -> bool {
    d.local.is_none() && d.lookup.is_none() && d.member.is_none()
}

/// Says whether a calibrator uses other values of the packet, still to be
/// bound.
///
/// # Arguments
///
/// * `cal` - The calibrator.
///
/// # Returns
///
/// Whether it does.
pub fn needs_binding(cal: Option<&Calibrator>) -> bool {
    match cal {
        Some(Calibrator::Context(c)) => !c.bound,
        Some(Calibrator::Math(m)) => {
            let mut refs = Vec::new();
            expr_refs(&m.expr, &mut refs);
            refs.iter().any(|r| r.get().local.is_none())
        }
        _ => false,
    }
}

/// Lists the other values of the packet an expression uses.
///
/// # Arguments
///
/// * `e`   - The expression.
/// * `out` - Where its ValueRefs go, in the order the tree holds them.
pub fn expr_refs(e: &Expr, out: &mut Vec<Shared<ValueRef>>) {
    match e {
        Expr::Value(v) => out.push(v.clone()),
        Expr::Op(_, args) => {
            for a in args {
                expr_refs(a, out);
            }
        }
        _ => {}
    }
}

/// A LocationInContainerInBits, of a parameter's entry.
pub type LocRef<'b> = Option<&'b x::LocationInContainerInBitsType>;

impl Compiler<'_> {
    /// Makes a new value.
    ///
    /// # Arguments
    ///
    /// * `name` - Its XTCE name.
    /// * `key`  - Its parameter's or argument's qualified name.
    /// * `vt`   - Its type.
    ///
    /// # Returns
    ///
    /// The Local, with the next number.
    pub fn local(
        &mut self,
        name: &str,
        key: &str,
        vt: Rc<VType>,
    ) -> Shared<Local> {
        let id = self.next_id();
        Shared::new(Local {
            id,
            name: name.into(),
            key: key.into(),
            vtype: Some(vt),
            optional: false,
            length: None,
            measures: false,
            checksum: false,
        })
    }

    /// Compiles an entry's location; one from other values is bound to
    /// the scope and must not move backwards.
    ///
    /// # Arguments
    ///
    /// * `loc`     - The entry's LocationInContainerInBits, or `None`.
    /// * `start`   - The id of the Mark at its container's start.
    /// * `path`    - The key of the SpaceSystem the container is in.
    /// * `scope`   - The Locals decoded before the entry.
    /// * `where_`  - What the container is, for error messages.
    /// * `arg_key` - In a command, gives the key of an argument from its name;
    ///               `None` elsewhere.
    ///
    /// # Returns
    ///
    /// The items that place it: none, a Seek, an EndSeek or a NextSeek.
    ///
    /// # Errors
    ///
    /// Unsupported, if the location is of an unsupported form, or one
    /// from the container's end or the next entry comes from other
    /// values.
    pub fn location(
        &mut self,
        loc: LocRef<'_>,
        start: i64,
        path: &Key,
        scope: &[Shared<Local>],
        where_: &str,
        arg_key: ArgKey<'_>,
    ) -> Result<Vec<Item>> {
        let Some(loc) = loc else {
            return Ok(Vec::new());
        };
        let reference = loc
            .reference_location
            .map(|r| r.as_str())
            .unwrap_or("previousEntry");
        let off = match &loc.choice {
            x::LocationInContainerInBitsChoice::FixedValue(n) => Count::Int(*n),
            x::LocationInContainerInBitsChoice::DynamicValue(d) => {
                let d = self.dyn_size(
                    super::types::DynRef::P(d),
                    path,
                    "location",
                    where_,
                )?;
                self.bound_location(d, scope, where_, arg_key)?
            }
            x::LocationInContainerInBitsChoice::DiscreteLookupList(l) => {
                let d = DynSize {
                    table: Some((
                        path.clone(),
                        LookupList::Param(Elem {
                            value: l.clone(),
                            class: "DiscreteLookupListType",
                        }),
                    )),
                    ..DynSize::default()
                };
                self.bound_location(d, scope, where_, arg_key)?
            }
        };
        match reference {
            "containerStart" => {
                Ok(vec![Item::Seek(Seek::new(Some(start), off))])
            }
            "previousEntry" => Ok(match off {
                Count::Int(0) => Vec::new(),
                off => vec![Item::Seek(Seek::new(None, off))],
            }),
            "containerEnd" => match off {
                Count::Int(o) => {
                    Ok(vec![Item::EndSeek(EndSeek { start, offset: o })])
                }
                Count::Dyn(_) => unsupported(format!(
                    "{where_}: a location from the container's end that other \
                     values give"
                )),
            },
            _ => match off {
                Count::Int(o) => {
                    Ok(vec![Item::NextSeek(NextSeek { offset: o })])
                }
                Count::Dyn(_) => unsupported(format!(
                    "{where_}: a location from the next entry that other \
                     values give"
                )),
            },
        }
    }

    /// Binds a location's DynSize and bounds it.
    ///
    /// # Arguments
    ///
    /// * `d`       - The DynSize.
    /// * `scope`   - The Locals decoded before the entry.
    /// * `where_`  - What the container is, for error messages.
    /// * `arg_key` - In a command, gives the key of an argument.
    ///
    /// # Returns
    ///
    /// The bound offset.
    ///
    /// # Errors
    ///
    /// As [`Compiler::bind_dyn`]'s.
    pub fn bound_location(
        &mut self,
        d: DynSize,
        scope: &[Shared<Local>],
        where_: &str,
        arg_key: ArgKey<'_>,
    ) -> Result<Count> {
        let (mut off, rng) = self.bind_dyn(&d, scope, where_, arg_key)?;
        off.max = dyn_units(&off, &rng, self.max_field_bits);
        Ok(Count::Dyn(off))
    }

    /// Turns the value of an entry with a RepeatEntry into an array.
    ///
    /// # Arguments
    ///
    /// * `local`  - The entry's value; its type is replaced.
    /// * `rp`     - The entry's RepeatEntry, if it has one.
    /// * `path`   - The key of the SpaceSystem the entry is in.
    /// * `where_` - What it is in, for error messages.
    ///
    /// # Returns
    ///
    /// `local`, its type now an array of the repeats if the count isn't 1.
    ///
    /// # Errors
    ///
    /// Unsupported, if it is a length or checksum field or takes the rest
    /// of the packet, or its Count isn't positive or its Offset negative.
    pub fn repeat(
        &mut self,
        local: Shared<Local>,
        rp: Option<RepeatRef<'_>>,
        path: &Key,
        where_: &str,
    ) -> Result<Shared<Local>> {
        let Some(rp) = rp else {
            return Ok(local);
        };
        if local.get().computed() {
            return unsupported(format!(
                "{where_}: RepeatEntry on packet length or checksum field {}",
                local.get().name
            ));
        }
        let vt = local.get().vt();
        if is_rest(&vt) {
            return unsupported(format!(
                "{where_}: RepeatEntry on a value that takes the rest of the \
                 packet"
            ));
        }
        let count =
            self.int_value(rp.count, path, "RepeatEntry Count", where_)?;
        let mut gap = Count::Int(0);
        if let Some(o) = rp.offset {
            gap = match self.int_value(o, path, "RepeatEntry Offset", where_)? {
                IntValue::Int(g) if g < 0 => {
                    return unsupported(format!(
                        "{where_}: RepeatEntry Offset {g} is negative"
                    ))
                }
                IntValue::Int(g) => Count::Int(g),
                IntValue::Dyn(d) => Count::Dyn(d),
            };
        }
        let count = match count {
            IntValue::Int(c) if c < 1 => {
                return unsupported(format!(
                    "{where_}: RepeatEntry Count {c} is not positive"
                ))
            }
            IntValue::Int(1) => return Ok(local),
            IntValue::Int(c) => Count::Int(c),
            IntValue::Dyn(d) => Count::Dyn(d),
        };
        let (shape, size) = match &count {
            Count::Int(c) => (vec![*c], Some(*c)),
            Count::Dyn(_) => (Vec::new(), None),
        };
        let mut nvt = VType::new(
            &vt.key,
            &vt.name,
            "array",
            Encoding::Array(ArrayEnc {
                elem: vt.clone(),
                count,
                gap,
                dims: 1,
                starts: vec![0],
                shape,
                sizes: vec![size],
            }),
        );
        nvt.doc = vt.doc.clone();
        local.get_mut().vtype = Some(Rc::new(nvt));
        Ok(local)
    }

    /// Turns the value of an array entry with a DimensionList into the
    /// subset it gives.
    ///
    /// # Arguments
    ///
    /// * `local`  - The entry's value; its type is replaced.
    /// * `dl`     - The entry's DimensionList, if it has one.
    /// * `path`   - The key of the SpaceSystem the entry is in.
    /// * `where_` - What the entry is in, for error messages.
    ///
    /// # Returns
    ///
    /// `local`, its type now the subset's if the entry has a
    /// DimensionList.
    ///
    /// # Errors
    ///
    /// Unsupported, if the value isn't an array, or the subset has another
    /// number of dimensions or lies outside it.
    pub fn subset(
        &mut self,
        local: Shared<Local>,
        dl: Option<&x::DimensionListType>,
        path: &Key,
        where_: &str,
    ) -> Result<Shared<Local>> {
        let Some(dl) = dl else {
            return Ok(local);
        };
        let vt = local.get().vt();
        let name = local.get().name.clone();
        let Encoding::Array(full) = &vt.enc else {
            return unsupported(format!(
                "{where_}: DimensionList on {} value {name}",
                vt.kind
            ));
        };
        let dims: Vec<(IntValueRef<'_>, IntValueRef<'_>)> = dl
            .dimension
            .iter()
            .map(|d| {
                (
                    IntValueRef::P(&d.starting_index),
                    IntValueRef::P(&d.ending_index),
                )
            })
            .collect();
        let enc = self.dimensions(full.elem.clone(), &dims, path, where_)?;
        if enc.dims != full.dims {
            return unsupported(format!(
                "{where_}: {} dimensions for {}-dimensional {name}",
                enc.dims, full.dims
            ));
        }
        for (k, start) in enc.starts.iter().enumerate() {
            if *start < full.starts[k] {
                return unsupported(format!(
                    "{where_}: subset of {name} starts before its index {}",
                    full.starts[k]
                ));
            }
            if !full.shape.is_empty()
                && !enc.shape.is_empty()
                && start + enc.shape[k] > full.starts[k] + full.shape[k]
            {
                return unsupported(format!(
                    "{where_}: subset of {name} ends after its index {}",
                    full.starts[k] + full.shape[k] - 1
                ));
            }
        }
        let doc = format!(
            "a subset of {}{}",
            vt.key,
            if vt.doc.is_empty() {
                String::new()
            } else {
                format!("; {}", vt.doc)
            }
        );
        let mut nvt =
            VType::new(&vt.key, &vt.name, "array", Encoding::Array(enc));
        nvt.doc = doc;
        local.get_mut().vtype = Some(Rc::new(nvt));
        Ok(local)
    }

    /// Binds the size or count of a value to what gives it, which must be
    /// decoded before it, and bounds it.
    ///
    /// # Arguments
    ///
    /// * `local`   - The value; its type is replaced.
    /// * `scope`   - The Locals decoded before it.
    /// * `where_`  - What it is in, for error messages.
    /// * `arg_key` - In a command, gives the key of an argument.
    ///
    /// # Returns
    ///
    /// `local`.
    ///
    /// # Errors
    ///
    /// As [`Compiler::bind_sizes`]'s and [`Compiler::bind_calibrators`]'.
    pub fn bind(
        &mut self,
        local: Shared<Local>,
        scope: &[Shared<Local>],
        where_: &str,
        arg_key: ArgKey<'_>,
    ) -> Result<Shared<Local>> {
        let vt = local.get().vt();
        let vt = self.bind_sizes(&vt, scope, where_, arg_key)?;
        let vt = self.bind_calibrators(&vt, scope, where_, arg_key)?;
        local.get_mut().vtype = Some(vt);
        Ok(local)
    }

    /// Binds the sizes, counts and gaps of a type that other values give,
    /// its array elements' too, to the values in the scope.
    ///
    /// # Arguments
    ///
    /// * `vt`      - The type.
    /// * `scope`   - The Locals decoded before the value.
    /// * `where_`  - What it is in, for error messages.
    /// * `arg_key` - In a command, gives the key of an argument.
    ///
    /// # Returns
    ///
    /// `vt`, or a bounded copy if anything was bound.
    ///
    /// # Errors
    ///
    /// As [`Compiler::bind_dyn`]'s.
    pub fn bind_sizes(
        &mut self,
        vt: &Rc<VType>,
        scope: &[Shared<Local>],
        where_: &str,
        arg_key: ArgKey<'_>,
    ) -> Result<Rc<VType>> {
        let mut vt = vt.clone();
        if let Encoding::Array(e) = &vt.enc {
            let elem = self.bind_sizes(&e.elem, scope, where_, arg_key)?;
            let mut gap = e.gap.clone();
            let mut gap_bound = false;
            if let Count::Dyn(g) = &e.gap {
                if unbound(g) {
                    let (mut b, rng) =
                        self.bind_dyn(g, scope, where_, arg_key)?;
                    b.max = dyn_units(&b, &rng, self.max_field_bits);
                    gap = Count::Dyn(b);
                    gap_bound = true;
                }
            }
            if !Rc::ptr_eq(&elem, &e.elem) || gap_bound {
                let enc = Encoding::Array(ArrayEnc {
                    elem,
                    gap,
                    ..e.clone()
                });
                vt = Rc::new(VType {
                    enc,
                    ..(*vt).clone()
                });
            }
        }
        if let Some(d) = vt.dyn_size() {
            if unbound(d) {
                let d = d.clone();
                let (b, rng) = self.bind_dyn(&d, scope, where_, arg_key)?;
                vt = Rc::new(self.bound(&vt, b, &rng, where_));
            }
        }
        Ok(vt)
    }

    /// Binds the calibrators in a type that use other values of the
    /// packet to those in the scope.
    ///
    /// # Arguments
    ///
    /// * `vt`      - The type.
    /// * `scope`   - The Locals decoded before the value.
    /// * `where_`  - What it is in, for error messages.
    /// * `arg_key` - In a command, gives the key of an argument.
    ///
    /// # Returns
    ///
    /// `vt`, or a copy with bound copies of its calibrators (its array
    /// elements' and members' too).
    ///
    /// # Errors
    ///
    /// As [`Compiler::bind_cal`]'s.
    pub fn bind_calibrators(
        &mut self,
        vt: &Rc<VType>,
        scope: &[Shared<Local>],
        where_: &str,
        arg_key: ArgKey<'_>,
    ) -> Result<Rc<VType>> {
        match &vt.enc {
            Encoding::Array(e) => {
                let elem =
                    self.bind_calibrators(&e.elem, scope, where_, arg_key)?;
                if Rc::ptr_eq(&elem, &e.elem) {
                    return Ok(vt.clone());
                }
                Ok(Rc::new(VType {
                    enc: Encoding::Array(ArrayEnc { elem, ..e.clone() }),
                    ..(**vt).clone()
                }))
            }
            Encoding::Aggregate(e) => {
                // The same type, with its members' calibrators bound here.
                let mut members = Vec::new();
                for m in &e.members {
                    let t = self
                        .bind_calibrators(&m.vtype, scope, where_, arg_key)?;
                    members.push(crate::model::Member {
                        vtype: t,
                        ..m.clone()
                    });
                }
                if members
                    .iter()
                    .zip(&e.members)
                    .all(|(a, b)| Rc::ptr_eq(&a.vtype, &b.vtype))
                {
                    return Ok(vt.clone());
                }
                Ok(Rc::new(VType {
                    enc: Encoding::Aggregate(crate::model::AggregateEnc {
                        members,
                        ..e.clone()
                    }),
                    origin: Some(
                        vt.origin.clone().unwrap_or_else(|| vt.clone()),
                    ),
                    ..(**vt).clone()
                }))
            }
            _ => {
                if !needs_binding(vt.calibrator.as_ref()) {
                    return Ok(vt.clone());
                }
                let cal = vt.calibrator.clone().unwrap();
                let cal = self.bind_cal(&cal, scope, where_, arg_key)?;
                Ok(Rc::new(VType {
                    calibrator: Some(cal),
                    ..(**vt).clone()
                }))
            }
        }
    }

    /// Binds a calibrator to the values in the scope.
    ///
    /// # Arguments
    ///
    /// * `cal`     - The calibrator.
    /// * `scope`   - The Locals decoded before the value.
    /// * `where_`  - What it is in, for error messages.
    /// * `arg_key` - In a command, gives the key of an argument.
    ///
    /// # Returns
    ///
    /// A bound copy of a ContextCal or MathOp; any other calibrator as it
    /// is.
    ///
    /// # Errors
    ///
    /// Unsupported, if a context depends on a packet length or checksum
    /// field, or a value a math operation uses can't be.
    pub fn bind_cal(
        &mut self,
        cal: &Calibrator,
        scope: &[Shared<Local>],
        where_: &str,
        arg_key: ArgKey<'_>,
    ) -> Result<Calibrator> {
        match cal {
            Calibrator::Context(c) => {
                let mut cases = Vec::new();
                for case in &c.cases {
                    let (path, mc, k) = match case {
                        CalCase::Unbound(p, m, k) => (p, m, k),
                        CalCase::Bound(..) => unreachable!("an unbound case"),
                    };
                    let terms = self.match_(
                        CritRef::context(&mc.value),
                        path,
                        scope,
                        where_,
                        arg_key,
                    )?;
                    for lc in terms.iter().flat_map(|t| t.locals()) {
                        if lc.get().computed() {
                            return unsupported(format!(
                                "{where_}: a context calibrator on packet \
                                 length or checksum field {}",
                                lc.get().name
                            ));
                        }
                    }
                    let k = self.bind_cal(k, scope, where_, arg_key)?;
                    cases.push(CalCase::Bound(terms, k));
                }
                let default = match &c.default {
                    Some(d) => Some(Box::new(
                        self.bind_cal(d, scope, where_, arg_key)?,
                    )),
                    None => None,
                };
                Ok(Calibrator::Context(ContextCal {
                    cases,
                    default,
                    bound: true,
                }))
            }
            Calibrator::Math(m) => Ok(Calibrator::Math(MathOp {
                expr: self.bind_expr(&m.expr, scope, where_, arg_key)?,
            })),
            other => Ok(other.clone()),
        }
    }

    /// Binds the values a MathOperationCalibrator's tree uses.
    ///
    /// # Arguments
    ///
    /// * `e`       - The tree, or a subtree.
    /// * `scope`   - The Locals decoded before the value.
    /// * `where_`  - What it is in, for error messages.
    /// * `arg_key` - In a command, gives the key of an argument.
    ///
    /// # Returns
    ///
    /// A copy whose ValueRefs hold the Locals they use.
    ///
    /// # Errors
    ///
    /// Unsupported, if a value used is optional, isn't a number, is a
    /// length or checksum field, or is raw but calibrated.
    pub fn bind_expr(
        &mut self,
        e: &Expr,
        scope: &[Shared<Local>],
        where_: &str,
        arg_key: ArgKey<'_>,
    ) -> Result<Expr> {
        match e {
            Expr::Const(_) | Expr::Raw => Ok(e.clone()),
            Expr::Op(op, args) => {
                let mut out = Vec::new();
                for a in args {
                    out.push(self.bind_expr(a, scope, where_, arg_key)?);
                }
                Ok(Expr::Op(op.clone(), out))
            }
            Expr::Value(r) => {
                let (rf, path) = {
                    let r = r.get();
                    (r.ref_.clone(), r.path.clone())
                };
                let (local, member, vt, name) = self.value_ref(
                    &XRef::param(&rf.value),
                    &path,
                    scope,
                    where_,
                    arg_key,
                )?;
                if local.get().optional {
                    return unsupported(format!(
                        "{where_}: MathOperationCalibrator uses {name}, which \
                         has an IncludeCondition"
                    ));
                }
                if !matches!(
                    vt.kind.as_str(),
                    "int" | "float" | "enum" | "bool"
                ) {
                    return unsupported(format!(
                        "{where_}: MathOperationCalibrator uses {} value \
                         {name}",
                        vt.kind
                    ));
                }
                if local.get().computed() {
                    return unsupported(format!(
                        "{where_}: MathOperationCalibrator uses packet length \
                         or checksum field {name}"
                    ));
                }
                if rf.value.use_calibrated_value == Some(false)
                    && vt.calibrator.is_some()
                {
                    return unsupported(format!(
                        "{where_}: MathOperationCalibrator uses the raw value \
                         of calibrated {name}"
                    ));
                }
                Ok(Expr::Value(Shared::new(ValueRef {
                    ref_: rf,
                    path,
                    local: Some(local),
                    member,
                    vtype: Some(vt),
                })))
            }
        }
    }

    /// Binds a DynSize to the values in the scope.
    ///
    /// # Arguments
    ///
    /// * `d`       - The DynSize.
    /// * `scope`   - The Locals decoded before the value it sizes.
    /// * `where_`  - What it is in, for error messages.
    /// * `arg_key` - In a command, gives the key of an argument.
    ///
    /// # Returns
    ///
    /// A bound copy, and the range (lo, hi) of the value it adjusts.
    ///
    /// # Errors
    ///
    /// Unsupported, if the value it names isn't there, or can't give a
    /// size.
    pub fn bind_dyn(
        &mut self,
        d: &DynSize,
        scope: &[Shared<Local>],
        where_: &str,
        arg_key: ArgKey<'_>,
    ) -> Result<(DynSize, (BigInt, BigInt))> {
        if let Some((path, table)) = &d.table {
            let LookupList::Param(t) = table else {
                return self.bind_arg_lookup(d, scope, where_, arg_key);
            };
            let mut lookup = Vec::new();
            for lk in &t.value.discrete_lookup {
                let terms = self.match_(
                    CritRef::lookup(lk),
                    path,
                    scope,
                    where_,
                    arg_key,
                )?;
                lookup.push((terms, lk.value));
            }
            for (crit, _) in &lookup {
                for lc in crit.iter().flat_map(|c| c.locals()) {
                    if lc.get().computed() {
                        return unsupported(format!(
                            "{where_}: size or count from packet length or \
                             checksum field {}",
                            lc.get().name
                        ));
                    }
                }
            }
            let default = t.value.default_value;
            let values: Vec<i64> = lookup
                .iter()
                .map(|l| l.1)
                .chain(std::iter::once(default))
                .collect();
            let rng = (
                BigInt::from(*values.iter().min().unwrap()),
                BigInt::from(*values.iter().max().unwrap()),
            );
            return Ok((
                DynSize {
                    lookup: Some(lookup),
                    default,
                    ..d.clone()
                },
                rng,
            ));
        }
        let (key, name) = if let Some(arg) = &d.arg {
            let Some(ak) = arg_key else {
                return unsupported(format!(
                    "{where_}: size from argument {} outside a command",
                    repr(arg)
                ));
            };
            (ak(arg)?, arg.clone())
        } else if let Some(k) = &d.key {
            (k.clone(), k.clone())
        } else {
            return unsupported(format!(
                "{where_}: unresolved parameter reference {}",
                repr(&d.name)
            ));
        };
        let Some(r) = instance_of(scope, &key, d.instance, where_)? else {
            return unsupported(format!(
                "{where_}: size or count from {name}, which is not decoded \
                 before it"
            ));
        };
        if r.get().optional {
            return unsupported(format!(
                "{where_}: size or count from {name}, which has an \
                 IncludeCondition"
            ));
        }
        if r.get().computed() {
            return unsupported(format!(
                "{where_}: size or count from packet length or checksum field \
                 {name}"
            ));
        }
        let rvt = r.get().vt();
        let (member, vt) = walk_members(
            &rvt,
            &d.members,
            &format!("{where_}: size or count from {}", d.name),
        )?;
        if vt.kind != "int" {
            return unsupported(format!(
                "{where_}: size or count from {} value {}",
                vt.kind,
                if d.name.is_empty() { &name } else { &d.name }
            ));
        }
        let rng = int_bounds(&vt);
        let wide = if vt.wide() { Some(vt.clone()) } else { None };
        Ok((
            DynSize {
                local: Some(r),
                path: member,
                wide_type: wide,
                ..d.clone()
            },
            rng,
        ))
    }

    /// Binds a DynSize whose DiscreteLookupList is an argument's: commands
    /// are compiled in a later stage of the port.
    ///
    /// # Arguments
    ///
    /// * `d`       - The DynSize.
    /// * `scope`   - The Locals decoded before the value it sizes.
    /// * `where_`  - What it is in, for error messages.
    /// * `arg_key` - In a command, gives the key of an argument.
    ///
    /// # Returns
    ///
    /// Nothing yet.
    ///
    /// # Errors
    ///
    /// Unsupported, always, until commands are ported.
    fn bind_arg_lookup(
        &mut self,
        _d: &DynSize,
        _scope: &[Shared<Local>],
        where_: &str,
        _arg_key: ArgKey<'_>,
    ) -> Result<(DynSize, (BigInt, BigInt))> {
        unsupported(format!(
            "{where_}: an argument's DiscreteLookupList, which the Rust model \
             doesn't compile yet"
        ))
    }

    /// Makes a new value of a parameter, for an entry that holds it.
    ///
    /// # Arguments
    ///
    /// * `ref_` - The reference to the parameter.
    /// * `path` - The key of the SpaceSystem the reference is written in.
    ///
    /// # Returns
    ///
    /// The Local, with the parameter's type, and its length adjustment if
    /// it is a packet length field.
    ///
    /// # Errors
    ///
    /// Unsupported, if the reference or the parameter's type doesn't
    /// resolve, or the type isn't supported.
    pub fn param_local(
        &mut self,
        ref_: &str,
        path: &[String],
    ) -> Result<Shared<Local>> {
        let params = |k: &[String]| self.params.contains(k);
        let pkey = Self::resolve(ref_, path, &params, "parameter")?;
        let (ppath, p) = self.params.get(&pkey).expect("a parameter").clone();
        let tkey = self.resolve_type(
            &p.parameter_type_ref,
            &ppath,
            TypeTable::Param,
            "parameter type",
        )?;
        let vt = self.vtype(&tkey, TypeTable::Param)?;
        let local = self.local(&p.name, &qname(&pkey), vt.clone());
        let len = length_adjust(
            p.ancillary_data_set.as_ref(),
            &vt,
            &format!("parameter {}", qname(&pkey)),
        )?;
        local.get_mut().length = len;
        Ok(local)
    }

    /// Places an entry with a nextEntry location, now that the entry after
    /// it has been located: the earlier entry ends where the later one
    /// starts, less the offset.
    ///
    /// # Arguments
    ///
    /// * `items`  - The items so far; the last NextSeek in them, if any, is
    ///              replaced by a Seek or removed.
    /// * `loc`    - The location items of the entry after it.
    /// * `where_` - What they belong to, for error messages.
    ///
    /// # Returns
    ///
    /// `loc`, adjusted.
    ///
    /// # Errors
    ///
    /// Unsupported, if the earlier entry's size varies, the later entry is
    /// located from another, or its location says something else.
    pub fn next_seek(
        items: &mut Vec<Item>,
        loc: Vec<Item>,
        where_: &str,
    ) -> Result<Vec<Item>> {
        let Some(at) =
            items.iter().rposition(|i| matches!(i, Item::NextSeek(_)))
        else {
            return Ok(loc);
        };
        let Item::NextSeek(ns) = &items[at] else {
            unreachable!("a NextSeek");
        };
        let k = ns.offset;
        if let [Item::Seek(s)] = &loc[..] {
            if let (Some(from), Count::Int(off)) = (s.from_start, &s.offset) {
                // The next entry is placed from its container's start, so
                // this one goes back from there.
                let mut bits = 0;
                for it in &items[at + 1..] {
                    if let Item::Value(v) = it {
                        let vt = v.local.get().vt();
                        if fixed_bits(&vt).is_none() {
                            return unsupported(format!(
                                "{where_}: an entry located from the next \
                                 entry whose size varies"
                            ));
                        }
                        bits += item_bits(&vt);
                    }
                }
                items[at] = Item::Seek(Seek::new(
                    Some(from),
                    Count::Int(off - k - bits),
                ));
                return Ok(loc);
            }
        }
        if loc
            .iter()
            .any(|i| matches!(i, Item::EndSeek(_) | Item::NextSeek(_)))
        {
            return unsupported(format!(
                "{where_}: an entry located from the next entry, which is \
                 located from another"
            ));
        }
        // The next entry follows this one, k bits after its end.
        let given: i64 = loc
            .iter()
            .filter_map(|i| match i {
                Item::Seek(Seek {
                    offset: Count::Int(o),
                    ..
                }) => Some(*o),
                _ => None,
            })
            .sum();
        if !loc.is_empty() && (given != k || loc.len() > 1) {
            return unsupported(format!(
                "{where_}: an entry located {k} bits before the next one, \
                 which says otherwise"
            ));
        }
        items.remove(at);
        Ok(if k != 0 {
            vec![Item::Seek(Seek::new(None, Count::Int(k)))]
        } else {
            Vec::new()
        })
    }

    /// Rejects a nextEntry location with no entry after it in its entry
    /// list, or inside an IncludeCondition.
    ///
    /// # Arguments
    ///
    /// * `items`  - The items compiled from the list.
    /// * `where_` - What they belong to, for error messages.
    ///
    /// # Errors
    ///
    /// Unsupported, if there is a NextSeek among them.
    pub fn no_next(items: &[Item], where_: &str) -> Result<()> {
        if crate::model::walk(items, true)
            .iter()
            .any(|i| matches!(i, Item::NextSeek(_)))
        {
            return unsupported(format!(
                "{where_}: an entry located from the next entry, without one \
                 after it in its entry list and condition"
            ));
        }
        Ok(())
    }
}

/// A RepeatEntry: its Count and Offset.
#[derive(Debug, Clone, Copy)]
pub struct RepeatRef<'b> {
    /// The Count.
    pub count: IntValueRef<'b>,
    /// The Offset, if given.
    pub offset: Option<IntValueRef<'b>>,
}

impl<'b> RepeatRef<'b> {
    /// Views a parameter entry's RepeatEntry.
    ///
    /// # Arguments
    ///
    /// * `r` - The RepeatEntry, if any.
    ///
    /// # Returns
    ///
    /// The view, if there is one.
    pub fn of(r: Option<&'b x::RepeatType>) -> Option<Self> {
        r.map(|r| RepeatRef {
            count: IntValueRef::P(&r.count),
            offset: r.offset.as_ref().map(IntValueRef::P),
        })
    }
}
