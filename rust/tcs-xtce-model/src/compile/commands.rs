//! Commands: meta-commands, their arguments, argument assignments and
//! CommandContainers, the containers they include, and messages, as the
//! Python compiler's `tc_node`, `tc_items`, `msg_node` and what they call
//! make them.

use std::collections::HashMap;
use std::rc::Rc;

use num_bigint::BigInt;
use num_traits::Zero;
use tcs_xtce::generated as x;

use super::bind::{dims_a, dims_p, length_adjust, LocRef, RepeatRef};
use super::checks::{check_length, edc_items};
use super::containers::{
    check_tail, has_transform, measure, Indirect, SegRef, Segs,
};
use super::criteria::{literal, ArgKey, CritRef};
use super::layout::check_aligned;
use super::types::{hex_bytes, IntValue, IntValueRef};
use super::{qname, Bin, Compiler, TypeTable};
use crate::dump::Shared;
use crate::model::{
    unsupported, walk, Cond, Count, Criterion, Error, Fixed, Item, Key, Local,
    Mark, Node, Require, Result, Seek, Term, VType, Value,
};

/// The arguments visible in a meta-command: each one's name, with the key
/// of the meta-command that has it and its definition.
pub type ArgScope<'a> = HashMap<String, (Key, &'a x::ArgumentType)>;

/// What an entry holds, of a CommandContainer or of a container a command
/// includes.
#[derive(Clone, Copy)]
enum Kind<'b> {
    /// A ParameterRefEntry or ArrayParameterRefEntry.
    Param {
        /// The parameter's reference.
        name: &'b str,
        /// Its RepeatEntry.
        rp: Option<RepeatRef<'b>>,
        /// Its DimensionList, for an array entry.
        dl: Option<Dims<'b>>,
    },
    /// An ArgumentRefEntry or ArrayArgumentRefEntry.
    Arg {
        /// The argument's name.
        name: &'b str,
        /// Its RepeatEntry.
        rp: Option<RepeatRef<'b>>,
        /// Its DimensionList, for an array entry.
        dl: Option<Dims<'b>>,
    },
    /// A segment entry.
    Seg(SegRef<'b>),
    /// An IndirectParameterRefEntry.
    Indirect(Indirect<'b>),
    /// A ContainerRefEntry.
    Container {
        /// The container's reference.
        name: &'b str,
        /// Its RepeatEntry.
        rp: Option<RepeatRef<'b>>,
    },
    /// A FixedValueEntry.
    Fixed(&'b x::ArgumentFixedValueEntryType),
}

/// A DimensionList, of either kind.
#[derive(Clone, Copy)]
enum Dims<'b> {
    /// A parameter entry's.
    P(&'b x::DimensionListType),
    /// An argument entry's.
    A(&'b x::ArgumentDimensionListType),
}

impl<'b> Dims<'b> {
    /// Lists each dimension's bounds.
    ///
    /// # Returns
    ///
    /// The starting and ending index of each.
    fn bounds(self) -> Vec<(IntValueRef<'b>, IntValueRef<'b>)> {
        match self {
            Dims::P(d) => dims_p(d),
            Dims::A(d) => dims_a(d),
        }
    }
}

/// An entry of a CommandContainer, or of a container a command includes.
#[derive(Clone, Copy)]
struct Entry<'b> {
    /// Its element's name, for error messages.
    name: &'static str,
    /// Its location.
    loc: Option<LocRef<'b>>,
    /// Its IncludeCondition.
    cond: Option<CritRef<'b>>,
    /// What it holds.
    kind: Kind<'b>,
}

/// Views the entries of a container a command includes.
///
/// # Arguments
///
/// * `sc` - The container.
///
/// # Returns
///
/// Its entries, in order.
fn tm_entries(sc: &x::SequenceContainerType) -> Vec<Entry<'_>> {
    use x::EntryListChoice as E;
    sc.entry_list
        .items
        .iter()
        .map(|ch| {
            macro_rules! e {
                ($v:expr, $name:literal, $kind:expr) => {
                    Entry {
                        name: $name,
                        loc: $v
                            .location_in_container_in_bits
                            .as_ref()
                            .map(LocRef::P),
                        cond: $v
                            .include_condition
                            .as_ref()
                            .map(CritRef::match_criteria),
                        kind: $kind,
                    }
                };
            }
            match ch {
                E::ParameterRefEntry(v) => e!(
                    v,
                    "ParameterRefEntry",
                    Kind::Param {
                        name: &v.parameter_ref,
                        rp: RepeatRef::of(v.repeat_entry.as_ref()),
                        dl: None,
                    }
                ),
                E::ArrayParameterRefEntry(v) => e!(
                    v,
                    "ArrayParameterRefEntry",
                    Kind::Param {
                        name: &v.parameter_ref,
                        rp: RepeatRef::of(v.repeat_entry.as_ref()),
                        dl: v.dimension_list.as_ref().map(Dims::P),
                    }
                ),
                E::ParameterSegmentRefEntry(v) => e!(
                    v,
                    "ParameterSegmentRefEntry",
                    Kind::Seg(SegRef::Param(v))
                ),
                E::ContainerSegmentRefEntry(v) => e!(
                    v,
                    "ContainerSegmentRefEntry",
                    Kind::Seg(SegRef::Container(v))
                ),
                E::StreamSegmentEntry(v) => {
                    e!(v, "StreamSegmentEntry", Kind::Seg(SegRef::Stream(v)))
                }
                E::IndirectParameterRefEntry(v) => e!(
                    v,
                    "IndirectParameterRefEntry",
                    Kind::Indirect((
                        v.alias_name_space.as_deref(),
                        v.repeat_entry.is_some(),
                        &v.parameter_instance,
                    ))
                ),
                E::ContainerRefEntry(v) => e!(
                    v,
                    "ContainerRefEntry",
                    Kind::Container {
                        name: &v.container_ref,
                        rp: RepeatRef::of(v.repeat_entry.as_ref()),
                    }
                ),
            }
        })
        .collect()
}

/// Views the entries of a CommandContainer.
///
/// # Arguments
///
/// * `cc` - The container.
///
/// # Returns
///
/// Its entries, in order.
fn tc_entries(cc: &x::CommandContainerType) -> Vec<Entry<'_>> {
    use x::CommandContainerEntryListChoice as E;
    cc.entry_list
        .items
        .iter()
        .map(|ch| {
            macro_rules! e {
                ($v:expr, $name:literal, $kind:expr) => {
                    Entry {
                        name: $name,
                        loc: $v
                            .location_in_container_in_bits
                            .as_ref()
                            .map(LocRef::A),
                        cond: $v
                            .include_condition
                            .as_ref()
                            .map(CritRef::arg_match),
                        kind: $kind,
                    }
                };
            }
            match ch {
                E::ParameterRefEntry(v) => e!(
                    v,
                    "ParameterRefEntry",
                    Kind::Param {
                        name: &v.parameter_ref,
                        rp: RepeatRef::arg(v.repeat_entry.as_ref()),
                        dl: None,
                    }
                ),
                E::ArrayParameterRefEntry(v) => e!(
                    v,
                    "ArrayParameterRefEntry",
                    Kind::Param {
                        name: &v.parameter_ref,
                        rp: RepeatRef::arg(v.repeat_entry.as_ref()),
                        dl: v.dimension_list.as_ref().map(Dims::P),
                    }
                ),
                E::ArgumentRefEntry(v) => e!(
                    v,
                    "ArgumentRefEntry",
                    Kind::Arg {
                        name: &v.argument_ref,
                        rp: RepeatRef::arg(v.repeat_entry.as_ref()),
                        dl: None,
                    }
                ),
                E::ArrayArgumentRefEntry(v) => e!(
                    v,
                    "ArrayArgumentRefEntry",
                    Kind::Arg {
                        name: &v.argument_ref,
                        rp: RepeatRef::arg(v.repeat_entry.as_ref()),
                        dl: v.dimension_list.as_ref().map(Dims::A),
                    }
                ),
                E::ParameterSegmentRefEntry(v) => e!(
                    v,
                    "ParameterSegmentRefEntry",
                    Kind::Seg(SegRef::AParam(v))
                ),
                E::ContainerSegmentRefEntry(v) => e!(
                    v,
                    "ContainerSegmentRefEntry",
                    Kind::Seg(SegRef::AContainer(v))
                ),
                E::StreamSegmentEntry(v) => {
                    e!(v, "StreamSegmentEntry", Kind::Seg(SegRef::AStream(v)))
                }
                E::IndirectParameterRefEntry(v) => e!(
                    v,
                    "IndirectParameterRefEntry",
                    Kind::Indirect((
                        v.alias_name_space.as_deref(),
                        v.repeat_entry.is_some(),
                        &v.parameter_instance,
                    ))
                ),
                E::ContainerRefEntry(v) => e!(
                    v,
                    "ContainerRefEntry",
                    Kind::Container {
                        name: &v.container_ref,
                        rp: RepeatRef::arg(v.repeat_entry.as_ref()),
                    }
                ),
                E::FixedValueEntry(v) => {
                    e!(v, "FixedValueEntry", Kind::Fixed(v))
                }
            }
        })
        .collect()
}

