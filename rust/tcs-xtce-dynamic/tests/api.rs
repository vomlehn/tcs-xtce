//! Packs and unpacks the demo mission's packets through the codec's
//! interface for programs: slots, a store of current values, and a
//! scratch that unpacking fills and committing copies.

use std::fs::File;
use std::path::PathBuf;

use tcs_xtce::buffer::Buffer;
use tcs_xtce_dynamic::{Datum, Mission, RunError, SetError};

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
fn what_is_not_run_yet_is_listed_and_refused() {
    let m = demo();
    let left = m.unsupported();
    assert!(left.iter().any(|w| w.contains("transform")), "{left:?}");
    let abstract_ = m.container(m.top(), "/Demo/CCSDSPacket").unwrap();
    let mut out = Buffer::with_capacity(64);
    let e = m.pack(abstract_, &m.store(), &mut out).unwrap_err();
    assert!(matches!(e, RunError::Unsupported(_)), "{e:?}");
}
