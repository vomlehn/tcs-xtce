//! Telemetry containers: their entries compiled into items, the
//! containers they include and repeat, transforms, and the nodes of the
//! inheritance tree, as the Python compiler's `tm_items` and `tm_node` and
//! what they call make them.

use std::collections::HashMap;
use std::rc::Rc;

use tcs_xtce::generated as x;

use super::bind::RepeatRef;
use super::checks::{check_length, edc_items};
use super::criteria::{ArgKey, CritRef, XRef};
use super::layout::{check_aligned, fixed_bits, max_bits, span_of, static_end};
use super::types::{dyn_units, int_bounds, is_rest, IntValue, IntValueRef};
use super::{qname, Compiler, Included};
use crate::dump::{Mut, Shared};
use crate::model::{
    unsupported, walk, AggregateEnc, AnyOf, BinaryEnc, Cond, Count, Criterion,
    DynSize, Encoding, EndSeek, Error, Fill, IntEnc, Item, Key, LengthEnd,
    Local, Mark, Member, Node, Pad, PathStep, Require, Result, Seek, Segment,
    TailPad, TailSeek, Term, Transform, VType, Value, Verify, XformEnc,
};
use crate::names::{snake, Namer};

/// One segment of a value an entry list holds in segments: the segment,
/// its order attribute, and which segment of the value it is.
type Part = (Mut<Segment>, Option<i64>, i64);

/// The segments of an entry list, as `one_segment` collects them.
#[derive(Default)]
pub struct Segs {
    /// How many segments of each thing there have been.
    counts: HashMap<String, i64>,
    /// The values still incomplete, in the order first seen: each one's
    /// Local, the bits of its encoding, and its segments.
    groups: Vec<(String, Shared<Local>, Option<i64>, Vec<Part>)>,
}

/// The names of the algorithms that transform the bits of a container's
/// BinaryEncoding.
///
/// # Arguments
///
/// * `be` - The BinaryEncoding.
///
/// # Returns
///
/// The from-binary and to-binary names, either `None` if missing, or
/// `None` if it has neither.
pub fn container_transform(
    be: &x::ContainerBinaryDataEncodingType,
) -> Option<(Option<String>, Option<String>)> {
    let fa = be
        .from_binary_transform_algorithm
        .as_ref()
        .map(|a| a.name.clone());
    let ta = be
        .to_binary_transform_algorithm
        .as_ref()
        .map(|a| a.name.clone());
    if fa.is_none() && ta.is_none() {
        None
    } else {
        Some((fa, ta))
    }
}

/// Says whether a container's BinaryEncoding has transform algorithms.
///
/// # Arguments
///
/// * `sc` - The container.
///
/// # Returns
///
/// Whether it has.
pub fn has_transform(sc: &x::SequenceContainerType) -> bool {
    sc.binary_encoding
        .as_ref()
        .is_some_and(|b| container_transform(b).is_some())
}

/// Finds the value of a repeated segment entry whose size varies, for a
/// location from an end.
///
/// # Arguments
///
/// * `items` - The entry's items.
/// * `bits`  - The bits the entry takes, `None` when they vary.
///
/// # Returns
///
/// The repeated segments' Local, or `None` when the size is `bits`.
fn varying_segment(items: &[Item], bits: Option<i64>) -> Option<Shared<Local>> {
    if bits.is_some() {
        return None;
    }
    items.iter().find_map(|i| match i {
        Item::Value(v) => Some(v.local.clone()),
        _ => None,
    })
}

/// A segment entry, of any of the three kinds.
#[derive(Clone, Copy)]
pub enum SegRef<'b> {
    /// A ParameterSegmentRefEntry.
    Param(&'b x::ParameterSegmentRefEntryType),
    /// A ContainerSegmentRefEntry.
    Container(&'b x::ContainerSegmentRefEntryType),
    /// A StreamSegmentEntry.
    Stream(&'b x::StreamSegmentEntryType),
}

impl SegRef<'_> {
    /// Gives the entry's sizeInBits.
    ///
    /// # Returns
    ///
    /// It.
    fn size_in_bits(&self) -> i64 {
        match self {
            SegRef::Param(e) => e.size_in_bits,
            SegRef::Container(e) => e.size_in_bits,
            SegRef::Stream(e) => e.size_in_bits,
        }
    }

    /// Gives the entry's order.
    ///
    /// # Returns
    ///
    /// It, if given.
    fn order(&self) -> Option<i64> {
        match self {
            SegRef::Param(e) => e.order,
            SegRef::Container(e) => e.order,
            SegRef::Stream(e) => e.order,
        }
    }

    /// Gives the entry's RepeatEntry.
    ///
    /// # Returns
    ///
    /// It, if given.
    fn repeat(&self) -> Option<&x::RepeatType> {
        match self {
            SegRef::Param(e) => e.repeat_entry.as_ref(),
            SegRef::Container(e) => e.repeat_entry.as_ref(),
            SegRef::Stream(e) => e.repeat_entry.as_ref(),
        }
    }
}

/// What every kind of entry has.
struct Entry<'b> {
    /// Its location.
    loc: Option<&'b x::LocationInContainerInBitsType>,
    /// Its IncludeCondition.
    cond: Option<&'b x::MatchCriteriaType>,
}

/// Views an entry's common parts.
///
/// # Arguments
///
/// * `ch` - The entry.
///
/// # Returns
///
/// Its location and condition.
fn entry(ch: &x::EntryListChoice) -> Entry<'_> {
    use x::EntryListChoice as E;
    macro_rules! e {
        ($v:expr) => {
            Entry {
                loc: $v.location_in_container_in_bits.as_ref(),
                cond: $v.include_condition.as_ref(),
            }
        };
    }
    match ch {
        E::ParameterRefEntry(v) => e!(v),
        E::ParameterSegmentRefEntry(v) => e!(v),
        E::ContainerRefEntry(v) => e!(v),
        E::ContainerSegmentRefEntry(v) => e!(v),
        E::StreamSegmentEntry(v) => e!(v),
        E::IndirectParameterRefEntry(v) => e!(v),
        E::ArrayParameterRefEntry(v) => e!(v),
    }
}

impl<'a> Compiler<'a> {
    /// Resolves a reference to a container.
    ///
    /// # Arguments
    ///
    /// * `ref_` - The reference.
    /// * `path` - The key of the SpaceSystem it is written in.
    ///
    /// # Returns
    ///
    /// The container's key.
    ///
    /// # Errors
    ///
    /// Unsupported, if it names none.
    pub fn container_key(&self, ref_: &str, path: &[String]) -> Result<Key> {
        let has = |k: &[String]| self.containers.contains(k);
        Self::resolve(ref_, path, &has, "container")
    }

