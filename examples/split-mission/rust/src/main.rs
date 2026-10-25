//! Records a rover's PUS telemetry, described by dt.xml, pus.xml and
//! rover.xml, in one stream, and decodes it again, using the code tcs-xtce
//! generated into rover.rs from the three documents together.
//!
//! The stream starts with the digest of the set of documents, which doesn't
//! depend on the order they are listed in, so that whatever reads it can
//! check that it was built from the same definitions.  The C, C++ and
//! Python versions of this program print identical output.  Run it in the
//! directory that holds the documents.

mod rover;

use rover::{CcsdsKind, Event, EventType, Hk};
use std::process::exit;
use tcs_xtce::digest::{digest_files_bytes, hex as digest_hex};
use tcs_xtce::packet::Packet;

/// The mission's documents: those the others build on first.
const DOCUMENTS: [&str; 3] = ["dt.xml", "pus.xml", "rover.xml"];

fn fail(msg: String) -> ! {
    eprintln!("split-mission: {msg}");
    exit(1);
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

fn digest(paths: &[&str]) -> [u8; 16] {
    digest_files_bytes(paths).unwrap_or_else(|e| fail(e.to_string()))
}

/// A housekeeping packet: PUS service 3, subservice 25.
fn hk(count: u16, battery: f64, motor_temp: f64, turns: (u16, u16)) -> Hk {
    Hk {
        ccsds_apid: 100,
        ccsds_seq_flags: 3,
        ccsds_seq_count: count,
        pus_version: 2,
        pus_counter: count,
        battery,
        motor_temp,
        left_turns: turns.0,
        right_turns: turns.1,
        ..Hk::default()
    }
}

fn main() {
    // The digest of the set is the same in any order.
    let set = digest(&DOCUMENTS);
    println!(
        "set digest: {} ({})",
        digest_hex(&set),
        DOCUMENTS.join(", ")
    );
    let shuffled = ["rover.xml", "dt.xml", "pus.xml"];
    if digest(&shuffled) != set {
        fail("the digest depends on the order of the documents".into());
    }
    println!("set digest: the same for {}", shuffled.join(", "));

    // The stream: the digest, then the packets, one after another.
    let packets: Vec<CcsdsKind> = vec![
        hk(1, 28.5, 41.25, (120, 118)).into(),
        Event {
            ccsds_apid: 100,
            ccsds_seq_flags: 3,
            ccsds_seq_count: 2,
            pus_version: 2,
            pus_counter: 2,
            event: EventType::OVERHEAT,
            event_data: 85,
            ..Event::default()
        }
        .into(),
        hk(3, 27.75, 38.5, (0, 3)).into(),
    ];
    let mut stream = set.to_vec();
    for p in &packets {
        let b = p.to_bytes().unwrap_or_else(|e| fail(format!("{p}: {e}")));
        println!("encode {p} -> {}", hex(&b));
        stream.extend_from_slice(&b);
    }
    println!(
        "stream: the set digest, then {} packets, {} bytes",
        packets.len(),
        stream.len()
    );

    // Reading it back: check the digest, then split the stream by each
    // packet's length (the bytes after its 6-byte header, minus one).
    if stream[..16] != digest(&DOCUMENTS) {
        fail("the stream's digest isn't that of these documents".into());
    }
    println!("set digest: the stream's matches");
    let mut at = 16;
    for p in &packets {
        let len =
            usize::from(u16::from_be_bytes([stream[at + 4], stream[at + 5]]))
                + 7;
        let got = CcsdsKind::decode(&stream[at..at + len])
            .unwrap_or_else(|e| fail(format!("decoding at {at}: {e}")));
        println!("decode at {at}: {len} bytes -> {got}");
        if got != *p {
            fail(format!("{got} isn't {p}"));
        }
        at += len;
    }

    // A wheel can't turn more than 1000 times between reports.
    let p = hk(4, 27.5, 30.0, (1001, 0));
    match p.to_bytes() {
        Ok(b) => fail(format!("encoding {p} gave {}", hex(&b))),
        Err(e) => println!("encode {p} -> error:{} ({e})", e.kind()),
    }
}
