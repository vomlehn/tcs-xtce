//! The tcs-xtce run-time codec: reads a mission's XTCE documents while a
//! program runs, resolves them with the compiler's own analysis
//! (`tcs-xtce-model`), and packs and unpacks their packets with values
//! held dynamically, for tools that are given their XTCE when they run,
//! such as expectb. See `docs/runtime-codec-design.rst`.
//!
//! This first part reads and resolves a mission and looks names up in
//! it: SpaceSystems, containers, meta-commands, parameters and
//! arguments, and the members and elements of aggregates and arrays,
//! by the rules XTCE documents use for their own references.

pub mod api;
pub mod interp;
mod wide;

pub use api::{Datum, RunError, Scratch, SetError, Store};

use std::fmt;
use std::rc::Rc;
use std::sync::atomic::{AtomicU64, Ordering};

use tcs_xtce::{Document, SpaceSystemType};
use tcs_xtce_model::compile::types::member_path;
use tcs_xtce_model::compile::{candidates, qname, Compiler, TypeTable};
use tcs_xtce_model::dump::Shared;
use tcs_xtce_model::model::{
    Count, Encoding, Error as ModelError, Key, Node, Step, VType,
};

/// The number of the next mission made, so that a handle names the
/// mission that gave it out.
static NEXT_MISSION: AtomicU64 = AtomicU64::new(1);

/// Why a mission can't be read or resolved.
#[derive(Debug, Clone, PartialEq)]
pub enum Error {
    /// A document isn't valid XTCE, or can't be read.
    Read {
        /// Which document, from 0, in the order given.
        document: usize,
        /// What is wrong, and where.
        message: String,
    },
    /// The mission uses something that isn't supported.
    Unsupported(String),
    /// The documents define something in terms of itself.
    Cycle(String),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Read { document, message } => {
                write!(f, "document {}: {message}", document + 1)
            }
            Error::Unsupported(m) | Error::Cycle(m) => f.write_str(m),
        }
    }
}

impl std::error::Error for Error {}

impl From<ModelError> for Error {
    fn from(e: ModelError) -> Self {
        match e {
            ModelError::Unsupported(m) => Error::Unsupported(m),
            ModelError::Cycle(m) => Error::Cycle(m),
        }
    }
}

/// Why a lookup found nothing.
#[derive(Debug, Clone, PartialEq)]
pub struct LookupError {
    /// What was looked for: `SpaceSystem`, `container`, `meta-command`,
    /// `parameter`, `argument` or `entry`.
    pub what: &'static str,
    /// The path as given.
    pub path: String,
    /// The absolute paths tried, in the order they were tried.
    pub tried: Vec<String>,
    /// Why what was found can't be used, if something was found.
    pub reason: Option<String>,
}

impl fmt::Display for LookupError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match &self.reason {
            Some(r) => write!(f, "{} {}: {r}", self.what, self.path),
            None if self.tried.is_empty() => {
                write!(f, "no {} {}", self.what, self.path)
            }
            None => write!(
                f,
                "no {} {}: looked for {}",
                self.what,
                self.path,
                self.tried.join(", ")
            ),
        }
    }
}

impl std::error::Error for LookupError {}

/// A SpaceSystem of a mission, as the mission that gave it out names it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct SpaceSystemId {
    /// The mission's number.
    mission: u64,
    /// The SpaceSystem's place in the mission's list; 0 is the top.
    index: usize,
}

/// A container or meta-command of a mission.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct NodeId {
    /// The mission's number.
    mission: u64,
    /// The node's place in the mission's list.
    index: usize,
}

/// One scalar value of a mission: a parameter or argument, or a member
/// or element of one.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct SlotId {
    /// The mission's number.
    mission: u64,
    /// The slot's place in the mission's list.
    index: usize,
}

/// The type of a slot: what a program needs to check a value before it
/// gives one.
#[derive(Debug, Clone)]
pub struct Type {
    /// The model's type.
    vt: Rc<VType>,
}

