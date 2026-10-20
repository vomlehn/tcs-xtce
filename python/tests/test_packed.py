"""Tests of the packet codecs generated from tests/mission/demo.xml with
--bit-packing.

tests/packet_check.sh checks that they decode and encode exactly like the
unpacked codecs; these tests cover the accessors and the layout.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "mission"))

import demo  # noqa: E402
import demo_packed  # noqa: E402
from xtce.packet import PacketError, packed_get  # noqa: E402

VALUES = dict(
    version=5,
    sec_hdr_flag=True,
    seq_flags=demo.SeqFlagsType.UNSEGMENTED,
    seq_count=42,
    packet_length=18,
    mode=demo.ModeType.NOMINAL,
    temperature=25.0,
    battery_voltage=12.0,
    charging=True,
    bus_current=1.5,
    uptime=0x01020304,
    attitude=1.5,
    delta=-2,
    offset=-5,
    trim=-3,
)


def housekeeping() -> demo_packed.Housekeeping:
    return demo_packed.Housekeeping(**VALUES)


class PackedTests(unittest.TestCase):
    def test_packs_without_padding(self):
        hk = housekeeping()
        # 113 bits of integer, enumerated and boolean fields in 15 bytes.
        self.assertEqual(len(hk.packed), 15)
        # Version (3 bits), SecHdrFlag (1), SeqFlags (2) and SeqCount (14)
        # are stored as XTCE packs encoded data, most significant bit first
        # from the top of byte 0, SeqCount crossing into the second byte.
        self.assertEqual(hk.packed[:2], bytes([0xBC, 0x02]))
        # Uptime is little-endian, so it is stored with its bytes swapped, as
        # it is encoded.
        self.assertEqual(packed_get(bytearray(hk.packed), 45, 32), 0x04030201)

    def test_accessors_round_trip(self):
        hk = housekeeping()
        for name, v in VALUES.items():
            self.assertEqual(getattr(hk, name), v, name)
        self.assertIs(type(hk.mode), demo_packed.ModeType)
        hk.sec_hdr_flag = False
        self.assertFalse(hk.sec_hdr_flag)
        self.assertEqual((hk.version, hk.seq_flags), (5, 3))

    def test_setters_check_the_encoding(self):
        hk = housekeeping()
        for name, v in [("version", 8), ("seq_count", 1 << 14), ("offset", 2048), ("trim", -128), ("seq_flags", 4)]:
            with self.assertRaises(PacketError) as cm:
                setattr(hk, name, v)
            self.assertEqual(cm.exception.kind, "OutOfRange", name)
        # A failed set leaves the field as it was.
        self.assertEqual(hk, housekeeping())

    def test_encodes_like_the_unpacked_codec(self):
        hk = housekeeping()
        plain = demo.Housekeeping(**VALUES)
        data = hk.to_bytes()
        self.assertEqual(data, plain.to_bytes())
        self.assertEqual(hk.describe(), plain.describe())
        back = demo_packed.CCSDSPacketKind.decode(data)
        self.assertIsInstance(back, demo_packed.Housekeeping)
        self.assertEqual(back.to_bytes(), data)
        self.assertEqual((back.seq_count, back.offset), (42, -5))
        self.assertIn("seq_count=42", repr(back))


if __name__ == "__main__":
    unittest.main()
