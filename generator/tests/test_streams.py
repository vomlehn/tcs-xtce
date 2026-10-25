"""Tests of the streams the compiler takes from a StreamSet."""

import unittest

from tests.test_compile import compile_xml, mission


def stream_mission(stream: str) -> str:
    """Telemetry with a container F and a StreamSet holding ``stream``."""
    return mission(tm=f"""
        <ParameterTypeSet>
          <IntegerParameterType name="U8" signed="false">
          <IntegerDataEncoding sizeInBits="8"/></IntegerParameterType>
        </ParameterTypeSet>
        <ParameterSet><Parameter name="A" parameterTypeRef="U8"/>
        </ParameterSet>
        <ContainerSet><SequenceContainer name="F"><EntryList>
          <ParameterRefEntry parameterRef="A"/></EntryList>
        </SequenceContainer></ContainerSet>
        <StreamSet>{stream}</StreamSet>""")


def fixed(pattern: str, extra: str = "", attrs: str = "") -> str:
    return f"""<FixedFrameStream name="S" frameLengthInBits="64" {attrs}>
        <ContainerRef containerRef="F"/>
        <SyncStrategy>{extra}<SyncPattern {pattern}/></SyncStrategy>
        </FixedFrameStream>"""


class StreamTests(unittest.TestCase):
    def test_fixed(self):
        m = compile_xml(
            stream_mission(
                fixed('pattern="FF1ACFFC1D" patternLengthInBits="32" '
                      'mask="F0F0" maskLengthInBits="12"',
                      '<AutoInvert badFramesToAutoInvert="2"/>',
                      'syncApertureInBits="2" pcmType="NRZS" inverted="true"')))
        (st,) = m.streams
        self.assertEqual((st.ident, st.container, st.fixed),
                         ("SStream", "/Sat/F", True))
        # Truncated from the left: the pattern to 32 bits, the mask to 12,
        # and the bits the mask doesn't reach are compared.
        self.assertEqual((st.pattern, st.mask), (0x1ACFFC1D, 0xFFFFF0F0))
        self.assertEqual((st.aperture, st.invert_after, st.pcm, st.inverted),
                         (2, 128, "NRZS", True))
        self.assertEqual((st.verify, st.check), (4, 1))

    def test_variable(self):
        m = compile_xml(stream_mission('<VariableFrameStream name="V">'
                                       '<ContainerRef containerRef="F"/>'
                                       '<SyncStrategy><Flag flagSizeInBits="7" '
                                       'flagBitType="zeros"/></SyncStrategy>'
                                       '</VariableFrameStream>'))
        (st,) = m.streams
        self.assertEqual((st.fixed, st.flag_bits, st.flag_ones, st.max_bits),
                         (False, 7, False, 8))

    def test_unsupported(self):
        for stream, why in (
                (fixed('pattern="1ACF" patternLengthInBits="16"', "",
                       'pcmType="BiPhaseL"'), "pcmType BiPhaseL"),
                (fixed('pattern="1ACF" patternLengthInBits="16" '
                       'bitLocationFromStartOfContainer="50"'),
                 "a sync pattern outside the frame"),
                (fixed('pattern="1ACF" patternLengthInBits="16"', "",
                       'syncApertureInBits="32"'),
                 "a sync aperture of half a frame or more")):
            with self.subTest(why=why):
                m = compile_xml(stream_mission(stream), skip_unsupported=True)
                self.assertEqual(m.streams, [])
                self.assertIn(why, m.skipped[0])


if __name__ == "__main__":
    unittest.main()