impl Type {
    /// Gives the kind of value.
    ///
    /// # Returns
    ///
    /// `int`, `float`, `enum`, `bool`, `string` or `binary`.
    pub fn kind(&self) -> &str {
        &self.vt.kind
    }

    /// Gives an enumeration's labels.
    ///
    /// # Returns
    ///
    /// Each label and its value; none for other kinds.
    pub fn labels(&self) -> Vec<(&str, i64)> {
        self.vt
            .labels
            .iter()
            .map(|l| (l.label.as_str(), l.value))
            .collect()
    }

    /// Gives an integer's smallest and largest valid values.
    ///
    /// # Returns
    ///
    /// The range, if the type gives one.
    pub fn valid_range(&self) -> Option<(i64, i64)> {
        self.vt.valid_range
    }

    /// Says whether the value is calibrated: whether its engineering
    /// value differs from its raw one.
    ///
    /// # Returns
    ///
    /// Whether it has a calibrator.
    pub fn calibrated(&self) -> bool {
        self.vt.calibrator.is_some()
    }

    /// Gives the model's type, which says everything about the encoding.
    ///
    /// # Returns
    ///
    /// The type.
    pub fn model(&self) -> &VType {
        &self.vt
    }
}

/// A container or meta-command, as a mission holds it.
#[derive(Debug, Clone)]
struct NodeEntry {
    /// Its key.
    key: Key,
    /// Its absolute path.
    path: String,
    /// The node.
    node: Shared<Node>,
    /// Whether it is a meta-command.
    command: bool,
    /// Its arguments, a meta-command's: each name, with the key of the
    /// meta-command that has it.
    args: Vec<(String, String)>,
}

/// A scalar value, as a mission holds it.
#[derive(Debug, Clone)]
struct SlotEntry {
    /// The member and index steps into it.
    steps: Vec<Step>,
    /// Its absolute path.
    path: String,
    /// Its type.
    vt: Rc<VType>,
}

/// A parameter or argument, with its slots or why it has none.
#[derive(Debug, Clone)]
struct Owner {
    /// Its key.
    key: String,
    /// Its slots, or why it has none.
    slots: std::result::Result<Vec<usize>, String>,
}

/// A mission, read and resolved: its packets and the names they use.
#[derive(Debug)]
pub struct Mission {
    /// The mission's number, which its handles carry.
    id: u64,
    /// The resolved model.
    model: tcs_xtce_model::model::Mission,
    /// The key of each SpaceSystem, the top (empty) first.
    spaces: Vec<Key>,
    /// The containers and meta-commands.
    nodes: Vec<NodeEntry>,
    /// The parameters.
    params: Vec<Owner>,
    /// The arguments.
    args: Vec<Owner>,
    /// The slots.
    slots: Vec<SlotEntry>,
}

/// Adds a SpaceSystem and those inside it to a list, each once.
///
/// # Arguments
///
/// * `ss`   - The SpaceSystem.
/// * `path` - The key of the SpaceSystem it is in.
/// * `out`  - The list.
fn add_spaces(ss: &SpaceSystemType, path: &[String], out: &mut Vec<Key>) {
    let mut key = path.to_vec();
    key.push(ss.name.clone());
    if !out.contains(&key) {
        out.push(key.clone());
    }
    for c in &ss.space_system {
        add_spaces(c, &key, out);
    }
}

/// Writes a member or index step, as a path writes it.
///
/// # Arguments
///
/// * `s` - The step.
///
/// # Returns
///
/// `.name` or `[index]`.
fn step_text(s: &Step) -> String {
    match s {
        Step::Name(n) => format!(".{n}"),
        Step::Index(i) => format!("[{i}]"),
    }
}

