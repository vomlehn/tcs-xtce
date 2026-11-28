"""Tests of tcs_xtce.dynamic, the run-time codec, on the demo mission: the
demo's custom algorithms supplied as Python functions, every vector
unpacking and packing again to the bytes the generated codecs give, with
the alarm levels they give, and lookups, values and a stream."""

import math
import unittest
from pathlib import Path

from tcs_xtce.dynamic import Mission, PacketError, SetError

MISSION = Path(__file__).resolve().parents[2] / "tests" / "mission"


def ms(s: float):
    """Gives seconds as whole milliseconds.

    Args:
        s: The seconds.

    Returns:
        The milliseconds, or None outside 0 to 2^32 - 1.
    """
    v = math.floor(s * 1000.0 + 0.5)
    return v if 0 <= v < 1 << 32 else None


def shrink(b: bytes):
    """Takes every other byte; nothing when a pair differs.

    Args:
        b: The bytes.

    Returns:
        The bytes taken and those made, or None.
    """
    if len(b) % 2 or any(b[i] != b[i + 1] for i in range(0, len(b), 2)):
        return None
    return len(b), b[::2]


def unstuff(b: bytes):
    """Takes bytes framed with their length.

    Args:
        b: The bytes.

    Returns:
        The bytes taken and those made, or None.
    """
    if not b or len(b) <= b[0]:
        return None
    return b[0] + 1, b[1:b[0] + 1]


def var_from(b: bytes):
    """Reads milliseconds in a varint.

    Args:
        b: The bytes.

    Returns:
        The bytes taken and the seconds, or None.
    """
    v = 0
    for i, x in enumerate(b[:5]):
        v |= (x & 0x7F) << (7 * i)
        if not x & 0x80:
            return i + 1, v / 1000.0
    return None


def var_to(s: float):
    """Writes milliseconds in a varint.

    Args:
        s: The seconds.

    Returns:
        The bytes, or None.
    """
    v = ms(s)
    if v is None:
        return None
    out = bytearray()
    while v >= 0x80:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v)
    return bytes(out)


