"""Tests of the model's canonical text (tcs_xtce.compiler.dump)."""

import os
import subprocess
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path

import tcs_xtce
from tcs_xtce.compiler.compile import Compiler, compile_documents
from tcs_xtce.compiler.dump import (Dumper, dump, dump_containers, dump_types,
                                    string)

TOP = Path(__file__).resolve().parents[3]

# Every mission the tests and examples compile, each a list of its
# documents in the order they are given to the compiler.
MISSIONS = [
    ["tests/mission/demo.xtce"], ["examples/enum-labels/modes.xtce"],
    ["examples/housekeeping/housekeeping.xtce"],
    ["examples/simple-string/simple_string.xtce"],
    ["examples/split-mission/pus.xtce", "examples/split-mission/dt.xtce",
     "examples/split-mission/rover.xtce"], ["examples/to-from/to_from.xtce"],
    ["examples/variable-array/variable_array.xtce"]]


def model_text(names: list) -> str:
    """Compiles a mission and writes its model.

    Args:
        names: Its documents, relative to the top of the repository.

    Returns:
        The model's canonical text.
    """
    roots = [tcs_xtce.Document.from_file(str(TOP / n)).root for n in names]
    return dump(compile_documents(roots))


@dataclass(eq=False)
class Leaf:
    """A record for the tests to share between others.

    Attributes:
        n: A number, to tell leaves apart.
    """

    n: int


@dataclass
class Branch:
    """A record holding the values the tests write.

    Attributes:
        left:  Any value.
        right: Any value.
        more:  A dictionary, empty unless a test fills it.
    """

    left: object
    right: object
    more: dict = field(default_factory=dict)


@dataclass(eq=False)
class Knot:
    """A record that compares by identity, which may hold itself.

    Attributes:
        next: Any value.
    """

    next: object


def text(v) -> str:
    """Writes any value as the dump does.

    Args:
        v: The value.

    Returns:
        Its canonical text, a line to an element.
    """
    d = Dumper()
    d.count(v)
    d.put(0, "", v)
    return "\n".join(d.lines)


class Scalars(unittest.TestCase):
    def test_strings_are_quoted_and_escaped(self):
        self.assertEqual(string('a"b\\c'), '"a\\"b\\\\c"')
        self.assertEqual(string("\n\r\t\x01\x7f"), '"\\n\\r\\t\\x01\\x7f"')
        self.assertEqual(string("é"), '"é"')

    def test_numbers_and_constants(self):
        self.assertEqual(text([None, True, False, 0, -12, 2**70]),
                         "[\n  none\n  true\n  false\n  0\n  -12\n"
                         f"  {2**70}\n]")
        self.assertEqual(text([1.0, 1e16, 1e-5, float("inf"), float("nan")]),
                         "[\n  1.0\n  1e+16\n  1e-05\n  inf\n  nan\n]")

    def test_empty_containers(self):
        self.assertEqual(text(Branch([], ())),
                         "Branch {\n  left: []\n  right: []\n  more: {}\n}")


class Records(unittest.TestCase):
    def test_fields_in_declared_order(self):
        self.assertEqual(text(Branch(1, "x")),
                         'Branch {\n  left: 1\n  right: "x"\n  more: {}\n}')

    def test_shared_records_are_numbered(self):
        a, b = Leaf(1), Leaf(2)
        out = text(Branch([a, b], a, {a: b}))
        self.assertEqual(out,
                         "\n".join(["Branch {", "  left: [", "    Leaf #1 {",
                                    "      n: 1", "    }", "    Leaf #2 {",
                                    "      n: 2", "    }", "  ]",
                                    "  right: Leaf #1", "  more: {",
                                    "    Leaf #1 => Leaf #2", "  }", "}"]))

    def test_shared_values_are_written_in_full(self):
        v = Branch(1, 2)
        self.assertEqual(text([v, v]),
                         "\n".join(["[", "  Branch {", "    left: 1",
                                    "    right: 2", "    more: {}", "  }",
                                    "  Branch {", "    left: 1", "    right: 2",
                                    "    more: {}", "  }", "]"]))

    def test_loops_end_at_a_number(self):
        k = Knot(None)
        k.next = Branch(k, None)
        self.assertEqual(text(k),
                         "\n".join(["Knot #1 {", "  next: Branch {",
                                    "    left: Knot #1", "    right: none",
                                    "    more: {}", "  }", "}"]))

    def test_a_value_inside_itself_points_above(self):
        b = Branch(None, None)
        b.left = [b]
        self.assertEqual(text(b),
                         "Branch {\n  left: [\n    Branch ^\n  ]\n"
                         "  right: none\n  more: {}\n}")

    def test_document_elements_are_named(self):
        self.assertEqual(
            text(Branch(tcs_xtce.ParameterInstanceRefType(parameter_ref="P"),
                        None)),
            "Branch {\n  left: xtce ParameterInstanceRefType\n"
            "  right: none\n  more: {}\n}")

    def test_unknown_values_are_refused(self):
        with self.assertRaises(TypeError):
            text(Branch({1, 2}, None))
        with self.assertRaises(TypeError):
            text(Branch(None, None, {Leaf(1): 1}))


class Missions(unittest.TestCase):
    def test_every_mission_dumps(self):
        for names in MISSIONS:
            with self.subTest(names=names):
                out = model_text(names)
                self.assertTrue(out.startswith("Mission {\n"), out[:40])
                self.assertTrue(out.endswith("}\n"))

    def test_every_mission_dumps_its_stages(self):
        for names in MISSIONS:
            with self.subTest(names=names):
                roots = [tcs_xtce.Document.from_file(str(TOP / n)).root
                         for n in names]
                types = dump_types(Compiler(roots))
                self.assertTrue(types.startswith("Types {\n"), types[:40])
                containers = dump_containers(Compiler(roots))
                self.assertTrue(containers.startswith("Containers {\n"),
                                containers[:40])

    def test_the_text_does_not_depend_on_hashing(self):
        # Python hashes strings differently in each process, so a set the
        # compiler walked would show here as a different order.
        code = ("import sys; from tests.compiler.test_dump import model_text, "
                "MISSIONS; sys.stdout.write(model_text(MISSIONS[0]))")
        outs = set()
        for seed in ("1", "2"):
            env = dict(os.environ, PYTHONHASHSEED=seed,
                       PYTHONPATH=os.pathsep.join([str(TOP / "python"),
                                                   os.environ.get("PYTHONPATH",
                                                                  "")]))
            outs.add(subprocess.run([sys.executable, "-c", code], env=env,
                                    cwd=TOP / "python", check=True,
                                    capture_output=True, text=True).stdout)
        self.assertEqual(len(outs), 1)


if __name__ == "__main__":
    unittest.main()
