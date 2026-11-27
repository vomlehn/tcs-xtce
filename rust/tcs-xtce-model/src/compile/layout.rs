//! Where items are and how many bits they take, as the Python compiler's
//! `fixed_bits`, `static_end`, `max_bits` and their kin measure them.

use std::collections::HashMap;

use super::types::item_bits;
use crate::model::{
    unsupported, Count, Encoding, Item, Result, Span, Term, VType, Value,
};

/// Finds the bits a value of a type always occupies.
///
/// # Arguments
///
/// * `vt` - The type.
///
/// # Returns
///
/// The bits, or `None` if it varies.
pub fn fixed_bits(vt: &VType) -> Option<i64> {
    match &vt.enc {
        Encoding::Array(e) => {
            let b = fixed_bits(&e.elem)?;
            let Count::Int(n) = e.count else {
                return None;
            };
            if matches!(e.gap, Count::Dyn(_)) && n > 1 {
                return None;
            }
            Some(n * b + (n - 1).max(0) * e.gap.max_total())
        }
        Encoding::Aggregate(e) => match &e.transform {
            Some(t) => Some(t.bits()),
            None => {
                let mut sum = 0;
                for m in &e.members {
                    sum += fixed_bits(&m.vtype)? + m.skip;
                }
                Some(sum + e.pad)
            }
        },
        Encoding::String(e) => {
            if e.dyn_.is_none() {
                e.fixed_bits
            } else {
                None
            }
        }
        Encoding::Binary(e) => {
            if e.dyn_.is_some() || e.rest || e.embedded {
                None
            } else {
                Some(e.bits)
            }
        }
        Encoding::Xform(e) => e.raw.map(|_| e.bits()),
        e => Some(e.bits()),
    }
}

/// Gives the bits an item takes itself, for those whose size the Python
/// reads as `it.bits`.
///
/// # Arguments
///
/// * `it` - The item.
///
/// # Returns
///
/// Its bits: a Pad's, a Fixed's, a Segment's, a TailPad's, a
/// Transform's, a TailSeek's 7, and 0 for those that take none.
pub fn own_bits(it: &Item) -> i64 {
    match it {
        Item::Pad(p) => p.bits,
        Item::Fixed(f) => f.bits,
        Item::Segment(s) => s.get().bits,
        Item::TailPad(t) => t.bits,
        Item::TailSeek(_) => 7,
        Item::Transform(t) => t.get().bits(),
        Item::Require(_)
        | Item::Verify(_)
        | Item::LengthEnd(_)
        | Item::Fill(_) => 0,
        _ => panic!("own_bits of an item with no bits of its own"),
    }
}

/// Finds where a list of items ends, when that doesn't depend on the
/// data.
///
/// # Arguments
///
/// * `items` - The items.
/// * `start` - The id of the Mark they are measured from.
///
/// # Returns
///
/// (position, furthest bit) after them, in bits from the Mark, or `None`
/// when they depend on the data.
pub fn static_end(items: &[Item], start: i64) -> Option<(i64, i64)> {
    let (mut pos, mut extent) = (0i64, 0i64);
    let mut marks: HashMap<i64, i64> = HashMap::from([(start, 0)]);
    for it in items {
        match it {
            Item::Cond(_) | Item::TailSeek(_) => return None,
            Item::Transform(t) if t.get().raw.is_none() => return None,
            Item::Fill(f) => {
                let Count::Int(size) = f.size else {
                    return None;
                };
                pos = marks.get(&f.start)? + size;
            }
            Item::Seek(s) => {
                if s.varies() {
                    return None;
                }
                let Count::Int(off) = s.offset else {
                    return None;
                };
                pos = match s.from_start {
                    Some(m) => *marks.get(&m)?,
                    None => pos,
                } + off;
            }
            Item::Mark(m) => {
                marks.insert(m.id, pos);
            }
            Item::Value(v) => {
                let bits = if v.assembled != 0 {
                    0
                } else {
                    fixed_bits(&v.local.get().vt())?
                };
                pos += bits;
            }
            other => pos += own_bits(other),
        }
        extent = extent.max(pos);
    }
    Some((pos, extent))
}

/// Gives the criteria of a conditional block and the blocks inside it.
///
/// # Arguments
///
/// * `it` - The item.
///
/// # Returns
///
/// The terms, none for an item that isn't a Cond.
pub fn conds(it: &Item) -> Vec<Term> {
    let Item::Cond(c) = it else {
        return Vec::new();
    };
    let mut out = c.criteria.clone();
    for i in &c.items {
        out.extend(conds(i));
    }
    out
}

