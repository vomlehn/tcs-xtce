//! Tests of the packet codecs generated from `tests/mission/demo.xml`.
//!
//! Decoding of hand-built packets is checked across languages by
//! `tests/packet_check.sh`; these tests cover encoding from structs.

#[path = "mission/demo.rs"]
mod demo;

use demo::*;
use xtce::packet::{Codec, Packet, PacketError};

fn housekeeping() -> Housekeeping {
    Housekeeping {
        sec_hdr_flag: true,
        seq_flags: SeqFlagsType::UNSEGMENTED,
        seq_count: 42,
        packet_length: 18,
        mode: ModeType::NOMINAL,
        temperature: 25.0,
        battery_voltage: 12.0,
        charging: true,
        bus_current: 1.5,
        uptime: 0x0102_0304,
        attitude: 1.5,
        delta: -2,
        offset: -5,
        trim: -3,
        ..Default::default()
    }
}

#[test]
fn encodes_with_inherited_constants() {
    let bytes = housekeeping().to_bytes().unwrap();
    // APID 100 and packet type TM come from the restriction criteria.
    assert_eq!(&bytes[..2], &[0x08, 0x64]);
    assert_eq!(bytes.len(), Housekeeping::MAX_BYTES);
    match CCSDSPacketKind::decode(&bytes).unwrap() {
        CCSDSPacketKind::Housekeeping(hk) => {
            assert_eq!(hk.mode, ModeType::NOMINAL);
            // 25 degC is quantized by the 8-bit spline encoding.
            assert!((hk.temperature - 25.0).abs() < 0.5);
            assert_eq!(hk.trim, -3);
        }
        other => panic!("decoded as {other}"),
    }
}

#[test]
fn dispatches_to_derived_containers() {
    let base = housekeeping();
    let science = ScienceHousekeeping {
        temperature: 20.0,
        science_count: u64::MAX,
        seq_count: base.seq_count,
        ..Default::default()
    };
    let bytes = science.to_bytes().unwrap();
    let kind = CCSDSPacketKind::decode(&bytes).unwrap();
    assert!(
        matches!(kind, CCSDSPacketKind::ScienceHousekeeping(ref s) if s.science_count == u64::MAX)
    );
    // The same packet also decodes as its base container.
    assert_eq!(
        Housekeeping::decode(&bytes).unwrap().mode,
        ModeType::SCIENCE
    );
}

#[test]
fn encoding_checks_criteria_and_ranges() {
    let hot = ScienceHousekeeping {
        temperature: 45.0,
        ..Default::default()
    };
    assert_eq!(hot.to_bytes().unwrap_err().kind(), "Mismatch");

    let mut hk = housekeeping();
    hk.seq_count = 1 << 14;
    assert_eq!(
        hk.to_bytes(),
        Err(PacketError::OutOfRange { field: "SeqCount" })
    );

    let mut hk = housekeeping();
    hk.temperature = 150.0;
    assert_eq!(hk.to_bytes().unwrap_err().kind(), "Calibration");

    let mut hk = housekeeping();
    hk.offset = -2048;
    assert_eq!(hk.to_bytes().unwrap_err().kind(), "OutOfRange");

    let thermal = Thermal::default();
    assert_eq!(
        thermal.to_bytes(),
        Err(PacketError::NotInvertible {
            field: "HeaterPower"
        })
    );
}

#[test]
fn strings_and_binary() {
    let mut ev = Event {
        origin: "ORBIT".into(),
        message: "x".repeat(32),
        source: "abc".into(),
        data_: vec![1, 2, 3, 4],
        ..Default::default()
    };
    let back = Event::decode(&ev.to_bytes().unwrap()).unwrap();
    assert_eq!(back, ev);

    ev.message.push('x'); // 33 bytes do not fit in 264 bits with the size tag
    assert_eq!(ev.to_bytes().unwrap_err().kind(), "TooLong");
    ev.message.clear();
    ev.origin = "NINE BYTE".into();
    assert_eq!(ev.to_bytes().unwrap_err().kind(), "TooLong");
    ev.origin = "a\0b".into();
    assert_eq!(ev.to_bytes().unwrap_err().kind(), "InvalidString");
    ev.origin.clear();
    ev.data_ = vec![1, 2, 3];
    assert_eq!(ev.to_bytes().unwrap_err().kind(), "OutOfRange");
}

#[test]
fn commands() {
    let cmd = Reboot {
        seq_count: 6,
        packet_length: 10,
        delay: 30.0,
        force: true,
        reason: "maint".into(),
    };
    let bytes = cmd.to_bytes().unwrap();
    assert_eq!(
        bytes,
        [
            0x11, 0x2c, 0xc0, 0x06, 0x00, 0x0a, 0x02, 0x01, 0x2c, 0x01, 0x00, 0x05, b'm', b'a',
            b'i', b'n', b't'
        ]
    );
    assert_eq!(
        CCSDSCommandKind::decode(&bytes).unwrap(),
        CCSDSCommandKind::Reboot(cmd)
    );
    let set = SetMode {
        mode: ModeArgType(9),
        ..Default::default()
    };
    // Unlisted enumeration values are kept.
    let back = SetMode::decode(&set.to_bytes().unwrap()).unwrap();
    assert_eq!(back.mode, ModeArgType(9));
    assert_eq!(back.mode.label(), None);
}

#[test]
fn codec_reuses_its_buffers() {
    let mut codec = Codec::for_packet::<CCSDSPacketKind>();
    let (input, output) = (codec.input_capacity(), codec.output_capacity());
    assert!(output >= CCSDSPacketKind::MAX_BYTES);
    let packets: Vec<CCSDSPacketKind> = vec![
        housekeeping().into(),
        Event {
            data_: vec![0; 4],
            ..Default::default()
        }
        .into(),
        ScienceHousekeeping::default().into(),
    ];
    for _ in 0..100 {
        for p in &packets {
            let bytes = codec.encode(p).unwrap().to_vec();
            codec.load(&bytes);
            // Calibrated values are quantized, so compare encodings.
            let back = codec.decode::<CCSDSPacketKind>().unwrap();
            assert_eq!(codec.encode(&back).unwrap(), &bytes[..]);
        }
    }
    assert_eq!(codec.input_capacity(), input);
    assert_eq!(codec.output_capacity(), output);
}

#[test]
fn include_condition() {
    let mut ev = Event {
        sec_hdr_flag: true,
        data_: vec![0; 4],
        coarse_time: Some(1_600_000_000.0),
        fine_time: Some(0.5),
        ..Default::default()
    };
    let back = Event::decode(&ev.to_bytes().unwrap()).unwrap();
    assert_eq!(back.fine_time, Some(0.5));

    // Without the secondary header flag the time stamp is not encoded.
    ev.sec_hdr_flag = false;
    let short = ev.to_bytes().unwrap();
    assert_eq!(short.len() + 6, back.to_bytes().unwrap().len());
    assert_eq!(Event::decode(&short).unwrap().coarse_time, None);

    ev.sec_hdr_flag = true;
    ev.fine_time = None;
    assert_eq!(
        ev.to_bytes(),
        Err(PacketError::Missing { field: "FineTime" })
    );
}
