//! Builds the `Status` record described by `bit_packing.xml`, using the code
//! `tcsxtce --bit-packing` generated into `bit_packing.rs`, and shows its
//! packed storage, a rejected value, and a serialize/deserialize round trip.
//!
//! The C/C++ and Python versions of this program print identical output.

mod bit_packing;

use bit_packing::{ModeType, Status};
use xtce::packet::Codec;

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

fn main() {
    // Packed fields are set through setters, which check that each value
    // fits its encoding.
    let mut status = Status::default();
    status
        .set_mode(ModeType::SCIENCE)
        .expect("Mode fits 3 bits");
    status.set_level(19).expect("Level fits 5 bits");
    status.set_enabled(true);
    status.set_offset(-3).expect("Offset fits 4 bits");
    status.set_count(600).expect("Count fits 10 bits");
    println!("value: {status}");

    // The five fields share 3 bytes, in the order XTCE packs encoded data.
    let packed = status.packed();
    println!("storage: {} bytes", packed.len());
    for (i, b) in packed.iter().enumerate() {
        println!("storage byte {i}: {b:08b}");
    }

    // A value too large for its 5 bits is rejected and nothing changes.
    match status.set_level(32) {
        Ok(()) => println!("set level 32 -> accepted"),
        Err(e) => println!(
            "set level 32 -> error:{} ({e}); level is still {}",
            e.kind(),
            status.level()
        ),
    }

    // The wire format is the XTCE one, most significant bit first.
    let mut codec = Codec::for_packet::<Status>();
    let bytes = codec.encode(&status).expect("encode").to_vec();
    println!("serialize -> {}", hex(&bytes));
    codec.load(&bytes);
    let back: Status = codec.decode().expect("decode");
    println!("deserialize -> {back}");
    println!(
        "storage after deserializing matches: {}",
        back.packed() == status.packed()
    );
}
