"""Tests of the streams the compiler takes from a StreamSet."""

import unittest

from tests.test_compile import compile_xml, mission


def stream_mission(stream: str) -> str:
    """Builds telemetry with a container F and a StreamSet.

    Args:
        stream: The XML inside the StreamSet.

    Returns:
        The XTCE document.
    """
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


def fixed(pattern: str, extra: str = "", attrs: str = "",
          frame_bits: int = 64) -> str:
    """Builds a FixedFrameStream S carrying container F.

    Args:
        pattern:    The SyncPattern element's attributes.
        extra:      XML to put in the SyncStrategy before the SyncPattern.
        attrs:      More attributes of the FixedFrameStream.
        frame_bits: The frame length in bits.

    Returns:
        The FixedFrameStream's XML.
    """
    return f"""<FixedFrameStream name="S" frameLengthInBits="{frame_bits}"
        {attrs}>
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

    def test_service(self):
        text = stream_mission('<VariableFrameStream name="V"><ServiceRef '
                              'serviceRef="Svc"/><SyncStrategy><Flag/>'
                              '</SyncStrategy></VariableFrameStream>')
        text = text.replace("</TelemetryMetaData>",
                            "</TelemetryMetaData>"
                            '<ServiceSet><Service name="Svc"><ContainerRefSet>'
                            '<ContainerRef containerRef="F"/></ContainerRefSet>'
                            '</Service></ServiceSet>')
        (st,) = compile_xml(text).streams
        self.assertEqual((st.container, st.service, st.max_bits),
                         ("/Sat/Svc", ["/Sat/F"], 8))

    def test_biphase_and_long_patterns(self):
        for pcm in ("BiPhaseL", "BiPhaseM", "BiPhaseS"):
            with self.subTest(pcm=pcm):
                m = compile_xml(
                    stream_mission(fixed('pattern="FAF320034ACFFC1D5A5A" '
                                         'patternLengthInBits="80"', "",
                                         f'pcmType="{pcm}"', 160)))
                st = m.streams[0]
                self.assertEqual((st.pcm, st.pattern_bits), (pcm, 80))
                self.assertEqual(st.pattern, 0xFAF320034ACFFC1D5A5A)

    def test_wide_aperture(self):
        m = compile_xml(stream_mission(fixed('pattern="1ACF" '
                                             'patternLengthInBits="16"', "",
                                             'syncApertureInBits="100"')))
        self.assertEqual(m.streams[0].aperture, 100)

    def test_chains(self):
        custom = ('<CustomStream name="C" encodedStreamRef="C" '
                  'decodedStreamRef="S"><EncodingAlgorithm name="E"/>'
                  '<DecodingAlgorithm name="D"/></CustomStream>')
        nested = ('<VariableFrameStream name="V"><ContainerRef '
                  'containerRef="F"/><StreamRef streamRef="S"/><SyncStrategy>'
                  '<Flag/></SyncStrategy></VariableFrameStream>')
        inverted = fixed('pattern="1ACF" patternLengthInBits="16"',
                         '<AutoInvert><InvertAlgorithm name="I"/></AutoInvert>')
        m = compile_xml(stream_mission(nested + inverted + custom))
        c, s, v = m.streams  # each after the streams that feed it
        self.assertEqual((c.key, c.custom, c.decoder, c.feeder),
                         ("/Sat/C", True, "D", None))
        self.assertEqual((s.feeder, s.inverter), (c, "I"))
        self.assertIs(v.feeder, s)
        self.assertEqual([(k, x.key) for k, x in v.functions()],
                         [("decode", "/Sat/C"), ("invert", "/Sat/S")])

    def test_bad_chains(self):
        custom = ('<CustomStream name="C" encodedStreamRef="C" '
                  'decodedStreamRef="{0}"><EncodingAlgorithm name="E"/>'
                  '<DecodingAlgorithm name="D"/></CustomStream>')
        looped = ('<VariableFrameStream name="V"><ContainerRef '
                  'containerRef="F"/><StreamRef streamRef="W"/><SyncStrategy>'
                  '<Flag/></SyncStrategy></VariableFrameStream>'
                  '<VariableFrameStream name="W"><ContainerRef '
                  'containerRef="F"/><StreamRef streamRef="V"/><SyncStrategy>'
                  '<Flag/></SyncStrategy></VariableFrameStream>')
        for stream, why in ((custom.format("C"), "decodes into itself"),
                            (looped, "feed each other in a cycle")):
            with self.subTest(why=why):
                m = compile_xml(stream_mission(stream), skip_unsupported=True)
                self.assertIn(why, " ".join(m.skipped))

    def test_unsupported(self):
        for stream, why in ((fixed('pattern="1ACF" patternLengthInBits="16" '
                                   'bitLocationFromStartOfContainer="50"'),
                             "a sync pattern outside the frame"),):
            with self.subTest(why=why):
                m = compile_xml(stream_mission(stream), skip_unsupported=True)
                self.assertEqual(m.streams, [])
                self.assertIn(why, m.skipped[0])


if __name__ == "__main__":
    unittest.main()