/// Measures the entries after a value.
///
/// # Arguments
///
/// * `items` - The items, as `walk(inner=false)` gives them.
/// * `it`    - The value.
///
/// # Returns
///
/// The bits of the entries after it, or `None` when it isn't among the
/// items or they vary.
pub fn tail_bits(items: &[Item], it: &Value) -> Option<i64> {
    let i = items
        .iter()
        .position(|x| matches!(x, Item::Value(v) if v.local.same(&it.local)))?;
    let mut bits = 0;
    for x in &items[i + 1..] {
        match x {
            Item::Require(_)
            | Item::Mark(_)
            | Item::Verify(_)
            | Item::LengthEnd(_) => {}
            Item::Segment(_) | Item::Pad(_) | Item::Fixed(_) => {
                bits += own_bits(x)
            }
            Item::Value(v) if !v.local.get().optional => {
                bits += if v.assembled != 0 {
                    0
                } else {
                    fixed_bits(&v.local.get().vt())?
                };
            }
            _ => return None,
        }
    }
    Some(bits)
}

/// Gives the bytes after a value that encoding it may use for a while.
///
/// # Arguments
///
/// * `vt` - The value's type.
///
/// # Returns
///
/// The bytes, for the value and those inside it.
pub fn work_bytes(vt: &VType) -> i64 {
    let inner = match &vt.enc {
        Encoding::Array(e) => work_bytes(&e.elem),
        Encoding::Aggregate(e) => e
            .members
            .iter()
            .map(|m| work_bytes(&m.vtype))
            .max()
            .unwrap_or(0),
        _ => 0,
    };
    match &vt.enc {
        Encoding::Xform(e) if !e.time => e.cap,
        Encoding::Aggregate(e) if e.transform.is_some() => {
            e.transform.as_ref().unwrap().cap + inner
        }
        _ => inner,
    }
}

/// Measures a value whose size varies by the values that give it, for a
/// location from an end.
///
/// # Arguments
///
/// * `vt` - The value's type, whose sizes are bound.
///
/// # Returns
///
/// The Span, or `None` for a size that only decoding the value tells.
pub fn span_of(vt: &VType) -> Option<Span> {
    match &vt.enc {
        Encoding::Array(e) => fixed_bits(&e.elem).map(|each| Span {
            count: e.count.clone(),
            each,
            gap: e.gap.clone(),
        }),
        Encoding::Binary(e) if !e.rest && !e.embedded => {
            e.dyn_.as_ref().map(|d| Span {
                count: Count::Dyn(d.clone()),
                each: 8,
                gap: Count::Int(0),
            })
        }
        Encoding::String(e) => e.dyn_.as_ref().map(|d| Span {
            count: Count::Dyn(d.clone()),
            each: 8,
            gap: Count::Int(0),
        }),
        _ => None,
    }
}

/// A position among items, or a list of alternatives, as `alternatives`
/// groups them.
#[derive(Debug, Clone)]
pub enum Alt {
    /// One item.
    One(Item),
    /// Alternatives: each a branch of items.
    Branches(Vec<Vec<Alt>>),
}

/// Groups the alternatives among items.
///
/// # Arguments
///
/// * `items` - The items.
///
/// # Returns
///
/// The items as `walk(inner=false)` gives them, but with each run of
/// Conds that are alternatives (the same nonzero group) as one list of
/// branches.
pub fn alternatives(items: &[Item]) -> Vec<Alt> {
    let mut out: Vec<Alt> = Vec::new();
    let mut group = 0;
    for it in items {
        if let Item::Cond(c) = it {
            if c.group != 0 {
                let branch = alternatives(&c.items);
                if c.group == group {
                    if let Some(Alt::Branches(b)) = out.last_mut() {
                        b.push(branch);
                    }
                } else {
                    out.push(Alt::Branches(vec![branch]));
                }
                group = c.group;
                continue;
            }
        }
        group = 0;
        match it {
            Item::Cond(c) => out.extend(alternatives(&c.items)),
            _ => out.push(Alt::One(it.clone())),
        }
    }
    out
}