/// Makes the slots of a value: one for a scalar, one for each member of
/// an aggregate, and one for each element of an array.
///
/// # Arguments
///
/// * `path`  - The path of the value.
/// * `steps` - The steps to it from the parameter or argument.
/// * `vt`    - Its type.
/// * `out`   - Where the slots go.
///
/// # Errors
///
/// Why it can't have slots: it is an array with no largest size.
fn expand(
    path: &str,
    steps: &[Step],
    vt: &Rc<VType>,
    out: &mut Vec<SlotEntry>,
) -> std::result::Result<(), String> {
    match &vt.enc {
        Encoding::Aggregate(a) => {
            for m in &a.members {
                let mut st = steps.to_vec();
                st.push(Step::Name(m.name.clone()));
                expand(&format!("{path}.{}", m.name), &st, &m.vtype, out)?;
            }
            Ok(())
        }
        Encoding::Array(a) => {
            let shape: Vec<i64> = if !a.shape.is_empty() {
                a.shape.clone()
            } else {
                match (&a.count, a.dims) {
                    (Count::Dyn(d), 1) if d.max > 0 => vec![d.max],
                    _ => {
                        return Err(format!(
                            "{path} is an array with no largest size"
                        ))
                    }
                }
            };
            let mut index = vec![0i64; shape.len()];
            loop {
                let mut st = steps.to_vec();
                let mut p = path.to_string();
                for (k, i) in index.iter().enumerate() {
                    let at = a.starts.get(k).copied().unwrap_or(0) + i;
                    st.push(Step::Index(at));
                    p.push_str(&format!("[{at}]"));
                }
                expand(&p, &st, &a.elem, out)?;
                // The last index fastest, as XTCE flattens arrays.
                let mut k = shape.len();
                loop {
                    if k == 0 {
                        return Ok(());
                    }
                    k -= 1;
                    index[k] += 1;
                    if index[k] < shape[k] {
                        break;
                    }
                    index[k] = 0;
                }
            }
        }
        _ => {
            out.push(SlotEntry {
                steps: steps.to_vec(),
                path: path.to_string(),
                vt: vt.clone(),
            });
            Ok(())
        }
    }
}

impl Mission {
    /// Reads and resolves a mission from its documents.
    ///
    /// # Arguments
    ///
    /// * `readers` - Each document, as anything that reads its text, in the
    ///               order the mission's documents build on one another.
    ///
    /// # Returns
    ///
    /// The mission.
    ///
    /// # Errors
    ///
    /// Read, if a document can't be read or isn't valid XTCE; Unsupported,
    /// if the mission uses something that isn't supported; Cycle, if it
    /// defines something in terms of itself.
    pub fn read_from<I, R>(readers: I) -> Result<Mission, Error>
    where
        I: IntoIterator<Item = R>,
        R: std::io::Read,
    {
        let mut docs = Vec::new();
        for (i, r) in readers.into_iter().enumerate() {
            let mut d = Document::new(SpaceSystemType::default());
            d.read_from(r).map_err(|e| Error::Read {
                document: i,
                message: e.to_string(),
            })?;
            docs.push(d);
        }
        Self::from_documents(&docs)
    }

    /// Resolves a mission from documents already read. The mission keeps
    /// nothing of them, so they can be dropped as soon as it returns.
    ///
    /// # Arguments
    ///
    /// * `docs` - The documents, in the order they build on one another.
    ///
    /// # Returns
    ///
    /// The mission.
    ///
    /// # Errors
    ///
    /// Unsupported, if the mission uses something that isn't supported;
    /// Cycle, if it defines something in terms of itself.
    pub fn from_documents(docs: &[Document]) -> Result<Mission, Error> {
        Self::from_documents_in(docs, None)
    }

