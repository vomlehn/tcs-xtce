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

#[test]
fn arrays_and_sizes_from_other_fields() {
    let mut s = Samples {
        sec_hdr_flag: true,
        sample_count: 2,
        readings: vec![-1, 7],
        grid: Some(vec![1, 2, 3, 4, 5, 6]),
        valid: vec![true, false, false, true],
        label_size: 4,
        label_: "abc".into(),
        payload_size: 1,
        payload: vec![0x5a],
        ..Default::default()
    };
    let bytes = s.to_bytes().unwrap();
    assert_eq!(Samples::decode(&bytes).unwrap(), s);

    // The data must have the count or size that its field gives.
    let bad = |s: &Samples| s.to_bytes().map_err(|e| e.to_string());
    s.readings.push(3);
    assert_eq!(bad(&s), Err("invalid size or count for Readings".into()));
    s.readings.pop();
    s.payload.clear();
    assert_eq!(bad(&s), Err("invalid size or count for Payload".into()));
    s.payload.push(0x5a);
    s.valid.pop();
    assert_eq!(bad(&s), Err("invalid size or count for Valid".into()));
    s.valid.push(true);
    s.label_size = 11; // beyond the ValidRange that bounds the label
    assert_eq!(bad(&s), Err("invalid size or count for Label".into()));
    s.label_size = 4;
    s.label_ = "abcde".into();
    assert_eq!(bad(&s), Err("Label is too long for its encoding".into()));
    s.label_ = "abcd".into(); // fills its size, without a terminator
    assert_eq!(Samples::decode(&s.to_bytes().unwrap()).unwrap(), s);

    let mut up = Upload {
        word_count: 3,
        words: vec![1, 2, 3],
        gains: vec![1.0, 2.5],
        tag_size: 4,
        tag: "ab".into(),
        ..Default::default()
    };
    let back = Upload::decode(&up.to_bytes().unwrap()).unwrap();
    assert_eq!(back, up);
    up.gains.push(0.0);
    assert_eq!(up.to_bytes(), Err(PacketError::BadSize { field: "Gains" }));
}

#[test]
fn aggregates() {
    let mut t = Targets {
        sec_hdr_flag: true,
        start: PositionType { x: 1, y: -1 },
        target_count: 1,
        targets: vec![TargetType {
            id_: 3,
            mode: ModeType::SAFE,
            name_: "a".into(),
            where_: PositionType { x: 2, y: 3 },
            weights: vec![1, 2],
        }],
        home: Some(PositionType { x: 4, y: 5 }),
        ..Default::default()
    };
    assert_eq!(Targets::decode(&t.to_bytes().unwrap()).unwrap(), t);

    // Members are checked like fields; errors name the parameter.
    t.targets[0].id_ = 16;
    assert_eq!(
        t.to_bytes(),
        Err(PacketError::OutOfRange { field: "Targets" })
    );
    t.targets[0].id_ = 3;
    t.targets[0].weights.pop();
    assert_eq!(t.to_bytes(), Err(PacketError::BadSize { field: "Targets" }));
    t.targets[0].weights.push(2);
    t.home = None;
    assert_eq!(t.to_bytes(), Err(PacketError::Missing { field: "Home" }));

    let g = Goto {
        waypoint: WaypointType {
            north: 1.5,
            east: 2.0,
            hold: true,
        },
        ..Default::default()
    };
    assert_eq!(Goto::decode(&g.to_bytes().unwrap()).unwrap(), g);
}

