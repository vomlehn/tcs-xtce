//! Strings in the character sets XTCE names, as the compiler checks the
//! fixed strings a document gives.

use crate::model::{Encoding, StringEnc, VType};

/// Gives the bytes in one code unit of an XTCE string encoding.
///
/// # Arguments
///
/// * `charset` - The encoding, such as `UTF-16LE`.
///
/// # Returns
///
/// 2 for UTF-16, 4 for UTF-32, and 1 otherwise.
pub fn code_unit(charset: &str) -> usize {
    if charset.contains("16") {
        2
    } else if charset.contains("32") {
        4
    } else {
        1
    }
}

/// The characters Windows-1252 puts at 0x80 to 0x9F, `None` where it has
/// none.
const CP1252_HIGH: [Option<char>; 32] = [
    Some('\u{20AC}'),
    None,
    Some('\u{201A}'),
    Some('\u{0192}'),
    Some('\u{201E}'),
    Some('\u{2026}'),
    Some('\u{2020}'),
    Some('\u{2021}'),
    Some('\u{02C6}'),
    Some('\u{2030}'),
    Some('\u{0160}'),
    Some('\u{2039}'),
    Some('\u{0152}'),
    None,
    Some('\u{017D}'),
    None,
    None,
    Some('\u{2018}'),
    Some('\u{2019}'),
    Some('\u{201C}'),
    Some('\u{201D}'),
    Some('\u{2022}'),
    Some('\u{2013}'),
    Some('\u{2014}'),
    Some('\u{02DC}'),
    Some('\u{2122}'),
    Some('\u{0161}'),
    Some('\u{203A}'),
    Some('\u{0153}'),
    None,
    Some('\u{017E}'),
    Some('\u{0178}'),
];

/// Encodes a string in an XTCE character set.
///
/// # Arguments
///
/// * `s`       - The string.
/// * `charset` - The character set: UTF-8, US-ASCII, ISO-8859-1, Windows-1252,
///               or UTF-16 or UTF-32 in either byte order, big endian when it
///               doesn't say.
///
/// # Returns
///
/// Its bytes, or `None` if the set can't hold one of its characters.
pub fn encode_text(s: &str, charset: &str) -> Option<Vec<u8>> {
    let mut out = Vec::new();
    match charset {
        "UTF-8" => out.extend_from_slice(s.as_bytes()),
        "US-ASCII" | "ISO-8859-1" => {
            let top = if charset == "US-ASCII" { 0x7f } else { 0xff };
            for c in s.chars() {
                if c as u32 > top {
                    return None;
                }
                out.push(c as u8);
            }
        }
        "Windows-1252" => {
            for c in s.chars() {
                let u = c as u32;
                if u < 0x80 || (0xa0..=0xff).contains(&u) {
                    out.push(u as u8);
                } else {
                    let i = CP1252_HIGH.iter().position(|h| *h == Some(c))?;
                    out.push(0x80 + i as u8);
                }
            }
        }
        "UTF-16" | "UTF-16BE" | "UTF-16LE" => {
            for u in s.encode_utf16() {
                if charset == "UTF-16LE" {
                    out.extend_from_slice(&u.to_le_bytes());
                } else {
                    out.extend_from_slice(&u.to_be_bytes());
                }
            }
        }
        "UTF-32" | "UTF-32BE" | "UTF-32LE" => {
            for c in s.chars() {
                let u = c as u32;
                if charset == "UTF-32LE" {
                    out.extend_from_slice(&u.to_le_bytes());
                } else {
                    out.extend_from_slice(&u.to_be_bytes());
                }
            }
        }
        _ => return None,
    }
    Some(out)
}

/// Finds a terminator in a string's bytes.
///
/// # Arguments
///
/// * `b`    - The string's bytes.
/// * `term` - The terminator's bytes.
/// * `unit` - The bytes in one code unit of the string's encoding.
///
/// # Returns
///
/// The first position of `term` in `b` that starts a code unit, or
/// `None` if there is none.
pub fn find_unit(b: &[u8], term: &[u8], unit: usize) -> Option<usize> {
    if term.is_empty() {
        return Some(0);
    }
    (0..b.len().saturating_sub(term.len() - 1))
        .find(|&i| i % unit == 0 && b[i..].starts_with(term))
}

/// Says how much content a string field can hold on the wire.
///
/// # Arguments
///
/// * `vt` - The type of a string field, of a boolean encoded as a string, or of
///          a string encoded as binary.
///
/// # Returns
///
/// The most bytes of content, in its charset, that the field can hold.
pub fn wire_cap(vt: &VType) -> i64 {
    let e: &StringEnc = match &vt.enc {
        Encoding::Binary(b) => return b.bits / 8,
        Encoding::String(s) => s,
        _ => panic!("wire_cap of a type that is not a string"),
    };
    let mut fixed = e.fixed_bits;
    let mx = e.max_bits;
    let tag = e.size_tag_bits.unwrap_or(0);
    if e.dyn_.is_some() {
        fixed = Some(mx);
    }
    let tag_cap = |bits: i64| {
        let most = if tag >= 63 {
            i64::MAX
        } else {
            (1i64 << tag) - 1
        };
        ((bits - tag).div_euclid(8)).min(most).max(0)
    };
    match (fixed, &e.terminator) {
        (Some(f), Some(_)) => f.div_euclid(8),
        (Some(f), None) => tag_cap(f),
        (None, Some(t)) => (mx.div_euclid(8) - t.len() as i64).max(0),
        (None, None) => tag_cap(mx),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn character_sets_encode_as_python_does() {
        assert_eq!(encode_text("é", "UTF-8").unwrap(), vec![0xc3, 0xa9]);
        assert_eq!(encode_text("é", "US-ASCII"), None);
        assert_eq!(encode_text("€", "Windows-1252").unwrap(), vec![0x80]);
        assert_eq!(encode_text("\u{81}", "Windows-1252"), None);
        assert_eq!(encode_text("A", "UTF-16LE").unwrap(), vec![0x41, 0]);
        assert_eq!(encode_text("A", "UTF-32").unwrap(), vec![0, 0, 0, 0x41]);
    }

    #[test]
    fn a_terminator_is_found_only_where_a_unit_starts() {
        assert_eq!(find_unit(&[0x41, 0, 0, 0x42], &[0, 0], 2), None);
        assert_eq!(find_unit(&[0x41, 0, 0, 0], &[0, 0], 2), Some(2));
        assert_eq!(find_unit(b"ab", b"b", 1), Some(1));
    }
}
