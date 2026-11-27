//! The whole mission: every container, meta-command, message and stream
//! compiled, each concrete packet finished (the values its inheritance
//! fixes, the sizes derived from others, its fields, alarms, algorithms
//! and hooks), and the names the generated code defines, as the Python
//! compiler's `compile`, `_finish`, `bind_alarms` and `bind_algorithms`
//! make them.

use std::rc::Rc;

use tcs_xtce::generated as x;

use super::criteria::{inputs_of, set_inputs, CritRef, XRef};
use super::layout::{max_bits, tree};
use super::types::{is_rest, triggered_items};
use super::{qname, Compiler};
use crate::dump::{Dict, Shared};
use crate::model::{
    unsupported, walk, Alarm, AlarmContext, AlarmCriteria, AlarmDef, Algorithm,
    Count, CustomAlarm, DynSize, Encoding, Error, Expr, Hook, Input, Item,
    Kind, Lit, Local, Mission, Node, Result, Spec, Term, VType, ValueRef,
};
use crate::names::{camel, snake, Namer, RUNTIME_NAMES};

/// How a packet uses one of its values, for `derived_sizes`.
#[derive(Clone)]
enum Use {
    /// It gives the size or count of a whole value that is always present:
    /// that value and the DynSize.
    Size(Shared<Local>, DynSize),
    /// A criterion, location or lookup reads it.
    Read,
    /// Any other use.
    Other,
}

/// Says whether a list of Locals holds one.
///
/// # Arguments
///
/// * `v`  - The list.
/// * `lc` - The Local.
///
/// # Returns
///
/// Whether it does.
fn holds(v: &[Shared<Local>], lc: &Shared<Local>) -> bool {
    v.iter().any(|x| x.same(lc))
}

/// Says whether a list of hooks holds one.
///
/// # Arguments
///
/// * `v` - The list.
/// * `h` - The hook.
///
/// # Returns
///
/// Whether it does.
fn has_hook(v: &[Shared<Hook>], h: &Shared<Hook>) -> bool {
    v.iter().any(|x| x.same(h))
}

/// Adds hooks to a list, each once.
///
/// # Arguments
///
/// * `out` - The list.
/// * `hs`  - The hooks to add.
fn add_hooks(
    out: &mut Vec<Shared<Hook>>,
    hs: impl IntoIterator<Item = Shared<Hook>>,
) {
    for h in hs {
        if !has_hook(out, &h) {
            out.push(h);
        }
    }
}

/// Finds the hooks a term calls.
///
/// # Arguments
///
/// * `t` - The term.
///
/// # Returns
///
/// The hooks of its Custom terms, in order.
fn term_hooks(t: &Term) -> Vec<Shared<Hook>> {
    match t {
        Term::Custom(c) => vec![c.get().hook.clone()],
        Term::Criterion(_) => Vec::new(),
        Term::AllOf(a) => a.get().terms.iter().flat_map(term_hooks).collect(),
        Term::AnyOf(a) => a.get().terms.iter().flat_map(term_hooks).collect(),
    }
}

/// Lists the hooks that criteria call.
///
/// # Arguments
///
/// * `terms` - The criteria.
///
/// # Returns
///
/// Each hook they call, once, in the order they first call it.
fn terms_hooks(terms: &[Term]) -> Vec<Shared<Hook>> {
    let mut out = Vec::new();
    for t in terms {
        add_hooks(&mut out, term_hooks(t));
    }
    out
}

/// Lists the hooks of custom checksums.
///
/// # Arguments
///
/// * `checks` - The checks.
///
/// # Returns
///
/// Each custom checksum's hook, once, in order.
fn checks_hooks(checks: &[Shared<crate::model::Check>]) -> Vec<Shared<Hook>> {
    let mut out = Vec::new();
    for c in checks {
        if let Spec::Custom(h) = &c.get().spec {
            add_hooks(&mut out, [h.clone()]);
        }
    }
    out
}

/// Gives the criteria of a conditional block and the blocks inside it.
///
/// # Arguments
///
/// * `it` - The item.
///
/// # Returns
///
/// The criteria; none for an item that isn't a Cond.
fn conds(it: &Item) -> Vec<Term> {
    match it {
        Item::Cond(c) => {
            let mut out = c.criteria.clone();
            for i in &c.items {
                out.extend(conds(i));
            }
            out
        }
        _ => Vec::new(),
    }
}

/// Gives the DynSizes of a Count.
///
/// # Arguments
///
/// * `c` - The count.
///
/// # Returns
///
/// Its DynSize, if it is one.
fn count_dyn(c: &Count) -> Option<DynSize> {
    match c {
        Count::Dyn(d) => Some(d.clone()),
        Count::Int(_) => None,
    }
}

/// Gives the DynSizes of a Span: its count's and its gap's.
///
/// # Arguments
///
/// * `s` - The span, if any.
///
/// # Returns
///
/// The DynSizes.
fn span_dyns(s: &Option<crate::model::Span>) -> Vec<DynSize> {
    match s {
        Some(s) => count_dyn(&s.count)
            .into_iter()
            .chain(count_dyn(&s.gap))
            .collect(),
        None => Vec::new(),
    }
}

