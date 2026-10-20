//! Conversion of scalar values to and from their XML lexical forms.
//!
//! The formats are identical in the Rust, C++ and Python libraries so that
//! all three serialize a given document to the same bytes.

/// A value that appears as attribute text or simple element content.
pub trait Value: Sized {
    /// Parses the XML lexical form.
    fn parse_xml(s: &str) -> Result<Self, String>;
    /// Appends the canonical XML lexical form to `out`.
    fn fmt_xml(&self, out: &mut String);
}

impl Value for String {
    fn parse_xml(s: &str) -> Result<Self, String> {
        Ok(s.to_owned())
    }

    fn fmt_xml(&self, out: &mut String) {
        out.push_str(self);
    }
}

impl Value for bool {
    fn parse_xml(s: &str) -> Result<Self, String> {
        match s.trim() {
            "true" | "1" => Ok(true),
            "false" | "0" => Ok(false),
            other => Err(format!("invalid boolean {other:?}")),
        }
    }

    fn fmt_xml(&self, out: &mut String) {
        out.push_str(if *self { "true" } else { "false" });
    }
}

macro_rules! integer_value {
    ($($t:ty),*) => {$(
        impl Value for $t {
            fn parse_xml(s: &str) -> Result<Self, String> {
                let t = s.trim();
                let t = t.strip_prefix('+').unwrap_or(t);
                t.parse::<$t>()
                    .map_err(|e| format!("invalid {} {s:?}: {e}", stringify!($t)))
            }

            fn fmt_xml(&self, out: &mut String) {
                use std::fmt::Write;
                let _ = write!(out, "{self}");
            }
        }
    )*};
}

integer_value!(i32, i64, u16);

impl Value for f64 {
    fn parse_xml(s: &str) -> Result<Self, String> {
        match s.trim() {
            "INF" | "+INF" => Ok(f64::INFINITY),
            "-INF" => Ok(f64::NEG_INFINITY),
            "NaN" => Ok(f64::NAN),
            t => {
                // Rust accepts "inf"/"nan" spellings that XML Schema does not.
                if t.bytes()
                    .any(|b| b.is_ascii_alphabetic() && b != b'e' && b != b'E')
                {
                    return Err(format!("invalid double {s:?}"));
                }
                t.parse::<f64>()
                    .map_err(|e| format!("invalid double {s:?}: {e}"))
            }
        }
    }

    fn fmt_xml(&self, out: &mut String) {
        format_double(*self, out);
    }
}

/// Formats a double as the shortest round-trip string, in the style of
/// Python's `repr`: fixed notation for exponents in [-4, 16), otherwise
/// scientific notation with a signed, at least two digit exponent.
pub fn format_double(x: f64, out: &mut String) {
    if x.is_nan() {
        out.push_str("NaN");
        return;
    }
    if x.is_infinite() {
        out.push_str(if x > 0.0 { "INF" } else { "-INF" });
        return;
    }
    // `{:e}` yields the shortest round-trip digit count, e.g. "-1.25e-7", but
    // when two candidates of that length are equally close it may pick
    // either.  Reformatting at that precision rounds the exact value half to
    // even, which is what Python's `repr` and C++'s `to_chars` produce.
    let shortest = format!("{x:e}");
    let digits = shortest
        .split('e')
        .next()
        .unwrap_or("")
        .chars()
        .filter(char::is_ascii_digit)
        .count();
    let sci = format!("{:.*e}", digits.saturating_sub(1), x);
    let (mantissa, exp) = sci.split_once('e').unwrap_or((&sci, "0"));
    let exp: i32 = exp.parse().unwrap_or(0);
    let (neg, mantissa) = match mantissa.strip_prefix('-') {
        Some(m) => (true, m),
        None => (false, mantissa),
    };
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    if neg {
        out.push('-');
    }
    if (-4..16).contains(&exp) {
        if exp < 0 {
            out.push_str("0.");
            for _ in 0..(-exp - 1) {
                out.push('0');
            }
            out.push_str(&digits);
        } else {
            let point = exp as usize + 1;
            if digits.len() <= point {
                out.push_str(&digits);
                for _ in digits.len()..point {
                    out.push('0');
                }
                out.push_str(".0");
            } else {
                out.push_str(&digits[..point]);
                out.push('.');
                out.push_str(&digits[point..]);
            }
        }
    } else {
        out.push_str(&digits[..1]);
        if digits.len() > 1 {
            out.push('.');
            out.push_str(&digits[1..]);
        }
        out.push('e');
        out.push(if exp < 0 { '-' } else { '+' });
        let e = exp.unsigned_abs();
        if e < 10 {
            out.push('0');
        }
        out.push_str(&e.to_string());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fmt(x: f64) -> String {
        let mut s = String::new();
        format_double(x, &mut s);
        s
    }

    #[test]
    fn doubles_match_python_repr() {
        assert_eq!(fmt(1.0), "1.0");
        assert_eq!(fmt(1.5), "1.5");
        assert_eq!(fmt(-0.25), "-0.25");
        assert_eq!(fmt(0.0001), "0.0001");
        assert_eq!(fmt(0.00001), "1e-05");
        assert_eq!(fmt(1e16), "1e+16");
        assert_eq!(fmt(1234567890123456.0), "1234567890123456.0");
        assert_eq!(fmt(1.5e300), "1.5e+300");
        assert_eq!(fmt(100.0), "100.0");
        assert_eq!(fmt(f64::INFINITY), "INF");
        // Two shortest candidates are equally close; Python picks the even one.
        assert_eq!(fmt(-830.72296142578125), "-830.7229614257812");
        assert_eq!(fmt(5e-324), "5e-324");
        assert_eq!(fmt(-0.0), "-0.0");
    }

    #[test]
    fn rejects_non_xml_doubles() {
        assert!(f64::parse_xml("inf").is_err());
        assert_eq!(f64::parse_xml(" -INF ").unwrap(), f64::NEG_INFINITY);
        assert_eq!(f64::parse_xml("1E3").unwrap(), 1000.0);
    }
}
