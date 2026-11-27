//! The compiler's analysis: from the XTCE documents of a mission, as the
//! `tcs-xtce` binding reads them, to the resolved model, as the Python
//! compiler's `compile.py` does.
//!
//! A mission may be split across several documents, as Yamcs allows: the
//! root SpaceSystem of each document sits at the top of one tree, so a
//! reference such as `/PUS/pus-tm` in one document can name a container
//! defined in another whose root SpaceSystem is `PUS`.

mod alarms;
pub mod bind;
pub mod checks;
pub mod containers;
pub mod criteria;
pub mod layout;
pub mod types;
pub mod xtype;

use std::collections::{HashMap, HashSet};
use std::rc::Rc;

use tcs_xtce::generated as x;

use crate::dump::Shared;
use crate::model::{
    unsupported, Error, Hook, Key, Node, Result, VType, MAX_FIELD_BITS,
    MAX_FIELD_BYTES_LIMIT,
};
use crate::pyfmt::repr;
use xtype::XType;

/// The AncillaryData that marks a parameter or argument as a packet length
/// field.
pub const LENGTH_TAG: &str = "tcs-xtce.length";
/// The AncillaryData of a root SpaceSystem that gives the most bytes a
/// field whose size another value gives may take.
pub const MAX_FIELD_TAG: &str = "tcs-xtce.max-field-bytes";
/// The AncillaryData of a root SpaceSystem that names the dialect of XTCE
/// the documents follow.
pub const DIALECT_TAG: &str = "tcs-xtce.dialect";
/// The dialects the compiler knows.
pub const DIALECTS: &[&str] = &["yamcs"];
/// The AncillaryData that, in the Yamcs dialect, makes binary data of
/// size -1 a complete CCSDS packet.
pub const EMBEDDED_TC_TAG: &str = "Yamcs:EmbeddedTc";
/// The most bytes a CCSDS space packet takes.
pub const CCSDS_MAX_BYTES: i64 = 65542;
/// The parameter that, in the Yamcs dialect, a string's Variable size
/// names when its LeadingSize or TerminationChar alone gives the size.
pub const YAMCS_IGNORE: &str = "_yamcs_ignore";

/// Writes a key as a qualified name.
///
/// # Arguments
///
/// * `key` - The key.
///
/// # Returns
///
/// `/` and its parts joined by `/`.
pub fn qname(key: &[String]) -> String {
    format!("/{}", key.join("/"))
}

/// Appends a name to a key.
///
/// # Arguments
///
/// * `path` - The key of a SpaceSystem.
/// * `name` - The name.
///
/// # Returns
///
/// The key of the name in that SpaceSystem.
pub fn child(path: &[String], name: &str) -> Key {
    let mut k = path.to_vec();
    k.push(name.to_string());
    k
}

/// A table of definitions by key that keeps the order they were first
/// added in, as a Python dictionary does: adding a key again replaces its
/// value where it stands.
#[derive(Debug)]
pub struct Table<V> {
    /// The keys, in order.
    keys: Vec<Key>,
    /// Each key's value.
    map: HashMap<Key, V>,
}

impl<V> Default for Table<V> {
    fn default() -> Self {
        Table {
            keys: Vec::new(),
            map: HashMap::new(),
        }
    }
}

impl<V> Table<V> {
    /// Adds a definition, or replaces one.
    ///
    /// # Arguments
    ///
    /// * `key`   - Its key.
    /// * `value` - The definition.
    pub fn insert(&mut self, key: Key, value: V) {
        if self.map.insert(key.clone(), value).is_none() {
            self.keys.push(key);
        }
    }

    /// Adds a definition unless the key has one already.
    ///
    /// # Arguments
    ///
    /// * `key`   - Its key.
    /// * `value` - The definition.
    pub fn set_default(&mut self, key: Key, value: V) {
        if !self.map.contains_key(&key) {
            self.insert(key, value);
        }
    }

    /// Says whether a key has a definition.
    ///
    /// # Arguments
    ///
    /// * `key` - The key.
    ///
    /// # Returns
    ///
    /// Whether it has.
    pub fn contains(&self, key: &[String]) -> bool {
        self.map.contains_key(key)
    }

