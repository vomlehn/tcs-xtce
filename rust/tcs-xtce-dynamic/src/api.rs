//! The run-time codec's interface for programs: values as [`Datum`]s,
//! the current value of every slot in a [`Store`], and packing and
//! unpacking packets between bytes and a store, as
//! `docs/runtime-codec-design.rst` describes.
//!
//! A packet's values map to slots in the order the lookups make them: a
//! scalar's one slot, an aggregate's members in order, and an array's
//! elements, the last index fastest. An array whose count varies has a
//! slot for each element it may hold; unpacking leaves those past its
//! count without a value, and packing takes its elements up to the first
//! slot without one.
//!
//! A store and a scratch get all the storage they need when they are
//! made, each string, binary value and array the room its largest value
//! needs, so packing, unpacking, setting and committing allocate nothing,
//! except for integers of more than 64 bits, which are `BigInt`s, and
//! for the message of an error.

use std::cell::RefCell;
use std::fmt;
use std::rc::Rc;

use num_bigint::BigInt;
use num_traits::ToPrimitive;
use tcs_xtce::buffer::Buffer;
use tcs_xtce_model::dump::Shared;
use tcs_xtce_model::model::{Count, Encoding, Node, VType};

use crate::interp::{self, CodecError, Look, Val, Values, Workspace};
use crate::{Mission, NodeId, SlotId};

/// A value of a slot, as a program gives and reads it: an engineering
/// value, after calibration.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Datum<'s> {
    /// An integer of up to 128 bits.
    Int(i128),
    /// An integer of more than 128 bits.
    Big(&'s BigInt),
    /// A float.
    Float(f64),
    /// An enumeration's value; its label is in the type.
    Enum(i128),
    /// A boolean.
    Bool(bool),
    /// A string.
    Str(&'s str),
    /// Binary data.
    Binary(&'s [u8]),
}

/// Why a packet couldn't be packed or unpacked.
#[derive(Debug, Clone, PartialEq)]
pub enum RunError {
    /// What decoding or encoding gave.
    Packet(CodecError),
    /// The packet uses something the codec doesn't run yet.
    Unsupported(String),
    /// A handle or store of another mission was given.
    Foreign,
}

impl fmt::Display for RunError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            RunError::Packet(e) => write!(f, "{}: {}", e.field, e.error),
            RunError::Unsupported(w) => write!(f, "not supported yet: {w}"),
            RunError::Foreign => {
                f.write_str("a handle or store of another mission")
            }
        }
    }
}

impl std::error::Error for RunError {}

/// Why a value can't be given to a slot.
#[derive(Debug, Clone, PartialEq)]
pub enum SetError {
    /// The value is of another kind than the slot's type.
    Kind {
        /// The slot's kind.
        want: String,
    },
    /// The value is outside the type's valid ranges, isn't one of an
    /// enumeration's values, or is longer than the slot holds.
    OutOfRange,
    /// The slot is another mission's.
    Foreign,
}

impl fmt::Display for SetError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            SetError::Kind { want } => {
                write!(f, "the slot holds {want} values")
            }
            SetError::OutOfRange => f.write_str("the value is out of range"),
            SetError::Foreign => f.write_str("a slot of another mission"),
        }
    }
}

impl std::error::Error for SetError {}

/// The current value of every slot of a mission, as a ground system's
/// table of current values holds them.
#[derive(Debug)]
pub struct Store {
    /// The number of the mission it belongs to.
    mission: u64,
    /// Each slot's type.
    types: Rc<Vec<Rc<VType>>>,
    /// Each slot's raw type.
    raw_types: Rc<Vec<Rc<VType>>>,
    /// The most bytes each string or binary slot holds.
    caps: Rc<Vec<usize>>,
    /// Each slot's value.
    values: Values,
    /// Each slot's raw value, where unpacking or `set_raw` gave one.
    raws: Values,
    /// Whether each slot's raw value is to be packed as it is, because
    /// `set_raw` gave it.
    raw_set: Vec<bool>,
    /// The storage packing reuses.
    work: RefCell<Workspace>,
}

/// A packet decoded and not yet committed: the values of the slots it
/// sets.
#[derive(Debug)]
pub struct Scratch {
    /// The number of the mission it belongs to.
    mission: u64,
    /// The storage decoding reuses.
    work: Workspace,
    /// The value of each slot the packet sets.
    values: Values,
    /// The raw value of each such slot.
    raws: Values,
    /// The slots the packet sets, in order.
    touched: Vec<usize>,
}

