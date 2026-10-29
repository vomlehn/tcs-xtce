//! Decodes and encodes CCSDS time codes as `tests/ccsds_check.sh` asks, one
//! case per line of standard input; the C, C++ and Python versions of this
//! tool print the same.  Octets are in hex (`x` for none) and seconds are
//! the bits of an f64 in hex:
//!
//! * `cuc_decode HEX` and `cds_decode HEX` decode a time that starts with
//!   its P-field; `cuc_decode_t COARSE FINE EPOCH HEX` and `cds_decode_t
//!   DAY24 SUB EPOCH HEX` a T-field in that layout.  They print the octets
//!   read and the seconds, or `none`.
//! * `cuc_encode COARSE FINE EPOCH SECONDS PFIELD CAP` and `cds_encode
//!   DAY24 SUB EPOCH SECONDS PFIELD CAP` encode, with the P-field when
//!   PFIELD is 1, into CAP octets: they print the octets, `needs N`, or
//!   `none`.
//!
//! EPOCH is 0 for CCSDS's and 1 for the agency's, SUB 0, 1 or 2 for none,
//! microseconds or picoseconds.

use std::io::{self, BufRead, Write};
use tcs_ccsds::time::{decode_cds, decode_cuc, Cds, Cuc, Epoch, SubMs};

fn unhex(s: &str) -> Vec<u8> {
    (0..s.len() / 2)
        .map(|i| u8::from_str_radix(&s[2 * i..2 * i + 2], 16).unwrap())
        .collect()
}

/// Formats octets in hex.
///
/// # Arguments
///
/// * `b` - The octets.
///
/// # Returns
///
/// Two hex digits per octet, or `x` for none.
fn hex(b: &[u8]) -> String {
    if b.is_empty() {
        return "x".into();
    }
    b.iter().map(|x| format!("{x:02x}")).collect()
}

fn decoded(r: Option<(usize, f64)>) -> String {
    r.map_or("none".into(), |(k, s)| format!("{k} {:016x}", s.to_bits()))
}

/// Formats the result of an encoding.
///
/// # Arguments
///
/// * `r`   - What the encoding gave: the octets written or needed, or
///           `None` for a time the layout can't hold.
/// * `out` - The octets it wrote.
/// * `cap` - The octets there was room for.
///
/// # Returns
///
/// The octets in hex, `needs N`, or `none`.
fn encoded(r: Option<usize>, out: &[u8], cap: usize) -> String {
    match r {
        None => "none".into(),
        Some(n) if n > cap => format!("needs {n}"),
        Some(n) => hex(&out[..n]),
    }
}

/// Parses an EPOCH argument.
///
/// # Arguments
///
/// * `s` - `1` for the agency's epoch; anything else is CCSDS's.
///
/// # Returns
///
/// The epoch.
fn epoch(s: &str) -> Epoch {
    if s == "1" {
        Epoch::Agency
    } else {
        Epoch::Ccsds
    }
}

/// Parses a SUB argument.
///
/// # Arguments
///
/// * `s` - `1` for microseconds, `2` for picoseconds; anything else is
///         none.
///
/// # Returns
///
/// The resolution below a millisecond.
fn sub(s: &str) -> SubMs {
    match s {
        "1" => SubMs::Micro,
        "2" => SubMs::Pico,
        _ => SubMs::None,
    }
}

/// Computes one case.
///
/// # Arguments
///
/// * `w` - The case's words: the operation, then its arguments.
///
/// # Returns
///
/// The line the tool prints for the case.
///
/// # Panics
///
/// If the operation isn't known or an argument is missing or
/// malformed.
fn run(w: &[&str]) -> String {
    let n = |i: usize| w[i].parse::<u8>().unwrap();
    let secs =
        |i: usize| f64::from_bits(u64::from_str_radix(w[i], 16).unwrap());
    match w[0] {
        "cuc_decode" => decoded(decode_cuc(&unhex(w[1]))),
        "cds_decode" => decoded(decode_cds(&unhex(w[1]))),
        "cuc_decode_t" => {
            let c = Cuc::new(n(1), n(2), epoch(w[3])).unwrap();
            decoded(c.decode_t(&unhex(w[4])))
        }
        "cds_decode_t" => {
            let c = Cds::new(w[1] == "1", sub(w[2]), epoch(w[3]));
            decoded(c.decode_t(&unhex(w[4])))
        }
        "cuc_encode" | "cds_encode" => {
            let cap: usize = w[6].parse().unwrap();
            let mut out = vec![0u8; cap];
            let (s, p) = (secs(4), w[5] == "1");
            let r = if w[0] == "cuc_encode" {
                let c = Cuc::new(n(1), n(2), epoch(w[3])).unwrap();
                if p {
                    c.encode(s, &mut out)
                } else {
                    c.encode_t(s, &mut out)
                }
            } else {
                let c = Cds::new(w[1] == "1", sub(w[2]), epoch(w[3]));
                if p {
                    c.encode(s, &mut out)
                } else {
                    c.encode_t(s, &mut out)
                }
            };
            encoded(r, &out, cap)
        }
        op => panic!("unknown operation {op}"),
    }
}

/// Reads cases from standard input and prints each one's result.
///
/// # Panics
///
/// If the input or output fails or a case is malformed.
fn main() {
    let stdin = io::stdin();
    let mut out = io::BufWriter::new(io::stdout().lock());
    for line in stdin.lock().lines() {
        let line = line.unwrap();
        let w: Vec<&str> = line.split_whitespace().collect();
        if !w.is_empty() {
            writeln!(out, "{}", run(&w)).unwrap();
        }
    }
}
