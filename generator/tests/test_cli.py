"""Tests of the tcs-xtce command line."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from tcs_xtce.__main__ import main

DEMO = str(Path(__file__).resolve().parents[2] / "tests" / "mission"
           / "demo.xml")


def generate(*args: str) -> str:
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


if __name__ == "__main__":
    unittest.main()
