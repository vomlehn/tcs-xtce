"""Tests of tcs_xtce.binding.xsd, which reads the XTCE schema the bindings
are generated from."""

import dataclasses
import os
import re
import unittest

from tcs_xtce.binding.xsd import parse

# The schema the bindings are generated from, as make references fetches
# it.
SCHEMA = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                      "references", "SpaceSystem.xsd")


def docs(node, seen=None):
    """Gathers every description in a schema model.

    Args:
        node: The model, or any part of it.
        seen: The parts visited already, by id, so that a part shared
              among several is visited once.

    Returns:
        Each ``doc`` the model holds.
    """
    seen = set() if seen is None else seen
    if id(node) in seen:
        return []
    seen.add(id(node))
    found = []
    if dataclasses.is_dataclass(node) and not isinstance(node, type):
        for f in dataclasses.fields(node):
            value = getattr(node, f.name)
            if f.name == "doc" and isinstance(value, str):
                found.append(value)
            else:
                found += docs(value, seen)
    elif isinstance(node, dict):
        for value in node.values():
            found += docs(value, seen)
    elif isinstance(node, (list, tuple)):
        for value in node:
            found += docs(value, seen)
    return found


@unittest.skipUnless(os.path.exists(SCHEMA),
                     "references/SpaceSystem.xsd is fetched by make")
class OwnWords(unittest.TestCase):
    def test_copies_none_of_omgs_documentation(self):
        """The schema's documentation is OMG's, which OMG does not allow
        to be redistributed, and the generated code is: so no sentence of
        it may reach a description the generator writes."""
        with open(SCHEMA, encoding="utf-8") as f:
            text = f.read()
        sentences = set()
        for d in re.findall(r"<(?:\w+:)?documentation[^>]*>(.*?)</", text,
                            re.S):
            for s in re.split(r"(?<=\.)\s+", d):
                s = " ".join(s.split())
                if len(s) > 30:
                    sentences.add(s)
        self.assertGreater(len(sentences), 100)
        written = docs(parse(SCHEMA))
        self.assertGreater(len(written), 100)
        copied = [d for d in written if any(s in d for s in sentences)]
        self.assertEqual(copied, [])


if __name__ == "__main__":
    unittest.main()