/// Gives the number of slots a value of a type has.
///
/// # Arguments
///
/// * `vt` - The type.
///
/// # Returns
///
/// One for a scalar, and the sum of its members' or elements' for an
/// aggregate or array.
fn slot_count(vt: &VType) -> usize {
    match &vt.enc {
        Encoding::Aggregate(e) => {
            e.members.iter().map(|m| slot_count(&m.vtype)).sum()
        }
        Encoding::Array(e) => array_len(e) * slot_count(&e.elem),
        _ => 1,
    }
}

/// Gives the most elements an array holds.
///
/// # Arguments
///
/// * `e` - The array's encoding.
///
/// # Returns
///
/// The product of its dimensions' sizes, or the most its count allows.
fn array_len(e: &tcs_xtce_model::model::ArrayEnc) -> usize {
    if !e.shape.is_empty() {
        e.shape.iter().product::<i64>() as usize
    } else {
        match &e.count {
            Count::Dyn(d) => d.max.max(0) as usize,
            Count::Int(n) => *n as usize,
        }
    }
}

/// Copies a value into storage that has the room for it, keeping the
/// storage's buffers.
///
/// # Arguments
///
/// * `dst` - The storage.
/// * `src` - The value.
fn assign(dst: &mut Val, src: &Val) {
    match (dst, src) {
        (Val::Str(a), Val::Str(b)) => {
            a.clear();
            a.push_str(b);
        }
        (Val::Bytes(a), Val::Bytes(b)) => {
            a.clear();
            a.extend_from_slice(b);
        }
        (Val::Big(a), Val::Big(b)) => a.clone_from(b),
        (Val::Agg(a), Val::Agg(b)) => {
            for (x, y) in a.iter_mut().zip(b) {
                assign(x, y);
            }
        }
        (Val::Arr(a, n), Val::Arr(b, m)) => {
            while a.len() < *m {
                a.push(b[a.len()].clone());
            }
            for (x, y) in a.iter_mut().zip(&b[..*m]) {
                assign(x, y);
            }
            *n = *m;
        }
        (d, s) => *d = s.clone(),
    }
}

/// Spreads a value of a packet over its slots.
///
/// # Arguments
///
/// * `vt`    - Its type.
/// * `v`     - The value, or `None` where it is absent.
/// * `slots` - The slots' storage.
/// * `at`    - The next slot to fill; it is moved past the value's.
fn scatter(vt: &VType, v: Option<&Val>, slots: &mut Values, at: &mut usize) {
    match (&vt.enc, v) {
        (Encoding::Aggregate(e), Some(Val::Agg(ms))) => {
            for (m, x) in e.members.iter().zip(ms) {
                scatter(&m.vtype, Some(x), slots, at);
            }
        }
        (Encoding::Array(e), Some(Val::Arr(xs, used))) => {
            for i in 0..array_len(e) {
                scatter(&e.elem, (i < *used).then(|| &xs[i]), slots, at);
            }
        }
        (Encoding::Aggregate(_) | Encoding::Array(_), _) => {
            for _ in 0..slot_count(vt) {
                slots.absent(*at as i64);
                *at += 1;
            }
        }
        (_, Some(x)) => {
            let mut s = slots.take(*at as i64);
            assign(&mut s, x);
            slots.put(*at as i64, s);
            *at += 1;
        }
        (_, None) => {
            slots.absent(*at as i64);
            *at += 1;
        }
    }
}