    /// Gives a key's definition.
    ///
    /// # Arguments
    ///
    /// * `key` - The key.
    ///
    /// # Returns
    ///
    /// The definition, if it has one.
    pub fn get(&self, key: &[String]) -> Option<&V> {
        self.map.get(key)
    }

    /// Lists the keys in order.
    ///
    /// # Returns
    ///
    /// The keys.
    pub fn keys(&self) -> &[Key] {
        &self.keys
    }

    /// Lists the definitions in order.
    ///
    /// # Returns
    ///
    /// Each key and its definition.
    pub fn iter(&self) -> impl Iterator<Item = (&Key, &V)> {
        self.keys.iter().map(|k| (k, &self.map[k]))
    }
}

/// Which table of types a type is in: parameter and argument types are
/// separate namespaces.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum TypeTable {
    /// The parameter types, `ptypes`.
    Param,
    /// The argument types, `atypes`.
    Arg,
}

/// Reads one of an item's AncillaryData values.
///
/// # Arguments
///
/// * `ads`    - The item's AncillaryDataSet, if it has one.
/// * `tag`    - The AncillaryData name.
/// * `where_` - What the item is, for error messages.
///
/// # Returns
///
/// The value, without surrounding space, or `None` if it has none.
///
/// # Errors
///
/// Unsupported, if the item has more than one `tag`.
pub fn ancillary_str(
    ads: Option<&x::AncillaryDataSetType>,
    tag: &str,
    where_: &str,
) -> Result<Option<String>> {
    let tags: Vec<&x::AncillaryDataType> = ads
        .map(|a| a.ancillary_data.iter().filter(|d| d.name == tag).collect())
        .unwrap_or_default();
    match tags.len() {
        0 => Ok(None),
        1 => Ok(Some(tags[0].value.trim().to_string())),
        _ => unsupported(format!("{where_}: more than one {tag}")),
    }
}

/// Reads one of an item's AncillaryData values as an integer.
///
/// # Arguments
///
/// * `ads`    - The item's AncillaryDataSet, if it has one.
/// * `tag`    - The AncillaryData name.
/// * `where_` - What the item is, for error messages.
///
/// # Returns
///
/// The value, or `None` if it has none.
///
/// # Errors
///
/// Unsupported, if there is more than one `tag` or it isn't an integer.
pub fn ancillary_int(
    ads: Option<&x::AncillaryDataSetType>,
    tag: &str,
    where_: &str,
) -> Result<Option<i64>> {
    match ancillary_str(ads, tag, where_)? {
        None => Ok(None),
        Some(v) => match crate::pyparse::int(&v) {
            Some(n) => Ok(Some(n)),
            None => unsupported(format!(
                "{where_}: {tag} {} is not an integer",
                repr(&v)
            )),
        },
    }
}

