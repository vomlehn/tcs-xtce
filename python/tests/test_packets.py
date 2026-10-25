"""Tests of the packet codecs generated from tests/mission/demo.xml."""

import dataclasses
import math
import random
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "mission"))

import demo  # noqa: E402
from xtce import ManagedBuffer  # noqa: E402
from xtce.packet import (  # noqa: E402
    BitReader, BitWriter, Codec, PacketError, bcd_decode, bcd_encode,
    decode_text, encode_text, enum_value, float_decode, float_encode,
    float_to_raw)


def with_length(data, length: int) -> bytes:
    """``data`` with the CCSDS packet length set to ``length``."""
    return bytes(data[:4]) + length.to_bytes(2, "big") + bytes(data[6:])


def housekeeping(**kw) -> demo.Housekeeping:
    args = dict(sec_hdr_flag=True, seq_flags=demo.SeqFlagsType.UNSEGMENTED,
                seq_count=42, mode=demo.ModeType.NOMINAL, temperature=25.0,
                battery_voltage=12.0, charging=True, bus_current=1.5,
                uptime=0x01020304, attitude=1.5, delta=-2, offset=-5, trim=-3)
    args.update(kw)
    return demo.Housekeeping(**args)


class PacketTests(unittest.TestCase):
    def kind(self, packet) -> str:
        with self.assertRaises(PacketError) as cm:
            packet.to_bytes()
        return cm.exception.kind

    def test_binary32_rounding(self):
        # The Rust and C runtimes must give the same bits.
        for x, raw in ((3.4028235e38, 0x7F7FFFFF), (-3.4028235e38, 0xFF7FFFFF),
                       (3.4028235677973362e38, 0x7F7FFFFF),
                       (3.4028235677973366e38, 0x7F800000), (1e39, 0x7F800000),
                       (-1e39, 0xFF800000)):
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
        data = demo.ScienceHousekeeping(temperature=20.0,
                                        science_count=2**64 - 1).to_bytes()
        p = demo.CCSDSPacketKind.decode(data)
        self.assertIsInstance(p, demo.ScienceHousekeeping)
        self.assertEqual(p.science_count, 2**64 - 1)
        # Its base container's entries end before the packet length does.
        with self.assertRaises(PacketError) as cm:
            demo.Housekeeping.decode(data)
        self.assertEqual((cm.exception.kind, cm.exception.what),
                         ("BadLength", ("PacketLength",)))

    def test_encode_errors(self):
        self.assertEqual(self.kind(demo.ScienceHousekeeping(temperature=45.0)),
                         "Mismatch")
        self.assertEqual(self.kind(housekeeping(seq_count=1 << 14)),
                         "OutOfRange")
        self.assertEqual(self.kind(housekeeping(temperature=150.0)),
                         "Calibration")
        self.assertEqual(self.kind(housekeeping(offset=-2048)), "OutOfRange")
        self.assertEqual(self.kind(demo.Thermal()), "NotInvertible")

    def test_strings_and_binary(self):
        ev = demo.Event(origin="ORBIT", message="x" * 32, source="abc",
                        data_=b"\x01\x02\x03\x04")
        self.assertEqual(demo.Event.decode(ev.to_bytes()), ev)
        for change, kind in [(dict(message="x" * 33), "TooLong"),
                             (dict(origin="NINE BYTE"), "TooLong"),
                             (dict(origin="a\0b"), "InvalidString"),
                             (dict(data_=b"\x01\x02\x03"), "OutOfRange")]:
            self.assertEqual(self.kind(dataclasses.replace(ev, **change)), kind,
                             change)

    def test_commands(self):
        cmd = demo.Reboot(seq_count=6, delay=30.0, force=True, reason="maint")
        data = cmd.to_bytes()
        self.assertEqual(data.hex(), "112cc006000a02012c0100056d61696e74")
        self.assertEqual(demo.CCSDSCommandKind.decode(data), cmd)
        back = demo.SetMode.decode(
            demo.SetMode(mode=enum_value(demo.ModeArgType, 9)).to_bytes())
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
                (with_length(data, 11),
                 "Truncated"),  # beyond the data received
                # ends inside the reason string
                (with_length(data, 9), "Truncated"),
                # bytes the entries don't use
                (with_length(data, 11) + b"\0", "BadLength")]:
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
        packets = [housekeeping(), demo.Event(data_=bytes(4)),
                   demo.ScienceHousekeeping()]
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
        ev = demo.Event(sec_hdr_flag=True, data_=bytes(4), coarse_time=1.6e9,
                        fine_time=0.5)
        full = ev.to_bytes()
        self.assertEqual(demo.Event.decode(full).fine_time, 0.5)
        short = dataclasses.replace(ev, sec_hdr_flag=False).to_bytes()
        self.assertEqual(len(short) + 6, len(full))
        self.assertIsNone(demo.Event.decode(short).coarse_time)
        self.assertEqual(self.kind(dataclasses.replace(ev, fine_time=None)),
                         "Missing")

    def test_arrays_and_sizes_from_other_fields(self):
        s = demo.Samples(sec_hdr_flag=True, readings=[-1, 7],
                         grid=[1, 2, 3, 4, 5, 6],
                         valid=[True, False, False, True], label_size=4,
                         label_="abc", payload=b"\x5a")
        self.assertEqual(demo.Samples.decode(s.to_bytes()), s)
        # SampleCount and PayloadSize come from the data, which must fit them.
        self.assertEqual(s.to_bytes()[6], 2)
        for more in (dataclasses.replace(s, readings=[-1, 7, 3]),
                     dataclasses.replace(s, payload=b"")):
            self.assertEqual(demo.Samples.decode(more.to_bytes()), more)
        # Their ValidRanges allow 6 readings and 8 bytes.
        self.assertEqual(self.kind(dataclasses.replace(s, readings=[0] * 7)),
                         "BadSize")
        self.assertEqual(self.kind(dataclasses.replace(s, payload=bytes(9))),
                         "BadSize")
        # A count that the application sets must still match its data.
        self.assertEqual(self.kind(dataclasses.replace(s, valid=[True])),
                         "BadSize")
        self.assertEqual(self.kind(dataclasses.replace(s, label_size=11)),
                         "BadSize")
        self.assertEqual(self.kind(dataclasses.replace(s, label_="abcde")),
                         "TooLong")
        full = dataclasses.replace(s, label_="abcd")
        self.assertEqual(demo.Samples.decode(full.to_bytes()), full)
        self.assertEqual(self.kind(dataclasses.replace(s, grid=None)),
                         "Missing")

        up = demo.Upload(words=[1, 2, 3], gains=[1.0, 2.5], tag_size=4,
                         tag="ab")
        self.assertEqual(demo.Upload.decode(up.to_bytes()), up)
        self.assertEqual(self.kind(dataclasses.replace(up, gains=[1.0])),
                         "BadSize")
        # Lists are not shared between instances.
        self.assertIsNot(demo.Upload().words, demo.Upload().words)

    def test_aggregates(self):
        target = demo.TargetType(id_=3, mode=demo.ModeType.SAFE, name_="a",
                                 where_=demo.PositionType(2, 3), weights=[1, 2])
        t = demo.Targets(sec_hdr_flag=True, start=demo.PositionType(1, -1),
                         targets=[target], home=demo.PositionType(4, 5))
        self.assertEqual(demo.Targets.decode(t.to_bytes()), t)
        # Members are checked like fields.
        bad = dataclasses.replace(target, id_=16)
        self.assertEqual(self.kind(dataclasses.replace(t, targets=[bad])),
                         "OutOfRange")
        bad = dataclasses.replace(target, weights=[1])
        self.assertEqual(self.kind(dataclasses.replace(t, targets=[bad])),
                         "BadSize")
        self.assertEqual(self.kind(dataclasses.replace(t, home=None)),
                         "Missing")
        # Aggregates are not shared between instances.
        self.assertIsNot(demo.Targets().start, demo.Targets().start)

        g = demo.Goto(waypoint=demo.WaypointType(north=1.5, east=2.0,
                                                 hold=True))
        self.assertEqual(demo.Goto.decode(g.to_bytes()), g)

    def test_calibrated_integers_and_the_rest_of_the_packet(self):
        d = demo.Dump(sec_hdr_flag=True, board_temp=25, record_count=2,
                      records=[7, 8], rest=b"\xde\xad")
        b = d.to_bytes()
        # Raw values: 25 = -100 + 125 and 2 = -4 + 12 / 2.
        self.assertEqual(bytes(b[6:]), bytes([0x7D, 0x0C, 7, 8, 0xDE, 0xAD]))
        self.assertEqual(demo.Dump.decode(b), d)
        # The rest of the packet is whatever follows the records, up to the
        # end that the packet length gives.
        self.assertEqual(demo.Dump.decode(with_length(b[:11], 4)).rest, b"\xde")
        self.assertEqual(self.kind(dataclasses.replace(d, record_count=200)),
                         "OutOfRange")
        self.assertEqual(self.kind(dataclasses.replace(d, record_count=3)),
                         "BadSize")
        self.assertEqual(self.kind(dataclasses.replace(d, rest=bytes(65537))),
                         "TooLong")

        # 21 needs raw 5.5, which rounds to 6 and decodes as 22.
        q = demo.Poke.decode(demo.Poke(level=21, data_=b"\xbe\xef").to_bytes())
        self.assertEqual((q.level, q.data_), (22, b"\xbe\xef"))

    def test_repeated_containers_and_container_sizes(self):
        log = demo.Log(sec_hdr_flag=True,
                       log_record=[demo.LogRecord(channel=3, reading=0x1234),
                                   demo.LogRecord(channel=15, reading=-1)],
                       closed=True)
        b = log.to_bytes()
        # Each record is 4 + 2 + 16 bits padded to 32; the trailer 1 padded to
        # 16.
        self.assertEqual(bytes(b[6:]).hex(), "023048d000f3fffc008000")
        self.assertEqual(demo.Log.decode(b), log)
        # Padding must be there, though its value is ignored.
        with self.assertRaises(PacketError) as cm:
            demo.Log.decode(with_length(b[:-1], len(b) - 1 - 7))
        self.assertEqual((cm.exception.kind, cm.exception.what),
                         ("Truncated", ("/Demo/Log",)))
        # LogCount's ValidRange allows 6 records.
        self.assertEqual(
            self.kind(dataclasses.replace(log,
                                          log_record=[demo.LogRecord()] * 7)),
            "BadSize")

    def test_sizes_from_aggregate_members(self):
        b = demo.Batches(sec_hdr_flag=True,
                         batches=[demo.BatchType(size=2, values=[0xAA, 0xBB]),
                                  demo.BatchType(size=0, values=[])])
        data = b.to_bytes()
        self.assertEqual(bytes(data[6:]).hex(), "022aabb0")
        self.assertEqual(demo.Batches.decode(data), b)
        # Each copy's size must match its own values.
        bad = dataclasses.replace(
            b, batches=[b.batches[0], demo.BatchType(size=1, values=[])])
        self.assertEqual(self.kind(bad), "BadSize")

    def test_booleans_and_strings_with_other_encodings(self):
        r = demo.Reply(sec_hdr_flag=True, answer=True, code="ABC", note_size=2,
                       note="\u00e9")
        b = r.to_bytes()
        self.assertEqual(bytes(b[6:]), b"yes\0ABC\x02\xc3\xa9")
        self.assertEqual(demo.Reply.decode(b), r)
        self.assertEqual(
            bytes(dataclasses.replace(r, answer=False).to_bytes()[6:10]),
            b"no\0\0")
        # Strings encoded as binary fill their field exactly.
        self.assertEqual(self.kind(dataclasses.replace(r, code="AB")),
                         "OutOfRange")
        self.assertEqual(self.kind(dataclasses.replace(r, code="ABCD")),
                         "TooLong")
        self.assertEqual(self.kind(dataclasses.replace(r, note="e")), "BadSize")

    def test_lookups_and_locations_from_other_values(self):
        f = demo.Frame(sec_hdr_flag=True, frame_kind=1, frame_gap=3,
                       frame_body=b"\xab\xcd", frame_end=True)
        b = f.to_bytes()
        # Kind 1 means a 2-byte body; FrameEnd comes 3 bits after it.
        self.assertEqual(bytes(b[6:]).hex(), "013abcd1")
        self.assertEqual(demo.Frame.decode(b), f)
        self.assertEqual(self.kind(dataclasses.replace(f, frame_kind=2)),
                         "BadSize")

        t = demo.Tune(mode=demo.ModeArgType.SCIENCE, setting=b"\x01\x02\x03")
        self.assertEqual(demo.Tune.decode(t.to_bytes()), t)
        # Nominal takes the default of 1 byte.
        self.assertEqual(
            self.kind(dataclasses.replace(t, mode=demo.ModeArgType.NOMINAL)),
            "BadSize")

    def test_references_to_aggregate_members(self):
        r = demo.Route(sec_hdr_flag=True,
                       route_hdr=demo.RouteHeaderType(kind=2, count=3),
                       stops=[1, 2, 3], detour=9)
        b = r.to_bytes()
        self.assertEqual(bytes(b[6:]).hex(), "2301020309")
        self.assertEqual(demo.Route.decode(b), r)
        # RouteHdr.Kind 2 includes the detour, and RouteHdr.Count counts the
        # stops.
        self.assertEqual(self.kind(dataclasses.replace(r, detour=None)),
                         "Missing")
        short = dataclasses.replace(
            r, route_hdr=demo.RouteHeaderType(kind=1, count=3), detour=None)
        self.assertEqual(demo.Route.decode(short.to_bytes()), short)
        self.assertEqual(self.kind(dataclasses.replace(short, stops=[1])),
                         "BadSize")

        p = demo.Plan(leg=demo.LegType(mode=demo.ModeArgType.NOMINAL, count=2),
                      points=[10, 20])
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
        self.assertEqual(self.kind(dataclasses.replace(g, gate_span=None)),
                         "Missing")

        # Data is 2 bytes when Lo < Hi.
        o = demo.Open(lo=1, hi=2, data_=b"\x12\x34")
        self.assertEqual(demo.Open.decode(o.to_bytes()), o)
        self.assertEqual(self.kind(dataclasses.replace(o, lo=2)), "BadSize")

    def test_references_to_array_elements(self):
        probes = [demo.ProbeType(id_=1, gains=[2, 3]),
                  demo.ProbeType(id_=5, gains=[2, 0])]
        p = demo.ProbeOne(sec_hdr_flag=True, probes=probes,
                          probe_data=b"\xab\xcd", probe_note=0x77)
        b = p.to_bytes()
        self.assertEqual(bytes(b[6:]).hex(), "123520abcd77")
        # Probes[0].Id == 1 picks ProbeOne; Probes[1].Gains[0] sizes the data.
        self.assertEqual(demo.CCSDSPacketKind.decode(b), p)
        other = [demo.ProbeType(id_=4, gains=[2, 3]), probes[1]]
        self.assertEqual(self.kind(dataclasses.replace(p, probes=other)),
                         "Mismatch")
        # The criterion indexes Probes, so its length is checked first.
        self.assertEqual(self.kind(dataclasses.replace(p, probes=[])),
                         "BadSize")
        self.assertEqual(self.kind(dataclasses.replace(p, probe_data=b"\xab")),
                         "BadSize")

    def test_elements_of_arrays_whose_count_varies(self):
        t = demo.Tally(sec_hdr_flag=True, tallies=[2, 7],
                       tally_data=b"\xab\xcd", tally_extra=0x55)
        b = t.to_bytes()
        self.assertEqual(bytes(b[6:]).hex(), "020207abcd55")
        self.assertEqual(demo.Tally.decode(b), t)
        # Tallies[1] == 7 includes the extra, so with one tally the element
        # it reads is missing.
        short = dataclasses.replace(t, tallies=[0], tally_data=b"")
        self.assertEqual(self.kind(short), "BadSize")
        # Tallies[0] sizes the data, so without tallies it is missing too.
        self.assertEqual(self.kind(dataclasses.replace(short, tallies=[])),
                         "BadSize")
        # TallyCount is computed from the length of Tallies, and TallyCount
        # >= 3 includes TallyMore.
        three = demo.Tally(sec_hdr_flag=True, tallies=[0, 7, 1], tally_data=b"",
                           tally_extra=0x55, tally_more=0x66)
        b = three.to_bytes()
        self.assertEqual(bytes(b[6:]).hex(), "030007015566")
        self.assertEqual(demo.Tally.decode(b), three)
        self.assertEqual(self.kind(dataclasses.replace(three, tally_more=None)),
                         "Missing")
        # Decoding fails the same way.
        for body in ("0100", "00"):
            data = bytes.fromhex(f"0972c02a{len(body) // 2 - 1:04x}{body}")
            with self.assertRaises(PacketError) as cm:
                demo.Tally.decode(data)
            self.assertEqual((cm.exception.kind, cm.exception.what),
                             ("BadSize", ("Tallies",)))

    def test_array_subsets(self):
        # Rows 1..2 and columns 1..2 of the 3x4 Matrix; Matrix[1][2] is the
        # second of them.
        m = demo.MatrixMiddle(sec_hdr_flag=True, matrix=[1, 7, 2, 6],
                              matrix_note=0xAB)
        b = m.to_bytes()
        self.assertEqual(bytes(b[6:]).hex(), "1726ab")
        self.assertEqual(demo.MatrixMiddle.decode(b), m)
        self.assertEqual(
            self.kind(dataclasses.replace(m, matrix=[1, 7, 2, 6, 0])),
            "BadSize")

        # Slots 2..Last: encoding sets Last to the last index, which its
        # ValidRange limits to 4, three slots.
        f = demo.Fill(slots=[0xAA, 0xBB])
        self.assertEqual(demo.Fill.decode(f.to_bytes()), f)
        self.assertEqual(self.kind(dataclasses.replace(f, slots=[1, 2, 3, 4])),
                         "BadSize")

    def test_encode_into_existing_buffer(self):
        buf = ManagedBuffer(64)
        housekeeping().encode(buf)
        self.assertEqual(len(buf), demo.Housekeeping.MAX_BYTES)
        self.assertEqual(buf.capacity, 64)


