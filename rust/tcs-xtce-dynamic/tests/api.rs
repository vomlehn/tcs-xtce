//! Packs and unpacks the demo mission's packets through the codec's
//! interface for programs: slots, a store of current values, and a
//! scratch that unpacking fills and committing copies.

use std::fs::File;
use std::path::PathBuf;

use tcs_xtce::buffer::Buffer;
use tcs_xtce_dynamic::{Algorithm, Datum, Mission, RunError, SetError};

mod common;

/// Reads the demo mission.
///
/// # Returns
///
/// The mission.
fn demo() -> Mission {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/mission/demo.xtce");
    Mission::read_from([File::open(path).unwrap()]).unwrap()
}

/// Reads hexadecimal.
///
/// # Arguments
///
/// * `h` - The digits.
///
/// # Returns
///
/// The bytes.
fn bytes(h: &str) -> Vec<u8> {
    (0..h.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&h[i..i + 2], 16).unwrap())
        .collect()
}

/// The first housekeeping vector, and the bytes the generated codecs
/// encode it again to.
const HK: &str = "0864c02a001201a04e2805dc040302013fc00000fffe8050fc";

#[test]
fn a_packet_unpacks_into_the_store_and_packs_again() {
    let m = demo();
    let top = m.top();
    let root = m.container(top, "/Demo/CCSDSPacket").unwrap();
    let mut scratch = m.scratch();
    let hk = m.unpack(root, &bytes(HK), &mut scratch).unwrap();
    assert_eq!(m.path_of_node(hk), "/Demo/Housekeeping");
    let mut store = m.store();
    let seq = m.parameter(top, "/Demo/SeqCount").unwrap();
    assert_eq!(store.get(seq), None);
    store.commit(&scratch).unwrap();
    assert_eq!(store.get(seq), Some(Datum::Int(42)));
    let mode = m.parameter(top, "/Demo/Mode").unwrap();
    assert_eq!(store.get(mode), Some(Datum::Enum(1)));
    let mut out = Buffer::with_capacity(256);
    let packed = m.pack(hk, &store, &mut out).unwrap();
    assert_eq!(packed, &bytes(HK)[..]);
}

#[test]
fn values_set_are_packed() {
    let m = demo();
    let top = m.top();
    let root = m.container(top, "/Demo/CCSDSPacket").unwrap();
    let mut scratch = m.scratch();
    let hk = m.unpack(root, &bytes(HK), &mut scratch).unwrap();
    let mut store = m.store();
    store.commit(&scratch).unwrap();
    let mode = m.parameter(top, "/Demo/Mode").unwrap();
    // Safe, which keeps it a Housekeeping packet: Science would make it
    // a ScienceHousekeeping one.
    store.set(mode, Datum::Enum(0)).unwrap();
    let seq = m.parameter(top, "/Demo/SeqCount").unwrap();
    store.set(seq, Datum::Int(7)).unwrap();
    let mut out = Buffer::with_capacity(256);
    let packed = m.pack(hk, &store, &mut out).unwrap().to_vec();
    let again = m.unpack(root, &packed, &mut scratch).unwrap();
    let mut other = m.store();
    other.commit(&scratch).unwrap();
    assert_eq!(m.path_of_node(again), m.path_of_node(hk));
    assert_eq!(other.get(mode), Some(Datum::Enum(0)));
    assert_eq!(other.get(seq), Some(Datum::Int(7)));
}

#[test]
fn a_packet_that_fails_sets_nothing() {
    let m = demo();
    let top = m.top();
    let root = m.container(top, "/Demo/CCSDSPacket").unwrap();
    let mut scratch = m.scratch();
    let mut store = m.store();
    let truncated = &bytes(HK)[..12];
    let e = m.unpack(root, truncated, &mut scratch).unwrap_err();
    assert!(matches!(e, RunError::Packet(_)), "{e:?}");
    let seq = m.parameter(top, "/Demo/SeqCount").unwrap();
    assert_eq!(store.get(seq), None);
    store.commit(&m.scratch()).unwrap();
    assert_eq!(store.get(seq), None);
}