/// Builds a value of a packet from its slots, in its storage.
///
/// # Arguments
///
/// * `vt`    - Its type.
/// * `dst`   - Its storage.
/// * `store` - The current values.
/// * `at`    - The next slot to read; it is moved past the value's.
///
/// # Returns
///
/// Whether every slot it needs has a value.
fn gather(vt: &VType, dst: &mut Val, store: &Store, at: &mut usize) -> bool {
    match &vt.enc {
        Encoding::Aggregate(e) => {
            let Val::Agg(ms) = dst else {
                unreachable!("an aggregate")
            };
            let mut ok = true;
            for (m, x) in e.members.iter().zip(ms.iter_mut()) {
                ok &= gather(&m.vtype, x, store, at);
            }
            ok
        }
        Encoding::Array(e) => {
            let Val::Arr(es, used) = dst else {
                unreachable!("an array")
            };
            let n = slot_count(&e.elem);
            let fixed = matches!(e.count, Count::Int(_));
            let most = array_len(e);
            while es.len() < most {
                es.push(interp::template(&e.elem));
            }
            let mut got = 0;
            let mut ended = false;
            for x in es.iter_mut().take(most) {
                if ended {
                    *at += n;
                    continue;
                }
                let mut k = *at;
                if gather(&e.elem, x, store, &mut k) {
                    got += 1;
                } else if fixed {
                    *at += n * (most - got);
                    return false;
                } else {
                    // An array whose count varies ends at the first
                    // element without a value.
                    ended = true;
                }
                *at += n;
            }
            *used = got;
            true
        }
        _ => {
            let i = *at;
            *at += 1;
            if store.raw_set[i] {
                // A raw value set for a slot is packed as it is.
                match store.raws.val(i as i64) {
                    Some(Val::Int(x)) => *dst = Val::RawInt(*x),
                    Some(Val::Float(x)) => *dst = Val::RawFloat(*x),
                    Some(x) => assign(dst, x),
                    None => return false,
                }
                return true;
            }
            match store.values.val(i as i64) {
                Some(x) => {
                    if matches!(dst, Val::RawInt(_) | Val::RawFloat(_)) {
                        *dst = Val::Bool(false);
                    }
                    assign(dst, x);
                    true
                }
                None => false,
            }
        }
    }
}

/// Converts a value to a datum.
///
/// # Arguments
///
/// * `v` - The value.
///
/// # Returns
///
/// The datum.
fn to_datum(v: &Val) -> Datum<'_> {
    match v {
        Val::Int(i) | Val::RawInt(i) => Datum::Int(*i),
        Val::Big(b) => match b.to_i128() {
            Some(i) => Datum::Int(i),
            None => Datum::Big(b),
        },
        Val::Float(x) | Val::RawFloat(x) => Datum::Float(*x),
        Val::Enum(i) => Datum::Enum(*i as i128),
        Val::Bool(b) => Datum::Bool(*b),
        Val::Str(s) => Datum::Str(s),
        Val::Bytes(b) => Datum::Binary(b),
        Val::Agg(_) | Val::Arr(..) => unreachable!("a scalar slot"),
    }
}

/// Puts a datum into a slot's storage, checked against its kind and the
/// room it has.
///
/// # Arguments
///
/// * `vt`  - The slot's type.
/// * `cap` - The most bytes the slot holds, for a string or binary slot.
/// * `dst` - The slot's storage.
/// * `d`   - The datum.
///
/// # Errors
///
/// Kind, if the datum is of another kind than the type; OutOfRange, if it
/// is longer than the slot holds, or doesn't fit an enumeration.
fn put_datum(
    vt: &VType,
    cap: usize,
    dst: &mut Val,
    d: Datum<'_>,
) -> Result<(), SetError> {
    let kind = || SetError::Kind {
        want: vt.kind.clone(),
    };
    match (vt.kind.as_str(), d) {
        ("int", Datum::Int(i)) if vt.wide() => *dst = Val::Big(BigInt::from(i)),
        ("int", Datum::Big(b)) if vt.wide() => *dst = Val::Big(b.clone()),
        ("int", Datum::Int(i)) => *dst = Val::Int(i),
        ("enum", Datum::Enum(i)) if vt.wide() => {
            *dst = Val::Big(BigInt::from(i))
        }
        ("enum", Datum::Enum(i)) => {
            *dst =
                Val::Enum(i64::try_from(i).map_err(|_| SetError::OutOfRange)?)
        }
        ("float", Datum::Float(x)) => *dst = Val::Float(x),
        ("float", Datum::Int(i)) => *dst = Val::Float(i as f64),
        ("bool", Datum::Bool(b)) => *dst = Val::Bool(b),
        ("string", Datum::Str(s)) => {
            if s.len() > cap {
                return Err(SetError::OutOfRange);
            }
            match dst {
                Val::Str(d) => {
                    d.clear();
                    d.push_str(s);
                }
                _ => *dst = Val::Str(s.to_string()),
            }
        }
        ("binary", Datum::Binary(b)) => {
            if b.len() > cap {
                return Err(SetError::OutOfRange);
            }
            match dst {
                Val::Bytes(d) => {
                    d.clear();
                    d.extend_from_slice(b);
                }
                _ => *dst = Val::Bytes(b.to_vec()),
            }
        }
        _ => return Err(kind()),
    }
    Ok(())
}

