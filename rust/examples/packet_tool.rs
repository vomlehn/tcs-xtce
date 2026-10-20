//! Decodes and re-encodes the packet vectors in `tests/mission/vectors.txt`.
//!
//! For each vector prints `<description of the decoded packet> | <hex of the
//! re-encoded packet>`, with `error:<kind>` in place of either on failure.
//! The C++ and Python versions of this tool print identical output.  With
//! `--packed` it uses the codecs generated with `--bit-packing`, which must
//! print the same.
//!
//! Usage: `packet_tool [--packed] VECTORS`

#[path = "../tests/mission/demo.rs"]
mod demo;
#[path = "../tests/mission/demo_packed.rs"]
mod demo_packed;

use std::fmt::Display;
use xtce::packet::{Codec, Packet};

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

fn run<P: Packet + Display>(data: &[u8]) -> String {
    let mut codec = Codec::for_packet::<P>();
    codec.load(data);
    let packet = match codec.decode::<P>() {
        Ok(p) => p,
        Err(e) => return format!("error:{} | -", e.kind()),
    };
    let again = match codec.encode(&packet) {
        Ok(out) => hex(out),
        Err(e) => format!("error:{}", e.kind()),
    };
    format!("{packet} | {again}")
}

fn main() {
    let mut args: Vec<String> = std::env::args().skip(1).collect();
    let packed = args.first().is_some_and(|a| a == "--packed");
    if packed {
        args.remove(0);
    }
    let path = args.first().expect("usage: packet_tool [--packed] VECTORS");
    let text = std::fs::read_to_string(path).expect("cannot read vectors");
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
        let out = match (kind, packed) {
            ("tm", false) => run::<demo::CCSDSPacketKind>(&data),
            ("tc", false) => run::<demo::CCSDSCommandKind>(&data),
            ("hk", false) => run::<demo::Housekeeping>(&data),
            ("tm", true) => run::<demo_packed::CCSDSPacketKind>(&data),
            ("tc", true) => run::<demo_packed::CCSDSCommandKind>(&data),
            ("hk", true) => run::<demo_packed::Housekeeping>(&data),
            _ => panic!("unknown vector kind {kind}"),
        };
        println!("{out}");
    }
}
