//! Converts floats as `tests/float_check.sh` asks, one case per line of
//! standard input: `d FORMAT BITS RAW` decodes the raw value (hex) and
//! prints the double's bits, and `e FORMAT BITS DOUBLE` encodes the double
//! (its bits in hex) and prints the raw value; an error prints its kind.
//! `m OP A B` applies a MathOperationCalibrator operator to two doubles
//! (their bits in hex) and prints the result's bits.

use std::io::BufRead;
use tcs_xtce::packet::{
    float_decode, float_encode, math, FloatFormat, MathOperator,
};

fn format(name: &str) -> FloatFormat {
    match name {
        "IEEE754" => FloatFormat::Ieee,
        "MILSTD_1750A" => FloatFormat::Mil1750a,
        "DEC" => FloatFormat::Dec,
        "IBM" => FloatFormat::Ibm,
        _ => FloatFormat::Ti,
    }
}

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

/// The bits of `x`; every runtime prints the same NaN.
fn bits(x: f64) -> u64 {
    if x.is_nan() {
        0x7ff8_0000_0000_0000
    } else {
        x.to_bits()
    }
}

fn main() {
    for line in std::io::stdin().lock().lines() {
        let line = line.unwrap();
        let w: Vec<&str> = line.split_whitespace().collect();
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