impl Store {
    /// Gives a slot's current value.
    ///
    /// # Arguments
    ///
    /// * `slot` - The slot, which must be this store's mission's.
    ///
    /// # Returns
    ///
    /// The value, or `None` if nothing has set it.
    pub fn get(&self, slot: SlotId) -> Option<Datum<'_>> {
        if slot.mission != self.mission {
            return None;
        }
        self.values.val(slot.index as i64).map(to_datum)
    }

    /// Gives a slot a value, checked against its type as encoding checks
    /// it.
    ///
    /// # Arguments
    ///
    /// * `slot`  - The slot.
    /// * `value` - The engineering value.
    ///
    /// # Errors
    ///
    /// Kind, if the value is of another kind than the slot's type;
    /// OutOfRange, if it is outside the type's valid ranges, isn't one of
    /// an enumeration's values, or is longer than the slot holds;
    /// Foreign, for another mission's slot.
    pub fn set(
        &mut self,
        slot: SlotId,
        value: Datum<'_>,
    ) -> Result<(), SetError> {
        if slot.mission != self.mission {
            return Err(SetError::Foreign);
        }
        let i = slot.index;
        let vt = self.types[i].clone();
        // Checked before the slot changes.
        match value {
            Datum::Enum(x) => {
                if !vt
                    .labels
                    .iter()
                    .any(|l| (l.value as i128) <= x && x <= l.max_value as i128)
                {
                    return Err(SetError::OutOfRange);
                }
            }
            Datum::Int(x) if vt.kind == "int" && !vt.wide() => {
                interp::check_valid(&vt, &Val::Int(x))
                    .map_err(|_| SetError::OutOfRange)?;
            }
            Datum::Int(x) if vt.kind == "float" => {
                interp::check_valid(&vt, &Val::Float(x as f64))
                    .map_err(|_| SetError::OutOfRange)?;
            }
            Datum::Float(x) => {
                interp::check_valid(&vt, &Val::Float(x))
                    .map_err(|_| SetError::OutOfRange)?;
            }
            _ => {}
        }
        let mut x = self.values.take(i as i64);
        match put_datum(&vt, self.caps[i], &mut x, value) {
            Ok(()) => {
                if vt.wide() {
                    if let Err(_) = interp::check_valid(&vt, &x) {
                        self.values.restore(i as i64, x);
                        return Err(SetError::OutOfRange);
                    }
                }
                self.values.put(i as i64, x);
                self.raws.absent(i as i64);
                self.raw_set[i] = false;
                Ok(())
            }
            Err(e) => {
                let had = self.values.val(i as i64).is_some();
                if had {
                    self.values.put(i as i64, x);
                } else {
                    self.values.restore(i as i64, x);
                }
                Err(e)
            }
        }
    }

    /// Gives a slot's raw value: what the packet held, before its
    /// calibrator, for a calibrated slot; its value for any other.
    ///
    /// # Arguments
    ///
    /// * `slot` - The slot, which must be this store's mission's.
    ///
    /// # Returns
    ///
    /// The raw value, or `None` if nothing has set it.
    pub fn get_raw(&self, slot: SlotId) -> Option<Datum<'_>> {
        if slot.mission != self.mission {
            return None;
        }
        match self.raws.val(slot.index as i64) {
            Some(v) => Some(to_datum(v)),
            None => self.get(slot),
        }
    }

    /// Gives a slot a raw value, which packing writes as it is: it is
    /// not calibrated, and is checked against its encoding only, not the
    /// type's valid ranges, so that a test can send a value that no
    /// engineering value gives. The slot has no engineering value until
    /// one is set or unpacked.
    ///
    /// # Arguments
    ///
    /// * `slot`  - The slot.
    /// * `value` - The raw value: an integer for an integer encoding, a
    ///             float for a float encoding.
    ///
    /// # Errors
    ///
    /// Kind, if the value is of another kind than the encoding's;
    /// OutOfRange, if it is longer than the slot holds; Foreign, for
    /// another mission's slot.
    pub fn set_raw(
        &mut self,
        slot: SlotId,
        value: Datum<'_>,
    ) -> Result<(), SetError> {
        if slot.mission != self.mission {
            return Err(SetError::Foreign);
        }
        let i = slot.index;
        let rt = self.raw_types[i].clone();
        let mut x = self.raws.take(i as i64);
        if let Err(e) = put_datum(&rt, self.caps[i], &mut x, value) {
            self.raws.restore(i as i64, x);
            return Err(e);
        }
        self.raws.put(i as i64, x);
        self.values.absent(i as i64);
        self.raw_set[i] = true;
        Ok(())
    }

    /// Takes a slot's value away.
    ///
    /// # Arguments
    ///
    /// * `slot` - The slot, which must be this store's mission's.
    pub fn clear(&mut self, slot: SlotId) {
        if slot.mission == self.mission {
            self.values.absent(slot.index as i64);
            self.raws.absent(slot.index as i64);
            self.raw_set[slot.index] = false;
        }
    }

    /// Makes the values a packet decoded into a scratch the current ones.
    ///
    /// # Arguments
    ///
    /// * `scratch` - What unpacking gave.
    ///
    /// # Errors
    ///
    /// Foreign, if the scratch is another mission's.
    pub fn commit(&mut self, scratch: &Scratch) -> Result<(), SetError> {
        if scratch.mission != self.mission {
            return Err(SetError::Foreign);
        }
        for &i in &scratch.touched {
            let id = i as i64;
            for (dst, src) in [
                (&mut self.values, &scratch.values),
                (&mut self.raws, &scratch.raws),
            ] {
                match src.val(id) {
                    Some(v) => {
                        let mut x = dst.take(id);
                        assign(&mut x, v);
                        dst.put(id, x);
                    }
                    None => dst.absent(id),
                }
            }
            self.raw_set[i] = false;
        }
        Ok(())
    }
}

