"""Tests of the packet compiler (tcs_xtce.compiler.compile) on small XTCE
documents."""

import sys
import unittest
from pathlib import Path
from typing import Sequence

# The compiler reads XTCE documents with the generated `xtce` Python package.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import tcs_xtce  # noqa: E402

from tcs_xtce.compiler import emit_c  # noqa: E402
from tcs_xtce.compiler.compile import (  # noqa: E402
    compile_document, compile_documents, item_bits, member_path, value_range)
from tcs_xtce.compiler.model import (  # noqa: E402
    MAX_FIELD_BITS, AnyOf, Cond, ContextCal, Fill, Fixed, Index, MathOp, Pad,
    Require, Seek, Segment, Spline, TailSeek, Unsupported, Value, walk,
    LengthEnd, Transform, Verify, XformEnc)

NS = "http://www.omg.org/spec/XTCE/20250214"


def mission(tm: str = "", tc: str = "") -> str:
    tm = f"<TelemetryMetaData>{tm}</TelemetryMetaData>" if tm else ""
    tc = f"<CommandMetaData>{tc}</CommandMetaData>" if tc else ""
    return f'<SpaceSystem xmlns="{NS}" name="Sat">{tm}{tc}</SpaceSystem>'


def compile_xml(text: str, **kw):
    return compile_document(tcs_xtce.Document.from_bytes(text.encode()).root,
                            **kw)


def command(name: str, args: str = "", entries: str = "",
            base: str = "") -> str:
    """Builds a MetaCommand with its arguments and command container entries.

    Args:
        name:    The MetaCommand's name; its container is ``name`` + "C".
        args:    The XML inside its ArgumentList; "" for no list.
        entries: The XML of its container's entries.
        base:    The name of the MetaCommand it extends; "" for none.

    Returns:
        The MetaCommand's XML.
    """
    base = f'<BaseMetaCommand metaCommandRef="{base}"/>' if base else ""
    args = f"<ArgumentList>{args}</ArgumentList>" if args else ""
    return (f'<MetaCommand name="{name}">{base}{args}<CommandContainer '
            f'name="{name}C"><EntryList>{entries}</EntryList>'
            '</CommandContainer></MetaCommand>')


class TypeTests(unittest.TestCase):
    def test_parameter_and_argument_types_with_one_name_stay_apart(self):
        text = mission(tm="""
            <ParameterTypeSet>
              <IntegerParameterType name="ModeType" signed="false">
                <IntegerDataEncoding sizeInBits="8"/>
              </IntegerParameterType>
            </ParameterTypeSet>
            <ParameterSet><Parameter name="Mode" parameterTypeRef="ModeType"/>
            </ParameterSet>
            <ContainerSet>
              <SequenceContainer name="Status">
                <EntryList><ParameterRefEntry parameterRef="Mode"/></EntryList>
              </SequenceContainer>
            </ContainerSet>""",
                       tc="""
            <ArgumentTypeSet>
              <IntegerArgumentType name="ModeType">
                <IntegerDataEncoding sizeInBits="16" encoding="twosComplement"/>
              </IntegerArgumentType>
            </ArgumentTypeSet>
            <MetaCommandSet>"""
                       + command("SetMode",
                                 '<Argument name="Mode" '
                                 'argumentTypeRef="ModeType"/>',
                                 '<ArgumentRefEntry argumentRef="Mode"/>')
                       + "</MetaCommandSet>")
        m = compile_xml(text)
        nodes = {n.ident: n for n in m.nodes}
        param = nodes["Status"].fields[0][1].vtype
        arg = nodes["SetMode"].fields[0][1].vtype
        self.assertEqual((param.enc.bits, param.signed), (8, False))
        self.assertEqual((arg.enc.bits, arg.signed), (16, True))
        self.assertEqual(nodes["SetMode"].max_bytes, 2)



U8_ARG = """
    <ArgumentTypeSet>
      <IntegerArgumentType name="U8"><IntegerDataEncoding sizeInBits="8"/>
      </IntegerArgumentType>
    </ArgumentTypeSet>"""


class ErrorTests(unittest.TestCase):
    """Bad documents are reported as Unsupported, never as a crash."""

    def unsupported(self, text: str) -> str:
        with self.assertRaises(Unsupported) as cm:
            compile_xml(text)
        return str(cm.exception)

    def test_meta_command_inheritance_cycle(self):
        text = mission(tc=U8_ARG + "<MetaCommandSet>"
                       + command("A",
                                 '<Argument name="X" argumentTypeRef="U8"/>',
                                 '<ArgumentRefEntry argumentRef="X"/>', "B")
                       + command("B", "", "", "A") + "</MetaCommandSet>")
        self.assertIn("cycle", self.unsupported(text))
        # --skip-unsupported leaves both commands out.
        self.assertEqual(compile_xml(text, skip_unsupported=True).nodes, [])


    def test_malformed_fixed_value(self):
        for value in ("0xZZ", "0x10", "1_0", "zz"):
            text = mission(
                tc="<MetaCommandSet>"
                + command("Ping",
                          entries=f'<FixedValueEntry binaryValue="{value}" '
                                  'sizeInBits="8"/>') + "</MetaCommandSet>")
            self.assertIn("is not hexBinary", self.unsupported(text), value)
            self.assertEqual(compile_xml(text, skip_unsupported=True).nodes, [])

    def test_fixed_values(self):
        for value, bits, expect in (("A5", 8, 0xA5), ("3", 2, 3), ("", 4, 0),
                                    ("0102", 16, 0x102)):
            text = mission(tc="<MetaCommandSet>"
                           + command("Ping",
                                     entries='<FixedValueEntry '
                                             f'binaryValue="{value}" '
                                             f'sizeInBits="{bits}"/>')
                           + "</MetaCommandSet>")
            fixed = compile_xml(text).nodes[0].items[0]
            self.assertEqual((fixed.bits, fixed.value), (bits, expect), value)

    def test_malformed_termination_char(self):
        text = mission(tm="""
            <ParameterTypeSet>
              <StringParameterType name="S">
                <StringDataEncoding>
                  <Variable maxSizeInBits="64">
                  <TerminationChar>GG</TerminationChar></Variable>
                </StringDataEncoding>
              </StringParameterType>
            </ParameterTypeSet>
            <ParameterSet><Parameter name="Name" parameterTypeRef="S"/>
            </ParameterSet>
            <ContainerSet>
              <SequenceContainer name="Status">
                <EntryList><ParameterRefEntry parameterRef="Name"/></EntryList>
              </SequenceContainer>
            </ContainerSet>""")
        self.assertIn("is not hexBinary", self.unsupported(text))


    def test_reports_root_causes(self):
        derived = "".join(f'''<SequenceContainer name="D{i}">
                  <EntryList/>
                  <BaseContainer containerRef="Base"/>
                </SequenceContainer>''' for i in range(3))
        text = mission(tm=f"""
            <ParameterTypeSet>
              <EnumeratedParameterType name="EC">
                <IntegerDataEncoding sizeInBits="8"><DefaultCalibrator>
                <PolynomialCalibrator><Term coefficient="2" exponent="1"/>
                </PolynomialCalibrator></DefaultCalibrator>
                </IntegerDataEncoding>
                <EnumerationList><Enumeration value="0" label="A"/>
                </EnumerationList>
              </EnumeratedParameterType>
            </ParameterTypeSet>
            <ParameterSet><Parameter name="X" parameterTypeRef="EC"/>
            </ParameterSet>
            <ContainerSet>
              <SequenceContainer name="Base" abstract="true">
                <EntryList><ParameterRefEntry parameterRef="X"/></EntryList>
              </SequenceContainer>
              {derived}
            </ContainerSet>""")
        msg = self.unsupported(text)
        why = "calibrator on an enumerated type"
        self.assertEqual(msg.count(why), 1, msg)
        self.assertNotIn("/Sat/D0", msg)
        self.assertIn("3 containers or meta-commands derived from them", msg)
        # Skipping still names every node left out.
        skipped = compile_xml(text, skip_unsupported=True).skipped
        self.assertEqual(len(skipped), 4, skipped)
        self.assertIn(why, skipped[0])


    def test_negative_sizes(self):
        # -1 is a Yamcs convention for binary values (RestTests); other
        # negative sizes are errors.
        for ptype, why in (
                ("<BinaryParameterType name='T'><BinaryDataEncoding>"
                 "<SizeInBits><FixedValue>-8</FixedValue></SizeInBits>"
                 "</BinaryDataEncoding></BinaryParameterType>",
                 "binary size -8 is negative"),
                ("<StringParameterType name='T'><StringDataEncoding>"
                 "<SizeInBits><Fixed><FixedValue>-8</FixedValue></Fixed>"
                 "<TerminationChar>00</TerminationChar></SizeInBits>"
                 "</StringDataEncoding></StringParameterType>",
                 "string size -8 is negative"),
                ("<StringParameterType name='T'><StringDataEncoding><Variable "
                 "maxSizeInBits='-8'><TerminationChar>00</TerminationChar>"
                 "</Variable></StringDataEncoding></StringParameterType>",
                 "string size -8 is negative")):
            text = mission(tm=f"""
                <ParameterTypeSet>{ptype}</ParameterTypeSet>
                <ParameterSet><Parameter name="X" parameterTypeRef="T"/>
                </ParameterSet>
                <ContainerSet>
                  <SequenceContainer name="Status">
                    <EntryList><ParameterRefEntry parameterRef="X"/></EntryList>
                  </SequenceContainer>
                </ContainerSet>""")
            self.assertIn(why, self.unsupported(text))


def wide_mission(types: str, entries: str, params: str = "") -> str:
    """Builds telemetry with wide integers.

    A container Status of integers W (72 bits, unsigned), S (130 bits,
    signed) and N (16 bits), then ``entries``.

    Args:
        types:   More parameter types' XML.
        entries: More of Status's entries' XML, after N.
        params:  More parameters' XML.

    Returns:
        The XTCE document.
    """
    return mission(tm=f"""
        <ParameterTypeSet>
          <IntegerParameterType name="W72" signed="false">
            <IntegerDataEncoding sizeInBits="72"/>
          </IntegerParameterType>
          <IntegerParameterType name="S130">
            <IntegerDataEncoding sizeInBits="130"
              encoding="twosComplement"/>
          </IntegerParameterType>
          <IntegerParameterType name="U16" signed="false">
            <IntegerDataEncoding sizeInBits="16"/>
          </IntegerParameterType>
          {types}
        </ParameterTypeSet>
        <ParameterSet>
          <Parameter name="W" parameterTypeRef="W72"/>
          <Parameter name="S" parameterTypeRef="S130"/>
          <Parameter name="N" parameterTypeRef="U16"/>
          {params}
        </ParameterSet>
        <ContainerSet>
          <SequenceContainer name="Status">
            <EntryList>
              <ParameterRefEntry parameterRef="W"/>
              <ParameterRefEntry parameterRef="S"/>
              <ParameterRefEntry parameterRef="N"/>
              {entries}
            </EntryList>
          </SequenceContainer>
        </ContainerSet>""")


class WideIntegerTests(unittest.TestCase):
    """Integers of more than 64 bits are decoded, encoded and compared, but
    nothing computes with them."""

    def test_fields(self):
        m = compile_xml(wide_mission("", ""))
        vts = {lc.name: lc.vtype for _, lc in m.nodes[0].fields}
        self.assertEqual((vts["W"].width, vts["W"].signed, vts["W"].wide),
                         (128, False, True))
        self.assertEqual((vts["S"].width, vts["S"].signed), (136, True))
        self.assertFalse(vts["N"].wide)
        self.assertEqual(item_bits(vts["S"]), 130)

    def test_compared(self):
        cond = ('<ParameterRefEntry parameterRef="Opt"><IncludeCondition>'
                '<Comparison parameterRef="W" comparisonOperator="&gt;" '
                'value="1000000000000000000000"/></IncludeCondition>'
                '</ParameterRefEntry>')
        m = compile_xml(wide_mission("", cond,
                                     '<Parameter name="Opt" '
                                     'parameterTypeRef="U16"/>'))
        opt = next(lc for _, lc in m.nodes[0].fields if lc.name == "Opt")
        self.assertTrue(opt.optional)

    def test_length_field(self):
        # A packet length field may have more than 64 bits: it holds the
        # length with zeros above it.
        params = ('<Parameter name="L" parameterTypeRef="W72">'
                  '<AncillaryDataSet><AncillaryData name="tcs-xtce.length">0'
                  '</AncillaryData></AncillaryDataSet></Parameter>')
        n = compile_xml(wide_mission("",
                                     '<ParameterRefEntry parameterRef="L"/>',
                                     params)).nodes[0]
        lc = n.length_field()
        self.assertEqual((lc.vtype.enc.bits, lc.vtype.wide), (72, True))

    def test_computed_through_doubles(self):
        # A float, a calibrated integer and a time may have an integer
        # encoding of more than 64 bits: their fields are doubles, or the
        # calibrated integer's own size, not wide integers.
        e65 = '<IntegerDataEncoding sizeInBits="65"/>'
        for types, kind in (
                (f'<FloatParameterType name="T">{e65}</FloatParameterType>',
                 "float"),
                ('<IntegerParameterType name="T"><IntegerDataEncoding '
                 'sizeInBits="65"><DefaultCalibrator><PolynomialCalibrator>'
                 '<Term coefficient="2" exponent="1"/></PolynomialCalibrator>'
                 '</DefaultCalibrator></IntegerDataEncoding>'
                 '</IntegerParameterType>', "int"),
                (f'<AbsoluteTimeParameterType name="T"><Encoding>{e65}'
                 '</Encoding><ReferenceTime><Epoch>TAI</Epoch></ReferenceTime>'
                 '</AbsoluteTimeParameterType>', "float")):
            with self.subTest(kind=kind):
                n = compile_xml(
                    wide_mission(types, '<ParameterRefEntry parameterRef="X"/>',
                                 '<Parameter name="X" '
                                 'parameterTypeRef="T"/>')).nodes[0]
                vt = n.fields[-1][1].vtype
                self.assertEqual((vt.kind, vt.enc.bits, vt.wide),
                                 (kind, 65, False))

    def test_size_and_choices(self):
        # A size from a wide integer stays a field the application sets;
        # an enumeration and a boolean may have wide encodings.
        e65 = '<IntegerDataEncoding sizeInBits="65"/>'
        sized = ('<BinaryParameterType name="T"><BinaryDataEncoding>'
                 '<SizeInBits><DynamicValue><ParameterInstanceRef '
                 'parameterRef="W"/></DynamicValue></SizeInBits>'
                 '</BinaryDataEncoding></BinaryParameterType>')
        n = compile_xml(wide_mission(sized,
                                     '<ParameterRefEntry parameterRef="W"/>'
                                     '<ParameterRefEntry parameterRef="X"/>',
                                     '<Parameter name="X" '
                                     'parameterTypeRef="T"/>')).nodes[0]
        self.assertEqual(n.fields[-1][1].vtype.dyn.wide_type.enc.bits, 72)
        self.assertEqual(n.derived, {})
        for types, kind in (
                (f'<EnumeratedParameterType name="T">{e65}<EnumerationList>'
                 '<Enumeration value="0" label="A"/></EnumerationList>'
                 '</EnumeratedParameterType>', "enum"),
                (f'<BooleanParameterType name="T">{e65}</BooleanParameterType>',
                 "bool")):
            with self.subTest(kind=kind):
                n = compile_xml(
                    wide_mission(types, '<ParameterRefEntry parameterRef="X"/>',
                                 '<Parameter name="X" '
                                 'parameterTypeRef="T"/>')).nodes[0]
                self.assertEqual(n.fields[-1][1].vtype.kind, kind)

    def test_alarm(self):
        # Its range's bounds are whole numbers, compared exactly.
        alarm = ('<IntegerParameterType name="A" signed="false">'
                 '<IntegerDataEncoding sizeInBits="72"/><DefaultAlarm>'
                 '<StaticAlarmRanges><WarningRange maxInclusive="1e20"/>'
                 '</StaticAlarmRanges></DefaultAlarm></IntegerParameterType>')
        m = compile_xml(
            wide_mission(alarm, '<ParameterRefEntry parameterRef="X"/>',
                         '<Parameter name="X" parameterTypeRef="A"/>'))
        self.assertEqual(m.nodes[0].alarm_notes, [])
        _, _, a = m.nodes[0].alarms[0]
        self.assertEqual(a.default.bands[0].hi, 10**20)


def calibrated_mission(itype: str,
                       entries: str = '<ParameterRefEntry '
                                      'parameterRef="X"/>') -> str:
    """Builds telemetry with parameter X of type T and a binary Rest.

    Args:
        itype:   The XML of X's type, named T.
        entries: The XML of container Status's entries.

    Returns:
        The XTCE document.
    """
    return mission(tm=f"""
        <ParameterTypeSet>
          {itype}
          <BinaryParameterType name="R"><BinaryDataEncoding><SizeInBits>
            <FixedValue>-1</FixedValue></SizeInBits></BinaryDataEncoding>
            </BinaryParameterType>
        </ParameterTypeSet>
        <ParameterSet>
          <Parameter name="X" parameterTypeRef="T"/>
          <Parameter name="Rest" parameterTypeRef="R"/>
        </ParameterSet>
        <ContainerSet>
          <SequenceContainer name="Status"><EntryList>{entries}</EntryList>
          </SequenceContainer>
        </ContainerSet>""")


def calibrated(attrs: str = "",
               terms: str = '<Term coefficient="-4" exponent="0"/><Term '
                            'coefficient="0.5" exponent="1"/>') -> str:
    """Builds an 8-bit integer type T with a default polynomial calibrator.

    Args:
        attrs: More attributes of the IntegerParameterType.
        terms: The polynomial's Term elements.

    Returns:
        The type's XML.
    """
    return (f'<IntegerParameterType name="T" {attrs}><IntegerDataEncoding '
            f'sizeInBits="8"><DefaultCalibrator><PolynomialCalibrator>{terms}'
            '</PolynomialCalibrator></DefaultCalibrator></IntegerDataEncoding>'
            '</IntegerParameterType>')


class CalibratedIntegerTests(unittest.TestCase):
    def vtype(self, text: str):
        return compile_xml(text).nodes[0].fields[0][1].vtype

    def test_type_size_gives_the_field(self):
        vt = self.vtype(calibrated_mission(calibrated()))
        self.assertEqual((vt.kind, vt.width, vt.signed), ("int", 32, True))
        self.assertEqual(vt.eng_limits, (-(1 << 31), 1 << 31))
        vt = self.vtype(calibrated_mission(calibrated('sizeInBits="16" '
                                                      'signed="false"')))
        self.assertEqual((vt.width, vt.signed, vt.eng_limits),
                         (16, False, (0, 1 << 16)))

    def test_range_through_the_calibrator(self):
        # Raw 0..255 calibrates to -4..123.5, which rounds to at most 124.
        self.assertEqual(
            value_range(self.vtype(calibrated_mission(calibrated()))),
            (-4, 124))
        # A ValidRange on the calibrated value takes precedence.
        vr = '<ValidRange minInclusive="0" maxInclusive="9"/>'
        vt = self.vtype(
            calibrated_mission(calibrated().replace("</IntegerDataEncoding>",
                                                    "</IntegerDataEncoding>"
                                                    + vr)))
        self.assertEqual(vt.valid_range, (0, 9))
        vr = ('<ValidRange minInclusive="0" maxInclusive="9" '
              'validRangeAppliesToCalibrated="false"/>')
        vt = self.vtype(
            calibrated_mission(calibrated().replace("</IntegerDataEncoding>",
                                                    "</IntegerDataEncoding>"
                                                    + vr)))
        self.assertIsNone(vt.valid_range)

    def test_raw_comparison_rejected(self):
        text = mission(tm=f"""
            <ParameterTypeSet>{calibrated()}</ParameterTypeSet>
            <ParameterSet><Parameter name="X" parameterTypeRef="T"/>
            </ParameterSet>
            <ContainerSet>
              <SequenceContainer name="Base" abstract="true">
                <EntryList><ParameterRefEntry parameterRef="X"/></EntryList>
              </SequenceContainer>
              <SequenceContainer name="Derived"><EntryList/>
                <BaseContainer containerRef="Base"><RestrictionCriteria>
                  <Comparison parameterRef="X" value="3"
                    useCalibratedValue="false"/>
                </RestrictionCriteria></BaseContainer>
              </SequenceContainer>
            </ContainerSet>""")
        with self.assertRaises(Unsupported) as cm:
            compile_xml(text)
        self.assertIn("raw comparison on int parameter", str(cm.exception))


