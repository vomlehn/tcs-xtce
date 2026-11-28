//! Runs the demo mission's streams through the codec, as
//! `examples/stream_tool.rs` runs the generated code's, and checks that
//! they find the frames `tests/mission/streams.expected` gives.

use std::fs::File;
use std::path::PathBuf;

use tcs_xtce_dynamic::{Algorithm, Mission};

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

/// XORs each byte with 0x55, the demo's Descramble.
///
/// # Arguments
///
/// * `data` - The bytes.
/// * `out`  - Called with the bytes it decodes, in pieces.
fn descramble(data: &[u8], out: &mut dyn FnMut(&[u8])) {
    let mut buf = [0u8; 64];
    for chunk in data.chunks(buf.len()) {
        for (b, &x) in buf.iter_mut().zip(chunk) {
            *b = x ^ 0x55;
        }
        out(&buf[..chunk.len()]);
    }
}

/// Flips a bit, the demo's Invert.
///
/// # Arguments
///
/// * `bit` - The bit.
///
/// # Returns
///
/// The other bit.
fn invert(bit: u8) -> u8 {
    bit ^ 1
}

/// Writes bytes in hexadecimal.
///
/// # Arguments
///
/// * `b` - The bytes.
///
/// # Returns
///
/// The digits.
fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

#[test]
fn streams_find_the_frames_the_generated_code_finds() {
    let mut m =
        Mission::read_from([File::open(mission_file("demo.xtce")).unwrap()])
            .unwrap();
    // Not set yet: the streams that need them can't be made.
    let e = m.stream("/Demo/Scrambled").unwrap_err();
    assert!(e.to_string().contains("Descramble"), "{e}");
    m.set_algorithm("Descramble", Algorithm::stream_decode(descramble))
        .unwrap();
    m.set_algorithm("Invert", Algorithm::bit_invert(invert))
        .unwrap();
    let names = [
        "downlink",
        "uplink",
        "deep",
        "beacon",
        "relay",
        "scrambled",
        "clear",
        "nested",
    ];
    let mut streams: Vec<_> = names
        .iter()
        .map(|n| {
            let mut key = n.to_string();
            key[..1].make_ascii_uppercase();
            m.stream(&format!("/Demo/{key}")).unwrap()
        })
        .collect();
    let text = std::fs::read_to_string(mission_file("streams.txt")).unwrap();
    let mut got = String::new();
    for line in text.lines() {
        let line = line.split('#').next().unwrap_or("");
        let mut words = line.split_whitespace();
        let (Some(name), Some(h)) = (words.next(), words.next()) else {
            continue;
        };
        let data: Vec<u8> = (0..h.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&h[i..i + 2], 16).unwrap())
            .collect();
        let i = names.iter().position(|n| *n == name).unwrap();
        let mut frames = Vec::new();
        streams[i].push(&data, |f| frames.push(hex(f)));
        if frames.is_empty() {
            got += &format!("{name}: -\n");
        } else {
            got += &format!("{name}: {}\n", frames.join(" "));
        }
    }
    let want =
        std::fs::read_to_string(mission_file("streams.expected")).unwrap();
    assert_eq!(got, want);
    let dl = m.stream_container("/Demo/Downlink").unwrap().unwrap();
    assert_eq!(m.path_of_node(dl), "/Demo/DlFrame");
    assert_eq!(m.stream_container("/Demo/Scrambled").unwrap(), None);
}
