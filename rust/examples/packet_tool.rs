//! Decodes and re-encodes the packet vectors in `tests/mission/vectors.txt`.
//!
//! For each vector prints `<description of the decoded packet> | <hex of the
//! re-encoded packet>`, with `error:<kind>` in place of either on failure,
//! then ` | alarms: <field=level, ...>` for a packet with alarms, and
//! ` | monitor: <field=level, ...>` when one alarm monitor, kept across the
//! vectors (each at the time of its index), gives other levels.  The C++
//! and Python versions of this tool print identical output.
//!
//! Usage: `packet_tool VECTORS`

#[path = "../tests/mission/demo.rs"]
mod demo;

use std::fmt::Display;
use tcs_xtce::packet::{Alarms, Codec, Packet};

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

fn alarm_text<const N: usize>(a: &Alarms<N>, m: &Alarms<N>) -> String {
    let mut s = String::new();
    if !a.as_slice().is_empty() {
        s += &format!(" | alarms: {a}");
        if a.to_string() != m.to_string() {
            s += &format!(" | monitor: {m}");
        }
    }
    s
}

fn run<P: Packet + Display>(
    data: &[u8],
    alarms: impl FnOnce(&P) -> String,
) -> String {
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
    format!("{packet} | {again}{}", alarms(&packet))
}

fn main() {
    let path = std::env::args().nth(1).expect("usage: packet_tool VECTORS");
    let text = std::fs::read_to_string(path).expect("cannot read vectors");
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
        let t = f64::from(index);
        index += 1;
        let out = match kind {
            "tm" => run(&data, |p: &demo::CCSDSPacketKind| {
                alarm_text(&p.alarms(), &p.monitor(&mut mon, t))
            }),
            "tc" => run(&data, |p: &demo::CCSDSCommandKind| {
                alarm_text(&p.alarms(), &p.monitor(&mut mon, t))
            }),
            "hk" => run(&data, |p: &demo::Housekeeping| {
                alarm_text(&p.alarms(), &p.monitor(&mut mon, t))
            }),
            _ => panic!("unknown vector kind {kind}"),
        };
        println!("{out}");
    }
}