#[test]
fn values_are_checked_when_set() {
    let m = demo();
    let top = m.top();
    let mut store = m.store();
    let mode = m.parameter(top, "/Demo/Mode").unwrap();
    assert!(matches!(
        store.set(mode, Datum::Int(1)),
        Err(SetError::Kind { .. })
    ));
    assert_eq!(store.set(mode, Datum::Enum(99)), Err(SetError::OutOfRange));
    let other = demo();
    let foreign = other.parameter(other.top(), "/Demo/Mode").unwrap();
    assert_eq!(store.set(foreign, Datum::Enum(1)), Err(SetError::Foreign));
}

#[test]
fn a_command_unpacks_into_its_arguments() {
    let m = demo();
    let top = m.top();
    let root = m.meta_command(top, "/Demo/CCSDSCommand").unwrap();
    let mut scratch = m.scratch();
    let cmd = m
        .unpack(root, &bytes("112cc00500010102"), &mut scratch)
        .unwrap();
    assert_eq!(m.path_of_node(cmd), "/Demo/SetMode");
    let mut store = m.store();
    store.commit(&scratch).unwrap();
    let mode = m.argument(cmd, "Mode").unwrap();
    assert_eq!(store.get(mode), Some(Datum::Enum(2)));
    let mut out = Buffer::with_capacity(64);
    let packed = m.pack(cmd, &store, &mut out).unwrap();
    assert_eq!(packed, &bytes("112cc00500010102")[..]);
}

#[test]
fn abstract_nodes_are_refused_for_packing() {
    let m = demo();
    assert_eq!(m.unsupported(), Vec::<String>::new());
    let abstract_ = m.container(m.top(), "/Demo/CCSDSPacket").unwrap();
    let mut out = Buffer::with_capacity(64);
    let e = m.pack(abstract_, &m.store(), &mut out).unwrap_err();
    assert!(matches!(e, RunError::Unsupported(_)), "{e:?}");
}

/// The vector of a packet with a transform: XorBlock, its data masked.
const XOR: &str = "0a62c02a0004f0e1968777";

#[test]
fn algorithms_are_listed_and_needed_until_supplied() {
    let mut m = demo();
    let left: Vec<_> = m
        .algorithms()
        .into_iter()
        .filter(|a| !a.2)
        .map(|a| a.0.to_string())
        .collect();
    assert!(left.iter().any(|a| a == "Unmask"), "{left:?}");
    let root = m.container(m.top(), "/Demo/CCSDSPacket").unwrap();
    let mut scratch = m.scratch();
    let e = m.unpack(root, &bytes(XOR), &mut scratch).unwrap_err();
    let RunError::Packet(p) = &e else {
        panic!("{e:?}")
    };
    assert_eq!(p.kind(), "NoAlgorithm", "{e:?}");
    // Of another kind, or not of the mission.
    assert!(m
        .set_algorithm("Unmask", Algorithm::Criterion(|_| true))
        .is_err());
    assert!(m
        .set_algorithm("/Demo/Nothing", Algorithm::Criterion(|_| true))
        .is_err());
    common::set_algorithms(&mut m);
    assert!(m.algorithms().iter().all(|a| a.2));
    let got = m.unpack(root, &bytes(XOR), &mut scratch).unwrap();
    let mut store = m.store();
    store.commit(&scratch).unwrap();
    let mut out = Buffer::with_capacity(256);
    assert_eq!(m.pack(got, &store, &mut out).unwrap(), &bytes(XOR)[..]);
}

