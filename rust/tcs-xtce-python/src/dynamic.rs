//! `tcs_xtce._native.dynamic`: the run-time codec, tcs-xtce-dynamic, for
//! Python, which `tcs_xtce.dynamic` gives. A thin layer that does nothing
//! the Rust codec doesn't: values pass as Python's own, the functions a
//! mission's algorithms need are any Python callable, and failures are
//! raised.

use std::cell::{Cell, RefCell};

use pyo3::create_exception;
use pyo3::exceptions::{PyException, PyLookupError, PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyList};

use tcs_xtce_dynamic as xd;
use xd::{Algorithm, Datum};

create_exception!(
    dynamic,
    PacketError,
    PyException,
    "A packet that doesn't decode or encode."
);
create_exception!(
    dynamic,
    UnsupportedError,
    PacketError,
    "A packet that uses what the codec doesn't run yet."
);
create_exception!(
    dynamic,
    SetError,
    PyValueError,
    "A value that can't be given to a slot."
);

thread_local! {
    /// The first exception a Python function supplied for an algorithm
    /// raised during the codec's current call, raised when it returns.
    static PENDING: RefCell<Option<PyErr>> = const { RefCell::new(None) };
}

/// Keeps an exception a supplied function raised, to raise later.
///
/// # Arguments
///
/// * `e` - The exception.
fn keep(e: PyErr) {
    PENDING.with(|p| {
        let mut p = p.borrow_mut();
        if p.is_none() {
            *p = Some(e);
        }
    });
}

/// Raises an exception a supplied function raised, if one did.
///
/// # Returns
///
/// Nothing.
///
/// # Errors
///
/// The exception.
fn raise_kept() -> PyResult<()> {
    match PENDING.with(|p| p.borrow_mut().take()) {
        Some(e) => Err(e),
        None => Ok(()),
    }
}

/// Gives the Python exception of a failure to pack or unpack.
///
/// # Arguments
///
/// * `e` - The failure.
///
/// # Returns
///
/// The exception, or the one a supplied function raised if one did.
fn run_error(e: xd::RunError) -> PyErr {
    if let Some(k) = PENDING.with(|p| p.borrow_mut().take()) {
        return k;
    }
    match e {
        xd::RunError::Unsupported(w) => UnsupportedError::new_err(w),
        xd::RunError::Foreign => {
            PyValueError::new_err("a handle of another mission")
        }
        e => PacketError::new_err(e.to_string()),
    }
}

/// Gives the Python exception of a lookup that found nothing.
///
/// # Arguments
///
/// * `e` - The failure.
///
/// # Returns
///
/// A `LookupError`.
fn lookup_error(e: xd::LookupError) -> PyErr {
    PyLookupError::new_err(e.to_string())
}

/// A SpaceSystem of a mission.
#[pyclass(frozen, eq, hash, module = "tcs_xtce.dynamic")]
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
pub struct Space {
    /// The mission's number.
    mission: u64,
    /// Its place in the mission's list.
    index: usize,
}

/// A container or meta-command of a mission.
#[pyclass(frozen, eq, hash, module = "tcs_xtce.dynamic")]
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
pub struct Node {
    /// The mission's number.
    mission: u64,
    /// Its place in the mission's list.
    index: usize,
}

/// One scalar value of a mission.
#[pyclass(frozen, eq, hash, module = "tcs_xtce.dynamic")]
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
pub struct Slot {
    /// The mission's number.
    mission: u64,
    /// Its place in the mission's list.
    index: usize,
}

/// Gives a SpaceSystem's Python handle.
///
/// # Arguments
///
/// * `h` - The Rust handle.
///
/// # Returns
///
/// The Python handle.
fn py_space(h: xd::SpaceSystemId) -> Space {
    let (mission, index) = xd::Mission::space_system_raw(h);
    Space { mission, index }
}

/// Gives a node's Python handle.
///
/// # Arguments
///
/// * `h` - The Rust handle.
///
/// # Returns
///
/// The Python handle.
fn py_node(h: xd::NodeId) -> Node {
    let (mission, index) = xd::Mission::node_raw(h);
    Node { mission, index }
}

/// Gives a slot's Python handle.
///
/// # Arguments
///
/// * `h` - The Rust handle.
///
/// # Returns
///
/// The Python handle.
fn py_slot(h: xd::SlotId) -> Slot {
    let (mission, index) = xd::Mission::slot_raw(h);
    Slot { mission, index }
}

/// The message for a handle that isn't the mission's.
const FOREIGN: &str = "a handle of another mission";

/// A mission, read and resolved, and what it is asked.
#[pyclass(unsendable, module = "tcs_xtce.dynamic")]
pub struct Mission {
    /// The mission.
    m: xd::Mission,
    /// How many of its streams exist, which borrow it.
    streams: Cell<usize>,
}

