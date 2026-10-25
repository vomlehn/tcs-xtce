"""Tests that the code the emitters write compiles, for packets the demo
mission doesn't have."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOP / "python"))

import xtce  # noqa: E402

from xtcepkt import emit_c, emit_python  # noqa: E402
from xtcepkt.compile import compile_document  # noqa: E402

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
    return compile_document(xtce.Document.from_bytes(text.encode()).root)


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


if __name__ == "__main__":
    unittest.main()
