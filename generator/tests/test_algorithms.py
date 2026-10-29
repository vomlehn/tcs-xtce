"""Tests of the MathAlgorithms that packets evaluate."""

import unittest

from tests.test_compile import compile_xml, mission


def algorithm_mission(operands: str, triggers: str,
                      entries: str = "A B") -> str:
    """Builds telemetry for MathAlgorithm tests.

    Telemetry with U8 A and B, float output O, container P holding
    ``entries``, and MathAlgorithm M computing O from ``operands``.

    Args:
        operands: The XML of M's MathOperation operands and operators.
        triggers: The XML inside M's TriggerSet.
        entries:  The names of the parameters in P, separated by spaces.

    Returns:
        The XTCE document.
    """
    refs = "".join(f'<ParameterRefEntry parameterRef="{e}"/>'
                   for e in entries.split())
    return mission(tm=f"""
        <ParameterTypeSet>
          <IntegerParameterType name="U8" signed="false">
          <IntegerDataEncoding sizeInBits="8"/></IntegerParameterType>
          <FloatParameterType name="F"><FloatDataEncoding sizeInBits="32"/>
          </FloatParameterType>
        </ParameterTypeSet>
        <ParameterSet>
          <Parameter name="A" parameterTypeRef="U8"/>
          <Parameter name="B" parameterTypeRef="U8"/>
          <Parameter name="O" parameterTypeRef="F"/>
        </ParameterSet>
        <ContainerSet>
          <SequenceContainer name="P"><EntryList>{refs}</EntryList>
          </SequenceContainer>
        </ContainerSet>
        <AlgorithmSet><MathAlgorithm name="M">
          <MathOperation outputParameterRef="O">{operands}
          <TriggerSet>{triggers}</TriggerSet></MathOperation>
        </MathAlgorithm></AlgorithmSet>""")


SUM = ('<ParameterInstanceRefOperand parameterRef="A"/>'
       '<ParameterInstanceRefOperand parameterRef="B"/><Operator>+</Operator>')
ON_A = '<OnParameterUpdateTrigger parameterRef="A"/>'
ON_P = '<OnContainerUpdateTrigger containerRef="P"/>'


class AlgorithmTests(unittest.TestCase):
    def node(self, *args):
        return compile_xml(algorithm_mission(*args)).nodes[0]

    def test_triggered(self):
        for trigger in (ON_A, ON_P):
            with self.subTest(trigger=trigger):
                (alg,) = self.node(SUM, trigger).algorithms
                self.assertEqual((alg.key, alg.name), ("/Sat/O", "o"))
                self.assertEqual(alg.expr[0], "+")
                self.assertEqual(alg.expr[1][1].local.name, "A")

    def test_not_triggered(self):
        n = self.node(SUM,
                      '<OnParameterUpdateTrigger parameterRef="O"/>'
                      '<OnPeriodicRateTrigger fireRateInSeconds="1"/>')
        self.assertEqual((n.algorithms, n.algorithm_notes), ([], []))

    def test_notes(self):
        for operands, entries, why in (
                (SUM, "A", "/Sat/B, which is not decoded before it"),
                ("<ThisParameterOperand/><ValueOperand>1</ValueOperand>"
                 "<Operator>+</Operator>", "A B",
                 "ThisParameterOperand, the output's own earlier value")):
            with self.subTest(why=why):
                n = self.node(operands, ON_A, entries)
                self.assertEqual(n.algorithms, [])
                (note,) = n.algorithm_notes
                self.assertIn(why, note)


if __name__ == "__main__":
    unittest.main()
