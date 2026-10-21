"""Tests of the packet compiler (xtcepkt.compile) on small XTCE documents."""

import sys
import unittest
from pathlib import Path

# The compiler reads XTCE documents with the generated `xtce` Python package.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

import xtce  # noqa: E402

from xtcepkt import emit_c  # noqa: E402
from xtcepkt.compile import compile_document, item_bits  # noqa: E402
from xtcepkt.model import MAX_FIELD_BITS, Unsupported  # noqa: E402

NS = "http://www.omg.org/spec/XTCE/20250214"


def mission(tm: str = "", tc: str = "") -> str:
    tm = f"<TelemetryMetaData>{tm}</TelemetryMetaData>" if tm else ""
    tc = f"<CommandMetaData>{tc}</CommandMetaData>" if tc else ""
    return f'<SpaceSystem xmlns="{NS}" name="Sat">{tm}{tc}</SpaceSystem>'


def compile_xml(text: str, **kw):
    return compile_document(xtce.Document.from_bytes(text.encode()).root, **kw)


def command(name: str, args: str = "", entries: str = "", base: str = "") -> str:
    """A MetaCommand with its arguments and command container entries."""
    base = f'<BaseMetaCommand metaCommandRef="{base}"/>' if base else ""
    args = f"<ArgumentList>{args}</ArgumentList>" if args else ""
    return (
        f'<MetaCommand name="{name}">{base}{args}'
        f'<CommandContainer name="{name}C"><EntryList>{entries}</EntryList></CommandContainer>'
        "</MetaCommand>"
    )