    /// Resolves a mission from documents already read, in a dialect of
    /// XTCE.
    ///
    /// # Arguments
    ///
    /// * `docs`    - The documents, in the order they build on one another.
    /// * `dialect` - The dialect they follow, such as `yamcs`, when they don't
    ///               say; `None` for none.
    ///
    /// # Returns
    ///
    /// The mission.
    ///
    /// # Errors
    ///
    /// As [`Mission::from_documents`]'s.
    pub fn from_documents_in(
        docs: &[Document],
        dialect: Option<&str>,
    ) -> Result<Mission, Error> {
        let roots: Vec<&SpaceSystemType> =
            docs.iter().map(|d| &d.root).collect();
        let mut spaces = vec![Key::new()];
        for r in &roots {
            add_spaces(r, &[], &mut spaces);
        }
        let mut c = Compiler::new(roots, false, dialect)?;
        let model = c.compile()?;
        let mut slots = Vec::new();
        let mut params = Vec::new();
        for key in c.params.keys().to_vec() {
            let (ppath, p) = c.params.get(&key).unwrap().clone();
            let pkey = qname(&key);
            let got = c
                .resolve_type(
                    &p.parameter_type_ref,
                    &ppath,
                    TypeTable::Param,
                    "parameter type",
                )
                .and_then(|t| c.vtype(&t, TypeTable::Param))
                .map_err(|e| e.message().to_string())
                .and_then(|vt| {
                    let first = slots.len();
                    expand(&pkey, &[], &vt, &mut slots)?;
                    Ok((first..slots.len()).collect())
                });
            params.push(Owner {
                key: pkey,
                slots: got,
            });
        }
        let mut nodes = Vec::new();
        let mut args = Vec::new();
        let containers = c.containers.keys().to_vec();
        let commands = c.mcs.keys().to_vec();
        for (key, command) in containers
            .iter()
            .map(|k| (k, false))
            .chain(commands.iter().map(|k| (k, true)))
        {
            let node = c.nodes.get(key).unwrap().clone();
            let mut names = Vec::new();
            if command {
                let scope = c.arg_scope(key, &[])?;
                // In the order the meta-command and its bases list them.
                let mut order: Vec<&str> = Vec::new();
                let mut k = key.clone();
                let mut chain = vec![];
                loop {
                    let (path, m) = c.mcs.get(&k).unwrap().clone();
                    chain.push(m);
                    match &m.base_meta_command {
                        Some(b) => {
                            let has = |x: &[String]| c.mcs.contains(x);
                            k = Compiler::resolve(
                                &b.meta_command_ref,
                                &path,
                                &has,
                                "meta-command",
                            )?;
                        }
                        None => break,
                    }
                }
                for m in chain.iter().rev() {
                    for a in
                        m.argument_list.iter().flat_map(|l| l.argument.iter())
                    {
                        if !order.contains(&a.name.as_str()) {
                            order.push(&a.name);
                        }
                    }
                }
                for name in order {
                    let (owner, a) = scope.get(name).unwrap().clone();
                    let akey = format!("{}#{name}", qname(&owner));
                    names.push((name.to_string(), akey.clone()));
                    if args.iter().any(|o: &Owner| o.key == akey) {
                        continue;
                    }
                    let opath = c.mcs.get(&owner).unwrap().0.clone();
                    let got = c
                        .resolve_type(
                            &a.argument_type_ref,
                            &opath,
                            TypeTable::Arg,
                            "argument type",
                        )
                        .and_then(|t| c.vtype(&t, TypeTable::Arg))
                        .map_err(|e| e.message().to_string())
                        .and_then(|vt| {
                            let first = slots.len();
                            expand(&akey, &[], &vt, &mut slots)?;
                            Ok((first..slots.len()).collect())
                        });
                    args.push(Owner {
                        key: akey,
                        slots: got,
                    });
                }
            }
            nodes.push(NodeEntry {
                key: key.clone(),
                path: qname(key),
                node,
                command,
                args: names,
            });
        }
        Ok(Mission {
            id: NEXT_MISSION.fetch_add(1, Ordering::Relaxed),
            model,
            spaces,
            nodes,
            params,
            args,
            slots,
        })
    }

    /// Gives the top: the SpaceSystem above every document's root.
    ///
    /// # Returns
    ///
    /// Its handle.
    pub fn top(&self) -> SpaceSystemId {
        SpaceSystemId {
            mission: self.id,
            index: 0,
        }
    }

