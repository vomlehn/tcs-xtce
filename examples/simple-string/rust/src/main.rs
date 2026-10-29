//! Serializes and deserializes the `SimpleString` structure described by
//! `simple_string.xml`, using the code `tcs-xtce` generated into
//! `simple_string.rs`.
//!
//! The C/C++ and Python versions of this program print identical output.

mod simple_string;

use simple_string::SimpleString;
use tcs_xtce::packet::{describe_str, Codec};

fn quoted(s: &str) -> String {
    let mut out = String::new();
    // Writing to a String cannot fail.
    let _ = describe_str(s, &mut out);
    out
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

/// Serializes and deserializes strings that fit, are empty and are too
/// long, then checks that the codec's buffers were never reallocated.
///
/// # Panics
///
/// If a string that serialized doesn't deserialize to itself.
fn main() {
    // The codec owns an input and an output buffer, both preallocated to
    // SimpleString::MAX_BYTES (8) and reused for every packet.
    let mut codec = Codec::for_packet::<SimpleString>();
    let capacity = (codec.input_capacity(), codec.output_capacity());

    for text in ["ABCDEFGH", "Hi", "", "h\u{e9}llo", "ABCDEFGHI"] {
        let value = SimpleString { text: text.into() };

        // Serialize into the managed output buffer.
        let bytes = match codec.encode(&value) {
            Ok(bytes) => bytes.to_vec(),
            Err(e) => {
                println!(
                    "serialize {} -> error:{} ({e})",
                    quoted(text),
                    e.kind()
                );
                continue;
            }
        };
        println!("serialize {} -> {}", quoted(text), hex(&bytes));

        // Deserialize from the managed input buffer.
        codec.load(&bytes);
        let back: SimpleString = codec.decode().expect("decode");
        println!("deserialize {} -> {back}", hex(&bytes));
        assert_eq!(back, value);
    }

    let unchanged =
        capacity == (codec.input_capacity(), codec.output_capacity());
    println!("buffers reused without reallocation: {unchanged}");
}
