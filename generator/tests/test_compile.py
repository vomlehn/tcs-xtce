"""Tests of the packet compiler (xtcepkt.compile) on small XTCE documents."""

import sys
import unittest
from pathlib import Path

# The compiler reads XTCE documents with the generated `xtce` Python package.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

import xtce  # noqa: E402

from xtcepkt import emit_c  # noqa: E402
from xtcepkt.compile import compile_document  # noqa: E402
from xtcepkt.model import Unsupported  # noqa: E402

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
