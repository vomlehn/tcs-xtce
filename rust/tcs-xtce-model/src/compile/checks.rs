//! A packet's ErrorDetectCorrect checks, and the checks of what a packet
//! may hold, as the Python compiler's `check`, `node_checks`,
//! `check_rest`, `check_length` and their kin make them.

use num_bigint::BigInt;
use num_traits::ToPrimitive;
use tcs_xtce::generated as x;

use super::bind::plain_order;
use super::layout::{conds, fixed_bits, max_bits, tail_bits};
use super::types::{hex_bytes, is_rest};
use super::{child, qname, Bin, Compiler};

use crate::dump::{Mut, Shared};
use crate::model::{
    unsupported, walk, Check, Encoding, Item, Key, Local, Mark, Node, Result,
    Spec, Transform, VType,
};

/// Lists the ErrorDetectCorrect items of a container's BinaryEncoding
/// that name the parameter holding their value; the others only document
/// the container.
///
/// # Arguments
///
/// * `be` - The container's BinaryEncoding, or `None`.
///
/// # Returns
///
/// Each, with its element name.
pub fn edc_items(
    be: Bin<'_>,
) -> Vec<(&'static str, &x::ErrorDetectCorrectChoice)> {
    let Some(edc) = be.and_then(|b| b.error_detect_correct.as_ref()) else {
        return Vec::new();
    };
    edc.items
        .iter()
        .filter_map(|ch| {
            use x::ErrorDetectCorrectChoice as E;
            let (name, pref) = match ch {
                E::Checksum(c) => ("Checksum", &c.parameter_ref),
                E::CRC(c) => ("CRC", &c.parameter_ref),
                E::XOR(c) => ("XOR", &c.parameter_ref),
                E::Parity(c) => ("Parity", &c.parameter_ref),
            };
            pref.as_ref().map(|_| (name, ch))
        })
        .collect()
}

/// Reads a CRC parameter in hexadecimal.
///
/// # Arguments
///
/// * `h`      - The digits, if given.
/// * `mask`   - The width's mask.
/// * `where_` - Where they are, for error messages.
///
/// # Returns
///
/// The number, masked, or 0 when none is given.
///
/// # Errors
///
/// Unsupported, if they aren't hexadecimal.
fn hexnum(h: Option<&str>, mask: u64, where_: &str) -> Result<u64> {
    let Some(h) = h.filter(|h| !h.trim().is_empty()) else {
        return Ok(0);
    };
    let b = hex_bytes(h, where_)?;
    let n =
        BigInt::from_bytes_be(num_bigint::Sign::Plus, &b) & BigInt::from(mask);
    Ok(n.to_u64().expect("a masked number"))
}