/// Gives the key of an argument visible in a meta-command.
///
/// # Arguments
///
/// * `scope`  - The arguments visible, as `arg_scope` gives them.
/// * `name`   - The argument's name.
/// * `where_` - What refers to it, for error messages.
///
/// # Returns
///
/// Its key: the qualified name of the meta-command that has it, `#` and
/// its name.
///
/// # Errors
///
/// Unsupported, if no such argument is visible.
pub fn arg_key_of(
    scope: &ArgScope<'_>,
    name: &str,
    where_: &str,
) -> Result<String> {
    match scope.get(name) {
        Some((owner, _)) => Ok(format!("{}#{name}", qname(owner))),
        None => unsupported(format!(
            "{where_}: unknown argument {}",
            crate::pyfmt::repr(name)
        )),
    }
}

/// Gives the text of a set of criteria, as a Require writes it.
///
/// # Arguments
///
/// * `terms` - The criteria.
///
/// # Returns
///
/// Each one's text, joined by `and`.
fn and_text(terms: &[Term]) -> String {
    let t: Vec<String> = terms.iter().map(|t| t.text()).collect();
    t.join(" and ")
}

/// Makes the criterion that an argument assignment fixes.
///
/// # Arguments
///
/// * `local`  - The argument's value.
/// * `name`   - The argument's name.
/// * `text`   - The value assigned, as written.
/// * `where_` - What assigns it, for error messages.
///
/// # Returns
///
/// The criterion `name == text`.
///
/// # Errors
///
/// Unsupported, if the value doesn't fit the argument's type.
fn assignment(
    local: Shared<Local>,
    name: &str,
    text: &str,
    where_: &str,
) -> Result<Term> {
    let vt = local.get().vt();
    let value = literal(&vt, text, false, where_)?;
    Ok(Term::Criterion(Shared::new(Criterion {
        local,
        op: "==".into(),
        value: Some(value),
        text: format!("{name} = {text}"),
        path: Vec::new(),
        target: None,
        other: None,
        other_path: Vec::new(),
        other_target: None,
    })))
}

