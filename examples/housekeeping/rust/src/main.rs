//! Encodes housekeeping `Status` packets described by `housekeeping.xtce`
//! into one stream, then walks the stream and decodes them again, using the
//! code `tcs-xtce` generated into `housekeeping.rs`.
//!
//! A `Status` packet shows three things the generated code does:
//!
//! * `PacketLength` holds the number of bytes after it. Encoding computes
//!   it, and decoding checks it, so packets stored one after another can be
//!   split apart.
//! * `Temperature`, `BusVoltage` and `HeaterPower` are calibrated: the
//!   structure holds degrees, volts and watts, and the packet holds raw
//!   counts.
//! * `HeaterPower` is in the packet only when the heater is on.
//!
//! The stream starts with the digest of `housekeeping.xtce`, so that whatever
//! reads it can check that it uses the same definition of a `Status`.  The
//! C/C++ and Python versions of this program print identical output.  Run it
//! in the directory that holds `housekeeping.xtce`.

mod housekeeping;

use std::process;

use housekeeping::Status;
use tcs_xtce::digest::{digest_file_bytes, hex as digest_hex};
use tcs_xtce::packet::Codec;

/// The XTCE file that describes a `Status`, and the size of its digest.
const XTCE: &str = "housekeeping.xtce";
const DIGEST_BYTES: usize = 16;

/// A `Status` to encode, and the values decoding it must give: the nearest
/// the calibrators can represent.
struct Reading {
    status: Status,
    temperature: f64,
    bus_voltage: f64,
    heater_power: Option<f64>,
}

/// Builds the statuses the example encodes.
///
/// # Returns
///
/// Each status, with the values decoding it must give.
fn readings() -> [Reading; 4] {
    let status = |heater_on, temperature, bus_voltage, heater_power| Status {
        heater_on,
        temperature,
        bus_voltage,
        heater_power,
    };
    [
        Reading {
            status: status(false, 21.53, 28.3, None),
            temperature: 21.5,
            bus_voltage: 28.25,
            heater_power: None,
        },
        Reading {
            status: status(true, -12.25, 27.5, Some(4.5)),
            temperature: -12.25,
            bus_voltage: 27.5,
            heater_power: Some(4.5),
        },
        Reading {
            status: status(true, -20.01, 26.9, Some(12.74)),
            temperature: -20.0,
            bus_voltage: 26.875,
            heater_power: Some(12.734375),
        },
        Reading {
            status: status(false, 5.0, 28.0, None),
            temperature: 5.0,
            bus_voltage: 28.0,
            heater_power: None,
        },
    ]
}

fn fail(msg: &str) -> ! {
    eprintln!("housekeeping: {msg}");
    process::exit(1);
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

/// Encodes a `Status` that must fail, and prints why it did; exits with
/// status 1 if it succeeds.
///
/// # Arguments
///
/// * `codec`  - The codec to encode with.
/// * `status` - The status, which has a value encoding must reject.
fn try_encode(codec: &mut Codec, status: &Status) {
    match codec.encode(status) {
        Ok(bytes) => fail(&format!("encoding {status} gave {}", hex(bytes))),
        Err(e) => println!("encode {status} -> error:{} ({e})", e.kind()),
    }
}

/// Decodes bytes that must fail, and prints why they did; exits with
/// status 1 if they decode.
///
/// # Arguments
///
/// * `codec` - The codec to decode with.
/// * `data`  - The bytes.
/// * `why`   - What is wrong with them, for the output.
fn try_decode(codec: &mut Codec, data: &[u8], why: &str) {
    codec.load(data);
    match codec.decode::<Status>() {
        Ok(status) => fail(&format!("decoding {} gave {status}", hex(data))),
        Err(e) => {
            println!("decode {} ({why}) -> error:{} ({e})", hex(data), e.kind())
        }
    }
}

/// Encodes the readings into a stream after the XTCE digest, decodes them
/// from it and checks they decode and re-encode as they must.
fn main() {
    // The codec owns an input and an output buffer, both preallocated to
    // Status::MAX_BYTES (7) and reused for every packet.
    let mut codec = Codec::for_packet::<Status>();
    let readings = readings();
    let digest = digest_file_bytes(XTCE)
        .unwrap_or_else(|e| fail(&format!("{XTCE}: {e}")));

    // The stream starts with the digest of the XTCE file.  Then encode each
    // reading and append it, as a recorder would store packets: one after
    // another, with nothing between them.
    let mut stream = digest.to_vec();
    for r in &readings {
        let bytes = codec
            .encode(&r.status)
            .unwrap_or_else(|e| fail(&format!("encoding {}: {e}", r.status)));
        println!("encode {} -> {}", r.status, hex(bytes));
        stream.extend_from_slice(bytes);
    }
    println!(
        "stream: the XTCE digest, then {} packets, {} bytes",
        readings.len(),
        stream.len()
    );

    // The stream's digest must be the digest of this program's XTCE file:
    // then it reads the packets as they were written.
    if stream[..DIGEST_BYTES] != digest {
        fail(&format!(
            "the stream's XTCE digest {} is not {}, the digest of {XTCE}",
            hex(&stream[..DIGEST_BYTES]),
            digest_hex(&digest)
        ));
    }
    println!(
        "XTCE digest: {}, the same on both sides",
        digest_hex(&digest)
    );

    // Walk the packets. Each starts with PacketLength, the number of bytes
    // after it, so the packet is 2 + PacketLength bytes long.
    let mut at = DIGEST_BYTES;
    for r in &readings {
        if stream.len() - at < 2 {
            fail(&format!("the stream ends inside a packet at byte {at}"));
        }
        let size =
            2 + usize::from(u16::from_be_bytes([stream[at], stream[at + 1]]));
        let packet = &stream[at..(at + size).min(stream.len())];
        codec.load(packet);
        let status: Status = codec
            .decode()
            .unwrap_or_else(|e| fail(&format!("decoding at {at}: {e}")));
        println!("decode at {at}: {size} bytes -> {status}");
        let want = Status {
            heater_on: r.status.heater_on,
            temperature: r.temperature,
            bus_voltage: r.bus_voltage,
            heater_power: r.heater_power,
        };
        if status != want {
            fail(&format!("decoded {status}, expected {want}"));
        }
        match codec.encode(&status) {
            Ok(bytes) if bytes == packet => {}
            Ok(bytes) => fail(&format!(
                "re-encoding {status} gave {}, expected {}",
                hex(bytes),
                hex(packet)
            )),
            Err(e) => fail(&format!("re-encoding {status}: {e}")),
        }
        at += size;
    }
    if at != stream.len() {
        fail(&format!(
            "{} bytes after the last packet",
            stream.len() - at
        ));
    }

    // What the generated code rejects.
    try_encode(
        &mut codec,
        &Status {
            heater_on: true,
            temperature: 20.0,
            bus_voltage: 28.0,
            heater_power: None,
        },
    );
    try_encode(
        &mut codec,
        &Status {
            heater_on: false,
            temperature: 300.0,
            bus_voltage: 28.0,
            heater_power: None,
        },
    );
    let first = DIGEST_BYTES;
    let on = stream[first + 5..first + 12].to_vec();
    try_decode(&mut codec, &on[..4], "cut short");
    let mut off = stream[first..first + 5].to_vec();
    off[1] += 1;
    off.push(0);
    try_decode(&mut codec, &off, "PacketLength one too many");
}