impl Mission {
    /// Gives the Rust handle of a SpaceSystem.
    ///
    /// # Arguments
    ///
    /// * `h` - The Python handle.
    ///
    /// # Returns
    ///
    /// The Rust handle.
    ///
    /// # Errors
    ///
    /// `ValueError`, if it isn't the mission's.
    fn space(&self, h: &Space) -> PyResult<xd::SpaceSystemId> {
        self.m
            .space_system_from_raw(h.mission, h.index)
            .ok_or_else(|| PyValueError::new_err(FOREIGN))
    }

    /// Gives the Rust handle of a node.
    ///
    /// # Arguments
    ///
    /// * `h` - The Python handle.
    ///
    /// # Returns
    ///
    /// The Rust handle.
    ///
    /// # Errors
    ///
    /// `ValueError`, if it isn't the mission's.
    fn node(&self, h: &Node) -> PyResult<xd::NodeId> {
        self.m
            .node_from_raw(h.mission, h.index)
            .ok_or_else(|| PyValueError::new_err(FOREIGN))
    }

    /// Gives the Rust handle of a slot.
    ///
    /// # Arguments
    ///
    /// * `h` - The Python handle.
    ///
    /// # Returns
    ///
    /// The Rust handle.
    ///
    /// # Errors
    ///
    /// `ValueError`, if it isn't the mission's.
    fn slot(&self, h: &Slot) -> PyResult<xd::SlotId> {
        self.m
            .slot_from_raw(h.mission, h.index)
            .ok_or_else(|| PyValueError::new_err(FOREIGN))
    }

    /// Gives a run of slots as a list.
    ///
    /// # Arguments
    ///
    /// * `s` - The slots.
    ///
    /// # Returns
    ///
    /// Each slot's handle.
    fn slot_list(s: xd::Slots) -> Vec<Slot> {
        s.iter().map(py_slot).collect()
    }
}

