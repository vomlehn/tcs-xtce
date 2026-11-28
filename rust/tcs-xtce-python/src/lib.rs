//! `tcs_xtce._native`: the tcs-xtce crate's XML binding, for Python.
//!
//! The tcs-xtce Python distribution reads and writes XTCE with this
//! module. The Rust binding parses and checks the XML and writes it; the
//! conversions in `generated` turn what it reads into objects of the
//! Python binding's classes, `tcs_xtce.generated`, and those objects back
//! into Rust values to write. Python code sees the classes it always has.

use pyo3::exceptions::PyKeyError;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyBytes, PyList};
use pyo3::IntoPyObjectExt;

mod dynamic;
mod generated;

/// Builds the Python object for a Rust value of the XML binding.
pub(crate) trait ToPy {
    /// Converts the value to its Python form.
    ///
    /// # Arguments
    ///
    /// * `py` - The Python interpreter.
    ///
    /// # Returns
    ///
    /// The Python object: an object of the Python binding's class, an
    /// enumeration's member, a list, `None` or a scalar.
    ///
    /// # Errors
    ///
    /// Fails if Python can't make the object.
    fn to_py<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>>;
}

/// Builds a Rust value of the XML binding from its Python object.
pub(crate) trait FromPy: Sized {
    /// Converts a Python object to the Rust value.
    ///
    /// # Arguments
    ///
    /// * `o` - The object, of the type the Python binding gives the value.
    ///
    /// # Returns
    ///
    /// The value.
    ///
    /// # Errors
    ///
    /// Fails if the object, or one of its fields, is not of its type.
    fn from_py(o: &Bound<'_, PyAny>) -> PyResult<Self>;
}

/// Implements both traits for scalars, which PyO3 converts itself.
macro_rules! scalar {
    ($($t:ty),*) => {$(
        impl ToPy for $t {
            fn to_py<'py>(&self, py: Python<'py>)
                -> PyResult<Bound<'py, PyAny>> {
                self.clone().into_bound_py_any(py)
            }
        }

        impl FromPy for $t {
            fn from_py(o: &Bound<'_, PyAny>) -> PyResult<Self> {
                o.extract()
            }
        }
    )*};
}

scalar!(String, bool, i32, i64, u16, f64);

impl<T: ToPy> ToPy for Option<T> {
    fn to_py<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        match self {
            Some(v) => v.to_py(py),
            None => Ok(py.None().into_bound(py)),
        }
    }
}

impl<T: FromPy> FromPy for Option<T> {
    fn from_py(o: &Bound<'_, PyAny>) -> PyResult<Self> {
        if o.is_none() {
            Ok(None)
        } else {
            T::from_py(o).map(Some)
        }
    }
}

impl<T: ToPy> ToPy for Vec<T> {
    fn to_py<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let items = self
            .iter()
            .map(|v| v.to_py(py))
            .collect::<PyResult<Vec<_>>>()?;
        Ok(PyList::new(py, items)?.into_any())
    }
}

impl<T: FromPy> FromPy for Vec<T> {
    fn from_py(o: &Bound<'_, PyAny>) -> PyResult<Self> {
        o.try_iter()?.map(|v| T::from_py(&v?)).collect()
    }
}

impl<T: ToPy> ToPy for Box<T> {
    fn to_py<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        (**self).to_py(py)
    }
}

impl<T: FromPy> FromPy for Box<T> {
    fn from_py(o: &Bound<'_, PyAny>) -> PyResult<Self> {
        T::from_py(o).map(Box::new)
    }
}

