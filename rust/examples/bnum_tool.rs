//! Computes integer operations with the bnum crate, one case per line of
//! standard input, as tests/bnum_check.sh asks: the reference that
//! cpp/include/xtce-bnum.h and xtce-bnum.hpp must agree with.
//!
//! A case is `OP TYPE ARGS...`, where TYPE is `u` or `i` and a size in bytes
//! (u17 is bnum's Uint<17>), and integers are in decimal.  It prints the
//! result: an integer, `none` for a checked operation that gave none, an
//! overflowing operation's result and `true` or `false`, or a parse error's
//! kind.  tests/bnum_cases.py lists the operations.

use bnum::cast::{As, CastFrom};
use bnum::errors::ParseIntError;
use bnum::Integer;
use std::fmt::Display;
use std::io::{self, BufRead, Write};

type I<const S: bool, const N: usize> = Integer<S, N>;

fn opt<T: Display>(o: Option<T>) -> String {
    o.map_or("none".to_string(), |v| v.to_string())
}

fn ovf<T: Display>((v, o): (T, bool)) -> String {
    format!("{v} {o}")
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

/// Hex bytes; "x" is none.
fn unhex(s: &str) -> Vec<u8> {
    (0..s.len() / 2)
        .map(|i| u8::from_str_radix(&s[2 * i..2 * i + 2], 16).unwrap())
        .collect()
}

fn err(e: ParseIntError) -> String {
    format!("{:?}", e.kind())
}

/// Computes an operation that every integer type has.
///
/// # Arguments
///
/// * `op`   - The operation's name, as bnum names the method.
/// * `args` - Its operands in decimal; a shift, power or radix is the
///            second, and a slice or float is hexadecimal.
///
/// # Returns
///
/// The result as the tool prints it.
///
/// # Panics
///
/// If `op` isn't known or an operand is missing or malformed.
fn common<const S: bool, const N: usize>(op: &str, args: &[&str]) -> String {
    let p = |s: &str| I::<S, N>::from_str_radix(s, 10).unwrap();
    let a = || p(args[0]);
    let b = || p(args[1]);
    let k = || args[1].parse::<u32>().unwrap();
    match op {
        "wrapping_add" => a().wrapping_add(b()).to_string(),
        "wrapping_sub" => a().wrapping_sub(b()).to_string(),
        "wrapping_mul" => a().wrapping_mul(b()).to_string(),
        "wrapping_div" => a().wrapping_div(b()).to_string(),
        "wrapping_rem" => a().wrapping_rem(b()).to_string(),
        "wrapping_div_euclid" => a().wrapping_div_euclid(b()).to_string(),
        "wrapping_rem_euclid" => a().wrapping_rem_euclid(b()).to_string(),
        "wrapping_neg" => a().wrapping_neg().to_string(),
        "wrapping_shl" => a().wrapping_shl(k()).to_string(),
        "wrapping_shr" => a().wrapping_shr(k()).to_string(),
        "wrapping_pow" => a().wrapping_pow(k()).to_string(),
        "overflowing_add" => ovf(a().overflowing_add(b())),
        "overflowing_sub" => ovf(a().overflowing_sub(b())),
        "overflowing_mul" => ovf(a().overflowing_mul(b())),
        "overflowing_div" => ovf(a().overflowing_div(b())),
        "overflowing_rem" => ovf(a().overflowing_rem(b())),
        "overflowing_neg" => ovf(a().overflowing_neg()),
        "overflowing_shl" => ovf(a().overflowing_shl(k())),
        "overflowing_shr" => ovf(a().overflowing_shr(k())),
        "overflowing_pow" => ovf(a().overflowing_pow(k())),
        "checked_add" => opt(a().checked_add(b())),
        "checked_sub" => opt(a().checked_sub(b())),
        "checked_mul" => opt(a().checked_mul(b())),
        "checked_div" => opt(a().checked_div(b())),
        "checked_rem" => opt(a().checked_rem(b())),
        "checked_div_euclid" => opt(a().checked_div_euclid(b())),
        "checked_rem_euclid" => opt(a().checked_rem_euclid(b())),
        "checked_neg" => opt(a().checked_neg()),
        "checked_shl" => opt(a().checked_shl(k())),
        "checked_shr" => opt(a().checked_shr(k())),
        "checked_pow" => opt(a().checked_pow(k())),
        "checked_ilog2" => opt(a().checked_ilog2()),
        "checked_ilog10" => opt(a().checked_ilog10()),
        "saturating_add" => a().saturating_add(b()).to_string(),
        "saturating_sub" => a().saturating_sub(b()).to_string(),
        "saturating_mul" => a().saturating_mul(b()).to_string(),
        "saturating_pow" => a().saturating_pow(k()).to_string(),
        "unbounded_shl" => a().unbounded_shl(k()).to_string(),
        "unbounded_shr" => a().unbounded_shr(k()).to_string(),
        "rotate_left" => a().rotate_left(k()).to_string(),
        "rotate_right" => a().rotate_right(k()).to_string(),
        "abs_diff" => a().abs_diff(b()).to_string(),
        "bitand" => (a() & b()).to_string(),
        "bitor" => (a() | b()).to_string(),
        "bitxor" => (a() ^ b()).to_string(),
        "not" => (!a()).to_string(),
        "cmp" => (a().cmp(&b()) as i8).to_string(),
        "min" => a().min(b()).to_string(),
        "max" => a().max(b()).to_string(),
        "count_ones" => a().count_ones().to_string(),
        "count_zeros" => a().count_zeros().to_string(),
        "leading_zeros" => a().leading_zeros().to_string(),
        "leading_ones" => a().leading_ones().to_string(),
        "trailing_zeros" => a().trailing_zeros().to_string(),
        "trailing_ones" => a().trailing_ones().to_string(),
        "swap_bytes" => a().swap_bytes().to_string(),
        "reverse_bits" => a().reverse_bits().to_string(),
        "isqrt" => a().isqrt().to_string(),
        "to_be_bytes" => hex(&a().to_be_bytes()),
        "to_str_radix" => a().to_str_radix(k()),
        "from_str_radix" => {
            let text = args.get(1).copied().unwrap_or("");
            let r = I::<S, N>::from_str_radix(text, k_at(args, 0));
            r.map_or_else(err, |v| v.to_string())
        }
        "from_le_slice" => opt(I::<S, N>::from_le_slice(&unhex(args[0]))),
        "from_be_slice" => opt(I::<S, N>::from_be_slice(&unhex(args[0]))),
        "cast" => cast(a(), args[1]),
        "to_f64" => format!("{:016x}", a().as_::<f64>().to_bits()),
        "from_f64" => {
            let bits = u64::from_str_radix(args[0], 16).unwrap();
            I::<S, N>::cast_from(f64::from_bits(bits)).to_string()
        }
        _ => panic!("unknown operation {op}"),
    }
}

fn k_at(args: &[&str], i: usize) -> u32 {
    args[i].parse().unwrap()
}

/// Computes an operation on an unsigned integer of `N` bytes.
///
/// # Arguments
///
/// * `op`   - The operation's name, as bnum names the method.
/// * `args` - Its operands, in decimal.
///
/// # Returns
///
/// The result as the tool prints it.
///
/// # Panics
///
/// If `op` isn't known or an operand is missing or malformed.
fn unsigned<const N: usize>(op: &str, args: &[&str]) -> String {
    let a = || bnum::Uint::<N>::from_str_radix(args[0], 10).unwrap();
    match op {
        "is_power_of_two" => a().is_power_of_two().to_string(),
        "bit_width" => a().bit_width().to_string(),
        _ => common::<false, N>(op, args),
    }
}

/// Computes an operation on a signed integer of `N` bytes.
///
/// # Arguments
///
/// * `op`   - The operation's name, as bnum names the method.
/// * `args` - Its operands, in decimal.
///
/// # Returns
///
/// The result as the tool prints it.
///
/// # Panics
///
/// If `op` isn't known or an operand is missing or malformed.
fn signed<const N: usize>(op: &str, args: &[&str]) -> String {
    let a = || bnum::Int::<N>::from_str_radix(args[0], 10).unwrap();
    match op {
        "wrapping_abs" => a().wrapping_abs().to_string(),
        "overflowing_abs" => ovf(a().overflowing_abs()),
        "checked_abs" => opt(a().checked_abs()),
        "saturating_abs" => a().saturating_abs().to_string(),
        "saturating_neg" => a().saturating_neg().to_string(),
        "unsigned_abs" => a().unsigned_abs().to_string(),
        "signum" => a().signum().to_string(),
        _ => common::<true, N>(op, args),
    }
}

/// Converts an integer to another integer type, as Rust's `as` does.
///
/// # Arguments
///
/// * `x`  - The integer.
/// * `to` - The type to convert it to, such as `u17` or `i3`.
///
/// # Returns
///
/// The converted value in decimal.
///
/// # Panics
///
/// If `to` isn't one of the types the tool knows.
fn cast<const S: bool, const N: usize>(x: I<S, N>, to: &str) -> String {
    macro_rules! to {
        ($($n:literal)*) => {
            match to {
                $(
                    t if t == concat!("u", $n) =>
                        x.as_::<I<false, $n>>().to_string(),
                    t if t == concat!("i", $n) =>
                        x.as_::<I<true, $n>>().to_string(),
                )*
                _ => panic!("unknown type {to}"),
            }
        };
    }
    to!(1 2 3 8 16 17 24 32)
}

/// Computes one case.
///
/// # Arguments
///
/// * `op`   - The operation's name, as bnum names the method.
/// * `ty`   - The integer type, `u` or `i` and a size in bytes.
/// * `args` - The operation's operands.
///
/// # Returns
///
/// The result as the tool prints it.
///
/// # Panics
///
/// If `ty` or `op` isn't known or an operand is missing or
/// malformed.
fn run(op: &str, ty: &str, args: &[&str]) -> String {
    macro_rules! sizes {
        ($($n:literal)*) => {
            match ty {
                $(
                    t if t == concat!("u", $n) => unsigned::<$n>(op, args),
                    t if t == concat!("i", $n) => signed::<$n>(op, args),
                )*
                _ => panic!("unknown type {ty}"),
            }
        };
    }
    sizes!(1 2 3 8 16 17 24 32)
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
        if w.is_empty() {
            continue;
        }
        writeln!(out, "{}", run(w[0], w[1], &w[2..])).unwrap();
    }
}
