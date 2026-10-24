"""Tests of xtcegen.wrap, which keeps generated code within 80 columns."""

import ast
import re
import shutil
import subprocess
import unittest

from xtcegen.wrap import WIDTH, split_rust_strings, wrap


def widest(text: str) -> int:
    return max(len(line) for line in text.split("\n"))


class PythonTest(unittest.TestCase):
    def same_code(self, src: str) -> str:
        """Wrap ``src`` and check that it still means the same thing."""
        out = wrap(src, "python")
        self.assertEqual(ast.dump(ast.parse(src)), ast.dump(ast.parse(out)))
        return out

    def test_short_lines_unchanged(self):
        src = "x = f(a, b)\n"
        self.assertEqual(wrap(src, "python"), src)

    def test_call_breaks_inside_brackets(self):
        args = ", ".join(f"argument_{i}" for i in range(12))
        out = self.same_code(f"def f():\n    return g({args})\n")
        self.assertLessEqual(widest(out), WIDTH)

    def test_conditional_expression_is_parenthesized(self):
        # Nothing here is in brackets, so the value must be put in some.
        src = (
            "def f():\n"
            "    value = first_alternative_with_a_long_name"
            " if some_condition_that_is_long else second_alternative\n"
        )
        out = self.same_code(src)
        self.assertLessEqual(widest(out), WIDTH)
        self.assertIn("value = (", out)

    def test_def_breaks_before_return_annotation(self):
        src = (
            "class C:\n"
            "    def dynamic_value(cls, value: ArgumentDynamicValueType)"
            " -> ArgumentLocationInContainerInBitsChoice:\n"
            "        pass\n"
        )
        out = self.same_code(src)
        self.assertLessEqual(widest(out), WIDTH)

    def test_long_strings_split_and_keep_their_value(self):
        text = "word " * 30
        src = f"x = f({text!r}, f'{{y}} {text}')\n"
        out = self.same_code(src.replace("x = f(", "x = g("))
        self.assertLessEqual(widest(out), WIDTH)
        y = 1
        self.assertEqual(
            eval(src.split(" = ", 1)[1].replace("f(", "(", 1)),
            eval(out.split(" = ", 1)[1].replace("g(", "(", 1)),
        )

    def test_trailing_comment_moves_above(self):
        src = (
            "    range_form: Optional[RangeFormType] = None"
            "  # attribute 'rangeForm', schema default 'outside'\n"
        )
        out = wrap(src, "python")
        self.assertLessEqual(widest(out), WIDTH)
        self.assertTrue(out.lstrip().startswith("# attribute"))

    def test_docstring_rewrapped(self):
        words = " ".join(["Identical to the other type but different."] * 4)
        out = wrap(f'def f():\n    """{words}"""\n', "python")
        self.assertLessEqual(widest(out), WIDTH)
        # Only the whitespace in the docstring changes.
        doc = ast.get_docstring(ast.parse(out).body[0])
        self.assertEqual(" ".join(doc.split()), words)


class CTest(unittest.TestCase):
    def test_if_statement_breaks_after_condition(self):
        src = (
            '        if (r.peek_child() == "AncillaryDataSet") '
            "v.ancillary_data_set = r.read_child<AncillaryDataSetType>();"
        )
        out = wrap(src, "c")
        self.assertLessEqual(widest(out), WIDTH)
        self.assertEqual(out.split("\n")[0].strip()[-1], ")")

    def test_string_split_into_adjacent_literals(self):
        src = (
            "        check_count(v.parameter_to_suspend_alarms_on.size(), 1, "
            '-1, "ParametersToSuspendAlarmsOnSetType.ParameterToSuspend'
            'AlarmsOn");'
        )
        out = wrap(src, "c")
        self.assertLessEqual(widest(out), WIDTH)
        literals = [s for s in out.split('"')[1::2]]
        self.assertEqual(
            "".join(literals),
            "ParametersToSuspendAlarmsOnSetType.ParameterToSuspendAlarmsOn",
        )

    def test_trailing_block_comment_moves_above(self):
        src = (
            "        demo_PacketTypeType packet_type; "
            "/* /Demo/PacketType (/Demo/PacketTypeType) and more words */"
        )
        out = wrap(src, "c").split("\n")
        self.assertTrue(out[0].lstrip().startswith("/*"))
        self.assertEqual(out[-1].strip(), "demo_PacketTypeType packet_type;")

    def test_block_comment_rewrapped(self):
        words = " ".join(["Packet length: computed when encoding."] * 4)
        out = wrap(f"/* {words} */", "c")
        self.assertLessEqual(widest(out), WIDTH)
        self.assertTrue(out.endswith("*/"))

    def test_preprocessor_lines_left_alone(self):
        src = "#define X " + "y " * 50
        self.assertEqual(wrap(src, "c"), src)


class RustTest(unittest.TestCase):
    def test_doc_comment_rewrapped(self):
        words = " ".join(["Reference to the parameter."] * 6)
        out = wrap(f"    /// {words}", "rust")
        self.assertLessEqual(widest(out), WIDTH)
        self.assertTrue(
            all(line.startswith("    /// ") for line in out.split("\n"))
        )

    def test_split_strings_keep_their_value(self):
        text = "BinaryDataEncoding|FloatDataEncoding|IntegerDataEncoding|Str"
        src = f'fn f() -> &\'static str {{ "{text}" }}\n'
        out = split_rust_strings(src)
        self.assertIn("\\\n", out)
        # Rust drops a backslash at the end of a line inside a string, with
        # the newline and the whitespace that follows.
        self.assertIn(text, re.sub(r"\\\n\s*", "", out))

    @unittest.skipIf(shutil.which("rustfmt") is None, "needs rustfmt")
    def test_rustfmt_keeps_split_strings(self):
        text = "BinaryDataEncoding|FloatDataEncoding|IntegerDataEncoding|Str"
        src = (
            "fn f(r: &mut R) -> Result<(), E> {\n"
            f'    match r.peek() {{ _ => return Err(r.expected("{text}")), }}\n'
            "}\n"
        )
        from xtcegen.__main__ import rustfmt

        out = rustfmt(src)
        self.assertLessEqual(widest(out), WIDTH)
        res = subprocess.run(
            [
                "rustfmt",
                "--edition",
                "2021",
                "--config",
                "max_width=80",
                "--check",
            ],
            input=out,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, res.stdout)


if __name__ == "__main__":
    unittest.main()