/// The compiler: the definitions of a mission's documents, indexed by
/// key, and what compiling them has made so far.
pub struct Compiler<'a> {
    /// The root SpaceSystem of each document.
    pub roots: Vec<&'a x::SpaceSystemType>,
    /// Whether `compile` leaves out what it can't compile.
    pub skip_unsupported: bool,
    /// The dialect of XTCE the documents follow, or `None`.
    pub dialect: Option<String>,
    /// The parameter types: each one's SpaceSystem and definition.
    pub ptypes: Table<(Key, XType)>,
    /// The parameters.
    pub params: Table<(Key, &'a x::ParameterType)>,
    /// The argument types.
    pub atypes: Table<(Key, XType)>,
    /// The sequence containers.
    pub containers: Table<(Key, &'a x::SequenceContainerType)>,
    /// The messages.
    pub messages: Table<(Key, &'a x::MessageType)>,
    /// The algorithms.
    pub algorithms: Table<(Key, &'a x::AlgorithmSetChoice)>,
    /// The streams.
    pub streams: Table<(Key, &'a x::StreamSetChoice)>,
    /// The services.
    pub services: Table<(Key, &'a x::ServiceType)>,
    /// The hooks for the custom algorithms used, by (kind, name).
    pub hooks: HashMap<(String, String), Shared<Hook>>,
    /// The order the hooks were made in.
    pub hook_order: Vec<Shared<Hook>>,
    /// The meta-commands.
    pub mcs: Table<(Key, &'a x::MetaCommandType)>,
    /// What the documents allow that generated code can only guess at, a
    /// warning each, once, in the order found.
    pub warnings: Vec<String>,
    /// Each CommandContainer's meta-command.
    pub mc_containers: HashMap<Key, Key>,
    /// The containers a CommandContainer may include or extend: those of
    /// CommandContainerSets, then telemetry's.
    pub tc_containers: Table<(Key, &'a x::SequenceContainerType)>,
    /// The types compiled so far.
    pub vtypes: HashMap<(TypeTable, Key), Rc<VType>>,
    /// The last number given to a Local.
    pub ids: i64,
    /// The aggregates being compiled, to find cycles.
    pub aggregating: HashSet<(TypeTable, Key)>,
    /// The most bits a field whose size another value gives may take.
    pub max_field_bits: i64,
    /// The nodes compiled so far, by key (a message's after `message`).
    pub nodes: HashMap<Key, Shared<Node>>,
    /// The nodes in the order they were finished.
    pub order: Vec<Shared<Node>>,
    /// The ids of the Locals `check_rest` has seen.
    pub tailed: HashSet<i64>,
    /// The aggregate of each repeated container compiled so far.
    pub ctypes: HashMap<Key, Rc<VType>>,
    /// The ErrorDetectCorrect of the containers included in the node being
    /// compiled.
    pub included_checks: Vec<Included<'a>>,
    /// Each node's own container and its SpaceSystem, whose checks cover
    /// the packet, by the node's address; `None` for one whose checks are
    /// in its Transform.
    pub node_edc: HashMap<usize, Option<(&'a x::SequenceContainerType, Key)>>,
    /// The ErrorDetectCorrect of the containers each node includes, by
    /// the node's address.
    pub node_included: HashMap<usize, Vec<Included<'a>>>,
}

/// The ErrorDetectCorrect of an included container, to compile once the
/// packet is known.
#[derive(Clone)]
pub struct Included<'a> {
    /// The container.
    pub container: &'a x::SequenceContainerType,
    /// The SpaceSystem it is defined in.
    pub path: Key,
    /// The Mark at its start.
    pub start: i64,
    /// The Mark at its end.
    pub end: i64,
    /// The values decoded by its end.
    pub scope: Vec<Shared<crate::model::Local>>,
    /// Its qualified name.
    pub key: String,
}

impl<'a> Compiler<'a> {
    /// Indexes the definitions of a mission's documents.
    ///
    /// # Arguments
    ///
    /// * `roots`            - The root SpaceSystem of each document.
    /// * `skip_unsupported` - Whether `compile` leaves out what it can't
    ///                        compile instead of failing.
    /// * `dialect`          - The dialect the documents follow when they don't
    ///                        say, or `None`.
    ///
    /// # Returns
    ///
    /// The compiler, with nothing compiled yet.
    ///
    /// # Errors
    ///
    /// Unsupported, if the dialect is unknown, two documents have the same
    /// root SpaceSystem, or the roots' AncillaryData is invalid or
    /// disagrees.
    pub fn new(
        roots: Vec<&'a x::SpaceSystemType>,
        skip_unsupported: bool,
        dialect: Option<&str>,
    ) -> Result<Self> {
        if let Some(d) = dialect {
            if !DIALECTS.contains(&d) {
                return unsupported(format!(
                    "dialect {} is not one of {}",
                    repr(d),
                    DIALECTS.join(", ")
                ));
            }
        }
        let mut c = Compiler {
            roots,
            skip_unsupported,
            dialect: dialect.map(String::from),
            ptypes: Table::default(),
            params: Table::default(),
            atypes: Table::default(),
            containers: Table::default(),
            messages: Table::default(),
            algorithms: Table::default(),
            streams: Table::default(),
            services: Table::default(),
            hooks: HashMap::new(),
            hook_order: Vec::new(),
            mcs: Table::default(),
            warnings: Vec::new(),
            mc_containers: HashMap::new(),
            tc_containers: Table::default(),
            vtypes: HashMap::new(),
            ids: 0,
            aggregating: HashSet::new(),
            max_field_bits: 0,
            nodes: HashMap::new(),
            order: Vec::new(),
            tailed: HashSet::new(),
            ctypes: HashMap::new(),
            included_checks: Vec::new(),
            node_edc: HashMap::new(),
            node_included: HashMap::new(),
        };
        let mut seen = HashSet::new();
        for root in c.roots.clone() {
            if !seen.insert(root.name.clone()) {
                return unsupported(format!(
                    "two documents have the root SpaceSystem {}",
                    repr(&root.name)
                ));
            }
            c.index(root, &[])?;
        }
        c.max_field_bits = 8 * c.max_field_bytes()?;
        c.dialect = c.root_dialect(c.dialect.clone())?;
        let tm: Vec<(Key, (Key, &'a x::SequenceContainerType))> = c
            .containers
            .iter()
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        for (k, (p, sc)) in tm {
            c.tc_containers.set_default(k, (p, sc));
        }
        Ok(c)
    }

    /// Finds the most bytes a field whose size another value gives may
    /// take on the wire.
    ///
    /// # Returns
    ///
    /// The roots' tcs-xtce.max-field-bytes, which all that give one must
    /// agree on, or the default.
    ///
    /// # Errors
    ///
    /// Unsupported, if a value is out of range or the roots disagree.
    fn max_field_bytes(&self) -> Result<i64> {
        let mut given: Vec<(String, i64)> = Vec::new();
        for root in &self.roots {
            let where_ = format!("SpaceSystem {}", root.name);
            let Some(n) = ancillary_int(
                root.ancillary_data_set.as_ref(),
                MAX_FIELD_TAG,
                &where_,
            )?
            else {
                continue;
            };
            if !(1..=MAX_FIELD_BYTES_LIMIT).contains(&n) {
                return unsupported(format!(
                    "SpaceSystem {}: {MAX_FIELD_TAG} {n} is not from 1 to \
                     {MAX_FIELD_BYTES_LIMIT}",
                    root.name
                ));
            }
            given.push((root.name.clone(), n));
        }
        let distinct: HashSet<i64> = given.iter().map(|g| g.1).collect();
        if distinct.len() > 1 {
            let names: Vec<String> =
                given.iter().map(|(k, v)| format!("{k} ({v})")).collect();
            return unsupported(format!(
                "the documents' {MAX_FIELD_TAG} differ: {}",
                names.join(", ")
            ));
        }
        Ok(given.first().map(|g| g.1).unwrap_or(MAX_FIELD_BITS / 8))
    }

    /// Finds the dialect of XTCE the documents follow.
    ///
    /// # Arguments
    ///
    /// * `default` - The dialect given to the compiler, or `None`.
    ///
    /// # Returns
    ///
    /// The roots' tcs-xtce.dialect, which all that give one must agree on,
    /// and `default` too if given; or `default`.
    ///
    /// # Errors
    ///
    /// Unsupported, if a dialect is unknown or they disagree.
    fn root_dialect(&self, default: Option<String>) -> Result<Option<String>> {
        let mut given: Vec<(String, String)> = Vec::new();
        if let Some(d) = default {
            given.push(("(the dialect given)".into(), d));
        }
        for root in &self.roots {
            let where_ = format!("SpaceSystem {}", root.name);
            let Some(d) = ancillary_str(
                root.ancillary_data_set.as_ref(),
                DIALECT_TAG,
                &where_,
            )?
            else {
                continue;
            };
            if !DIALECTS.contains(&d.as_str()) {
                return unsupported(format!(
                    "SpaceSystem {}: {DIALECT_TAG} {} is not one of {}",
                    root.name,
                    repr(&d),
                    DIALECTS.join(", ")
                ));
            }
            // A Python dictionary keeps one entry for each root name.
            match given.iter_mut().find(|g| g.0 == root.name) {
                Some(g) => g.1 = d,
                None => given.push((root.name.clone(), d)),
            }
        }
        let distinct: HashSet<&String> = given.iter().map(|g| &g.1).collect();
        if distinct.len() > 1 {
            let names: Vec<String> =
                given.iter().map(|(k, v)| format!("{k} ({v})")).collect();
            return unsupported(format!(
                "the documents' {DIALECT_TAG} differ: {}",
                names.join(", ")
            ));
        }
        Ok(given.into_iter().next().map(|g| g.1))
    }

    /// Records the definitions of a SpaceSystem and those within it in the
    /// compiler's tables, by key.
    ///
    /// # Arguments
    ///
    /// * `ss`     - The SpaceSystem.
    /// * `parent` - The key of the SpaceSystem that holds it; empty for a
    ///              document's root.
    ///
    /// # Errors
    ///
    /// Unsupported, if a SpaceSystem other than a root has tcs-xtce
    /// AncillaryData that only roots may have.
    fn index(
        &mut self,
        ss: &'a x::SpaceSystemType,
        parent: &[String],
    ) -> Result<()> {
        let path = child(parent, &ss.name);
        let ads = ss.ancillary_data_set.as_ref();
        if !parent.is_empty()
            && ancillary_int(ads, MAX_FIELD_TAG, &qname(&path))?.is_some()
        {
            return unsupported(format!(
                "SpaceSystem {}: {MAX_FIELD_TAG} is only read on a \
                 document's root SpaceSystem",
                qname(&path)
            ));
        }
        if !parent.is_empty()
            && ancillary_str(ads, DIALECT_TAG, &qname(&path))?.is_some()
        {
            return unsupported(format!(
                "SpaceSystem {}: {DIALECT_TAG} is only read on a \
                 document's root SpaceSystem",
                qname(&path)
            ));
        }
        if let Some(sset) = &ss.service_set {
            for sv in &sset.service {
                self.services
                    .insert(child(&path, &sv.name), (path.clone(), sv));
            }
        }
        let tm = ss.telemetry_meta_data.as_ref();
        let tc = ss.command_meta_data.as_ref();
        // Each holds parameter types, parameters and algorithms.
        let mds: Vec<(
            Option<&'a x::ParameterTypeSetType>,
            Option<&'a x::ParameterSetType>,
            Option<&'a x::AlgorithmSetType>,
        )> = [
            tm.map(|m| {
                (
                    m.parameter_type_set.as_ref(),
                    m.parameter_set.as_ref(),
                    m.algorithm_set.as_ref(),
                )
            }),
            tc.map(|m| {
                (
                    m.parameter_type_set.as_ref(),
                    m.parameter_set.as_ref(),
                    m.algorithm_set.as_ref(),
                )
            }),
        ]
        .into_iter()
        .flatten()
        .collect();
        for (pts, ps, als) in mds {
            if let Some(als) = als {
                for ch in &als.items {
                    let name = match ch {
                        x::AlgorithmSetChoice::CustomAlgorithm(a) => &a.name,
                        x::AlgorithmSetChoice::MathAlgorithm(a) => &a.name,
                    };
                    self.algorithms
                        .insert(child(&path, name), (path.clone(), ch));
                }
            }
            if let Some(pts) = pts {
                for ch in &pts.items {
                    let t = XType::P(ch.clone());
                    self.ptypes
                        .insert(child(&path, t.name()), (path.clone(), t));
                }
            }
            if let Some(ps) = ps {
                for ch in &ps.items {
                    if let x::ParameterSetChoice::Parameter(p) = ch {
                        self.params
                            .insert(child(&path, &p.name), (path.clone(), p));
                    }
                }
            }
        }
        if let Some(tm) = tm {
            if let Some(ss_) = &tm.stream_set {
                for ch in &ss_.items {
                    let name = match ch {
                        x::StreamSetChoice::FixedFrameStream(s) => &s.name,
                        x::StreamSetChoice::VariableFrameStream(s) => &s.name,
                        x::StreamSetChoice::CustomStream(s) => &s.name,
                    };
                    self.streams.insert(child(&path, name), (path.clone(), ch));
                }
            }
            if let Some(ms) = &tm.message_set {
                for m in &ms.message {
                    self.messages
                        .insert(child(&path, &m.name), (path.clone(), m));
                }
            }
            if let Some(cs) = &tm.container_set {
                for sc in &cs.sequence_container {
                    self.containers
                        .insert(child(&path, &sc.name), (path.clone(), sc));
                }
            }
        }
        if let Some(tc) = tc {
            if let Some(ats) = &tc.argument_type_set {
                for ch in &ats.items {
                    let t = XType::A(ch.clone());
                    self.atypes
                        .insert(child(&path, t.name()), (path.clone(), t));
                }
            }
            if let Some(ccs) = &tc.command_container_set {
                for c in &ccs.command_container {
                    self.tc_containers
                        .insert(child(&path, &c.name), (path.clone(), c));
                }
            }
            if let Some(mcs) = &tc.meta_command_set {
                for ch in &mcs.items {
                    if let x::MetaCommandSetChoice::MetaCommand(mc) = ch {
                        let key = child(&path, &mc.name);
                        self.mcs.insert(key.clone(), (path.clone(), mc));
                        if let Some(cc) = &mc.command_container {
                            self.mc_containers
                                .insert(child(&path, &cc.name), key);
                        }
                    }
                }
            }
        }
        for sub in &ss.space_system {
            self.index(sub, &path)?;
        }
        Ok(())
    }

    /// Resolves an XTCE name reference relative to a SpaceSystem.
    ///
    /// Absolute paths start with `/` and the name of a document's root.
    /// Relative paths may use `.` and `..`. An unqualified name, or a
    /// relative path that doesn't start with `.` or `..`, is looked up from
    /// `cur` and then from each enclosing SpaceSystem, as Yamcs does.
    ///
    /// # Arguments
    ///
    /// * `ref_` - The reference.
    /// * `cur`  - The key of the SpaceSystem it is written in.
    /// * `has`  - Says whether a key names one of what it names.
    /// * `what` - What it names, for error messages.
    ///
    /// # Returns
    ///
    /// The key of the definition it names.
    ///
    /// # Errors
    ///
    /// Unsupported, if it names nothing.
    pub fn resolve(
        ref_: &str,
        cur: &[String],
        has: &dyn Fn(&[String]) -> bool,
        what: &str,
    ) -> Result<Key> {
        let r = ref_.trim();
        let parts: Vec<&str> = r.split('/').collect();
        if r.starts_with('/') {
            let key: Key = parts[1..]
                .iter()
                .filter(|p| !p.is_empty())
                .map(|p| p.to_string())
                .collect();
            if has(&key) {
                return Ok(key);
            }
        } else if parts.len() == 1 {
            for i in (1..=cur.len()).rev() {
                let key = child(&cur[..i], r);
                if has(&key) {
                    return Ok(key);
                }
            }
        } else {
            let starts: Vec<&[String]> = if matches!(parts[0], "." | "..") {
                vec![cur]
            } else {
                (0..=cur.len()).rev().map(|i| &cur[..i]).collect()
            };
            for start in starts {
                let mut base: Vec<String> = start.to_vec();
                for p in &parts[..parts.len() - 1] {
                    if *p == ".." {
                        base.pop();
                    } else if !p.is_empty() && *p != "." {
                        base.push(p.to_string());
                    }
                }
                base.push(parts[parts.len() - 1].to_string());
                if has(&base) {
                    return Ok(base);
                }
            }
        }
        unsupported(format!(
            "unresolved {what} reference {} in {}",
            repr(r),
            qname(cur)
        ))
    }

    /// Resolves a reference to a type.
    ///
    /// # Arguments
    ///
    /// * `ref_`  - The reference.
    /// * `cur`   - The key of the SpaceSystem it is written in.
    /// * `table` - The table of types it names one of.
    /// * `what`  - What it names, for error messages.
    ///
    /// # Returns
    ///
    /// The type's key.
    ///
    /// # Errors
    ///
    /// Unsupported, if it names no type.
    pub fn resolve_type(
        &self,
        ref_: &str,
        cur: &[String],
        table: TypeTable,
        what: &str,
    ) -> Result<Key> {
        let t = self.types(table);
        Self::resolve(ref_, cur, &|k| t.contains(k), what)
    }

    /// Gives a table of types.
    ///
    /// # Arguments
    ///
    /// * `table` - Which.
    ///
    /// # Returns
    ///
    /// The table.
    pub fn types(&self, table: TypeTable) -> &Table<(Key, XType)> {
        match table {
            TypeTable::Param => &self.ptypes,
            TypeTable::Arg => &self.atypes,
        }
    }

    /// Resolves a reference to a parameter, or to a member or element of
    /// one. In the Yamcs dialect a path that names no parameter may end in
    /// member names after `/`, as Yamcs allows.
    ///
    /// # Arguments
    ///
    /// * `ref_` - The reference.
    /// * `cur`  - The key of the SpaceSystem it is written in.
    ///
    /// # Returns
    ///
    /// The parameter's key and the member steps.
    ///
    /// # Errors
    ///
    /// Unsupported, if it names no parameter.
    pub fn param_ref(
        &self,
        ref_: &str,
        cur: &[String],
    ) -> Result<(Key, Vec<crate::model::Step>)> {
        let (base, members) = types::member_path(ref_)?;
        let params = |k: &[String]| self.params.contains(k);
        match Self::resolve(&base, cur, &params, "parameter") {
            Ok(k) => Ok((k, members)),
            Err(e) => {
                if self.dialect.as_deref() != Some("yamcs")
                    || !members.is_empty()
                {
                    return Err(e);
                }
                let parts: Vec<&str> = base.split('/').collect();
                for i in (1..parts.len()).rev() {
                    let head = parts[..i].join("/");
                    let steps = &parts[i..];
                    if head.trim_matches('/').is_empty()
                        || !steps.iter().all(|p| types::is_member_name(p))
                    {
                        continue;
                    }
                    if let Ok(key) =
                        Self::resolve(&head, cur, &params, "parameter")
                    {
                        return Ok((
                            key,
                            steps
                                .iter()
                                .map(|s| {
                                    crate::model::Step::Name(s.to_string())
                                })
                                .collect(),
                        ));
                    }
                }
                Err(e)
            }
        }
    }

    /// Gives the next number for a Local.
    ///
    /// # Returns
    ///
    /// The number.
    pub fn next_id(&mut self) -> i64 {
        self.ids += 1;
        self.ids
    }

    /// Gets the hook for a custom algorithm, making it the first time.
    ///
    /// # Arguments
    ///
    /// * `kind`    - What it computes, such as `checksum`.
    /// * `name`    - The algorithm's qualified name.
    /// * `outputs` - How many values it gives, for an `algorithm`.
    ///
    /// # Returns
    ///
    /// The one hook for `kind` and `name`.
    pub fn hook(
        &mut self,
        kind: &str,
        name: &str,
        outputs: i64,
    ) -> Shared<Hook> {
        let k = (kind.to_string(), name.to_string());
        if let Some(h) = self.hooks.get(&k) {
            return h.clone();
        }
        let h = Shared::new(Hook {
            kind: kind.into(),
            name: name.into(),
            ident: String::new(),
            outputs,
        });
        self.hooks.insert(k, h.clone());
        self.hook_order.push(h.clone());
        h
    }

    /// Notes a warning, once however often it is found.
    ///
    /// # Arguments
    ///
    /// * `message` - What to say.
    pub fn warn(&mut self, message: String) {
        if !self.warnings.contains(&message) {
            self.warnings.push(message);
        }
    }
}

/// Keeps errors that are not cycles, so that the caller can tell them
/// apart.
///
/// # Arguments
///
/// * `e` - The error.
///
/// # Returns
///
/// Whether it is Unsupported.
pub fn is_unsupported(e: &Error) -> bool {
    matches!(e, Error::Unsupported(_))
}
