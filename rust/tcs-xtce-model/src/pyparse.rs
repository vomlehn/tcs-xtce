//! Numbers read from text as Python's `int()` and `float()` read them,
//! for the numbers the documents give as text (AncillaryData values and
//! literals), so that the Rust takes what the Python took.

use num_bigint::BigInt;

/// Checks the underscores of a run of digits as Python does, and drops
/// them: each must stand between two digits.
///
/// # Arguments
///
/// * `s`     - The digits.
/// * `digit` - Says whether a character is a digit of the base.
///
/// # Returns
///
/// The digits without underscores, or `None` if an underscore is out of
/// place or a character isn't a digit.
fn digits(s: &str, digit: impl Fn(char) -> bool) -> Option<String> {
    let cs: Vec<char> = s.chars().collect();
    if cs.is_empty() {
        return None;
    }
    let mut out = String::new();
    for (i, &c) in cs.iter().enumerate() {
        if c == '_' {
            let ok = i > 0
                && i + 1 < cs.len()
                && digit(cs[i - 1])
                && digit(cs[i + 1]);
            if !ok {
                return None;
            }
        } else if digit(c) {
            out.push(c);
        } else {
            return None;
        }
    }
    Some(out)
}

/// Reads an integer as Python's `int(s)` does.
///
/// # Arguments
///
/// * `s` - The text: space, an optional sign, then decimal digits that may have
///         `_` between them, then space.
///
/// # Returns
///
/// The integer, or `None` if the text isn't one or it doesn't fit 64
/// bits.
pub fn int(s: &str) -> Option<i64> {
    let i = big_int(s, 10)?;
    i64::try_from(i).ok()
}

/// Reads an integer of any size as Python's `int(s, base)` does.
///
/// # Arguments
///
/// * `s`    - The text.
/// * `base` - 10, or 0 to take `0x`, `0o` and `0b` prefixes as Python does (and
///            refuse other leading zeros).
///
/// # Returns
///
/// The integer, or `None` if the text isn't one.
pub fn big_int(s: &str, base: u32) -> Option<BigInt> {
    let t = s.trim();
    let (neg, body) = match t.strip_prefix('-') {
        Some(b) => (true, b),
        None => (false, t.strip_prefix('+').unwrap_or(t)),
    };
    let lower = body.to_ascii_lowercase();
    let (radix, rest) = if base == 0 {
        if let Some(r) = lower.strip_prefix("0x") {
            (16, r.strip_prefix('_').unwrap_or(r))
        } else if let Some(r) = lower.strip_prefix("0o") {
            (8, r.strip_prefix('_').unwrap_or(r))
        } else if let Some(r) = lower.strip_prefix("0b") {
            (2, r.strip_prefix('_').unwrap_or(r))
        } else {
            (10, lower.as_str())
        }
    } else {
        (base, lower.as_str())
    };
    let ds = digits(rest, |c| c.is_digit(radix))?;
    // Base 0 refuses a decimal with leading zeros, other than zero itself.
    if base == 0 && radix == 10 && ds.len() > 1 && ds.starts_with('0') {
        if ds.chars().any(|c| c != '0') {
            return None;
        }
    }
    let v = BigInt::parse_bytes(ds.as_bytes(), radix)?;
    Some(if neg { -v } else { v })
}

/// Reads a float as Python's `float(s)` does.
///
/// # Arguments
///
/// * `s` - The text: space, an optional sign, then a decimal number, `inf`,
///         `infinity` or `nan` in any case, then space; digits may have `_`
///         between them.
///
/// # Returns
///
/// The float, or `None` if the text isn't one.
pub fn float(s: &str) -> Option<f64> {
    let t = s.trim();
    let (sign, body) = match t.strip_prefix('-') {
        Some(b) => (-1.0, b),
        None => (1.0, t.strip_prefix('+').unwrap_or(t)),
    };
    match body.to_ascii_lowercase().as_str() {
        "inf" | "infinity" => return Some(sign * f64::INFINITY),
        "nan" => return Some(f64::NAN),
        _ => {}
    }
    // Mantissa, then an optional exponent; each part's digits may have
    // underscores between them.
    let lower = body.to_ascii_lowercase();
    let (mant, exp) = match lower.split_once('e') {
        Some((m, e)) => (m, Some(e)),
        None => (lower.as_str(), None),
    };
    let (int_part, frac) = match mant.split_once('.') {
        Some((a, b)) => (a, Some(b)),
        None => (mant, None),
    };
    let dec = |p: &str| digits(p, |c| c.is_ascii_digit());
    let mut text = String::new();
    if !int_part.is_empty() {
        text += &dec(int_part)?;
    }
    if let Some(f) = frac {
        text.push('.');
        if !f.is_empty() {
            text += &dec(f)?;
        }
    }
    if text.is_empty() || text == "." {
        return None;
    }
    if let Some(e) = exp {
        let (es, eb) = match e.strip_prefix('-') {
            Some(b) => ("-", b),
            None => ("", e.strip_prefix('+').unwrap_or(e)),
        };
        text.push('e');
        text.push_str(es);
        text += &dec(eb)?;
    }
    text.parse::<f64>().ok().map(|v| sign * v)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn integers_read_as_python_reads_them() {
        assert_eq!(int(" 42 "), Some(42));
        assert_eq!(int("+7"), Some(7));
        assert_eq!(int("-1_000"), Some(-1000));
        assert_eq!(int("1__0"), None);
        assert_eq!(int("_1"), None);
        assert_eq!(int("0x10"), None);
        assert_eq!(int(""), None);
        assert_eq!(big_int("0x1_f", 0), Some(BigInt::from(31)));
        assert_eq!(big_int("0b101", 0), Some(BigInt::from(5)));
        assert_eq!(big_int("007", 0), None);
        assert_eq!(big_int("000", 0), Some(BigInt::from(0)));
    }

    #[test]
    fn floats_read_as_python_reads_them() {
        assert_eq!(float(" 1.5 "), Some(1.5));
        assert_eq!(float("1e3"), Some(1000.0));
        assert_eq!(float(".5"), Some(0.5));
        assert_eq!(float("5."), Some(5.0));
        assert_eq!(float("-Infinity"), Some(f64::NEG_INFINITY));
        assert!(float("nan").unwrap().is_nan());
        assert_eq!(float("1_0.0_1"), Some(10.01));
        assert_eq!(float("1e"), None);
        assert_eq!(float("."), None);
        assert_eq!(float("abc"), None);
    }
}