impl<'a> Compiler<'a> {
    /// Compiles one ErrorDetectCorrect algorithm, and marks its field as a
    /// checksum.
    ///
    /// # Arguments
    ///
    /// * `name`      - The algorithm's element name.
    /// * `v`         - The XTCE algorithm.
    /// * `path`      - The key of the SpaceSystem it is written in.
    /// * `scope`     - The values it may be held in; the latest that is its
    ///                 parameter is used.
    /// * `start`     - The id of the Mark at the start of what it covers.
    /// * `end`       - The id of the Mark at the end, or `None` for the end of
    ///                 the packet.
    /// * `container` - The name of the container it belongs to.
    /// * `where_`    - What it is in, for error messages.
    ///
    /// # Returns
    ///
    /// The Check.
    ///
    /// # Errors
    ///
    /// Unsupported, if its field isn't in the scope, isn't a plain
    /// unsigned integer, has an IncludeCondition or is already computed,
    /// or a CRC isn't 1 to 64 bits.
    #[allow(clippy::too_many_arguments)]
    pub fn check(
        &mut self,
        name: &str,
        v: &x::ErrorDetectCorrectChoice,
        path: &Key,
        scope: &[Shared<Local>],
        start: i64,
        end: Option<i64>,
        container: &str,
        where_: &str,
    ) -> Result<Shared<Check>> {
        use x::ErrorDetectCorrectChoice as E;
        let (pref, bits_from, reference) = match v {
            E::Checksum(c) => {
                (&c.parameter_ref, c.bits_from_reference, c.reference)
            }
            E::CRC(c) => (&c.parameter_ref, c.bits_from_reference, c.reference),
            E::XOR(c) => (&c.parameter_ref, c.bits_from_reference, c.reference),
            E::Parity(c) => {
                (&c.parameter_ref, c.bits_from_reference, c.reference)
            }
        };
        let params = |k: &[String]| self.params.contains(k);
        let pkey = qname(&Self::resolve(
            pref.as_deref().expect("a parameter"),
            path,
            &params,
            "parameter",
        )?);
        let Some(local) =
            scope.iter().rev().find(|l| l.get().key == pkey).cloned()
        else {
            return unsupported(format!(
                "{where_}: the {name} value {pkey} is not in container \
                 {container}"
            ));
        };
        let vt = local.get().vt();
        let lname = local.get().name.clone();
        let plain = matches!(&vt.enc, Encoding::Int(e)
            if vt.kind == "int" && !e.signed() && !e.bcd()
                && vt.calibrator.is_none() && plain_order(e));
        if !plain {
            return unsupported(format!(
                "{where_}: checksum field {lname} must be an uncalibrated \
                 unsigned binary integer, big or little endian in whole bytes, \
                 most significant bit first"
            ));
        }
        if local.get().optional {
            return unsupported(format!(
                "{where_}: checksum field {lname} has an IncludeCondition"
            ));
        }
        if local.get().computed() {
            return unsupported(format!(
                "{where_}: checksum field {lname} is already a packet length \
                 or checksum field"
            ));
        }
        let spec = match v {
            E::CRC(c) => {
                let width = c.width;
                if !(1..=64).contains(&width) {
                    return unsupported(format!("{where_}: a {width}-bit CRC"));
                }
                let m = if width == 64 {
                    u64::MAX
                } else {
                    (1u64 << width) - 1
                };
                let lsb = c.direction
                    == Some(x::BitOrderType::LeastSignificantBitFirst);
                Spec::Crc(
                    width,
                    hexnum(Some(&c.polynomial), m, where_)?,
                    hexnum(c.init_remainder.as_deref(), m, where_)?,
                    hexnum(c.final_xor.as_deref(), m, where_)?,
                    c.reflect_data.unwrap_or(false) != lsb,
                    c.reflect_remainder.unwrap_or(false),
                )
            }
            E::Checksum(c) => {
                let algo = c.name.as_str();
                if algo == "custom" {
                    let n = match &c.input_algorithm {
                        Some(a) => a.name.clone(),
                        None => format!(
                            "{}Checksum",
                            pkey.rsplit('/').next().unwrap_or("")
                        ),
                    };
                    Spec::Custom(self.hook(
                        "checksum",
                        &qname(&child(path, &n)),
                        0,
                    ))
                } else {
                    let (head, digits) = ["sum", "fletcher"]
                        .iter()
                        .find_map(|p| algo.strip_prefix(p).map(|d| (*p, d)))
                        .unwrap_or(("", ""));
                    if !head.is_empty()
                        && !digits.is_empty()
                        && digits.chars().all(|c| c.is_ascii_digit())
                    {
                        Spec::Named(
                            head.into(),
                            Some(digits.parse().expect("digits")),
                        )
                    } else {
                        Spec::Named(algo.into(), None)
                    }
                }
            }
            E::XOR(_) => Spec::Xor(vt.enc.bits()),
            E::Parity(p) => Spec::Parity(p.type_.as_str() == "Odd"),
        };
        local.get_mut().checksum = true;
        Ok(Shared::new(Check {
            spec,
            local,
            start,
            end,
            skip: bits_from.unwrap_or(0),
            from_end: reference.map(|r| r.as_str()) == Some("end"),
            container: container.into(),
            stop: None,
            stop_back: 0,
            late: None,
            verified: false,
        }))
    }

