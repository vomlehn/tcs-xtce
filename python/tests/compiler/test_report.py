"""Tests of the size report (tcs_xtce.compiler.report)."""

import unittest

from tests.compiler.test_compile import command, compile_xml, mission
from tcs_xtce.compiler.report import bits_for, report, rows


class BitsFor(unittest.TestCase):
    def test_unsigned(self):
        self.assertEqual(bits_for(0, 0, "unsigned"), 1)
        self.assertEqual(bits_for(0, 1, "unsigned"), 1)
        self.assertEqual(bits_for(0, 1000, "unsigned"), 10)
        self.assertEqual(bits_for(0, 1023, "unsigned"), 10)
        self.assertEqual(bits_for(0, 1024, "unsigned"), 11)
        self.assertIsNone(bits_for(-1, 5, "unsigned"))

    def test_twos_complement(self):
        self.assertEqual(bits_for(-128, 127, "twosComplement"), 8)
        self.assertEqual(bits_for(-129, 127, "twosComplement"), 9)
        self.assertEqual(bits_for(-128, 128, "twosComplement"), 9)
        # no 1-bit signed
        self.assertEqual(bits_for(-1, 0, "twosComplement"), 2)

    def test_sign_magnitude_and_ones_complement(self):
        for enc in ("signMagnitude", "onesComplement"):
            self.assertEqual(bits_for(-127, 127, enc), 8)
            self.assertEqual(bits_for(-128, 0, enc), 9)


MISSION = mission(tm="""
    <ParameterTypeSet>
      <IntegerParameterType name="Count" signed="false">
        <IntegerDataEncoding sizeInBits="32"/>
        <ValidRange minInclusive="0" maxInclusive="1000"/>
      </IntegerParameterType>
      <IntegerParameterType name="Delta">
        <IntegerDataEncoding sizeInBits="16" encoding="twosComplement"/>
        <ValidRange minInclusive="-100" maxInclusive="100"/>
      </IntegerParameterType>
      <IntegerParameterType name="Level">
        <IntegerDataEncoding sizeInBits="16" encoding="twosComplement"/>
        <ValidRange minInclusive="0" maxInclusive="100"/>
      </IntegerParameterType>
      <IntegerParameterType name="Little" signed="false">
        <IntegerDataEncoding sizeInBits="32"
          byteOrder="leastSignificantByteFirst"/>
        <ValidRange minInclusive="0" maxInclusive="300"/>
      </IntegerParameterType>
      <IntegerParameterType name="Open" signed="false">
        <IntegerDataEncoding sizeInBits="16"/>
      </IntegerParameterType>
      <EnumeratedParameterType name="Mode">
        <IntegerDataEncoding sizeInBits="8"/>
        <EnumerationList>
          <Enumeration value="0" label="Off"/>
          <Enumeration value="5" label="On"/>
        </EnumerationList>
      </EnumeratedParameterType>
      <BooleanParameterType name="Flag">
        <IntegerDataEncoding sizeInBits="8"/>
      </BooleanParameterType>
      <StringParameterType name="Name">
        <StringDataEncoding>
          <SizeInBits><Fixed><FixedValue>64</FixedValue></Fixed>
          <TerminationChar>00</TerminationChar></SizeInBits>
        </StringDataEncoding>
      </StringParameterType>
    </ParameterTypeSet>
    <ParameterSet>
      <Parameter name="C" parameterTypeRef="Count"/>
      <Parameter name="D" parameterTypeRef="Delta"/>
      <Parameter name="L" parameterTypeRef="Level"/>
      <Parameter name="LE" parameterTypeRef="Little"/>
      <Parameter name="O" parameterTypeRef="Open"/>
      <Parameter name="M" parameterTypeRef="Mode"/>
      <Parameter name="F" parameterTypeRef="Flag"/>
      <Parameter name="N" parameterTypeRef="Name"/>
    </ParameterSet>
    <ContainerSet>
      <SequenceContainer name="Status">
        <EntryList>
          <ParameterRefEntry parameterRef="C"/>
          <ParameterRefEntry parameterRef="D"/>
          <ParameterRefEntry parameterRef="L"/>
          <ParameterRefEntry parameterRef="LE"/>
          <ParameterRefEntry parameterRef="O"/>
          <ParameterRefEntry parameterRef="M"/>
          <ParameterRefEntry parameterRef="F"/>
          <ParameterRefEntry parameterRef="N">
            <LocationInContainerInBits referenceLocation="previousEntry">
              <FixedValue>4</FixedValue>
            </LocationInContainerInBits>
          </ParameterRefEntry>
        </EntryList>
      </SequenceContainer>
    </ContainerSet>""",
                  tc="""
    <ArgumentTypeSet>
      <IntegerArgumentType name="Arg" signed="false">
        <IntegerDataEncoding sizeInBits="16"/>
        <ValidRangeSet>
          <ValidRange minInclusive="1" maxInclusive="10"/>
          <ValidRange minInclusive="20" maxInclusive="40"/>
        </ValidRangeSet>
      </IntegerArgumentType>
    </ArgumentTypeSet>
    <MetaCommandSet>"""
                  + command("Go", '<Argument name="A" argumentTypeRef="Arg"/>',
                            '<ArgumentRefEntry argumentRef="A"/>')
                  + "</MetaCommandSet>")


class Report(unittest.TestCase):
    def setUp(self):
        m = compile_xml(MISSION)
        self.mission = m
        self.nodes = {n.ident: n for n in m.nodes}

    def test_fields(self):
        got = {r.name: (r.bits, r.need) for r in rows(self.nodes["Status"])}
        self.assertEqual(
            got,
            {
                "C": (32, 10),  # 0..1000
                "D": (16, 8),  # -100..100, two's complement
                "L": (16, 7),  # 0..100: unsigned would do
                # 0..300 needs 9 bits, little endian or not
                "LE": (32, 9),
                "O": (16, None),  # no ValidRange
                "M": (8, 3),  # values 0..5
                "F": (8, 1),  # boolean
                "N": (64, None)  # fixed-size string: flagged, no figure
            })
        notes = {r.name: r.note for r in rows(self.nodes["Status"])}
        self.assertIn("as unsigned", notes["L"])
        self.assertIn("padded", notes["N"])

    def test_argument_range_set(self):
        (row,) = rows(self.nodes["Go"])
        self.assertEqual((row.bits, row.need), (16, 6))  # 1..40

    def test_text(self):
        text = report(self.mission)
        self.assertIn("4 bits are skipped by location offsets or padding", text)
        # 32+16+16+32+16+8+8 = 128 bits of integers etc. save 22+8+9+23+5+7 =
        # 74.
        self.assertIn("right-sized fields save 74 bits", text)
        self.assertIn("/Sat/Status: 25 -> 16 bytes", text)
        self.assertIn("/Sat/Go: 2 -> 1 byte", text)
        self.assertIn("1 integer types have no ValidRange", text)


if __name__ == "__main__":
    unittest.main()
