"""Tests of the packet compiler (xtcepkt.compile) on small XTCE documents."""

import sys
import unittest
from pathlib import Path

# The compiler reads XTCE documents with the generated `xtce` Python package.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

import xtce  # noqa: E402

from xtcepkt import emit_c  # noqa: E402
from xtcepkt.compile import (  # noqa: E402
    compile_document, compile_documents, item_bits, member_path, value_range)
from xtcepkt.model import (  # noqa: E402
    MAX_FIELD_BITS, AnyOf, Index, Pad, Seek, Unsupported)

NS = "http://www.omg.org/spec/XTCE/20250214"


def mission(tm: str = "", tc: str = "") -> str:
    tm = f"<TelemetryMetaData>{tm}</TelemetryMetaData>" if tm else ""
    tc = f"<CommandMetaData>{tc}</CommandMetaData>" if tc else ""
    return f'<SpaceSystem xmlns="{NS}" name="Sat">{tm}{tc}</SpaceSystem>'


def compile_xml(text: str, **kw):
    return compile_document(xtce.Document.from_bytes(text.encode()).root, **kw)


def command(name: str, args: str = "", entries: str = "",
            base: str = "") -> str:
    """A MetaCommand with its arguments and command container entries."""
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
              <IntegerParameterType name="U3LE" signed="false">
                <IntegerDataEncoding sizeInBits="3"
                  byteOrder="leastSignificantByteFirst"/>
              </IntegerParameterType>
            </ParameterTypeSet>
            <ParameterSet><Parameter name="X" parameterTypeRef="U3LE"/>
            </ParameterSet>
            <ContainerSet>
              <SequenceContainer name="Base" abstract="true">
                <EntryList><ParameterRefEntry parameterRef="X"/></EntryList>
              </SequenceContainer>
              {derived}
            </ContainerSet>""")
        msg = self.unsupported(text)
        self.assertEqual(msg.count("not whole bytes"), 1, msg)
        self.assertNotIn("/Sat/D0", msg)
        self.assertIn("3 containers or meta-commands derived from them", msg)
        # Skipping still names every node left out.
        skipped = compile_xml(text, skip_unsupported=True).skipped
        self.assertEqual(len(skipped), 4, skipped)
        self.assertIn("not whole bytes", skipped[0])


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


def calibrated_mission(itype: str,
                       entries: str = '<ParameterRefEntry '
                                      'parameterRef="X"/>') -> str:
    """Telemetry with parameter X of type ``itype`` and a binary Rest."""
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


class RestTests(unittest.TestCase):
    def test_last_value_takes_the_rest(self):
        m = compile_xml(calibrated_mission(calibrated(),
                                           '<ParameterRefEntry '
                                           'parameterRef="Rest"/>'))
        vt = m.nodes[0].fields[0][1].vtype
        self.assertTrue(vt.enc.rest)
        self.assertEqual(vt.enc.bits, MAX_FIELD_BITS)

    def test_must_be_last(self):
        text = calibrated_mission(calibrated(),
                                  '<ParameterRefEntry parameterRef="Rest"/>'
                                  '<ParameterRefEntry parameterRef="X"/>')
        with self.assertRaises(Unsupported) as cm:
            compile_xml(text)
        self.assertIn("Rest takes the rest of the packet but is not last",
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
    """Telemetry with an array type ``A`` of ``element`` and the parameters
    ``N`` (a count) and ``Arr``, and a container with ``entries``."""
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
             "EndingIndex 1 is before StartingIndex 3"),
            (dim(fixed(0), fixed(1)),
             '<ArrayParameterRefEntry parameterRef="Nested"/>', "U8",
             "arrays of arrays")]
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
        on_array = ('<ArrayParameterRefEntry parameterRef="Arr"><RepeatEntry>'
                    '<Count><FixedValue>2</FixedValue></Count></RepeatEntry>'
                    '</ArrayParameterRefEntry>')
        self.unsupported(array_mission(dim(fixed(0), fixed(0)), on_array),
                         "RepeatEntry on an array")

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
        header = emit_c.emit(m, "t.xml")
        self.assertIn("typedef struct sat_uint16_list5 {", header)
        self.assertIn("uint16_t data[5];", header)


def aggregate_mission(types: str,
                      entries: str = '<ParameterRefEntry '
                                     'parameterRef="Agg"/>') -> str:
    """Telemetry with an aggregate parameter ``Agg`` of type ``A``."""
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
    """Telemetry where Status includes container Record with ``entry``
    (the inside of a ContainerRefEntry); ``record`` is Record's contents."""
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

    def test_rejected(self):
        for record, entry, why in (
                (size(8) + entries(ref("A"), ref("B")), "",
                 "entries take 12 bits, more than the container size 8"),
                (size(64) + entries(ref("Text")), "",
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



def bool_string(attrs: str, bits: int = 32) -> str:
    """Telemetry with a boolean X encoded as a fixed, terminated string."""
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
    """A DiscreteLookupList on parameter N: (value of N, result) pairs."""
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
    """Telemetry with U8 parameters A and B, a float F, and a derived
    container chosen by ``criteria`` (the inside of RestrictionCriteria)."""
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
    """A BooleanExpression Condition; ``rhs`` is a parameter name or =value."""
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
        for cond, why in (
                (check("A", "<", "S"),
                 "comparison of signed and unsigned values /Sat/A and /Sat/S"),
                (check("A", "==", "X"),
                 "comparison of int /Sat/A and float /Sat/X")):
            with self.subTest(why=why), self.assertRaises(Unsupported) as cm:
                compile_xml(gate_mission(f"<BooleanExpression>{cond}"
                                         "</BooleanExpression>"))
            self.assertIn(why, str(cm.exception))



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
        """Arr of type A with ``dims``, then F included when ``ref`` == 1."""
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
                (dim(fixed(0), fixed(1)) + dim(fixed(0), dynamic("N")),
                 "Arr[0][0]",
                 "element of an array whose count varies in a dimension other "
                 "than the first"),
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
    return compile_documents([xtce.Document.from_bytes(t.encode()).root
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
    """A mission with the parameter types ``types`` and one packet holding a
    parameter of type ``ref``."""
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


if __name__ == "__main__":
    unittest.main()


def length_tag(adjust: str = "-1") -> str:
    return (f'<AncillaryDataSet><AncillaryData name="tcs-xtce.length">{adjust}'
            '</AncillaryData></AncillaryDataSet>')


def length_mission(entries: str, len_type: str = "U16", tag: str = length_tag(),
                   containers: str = "") -> str:
    """Telemetry with a packet length field Len, a second one Len2, and
    values X, Data and Looked in container Status."""
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
                 "must be an uncalibrated unsigned integer"),
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
                 "size or count from packet length field"),
                (dict(entries=LEN
                      + '<ParameterRefEntry parameterRef="Looked"/>'),
                 "size or count from packet length field Len"),
                (dict(entries='<ParameterRefEntry parameterRef="Len">'
                              '<RepeatEntry><Count><FixedValue>2</FixedValue>'
                              '</Count></RepeatEntry></ParameterRefEntry>'),
                 "RepeatEntry on packet length field Len"),
                (dict(entries='<ContainerRefEntry containerRef="Rec">'
                              '<RepeatEntry><Count><FixedValue>2</FixedValue>'
                              '</Count></RepeatEntry></ContainerRefEntry>',
                      containers=repeated), "Len is a packet length field")):
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
