"""Tests of xtcegen.pyalign, which lays out Python in the aligned style."""

import ast
import unittest

from xtcegen.pyalign import WIDTH, align


def widest(text: str) -> int:
    return max(len(line) for line in text.split("\n"))


class AlignTest(unittest.TestCase):
    def same_code(self, src: str) -> str:
        """Align ``src`` and check that it still means the same thing and
        fits."""
        out = align(src)
        self.assertEqual(ast.dump(ast.parse(src)), ast.dump(ast.parse(out)))
        self.assertLessEqual(widest(out), WIDTH)
        return out

    def test_continuation_lines_up_under_the_bracket(self):
        src = ("result = some_function(first_argument, second_argument,\n"
               "    third_argument, fourth_argument, fifth_argument)\n")
        self.assertEqual(self.same_code(src),
                         "result = some_function(first_argument, "
                         "second_argument, third_argument,\n"
                         "                       fourth_argument, "
                         "fifth_argument)\n")

    def test_short_statements_join_one_line(self):
        src = "x = f(\n    a,\n    b,\n)\n"
        self.assertEqual(self.same_code(src), "x = f(a, b)\n")

    def test_one_element_tuple_keeps_its_comma(self):
        src = "x = (\n    a,\n)\n"
        self.assertEqual(self.same_code(src), "x = (a,)\n")

    def test_staircase_hangs_instead(self):
        # Aligned, the inner call would break deeper than the outer one.
        src = ("        v11 = pk.polynomial(pk.int_to_float(r.uint_order(12, "
               "False,\n            \"BatteryVoltage\"), 12, \"unsigned\"), "
               "((-0.5, 0), (0.01, 1)))\n")
        out = self.same_code("if x:\n    if y:\n" + src)
        self.assertIn("pk.polynomial(\n", out)

    def test_comments_hang_and_stay_with_their_items(self):
        src = ('ITEMS = [\n    # The first.\n    "a",\n    "b",  # second\n'
               "]\n")
        out = self.same_code(src)
        self.assertIn("    # The first.\n", out)
        self.assertIn('"b"  # second\n', out)

    def test_comment_on_the_opening_line_stays_there(self):
        src = ("from package.module import (alpha, beta,  # noqa: E402\n"
               "    gamma)\n")
        out = self.same_code(src)
        self.assertTrue(out.startswith("from package.module import (  # noqa: "
                                       "E402\n"), out)

    def test_long_value_is_parenthesized(self):
        src = ("total = first_long_name + second_long_name + third_long_name + "
               "\\\n    fourth_long_name + fifth_long_name\n")
        self.assertIn("total = (first_long_name", self.same_code(src))

    def test_long_strings_split_and_keep_their_value(self):
        words = "word " * 30
        src = f"x = f(\n    {words!r},\n)\n"
        out = self.same_code(src)
        self.assertGreater(out.count("\n"), 1)

    def test_layout_is_stable(self):
        src = ("value = call(argument_one, argument_two, argument_three, "
               "argument_four,\n    argument_five)\n")
        once = align(src)
        self.assertEqual(align(once), once)

    def test_code_that_does_not_parse_is_left_alone(self):
        src = "    indented fragment(\n"
        self.assertEqual(align(src), src)


if __name__ == "__main__":
    unittest.main()