    /// Compiles a container's entries into items.
    ///
    /// # Arguments
    ///
    /// * `sc`          - The XTCE container.
    /// * `path`        - The key of the SpaceSystem it is defined in.
    /// * `stack`       - The keys of the containers being compiled, the
    ///                   outermost first and this one's last.
    /// * `start`       - The id of the Mark at the container's start.
    /// * `scope`       - The values read so far; grows as entries are added.
    /// * `conditional` - Whether the container is inside an IncludeCondition.
    ///
    /// # Returns
    ///
    /// The items.
    ///
    /// # Errors
    ///
    /// Unsupported, if an entry uses something not supported; Cycle, if a
    /// container includes itself.
    pub fn tm_items(
        &mut self,
        sc: &'a x::SequenceContainerType,
        path: &Key,
        stack: &[Key],
        start: i64,
        scope: &mut Vec<Shared<Local>>,
        conditional: bool,
    ) -> Result<Vec<Item>> {
        use x::EntryListChoice as E;
        let mut items: Vec<Item> = Vec::new();
        let mut segs = Segs::default();
        let where_ = format!("container {}", qname(stack.last().unwrap()));
        for ch in &sc.entry_list.items {
            let en = entry(ch);
            if let E::ContainerRefEntry(_) = ch {
                if en
                    .loc
                    .and_then(|l| l.reference_location)
                    .is_some_and(|r| r.as_str() == "containerEnd")
                {
                    return unsupported(format!(
                        "{where_}: an included container located from the \
                         container's end"
                    ));
                }
            }
            let cond = match en.cond {
                Some(c) => Some(self.match_(
                    CritRef::match_criteria(c),
                    path,
                    scope,
                    &where_,
                    None,
                )?),
                None => None,
            };
            let loc =
                self.location(en.loc, start, path, scope, &where_, None)?;
            let mut out = Self::next_seek(&mut items, loc, &where_)?;
            match ch {
                E::ParameterSegmentRefEntry(_)
                | E::ContainerSegmentRefEntry(_)
                | E::StreamSegmentEntry(_) => {
                    let seg = match ch {
                        E::ParameterSegmentRefEntry(e) => SegRef::Param(e),
                        E::ContainerSegmentRefEntry(e) => SegRef::Container(e),
                        E::StreamSegmentEntry(e) => SegRef::Stream(e),
                        _ => unreachable!("a segment"),
                    };
                    let es = match out.last() {
                        Some(Item::EndSeek(es)) => Some(es.clone()),
                        _ => None,
                    };
                    let before = scope.clone();
                    let got = self.segment(
                        seg,
                        path,
                        &where_,
                        &mut segs,
                        cond.is_some(),
                        scope,
                        None,
                    )?;
                    let mut after = Vec::new();
                    if let Some(es) = es {
                        let bits = self.segment_bits(seg, path, &where_)?;
                        let (s, a) = self.end_seek(
                            &es,
                            sc,
                            varying_segment(&got, bits),
                            &where_,
                            bits.unwrap_or(0),
                            Some(&before),
                            path,
                            None,
                        )?;
                        *out.last_mut().unwrap() = s;
                        after = a;
                    }
                    out.extend(got);
                    out.extend(after);
                }
                E::IndirectParameterRefEntry(e) => {
                    let got = self.indirect(e, path, scope, &where_, None)?;
                    out.extend(got);
                }
                E::ParameterRefEntry(_) | E::ArrayParameterRefEntry(_) => {
                    let (pref, rp, dl) = match ch {
                        E::ParameterRefEntry(e) => {
                            (&e.parameter_ref, e.repeat_entry.as_ref(), None)
                        }
                        E::ArrayParameterRefEntry(e) => (
                            &e.parameter_ref,
                            e.repeat_entry.as_ref(),
                            e.dimension_list.as_ref(),
                        ),
                        _ => unreachable!("a parameter entry"),
                    };
                    let local = self.param_local(pref, path)?;
                    let local = self.subset(local, dl, path, &where_)?;
                    let local =
                        self.repeat(local, RepeatRef::of(rp), path, &where_)?;
                    let local = self.bind(local, scope, &where_, None)?;
                    let mut after = Vec::new();
                    if let Some(Item::EndSeek(es)) = out.last() {
                        let es = es.clone();
                        let (s, a) = self.end_seek(
                            &es,
                            sc,
                            Some(local.clone()),
                            &where_,
                            0,
                            Some(scope),
                            path,
                            None,
                        )?;
                        *out.last_mut().unwrap() = s;
                        after = a;
                    }
                    out.push(Item::Value(Value {
                        local: local.clone(),
                        assembled: 0,
                    }));
                    out.extend(after);
                    scope.push(local);
                }
                E::ContainerRefEntry(e) => {
                    let got = self.container_ref(
                        e,
                        path,
                        stack,
                        scope,
                        &where_,
                        conditional || cond.is_some(),
                    )?;
                    out.extend(got);
                }
            }
            match cond {
                None => items.extend(out),
                Some(cond) => {
                    for it in walk(&out, true) {
                        if let Item::Value(v) = it {
                            v.local.get_mut().optional = true;
                        }
                    }
                    items.push(Item::Cond(Cond {
                        criteria: cond,
                        items: out,
                        group: 0,
                    }));
                }
            }
        }
        Self::no_next(&items, &where_)?;
        self.raw_segments(&mut items, segs, scope)?;
        Ok(items)
    }

    /// Compiles a ContainerRefEntry: the container's items, or a value of
    /// its aggregate when it is repeated.
    ///
    /// # Arguments
    ///
    /// * `e`           - The entry.
    /// * `path`        - The key of the SpaceSystem it is in.
    /// * `stack`       - The keys of the containers being compiled.
    /// * `scope`       - The values read so far; grows.
    /// * `where_`      - What it is in, for error messages.
    /// * `conditional` - Whether it is inside an IncludeCondition.
    ///
    /// # Returns
    ///
    /// The items.
    ///
    /// # Errors
    ///
    /// Unsupported, if the container can't be included so; Cycle, if it
    /// includes itself.
    fn container_ref(
        &mut self,
        e: &'a x::ContainerRefEntryType,
        path: &Key,
        stack: &[Key],
        scope: &mut Vec<Shared<Local>>,
        where_: &str,
        conditional: bool,
    ) -> Result<Vec<Item>> {
        let ckey = self.container_key(&e.container_ref, path)?;
        if stack.contains(&ckey) {
            return Err(Error::Cycle(format!(
                "{where_}: container includes itself, a loop"
            )));
        }
        let (cpath, inc) = self.containers.get(&ckey).unwrap().clone();
        if inc.base_container.is_some() && e.repeat_entry.is_some() {
            return unsupported(format!(
                "{where_}: repeated container {} has a BaseContainer",
                qname(&ckey)
            ));
        }
        let mut out = Vec::new();
        if e.repeat_entry.is_some() {
            let vt = self.container_type(&ckey, stack, where_)?;
            let local = self.local(&inc.name, &qname(&ckey), vt);
            let local = self.repeat(
                local,
                RepeatRef::of(e.repeat_entry.as_ref()),
                path,
                where_,
            )?;
            let local = self.bind(local, scope, where_, None)?;
            out.push(Item::Value(Value {
                local: local.clone(),
                assembled: 0,
            }));
            scope.push(local);
            return Ok(out);
        }
        let m = self.next_id();
        let mut inner = vec![Item::Mark(Mark { id: m })];
        // A derived container is its bases' entries, then its own.
        for (kp, kc, kkey) in self.include_chain(&ckey, stack, where_)? {
            let mut st = stack.to_vec();
            st.push(kkey.clone());
            inner.extend(self.tm_items(kc, &kp, &st, m, scope, conditional)?);
            if let Some(rc) = kc
                .base_container
                .as_ref()
                .and_then(|b| b.restriction_criteria.as_ref())
            {
                let terms = self.match_(
                    CritRef::restriction(rc),
                    &kp,
                    scope,
                    where_,
                    None,
                )?;
                let text: Vec<String> =
                    terms.iter().map(|t| t.text()).collect();
                inner.push(Item::Require(Require {
                    criteria: terms,
                    container: qname(&kkey),
                    text: text.join(" and "),
                    fixes: false,
                }));
            }
        }
        let pad = self.size_pad(inc, &inner, m, where_, Some(scope), &cpath)?;
        inner.extend(pad);
        let wrapped = self.wrap_transform(
            inc,
            inner,
            m,
            where_,
            &cpath,
            &qname(&ckey),
            false,
        )?;
        out.extend(measure(wrapped, where_)?);
        if !edc_items(Some(inc)).is_empty() && !has_transform(inc) {
            let end = self.next_id();
            out.push(Item::Mark(Mark { id: end }));
            if conditional {
                // Checked where the container ends, when it is there.
                for (name, v) in edc_items(Some(inc)) {
                    let c = self.check(
                        name,
                        v,
                        &cpath,
                        scope,
                        m,
                        Some(end),
                        &qname(&ckey),
                        where_,
                    )?;
                    out.push(Item::Verify(Verify { check: c }));
                }
            } else {
                self.included_checks.push(Included {
                    container: inc,
                    path: cpath.clone(),
                    start: m,
                    end,
                    scope: scope.clone(),
                    key: qname(&ckey),
                });
            }
        }
        Ok(out)
    }

    /// Gives the items for a segment entry. The segments of a parameter in
    /// one entry list that make up its whole encoding (at most 64 bits)
    /// are assembled into the value; container and stream segments, and
    /// segments with an IncludeCondition, are always fields of their own.
    ///
    /// # Arguments
    ///
    /// * `e`       - The entry.
    /// * `path`    - The key of the SpaceSystem it is in.
    /// * `where_`  - What it is in, for error messages.
    /// * `segs`    - The entry list's incomplete parameters and counts.
    /// * `cond`    - Whether the entry has an IncludeCondition.
    /// * `scope`   - The values read so far; grows.
    /// * `arg_key` - In a command, gives the key of an argument.
    ///
    /// # Returns
    ///
    /// The items for the entry.
    ///
    /// # Errors
    ///
    /// Unsupported, if a repeated segment has a Count that isn't positive,
    /// or a segment is invalid.
    #[allow(clippy::too_many_arguments)]
    pub fn segment(
        &mut self,
        e: SegRef<'_>,
        path: &Key,
        where_: &str,
        segs: &mut Segs,
        cond: bool,
        scope: &mut Vec<Shared<Local>>,
        arg_key: ArgKey<'_>,
    ) -> Result<Vec<Item>> {
        let Some(rp) = e.repeat() else {
            return self.one_segment(
                e,
                path,
                where_,
                segs,
                cond,
                scope,
                e.order(),
            );
        };
        let count = self.int_value(
            IntValueRef::P(&rp.count),
            path,
            "RepeatEntry Count",
            where_,
        )?;
        let gap = match &rp.offset {
            Some(o) => Some(self.int_value(
                IntValueRef::P(o),
                path,
                "RepeatEntry Offset",
                where_,
            )?),
            None => None,
        };
        let no_gap = matches!(gap, None | Some(IntValue::Int(0)));
        if let (IntValue::Int(c), true) = (&count, no_gap) {
            if *c < 1 {
                return unsupported(format!(
                    "{where_}: RepeatEntry Count {c} is not positive"
                ));
            }
            let mut out = Vec::new();
            for i in 0..*c {
                let order = e.order().map(|o| o + i);
                out.extend(
                    self.one_segment(
                        e, path, where_, segs, cond, scope, order,
                    )?,
                );
            }
            return Ok(out);
        }
        let r = self.segment_ref(e, path)?;
        let k0 = *segs.counts.get(&r).unwrap_or(&0);
        segs.counts.insert(r.clone(), k0 + 1);
        let k = e.order().unwrap_or(k0);
        let lc = self.raw_segment(&r, k, e.size_in_bits(), where_)?;
        let lc = self.repeat(lc, RepeatRef::of(Some(rp)), path, where_)?;
        let lc = self.bind(lc, scope, where_, arg_key)?;
        scope.push(lc.clone());
        Ok(vec![Item::Value(Value {
            local: lc,
            assembled: 0,
        })])
    }