impl<'a> Compiler<'a> {
    /// Resolves a reference to a meta-command.
    ///
    /// # Arguments
    ///
    /// * `ref_` - The reference.
    /// * `path` - The key of the SpaceSystem it is written in.
    ///
    /// # Returns
    ///
    /// The meta-command's key.
    ///
    /// # Errors
    ///
    /// Unsupported, if it names none.
    fn mc_key(&self, ref_: &str, path: &[String]) -> Result<Key> {
        let has = |k: &[String]| self.mcs.contains(k);
        Self::resolve(ref_, path, &has, "meta-command")
    }

    /// Resolves a reference to a container a command may include.
    ///
    /// # Arguments
    ///
    /// * `ref_` - The reference.
    /// * `path` - The key of the SpaceSystem it is written in.
    ///
    /// # Returns
    ///
    /// The container's key in `tc_containers`.
    ///
    /// # Errors
    ///
    /// Unsupported, if it names none.
    fn tc_key(&self, ref_: &str, path: &[String]) -> Result<Key> {
        let has = |k: &[String]| self.tc_containers.contains(k);
        Self::resolve(ref_, path, &has, "container")
    }

    /// Resolves a reference to a meta-command's CommandContainer.
    ///
    /// # Arguments
    ///
    /// * `ref_` - The reference.
    /// * `path` - The key of the SpaceSystem it is written in.
    ///
    /// # Returns
    ///
    /// The CommandContainer's key in `mc_containers`.
    ///
    /// # Errors
    ///
    /// Unsupported, if it names none.
    fn mcc_key(&self, ref_: &str, path: &[String]) -> Result<Key> {
        let has = |k: &[String]| self.mc_containers.contains_key(k);
        Self::resolve(ref_, path, &has, "command container")
    }

