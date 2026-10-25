//! Encodes and decodes a probe's status, described by modes.xml, using the
//! code tcs-xtce generated into probe.rs.
//!
//! The status's mode is an enumeration, and everything that depends on it
//! names a label: Target is there only in SCIENCE mode and Station only in
//! DOWNLINK mode, a SafeStatus is a Status in SAFE mode, and the mode's alarm
//! is a warning in SAFE mode and a watch in TEST mode.  So this program names
//! labels too, never numbers.  The C, C++ and Python versions of this
//! program print identical output.

mod probe;

use probe::{ModeType, ReasonType, SafeStatus, Status, StatusKind};
use std::process::exit;
use tcs_xtce::packet::Packet;

fn fail(msg: String) -> ! {
    eprintln!("enum-labels: {msg}");
    exit(1);
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

fn bytes(h: &str) -> Vec<u8> {
    (0..h.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&h[i..i + 2], 16).unwrap())
        .collect()
}

/// Encodes a packet, prints it, and returns the bytes.
fn encode<P: Packet + std::fmt::Display>(p: &P) -> Vec<u8> {
    match p.to_bytes() {
        Ok(b) => {
            println!("encode {p} -> {}", hex(&b));
            b
        }
        Err(e) => fail(format!("encoding {p}: {e}")),
    }
}

/// Decodes a status of either kind and prints it, with its alarms.
fn decode(b: &[u8]) -> StatusKind {
    match StatusKind::decode(b) {
        Ok(p) => {
            println!("decode {} -> {p} | alarms: {}", hex(b), p.alarms());
            p
        }
        Err(e) => fail(format!("decoding {}: {e}", hex(b))),
    }
}

fn main() {
    // Labels name the values, and one label can name a range of them.
    for v in [0, 2, 20, 40] {
        match ModeType(v).label() {
            Some(l) => println!("mode {v} is {l}"),
            None => println!("mode {v} has no label"),
        }
    }

    // One status in each mode but SAFE, which is a SafeStatus: its
    // restriction fixes the mode, so the structure has no mode field.
    let statuses = [
        Status {
            mode: ModeType::IDLE,
            battery: 80,
            ..Status::default()
        },
        Status {
            mode: ModeType::SCIENCE,
            battery: 75,
            target: Some(1234),
            ..Status::default()
        },
        Status {
            mode: ModeType::DOWNLINK,
            battery: 70,
            station: Some(7),
            ..Status::default()
        },
    ];
    let safe = SafeStatus {
        battery: 9,
        reason: ReasonType::LOW_POWER,
        ..SafeStatus::default()
    };
    let mut packets: Vec<Vec<u8>> = statuses.iter().map(encode).collect();
    packets.push(encode(&safe));

    // Decoding picks the SafeStatus by its mode's label, SAFE.
    for (i, b) in packets.iter().enumerate() {
        let p = decode(b);
        let back = match (i, &p) {
            (3, StatusKind::SafeStatus(s)) => *s == safe,
            (_, StatusKind::Status(s)) => i < 3 && *s == statuses[i],
            _ => false,
        };
        if !back {
            fail(format!("{} decoded to {p}", hex(b)));
        }
    }

    // A value in TEST's range, and one with no label: decoding keeps it.
    for h in ["1463", "2850"] {
        let p = decode(&bytes(h));
        if hex(&p.to_bytes().unwrap()) != h {
            fail(format!("{h} encodes again as something else"));
        }
    }

    // SCIENCE mode needs a Target.
    let s = Status {
        mode: ModeType::SCIENCE,
        battery: 50,
        ..Status::default()
    };
    match s.to_bytes() {
        Ok(b) => fail(format!("encoding {s} gave {}", hex(&b))),
        Err(e) => println!("encode {s} -> error:{} ({e})", e.kind()),
    }
}