    /// Compiles the ErrorDetectCorrect checks of a concrete packet: those
    /// of the containers in its inheritance chain, which cover the packet,
    /// and those of the containers it includes.
    ///
    /// # Arguments
    ///
    /// * `node`   - The packet.
    /// * `where_` - What it is, for error messages.
    ///
    /// # Returns
    ///
    /// The checks, in the order of their fields in the packet.
    ///
    /// # Errors
    ///
    /// Unsupported, if a checksum field is in transformed data and its
    /// check covers bytes that can't be computed first, or a criterion or
    /// assignment is on one.
    pub fn node_checks(
        &mut self,
        node: &Shared<Node>,
        where_: &str,
    ) -> Result<Vec<Shared<Check>>> {
        let mut out = Vec::new();
        let chain = node.chain();
        for (k, n) in chain.iter().enumerate() {
            let edc = self.node_edc.get(&n.addr()).cloned().flatten();
            let (sc, path) = match &edc {
                Some((be, p)) => (*be, p.clone()),
                None => (None, Key::new()),
            };
            // A check's field is in its container or one derived from it,
            // which all share the check.
            let below: Vec<Shared<Local>> = chain[k..]
                .iter()
                .flat_map(|m| walk(&m.get().items, true))
                .filter_map(|i| match i {
                    Item::Value(v) => Some(v.local),
                    _ => None,
                })
                .collect();
            let key = n.get().key.clone();
            for (name, v) in edc_items(sc) {
                out.push(
                    self.check(name, v, &path, &below, 0, None, &key, where_)?,
                );
            }
        }
        for n in &chain {
            let inc = self
                .node_included
                .get(&n.addr())
                .cloned()
                .unwrap_or_default();
            for i in inc {
                for (name, v) in edc_items(i.container) {
                    out.push(self.check(
                        name,
                        v,
                        &i.path,
                        &i.scope,
                        i.start,
                        Some(i.end),
                        &i.key,
                        where_,
                    )?);
                }
            }
        }
        for c in &out {
            self.early_check(node, c, &out, where_)?;
        }
        let items = node.all_items();
        let mut criteria = node.all_criteria();
        for it in &items {
            criteria.extend(conds(it));
        }
        let verified: Vec<Shared<Check>> = walk(&items, true)
            .into_iter()
            .filter_map(|i| match i {
                Item::Verify(v) => Some(v.check),
                _ => None,
            })
            .collect();
        for k in &verified {
            k.get_mut().verified = true;
        }
        let all: Vec<Shared<Check>> =
            out.iter().chain(&verified).cloned().collect();
        stale_checks(node, &all, where_)?;
        for c in &all {
            let cl = c.get().local.clone();
            if criteria
                .iter()
                .flat_map(|t| t.locals())
                .any(|x| x.same(&cl))
            {
                return unsupported(format!(
                    "{where_}: a criterion or assignment on checksum field {}",
                    cl.get().name
                ));
            }
        }
        let order: Vec<Shared<Local>> = walk(&node.all_items(), true)
            .into_iter()
            .filter_map(|i| match i {
                Item::Value(v) => Some(v.local),
                _ => None,
            })
            .collect();
        let pos = |c: &Shared<Check>| {
            let l = c.get().local.clone();
            // The last Value of the local, as the Python's dict keeps.
            order
                .iter()
                .rposition(|x| x.same(&l))
                .expect("a checked field")
        };
        out.sort_by_key(|c| pos(c));
        Ok(out)
    }

