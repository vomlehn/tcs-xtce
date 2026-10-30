"""Tests of custom algorithms, which become hooks the application
supplies."""

import unittest

from tests.compiler.test_compile import compile_xml, mission
from tcs_xtce.compiler.model import Custom, Unsupported


def hook_mission(entries: str, alarm: str = "", algorithms: str = "",
                 encoding: str = "") -> str:
    """Builds telemetry for hook tests.

    Telemetry with U8 A (whose type has ``alarm``), U8 B, float O and P,
    container C, and ``algorithms`` in the AlgorithmSet.

    Args:
        entries:    The XML of C's entries.
        alarm:      The XML that follows the encoding in A's type.
        algorithms: The XML inside the AlgorithmSet; "" for none.
        encoding:   C's BinaryEncoding element, or "" for none.

    Returns:
        The XTCE document.
    """
    algs = f"<AlgorithmSet>{algorithms}</AlgorithmSet>" if algorithms else ""
    return mission(tm=f"""
        <ParameterTypeSet>
          <IntegerParameterType name="AT" signed="false">
          <IntegerDataEncoding sizeInBits="8"/>{alarm}</IntegerParameterType>
          <IntegerParameterType name="U8" signed="false">
          <IntegerDataEncoding sizeInBits="8"/></IntegerParameterType>
          <FloatParameterType name="F"><FloatDataEncoding sizeInBits="32"/>
          </FloatParameterType>
        </ParameterTypeSet>
        <ParameterSet>
          <Parameter name="A" parameterTypeRef="AT"/>
          <Parameter name="B" parameterTypeRef="U8"/>
          <Parameter name="O" parameterTypeRef="F"/>
          <Parameter name="P" parameterTypeRef="F"/>
        </ParameterSet>
        <ContainerSet><SequenceContainer name="C">{encoding}
          <EntryList>{entries}</EntryList></SequenceContainer>
        </ContainerSet>{algs}""")


A = '<ParameterRefEntry parameterRef="A"/>'
CUSTOM = ('<CustomAlgorithm name="Odd"><InputSet><InputParameterInstanceRef '
          'parameterRef="A"/><Constant constantName="k" value="2.5"/>'
          '</InputSet></CustomAlgorithm>')
B_IF_ODD = (f'<ParameterRefEntry parameterRef="B"><IncludeCondition>{CUSTOM}'
            '</IncludeCondition></ParameterRefEntry>')


class HookTests(unittest.TestCase):
    def test_criterion(self):
        m = compile_xml(hook_mission(A + B_IF_ODD))
        (h,) = m.hooks
        self.assertEqual((h.kind, h.name, h.ident),
                         ("criterion", "/Sat/Odd", "odd"))
        n = m.nodes[0]
        self.assertEqual(n.hooks, [h])
        (cond,) = [it for it in n.items if hasattr(it, "criteria")]
        (t,) = cond.criteria
        self.assertIsInstance(t, Custom)
        self.assertEqual([x[0] for x in t.inputs], ["value", "const"])
        self.assertEqual(t.inputs[1][1], 2.5)

    def test_algorithm(self):
        alg = ('<CustomAlgorithm name="Two"><InputSet>'
               '<InputParameterInstanceRef parameterRef="A"/></InputSet>'
               '<OutputSet><OutputParameterRef parameterRef="O"/>'
               '<OutputParameterRef parameterRef="P"/></OutputSet><TriggerSet>'
               '<OnContainerUpdateTrigger containerRef="C"/></TriggerSet>'
               '</CustomAlgorithm>')
        m = compile_xml(hook_mission(A, algorithms=alg))
        (h,) = m.hooks
        self.assertEqual((h.kind, h.outputs), ("algorithm", 2))
        self.assertEqual(
            [(a.name, a.hook, a.index) for a in m.nodes[0].algorithms],
            [("o", h, 0), ("p", h, 1)])
        # Algorithms only run when asked: decoding doesn't need the hook.
        self.assertEqual(m.nodes[0].hooks, [])

    def test_alarm(self):
        alarm = ('<DefaultAlarm><CustomAlarm><InputAlgorithm name="Level">'
                 '<InputSet><InputParameterInstanceRef parameterRef="A"/>'
                 '</InputSet></InputAlgorithm></CustomAlarm></DefaultAlarm>')
        m = compile_xml(hook_mission(A, alarm=alarm))
        ((_, _, a),) = m.nodes[0].alarms
        h, inputs = a.default.custom
        self.assertEqual((h.kind, h.ident), ("alarm", "level"))
        self.assertEqual(inputs[0][1].name, "A")

    def test_checksum(self):
        enc = ('<BinaryEncoding><ErrorDetectCorrect><Checksum name="custom" '
               'parameterRef="B"><InputAlgorithm name="Sum"/></Checksum>'
               '</ErrorDetectCorrect></BinaryEncoding>')
        m = compile_xml(hook_mission(A
                                     + '<ParameterRefEntry parameterRef="B"/>',
                                     encoding=enc))
        (c,) = m.nodes[0].checks
        self.assertEqual(c.spec[0], "custom")
        self.assertEqual(m.nodes[0].hooks, [c.spec[1]])

    def test_rejected(self):
        optional = (
            A
            + '<ParameterRefEntry parameterRef="B"><IncludeCondition>'
              '<Comparison parameterRef="A" value="1"/></IncludeCondition>'
              '</ParameterRefEntry><ParameterRefEntry parameterRef="O">'
              '<IncludeCondition><CustomAlgorithm name="X"><InputSet>'
              '<InputParameterInstanceRef parameterRef="B"/></InputSet>'
              '</CustomAlgorithm></IncludeCondition></ParameterRefEntry>')
        with self.assertRaises(Unsupported) as cm:
            compile_xml(hook_mission(optional))
        self.assertIn("custom algorithm input /Sat/B has an IncludeCondition",
                      str(cm.exception))


if __name__ == "__main__":
    unittest.main()
