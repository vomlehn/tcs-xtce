//! The tcs-xtce compiler's analysis, in Rust: from the XTCE documents of a
//! mission, as the `tcs-xtce` binding reads them, to the resolved model
//! of its packets, as the Python compiler's `compile.py` and `model.py`
//! make it.
//!
//! The model is ported a stage at a time, each stage compared with the
//! Python's on every mission the tests compile: both write it as the same
//! canonical text ([`dump`]), which must be identical line for line (see
//! `docs/rust-migration-design.rst`). The first stage is the parameter and
//! argument types, which [`dump_types`] writes; the second the telemetry
//! containers, which [`dump_containers`] writes.

pub mod compile;
pub mod dump;
pub mod model;
pub mod names;
pub mod pyfmt;
pub mod pyparse;
pub mod regex;
pub mod text;

use compile::{qname, Compiler, TypeTable};
use dump::{Cx, Dict, ToTree, Tree};
use model::{Error, VType};

/// A type that does not resolve, as [`dump_types`] writes it.
#[derive(Debug, Clone)]
pub struct Failed {
    /// `Unsupported` or `Cycle`.
    pub error: String,
    /// What it says.
    pub message: String,
}
value_record!(Failed, "Failed", [error, message]);

/// A type's resolution: its type, or why it fails.
#[derive(Debug, Clone)]
pub enum Resolved {
    /// The type.
    Type(std::rc::Rc<VType>),
    /// Why it fails.
    Failed(Failed),
}

impl ToTree for Resolved {
    fn tree(&self, cx: &mut Cx) -> Tree {
        match self {
            Resolved::Type(t) => t.tree(cx),
            Resolved::Failed(f) => f.tree(cx),
        }
    }
}

/// Every type of a mission, each resolved alone.
#[derive(Debug, Clone)]
pub struct Types {
    /// Each parameter type's resolution, by qualified name, in the
    /// documents' order.
    pub parameter: Dict<String, Resolved>,
    /// Each argument type's, likewise.
    pub argument: Dict<String, Resolved>,
    /// The warnings resolving them gave, in order.
    pub warnings: Vec<String>,
}
value_record!(Types, "Types", [parameter, argument, warnings]);

/// Writes every parameter and argument type of a mission, each resolved
/// alone, as canonical text, as the Python compiler's `--dump-types` does.
///
/// # Arguments
///
/// * `compiler` - The compiler, with the documents indexed and nothing compiled
///                yet.
///
/// # Returns
///
/// The text of a `Types`, ending in a newline.
pub fn dump_types(compiler: &mut Compiler<'_>) -> String {
    let mut each = |table: TypeTable| -> Dict<String, Resolved> {
        let keys = compiler.types(table).keys().to_vec();
        let mut out = Dict::default();
        for key in keys {
            let r = match compiler.vtype(&key, table) {
                Ok(t) => Resolved::Type(t),
                Err(e) => Resolved::Failed(Failed {
                    error: e.class().into(),
                    message: e.message().into(),
                }),
            };
            out.0.push((qname(&key), r));
        }
        out
    };
    let parameter = each(TypeTable::Param);
    let argument = each(TypeTable::Arg);
    let types = Types {
        parameter,
        argument,
        warnings: compiler.warnings.clone(),
    };
    dump::dump(&types)
}

/// Says what an error is, as the Python compiler's command does.
///
/// # Arguments
///
/// * `e` - The error.
///
/// # Returns
///
/// Its message.
pub fn describe(e: &Error) -> String {
    e.message().to_string()
}

/// A container's node, or why it fails, as [`dump_containers`] writes it.
#[derive(Debug, Clone)]
pub enum Built {
    /// The node.
    Node(dump::Shared<model::Node>),
    /// Why it fails.
    Failed(Failed),
}

impl ToTree for Built {
    fn tree(&self, cx: &mut Cx) -> Tree {
        match self {
            Built::Node(n) => n.tree(cx),
            Built::Failed(f) => f.tree(cx),
        }
    }
}

/// Every telemetry container of a mission, each compiled as `tm_node`
/// compiles it.
#[derive(Debug, Clone)]
pub struct Containers {
    /// Each container's node, or why it fails, by qualified name, in the
    /// documents' order.
    pub container: Dict<String, Built>,
    /// The warnings compiling them gave, in order.
    pub warnings: Vec<String>,
}
value_record!(Containers, "Containers", [container, warnings]);

/// Writes every telemetry container of a mission, each compiled as
/// `tm_node` compiles it, before the mission is finished, as canonical
/// text, as the Python compiler's `--dump-containers` does.
///
/// # Arguments
///
/// * `compiler` - The compiler, with the documents indexed and nothing compiled
///                yet.
///
/// # Returns
///
/// The text of a `Containers`, ending in a newline.
pub fn dump_containers(compiler: &mut Compiler<'_>) -> String {
    let keys = compiler.containers.keys().to_vec();
    let mut container = Dict::default();
    for key in keys {
        let b = match compiler.tm_node(&key) {
            Ok(n) => Built::Node(n),
            Err(e) => Built::Failed(Failed {
                error: e.class().into(),
                message: e.message().into(),
            }),
        };
        container.0.push((qname(&key), b));
    }
    let c = Containers {
        container,
        warnings: compiler.warnings.clone(),
    };
    dump::dump(&c)
}
