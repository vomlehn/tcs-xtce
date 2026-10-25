//! Converts floats as `tests/float_check.sh` asks, one case per line of
//! standard input: `d FORMAT BITS RAW` decodes the raw value (hex) and
//! prints the double's bits, and `e FORMAT BITS DOUBLE` encodes the double
//! (its bits in hex) and prints the raw value; an error prints its kind.

use std::io::BufRead;
use tcs_xtce::packet::{float_decode, float_encode, FloatFormat};

fn format(name: &str) -> FloatFormat {
    match name {
        "IEEE754" => FloatFormat::Ieee,
        "MILSTD_1750A" => FloatFormat::Mil1750a,
        "DEC" => FloatFormat::Dec,
        "IBM" => FloatFormat::Ibm,
        _ => FloatFormat::Ti,
    }
}

fn main() {
    for line in std::io::stdin().lock().lines() {
        let line = line.unwrap();
        let w: Vec<&str> = line.split_whitespace().collect();
        let (fmt, bits) = (format(w[1]), w[2].parse::<u32>().unwrap());
        let v = u128::from_str_radix(w[3], 16).unwrap();
        let out = if w[0] == "d" {
            // Every runtime prints the same NaN.
            float_decode(fmt, bits, v, "x").map(|x| {
                let b = if x.is_nan() {
                    0x7ff8_0000_0000_0000
                } else {
                    x.to_bits()
                };
                format!("{b:x}")
            })
        } else {
            float_encode(fmt, bits, f64::from_bits(v as u64), "x")
                .map(|r| format!("{r:x}"))
        };
        match out {
            Ok(s) => println!("{s}"),
            Err(e) => println!("error:{}", e.kind()),
        }
    }
}
