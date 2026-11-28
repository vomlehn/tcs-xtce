//! Decodes and re-encodes the demo mission's packet vectors with the
//! interpreter, which must describe each packet and give its bytes as the
//! generated codecs do (`tests/mission/vectors.expected`), for each packet
//! whose constructs it runs so far.

mod common;

use std::fs::File;
use std::path::PathBuf;

use tcs_xtce::buffer::Buffer;
use tcs_xtce_dynamic::interp::{self, Decoded, Plan, Workspace};
use tcs_xtce_dynamic::Mission;

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
        let want = want
            .split(" | alarms:")
            .next()
            .unwrap()
            .split(" | derived:")
            .next()
            .unwrap();
        let got = line(got, plan, &mut ws);
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
