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
use xtce::fixed::{FixedBytes, FixedString};
use xtce::packet::{Codec, Packet};

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

    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
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

fn run<P: Packet + Display>(codec: &mut Codec, data: &[u8]) -> String {
    let mut text = Text::new();
    let mut bytes = Bytes::new();
    let before = ALLOCATIONS.load(Ordering::Relaxed);
    let packet = P::decode(data);
    let again = packet.as_ref().map(|p| {
        write!(text, "{p}").expect("description too long");
        codec
            .encode(p)
            .map(|out| bytes.extend_from_slice(out).expect("packet too long"))
    });
    let after = ALLOCATIONS.load(Ordering::Relaxed);
    assert_eq!(before, after, "the codec allocated heap memory");
    match again {
        Err(e) => format!("error:{} | -", e.kind()),
        Ok(Err(e)) => format!("{} | error:{}", text.as_str(), e.kind()),
        Ok(Ok(())) => format!("{} | {}", text.as_str(), hex(&bytes)),
    }
}

fn main() {
    let path = std::env::args()
        .nth(1)
        .expect("usage: packet_tool_fixed VECTORS");
    let text = std::fs::read_to_string(path).expect("cannot read vectors");
    // Created once, during initialization, sized for the largest packet.
    let mut tm = Codec::for_packet::<demo::CCSDSPacketKind>();
    let mut tc = Codec::for_packet::<demo::CCSDSCommandKind>();
    let mut hk = Codec::for_packet::<demo::Housekeeping>();
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
        let out = match kind {
            "tm" => run::<demo::CCSDSPacketKind>(&mut tm, &data),
            "tc" => run::<demo::CCSDSCommandKind>(&mut tc, &data),
            "hk" => run::<demo::Housekeeping>(&mut hk, &data),
            _ => panic!("unknown vector kind {kind}"),
        };
        println!("{out}");
    }
}