    /// Measures the bits that a segment entry takes, its repeats and the
    /// gaps between them too, for a location from the container's end.
    ///
    /// # Arguments
    ///
    /// * `e`      - The entry.
    /// * `path`   - The key of the SpaceSystem it is in.
    /// * `where_` - What it is in, for error messages.
    ///
    /// # Returns
    ///
    /// The bits, or `None` when its count or offset varies.
    ///
    /// # Errors
    ///
    /// Unsupported, if its count or offset is out of range.
    pub fn segment_bits(
        &mut self,
        e: SegRef<'_>,
        path: &Key,
        where_: &str,
    ) -> Result<Option<i64>> {
        let Some(rp) = e.repeat() else {
            return Ok(Some(e.size_in_bits()));
        };
        let count = self.int_value(
            IntValueRef::P(&rp.count),
            path,
            "RepeatEntry Count",
            where_,
        )?;
        let gap = match &rp.offset {
            Some(o) => self.int_value(
                IntValueRef::P(o),
                path,
                "RepeatEntry Offset",
                where_,
            )?,
            None => IntValue::Int(0),
        };
        let (IntValue::Int(c), IntValue::Int(g)) = (count, gap) else {
            return Ok(None);
        };
        if c < 1 || g < 0 {
            return unsupported(format!(
                "{where_}: RepeatEntry Count {c} or Offset {g} is out of range"
            ));
        }
        Ok(Some(c * e.size_in_bits() + (c - 1) * g))
    }

    /// Finds what a segment entry is a segment of.
    ///
    /// # Arguments
    ///
    /// * `e`    - The entry.
    /// * `path` - The key of the SpaceSystem it is in.
    ///
    /// # Returns
    ///
    /// The qualified name of the parameter or container, or the stream
    /// reference as written.
    ///
    /// # Errors
    ///
    /// Unsupported, if a reference doesn't resolve.
    pub fn segment_ref(&self, e: SegRef<'_>, path: &Key) -> Result<String> {
        match e {
            SegRef::Param(p) => {
                let has = |k: &[String]| self.params.contains(k);
                Ok(qname(&Self::resolve(
                    &p.parameter_ref,
                    path,
                    &has,
                    "parameter",
                )?))
            }
            SegRef::Container(c) => {
                Ok(qname(&self.container_key(&c.container_ref, path)?))
            }
            SegRef::Stream(s) => Ok(s.stream_ref.clone()),
        }
    }

    /// Compiles one segment of an entry. A parameter's segments are
    /// collected until they make up its encoding, when the value is
    /// assembled; other segments are raw fields of their own.
    ///
    /// # Arguments
    ///
    /// * `e`      - The entry.
    /// * `path`   - The key of the SpaceSystem it is in.
    /// * `where_` - What it is in, for error messages.
    /// * `segs`   - The entry list's incomplete parameters and counts.
    /// * `cond`   - Whether the entry has an IncludeCondition.
    /// * `scope`  - The values read so far; grows.
    /// * `order`  - Which part of the thing it is a segment of, from the order
    ///              attribute, or `None`.
    ///
    /// # Returns
    ///
    /// The Segment, followed by the parameter's Value once it is complete;
    /// or the Value of a raw field.
    ///
    /// # Errors
    ///
    /// Unsupported, if a parameter's segments have more bits than its
    /// encoding, or a raw segment is too big.
    #[allow(clippy::too_many_arguments)]
    fn one_segment(
        &mut self,
        e: SegRef<'_>,
        path: &Key,
        where_: &str,
        segs: &mut Segs,
        cond: bool,
        scope: &mut Vec<Shared<Local>>,
        order: Option<i64>,
    ) -> Result<Vec<Item>> {
        let bits = e.size_in_bits();
        let r = self.segment_ref(e, path)?;
        let k0 = *segs.counts.get(&r).unwrap_or(&0);
        segs.counts.insert(r.clone(), k0 + 1);
        let k = order.unwrap_or(k0);
        if let (SegRef::Param(p), false) = (e, cond) {
            let gi = match segs.groups.iter().position(|g| g.0 == r) {
                Some(i) => i,
                None => {
                    let local = self.param_local(&p.parameter_ref, path)?;
                    local.get_mut().length = None;
                    let total = fixed_bits(&local.get().vt());
                    segs.groups.push((r.clone(), local, total, Vec::new()));
                    segs.groups.len() - 1
                }
            };
            let (local, total) = (segs.groups[gi].1.clone(), segs.groups[gi].2);
            let seg = Mut::new(Segment {
                local: local.clone(),
                bits,
                total: total.unwrap_or(0),
                shift: 0,
                first: false,
            });
            segs.groups[gi].3.push((seg.clone(), order, k));
            let got: i64 =
                segs.groups[gi].3.iter().map(|s| s.0.get().bits).sum();
            if let Some(total) = total.filter(|t| *t <= 64 && got >= *t) {
                if got > total {
                    return unsupported(format!(
                        "{where_}: the segments of {r} have {got} bits, and \
                         its encoding {total}"
                    ));
                }
                let parts = segs.groups.remove(gi).3;
                assemble(&parts, where_, &r)?;
                scope.push(local.clone());
                return Ok(vec![
                    Item::Segment(seg),
                    Item::Value(Value {
                        local,
                        assembled: total,
                    }),
                ]);
            }
            return Ok(vec![Item::Segment(seg)]);
        }
        let lc = self.raw_segment(&r, k, bits, where_)?;
        scope.push(lc.clone());
        Ok(vec![Item::Value(Value {
            local: lc,
            assembled: 0,
        })])
    }

    /// Makes a field that holds a segment's bits as they are.
    ///
    /// # Arguments
    ///
    /// * `r`      - What it is a segment of.
    /// * `k`      - Which segment of it this is, from 0.
    /// * `bits`   - Its size in bits.
    /// * `where_` - What it is in, for error messages.
    ///
    /// # Returns
    ///
    /// The Local: an integer of up to 64 bits, or binary data of whole
    /// bytes.
    ///
    /// # Errors
    ///
    /// Unsupported, if it is over 64 bits and not whole bytes, or bigger
    /// than a field may be.
    pub fn raw_segment(
        &mut self,
        r: &str,
        k: i64,
        bits: i64,
        where_: &str,
    ) -> Result<Shared<Local>> {
        let key = format!("{r} segment");
        let name = format!("segment of {r}");
        let mut vt = if bits <= 64 {
            VType::new(
                &key,
                &name,
                "int",
                Encoding::Int(IntEnc {
                    bits,
                    encoding: "unsigned".into(),
                    order: crate::model::Order::Be,
                    lsb: false,
                }),
            )
        } else if bits % 8 == 0 && bits <= self.max_field_bits {
            VType::new(
                &key,
                &name,
                "binary",
                Encoding::Binary(BinaryEnc::new(bits)),
            )
        } else {
            return unsupported(format!(
                "{where_}: a {bits}-bit segment of {r}"
            ));
        };
        vt.doc = format!("segment {k} of {r}, its bits as they are");
        let short = r.rsplit('/').next().unwrap_or(r);
        Ok(self.local(
            &format!("{short}Segment{k}"),
            &format!("{r}#segment{k}"),
            Rc::new(vt),
        ))
    }

    /// Makes fields of the segments of values that an entry list holds
    /// only part of.
    ///
    /// # Arguments
    ///
    /// * `items` - The entry list's items; each such Segment is replaced by the
    ///             Value of a raw field.
    /// * `segs`  - The incomplete parameters.
    /// * `scope` - The values read so far; grows by the new fields.
    ///
    /// # Errors
    ///
    /// Unsupported, if a segment can't be a raw field.
    fn raw_segments(
        &mut self,
        items: &mut [Item],
        segs: Segs,
        scope: &mut Vec<Shared<Local>>,
    ) -> Result<()> {
        for (r, _, _, parts) in segs.groups {
            for (seg, _, k) in parts {
                let bits = seg.get().bits;
                let lc = self.raw_segment(&r, k, bits, "")?;
                let i = items
                    .iter()
                    .position(
                        |it| matches!(it, Item::Segment(s) if s.same(&seg)),
                    )
                    .expect("the segment among the items");
                items[i] = Item::Value(Value {
                    local: lc.clone(),
                    assembled: 0,
                });
                scope.push(lc);
            }
        }
        Ok(())
    }