/// Calls a Python function the application supplied for an algorithm,
/// keeping any exception it raises.
///
/// # Arguments
///
/// * `f`    - The function.
/// * `call` - Calls it and converts what it gives.
///
/// # Returns
///
/// What it gives, or `None` if it raised.
fn attach<T>(
    f: &Py<PyAny>,
    call: impl FnOnce(&Bound<'_, PyAny>) -> PyResult<T>,
) -> Option<T> {
    Python::attach(|py| match call(f.bind(py)) {
        Ok(x) => Some(x),
        Err(e) => {
            keep(e);
            None
        }
    })
}

/// Makes the codec's closure for a Python function supplied for an
/// algorithm of a kind.
///
/// # Arguments
///
/// * `kind` - The kind of function the algorithm needs.
/// * `f`    - The Python function.
///
/// # Returns
///
/// The codec's algorithm.
///
/// # Errors
///
/// `ValueError`, for a kind the codec has none of.
fn algorithm(kind: &str, f: Py<PyAny>) -> PyResult<Algorithm> {
    Ok(match kind {
        "criterion" => Algorithm::criterion(move |x| {
            attach(&f, |g| g.call1((x.to_vec(),))?.extract()).unwrap_or(false)
        }),
        "algorithm" => Algorithm::outputs(move |x, out| {
            let got = attach(&f, |g| {
                let r = g.call1((x.to_vec(),))?;
                if r.is_none() {
                    return Ok(None);
                }
                r.extract::<Vec<f64>>().map(Some)
            });
            match got.flatten() {
                Some(v) => {
                    for (o, y) in out.iter_mut().zip(v) {
                        *o = y;
                    }
                    true
                }
                None => false,
            }
        }),
        "alarm" => Algorithm::alarm(move |x| {
            attach(&f, |g| g.call1((x.to_vec(),))?.extract()).unwrap_or(0)
        }),
        "checksum" => Algorithm::checksum(move |d, s, e| {
            attach(&f, |g| {
                let b = PyBytes::new(g.py(), d);
                g.call1((b, s, e))?.extract()
            })
            .unwrap_or(0)
        }),
        "binary_from" => Algorithm::binary_from(move |b, out| {
            let got = attach(&f, |g| {
                let r = g.call1((PyBytes::new(g.py(), b),))?;
                if r.is_none() {
                    return Ok(None);
                }
                r.extract::<(usize, Vec<u8>)>().map(Some)
            });
            let (took, made) = got.flatten()?;
            if made.len() <= out.len() {
                out[..made.len()].copy_from_slice(&made);
            }
            Some((took, made.len()))
        }),
        "binary_to" => Algorithm::binary_to(move |b, out| {
            let got = attach(&f, |g| {
                let r = g.call1((PyBytes::new(g.py(), b),))?;
                if r.is_none() {
                    return Ok(None);
                }
                r.extract::<Vec<u8>>().map(Some)
            });
            let made = got.flatten()?;
            if made.len() <= out.len() {
                out[..made.len()].copy_from_slice(&made);
            }
            Some(made.len())
        }),
        "time_from" => Algorithm::time_from(move |b| {
            attach(&f, |g| {
                let r = g.call1((PyBytes::new(g.py(), b),))?;
                if r.is_none() {
                    return Ok(None);
                }
                r.extract::<(usize, f64)>().map(Some)
            })
            .flatten()
        }),
        "time_to" => Algorithm::time_to(move |s, out| {
            let got = attach(&f, |g| {
                let r = g.call1((s,))?;
                if r.is_none() {
                    return Ok(None);
                }
                r.extract::<Vec<u8>>().map(Some)
            });
            let made = got.flatten()?;
            if made.len() <= out.len() {
                out[..made.len()].copy_from_slice(&made);
            }
            Some(made.len())
        }),
        "stream_decode" => Algorithm::stream_decode(move |b, emit| {
            let got = attach(&f, |g| {
                let r = g.call1((PyBytes::new(g.py(), b),))?;
                if r.is_none() {
                    return Ok(Vec::new());
                }
                if let Ok(one) = r.extract::<Vec<u8>>() {
                    return Ok(vec![one]);
                }
                r.extract::<Vec<Vec<u8>>>()
            });
            for piece in got.unwrap_or_default() {
                emit(&piece);
            }
        }),
        "bit_invert" => Algorithm::bit_invert(move |bit| {
            attach(&f, |g| g.call1((bit,))?.extract()).unwrap_or(bit ^ 1)
        }),
        k => {
            return Err(PyValueError::new_err(format!(
                "no algorithms of kind {k}"
            )))
        }
    })
}

/// Gives a datum as a Python value.
///
/// # Arguments
///
/// * `py` - The Python interpreter.
/// * `d`  - The datum.
///
/// # Returns
///
/// An `int`, `float`, `bool`, `str` or `bytes`: an enumeration's value
/// and a wide integer are ints, and a time is its seconds.
///
/// # Errors
///
/// Fails if Python can't make the value.
fn to_py(py: Python<'_>, d: Datum<'_>) -> PyResult<Py<PyAny>> {
    use pyo3::IntoPyObjectExt;
    match d {
        Datum::Int(i) | Datum::Enum(i) => i.into_py_any(py),
        Datum::Big(w) => {
            let text = xd::to_big(w).to_string();
            Ok(py.get_type::<pyo3::types::PyInt>().call1((text,))?.unbind())
        }
        Datum::Float(x) | Datum::Time(x) => x.into_py_any(py),
        Datum::Bool(b) => b.into_py_any(py),
        Datum::Str(s) => s.into_py_any(py),
        Datum::Binary(b) => Ok(PyBytes::new(py, b).into_any().unbind()),
    }
}

/// The current value of every slot of a mission.
#[pyclass(unsendable, module = "tcs_xtce.dynamic")]
pub struct Store {
    /// The store.
    s: xd::Store,
    /// Where packing puts its bytes, kept from one packing to the next.
    out: tcs_xtce::buffer::Buffer,
    /// The mission, which checks the slots given.
    mission: Py<Mission>,
}

/// A packet decoded and not yet committed.
#[pyclass(unsendable, module = "tcs_xtce.dynamic")]
pub struct Scratch {
    /// The scratch.
    s: xd::Scratch,
}

/// The alarm state that change ranges and minimum violations count.
#[pyclass(unsendable, module = "tcs_xtce.dynamic")]
pub struct Monitor {
    /// The monitor.
    m: xd::Monitor,
}

/// A stream's frame synchronizer, which keeps its mission alive.
#[pyclass(unsendable, module = "tcs_xtce.dynamic")]
pub struct Deframer {
    /// The synchronizer, which borrows the mission below.
    d: Option<xd::Deframer<'static>>,
    /// The mission.
    mission: Py<Mission>,
}

impl Drop for Deframer {
    fn drop(&mut self) {
        // The synchronizer goes before the mission it borrows.
        self.d = None;
        Python::attach(|py| {
            let m = self.mission.borrow(py);
            m.streams.set(m.streams.get() - 1);
        });
    }
}

#[pymethods]
impl Deframer {
    /// Takes a stream's next bytes.
    ///
    /// # Arguments
    ///
    /// * `py`   - The Python interpreter.
    /// * `data` - The bytes, in the order they arrive.
    ///
    /// # Returns
    ///
    /// The frames they complete, each as bytes.
    ///
    /// # Errors
    ///
    /// What a supplied function raised.
    fn push<'py>(
        &mut self,
        py: Python<'py>,
        data: &[u8],
    ) -> PyResult<Bound<'py, PyList>> {
        let mut frames = Vec::new();
        if let Some(d) = &mut self.d {
            d.push(data, |f| frames.push(f.to_vec()));
        }
        raise_kept()?;
        let list = PyList::empty(py);
        for f in frames {
            list.append(PyBytes::new(py, &f))?;
        }
        Ok(list)
    }
}

#[pymethods]
impl Mission {
    /// Reads and resolves a mission from its documents.
    ///
    /// # Arguments
    ///
    /// * `docs` - Each document's text, as `bytes` or `str`, in the order
    ///            they build on one another.
    ///
    /// # Returns
    ///
    /// The mission.
    ///
    /// # Errors
    ///
    /// `ValueError`, if a document can't be read or resolved.
    #[new]
    fn new(docs: Vec<Bound<'_, PyAny>>) -> PyResult<Self> {
        let mut texts: Vec<Vec<u8>> = Vec::new();
        for d in &docs {
            if let Ok(s) = d.extract::<String>() {
                texts.push(s.into_bytes());
            } else {
                texts.push(d.extract::<Vec<u8>>()?);
            }
        }
        let m = xd::Mission::read_from(texts.iter().map(|t| &t[..]))
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        Ok(Mission {
            m,
            streams: Cell::new(0),
        })
    }

