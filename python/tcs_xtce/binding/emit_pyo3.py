"""Emit the Rust that converts the XML binding between Rust and Python.

The Python binding's classes (``tcs_xtce.generated``) and the Rust
binding's types (``tcs_xtce::generated``) are written from the same model,
so each Python class has a Rust type with the same name and fields.  This
module writes, for every record, choice and enumeration, an implementation
of the ``ToPy`` and ``FromPy`` traits of the ``tcs-xtce-python`` crate,
which build a Python object from a Rust value and a Rust value from a
Python object, and the two functions that read and write any of them as
XML by its class's name.  The Rust binding does the reading and writing;
the Python objects are the classes Python code has always used.
"""

from __future__ import annotations

from . import docs, header_lines
from .binding import MANY, OPT, Binding, ChoiceType, ElemField, Record
from .emit_python import ident as py_ident
from .emit_rust import ident as rust_ident
from .emit_rust import lit


def cell(name: str) -> str:
    """Gives the static that caches a Python class.

    Args:
        name: The class's name.

    Returns:
        The static's Rust name.
    """
    return f"CLASS_{name.upper()}"


class Pyo3Emitter:
    """Writes the conversions for a binding.

    Attributes:
        b:   The binding.
        out: The module's lines so far.
    """

    def __init__(self, b: Binding):
        """Starts a module for a binding.

        Args:
            b: The binding to write the conversions of.
        """
        self.b = b
        self.out: list[str] = []

    def w(self, line: str = "") -> None:
        """Appends one line to the module.

        Args:
            line: The line, without its newline.
        """
        self.out.append(line)

    def emit(self) -> str:
        """Writes the whole module.

        Returns:
            The module's text, before rustfmt lays it out.
        """
        for line in header_lines():
            self.w("// " + line)
        self.w("")
        self.w("//! Conversions of every type of the XTCE binding between Rust "
               "and Python.")
        self.w("")
        self.w("#![allow(non_snake_case, clippy::all)]")
        self.w("")
        self.w("use pyo3::prelude::*;")
        self.w("use pyo3::sync::PyOnceLock;")
        self.w("use pyo3::types::PyTuple;")
        self.w("use tcs_xtce::generated as g;")
        self.w("use tcs_xtce::Value;")
        self.w("")
        self.w("use crate::{class, xml_error, FromPy, ToPy};")
        self.w("")
        names = (sorted(self.b.schema.enums) + sorted(self.b.records)
                 + sorted(self.b.choices))
        for name in names:
            self.w(f"static {cell(name)}: PyOnceLock<Py<PyAny>> = "
                   "PyOnceLock::new();")
        self.w("")
        for name in sorted(self.b.schema.enums):
            self.enum(name)
        for name in sorted(self.b.records):
            self.record(self.b.records[name])
        for name in sorted(self.b.choices):
            self.choice(self.b.choices[name])
        self.dispatch()
        return "\n".join(self.out) + "\n"

    def enum(self, name: str) -> None:
        """Writes the conversions of an enumeration: to its Python member,
        by its XML value, and back, by parsing the member's value.

        Args:
            name: The enumeration's name.
        """
        self.w(f"impl ToPy for g::{name} {{")
        self.w("    fn to_py<'py>(&self, py: Python<'py>) -> PyResult<Bound"
               "<'py, PyAny>> {")
        self.w(f"        class(py, &{cell(name)}, "
               f"{lit(name)})?.call1((self.as_str(),))")
        self.w("    }")
        self.w("}")
        self.w("")
        self.w(f"impl FromPy for g::{name} {{")
        self.w("    fn from_py(o: &Bound<'_, PyAny>) -> PyResult<Self> {")
        self.w("        let s: String = o.extract()?;")
        self.w(f"        g::{name}::parse_xml(&s).map_err(xml_error)")
        self.w("    }")
        self.w("}")
        self.w("")

    def fields(self, r: Record) -> list[tuple[str, str]]:
        """Lists a record's fields, in the order its Python class declares
        them, which is its constructor's.

        Args:
            r: The record.

        Returns:
            Each field's Python attribute and Rust field names.
        """
        out = [(py_ident(a.ident), rust_ident(a.ident)) for a in r.attrs]
        if r.text is not None:
            out.append((py_ident(r.text.ident), rust_ident(r.text.ident)))
        out += [(py_ident(f.ident), rust_ident(f.ident)) for f in r.content]
        return out

    def record(self, r: Record) -> None:
        """Writes the conversions of a record: to its Python class, called
        with each field in order, and back, field by field.

        Args:
            r: The record.
        """
        fields = self.fields(r)
        self.w(f"impl ToPy for g::{r.name} {{")
        self.w("    fn to_py<'py>(&self, py: Python<'py>) -> PyResult<Bound"
               "<'py, PyAny>> {")
        self.w("        let args = PyTuple::new(py, [")
        for _, rf in fields:
            self.w(f"            self.{rf}.to_py(py)?,")
        self.w(f"        ] as [Bound<'py, PyAny>; {len(fields)}])?;")
        self.w(f"        class(py, &{cell(r.name)}, "
               f"{lit(r.name)})?.call1(args)")
        self.w("    }")
        self.w("}")
        self.w("")
        self.w(f"impl FromPy for g::{r.name} {{")
        self.w("    fn from_py(o: &Bound<'_, PyAny>) -> PyResult<Self> {")
        if not fields:
            self.w("        let _ = o;")
            self.w("        Ok(Self {})")
        else:
            self.w("        let py = o.py();")
            self.w("        Ok(Self {")
            for pf, rf in fields:
                self.w(f"            {rf}: "
                       "FromPy::from_py(&o.getattr(pyo3::intern!(py, "
                       f"{lit(pf)}))?)?,")
            self.w("        })")
        self.w("    }")
        self.w("}")
        self.w("")

    def choice(self, c: ChoiceType) -> None:
        """Writes the conversions of a choice: to its Python class, called
        with the alternative's element name and value, and back, by the
        name.

        Args:
            c: The choice.
        """
        self.w(f"impl ToPy for g::{c.name} {{")
        self.w("    fn to_py<'py>(&self, py: Python<'py>) -> PyResult<Bound"
               "<'py, PyAny>> {")
        self.w("        let (name, value) = match self {")
        for v in c.variants:
            self.w(f"            g::{c.name}::{v.ident}(x) => ({lit(v.xml)}, "
                   "x.to_py(py)?),")
        self.w("        };")
        self.w(f"        class(py, &{cell(c.name)}, "
               f"{lit(c.name)})?.call1((name, value))")
        self.w("    }")
        self.w("}")
        self.w("")
        self.w(f"impl FromPy for g::{c.name} {{")
        self.w("    fn from_py(o: &Bound<'_, PyAny>) -> PyResult<Self> {")
        self.w("        let py = o.py();")
        self.w("        let name: String = o.getattr(pyo3::intern!(py, "
               "\"name\"))?.extract()?;")
        self.w("        let value = o.getattr(pyo3::intern!(py, \"value\"))?;")
        self.w("        Ok(match name.as_str() {")
        for v in c.variants:
            self.w(f"            {lit(v.xml)} => "
                   f"g::{c.name}::{v.ident}(FromPy::from_py(&value)?),")
        self.w("            _ => {")
        self.w("                return Err(xml_error(format!(")
        self.w("                    \"{name:?} is not an alternative of "
               f"{c.name}\"")
        self.w("                )))")
        self.w("            }")
        self.w("        })")
        self.w("    }")
        self.w("}")
        self.w("")

    def dispatch(self) -> None:
        """Writes the functions that read and write a record as XML, given
        its class's name."""
        recs = sorted(self.b.records)
        self.out += docs.rust("Reads the element `tag` of `data` as the record "
                              "of the class named `cls`.",
                              [("py", "The Python interpreter."),
                               ("data", "The XML of a whole document."),
                               ("cls", "The name of the record's class."),
                               ("tag", "The name the root element must have.")],
                              "The record, as an object of its Python class, "
                              "or `None` if no record is named `cls`.",
                              errors="Fails if the XML is bad or its root "
                                     "isn't `tag`.")
        self.w("pub(crate) fn read<'py>(py: Python<'py>, data: &[u8], cls: "
               "&str, tag: &str) -> PyResult<Option<Bound<'py, PyAny>>> {")
        self.w("    Ok(Some(match cls {")
        for name in recs:
            self.w(f"        {lit(name)} => tcs_xtce::from_xml::"
                   f"<g::{name}>(data, tag).map_err(crate::error)?.to_py(py)?,")
        self.w("        _ => return Ok(None),")
        self.w("    }))")
        self.w("}")
        self.w("")
        self.out += docs.rust(
            "Writes a record, given its class's name, as the element `tag`.",
            [("value", "The record, an object of its Python class."),
             ("cls", "The name of the record's class."),
             ("tag", "The name of its element."),
             ("indent", "Whether to indent the XML."),
             ("buf", "Receives the XML, replacing what it held.")],
            "`false` if no record is named `cls`, writing nothing; otherwise "
            "`true`.",
            errors="Fails if the object's fields are not of their types, or it "
                   "can't be written as XML.")
        self.w("pub(crate) fn write(value: &Bound<'_, PyAny>, cls: &str, tag: "
               "&str, indent: bool, buf: &mut tcs_xtce::Buffer) -> PyResult"
               "<bool> {")
        self.w("    match cls {")
        for name in recs:
            self.w(f"        {lit(name)} => "
                   f"tcs_xtce::to_xml(&g::{name}::from_py(value)?, tag, buf, "
                   "indent).map_err(crate::error)?,")
        self.w("        _ => return Ok(false),")
        self.w("    }")
        self.w("    Ok(true)")
        self.w("}")


def emit(b: Binding) -> str:
    """Writes the conversions for a binding.

    Args:
        b: The binding.

    Returns:
        The Rust module's text, before rustfmt lays it out.
    """
    return Pyo3Emitter(b).emit()