    /// Gives the key of a SpaceSystem a handle names.
    ///
    /// # Arguments
    ///
    /// * `base` - The handle.
    /// * `what` - What is being looked up, for the error.
    /// * `path` - The path being looked up, for the error.
    ///
    /// # Returns
    ///
    /// The key.
    ///
    /// # Errors
    ///
    /// A LookupError, if the handle is another mission's.
    fn base(
        &self,
        base: SpaceSystemId,
        what: &'static str,
        path: &str,
    ) -> Result<&Key, LookupError> {
        if base.mission != self.id {
            return Err(LookupError {
                what,
                path: path.to_string(),
                tried: Vec::new(),
                reason: Some(
                    "the base SpaceSystem is another mission's".into(),
                ),
            });
        }
        Ok(&self.spaces[base.index])
    }

    /// Lists the keys a path from a base may name, as the documents' own
    /// references are resolved; from the top, a relative path means what
    /// it means as an absolute one.
    ///
    /// # Arguments
    ///
    /// * `base` - The key of the base SpaceSystem.
    /// * `path` - The path.
    ///
    /// # Returns
    ///
    /// The keys, the first to try first.
    fn tries(base: &Key, path: &str) -> Vec<Key> {
        let p = path.trim();
        if base.is_empty() && !p.starts_with('/') {
            candidates(&format!("/{p}"), base)
        } else {
            candidates(p, base)
        }
    }

    /// Finds the first of the keys a path may name that a table holds.
    ///
    /// # Arguments
    ///
    /// * `base`  - The key of the base SpaceSystem.
    /// * `path`  - The path.
    /// * `what`  - What is looked for, for the error.
    /// * `found` - Gives what a key names, if anything.
    ///
    /// # Returns
    ///
    /// What the first key that names something names.
    ///
    /// # Errors
    ///
    /// A LookupError listing the paths tried, if none names anything.
    fn find<T>(
        base: &Key,
        path: &str,
        what: &'static str,
        found: impl Fn(&Key) -> Option<T>,
    ) -> Result<T, LookupError> {
        let tries = Self::tries(base, path);
        for k in &tries {
            if let Some(t) = found(k) {
                return Ok(t);
            }
        }
        Err(LookupError {
            what,
            path: path.to_string(),
            tried: tries.iter().map(|k| qname(k)).collect(),
            reason: None,
        })
    }

    /// Looks a SpaceSystem up from a base.
    ///
    /// # Arguments
    ///
    /// * `base` - The SpaceSystem the path is relative to.
    /// * `path` - The path; `/` names the top.
    ///
    /// # Returns
    ///
    /// Its handle.
    ///
    /// # Errors
    ///
    /// A LookupError, if no SpaceSystem has the path.
    pub fn space_system(
        &self,
        base: SpaceSystemId,
        path: &str,
    ) -> Result<SpaceSystemId, LookupError> {
        let b = self.base(base, "SpaceSystem", path)?;
        let p = path.trim();
        if p == "/" {
            return Ok(self.top());
        }
        if matches!(p.rsplit('/').next(), Some("." | "..")) {
            // A path that ends at a SpaceSystem it moves to, such as
            // `..`: it is walked from the base, or from the top.
            let mut key = if p.starts_with('/') {
                Key::new()
            } else {
                b.clone()
            };
            for part in p.split('/') {
                match part {
                    "" | "." => {}
                    ".." => {
                        key.pop();
                    }
                    name => key.push(name.to_string()),
                }
            }
            return match self.spaces.iter().position(|s| *s == key) {
                Some(index) => Ok(SpaceSystemId {
                    mission: self.id,
                    index,
                }),
                None => Err(LookupError {
                    what: "SpaceSystem",
                    path: path.to_string(),
                    tried: vec![qname(&key)],
                    reason: None,
                }),
            };
        }
        let index = Self::find(b, path, "SpaceSystem", |k| {
            self.spaces.iter().position(|s| s == k)
        })?;
        Ok(SpaceSystemId {
            mission: self.id,
            index,
        })
    }