#[test]
fn calibrated_integers_and_the_rest_of_the_packet() {
    let d = Dump {
        sec_hdr_flag: true,
        board_temp: 25,
        record_count: 2,
        records: vec![7, 8],
        rest: vec![0xde, 0xad],
        ..Default::default()
    };
    let b = d.to_bytes().unwrap();
    // Raw values: 25 = -100 + 125 and 2 = -4 + 12 / 2.
    assert_eq!(&b[6..], &[0x7d, 0x0c, 7, 8, 0xde, 0xad]);
    assert_eq!(Dump::decode(&b).unwrap(), d);
    // The rest of the packet is whatever follows the records.
    assert_eq!(Dump::decode(&b[..11]).unwrap().rest, vec![0xde]);

    let mut e = d.clone();
    e.record_count = 200; // raw 408 does not fit 8 bits
    assert_eq!(
        e.to_bytes(),
        Err(PacketError::OutOfRange {
            field: "RecordCount"
        })
    );
    e.record_count = 3;
    assert_eq!(e.to_bytes(), Err(PacketError::BadSize { field: "Records" }));
    e.record_count = 2;
    e.rest = vec![0; 65537];
    assert_eq!(e.to_bytes(), Err(PacketError::TooLong { field: "Rest" }));

    // 21 needs raw 5.5, which rounds to 6 and decodes as 22.
    let p = Poke {
        level: 21,
        data_: vec![0xbe, 0xef],
        ..Default::default()
    };
    let q = Poke::decode(&p.to_bytes().unwrap()).unwrap();
    assert_eq!((q.level, q.data_), (22, vec![0xbe, 0xef]));
}

#[test]
fn repeated_containers_and_container_sizes() {
    let l = Log {
        sec_hdr_flag: true,
        log_count: 2,
        log_record: vec![
            LogRecord {
                channel: 3,
                reading: 0x1234,
            },
            LogRecord {
                channel: 15,
                reading: -1,
            },
        ],
        closed: true,
        ..Default::default()
    };
    let b = l.to_bytes().unwrap();
    // Each record is 4 + 2 + 16 bits padded to 32; the trailer 1 padded to 16.
    assert_eq!(
        &b[6..],
        &[0x02, 0x30, 0x48, 0xd0, 0x00, 0xf3, 0xff, 0xfc, 0x00, 0x80, 0x00]
    );
    assert_eq!(Log::decode(&b).unwrap(), l);
    // Padding must be there, though its value is ignored.
    assert_eq!(
        Log::decode(&b[..b.len() - 1]),
        Err(PacketError::Truncated { field: "/Demo/Log" })
    );
    let mut e = l.clone();
    e.log_count = 1;
    assert_eq!(
        e.to_bytes(),
        Err(PacketError::BadSize { field: "LogRecord" })
    );
}

#[test]
fn sizes_from_aggregate_members() {
    let b = Batches {
        sec_hdr_flag: true,
        batch_count: 2,
        batches: vec![
            BatchType {
                size: 2,
                values: vec![0xaa, 0xbb],
            },
            BatchType {
                size: 0,
                values: vec![],
            },
        ],
        ..Default::default()
    };
    let bytes = b.to_bytes().unwrap();
    assert_eq!(&bytes[6..], &[0x02, 0x2a, 0xab, 0xb0]);
    assert_eq!(Batches::decode(&bytes).unwrap(), b);
    // Each copy's size must match its own values.
    let mut e = b.clone();
    e.batches[1].size = 1;
    assert_eq!(e.to_bytes(), Err(PacketError::BadSize { field: "Batches" }));
}

#[test]
fn booleans_and_strings_with_other_encodings() {
    let r = Reply {
        sec_hdr_flag: true,
        answer: true,
        code: "ABC".into(),
        note_size: 2,
        note: "é".into(),
        ..Default::default()
    };
    let b = r.to_bytes().unwrap();
    assert_eq!(&b[6..], b"yes\0ABC\x02\xc3\xa9");
    assert_eq!(Reply::decode(&b).unwrap(), r);
    let mut e = r.clone();
    e.answer = false;
    assert_eq!(&e.to_bytes().unwrap()[6..10], b"no\0\0");
    // Strings encoded as binary fill their field exactly.
    e.code = "AB".into();
    assert_eq!(e.to_bytes(), Err(PacketError::OutOfRange { field: "Code" }));
    e.code = "ABCD".into();
    assert_eq!(e.to_bytes(), Err(PacketError::TooLong { field: "Code" }));
    e.code = "ABC".into();
    e.note = "e".into(); // 1 byte, but NoteSize says 2
    assert_eq!(e.to_bytes(), Err(PacketError::BadSize { field: "Note" }));
}