    /// Collects the arguments visible in a MetaCommand: its own and its
    /// bases'.
    ///
    /// # Arguments
    ///
    /// * `key`  - The MetaCommand's key.
    /// * `seen` - The keys of the meta-commands derived from it, to find
    ///            cycles; empty when called from outside.
    ///
    /// # Returns
    ///
    /// The arguments; its own replace its bases'.
    ///
    /// # Errors
    ///
    /// Cycle, if the inheritance chain is a loop; Unsupported, if a base
    /// doesn't resolve.
    pub fn arg_scope(&self, key: &Key, seen: &[Key]) -> Result<ArgScope<'a>> {
        if seen.contains(key) {
            return Err(Error::Cycle(format!(
                "meta-command inheritance loop through {}",
                qname(key)
            )));
        }
        let (path, mc) = self.mcs.get(key).unwrap().clone();
        let mut scope = ArgScope::new();
        if let Some(b) = &mc.base_meta_command {
            let bkey = self.mc_key(&b.meta_command_ref, &path)?;
            let mut s = seen.to_vec();
            s.push(key.clone());
            scope.extend(self.arg_scope(&bkey, &s)?);
        }
        for a in mc.argument_list.iter().flat_map(|l| l.argument.iter()) {
            scope.insert(a.name.clone(), (key.clone(), a));
        }
        Ok(scope)
    }

    /// Makes a new value of an argument, for an entry that holds it.
    ///
    /// # Arguments
    ///
    /// * `scope`  - The arguments visible, as `arg_scope` gives them.
    /// * `name`   - The argument's name.
    /// * `where_` - What refers to it, for error messages.
    ///
    /// # Returns
    ///
    /// The Local, with the argument's type, and its length adjustment if
    /// it is a packet length field.
    ///
    /// # Errors
    ///
    /// Unsupported, if the argument isn't visible, or its type isn't
    /// supported.
    pub fn arg_local(
        &mut self,
        scope: &ArgScope<'a>,
        name: &str,
        where_: &str,
    ) -> Result<Shared<Local>> {
        let Some((owner, a)) = scope.get(name).cloned() else {
            return unsupported(format!(
                "{where_}: unknown argument {}",
                crate::pyfmt::repr(name)
            ));
        };
        let opath = self.mcs.get(&owner).unwrap().0.clone();
        let tkey = self.resolve_type(
            &a.argument_type_ref,
            &opath,
            TypeTable::Arg,
            "argument type",
        )?;
        let vt = self.vtype(&tkey, TypeTable::Arg)?;
        let local =
            self.local(&a.name, &format!("{}#{}", qname(&owner), a.name), vt);
        let lvt = local.get().vt();
        let key = local.get().key.clone();
        let len = length_adjust(
            a.ancillary_data_set.as_ref(),
            &lvt,
            &format!("argument {key}"),
        )?;
        local.get_mut().length = len;
        Ok(local)
    }

    /// Compiles a Message: the root container it names, chosen by its
    /// MatchCriteria, like a container derived from it with no entries of
    /// its own.
    ///
    /// # Arguments
    ///
    /// * `key` - The Message's key.
    ///
    /// # Returns
    ///
    /// Its Node; one that can't be compiled has an `error` saying why.
    ///
    /// # Errors
    ///
    /// Cycle, if compiling its container finds a loop.
    pub fn msg_node(&mut self, key: &Key) -> Result<Shared<Node>> {
        let (path, m) = self.messages.get(key).unwrap().clone();
        let node = Shared::new(Node {
            kind: "tm".into(),
            key: qname(key),
            name: m.name.clone(),
            abstract_: false,
            ..Node::default()
        });
        // Messages are named apart from containers.
        let mut mkey = vec!["message".to_string()];
        mkey.extend(key.iter().cloned());
        self.nodes.insert(mkey, node.clone());
        let where_ = format!("message {}", node.get().key);
        let built: Result<()> = (|| {
            let ckey =
                self.container_key(&m.container_ref.container_ref, &path)?;
            let base = self.tm_node(&ckey)?;
            if base.get().error.is_some() {
                return unsupported(format!(
                    "base container {} is not supported",
                    base.get().key
                ));
            }
            node.get_mut().base = Some(base.clone());
            let crit = self.match_(
                CritRef::match_criteria(&m.match_criteria),
                &path,
                &base.scope(),
                &where_,
                None,
            )?;
            if crit.is_empty() {
                return unsupported(format!(
                    "{where_}: MatchCriteria with no comparisons"
                ));
            }
            node.get_mut().criteria = crit;
            base.get_mut().children.push(node.clone());
            Ok(())
        })();
        match built {
            Ok(()) => {}
            Err(Error::Unsupported(msg)) => {
                let mut n = node.get_mut();
                n.error = Some(msg);
                n.base = None;
            }
            Err(e) => return Err(e),
        }
        self.order.push(node.clone());
        Ok(node)
    }

    /// Compiles a MetaCommand, and its bases, once.
    ///
    /// # Arguments
    ///
    /// * `key` - The MetaCommand's key.
    ///
    /// # Returns
    ///
    /// Its Node; one that can't be compiled has an `error` saying why.
    ///
    /// # Errors
    ///
    /// Cycle, if its inheritance chain is a loop, or a container includes
    /// itself.
    pub fn tc_node(&mut self, key: &Key) -> Result<Shared<Node>> {
        if let Some(n) = self.nodes.get(key) {
            if n.get().building {
                return Err(Error::Cycle(format!(
                    "meta-command inheritance loop through {}",
                    qname(key)
                )));
            }
            return Ok(n.clone());
        }
        let (path, mc) = self.mcs.get(key).unwrap().clone();
        let node = Shared::new(Node {
            kind: "tc".into(),
            key: qname(key),
            name: mc.name.clone(),
            abstract_: mc.abstract_.unwrap_or(false),
            building: true,
            ..Node::default()
        });
        self.nodes.insert(key.clone(), node.clone());
        match self.tc_build(&node, key, &path, mc) {
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

    /// Builds a MetaCommand's node, for `tc_node`.
    ///
    /// # Arguments
    ///
    /// * `node` - The node, being built.
    /// * `key`  - The MetaCommand's key.
    /// * `path` - The key of the SpaceSystem it is defined in.
    /// * `mc`   - The MetaCommand.
    ///
    /// # Errors
    ///
    /// Unsupported, if it can't be compiled; Cycle, for a loop.
    fn tc_build(
        &mut self,
        node: &Shared<Node>,
        key: &Key,
        path: &Key,
        mc: &'a x::MetaCommandType,
    ) -> Result<()> {
        let nkey = node.get().key.clone();
        let where_ = format!("meta-command {nkey}");
        let mut base: Option<Shared<Node>> = None;
        let scope = self.arg_scope(key, &[])?;
        let ak = |n: &str| arg_key_of(&scope, n, &where_);
        if let Some(bmc) = &mc.base_meta_command {
            let bkey = self.mc_key(&bmc.meta_command_ref, path)?;
            let b = self.tc_node(&bkey)?;
            if b.get().error.is_some() {
                return unsupported(format!(
                    "base meta-command {} is not supported",
                    b.get().key
                ));
            }
            node.get_mut().base = Some(b.clone());
            let bscope = b.scope();
            for asg in bmc
                .argument_assignment_list
                .iter()
                .flat_map(|l| l.argument_assignment.iter())
            {
                let name = &asg.argument_name;
                let Some((owner, _)) = scope.get(name) else {
                    return unsupported(format!(
                        "{where_}: assignment to unknown argument {}",
                        crate::pyfmt::repr(name)
                    ));
                };
                let akey = format!("{}#{name}", qname(owner));
                let Some(local) =
                    bscope.iter().rev().find(|l| l.get().key == akey).cloned()
                else {
                    // The argument is not encoded in the packet.
                    continue;
                };
                let c = assignment(local, name, &asg.argument_value, &where_)?;
                node.get_mut().criteria.push(c);
            }
            base = Some(b);
        }
        if let Some(cc) = &mc.command_container {
            let be = cc.binary_encoding.as_ref();
            self.node_edc.insert(node.addr(), Some((be, path.clone())));
            let mut values =
                base.as_ref().map(|b| b.scope()).unwrap_or_default();
            let mut prefix: Vec<Item> = Vec::new();
            let mut shared = None;
            let mut bc = None;
            if let Some(bcon) = &cc.base_container {
                match self.mcc_key(&bcon.container_ref, path) {
                    Ok(k) => bc = Some(k),
                    // A container of a CommandContainerSet, or of
                    // telemetry: its entries come first.
                    Err(Error::Unsupported(_)) => {
                        shared = Some(self.tc_key(&bcon.container_ref, path)?)
                    }
                    Err(e) => return Err(e),
                }
            }
            if let Some(sh) = &shared {
                if base.is_some() {
                    return unsupported(format!(
                        "{where_}: a CommandContainer that extends a container \
                         other than its BaseMetaCommand's"
                    ));
                }
                prefix =
                    self.tc_include(sh, &scope, &where_, &mut values, &[])?;
                let rc = cc
                    .base_container
                    .as_ref()
                    .and_then(|b| b.restriction_criteria.as_ref());
                if let Some(rc) = rc {
                    let terms = self.match_(
                        CritRef::restriction(rc),
                        path,
                        &values,
                        &where_,
                        Some(&ak),
                    )?;
                    let text = and_text(&terms);
                    prefix.push(Item::Require(Require {
                        criteria: terms,
                        container: qname(sh),
                        text,
                        fixes: false,
                    }));
                }
            } else if let Some(bc) = &bc {
                let owner = self.mc_containers.get(bc).unwrap().clone();
                let same = match (&base, self.nodes.get(&owner)) {
                    (Some(b), Some(n)) => b.same(n),
                    _ => false,
                };
                if !same {
                    return unsupported(format!(
                        "{where_}: CommandContainer extends {}, which is not \
                         the container of its BaseMetaCommand",
                        qname(bc)
                    ));
                }
                let rc = cc
                    .base_container
                    .as_ref()
                    .and_then(|b| b.restriction_criteria.as_ref());
                if rc.is_some_and(|r| r.next_container.is_some()) {
                    return unsupported(format!(
                        "{where_}: NextContainer on a CommandContainer"
                    ));
                }
                if let Some(rc) = rc {
                    // Like argument assignments: what they fix, encoding
                    // writes, and decoding picks the command by them.
                    let bscope = base.as_ref().unwrap().scope();
                    let terms = self.match_(
                        CritRef::restriction(rc),
                        path,
                        &bscope,
                        &where_,
                        Some(&ak),
                    )?;
                    node.get_mut().criteria.extend(terms);
                }
            }
            // A CommandContainer without a BaseContainer still extends the
            // container of its BaseMetaCommand, as Yamcs reads it.
            let entries = tc_entries(cc);
            if has_transform(be) {
                // Its checks are in it.
                self.node_edc.insert(node.addr(), None);
                let m = self.next_id();
                let mut inner = vec![Item::Mark(Mark { id: m })];
                inner.extend(self.tc_items(
                    &entries,
                    be,
                    &scope,
                    path,
                    &where_,
                    &mut values,
                    m,
                    &[],
                )?);
                let wrapped = self
                    .wrap_transform(be, inner, m, &where_, path, &nkey, true)?;
                prefix.extend(wrapped);
                node.get_mut().items = prefix;
            } else {
                let own = self.tc_items(
                    &entries,
                    be,
                    &scope,
                    path,
                    &where_,
                    &mut values,
                    0,
                    &[],
                )?;
                prefix.extend(own);
                node.get_mut().items = prefix;
                // A size covers the whole command, base containers'
                // entries too.
                let all = node.all_items();
                let pad =
                    self.size_pad(be, &all, 0, &where_, Some(&values), path)?;
                node.get_mut().items.extend(pad);
            }
        }
        self.check_rest(node, &where_)?;
        check_tail(node, &where_)?;
        check_aligned(&node.all_items(), &where_)?;
        check_length(node, &where_)?;
        if !node.get().abstract_ {
            let checks = self.node_checks(node, &where_)?;
            node.get_mut().checks = checks;
        }
        if let Some(b) = base {
            b.get_mut().children.push(node.clone());
        }
        Ok(())
    }

    /// Compiles a CommandContainer's entries, or those of a container it
    /// includes, into items.
    ///
    /// # Arguments
    ///
    /// * `entries` - The container's entries.
    /// * `be`      - The container's BinaryEncoding.
    /// * `scope`   - The arguments visible, as `arg_scope` gives them.
    /// * `path`    - The key of the SpaceSystem the container is in.
    /// * `where_`  - What the command is, for error messages.
    /// * `values`  - The values encoded so far (base meta-commands first);
    ///               grows as entries are added.
    /// * `start`   - The id of the Mark at the container's start.
    /// * `stack`   - The keys of the containers being included (a
    ///               meta-command's after `meta-command`), to find ones
    ///               that include themselves.
    ///
    /// # Returns
    ///
    /// The items.
    ///
    /// # Errors
    ///
    /// Unsupported, if an entry uses something not supported; Cycle, if a
    /// container includes itself.
    #[allow(clippy::too_many_arguments)]
    fn tc_items(
        &mut self,
        entries: &[Entry<'a>],
        be: Bin<'a>,
        scope: &ArgScope<'a>,
        path: &Key,
        where_: &str,
        values: &mut Vec<Shared<Local>>,
        start: i64,
        stack: &[Key],
    ) -> Result<Vec<Item>> {
        let ak = |n: &str| arg_key_of(scope, n, where_);
        let arg_key: ArgKey<'_> = Some(&ak);
        let mut items: Vec<Item> = Vec::new();
        let mut segs = Segs::default();
        for en in entries {
            let cond = match en.cond {
                Some(c) => Some(self.match_(c, path, values, where_, arg_key)?),
                None => None,
            };
            let loc =
                self.location(en.loc, start, path, values, where_, arg_key)?;
            let mut out = Self::next_seek(&mut items, loc, where_)?;
            let at_end = matches!(out.last(), Some(Item::EndSeek(_)));
            if at_end
                && !matches!(
                    en.kind,
                    Kind::Param { .. } | Kind::Arg { .. } | Kind::Seg(_)
                )
            {
                return unsupported(format!(
                    "{where_}: a {} located from the container's end",
                    en.name
                ));
            }
            match en.kind {
                Kind::Seg(seg) => {
                    let es = match out.last() {
                        Some(Item::EndSeek(es)) => Some(es.clone()),
                        _ => None,
                    };
                    let before = values.clone();
                    let got = self.segment(
                        seg,
                        path,
                        where_,
                        &mut segs,
                        cond.is_some(),
                        values,
                        arg_key,
                    )?;
                    let mut after = Vec::new();
                    if let Some(es) = es {
                        let bits = self.segment_bits(seg, path, where_)?;
                        let (s, a) = self.end_seek(
                            &es,
                            be,
                            super::containers::varying_segment(&got, bits),
                            where_,
                            bits.unwrap_or(0),
                            Some(&before),
                            path,
                            arg_key,
                        )?;
                        *out.last_mut().unwrap() = s;
                        after = a;
                    }
                    out.extend(got);
                    out.extend(after);
                }
                Kind::Indirect(ind) => {
                    let got =
                        self.indirect(ind, path, values, where_, arg_key)?;
                    out.extend(got);
                }
                Kind::Container { name, rp } => {
                    if let Some(owner) = self.mc_owner(name, path) {
                        if rp.is_none() {
                            let got =
                                self.mc_include(&owner, where_, values, stack)?;
                            out.extend(measure(got, where_)?);
                        } else {
                            let vt =
                                self.mc_container_type(&owner, where_, stack)?;
                            let cname = self
                                .mcs
                                .get(&owner)
                                .unwrap()
                                .1
                                .command_container
                                .as_ref()
                                .map(|c| c.name.clone())
                                .unwrap_or_default();
                            let local = self.local(&cname, &qname(&owner), vt);
                            let local = self.repeat(local, rp, path, where_)?;
                            let local =
                                self.bind(local, values, where_, arg_key)?;
                            out.push(Item::Value(Value {
                                local: local.clone(),
                                assembled: 0,
                            }));
                            values.push(local);
                        }
                    } else {
                        let ckey = self.tc_key(name, path)?;
                        if rp.is_none() {
                            let got = self.tc_include(
                                &ckey, scope, where_, values, stack,
                            )?;
                            out.extend(measure(got, where_)?);
                        } else {
                            let inc = self.tc_containers.get(&ckey).unwrap().1;
                            if inc.base_container.is_some() {
                                return unsupported(format!(
                                    "{where_}: repeated container {} has a \
                                     BaseContainer",
                                    qname(&ckey)
                                ));
                            }
                            let vt = self
                                .container_type(&ckey, stack, where_, true)?;
                            let local =
                                self.local(&inc.name, &qname(&ckey), vt);
                            let local = self.repeat(local, rp, path, where_)?;
                            let local =
                                self.bind(local, values, where_, arg_key)?;
                            out.push(Item::Value(Value {
                                local: local.clone(),
                                assembled: 0,
                            }));
                            values.push(local);
                        }
                    }
                }
                Kind::Param { name, rp, dl } | Kind::Arg { name, rp, dl } => {
                    let local = if matches!(en.kind, Kind::Arg { .. }) {
                        self.arg_local(scope, name, where_)?
                    } else {
                        self.param_local(name, path)?
                    };
                    let local =
                        self.subset(local, dl.map(Dims::bounds), path, where_)?;
                    let local = self.repeat(local, rp, path, where_)?;
                    let local = self.bind(local, values, where_, arg_key)?;
                    let mut after = Vec::new();
                    if let Some(Item::EndSeek(es)) = out.last() {
                        let es = es.clone();
                        let (s, a) = self.end_seek(
                            &es,
                            be,
                            Some(local.clone()),
                            where_,
                            0,
                            Some(values),
                            path,
                            arg_key,
                        )?;
                        *out.last_mut().unwrap() = s;
                        after = a;
                    }
                    out.push(Item::Value(Value {
                        local: local.clone(),
                        assembled: 0,
                    }));
                    out.extend(after);
                    values.push(local);
                }
                Kind::Fixed(e) => {
                    let got = self.fixed_entry(e, path, where_)?;
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
        Self::no_next(&items, where_)?;
        self.raw_segments(&mut items, segs, values)?;
        Ok(items)
    }

    /// Compiles a container that a command includes: its bases' entries,
    /// then its own, each base's RestrictionCriteria checked after its
    /// entries, and padding up to its size.
    ///
    /// # Arguments
    ///
    /// * `ckey`   - The container's key in `tc_containers`.
    /// * `scope`  - The arguments visible, as `arg_scope` gives them.
    /// * `where_` - What the command is, for error messages.
    /// * `values` - The values encoded so far; grows as entries are added.
    /// * `stack`  - The keys of the containers being included, to find ones
    ///              that include themselves.
    ///
    /// # Returns
    ///
    /// The items, starting with a Mark and in a Transform if the container
    /// has transform algorithms.
    ///
    /// # Errors
    ///
    /// Unsupported, if a container has ErrorDetectCorrect, or a base has
    /// transform algorithms; Cycle, if a container includes itself.
    fn tc_include(
        &mut self,
        ckey: &Key,
        scope: &ArgScope<'a>,
        where_: &str,
        values: &mut Vec<Shared<Local>>,
        stack: &[Key],
    ) -> Result<Vec<Item>> {
        let m = self.next_id();
        let mut items = vec![Item::Mark(Mark { id: m })];
        let ak = |n: &str| arg_key_of(scope, n, where_);
        let mut chain: Vec<(Key, &'a x::SequenceContainerType, Key)> =
            Vec::new();
        let mut key = ckey.clone();
        loop {
            if stack.contains(&key) || chain.iter().any(|c| c.2 == key) {
                return Err(Error::Cycle(format!(
                    "{where_}: container {} includes itself, a loop",
                    qname(&key)
                )));
            }
            let (kpath, c) = self.tc_containers.get(&key).unwrap().clone();
            chain.push((kpath.clone(), c, key.clone()));
            match &c.base_container {
                None => break,
                Some(b) => key = self.tc_key(&b.container_ref, &kpath)?,
            }
        }
        let mut inner: Vec<Item> = Vec::new();
        for (kpath, c, key) in chain.iter().rev() {
            if !edc_items(c.binary_encoding.as_ref()).is_empty() {
                return unsupported(format!(
                    "{where_}: ErrorDetectCorrect in container {}, which a \
                     command includes",
                    qname(key)
                ));
            }
            let mut st = stack.to_vec();
            st.push(key.clone());
            let entries = tm_entries(c);
            inner.extend(self.tc_items(
                &entries,
                c.binary_encoding.as_ref(),
                scope,
                kpath,
                where_,
                values,
                m,
                &st,
            )?);
            let rc = c
                .base_container
                .as_ref()
                .and_then(|b| b.restriction_criteria.as_ref());
            if let Some(rc) = rc {
                let terms = self.match_(
                    CritRef::restriction(rc),
                    kpath,
                    values,
                    where_,
                    Some(&ak),
                )?;
                let text = and_text(&terms);
                inner.push(Item::Require(Require {
                    criteria: terms,
                    container: qname(key),
                    text,
                    fixes: false,
                }));
            }
        }
        let (cpath, inc) = self.tc_containers.get(ckey).unwrap().clone();
        for (_, c, key) in &chain[1..] {
            if has_transform(c.binary_encoding.as_ref()) {
                return unsupported(format!(
                    "{where_}: transform algorithms in base container {}",
                    qname(key)
                ));
            }
        }
        let be = inc.binary_encoding.as_ref();
        let pad = self.size_pad(be, &inner, m, where_, Some(values), &cpath)?;
        items.extend(inner);
        items.extend(pad);
        self.wrap_transform(be, items, m, where_, &cpath, &qname(ckey), false)
    }

    /// Finds the meta-command whose CommandContainer a reference names.
    ///
    /// # Arguments
    ///
    /// * `ref_` - The reference.
    /// * `path` - The key of the SpaceSystem it is written in.
    ///
    /// # Returns
    ///
    /// The meta-command's key, or `None` if `ref_` names a container of a
    /// CommandContainerSet or of telemetry, or nothing.
    fn mc_owner(&self, ref_: &str, path: &Key) -> Option<Key> {
        if self.tc_key(ref_, path).is_ok() {
            return None;
        }
        self.mcc_key(ref_, path)
            .ok()
            .map(|k| self.mc_containers.get(&k).unwrap().clone())
    }

    /// Gives the items for the CommandContainer of a meta-command, which a
    /// command includes: those of the container it extends (its
    /// BaseMetaCommand's, included the same way, or another), then its
    /// own, with its own arguments; its argument assignments, which fix
    /// what they assign; and padding up to its size.
    ///
    /// # Arguments
    ///
    /// * `key`    - The meta-command's key.
    /// * `where_` - What includes it, for error messages.
    /// * `values` - The values encoded so far; grows as entries are added.
    /// * `stack`  - The keys of the containers being included (a
    ///              meta-command's after `meta-command`), to find ones that
    ///              include themselves.
    ///
    /// # Returns
    ///
    /// The items, starting with a Mark and in a Transform if the container
    /// has transform algorithms.
    ///
    /// # Errors
    ///
    /// Unsupported, if it has no CommandContainer or has
    /// ErrorDetectCorrect, its container extends another than its
    /// BaseMetaCommand's, or an assignment or criterion is invalid; Cycle,
    /// if it includes itself.
    fn mc_include(
        &mut self,
        key: &Key,
        where_: &str,
        values: &mut Vec<Shared<Local>>,
        stack: &[Key],
    ) -> Result<Vec<Item>> {
        let mut mark = vec!["meta-command".to_string()];
        mark.extend(key.iter().cloned());
        if stack.contains(&mark) {
            return Err(Error::Cycle(format!(
                "{where_}: the CommandContainer of {} includes itself, a loop",
                qname(key)
            )));
        }
        let mut stack = stack.to_vec();
        stack.push(mark);
        let (path, mc) = self.mcs.get(key).unwrap().clone();
        let Some(cc) = &mc.command_container else {
            return unsupported(format!(
                "{where_}: meta-command {} has no CommandContainer",
                qname(key)
            ));
        };
        let be = cc.binary_encoding.as_ref();
        if !edc_items(be).is_empty() {
            return unsupported(format!(
                "{where_}: ErrorDetectCorrect in the container of meta-command \
                 {}, which a command includes",
                qname(key)
            ));
        }
        let scope = self.arg_scope(key, &[])?;
        let ak = |n: &str| arg_key_of(&scope, n, where_);
        let m = self.next_id();
        let start = values.len();
        let mut inner: Vec<Item> = Vec::new();
        let bkey = match &mc.base_meta_command {
            Some(b) => Some(self.mc_key(&b.meta_command_ref, &path)?),
            None => None,
        };
        let mut bc = None;
        let mut shared = None;
        if let Some(b) = &cc.base_container {
            match self.mcc_key(&b.container_ref, &path) {
                Ok(k) => bc = Some(k),
                Err(Error::Unsupported(_)) => {
                    shared = Some(self.tc_key(&b.container_ref, &path)?)
                }
                Err(e) => return Err(e),
            }
        }
        if let Some(sh) = &shared {
            if bkey.is_some() {
                return unsupported(format!(
                    "{where_}: the CommandContainer of {} extends a container \
                     other than its BaseMetaCommand's",
                    qname(key)
                ));
            }
            inner.extend(self.tc_include(sh, &scope, where_, values, &stack)?);
        } else if let Some(bk) = &bkey {
            if let Some(bc) = &bc {
                if self.mc_containers.get(bc) != Some(bk) {
                    return unsupported(format!(
                        "{where_}: the CommandContainer of {} extends {}, \
                         which is not the container of its BaseMetaCommand",
                        qname(key),
                        qname(bc)
                    ));
                }
            }
            inner.extend(self.mc_include(bk, where_, values, &stack)?);
            let mut crit = Vec::new();
            let bmc = mc.base_meta_command.as_ref().unwrap();
            for asg in bmc
                .argument_assignment_list
                .iter()
                .flat_map(|l| l.argument_assignment.iter())
            {
                let name = &asg.argument_name;
                let Some((owner, _)) = scope.get(name) else {
                    return unsupported(format!(
                        "{where_}: assignment to unknown argument {}",
                        crate::pyfmt::repr(name)
                    ));
                };
                let akey = format!("{}#{name}", qname(owner));
                let found = values[start..]
                    .iter()
                    .rev()
                    .find(|l| l.get().key == akey)
                    .cloned();
                if let Some(local) = found {
                    crit.push(assignment(
                        local,
                        name,
                        &asg.argument_value,
                        where_,
                    )?);
                }
            }
            if !crit.is_empty() {
                let text = and_text(&crit);
                inner.push(Item::Require(Require {
                    criteria: crit,
                    container: qname(key),
                    text,
                    fixes: true,
                }));
            }
        }
        let rc = cc
            .base_container
            .as_ref()
            .and_then(|b| b.restriction_criteria.as_ref());
        if let Some(rc) = rc {
            if rc.next_container.is_some() {
                return unsupported(format!(
                    "{where_}: NextContainer on a CommandContainer"
                ));
            }
            let terms = self.match_(
                CritRef::restriction(rc),
                &path,
                values,
                where_,
                Some(&ak),
            )?;
            let text = and_text(&terms);
            inner.push(Item::Require(Require {
                criteria: terms,
                container: qname(key),
                text,
                fixes: false,
            }));
        }
        let entries = tc_entries(cc);
        inner.extend(self.tc_items(
            &entries, be, &scope, &path, where_, values, m, &stack,
        )?);
        let pad = self.size_pad(be, &inner, m, where_, Some(values), &path)?;
        let mut items = vec![Item::Mark(Mark { id: m })];
        items.extend(inner);
        items.extend(pad);
        self.wrap_transform(be, items, m, where_, &path, &qname(key), false)
    }

    /// Compiles the aggregate for a repeated CommandContainer of a
    /// meta-command, as `container_type` does, with its arguments as
    /// members, once.
    ///
    /// # Arguments
    ///
    /// * `key`    - The meta-command's key.
    /// * `where_` - What repeats it, for error messages.
    /// * `stack`  - The keys of the containers being included (a
    ///              meta-command's after `meta-command`), to find ones that
    ///              include themselves.
    ///
    /// # Returns
    ///
    /// The aggregate type.
    ///
    /// # Errors
    ///
    /// Unsupported, if its entries can't be the members of an aggregate.
    fn mc_container_type(
        &mut self,
        key: &Key,
        where_: &str,
        stack: &[Key],
    ) -> Result<Rc<VType>> {
        let mut ckey = vec!["meta-command".to_string()];
        ckey.extend(key.iter().cloned());
        if let Some(t) = self.ctypes.get(&ckey) {
            return Ok(t.clone());
        }
        let name = self
            .mcs
            .get(key)
            .unwrap()
            .1
            .command_container
            .as_ref()
            .map(|c| c.name.clone())
            .unwrap_or_else(|| key.last().unwrap().clone());
        let what =
            format!("{where_}: repeated CommandContainer of {}", qname(key));
        let items = self.mc_include(key, where_, &mut Vec::new(), stack)?;
        // mc_include starts with its Mark.
        let m = match items.first() {
            Some(Item::Mark(mk)) => mk.id,
            Some(Item::Transform(t)) => t.get().start,
            _ => unreachable!("a Mark or Transform first"),
        };
        let vt = self.aggregate_of(
            &items,
            m,
            &what,
            &qname(key),
            &name,
            None,
            "one copy of a repeated container",
        )?;
        self.ctypes.insert(ckey, vt.clone());
        Ok(vt)
    }

    /// Compiles a FixedValueEntry of any size, repeated as its RepeatEntry
    /// says.
    ///
    /// # Arguments
    ///
    /// * `e`      - The XTCE entry.
    /// * `path`   - The key of the SpaceSystem it is in.
    /// * `where_` - What it is in, for error messages.
    ///
    /// # Returns
    ///
    /// Fixed items of at most 64 bits each, most significant first, with a
    /// Seek between repeats that have an Offset.
    ///
    /// # Errors
    ///
    /// Unsupported, if the value doesn't fit its size, or it is repeated
    /// as other values say, or its Count or Offset is out of range.
    fn fixed_entry(
        &mut self,
        e: &x::ArgumentFixedValueEntryType,
        path: &Key,
        where_: &str,
    ) -> Result<Vec<Item>> {
        let bits = e.size_in_bits;
        let text = e.binary_value.trim();
        let bytes =
            hex_bytes(if text.is_empty() { "0" } else { text }, where_)?;
        let value = BigInt::from_bytes_be(num_bigint::Sign::Plus, &bytes);
        if bits < 1 || !(value.clone() >> (bits as usize)).is_zero() {
            return unsupported(format!(
                "{where_}: FixedValueEntry {} in {bits} bits",
                e.binary_value
            ));
        }
        // Most significant first: a short first part, then 64 bits each.
        let mut one = Vec::new();
        let mut left = bits;
        while left > 0 {
            let n = if left % 64 == 0 { 64 } else { left % 64 };
            left -= n;
            let mask = (BigInt::from(1) << (n as usize)) - 1;
            let part = (value.clone() >> (left as usize)) & mask;
            one.push(Item::Fixed(Fixed {
                bits: n,
                value: part,
            }));
        }
        let Some(rp) = &e.repeat_entry else {
            return Ok(one);
        };
        let count = self.int_value(
            IntValueRef::A(&rp.count),
            path,
            "RepeatEntry Count",
            where_,
        )?;
        let gap = match &rp.offset {
            Some(o) => self.int_value(
                IntValueRef::A(o),
                path,
                "RepeatEntry Offset",
                where_,
            )?,
            None => IntValue::Int(0),
        };
        let (IntValue::Int(count), IntValue::Int(gap)) = (count, gap) else {
            return unsupported(format!(
                "{where_}: a FixedValueEntry repeated as other values say"
            ));
        };
        if count < 1 || gap < 0 {
            return unsupported(format!(
                "{where_}: RepeatEntry Count {count} or Offset {gap}"
            ));
        }
        let mut out = Vec::new();
        for i in 0..count {
            if i != 0 && gap != 0 {
                out.push(Item::Seek(Seek::new(None, Count::Int(gap))));
            }
            out.extend(one.iter().cloned());
        }
        Ok(out)
    }
}