#[test]
fn raw_values_are_read_and_packed_as_they_are() {
    let m = demo();
    let top = m.top();
    let root = m.container(top, "/Demo/CCSDSPacket").unwrap();
    let mut scratch = m.scratch();
    let hk = m.unpack(root, &bytes(HK), &mut scratch).unwrap();
    let mut store = m.store();
    store.commit(&scratch).unwrap();
    // An 8-bit raw value, calibrated by a spline to degrees C.
    let temp = m.parameter(top, "/Demo/Temperature").unwrap();
    assert!(matches!(store.get(temp), Some(Datum::Float(_))));
    let Some(Datum::Int(raw)) = store.get_raw(temp) else {
        panic!("no raw value: {:?}", store.get_raw(temp));
    };
    // Packing the raw value as it is gives the packet back.
    store.set_raw(temp, Datum::Int(raw)).unwrap();
    assert_eq!(store.get(temp), None);
    let mut out = Buffer::with_capacity(256);
    assert_eq!(m.pack(hk, &store, &mut out).unwrap(), &bytes(HK)[..]);
    // Any value the encoding holds can be sent, whatever it calibrates
    // to; one it doesn't hold can't.
    store.set_raw(temp, Datum::Int(255)).unwrap();
    let packed = m.pack(hk, &store, &mut out).unwrap().to_vec();
    m.unpack(root, &packed, &mut scratch).unwrap();
    let mut other = m.store();
    other.commit(&scratch).unwrap();
    assert_eq!(other.get_raw(temp), Some(Datum::Int(255)));
    store.set_raw(temp, Datum::Int(256)).unwrap();
    let e = m.pack(hk, &store, &mut out).unwrap_err();
    assert!(matches!(e, RunError::Packet(_)), "{e:?}");
    assert!(matches!(
        store.set_raw(temp, Datum::Float(1.0)),
        Err(SetError::Kind { .. })
    ));
}

#[test]
fn the_digest_is_xtce_digest_s() {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/mission/demo.xtce");
    let want = tcs_xtce::digest::digest_file_bytes(&path).unwrap();
    assert_eq!(demo().digest(), want);
}

#[test]
fn a_whole_value_is_read_slot_by_slot() {
    let m = demo();
    let root = m.container(m.top(), "/Demo/CCSDSPacket").unwrap();
    let mut scratch = m.scratch();
    m.unpack(root, &bytes("095ec02a0005123520abcd77"), &mut scratch)
        .unwrap();
    let mut store = m.store();
    store.commit(&scratch).unwrap();
    let probes = m.parameter_slots(m.top(), "/Demo/Probes").unwrap();
    let mut got = Vec::new();
    store.each(probes, |s, v| {
        got.push((m.path_of_slot(s).to_string(), v.map(|d| format!("{d:?}"))))
    });
    let want = [
        ("/Demo/Probes[0].Id", "Int(1)"),
        ("/Demo/Probes[0].Gains[0]", "Int(2)"),
        ("/Demo/Probes[0].Gains[1]", "Int(3)"),
        ("/Demo/Probes[1].Id", "Int(5)"),
        ("/Demo/Probes[1].Gains[0]", "Int(2)"),
        ("/Demo/Probes[1].Gains[1]", "Int(0)"),
    ];
    let want: Vec<_> = want
        .iter()
        .map(|(p, v)| (p.to_string(), Some(v.to_string())))
        .collect();
    assert_eq!(got, want);
}

/// A mission whose one packet is a signed integer of 1000 bits, then a
/// byte.
const HUGE: &str = r#"<?xml version="1.0" encoding="UTF-8"?>
<SpaceSystem xmlns="http://www.omg.org/spec/XTCE/20250214" name="Huge">
  <TelemetryMetaData>
    <ParameterTypeSet>
      <IntegerParameterType name="BigType" signed="true" sizeInBits="1000">
        <IntegerDataEncoding sizeInBits="1000" encoding="twosComplement"/>
      </IntegerParameterType>
      <IntegerParameterType name="U8" signed="false">
        <IntegerDataEncoding sizeInBits="8"/>
      </IntegerParameterType>
    </ParameterTypeSet>
    <ParameterSet>
      <Parameter name="Big" parameterTypeRef="BigType"/>
      <Parameter name="Tail" parameterTypeRef="U8"/>
    </ParameterSet>
    <ContainerSet>
      <SequenceContainer name="Packet">
        <EntryList>
          <ParameterRefEntry parameterRef="Big"/>
          <ParameterRefEntry parameterRef="Tail"/>
        </EntryList>
      </SequenceContainer>
    </ContainerSet>
  </TelemetryMetaData>