class TypeTests(unittest.TestCase):
    def test_parameter_and_argument_types_with_one_name_stay_apart(self):
        text = mission(
            tm="""
            <ParameterTypeSet>
              <IntegerParameterType name="ModeType" signed="false">
                <IntegerDataEncoding sizeInBits="8"/>
              </IntegerParameterType>
            </ParameterTypeSet>
            <ParameterSet><Parameter name="Mode" parameterTypeRef="ModeType"/></ParameterSet>
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
            + command(
                "SetMode",
                '<Argument name="Mode" argumentTypeRef="ModeType"/>',
                '<ArgumentRefEntry argumentRef="Mode"/>',
            )
            + "</MetaCommandSet>",
        )
        m = compile_xml(text)
        nodes = {n.ident: n for n in m.nodes}
        param = nodes["Status"].fields[0][1].vtype
        arg = nodes["SetMode"].fields[0][1].vtype
        self.assertEqual((param.enc.bits, param.signed), (8, False))
        self.assertEqual((arg.enc.bits, arg.signed), (16, True))
        self.assertEqual(nodes["SetMode"].max_bytes, 2)



U8_ARG = """
    <ArgumentTypeSet>
      <IntegerArgumentType name="U8"><IntegerDataEncoding sizeInBits="8"/></IntegerArgumentType>
    </ArgumentTypeSet>"""


class ErrorTests(unittest.TestCase):
    """Bad documents are reported as Unsupported, never as a crash."""

    def unsupported(self, text: str) -> str:
        with self.assertRaises(Unsupported) as cm:
            compile_xml(text)
        return str(cm.exception)

    def test_meta_command_inheritance_cycle(self):
        text = mission(
            tc=U8_ARG
            + "<MetaCommandSet>"
            + command("A", '<Argument name="X" argumentTypeRef="U8"/>', '<ArgumentRefEntry argumentRef="X"/>', "B")
            + command("B", "", "", "A")
            + "</MetaCommandSet>"
        )
        self.assertIn("cycle", self.unsupported(text))
        # --skip-unsupported leaves both commands out.
        self.assertEqual(compile_xml(text, skip_unsupported=True).nodes, [])


    def test_malformed_fixed_value(self):
        for value in ("0xZZ", "0x10", "1_0", "zz"):
            text = mission(
                tc="<MetaCommandSet>"
                + command("Ping", entries=f'<FixedValueEntry binaryValue="{value}" sizeInBits="8"/>')
                + "</MetaCommandSet>"
            )
            self.assertIn("is not hexBinary", self.unsupported(text), value)
            self.assertEqual(compile_xml(text, skip_unsupported=True).nodes, [])

    def test_fixed_values(self):
        for value, bits, expect in (("A5", 8, 0xA5), ("3", 2, 3), ("", 4, 0), ("0102", 16, 0x102)):
            text = mission(
                tc="<MetaCommandSet>"
                + command("Ping", entries=f'<FixedValueEntry binaryValue="{value}" sizeInBits="{bits}"/>')
                + "</MetaCommandSet>"
            )
            fixed = compile_xml(text).nodes[0].items[0]
            self.assertEqual((fixed.bits, fixed.value), (bits, expect), value)

    def test_malformed_termination_char(self):
        text = mission(
            tm="""
            <ParameterTypeSet>
              <StringParameterType name="S">
                <StringDataEncoding>
                  <Variable maxSizeInBits="64"><TerminationChar>GG</TerminationChar></Variable>
                </StringDataEncoding>
              </StringParameterType>
            </ParameterTypeSet>
            <ParameterSet><Parameter name="Name" parameterTypeRef="S"/></ParameterSet>
            <ContainerSet>
              <SequenceContainer name="Status">
                <EntryList><ParameterRefEntry parameterRef="Name"/></EntryList>
              </SequenceContainer>
            </ContainerSet>"""
        )
        self.assertIn("is not hexBinary", self.unsupported(text))


    def test_reports_root_causes(self):
        derived = "".join(
            f'''<SequenceContainer name="D{i}">
                  <EntryList/>
                  <BaseContainer containerRef="Base"/>
                </SequenceContainer>'''
            for i in range(3)
        )
        text = mission(
            tm=f"""
            <ParameterTypeSet>
              <IntegerParameterType name="U3LE" signed="false">
                <IntegerDataEncoding sizeInBits="3" byteOrder="leastSignificantByteFirst"/>
              </IntegerParameterType>
            </ParameterTypeSet>
            <ParameterSet><Parameter name="X" parameterTypeRef="U3LE"/></ParameterSet>
            <ContainerSet>
              <SequenceContainer name="Base" abstract="true">
                <EntryList><ParameterRefEntry parameterRef="X"/></EntryList>
              </SequenceContainer>
              {derived}
            </ContainerSet>"""
        )
        msg = self.unsupported(text)
        self.assertEqual(msg.count("not whole bytes"), 1, msg)
        self.assertNotIn("/Sat/D0", msg)
        self.assertIn("3 containers or meta-commands derived from them", msg)
        # Skipping still names every node left out.
        skipped = compile_xml(text, skip_unsupported=True).skipped
        self.assertEqual(len(skipped), 4, skipped)
        self.assertIn("not whole bytes", skipped[0])


    def test_negative_sizes(self):
        # Yamcs writes <FixedValue>-1</FixedValue> for "no fixed size".
        for ptype, why in (
            (
                "<BinaryParameterType name='T'><BinaryDataEncoding><SizeInBits>"
                "<FixedValue>-1</FixedValue></SizeInBits></BinaryDataEncoding></BinaryParameterType>",
                "binary size -1 is negative",
            ),
            (
                "<StringParameterType name='T'><StringDataEncoding><SizeInBits><Fixed>"
                "<FixedValue>-8</FixedValue></Fixed><TerminationChar>00</TerminationChar>"
                "</SizeInBits></StringDataEncoding></StringParameterType>",
                "string size -8 is negative",
            ),
            (
                "<StringParameterType name='T'><StringDataEncoding>"
                "<Variable maxSizeInBits='-8'><TerminationChar>00</TerminationChar></Variable>"
                "</StringDataEncoding></StringParameterType>",
                "string size -8 is negative",
            ),
        ):
            text = mission(
                tm=f"""
                <ParameterTypeSet>{ptype}</ParameterTypeSet>
                <ParameterSet><Parameter name="X" parameterTypeRef="T"/></ParameterSet>
                <ContainerSet>
                  <SequenceContainer name="Status">
                    <EntryList><ParameterRefEntry parameterRef="X"/></EntryList>
                  </SequenceContainer>
                </ContainerSet>"""
            )
            self.assertIn(why, self.unsupported(text))


if __name__ == "__main__":
    unittest.main()


def dim(start: str, end: str) -> str:
    return f"<Dimension><StartingIndex>{start}</StartingIndex><EndingIndex>{end}</EndingIndex></Dimension>"


def fixed(v: int) -> str:
    return f"<FixedValue>{v}</FixedValue>"


def dynamic(param: str, slope: str = "1", intercept: str = "0") -> str:
    return (
        f'<DynamicValue><ParameterInstanceRef parameterRef="{param}"/>'
        f'<LinearAdjustment slope="{slope}" intercept="{intercept}"/></DynamicValue>'
    )


def array_mission(dims: str, entries: str, count_type: str = "U8", element: str = "U16") -> str:
    """Telemetry with an array type ``A`` of ``element`` and the parameters
    ``N`` (a count) and ``Arr``, and a container with ``entries``."""
    return mission(
        tm=f"""
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
          <SequenceContainer name="P"><EntryList>{entries}</EntryList></SequenceContainer>
        </ContainerSet>""",
    )


COUNT_THEN_ARRAY = '<ParameterRefEntry parameterRef="N"/><ArrayParameterRefEntry parameterRef="Arr"/>'


class ArrayTests(unittest.TestCase):
    def array(self, m):
        return next(lc for _, lc in m.nodes[0].fields if lc.name == "Arr").vtype

    def unsupported(self, text: str, message: str) -> None:
        with self.assertRaises(Unsupported) as cm:
            compile_xml(text)
        self.assertIn(message, str(cm.exception))

    def test_fixed_dimensions_multiply(self):
        dims = dim(fixed(0), fixed(1)) + dim(fixed(2), fixed(4))
        m = compile_xml(array_mission(dims, '<ArrayParameterRefEntry parameterRef="Arr"/>'))
        self.assertEqual(self.array(m).enc.count, 6)
        self.assertEqual(m.nodes[0].max_bytes, 12)

    def test_count_bounded_by_valid_range(self):
        m = compile_xml(array_mission(dim(fixed(0), dynamic("N", intercept="-1")), COUNT_THEN_ARRAY, "Small"))
        d = self.array(m).enc.count
        self.assertEqual((d.slope, d.intercept, d.max, d.local.name), (1.0, 0.0, 5, "N"))
        self.assertEqual(m.nodes[0].max_bytes, 1 + 5 * 2)

    def test_dynamic_dimension_times_fixed_ones(self):
        dims = dim(fixed(0), dynamic("N")) + dim(fixed(0), fixed(2))
        m = compile_xml(array_mission(dims, COUNT_THEN_ARRAY, "Small"))
        d = self.array(m).enc.count
        # N + 1 rows of 3, with N at most 5: the intercept holds the + 1.
        self.assertEqual((d.intercept, d.factor, d.max, d.max_total), (1.0, 3, 6, 18))

    def test_count_without_range_is_capped(self):
        m = compile_xml(array_mission(dim(fixed(0), dynamic("N")), COUNT_THEN_ARRAY, "U32"))
        vt = self.array(m)
        self.assertEqual(vt.enc.max_count, MAX_FIELD_BITS // 16)
        self.assertEqual(item_bits(vt), MAX_FIELD_BITS)

    def test_rejected(self):
        cases = [
            (dim(fixed(0), dynamic("N")), '<ArrayParameterRefEntry parameterRef="Arr"/><ParameterRefEntry parameterRef="N"/>',
             "U8", "which is not decoded before it"),
            (dim(fixed(0), dynamic("F")), '<ParameterRefEntry parameterRef="F"/><ArrayParameterRefEntry parameterRef="Arr"/>',
             "U8", "size or count from float value"),
            (dim(dynamic("N"), fixed(3)), COUNT_THEN_ARRAY, "U8", "StartingIndex from DynamicValue"),
            (dim(fixed(0), dynamic("N")) * 2, COUNT_THEN_ARRAY, "U8", "more than one dimension from DynamicValue"),
            (dim(fixed(3), fixed(1)), COUNT_THEN_ARRAY, "U8", "EndingIndex 1 is before StartingIndex 3"),
            (dim(fixed(0), fixed(1)), '<ArrayParameterRefEntry parameterRef="Nested"/>', "U8", "arrays of arrays"),
        ]
        for dims, entries, count_type, message in cases:
            with self.subTest(message):
                self.unsupported(array_mission(dims, entries, count_type), message)

    def test_repeat_entry(self):
        entries = (
            '<ParameterRefEntry parameterRef="N"/>'
            '<ParameterRefEntry parameterRef="N"><RepeatEntry>'
            f'<Count>{dynamic("N")}</Count><Offset>{fixed(2)}</Offset>'
            "</RepeatEntry></ParameterRefEntry>"
        )
        m = compile_xml(array_mission(dim(fixed(0), fixed(0)), entries, "Small"))
        rep = m.nodes[0].fields[1][1].vtype
        self.assertEqual((rep.kind, rep.enc.gap, rep.enc.count.max), ("array", 2, 5))
        self.assertEqual(m.nodes[0].max_bytes, (8 + 5 * 8 + 4 * 2 + 7) // 8)
        once = '<ParameterRefEntry parameterRef="N"><RepeatEntry><Count><FixedValue>1</FixedValue></Count></RepeatEntry></ParameterRefEntry>'
        self.assertEqual(compile_xml(array_mission(dim(fixed(0), fixed(0)), once)).nodes[0].fields[0][1].vtype.kind, "int")
        on_array = (
            '<ArrayParameterRefEntry parameterRef="Arr"><RepeatEntry><Count><FixedValue>2</FixedValue></Count></RepeatEntry>'
            "</ArrayParameterRefEntry>"
        )
        self.unsupported(array_mission(dim(fixed(0), fixed(0)), on_array), "RepeatEntry on an array")

    def test_criterion_on_array(self):
        text = array_mission(dim(fixed(0), fixed(0)), '<ArrayParameterRefEntry parameterRef="Arr"/>').replace(
            '<SequenceContainer name="P"><EntryList><ArrayParameterRefEntry parameterRef="Arr"/></EntryList></SequenceContainer>',
            '<SequenceContainer name="B" abstract="true"><EntryList><ArrayParameterRefEntry parameterRef="Arr"/></EntryList>'
            "</SequenceContainer>"
            '<SequenceContainer name="P"><EntryList/><BaseContainer containerRef="B"><RestrictionCriteria>'
            '<Comparison parameterRef="Arr" value="1"/></RestrictionCriteria></BaseContainer></SequenceContainer>',
        )
        self.unsupported(text, "which is an array")

    def test_c_storage(self):
        m = compile_xml(array_mission(dim(fixed(0), dynamic("N", intercept="-1")), COUNT_THEN_ARRAY, "Small"))
        header = emit_c.emit(m, "t.xml")
        self.assertIn("typedef struct sat_uint16_list5 {", header)
        self.assertIn("uint16_t data[5];", header)


def aggregate_mission(types: str, entries: str = '<ParameterRefEntry parameterRef="Agg"/>') -> str:
    """Telemetry with an aggregate parameter ``Agg`` of type ``A``."""
    return mission(
        tm=f"""
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
          <SequenceContainer name="P"><EntryList>{entries}</EntryList></SequenceContainer>
        </ContainerSet>""",
    )


def aggregate(name: str, *members: tuple[str, str]) -> str:
    body = "".join(f'<Member name="{n}" typeRef="{t}"/>' for n, t in members)
    return f'<AggregateParameterType name="{name}"><MemberList>{body}</MemberList></AggregateParameterType>'


class AggregateTests(unittest.TestCase):
    def test_nested_members(self):
        types = aggregate("A", ("Count", "U8"), ("type", "U8"), ("Inner", "B")) + aggregate("B", ("X", "U8"))
        m = compile_xml(aggregate_mission(types))
        vt = m.nodes[0].fields[0][1].vtype
        self.assertEqual([x.ident for x in vt.enc.members], ["count", "type_", "inner"])
        # Inner aggregates come first, so that each is declared before use.
        self.assertEqual([a.ident for a in m.aggregates], ["B", "A"])
        self.assertEqual(m.nodes[0].max_bytes, 3)

    def test_rejected(self):
        cases = [
            (aggregate("A", ("X", "B")) + aggregate("B", ("Y", "A")), "aggregate contains itself"),
            (
                aggregate("A", ("Bytes", "Blob"))
                + '<BinaryParameterType name="Blob"><BinaryDataEncoding><SizeInBits>'
                + dynamic("N")
                + "</SizeInBits></BinaryDataEncoding></BinaryParameterType>",
                "member Bytes has a size that another value gives",
            ),
        ]
        for types, message in cases:
            with self.subTest(message), self.assertRaises(Unsupported) as cm:
                compile_xml(aggregate_mission(types, '<ParameterRefEntry parameterRef="N"/><ParameterRefEntry parameterRef="Agg"/>'))
            self.assertIn(message, str(cm.exception))

    def test_criterion_on_aggregate(self):
        types = aggregate("A", ("X", "U8"))
        text = aggregate_mission(types).replace(
            '<SequenceContainer name="P"><EntryList><ParameterRefEntry parameterRef="Agg"/></EntryList></SequenceContainer>',
            '<SequenceContainer name="B" abstract="true"><EntryList><ParameterRefEntry parameterRef="Agg"/></EntryList>'
            "</SequenceContainer>"
            '<SequenceContainer name="P"><EntryList/><BaseContainer containerRef="B"><RestrictionCriteria>'
            '<Comparison parameterRef="Agg" value="1"/></RestrictionCriteria></BaseContainer></SequenceContainer>',
        )
        with self.assertRaises(Unsupported) as cm:
            compile_xml(text)
        self.assertIn("which is an aggregate", str(cm.exception))