    /// Looks a node of one kind up from a base.
    ///
    /// # Arguments
    ///
    /// * `base`    - The SpaceSystem the path is relative to.
    /// * `path`    - The path.
    /// * `command` - Whether it is a meta-command rather than a container.
    ///
    /// # Returns
    ///
    /// Its handle.
    ///
    /// # Errors
    ///
    /// A LookupError, if none has the path, or the one found isn't
    /// supported.
    fn node(
        &self,
        base: SpaceSystemId,
        path: &str,
        command: bool,
    ) -> Result<NodeId, LookupError> {
        let what = if command { "meta-command" } else { "container" };
        let b = self.base(base, what, path)?;
        let index = Self::find(b, path, what, |k| {
            self.nodes
                .iter()
                .position(|n| n.command == command && &n.key == k)
        })?;
        if let Some(e) = &self.nodes[index].node.get().error {
            return Err(LookupError {
                what,
                path: path.to_string(),
                tried: Vec::new(),
                reason: Some(format!(
                    "{} is not supported: {e}",
                    self.nodes[index].path
                )),
            });
        }
        Ok(NodeId {
            mission: self.id,
            index,
        })
    }

    /// Looks a container up from a base.
    ///
    /// # Arguments
    ///
    /// * `base` - The SpaceSystem the path is relative to.
    /// * `path` - The path.
    ///
    /// # Returns
    ///
    /// Its handle.
    ///
    /// # Errors
    ///
    /// A LookupError, if no container has the path, or it isn't
    /// supported.
    pub fn container(
        &self,
        base: SpaceSystemId,
        path: &str,
    ) -> Result<NodeId, LookupError> {
        self.node(base, path, false)
    }

    /// Looks a meta-command up from a base.
    ///
    /// # Arguments
    ///
    /// * `base` - The SpaceSystem the path is relative to.
    /// * `path` - The path.
    ///
    /// # Returns
    ///
    /// Its handle.
    ///
    /// # Errors
    ///
    /// A LookupError, if no meta-command has the path, or it isn't
    /// supported.
    pub fn meta_command(
        &self,
        base: SpaceSystemId,
        path: &str,
    ) -> Result<NodeId, LookupError> {
        self.node(base, path, true)
    }

    /// Finds the slot of a parameter or argument at some steps.
    ///
    /// # Arguments
    ///
    /// * `owner` - The parameter or argument.
    /// * `steps` - The member and index steps into it.
    /// * `what`  - What is looked for, for the error.
    /// * `path`  - The path given, for the error.
    ///
    /// # Returns
    ///
    /// The slot's handle.
    ///
    /// # Errors
    ///
    /// A LookupError, if it has no slots, or none at the steps.
    fn slot(
        &self,
        owner: &Owner,
        steps: &[Step],
        what: &'static str,
        path: &str,
    ) -> Result<SlotId, LookupError> {
        let fail = |reason: String| LookupError {
            what,
            path: path.to_string(),
            tried: Vec::new(),
            reason: Some(reason),
        };
        let slots = owner.slots.as_ref().map_err(|e| fail(e.clone()))?;
        if let Some(&i) = slots.iter().find(|&&i| self.slots[i].steps == steps)
        {
            return Ok(SlotId {
                mission: self.id,
                index: i,
            });
        }
        let at: String = steps.iter().map(step_text).collect();
        let inside = slots
            .iter()
            .any(|&i| self.slots[i].steps.starts_with(steps));
        Err(fail(if inside {
            format!(
                "{}{at} is an aggregate or array: name one of its members \
                 or elements",
                owner.key
            )
        } else {
            format!("{} has no member or element {at}", owner.key)
        }))
    }