    /// Gives the mission's top SpaceSystem.
    ///
    /// # Returns
    ///
    /// Its handle.
    #[getter]
    fn top(&self) -> Space {
        py_space(self.m.top())
    }

    /// Gives the mission's digest, as xtce-digest computes it.
    ///
    /// # Arguments
    ///
    /// * `py` - The Python interpreter.
    ///
    /// # Returns
    ///
    /// Its 16 bytes.
    fn digest<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
        PyBytes::new(py, &self.m.digest())
    }

    /// Looks a SpaceSystem up from a base.
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
    /// `LookupError`, if there is none.
    fn space_system(&self, base: &Space, path: &str) -> PyResult<Space> {
        let b = self.space(base)?;
        self.m
            .space_system(b, path)
            .map(py_space)
            .map_err(lookup_error)
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
    /// `LookupError`, if there is none.
    fn container(&self, base: &Space, path: &str) -> PyResult<Node> {
        let b = self.space(base)?;
        self.m.container(b, path).map(py_node).map_err(lookup_error)
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
    /// `LookupError`, if there is none.
    fn meta_command(&self, base: &Space, path: &str) -> PyResult<Node> {
        let b = self.space(base)?;
        self.m
            .meta_command(b, path)
            .map(py_node)
            .map_err(lookup_error)
    }

    /// Looks a parameter, or a scalar member or element of one, up.
    ///
    /// # Arguments
    ///
    /// * `base` - The SpaceSystem the path is relative to.
    /// * `path` - The path, such as `Probes[1].Id`.
    ///
    /// # Returns
    ///
    /// Its slot.
    ///
    /// # Errors
    ///
    /// `LookupError`, if there is none, or it isn't a scalar.
    fn parameter(&self, base: &Space, path: &str) -> PyResult<Slot> {
        let b = self.space(base)?;
        self.m.parameter(b, path).map(py_slot).map_err(lookup_error)
    }

    /// Looks the slots of a parameter, or of a member or element of one,
    /// up.
    ///
    /// # Arguments
    ///
    /// * `base` - The SpaceSystem the path is relative to.
    /// * `path` - The path.
    ///
    /// # Returns
    ///
    /// Its slots, in order.
    ///
    /// # Errors
    ///
    /// `LookupError`, if there is none.
    fn parameter_slots(&self, base: &Space, path: &str) -> PyResult<Vec<Slot>> {
        let b = self.space(base)?;
        self.m
            .parameter_slots(b, path)
            .map(Self::slot_list)
            .map_err(lookup_error)
    }

    /// Looks an argument of a meta-command up.
    ///
    /// # Arguments
    ///
    /// * `cmd`  - The meta-command.
    /// * `name` - The argument's name.
    ///
    /// # Returns
    ///
    /// Its slot.
    ///
    /// # Errors
    ///
    /// `LookupError`, if there is none.
    fn argument(&self, cmd: &Node, name: &str) -> PyResult<Slot> {
        let n = self.node(cmd)?;
        self.m.argument(n, name).map(py_slot).map_err(lookup_error)
    }

    /// Looks the slots of an argument up.
    ///
    /// # Arguments
    ///
    /// * `cmd`  - The meta-command.
    /// * `name` - The argument's name.
    ///
    /// # Returns
    ///
    /// Its slots, in order.
    ///
    /// # Errors
    ///
    /// `LookupError`, if there is none.
    fn argument_slots(&self, cmd: &Node, name: &str) -> PyResult<Vec<Slot>> {
        let n = self.node(cmd)?;
        self.m
            .argument_slots(n, name)
            .map(Self::slot_list)
            .map_err(lookup_error)
    }

    /// Looks an entry of a container or meta-command up.
    ///
    /// # Arguments
    ///
    /// * `node` - The node.
    /// * `name` - The entry's name.
    ///
    /// # Returns
    ///
    /// Its slot.
    ///
    /// # Errors
    ///
    /// `LookupError`, if there is none.
    fn entry(&self, node: &Node, name: &str) -> PyResult<Slot> {
        let n = self.node(node)?;
        self.m.entry(n, name).map(py_slot).map_err(lookup_error)
    }

    /// Looks the slots of an entry up.
    ///
    /// # Arguments
    ///
    /// * `node` - The node.
    /// * `name` - The entry's name.
    ///
    /// # Returns
    ///
    /// Its slots, in order.
    ///
    /// # Errors
    ///
    /// `LookupError`, if there is none.
    fn entry_slots(&self, node: &Node, name: &str) -> PyResult<Vec<Slot>> {
        let n = self.node(node)?;
        self.m
            .entry_slots(n, name)
            .map(Self::slot_list)
            .map_err(lookup_error)
    }

    /// Gives the absolute path of a SpaceSystem, node or slot.
    ///
    /// # Arguments
    ///
    /// * `h` - The handle.
    ///
    /// # Returns
    ///
    /// The path.
    ///
    /// # Errors
    ///
    /// `TypeError`, for anything else; `ValueError`, for another
    /// mission's handle.
    fn path_of(&self, h: &Bound<'_, PyAny>) -> PyResult<String> {
        if let Ok(s) = h.cast::<Space>() {
            return Ok(self.m.path_of_space_system(self.space(s.get())?));
        }
        if let Ok(n) = h.cast::<Node>() {
            return Ok(self.m.path_of_node(self.node(n.get())?).to_string());
        }
        if let Ok(s) = h.cast::<Slot>() {
            return Ok(self.m.path_of_slot(self.slot(s.get())?).to_string());
        }
        Err(PyTypeError::new_err("a Space, Node or Slot"))
    }

    /// Gives the kind of a slot's type.
    ///
    /// # Arguments
    ///
    /// * `slot` - The slot.
    ///
    /// # Returns
    ///
    /// `int`, `float`, `enum`, `bool`, `string` or `binary`.
    ///
    /// # Errors
    ///
    /// `ValueError`, for another mission's slot.
    fn kind(&self, slot: &Slot) -> PyResult<String> {
        Ok(self.m.slot_type(self.slot(slot)?).kind().to_string())
    }

    /// Says whether a slot is a time, and which kind.
    ///
    /// # Arguments
    ///
    /// * `slot` - The slot.
    ///
    /// # Returns
    ///
    /// `absolute` or `relative`, or `None` for anything else.
    ///
    /// # Errors
    ///
    /// `ValueError`, for another mission's slot.
    fn time(&self, slot: &Slot) -> PyResult<Option<&'static str>> {
        Ok(self.m.slot_type(self.slot(slot)?).time())
    }

    /// Gives an enumeration slot's labels.
    ///
    /// # Arguments
    ///
    /// * `slot` - The slot.
    ///
    /// # Returns
    ///
    /// Each label and its value; empty for another kind.
    ///
    /// # Errors
    ///
    /// `ValueError`, for another mission's slot.
    fn labels(&self, slot: &Slot) -> PyResult<Vec<(String, i64)>> {
        let t = self.m.slot_type(self.slot(slot)?);
        Ok(t.labels()
            .into_iter()
            .map(|(l, v)| (l.to_string(), v))
            .collect())
    }

    /// Says whether a container or meta-command is abstract.
    ///
    /// # Arguments
    ///
    /// * `node` - The node.
    ///
    /// # Returns
    ///
    /// Whether it is.
    ///
    /// # Errors
    ///
    /// `ValueError`, for another mission's node.
    fn is_abstract(&self, node: &Node) -> PyResult<bool> {
        Ok(self.m.is_abstract(self.node(node)?))
    }

    /// Lists what the mission's packets use that the codec doesn't run
    /// yet.
    ///
    /// # Returns
    ///
    /// One line for each such container or meta-command.
    fn unsupported(&self) -> Vec<String> {
        self.m.unsupported()
    }

    /// Lists the string and binary slots with no maximum size of their
    /// own.
    ///
    /// # Returns
    ///
    /// The slots.
    fn unbounded(&self) -> Vec<Slot> {
        self.m.unbounded().iter().map(|&s| py_slot(s)).collect()
    }

    /// Lists the algorithms whose functions the application supplies.
    ///
    /// # Returns
    ///
    /// Each one's name, the kind of function it needs, and whether one is
    /// set.
    fn algorithms(&self) -> Vec<(String, String, bool)> {
        self.m
            .algorithms()
            .into_iter()
            .map(|(n, k, s)| (n.to_string(), k.to_string(), s))
            .collect()
    }

    /// Supplies the function for one of the mission's algorithms. It is
    /// called with Python values, as `tcs_xtce.dynamic` describes; an
    /// exception it raises is raised from the call of the codec that
    /// called it.
    ///
    /// # Arguments
    ///
    /// * `name` - The algorithm's name, as `algorithms` lists it.
    /// * `f`    - The function.
    /// * `kind` - The kind of function, for an algorithm used in more than
    ///            one way; `None` to take the one way it is used.
    ///
    /// # Errors
    ///
    /// `LookupError`, if the mission has no such algorithm;
    /// `RuntimeError`, while a stream made from the mission exists.
    #[pyo3(signature = (name, f, kind=None))]
    fn set_algorithm(
        &mut self,
        name: &str,
        f: Py<PyAny>,
        kind: Option<&str>,
    ) -> PyResult<()> {
        if self.streams.get() > 0 {
            return Err(pyo3::exceptions::PyRuntimeError::new_err(
                "the mission's streams must be gone before its algorithms \
                 are set",
            ));
        }
        let kinds: Vec<String> = self
            .m
            .algorithms()
            .into_iter()
            .filter(|a| a.0 == name)
            .map(|a| a.1.to_string())
            .collect();
        let kind = match (kind, kinds.as_slice()) {
            (Some(k), _) => k.to_string(),
            (None, [k]) => k.clone(),
            (None, []) => {
                return Err(PyLookupError::new_err(format!(
                    "no algorithm {name}"
                )))
            }
            (None, _) => {
                return Err(PyValueError::new_err(format!(
                    "{name} is used as {}: give the kind",
                    kinds.join(" and ")
                )))
            }
        };
        let a = algorithm(&kind, f)?;
        self.m.set_algorithm(name, a).map_err(lookup_error)
    }

    /// Makes a store for the mission's slots, none with a value.
    ///
    /// # Arguments
    ///
    /// * `slf` - The mission.
    ///
    /// # Returns
    ///
    /// The store.
    fn store(slf: Bound<'_, Self>) -> Store {
        let s = slf.borrow().m.store();
        Store {
            s,
            out: tcs_xtce::buffer::Buffer::with_capacity(4096),
            mission: slf.unbind(),
        }
    }

    /// Makes a scratch to unpack packets into.
    ///
    /// # Returns
    ///
    /// The scratch.
    fn scratch(&self) -> Scratch {
        Scratch {
            s: self.m.scratch(),
        }
    }

    /// Makes an alarm monitor with no samples.
    ///
    /// # Returns
    ///
    /// The monitor.
    fn monitor(&self) -> Monitor {
        Monitor {
            m: self.m.monitor(),
        }
    }

    /// Unpacks a packet into a scratch; for an abstract node, the packet
    /// is classified as the first derived from it whose criteria match.
    ///
    /// # Arguments
    ///
    /// * `node`    - The container or meta-command.
    /// * `data`    - The packet.
    /// * `scratch` - Where its values go.
    ///
    /// # Returns
    ///
    /// The concrete node it is.
    ///
    /// # Errors
    ///
    /// `PacketError` or `UnsupportedError`, or what a supplied function
    /// raised.
    fn unpack(
        &self,
        node: &Node,
        data: &[u8],
        scratch: &mut Scratch,
    ) -> PyResult<Node> {
        let n = self.node(node)?;
        let got = self.m.unpack(n, data, &mut scratch.s).map_err(run_error)?;
        raise_kept()?;
        Ok(py_node(got))
    }

    /// Packs a packet of a concrete node from a store's current values.
    ///
    /// # Arguments
    ///
    /// * `py`    - The Python interpreter.
    /// * `node`  - The container or meta-command.
    /// * `store` - The current values.
    ///
    /// # Returns
    ///
    /// The packet.
    ///
    /// # Errors
    ///
    /// `PacketError` or `UnsupportedError`, or what a supplied function
    /// raised.
    fn pack<'py>(
        &self,
        py: Python<'py>,
        node: &Node,
        store: &mut Store,
    ) -> PyResult<Bound<'py, PyBytes>> {
        let n = self.node(node)?;
        let b = self
            .m
            .pack(n, &store.s, &mut store.out)
            .map_err(run_error)?;
        let b = PyBytes::new(py, b);
        raise_kept()?;
        Ok(b)
    }

    /// Gives the alarm level of each field of the packet last unpacked
    /// into a scratch, on that packet alone.
    ///
    /// # Arguments
    ///
    /// * `scratch` - What unpacking gave.
    ///
    /// # Returns
    ///
    /// Each field's name and level, such as `warning`, or `None` when the
    /// field is absent.
    ///
    /// # Errors
    ///
    /// What a supplied function raised.
    fn alarms(
        &self,
        scratch: &Scratch,
    ) -> PyResult<Vec<(String, Option<&'static str>)>> {
        let mut v = Vec::new();
        self.m
            .alarms(&scratch.s, |n, l| {
                v.push((n.to_string(), l.map(|l| l.name())))
            })
            .map_err(run_error)?;
        raise_kept()?;
        Ok(v)
    }

    /// Gives the alarm levels of the packet last unpacked into a scratch,
    /// counting the earlier samples a monitor keeps.
    ///
    /// # Arguments
    ///
    /// * `scratch` - What unpacking gave.
    /// * `monitor` - The alarm monitor.
    /// * `t`       - The packet's time, in seconds.
    ///
    /// # Returns
    ///
    /// As `alarms`.
    ///
    /// # Errors
    ///
    /// What a supplied function raised.
    fn monitor_alarms(
        &self,
        scratch: &Scratch,
        monitor: &mut Monitor,
        t: f64,
    ) -> PyResult<Vec<(String, Option<&'static str>)>> {
        let mut v = Vec::new();
        self.m
            .monitor_alarms(&scratch.s, &mut monitor.m, t, |n, l| {
                v.push((n.to_string(), l.map(|l| l.name())))
            })
            .map_err(run_error)?;
        raise_kept()?;
        Ok(v)
    }

    /// Gives the outputs of the MathAlgorithms and CustomAlgorithms the
    /// packet last unpacked into a scratch triggers.
    ///
    /// # Arguments
    ///
    /// * `scratch` - What unpacking gave.
    ///
    /// # Returns
    ///
    /// Each output's name and value, `None` when it has none.
    ///
    /// # Errors
    ///
    /// What a supplied function raised.
    fn derived(
        &self,
        scratch: &Scratch,
    ) -> PyResult<Vec<(String, Option<f64>)>> {
        let mut v = Vec::new();
        self.m
            .derived(&scratch.s, |n, x| v.push((n.to_string(), x)))
            .map_err(run_error)?;
        raise_kept()?;
        Ok(v)
    }

    /// Lists the mission's streams.
    ///
    /// # Returns
    ///
    /// Each one's qualified name.
    fn streams(&self) -> Vec<String> {
        self.m.streams()
    }

    /// Makes a stream's frame synchronizer, which keeps the mission alive.
    ///
    /// # Arguments
    ///
    /// * `slf`  - The mission.
    /// * `path` - The stream's qualified name.
    ///
    /// # Returns
    ///
    /// The stream.
    ///
    /// # Errors
    ///
    /// `LookupError`, if there is none or a function it needs isn't set.
    fn stream(slf: Bound<'_, Self>, path: &str) -> PyResult<Deframer> {
        let me = slf.borrow();
        let d = me.m.stream(path).map_err(lookup_error)?;
        // The deframer borrows the mission, which the Python object it is
        // in keeps where it is, and which set_algorithm leaves alone while
        // a stream exists; the deframer is dropped before the reference.
        let d: xd::Deframer<'static> = unsafe { std::mem::transmute(d) };
        me.streams.set(me.streams.get() + 1);
        drop(me);
        Ok(Deframer {
            d: Some(d),
            mission: slf.unbind(),
        })
    }

    /// Gives the container each frame of a stream is.
    ///
    /// # Arguments
    ///
    /// * `path` - The stream's qualified name.
    ///
    /// # Returns
    ///
    /// The container, or `None` for a CustomStream.
    ///
    /// # Errors
    ///
    /// `LookupError`, if there is no such stream.
    fn stream_container(&self, path: &str) -> PyResult<Option<Node>> {
        self.m
            .stream_container(path)
            .map(|n| n.map(py_node))
            .map_err(lookup_error)
    }
}

