//! Tests of the packet codecs generated from `tests/mission/demo.xml` with
//! `--bit-packing`.
//!
//! `tests/packet_check.sh` checks that they decode and encode exactly like
//! the unpacked codecs; these tests cover the accessors and the layout.

#[path = "mission/demo.rs"]
mod demo;
#[path = "mission/demo_packed.rs"]
mod demo_packed;

use demo_packed::*;
use xtce::packet::{Packet, PacketError};

fn housekeeping() -> Housekeeping {
    let mut hk = Housekeeping::default();
    hk.set_version(5).unwrap();
    hk.set_sec_hdr_flag(true);
    hk.set_seq_flags(SeqFlagsType::UNSEGMENTED).unwrap();
    hk.set_seq_count(42).unwrap();
    hk.set_packet_length(18).unwrap();
    hk.set_mode(ModeType::NOMINAL).unwrap();
    hk.temperature = 25.0;
    hk.battery_voltage = 12.0;
    hk.set_charging(true);
    hk.bus_current = 1.5;
    hk.set_uptime(0x0102_0304).unwrap();
    hk.attitude = 1.5;
    hk.set_delta(-2).unwrap();
    hk.set_offset(-5).unwrap();
    hk.set_trim(-3).unwrap();
    hk
}

#[test]
fn packs_without_padding() {
    // 113 bits of integer, enumerated and boolean fields take 15 bytes plus
    // the four f64 fields, where the unpacked struct gives each its own
    // byte-aligned integer.
    assert_eq!(
        std::mem::size_of::<Housekeeping>(),
        15usize.next_multiple_of(8) + 4 * 8
    );
    assert!(std::mem::size_of::<Housekeeping>() < std::mem::size_of::<demo::Housekeeping>());
    // Version (3 bits), SecHdrFlag (1), SeqFlags (2) and SeqCount (14) are
    // stored as XTCE packs encoded data, most significant bit first from the
    // top of byte 0, SeqCount crossing into the second byte.
    assert_eq!(&housekeeping().packed()[..2], &[0xbc, 0x02]);
    // Uptime is little-endian, so it is stored with its bytes swapped, as it
    // is encoded.
    let raw = xtce::packet::packed_get(housekeeping().packed(), 45, 32, false);
    assert_eq!(raw, 0x0403_0201);
}

#[test]
fn accessors_round_trip() {
    let hk = housekeeping();
    assert_eq!(hk.version(), 5);
    assert!(hk.sec_hdr_flag());
    assert_eq!(hk.seq_flags(), SeqFlagsType::UNSEGMENTED);
    assert_eq!(hk.seq_count(), 42);
    assert_eq!(hk.mode(), ModeType::NOMINAL);
    assert_eq!(hk.uptime(), 0x0102_0304);
    assert_eq!((hk.delta(), hk.offset(), hk.trim()), (-2, -5, -3));
    let mut hk = hk;
    hk.set_sec_hdr_flag(false);
    assert!(!hk.sec_hdr_flag());
    assert_eq!(hk.version(), 5);
    assert_eq!(hk.seq_flags(), SeqFlagsType::UNSEGMENTED);
}

#[test]
fn setters_check_the_encoding() {
    let mut hk = housekeeping();
    let range = |f| Err(PacketError::OutOfRange { field: f });
    assert_eq!(hk.set_version(8), range("Version"));
    assert_eq!(hk.set_seq_count(1 << 14), range("SeqCount"));
    assert_eq!(hk.set_offset(2048), range("Offset"));
    assert_eq!(hk.set_trim(-128), range("Trim"));
    assert_eq!(hk.set_seq_flags(SeqFlagsType(4)), range("SeqFlags"));
    // A failed set leaves the field as it was.
    assert_eq!(hk, housekeeping());
}

#[test]
fn encodes_like_the_unpacked_codec() {
    let hk = housekeeping();
    let plain = demo::Housekeeping {
        version: 5,
        sec_hdr_flag: true,
        seq_flags: demo::SeqFlagsType::UNSEGMENTED,
        seq_count: 42,
        packet_length: 18,
        mode: demo::ModeType::NOMINAL,
        temperature: 25.0,
        battery_voltage: 12.0,
        charging: true,
        bus_current: 1.5,
        uptime: 0x0102_0304,
        attitude: 1.5,
        delta: -2,
        offset: -5,
        trim: -3,
    };
    let bytes = hk.to_bytes().unwrap();
    assert_eq!(bytes, plain.to_bytes().unwrap());
    assert_eq!(hk.to_string(), plain.to_string());
    match CCSDSPacketKind::decode(&bytes).unwrap() {
        // Temperature is calibrated, so compare what survives the round trip.
        CCSDSPacketKind::Housekeeping(back) => {
            assert_eq!(back.to_bytes().unwrap(), bytes);
            assert_eq!((back.seq_count(), back.offset()), (42, -5));
        }
        other => panic!("decoded {other}"),
    }
}