def max_field(text: str, value: str) -> str:
    """Puts tcs-xtce.max-field-bytes on a document's first SpaceSystem.

    Args:
        text:  The XTCE document.
        value: The ancillary data's value.

    Returns:
        ``text`` with the ancillary data added.
    """
    at = text.index(">") + 1
    return (text[:at]
            + '<AncillaryDataSet><AncillaryData '
              f'name="tcs-xtce.max-field-bytes">{value}</AncillaryData>'
              '</AncillaryDataSet>' + text[at:])


class MaxFieldBytesTests(unittest.TestCase):
    def array(self, m):
        return next(lc for _, lc in m.nodes[0].fields if lc.name == "Arr").vtype

    def unsupported(self, message: str, *texts: str) -> None:
        with self.assertRaises(Unsupported) as cm:
            compile_xmls(*texts)
        self.assertIn(message, str(cm.exception))

    def test_caps_a_count_without_range(self):
        text = array_mission(dim(fixed(0), dynamic("N")), COUNT_THEN_ARRAY,
                             "U32")
        vt = self.array(compile_xml(max_field(text, "1024")))
        self.assertEqual(vt.enc.max_count, 512)
        self.assertEqual(item_bits(vt), 1024 * 8)

    def test_caps_the_rest_of_the_packet(self):
        text = calibrated_mission(calibrated(),
                                  '<ParameterRefEntry parameterRef="Rest"/>')
        m = compile_xml(max_field(text, " 4294967295 "))
        self.assertEqual(m.nodes[0].fields[0][1].vtype.enc.bits, 4294967295 * 8)

    def test_documents_that_agree(self):
        m = compile_xmls(max_field(TYPES, "100"), HEADER, max_field(APP, "100"))
        self.assertTrue(m.nodes)

    def test_rejected(self):
        nested = mission().replace("</SpaceSystem>",
                                   max_field('<SpaceSystem name="Sub">', "100")
                                   + "</SpaceSystem>" + "</SpaceSystem>")
        for message, texts in (
                ("max-field-bytes 0 is not from 1 to 4294967295",
                 [max_field(mission(), "0")]),
                ("max-field-bytes 4294967296 is not from 1",
                 [max_field(mission(), "4294967296")]),
                ("max-field-bytes '1K' is not an integer",
                 [max_field(mission(), "1K")]),
                ("more than one tcs-xtce.max-field-bytes",
                 [max_field(mission(),
                            '1</AncillaryData><AncillaryData '
                            'name="tcs-xtce.max-field-bytes">2')]),
                ("max-field-bytes differ: dt (100), app (200)",
                 [max_field(TYPES, "100"), HEADER, max_field(APP, "200")]),
                ("only read on a document's root SpaceSystem", [nested])):
            with self.subTest(message):
                self.unsupported(message, *texts)


class RestTests(unittest.TestCase):
    def test_last_value_takes_the_rest(self):
        m = compile_xml(calibrated_mission(calibrated(),
                                           '<ParameterRefEntry '
                                           'parameterRef="Rest"/>'))
        vt = m.nodes[0].fields[0][1].vtype
        self.assertTrue(vt.enc.rest)
        self.assertEqual(vt.enc.bits, MAX_FIELD_BITS)

    def test_fixed_entries_may_follow(self):
        text = calibrated_mission(calibrated(),
                                  '<ParameterRefEntry parameterRef="Rest"/>'
                                  '<ParameterRefEntry parameterRef="X"/>'
                                  '<ParameterRefEntry parameterRef="X"/>')
        e = compile_xml(text).nodes[0].fields[0][1].vtype.enc
        self.assertEqual((e.rest, e.tail), (True, 16))

    def test_must_be_last_before_a_varying_size(self):
        text = calibrated_mission(calibrated(),
                                  '<ParameterRefEntry parameterRef="Rest"/>'
                                  '<ParameterRefEntry parameterRef="Rest"/>')
        with self.assertRaises(Unsupported) as cm:
            compile_xml(text)
        self.assertIn("Rest takes the rest of the packet but is not last",
                      str(cm.exception))

    def test_same_entries_after_in_every_packet(self):
        # Base is a packet itself, with nothing after Rest; Child adds X.
        text = calibrated_mission(calibrated()).replace(
            '<SequenceContainer name="Status"><EntryList><ParameterRefEntry '
            'parameterRef="X"/></EntryList>',
            '<SequenceContainer name="Base"><EntryList><ParameterRefEntry '
            'parameterRef="X"/><ParameterRefEntry parameterRef="Rest"/>'
            '</EntryList></SequenceContainer><SequenceContainer name="Child">'
            '<EntryList><ParameterRefEntry parameterRef="X"/></EntryList>'
            '<BaseContainer containerRef="Base"><RestrictionCriteria>'
            '<Comparison parameterRef="X" value="1"/></RestrictionCriteria>'
            '</BaseContainer>')
        with self.assertRaises(Unsupported) as cm:
            compile_xml(text)
        self.assertIn("Rest takes the rest of the packet, with a different "
                      "size of entries after it in another packet",
                      str(cm.exception))

    def test_not_repeated(self):
        text = calibrated_mission(calibrated(),
                                  '<ParameterRefEntry parameterRef="Rest">'
                                  '<RepeatEntry><Count><FixedValue>2'
                                  '</FixedValue></Count></RepeatEntry>'
                                  '</ParameterRefEntry>')
        with self.assertRaises(Unsupported) as cm:
            compile_xml(text)
        self.assertIn("RepeatEntry on a value that takes the rest of the "
                      "packet", str(cm.exception))


def dim(start: str, end: str) -> str:
    return (f"<Dimension><StartingIndex>{start}</StartingIndex>"
            f"<EndingIndex>{end}</EndingIndex></Dimension>")


def fixed(v: int) -> str:
    return f"<FixedValue>{v}</FixedValue>"


def dynamic(param: str, slope: str = "1", intercept: str = "0") -> str:
    return (f'<DynamicValue><ParameterInstanceRef parameterRef="{param}"/>'
            f'<LinearAdjustment slope="{slope}" intercept="{intercept}"/>'
            '</DynamicValue>')


def array_mission(dims: str, entries: str, count_type: str = "U8",
                  element: str = "U16") -> str:
    """Builds telemetry with arrays.

    An array type ``A`` of ``element``, the parameters ``N`` (a count) and
    ``Arr``, and a container with ``entries``.

    Args:
        dims:       The XML inside A's DimensionList.
        entries:    The XML of the container's entries.
        count_type: The type of N.
        element:    The type of A's elements.

    Returns:
        The XTCE document.
    """
    return mission(tm=f"""
        <ParameterTypeSet>
          <IntegerParameterType name="U8" signed="false">
            <IntegerDataEncoding sizeInBits="8"/>
          </IntegerParameterType>
          <IntegerParameterType name="Small" signed="false">
            <IntegerDataEncoding sizeInBits="8"/>
            <ValidRange minInclusive="0" maxInclusive="5"/>
          </IntegerParameterType>
          <IntegerParameterType name="U32" signed="false">
            <IntegerDataEncoding sizeInBits="32"/>
          </IntegerParameterType>
          <IntegerParameterType name="U16" signed="false">
            <IntegerDataEncoding sizeInBits="16"/>
          </IntegerParameterType>
          <FloatParameterType name="F32">
            <FloatDataEncoding sizeInBits="32"/>
          </FloatParameterType>
          <ArrayParameterType name="A" arrayTypeRef="{element}">
            <DimensionList>{dims}</DimensionList>
          </ArrayParameterType>
          <ArrayParameterType name="AA" arrayTypeRef="A">
            <DimensionList>{dim(fixed(0), fixed(1))}</DimensionList>
          </ArrayParameterType>
        </ParameterTypeSet>
        <ParameterSet>
          <Parameter name="N" parameterTypeRef="{count_type}"/>
          <Parameter name="F" parameterTypeRef="F32"/>
          <Parameter name="Arr" parameterTypeRef="A"/>
          <Parameter name="Nested" parameterTypeRef="AA"/>
        </ParameterSet>
        <ContainerSet>
          <SequenceContainer name="P"><EntryList>{entries}</EntryList>
          </SequenceContainer>
        </ContainerSet>""")


COUNT_THEN_ARRAY = ('<ParameterRefEntry parameterRef="N"/>'
                    '<ArrayParameterRefEntry parameterRef="Arr"/>')


