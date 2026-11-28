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

use std::fmt;
use std::rc::Rc;

use num_bigint::BigInt;
use num_traits::ToPrimitive;
use tcs_xtce::buffer::Buffer;
use tcs_xtce_model::dump::Shared;
use tcs_xtce_model::model::{Count, Encoding, Node, VType};

use crate::interp::{self, CodecError, Val};
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
    /// The value is outside the type's valid ranges, or isn't one of an
    /// enumeration's values.
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
#[derive(Debug, Clone)]
pub struct Store {
    /// The number of the mission it belongs to.
    mission: u64,
    /// Each slot's type.
    types: Rc<Vec<Rc<VType>>>,
    /// Each slot's value, or `None` if it has none.
    values: Vec<Option<Val>>,
    /// Each slot's raw value, where unpacking or `set_raw` gave one.
    raws: Vec<Option<Val>>,
    /// Whether each slot's raw value is to be packed as it is, because
    /// `set_raw` gave it.
    raw_set: Vec<bool>,
}

/// A packet decoded and not yet committed: the values of the slots it
/// sets.
#[derive(Debug, Clone, Default)]
pub struct Scratch {
    /// The number of the mission it belongs to.
    mission: u64,
    /// The slots set, each with its value, or `None` for one the packet
    /// leaves without a value, and its raw value where its type is
    /// calibrated.
    set: Vec<(usize, Option<Val>, Option<Val>)>,
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
            Count::Dyn(d) => d.max as usize,
            Count::Int(n) => *n as usize,
        }
    }
}

/// Spreads a value over its slots.
///
/// # Arguments
///
/// * `vt`  - Its type.
/// * `v`   - The value, or `None` if it is absent.
/// * `out` - Where each slot's value goes, in order.
fn flatten(vt: &VType, v: Option<&Val>, out: &mut Vec<Option<Val>>) {
    match (&vt.enc, v) {
        (Encoding::Aggregate(e), Some(Val::Agg(ms))) => {
            for (m, x) in e.members.iter().zip(ms) {
                flatten(&m.vtype, Some(x), out);
            }
        }
        (Encoding::Array(e), Some(Val::Arr(xs))) => {
            for i in 0..array_len(e) {
                flatten(&e.elem, xs.get(i), out);
            }
        }
        (Encoding::Aggregate(_) | Encoding::Array(_), _) => {
            out.extend(std::iter::repeat_n(None, slot_count(vt)));
        }
        (_, v) => out.push(v.cloned()),
    }
}

/// Builds a value from its slots.
///
/// # Arguments
///
/// * `vt`    - Its type.
/// * `slots` - Its slots' values, in order.
///
/// # Returns
///
/// The value, or `None` if a slot it needs has none.
fn gather(vt: &VType, slots: &[Option<Val>]) -> Option<Val> {
    match &vt.enc {
        Encoding::Aggregate(e) => {
            let mut out = Vec::new();
            let mut at = 0;
            for m in &e.members {
                let n = slot_count(&m.vtype);
                out.push(gather(&m.vtype, &slots[at..at + n])?);
                at += n;
            }
            Some(Val::Agg(out))
        }
        Encoding::Array(e) => {
            let n = slot_count(&e.elem);
            let fixed = matches!(e.count, Count::Int(_));
            let mut out = Vec::new();
            for i in 0..array_len(e) {
                match gather(&e.elem, &slots[i * n..(i + 1) * n]) {
                    Some(x) => out.push(x),
                    // An array whose count varies ends at the first
                    // element without a value.
                    None if !fixed => break,
                    None => return None,
                }
            }
            Some(Val::Arr(out))
        }
        _ => slots[0].clone(),
    }
}

