"""Tests of alarms: patterns compiled to automata (xtcepkt.regex) and the
alarms the compiler binds to each packet."""

import random
import re
import unittest

from tests.test_compile import compile_xml, mission
from xtcepkt.compile import int_band
from xtcepkt.model import Band, IntEnc, Unsupported, VType
from xtcepkt.regex import compile_pattern, matches

PATTERNS = [r"OK", r"ERR.*", r"(?i)fault|fail(ed)?", r"[A-Z]{2,4}\d+",
            r"^a+b?$", r"[^x]*x", r"\w+\s\w+", r".é.", r"[α-ω]+",
            r"(ab|cd){1,3}", r"a*?b", r"[\d.]+", r"", r"(a|b)*abb"]
ALPHABET = "abcdxyzOKERfaiulFAILed AZ019 é αβω._\n"


class PatternTests(unittest.TestCase):
    def test_like_re(self):
        rng = random.Random(1)
        for p in PATTERNS:
            dfa = compile_pattern(p)
            # The classes are ASCII, as Java's are.
            r = re.compile(p, re.ASCII if "α" not in p else 0)
            for _ in range(2000):
                s = "".join(rng.choice(ALPHABET)
                            for _ in range(rng.randrange(8)))
                with self.subTest(pattern=p, string=s):
                    self.assertEqual(matches(dfa, s.encode()),
                                     r.fullmatch(s) is not None)

    def test_minimal(self):
        self.assertEqual(len(compile_pattern("(a|b)*abb").accept), 4)
        self.assertEqual(len(compile_pattern("a*?b").accept), 2)

    def test_unsupported(self):
        for p in (r"(a)\1", r"a(?=b)", r"(?<=a)b", r"a{30000}", r"a\bb"):
            with self.subTest(pattern=p):
                self.assertRaises(Unsupported, compile_pattern, p)


def u8() -> VType:
    return VType("U8", "U8", "int", IntEnc(8))


class IntBandTests(unittest.TestCase):
    def test_bounds(self):
        def bounds(lo, lo_incl, hi, hi_incl):
            b = int_band(Band(2, lo, lo_incl, hi, hi_incl), u8())
            return b.lo, b.hi

        self.assertEqual(bounds(9.5, True, 20.0, False), (10, 19))
        self.assertEqual(bounds(10.0, False, 20.5, True), (11, 20))
        # At or beyond the type's limits, a bound is no bound.
        self.assertEqual(bounds(-3.0, True, 255.0, True), (None, None))
        self.assertEqual(bounds(None, True, 1e9, True), (None, None))
        # Empty ranges, inside the type or out of it.
        self.assertEqual(bounds(5.5, True, 5.7, True), (1, 0))
        self.assertEqual(bounds(300.0, True, None, True), (1, 0))
        self.assertEqual(bounds(-float("inf"), True, float("inf"), True),
                         (None, None))


def alarm_mission(alarm: str, entries: str = "A") -> str:
    """Telemetry with a U8 parameter A whose type has ``alarm`` (the
    elements after the encoding), U8 B, and a packet of ``entries``."""
    refs = "".join(f'<ParameterRefEntry parameterRef="{e}"/>'
                   for e in entries.split())
    return mission(tm=f"""
        <ParameterTypeSet>
          <IntegerParameterType name="AT" signed="false">
          <IntegerDataEncoding sizeInBits="8"/>{alarm}</IntegerParameterType>
          <IntegerParameterType name="U8" signed="false">
          <IntegerDataEncoding sizeInBits="8"/></IntegerParameterType>
        </ParameterTypeSet>
        <ParameterSet>
          <Parameter name="A" parameterTypeRef="AT"/>
          <Parameter name="B" parameterTypeRef="U8"/>
        </ParameterSet>
        <ContainerSet>
          <SequenceContainer name="P"><EntryList>{refs}</EntryList>
          </SequenceContainer>
        </ContainerSet>""")


RANGES = """<DefaultAlarm><StaticAlarmRanges>
    <WarningRange minInclusive="10" maxInclusive="20"/>
    </StaticAlarmRanges></DefaultAlarm>"""
CONTEXT = """<ContextAlarmList><ContextAlarm><StaticAlarmRanges>
    <WatchRange maxInclusive="5"/></StaticAlarmRanges>
    <ContextMatch><Comparison parameterRef="B" value="1"/></ContextMatch>
    </ContextAlarm></ContextAlarmList>"""


class AlarmTests(unittest.TestCase):
    def test_bound(self):
        n = compile_xml(alarm_mission(RANGES + CONTEXT, "B A")).nodes[0]
        ((name, lc, a),) = n.alarms
        self.assertEqual((name, lc.name), ("a", "A"))
        self.assertEqual(a.default.bands, [Band(2, 10, True, 20, True)])
        ((terms, d),) = a.contexts
        self.assertEqual(terms[0].text, "/Sat/B == 1")
        self.assertEqual(d.bands, [Band(1, None, True, 5, True)])

    def test_notes(self):
        # The context's B isn't in the packet; ChangeAlarmRanges need the
        # values of other packets.
        change = """<DefaultAlarm><ChangeAlarmRanges>
            <WarningRange maxInclusive="3"/></ChangeAlarmRanges>
            </DefaultAlarm>"""
        for alarm, why in ((RANGES + CONTEXT,
                            "criterion on /Sat/B, which is not decoded before "
                            "it"), (change, "ChangeAlarmRanges")):
            with self.subTest(why=why):
                n = compile_xml(alarm_mission(alarm)).nodes[0]
                self.assertEqual(n.alarms, [])
                (note,) = n.alarm_notes
                self.assertIn(why, note)


if __name__ == "__main__":
    unittest.main()
