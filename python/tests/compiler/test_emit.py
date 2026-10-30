"""Tests that the code the emitters write compiles, for packets the demo
mission doesn't have."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOP = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(TOP / "python"))

import tcs_xtce  # noqa: E402

from tcs_xtce.compiler import emit_c, emit_python, emit_rust  # noqa: E402
from tcs_xtce.compiler.compile import compile_document  # noqa: E402

NS = "http://www.omg.org/spec/XTCE/20250214"

# A meta-command with arguments but no command container has nothing to
# read or write, which once left unused variables and an empty Python
# function body.
NO_ENTRIES = f"""<SpaceSystem xmlns="{NS}" name="Sat"><CommandMetaData>
  <ArgumentTypeSet>
    <IntegerArgumentType name="U8"><IntegerDataEncoding sizeInBits="8"/>
    </IntegerArgumentType>
  </ArgumentTypeSet>
  <MetaCommandSet>
    <MetaCommand name="Base">
      <ArgumentList><Argument name="Op" argumentTypeRef="U8"/></ArgumentList>
    </MetaCommand>
    <MetaCommand name="Child">
      <BaseMetaCommand metaCommandRef="Base">
        <ArgumentAssignmentList>
          <ArgumentAssignment argumentName="Op" argumentValue="1"/>
        </ArgumentAssignmentList>
      </BaseMetaCommand>
      <ArgumentList><Argument name="A" argumentTypeRef="U8"/></ArgumentList>
      <CommandContainer name="ChildC">
        <EntryList><ArgumentRefEntry argumentRef="A"/></EntryList>
      </CommandContainer>
    </MetaCommand>
  </MetaCommandSet>
</CommandMetaData></SpaceSystem>"""


def mission(text: str):
    return compile_document(tcs_xtce.Document.from_bytes(text.encode()).root)


class EmptyPacketTests(unittest.TestCase):
    def test_python_compiles(self):
        code = emit_python.emit(mission(NO_ENTRIES), "test.xml")
        compile(code, "sat.py", "exec")

    @unittest.skipIf(shutil.which("cc") is None, "needs a C compiler")
    def test_c_compiles_without_warnings(self):
        code = emit_c.emit(mission(NO_ENTRIES), "test.xml")
        with tempfile.TemporaryDirectory() as d:
            header = Path(d) / "sat.h"
            header.write_text(code)
            for lang in ("c", "c++"):
                with self.subTest(lang=lang):
                    r = subprocess.run(
                        ["cc" if lang == "c" else "c++", "-x", lang,
                         "-std=c11" if lang == "c" else "-std=c++20", "-Wall",
                         "-Wextra", "-Werror", "-fsyntax-only",
                         f"-I{TOP / 'cpp' / 'include'}", str(header)],
                        capture_output=True, text=True)
                    self.assertEqual(r.returncode, 0, r.stderr)


# A packet whose binary Data, sized in bits by a 32-bit Len, may take 512 MiB:
# too much to count in bits in a 32-bit size_t.
HUGE = f"""<SpaceSystem xmlns="{NS}" name="Sat">
<AncillaryDataSet>
  <AncillaryData name="tcs-xtce.max-field-bytes">2147483648</AncillaryData>
</AncillaryDataSet>
<TelemetryMetaData>
  <ParameterTypeSet>
    <IntegerParameterType name="U32" signed="false">
      <IntegerDataEncoding sizeInBits="32"/>
    </IntegerParameterType>
    <BinaryParameterType name="Bin"><BinaryDataEncoding><SizeInBits>
      <DynamicValue><ParameterInstanceRef parameterRef="Len"/></DynamicValue>
    </SizeInBits></BinaryDataEncoding></BinaryParameterType>
  </ParameterTypeSet>
  <ParameterSet>
    <Parameter name="Len" parameterTypeRef="U32"/>
    <Parameter name="Data" parameterTypeRef="Bin"/>
  </ParameterSet>
  <ContainerSet>
    <SequenceContainer name="Big"><EntryList>
      <ParameterRefEntry parameterRef="Len"/>
      <ParameterRefEntry parameterRef="Data"/>
    </EntryList></SequenceContainer>
  </ContainerSet>
</TelemetryMetaData></SpaceSystem>"""


class HugePacketTests(unittest.TestCase):
    def test_c_refuses_a_narrow_size_t(self):
        code = emit_c.emit(mission(HUGE), "test.xml")
        self.assertIn("#if SIZE_MAX / 8 < sat_Big_MAX_BYTES\n"
                      '#error "/Sat/Big is too large for this size_t"\n'
                      "#endif", code)

    @unittest.skipIf(shutil.which("cc") is None, "needs a C compiler")
    def test_c_compiles_with_a_wide_size_t(self):
        code = emit_c.emit(mission(HUGE), "test.xml")
        with tempfile.TemporaryDirectory() as d:
            header = Path(d) / "sat.h"
            header.write_text(code)
            r = subprocess.run(
                ["cc", "-x", "c", "-std=c11", "-Wall", "-Wextra", "-Werror",
                 "-fsyntax-only", f"-I{TOP / 'cpp' / 'include'}", str(header)],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_rust_refuses_a_narrow_usize(self):
        code = emit_rust.emit(mission(HUGE), "test.xml")
        self.assertIn("const _: () = assert!(536870915u64 <= usize::MAX as "
                      'u64 / 8, "/Sat/Big is too large for this usize");', code)

    def test_small_packets_have_no_check(self):
        self.assertNotIn("SIZE_MAX / 8",
                         emit_c.emit(mission(NO_ENTRIES), "test.xml"))
        self.assertNotIn("usize::MAX as u64 / 8",
                         emit_rust.emit(mission(NO_ENTRIES), "test.xml"))


if __name__ == "__main__":
    unittest.main()