    /// Compiles an IndirectParameterRefEntry: the parameter that the value
    /// of its ParameterInstance names. Each parameter it can name becomes
    /// a field, present when the value names it.
    ///
    /// # Arguments
    ///
    /// * `e`       - The entry.
    /// * `path`    - The key of the SpaceSystem it is in.
    /// * `scope`   - The values read so far; grows by the parameters it can
    ///               select.
    /// * `where_`  - What it is in, for error messages.
    /// * `arg_key` - In a command, gives the key of an argument.
    ///
    /// # Returns
    ///
    /// A Require that the value selects a parameter, then a Cond for each
    /// parameter it can select.
    ///
    /// # Errors
    ///
    /// Unsupported, if it is repeated, the selector's kind can't name
    /// parameters, it can select none, or one it must select can't be
    /// read here.
    pub fn indirect(
        &mut self,
        e: &x::IndirectParameterRefEntryType,
        path: &Key,
        scope: &mut Vec<Shared<Local>>,
        where_: &str,
        arg_key: ArgKey<'_>,
    ) -> Result<Vec<Item>> {
        if e.repeat_entry.is_some() {
            return unsupported(format!(
                "{where_}: a repeated IndirectParameterRefEntry"
            ));
        }
        let sel = XRef::param(&e.parameter_instance);
        let (_, _, vt, pkey) =
            self.value_ref(&sel, path, scope, where_, arg_key)?;
        let aliases = e.alias_name_space.as_deref().map(|s| self.aliases(s));
        let has = |k: &[String]| self.params.contains(k);
        let choices: Vec<(Vec<String>, Key)> = if vt.kind == "enum" {
            let mut out = Vec::new();
            for lab in &vt.labels {
                let key = match &aliases {
                    Some(a) => {
                        a.iter().find(|x| x.0 == lab.label).map(|x| x.1.clone())
                    }
                    None => {
                        Self::resolve(&lab.label, path, &has, "parameter").ok()
                    }
                };
                if let Some(k) = key {
                    out.push((vec![lab.label.clone()], k));
                }
            }
            out
        } else if aliases.is_some() && (vt.kind == "int" || vt.kind == "string")
        {
            aliases
                .as_ref()
                .unwrap()
                .iter()
                .filter(|(a, _)| vt.kind == "string" || is_int_text(a))
                .map(|(a, k)| (vec![a.clone()], k.clone()))
                .collect()
        } else if vt.kind == "int" || vt.kind == "string" {
            self.named_by(&vt, path)
        } else {
            return unsupported(format!(
                "{where_}: an IndirectParameterRefEntry chosen by {} value \
                 {pkey}",
                vt.kind
            ));
        };
        let mut out: Vec<Item> = Vec::new();
        let group = self.next_id();
        // Only one choice is there, so each sees only the values before
        // the entry.
        let before = scope.clone();
        let mut chosen = Vec::new();
        let mut crits: Vec<Term> = Vec::new();
        for (texts, key) in &choices {
            let mut terms = Vec::new();
            for t in texts {
                terms.push(self.comparison(
                    &sel,
                    "==",
                    Some(t),
                    path,
                    &before,
                    where_,
                    arg_key,
                    None,
                )?);
            }
            let crit = if terms.len() == 1 {
                terms[0].clone()
            } else {
                let text: Vec<String> =
                    terms.iter().map(|t| t.text()).collect();
                Term::AnyOf(Shared::new(AnyOf {
                    terms,
                    text: text.join(" or "),
                }))
            };
            crits.push(crit.clone());
            let tried: Result<Shared<Local>> = (|| {
                let lc = self.param_local(&qname(key), &[])?;
                let lc = self.bind(lc, &before, where_, arg_key)?;
                if lc.get().computed() {
                    return unsupported(format!(
                        "{where_}: an IndirectParameterRefEntry selects length \
                         field {}",
                        lc.get().name
                    ));
                }
                if is_rest(&lc.get().vt()) {
                    return unsupported(format!(
                        "{} takes the rest of the packet",
                        lc.get().name
                    ));
                }
                Ok(lc)
            })();
            let lc = match tried {
                Ok(lc) => lc,
                Err(Error::Unsupported(xmsg)) => {
                    if vt.kind == "enum" || aliases.is_some() {
                        return Err(Error::Unsupported(xmsg));
                    }
                    // Any parameter in the mission can be named: one that
                    // can't be decoded here fails decoding when it is.
                    let text = format!(
                        "{pkey} names {}, which can't be read here",
                        qname(key)
                    );
                    let never = self.comparison(
                        &sel,
                        "!=",
                        Some(&texts[0]),
                        path,
                        &before,
                        where_,
                        arg_key,
                        None,
                    )?;
                    out.push(Item::Cond(Cond {
                        criteria: vec![crit],
                        items: vec![Item::Require(Require {
                            criteria: vec![never],
                            container: where_.into(),
                            text: format!("{text} ({xmsg})"),
                            fixes: false,
                        })],
                        group,
                    }));
                    continue;
                }
                Err(e) => return Err(e),
            };
            lc.get_mut().optional = true;
            out.push(Item::Cond(Cond {
                criteria: vec![crit],
                items: vec![Item::Value(Value {
                    local: lc.clone(),
                    assembled: 0,
                })],
                group,
            }));
            chosen.push(lc);
        }
        scope.extend(chosen);
        let any_value = out.iter().any(|it| {
            let Item::Cond(c) = it else { return false };
            matches!(c.items.first(), Some(Item::Value(_)))
        });
        if !any_value {
            if vt.kind == "int" && aliases.is_none() {
                // Every value would name no parameter, which fails: the
                // packet could never be read or written.
                return unsupported(format!(
                    "{where_}: no parameter has a name that integer {pkey} can \
                     hold, so it can never select one; give the entry an \
                     aliasNameSpace whose aliases are numbers, or make the \
                     selector an enumeration of parameter names or a string"
                ));
            }
            return unsupported(format!(
                "{where_}: no label of {pkey} names a parameter"
            ));
        }
        // A selector must be one of its labels, or name a parameter: one
        // that isn't fails, before anything after it is read.
        let why = if vt.kind == "enum" {
            let mut c = Vec::new();
            for lab in &vt.labels {
                c.push(self.comparison(
                    &sel,
                    "==",
                    Some(&lab.label),
                    path,
                    &before,
                    where_,
                    arg_key,
                    None,
                )?);
            }
            crits = c;
            "is none of its labels"
        } else {
            "names no parameter"
        };
        out.insert(
            0,
            Item::Require(Require {
                criteria: vec![Term::AnyOf(Shared::new(AnyOf {
                    terms: crits,
                    text: format!("not {pkey} {why}"),
                }))],
                container: where_.into(),
                text: format!("{pkey} {why}"),
                fixes: false,
            }),
        );
        Ok(out)
    }

    /// Finds the parameters that a string or integer value can name: by
    /// its full path, and by its own name when that finds it from the
    /// SpaceSystem, as references do.
    ///
    /// # Arguments
    ///
    /// * `vt`   - The type of the value.
    /// * `path` - The key of the SpaceSystem the entry is in.
    ///
    /// # Returns
    ///
    /// (names, parameter key) for each parameter it can name.
    fn named_by(&self, vt: &VType, path: &Key) -> Vec<(Vec<String>, Key)> {
        let mut limit: Option<i64> = None;
        let mut charset = String::new();
        if let Encoding::String(e) = &vt.enc {
            // The most bits of content: without the size tag or terminator.
            let fixed = e.fixed_bits.filter(|b| *b != 0);
            limit = Some(fixed.unwrap_or_else(|| {
                e.max_bits
                    - match e.size_tag_bits {
                        Some(t) => t,
                        None => {
                            8 * e
                                .terminator
                                .as_ref()
                                .map_or(0, |t| t.len() as i64)
                        }
                    }
            }));
            charset = e.charset.clone();
        }
        let has = |k: &[String]| self.params.contains(k);
        let mut out = Vec::new();
        for key in self.params.keys() {
            let mut names = Vec::new();
            let last = key.last().unwrap();
            if Self::resolve(last, path, &has, "parameter").ok().as_ref()
                == Some(key)
            {
                names.push(last.clone());
            }
            names.push(qname(key));
            if vt.kind == "int" {
                let (lo, hi) = int_bounds(vt);
                names.retain(|n| {
                    is_int_text(n)
                        && n.parse::<num_bigint::BigInt>()
                            .is_ok_and(|v| lo <= v && v <= hi)
                });
            } else if let Some(limit) = limit {
                names.retain(|n| {
                    crate::text::encode_text(n, &charset)
                        .is_some_and(|b| 8 * b.len() as i64 <= limit)
                });
            }
            if !names.is_empty() {
                out.push((names, key.clone()));
            }
        }
        out
    }