#[test]
fn lookups_and_locations_from_other_values() {
    let f = Frame {
        sec_hdr_flag: true,
        frame_kind: 1,
        frame_gap: 3,
        frame_body: vec![0xab, 0xcd],
        frame_end: true,
        ..Default::default()
    };
    let b = f.to_bytes().unwrap();
    // Kind 1 means a 2-byte body; FrameEnd comes 3 bits after it.
    assert_eq!(&b[6..], &[0x01, 0x3a, 0xbc, 0xd1]);
    assert_eq!(Frame::decode(&b).unwrap(), f);
    let mut e = f.clone();
    e.frame_kind = 2; // a 4-byte body
    assert_eq!(
        e.to_bytes(),
        Err(PacketError::BadSize { field: "FrameBody" })
    );

    let t = Tune {
        mode: ModeArgType::SCIENCE,
        setting: vec![1, 2, 3],
        ..Default::default()
    };
    assert_eq!(Tune::decode(&t.to_bytes().unwrap()).unwrap(), t);
    let t = Tune {
        mode: ModeArgType::NOMINAL, // the default of 1 byte
        setting: vec![1, 2, 3],
        ..Default::default()
    };
    assert_eq!(t.to_bytes(), Err(PacketError::BadSize { field: "Setting" }));
}

#[test]
fn references_to_aggregate_members() {
    let r = Route {
        sec_hdr_flag: true,
        route_hdr: RouteHeaderType { kind: 2, count: 3 },
        stops: vec![1, 2, 3],
        detour: Some(9),
        ..Default::default()
    };
    let b = r.to_bytes().unwrap();
    assert_eq!(&b[6..], &[0x23, 1, 2, 3, 9]);
    assert_eq!(Route::decode(&b).unwrap(), r);
    // RouteHdr.Kind 2 includes the detour, and RouteHdr.Count counts the stops.
    let mut e = r.clone();
    e.detour = None;
    assert_eq!(e.to_bytes(), Err(PacketError::Missing { field: "Detour" }));
    e.route_hdr.kind = 1;
    assert_eq!(Route::decode(&e.to_bytes().unwrap()).unwrap(), e);
    e.route_hdr.count = 2;
    assert_eq!(e.to_bytes(), Err(PacketError::BadSize { field: "Stops" }));

    let p = Plan {
        leg: LegType {
            mode: ModeArgType::NOMINAL,
            count: 2,
        },
        points: vec![10, 20],
        ..Default::default()
    };
    assert_eq!(Plan::decode(&p.to_bytes().unwrap()).unwrap(), p);
}

#[test]
fn boolean_expressions() {
    // GateSpan is there when GateLo < GateHi, or both are zero.
    let mut g = Gate {
        sec_hdr_flag: true,
        gate_lo: 1,
        gate_hi: 2,
        gate_span: Some(0xabcd),
        ..Default::default()
    };
    assert_eq!(&g.to_bytes().unwrap()[6..], &[1, 2, 0xab, 0xcd]);
    assert_eq!(Gate::decode(&g.to_bytes().unwrap()).unwrap(), g);
    g.gate_lo = 0;
    g.gate_hi = 0;
    assert_eq!(Gate::decode(&g.to_bytes().unwrap()).unwrap(), g);
    g.gate_lo = 3;
    assert_eq!(g.to_bytes().unwrap().len(), 8);
    g.gate_span = None;
    assert_eq!(Gate::decode(&g.to_bytes().unwrap()).unwrap(), g);

    // Data is 2 bytes when Lo < Hi.
    let mut o = Open {
        lo: 1,
        hi: 2,
        data_: vec![0x12, 0x34],
        ..Default::default()
    };
    assert_eq!(Open::decode(&o.to_bytes().unwrap()).unwrap(), o);
    o.lo = 2;
    assert_eq!(o.to_bytes(), Err(PacketError::BadSize { field: "Data" }));
}