/// Gives a Python value as the datum a slot takes.
///
/// # Arguments
///
/// * `m`    - The mission.
/// * `slot` - The slot.
/// * `v`    - The value.
/// * `raw`  - Whether it is a raw value, which an integer or float
///            encoding takes.
/// * `wide` - Where a wide integer goes.
/// * `text` - Where a string goes.
/// * `data` - Where bytes go.
///
/// # Returns
///
/// The datum.
///
/// # Errors
///
/// `TypeError`, if the value is of no kind the slot takes.
fn datum<'a>(
    m: &xd::Mission,
    slot: xd::SlotId,
    v: &Bound<'_, PyAny>,
    raw: bool,
    wide: &'a mut Option<xd::WideInt>,
    text: &'a mut String,
    data: &'a mut Vec<u8>,
) -> PyResult<Datum<'a>> {
    let t = m.slot_type(slot);
    let bad = || PyTypeError::new_err(format!("a {} value", t.kind()));
    let int = |v: &Bound<'_, PyAny>| -> PyResult<Result<i128, Vec<u8>>> {
        if let Ok(i) = v.extract::<i128>() {
            return Ok(Ok(i));
        }
        let bits: usize = v.call_method0("bit_length")?.extract()?;
        let n = bits / 8 + 1;
        let b: Vec<u8> =
            v.call_method1("to_bytes", (n, "little", true))?.extract()?;
        Ok(Err(b))
    };
    if raw {
        if let Ok(x) = v.extract::<f64>() {
            if v.is_instance_of::<pyo3::types::PyFloat>() {
                return Ok(Datum::Float(x));
            }
        }
        return match int(v)? {
            Ok(i) => Ok(Datum::Int(i)),
            Err(_) => Err(bad()),
        };
    }
    match t.kind() {
        "bool" => v.extract::<bool>().map(Datum::Bool).map_err(|_| bad()),
        "string" => {
            *text = v.extract::<String>().map_err(|_| bad())?;
            Ok(Datum::Str(text))
        }
        "binary" => {
            *data = v.extract::<Vec<u8>>().map_err(|_| bad())?;
            Ok(Datum::Binary(data))
        }
        "enum" => {
            if let Ok(label) = v.extract::<String>() {
                return t
                    .labels()
                    .into_iter()
                    .find(|(l, _)| *l == label)
                    .map(|(_, x)| Datum::Enum(x as i128))
                    .ok_or_else(|| {
                        SetError::new_err(format!("no label {label}"))
                    });
            }
            match int(v)? {
                Ok(i) => Ok(Datum::Enum(i)),
                Err(_) => Err(bad()),
            }
        }
        "float" => {
            let x = v.extract::<f64>().map_err(|_| bad())?;
            Ok(if t.time().is_some() {
                Datum::Time(x)
            } else {
                Datum::Float(x)
            })
        }
        _ => match int(v)? {
            Ok(i) => Ok(Datum::Int(i)),
            Err(b) => {
                *wide = Some(xd::from_le_bytes(&b).ok_or_else(|| {
                    SetError::new_err("an integer too wide for the codec")
                })?);
                Ok(Datum::Big(wide.as_ref().unwrap()))
            }
        },
    }
}