</SpaceSystem>
"#;

#[test]
fn integers_of_more_than_512_bits_unpack_and_pack() {
    let m = Mission::read_from([HUGE.as_bytes()]).unwrap();
    assert_eq!(m.unsupported(), Vec::<String>::new());
    let top = m.top();
    let packet = m.container(top, "/Huge/Packet").unwrap();
    // -2, then 7: 125 bytes of all ones but the last bit, then the byte.
    let mut data = vec![0xffu8; 125];
    data[124] = 0xfe;
    data.push(7);
    let mut scratch = m.scratch();
    m.unpack(packet, &data, &mut scratch).unwrap();
    let mut store = m.store();
    store.commit(&scratch).unwrap();
    let big = m.parameter(top, "/Huge/Big").unwrap();
    assert_eq!(store.get(big), Some(Datum::Int(-2)));
    let mut out = Buffer::with_capacity(256);
    assert_eq!(m.pack(packet, &store, &mut out).unwrap(), &data[..]);
    // 2^998 fits 1000 signed bits; 2^999 doesn't.
    let one = num_bigint::BigInt::from(1);
    let fits = tcs_xtce_dynamic::from_big(&(&one << 998)).unwrap();
    store.set(big, Datum::Big(&fits)).unwrap();
    let packed = m.pack(packet, &store, &mut out).unwrap().to_vec();
    m.unpack(packet, &packed, &mut scratch).unwrap();
    let mut other = m.store();
    other.commit(&scratch).unwrap();
    let Some(Datum::Big(got)) = other.get(big) else {
        panic!("{:?}", other.get(big))
    };
    assert_eq!(tcs_xtce_dynamic::to_big(got), &one << 998);
    let over = tcs_xtce_dynamic::from_big(&(&one << 999)).unwrap();
    store.set(big, Datum::Big(&over)).unwrap();
    assert!(m.pack(packet, &store, &mut out).is_err());
}

#[test]
fn times_are_times() {
    let m = demo();
    let top = m.top();
    let slot = |p: &str| m.parameter(top, p).unwrap();
    let (milli, days, iso) = (
        slot("/Demo/MilliTime"),
        slot("/Demo/Days"),
        slot("/Demo/IsoTime"),
    );
    assert_eq!(m.slot_type(milli).time(), Some("absolute"));
    assert_eq!(m.slot_type(days).time(), Some("relative"));
    // A time encoded as text keeps its text.
    assert_eq!(m.slot_type(iso).time(), None);
    assert_eq!(m.slot_type(slot("/Demo/Mode")).time(), None);
    let root = m.container(top, "/Demo/CCSDSPacket").unwrap();
    let mut scratch = m.scratch();
    let times = "099ac02a002a0012d68700030200324282309ce5400000323032362d31\
                 302d32355431323a30303a30305a0102030405ff";
    let got = m.unpack(root, &bytes(times), &mut scratch).unwrap();
    let mut store = m.store();
    store.commit(&scratch).unwrap();
    assert_eq!(store.get(milli), Some(Datum::Time(1234.567)));
    assert_eq!(store.get(days), Some(Datum::Time(259200.0)));
    assert_eq!(store.get(iso), Some(Datum::Str("2026-10-25T12:00:00Z")));
    store.set(days, Datum::Time(86400.0)).unwrap();
    let mut out = Buffer::with_capacity(256);
    let packed = m.pack(got, &store, &mut out).unwrap().to_vec();
    m.unpack(root, &packed, &mut scratch).unwrap();
    let mut other = m.store();
    other.commit(&scratch).unwrap();
    assert_eq!(other.get(days), Some(Datum::Time(86400.0)));
}
