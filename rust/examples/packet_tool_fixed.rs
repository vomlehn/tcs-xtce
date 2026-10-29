//! `packet_tool` for the codecs generated with `--dynamic-allocations
//! false`: prints exactly what `packet_tool` prints, and panics if
//! decoding, encoding or describing a packet allocates heap memory.
//!
//! Usage: `packet_tool_fixed VECTORS`

#[path = "../tests/mission/demo_fixed.rs"]
mod demo;

use std::alloc::{GlobalAlloc, Layout, System};
use std::fmt::{Display, Write as _};
use std::sync::atomic::{AtomicUsize, Ordering};
use tcs_xtce::fixed::{FixedBytes, FixedString};
use tcs_xtce::packet::{Alarms, Codec, Outputs, Packet};

/// The system allocator, counting the allocations made through it.
struct Counting;

static ALLOCATIONS: AtomicUsize = AtomicUsize::new(0);

unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        ALLOCATIONS.fetch_add(1, Ordering::Relaxed);
        System.alloc(layout)
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        System.dealloc(ptr, layout)
    }

    unsafe fn realloc(
        &self,
        ptr: *mut u8,
        layout: Layout,
        new_size: usize,
    ) -> *mut u8 {
        ALLOCATIONS.fetch_add(1, Ordering::Relaxed);
        System.realloc(ptr, layout, new_size)
    }
}

#[global_allocator]
static ALLOCATOR: Counting = Counting;

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

/// Large enough for the description and encoding of any packet of the demo
/// mission.
type Text = FixedString<8192>;
type Bytes = FixedBytes<4096>;

/// Describes a packet's alarms, and the monitor's levels if they differ.
///
/// # Arguments
///
/// * `a` - The packet's alarms.
/// * `m` - The levels the alarm monitor gives for the same packet.
/// * `t` - Where to append ` | alarms: ...`, then ` | monitor: ...` when
///         `m` differs from `a`; nothing is appended when the packet has
///         no alarms.
///
/// # Panics
///
/// If the description doesn't fit in a `Text`.
fn alarm_text<const N: usize>(a: &Alarms<N>, m: &Alarms<N>, t: &mut Text) {
    if !a.as_slice().is_empty() {
        write!(t, " | alarms: {a}").expect("alarms too long");
        let (mut x, mut y) = (Text::new(), Text::new());
        write!(x, "{a}").expect("alarms too long");
        write!(y, "{m}").expect("alarms too long");
        if x.as_str() != y.as_str() {
            write!(t, " | monitor: {m}").expect("alarms too long");
        }
    }
}

/// Describes the outputs of the MathAlgorithms a packet triggers.
///
/// # Arguments
///
/// * `o` - The algorithms' outputs.
/// * `t` - Where to append ` | derived: ...`, if there are outputs.
///
/// # Panics
///
/// If the description doesn't fit in a `Text`.
fn derived_text<const N: usize>(o: &Outputs<N>, t: &mut Text) {
    if !o.as_slice().is_empty() {
        write!(t, " | derived: {o}").expect("outputs too long");
    }
}

/// Decodes a packet, encodes it again and describes both, panicking if
/// any of that allocates heap memory.
///
/// # Arguments
///
/// * `codec`  - The codec to encode with, sized for the packet.
/// * `data`   - The packet's bytes.
/// * `alarms` - Appends a description of the decoded packet's alarms and
///              derived values to the text it is given.
///
/// # Returns
///
/// The line the tool prints for the vector.
///
/// # Panics
///
/// If decoding, encoding or describing the packet allocates heap
/// memory, or the description or encoding doesn't fit.
fn run<P: Packet + Display>(
    codec: &mut Codec,
    data: &[u8],
    alarms: impl FnOnce(&P, &mut Text),
) -> String {
    let mut text = Text::new();
    let mut alarmed = Text::new();
    let mut bytes = Bytes::new();
    let before = ALLOCATIONS.load(Ordering::Relaxed);
    let packet = P::decode(data);
    let again = packet.as_ref().map(|p| {
        write!(text, "{p}").expect("description too long");
        alarms(p, &mut alarmed);
        codec
            .encode(p)
            .map(|out| bytes.extend_from_slice(out).expect("packet too long"))
    });
    let after = ALLOCATIONS.load(Ordering::Relaxed);
    assert_eq!(before, after, "the codec allocated heap memory");
    match again {
        Err(e) => format!("error:{} | -", e.kind()),
        Ok(Err(e)) => format!(
            "{} | error:{}{}",
            text.as_str(),
            e.kind(),
            alarmed.as_str()
        ),
        Ok(Ok(())) => {
            format!("{} | {}{}", text.as_str(), hex(&bytes), alarmed.as_str())
        }
    }
}

/// Seconds as whole milliseconds, or None outside 0 to 2^32 - 1.
fn ms(s: f64) -> Option<u32> {
    let v = (s * 1000.0 + 0.5).floor();
    (v >= 0.0 && v < 4294967296.0).then_some(v as u32)
}