#[pymethods]
impl Store {
    /// Gives a slot's current value.
    ///
    /// # Arguments
    ///
    /// * `py`   - The Python interpreter.
    /// * `slot` - The slot.
    ///
    /// # Returns
    ///
    /// The value, or `None` if nothing has set it.
    ///
    /// # Errors
    ///
    /// Fails if Python can't make the value.
    fn get(&self, py: Python<'_>, slot: &Slot) -> PyResult<Option<Py<PyAny>>> {
        let h = self.mission.borrow(py).slot(slot)?;
        match self.s.get(h) {
            Some(d) => to_py(py, d).map(Some),
            None => Ok(None),
        }
    }

    /// Gives a slot's raw value: before its calibrator.
    ///
    /// # Arguments
    ///
    /// * `py`   - The Python interpreter.
    /// * `slot` - The slot.
    ///
    /// # Returns
    ///
    /// The value, or `None` if nothing has set it.
    ///
    /// # Errors
    ///
    /// Fails if Python can't make the value.
    fn get_raw(
        &self,
        py: Python<'_>,
        slot: &Slot,
    ) -> PyResult<Option<Py<PyAny>>> {
        let h = self.mission.borrow(py).slot(slot)?;
        match self.s.get_raw(h) {
            Some(d) => to_py(py, d).map(Some),
            None => Ok(None),
        }
    }

