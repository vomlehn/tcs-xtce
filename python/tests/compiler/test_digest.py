"""Tests of xtce-digest."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import hashlib

from tcs_xtce.digest_cli import (XtceError, digest, digest_file, digest_files,
                                 digest_set, digest_string, encoding)
from tcs_xtce.digest_cli.__main__ import main

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
    """Builds a small XTCE document: SpaceSystem Sat with container C.

    Args:
        types:   The parameter types' XML, one string each.
        params:  The parameters' XML, one string each.
        entries: C's entries' XML, one string each.
        root:    The attributes of the root SpaceSystem element.
        prefix:  The namespace prefix to put on every element, or "" for
                 none.

    Returns:
        The document.
    """
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


class LibraryTests(unittest.TestCase):
    def test_string_and_file_give_the_digest(self):
        text = doc()
        self.assertEqual(digest_string(text), md5(text))
        self.assertEqual(digest_string(text.encode()), md5(text))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sat.xml"
            p.write_text(text)
            self.assertEqual(digest_file(p), md5(text))
            self.assertEqual(digest_file(str(p)), md5(text))

    def test_only_valid_xtce_is_digested(self):
        for text in ("<SpaceSystem", "<other/>"):
            with self.subTest(text=text), self.assertRaises(XtceError):
                digest_string(text)
        with self.assertRaises(OSError):
            digest_file("/nonexistent/x.xml")


class SetTests(unittest.TestCase):
    """The digest of a mission split across several documents."""

    def test_definition(self):
        a, b = doc(), doc(root=f'xmlns="{NS}" name="Other"')
        codes = sorted([encoding(a.encode()), encoding(b.encode())])
        self.assertEqual(digest_set([a, b]),
                         hashlib.md5(b"".join(codes)).hexdigest())

    def test_one_document_has_its_own_digest(self):
        self.assertEqual(digest_set([doc()]), md5(doc()))

    def test_order_does_not_matter(self):
        a, b = doc(), doc(root=f'xmlns="{NS}" name="Other"')
        self.assertEqual(digest_set([a, b]), digest_set([b.encode(), a]))
        self.assertNotEqual(digest_set([a, b]), digest_set([a]))
        with tempfile.TemporaryDirectory() as d:
            pa, pb = Path(d) / "a.xml", Path(d) / "b.xml"
            pa.write_text(a)
            pb.write_text(b)
            self.assertEqual(digest_files([pb, str(pa)]), digest_set([a, b]))

    def test_every_document_must_be_read(self):
        with self.assertRaises(XtceError):
            digest_set([doc(), "<other/>"])
        with self.assertRaises(OSError):
            digest_files(["/nonexistent/x.xml"])


class CommandLineTests(unittest.TestCase):
    def run_main(self, *texts_or_paths, opts=()):
        """Runs xtce-digest on documents or files.

        Each string is written to a temporary file first.

        Args:
            *texts_or_paths: The documents, as strings, or files, as Paths.
            opts:            Command-line options to put before the names.

        Returns:
            Its exit status, its standard output, its standard error, and the
            file names it was given.
        """
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
                status = main([*opts, *names])
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

    def test_set(self):
        a, b = doc(), doc(root=f'xmlns="{NS}" name="Other"')
        status, out, _, _ = self.run_main(b, a, opts=["--set"])
        self.assertEqual((status, out), (0, digest_set([a, b]) + "\n"))
        status, out, err, names = self.run_main(a, "<other/>", opts=["--set"])
        self.assertEqual((status, out), (1, ""))
        self.assertTrue(err.startswith(f"xtce-digest: {names[1]}: "), err)

    def test_needs_a_file(self):
        with (contextlib.redirect_stderr(io.StringIO()),
              self.assertRaises(SystemExit) as cm):
            main([])
        self.assertNotEqual(cm.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