class EncodingTests(unittest.TestCase):
    """The runtime's bit and byte orders, BCD, float formats and character
    sets."""

    SIZES = {"IEEE754": (16, 32, 64, 80, 128), "MILSTD_1750A": (32, 48),
             "DEC": (32, 64), "IBM": (32, 64, 128), "TI": (16, 32, 40)}

    def test_bit_and_byte_orders(self):
        buf = ManagedBuffer(16)
        w = BitWriter(buf)
        # Least significant bit first, little endian: C bit fields.
        w.uint_bits(3, True, "le", 0b101)
        w.uint_bits(13, True, "le", 0x1ABC)
        w.uint_bits(12, False, "le", 0xFFD)
        w.uint(4, 0xA)
        w.uint_bits(32, False, (2, 3, 0, 1), 0x0A0B0C0D)
        self.assertEqual(buf.getvalue().hex(), "e5d5fdfa0b0a0d0c")
        r = BitReader(buf.getvalue())
        self.assertEqual(r.uint_bits(3, True, "le", "a"), 0b101)
        self.assertEqual(r.uint_bits(13, True, "le", "b"), 0x1ABC)
        self.assertEqual(r.uint_bits(12, False, "le", "c"), 0xFFD)
        self.assertEqual(r.uint(4, "d"), 0xA)
        self.assertEqual(r.uint_bits(32, False, (2, 3, 0, 1), "e"), 0x0A0B0C0D)

    def test_bcd(self):
        self.assertEqual(bcd_decode(0x1234, 16, True, "x"), 1234)
        self.assertEqual(bcd_decode(0x0506, 16, False, "x"), 56)
        self.assertEqual(bcd_encode(99, 16, False, "x"), 0x0909)
        for raw, packed in ((0x1A, True), (0x15, False)):
            with self.assertRaises(PacketError) as cm:
                bcd_decode(raw, 8, packed, "x")
            self.assertEqual(cm.exception.kind, "InvalidValue")
        with self.assertRaises(PacketError):
            bcd_encode(100, 16, False, "x")

    def test_floats_round_trip(self):
        # Every value a format decodes encodes back to itself.
        rng = random.Random(3)
        for fmt, sizes in self.SIZES.items():
            for bits in sizes:
                for _ in range(500):
                    try:
                        x = float_decode(fmt, bits, rng.getrandbits(bits), "f")
                    except PacketError:
                        continue
                    if not math.isfinite(x):
                        continue
                    y = float_decode(fmt, bits, float_encode(fmt, bits, x, "f"),
                                     "f")
                    self.assertEqual(x, y, (fmt, bits))

    def test_ieee_matches_struct(self):
        rng = random.Random(4)
        for code, bits in ((">e", 16), (">f", 32), (">d", 64)):
            for _ in range(2000):
                x = math.ldexp(rng.random() + 0.5, rng.randrange(-160, 140))
                try:
                    want = struct.pack(code, x)
                except OverflowError:
                    want = struct.pack(code, math.inf)
                raw = float_encode("IEEE754", bits, x, "f")
                self.assertEqual(raw.to_bytes(bits // 8, "big"), want, x)

    def test_charsets(self):
        for cs, s, wire in (
                ("ISO-8859-1", "Grüße", "4772fcdf65"),
                ("Windows-1252", "€ž", "809e"), ("UTF-16", "Aé", "004100e9"),
                ("UTF-16LE", "😀", "3dd800de"), ("UTF-32BE", "a", "00000061"),
                ("US-ASCII", "ok", "6f6b")):
            self.assertEqual(encode_text(s, cs, "f").hex(), wire)
            self.assertEqual(decode_text(bytes.fromhex(wire), cs, "f"), s)
        # A byte order mark picks the byte order of UTF-16.
        self.assertEqual(decode_text(bytes.fromhex("fffe4100"), "UTF-16", "f"),
                         "A")
        for cs, wire in (("Windows-1252", "81"), ("UTF-16BE", "d800"),
                         ("US-ASCII", "80"), ("UTF-32BE", "00110000")):
            with self.assertRaises(PacketError) as cm:
                decode_text(bytes.fromhex(wire), cs, "f")
            self.assertEqual(cm.exception.kind, "InvalidString")
        with self.assertRaises(PacketError):
            encode_text("€", "ISO-8859-1", "f")

    def test_encodings_packet(self):
        p = demo.Encodings(
            sec_hdr_flag=True, seq_flags=demo.SeqFlagsType.UNSEGMENTED,
            seq_count=1, bit_a=7, bit_b=8191, small=-2048, nibble=0, vax=1,
            packed=9999, digits=99, half_value=65504.0, extended_value=1e300,
            quad_value=-5e-324, mil32_value=0.5, mil48_value=-0.75,
            vax_f_value=-2.0, dec_d_value=1e30, ibm32_value=0.1,
            ibm64_value=-1e70, ibm128_value=1 / 3, ti16_value=-2.0,
            ti32_value=1e-30, ti40_value=-3.5, latin1="ÿ", ansi="Œ", wide="",
            wide_name="AB", wider="z", line="")
        q = demo.Encodings.decode(p.to_bytes())
        self.assertEqual(q.packed, 9999)
        self.assertEqual(q.small, -2048)
        self.assertEqual(q.extended_value, 1e300)
        self.assertEqual(q.quad_value, -5e-324)
        self.assertEqual(q.ibm128_value, 1 / 3)
        self.assertEqual(q.ti40_value, -3.5)
        self.assertEqual((q.latin1, q.ansi, q.wide_name), ("ÿ", "Œ", "AB"))
        # Values too large for formats without infinity don't encode.
        with self.assertRaises(PacketError) as cm:
            dataclasses.replace(p, ibm32_value=1e80).to_bytes()
        self.assertEqual(cm.exception.kind, "OutOfRange")
        with self.assertRaises(PacketError) as cm:
            dataclasses.replace(p, latin1="€").to_bytes()
        self.assertEqual(cm.exception.kind, "InvalidString")


if __name__ == "__main__":
    unittest.main()
