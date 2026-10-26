//! Finds the frames of the demo mission's streams, as
//! `tests/stream_check.sh` asks: each line of the file it is given is a
//! stream's name and the next bytes of that stream in hex, and it prints
//! `<stream>:` then the frames those bytes complete, in hex, or ` -`.  Each
//! stream keeps its state from line to line.  The C++ and Python versions of
//! this tool print identical output.
//!
//! Usage: `stream_tool STREAMS`

#[path = "../tests/mission/demo.rs"]
mod demo;

/// The demo's Descramble: each byte XOR 0x55.
fn descramble(data: &[u8], out: &mut dyn FnMut(&[u8])) {
    let mut buf = [0u8; 64];
    for chunk in data.chunks(buf.len()) {
        for (b, &x) in buf.iter_mut().zip(chunk) {
            *b = x ^ 0x55;
        }
        out(&buf[..chunk.len()]);
    }
}

/// The demo's Invert: the bit's complement, as AutoInvert does without it.
fn invert(bit: u8) -> u8 {
    bit ^ 1
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

fn main() {
    let path = std::env::args().nth(1).expect("usage: stream_tool STREAMS");
    let text = std::fs::read_to_string(path).expect("cannot read streams");
    let mut downlink = demo::DownlinkStream::new();
    let mut uplink = demo::UplinkStream::new();
    let mut deep = demo::DeepStream::new();
    let mut beacon = demo::BeaconStream::new();
    let mut relay = demo::RelayStream::new();
    let mut scrambled = demo::ScrambledStream::new(descramble);
    let mut clear = demo::ClearStream::new(descramble, invert);
    let mut nested = demo::NestedStream::new(descramble, invert);
    for line in text.lines() {
        let line = line.split('#').next().unwrap_or("");
        let mut words = line.split_whitespace();
        let (Some(name), Some(h)) = (words.next(), words.next()) else {
            continue;
        };
        let data: Vec<u8> = (0..h.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&h[i..i + 2], 16).expect("bad hex"))
            .collect();
        let mut frames = Vec::new();
        let mut take = |f: &[u8]| frames.push(hex(f));
        match name {
            "downlink" => downlink.push(&data, &mut take),
            "uplink" => uplink.push(&data, &mut take),
            "deep" => deep.push(&data, &mut take),
            "beacon" => beacon.push(&data, &mut take),
            "relay" => relay.push(&data, &mut take),
            "scrambled" => scrambled.push(&data, &mut take),
            "clear" => clear.push(&data, &mut take),
            "nested" => nested.push(&data, &mut take),
            _ => panic!("unknown stream {name}"),
        }
        if frames.is_empty() {
            println!("{name}: -");
        } else {
            println!("{name}: {}", frames.join(" "));
        }
    }
}