impl Mission {
    /// Makes a store for the mission's slots, none of them with a value,
    /// with all the storage packing them needs.
    ///
    /// # Returns
    ///
    /// The store.
    pub fn store(&self) -> Store {
        Store {
            mission: self.id,
            types: self.slot_types.clone(),
            raw_types: self.slot_raw_types.clone(),
            caps: self.slot_caps.clone(),
            values: Values::like(&self.slot_templates),
            raws: Values::like(&self.slot_raw_templates),
            raw_set: vec![false; self.slots.len()],
            work: RefCell::new(Workspace::new(&self.plan)),
        }
    }

    /// Makes a scratch for unpacking packets into, with all the storage
    /// unpacking needs.
    ///
    /// # Returns
    ///
    /// The scratch.
    pub fn scratch(&self) -> Scratch {
        Scratch {
            mission: self.id,
            work: Workspace::new(&self.plan),
            values: Values::like(&self.slot_templates),
            raws: Values::like(&self.slot_raw_templates),
            touched: Vec::with_capacity(self.slots.len()),
        }
    }

    /// Lists what each container and meta-command uses that the codec
    /// doesn't run yet, so that a program can refuse a mission before it
    /// starts.
    ///
    /// # Returns
    ///
    /// For each concrete node that can't be packed or unpacked, its path
    /// and what it uses.
    pub fn unsupported(&self) -> Vec<String> {
        self.nodes
            .iter()
            .filter(|n| !n.node.get().abstract_ && n.node.get().error.is_none())
            .filter_map(|n| self.plan.unsupported(&n.node).map(String::from))
            .collect()
    }

    /// Finds the handle of a node.
    ///
    /// # Arguments
    ///
    /// * `n` - The node.
    ///
    /// # Returns
    ///
    /// Its handle.
    fn node_id(&self, n: &Shared<Node>) -> NodeId {
        let index = self
            .nodes
            .iter()
            .position(|e| e.node.same(n))
            .expect("a node of the mission");
        NodeId {
            mission: self.id,
            index,
        }
    }