    /// Gives a slot a value, checked against its type: an int, float,
    /// bool, str or bytes as the slot's kind takes, an enumeration's
    /// value or label, or a time's seconds.
    ///
    /// # Arguments
    ///
    /// * `slot`  - The slot.
    /// * `value` - The value.
    ///
    /// # Errors
    ///
    /// `TypeError`, for a value of another kind; `SetError`, for one the
    /// slot doesn't take.
    fn set(&mut self, slot: &Slot, value: &Bound<'_, PyAny>) -> PyResult<()> {
        let mission = self.mission.borrow(value.py());
        let h = mission.slot(slot)?;
        let (mut w, mut t, mut b) = (None, String::new(), Vec::new());
        let d = datum(&mission.m, h, value, false, &mut w, &mut t, &mut b)?;
        self.s
            .set(h, d)
            .map_err(|e| SetError::new_err(e.to_string()))
    }

    /// Gives a slot a raw value, which packing writes as it is.
    ///
    /// # Arguments
    ///
    /// * `slot`  - The slot.
    /// * `value` - The raw value: an int, or a float for a float
    ///             encoding.
    ///
    /// # Errors
    ///
    /// `TypeError` or `SetError`.
    fn set_raw(
        &mut self,
        slot: &Slot,
        value: &Bound<'_, PyAny>,
    ) -> PyResult<()> {
        let mission = self.mission.borrow(value.py());
        let h = mission.slot(slot)?;
        let (mut w, mut t, mut b) = (None, String::new(), Vec::new());
        let d = datum(&mission.m, h, value, true, &mut w, &mut t, &mut b)?;
        self.s
            .set_raw(h, d)
            .map_err(|e| SetError::new_err(e.to_string()))
    }

