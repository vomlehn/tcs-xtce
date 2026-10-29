//! Converts floats as `tests/float_check.sh` asks, one case per line of
//! standard input: `d FORMAT BITS RAW` decodes the raw value (hex) and
//! prints the double's bits, and `e FORMAT BITS DOUBLE` encodes the double
//! (its bits in hex) and prints the raw value; an error prints its kind.
//! `m OP A B` applies a MathOperationCalibrator operator to two doubles
//! (their bits in hex) and prints the result's bits.  `k KIND BITS POLY
//! INIT XOR REFLECT_DATA REFLECT_REMAINDER ODD DATA START END` computes an
//! ErrorDetectCorrect checksum (numbers in hex, flags 0 or 1) of the bits
//! from START to END of DATA.

use std::io::BufRead;
use tcs_xtce::packet::{
    checksum, float_decode, float_encode, math, Checksum, FloatFormat,
    MathOperator,
};

/// Looks up a float encoding by its XTCE name.
///
/// # Arguments
///
/// * `name` - The encoding's XTCE name, such as `IEEE754`.
///
/// # Returns
///
/// The format; any name not otherwise known is TI's.
fn format(name: &str) -> FloatFormat {
    match name {
        "IEEE754" => FloatFormat::Ieee,
        "MILSTD_1750A" => FloatFormat::Mil1750a,
        "DEC" => FloatFormat::Dec,
        "IBM" => FloatFormat::Ibm,
        _ => FloatFormat::Ti,
    }
}

/// Looks up a MathOperationCalibrator operator by its XTCE symbol.
///
/// # Arguments
///
/// * `name` - The operator's XTCE symbol, such as `y^x`.
///
/// # Returns
///
/// The operator.
///
/// # Panics
///
/// If `name` isn't known.
fn operator(name: &str) -> MathOperator {
    match name {
        "/" => MathOperator::Div,
        "%" => MathOperator::Mod,
        "^" => MathOperator::Pow,
        "y^x" => MathOperator::RevPow,
        "ln" => MathOperator::Ln,
        "log" => MathOperator::Log10,
        "e^x" => MathOperator::Exp,
        "1/x" => MathOperator::Inv,
        "x!" => MathOperator::Factorial,
        "tan" => MathOperator::Tan,
        "cos" => MathOperator::Cos,
        "sin" => MathOperator::Sin,
        "atan" => MathOperator::Atan,
        "atan2" => MathOperator::Atan2,
        "acos" => MathOperator::Acos,
        "asin" => MathOperator::Asin,
        "tanh" => MathOperator::Tanh,
        "cosh" => MathOperator::Cosh,
        "sinh" => MathOperator::Sinh,
        "atanh" => MathOperator::Atanh,
        "acosh" => MathOperator::Acosh,
        "asinh" => MathOperator::Asinh,
        "<<" => MathOperator::Shl,
        ">>" => MathOperator::Shr,
        "&" => MathOperator::BitAnd,
        "|" => MathOperator::BitOr,
        "xor" => MathOperator::BitXor,
        "~" => MathOperator::BitNot,
        "&&" => MathOperator::And,
        "||" => MathOperator::Or,
        "!" => MathOperator::Not,
        "abs" => MathOperator::Abs,
        "div" => MathOperator::EuclidDiv,
        "int" => MathOperator::Trunc,
        ">" => MathOperator::Gt,
        ">=" => MathOperator::Ge,
        "<" => MathOperator::Lt,
        "<=" => MathOperator::Le,
        "==" => MathOperator::Eq,
        "!=" => MathOperator::Ne,
        "min" => MathOperator::Min,
        "max" => MathOperator::Max,
        _ => panic!("unknown operator {name}"),
    }
}

/// Gives the bits of a double, with one NaN for every NaN.
///
/// # Arguments
///
/// * `x` - The double.
///
/// # Returns
///
/// Its bits, or `0x7ff8_0000_0000_0000` for any NaN, so that every runtime
/// prints the same NaN.
fn bits(x: f64) -> u64 {
    if x.is_nan() {
        0x7ff8_0000_0000_0000
    } else {
        x.to_bits()
    }
}

/// Reads cases from standard input and prints each one's result.
///
/// # Panics
///
/// If the input fails or a case is malformed.
fn main() {
    for line in std::io::stdin().lock().lines() {
        let line = line.unwrap();
        let w: Vec<&str> = line.split_whitespace().collect();
        if w[0] == "k" {
            let n = |i: usize| u64::from_str_radix(w[i], 16).unwrap();
            let bits = n(2) as u32;
            let spec = match w[1] {
                "crc" => Checksum::Crc {
                    width: bits,
                    poly: n(3),
                    init: n(4),
                    final_xor: n(5),
                    reflect_data: w[6] == "1",
                    reflect_remainder: w[7] == "1",
                },
                "sum" => Checksum::Sum(bits),
                "unix_sum" => Checksum::UnixSum,
                "fletcher" => Checksum::Fletcher(bits),
                "adler32" => Checksum::Adler32,
                "luhn" => Checksum::Luhn,
                "verhoeff" => Checksum::Verhoeff,
                "damm" => Checksum::Damm,
                "xor" => Checksum::Xor(bits),
                _ => Checksum::Parity(w[8] == "1"),
            };
            let data: Vec<u8> = (0..w[9].len() / 2)
                .map(|i| {
                    u8::from_str_radix(&w[9][2 * i..2 * i + 2], 16).unwrap()
                })
                .collect();
            let (start, end) = (n(10) as usize, n(11) as usize);
            match checksum(&spec, &data, start, end, "x") {
                Ok(v) => println!("{v:x}"),
                Err(e) => println!("error:{}", e.kind()),
            }
            continue;
        }
        if w[0] == "m" {
            let arg =
                |s: &str| f64::from_bits(u64::from_str_radix(s, 16).unwrap());
            let x = math(operator(w[1]), arg(w[2]), arg(w[3]));
            println!("{:x}", bits(x));
            continue;
        }
        let (fmt, size) = (format(w[1]), w[2].parse::<u32>().unwrap());
        let v = u128::from_str_radix(w[3], 16).unwrap();
        let out = if w[0] == "d" {
            float_decode(fmt, size, v, "x").map(|x| format!("{:x}", bits(x)))
        } else {
            float_encode(fmt, size, f64::from_bits(v as u64), "x")
                .map(|r| format!("{r:x}"))
        };
        match out {
            Ok(s) => println!("{s}"),
            Err(e) => println!("error:{}", e.kind()),
        }
    }
}