    /// Unpacks a packet of a container or meta-command into a scratch;
    /// for an abstract one, the packet is classified as the first of
    /// those derived from it whose criteria match. Nothing is set unless
    /// the whole packet decodes: commit the scratch to a store to set the
    /// values.
    ///
    /// # Arguments
    ///
    /// * `node`    - The container or meta-command.
    /// * `bytes`   - The packet.
    /// * `scratch` - Where its values go; what it held before is replaced.
    ///
    /// # Returns
    ///
    /// The concrete container or meta-command it is.
    ///
    /// # Errors
    ///
    /// Packet, if it doesn't decode; Unsupported, if it is of a kind the
    /// codec doesn't run yet; Foreign, for another mission's node or
    /// scratch.
    pub fn unpack(
        &self,
        node: NodeId,
        bytes: &[u8],
        scratch: &mut Scratch,
    ) -> Result<NodeId, RunError> {
        if node.mission != self.id || scratch.mission != self.id {
            return Err(RunError::Foreign);
        }
        scratch.touched.clear();
        let n = &self.nodes[node.index].node;
        let d = if n.get().abstract_ {
            match interp::classify(n, bytes, &self.plan, &mut scratch.work) {
                Ok(d) => d.map_err(RunError::Packet)?,
                Err(w) => return Err(RunError::Unsupported(w)),
            }
        } else {
            if let Some(w) = self.plan.unsupported(n) {
                return Err(RunError::Unsupported(w.to_string()));
            }
            interp::decode(n, bytes, &self.plan, &mut scratch.work)
                .map_err(RunError::Packet)?
        };
        let dn = d.node.get();
        for (_, l) in &dn.fields {
            let l = l.get();
            let Some(&(first, count)) = self.owner_slots.get(l.key.as_str())
            else {
                continue;
            };
            let vt = l.vt();
            if slot_count(&vt) != count {
                continue;
            }
            // Absent: it keeps the value it had.
            let Some(v) = scratch.work.vals.val(l.id) else {
                continue;
            };
            let mut at = first;
            scatter(&vt, Some(v), &mut scratch.values, &mut at);
            let mut at = first;
            match scratch.work.raws.val(l.id) {
                Some(r) => {
                    let rt = interp::raw_type_of(&self.plan, l.id)
                        .expect("a raw type");
                    scatter(&rt, Some(r), &mut scratch.raws, &mut at);
                }
                None => {
                    for i in first..first + count {
                        scratch.raws.absent(i as i64);
                    }
                }
            }
            scratch.touched.extend(first..first + count);
        }
        drop(dn);
        Ok(self.node_id(&d.node))
    }

    /// Packs a packet of a concrete container or meta-command from the
    /// current values of its slots. What XTCE fixes -- restriction
    /// criteria, argument assignments, fixed values, length fields and
    /// checksums -- the codec writes.
    ///
    /// # Arguments
    ///
    /// * `node`   - The container or meta-command.
    /// * `values` - The current values.
    /// * `out`    - The buffer the bytes go to.
    ///
    /// # Returns
    ///
    /// The bytes.
    ///
    /// # Errors
    ///
    /// Packet, if a value is missing or doesn't fit, or the criteria
    /// don't hold; Unsupported, if the node is abstract or of a kind the
    /// codec doesn't run yet; Foreign, for another mission's node or
    /// store.
    pub fn pack<'b>(
        &self,
        node: NodeId,
        values: &Store,
        out: &'b mut Buffer,
    ) -> Result<&'b [u8], RunError> {
        if node.mission != self.id || values.mission != self.id {
            return Err(RunError::Foreign);
        }
        let n = &self.nodes[node.index].node;
        if n.get().abstract_ {
            return Err(RunError::Unsupported(format!(
                "{}: an abstract container or meta-command",
                n.get().key
            )));
        }
        if let Some(w) = self.plan.unsupported(n) {
            return Err(RunError::Unsupported(w.to_string()));
        }
        let mut ws = values.work.borrow_mut();
        ws.vals.clear();
        for (_, l) in &n.get().fields {
            let l = l.get();
            let Some(&(first, count)) = self.owner_slots.get(l.key.as_str())
            else {
                continue;
            };
            let vt = l.vt();
            if slot_count(&vt) != count {
                continue;
            }
            let mut x = ws.vals.take(l.id);
            let mut at = first;
            if gather(&vt, &mut x, values, &mut at) {
                ws.vals.put(l.id, x);
            } else {
                ws.vals.restore(l.id, x);
            }
        }
        interp::encode(n, &self.plan, &mut ws, out).map_err(RunError::Packet)
    }
}