    /// Looks a parameter, or a member or element of one, up from a base.
    ///
    /// # Arguments
    ///
    /// * `base` - The SpaceSystem the path is relative to.
    /// * `path` - The path, which may end in member and index steps, such as
    ///            `Attitude.q[2]`.
    ///
    /// # Returns
    ///
    /// The slot's handle.
    ///
    /// # Errors
    ///
    /// A LookupError, if no parameter has the path, or it names an
    /// aggregate or array rather than a scalar, or a member or element it
    /// doesn't have.
    pub fn parameter(
        &self,
        base: SpaceSystemId,
        path: &str,
    ) -> Result<SlotId, LookupError> {
        let b = self.base(base, "parameter", path)?;
        let (head, steps) = member_path(path).map_err(|e| LookupError {
            what: "parameter",
            path: path.to_string(),
            tried: Vec::new(),
            reason: Some(e.message().to_string()),
        })?;
        let owner = Self::find(b, &head, "parameter", |k| {
            let key = qname(k);
            self.params.iter().find(|p| p.key == key)
        })
        .map_err(|e| LookupError {
            path: path.to_string(),
            ..e
        })?;
        self.slot(owner, &steps, "parameter", path)
    }

    /// Gives the entry of a node a handle names.
    ///
    /// # Arguments
    ///
    /// * `node` - The handle.
    /// * `what` - What is looked for, for the error.
    /// * `name` - The name looked for, for the error.
    ///
    /// # Returns
    ///
    /// The entry.
    ///
    /// # Errors
    ///
    /// A LookupError, if the handle is another mission's.
    fn node_entry(
        &self,
        node: NodeId,
        what: &'static str,
        name: &str,
    ) -> Result<&NodeEntry, LookupError> {
        if node.mission != self.id {
            return Err(LookupError {
                what,
                path: name.to_string(),
                tried: Vec::new(),
                reason: Some("the node is another mission's".into()),
            });
        }
        Ok(&self.nodes[node.index])
    }

    /// Looks an argument of a meta-command up: one of its own or its
    /// bases', or a member or element of one.
    ///
    /// # Arguments
    ///
    /// * `cmd`  - The meta-command.
    /// * `name` - The argument's name, which may end in member and index
    ///            steps.
    ///
    /// # Returns
    ///
    /// The slot's handle.
    ///
    /// # Errors
    ///
    /// A LookupError, if the meta-command has no such argument, or it
    /// names an aggregate or array rather than a scalar.
    pub fn argument(
        &self,
        cmd: NodeId,
        name: &str,
    ) -> Result<SlotId, LookupError> {
        let n = self.node_entry(cmd, "argument", name)?;
        let fail = |reason: String| LookupError {
            what: "argument",
            path: name.to_string(),
            tried: Vec::new(),
            reason: Some(reason),
        };
        if !n.command {
            return Err(fail(format!(
                "{} is a container, not a meta-command",
                n.path
            )));
        }
        let (head, steps) =
            member_path(name).map_err(|e| fail(e.message().to_string()))?;
        let Some((_, akey)) = n.args.iter().find(|a| a.0 == head) else {
            return Err(fail(format!("{} has no argument {head}", n.path)));
        };
        let owner = self.args.iter().find(|o| &o.key == akey).unwrap();
        self.slot(owner, &steps, "argument", name)
    }

    /// Looks an entry of a container or meta-command up: a parameter or
    /// argument its entry list, or one of its bases', holds, by the name
    /// the parameter or argument has, or a member or element of one; or,
    /// for a meta-command, any of its arguments.
    ///
    /// # Arguments
    ///
    /// * `node` - The container or meta-command.
    /// * `name` - The name, which may end in member and index steps.
    ///
    /// # Returns
    ///
    /// The slot's handle.
    ///
    /// # Errors
    ///
    /// A LookupError, if the node holds no such value, or it names an
    /// aggregate or array rather than a scalar.
    pub fn entry(
        &self,
        node: NodeId,
        name: &str,
    ) -> Result<SlotId, LookupError> {
        let n = self.node_entry(node, "entry", name)?;
        let fail = |reason: String| LookupError {
            what: "entry",
            path: name.to_string(),
            tried: Vec::new(),
            reason: Some(reason),
        };
        let (head, steps) =
            member_path(name).map_err(|e| fail(e.message().to_string()))?;
        let held = n
            .node
            .scope()
            .into_iter()
            .rev()
            .find(|l| l.get().name == head)
            .map(|l| l.get().key.clone());
        let owner = match held {
            Some(k) => self
                .params
                .iter()
                .chain(self.args.iter())
                .find(|o| o.key == k),
            None => None,
        };
        match owner {
            Some(o) => self.slot(o, &steps, "entry", name),
            None if n.command => self
                .argument(node, name)
                .map_err(|e| LookupError { what: "entry", ..e }),
            None => Err(fail(format!("{} holds no {head}", n.path))),
        }
    }