/// Converts a datum to a value of a type.
///
/// # Arguments
///
/// * `vt` - The type.
/// * `d`  - The datum.
///
/// # Returns
///
/// The value.
///
/// # Errors
///
/// Kind, if the datum is of another kind than the type.
fn to_val(vt: &VType, d: Datum<'_>) -> Result<Val, SetError> {
    let kind = || SetError::Kind {
        want: vt.kind.clone(),
    };
    Ok(match (vt.kind.as_str(), d) {
        ("int", Datum::Int(i)) if vt.wide() => Val::Big(BigInt::from(i)),
        ("int", Datum::Big(b)) if vt.wide() => Val::Big(b.clone()),
        ("int", Datum::Int(i)) => Val::Int(i),
        ("enum", Datum::Enum(i)) if vt.wide() => Val::Big(BigInt::from(i)),
        ("enum", Datum::Enum(i)) => {
            Val::Enum(i64::try_from(i).map_err(|_| SetError::OutOfRange)?)
        }
        ("float", Datum::Float(x)) => Val::Float(x),
        ("float", Datum::Int(i)) => Val::Float(i as f64),
        ("bool", Datum::Bool(b)) => Val::Bool(b),
        ("string", Datum::Str(s)) => Val::Str(s.to_string()),
        ("binary", Datum::Binary(b)) => Val::Bytes(b.to_vec()),
        _ => return Err(kind()),
    })
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
        Val::Int(i) => Datum::Int(*i),
        Val::Big(b) => match b.to_i128() {
            Some(i) => Datum::Int(i),
            None => Datum::Big(b),
        },
        Val::Float(x) => Datum::Float(*x),
        Val::Enum(i) => Datum::Enum(*i as i128),
        Val::Bool(b) => Datum::Bool(*b),
        Val::Str(s) => Datum::Str(s),
        Val::Bytes(b) => Datum::Binary(b),
        Val::Raw(x) => to_datum(x),
        Val::Agg(_) | Val::Arr(_) => unreachable!("a scalar slot"),
    }
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
        self.values[slot.index].as_ref().map(to_datum)
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
    /// OutOfRange, if it is outside the type's valid ranges or isn't one
    /// of an enumeration's values; Foreign, for another mission's slot.
    pub fn set(
        &mut self,
        slot: SlotId,
        value: Datum<'_>,
    ) -> Result<(), SetError> {
        if slot.mission != self.mission {
            return Err(SetError::Foreign);
        }
        let vt = &self.types[slot.index];
        let v = to_val(vt, value)?;
        if let Val::Enum(x) = &v {
            if !vt.labels.iter().any(|l| l.value <= *x && *x <= l.max_value) {
                return Err(SetError::OutOfRange);
            }
        }
        interp::check_valid(vt, &v).map_err(|_| SetError::OutOfRange)?;
        self.values[slot.index] = Some(v);
        self.raws[slot.index] = None;
        self.raw_set[slot.index] = false;
        Ok(())
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
        match &self.raws[slot.index] {
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
    /// Foreign, for another mission's slot.
    pub fn set_raw(
        &mut self,
        slot: SlotId,
        value: Datum<'_>,
    ) -> Result<(), SetError> {
        if slot.mission != self.mission {
            return Err(SetError::Foreign);
        }
        let rt = interp::raw_type(&self.types[slot.index]);
        let v = to_val(&rt, value)?;
        self.values[slot.index] = None;
        self.raws[slot.index] = Some(v);
        self.raw_set[slot.index] = true;
        Ok(())
    }

    /// Takes a slot's value away.
    ///
    /// # Arguments
    ///
    /// * `slot` - The slot, which must be this store's mission's.
    pub fn clear(&mut self, slot: SlotId) {
        if slot.mission == self.mission {
            self.values[slot.index] = None;
            self.raws[slot.index] = None;
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
        for (i, v, raw) in &scratch.set {
            self.values[*i] = v.clone();
            self.raws[*i] = raw.clone();
            self.raw_set[*i] = false;
        }
        Ok(())
    }
}

impl Mission {
    /// Makes a store for the mission's slots, none of them with a value.
    ///
    /// # Returns
    ///
    /// The store.
    pub fn store(&self) -> Store {
        Store {
            mission: self.id,
            types: Rc::new(self.slots.iter().map(|s| s.vt.clone()).collect()),
            values: vec![None; self.slots.len()],
            raws: vec![None; self.slots.len()],
            raw_set: vec![false; self.slots.len()],
        }
    }

    /// Makes a scratch for unpacking packets into.
    ///
    /// # Returns
    ///
    /// The scratch.
    pub fn scratch(&self) -> Scratch {
        Scratch {
            mission: self.id,
            set: Vec::new(),
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
            .filter_map(|n| interp::unsupported(&n.node))
            .collect()
    }

    /// Gives the slots of a parameter or argument.
    ///
    /// # Arguments
    ///
    /// * `key` - Its key.
    ///
    /// # Returns
    ///
    /// The first of its slots and how many there are, if it has any.
    fn owner_slots(&self, key: &str) -> Option<(usize, usize)> {
        let o = self
            .params
            .iter()
            .chain(self.args.iter())
            .find(|o| o.key == key)?;
        let s = o.slots.as_ref().ok()?;
        Some((*s.first()?, s.len()))
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
        let n = &self.nodes[node.index].node;
        let d = if n.get().abstract_ {
            match interp::classify(n, bytes) {
                Ok(d) => d.map_err(RunError::Packet)?,
                Err(w) => return Err(RunError::Unsupported(w)),
            }
        } else {
            if let Some(w) = interp::unsupported(n) {
                return Err(RunError::Unsupported(w));
            }
            interp::decode(n, bytes).map_err(RunError::Packet)?
        };
        scratch.set.clear();
        let fields = d.node.get().fields.clone();
        for (((_, l), v), raw) in fields.iter().zip(&d.fields).zip(&d.raws) {
            let l = l.get();
            let Some((first, count)) = self.owner_slots(&l.key) else {
                continue;
            };
            if slot_count(&l.vt()) != count {
                continue;
            }
            // Absent: it keeps the value it had.
            let Some(v) = v else { continue };
            let mut out = Vec::with_capacity(count);
            flatten(&l.vt(), Some(v), &mut out);
            let mut raws = Vec::with_capacity(count);
            match raw {
                Some(r) => {
                    flatten(&interp::raw_type(&l.vt()), Some(r), &mut raws)
                }
                None => raws.resize(count, None),
            }
            for (k, (x, r)) in out.into_iter().zip(raws).enumerate().take(count)
            {
                scratch.set.push((first + k, x, r));
            }
        }
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
        if let Some(w) = interp::unsupported(n) {
            return Err(RunError::Unsupported(w));
        }
        let fields: Vec<Option<Val>> = n
            .get()
            .fields
            .iter()
            .map(|(_, l)| {
                let l = l.get();
                let (first, count) = self.owner_slots(&l.key)?;
                if slot_count(&l.vt()) != count {
                    return None;
                }
                // A raw value set for a slot is packed as it is.
                let slots: Vec<Option<Val>> = (first..first + count)
                    .map(|i| {
                        if values.raw_set[i] {
                            values.raws[i]
                                .clone()
                                .map(|x| Val::Raw(Box::new(x)))
                        } else {
                            values.values[i].clone()
                        }
                    })
                    .collect();
                gather(&l.vt(), &slots)
            })
            .collect();
        interp::encode(n, &fields, out).map_err(RunError::Packet)
    }
}