    /// Lets a check of a packet cover bytes outside the transformed data
    /// its field is in, where encoding can compute it before the
    /// transform, or after it, `late`.
    ///
    /// # Arguments
    ///
    /// * `node`   - The packet.
    /// * `c`      - The check; left alone unless its field is in transformed
    ///              data.
    /// * `checks` - The packet's checks.
    /// * `where_` - What the packet is, for error messages.
    ///
    /// # Errors
    ///
    /// Unsupported, if the container is conditional or follows the
    /// check's start, the check's bits run into it, or they hold a field
    /// that encoding fills in after the transform, or after the late
    /// check.
    fn early_check(
        &mut self,
        node: &Shared<Node>,
        c: &Shared<Check>,
        checks: &[Shared<Check>],
        where_: &str,
    ) -> Result<()> {
        let cl = c.get().local.clone();
        let holders: Vec<Mut<Transform>> = walk(&node.all_items(), true)
            .into_iter()
            .filter_map(|i| match i {
                Item::Transform(t) => Some(t),
                _ => None,
            })
            .filter(|t| {
                walk(&t.get().items, true)
                    .iter()
                    .any(|x| matches!(x, Item::Value(v) if v.local.same(&cl)))
            })
            .collect();
        let Some(outer) = holders.first().cloned() else {
            return Ok(());
        };
        let why = format!(
            "{where_}: checksum field {} is in transformed data, but its check \
             covers bytes outside it",
            cl.get().name
        );
        let is_outer =
            |it: &Item| matches!(it, Item::Transform(t) if t.same(&outer));
        let owner = node
            .chain()
            .into_iter()
            .find(|n| n.get().items.iter().any(is_outer));
        let Some(owner) = owner else {
            return unsupported(format!("{why} in a conditional container"));
        };
        let flat = walk(&node.all_items(), false);
        let at = flat.iter().position(is_outer).expect("the container");
        let mark_at = |mid: i64| {
            flat.iter()
                .position(|it| matches!(it, Item::Mark(m) if m.id == mid))
        };
        let (start, end_mark, skip, from_end) = {
            let c = c.get();
            (c.start, c.end, c.skip, c.from_end)
        };
        let first = if start == 0 { Some(0) } else { mark_at(start) };
        let Some(first) = first.filter(|f| *f <= at) else {
            return unsupported(format!("{why}, after it"));
        };
        let raw = outer.get().raw;
        let later: Vec<Shared<Local>> = checks
            .iter()
            .filter(|k| !k.same(c))
            .map(|k| k.get().local.clone())
            .collect();
        let in_later = |l: &Shared<Local>| later.iter().any(|x| x.same(l));
        let mut back = 0;
        if from_end {
            let end = match end_mark {
                None => Some(flat.len()),
                Some(e) => mark_at(e),
            };
            let bad = match (end, raw) {
                (Some(end), Some(raw)) => {
                    max_bits(&flat[at + 1..end]) != 0 || skip < 8 * raw
                }
                _ => true,
            };
            if bad {
                return unsupported(format!("{why}, which run into it"));
            }
            back = skip - 8 * raw.unwrap();
        } else if skip != 0 {
            // The bits start past the field, and so run into the
            // container, unless what comes before it is surely as long.
            let mut bits = 0;
            for it in &flat[first..at] {
                let b = match it {
                    Item::Mark(_)
                    | Item::Require(_)
                    | Item::Verify(_)
                    | Item::LengthEnd(_) => Some(0),
                    Item::Pad(p) => Some(p.bits),
                    Item::Fixed(f) => Some(f.bits),
                    Item::Segment(s) => Some(s.get().bits),
                    Item::Value(v) if !v.local.get().optional => {
                        fixed_bits(&v.local.get().vt())
                    }
                    Item::Transform(t) => t.get().raw.map(|r| 8 * r),
                    _ => None,
                };
                let Some(b) = b else {
                    return unsupported(format!(
                        "{why}, which may run into it"
                    ));
                };
                bits += b;
            }
            if bits < skip {
                match raw {
                    Some(r) if bits + 8 * r <= skip => {}
                    _ => {
                        return unsupported(format!("{why}, which run into it"))
                    }
                }
                if holders.len() > 1 {
                    return unsupported(format!(
                        "{why}, after it, in nested transformed data"
                    ));
                }
                let end = match end_mark {
                    None => flat.len(),
                    Some(e) => mark_at(e).unwrap_or(flat.len()),
                };
                for it in &flat[at + 1..end] {
                    if let Item::Value(v) = it {
                        if in_later(&v.local) {
                            return unsupported(format!(
                                "{why}, after it, among them {}, which \
                                 encoding fills in after it",
                                v.local.get().name
                            ));
                        }
                    }
                }
                c.get_mut().late = Some(outer.clone());
                outer.get_mut().keeps = true;
                return Ok(());
            }
        }
        let ended: Vec<Shared<Local>> = flat[..at]
            .iter()
            .filter_map(|i| match i {
                Item::LengthEnd(l) => Some(l.local.clone()),
                _ => None,
            })
            .collect();
        for it in &flat[first..at] {
            if let Item::Value(v) = it {
                let l = &v.local;
                let unended = l.get().length.is_some()
                    && !ended.iter().any(|e| e.same(l));
                if in_later(l) || unended {
                    return unsupported(format!(
                        "{why}, among them {}, which encoding fills in after \
                         the transform",
                        l.get().name
                    ));
                }
            }
        }
        let stop = self.next_id();
        {
            let mut c = c.get_mut();
            c.stop = Some(stop);
            c.stop_back = back;
        }
        {
            let mut o = owner.get_mut();
            let i = o.items.iter().position(is_outer).expect("the container");
            o.items.insert(i, Item::Mark(Mark { id: stop }));
        }
        holders.last().unwrap().get_mut().patches.push(c.clone());
        Ok(())
    }