    /// Gives the absolute path of a SpaceSystem.
    ///
    /// # Arguments
    ///
    /// * `ss` - Its handle, which must be this mission's.
    ///
    /// # Returns
    ///
    /// The path; `/` for the top.
    ///
    /// # Panics
    ///
    /// If the handle is another mission's.
    pub fn path_of_space_system(&self, ss: SpaceSystemId) -> String {
        assert_eq!(ss.mission, self.id, "a SpaceSystemId of another mission");
        if ss.index == 0 {
            "/".into()
        } else {
            qname(&self.spaces[ss.index])
        }
    }

    /// Gives the absolute path of a container or meta-command.
    ///
    /// # Arguments
    ///
    /// * `node` - Its handle, which must be this mission's.
    ///
    /// # Returns
    ///
    /// The path.
    ///
    /// # Panics
    ///
    /// If the handle is another mission's.
    pub fn path_of_node(&self, node: NodeId) -> &str {
        assert_eq!(node.mission, self.id, "a NodeId of another mission");
        &self.nodes[node.index].path
    }

    /// Gives the absolute path of a slot: its parameter's, or for an
    /// argument its meta-command's, `#` and its name, then its member and
    /// index steps.
    ///
    /// # Arguments
    ///
    /// * `slot` - Its handle, which must be this mission's.
    ///
    /// # Returns
    ///
    /// The path.
    ///
    /// # Panics
    ///
    /// If the handle is another mission's.
    pub fn path_of_slot(&self, slot: SlotId) -> &str {
        assert_eq!(slot.mission, self.id, "a SlotId of another mission");
        &self.slots[slot.index].path
    }

    /// Gives the type of a slot.
    ///
    /// # Arguments
    ///
    /// * `slot` - Its handle, which must be this mission's.
    ///
    /// # Returns
    ///
    /// The type.
    ///
    /// # Panics
    ///
    /// If the handle is another mission's.
    pub fn slot_type(&self, slot: SlotId) -> Type {
        assert_eq!(slot.mission, self.id, "a SlotId of another mission");
        Type {
            vt: self.slots[slot.index].vt.clone(),
        }
    }

    /// Says whether a container or meta-command is abstract: whether it is
    /// only ever the base of others, and so is unpacked by classifying a
    /// packet as one of them.
    ///
    /// # Arguments
    ///
    /// * `node` - Its handle, which must be this mission's.
    ///
    /// # Returns
    ///
    /// Whether it is.
    ///
    /// # Panics
    ///
    /// If the handle is another mission's.
    pub fn is_abstract(&self, node: NodeId) -> bool {
        assert_eq!(node.mission, self.id, "a NodeId of another mission");
        self.nodes[node.index].node.get().abstract_
    }

    /// Gives the most bytes a packet of a concrete container or
    /// meta-command takes.
    ///
    /// # Arguments
    ///
    /// * `node` - Its handle, which must be this mission's.
    ///
    /// # Returns
    ///
    /// The bytes, or `None` for an abstract node.
    ///
    /// # Panics
    ///
    /// If the handle is another mission's.
    pub fn max_bytes(&self, node: NodeId) -> Option<usize> {
        assert_eq!(node.mission, self.id, "a NodeId of another mission");
        let n = self.nodes[node.index].node.get();
        (!n.abstract_).then_some(n.max_bytes as usize)
    }

    /// Gives the resolved model the mission was made from.
    ///
    /// # Returns
    ///
    /// The model.
    pub fn model(&self) -> &tcs_xtce_model::model::Mission {
        &self.model
    }
}