/// Gives a class of the Python binding, finding it the first time.
///
/// # Arguments
///
/// * `py`   - The Python interpreter.
/// * `cell` - Where the class is kept once found.
/// * `name` - Its name in `tcs_xtce.generated`.
///
/// # Returns
///
/// The class.
///
/// # Errors
///
/// Fails if `tcs_xtce.generated` can't be imported or has no such name.
pub(crate) fn class<'py>(
    py: Python<'py>,
    cell: &'static PyOnceLock<Py<PyAny>>,
    name: &str,
) -> PyResult<Bound<'py, PyAny>> {
    let c = cell.get_or_try_init(py, || -> PyResult<Py<PyAny>> {
        Ok(py.import("tcs_xtce.generated")?.getattr(name)?.unbind())
    })?;
    Ok(c.bind(py).clone())
}

/// Makes the Python error for bad XTCE: tcsxtce's `XtceError`.
///
/// # Arguments
///
/// * `message` - What is wrong.
/// * `line`    - The line of the XML it is on, or 0 if none.
///
/// # Returns
///
/// The error, or the one that importing `tcsxtce` raised.
fn xtce_error(message: &str, line: usize) -> PyErr {
    Python::attach(|py| {
        match py
            .import("tcsxtce")
            .and_then(|m| m.getattr("XtceError"))
            .and_then(|c| c.call1((message, line)))
        {
            Ok(e) => PyErr::from_value(e),
            Err(e) => e,
        }
    })
}

/// Converts an error of the Rust binding to Python's.
///
/// # Arguments
///
/// * `e` - The error, with its message and line.
///
/// # Returns
///
/// The `XtceError` saying the same.
pub(crate) fn error(e: tcs_xtce::Error) -> PyErr {
    xtce_error(&e.message, e.line)
}

/// Makes the `XtceError` for a value that isn't in the XML binding's
/// lexical space, with no line.
///
/// # Arguments
///
/// * `message` - What is wrong.
///
/// # Returns
///
/// The error.
pub(crate) fn xml_error(message: String) -> PyErr {
    xtce_error(&message, 0)
}

/// Reads the element `tag` of `data` as a record of the class `cls`.
///
/// # Arguments
///
/// * `py`   - The Python interpreter.
/// * `data` - The XML of a whole document.
/// * `cls`  - The name of the record's class in `tcs_xtce.generated`.
/// * `tag`  - The name the root element must have.
///
/// # Returns
///
/// The record, an object of that class.
///
/// # Errors
///
/// Raises `XtceError` if the XML is bad or its root isn't `tag`, and
/// `KeyError` if no record's class is named `cls`.
#[pyfunction]
fn from_xml<'py>(
    py: Python<'py>,
    data: &[u8],
    cls: &str,
    tag: &str,
) -> PyResult<Bound<'py, PyAny>> {
    generated::read(py, data, cls, tag)?
        .ok_or_else(|| PyKeyError::new_err(cls.to_string()))
}

/// Writes a record as the element `tag`, as a whole document.
///
/// # Arguments
///
/// * `py`     - The Python interpreter.
/// * `value`  - The record, an object of a class of `tcs_xtce.generated`.
/// * `cls`    - The name of its class.
/// * `tag`    - The name of its element.
/// * `indent` - Whether to indent the XML.
///
/// # Returns
///
/// The XML, as UTF-8.
///
/// # Errors
///
/// Raises `XtceError` if the record can't be written as XML, `KeyError`
/// if no record's class is named `cls`, and the error of the conversion
/// if a field is not of its type.
#[pyfunction]
fn to_xml<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
    cls: &str,
    tag: &str,
    indent: bool,
) -> PyResult<Bound<'py, PyBytes>> {
    let mut buf = tcs_xtce::Buffer::new();
    if !generated::write(value, cls, tag, indent, &mut buf)? {
        return Err(PyKeyError::new_err(cls.to_string()));
    }
    Ok(PyBytes::new(py, buf.as_bytes()))
}

/// The module `tcs_xtce._native`.
///
/// # Arguments
///
/// * `m` - The module, to add the functions to.
///
/// # Errors
///
/// Fails if a function can't be added.
#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(from_xml, m)?)?;
    m.add_function(wrap_pyfunction!(to_xml, m)?)?;
    m.add_submodule(&dynamic::module(m.py())?)?;
    Ok(())
}