/// Lists the hooks that reading or writing entries calls.
///
/// # Arguments
///
/// * `items` - The entries of a container or meta-command.
///
/// # Returns
///
/// Each hook they call, once: first those that criteria call, then
/// custom checksums', then transforms'.
fn items_hooks(items: &[Item]) -> Vec<Shared<Hook>> {
    let mut terms: Vec<Term> = items.iter().flat_map(conds).collect();
    let mut checks = Vec::new();
    let mut transforms: Vec<Shared<Hook>> = Vec::new();
    for it in walk(items, true) {
        let mut dyns: Vec<DynSize> = Vec::new();
        match &it {
            Item::Transform(t) => {
                let t = t.get();
                transforms
                    .extend(t.decode.iter().chain(t.encode.iter()).cloned());
            }
            Item::Require(r) => terms.extend(r.criteria.iter().cloned()),
            Item::Verify(v) => checks.push(v.check.clone()),
            Item::Seek(s) => {
                dyns.extend(count_dyn(&s.offset));
                dyns.extend(span_dyns(&s.span));
            }
            Item::TailSeek(s) => dyns.extend(span_dyns(&s.span)),
            Item::Fill(f) => dyns.extend(count_dyn(&f.size)),
            Item::Value(v) => {
                let vt = v.local.get().vt();
                for t in tree(&vt) {
                    dyns.extend(t.dyn_size().cloned());
                }
                for t in tree(&vt) {
                    let xf = match &t.enc {
                        Encoding::Aggregate(a) => a.transform.as_ref(),
                        Encoding::Xform(e) => Some(e),
                        _ => None,
                    };
                    if let Some(e) = xf {
                        transforms.extend(
                            e.decode.iter().chain(e.encode.iter()).cloned(),
                        );
                    }
                }
            }
            _ => {}
        }
        for d in dyns {
            for (crit, _) in d.lookup.iter().flatten() {
                terms.extend(crit.iter().cloned());
            }
        }
    }
    let mut out = terms_hooks(&terms);
    add_hooks(&mut out, checks_hooks(&checks));
    let mut unique: Vec<Shared<Hook>> = Vec::new();
    add_hooks(&mut unique, transforms);
    add_hooks(&mut out, unique);
    out
}

/// Lists the values that alternatives hold (the parameters an
/// IndirectParameterRefEntry can select).
///
/// # Arguments
///
/// * `items` - The items to search, conditions included.
///
/// # Returns
///
/// The Locals.
fn chosen_locals(items: &[Item]) -> Vec<Shared<Local>> {
    let mut out = Vec::new();
    for it in items {
        if let Item::Cond(c) = it {
            if c.group != 0 {
                for x in walk(&c.items, true) {
                    if let Item::Value(v) = x {
                        out.push(v.local);
                    }
                }
            } else {
                out.extend(chosen_locals(&c.items));
            }
        }
    }
    out
}

/// Finds the values of a packet that encoding computes: each one, with
/// the value whose size or count it gives and that DynSize.
///
/// # Arguments
///
/// * `n` - The packet, its `fixed` values already found.
///
/// # Returns
///
/// The values, in the order of the packet.
fn derived_sizes(
    n: &Shared<Node>,
) -> Dict<Shared<Local>, (Shared<Local>, DynSize)> {
    let all = n.all_items();
    let items = walk(&all, true);
    let fixed: Vec<Shared<Local>> =
        n.get().fixed.0.iter().map(|f| f.0.clone()).collect();
    let mut uses: Vec<(Shared<Local>, Vec<Use>)> = Vec::new();
    let mut add = |lc: &Shared<Local>, u: Use| match uses
        .iter_mut()
        .find(|e| e.0.same(lc))
    {
        Some(e) => e.1.push(u),
        None => uses.push((lc.clone(), vec![u])),
    };
    /// Records the use of a value as a size or count.
    ///
    /// # Arguments
    ///
    /// * `add`      - Records one use.
    /// * `d`        - The DynSize.
    /// * `consumer` - The value it sizes, when that is a whole value that is
    ///                always present.
    /// * `read`     - Whether it is a location's offset, which only reads the
    ///                value.
    fn dyn_uses(
        add: &mut dyn FnMut(&Shared<Local>, Use),
        d: &DynSize,
        consumer: Option<Shared<Local>>,
        read: bool,
    ) {
        if let Some(lk) = &d.lookup {
            for (crit, _) in lk {
                for lc in crit.iter().flat_map(|c| c.locals()) {
                    add(&lc, Use::Read);
                }
            }
        } else if let Some(l) = &d.local {
            if read && d.path.is_empty() {
                add(l, Use::Read);
            } else {
                match consumer.filter(|_| d.path.is_empty()) {
                    Some(c) => add(l, Use::Size(c, d.clone())),
                    None => add(l, Use::Other),
                }
            }
        }
    }
    let mut crits = n.all_criteria();
    for it in &all {
        crits.extend(conds(it));
    }
    for c in &crits {
        for lc in c.locals() {
            add(&lc, Use::Read);
        }
    }
    for it in &items {
        match it {
            Item::Seek(s) => {
                if let Count::Dyn(d) = &s.offset {
                    dyn_uses(&mut add, d, None, true);
                }
                for d in span_dyns(&s.span) {
                    dyn_uses(&mut add, &d, None, true);
                }
            }
            Item::TailSeek(s) => {
                for d in span_dyns(&s.span) {
                    dyn_uses(&mut add, &d, None, true);
                }
            }
            Item::Value(v) => {
                let lc = &v.local;
                let vt = lc.get().vt();
                for t in tree(&vt) {
                    let Some(d) = t.dyn_size() else { continue };
                    let top = Rc::ptr_eq(&t, &vt)
                        && (t.kind == "array" || t.kind == "binary")
                        && !is_rest(&t);
                    let consumer =
                        (top && !lc.get().optional && !holds(&fixed, lc))
                            .then(|| lc.clone());
                    dyn_uses(&mut add, d, consumer, false);
                }
            }
            _ => {}
        }
    }
    let mut derived: Dict<Shared<Local>, (Shared<Local>, DynSize)> =
        Dict::default();
    for it in &items {
        let Item::Value(v) = it else { continue };
        let lc = &v.local;
        let vt = lc.get().vt();
        // A wide one stays a field the application sets, which encoding
        // checks against what it sizes.
        if lc.get().optional
            || holds(&fixed, lc)
            || lc.get().computed()
            || vt.kind != "int"
            || vt.calibrator.is_some()
            || vt.wide()
        {
            continue;
        }
        let found: Vec<Use> = uses
            .iter()
            .find(|e| e.0.same(lc))
            .map(|e| e.1.clone())
            .unwrap_or_default();
        let first = found.iter().find_map(|u| match u {
            Use::Size(c, d) => Some((c.clone(), d.clone())),
            _ => None,
        });
        let other = found.iter().any(|u| matches!(u, Use::Other));
        if let (Some(s), false) = (first, other) {
            match derived.0.iter_mut().find(|e| e.0.same(lc)) {
                Some(e) => e.1 = s,
                None => derived.0.push((lc.clone(), s)),
            }
        }
    }
    derived
}