/// Bounds the bits a packet occupies.
///
/// # Arguments
///
/// * `items` - Its items.
///
/// # Returns
///
/// The most bits it may occupy, and encoding may use while transforming
/// data.
pub fn max_bits(items: &[Item]) -> i64 {
    /// Where the walk is.
    struct State {
        /// The position.
        pos: i64,
        /// The furthest bit.
        extent: i64,
        /// Where each Mark is.
        marks: HashMap<i64, i64>,
    }
    /// Advances the state over items.
    ///
    /// # Arguments
    ///
    /// * `st`    - The state.
    /// * `items` - The items, as `alternatives` gives them.
    fn run(st: &mut State, items: &[Alt]) {
        for a in items {
            let it = match a {
                Alt::Branches(bs) => {
                    let start = st.pos;
                    let mut ends = Vec::new();
                    for b in bs {
                        st.pos = start;
                        run(st, b);
                        ends.push(st.pos);
                    }
                    st.pos = ends.into_iter().max().unwrap_or(start);
                    continue;
                }
                Alt::One(it) => it,
            };
            if let Item::Transform(t) = it {
                let t = t.get();
                st.extent = st.extent.max(st.pos + 8 * (t.cap + t.out_cap));
            }
            match it {
                Item::Seek(s) => {
                    st.pos = match s.from_start {
                        Some(m) => st.marks[&m],
                        None => st.pos,
                    } + s.offset.max_total();
                }
                Item::Fill(f) => {
                    st.pos = st.pos.max(st.marks[&f.start] + f.max_size());
                }
                Item::Mark(m) => {
                    st.marks.insert(m.id, st.pos);
                }
                Item::Value(v) => {
                    let vt = v.local.get().vt();
                    st.pos += if v.assembled != 0 { 0 } else { item_bits(&vt) };
                    st.extent = st.extent.max(st.pos + 8 * work_bytes(&vt));
                }
                other => st.pos += own_bits(other),
            }
            st.extent = st.extent.max(st.pos);
        }
    }
    let mut st = State {
        pos: 0,
        extent: 0,
        marks: HashMap::from([(0, 0)]),
    };
    run(&mut st, &alternatives(items));
    st.extent
}

/// Checks that transformed data starts on a byte boundary, as the
/// transform works on whole bytes.
///
/// # Arguments
///
/// * `items`  - A packet's items.
/// * `where_` - What it is, for error messages.
///
/// # Errors
///
/// Unsupported, if transformed data may not start on a byte boundary.
pub fn check_aligned(items: &[Item], where_: &str) -> Result<()> {
    let mut marks: HashMap<i64, Option<i64>> = HashMap::from([(0, Some(0))]);
    walk_aligned(items, Some(0), &mut marks, where_)?;
    Ok(())
}

/// Checks a list of items for `check_aligned`, and records where its
/// Marks are.
///
/// # Arguments
///
/// * `items`  - The items.
/// * `pos`    - Where they start, in bits after a byte boundary; `None` when
///              unknown.
/// * `marks`  - Where each Mark is, the same way.
/// * `where_` - What the packet is, for error messages.
///
/// # Returns
///
/// Where they end, the same way.
///
/// # Errors
///
/// Unsupported, if transformed data in them may not start on a byte
/// boundary.
fn walk_aligned(
    items: &[Item],
    mut pos: Option<i64>,
    marks: &mut HashMap<i64, Option<i64>>,
    where_: &str,
) -> Result<Option<i64>> {
    let m8 = |v: i64| v.rem_euclid(8);
    let (mut group, mut start) = (0i64, Some(0i64));
    for it in items {
        if let Item::Cond(c) = it {
            if c.group != 0 {
                // Alternatives each start where the first does.
                if c.group != group {
                    group = c.group;
                    start = pos;
                }
                let after = walk_aligned(&c.items, start, marks, where_)?;
                pos = if after == start { pos } else { None };
                continue;
            }
        }
        group = 0;
        match it {
            Item::Cond(c) => {
                let after = walk_aligned(&c.items, pos, marks, where_)?;
                pos = if after == pos { pos } else { None };
            }
            Item::Mark(m) => {
                marks.insert(m.id, pos);
            }
            Item::Seek(s) => {
                let base = match s.from_start {
                    None => pos,
                    Some(m) => marks.get(&m).copied().flatten(),
                };
                pos = match (base, &s.offset) {
                    (Some(b), Count::Int(off)) if !s.varies() => {
                        Some(m8(b + off - s.back))
                    }
                    _ => None,
                };
            }
            Item::Fill(f) => {
                let base = marks.get(&f.start).copied().flatten();
                pos = match (base, &f.size) {
                    (Some(b), Count::Int(size)) => Some(m8(b + size)),
                    _ => None,
                };
            }
            Item::Value(v) => {
                if v.assembled == 0 {
                    let l = v.local.get();
                    pos = advance(&l.vt(), pos, &l.name, where_)?;
                }
            }
            Item::EndSeek(_) | Item::TailSeek(_) => pos = None,
            Item::Transform(t) => {
                let t = t.get();
                if pos != Some(0) {
                    return unsupported(format!(
                        "{where_}: transformed data {} may not start on a \
                         byte boundary",
                        t.name
                    ));
                }
                walk_aligned(&t.items, Some(0), marks, where_)?;
                pos = if t.raw.is_some() { Some(0) } else { None };
            }
            other => pos = pos.map(|p| m8(p + own_bits(other))),
        }
    }
    Ok(pos)
}