def set_algorithms(m: Mission, calls: list) -> None:
    """Supplies the demo mission's custom algorithms.

    Args:
        m:     The mission.
        calls: Where Unmask counts its calls, a function with state.
    """
    def unmask(b):
        calls.append(1)
        return len(b), bytes(x ^ 0x5A for x in b)

    m.set_algorithm("/Demo/IsEven", lambda v: v[0] % 2 == 0)
    m.set_algorithm("/Demo/MinMax", lambda v: [min(v), max(v)])
    m.set_algorithm("/Demo/Bands", lambda v: int(v[0] / 50))
    m.set_algorithm("/Demo/Twice", lambda d, s, e: 2 * sum(d[s // 8:e // 8]))
    m.set_algorithm("Expand", lambda b: bytes(x for x in b for _ in "ab"))
    m.set_algorithm("Shrink", shrink)
    m.set_algorithm("Mask", lambda b: bytes(x ^ 0x5A for x in b))
    m.set_algorithm("Unmask", unmask)
    m.set_algorithm("Stuff",
                    lambda b: bytes([len(b)]) + b if len(b) < 256 else None)
    m.set_algorithm("Unstuff", unstuff)
    m.set_algorithm("MsFrom",
                    lambda b: (4, int.from_bytes(b[:4], "big") / 1000.0)
                    if len(b) >= 4 else None)
    m.set_algorithm(
        "MsTo", lambda s: None if ms(s) is None else ms(s).to_bytes(4, "big"))
    m.set_algorithm("VarFrom", var_from)
    m.set_algorithm("VarTo", var_to)
    m.set_algorithm("Descramble", lambda b: bytes(x ^ 0x55 for x in b))
    m.set_algorithm("Invert", lambda bit: bit ^ 1)


def number(text: str) -> float:
    """Reads a derived value as the runtimes write it.

    Args:
        text: The value, such as ``1.5`` or ``INF``.

    Returns:
        The value.
    """
    return {"INF": math.inf, "-INF": -math.inf, "NAN": math.nan}.get(
        text, None) or float(text)


class DynamicTest(unittest.TestCase):
    """The run-time codec, through Python."""

    def setUp(self):
        self.m = Mission([(MISSION / "demo.xtce").read_bytes()])
        self.calls = []
        set_algorithms(self.m, self.calls)

    def test_vectors_unpack_and_pack_as_the_generated_codecs_do(self):
        m = self.m
        roots = {"tm": m.container(m.top, "/Demo/CCSDSPacket"),
                 "tc": m.meta_command(m.top, "/Demo/CCSDSCommand"),
                 "hk": m.container(m.top, "/Demo/Housekeeping")}
        vectors = (MISSION / "vectors.txt").read_text().splitlines()
        expected = (MISSION / "vectors.expected").read_text().splitlines()
        scratch, mon = m.scratch(), m.monitor()
        matched, t = 0, 0
        for v, want in zip(vectors, expected):
            words = v.split("#")[0].split()
            if len(words) < 2:
                continue
            kind, data = words[0], bytes.fromhex(words[1])
            parts = want.split(" | ")
            t += 1
            try:
                node = m.unpack(roots[kind], data, scratch)
            except PacketError:
                self.assertTrue(want.startswith("error:"), v)
                matched += 1
                continue
            store = m.store()
            store.commit(scratch)
            try:
                again = m.pack(node, store).hex()
            except PacketError:
                again = "error"
            self.assertEqual(again.split(":")[0], parts[1].split(":")[0], v)
            extra = dict(p.split(": ", 1) for p in parts[2:])
            alarms = m.alarms(scratch)
            monitor = m.monitor_alarms(scratch, mon, float(t - 1))
            text = ", ".join(f"{n}={lv or 'none'}" for n, lv in alarms)
            if alarms:
                self.assertEqual(text, extra.get("alarms"), v)
                mtext = ", ".join(f"{n}={lv or 'none'}" for n, lv in monitor)
                self.assertEqual(mtext, extra.get("monitor", text), v)
            derived = m.derived(scratch)
            if derived:
                want_d = dict(x.split("=")
                              for x in extra["derived"].split(", "))
                for n, x in derived:
                    w = want_d[n]
                    if w == "none":
                        self.assertIsNone(x, v)
                    else:
                        self.assertTrue(math.isclose(x, number(w),
                                                     rel_tol=1e-15), v)
            matched += 1
        self.assertGreater(matched, 290)
        self.assertTrue(self.calls, "Unmask, a function with state")

    def test_values_lookups_and_streams(self):
        m = self.m
        root = m.container(m.top, "/Demo/CCSDSPacket")
        hk = bytes.fromhex("0864c02a001201a04e2805dc040302013fc00000fffe8050fc")
        scratch, store = m.scratch(), m.store()
        node = m.unpack(root, hk, scratch)
        self.assertEqual(m.path_of(node), "/Demo/Housekeeping")
        store.commit(scratch)
        seq = m.parameter(m.top, "/Demo/SeqCount")
        mode = m.parameter(m.top, "/Demo/Mode")
        self.assertEqual(store.get(seq), 42)
        self.assertEqual(m.kind(mode), "enum")
        store.set(mode, "Safe")
        self.assertEqual(store.get(mode), 0)
        with self.assertRaises(SetError):
            store.set(mode, 99)
        with self.assertRaises(LookupError):
            m.parameter(m.top, "/Demo/NoSuch")
        store.set(seq, 7)
        again = m.unpack(root, m.pack(node, store), scratch)
        other = m.store()
        other.commit(scratch)
        self.assertEqual(m.path_of(again), "/Demo/Housekeeping")
        self.assertEqual(other.get(seq), 7)
        probes = m.parameter_slots(m.top, "/Demo/Probes")
        self.assertEqual(m.path_of(probes[4]), "/Demo/Probes[1].Gains[0]")
        self.assertTrue(all(a[2] for a in m.algorithms()))
        dl = m.stream("/Demo/Downlink")
        frame = bytes.fromhex("1acffc1d011111111111111111111111")
        frames = [f for _ in range(3) for f in dl.push(frame)]
        self.assertTrue(frames)
        with self.assertRaises(RuntimeError):
            m.set_algorithm("Invert", lambda bit: bit)
        del dl
        m.set_algorithm("Invert", lambda bit: bit ^ 1)

    def test_an_exception_in_a_function_is_raised(self):
        m = self.m

        def broken(b):
            raise ZeroDivisionError("on purpose")

        m.set_algorithm("Unmask", broken)
        root = m.container(m.top, "/Demo/CCSDSPacket")
        with self.assertRaises(ZeroDivisionError):
            m.unpack(root, bytes.fromhex("0a62c02a0004f0e1968777"), m.scratch())


if __name__ == "__main__":
    unittest.main()