/// Gives the input Locals of a custom algorithm.
///
/// # Arguments
///
/// * `inputs` - Its inputs.
///
/// # Returns
///
/// The Locals of its value inputs, in order.
fn input_locals(inputs: &[Input]) -> Vec<Shared<Local>> {
    inputs
        .iter()
        .filter_map(|i| match i {
            Input::Value(l, _, _) => Some(l.clone()),
            Input::Const(_) => None,
        })
        .collect()
}

/// Gives the last part of a qualified name.
///
/// # Arguments
///
/// * `key` - The name.
///
/// # Returns
///
/// What follows its last `/`.
fn last(key: &str) -> &str {
    key.rsplit('/').next().unwrap_or(key)
}

/// Says whether an alarm definition's level depends on earlier samples.
///
/// # Arguments
///
/// * `d` - The definition.
///
/// # Returns
///
/// Whether it does.
fn def_stateful(d: &AlarmDef) -> bool {
    d.change.is_some() || d.min_violations > 1 || d.min_conformance > 1
}

/// Lists an alarm's definitions: its contexts', then its default.
///
/// # Arguments
///
/// * `a` - The alarm.
///
/// # Returns
///
/// The definitions.
fn defs(a: &Alarm) -> Vec<&AlarmDef> {
    a.contexts
        .iter()
        .map(|c| &c.1)
        .chain(a.default.iter())
        .collect()
}

/// Gives the samples of a value that an alarm's change ranges look back
/// at.
///
/// # Arguments
///
/// * `a` - The alarm.
///
/// # Returns
///
/// The most samples any of its change ranges needs, or 0.
fn history(a: &Alarm) -> i64 {
    defs(a)
        .iter()
        .filter_map(|d| d.change.as_ref().map(|c| c.samples))
        .max()
        .unwrap_or(0)
}

/// Lists every node that inherits from one.
///
/// # Arguments
///
/// * `n` - The node.
///
/// # Returns
///
/// The children and their descendants, depth first, each child before
/// its own descendants.
fn descendants(n: &Shared<Node>) -> Vec<Shared<Node>> {
    let mut out = Vec::new();
    for c in n.get().children.clone() {
        out.push(c.clone());
        out.extend(descendants(&c));
    }
    out
}

/// Adds a type to a list by identity, keeping the place of one already
/// there.
///
/// # Arguments
///
/// * `v`  - The list.
/// * `vt` - The type.
fn add_type(v: &mut Vec<Rc<VType>>, vt: &Rc<VType>) {
    if !v.iter().any(|t| Rc::ptr_eq(t, vt)) {
        v.push(vt.clone());
    }
}

