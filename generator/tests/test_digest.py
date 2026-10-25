"""Tests of xtce-digest."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from xtce_digest import digest
from xtce_digest.__main__ import main

NS = "http://www.omg.org/spec/XTCE/20250214"

TYPES = ('<IntegerParameterType name="U8" signed="false"><IntegerDataEncoding '
         'sizeInBits="8"/></IntegerParameterType>',
         '<IntegerParameterType name="U16" signed="false"><IntegerDataEncoding '
         'sizeInBits="16"/></IntegerParameterType>')
PARAMS = ('<Parameter name="A" parameterTypeRef="U8"/>',
          '<Parameter name="B" parameterTypeRef="U16"/>')
ENTRIES = ('<ParameterRefEntry parameterRef="A"/>',
           '<ParameterRefEntry parameterRef="B"/>')


def doc(types=TYPES, params=PARAMS, entries=ENTRIES,
        root=f'xmlns="{NS}" name="Sat"', prefix="") -> str:
    """A small XTCE document; ``prefix`` is the namespace prefix to use."""
    p = f"{prefix}:" if prefix else ""

    def tag(s: str) -> str:
        return s.replace("<", f"<{p}").replace(f"<{p}/", f"</{p}")

    body = (f"<TelemetryMetaData><ParameterTypeSet>{''.join(types)}"
            f"</ParameterTypeSet><ParameterSet>{''.join(params)}</ParameterSet>"
            "<ContainerSet><SequenceContainer "
            f'name="C"><EntryList>{"".join(entries)}</EntryList>'
            '</SequenceContainer></ContainerSet></TelemetryMetaData>')
    return f"<{p}SpaceSystem {root}>{tag(body)}</{p}SpaceSystem>"


def md5(text: str) -> str:
    return digest(text.encode())


class DigestTests(unittest.TestCase):
    def test_same_meaning_same_digest(self):
        base = md5(doc())
        same = [
            # Attributes in another order.
            doc(root=f'name="Sat" xmlns="{NS}"',
                params=('<Parameter parameterTypeRef="U8" name="A"/>',
                        PARAMS[1])),
            # Comments, processing instructions and whitespace between elements.
            '<?xml version="1.0"?>\n<!-- a comment -->\n'
            + doc().replace("><", ">\n  <!-- c -->\n  <"),
            # Another namespace prefix.
            doc(root=f'xmlns:x="{NS}" name="Sat"', prefix="x"),
            # Unordered collections in another order.
            doc(types=TYPES[::-1], params=PARAMS[::-1])]
        for text in same:
            self.assertEqual(md5(text), base, text)

    def test_different_meaning_different_digest(self):
        base = md5(doc())
        different = [
            # Entries are encoded in order.
            doc(entries=ENTRIES[::-1]),
            # Attribute values and names.
            doc(params=('<Parameter name="A" parameterTypeRef="U16"/>',
                        PARAMS[1])),
            doc(root=f'xmlns="{NS}" name="Sat2"'),
            # Elements added or removed.
            doc(entries=ENTRIES[:1])]
        for text in different:
            self.assertNotEqual(md5(text), base, text)

    def test_text_content(self):
        def described(text: str) -> str:
            return doc(params=('<Parameter name="A" parameterTypeRef="U8">'
                               f'<LongDescription>{text}</LongDescription>'
                               '</Parameter>', PARAMS[1]))

        base = md5(described("hot"))
        self.assertEqual(md5(described("\n   hot  ")), base)
        self.assertNotEqual(md5(described("cold")), base)


class CommandLineTests(unittest.TestCase):
    def run_main(self, *texts_or_paths):
        """Runs xtce-digest on documents (strings) or paths (Path), and
        returns its status, its output, and the names it was given."""
        with tempfile.TemporaryDirectory() as d:
            names = []
            for i, t in enumerate(texts_or_paths):
                if isinstance(t, Path):
                    names.append(str(t))
                else:
                    p = Path(d) / f"f{i}.xml"
                    p.write_text(t)
                    names.append(str(p))
            out, err = io.StringIO(), io.StringIO()
            with (contextlib.redirect_stdout(out),
                  contextlib.redirect_stderr(err)):
                status = main(names)
            return status, out.getvalue(), err.getvalue(), names

    def test_prints_each_file_and_its_digest_in_order(self):
        a, b = doc(), doc(entries=ENTRIES[::-1])
        status, out, _, names = self.run_main(a, b, a)
        self.assertEqual(status, 0)
        # Each file is digested on its own, so the same document repeats.
        self.assertEqual(out.splitlines(),
                         [f"{names[0]}: {md5(a)}", f"{names[1]}: {md5(b)}",
                          f"{names[2]}: {md5(a)}"])

    def test_errors_end_with_failure(self):
        bad = [
            "<SpaceSystem",  # not well-formed
            "<other/>",  # not XTCE
            Path("/nonexistent/x.xml")  # can't be read
        ]
        for b in bad:
            status, out, err, names = self.run_main(doc(), b, doc())
            self.assertEqual(status, 1)
            # The files before it are reported, and none after it.
            self.assertEqual(out.splitlines(), [f"{names[0]}: {md5(doc())}"])
            self.assertTrue(err.startswith(f"xtce-digest: {names[1]}: "), err)

    def test_needs_a_file(self):
        with (contextlib.redirect_stderr(io.StringIO()),
              self.assertRaises(SystemExit) as cm):
            main([])
        self.assertNotEqual(cm.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
