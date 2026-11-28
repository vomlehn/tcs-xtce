//! Decodes and re-encodes the demo mission's packet vectors with the
//! interpreter, which must describe each packet and give its bytes as the
//! generated codecs do (`tests/mission/vectors.expected`), for each packet
//! whose constructs it runs so far.

mod common;

use std::fs::File;
use std::path::PathBuf;

use tcs_xtce::buffer::Buffer;
use tcs_xtce_dynamic::interp::{self, Decoded, Plan, Workspace};
use tcs_xtce_dynamic::{AlarmLevel, Mission};

/// Gives a file of the demo mission.
///
/// # Arguments
///
/// * `name` - Its name.
///
/// # Returns
///
/// Its path.
fn mission_file(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/mission")
        .join(name)
}

/// Writes bytes as hexadecimal.
///
/// # Arguments
///
/// * `b` - The bytes.
///
/// # Returns
///
/// Two lower-case digits for each byte.
fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

/// Gives the line the generated codecs' tool prints for a packet, without
/// its alarms and derived values.
///
/// # Arguments
///
/// * `d`    - What decoding gave.
/// * `plan` - The mission's plan.
/// * `ws`   - The workspace it decoded into.
///
/// # Returns
///
/// `packet | re-encoded`, with `error:<kind>` in place of either.
fn line(
    d: interp::CodecResult<Decoded>,
    plan: &Plan,
    ws: &mut Workspace,
) -> String {
    let d = match d {
        Ok(d) => d,
        Err(e) => return format!("error:{} | -", e.kind()),
    };
    let text = interp::describe_packet(&d, ws);
    let mut out = Buffer::with_capacity(4096);
    let again = match interp::encode(&d.node, plan, ws, &mut out) {
        Ok(b) => hex(b),
        Err(e) => format!("error:{}", e.kind()),
    };
    format!("{text} | {again}")
}

/// Describes the alarms and derived values of the packet last unpacked,
/// as `examples/packet_tool.rs` does.
///
/// # Arguments
///
/// * `m`       - The mission.
/// * `scratch` - What unpacking gave.
/// * `mon`     - The alarm monitor kept across the vectors.
/// * `t`       - The packet's time.
///
/// # Returns
///
/// ` | alarms: ...`, then ` | monitor: ...` when the monitor's levels
/// differ, then ` | derived: ...`, each left out when there are none.
fn alarm_text(
    m: &Mission,
    scratch: &tcs_xtce_dynamic::Scratch,
    mon: &mut tcs_xtce_dynamic::Monitor,
    t: f64,
) -> String {
    let levels = |out: &mut String, n: &str, l: Option<AlarmLevel>| {
        if !out.is_empty() {
            out.push_str(", ");
        }
        match l {
            Some(l) => out.push_str(&format!("{n}={l}")),
            None => out.push_str(&format!("{n}=none")),
        }
    };
    let (mut a, mut mo, mut d) = (String::new(), String::new(), String::new());
    m.alarms(scratch, |n, l| levels(&mut a, n, l)).unwrap();
    m.monitor_alarms(scratch, mon, t, |n, l| levels(&mut mo, n, l))
        .unwrap();
    m.derived(scratch, |n, v| {
        if !d.is_empty() {
            d.push_str(", ");
        }
        d.push_str(n);
        d.push('=');
        match v {
            Some(v) => tcs_xtce::packet::describe_f64(v, &mut d).unwrap(),
            None => d.push_str("none"),
        }
    })
    .unwrap();
    let mut s = String::new();
    if !a.is_empty() {
        s += &format!(" | alarms: {a}");
        if a != mo {
            s += &format!(" | monitor: {mo}");
        }
    }
    if !d.is_empty() {
        s += &format!(" | derived: {d}");
    }
    s
}

#[test]
fn vectors_decode_and_encode_as_the_generated_codecs_do() {
    let mut m =
        Mission::read_from([File::open(mission_file("demo.xtce")).unwrap()])
            .unwrap();
    common::set_algorithms(&mut m);
    let model = m.model();
    let plan = m.plan();
    let mut ws = Workspace::new(plan);
    let root = |key: &str| {
        model
            .roots
            .iter()
            .find(|r| r.get().key == key)
            .unwrap_or_else(|| panic!("no tree {key}"))
            .clone()
    };
    let tm = root("/Demo/CCSDSPacket");
    let tc = root("/Demo/CCSDSCommand");
    let hk = model
        .nodes
        .iter()
        .find(|n| n.get().key == "/Demo/Housekeeping")
        .unwrap()
        .clone();
    let vectors = std::fs::read_to_string(mission_file("vectors.txt")).unwrap();
    let expected =
        std::fs::read_to_string(mission_file("vectors.expected")).unwrap();
    let (mut matched, mut skipped, mut wrong) = (0, 0, Vec::new());
    let top = m.top();
    let handles = [
        m.container(top, "/Demo/CCSDSPacket").unwrap(),
        m.meta_command(top, "/Demo/CCSDSCommand").unwrap(),
        m.container(top, "/Demo/Housekeeping").unwrap(),
    ];
    let mut scratch = m.scratch();
    let mut mon = m.monitor();
    let mut index = 0u32;
    for (v, want) in vectors.lines().zip(expected.lines()) {
        let v = v.split('#').next().unwrap_or("");
        let mut words = v.split_whitespace();
        let (Some(kind), Some(h)) = (words.next(), words.next()) else {
            continue;
        };
        let data: Vec<u8> = (0..h.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&h[i..i + 2], 16).unwrap())
            .collect();
        // The alarms, through the interface for programs, each packet at
        // the time of its index.
        let t = f64::from(index);
        index += 1;
        let node = match kind {
            "tm" => handles[0],
            "tc" => handles[1],
            _ => handles[2],
        };
        let extra = match m.unpack(node, &data, &mut scratch) {
            Ok(_) => alarm_text(&m, &scratch, &mut mon, t),
            Err(_) => String::new(),
        };
        let got = match kind {
            "tm" => interp::classify(&tm, &data, plan, &mut ws),
            "tc" => interp::classify(&tc, &data, plan, &mut ws),
            _ => match plan.unsupported(&hk) {
                Some(w) => Err(w.to_string()),
                None => Ok(interp::decode(&hk, &data, plan, &mut ws)),
            },
        };
        let got = match got {
            Ok(g) => g,
            Err(w) => {
                skipped += 1;
                if std::env::var_os("VECTORS_WHY").is_some() {
                    eprintln!("skipped: {w}");
                }
                continue;
            }
        };
        let got = line(got, plan, &mut ws) + &extra;
        if got == want {
            matched += 1;
        } else {
            wrong.push(format!("{v}\n  want {want}\n  got  {got}"));
        }
    }
    eprintln!("vectors: {matched} match, {skipped} use what isn't run yet");
    assert!(
        wrong.is_empty(),
        "{} differ:\n{}",
        wrong.len(),
        wrong.join("\n")
    );
    assert!(matched > 0, "no vector was run");
}
