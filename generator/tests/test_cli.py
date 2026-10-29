"""Tests of the tcs-xtce command line."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from tcs_xtce.__main__ import main

from .test_compile import APP, HEADER, TYPES

DEMO = str(Path(__file__).resolve().parents[2] / "tests" / "mission"
           / "demo.xml")


def generate(*args: str) -> str:
    """Runs tcs-xtce on the demo mission and reads what it wrote.

    Args:
        *args: More command-line arguments, after the input and output.

    Returns:
        The generated code.

    Raises:
        AssertionError: If tcs-xtce exits with a non-zero status.
    """
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "out"
        if main([DEMO, str(out), *args]) != 0:
            raise AssertionError("tcs-xtce failed")
        return out.read_text()


class DynamicAllocationTests(unittest.TestCase):
    def test_rust_without_dynamic_allocation(self):
        text = generate("--lang", "rust", "--dynamic-allocations", "false")
        for t in ("pk::FixedVec<", "pk::FixedString<", "pk::FixedBytes<",
                  "read_fixed_string::<"):
            self.assertIn(t, text)
        # Nothing that allocates: the heap-backed types and text building.
        for t in (r"\bVec\b", r"\bString\b", r"\bto_string\b", r"\bformat!",
                  r"\bto_vec\b", r"\bBox\b"):
            self.assertNotRegex(text, t)

    def test_rust_defaults_to_dynamic_allocation(self):
        text = generate("--lang", "rust")
        self.assertEqual(
            text, generate("--lang", "rust", "--dynamic-allocations", "true"))
        self.assertIn("Vec<", text)
        self.assertNotIn("pk::Fixed", text)

    def test_c_never_allocates(self):
        self.assertEqual(generate("--lang", "c"),
                         generate("--lang", "c", "--dynamic-allocations",
                                  "false"))

    def test_python_needs_dynamic_allocation(self):
        with (contextlib.redirect_stderr(io.StringIO()) as err,
              self.assertRaises(SystemExit)):
            generate("--lang", "python", "--dynamic-allocations", "false")
        self.assertIn("not available for --lang python", err.getvalue())

    def test_values_are_true_or_false(self):
        with (contextlib.redirect_stderr(io.StringIO()),
              self.assertRaises(SystemExit)):
            generate("--lang", "rust", "--dynamic-allocations", "no")


class InputTests(unittest.TestCase):
    """Several input documents, and where the output goes."""

    def run_main(self, *args: str):
        """Runs tcs-xtce's main, capturing its standard output and error.

        Args:
            *args: The command-line arguments.

        Returns:
            The exit status, the standard output and the standard error.
        """
        out, err = io.StringIO(), io.StringIO()
        with (contextlib.redirect_stdout(out), contextlib.redirect_stderr(err)):
            status = main(list(args))
        return status, out.getvalue(), err.getvalue()

    def test_several_documents(self):
        with tempfile.TemporaryDirectory() as d:
            names = []
            for name, text in (("dt", TYPES), ("hdr", HEADER), ("app", APP)):
                names.append(str(Path(d) / f"{name}.xml"))
                Path(names[-1]).write_text(text)
            status, out, err = self.run_main(*names, "-", "--lang", "rust")
            self.assertEqual((status, err), (0, ""))
            self.assertTrue(out.startswith("// @generated automatically by "
                                           "tcs-xtce from dt.xml, hdr.xml, "
                                           "app.xml\n// (SpaceSystems 'dt', "
                                           "'hdr', 'app').\n"), out)
            self.assertIn("change the XTCE documents instead", out)
            self.assertIn("pub struct Status", out)
            self.assertIn("pub struct Ping", out)
            # Without the document of types, references fail, and the error
            # names no one file.
            status, _, err = self.run_main(*names[1:], "-", "--lang", "rust")
            self.assertEqual(status, 1)
            self.assertTrue(err.startswith("tcs-xtce: unsupported"), err)

    def test_output_is_not_a_document(self):
        # Forgetting OUTPUT must not overwrite the last document.
        with self.assertRaises(SystemExit):
            self.run_main(DEMO, DEMO, "--lang", "rust")

    def test_size_report_output(self):
        status, out, _ = self.run_main(DEMO, "--size-report")
        self.assertEqual(status, 0)
        self.assertTrue(out.startswith("Size report for SpaceSystem 'Demo'"),
                        out[:80])
        with tempfile.TemporaryDirectory() as d:
            report = Path(d) / "report.txt"
            self.assertEqual(self.run_main(DEMO, str(report),
                                           "--size-report")[:2], (0, ""))
            self.assertEqual(report.read_text(), out)


if __name__ == "__main__":
    unittest.main()