/// The demo mission's custom algorithms, the same in every version of this
/// tool.
fn set_hooks() {
    demo::hooks::IS_EVEN.set(|v| v[0] % 2.0 == 0.0).ok();
    demo::hooks::MIN_MAX
        .set(|v, out| {
            out[0] = v.iter().copied().fold(f64::INFINITY, f64::min);
            out[1] = v.iter().copied().fold(f64::NEG_INFINITY, f64::max);
            true
        })
        .ok();
    demo::hooks::BANDS.set(|v| (v[0] / 50.0) as u8).ok();
    demo::hooks::TWICE
        .set(|d, start, end| {
            2 * d[start / 8..end / 8]
                .iter()
                .map(|&b| u64::from(b))
                .sum::<u64>()
        })
        .ok();
    // The demo's transforms: Expand doubles every byte, Shrink takes every
    // other one (and gives nothing when a pair differs); Mask and Unmask
    // are XOR 0x5A.
    demo::hooks::EXPAND
        .set(|b, out| {
            if 2 * b.len() > out.len() {
                return Some(2 * b.len()); // the room it needs
            }
            for (i, &x) in b.iter().enumerate() {
                out[2 * i] = x;
                out[2 * i + 1] = x;
            }
            Some(2 * b.len())
        })
        .ok();
    demo::hooks::SHRINK
        .set(|b, out| {
            if b.len() % 2 != 0 || b.chunks(2).any(|p| p[0] != p[1]) {
                return None;
            }
            if b.len() / 2 > out.len() {
                return Some((b.len(), b.len() / 2)); // the room it needs
            }
            for (o, p) in out.iter_mut().zip(b.chunks(2)) {
                *o = p[0];
            }
            Some((b.len(), b.len() / 2))
        })
        .ok();
    demo::hooks::MASK
        .set(|b, out| {
            for (o, &x) in out.iter_mut().zip(b) {
                *o = x ^ 0x5A;
            }
            Some(b.len())
        })
        .ok();
    demo::hooks::UNMASK
        .set(|b, out| {
            for (o, &x) in out.iter_mut().zip(b) {
                *o = x ^ 0x5A;
            }
            Some((b.len(), b.len()))
        })
        .ok();
    // Stuff and Unstuff frame bytes with their length; MsFrom and MsTo are
    // milliseconds in four bytes, VarFrom and VarTo milliseconds in a
    // varint.
    demo::hooks::STUFF
        .set(|b, out| {
            if b.len() > 255 {
                return None;
            }
            if b.len() + 1 > out.len() {
                return Some(b.len() + 1); // the room it needs
            }
            out[0] = b.len() as u8;
            out[1..=b.len()].copy_from_slice(b);
            Some(b.len() + 1)
        })
        .ok();
    demo::hooks::UNSTUFF
        .set(|b, out| {
            let n = usize::from(*b.first()?);
            if b.len() <= n {
                return None;
            }
            if n > out.len() {
                return Some((n + 1, n)); // the room it needs
            }
            out[..n].copy_from_slice(&b[1..=n]);
            Some((n + 1, n))
        })
        .ok();
    demo::hooks::MS_FROM
        .set(|b| {
            let v = u32::from_be_bytes(b.get(..4)?.try_into().ok()?);
            Some((4, f64::from(v) / 1000.0))
        })
        .ok();
    demo::hooks::MS_TO
        .set(|s, out| {
            let v = ms(s)?;
            if out.len() < 4 {
                return Some(4); // the room it needs
            }
            out[..4].copy_from_slice(&v.to_be_bytes());
            Some(4)
        })
        .ok();
    demo::hooks::VAR_FROM
        .set(|b| {
            let mut v = 0u64;
            for (i, &x) in b.iter().take(5).enumerate() {
                v |= u64::from(x & 0x7F) << (7 * i);
                if x & 0x80 == 0 {
                    return Some((i + 1, v as f64 / 1000.0));
                }
            }
            None
        })
        .ok();
    demo::hooks::VAR_TO
        .set(|s, out| {
            let mut v = ms(s)?;
            let mut n = 0;
            let mut buf = [0u8; 5];
            while v >= 0x80 {
                buf[n] = (v & 0x7F) as u8 | 0x80;
                v >>= 7;
                n += 1;
            }
            buf[n] = v as u8;
            n += 1;
            if n > out.len() {
                return Some(n); // the room it needs
            }
            out[..n].copy_from_slice(&buf[..n]);
            Some(n)
        })
        .ok();
}

/// Prints the line for each vector in the file on the command line.
///
/// # Panics
///
/// Without a vectors file, if it can't be read or holds bad hex, or if
/// handling a packet allocates heap memory.
fn main() {
    set_hooks();
    let path = std::env::args()
        .nth(1)
        .expect("usage: packet_tool_fixed VECTORS");
    let text = std::fs::read_to_string(path).expect("cannot read vectors");
    // Created once, during initialization, sized for the largest packet.
    let mut tm = Codec::for_packet::<demo::CCSDSPacketKind>();
    let mut tc = Codec::for_packet::<demo::CCSDSCommandKind>();
    let mut hk = Codec::for_packet::<demo::Housekeeping>();
    let mut mon = demo::AlarmMonitor::default();
    let mut index = 0u32;
    for line in text.lines() {
        let line = line.split('#').next().unwrap_or("");
        let mut words = line.split_whitespace();
        let (Some(kind), Some(h)) = (words.next(), words.next()) else {
            continue;
        };
        let data: Vec<u8> = (0..h.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&h[i..i + 2], 16).expect("bad hex"))
            .collect();
        let time = f64::from(index);
        index += 1;
        let out = match kind {
            "tm" => run(&mut tm, &data, |p: &demo::CCSDSPacketKind, t| {
                alarm_text(&p.alarms(), &p.monitor(&mut mon, time), t);
                derived_text(&p.algorithms(), t)
            }),
            "tc" => run(&mut tc, &data, |p: &demo::CCSDSCommandKind, t| {
                alarm_text(&p.alarms(), &p.monitor(&mut mon, time), t);
                derived_text(&p.algorithms(), t)
            }),
            "hk" => run(&mut hk, &data, |p: &demo::Housekeeping, t| {
                alarm_text(&p.alarms(), &p.monitor(&mut mon, time), t);
                derived_text(&p.algorithms(), t)
            }),
            _ => panic!("unknown vector kind {kind}"),
        };
        println!("{out}");
    }
}
