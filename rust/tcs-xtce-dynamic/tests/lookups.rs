//! Reads the demo mission with the run-time codec and looks its names
//! up, by the rules XTCE documents use for their own references.

use std::fs::File;
use std::path::PathBuf;

use tcs_xtce_dynamic::{Error, Mission};

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

#[test]
fn space_systems_are_found_from_any_base() {
    let m = demo();
    let top = m.top();
    assert_eq!(m.path_of_space_system(top), "/");
    let demo = m.space_system(top, "/Demo").unwrap();
    assert_eq!(m.path_of_space_system(demo), "/Demo");
    // From the top, a relative path means what it means as an absolute
    // one.
    assert_eq!(m.space_system(top, "Demo").unwrap(), demo);
    let power = m.space_system(demo, "Power").unwrap();
    assert_eq!(m.path_of_space_system(power), "/Demo/Power");
    assert_eq!(m.space_system(power, "..").unwrap(), demo);
    assert_eq!(m.space_system(top, "/").unwrap(), top);
}

#[test]
fn containers_and_meta_commands_are_found() {
    let m = demo();
    let demo = m.space_system(m.top(), "Demo").unwrap();
    let power = m.space_system(demo, "Power").unwrap();
    let block = m.container(power, "PowerBlock").unwrap();
    assert_eq!(m.path_of_node(block), "/Demo/Power/PowerBlock");
    assert!(!m.is_abstract(block));
    assert!(m.max_bytes(block).unwrap() > 0);
    let set = m.meta_command(power, "SetMode").unwrap();
    assert_eq!(m.path_of_node(set), "/Demo/SetMode");
    let base = m.meta_command(demo, "CCSDSCommand").unwrap();
    assert!(m.is_abstract(base));
    assert_eq!(m.max_bytes(base), None);
    // A meta-command isn't a container.
    let e = m.container(demo, "SetMode").unwrap_err();
    assert_eq!(e.tried, ["/Demo/SetMode"]);
}

#[test]
fn parameters_members_and_elements_are_slots() {
    let m = demo();
    let demo = m.space_system(m.top(), "Demo").unwrap();
    let power = m.space_system(demo, "Power").unwrap();
    let v = m.parameter(power, "BatteryVoltage").unwrap();
    assert_eq!(m.path_of_slot(v), "/Demo/Power/BatteryVoltage");
    // An unqualified name is looked for in the base, then in each
    // SpaceSystem that encloses it.
    let g = m.parameter(power, "Probes[1].Gains[0]").unwrap();
    assert_eq!(m.path_of_slot(g), "/Demo/Probes[1].Gains[0]");
    assert_eq!(m.slot_type(g).kind(), "int");
    let e = m.parameter(demo, "Probes[1]").unwrap_err();
    assert!(e.to_string().contains("aggregate or array"), "{e}");
    let e = m.parameter(demo, "Probes[2].Id").unwrap_err();
    assert!(e.to_string().contains("no member or element"), "{e}");
}

#[test]
fn arguments_and_entries_are_slots() {
    let m = demo();
    let demo = m.space_system(m.top(), "Demo").unwrap();
    let set = m.meta_command(demo, "SetMode").unwrap();
    let mode = m.argument(set, "Mode").unwrap();
    assert_eq!(m.path_of_slot(mode), "/Demo/SetMode#Mode");
    // Its base's arguments are its own too.
    let apid = m.argument(set, "APID").unwrap();
    assert_eq!(m.path_of_slot(apid), "/Demo/CCSDSCommand#APID");
    assert_eq!(m.entry(set, "Mode").unwrap(), mode);
    let power = m.space_system(demo, "Power").unwrap();
    let block = m.container(power, "PowerBlock").unwrap();
    let v = m.entry(block, "BatteryVoltage").unwrap();
    assert_eq!(v, m.parameter(power, "BatteryVoltage").unwrap());
    assert!(m.entry(block, "Mode").is_err());
    assert!(m.argument(block, "Mode").is_err());
}

#[test]
fn a_lookup_that_fails_says_where_it_looked() {
    let m = demo();
    let demo = m.space_system(m.top(), "Demo").unwrap();
    let power = m.space_system(demo, "Power").unwrap();
    let e = m.parameter(power, "Nothing").unwrap_err();
    assert_eq!(e.tried, ["/Demo/Power/Nothing", "/Demo/Nothing"]);
    assert_eq!(
        e.to_string(),
        "no parameter Nothing: looked for /Demo/Power/Nothing, /Demo/Nothing"
    );
    let e = m.container(power, "../Nothing").unwrap_err();
    assert_eq!(e.tried, ["/Demo/Nothing"]);
}

#[test]
fn handles_belong_to_their_mission() {
    let a = demo();
    let b = demo();
    let e = b.space_system(a.top(), "Demo").unwrap_err();
    assert!(e.to_string().contains("another mission"), "{e}");
}

#[test]
fn a_document_that_is_not_xtce_is_refused() {
    let e = Mission::read_from([&b"<NotXtce/>"[..]]).unwrap_err();
    assert!(matches!(e, Error::Read { document: 0, .. }), "{e:?}");
}

#[test]
fn whole_values_are_runs_of_slots() {
    let m = demo();
    let demo = m.space_system(m.top(), "Demo").unwrap();
    let all = m.parameter_slots(demo, "Probes").unwrap();
    let paths: Vec<&str> = all.iter().map(|s| m.path_of_slot(s)).collect();
    assert_eq!(
        paths,
        [
            "/Demo/Probes[0].Id",
            "/Demo/Probes[0].Gains[0]",
            "/Demo/Probes[0].Gains[1]",
            "/Demo/Probes[1].Id",
            "/Demo/Probes[1].Gains[0]",
            "/Demo/Probes[1].Gains[1]",
        ]
    );
    let one = m.parameter_slots(demo, "Probes[1]").unwrap();
    assert_eq!(one.len(), 3);
    assert_eq!(one.get(0), Some(m.parameter(demo, "Probes[1].Id").unwrap()));
    let gains = m.parameter_slots(demo, "Probes[1].Gains").unwrap();
    assert_eq!(gains.len(), 2);
    // A scalar is a run of one.
    let id = m.parameter_slots(demo, "Probes[1].Id").unwrap();
    assert_eq!(id.len(), 1);
    let e = m.parameter_slots(demo, "Probes[2]").unwrap_err();
    assert!(e.to_string().contains("no member or element"), "{e}");
    let probe = m.container(demo, "ProbeOne").unwrap();
    assert_eq!(m.entry_slots(probe, "Probes").unwrap(), all);
}

#[test]
fn slots_of_no_maximum_size_are_listed() {
    let m = demo();
    let left: Vec<&str> =
        m.unbounded().iter().map(|&s| m.path_of_slot(s)).collect();
    // Binary data that takes the rest of the packet.
    assert!(left.contains(&"/Demo/Rest"), "{left:?}");
    // A string or binary value, each with the room the maximum field
    // size gives.
    for &s in m.unbounded() {
        let k = m.slot_type(s).kind().to_string();
        assert!(k == "string" || k == "binary", "{k}");
    }
}