class ArrayTests(unittest.TestCase):
    def array(self, m):
        return next(lc for _, lc in m.nodes[0].fields if lc.name == "Arr").vtype

    def unsupported(self, text: str, message: str) -> None:
        with self.assertRaises(Unsupported) as cm:
            compile_xml(text)
        self.assertIn(message, str(cm.exception))

    def test_fixed_dimensions_multiply(self):
        dims = dim(fixed(0), fixed(1)) + dim(fixed(2), fixed(4))
        m = compile_xml(
            array_mission(dims, '<ArrayParameterRefEntry parameterRef="Arr"/>'))
        self.assertEqual(self.array(m).enc.count, 6)
        self.assertEqual(m.nodes[0].max_bytes, 12)

    def test_count_bounded_by_valid_range(self):
        m = compile_xml(
            array_mission(dim(fixed(0), dynamic("N", intercept="-1")),
                          COUNT_THEN_ARRAY, "Small"))
        d = self.array(m).enc.count
        self.assertEqual((d.slope, d.intercept, d.max, d.local.name),
                         (1.0, 0.0, 5, "N"))
        self.assertEqual(m.nodes[0].max_bytes, 1 + 5 * 2)

    def test_dynamic_dimension_times_fixed_ones(self):
        dims = dim(fixed(0), dynamic("N")) + dim(fixed(0), fixed(2))
        m = compile_xml(array_mission(dims, COUNT_THEN_ARRAY, "Small"))
        d = self.array(m).enc.count
        # N + 1 rows of 3, with N at most 5: the intercept holds the + 1.
        self.assertEqual((d.intercept, d.factor, d.max, d.max_total),
                         (1.0, 3, 6, 18))

    def test_count_without_range_is_capped(self):
        m = compile_xml(array_mission(dim(fixed(0), dynamic("N")),
                                      COUNT_THEN_ARRAY, "U32"))
        vt = self.array(m)
        self.assertEqual(vt.enc.max_count, MAX_FIELD_BITS // 16)
        self.assertEqual(item_bits(vt), MAX_FIELD_BITS)

    def test_rejected(self):
        cases = [
            (dim(fixed(0), dynamic("N")),
             '<ArrayParameterRefEntry parameterRef="Arr"/><ParameterRefEntry '
             'parameterRef="N"/>', "U8", "which is not decoded before it"),
            (dim(fixed(0), dynamic("F")),
             '<ParameterRefEntry parameterRef="F"/><ArrayParameterRefEntry '
             'parameterRef="Arr"/>', "U8", "size or count from float value"),
            (dim(dynamic("N"), fixed(3)), COUNT_THEN_ARRAY, "U8",
             "StartingIndex from DynamicValue"),
            (dim(fixed(0), dynamic("N")) * 2, COUNT_THEN_ARRAY, "U8",
             "more than one dimension from DynamicValue"),
            (dim(fixed(3), fixed(1)), COUNT_THEN_ARRAY, "U8",
             "EndingIndex 1 is before StartingIndex 3")]
        for dims, entries, count_type, message in cases:
            with self.subTest(message):
                self.unsupported(array_mission(dims, entries, count_type),
                                 message)

    def test_repeat_entry(self):
        entries = ('<ParameterRefEntry parameterRef="N"/><ParameterRefEntry '
                   f'parameterRef="N"><RepeatEntry><Count>{dynamic("N")}'
                   f'</Count><Offset>{fixed(2)}</Offset></RepeatEntry>'
                   '</ParameterRefEntry>')
        m = compile_xml(array_mission(dim(fixed(0), fixed(0)), entries,
                                      "Small"))
        rep = m.nodes[0].fields[0][1].vtype
        self.assertEqual((rep.kind, rep.enc.gap, rep.enc.count.max),
                         ("array", 2, 5))
        self.assertEqual(m.nodes[0].max_bytes, (8 + 5 * 8 + 4 * 2 + 7) // 8)
        once = ('<ParameterRefEntry parameterRef="N"><RepeatEntry><Count>'
                '<FixedValue>1</FixedValue></Count></RepeatEntry>'
                '</ParameterRefEntry>')
        m = compile_xml(array_mission(dim(fixed(0), fixed(0)), once))
        self.assertEqual(m.nodes[0].fields[0][1].vtype.kind, "int")
        # A repeated array is an array of arrays.
        on_array = ('<ArrayParameterRefEntry parameterRef="Arr"><RepeatEntry>'
                    '<Count><FixedValue>2</FixedValue></Count></RepeatEntry>'
                    '</ArrayParameterRefEntry>')
        vt = compile_xml(array_mission(dim(fixed(0), fixed(0)),
                                       on_array)).nodes[0].fields[0][1].vtype
        self.assertEqual((vt.kind, vt.enc.count, vt.elem.kind),
                         ("array", 2, "array"))

    def test_arrays_of_arrays(self):
        vt = compile_xml(
            array_mission(
                dim(fixed(0), fixed(1)),
                '<ArrayParameterRefEntry '
                'parameterRef="Nested"/>')).nodes[0].fields[0][1].vtype
        self.assertEqual((vt.kind, vt.elem.kind), ("array", "array"))

    def test_criterion_on_array(self):
        text = array_mission(dim(fixed(0), fixed(0)),
                             '<ArrayParameterRefEntry '
                             'parameterRef="Arr"/>').replace(
            # P's own closing tag, which follows, closes the new P.
            '<SequenceContainer name="P"><EntryList><ArrayParameterRefEntry '
            'parameterRef="Arr"/></EntryList>',
            '<SequenceContainer name="B" abstract="true"><EntryList>'
            '<ArrayParameterRefEntry parameterRef="Arr"/></EntryList>'
            '</SequenceContainer><SequenceContainer name="P"><EntryList/>'
            '<BaseContainer containerRef="B"><RestrictionCriteria><Comparison '
            'parameterRef="Arr" value="1"/></RestrictionCriteria>'
            '</BaseContainer>')
        self.unsupported(text, "which is an array")

    def test_c_storage(self):
        m = compile_xml(
            array_mission(dim(fixed(0), dynamic("N", intercept="-1")),
                          COUNT_THEN_ARRAY, "Small"))
        header = emit_c.emit(m, "t.xtce")
        self.assertIn("typedef struct sat_uint16_list5 {", header)
        self.assertIn("uint16_t data[5];", header)


def aggregate_mission(types: str,
                      entries: str = '<ParameterRefEntry '
                                     'parameterRef="Agg"/>') -> str:
    """Builds telemetry with an aggregate parameter ``Agg`` of type ``A``.

    Args:
        types:   More parameter types' XML, which must define A.
        entries: The XML of container P's entries.

    Returns:
        The XTCE document.
    """
    return mission(tm=f"""
        <ParameterTypeSet>
          <IntegerParameterType name="U8" signed="false">
            <IntegerDataEncoding sizeInBits="8"/>
          </IntegerParameterType>
          {types}
        </ParameterTypeSet>
        <ParameterSet>
          <Parameter name="N" parameterTypeRef="U8"/>
          <Parameter name="Agg" parameterTypeRef="A"/>
        </ParameterSet>
        <ContainerSet>
          <SequenceContainer name="P"><EntryList>{entries}</EntryList>
          </SequenceContainer>
        </ContainerSet>""")


def aggregate(name: str, *members: tuple[str, str]) -> str:
    body = "".join(f'<Member name="{n}" typeRef="{t}"/>' for n, t in members)
    return (f'<AggregateParameterType name="{name}"><MemberList>{body}'
            '</MemberList></AggregateParameterType>')


class AggregateTests(unittest.TestCase):
    def test_nested_members(self):
        types = (aggregate("A", ("Count", "U8"), ("type", "U8"), ("Inner", "B"))
                 + aggregate("B", ("X", "U8")))
        m = compile_xml(aggregate_mission(types))
        vt = m.nodes[0].fields[0][1].vtype
        self.assertEqual([x.ident for x in vt.enc.members],
                         ["count", "type_", "inner"])
        # Inner aggregates come first, so that each is declared before use.
        self.assertEqual([a.ident for a in m.aggregates], ["B", "A"])
        self.assertEqual(m.nodes[0].max_bytes, 3)

    def test_rejected(self):
        cases = [(aggregate("A", ("X", "B")) + aggregate("B", ("Y", "A")),
                  "aggregate contains itself"),
                 (aggregate("A", ("Bytes", "Blob"))
                  + '<BinaryParameterType name="Blob"><BinaryDataEncoding>'
                    '<SizeInBits>' + dynamic("N")
                  + "</SizeInBits></BinaryDataEncoding></BinaryParameterType>",
                  "member Bytes has a size that another value gives")]
        for types, message in cases:
            with self.subTest(message), self.assertRaises(Unsupported) as cm:
                compile_xml(
                    aggregate_mission(types,
                                      '<ParameterRefEntry parameterRef="N"/>'
                                      '<ParameterRefEntry '
                                      'parameterRef="Agg"/>'))
            self.assertIn(message, str(cm.exception))

    def test_size_from_an_earlier_member(self):
        types = (aggregate("A", ("N", "U8"), ("Data", "Blob"))
                 + '<BinaryParameterType name="Blob"><BinaryDataEncoding>'
                   '<SizeInBits>' + dynamic("N", slope="8")
                 + "</SizeInBits></BinaryDataEncoding></BinaryParameterType>")
        m = compile_xml(aggregate_mission(types))
        data = m.aggregates[0].enc.members[1].vtype
        self.assertEqual((data.dyn.member, data.dyn.local, data.dyn.max),
                         ("n", None, 255))
        # Only earlier members count.
        types = types.replace('<Member name="N" typeRef="U8"/><Member '
                              'name="Data" typeRef="Blob"/>',
                              '<Member name="Data" typeRef="Blob"/><Member '
                              'name="N" typeRef="U8"/>')
        with self.assertRaises(Unsupported) as cm:
            compile_xml(aggregate_mission(types))
        self.assertIn("N is not an earlier member", str(cm.exception))

    def test_criterion_on_aggregate(self):
        types = aggregate("A", ("X", "U8"))
        text = aggregate_mission(types).replace(
            # P's own closing tag, which follows, closes the new P.
            '<SequenceContainer name="P"><EntryList><ParameterRefEntry '
            'parameterRef="Agg"/></EntryList>',
            '<SequenceContainer name="B" abstract="true"><EntryList>'
            '<ParameterRefEntry parameterRef="Agg"/></EntryList>'
            '</SequenceContainer><SequenceContainer name="P"><EntryList/>'
            '<BaseContainer containerRef="B"><RestrictionCriteria><Comparison '
            'parameterRef="Agg" value="1"/></RestrictionCriteria>'
            '</BaseContainer>')
        with self.assertRaises(Unsupported) as cm:
            compile_xml(text)
        self.assertIn("which is an aggregate", str(cm.exception))


def container_mission(record: str, entry: str) -> str:
    """Builds telemetry where Status includes container Record.

    Args:
        record: The XML inside Record.
        entry:  The XML inside Status's ContainerRefEntry to Record.

    Returns:
        The XTCE document.
    """
    return mission(tm=f"""
        <ParameterTypeSet>
          <IntegerParameterType name="U4" signed="false">
          <IntegerDataEncoding sizeInBits="4"/></IntegerParameterType>
          <IntegerParameterType name="U8" signed="false">
          <IntegerDataEncoding sizeInBits="8"/></IntegerParameterType>
          <StringParameterType name="S"><StringDataEncoding>
          <Variable maxSizeInBits="64">
            <TerminationChar>00</TerminationChar></Variable>
            </StringDataEncoding></StringParameterType>
        </ParameterTypeSet>
        <ParameterSet>
          <Parameter name="N" parameterTypeRef="U8"/>
          <Parameter name="A" parameterTypeRef="U4"/>
          <Parameter name="B" parameterTypeRef="U8"/>
          <Parameter name="Text" parameterTypeRef="S"/>
        </ParameterSet>
        <ContainerSet>
          <SequenceContainer name="Status"><EntryList>
            <ParameterRefEntry parameterRef="N"/>
            <ContainerRefEntry containerRef="Record">{entry}</ContainerRefEntry>
          </EntryList></SequenceContainer>
          <SequenceContainer name="Record" abstract="true">
            {record}
          </SequenceContainer>
        </ContainerSet>""")


REPEAT = f"<RepeatEntry><Count>{dynamic('N')}</Count></RepeatEntry>"


def size(bits: int) -> str:
    return (f"<BinaryEncoding><SizeInBits><FixedValue>{bits}</FixedValue>"
            "</SizeInBits></BinaryEncoding>")


def entries(*refs: str) -> str:
    return "<EntryList>" + "".join(refs) + "</EntryList>"


def ref(name: str, offset: str = "") -> str:
    """Builds a ParameterRefEntry.

    Args:
        name:   The parameter's name.
        offset: "reference:bits" for a LocationInContainerInBits with that
                referenceLocation and fixed offset; "" for none.

    Returns:
        The entry's XML.
    """
    loc = ('<LocationInContainerInBits '
           f'referenceLocation="{offset.split(":")[0]}">'
           f"<FixedValue>{offset.split(':')[1]}</FixedValue>"
           "</LocationInContainerInBits>" if offset else "")
    return f'<ParameterRefEntry parameterRef="{name}">{loc}</ParameterRefEntry>'


class ContainerTests(unittest.TestCase):
    def test_repeated_container_is_a_list_of_aggregates(self):
        record = entries(ref("A"), ref("B", "previousEntry:3"))
        m = compile_xml(container_mission(size(24) + record, REPEAT))
        vt = m.nodes[0].fields[0][1].vtype
        self.assertEqual(vt.kind, "array")
        agg = vt.elem.enc
        self.assertEqual([(x.ident, x.skip) for x in agg.members],
                         [("a", 0), ("b", 3)])
        self.assertEqual(agg.pad, 9)  # 4 + 3 + 8 = 15 of 24 bits
        self.assertEqual(item_bits(vt.elem), 24)
        self.assertEqual(m.aggregates[0].ident, "Record")

    def test_container_start_locations(self):
        record = entries(ref("A"), ref("B", "containerStart:8"))
        m = compile_xml(container_mission(record, REPEAT))
        agg = m.nodes[0].fields[0][1].vtype.elem.enc
        self.assertEqual(([x.skip for x in agg.members], agg.pad), ([0, 4], 0))

    def test_fixed_size_pads_an_included_container(self):
        m = compile_xml(container_mission(size(16) + entries(ref("A")), ""))
        self.assertIsInstance(m.nodes[0].items[-1], Pad)
        self.assertEqual(m.nodes[0].items[-1].bits, 12)
        self.assertEqual(m.nodes[0].max_bytes, 3)

    def test_size_with_entries_whose_size_varies_is_filled(self):
        m = compile_xml(container_mission(size(64) + entries(ref("Text")), ""))
        fill = m.nodes[0].items[-1]
        self.assertIsInstance(fill, Fill)
        self.assertEqual(fill.size, 64)
        self.assertEqual(m.nodes[0].max_bytes, 9)  # N, then the 64 bits

    def test_end_of_a_container_whose_size_a_value_gives(self):
        # Record is N bits, and B ends at its end: a Seek from Record's
        # start of N bits less B's 8, which must not go back.
        record = (f"<BinaryEncoding><SizeInBits>{dynamic('N')}</SizeInBits>"
                  "</BinaryEncoding>"
                  + entries(ref("A"), ref("B", "containerEnd:0")))
        items = compile_xml(container_mission(record, "")).nodes[0].items
        seek = next(it for it in items if isinstance(it, Seek))
        self.assertEqual((seek.back, seek.ahead), (8, True))
        self.assertEqual(seek.offset.local.name, "N")

    def test_end_of_a_container_by_a_count(self):
        # N copies of B end the 64-bit Record: a Seek 64 bits from its
        # start, less N bytes, which must not go back.
        end = ('<ParameterRefEntry parameterRef="B">'
               '<LocationInContainerInBits referenceLocation="containerEnd">'
               f'<FixedValue>0</FixedValue></LocationInContainerInBits>{REPEAT}'
               '</ParameterRefEntry>')
        items = compile_xml(container_mission(size(64) + entries(ref("A"), end),
                                              "")).nodes[0].items
        seek = next(it for it in items if isinstance(it, Seek))
        self.assertEqual((seek.offset, seek.back, seek.ahead), (64, 0, True))
        self.assertEqual((seek.span.count.local.name, seek.span.each), ("N", 8))

    def test_rejected(self):
        for record, entry, why in (
                (size(64) + entries(ref("A"), ref("Text", "containerEnd:0")),
                 "",
                 "a location from the container's end for Text, whose size "
                 "varies"),
                (entries(ref("Text"), ref("B", "containerEnd:0")), "",
                 "a location from the end of an included container whose size "
                 "varies"),
                (size(8) + entries(ref("A"), ref("B")), "",
                 "entries take 12 bits, more than the container size 8"),
                (size(64) + entries(ref("Text")), REPEAT,
                 "entries whose size or position varies"),
                (entries(ref("A"), ref("B", "previousEntry:-2")), REPEAT,
                 "has entries that overlap"),
                (entries(ref("Text"), ref("A", "containerStart:80")), REPEAT,
                 "location after a value whose size varies"),
                # Sizes inside a copy can't come from values outside it.
                (entries(f'<ParameterRefEntry parameterRef="B">{REPEAT}'
                         '</ParameterRefEntry>'), REPEAT,
                 "size or count from /Sat/N, which is not decoded before it")):
            with self.subTest(why=why):
                text = container_mission(record, entry)
                with self.assertRaises(Unsupported) as cm:
                    compile_xml(text)
                self.assertIn(why, str(cm.exception))



def packet_mission(*refs: str, record: str = "") -> str:
    """Builds container_mission with Status's own entries.

    Args:
        *refs:  The XML of Status's entries after N.
        record: Record's contents; when given, Record is included before
                ``refs``.

    Returns:
        The XTCE document.
    """
    inc = '<ContainerRefEntry containerRef="Record"/>' if record else ""
    return container_mission(record or entries(),
                             "").replace(
        '<ContainerRefEntry containerRef="Record"></ContainerRefEntry>',
        inc + "".join(refs))


class AggregateCalibratorTests(unittest.TestCase):
    def test_member_calibrator_uses_an_earlier_value(self):
        text = mission(tm="""
            <ParameterTypeSet>
              <IntegerParameterType name="U8" signed="false">
              <IntegerDataEncoding sizeInBits="8"/></IntegerParameterType>
              <FloatParameterType name="F"><IntegerDataEncoding sizeInBits="8">
                <ContextCalibratorList><ContextCalibrator><ContextMatch>
                <Comparison parameterRef="M" value="1"/></ContextMatch>
                <Calibrator><PolynomialCalibrator>
                <Term coefficient="2" exponent="1"/></PolynomialCalibrator>
                </Calibrator></ContextCalibrator></ContextCalibratorList>
              </IntegerDataEncoding></FloatParameterType>
              <AggregateParameterType name="A"><MemberList>
                <Member name="V" typeRef="F"/></MemberList>
              </AggregateParameterType>
            </ParameterTypeSet>
            <ParameterSet>
              <Parameter name="M" parameterTypeRef="U8"/>
              <Parameter name="P" parameterTypeRef="A"/>
              <Parameter name="Q" parameterTypeRef="A"/>
            </ParameterSet>
            <ContainerSet>
              <SequenceContainer name="S"><EntryList>
                <ParameterRefEntry parameterRef="M"/>
                <ParameterRefEntry parameterRef="P"/>
                <ParameterRefEntry parameterRef="Q"/>
              </EntryList></SequenceContainer>
            </ContainerSet>""")
        m = compile_xml(text)
        p, q = (lc.vtype for _, lc in m.nodes[0].fields[1:])
        # Each use is bound to the packet's M; both are the one type A.
        self.assertTrue(p.enc.members[0].vtype.calibrator.bound)
        self.assertIs(p.origin, q.origin)
        self.assertEqual([vt.ident for vt in m.aggregates], [p.ident])
        self.assertEqual(q.ident, p.ident)


def transform_mission(encoding: str = "", entries: str = "",
                      bits: int = 32) -> str:
    """Builds telemetry with a transformed binary.

    A binary B, transformed by F and T, and a container P.

    Args:
        encoding: P's BinaryEncoding element, or "" for none.
        entries:  The XML of P's entries.
        bits:     B's size on the wire, in bits.

    Returns:
        The XTCE document.
    """
    return mission(tm=f"""
        <ParameterTypeSet>
          <IntegerParameterType name="U8" signed="false">
          <IntegerDataEncoding sizeInBits="8"/></IntegerParameterType>
          <BinaryParameterType name="Blob"><BinaryDataEncoding>
            <SizeInBits><FixedValue>{bits}</FixedValue></SizeInBits>
            <FromBinaryTransformAlgorithm name="F"/>
            <ToBinaryTransformAlgorithm name="T"/>
          </BinaryDataEncoding></BinaryParameterType>
        </ParameterTypeSet>
        <ParameterSet>
          <Parameter name="A" parameterTypeRef="U8"/>
          <Parameter name="B" parameterTypeRef="Blob"/>
        </ParameterSet>
        <ContainerSet>
          <SequenceContainer name="P">{encoding}<EntryList>{entries}
          </EntryList></SequenceContainer>
        </ContainerSet>""")


TRANSFORMS = ("<FromBinaryTransformAlgorithm name='Unzip'/>"
              "<ToBinaryTransformAlgorithm name='Zip'/>")


def dialect(text: str, value: str) -> str:
    """Puts tcs-xtce.dialect on a document's first SpaceSystem.

    Args:
        text:  The XTCE document.
        value: The dialect.

    Returns:
        ``text`` with the ancillary data added.
    """
    at = text.index(">") + 1
    return (text[:at]
            + '<AncillaryDataSet><AncillaryData '
              f'name="tcs-xtce.dialect">{value}</AncillaryData>'
              '</AncillaryDataSet>' + text[at:])


class TransformTests(unittest.TestCase):
    B_A = '<ParameterRefEntry parameterRef="B"/><ParameterRefEntry ' \
          'parameterRef="A"/>'

    def test_binary_value(self):
        n = compile_xml(transform_mission(entries=self.B_A)).nodes[0]
        e = n.items[0].local.vtype.enc
        self.assertIsInstance(e, XformEnc)
        self.assertEqual((e.raw, e.decode.name, e.encode.name, e.time),
                         (4, "F", "T", False))
        self.assertEqual((e.decode.kind, e.encode.kind),
                         ("binary_from", "binary_to"))
        self.assertEqual([lc.name for _, lc in n.fields], ["B", "A"])

    def test_rest_of_packet(self):
        # A size of -1 offers the decoding transform the rest of the
        # packet: in the standard reading, nothing may follow.
        text = transform_mission(entries=self.B_A, bits=-1)
        with self.assertRaises(Unsupported) as cm:
            compile_xml(text)
        self.assertIn("B takes the rest of the packet but is not last",
                      str(cm.exception))
        n = compile_xml(
            transform_mission(entries='<ParameterRefEntry parameterRef="B"/>',
                              bits=-1)).nodes[0]
        e = n.items[0].local.vtype.enc
        self.assertEqual((e.raw, e.sized), (None, False))

    def test_yamcs_sized(self):
        # In the Yamcs dialect the decoding transform says how much of the
        # rest it takes, so entries may follow.
        n = compile_xml(dialect(transform_mission(entries=self.B_A, bits=-1),
                                "yamcs")).nodes[0]
        e = n.items[0].local.vtype.enc
        self.assertEqual((e.raw, e.sized), (None, True))
        self.assertEqual([lc.name for _, lc in n.fields], ["B", "A"])

    def test_dialects(self):
        with self.assertRaises(Unsupported) as cm:
            compile_xml(dialect(transform_mission(entries=self.B_A), "xtce"))
        self.assertIn("tcs-xtce.dialect 'xtce' is not one of yamcs",
                      str(cm.exception))
        sub = dialect('<SpaceSystem name="Sub"></SpaceSystem>', "yamcs")
        with self.assertRaises(Unsupported) as cm:
            compile_xml(mission().replace("</SpaceSystem>",
                                          sub + "</SpaceSystem>"))
        self.assertIn("is only read on a document's root SpaceSystem",
                      str(cm.exception))

    def test_time(self):
        text = mission(tm="""
            <ParameterTypeSet>
              <AbsoluteTimeParameterType name="Stamp"><Encoding>
                <BinaryDataEncoding>
                  <SizeInBits><FixedValue>32</FixedValue></SizeInBits>
                  <FromBinaryTransformAlgorithm name="MsFrom"/>
                  <ToBinaryTransformAlgorithm name="MsTo"/>
                </BinaryDataEncoding></Encoding>
                <ReferenceTime><Epoch>TAI</Epoch></ReferenceTime>
              </AbsoluteTimeParameterType>
            </ParameterTypeSet>
            <ParameterSet>
              <Parameter name="T" parameterTypeRef="Stamp"/>
            </ParameterSet>
            <ContainerSet>
              <SequenceContainer name="P"><EntryList>
                <ParameterRefEntry parameterRef="T"/>
              </EntryList></SequenceContainer>
            </ContainerSet>""")
        vt = compile_xml(text).nodes[0].items[0].local.vtype
        self.assertEqual((vt.kind, vt.enc.raw, vt.enc.time), ("float", 4, True))
        self.assertEqual((vt.enc.decode.kind, vt.enc.encode.kind),
                         ("time_from", "time_to"))

    def test_container(self):
        n = compile_xml(
            transform_mission(f"<BinaryEncoding>{TRANSFORMS}</BinaryEncoding>",
                              '<ParameterRefEntry parameterRef="A"/>')).nodes[0]
        (t,) = n.items
        self.assertEqual((t.raw, t.cap, t.decode.name), (None, 1, "Unzip"))
        self.assertEqual([lc.name for _, lc in n.fields], ["A"])

    def test_one_way(self):
        # Decoding works, and encoding fails with NotInvertible.
        one_way = TRANSFORMS.split("<To")[0]
        n = compile_xml(
            transform_mission(f"<BinaryEncoding>{one_way}</BinaryEncoding>",
                              '<ParameterRefEntry parameterRef="A"/>')).nodes[0]
        self.assertEqual((n.items[0].decode.name, n.items[0].encode),
                         ("Unzip", None))

    def test_conditional(self):
        # A transformed value may be optional.
        n = compile_xml(
            transform_mission(
                entries='<ParameterRefEntry parameterRef="A"/>'
                        '<ParameterRefEntry parameterRef="B"><IncludeCondition>'
                        '<Comparison parameterRef="A" value="1"/>'
                        '</IncludeCondition></ParameterRefEntry>')).nodes[0]
        self.assertTrue(n.fields[1][1].optional)

    @staticmethod
    def with_q(text: str,
               size: str = "<SizeInBits><FixedValue>16</FixedValue>"
                           "</SizeInBits>") -> str:
        """Adds a container Q of A, transformed, before container P.

        Args:
            text: The XTCE document, from transform_mission.
            size: The XML of Q's size, inside its BinaryEncoding.

        Returns:
            ``text`` with Q added.
        """
        return text.replace(
            '<SequenceContainer name="P">',
            f'<SequenceContainer name="Q"><BinaryEncoding>{size}{TRANSFORMS}'
            '</BinaryEncoding><EntryList><ParameterRefEntry parameterRef="A"/>'
            '</EntryList></SequenceContainer><SequenceContainer name="P">')

    def test_conditional_container(self):
        # So may a transformed container.
        text = self.with_q(
            transform_mission(
                entries='<ParameterRefEntry parameterRef="A"/>'
                        '<ContainerRefEntry containerRef="Q"><IncludeCondition>'
                        '<Comparison parameterRef="A" value="1"/>'
                        '</IncludeCondition></ContainerRefEntry>'))
        p = next(n for n in compile_xml(text).nodes if n.name == "P")
        cond = p.items[-1]
        self.assertIsInstance(cond, Cond)
        self.assertIsInstance(cond.items[-1], Transform)
        self.assertTrue(p.fields[1][1].optional)

    REPEAT = ('<ParameterRefEntry parameterRef="A"/><ContainerRefEntry '
              'containerRef="Q"><RepeatEntry><Count><DynamicValue>'
              '<ParameterInstanceRef parameterRef="A"/></DynamicValue></Count>'
              '</RepeatEntry></ContainerRefEntry>')

    def test_repeated_container(self):
        # Each copy is transformed: 2 bytes on the wire, from which A is
        # read.
        text = self.with_q(transform_mission(entries=self.REPEAT))
        p = next(n for n in compile_xml(text).nodes if n.name == "P")
        copy = p.items[-1].local.vtype.enc.elem
        x = copy.enc.transform
        self.assertEqual((x.raw, x.cap, x.decode.name, x.encode.name),
                         (2, 1, "Unzip", "Zip"))
        self.assertEqual([m.name for m in copy.enc.members], ["A"])

    def test_included_in_a_repeated_container(self):
        # R, repeated, holds B and then Q: each copy has Q as a member,
        # transformed as a repeated Q would be.
        text = self.with_q(
            transform_mission(entries=self.REPEAT.replace(
                                  '"Q"', '"R"'))).replace(
            '<SequenceContainer name="P">',
            '<SequenceContainer name="R"><EntryList><ParameterRefEntry '
            'parameterRef="B"/><ContainerRefEntry containerRef="Q"/>'
            '</EntryList></SequenceContainer><SequenceContainer name="P">')
        p = next(n for n in compile_xml(text).nodes if n.name == "P")
        copy = p.items[-1].local.vtype.enc.elem
        self.assertEqual([m.name for m in copy.enc.members], ["B", "Q"])
        q = copy.enc.members[1].vtype
        x = q.enc.transform
        self.assertEqual((x.raw, x.cap, x.decode.name, x.encode.name),
                         (2, 1, "Unzip", "Zip"))
        self.assertEqual([m.name for m in q.enc.members], ["A"])

    SUM = ('<ErrorDetectCorrect><Checksum name="sum8" parameterRef="A"/>'
           '</ErrorDetectCorrect>')

    def test_check_covers_the_bytes_before_the_transform(self):
        # The check is verified at the end of the transformed items, which
        # are the bytes before the transform, not with the packet's.
        n = compile_xml(transform_mission(f"<BinaryEncoding>{self.SUM}"
                                          f"{TRANSFORMS}</BinaryEncoding>",
                                          '<ParameterRefEntry '
                                          'parameterRef="A"/>')).nodes[0]
        self.assertIsInstance(n.items[0], Transform)
        self.assertIsInstance(n.items[0].items[-1], Verify)
        self.assertEqual(n.checks, [])

    def test_entry_from_the_end_of_its_bytes(self):
        n = compile_xml(transform_mission(f"<BinaryEncoding>{TRANSFORMS}"
                                          "</BinaryEncoding>",
                                          ref("A", "containerEnd:8"))).nodes[0]
        kinds = [type(it).__name__ for it in n.items[0].items]
        self.assertEqual(kinds, ["Mark", "TailSeek", "Value", "TailPad"])

    @staticmethod
    def with_length(text: str) -> str:
        """Adds parameter L, a length field of the bytes after it.

        Args:
            text: The XTCE document, from transform_mission.

        Returns:
            ``text`` with L added.
        """
        return text.replace('<Parameter name="A" parameterTypeRef="U8"/>',
                            '<Parameter name="A" parameterTypeRef="U8"/>'
                            '<Parameter name="L" parameterTypeRef="U8">'
                            '<AncillaryDataSet><AncillaryData '
                            'name="tcs-xtce.length">0</AncillaryData>'
                            '</AncillaryDataSet></Parameter>')

    L_A = ('<ParameterRefEntry parameterRef="L"/><ParameterRefEntry '
           'parameterRef="A"/>')

    def test_length_field_counts_the_bytes_before_the_transform(self):
        # L, in a transformed packet, is its Transform's, not the
        # packet's.
        n = compile_xml(self.with_length(transform_mission("<BinaryEncoding>"
                                                           f"{TRANSFORMS}"
                                                           "</BinaryEncoding>",
                                                           self.L_A))).nodes[0]
        (t,) = n.items
        self.assertEqual(t.length.name, "L")
        self.assertIsNone(n.length_field())

    def test_included_length_field_measures_the_bytes_before(self):
        # L, in included container Q, measures Q inside its Transform.
        text = self.with_length(
            self.with_q(
                transform_mission(
                    entries='<ContainerRefEntry '
                            'containerRef="Q"/>'))).replace(
            '<EntryList><ParameterRefEntry parameterRef="A"/>',
            f"<EntryList>{self.L_A}", 1)
        p = next(n for n in compile_xml(text).nodes if n.name == "P")
        t = next(it for it in p.items if isinstance(it, Transform))
        self.assertIsNone(t.length)
        self.assertIsInstance(t.items[-1], LengthEnd)
        self.assertEqual(t.items[-1].local.name, "L")
        self.assertIsNone(p.length_field())

    def test_two_length_fields_in_transformed_data(self):
        text = self.with_length(
            transform_mission(f"<BinaryEncoding>{TRANSFORMS}</BinaryEncoding>",
                              self.L_A
                              + '<ParameterRefEntry parameterRef="L"/>'))
        with self.assertRaises(Unsupported) as cm:
            compile_xml(text)
        self.assertIn("more than one packet length field in a container with "
                      "transform algorithms", str(cm.exception))

    @staticmethod
    def outside_check(edc: str, entries: str, after: str = "") -> str:
        """Builds P with a check of its own on A, which is in Q,
        transformed and 2 bytes on the wire, after ``entries``.

        Args:
            edc:     P's ErrorDetectCorrect element.
            entries: The XML of P's entries before Q.
            after:   The XML of P's entries after Q.

        Returns:
            The XTCE document, with U8 parameter C and length field L.
        """
        return TransformTests.with_length(
            TransformTests.with_q(
                transform_mission(f"<BinaryEncoding>{edc}</BinaryEncoding>",
                                  entries
                                  + '<ContainerRefEntry containerRef="Q"/>'
                                  + after))).replace(
            '<Parameter name="L"',
            '<Parameter name="C" parameterTypeRef="U8"/><Parameter name="L"')

    C = '<ParameterRefEntry parameterRef="C"/>'

    @staticmethod
    def sum_end(bits: int) -> str:
        return ('<ErrorDetectCorrect><Checksum name="sum8" parameterRef="A" '
                f'reference="end" bitsFromReference="{bits}"/>'
                '</ErrorDetectCorrect>')

    def test_outside_check_of_a_field_inside(self):
        # P's check covers its bytes up to Q, which holds A: Q's transform
        # computes it first, from a Mark where Q starts on the wire.
        for edc in (self.SUM, self.sum_end(16), self.sum_end(24)):
            with self.subTest(edc=edc):
                p = next(n
                         for n in compile_xml(self.outside_check(edc,
                                                                 self.C)).nodes
                         if n.name == "P")
                (c,) = p.checks
                mark, t = p.items[-2:]
                self.assertEqual((mark.id, t.patches), (c.stop, [c]))
                self.assertEqual(c.stop_back, 8 if "24" in edc else 0)

    @staticmethod
    def sum_from(bits: int) -> str:
        return ('<ErrorDetectCorrect><Checksum name="sum8" parameterRef="A" '
                f'bitsFromReference="{bits}"/></ErrorDetectCorrect>')

    def test_check_of_bits_after_transformed_data(self):
        # P's check skips C and Q, which holds A, and covers the C after
        # them: Q keeps its bytes before the transform, and the check puts
        # A in them once P is written. Room for them, and for what the
        # transform makes of them, comes after the packet.
        p = next(n
                 for n in compile_xml(self.outside_check(self.sum_from(24),
                                                         self.C, self.C)).nodes
                 if n.name == "P")
        (c,) = p.checks
        t = c.late
        self.assertIsInstance(t, Transform)
        self.assertTrue(t.keeps)
        self.assertEqual((c.stop, t.patches), (None, []))
        self.assertEqual(p.max_bytes, 4 + t.cap + t.out_cap)

    def test_outside_check_rejected(self):
        for edc, entries, why in (
                (self.sum_end(8), self.C, "which run into it"),
                (self.SUM, '<ParameterRefEntry parameterRef="L"/>',
                 "among them L, which encoding fills in after the transform"),
                # Past C and into Q.
                (self.sum_from(16), self.C, "which run into it")):
            with self.subTest(why=why):
                with self.assertRaises(Unsupported) as cm:
                    compile_xml(self.outside_check(edc, entries))
                self.assertIn("checksum field A is in transformed data, but "
                              f"its check covers bytes outside it, {why}",
                              str(cm.exception))

    def test_repeated_container_needs_a_size(self):
        text = self.with_q(transform_mission(entries=self.REPEAT), size="")
        with self.assertRaises(Unsupported) as cm:
            compile_xml(text)
        self.assertIn("repeated container /Sat/Q has transform algorithms and "
                      "no SizeInBits of whole bytes", str(cm.exception))


class YamcsDialectTests(unittest.TestCase):
    REPEATED = mission(tm="""
        <ParameterTypeSet>
          <IntegerParameterType name="U8" signed="false">
          <IntegerDataEncoding sizeInBits="8"/></IntegerParameterType>
          <BinaryParameterType name="Data"><BinaryDataEncoding><SizeInBits>
            <DynamicValue><ParameterInstanceRef parameterRef="L"/>
            <LinearAdjustment slope="8"/></DynamicValue>
          </SizeInBits></BinaryDataEncoding></BinaryParameterType>
        </ParameterTypeSet>
        <ParameterSet>
          <Parameter name="N" parameterTypeRef="U8"/>
          <Parameter name="L" parameterTypeRef="U8"/>
          <Parameter name="D" parameterTypeRef="Data"/>
        </ParameterSet>
        <ContainerSet>
          <SequenceContainer name="E"><EntryList>
            <ParameterRefEntry parameterRef="L"/>
            <ParameterRefEntry parameterRef="D"/>
          </EntryList></SequenceContainer>
          <SequenceContainer name="P"><EntryList>
            <ParameterRefEntry parameterRef="N"/>
            <ContainerRefEntry containerRef="E"><RepeatEntry><Count>
              <DynamicValue><ParameterInstanceRef parameterRef="N"/>
              </DynamicValue></Count></RepeatEntry></ContainerRefEntry>
          </EntryList></SequenceContainer>
        </ContainerSet>""")

    def test_size_from_an_entry_of_each_copy(self):
        m = compile_xml(self.REPEATED)
        p = next(n for n in m.nodes if n.name == "P")
        copy = p.fields[-1][1].vtype.enc.elem
        d = copy.enc.members[1].vtype.dyn
        self.assertEqual((d.member, d.max), ("l", 255))

    LABEL = mission(tm="""
        <ParameterTypeSet>
          <StringParameterType name="Label"><StringDataEncoding>
            <Variable maxSizeInBits="64"><DynamicValue>
              <ParameterInstanceRef parameterRef="_yamcs_ignore"/>
            </DynamicValue><LeadingSize sizeInBitsOfSizeTag="8"/>
            </Variable></StringDataEncoding></StringParameterType>
        </ParameterTypeSet>
        <ParameterSet>
          <Parameter name="S" parameterTypeRef="Label"/>
        </ParameterSet>
        <ContainerSet>
          <SequenceContainer name="P"><EntryList>
            <ParameterRefEntry parameterRef="S"/>
          </EntryList></SequenceContainer>
        </ContainerSet>""")

    def test_yamcs_ignore(self):
        with self.assertRaises(Unsupported) as cm:
            compile_xml(self.LABEL)
        self.assertIn("unresolved parameter reference '_yamcs_ignore'",
                      str(cm.exception))
        e = compile_xml(dialect(self.LABEL, "yamcs")).nodes[0].fields[0][1]
        self.assertEqual((e.vtype.enc.dyn, e.vtype.enc.size_tag_bits),
                         (None, 8))

    SLASH = mission(tm="""
        <ParameterTypeSet>
          <IntegerParameterType name="U8" signed="false">
          <IntegerDataEncoding sizeInBits="8"/></IntegerParameterType>
          <AggregateParameterType name="H"><MemberList>
            <Member name="V" typeRef="U8"/><Member name="A" typeRef="U8"/>
          </MemberList></AggregateParameterType>
        </ParameterTypeSet>
        <ParameterSet>
          <Parameter name="Hdr" parameterTypeRef="H"/>
          <Parameter name="X" parameterTypeRef="U8"/>
        </ParameterSet>
        <ContainerSet>
          <SequenceContainer name="Base" abstract="true"><EntryList>
            <ParameterRefEntry parameterRef="Hdr"/>
          </EntryList></SequenceContainer>
          <SequenceContainer name="P"><EntryList>
            <ParameterRefEntry parameterRef="X"/></EntryList>
            <BaseContainer containerRef="Base"><RestrictionCriteria>
              <Comparison parameterRef="Hdr/A" value="5"/>
            </RestrictionCriteria></BaseContainer></SequenceContainer>
        </ContainerSet>""")

    def test_member_after_slash(self):
        with self.assertRaises(Unsupported) as cm:
            compile_xml(self.SLASH)
        self.assertIn("unresolved parameter reference 'Hdr/A'",
                      str(cm.exception))
        m = compile_xml(dialect(self.SLASH, "yamcs"))
        p = next(n for n in m.nodes if n.name == "P")
        c = p.criteria[0]
        self.assertEqual((c.text, c.path, c.value),
                         ("/Sat/Hdr.A == 5", ("a",), 5))

    EMBEDDED = mission(tc="""
        <ArgumentTypeSet>
          <BinaryArgumentType name="Tc"><AncillaryDataSet>
            <AncillaryData name="Yamcs:EmbeddedTc"/></AncillaryDataSet>
            <BinaryDataEncoding><SizeInBits><FixedValue>-1</FixedValue>
            </SizeInBits></BinaryDataEncoding></BinaryArgumentType>
          <IntegerArgumentType name="U8" signed="false">
          <IntegerDataEncoding sizeInBits="8"/></IntegerArgumentType>
        </ArgumentTypeSet>
        <MetaCommandSet>"""
                       + command("C",
                                 '<Argument name="T" argumentTypeRef="Tc"/>'
                                 '<Argument name="Y" argumentTypeRef="U8"/>',
                                 '<ArgumentRefEntry argumentRef="T"/>'
                                 '<ArgumentRefEntry argumentRef="Y"/>')
                       + "</MetaCommandSet>")

    def test_embedded_tc(self):
        # Standard XTCE: T takes the rest of the packet but Y.
        e = compile_xml(self.EMBEDDED).nodes[0].fields[0][1].vtype.enc
        self.assertEqual((e.embedded, e.rest, e.tail), (False, True, 8))
        n = compile_xml(dialect(self.EMBEDDED, "yamcs")).nodes[0]
        e = n.fields[0][1].vtype.enc
        self.assertEqual((e.embedded, e.rest, e.bits), (True, False, 8 * 65536))


class LocationTests(unittest.TestCase):
    def items(self, *refs: str, record: str = "") -> list:
        return compile_xml(packet_mission(*refs, record=record)).nodes[0].items

    def test_next_entry_follows(self):
        # A ends 4 bits before B starts.
        items = self.items(ref("A", "nextEntry:4"), ref("B"))
        self.assertEqual([type(it).__name__ for it in items],
                         ["Value", "Value", "Seek", "Value"])
        self.assertEqual((items[2].from_start, items[2].offset), (None, 4))

    def test_next_entry_backward(self):
        # B is 32 bits from the start, so A (4 bits) ends 2 bits before.
        items = self.items(ref("A", "nextEntry:2"),
                           ref("B", "containerStart:32"))
        self.assertEqual((items[1].from_start, items[1].offset), (0, 26))

    def test_container_end_in_a_packet_of_varying_size(self):
        items = self.items(ref("Text"), ref("B", "containerEnd:8"))
        kinds = [type(it).__name__ for it in items]
        self.assertEqual(kinds[-3:], ["TailSeek", "Value", "TailPad"])
        self.assertEqual((items[-3].back, items[-1].bits), (16, 8))

    def test_rejected(self):
        for refs, record, why in (
                ((ref("A", "nextEntry:4"), ref("B", "previousEntry:3")), "",
                 "an entry located 4 bits before the next one, which says "
                 "otherwise"),
                ((ref("A"), ref("B", "nextEntry:4")), "",
                 "an entry located from the next entry, without one after"),
                ((ref("Text", "nextEntry:0"), ref("B", "containerStart:80")),
                 "", "an entry located from the next entry whose size varies"),
                ((ref("B", "containerEnd:0"), ref("A")), "", "is not its last"),
                ((), entries(ref("Text"), ref("A", "containerEnd:0")),
                 "a location from the end of an included container")):
            with self.subTest(why=why):
                with self.assertRaises(Unsupported) as cm:
                    self.items(*refs, record=record)
                self.assertIn(why, str(cm.exception))


def bool_string(attrs: str, bits: int = 32) -> str:
    """Builds telemetry with a boolean X encoded as a terminated string.

    Args:
        attrs: More attributes of X's BooleanParameterType.
        bits:  The string's fixed size in bits.

    Returns:
        The XTCE document.
    """
    return mission(tm=f"""
        <ParameterTypeSet>
          <BooleanParameterType name="T" {attrs}><StringDataEncoding>
          <SizeInBits>
            <Fixed><FixedValue>{bits}</FixedValue></Fixed>
            <TerminationChar>00</TerminationChar>
          </SizeInBits></StringDataEncoding></BooleanParameterType>
        </ParameterTypeSet>
        <ParameterSet><Parameter name="X" parameterTypeRef="T"/></ParameterSet>
        <ContainerSet>
          <SequenceContainer name="Status"><EntryList>
          <ParameterRefEntry parameterRef="X"/></EntryList></SequenceContainer>
        </ContainerSet>""")


class EncodingTests(unittest.TestCase):
    def test_boolean_as_a_string(self):
        m = compile_xml(bool_string('oneStringValue="on" '
                                    'zeroStringValue="off"'))
        vt = m.nodes[0].fields[0][1].vtype
        self.assertEqual((vt.kind, vt.one_string, vt.zero_string,
                          item_bits(vt)), ("bool", "on", "off", 32))
        # The defaults are True and False, which need 5 bytes.
        m = compile_xml(bool_string("", 40))
        self.assertEqual(m.nodes[0].fields[0][1].vtype.zero_string, "False")

    def test_boolean_strings_rejected(self):
        for attrs, bits, why in (
                ('oneStringValue="a" zeroStringValue="a"', 32, "are both 'a'"),
                ('oneStringValue="toolong" zeroStringValue="no"', 32,
                 "'toolong' does not fit its encoding"),
                ("", 32, "'False' does not fit its encoding")):
            with self.subTest(why=why), self.assertRaises(Unsupported) as cm:
                compile_xml(bool_string(attrs, bits))
            self.assertIn(why, str(cm.exception))

    def test_string_as_binary(self):
        text = mission(tm="""
            <ParameterTypeSet>
              <StringParameterType name="T"><BinaryDataEncoding><SizeInBits>
                <FixedValue>24</FixedValue></SizeInBits></BinaryDataEncoding>
                </StringParameterType>
              <StringParameterType name="R"><BinaryDataEncoding><SizeInBits>
                <FixedValue>-1</FixedValue></SizeInBits></BinaryDataEncoding>
                </StringParameterType>
            </ParameterTypeSet>
            <ParameterSet><Parameter name="X" parameterTypeRef="T"/>
            <Parameter name="Y" parameterTypeRef="R"/></ParameterSet>
            <ContainerSet>
              <SequenceContainer name="Status"><EntryList>
              <ParameterRefEntry parameterRef="X"/></EntryList>
              </SequenceContainer>
              <SequenceContainer name="Rest"><EntryList>
              <ParameterRefEntry parameterRef="Y"/></EntryList>
              </SequenceContainer>
            </ContainerSet>""")
        m = compile_xml(text, skip_unsupported=True)
        vt = m.nodes[0].fields[0][1].vtype
        self.assertEqual((vt.kind, vt.enc.bits), ("string", 24))
        self.assertIn("string with binary size -1", m.skipped[0])



def lookup(*cases: tuple[str, int], default: int = 0) -> str:
    """Builds a DiscreteLookupList on parameter N.

    Args:
        *cases:  (value of N, result) pairs.
        default: The result when no case matches.

    Returns:
        The list's XML.
    """
    body = "".join(f'<DiscreteLookup value="{v}"><Comparison parameterRef="N" '
                   f'value="{n}"/></DiscreteLookup>' for n, v in cases)
    return (f'<DiscreteLookupList defaultValue="{default}">{body}'
            '</DiscreteLookupList>')


class LookupAndLocationTests(unittest.TestCase):
    def test_count_from_a_lookup(self):
        text = array_mission(
            dim(fixed(0), lookup(("1", 2), ("2", 5), default=-1)),
            '<ParameterRefEntry parameterRef="N"/><ParameterRefEntry '
            'parameterRef="Arr"/>')
        vt = compile_xml(text).nodes[0].fields[1][1].vtype
        d = vt.enc.count
        self.assertEqual([(c[0].value, v) for c, v in d.lookup],
                         [(1, 2), (2, 5)])
        # EndingIndex 5 is 6 elements
        self.assertEqual((d.default, d.intercept, d.max), (-1, 1.0, 6))

    def test_location_from_a_value(self):
        loc = ('<LocationInContainerInBits referenceLocation="previousEntry">'
               '<DynamicValue><ParameterInstanceRef parameterRef="N"/>'
               '<LinearAdjustment slope="2"/></DynamicValue>'
               '</LocationInContainerInBits>')
        text = array_mission(dim(fixed(0), fixed(1)),
                             '<ParameterRefEntry parameterRef="N"/>'
                             f'<ParameterRefEntry parameterRef="Arr">{loc}'
                             '</ParameterRefEntry>')
        n = compile_xml(text).nodes[0]
        seek = n.items[1]
        self.assertIsInstance(seek, Seek)
        self.assertEqual((seek.offset.slope, seek.max_offset), (2.0, 510))
        self.assertEqual(n.max_bytes, (8 + 510 + 32 + 7) // 8)

    def test_location_needs_an_earlier_value(self):
        loc = ('<LocationInContainerInBits><DiscreteLookupList '
               'defaultValue="0"><DiscreteLookup value="4"><Comparison '
               'parameterRef="N" value="1"/></DiscreteLookup>'
               '</DiscreteLookupList></LocationInContainerInBits>')
        text = array_mission(dim(fixed(0), fixed(1)),
                             f'<ParameterRefEntry parameterRef="Arr">{loc}'
                             '</ParameterRefEntry><ParameterRefEntry '
                             'parameterRef="N"/>')
        with self.assertRaises(Unsupported) as cm:
            compile_xml(text)
        self.assertIn("criterion on /Sat/N, which is not decoded before it",
                      str(cm.exception))



class MemberPathTests(unittest.TestCase):
    def test_split(self):
        self.assertEqual(member_path("P.a.b"), ("P", ("a", "b")))
        self.assertEqual(member_path("../Sys/P.a"), ("../Sys/P", ("a",)))
        self.assertEqual(member_path("../P"), ("../P", ()))

    def test_criterion_on_a_nested_member(self):
        text = aggregate_mission(aggregate("A", ("Inner", "C"))
                                 + aggregate("C", ("N", "U8"), ("M", "U8")),
                                 '<ParameterRefEntry parameterRef="Agg"/>'
                                 '<ParameterRefEntry parameterRef="N">'
                                 '<IncludeCondition><Comparison '
                                 'parameterRef="Agg.Inner.M" value="1"/>'
                                 '</IncludeCondition></ParameterRefEntry>')
        crit = compile_xml(text).nodes[0].items[1].criteria[0]
        self.assertEqual((crit.path, crit.vtype.key, crit.text),
                         (("inner", "m"), "/Sat/U8", "/Sat/Agg.Inner.M == 1"))

    def test_rejected(self):
        for ref, why in (
                ("Agg.Nope", "aggregate type /Sat/A has no member 'Nope'"),
                ("Agg.N.x", "int type /Sat/U8 has no member 'x'"),
                ("N.x", "int type /Sat/U8 has no member 'x'")):
            text = aggregate_mission(
                aggregate("A", ("N", "U8")),
                '<ParameterRefEntry parameterRef="N"/><ParameterRefEntry '
                'parameterRef="Agg"/><ParameterRefEntry parameterRef="N">'
                f'<IncludeCondition><Comparison parameterRef="{ref}" '
                'value="1"/></IncludeCondition></ParameterRefEntry>')
            with self.subTest(ref), self.assertRaises(Unsupported) as cm:
                compile_xml(text)
            self.assertIn(why, str(cm.exception))



def gate_mission(criteria: str, types: str = "") -> str:
    """Builds telemetry with a restricted derived container.

    U8 parameters A and B, an S8 S, a float X, and a container Derived of
    abstract Base chosen by ``criteria``.

    Args:
        criteria: The XML inside RestrictionCriteria.
        types:    More parameter types' XML.

    Returns:
        The XTCE document.
    """
    return mission(tm=f"""
        <ParameterTypeSet>
          <IntegerParameterType name="U8" signed="false">
          <IntegerDataEncoding sizeInBits="8"/></IntegerParameterType>
          <IntegerParameterType name="S8">
          <IntegerDataEncoding sizeInBits="8" encoding="twosComplement"/>
          </IntegerParameterType>
          <FloatParameterType name="F"><FloatDataEncoding sizeInBits="32"/>
          </FloatParameterType>
          {types}
        </ParameterTypeSet>
        <ParameterSet>
          <Parameter name="A" parameterTypeRef="U8"/>
          <Parameter name="B" parameterTypeRef="U8"/>
          <Parameter name="S" parameterTypeRef="S8"/>
          <Parameter name="X" parameterTypeRef="F"/>
        </ParameterSet>
        <ContainerSet>
          <SequenceContainer name="Base" abstract="true"><EntryList>
            <ParameterRefEntry parameterRef="A"/>
            <ParameterRefEntry parameterRef="B"/>
            <ParameterRefEntry parameterRef="S"/>
            <ParameterRefEntry parameterRef="X"/>
          </EntryList></SequenceContainer>
          <SequenceContainer name="Derived"><EntryList/>
            <BaseContainer containerRef="Base">
            <RestrictionCriteria>{criteria}</RestrictionCriteria>
            </BaseContainer>
          </SequenceContainer>
        </ContainerSet>""")


def check(lhs: str, op: str, rhs: str) -> str:
    """Builds a BooleanExpression Condition comparing two values.

    Args:
        lhs: The parameter on the left.
        op:  The comparison operator, such as "<=".
        rhs: The parameter on the right, or "=" and a value.

    Returns:
        The Condition's XML.
    """
    right = (f"<Value>{rhs[1:]}</Value>" if rhs.startswith("=")
             else f'<ParameterInstanceRef parameterRef="{rhs}"/>')
    op = op.replace("<", "&lt;").replace(">", "&gt;")
    return (f'<Condition><ParameterInstanceRef parameterRef="{lhs}"/>'
            f'<ComparisonOperator>{op}</ComparisonOperator>{right}</Condition>')


class BooleanExpressionTests(unittest.TestCase):
    def test_tree(self):
        be = (f"<BooleanExpression><ORedConditions>{check('A', '<', 'B')}"
              f"<ANDedConditions>{check('A', '==', '=0')}"
              f"{check('B', '==', '=0')}</ANDedConditions></ORedConditions>"
              "</BooleanExpression>")
        n = compile_xml(gate_mission(be)).nodes[0]
        (t,) = n.criteria
        self.assertIsInstance(t, AnyOf)
        self.assertEqual(t.text,
                         "(/Sat/A < /Sat/B or (/Sat/A == 0 and /Sat/B == 0))")
        self.assertEqual(t.terms[0].other.name, "B")
        # Only plain comparisons fix a field's value.
        self.assertEqual(n.fixed, {})

    def test_rejected(self):
        cond = check("A", "==", "X")
        with self.assertRaises(Unsupported) as cm:
            compile_xml(gate_mission(f"<BooleanExpression>{cond}"
                                     "</BooleanExpression>"))
        self.assertIn("comparison of int /Sat/A and float /Sat/X",
                      str(cm.exception))

    def test_mixed_signs(self):
        be = f"<BooleanExpression>{check('A', '<', 'S')}</BooleanExpression>"
        (t,) = compile_xml(gate_mission(be)).nodes[0].criteria
        self.assertFalse(t.target.signed)
        self.assertTrue(t.other_target.signed)



class DerivedSizeTests(unittest.TestCase):
    """Which counts and sizes encoding computes, leaving them out of the
    generated struct."""

    COUNTED = dim(fixed(0), dynamic("N", intercept="-1"))

    def node(self, entries: str, count_type: str = "U8"):
        return compile_xml(array_mission(self.COUNTED, entries,
                                         count_type)).nodes[0]

    def test_count_only_used_for_a_count_is_computed(self):
        n = self.node(COUNT_THEN_ARRAY)
        self.assertEqual([name for name, _ in n.fields], ["arr"])
        ((lc, (consumer, _)),) = n.derived.items()
        self.assertEqual((lc.name, consumer.name), ("N", "Arr"))

    def test_count_that_is_also_read_is_computed(self):
        # A location and an include condition read the computed value.
        located = ('<ParameterRefEntry parameterRef="N"/>'
                   '<ArrayParameterRefEntry parameterRef="Arr">'
                   '<LocationInContainerInBits '
                   f'referenceLocation="previousEntry">{dynamic("N")}'
                   '</LocationInContainerInBits></ArrayParameterRefEntry>')
        read = (COUNT_THEN_ARRAY
                + '<ParameterRefEntry parameterRef="F"><IncludeCondition>'
                  '<Comparison parameterRef="N" value="2" '
                  'comparisonOperator="&gt;="/></IncludeCondition>'
                  '</ParameterRefEntry>')
        for entries in (located, read):
            n = self.node(entries)
            self.assertEqual([lc.name for lc in n.derived], ["N"])
            self.assertEqual([name for name, _ in n.fields][0], "arr")

    def test_count_of_a_value_that_may_be_absent_stays_a_field(self):
        conditional = ('<ParameterRefEntry parameterRef="N"/>'
                       '<ArrayParameterRefEntry parameterRef="Arr">'
                       '<IncludeCondition><Comparison parameterRef="N" '
                       'value="0" comparisonOperator="!="/></IncludeCondition>'
                       '</ArrayParameterRefEntry>')
        n = self.node(conditional)
        self.assertEqual(n.derived, {})
        self.assertEqual([name for name, _ in n.fields], ["n", "arr"])


class ElementTests(unittest.TestCase):
    def condition(self, dims: str, ref: str) -> str:
        """Builds Arr of type A, then F included when ``ref`` is 1.

        Args:
            dims: The XML inside A's DimensionList.
            ref:  The parameter the IncludeCondition compares.

        Returns:
            The XTCE document.
        """
        return array_mission(
            dims,
            '<ParameterRefEntry parameterRef="N"/><ParameterRefEntry '
            'parameterRef="Arr"/><ParameterRefEntry parameterRef="F">'
            f'<IncludeCondition><Comparison parameterRef="{ref}" value="1"/>'
            '</IncludeCondition></ParameterRefEntry>')

    def test_split(self):
        self.assertEqual(member_path("P[1].a[0]"), ("P", (1, "a", 0)))
        with self.assertRaises(Unsupported):
            member_path("P[x]")

    def test_criterion_on_an_element(self):
        m = compile_xml(self.condition(dim(fixed(0), fixed(2)), "Arr[2]"))
        crit = m.nodes[0].items[2].criteria[0]
        self.assertEqual((crit.path, crit.text),
                         ((Index(2, 3),), "/Sat/Arr[2] == 1"))

    def test_element_of_a_two_dimensional_array(self):
        # Rows 1..2 of 3 elements: Arr[2][1] is the 5th.
        text = self.condition(dim(fixed(1), fixed(2)) + dim(fixed(0), fixed(2)),
                              "Arr[2][1]")
        crit = compile_xml(text).nodes[0].items[2].criteria[0]
        self.assertEqual((crit.path, crit.text),
                         ((Index(4, 6),), "/Sat/Arr[2][1] == 1"))

    def test_element_of_an_array_whose_count_varies(self):
        # Whether the element is there is checked when the packet is
        # decoded or encoded, so its Index has no count.
        m = compile_xml(self.condition(dim(fixed(0), dynamic("N")), "Arr[2]"))
        crit = m.nodes[0].items[2].criteria[0]
        self.assertEqual(crit.path, (Index(2, None),))
        # Rows 1..N of 3 elements: Arr[2][1] is the 5th.
        text = self.condition(dim(fixed(1), dynamic("N"))
                              + dim(fixed(0), fixed(2)), "Arr[2][1]")
        crit = compile_xml(text).nodes[0].items[2].criteria[0]
        self.assertEqual(crit.path, (Index(4, None),))
        # Two rows of N: Arr[1][2] is element 2 + N, there when N >= 3.
        text = self.condition(dim(fixed(0), fixed(1))
                              + dim(fixed(0), dynamic("N")), "Arr[1][2]")
        crit = compile_xml(text).nodes[0].items[2].criteria[0]
        self.assertEqual(crit.path, (Index(2, None, 1, 2, 3),))

    def test_subset(self):
        # Elements 1..3 of 0..4 in the entry, then 2 of them in another.
        entries = ('<ParameterRefEntry parameterRef="N"/>'
                   '<ArrayParameterRefEntry parameterRef="Arr">'
                   f'<DimensionList>{dim(fixed(1), fixed(3))}</DimensionList>'
                   '</ArrayParameterRefEntry>')
        m = compile_xml(array_mission(dim(fixed(0), fixed(4)), entries))
        e = m.nodes[0].fields[1][1].vtype.enc
        self.assertEqual((e.count, e.starts, e.shape), (3, (1,), (3,)))
        self.assertEqual(m.nodes[0].max_bytes, 1 + 3 * 2)
        dynamic_end = entries.replace(fixed(3), dynamic("N"))
        m = compile_xml(array_mission(dim(fixed(0), fixed(4)), dynamic_end))
        e = m.nodes[0].fields[0][1].vtype.enc
        self.assertEqual((e.count.max, e.shape), (255, ()))

    def test_subset_rejected(self):
        for type_dims, subset, why in (
                (dim(fixed(0), fixed(4)), dim(fixed(2), fixed(5)),
                 "subset of Arr ends after its index 4"),
                (dim(fixed(1), fixed(4)), dim(fixed(0), fixed(2)),
                 "subset of Arr starts before its index 1"),
                (dim(fixed(0), fixed(4)), dim(fixed(0), fixed(1)) * 2,
                 "2 dimensions for 1-dimensional Arr")):
            entries = ('<ArrayParameterRefEntry parameterRef="Arr">'
                       f'<DimensionList>{subset}</DimensionList>'
                       '</ArrayParameterRefEntry>')
            with self.subTest(why=why), self.assertRaises(Unsupported) as cm:
                compile_xml(array_mission(type_dims, entries))
            self.assertIn(why, str(cm.exception))

    def test_rejected(self):
        for dims, ref, why in (
                (dim(fixed(0), fixed(2)), "Arr[3]", "index 3 is outside 0..2"),
                (dim(fixed(1), dynamic("N")), "Arr[0]", "index 0 is before 1"),
                (dim(fixed(0), fixed(1)) + dim(fixed(0), fixed(1)), "Arr[0]",
                 "1 indexes for a 2-dimensional array"),
                (dim(fixed(1), fixed(2)), "Arr[0]", "index 0 is outside 1..2"),
                (dim(fixed(0), fixed(1)), "N[0]",
                 "int type /Sat/U8 is not an array"),
                (dim(fixed(0), fixed(1)), "Arr[0",
                 "malformed reference 'Arr[0'")):
            with self.subTest(why=why), self.assertRaises(Unsupported) as cm:
                compile_xml(self.condition(dims, ref))
            self.assertIn(why, str(cm.exception))


def space(name: str, tm: str = "", tc: str = "") -> str:
    """A document whose root SpaceSystem is ``name``."""
    return mission(tm, tc).replace('name="Sat"', f'name="{name}"', 1)


def compile_xmls(*texts: str, **kw):
    return compile_documents([tcs_xtce.Document.from_bytes(t.encode()).root
                              for t in texts], **kw)


# A mission in three documents, as Yamcs's PUS example is split: data types,
# a packet header and command that build on them, and an application's
# packets that build on both.
TYPES = space("dt", tm="""<ParameterTypeSet>
                    <IntegerParameterType name="U8" signed="false">
                      <IntegerDataEncoding sizeInBits="8"/>
                    </IntegerParameterType></ParameterTypeSet>""",
              tc="""<ArgumentTypeSet>
                    <IntegerArgumentType name="U8" signed="false">
                      <IntegerDataEncoding sizeInBits="8"/>
                    </IntegerArgumentType></ArgumentTypeSet>""")
HEADER = space(
    "hdr", tm="""<ParameterSet>
                     <Parameter name="Id" parameterTypeRef="/dt/U8"/>
                     </ParameterSet>
                     <ContainerSet>
                       <SequenceContainer name="Packet" abstract="true">
                         <EntryList><ParameterRefEntry parameterRef="Id"/>
                         </EntryList>
                       </SequenceContainer>
                     </ContainerSet>""",
    tc="<MetaCommandSet>"
    + command("Cmd", '<Argument name="Op" argumentTypeRef="/dt/U8"/>',
              '<ArgumentRefEntry argumentRef="Op"/>').replace(
        "<MetaCommand ", '<MetaCommand abstract="true" ') + "</MetaCommandSet>")
# The application's references: an absolute path, a relative path that
# climbs with .., and one that is found from an enclosing SpaceSystem, as
# yamcs-cfs's cfs-ccsds/cfs-cmd is.
APP = space("app", tm="""<ParameterSet>
                  <Parameter name="Temp" parameterTypeRef="../dt/U8"/>
                  </ParameterSet>
                  <ContainerSet>
                    <SequenceContainer name="Status">
                      <EntryList><ParameterRefEntry parameterRef="Temp"/>
                      </EntryList>
                      <BaseContainer containerRef="hdr/Packet">
                        <RestrictionCriteria>
                          <Comparison parameterRef="/hdr/Id" value="5"/>
                        </RestrictionCriteria>
                      </BaseContainer>
                    </SequenceContainer>
                  </ContainerSet>""", tc="""<MetaCommandSet>
                  <MetaCommand name="Ping">
                    <BaseMetaCommand metaCommandRef="/hdr/Cmd">
                      <ArgumentAssignmentList>
                        <ArgumentAssignment argumentName="Op"
                          argumentValue="1"/>
                      </ArgumentAssignmentList>
                    </BaseMetaCommand>
                  </MetaCommand>
                  </MetaCommandSet>""")


class MultiDocumentTests(unittest.TestCase):
    def test_references_reach_other_documents(self):
        m = compile_xmls(TYPES, HEADER, APP)
        nodes = {n.key: n for n in m.nodes}
        self.assertEqual(sorted(nodes), ["/app/Ping", "/app/Status"])
        status = nodes["/app/Status"]
        self.assertEqual([name for name, _ in status.fields], ["temp"])
        self.assertEqual(status.base.key, "/hdr/Packet")
        self.assertEqual(status.max_bytes, 2)
        self.assertEqual(nodes["/app/Ping"].max_bytes, 1)

    def test_named_for_the_last_document(self):
        m = compile_xmls(TYPES, HEADER, APP)
        self.assertEqual(m.name, "app")
        self.assertEqual(m.spaces, ["dt", "hdr", "app"])

    def test_order_does_not_matter(self):
        a = compile_xmls(TYPES, HEADER, APP)
        b = compile_xmls(APP, TYPES, HEADER)
        self.assertEqual(sorted(n.key for n in a.nodes),
                         sorted(n.key for n in b.nodes))

    def test_a_missing_document_leaves_references_unresolved(self):
        with self.assertRaises(Unsupported) as cm:
            compile_xmls(HEADER, APP)
        self.assertIn("unresolved parameter type reference '/dt/U8'",
                      str(cm.exception))

    def test_root_names_differ(self):
        with self.assertRaises(Unsupported) as cm:
            compile_xmls(TYPES, HEADER, TYPES)
        self.assertIn("two documents have the root SpaceSystem 'dt'",
                      str(cm.exception))

    def test_one_document_is_the_same_as_before(self):
        m = compile_xmls(mission())
        self.assertEqual((m.name, m.spaces), ("Sat", ["Sat"]))

    def test_dot_paths_are_not_searched_upward(self):
        # ./Packet names only a container in the current SpaceSystem.
        app = APP.replace('containerRef="hdr/Packet"',
                          'containerRef="./Packet"')
        with self.assertRaises(Unsupported) as cm:
            compile_xmls(TYPES, HEADER, app)
        self.assertIn("unresolved container reference './Packet'",
                      str(cm.exception))


def typed(types: str, ref: str = "T") -> str:
    """Builds a mission with one packet P holding a parameter X.

    Args:
        types: The parameter types' XML.
        ref:   The name of X's type.

    Returns:
        The XTCE document.
    """
    return mission(tm=f"""
        <ParameterTypeSet>{types}</ParameterTypeSet>
        <ParameterSet><Parameter name="X" parameterTypeRef="{ref}"/>
        </ParameterSet>
        <ContainerSet><SequenceContainer name="P">
          <EntryList><ParameterRefEntry parameterRef="X"/></EntryList>
        </SequenceContainer></ContainerSet>""")


U16 = ('<IntegerParameterType name="U16" signed="false"><IntegerDataEncoding '
       'sizeInBits="16"/></IntegerParameterType>')


class BaseTypeTests(unittest.TestCase):
    def vtype(self, text: str):
        return compile_xml(text).nodes[0].fields[0][1].vtype

    def test_everything_is_inherited(self):
        vt = self.vtype(
            typed(U16 + '<IntegerParameterType name="T" baseType="U16"/>'))
        self.assertEqual((vt.kind, vt.enc.bits, vt.signed), ("int", 16, False))

    def test_what_a_type_gives_replaces_its_base(self):
        vt = self.vtype(typed(U16
                              + '<IntegerParameterType name="T" baseType="U16" '
                                'signed="true"><IntegerDataEncoding '
                                'sizeInBits="8" encoding="twosComplement"/>'
                                '</IntegerParameterType>'))
        self.assertEqual((vt.enc.bits, vt.signed), (8, True))

    def test_chains_and_other_documents(self):
        types = space("dt", tm=f"<ParameterTypeSet>{U16}</ParameterTypeSet>")
        app = typed('<IntegerParameterType name="M" baseType="/dt/U16"/>'
                    '<IntegerParameterType name="T" baseType="M"/>')
        vt = compile_xmls(types, app).nodes[0].fields[0][1].vtype
        self.assertEqual((vt.enc.bits, vt.signed), (16, False))

    def test_rejected(self):
        cases = [("is another kind of type, FloatParameterType",
                  '<FloatParameterType name="F"/><IntegerParameterType '
                  'name="T" baseType="F"/>'),
                 ("its baseType chain is a cycle",
                  '<IntegerParameterType name="T" baseType="T2"/>'
                  '<IntegerParameterType name="T2" baseType="T"/>'),
                 ("unresolved base type reference 'Nope'",
                  '<IntegerParameterType name="T" baseType="Nope"/>')]
        for msg, types in cases:
            with self.subTest(msg=msg), self.assertRaises(Unsupported) as cm:
                compile_xml(typed(types))
            self.assertIn(msg, str(cm.exception))


class CommandContainerTests(unittest.TestCase):
    def test_container_extends_its_base_commands_container(self):
        # Without a BaseContainer, a command's container still follows its
        # base command's, as Yamcs reads it: Ping is Op, then Level.
        u8 = ('<IntegerArgumentType name="U8" signed="false">'
              '<IntegerDataEncoding sizeInBits="8"/></IntegerArgumentType>')
        text = mission(tc=f"""
            <ArgumentTypeSet>{u8}</ArgumentTypeSet>
            <MetaCommandSet>
            {command("Cmd", '<Argument name="Op" argumentTypeRef="U8"/>',
                     '<ArgumentRefEntry argumentRef="Op"/>')}
            <MetaCommand name="Ping">
              <BaseMetaCommand metaCommandRef="Cmd">
                <ArgumentAssignmentList>
                  <ArgumentAssignment argumentName="Op" argumentValue="1"/>
                </ArgumentAssignmentList>
              </BaseMetaCommand>
              <ArgumentList>
                <Argument name="Level" argumentTypeRef="U8"/>
              </ArgumentList>
              <CommandContainer name="PingC"><EntryList>
                <ArgumentRefEntry argumentRef="Level"/>
              </EntryList></CommandContainer>
            </MetaCommand>
            </MetaCommandSet>""")
        ping = next(n for n in compile_xml(text).nodes if n.name == "Ping")
        self.assertEqual(ping.base.name, "Cmd")
        self.assertEqual([name for name, _ in ping.fields], ["level"])
        self.assertEqual(ping.max_bytes, 2)


class EncodingRuleTests(unittest.TestCase):
    """Bit and byte orders, BCD, float formats and character sets."""

    def vtype(self, ptype: str):
        return compile_xml(typed(ptype)).nodes[0].fields[0][1].vtype

    def rejected(self, ptype: str) -> str:
        with self.assertRaises(Unsupported) as cm:
            compile_xml(typed(ptype))
        return str(cm.exception)

    def test_orders(self):
        def enc(attrs: str):
            return self.vtype('<IntegerParameterType name="T" signed="false">'
                              f'<IntegerDataEncoding {attrs}/>'
                              '</IntegerParameterType>').enc

        e = enc('sizeInBits="32" byteOrder="2,3,0,1"')
        self.assertEqual((e.order, e.lsb), ((2, 3, 0, 1), False))
        # Lists that amount to big or little endian are those.
        self.assertEqual(enc('sizeInBits="16" byteOrder="1,0"').order, "be")
        self.assertEqual(enc('sizeInBits="16" byteOrder="0,1"').order, "le")
        e = enc('sizeInBits="12" bitOrder="leastSignificantBitFirst" '
                'byteOrder="leastSignificantByteFirst"')
        self.assertEqual((e.order, e.lsb, e.bits), ("le", True, 12))
        for attrs in ('sizeInBits="32" byteOrder="0,1,2"',
                      'sizeInBits="16" byteOrder="0,0"',
                      'sizeInBits="12" byteOrder="0,1"'):
            with self.subTest(attrs=attrs):
                self.assertIn("does not give each of the",
                              self.rejected('<IntegerParameterType name="T">'
                                            f'<IntegerDataEncoding {attrs}/>'
                                            '</IntegerParameterType>'))

    def test_bcd(self):
        vt = self.vtype('<IntegerParameterType name="T" signed="false">'
                        '<IntegerDataEncoding sizeInBits="12" '
                        'encoding="packedBCD"/></IntegerParameterType>')
        self.assertTrue(vt.enc.bcd)
        self.assertFalse(vt.signed)
        # Three digits, so 0 to 999, and not what 12 signed bits hold.
        self.assertEqual(value_range(vt), (0, 999))
        vt = self.vtype('<IntegerParameterType name="T" signed="false">'
                        '<IntegerDataEncoding sizeInBits="16" encoding="BCD"/>'
                        '</IntegerParameterType>')
        self.assertEqual(value_range(vt), (0, 99))
        for size, kind, why in (("12", "BCD", "not whole bytes"),
                                ("10", "packedBCD", "not whole digits")):
            with self.subTest(kind=kind):
                self.assertIn(why,
                              self.rejected('<IntegerParameterType name="T">'
                                            '<IntegerDataEncoding '
                                            f'sizeInBits="{size}" '
                                            f'encoding="{kind}"/>'
                                            '</IntegerParameterType>'))

    def test_float_formats(self):
        for kind, size in (("IEEE754_1985", 16), ("IEEE754", 128),
                           ("MILSTD_1750A", 48), ("DEC", 64), ("IBM", 128),
                           ("TI", 40)):
            with self.subTest(kind=kind):
                vt = self.vtype('<FloatParameterType name="T">'
                                f'<FloatDataEncoding encoding="{kind}" '
                                f'sizeInBits="{size}"/></FloatParameterType>')
                self.assertEqual(vt.enc.bits, size)
        self.assertIn("TI has no 64-bit format",
                      self.rejected('<FloatParameterType name="T">'
                                    '<FloatDataEncoding encoding="TI" '
                                    'sizeInBits="64"/></FloatParameterType>'))

    def test_charsets(self):
        def string(cs: str, size: str = "64", term: str = "") -> str:
            """Builds a fixed-size string type T.

            Args:
                cs:   The character set.
                size: The size in bits.
                term: The termination character in hex; "" for none.

            Returns:
                The type's XML.
            """
            term = f"<TerminationChar>{term}</TerminationChar>" if term else ""
            return ('<StringParameterType name="T"><StringDataEncoding '
                    f'encoding="{cs}"><SizeInBits><Fixed><FixedValue>{size}'
                    f'</FixedValue></Fixed>{term}</SizeInBits>'
                    '</StringDataEncoding></StringParameterType>')

        # A zero terminator is a zero code unit.
        self.assertEqual(self.vtype(string("UTF-16")).enc.terminator, bytes(2))
        self.assertEqual(
            self.vtype(string("UTF-8", term="0D0A")).enc.terminator, b"\r\n")
        vt = self.vtype(
            string("UTF-16", term="0000").replace(
                'encoding="UTF-16"',
                'encoding="UTF-16" byteOrder="leastSignificantByteFirst"'))
        self.assertEqual(vt.enc.charset, "UTF-16LE")
        self.assertIn("not whole UTF-32 code units",
                      self.rejected(string("UTF-32", size="48")))
        self.assertIn("is not 1 to 8 UTF-16 code units",
                      self.rejected(string("UTF-16", term="0A")))

    def test_boolean_strings_must_fit_the_charset(self):
        text = ('<BooleanParameterType name="T" oneStringValue="€" '
                'zeroStringValue="no"><StringDataEncoding encoding="US-ASCII">'
                '<SizeInBits><Fixed><FixedValue>32</FixedValue></Fixed>'
                '</SizeInBits></StringDataEncoding></BooleanParameterType>')
        self.assertIn("'€' is not US-ASCII", self.rejected(text))


def calibrated_by(cal: str, before: Sequence[str] = (),
                  ptype: str = "Float") -> str:
    """Builds a mission with a calibrated parameter X.

    Args:
        cal:    The XML inside X's IntegerDataEncoding: its calibrator.
        before: The names of 8-bit unsigned parameters to put before X.
        ptype:  The kind of X's type, such as "Float" or "Integer".

    Returns:
        The XTCE document.
    """
    params = "".join(f'<Parameter name="{n}" parameterTypeRef="U8"/>'
                     for n in before)
    entries = "".join(f'<ParameterRefEntry parameterRef="{n}"/>'
                      for n in before)
    return mission(tm=f"""
        <ParameterTypeSet>
          <IntegerParameterType name="U8" signed="false">
            <IntegerDataEncoding sizeInBits="8"/></IntegerParameterType>
          <{ptype}ParameterType name="T">
            <IntegerDataEncoding sizeInBits="8">{cal}</IntegerDataEncoding>
          </{ptype}ParameterType>
        </ParameterTypeSet>
        <ParameterSet>{params}<Parameter name="X" parameterTypeRef="T"/>
        </ParameterSet>
        <ContainerSet><SequenceContainer name="P"><EntryList>{entries}
          <ParameterRefEntry parameterRef="X"/></EntryList>
        </SequenceContainer></ContainerSet>""")


def rpn(*items: str) -> str:
    """Builds a default MathOperationCalibrator.

    Args:
        *items: The operation in reverse Polish: "x" for this parameter,
                "@" and a name for that parameter, a number for a value,
                anything else for an operator.

    Returns:
        The calibrator's XML.
    """
    out = []
    for it in items:
        if it == "x":
            out.append("<ThisParameterOperand/>")
        elif it.startswith("@"):
            out.append("<ParameterInstanceRefOperand "
                       f'parameterRef="{it[1:]}"/>')
        elif it[0].isdigit() or it[0] == "-" and len(it) > 1:
            out.append(f"<ValueOperand>{it}</ValueOperand>")
        else:
            out.append(f"<Operator>{it.replace('&', '&amp;')}</Operator>")
    return ("<DefaultCalibrator><MathOperationCalibrator>" + "".join(out)
            + "</MathOperationCalibrator></DefaultCalibrator>")


class CalibratorTests(unittest.TestCase):
    def cal(self, text: str):
        return compile_xml(text).nodes[0].fields[-1][1].vtype.calibrator

    def rejected(self, text: str) -> str:
        with self.assertRaises(Unsupported) as cm:
            compile_xml(text)
        return str(cm.exception)

    def test_math_operation_is_a_tree(self):
        cal = self.cal(calibrated_by(rpn("x", "2", "*", "@A", "+", "dup", "*",
                                         "1", "swap", "-"), ["A"]))
        self.assertIsInstance(cal, MathOp)
        op, one, sq = cal.expr
        self.assertEqual((op, one), ("-", ("const", 1.0)))
        self.assertEqual(sq[0], "*")
        self.assertEqual(repr(sq[1][1]), repr(sq[2][1]))  # dup copies it
        self.assertEqual(sq[1][1], ("*", ("raw",), ("const", 2.0)))
        self.assertEqual(sq[1][2][1].local.name, "A")  # bound to A
        self.assertFalse(cal.invertible)

    def test_math_operation_rejected(self):
        for items, why in ((("x", "+"), "has too few operands"),
                           (("x", "2"), "leaves 2 values on its stack"),
                           (("x", "two", "*"), "'two' is not a number"),
                           (("x", "@A", "+"),
                            "unresolved parameter reference 'A'")):
            with self.subTest(items=items):
                text = calibrated_by(rpn(*items))
                if "two" in items:
                    text = text.replace("<Operator>two</Operator>",
                                        "<ValueOperand>two</ValueOperand>")
                self.assertIn(why, self.rejected(text))

    def test_context_calibrators_pick_by_earlier_values(self):
        poly = ('<PolynomialCalibrator><Term coefficient="{c}" exponent="1"/>'
                '</PolynomialCalibrator>')
        text = calibrated_by("<DefaultCalibrator>" + poly.format(c=1)
                             + "</DefaultCalibrator><ContextCalibratorList>"
                               "<ContextCalibrator><ContextMatch>"
                               '<Comparison parameterRef="M" value="1"/>'
                               '</ContextMatch><Calibrator>' + poly.format(c=2)
                             + "</Calibrator></ContextCalibrator>"
                               "</ContextCalibratorList>", ["M"])
        cal = self.cal(text)
        self.assertIsInstance(cal, ContextCal)
        ((terms, case),) = cal.cases
        self.assertEqual(terms[0].local.name, "M")
        self.assertEqual(case.terms, [(2.0, 1)])
        self.assertEqual(cal.default.terms, [(1.0, 1)])
        self.assertIn("not decoded before it",
                      self.rejected(text.replace('<ParameterRefEntry '
                                                 'parameterRef="M"/>', "")))

    def test_spline_orders(self):
        def spline(points: str, order: str = "") -> str:
            return (f"<DefaultCalibrator><SplineCalibrator {order}>{points}"
                    "</SplineCalibrator></DefaultCalibrator>")

        pts = "".join(f'<SplinePoint raw="{r}" calibrated="{r * r}"/>'
                      for r in range(4))
        cal = self.cal(calibrated_by(spline(pts, 'order="2"')))
        self.assertEqual(cal.orders, (2, 2, 2, 2))
        self.assertFalse(cal.invertible)
        cal = self.cal(calibrated_by(spline(pts, 'order="0"')))
        self.assertEqual((cal.order, cal.orders), (0, ()))
        mixed = pts.replace('raw="1" calibrated="1"',
                            'raw="1" calibrated="1" order="0"')
        self.assertEqual(self.cal(calibrated_by(spline(mixed))).orders,
                         (1, 0, 1, 1))
        self.assertIn(
            "two points at raw value 1",
            self.rejected(calibrated_by(spline(pts
                                               + '<SplinePoint raw="1" '
                                                 'calibrated="5"/>'))))

    def test_bound_calibrators_in_aggregates(self):
        text = mission(tm="""
            <ParameterTypeSet>
              <IntegerParameterType name="U8" signed="false">
                <IntegerDataEncoding sizeInBits="8"/></IntegerParameterType>
              <FloatParameterType name="T"><IntegerDataEncoding sizeInBits="8">
              """ + rpn("x", "@A", "+") + """</IntegerDataEncoding>
              </FloatParameterType>
              <AggregateParameterType name="Agg"><MemberList>
                <Member name="M" typeRef="T"/></MemberList>
              </AggregateParameterType>
            </ParameterTypeSet>
            <ParameterSet><Parameter name="A" parameterTypeRef="U8"/>
              <Parameter name="X" parameterTypeRef="Agg"/></ParameterSet>
            <ContainerSet><SequenceContainer name="P"><EntryList>
              <ParameterRefEntry parameterRef="A"/>
              <ParameterRefEntry parameterRef="X"/></EntryList>
            </SequenceContainer></ContainerSet>""")
        x = compile_xml(text).nodes[0].fields[1][1].vtype
        cal = x.enc.members[0].vtype.calibrator
        self.assertIsInstance(cal, MathOp)
        self.assertEqual(cal.expr[2][1].local.name, "A")


def checked_mission(edc: str, entries: str = "", ctype: str = "U8") -> str:
    """Builds telemetry with a checked container.

    A container P of A (8 bits) and K, with ErrorDetectCorrect items.

    Args:
        edc:     The XML inside ErrorDetectCorrect.
        entries: The XML of P's entries; "" for A then K.
        ctype:   The type of K.

    Returns:
        The XTCE document.
    """
    return mission(tm=f"""
        <ParameterTypeSet>
          <IntegerParameterType name="U8" signed="false">
            <IntegerDataEncoding sizeInBits="8"/></IntegerParameterType>
          <IntegerParameterType name="S8">
            <IntegerDataEncoding sizeInBits="8" encoding="twosComplement"/>
          </IntegerParameterType>
        </ParameterTypeSet>
        <ParameterSet><Parameter name="A" parameterTypeRef="U8"/>
          <Parameter name="K" parameterTypeRef="{ctype}"/></ParameterSet>
        <ContainerSet><SequenceContainer name="P">
          <BinaryEncoding><ErrorDetectCorrect>{edc}</ErrorDetectCorrect>
          </BinaryEncoding>
          <EntryList>{entries or '<ParameterRefEntry parameterRef="A"/>'
                      '<ParameterRefEntry parameterRef="K"/>'}</EntryList>
        </SequenceContainer></ContainerSet>""")


class ErrorDetectionTests(unittest.TestCase):
    def node(self, text: str):
        return compile_xml(text).nodes[0]

    def rejected(self, text: str) -> str:
        with self.assertRaises(Unsupported) as cm:
            compile_xml(text)
        return str(cm.exception)

    def test_checks(self):
        n = self.node(checked_mission('<CRC width="8" parameterRef="K" '
                                      'reflectData="true"><Polynomial>107'
                                      '</Polynomial><FinalXOR>55</FinalXOR>'
                                      '</CRC>'))
        (c,) = n.checks
        # The polynomial and final XOR are truncated to the width.
        self.assertEqual(c.spec, ("crc", 8, 0x07, 0, 0x55, True, False))
        self.assertEqual((c.local.name, c.skip, c.from_end), ("K", 0, False))
        self.assertEqual([name for name, _ in n.fields], ["a"])
        self.assertTrue(c.local.computed)
        n = self.node(checked_mission('<Checksum name="fletcher16" '
                                      'parameterRef="K" reference="end" '
                                      'bitsFromReference="8"/>'))
        (c,) = n.checks
        self.assertEqual((c.spec, c.skip, c.from_end),
                         (("fletcher", 16), 8, True))
        n = self.node(checked_mission('<XOR parameterRef="K"/><Parity '
                                      'type="Even"/>'))
        self.assertEqual([c.spec for c in n.checks], [("xor", 8)])

    def test_rejected(self):
        cases = [
            (checked_mission('<XOR parameterRef="K"/>', ctype="S8"),
             "checksum field K must be an uncalibrated unsigned"),
            (checked_mission('<XOR parameterRef="K"/>',
                             entries='<ParameterRefEntry parameterRef="A"/>'),
             "the XOR value /Sat/K is not in container /Sat/P"),
            (checked_mission('<XOR parameterRef="K"/><Parity type="Odd" '
                             'parameterRef="K"/>'),
             "already a packet length or checksum field"),
            (checked_mission('<CRC width="65" parameterRef="K"><Polynomial>07'
                             '</Polynomial></CRC>'), "a 65-bit CRC")]
        for text, why in cases:
            with self.subTest(why=why):
                self.assertIn(why, self.rejected(text))

    def test_deprecated_in_a_data_encoding(self):
        text = typed('<IntegerParameterType name="T"><IntegerDataEncoding>'
                     '<ErrorDetectCorrect><Parity type="Odd"/>'
                     '</ErrorDetectCorrect></IntegerDataEncoding>'
                     '</IntegerParameterType>')
        self.assertIn("ErrorDetectCorrect in a data encoding (deprecated",
                      self.rejected(text))


def timed(time_type: str) -> str:
    """Builds a mission with one packet holding a parameter of time type T.

    Args:
        time_type: The XML of type T.

    Returns:
        The XTCE document.
    """
    return mission(tm=f"""
        <ParameterTypeSet>{time_type}</ParameterTypeSet>
        <ParameterSet><Parameter name="X" parameterTypeRef="T"/>
        </ParameterSet>
        <ContainerSet><SequenceContainer name="P">
          <EntryList><ParameterRefEntry parameterRef="X"/></EntryList>
        </SequenceContainer></ContainerSet>""")


class TimeTests(unittest.TestCase):
    def vtype(self, text: str):
        return compile_xml(text).nodes[0].fields[0][1].vtype

    def test_units_scale_and_offset(self):
        for units, factor in (("milliseconds", 1e-3), ("days", 86400.0),
                              ("years", 31556952.0)):
            vt = self.vtype(timed('<RelativeTimeParameterType name="T">'
                                  f'<Encoding units="{units}" scale="2" '
                                  'offset="3"><IntegerDataEncoding '
                                  'sizeInBits="16"/></Encoding>'
                                  '</RelativeTimeParameterType>'))
            self.assertEqual(vt.calibrator.terms,
                             [(3 * factor, 0), (2 * factor, 1)])

    def test_calibrator_inside_the_encoding(self):
        vt = self.vtype(timed('<RelativeTimeParameterType name="T"><Encoding '
                              'units="minutes" offset="1"><IntegerDataEncoding '
                              'sizeInBits="8"><DefaultCalibrator>'
                              '<SplineCalibrator><SplinePoint raw="0" '
                              'calibrated="0"/><SplinePoint raw="10" '
                              'calibrated="1"/></SplineCalibrator>'
                              '</DefaultCalibrator></IntegerDataEncoding>'
                              '</Encoding></RelativeTimeParameterType>'))
        self.assertIsInstance(vt.calibrator, Spline)
        self.assertEqual(vt.calibrator.points, [(0, 60.0), (10, 120.0)])

    def test_text_and_bytes(self):
        vt = self.vtype(
            timed('<AbsoluteTimeParameterType name="T"><Encoding>'
                  "<StringDataEncoding><Variable maxSizeInBits=\"256\">"
                  "<TerminationChar>00</TerminationChar></Variable>"
                  "</StringDataEncoding></Encoding><ReferenceTime><Epoch>J2000"
                  "</Epoch></ReferenceTime></AbsoluteTimeParameterType>"))
        self.assertEqual(vt.kind, "string")
        self.assertIn("since epoch J2000", vt.doc)
        with self.assertRaises(Unsupported) as cm:
            compile_xml(timed('<AbsoluteTimeParameterType name="T"><Encoding '
                              'scale="2"><BinaryDataEncoding><SizeInBits>'
                              '<FixedValue>8</FixedValue></SizeInBits>'
                              '</BinaryDataEncoding></Encoding>'
                              '</AbsoluteTimeParameterType>'))
        self.assertIn("with units, scale or offset", str(cm.exception))


DEMO = (Path(__file__).resolve().parents[3] / "tests" / "mission" / "demo.xtce")


class ContainerFeatureTests(unittest.TestCase):
    """Included derived containers, sizes that vary, locations from a
    container's end, NextContainer, and command include conditions,
    restriction criteria and long fixed values, as the demo uses them."""

    @classmethod
    def setUpClass(cls):
        cls.m = compile_document(tcs_xtce.Document.from_file(str(DEMO)).root)
        cls.nodes = {n.name: n for n in cls.m.nodes}

    def test_included_derived_container(self):
        items = walk(self.nodes["Framed"].items)
        (req,) = [it for it in items if isinstance(it, Require)]
        self.assertEqual((req.container, req.text),
                         ("/Demo/Section", "/Demo/SectionKind == 2"))
        names = [it.local.name for it in items if hasattr(it, "local")]
        self.assertEqual(names[:3],
                         ["SectionKind", "SectionValue", "SectionExtra"])

    def test_sizes_and_end_locations(self):
        items = walk(self.nodes["Framed"].items)
        fills = [it for it in items if isinstance(it, Fill)]
        self.assertEqual(fills[0].size, 48)  # Carton: the note varies
        self.assertEqual(fills[1].size.local.name, "BagBits")
        # TrayLast ends the 32-bit Tray: it starts 24 bits in.
        seeks = [it for it in items if isinstance(it, Seek)]
        self.assertEqual(seeks[-1].offset, 24)

    def test_next_container(self):
        follower = self.nodes["Follower"]
        self.assertEqual(follower.next_container, "Framed")
        self.assertNotIn(follower, follower.base.children)

    def test_command_features(self):
        arm = self.nodes["Arm"]
        names = {lc.name: lc for _, lc in arm.fields}
        self.assertTrue(names["Level"].optional)
        fixed = [it for it in walk(arm.items) if isinstance(it, Fixed)]
        # 72 bits are a byte and 64 bits, twice.
        self.assertEqual([f.bits for f in fixed], [8, 64, 8, 64])

    def test_command_restriction_criteria(self):
        u8 = ('<IntegerArgumentType name="U8" signed="false">'
              '<IntegerDataEncoding sizeInBits="8"/></IntegerArgumentType>')
        text = mission(tm="""
            <ParameterTypeSet><IntegerParameterType name="P8" signed="false">
              <IntegerDataEncoding sizeInBits="8"/></IntegerParameterType>
            </ParameterTypeSet>
            <ParameterSet><Parameter name="Mode" parameterTypeRef="P8"/>
            </ParameterSet>""", tc=f"""
            <ArgumentTypeSet>{u8}</ArgumentTypeSet>
            <MetaCommandSet>
              <MetaCommand name="Base" abstract="true">
                <CommandContainer name="BaseC"><EntryList>
                  <ParameterRefEntry parameterRef="Mode"/></EntryList>
                </CommandContainer>
              </MetaCommand>
              <MetaCommand name="Go">
                <BaseMetaCommand metaCommandRef="Base"/>
                <CommandContainer name="GoC"><EntryList/>
                  <BaseContainer containerRef="BaseC"><RestrictionCriteria>
                    <Comparison parameterRef="Mode" value="3"/>
                  </RestrictionCriteria></BaseContainer>
                </CommandContainer>
              </MetaCommand>
            </MetaCommandSet>""")
        go = next(n for n in compile_xml(text).nodes if n.name == "Go")
        (c,) = go.criteria
        self.assertEqual((c.local.name, c.value), ("Mode", 3))
        self.assertEqual(go.fixed, {c.local: 3})



def length_tag(adjust: str = "-1") -> str:
    return (f'<AncillaryDataSet><AncillaryData name="tcs-xtce.length">{adjust}'
            '</AncillaryData></AncillaryDataSet>')


def length_mission(entries: str, len_type: str = "U16", tag: str = length_tag(),
                   containers: str = "") -> str:
    """Builds telemetry with packet length fields.

    A packet length field Len, a second one Len2, and values X, Data and
    Looked in container Status.

    Args:
        entries:    The XML of Status's entries.
        len_type:   The type of Len.
        tag:        Len's ancillary data.
        containers: More containers' XML.

    Returns:
        The XTCE document.
    """
    return mission(tm=f"""
        <ParameterTypeSet>
          <IntegerParameterType name="U16" signed="false">
          <IntegerDataEncoding sizeInBits="16"/></IntegerParameterType>
          <IntegerParameterType name="S16">
          <IntegerDataEncoding sizeInBits="16" encoding="twosComplement"/>
          </IntegerParameterType>
          <IntegerParameterType name="U8" signed="false">
          <IntegerDataEncoding sizeInBits="8"/></IntegerParameterType>
          <BinaryParameterType name="B"><BinaryDataEncoding><SizeInBits>
          <DynamicValue>
            <ParameterInstanceRef parameterRef="Len"/>
          </DynamicValue></SizeInBits></BinaryDataEncoding>
          </BinaryParameterType>
          <BinaryParameterType name="BL"><BinaryDataEncoding><SizeInBits>
            <DiscreteLookupList defaultValue="0"><DiscreteLookup value="8">
              <Comparison parameterRef="Len" value="1"/>
            </DiscreteLookup></DiscreteLookupList>
          </SizeInBits></BinaryDataEncoding></BinaryParameterType>
        </ParameterTypeSet>
        <ParameterSet>
          <Parameter name="Len" parameterTypeRef="{len_type}">{tag}</Parameter>
          <Parameter name="Len2" parameterTypeRef="U16">
            {length_tag("0")}
          </Parameter>
          <Parameter name="X" parameterTypeRef="U8"/>
          <Parameter name="Data" parameterTypeRef="B"/>
          <Parameter name="Looked" parameterTypeRef="BL"/>
        </ParameterSet>
        <ContainerSet>
          <SequenceContainer name="Status"><EntryList>{entries}</EntryList>
          </SequenceContainer>
          {containers}
        </ContainerSet>""")


LEN = '<ParameterRefEntry parameterRef="Len"/>'
X = '<ParameterRefEntry parameterRef="X"/>'


class LengthTests(unittest.TestCase):
    def test_length_field_is_computed(self):
        m = compile_xml(length_mission(LEN + X))
        n = m.nodes[0]
        self.assertEqual([name for name, _ in n.fields], ["x"])
        self.assertEqual((n.length_field().name, n.length_field().length),
                         ("Len", -1))

    def test_argument_length_field(self):
        text = mission(
            tc=U8_ARG + "<MetaCommandSet>"
            + command("Cmd",
                      '<Argument name="Len" '
                      f'argumentTypeRef="U8">{length_tag("3")}</Argument>'
                      '<Argument name="A" argumentTypeRef="U8"/>',
                      '<ArgumentRefEntry argumentRef="Len"/><ArgumentRefEntry '
                      'argumentRef="A"/>') + "</MetaCommandSet>")
        n = compile_xml(text).nodes[0]
        self.assertEqual([name for name, _ in n.fields], ["a"])
        self.assertEqual(n.length_field().length, 3)

    def test_rejected(self):
        criterion = ('<SequenceContainer name="Short"><EntryList/>'
                     '<BaseContainer containerRef="Status">'
                     '<RestrictionCriteria><Comparison parameterRef="Len" '
                     'value="1"/></RestrictionCriteria></BaseContainer>'
                     '</SequenceContainer>')
        repeated = ('<SequenceContainer name="Rec"><EntryList>' + LEN
                    + "</EntryList></SequenceContainer>")
        for kw, why in (
                (dict(entries=LEN, len_type="S16"),
                 "must be an uncalibrated unsigned binary integer"),
                (dict(entries=LEN, tag=length_tag("one")),
                 "tcs-xtce.length 'one' is not an integer"),
                (dict(entries=LEN,
                      tag='<AncillaryDataSet><AncillaryData '
                          'name="tcs-xtce.length">-1</AncillaryData>'
                          '<AncillaryData name="tcs-xtce.length">0'
                          '</AncillaryData></AncillaryDataSet>'),
                 "more than one tcs-xtce.length"),
                (dict(entries=LEN + '<ParameterRefEntry parameterRef="Len2"/>'),
                 "more than one packet length field"),
                (dict(entries=X
                      + '<ParameterRefEntry parameterRef="Len">'
                        '<IncludeCondition><Comparison parameterRef="X" '
                        'value="1"/></IncludeCondition></ParameterRefEntry>'),
                 "packet length field Len has an IncludeCondition"),
                (dict(entries=LEN, containers=criterion),
                 "a criterion or assignment on packet length field Len"),
                (dict(entries=LEN + '<ParameterRefEntry parameterRef="Data"/>'),
                 "size or count from packet length or checksum field"),
                (dict(entries=LEN
                      + '<ParameterRefEntry parameterRef="Looked"/>'),
                 "size or count from packet length or checksum field Len"),
                (dict(entries='<ParameterRefEntry parameterRef="Len">'
                              '<RepeatEntry><Count><FixedValue>2</FixedValue>'
                              '</Count></RepeatEntry></ParameterRefEntry>'),
                 "RepeatEntry on packet length or checksum field Len"),
                (dict(entries='<ContainerRefEntry containerRef="Rec">'
                              '<RepeatEntry><Count><FixedValue>2</FixedValue>'
                              '</Count></RepeatEntry></ContainerRefEntry>',
                      containers=repeated),
                 "Len is a packet length or checksum field")):
            with self.subTest(why=why):
                with self.assertRaises(Unsupported) as cm:
                    compile_xml(length_mission(**kw))
                self.assertIn(why, str(cm.exception))

    def test_assignment_rejected(self):
        text = mission(
            tc=U8_ARG + "<MetaCommandSet>"
            + command("Base",
                      '<Argument name="Len" '
                      f'argumentTypeRef="U8">{length_tag()}</Argument>',
                      '<ArgumentRefEntry argumentRef="Len"/>')
            + '<MetaCommand name="Cmd"><BaseMetaCommand metaCommandRef="Base">'
              '<ArgumentAssignmentList><ArgumentAssignment argumentName="Len" '
              'argumentValue="4"/></ArgumentAssignmentList></BaseMetaCommand>'
              '</MetaCommand>' + "</MetaCommandSet>")
        with self.assertRaises(Unsupported) as cm:
            compile_xml(text)
        self.assertIn("a criterion or assignment on packet length field Len",
                      str(cm.exception))


def segment_mission(entries: str) -> str:
    """Builds telemetry for segment tests.

    A 16-bit W, a binary V whose size W gives, and a container P.

    Args:
        entries: The XML of P's entries.

    Returns:
        The XTCE document.
    """
    return mission(tm=f"""
        <ParameterTypeSet>
          <IntegerParameterType name="U16" signed="false">
          <IntegerDataEncoding sizeInBits="16"/></IntegerParameterType>
          <BinaryParameterType name="T"><BinaryDataEncoding><SizeInBits>
          <DynamicValue><ParameterInstanceRef parameterRef="W"/>
          </DynamicValue></SizeInBits></BinaryDataEncoding>
          </BinaryParameterType>
        </ParameterTypeSet>
        <ParameterSet>
          <Parameter name="W" parameterTypeRef="U16"/>
          <Parameter name="V" parameterTypeRef="T"/>
        </ParameterSet>
        <ContainerSet>
          <SequenceContainer name="P"><EntryList>{entries}</EntryList>
          </SequenceContainer>
        </ContainerSet>""")


def seg(name: str, bits: int, order: str = "", repeat: str = "",
        end: str = "") -> str:
    """Builds a ParameterSegmentRefEntry.

    Args:
        name:   The parameter's name.
        bits:   The segment's size in bits.
        order:  The segment's order; "" for none.
        repeat: XML to put in the entry after its location, such as a
                RepeatEntry.
        end:    The segment's offset in bits from the container's end; ""
                to leave it unlocated.

    Returns:
        The entry's XML.
    """
    order = f' order="{order}"' if order else ""
    loc = ('<LocationInContainerInBits '
           f'referenceLocation="containerEnd">{fixed(end)}'
           '</LocationInContainerInBits>' if end else "")
    return (f'<ParameterSegmentRefEntry parameterRef="{name}" '
            f'sizeInBits="{bits}"{order}>{loc}{repeat}'
            '</ParameterSegmentRefEntry>')


def repeat(count: str, offset: str = "") -> str:
    offset = f"<Offset>{offset}</Offset>" if offset else ""
    return f"<RepeatEntry><Count>{count}</Count>{offset}</RepeatEntry>"


class SegmentTests(unittest.TestCase):
    def items(self, entries: str) -> list:
        return compile_xml(segment_mission(entries)).nodes[0].items

    def test_assembled(self):
        a, b, v = self.items(seg("W", 4) + seg("W", 12))
        self.assertIsInstance(a, Segment)
        self.assertEqual((a.bits, a.shift, a.first), (4, 12, True))
        self.assertEqual((b.bits, b.shift, b.first), (12, 0, False))
        self.assertIsInstance(v, Value)
        self.assertEqual(v.assembled, 16)
        self.assertIs(v.local, a.local)

    def test_order(self):
        a, b, _ = self.items(seg("W", 4, "2") + seg("W", 12, "1"))
        self.assertEqual((a.shift, b.shift), (0, 4))
        self.assertTrue(a.first)

    def test_raw(self):
        # Half of W, and a segment of a value that varies in size.
        n = compile_xml(segment_mission('<ParameterRefEntry parameterRef="W"/>'
                                        + seg("W", 8) + seg("V", 8))).nodes[0]
        self.assertEqual([lc.name for _, lc in n.fields],
                         ["W", "WSegment0", "VSegment0"])
        self.assertEqual(n.fields[1][1].vtype.enc.bits, 8)

    def test_repeated_fixed(self):
        # Four 4-bit segments in a row are W's 16 bits.
        *segs, v = self.items(seg("W", 4, repeat=repeat(fixed(4))))
        self.assertEqual([s.shift for s in segs], [12, 8, 4, 0])
        self.assertEqual(v.assembled, 16)

    def test_repeated_with_an_order(self):
        # Two 4-bit segments with order 3 are W's parts 3 and 4, after the
        # 8-bit part 1 that follows them.
        *segs, v = self.items(seg("W", 4, "3", repeat(fixed(2)))
                              + seg("W", 8, "1"))
        self.assertEqual([s.shift for s in segs], [4, 0, 8])
        self.assertEqual(v.assembled, 16)

    def test_repeated_raw(self):
        # A count from W, or an Offset, leaves a list of raw segments.
        for entries in ('<ParameterRefEntry parameterRef="W"/>'
                        + seg("V", 8, repeat=repeat(dynamic("W"))),
                        seg("W", 8, repeat=repeat(fixed(2), fixed(1)))):
            with self.subTest(entries=entries):
                n = compile_xml(segment_mission(entries)).nodes[0]
                vt = n.fields[-1][1].vtype
                self.assertEqual((vt.kind, vt.elem.enc.bits), ("array", 8))

    def test_repeated_from_end(self):
        # Four 4-bit segments a byte before the end, and two 8-bit ones
        # 4 bits apart at the end: their bits and the gaps between them.
        seek, *segs, v, pad = self.items(seg("W", 4, repeat=repeat(fixed(4)),
                                             end="8"))
        self.assertEqual((seek.back, len(segs), v.assembled, pad.bits),
                         (24, 4, 16, 8))
        seek, v, pad = self.items(seg("W", 8, repeat=repeat(fixed(2), fixed(4)),
                                      end="0"))
        self.assertEqual((seek.back, pad.bits), (20, 0))

    def test_repeated_from_end_by_a_count(self):
        # W 8-bit segments at the end: no bits are known, and W's value
        # counts the rest.
        *_, seek, v, pad = self.items('<ParameterRefEntry parameterRef="W"/>'
                                      + seg("W", 8, repeat=repeat(dynamic("W")),
                                            end="0"))
        self.assertIsInstance(seek, TailSeek)
        self.assertEqual((seek.back, seek.span.each, seek.span.gap, pad.bits),
                         (0, 8, 0, 0))
        self.assertEqual(seek.span.count.local.name, "W")
        self.assertIs(v.local.vtype.enc.count, seek.span.count)

    def test_rejected(self):
        for entries, why in (
                (seg("W", 4, "1", repeat(fixed(2))) + seg("W", 8, "2"),
                 "segments of /Sat/W whose order is missing or repeated"),
                (seg("W", 8, repeat=repeat(fixed(0))),
                 "RepeatEntry Count 0 is not positive"),
                (seg("W", 8, "1") + seg("W", 8, "1"),
                 "segments of /Sat/W whose order is missing or repeated"),
                (seg("W", 8) + seg("W", 12),
                 "the segments of /Sat/W have 20 bits, and its encoding 16"),
                (seg("V", 70), "a 70-bit segment of /Sat/V")):
            with self.subTest(why=why):
                with self.assertRaises(Unsupported) as cm:
                    self.items(entries)
                self.assertIn(why, str(cm.exception))


def shared_mission(base: str, entries: str = "", base_meta: str = "") -> str:
    """Builds commands with shared command containers.

    A CommandContainerSet holding Head (parameter K) and Body (extending
    Head, with parameter T), and meta-command C with argument X.

    Args:
        base:      The container C's container extends; "" for none.
        entries:   The XML of C's container's entries after X.
        base_meta: XML to put first in C, such as a BaseMetaCommand.

    Returns:
        The XTCE document.
    """
    bc = f'<BaseContainer containerRef="{base}"/>' if base else ""
    return mission(tc=f"""
        <ParameterTypeSet>
          <IntegerParameterType name="U8" signed="false">
          <IntegerDataEncoding sizeInBits="8"/></IntegerParameterType>
        </ParameterTypeSet>
        <ParameterSet>
          <Parameter name="K" parameterTypeRef="U8"/>
          <Parameter name="T" parameterTypeRef="U8"/>
        </ParameterSet>
        <ArgumentTypeSet>
          <IntegerArgumentType name="A8" signed="false">
          <IntegerDataEncoding sizeInBits="8"/></IntegerArgumentType>
        </ArgumentTypeSet>
        <MetaCommandSet>
          <MetaCommand name="B">
            <CommandContainer name="BC"><EntryList/></CommandContainer>
          </MetaCommand>
          <MetaCommand name="C">{base_meta}
            <ArgumentList><Argument name="X" argumentTypeRef="A8"/>
            </ArgumentList>
            <CommandContainer name="CC">
              <BinaryEncoding><SizeInBits><FixedValue>40</FixedValue>
              </SizeInBits></BinaryEncoding>
              <EntryList><ArgumentRefEntry argumentRef="X"/>{entries}
              </EntryList>{bc}
            </CommandContainer>
          </MetaCommand>
        </MetaCommandSet>
        <CommandContainerSet>
          <CommandContainer name="Head"><EntryList>
            <ParameterRefEntry parameterRef="K"/></EntryList>
          </CommandContainer>
          <CommandContainer name="Body"><EntryList>
            <ParameterRefEntry parameterRef="T"/></EntryList>
            <BaseContainer containerRef="Head"><RestrictionCriteria>
            <Comparison parameterRef="K" value="3"/></RestrictionCriteria>
            </BaseContainer>
          </CommandContainer>
        </CommandContainerSet>""")


class CommandContainerSetTests(unittest.TestCase):
    def node(self, text: str):
        return next(n for n in compile_xml(text).nodes if n.name == "C")

    def test_extends_shared(self):
        n = self.node(shared_mission("Body"))
        self.assertEqual([lc.name for _, lc in n.fields], ["K", "T", "X"])
        self.assertTrue(any(isinstance(it, Require) for it in n.items))
        # Padded up to the container's 40 bits.
        self.assertEqual(n.max_bytes, 5)
        self.assertIsInstance(n.items[-1], Pad)

    def test_includes_shared(self):
        n = self.node(
            shared_mission("", '<ContainerRefEntry containerRef="Body"/>'))
        self.assertEqual([lc.name for _, lc in n.fields], ["X", "K", "T"])

    @staticmethod
    def fragment_mission(entries: str) -> str:
        """Builds shared_mission with abstract meta-commands.

        Adds Base (argument Kind) and Frag (extending it, assigning Kind 7,
        with argument Y).

        Args:
            entries: The XML of C's container's entries.

        Returns:
            The XTCE document.
        """
        frag = ('<MetaCommand name="Base" abstract="true"><ArgumentList>'
                '<Argument name="Kind" argumentTypeRef="U8A"/></ArgumentList>'
                '<CommandContainer name="BaseC"><EntryList><ArgumentRefEntry '
                'argumentRef="Kind"/></EntryList></CommandContainer>'
                '</MetaCommand><MetaCommand name="Frag" abstract="true">'
                '<BaseMetaCommand metaCommandRef="Base">'
                '<ArgumentAssignmentList><ArgumentAssignment '
                'argumentName="Kind" argumentValue="7"/>'
                '</ArgumentAssignmentList></BaseMetaCommand><ArgumentList>'
                '<Argument name="Y" argumentTypeRef="U8A"/></ArgumentList>'
                '<CommandContainer name="FragC"><EntryList><ArgumentRefEntry '
                'argumentRef="Y"/></EntryList><BaseContainer '
                'containerRef="BaseC"/></CommandContainer></MetaCommand>')
        text = shared_mission("", entries)
        text = text.replace("<MetaCommandSet>", "<MetaCommandSet>" + frag)
        return text.replace("</ArgumentTypeSet>",
                            '<IntegerArgumentType name="U8A" signed="false">'
                            '<IntegerDataEncoding sizeInBits="8"/>'
                            '</IntegerArgumentType></ArgumentTypeSet>')

    def test_includes_meta_command(self):
        n = self.node(self.fragment_mission('<ContainerRefEntry '
                                            'containerRef="FragC"/>'))
        # Kind is fixed by Frag's assignment; Y is a field.
        self.assertEqual([lc.name for _, lc in n.fields], ["X", "Y"])
        self.assertEqual([v for lc, v in n.fixed.items() if lc.name == "Kind"],
                         [7])

    def test_repeats_meta_command(self):
        text = self.fragment_mission('<ContainerRefEntry containerRef="FragC">'
                                     '<RepeatEntry><Count><FixedValue>2'
                                     '</FixedValue></Count></RepeatEntry>'
                                     '</ContainerRefEntry>')
        vt = self.node(text).fields[-1][1].vtype
        agg = vt.elem.enc
        self.assertEqual((vt.kind, [m.ident for m in agg.members]),
                         ("array", ["kind", "y"]))
        # Frag's assignment is checked on each copy.
        (check,) = agg.checks
        self.assertEqual(check.criteria[0].path, ("kind",))
        self.assertEqual(check.criteria[0].value, 7)

    def test_included_length_field(self):
        # Frag's Kind measures the container it is in.
        text = self.fragment_mission(
            '<ContainerRefEntry containerRef="FragC"/>').replace(
            '<Argument name="Kind" argumentTypeRef="U8A"/>',
            '<Argument name="Kind" argumentTypeRef="U8A"><AncillaryDataSet>'
            '<AncillaryData name="tcs-xtce.length">0</AncillaryData>'
            '</AncillaryDataSet></Argument>').replace(
            '<ArgumentAssignmentList><ArgumentAssignment argumentName="Kind" '
            'argumentValue="7"/></ArgumentAssignmentList>', "")
        n = self.node(text)
        kind = next(it.local for it in walk(n.items) if isinstance(it, Value)
                    and it.local.name == "Kind")
        self.assertTrue(kind.measures)
        self.assertIsNone(n.length_field())
        self.assertTrue(any(isinstance(it, LengthEnd) and it.local is kind
                            for it in n.items))

    def test_rejected(self):
        meta = '<BaseMetaCommand metaCommandRef="B"/>'
        for text, why in (
                (shared_mission("Body", base_meta=meta),
                 "extends a container other than its BaseMetaCommand's"),
                (shared_mission("", '<ContainerRefEntry containerRef="CC"/>'),
                 "the CommandContainer of /Sat/C includes itself"),
                (shared_mission("", '<ContainerRefEntry containerRef="ZZ"/>'),
                 "unresolved container reference 'ZZ'")):
            with self.subTest(why=why):
                c = compile_xml(text, skip_unsupported=True)
                self.assertFalse(any(n.name == "C" for n in c.nodes))
                self.assertTrue(any(why in m for m in c.skipped), c.skipped)


def indirect_mission(selector: str, labels: str = "A") -> str:
    """Builds telemetry for IndirectParameterRefEntry tests.

    Parameter A, an integer I, an enumeration E, and a container whose
    IndirectParameterRefEntry is chosen by ``selector``.

    Args:
        selector: The XML inside the IndirectParameterRefEntry.
        labels:   E's labels, separated by spaces, valued from 0.

    Returns:
        The XTCE document.
    """
    enums = "".join(f'<Enumeration value="{i}" label="{lab}"/>'
                    for i, lab in enumerate(labels.split()))
    return mission(tm=f"""
        <ParameterTypeSet>
          <IntegerParameterType name="U8" signed="false">
          <IntegerDataEncoding sizeInBits="8"/></IntegerParameterType>
          <EnumeratedParameterType name="ET">
          <IntegerDataEncoding sizeInBits="8"/>
          <EnumerationList>{enums}</EnumerationList>
          </EnumeratedParameterType>
        </ParameterTypeSet>
        <ParameterSet>
          <Parameter name="A" parameterTypeRef="U8"/>
          <Parameter name="I" parameterTypeRef="U8"/>
          <Parameter name="E" parameterTypeRef="ET"/>
        </ParameterSet>
        <ContainerSet>
          <SequenceContainer name="P"><EntryList>
            <ParameterRefEntry parameterRef="I"/>
            <ParameterRefEntry parameterRef="E"/>
            <IndirectParameterRefEntry>
            <ParameterInstance parameterRef="{selector}"/>
            </IndirectParameterRefEntry>
          </EntryList></SequenceContainer>
        </ContainerSet>""")


class IndirectTests(unittest.TestCase):
    def test_choices(self):
        n = compile_xml(indirect_mission("E", "NONE A I")).nodes[0]
        conds = [it for it in n.items if isinstance(it, Cond)]
        self.assertEqual([c.criteria[0].text for c in conds],
                         ["/Sat/E == A", "/Sat/E == I"])
        self.assertEqual([c.items[0].local.name for c in conds], ["A", "I"])
        self.assertTrue(all(c.items[0].local.optional for c in conds))

    def test_selector_must_be_a_label(self):
        # NONE names no parameter, so it selects nothing; a value that is
        # none of E's labels fails.
        n = compile_xml(indirect_mission("E", "NONE A I")).nodes[0]
        req = next(it for it in n.items if isinstance(it, Require))
        self.assertEqual(req.text, "/Sat/E is none of its labels")
        self.assertEqual([t.text for t in req.criteria[0].terms],
                         ["/Sat/E == NONE", "/Sat/E == A", "/Sat/E == I"])

    def test_alias_must_name_a_parameter(self):
        text = indirect_mission("I").replace(
            '<Parameter name="A" parameterTypeRef="U8"/>',
            '<Parameter name="A" parameterTypeRef="U8"><AliasSet><Alias '
            'nameSpace="ns" alias="7"/></AliasSet>'
            '</Parameter>').replace("<IndirectParameterRefEntry>",
                                    '<IndirectParameterRefEntry '
                                    'aliasNameSpace="ns">')
        n = compile_xml(text).nodes[0]
        req = next(it for it in n.items if isinstance(it, Require))
        self.assertEqual(req.text, "/Sat/I names no parameter")
        self.assertEqual([t.text for t in req.criteria[0].terms],
                         ["/Sat/I == 7"])

    def test_rejected(self):
        for text, why in (
                (indirect_mission("I"),
                 "no parameter has a name that integer /Sat/I can hold"),
                (indirect_mission("E", "X Y"),
                 "no label of /Sat/E names a parameter")):
            with self.subTest(why=why):
                with self.assertRaises(Unsupported) as cm:
                    compile_xml(text)
                self.assertIn(why, str(cm.exception))


def named_mission() -> str:
    """Builds telemetry with a parameter named by a string.

    Container P has a string N, of at most 8 bytes, that names the
    parameter after it, and then Z.  LongName12 is too long for N to name,
    and R, which takes the rest of the packet, can't be read there.

    Returns:
        The XTCE document.
    """
    return mission(tm="""
        <ParameterTypeSet>
          <IntegerParameterType name="U8" signed="false">
          <IntegerDataEncoding sizeInBits="8"/></IntegerParameterType>
          <IntegerParameterType name="U32" signed="false">
          <IntegerDataEncoding sizeInBits="32"/></IntegerParameterType>
          <StringParameterType name="NT"><StringDataEncoding>
          <Variable maxSizeInBits="72"><LeadingSize sizeInBitsOfSizeTag="8"/>
          </Variable></StringDataEncoding></StringParameterType>
          <BinaryParameterType name="RT"><BinaryDataEncoding><SizeInBits>
          <FixedValue>-1</FixedValue></SizeInBits></BinaryDataEncoding>
          </BinaryParameterType>
        </ParameterTypeSet>
        <ParameterSet>
          <Parameter name="N" parameterTypeRef="NT"/>
          <Parameter name="A" parameterTypeRef="U8"/>
          <Parameter name="B" parameterTypeRef="U32"/>
          <Parameter name="LongName12" parameterTypeRef="U8"/>
          <Parameter name="R" parameterTypeRef="RT"/>
          <Parameter name="Z" parameterTypeRef="U8"/>
        </ParameterSet>
        <ContainerSet>
          <SequenceContainer name="P"><EntryList>
            <ParameterRefEntry parameterRef="N"/>
            <IndirectParameterRefEntry>
            <ParameterInstance parameterRef="N"/>
            </IndirectParameterRefEntry>
            <ParameterRefEntry parameterRef="Z"/>
          </EntryList></SequenceContainer>
        </ContainerSet>""")


class NamedIndirectTests(unittest.TestCase):
    """A string that is the name of the parameter after it."""

    def node(self):
        return next(n for n in compile_xml(named_mission()).nodes
                    if n.name == "P")

    def test_any_parameter_by_name_or_path(self):
        conds = [it for it in self.node().items if isinstance(it, Cond)]
        self.assertEqual([c.criteria[0].text for c in conds],
                         ["/Sat/N == N or /Sat/N == /Sat/N",
                          "/Sat/N == A or /Sat/N == /Sat/A",
                          "/Sat/N == B or /Sat/N == /Sat/B",
                          "/Sat/N == R or /Sat/N == /Sat/R",
                          "/Sat/N == Z or /Sat/N == /Sat/Z"])
        # Alternatives: at most one of them is there.
        self.assertEqual(len({c.group for c in conds}), 1)
        self.assertTrue(conds[0].group)

    def test_unknown_name_fails(self):
        # Before any choice, N must name a parameter.
        req = self.node().items[1]
        self.assertIsInstance(req, Require)
        self.assertEqual(req.text, "/Sat/N names no parameter")
        self.assertEqual(len(req.criteria[0].terms), 5)

    def test_choices_compare_the_selector(self):
        # N can name itself, but every choice, even after that one, is
        # chosen by N itself, not by the choice that is a copy of it.
        n = self.node()
        sel = n.items[0].local
        conds = [it for it in n.items if isinstance(it, Cond)]
        self.assertTrue(all(t.local is sel for c in conds
                            for t in c.criteria[0].terms))

    def test_one_that_cant_be_read_fails(self):
        (r,) = [it for it in self.node().items if isinstance(it, Cond)
                and isinstance(it.items[0], Require)]
        self.assertIn("/Sat/N names /Sat/R, which can't be read here",
                      r.items[0].text)

    def test_largest_alternative_counts(self):
        # N (a tag and 8 bytes), then the largest alternative (N again, not
        # every one added up), then Z.
        self.assertEqual(self.node().max_bytes, 9 + 9 + 1)

    def test_own_fields_keep_their_names(self):
        self.assertEqual([f for f, _ in self.node().fields],
                         ["n", "n2", "a", "b", "z2", "z"])


def lsb_mission(entries: str) -> str:
    """Builds telemetry with least significant bit first fields.

    A 4-bit N, a byte B, an lsbFirst 2-byte string S and lsbFirst binary L
    whose size B gives, in a container.

    Args:
        entries: The XML of the container's entries.

    Returns:
        The XTCE document.
    """
    return mission(tm=f"""
        <ParameterTypeSet>
          <IntegerParameterType name="U4" signed="false">
          <IntegerDataEncoding sizeInBits="4"/></IntegerParameterType>
          <IntegerParameterType name="U8" signed="false">
          <IntegerDataEncoding sizeInBits="8"/></IntegerParameterType>
          <StringParameterType name="ST">
          <StringDataEncoding bitOrder="leastSignificantBitFirst">
          <SizeInBits><Fixed><FixedValue>16</FixedValue></Fixed>
          </SizeInBits></StringDataEncoding></StringParameterType>
          <BinaryParameterType name="LT">
          <BinaryDataEncoding bitOrder="leastSignificantBitFirst">
          <SizeInBits><DynamicValue><ParameterInstanceRef parameterRef="B"/>
          </DynamicValue></SizeInBits></BinaryDataEncoding>
          </BinaryParameterType>
        </ParameterTypeSet>
        <ParameterSet>
          <Parameter name="N" parameterTypeRef="U4"/>
          <Parameter name="B" parameterTypeRef="U8"/>
          <Parameter name="S" parameterTypeRef="ST"/>
          <Parameter name="L" parameterTypeRef="LT"/>
        </ParameterSet>
        <ContainerSet>
          <SequenceContainer name="P"><EntryList>{entries}</EntryList>
          </SequenceContainer>
        </ContainerSet>""")


def refs(*names: str) -> str:
    return "".join(f'<ParameterRefEntry parameterRef="{n}"/>' for n in names)


class LsbFirstTests(unittest.TestCase):
    def test_aligned(self):
        # Two 4-bit values realign S; variable-size L keeps whole bytes.
        text = lsb_mission(refs("N", "N", "S", "B", "L", "S"))
        n = compile_xml(text).nodes[0]
        self.assertTrue(n.fields[2][1].vtype.enc.lsb)

    def test_misaligned(self):
        # Each byte is a group of 8 bits read least significant first,
        # wherever it starts.
        for entries in (refs("N", "S"), refs("B", "N", "L")):
            with self.subTest(entries=entries):
                n = compile_xml(lsb_mission(entries)).nodes[0]
                self.assertTrue(n.fields[-1][1].vtype.enc.lsb)


class InstanceTests(unittest.TestCase):
    def mission(self, instance: str) -> str:
        """Builds gate_mission with A twice, then B when A is 1 there.

        Args:
            instance: The instance of A the condition compares.

        Returns:
            The XTCE document.
        """
        return gate_mission('<Comparison parameterRef="S" '
                            'value="0"/>').replace(
            '<ParameterRefEntry parameterRef="B"/>',
            '<ParameterRefEntry parameterRef="A"/><ParameterRefEntry '
            'parameterRef="B"><IncludeCondition><Comparison parameterRef="A" '
            f'instance="{instance}" value="1"/></IncludeCondition>'
            '</ParameterRefEntry>')

    def test_previous(self):
        base = compile_xml(self.mission("-1")).nodes[0]
        first, second = [it.local for it in base.all_items()
                         if isinstance(it, Value) and it.local.name == "A"]
        (cond,) = [it for it in base.all_items() if isinstance(it, Cond)]
        self.assertIs(cond.criteria[0].local, first)
        base = compile_xml(self.mission("0")).nodes[0]
        (cond,) = [it for it in base.all_items() if isinstance(it, Cond)]
        self.assertEqual(cond.criteria[0].local.name, "A")
        self.assertIsNot(cond.criteria[0].local, first)

    def test_rejected(self):
        for instance, why in (
                ("1", "instance 1 of /Sat/A, a value yet to come"),
                ("-2",
                 "instance -2 of /Sat/A, which the packet holds 2 times before "
                 "it")):
            with self.subTest(instance=instance):
                with self.assertRaises(Unsupported) as cm:
                    compile_xml(self.mission(instance))
                self.assertIn(why, str(cm.exception))


class MessageTests(unittest.TestCase):
    def mission(self, container: str) -> str:
        """Builds root container R with U8 K, and message M picking a container.

        Args:
            container: The container M refers to when K is 4.

        Returns:
            The XTCE document.
        """
        return mission(tm=f"""
            <ParameterTypeSet>
              <IntegerParameterType name="U8" signed="false">
              <IntegerDataEncoding sizeInBits="8"/></IntegerParameterType>
            </ParameterTypeSet>
            <ParameterSet><Parameter name="K" parameterTypeRef="U8"/>
            </ParameterSet>
            <ContainerSet><SequenceContainer name="R"><EntryList>
              <ParameterRefEntry parameterRef="K"/></EntryList>
            </SequenceContainer></ContainerSet>
            <MessageSet><Message name="M"><MatchCriteria>
              <Comparison parameterRef="K" value="4"/></MatchCriteria>
              <ContainerRef containerRef="{container}"/></Message>
            </MessageSet>""")

    def test_message(self):
        m = compile_xml(self.mission("R"))
        r, msg = m.nodes
        self.assertEqual((msg.key, msg.base), ("/Sat/M", r))
        self.assertEqual(msg.fixed, {r.fields[0][1]: 4})
        self.assertEqual(msg.fields, [])
        self.assertEqual([k.members for k in m.kinds], [[r, msg]])

    def test_unknown_container(self):
        m = compile_xml(self.mission("Q"), skip_unsupported=True)
        self.assertEqual([n.name for n in m.nodes], ["R"])
        self.assertIn("unresolved container reference 'Q'", m.skipped[0])


if __name__ == "__main__":
    unittest.main()