/// Checks a value that may hold transformed data, for `check_aligned`.
///
/// # Arguments
///
/// * `vt`     - The value's type.
/// * `pos`    - Where it starts, in bits after a byte boundary; `None` when
///              unknown.
/// * `name`   - The value's name, for error messages.
/// * `where_` - What the packet is, for error messages.
///
/// # Returns
///
/// Where it ends, the same way.
///
/// # Errors
///
/// Unsupported, if transformed data in it may not start on a byte
/// boundary.
fn advance(
    vt: &VType,
    mut pos: Option<i64>,
    name: &str,
    where_: &str,
) -> Result<Option<i64>> {
    let m8 = |v: i64| v.rem_euclid(8);
    let refuse = || {
        unsupported(format!(
            "{where_}: transformed data {name} may not start on a byte \
             boundary"
        ))
    };
    match &vt.enc {
        Encoding::Aggregate(e) => {
            if e.transform.is_some() {
                if pos != Some(0) {
                    return refuse();
                }
                pos = Some(0);
            }
            for m in &e.members {
                pos = pos.map(|p| m8(p + m.skip));
                pos = advance(&m.vtype, pos, name, where_)?;
            }
            if e.transform.is_some() {
                return Ok(Some(0));
            }
            Ok(pos.map(|p| m8(p + e.pad)))
        }
        Encoding::Array(e) => {
            let gap = match e.gap {
                Count::Int(g) => Some(g),
                Count::Dyn(_) => None,
            };
            if let (Count::Int(n), Some(g)) = (&e.count, gap) {
                for _ in 0..*n {
                    pos = advance(&e.elem, pos, name, where_)?;
                    pos = pos.map(|p| m8(p + g));
                }
                return Ok(match pos {
                    Some(p) if *n != 0 => Some(m8(p - g)),
                    _ => None,
                });
            }
            // Elements that keep the alignment keep it however many there
            // are.
            let first = advance(&e.elem, pos, name, where_)?;
            let step = match (first, gap, pos) {
                (Some(f), Some(g), Some(p)) => Some(m8(f - p + g)),
                _ => None,
            };
            if step == Some(0) {
                return Ok(pos);
            }
            for t in tree(&e.elem) {
                let xf = matches!(t.enc, Encoding::Xform(_))
                    || matches!(&t.enc, Encoding::Aggregate(a)
                        if a.transform.is_some());
                if xf {
                    advance(&t, None, name, where_)?;
                }
            }
            Ok(None)
        }
        Encoding::Xform(_) => {
            if pos != Some(0) {
                return refuse();
            }
            Ok(Some(0))
        }
        e => {
            let bits = match fixed_bits(vt) {
                Some(b) => b,
                // Variable strings and binary data occupy whole bytes,
                // after a string's size tag.
                None => match e {
                    Encoding::String(s) => s.size_tag_bits.unwrap_or(0),
                    _ => 0,
                },
            };
            Ok(pos.map(|p| m8(p + bits)))
        }
    }
}

/// Lists a type and every type inside it.
///
/// # Arguments
///
/// * `vt` - The type.
///
/// # Returns
///
/// The types, inner types first and this one last.
pub fn tree(vt: &std::rc::Rc<VType>) -> Vec<std::rc::Rc<VType>> {
    let mut out = Vec::new();
    match &vt.enc {
        Encoding::Array(e) => out.extend(tree(&e.elem)),
        Encoding::Aggregate(e) => {
            for m in &e.members {
                out.extend(tree(&m.vtype));
            }
        }
        _ => {}
    }
    out.push(vt.clone());
    out
}
