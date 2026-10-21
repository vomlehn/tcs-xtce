"""Tests of the packet codecs generated from tests/mission/demo.xml."""

import dataclasses
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "mission"))

import demo  # noqa: E402
from xtce import ManagedBuffer  # noqa: E402
from xtce.packet import Codec, PacketError, enum_value, float_to_raw  # noqa: E402


def with_length(data, length: int) -> bytes:
    """``data`` with the CCSDS packet length set to ``length``."""
    return bytes(data[:4]) + length.to_bytes(2, "big") + bytes(data[6:])


def housekeeping(**kw) -> demo.Housekeeping:
    args = dict(
        sec_hdr_flag=True,
        seq_flags=demo.SeqFlagsType.UNSEGMENTED,
        seq_count=42,
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
    args.update(kw)
    return demo.Housekeeping(**args)


class PacketTests(unittest.TestCase):
    def kind(self, packet) -> str:
        with self.assertRaises(PacketError) as cm:
            packet.to_bytes()
        return cm.exception.kind

    def test_binary32_rounding(self):
        # The Rust and C runtimes must give the same bits.
        for x, raw in (
            (3.4028235e38, 0x7F7FFFFF),
            (-3.4028235e38, 0xFF7FFFFF),
            (3.4028235677973362e38, 0x7F7FFFFF),
            (3.4028235677973366e38, 0x7F800000),
            (1e39, 0x7F800000),
            (-1e39, 0xFF800000),
        ):
            self.assertEqual(float_to_raw(x, 32), raw, x)

    def test_inherited_constants(self):
        data = housekeeping().to_bytes()
        self.assertEqual(data[:2], b"\x08\x64")
        self.assertEqual(len(data), demo.Housekeeping.MAX_BYTES)
        hk = demo.CCSDSPacketKind.decode(data)
        self.assertIsInstance(hk, demo.Housekeeping)
        self.assertAlmostEqual(hk.temperature, 25.0, delta=0.5)
        self.assertEqual(hk.trim, -3)

    def test_dispatch(self):
        data = demo.ScienceHousekeeping(temperature=20.0, science_count=2**64 - 1).to_bytes()
        p = demo.CCSDSPacketKind.decode(data)
        self.assertIsInstance(p, demo.ScienceHousekeeping)
        self.assertEqual(p.science_count, 2**64 - 1)
        # Its base container's entries end before the packet length does.
        with self.assertRaises(PacketError) as cm:
            demo.Housekeeping.decode(data)
        self.assertEqual((cm.exception.kind, cm.exception.what), ("BadLength", ("PacketLength",)))

    def test_encode_errors(self):
        self.assertEqual(self.kind(demo.ScienceHousekeeping(temperature=45.0)), "Mismatch")
        self.assertEqual(self.kind(housekeeping(seq_count=1 << 14)), "OutOfRange")
        self.assertEqual(self.kind(housekeeping(temperature=150.0)), "Calibration")
        self.assertEqual(self.kind(housekeeping(offset=-2048)), "OutOfRange")
        self.assertEqual(self.kind(demo.Thermal()), "NotInvertible")

    def test_strings_and_binary(self):
        ev = demo.Event(origin="ORBIT", message="x" * 32, source="abc", data_=b"\x01\x02\x03\x04")
        self.assertEqual(demo.Event.decode(ev.to_bytes()), ev)
        for change, kind in [
            (dict(message="x" * 33), "TooLong"),
            (dict(origin="NINE BYTE"), "TooLong"),
            (dict(origin="a\0b"), "InvalidString"),
            (dict(data_=b"\x01\x02\x03"), "OutOfRange"),
        ]:
            self.assertEqual(self.kind(dataclasses.replace(ev, **change)), kind, change)

    def test_commands(self):
        cmd = demo.Reboot(seq_count=6, delay=30.0, force=True, reason="maint")
        data = cmd.to_bytes()
        self.assertEqual(data.hex(), "112cc006000a02012c0100056d61696e74")
        self.assertEqual(demo.CCSDSCommandKind.decode(data), cmd)
        back = demo.SetMode.decode(demo.SetMode(mode=enum_value(demo.ModeArgType, 9)).to_bytes())
        self.assertEqual(int(back.mode), 9)
        self.assertIsNone(back.mode.label)
        self.assertEqual(demo.ModeType(16).label, "Error")
        self.assertEqual(enum_value(demo.ModeType, 20).label, "Error")

    def test_packet_length_field(self):
        # The CCSDS length is the number of bytes after the 6-byte primary
        # header, minus 1, so it follows the length of the reason string.
        for reason, length in [("", 5), ("maint", 10)]:
            cmd = demo.Reboot(reason=reason)
            data = cmd.to_bytes()
            self.assertEqual(len(data), 7 + length)
            self.assertEqual(int.from_bytes(data[4:6], "big"), length)
            # Bytes after the packet are ignored.
            self.assertEqual(demo.Reboot.decode(data + b"\xee\xee"), cmd)
        data = demo.Reboot(reason="maint").to_bytes()
        for bad, kind in [
            (with_length(data, 11), "Truncated"),  # beyond the data received
            (with_length(data, 9), "Truncated"),  # ends inside the reason string
            (with_length(data, 11) + b"\0", "BadLength"),  # bytes the entries don't use
        ]:
            with self.assertRaises(PacketError) as cm:
                demo.Reboot.decode(bad)
            self.assertEqual(cm.exception.kind, kind)
        with self.assertRaises(PacketError) as cm:
            demo.Reboot.decode(with_length(data, 11))
        self.assertEqual(cm.exception.what, ("PacketLength",))

    def test_codec_reuses_buffers(self):
        codec = Codec.for_packet(demo.CCSDSPacketKind)
        caps = (codec.input_capacity, codec.output_capacity)
        self.assertGreaterEqual(caps[1], demo.CCSDSPacketKind.MAX_BYTES)
        packets = [housekeeping(), demo.Event(data_=bytes(4)), demo.ScienceHousekeeping()]
        for _ in range(100):
            for p in packets:
                with codec.encode(p) as v:
                    data = bytes(v)
                codec.load(data)
                back = codec.decode(demo.CCSDSPacketKind)
                with codec.encode(back) as v:
                    self.assertEqual(bytes(v), data)
        self.assertEqual((codec.input_capacity, codec.output_capacity), caps)

    def test_include_condition(self):
        ev = demo.Event(sec_hdr_flag=True, data_=bytes(4), coarse_time=1.6e9, fine_time=0.5)
        full = ev.to_bytes()
        self.assertEqual(demo.Event.decode(full).fine_time, 0.5)
        short = dataclasses.replace(ev, sec_hdr_flag=False).to_bytes()
        self.assertEqual(len(short) + 6, len(full))
        self.assertIsNone(demo.Event.decode(short).coarse_time)
        self.assertEqual(self.kind(dataclasses.replace(ev, fine_time=None)), "Missing")

    def test_arrays_and_sizes_from_other_fields(self):
        s = demo.Samples(
            sec_hdr_flag=True,
            sample_count=2,
            readings=[-1, 7],
            grid=[1, 2, 3, 4, 5, 6],
            valid=[True, False, False, True],
            label_size=4,
            label_="abc",
            payload_size=1,
            payload=b"\x5a",
        )
        self.assertEqual(demo.Samples.decode(s.to_bytes()), s)
        # The data must have the count or size that its field gives.
        self.assertEqual(self.kind(dataclasses.replace(s, readings=[-1, 7, 3])), "BadSize")
        self.assertEqual(self.kind(dataclasses.replace(s, payload=b"")), "BadSize")
        self.assertEqual(self.kind(dataclasses.replace(s, valid=[True])), "BadSize")
        self.assertEqual(self.kind(dataclasses.replace(s, label_size=11)), "BadSize")
        self.assertEqual(self.kind(dataclasses.replace(s, label_="abcde")), "TooLong")
        full = dataclasses.replace(s, label_="abcd")
        self.assertEqual(demo.Samples.decode(full.to_bytes()), full)
        self.assertEqual(self.kind(dataclasses.replace(s, grid=None)), "Missing")

        up = demo.Upload(word_count=3, words=[1, 2, 3], gains=[1.0, 2.5], tag_size=4, tag="ab")
        self.assertEqual(demo.Upload.decode(up.to_bytes()), up)
        self.assertEqual(self.kind(dataclasses.replace(up, gains=[1.0])), "BadSize")
        # Lists are not shared between instances.
        self.assertIsNot(demo.Upload().words, demo.Upload().words)

    def test_aggregates(self):
        target = demo.TargetType(id_=3, mode=demo.ModeType.SAFE, name_="a", where_=demo.PositionType(2, 3), weights=[1, 2])
        t = demo.Targets(
            sec_hdr_flag=True,
            start=demo.PositionType(1, -1),
            target_count=1,
            targets=[target],
            home=demo.PositionType(4, 5),
        )
        self.assertEqual(demo.Targets.decode(t.to_bytes()), t)
        # Members are checked like fields.
        bad = dataclasses.replace(target, id_=16)
        self.assertEqual(self.kind(dataclasses.replace(t, targets=[bad])), "OutOfRange")
        bad = dataclasses.replace(target, weights=[1])
        self.assertEqual(self.kind(dataclasses.replace(t, targets=[bad])), "BadSize")
        self.assertEqual(self.kind(dataclasses.replace(t, home=None)), "Missing")
        # Aggregates are not shared between instances.
        self.assertIsNot(demo.Targets().start, demo.Targets().start)

        g = demo.Goto(waypoint=demo.WaypointType(north=1.5, east=2.0, hold=True))
        self.assertEqual(demo.Goto.decode(g.to_bytes()), g)

    def test_calibrated_integers_and_the_rest_of_the_packet(self):
        d = demo.Dump(sec_hdr_flag=True, board_temp=25, record_count=2, records=[7, 8], rest=b"\xde\xad")
        b = d.to_bytes()
        # Raw values: 25 = -100 + 125 and 2 = -4 + 12 / 2.
        self.assertEqual(bytes(b[6:]), bytes([0x7D, 0x0C, 7, 8, 0xDE, 0xAD]))
        self.assertEqual(demo.Dump.decode(b), d)
        # The rest of the packet is whatever follows the records, up to the
        # end that the packet length gives.
        self.assertEqual(demo.Dump.decode(with_length(b[:11], 4)).rest, b"\xde")
        self.assertEqual(self.kind(dataclasses.replace(d, record_count=200)), "OutOfRange")
        self.assertEqual(self.kind(dataclasses.replace(d, record_count=3)), "BadSize")
        self.assertEqual(self.kind(dataclasses.replace(d, rest=bytes(65537))), "TooLong")

        # 21 needs raw 5.5, which rounds to 6 and decodes as 22.
        q = demo.Poke.decode(demo.Poke(level=21, data_=b"\xbe\xef").to_bytes())
        self.assertEqual((q.level, q.data_), (22, b"\xbe\xef"))

    def test_repeated_containers_and_container_sizes(self):
        log = demo.Log(
            sec_hdr_flag=True,
            log_count=2,
            log_record=[demo.LogRecord(channel=3, reading=0x1234), demo.LogRecord(channel=15, reading=-1)],
            closed=True,
        )
        b = log.to_bytes()
        # Each record is 4 + 2 + 16 bits padded to 32; the trailer 1 padded to 16.
        self.assertEqual(bytes(b[6:]).hex(), "023048d000f3fffc008000")
        self.assertEqual(demo.Log.decode(b), log)
        # Padding must be there, though its value is ignored.
        with self.assertRaises(PacketError) as cm:
            demo.Log.decode(with_length(b[:-1], len(b) - 1 - 7))
        self.assertEqual((cm.exception.kind, cm.exception.what), ("Truncated", ("/Demo/Log",)))
        self.assertEqual(self.kind(dataclasses.replace(log, log_count=1)), "BadSize")

    def test_sizes_from_aggregate_members(self):
        b = demo.Batches(
            sec_hdr_flag=True,
            batch_count=2,
            batches=[demo.BatchType(size=2, values=[0xAA, 0xBB]), demo.BatchType(size=0, values=[])],
        )
        data = b.to_bytes()
        self.assertEqual(bytes(data[6:]).hex(), "022aabb0")
        self.assertEqual(demo.Batches.decode(data), b)
        # Each copy's size must match its own values.
        bad = dataclasses.replace(b, batches=[b.batches[0], demo.BatchType(size=1, values=[])])
        self.assertEqual(self.kind(bad), "BadSize")

    def test_booleans_and_strings_with_other_encodings(self):
        r = demo.Reply(sec_hdr_flag=True, answer=True, code="ABC", note_size=2, note="\u00e9")
        b = r.to_bytes()
        self.assertEqual(bytes(b[6:]), b"yes\0ABC\x02\xc3\xa9")
        self.assertEqual(demo.Reply.decode(b), r)
        self.assertEqual(bytes(dataclasses.replace(r, answer=False).to_bytes()[6:10]), b"no\0\0")
        # Strings encoded as binary fill their field exactly.
        self.assertEqual(self.kind(dataclasses.replace(r, code="AB")), "OutOfRange")
        self.assertEqual(self.kind(dataclasses.replace(r, code="ABCD")), "TooLong")
        self.assertEqual(self.kind(dataclasses.replace(r, note="e")), "BadSize")

    def test_lookups_and_locations_from_other_values(self):
        f = demo.Frame(sec_hdr_flag=True, frame_kind=1, frame_gap=3, frame_body=b"\xab\xcd", frame_end=True)
        b = f.to_bytes()
        # Kind 1 means a 2-byte body; FrameEnd comes 3 bits after it.
        self.assertEqual(bytes(b[6:]).hex(), "013abcd1")
        self.assertEqual(demo.Frame.decode(b), f)
        self.assertEqual(self.kind(dataclasses.replace(f, frame_kind=2)), "BadSize")

        t = demo.Tune(mode=demo.ModeArgType.SCIENCE, setting=b"\x01\x02\x03")
        self.assertEqual(demo.Tune.decode(t.to_bytes()), t)
        # Nominal takes the default of 1 byte.
        self.assertEqual(self.kind(dataclasses.replace(t, mode=demo.ModeArgType.NOMINAL)), "BadSize")

    def test_references_to_aggregate_members(self):
        r = demo.Route(sec_hdr_flag=True, route_hdr=demo.RouteHeaderType(kind=2, count=3), stops=[1, 2, 3], detour=9)
        b = r.to_bytes()
        self.assertEqual(bytes(b[6:]).hex(), "2301020309")
        self.assertEqual(demo.Route.decode(b), r)
        # RouteHdr.Kind 2 includes the detour, and RouteHdr.Count counts the stops.
        self.assertEqual(self.kind(dataclasses.replace(r, detour=None)), "Missing")
        short = dataclasses.replace(r, route_hdr=demo.RouteHeaderType(kind=1, count=3), detour=None)
        self.assertEqual(demo.Route.decode(short.to_bytes()), short)
        self.assertEqual(self.kind(dataclasses.replace(short, stops=[1])), "BadSize")

        p = demo.Plan(leg=demo.LegType(mode=demo.ModeArgType.NOMINAL, count=2), points=[10, 20])
        self.assertEqual(demo.Plan.decode(p.to_bytes()), p)

    def test_boolean_expressions(self):
        # GateSpan is there when GateLo < GateHi, or both are zero.
        g = demo.Gate(sec_hdr_flag=True, gate_lo=1, gate_hi=2, gate_span=0xABCD)
        self.assertEqual(bytes(g.to_bytes()[6:]).hex(), "0102abcd")
        for lo, hi in ((1, 2), (0, 0)):
            x = dataclasses.replace(g, gate_lo=lo, gate_hi=hi)
            self.assertEqual(demo.Gate.decode(x.to_bytes()), x)
        x = dataclasses.replace(g, gate_lo=3, gate_span=None)
        self.assertEqual(demo.Gate.decode(x.to_bytes()), x)
        self.assertEqual(self.kind(dataclasses.replace(g, gate_span=None)), "Missing")

        # Data is 2 bytes when Lo < Hi.
        o = demo.Open(lo=1, hi=2, data_=b"\x12\x34")
        self.assertEqual(demo.Open.decode(o.to_bytes()), o)
        self.assertEqual(self.kind(dataclasses.replace(o, lo=2)), "BadSize")

    def test_references_to_array_elements(self):
        probes = [demo.ProbeType(id_=1, gains=[2, 3]), demo.ProbeType(id_=5, gains=[2, 0])]
        p = demo.ProbeOne(sec_hdr_flag=True, probes=probes, probe_data=b"\xab\xcd", probe_note=0x77)
        b = p.to_bytes()
        self.assertEqual(bytes(b[6:]).hex(), "123520abcd77")
        # Probes[0].Id == 1 picks ProbeOne; Probes[1].Gains[0] sizes the data.
        self.assertEqual(demo.CCSDSPacketKind.decode(b), p)
        other = [demo.ProbeType(id_=4, gains=[2, 3]), probes[1]]
        self.assertEqual(self.kind(dataclasses.replace(p, probes=other)), "Mismatch")
        # The criterion indexes Probes, so its length is checked first.
        self.assertEqual(self.kind(dataclasses.replace(p, probes=[])), "BadSize")
        self.assertEqual(self.kind(dataclasses.replace(p, probe_data=b"\xab")), "BadSize")

    def test_array_subsets(self):
        # Rows 1..2 and columns 1..2 of the 3x4 Matrix; Matrix[1][2] is the
        # second of them.
        m = demo.MatrixMiddle(sec_hdr_flag=True, matrix=[1, 7, 2, 6], matrix_note=0xAB)
        b = m.to_bytes()
        self.assertEqual(bytes(b[6:]).hex(), "1726ab")
        self.assertEqual(demo.MatrixMiddle.decode(b), m)
        self.assertEqual(self.kind(dataclasses.replace(m, matrix=[1, 7, 2, 6, 0])), "BadSize")

        # Slots 2..Last.
        f = demo.Fill(last=3, slots=[0xAA, 0xBB])
        self.assertEqual(demo.Fill.decode(f.to_bytes()), f)
        self.assertEqual(self.kind(dataclasses.replace(f, last=4)), "BadSize")

    def test_encode_into_existing_buffer(self):
        buf = ManagedBuffer(64)
        housekeeping().encode(buf)
        self.assertEqual(len(buf), demo.Housekeeping.MAX_BYTES)
        self.assertEqual(buf.capacity, 64)


if __name__ == "__main__":
    unittest.main()