impl Compiler<'_> {
    /// Compiles every container, meta-command, message and stream, and
    /// names what the generated code defines.
    ///
    /// # Returns
    ///
    /// The mission, without those left out when `skip_unsupported` is set
    /// (`skipped` lists them and why).
    ///
    /// # Errors
    ///
    /// Unsupported, if something can't be compiled and `skip_unsupported`
    /// isn't set, the message listing the root causes; Cycle, for a loop
    /// in the documents.
    pub fn compile(&mut self) -> Result<Mission> {
        for key in self.containers.keys().to_vec() {
            self.tm_node(&key)?;
        }
        for key in self.mcs.keys().to_vec() {
            self.tc_node(&key)?;
        }
        for key in self.messages.keys().to_vec() {
            self.msg_node(&key)?;
        }
        // A node whose base failed fails too. Errors list the root causes
        // only; skipping names every node left out, root causes first.
        // The containers others derive from.
        let mut bases: Vec<String> = Vec::new();
        for (_, (path, sc)) in self.containers.iter() {
            if let Some(b) = &sc.base_container {
                if let Ok(k) = self.container_key(&b.container_ref, path) {
                    bases.push(qname(&k));
                }
            }
        }
        let mut roots: Vec<String> = Vec::new();
        let mut derived: Vec<String> = Vec::new();
        for n in &self.order {
            let n = n.get();
            // An abstract container that nothing derives from is only ever
            // included, where it is compiled with what surrounds it.
            if n.error.is_some()
                && n.abstract_
                && n.kind == "tm"
                && !bases.contains(&n.key)
            {
                continue;
            }
            if let Some(e) = &n.error {
                let m = format!("{}: {e}", n.key);
                if e.starts_with("base ") {
                    derived.push(m);
                } else {
                    roots.push(m);
                }
            }
        }
        let mut compiled = Vec::new();
        for key in self.streams.keys().to_vec() {
            match self.stream(&key) {
                Ok(s) => compiled.push(s),
                Err(Error::Unsupported(m)) => roots.push(m),
                Err(e) => return Err(e),
            }
        }
        let streams = self.link_streams(&compiled, &mut roots)?;
        let mut msgs = roots.clone();
        msgs.extend(derived.iter().cloned());
        if !msgs.is_empty() && !self.skip_unsupported {
            if !derived.is_empty() {
                roots.push(format!(
                    "and {} containers or meta-commands derived from them",
                    derived.len()
                ));
            }
            return unsupported(format!(
                "unsupported XTCE constructs:\n  {}",
                roots.join("\n  ")
            ));
        }
        let pos = |n: &Shared<Node>, me: &Self| {
            me.nodes
                .iter()
                .position(|(_, m)| m.same(n))
                .unwrap_or(usize::MAX)
        };
        let mut ok: Vec<Shared<Node>> = self
            .order
            .iter()
            .filter(|n| n.get().error.is_none())
            .cloned()
            .collect();
        let mut keyed: Vec<(bool, usize, Shared<Node>)> = ok
            .drain(..)
            .map(|n| {
                let tc = n.get().kind != "tm";
                (tc, pos(&n, self), n)
            })
            .collect();
        keyed.sort_by_key(|k| (k.0, k.1));
        let ok: Vec<Shared<Node>> = keyed.into_iter().map(|k| k.2).collect();
        let mut names = Namer::with_reserved(RUNTIME_NAMES.iter().copied());
        for n in &ok {
            if !n.get().abstract_ {
                let (cn, ck) = (camel(&n.get().name), camel(&n.get().key));
                n.get_mut().ident = names.take(&[&cn, &ck]);
            }
        }
        for st in &streams {
            let key = st.get().key.clone();
            let a = camel(last(&key)) + "Stream";
            let b = camel(&key) + "Stream";
            st.get_mut().ident = names.take(&[&a, &b]);
        }
        let tree_roots: Vec<Shared<Node>> = ok
            .iter()
            .filter(|n| n.get().base.is_none() && !n.get().children.is_empty())
            .cloned()
            .collect();
        let mut kinds: Vec<Shared<Kind>> = Vec::new();
        for r in &tree_roots {
            let mut members = vec![r.clone()];
            members.extend(descendants(r));
            members.retain(|m| !m.get().abstract_);
            if !members.is_empty() {
                let a = camel(&r.get().name) + "Kind";
                let b = camel(&r.get().key) + "Kind";
                kinds.push(Shared::new(Kind {
                    ident: names.take(&[&a, &b]),
                    root: r.clone(),
                    members,
                    max_bytes: 0,
                }));
            }
        }
        let concrete: Vec<Shared<Node>> =
            ok.iter().filter(|n| !n.get().abstract_).cloned().collect();
        let mut used: Vec<Rc<VType>> = Vec::new();
        let mut aggregates: Vec<Rc<VType>> = Vec::new();
        let mut copies: Vec<Rc<VType>> = Vec::new();
        let mut monitored: Vec<(String, i64)> = Vec::new();
        for n in &concrete {
            self.finish(n);
            self.bind_alarms(n)?;
            self.bind_algorithms(n)?;
            for (_, _, a) in n.get().alarms.iter() {
                if defs(a).iter().any(|d| def_stateful(d)) {
                    let h = history(a);
                    match monitored.iter_mut().find(|m| m.0 == a.key) {
                        Some(m) => m.1 = m.1.max(h),
                        None => monitored.push((a.key.clone(), h.max(0))),
                    }
                }
            }
            for it in walk(&n.all_items(), true) {
                let Item::Value(v) = it else { continue };
                for vt in tree(&v.local.get().vt()) {
                    if vt.kind == "enum" {
                        add_type(&mut used, &vt);
                    } else if vt.kind == "aggregate" {
                        match &vt.origin {
                            Some(o) => {
                                copies.push(vt.clone());
                                add_type(&mut aggregates, o);
                            }
                            None => add_type(&mut aggregates, &vt),
                        }
                    }
                }
            }
        }
        for vt in &used {
            let id = names.take(&[&camel(&vt.name), &camel(&vt.key)]);
            *vt.ident.borrow_mut() = id;
        }
        for vt in &aggregates {
            // A repeated container is often a packet too.
            let a = camel(&vt.name);
            let b = a.clone() + "Item";
            let id = names.take(&[&a, &b, &camel(&vt.key)]);
            *vt.ident.borrow_mut() = id;
        }
        for vt in &copies {
            let id = vt.origin.as_ref().unwrap().ident.borrow().clone();
            *vt.ident.borrow_mut() = id;
        }
        for k in &kinds {
            let m = k
                .get()
                .members
                .iter()
                .map(|m| m.get().max_bytes)
                .max()
                .unwrap_or(0);
            k.get_mut().max_bytes = m;
        }
        for st in &streams {
            let keys = {
                let s = st.get();
                if s.service.is_empty() {
                    vec![s.container.clone()]
                } else {
                    s.service.clone()
                }
            };
            let mut sizes: Vec<i64> = kinds
                .iter()
                .filter(|k| keys.contains(&k.get().root.get().key))
                .map(|k| k.get().max_bytes)
                .collect();
            sizes.extend(
                concrete
                    .iter()
                    .filter(|n| keys.contains(&n.get().key))
                    .map(|n| n.get().max_bytes),
            );
            let most =
                sizes.into_iter().max().unwrap_or(self.max_field_bits / 8);
            st.get_mut().max_bits = 8 * most;
        }
        let mut mnames = Namer::default();
        let mut hnames = Namer::default();
        for h in &self.hook_order {
            let name = h.get().name.clone();
            let id = hnames.take(&[&snake(last(&name)), &snake(&name)]);
            h.get_mut().ident = id;
        }
        // The mission is named for its last document, which by convention
        // is its own: the documents it builds on come first.
        let monitored = monitored
            .into_iter()
            .map(|(k, h)| {
                let id = mnames.take(&[&snake(last(&k)), &snake(&k)]);
                (k, id, h)
            })
            .collect();
        Ok(Mission {
            name: self.roots.last().unwrap().name.clone(),
            spaces: self.roots.iter().map(|r| r.name.clone()).collect(),
            source: String::new(),
            types: used,
            aggregates,
            nodes: concrete,
            kinds,
            roots: tree_roots,
            skipped: msgs,
            warnings: self.warnings.clone(),
            monitored,
            streams,
            hooks: self.hook_order.clone(),
        })
    }

    /// Completes a concrete packet: the values its inheritance fixes, the
    /// sizes derived from others, its fields and their names, its largest
    /// size and the hooks it calls.
    ///
    /// # Arguments
    ///
    /// * `n` - The packet.
    fn finish(&mut self, n: &Shared<Node>) {
        let mut fixed: Dict<Shared<Local>, Lit> = Dict::default();
        let has = |f: &Dict<Shared<Local>, Lit>, l: &Shared<Local>| {
            f.0.iter().any(|e| e.0.same(l))
        };
        for c in n.all_criteria() {
            if let Term::Criterion(c) = c {
                let c = c.get();
                let fixes =
                    c.op == "==" && c.path.is_empty() && c.other.is_none();
                if fixes && !has(&fixed, &c.local) {
                    fixed.0.push((c.local.clone(), c.value.clone().unwrap()));
                }
            }
        }
        // An included meta-command's argument assignments, unless inside a
        // Cond (which walk would splice in).
        for it in n.all_items() {
            if let Item::Require(r) = it {
                if r.fixes {
                    for c in &r.criteria {
                        if let Term::Criterion(c) = c {
                            let c = c.get();
                            if !has(&fixed, &c.local) && !c.local.get().optional
                            {
                                fixed.0.push((
                                    c.local.clone(),
                                    c.value.clone().unwrap(),
                                ));
                            }
                        }
                    }
                }
            }
        }
        n.get_mut().fixed = fixed;
        let derived = derived_sizes(n);
        n.get_mut().derived = derived;
        let all = n.all_items();
        let values: Vec<Shared<Local>> = {
            let nb = n.get();
            walk(&all, true)
                .into_iter()
                .filter_map(|it| match it {
                    Item::Value(v) => Some(v.local),
                    _ => None,
                })
                .filter(|lc| {
                    !nb.fixed.0.iter().any(|e| e.0.same(lc))
                        && !lc.get().computed()
                        && !nb.derived.0.iter().any(|e| e.0.same(lc))
                })
                .collect()
        };
        // The values an IndirectParameterRefEntry can select are named
        // last, so the packet's own keep their names.
        let chosen = chosen_locals(&all);
        let mut names = Namer::default();
        let mut idents: Vec<(Shared<Local>, String)> = Vec::new();
        let mut name_them =
            |pick: bool, idents: &mut Vec<(Shared<Local>, String)>| {
                for lc in &values {
                    if holds(&chosen, lc) == pick {
                        let id = names.take(&[&snake(&lc.get().name)]);
                        match idents.iter_mut().find(|e| e.0.same(lc)) {
                            Some(e) => e.1 = id,
                            None => idents.push((lc.clone(), id)),
                        }
                    }
                }
            };
        name_them(false, &mut idents);
        name_them(true, &mut idents);
        let fields: Vec<(String, Shared<Local>)> = values
            .iter()
            .map(|lc| {
                let id =
                    idents.iter().find(|e| e.0.same(lc)).unwrap().1.clone();
                (id, lc.clone())
            })
            .collect();
        n.get_mut().fields = fields;
        // A late check puts the container's bytes after the packet's, and
        // what the transform makes of them after those.
        let late: i64 = n
            .get()
            .checks
            .iter()
            .filter_map(|c| c.get().late.clone())
            .map(|t| t.get().cap + t.get().out_cap)
            .max()
            .unwrap_or(0);
        n.get_mut().max_bytes = (max_bits(&all) + 7).div_euclid(8) + late;
        let mut hooks = terms_hooks(&n.all_criteria());
        let mut more = items_hooks(&all);
        more.extend(checks_hooks(&n.get().checks));
        add_hooks(&mut hooks, more);
        n.get_mut().hooks = hooks;
    }

    /// Adds the alarms of a packet's fields to its `alarms`, their
    /// criteria bound to its values. An array's alarm is its elements'.
    /// Alarms that can't be evaluated are left out, and `alarm_notes`
    /// says why.
    ///
    /// # Arguments
    ///
    /// * `n` - The packet.
    ///
    /// # Errors
    ///
    /// Cycle, if compiling a criterion finds a loop.
    fn bind_alarms(&mut self, n: &Shared<Node>) -> Result<()> {
        let field_list: Vec<(String, Shared<Local>)> = n.get().fields.clone();
        let mut known: Vec<Shared<Local>> =
            field_list.iter().map(|f| f.1.clone()).collect();
        known.extend(n.get().fixed.0.iter().map(|f| f.0.clone()));
        let scope = n.scope();
        let nkey = n.get().key.clone();
        // A value its inheritance fixes has an alarm too, of that value.
        let mut names =
            Namer::with_reserved(field_list.iter().map(|f| f.0.as_str()));
        let mut all = field_list.clone();
        let fixed_locals: Vec<Shared<Local>> =
            n.get().fixed.0.iter().map(|f| f.0.clone()).collect();
        for lc in fixed_locals {
            let kind = lc.get().vt().kind.clone();
            if matches!(kind.as_str(), "int" | "float" | "enum" | "bool") {
                let id = names.take(&[&snake(&lc.get().name)]);
                all.push((id, lc));
            }
        }
        for (name, lc) in all {
            let vt = lc.get().vt();
            let lname = lc.get().name.clone();
            let target = match &vt.enc {
                Encoding::Array(e) => e.elem.clone(),
                _ => vt.clone(),
            };
            if target.kind == "array" || target.kind == "aggregate" {
                if tree(&target).iter().any(|t| t.alarm.is_some()) {
                    n.get_mut().alarm_notes.push(format!(
                        "{nkey}: the alarms inside {lname} are not evaluated"
                    ));
                }
                continue;
            }
            let Some(a) = target.alarm.clone() else {
                continue;
            };
            if !a.note.is_empty() {
                n.get_mut()
                    .alarm_notes
                    .push(format!("{nkey}: alarm of {lname}: {}", a.note));
                continue;
            }
            let where_ = format!("{nkey}: alarm of {lname}");
            if vt.kind == "array" && history(&a) != 0 {
                n.get_mut()
                    .alarm_notes
                    .push(format!("{where_}: change ranges on an array"));
                continue;
            }
            let bound: Result<Alarm> = (|| {
                let default = match &a.default {
                    Some(d) => {
                        Some(self.bound_def(d, &a, &scope, &known, &where_)?)
                    }
                    None => None,
                };
                let mut contexts = Vec::new();
                for (mc, d) in &a.contexts {
                    let terms = match mc {
                        AlarmContext::Unbound(e) => self.alarm_terms(
                            CritRef::context(&e.value),
                            &a,
                            &scope,
                            &known,
                            &where_,
                        )?,
                        AlarmContext::Bound(t) => t.clone(),
                    };
                    let d = self.bound_def(d, &a, &scope, &known, &where_)?;
                    contexts.push((AlarmContext::Bound(terms), d));
                }
                Ok(Alarm {
                    path: a.path.clone(),
                    default,
                    contexts,
                    note: String::new(),
                    key: lc.get().key.clone(),
                })
            })();
            match bound {
                Ok(b) => n.get_mut().alarms.push((name, lc.clone(), b)),
                Err(Error::Unsupported(m)) => n.get_mut().alarm_notes.push(m),
                Err(e) => return Err(e),
            }
        }
        Ok(())
    }

    /// Compiles an alarm's context or condition on a packet.
    ///
    /// # Arguments
    ///
    /// * `mc`     - The XTCE match criteria.
    /// * `a`      - The alarm.
    /// * `scope`  - The packet's values.
    /// * `known`  - The packet's fields and the values it fixes.
    /// * `where_` - What the alarm is, for error messages.
    ///
    /// # Returns
    ///
    /// Its terms.
    ///
    /// # Errors
    ///
    /// Unsupported, if they use a value the packet computes.
    fn alarm_terms(
        &mut self,
        mc: CritRef<'_>,
        a: &Alarm,
        scope: &[Shared<Local>],
        known: &[Shared<Local>],
        where_: &str,
    ) -> Result<Vec<Term>> {
        let ts = self.match_(mc, &a.path, scope, where_, None)?;
        for t in &ts {
            for x in t.locals() {
                if !holds(known, &x) {
                    return unsupported(format!(
                        "{where_}: criteria on {}, which the packet computes",
                        x.get().name
                    ));
                }
            }
        }
        Ok(ts)
    }

    /// Binds an alarm definition to a packet.
    ///
    /// # Arguments
    ///
    /// * `d`      - The definition.
    /// * `a`      - The alarm it is of.
    /// * `scope`  - The packet's values.
    /// * `known`  - The packet's fields and the values it fixes.
    /// * `where_` - What the alarm is, for error messages.
    ///
    /// # Returns
    ///
    /// A copy with its conditions and its custom alarm's inputs bound.
    ///
    /// # Errors
    ///
    /// Unsupported, if they use a value the packet computes.
    fn bound_def(
        &mut self,
        d: &AlarmDef,
        a: &Alarm,
        scope: &[Shared<Local>],
        known: &[Shared<Local>],
        where_: &str,
    ) -> Result<AlarmDef> {
        let custom = match &d.custom {
            None => None,
            Some(CustomAlarm::Bound(h, i)) => {
                Some(CustomAlarm::Bound(h.clone(), i.clone()))
            }
            Some(CustomAlarm::Unbound(h, e)) => {
                let inputs = self.hook_inputs(
                    &inputs_of(&e.value),
                    &a.path,
                    scope,
                    where_,
                    None,
                )?;
                for x in input_locals(&inputs) {
                    if !holds(known, &x) {
                        return unsupported(format!(
                            "{where_}: input {}, which the packet computes",
                            x.get().name
                        ));
                    }
                }
                Some(CustomAlarm::Bound(h.clone(), inputs))
            }
        };
        let mut conditions = Vec::new();
        for (lv, mc) in &d.conditions {
            let terms = match mc {
                AlarmCriteria::Unbound(e) => self.alarm_terms(
                    CritRef::match_criteria(&e.value),
                    a,
                    scope,
                    known,
                    where_,
                )?,
                AlarmCriteria::Bound(t) => t.clone(),
            };
            conditions.push((*lv, AlarmCriteria::Bound(terms)));
        }
        Ok(AlarmDef {
            conditions,
            custom,
            ..d.clone()
        })
    }

    /// Finds the MathAlgorithms and CustomAlgorithms that a packet
    /// triggers, and adds them to its `algorithms`; one whose inputs the
    /// packet doesn't hold is left out, and `algorithm_notes` says why.
    ///
    /// # Arguments
    ///
    /// * `n` - The packet.
    ///
    /// # Errors
    ///
    /// Cycle, if compiling an expression finds a loop.
    fn bind_algorithms(&mut self, n: &Shared<Node>) -> Result<()> {
        let scope = n.scope();
        let mut known: Vec<Shared<Local>> =
            n.get().fields.iter().map(|f| f.1.clone()).collect();
        known.extend(n.get().fixed.0.iter().map(|f| f.0.clone()));
        let keys: Vec<String> =
            scope.iter().map(|l| l.get().key.clone()).collect();
        let chain: Vec<String> =
            n.chain().iter().map(|x| x.get().key.clone()).collect();
        let nkey = n.get().key.clone();
        let mut names = Namer::default();
        let algs: Vec<(super::Key, (super::Key, &x::AlgorithmSetChoice))> =
            self.algorithms
                .iter()
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect();
        for (key, (path, alg)) in algs {
            let (custom, triggers) = match alg {
                x::AlgorithmSetChoice::CustomAlgorithm(c) => {
                    (Some(c), c.trigger_set.as_ref())
                }
                x::AlgorithmSetChoice::MathAlgorithm(m) => {
                    (None, Some(&m.math_operation.trigger_set))
                }
            };
            let where_ = format!(
                "{nkey}: {} {}",
                if custom.is_some() {
                    "CustomAlgorithm"
                } else {
                    "MathAlgorithm"
                },
                qname(&key)
            );
            let mut triggered = false;
            for ch in triggers.iter().flat_map(|t| t.items.iter()) {
                match ch {
                    x::TriggerSetChoice::OnParameterUpdateTrigger(t) => {
                        let has = |k: &[String]| self.params.contains(k);
                        if let Ok(k) = Self::resolve(
                            &t.parameter_ref,
                            &path,
                            &has,
                            "parameter",
                        ) {
                            triggered |= keys.contains(&qname(&k));
                        }
                    }
                    x::TriggerSetChoice::OnContainerUpdateTrigger(t) => {
                        if let Ok(k) =
                            self.container_key(&t.container_ref, &path)
                        {
                            triggered |= chain.contains(&qname(&k));
                        }
                    }
                    x::TriggerSetChoice::OnPeriodicRateTrigger(_) => {}
                }
            }
            if !triggered {
                continue;
            }
            if let Some(c) = custom {
                self.custom_algorithm(
                    n, &key, &path, c, &scope, &known, &mut names, &where_,
                )?;
                continue;
            }
            let x::AlgorithmSetChoice::MathAlgorithm(m) = alg else {
                unreachable!("a MathAlgorithm")
            };
            let mo = &m.math_operation;
            let got: Result<(String, Expr)> = (|| {
                let has = |k: &[String]| self.params.contains(k);
                let out = qname(&Self::resolve(
                    &mo.output_parameter_ref,
                    &path,
                    &has,
                    "parameter",
                )?);
                let expr = self
                    .math_operation(&triggered_items(mo), &path, &where_)?
                    .expr;
                let expr = self.bind_inputs(&expr, &scope, &known, &where_)?;
                Ok((out, expr))
            })();
            match got {
                Ok((out, expr)) => {
                    let name = names.take(&[&snake(last(&out)), &snake(&out)]);
                    n.get_mut().algorithms.push(Algorithm {
                        key: out,
                        name,
                        expr: Some(expr),
                        hook: None,
                        inputs: Vec::new(),
                        index: 0,
                    });
                }
                Err(Error::Unsupported(e)) => {
                    n.get_mut().algorithm_notes.push(e)
                }
                Err(e) => return Err(e),
            }
        }
        Ok(())
    }

    /// Adds the outputs of a CustomAlgorithm that a packet triggers to its
    /// `algorithms`; or, if it can't be computed, why to its
    /// `algorithm_notes`.
    ///
    /// # Arguments
    ///
    /// * `n`      - The packet.
    /// * `key`    - The algorithm's key.
    /// * `path`   - The key of the SpaceSystem it is defined in.
    /// * `alg`    - The XTCE algorithm.
    /// * `scope`  - The packet's values.
    /// * `known`  - The packet's fields and the values it fixes.
    /// * `names`  - The namer of the packet's algorithm identifiers.
    /// * `where_` - What the algorithm is, for messages.
    ///
    /// # Errors
    ///
    /// Cycle, if compiling an input finds a loop.
    #[allow(clippy::too_many_arguments)]
    fn custom_algorithm(
        &mut self,
        n: &Shared<Node>,
        key: &super::Key,
        path: &super::Key,
        alg: &x::InputOutputTriggerAlgorithmType,
        scope: &[Shared<Local>],
        known: &[Shared<Local>],
        names: &mut Namer,
        where_: &str,
    ) -> Result<()> {
        let got: Result<(Vec<Input>, Vec<String>)> = (|| {
            let inputs = self.hook_inputs(
                &set_inputs(alg.input_set.as_ref()),
                path,
                scope,
                where_,
                None,
            )?;
            for x in input_locals(&inputs) {
                if !holds(known, &x) {
                    return unsupported(format!(
                        "{where_}: input {} is computed by the packet",
                        x.get().name
                    ));
                }
            }
            let has = |k: &[String]| self.params.contains(k);
            let mut outs = Vec::new();
            for o in alg
                .output_set
                .iter()
                .flat_map(|s| s.output_parameter_ref.iter())
            {
                outs.push(qname(&Self::resolve(
                    &o.parameter_ref,
                    path,
                    &has,
                    "parameter",
                )?));
            }
            if outs.is_empty() {
                return unsupported(format!("{where_}: no outputs"));
            }
            Ok((inputs, outs))
        })();
        let (inputs, outs) = match got {
            Ok(g) => g,
            Err(Error::Unsupported(e)) => {
                n.get_mut().algorithm_notes.push(e);
                return Ok(());
            }
            Err(e) => return Err(e),
        };
        let hook = self.hook("algorithm", &qname(key), outs.len() as i64);
        for (i, out) in outs.into_iter().enumerate() {
            let name = names.take(&[&snake(last(&out)), &snake(&out)]);
            n.get_mut().algorithms.push(Algorithm {
                key: out,
                name,
                expr: None,
                hook: Some(hook.clone()),
                inputs: inputs.clone(),
                index: i as i64,
            });
        }
        Ok(())
    }

    /// Binds a MathAlgorithm's expression to the fields of a packet.
    ///
    /// # Arguments
    ///
    /// * `e`      - The expression tree, or a subtree.
    /// * `scope`  - The packet's values.
    /// * `known`  - The packet's fields and the values it fixes.
    /// * `where_` - What the algorithm is, for error messages.
    ///
    /// # Returns
    ///
    /// A copy of the tree whose ValueRefs hold the Locals they use.
    ///
    /// # Errors
    ///
    /// Unsupported, if it uses the output's own earlier value, or an input
    /// the packet computes, isn't a number, or is raw but calibrated.
    fn bind_inputs(
        &self,
        e: &Expr,
        scope: &[Shared<Local>],
        known: &[Shared<Local>],
        where_: &str,
    ) -> Result<Expr> {
        match e {
            Expr::Const(_) => Ok(e.clone()),
            Expr::Raw => unsupported(format!(
                "{where_}: ThisParameterOperand, the output's own earlier value"
            )),
            Expr::Op(op, args) => {
                let mut out = Vec::new();
                for a in args {
                    out.push(self.bind_inputs(a, scope, known, where_)?);
                }
                Ok(Expr::Op(op.clone(), out))
            }
            Expr::Value(r) => {
                let r = r.get();
                let xr = XRef::param(&r.ref_.value);
                let (local, member, vt, name) =
                    self.value_ref(&xr, &r.path, scope, where_, None)?;
                if !holds(known, &local) {
                    return unsupported(format!(
                        "{where_}: input {name} is computed by the packet"
                    ));
                }
                if !matches!(
                    vt.kind.as_str(),
                    "int" | "float" | "enum" | "bool"
                ) {
                    return unsupported(format!(
                        "{where_}: input {name} is a {} value",
                        vt.kind
                    ));
                }
                if r.ref_.value.use_calibrated_value == Some(false)
                    && vt.calibrator.is_some()
                {
                    return unsupported(format!(
                        "{where_}: the raw value of calibrated {name}"
                    ));
                }
                Ok(Expr::Value(Shared::new(ValueRef {
                    ref_: r.ref_.clone(),
                    path: r.path.clone(),
                    local: Some(local),
                    member,
                    vtype: Some(vt),
                })))
            }
        }
    }
}
