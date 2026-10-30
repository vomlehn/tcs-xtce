"""Tests for the Python XTCE library (standard-library unittest only)."""

from __future__ import annotations

import doctest
import glob
import io
import os
import unittest

import tcs_xtce
from tcs_xtce import Document, ManagedBuffer, SpaceSystemType, XtceError
from tcs_xtce import _xml as X

SAMPLES = sorted(glob.glob(os.path.join(os.path.dirname(__file__), "..", "..",
                                        "tests", "samples", "*.xml")))


class RoundTrip(unittest.TestCase):
    def test_samples_exist(self):
        self.assertTrue(SAMPLES, "no samples; run `make samples`")

    def test_samples_round_trip(self):
        for path in SAMPLES:
            with self.subTest(path=os.path.basename(path)):
                doc = Document.from_file(path)
                first = doc.to_bytes()
                again = Document.from_bytes(first)
                self.assertEqual(again.root, doc.root)
                self.assertEqual(again.to_bytes(), first)

    def test_build_and_serialize(self):
        ss = SpaceSystemType(name="Built",
                             short_description='a "quoted" <desc>')
        doc = Document(ss)
        back = Document.from_bytes(doc.to_bytes())
        self.assertEqual(back.root, ss)

    def test_choice_constructors(self):
        names = [name for name in dir(tcs_xtce) if name.endswith("Choice")]
        self.assertTrue(names)
        for name in names:
            cls = getattr(tcs_xtce, name)
            self.assertIn(cls().name, cls.NAMES)


class Buffers(unittest.TestCase):
    def test_output_buffer_is_reused(self):
        doc = Document.from_file(SAMPLES[0])
        with doc.serialize() as view:
            n = len(view)
        cap = doc.output.capacity
        storage = doc.output._data
        for _ in range(3):
            with doc.serialize() as view:
                self.assertEqual(len(view), n)
            self.assertEqual(doc.output.capacity, cap)
            self.assertIs(doc.output._data, storage)

    def test_input_buffer_is_reused(self):
        with open(SAMPLES[0], "rb") as f:
            data = f.read()
        doc = Document.from_bytes(data)
        cap = doc.input.capacity
        doc.load(data)
        doc.read_from(io.BytesIO(data))
        self.assertEqual(doc.input.capacity, cap)

    def test_managed_buffer(self):
        b = ManagedBuffer(4)
        b.write(b"abc")
        b.write(b"defgh")
        self.assertEqual(b.getvalue(), b"abcdefgh")
        b.clear()
        self.assertEqual(len(b), 0)
        self.assertGreaterEqual(b.capacity, 8)

    def test_load_from_stream_without_readinto(self):
        class Reader:
            """A stream with read() but no readinto(), returning more bytes
            than the buffer has room for."""

            def __init__(self, data):
                self.stream = io.BytesIO(data)

            def read(self, n=-1):
                return self.stream.read(n)

        data = bytes(range(256)) * 40
        for stream in (Reader(data), io.BytesIO(data)):
            buf = ManagedBuffer(16)
            self.assertEqual(buf.load_from(stream), len(data))
            self.assertEqual(buf.getvalue(), data)

    def test_close_releases(self):
        with Document() as doc:
            doc.to_bytes()
        self.assertEqual(doc.output.capacity, 0)


class Errors(unittest.TestCase):
    def test_unknown_element_has_line(self):
        with self.assertRaises(XtceError) as cm:
            Document.from_bytes(b"<SpaceSystem name='a'>\n<Bogus/>"
                                b"</SpaceSystem>")
        self.assertEqual(cm.exception.line, 2)
        self.assertIn("Bogus", cm.exception.message)

    def test_missing_attribute(self):
        with self.assertRaisesRegex(XtceError, "name"):
            Document.from_bytes(b"<SpaceSystem/>")

    def test_wrong_root(self):
        with self.assertRaisesRegex(XtceError, "root"):
            Document.from_bytes(b"<Other name='a'/>")

    def test_malformed(self):
        with self.assertRaises(XtceError):
            Document.from_bytes(b"<SpaceSystem name='a'>")


class Scalars(unittest.TestCase):
    def test_doubles_match_other_languages(self):
        cases = {1.0: "1.0", 1.5: "1.5", 0.0001: "0.0001", 0.00001: "1e-05",
                 1e16: "1e+16", float("inf"): "INF"}
        for v, s in cases.items():
            self.assertEqual(X.f_double(v), s)
            self.assertEqual(X.p_double(s), v)

    def test_rejects_non_xml_numbers(self):
        for bad in ("inf", "nan", "1_000", ""):
            with self.assertRaises(ValueError):
                X.p_double(bad)
        with self.assertRaises(ValueError):
            X.p_i64("1_0")
        with self.assertRaises(ValueError):
            X.p_u16("-1")


def load_tests(loader, tests, ignore):
    tests.addTests(doctest.DocTestSuite(tcs_xtce))
    return tests


if __name__ == "__main__":
    unittest.main()
