//! Python's ways of writing values, which the compiler's messages and the
//! canonical text use, so that the Rust says what the Python said.

/// Says whether Python's `str.isprintable` holds for a character, as far
/// as the documents need: control characters, the C1 controls, the soft
/// hyphen, the line and paragraph separators and unassigned or private
/// planes are not printable.
///
/// # Arguments
///
/// * `c` - The character.
///
/// # Returns
///
/// Whether `repr` writes it as it is.
fn printable(c: char) -> bool {
    let u = c as u32;
    !(u < 0x20
        || (0x7f..0xa0).contains(&u)
        || u == 0xad
        || u == 0x2028
        || u == 0x2029
        || (0x200b..=0x200f).contains(&u)
        || (0x202a..=0x202e).contains(&u)
        || (0x2060..=0x2064).contains(&u)
        || u == 0xfeff
        || (0xe000..=0xf8ff).contains(&u)
        || (0xfff0..=0xfffb).contains(&u)
        || u >= 0xf0000)
}

/// Writes a string as Python's `repr` does.
///
/// # Arguments
///
/// * `s` - The string.
///
/// # Returns
///
/// It in single quotes, or double quotes if it holds a single quote and
/// no double quote, with backslashes, the quote, `\n`, `\r`, `\t` and
/// unprintable characters escaped.
pub fn repr(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::new();
    out.push(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if printable(c) => out.push(c),
            c if (c as u32) < 0x100 => {
                out.push_str(&format!("\\x{:02x}", c as u32))
            }
            c if (c as u32) < 0x10000 => {
                out.push_str(&format!("\\u{:04x}", c as u32))
            }
            c => out.push_str(&format!("\\U{:08x}", c as u32)),
        }
    }
    out.push(quote);
    out
}

/// Writes a float as Python's `format(x, "g")` does: six significant
/// digits, trailing zeros dropped, in scientific notation when the
/// exponent is below -4 or at least 6.
///
/// # Arguments
///
/// * `x` - The float.
///
/// # Returns
///
/// Its text, such as `0.5`, `1e+06` or `inf`.
pub fn g(x: f64) -> String {
    if x.is_nan() {
        return "nan".into();
    }
    if x.is_infinite() {
        return if x > 0.0 { "inf" } else { "-inf" }.into();
    }
    if x == 0.0 {
        return if x.is_sign_negative() { "-0" } else { "0" }.into();
    }
    // Rounded to six significant digits, which may carry into the
    // exponent.
    let sci = format!("{:.5e}", x);
    let (mant, exp) = sci.split_once('e').expect("an exponent");
    let exp: i32 = exp.parse().expect("an integer exponent");
    if (-4..6).contains(&exp) {
        let decimals = (5 - exp).max(0) as usize;
        let s = format!("{:.*}", decimals, x);
        return strip_zeros(&s);
    }
    let m = strip_zeros(mant);
    let sign = if exp < 0 { '-' } else { '+' };
    format!("{m}e{sign}{:02}", exp.abs())
}

/// Drops the trailing zeros of a decimal fraction, and its point if
/// nothing follows it.
///
/// # Arguments
///
/// * `s` - The number's text.
///
/// # Returns
///
/// It without them.
fn strip_zeros(s: &str) -> String {
    if !s.contains('.') {
        return s.into();
    }
    s.trim_end_matches('0').trim_end_matches('.').into()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strings_are_written_as_python_repr_does() {
        assert_eq!(repr("ab"), "'ab'");
        assert_eq!(repr("a'b"), "\"a'b\"");
        assert_eq!(repr("a'\"b"), "'a\\'\"b'");
        assert_eq!(repr("a\nb\\"), "'a\\nb\\\\'");
        assert_eq!(repr("é\u{1}"), "'é\\x01'");
    }

    #[test]
    fn floats_are_written_as_python_g_does() {
        assert_eq!(g(0.5), "0.5");
        assert_eq!(g(1.0), "1");
        assert_eq!(g(2.75), "2.75");
        assert_eq!(g(1e6), "1e+06");
        assert_eq!(g(123456.0), "123456");
        assert_eq!(g(1234567.0), "1.23457e+06");
        assert_eq!(g(0.0001), "0.0001");
        assert_eq!(g(0.00001), "1e-05");
        assert_eq!(g(-3.5), "-3.5");
        assert_eq!(g(999999.5), "1e+06");
    }
}