    /// Collects the parameter aliases in a name space.
    ///
    /// # Arguments
    ///
    /// * `space` - The name space.
    ///
    /// # Returns
    ///
    /// Each alias in it, with the key of the first parameter that has it,
    /// in the order first found.
    fn aliases(&self, space: &str) -> Vec<(String, Key)> {
        let mut out: Vec<(String, Key)> = Vec::new();
        for (key, (_, p)) in self.params.iter() {
            for a in p.alias_set.iter().flat_map(|s| s.alias.iter()) {
                if a.name_space == space && !out.iter().any(|x| x.0 == a.alias)
                {
                    out.push((a.alias.clone(), key.clone()));
                }
            }
        }
        out
    }

    /// Lists an included container and its bases.
    ///
    /// # Arguments
    ///
    /// * `ckey`   - The included container's key.
    /// * `stack`  - The keys of the containers being compiled.
    /// * `where_` - What includes it, for error messages.
    ///
    /// # Returns
    ///
    /// (SpaceSystem key, container, key) of each, the root first.
    ///
    /// # Errors
    ///
    /// Cycle, if a container includes itself; Unsupported, if a base
    /// doesn't resolve.
    pub fn include_chain(
        &self,
        ckey: &Key,
        stack: &[Key],
        where_: &str,
    ) -> Result<Vec<(Key, &'a x::SequenceContainerType, Key)>> {
        let mut chain: Vec<(Key, &'a x::SequenceContainerType, Key)> =
            Vec::new();
        let mut key = ckey.clone();
        loop {
            if stack.contains(&key) || chain.iter().any(|c| c.2 == key) {
                return Err(Error::Cycle(format!(
                    "{where_}: container includes itself, a loop"
                )));
            }
            let (path, c) = self.containers.get(&key).unwrap().clone();
            chain.push((path.clone(), c, key.clone()));
            match &c.base_container {
                None => {
                    chain.reverse();
                    return Ok(chain);
                }
                Some(b) => key = self.container_key(&b.container_ref, &path)?,
            }
        }
    }

    /// Gives the Seek that ends an entry `es.offset` bits before the end
    /// of its container, and the items to follow it.
    ///
    /// # Arguments
    ///
    /// * `es`      - The entry's EndSeek.
    /// * `sc`      - The XTCE container.
    /// * `local`   - The entry's value, or `None` for a segment.
    /// * `where_`  - What the container is, for error messages.
    /// * `bits`    - A segment's size in bits.
    /// * `scope`   - The values decoded before the entry, or `None` if a size
    ///               another value gives can't be used.
    /// * `path`    - The key of the SpaceSystem the container is in.
    /// * `arg_key` - In a command, gives the key of an argument.
    ///
    /// # Returns
    ///
    /// The Seek or TailSeek, and the items to follow the entry.
    ///
    /// # Errors
    ///
    /// Unsupported, if the entry's size varies in a way values decoded
    /// before it don't give, or the container is an included one whose
    /// size varies.
    #[allow(clippy::too_many_arguments)]
    pub fn end_seek(
        &mut self,
        es: &EndSeek,
        sc: &x::SequenceContainerType,
        local: Option<Shared<Local>>,
        where_: &str,
        mut bits: i64,
        scope: Option<&[Shared<Local>]>,
        path: &Key,
        arg_key: ArgKey<'_>,
    ) -> Result<(Item, Vec<Item>)> {
        let mut span = None;
        if let Some(l) = &local {
            let vt = l.get().vt();
            let rest = is_rest(&vt);
            let fb = if rest { Some(0) } else { fixed_bits(&vt) };
            if fb.is_none() {
                span = span_of(&vt);
            }
            if (fb.is_none() && span.is_none()) || rest {
                return unsupported(format!(
                    "{where_}: a location from the container's end for {}, \
                     whose size varies",
                    l.get().name
                ));
            }
            bits = fb.unwrap_or(0);
        }
        if has_transform(sc) {
            // Its end is that of its bytes before the transform.
            return Ok((
                Item::TailSeek(TailSeek {
                    back: es.offset + bits,
                    span,
                }),
                vec![Item::TailPad(TailPad { bits: es.offset })],
            ));
        }
        let size = match sc
            .binary_encoding
            .as_ref()
            .and_then(|b| b.size_in_bits.as_ref())
        {
            Some(s) => Some(self.int_value(
                IntValueRef::P(s),
                path,
                "container SizeInBits",
                where_,
            )?),
            None => None,
        };
        match size {
            Some(IntValue::Int(size)) => Ok((
                Item::Seek(Seek {
                    from_start: Some(es.start),
                    offset: Count::Int(size - es.offset - bits),
                    back: 0,
                    ahead: span.is_some(),
                    span,
                }),
                Vec::new(),
            )),
            Some(IntValue::Dyn(d)) if scope.is_some() => {
                let (mut off, rng) =
                    self.bind_dyn(&d, scope.unwrap(), where_, arg_key)?;
                off.max = dyn_units(&off, &rng, self.max_field_bits);
                Ok((
                    Item::Seek(Seek {
                        from_start: Some(es.start),
                        offset: Count::Dyn(off),
                        back: es.offset + bits,
                        ahead: true,
                        span,
                    }),
                    Vec::new(),
                ))
            }
            size => {
                if es.start != 0 || size.is_some() {
                    return unsupported(format!(
                        "{where_}: a location from the end of an included \
                         container whose size varies"
                    ));
                }
                Ok((
                    Item::TailSeek(TailSeek {
                        back: es.offset + bits,
                        span,
                    }),
                    vec![Item::TailPad(TailPad { bits: es.offset })],
                ))
            }
        }
    }

    /// Makes the padding that makes a container with a BinaryEncoding
    /// occupy its SizeInBits.
    ///
    /// # Arguments
    ///
    /// * `sc`     - The XTCE container.
    /// * `items`  - Its entries' items.
    /// * `start`  - The id of the Mark at its start.
    /// * `where_` - What it is, for error messages.
    /// * `scope`  - The values decoded before its end, or `None` for a repeated
    ///              container.
    /// * `path`   - The key of the SpaceSystem it is defined in.
    ///
    /// # Returns
    ///
    /// None, a Pad, or a Fill done when the packet is decoded or encoded.
    ///
    /// # Errors
    ///
    /// Unsupported, if the entries take more than the size or overlap, or
    /// a repeated container's size or entries vary.
    pub fn size_pad(
        &mut self,
        sc: &x::SequenceContainerType,
        items: &[Item],
        start: i64,
        where_: &str,
        scope: Option<&[Shared<Local>]>,
        path: &Key,
    ) -> Result<Vec<Item>> {
        let Some(be) = &sc.binary_encoding else {
            return Ok(Vec::new());
        };
        if be.from_binary_transform_algorithm.is_some()
            || be.to_binary_transform_algorithm.is_some()
        {
            // Its SizeInBits is its transformed size.
            return Ok(Vec::new());
        }
        let Some(sib) = &be.size_in_bits else {
            return Ok(Vec::new());
        };
        let size = self.int_value(
            IntValueRef::P(sib),
            path,
            "container SizeInBits",
            where_,
        )?;
        let size = match size {
            IntValue::Dyn(d) => {
                let Some(scope) = scope else {
                    return unsupported(format!(
                        "{where_}: a repeated container whose size another \
                         value gives"
                    ));
                };
                let (mut off, rng) = self.bind_dyn(&d, scope, where_, None)?;
                off.max = dyn_units(&off, &rng, self.max_field_bits);
                return Ok(vec![Item::Fill(Fill {
                    start,
                    size: Count::Dyn(off),
                    container: where_.into(),
                })]);
            }
            IntValue::Int(s) => s,
        };
        if size < 0 {
            return unsupported(format!(
                "{where_}: container size {size} is negative"
            ));
        }
        let Some((pos, extent)) = static_end(items, start) else {
            // Where the entries end varies: pad up to the size at run time.
            if scope.is_none() {
                return unsupported(format!(
                    "{where_}: a container size with entries whose size or \
                     position varies"
                ));
            }
            return Ok(vec![Item::Fill(Fill {
                start,
                size: Count::Int(size),
                container: where_.into(),
            })]);
        };
        if extent > size {
            return unsupported(format!(
                "{where_}: entries take {extent} bits, more than the container \
                 size {size}"
            ));
        }
        if pos != extent {
            return unsupported(format!(
                "{where_}: a container size after entries that overlap"
            ));
        }
        Ok(if size > pos {
            vec![Item::Pad(Pad { bits: size - pos })]
        } else {
            Vec::new()
        })
    }

    /// Compiles the aggregate holding one copy of a repeated container's
    /// entries, once.
    ///
    /// # Arguments
    ///
    /// * `ckey`   - The container's key.
    /// * `stack`  - The keys of the containers being compiled.
    /// * `where_` - What repeats it, for error messages.
    ///
    /// # Returns
    ///
    /// The aggregate type.
    ///
    /// # Errors
    ///
    /// Unsupported, if its entries can't be the members of an aggregate.
    pub fn container_type(
        &mut self,
        ckey: &Key,
        stack: &[Key],
        where_: &str,
    ) -> Result<Rc<VType>> {
        if let Some(t) = self.ctypes.get(ckey) {
            return Ok(t.clone());
        }
        let (cpath, inc) = self.containers.get(ckey).unwrap().clone();
        let what = format!("{where_}: repeated container {}", qname(ckey));
        let m = self.next_id();
        // Its entries are values of each copy, so sizes can't come from
        // values outside it.
        let mut st = stack.to_vec();
        st.push(ckey.clone());
        let mut scope = Vec::new();
        let mut items =
            self.tm_items(inc, &cpath, &st, m, &mut scope, false)?;
        let xf = self.copy_transform(inc, &items, &what, &cpath)?;
        if xf.is_none() {
            let pad =
                self.size_pad(inc, &items, m, where_, None, &Key::new())?;
            items.extend(pad);
        }
        let vt = self.aggregate_of(
            &items,
            m,
            &what,
            &qname(ckey),
            &inc.name,
            xf,
            "one copy of a repeated container",
        )?;
        self.ctypes.insert(ckey.clone(), vt.clone());
        Ok(vt)
    }

    /// Compiles the transform of each copy of a repeated container, if its
    /// BinaryEncoding has transform algorithms.
    ///
    /// # Arguments
    ///
    /// * `sc`    - The XTCE container.
    /// * `items` - Its entries' items.
    /// * `what`  - What it is, for error messages.
    /// * `path`  - The key of the SpaceSystem it is defined in.
    ///
    /// # Returns
    ///
    /// The XformEnc, or `None` if it has no transform algorithms.
    ///
    /// # Errors
    ///
    /// Unsupported, if it also has ErrorDetectCorrect, or no SizeInBits
    /// of whole bytes.
    fn copy_transform(
        &mut self,
        sc: &x::SequenceContainerType,
        items: &[Item],
        what: &str,
        path: &Key,
    ) -> Result<Option<XformEnc>> {
        let Some(be) = &sc.binary_encoding else {
            return Ok(None);
        };
        let Some((fa, ta)) = container_transform(be) else {
            return Ok(None);
        };
        if !edc_items(Some(sc)).is_empty() {
            return unsupported(format!(
                "{what} has ErrorDetectCorrect and transform algorithms"
            ));
        }
        let size = match &be.size_in_bits {
            Some(s) => Some(self.int_value(
                IntValueRef::P(s),
                path,
                "container SizeInBits",
                what,
            )?),
            None => None,
        };
        let size = match size {
            Some(IntValue::Int(s)) if s % 8 == 0 && s >= 0 => s,
            _ => {
                return unsupported(format!(
                    "{what} has transform algorithms and no SizeInBits of \
                     whole bytes"
                ))
            }
        };
        let decode = fa.map(|n| self.hook("binary_from", &n, 0));
        let encode = ta.map(|n| self.hook("binary_to", &n, 0));
        Ok(Some(XformEnc {
            raw: Some(size / 8),
            decode,
            encode,
            cap: (max_bits(items) + 7).div_euclid(8),
            time: false,
            sized: false,
        }))
    }

    /// Makes the aggregate of a repeated container's items. A size or
    /// count from an earlier value of the copy is that member's.
    ///
    /// # Arguments
    ///
    /// * `items`     - The items.
    /// * `m`         - The id of the Mark they start at.
    /// * `what`      - What the container is, for error messages.
    /// * `key`       - The aggregate's key.
    /// * `name`      - The aggregate's name.
    /// * `transform` - The transform of each copy, if any.
    /// * `doc`       - The aggregate's documentation.
    ///
    /// # Returns
    ///
    /// The aggregate type, its members the values with the bits to skip
    /// before each, and its restriction criteria as checks.
    ///
    /// # Errors
    ///
    /// Unsupported, if the items have something an aggregate can't hold.
    #[allow(clippy::too_many_arguments)]
    pub fn aggregate_of(
        &mut self,
        items: &[Item],
        m: i64,
        what: &str,
        key: &str,
        name: &str,
        transform: Option<XformEnc>,
        doc: &str,
    ) -> Result<Rc<VType>> {
        let this = Shared::new(Local {
            id: 0,
            name: name.into(),
            key: key.into(),
            vtype: None,
            optional: false,
            length: None,
            measures: false,
            checksum: false,
        });
        // The member of each value so far, by its Local's address, in order.
        let mut idents: Vec<(usize, Member)> = Vec::new();
        let mut checks: Vec<Require> = Vec::new();
        let mut members: Vec<Member> = Vec::new();
        let mut names = Namer::default();
        let mut pos: Option<i64> = Some(0);
        let mut marks: HashMap<i64, Option<i64>> =
            HashMap::from([(m, Some(0))]);
        let mut skip = 0;
        for it in items {
            match it {
                Item::Cond(_) => {
                    return unsupported(format!(
                        "{what} has an IncludeCondition"
                    ))
                }
                Item::Require(r) => {
                    let mut crit = Vec::new();
                    for c in &r.criteria {
                        let Term::Criterion(cr) = c else {
                            return unsupported(format!(
                                "{what} has criteria on values outside it"
                            ));
                        };
                        let cr = cr.get();
                        let mem =
                            idents.iter().find(|i| i.0 == cr.local.addr());
                        let (Some((_, mem)), None) = (mem, &cr.other) else {
                            return unsupported(format!(
                                "{what} has criteria on values outside it"
                            ));
                        };
                        let mut p = vec![PathStep::Field(mem.ident.clone())];
                        p.extend(cr.path.iter().cloned());
                        crit.push(Term::Criterion(Shared::new(Criterion {
                            local: this.clone(),
                            op: cr.op.clone(),
                            value: cr.value.clone(),
                            text: cr.text.clone(),
                            path: p,
                            target: Some(
                                cr.target
                                    .clone()
                                    .unwrap_or_else(|| mem.vtype.clone()),
                            ),
                            other: None,
                            other_path: Vec::new(),
                            other_target: None,
                        })));
                    }
                    checks.push(Require {
                        criteria: crit,
                        container: r.container.clone(),
                        text: r.text.clone(),
                        fixes: false,
                    });
                }
                Item::Fill(_) => {
                    return unsupported(format!(
                        "{what} includes a container whose size varies"
                    ))
                }
                Item::Verify(_) => {
                    return unsupported(format!("{what} has a checksum"))
                }
                Item::Transform(t) => {
                    // An included container whose entries are transformed:
                    // a member holding them, transformed as a repeated one
                    // is.
                    let t = t.get();
                    let Some(raw) = t.raw else {
                        return unsupported(format!(
                            "{what} has transformed data whose size varies"
                        ));
                    };
                    let short = t
                        .name
                        .rsplit('/')
                        .next()
                        .unwrap_or(&t.name)
                        .to_string();
                    let inner = self.aggregate_of(
                        &t.items,
                        t.start,
                        what,
                        &t.name,
                        &short,
                        Some(XformEnc {
                            raw: Some(raw),
                            decode: t.decode.clone(),
                            encode: t.encode.clone(),
                            cap: t.cap,
                            time: false,
                            sized: false,
                        }),
                        "an included container, transformed on the wire",
                    )?;
                    members.push(Member {
                        name: inner.name.clone(),
                        ident: names.take(&[&snake(&inner.name)]),
                        vtype: inner,
                        skip,
                    });
                    skip = 0;
                    pos = pos.map(|p| p + 8 * raw);
                }
                Item::Seek(s) => {
                    if s.varies() {
                        return unsupported(format!(
                            "{what} has a location that other values give"
                        ));
                    }
                    let Count::Int(off) = s.offset else {
                        unreachable!("a fixed offset");
                    };
                    let delta = match s.from_start {
                        Some(f) => {
                            let Some(mk) = marks.get(&f) else {
                                return unsupported(format!(
                                    "{what} has a location relative to the \
                                     start of another container"
                                ));
                            };
                            let (Some(mk), Some(p)) = (mk, pos) else {
                                return unsupported(format!(
                                    "{what} has a location after a value whose \
                                     size varies"
                                ));
                            };
                            mk + off - p
                        }
                        None => off,
                    };
                    if delta < 0 {
                        return unsupported(format!(
                            "{what} has entries that overlap"
                        ));
                    }
                    skip += delta;
                    pos = pos.map(|p| p + delta);
                }
                Item::Mark(mk) => {
                    marks.insert(mk.id, pos);
                }
                Item::Pad(p) => {
                    skip += p.bits;
                    pos = pos.map(|x| x + p.bits);
                }
                Item::Segment(_) => {
                    return unsupported(format!(
                        "{what} has a value in segments"
                    ))
                }
                Item::Value(Value { local, .. })
                | Item::LengthEnd(LengthEnd { local }) => {
                    let mut vt = local.get().vt();
                    if vt.dyn_size().is_some() {
                        vt = self.member_bound(
                            &vt,
                            &idents,
                            items,
                            what,
                            &local.get().name,
                        )?;
                    }
                    if local.get().computed() {
                        return unsupported(format!(
                            "{what}: {} is a packet length or checksum field",
                            local.get().name
                        ));
                    }
                    let mem = Member {
                        name: local.get().name.clone(),
                        ident: names.take(&[&snake(&local.get().name)]),
                        vtype: vt.clone(),
                        skip,
                    };
                    members.push(mem.clone());
                    match idents.iter_mut().find(|i| i.0 == local.addr()) {
                        Some(i) => i.1 = mem,
                        None => idents.push((local.addr(), mem)),
                    }
                    skip = 0;
                    pos = match (pos, fixed_bits(&vt)) {
                        (Some(p), Some(b)) => Some(p + b),
                        _ => None,
                    };
                }
                _ => {
                    return unsupported(format!(
                        "{what} has an entry an aggregate can't hold"
                    ))
                }
            }
        }
        if members.is_empty() {
            return unsupported(format!("{what} has no entries"));
        }
        let mut vt = VType::new(
            key,
            name,
            "aggregate",
            Encoding::Aggregate(AggregateEnc {
                members,
                pad: skip,
                checks,
                this: Some(this.clone()),
                transform,
            }),
        );
        vt.doc = doc.into();
        let vt = Rc::new(vt);
        this.get_mut().vtype = Some(vt.clone());
        Ok(vt)
    }

    /// Binds a type whose size or count another value gives to the latest
    /// earlier member of a repeated container's copy that is that value.
    ///
    /// # Arguments
    ///
    /// * `vt`     - The type.
    /// * `idents` - The member for each value so far, by its Local's address.
    /// * `items`  - The copy's items.
    /// * `what`   - What the container is, for error messages.
    /// * `name`   - The name of the value, for error messages.
    ///
    /// # Returns
    ///
    /// A bounded copy of `vt`.
    ///
    /// # Errors
    ///
    /// Unsupported, if the value isn't an earlier integer entry of the
    /// copy.
    fn member_bound(
        &mut self,
        vt: &Rc<VType>,
        idents: &[(usize, Member)],
        items: &[Item],
        what: &str,
        name: &str,
    ) -> Result<Rc<VType>> {
        let d = vt.dyn_size().unwrap().clone();
        let locs: Vec<Shared<Local>> = items
            .iter()
            .filter_map(|i| match i {
                Item::Value(v) => Some(v.local.clone()),
                _ => None,
            })
            .collect();
        let mut sib: Option<&Member> = None;
        for (addr, mem) in idents {
            let Some(lc) = locs.iter().find(|l| l.addr() == *addr) else {
                continue;
            };
            let lc = lc.get();
            if d.key.as_ref() == Some(&lc.key)
                || d.arg.as_ref() == Some(&lc.name)
            {
                sib = Some(mem);
            }
        }
        let sib = match sib {
            Some(s)
                if d.table.is_none()
                    && d.members.is_empty()
                    && d.instance == 0 =>
            {
                s
            }
            _ => {
                return unsupported(format!(
                    "{what}: {name} has a size that another value gives, other \
                     than an earlier entry of the container"
                ))
            }
        };
        if sib.vtype.kind != "int" {
            return unsupported(format!(
                "{what}: size or count from {} entry {}",
                sib.vtype.kind, sib.name
            ));
        }
        let wide = if sib.vtype.wide() {
            Some(sib.vtype.clone())
        } else {
            None
        };
        let b = DynSize {
            member: Some(sib.ident.clone()),
            wide_type: wide,
            ..d
        };
        let rng = int_bounds(&sib.vtype);
        Ok(Rc::new(self.bound(vt, b, &rng, what)))
    }

    /// Puts a container's items (from Mark `start`, which must be first)
    /// in a Transform, if its BinaryEncoding has transform algorithms,
    /// with their checks as Verify items at its end.
    ///
    /// # Arguments
    ///
    /// * `sc`     - The XTCE container.
    /// * `items`  - Its compiled items.
    /// * `start`  - The id of the Mark at its start.
    /// * `where_` - What it is, for error messages.
    /// * `path`   - The key of the SpaceSystem it is defined in.
    /// * `name`   - Its name, given to the Transform and its checks.
    /// * `packet` - Whether it is the packet itself rather than an included
    ///              container.
    ///
    /// # Returns
    ///
    /// The items as they are, or a list of the one Transform holding them.
    ///
    /// # Errors
    ///
    /// Unsupported, if it holds more than one packet length field, or its
    /// SizeInBits isn't a number of whole bytes, or an included container
    /// has none.
    #[allow(clippy::too_many_arguments)]
    pub fn wrap_transform(
        &mut self,
        sc: &'a x::SequenceContainerType,
        items: Vec<Item>,
        start: i64,
        where_: &str,
        path: &Key,
        name: &str,
        packet: bool,
    ) -> Result<Vec<Item>> {
        let Some(be) = &sc.binary_encoding else {
            return Ok(items);
        };
        let Some((fa, ta)) = container_transform(be) else {
            return Ok(items);
        };
        let mut items = if packet {
            items
        } else {
            measure(items, where_)?
        };
        let lengths: Vec<Shared<Local>> = walk(&items, false)
            .into_iter()
            .filter_map(|i| match i {
                Item::Value(v)
                    if v.local.get().length.is_some()
                        && !v.local.get().measures =>
                {
                    Some(v.local)
                }
                _ => None,
            })
            .collect();
        if lengths.len() > 1 {
            return unsupported(format!(
                "{where_}: more than one packet length field in a container \
                 with transform algorithms"
            ));
        }
        let marks: Vec<i64> = walk(&items, true)
            .iter()
            .filter_map(|i| match i {
                Item::Mark(m) => Some(m.id),
                _ => None,
            })
            .collect();
        let (inside, rest): (Vec<Included<'a>>, Vec<Included<'a>>) = self
            .included_checks
            .drain(..)
            .partition(|c| marks.contains(&c.start));
        self.included_checks = rest;
        let mut checks = Vec::new();
        for inc in &inside {
            for (n, v) in edc_items(Some(inc.container)) {
                let c = self.check(
                    n,
                    v,
                    &inc.path,
                    &inc.scope,
                    inc.start,
                    Some(inc.end),
                    &inc.key,
                    where_,
                )?;
                checks.push(Item::Verify(Verify { check: c }));
            }
        }
        if !edc_items(Some(sc)).is_empty() {
            let end = self.next_id();
            let scope: Vec<Shared<Local>> = walk(&items, true)
                .into_iter()
                .filter_map(|i| match i {
                    Item::Value(v) => Some(v.local),
                    _ => None,
                })
                .collect();
            let mut own = vec![Item::Mark(Mark { id: end })];
            own.extend(checks);
            for (n, v) in edc_items(Some(sc)) {
                let c = self.check(
                    n,
                    v,
                    path,
                    &scope,
                    start,
                    Some(end),
                    name,
                    where_,
                )?;
                own.push(Item::Verify(Verify { check: c }));
            }
            checks = own;
        }
        items.extend(checks);
        let mut raw = None;
        if let Some(s) = &be.size_in_bits {
            let size = self.int_value(
                IntValueRef::P(s),
                path,
                "container SizeInBits",
                where_,
            )?;
            match size {
                IntValue::Int(n) if n % 8 == 0 => raw = Some(n / 8),
                _ => {
                    return unsupported(format!(
                        "{where_}: a transformed container whose size isn't a \
                         number of whole bytes"
                    ))
                }
            }
        } else if !packet {
            return unsupported(format!(
                "{where_}: an included container with transform algorithms and \
                 no fixed SizeInBits"
            ));
        }
        let cap = (max_bits(&items) + 7).div_euclid(8);
        let out_cap = raw.unwrap_or(self.max_field_bits / 8);
        let decode = fa.map(|n| self.hook("binary_from", &n, 0));
        let encode = ta.map(|n| self.hook("binary_to", &n, 0));
        Ok(vec![Item::Transform(Mut::new(Transform {
            items,
            decode,
            encode,
            raw,
            cap,
            out_cap,
            name: name.into(),
            start,
            length: lengths.into_iter().next(),
            patches: Vec::new(),
            keeps: false,
        }))])
    }

    /// Compiles a SequenceContainer, and its bases, once.
    ///
    /// # Arguments
    ///
    /// * `key` - The container's key.
    ///
    /// # Returns
    ///
    /// Its Node; one that can't be compiled has an `error` saying why.
    ///
    /// # Errors
    ///
    /// Cycle, if its inheritance chain is a loop, or a container includes
    /// itself.
    pub fn tm_node(&mut self, key: &Key) -> Result<Shared<Node>> {
        if let Some(n) = self.nodes.get(key) {
            if n.get().building {
                return Err(Error::Cycle(format!(
                    "container inheritance loop through {}",
                    qname(key)
                )));
            }
            return Ok(n.clone());
        }
        let (path, sc) = self.containers.get(key).unwrap().clone();
        let node = Shared::new(Node {
            kind: "tm".into(),
            key: qname(key),
            name: sc.name.clone(),
            abstract_: sc.abstract_.unwrap_or(false),
            building: true,
            ..Node::default()
        });
        self.nodes.insert(key.clone(), node.clone());
        match self.tm_build(&node, key, &path, sc) {
            Ok(()) => {}
            Err(Error::Unsupported(m)) => {
                let mut n = node.get_mut();
                n.error = Some(m);
                n.base = None;
            }
            Err(e) => return Err(e),
        }
        node.get_mut().building = false;
        self.order.push(node.clone());
        Ok(node)
    }

    /// Builds a SequenceContainer's node, for `tm_node`.
    ///
    /// # Arguments
    ///
    /// * `node` - The node, being built.
    /// * `key`  - The container's key.
    /// * `path` - The key of the SpaceSystem it is defined in.
    /// * `sc`   - The container.
    ///
    /// # Errors
    ///
    /// Unsupported, if it can't be compiled; Cycle, for a loop.
    fn tm_build(
        &mut self,
        node: &Shared<Node>,
        key: &Key,
        path: &Key,
        sc: &'a x::SequenceContainerType,
    ) -> Result<()> {
        let nkey = node.get().key.clone();
        let where_ = format!("container {nkey}");
        let mut base = None;
        let mut scope: Vec<Shared<Local>> = Vec::new();
        if let Some(bc) = &sc.base_container {
            let bkey = self.container_key(&bc.container_ref, path)?;
            let b = self.tm_node(&bkey)?;
            if b.get().error.is_some() {
                return unsupported(format!(
                    "base container {} is not supported",
                    b.get().key
                ));
            }
            node.get_mut().base = Some(b.clone());
            scope = b.scope();
            if let Some(rc) = &bc.restriction_criteria {
                let crit = self.match_(
                    CritRef::restriction(rc),
                    path,
                    &scope,
                    &where_,
                    None,
                )?;
                node.get_mut().criteria = crit;
                if let Some(nc) = &rc.next_container {
                    // It also depends on the container that follows it in
                    // a stream: decoding its base never picks it.
                    node.get_mut().next_container =
                        Some(nc.container_ref.clone());
                }
            }
            base = Some(b);
        }
        self.node_edc.insert(node.addr(), Some((sc, path.clone())));
        self.included_checks = Vec::new();
        if has_transform(sc) {
            // Its checks are in its Transform.
            self.node_edc.insert(node.addr(), None);
            // Its own entries are transformed: they start at the start of
            // what the transform gives.
            let m = self.next_id();
            let mut items = vec![Item::Mark(Mark { id: m })];
            items.extend(self.tm_items(
                sc,
                path,
                &[key.clone()],
                m,
                &mut scope,
                false,
            )?);
            let wrapped =
                self.wrap_transform(sc, items, m, &where_, path, &nkey, true)?;
            node.get_mut().items = wrapped;
        } else {
            let items =
                self.tm_items(sc, path, &[key.clone()], 0, &mut scope, false)?;
            node.get_mut().items = items;
            // A size covers the whole packet, base containers' entries too.
            let all = node.all_items();
            let pad =
                self.size_pad(sc, &all, 0, &where_, Some(&scope), path)?;
            node.get_mut().items.extend(pad);
        }
        let inc = std::mem::take(&mut self.included_checks);
        self.node_included.insert(node.addr(), inc);
        self.check_rest(node, &where_)?;
        check_tail(node, &where_)?;
        check_aligned(&node.all_items(), &where_)?;
        check_length(node, &where_)?;
        if !node.get().abstract_ {
            let checks = self.node_checks(node, &where_)?;
            node.get_mut().checks = checks;
        }
        if let Some(b) = base {
            if node.get().next_container.is_none() {
                b.get_mut().children.push(node.clone());
            }
        }
        Ok(())
    }
}

/// Says whether text is an integer, as the Python's `-?\d+` says.
///
/// # Arguments
///
/// * `s` - The text.
///
/// # Returns
///
/// Whether it is.
fn is_int_text(s: &str) -> bool {
    let d = s.strip_prefix('-').unwrap_or(s);
    !d.is_empty() && d.chars().all(|c| c.is_ascii_digit())
}

/// Places each Segment of a value in its encoding: by their order
/// attributes, or in document order without them. Sets each one's shift,
/// and marks the first in the document.
///
/// # Arguments
///
/// * `parts`  - Each of the value's segments, in document order.
/// * `where_` - What they are in, for error messages.
/// * `r`      - What they are segments of, for error messages.
///
/// # Errors
///
/// Unsupported, if some but not all have an order, or an order is
/// repeated.
fn assemble(parts: &[Part], where_: &str, r: &str) -> Result<()> {
    let orders: Vec<Option<i64>> = parts.iter().map(|p| p.1).collect();
    let ranked: Vec<Mut<Segment>> = if orders.iter().all(|o| o.is_none()) {
        parts.iter().map(|p| p.0.clone()).collect()
    } else {
        let mut seen = std::collections::HashSet::new();
        if orders.iter().any(|o| o.is_none() || !seen.insert(*o)) {
            return unsupported(format!(
                "{where_}: segments of {r} whose order is missing or repeated"
            ));
        }
        let mut sorted: Vec<&Part> = parts.iter().collect();
        sorted.sort_by_key(|p| p.1);
        sorted.into_iter().map(|p| p.0.clone()).collect()
    };
    let mut left = parts[0].0.get().total;
    for s in ranked {
        let mut s = s.get_mut();
        left -= s.bits;
        s.shift = left;
    }
    parts[0].0.get_mut().first = true;
    Ok(())
}

/// Makes an included container's length fields (those not already
/// measuring a container inside it) measure it.
///
/// # Arguments
///
/// * `items`  - The container's items; their Locals are marked.
/// * `where_` - What includes it, for error messages.
///
/// # Returns
///
/// The items, with a LengthEnd after them for each length field.
///
/// # Errors
///
/// Unsupported, if a length field has an IncludeCondition.
pub fn measure(mut items: Vec<Item>, where_: &str) -> Result<Vec<Item>> {
    let mut ends = Vec::new();
    for it in walk(&items, true) {
        if let Item::Value(v) = it {
            let l = &v.local;
            if l.get().length.is_some() && !l.get().measures {
                if l.get().optional {
                    return unsupported(format!(
                        "{where_}: length field {} of an included container \
                         has an IncludeCondition",
                        l.get().name
                    ));
                }
                l.get_mut().measures = true;
                ends.push(Item::LengthEnd(LengthEnd { local: l.clone() }));
            }
        }
    }
    items.extend(ends);
    Ok(items)
}

/// Checks that an entry located from the end of a packet whose size
/// varies, or of a transformed container's bytes, is its last, and is
/// always there.
///
/// # Arguments
///
/// * `node`   - The packet.
/// * `where_` - What it is, for error messages.
///
/// # Errors
///
/// Unsupported, if such an entry has an IncludeCondition or isn't last.
pub fn check_tail(node: &Shared<Node>, where_: &str) -> Result<()> {
    /// Checks the entries located from the end of a list of items and of
    /// the Transforms in it.
    ///
    /// # Arguments
    ///
    /// * `items`  - The items.
    /// * `what`   - What they make up, for error messages.
    /// * `where_` - What the packet is, for error messages.
    ///
    /// # Errors
    ///
    /// Unsupported, if such an entry has an IncludeCondition or isn't
    /// last.
    fn last(items: &[Item], what: &str, where_: &str) -> Result<()> {
        for it in items {
            if let Item::Cond(c) = it {
                if walk(&c.items, false)
                    .iter()
                    .any(|x| matches!(x, Item::TailSeek(_)))
                {
                    return unsupported(format!(
                        "{where_}: an entry located from the {what}'s end has \
                         an IncludeCondition"
                    ));
                }
            }
        }
        let at: Vec<usize> = items
            .iter()
            .enumerate()
            .filter(|(_, i)| matches!(i, Item::TailSeek(_)))
            .map(|(i, _)| i)
            .collect();
        if let Some(&first) = at.first() {
            // The entry is everything up to its TailPad.
            let pad = (first..items.len())
                .find(|&i| matches!(items[i], Item::TailPad(_)))
                .expect("a TailPad after a TailSeek");
            let rest = &items[pad + 1..];
            if at.len() > 1
                || rest.iter().any(|i| {
                    !matches!(
                        i,
                        Item::Require(_) | Item::Mark(_) | Item::Verify(_)
                    )
                })
            {
                return unsupported(format!(
                    "{where_}: an entry located from the end of a {what} is \
                     not its last"
                ));
            }
        }
        for it in walk(items, false) {
            if let Item::Transform(t) = it {
                last(&t.get().items, "transformed container", where_)?;
            }
        }
        Ok(())
    }
    last(&node.all_items(), "packet whose size varies", where_)
}