    /// Checks that a value that takes the rest of the packet comes last,
    /// as must a transformed one that does, unless it is binary data with
    /// only entries of a fixed size after it: then it takes the rest but
    /// them, which its `tail` records.
    ///
    /// # Arguments
    ///
    /// * `node`   - The packet.
    /// * `where_` - What it is, for error messages.
    ///
    /// # Errors
    ///
    /// Unsupported, if such a value isn't last, or has entries of another
    /// size after it in another packet.
    pub fn check_rest(
        &mut self,
        node: &Shared<Node>,
        where_: &str,
    ) -> Result<()> {
        let top = node.all_items();
        let items = walk(&top, false);
        for (i, it) in items.iter().enumerate() {
            let (what, value) = match it {
                Item::Value(v) if is_rest(&v.local.get().vt()) => {
                    (v.local.get().name.clone(), Some(v))
                }
                Item::Transform(t) if t.get().raw.is_none() => {
                    (t.get().name.clone(), None)
                }
                _ => continue,
            };
            let mut tail = Some(0);
            let rest_only = items[i + 1..]
                .iter()
                .all(|x| matches!(x, Item::Require(_) | Item::Mark(_)));
            if !rest_only {
                tail = match value {
                    Some(v)
                        if matches!(
                            v.local.get().vt().enc,
                            Encoding::Binary(_)
                        ) =>
                    {
                        tail_bits(&top, v)
                    }
                    _ => None,
                };
                if tail.is_none() {
                    return unsupported(format!(
                        "{where_}: {what} takes the rest of the packet but is \
                         not last"
                    ));
                }
            }
            let Some(v) = value else {
                continue;
            };
            let tail = tail.unwrap();
            let local = v.local.clone();
            let vt = local.get().vt();
            let id = local.get().id;
            if let Encoding::Binary(b) = &vt.enc {
                if b.tail != tail {
                    if self.tailed.contains(&id) {
                        return unsupported(format!(
                            "{where_}: {what} takes the rest of the packet, \
                             with a different size of entries after it in \
                             another packet"
                        ));
                    }
                    let enc = Encoding::Binary(crate::model::BinaryEnc {
                        tail,
                        ..b.clone()
                    });
                    local.get_mut().vtype = Some(std::rc::Rc::new(VType {
                        enc,
                        ..(*vt).clone()
                    }));
                }
            }
            self.tailed.insert(id);
        }
        Ok(())
    }
}

/// Checks that no check computed before a late one covers the container
/// it transforms again.
///
/// # Arguments
///
/// * `node`   - The packet.
/// * `checks` - Every check of the packet.
/// * `where_` - What the packet is, for error messages.
///
/// # Errors
///
/// Unsupported, if one does.
fn stale_checks(
    node: &Shared<Node>,
    checks: &[Shared<Check>],
    where_: &str,
) -> Result<()> {
    let flat = walk(&node.all_items(), false);
    let mark_at = |mid: i64, default: usize| {
        flat.iter()
            .position(|it| matches!(it, Item::Mark(m) if m.id == mid))
            .unwrap_or(default)
    };
    for c in checks {
        let Some(late) = c.get().late.clone() else {
            continue;
        };
        let at = flat
            .iter()
            .position(|it| matches!(it, Item::Transform(t) if t.same(&late)))
            .expect("the late container");
        for k in checks {
            let kk = k.get();
            if k.same(c)
                || (kk.late.is_none() && kk.stop.is_none() && !kk.verified)
            {
                continue;
            }
            let first = if kk.start == 0 {
                0
            } else {
                mark_at(kk.start, flat.len())
            };
            let end = match (kk.stop, kk.end) {
                (Some(s), _) => mark_at(s, flat.len()),
                (None, None) => flat.len(),
                (None, Some(e)) => mark_at(e, flat.len()),
            };
            if first <= at && at < end {
                let cn = c.get().local.get().name.clone();
                return unsupported(format!(
                    "{where_}: checksum field {cn}'s check is computed once \
                     the packet is written, after {}'s, which covers the \
                     transformed data {cn} is in",
                    kk.local.get().name
                ));
            }
        }
    }
    Ok(())
}

/// Checks that a packet has at most one length field outside transformed
/// data, and that each is always present and nothing compares or takes a
/// size from it.
///
/// # Arguments
///
/// * `node`   - The packet.
/// * `where_` - What it is, for error messages.
///
/// # Errors
///
/// Unsupported, if it doesn't.
pub fn check_length(node: &Shared<Node>, where_: &str) -> Result<()> {
    let items = node.all_items();
    let is_length = |it: &Item| {
        matches!(it, Item::Value(v)
            if v.local.get().length.is_some() && !v.local.get().measures)
    };
    if walk(&items, false).iter().filter(|i| is_length(i)).count() > 1 {
        return unsupported(format!(
            "{where_}: more than one packet length field"
        ));
    }
    let mut criteria = node.all_criteria();
    for it in &items {
        criteria.extend(conds(it));
    }
    for it in walk(&items, true).iter().filter(|i| is_length(i)) {
        let Item::Value(v) = it else {
            unreachable!("a value")
        };
        let lc = &v.local;
        if lc.get().optional {
            return unsupported(format!(
                "{where_}: packet length field {} has an IncludeCondition",
                lc.get().name
            ));
        }
        if criteria.iter().flat_map(|c| c.locals()).any(|x| x.same(lc)) {
            return unsupported(format!(
                "{where_}: a criterion or assignment on packet length field {}",
                lc.get().name
            ));
        }
    }
    Ok(())
}