    /// Takes a slot's value away.
    ///
    /// # Arguments
    ///
    /// * `py`   - The Python interpreter.
    /// * `slot` - The slot.
    ///
    /// # Errors
    ///
    /// `ValueError`, for another mission's slot.
    fn clear(&mut self, py: Python<'_>, slot: &Slot) -> PyResult<()> {
        let h = self.mission.borrow(py).slot(slot)?;
        self.s.clear(h);
        Ok(())
    }

    /// Makes the values a scratch holds the current ones.
    ///
    /// # Arguments
    ///
    /// * `scratch` - What unpacking gave.
    ///
    /// # Errors
    ///
    /// `ValueError`, for another mission's scratch.
    fn commit(&mut self, scratch: &Scratch) -> PyResult<()> {
        self.s
            .commit(&scratch.s)
            .map_err(|e| PyValueError::new_err(e.to_string()))
    }
}

/// Makes the module `tcs_xtce._native.dynamic`.
///
/// # Arguments
///
/// * `py` - The Python interpreter.
///
/// # Returns
///
/// The module.
///
/// # Errors
///
/// Fails if a class can't be added.
pub fn module(py: Python<'_>) -> PyResult<Bound<'_, PyModule>> {
    let m = PyModule::new(py, "dynamic")?;
    m.add_class::<Mission>()?;
    m.add_class::<Store>()?;
    m.add_class::<Scratch>()?;
    m.add_class::<Monitor>()?;
    m.add_class::<Deframer>()?;
    m.add_class::<Space>()?;
    m.add_class::<Node>()?;
    m.add_class::<Slot>()?;
    m.add("PacketError", py.get_type::<PacketError>())?;
    m.add("UnsupportedError", py.get_type::<UnsupportedError>())?;
    m.add("SetError", py.get_type::<SetError>())?;
    Ok(m)
}
