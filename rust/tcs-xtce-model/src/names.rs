//! The identifiers generated code uses for the names in a mission, the
//! same in every target language.

use std::collections::HashSet;

/// Words reserved in any of the target languages, and names the runtimes
/// and generated code use themselves, sorted: names are made identical in
/// every language, so the union is avoided everywhere.
pub const RESERVED: &[&str] = &[
    "EOF",
    "False",
    "NULL",
    "None",
    "True",
    "abstract",
    "alignas",
    "alignof",
    "and",
    "and_eq",
    "as",
    "asm",
    "assert",
    "async",
    "auto",
    "await",
    "become",
    "bitand",
    "bitor",
    "bool",
    "box",
    "break",
    "buf",
    "bytes",
    "case",
    "catch",
    "char",
    "char16_t",
    "char32_t",
    "char8_t",
    "class",
    "co_await",
    "co_return",
    "co_yield",
    "compl",
    "concept",
    "const",
    "const_cast",
    "consteval",
    "constexpr",
    "constinit",
    "continue",
    "crate",
    "data",
    "dataclasses",
    "decltype",
    "decode",
    "decode_prefix",
    "def",
    "default",
    "del",
    "delete",
    "describe",
    "dict",
    "do",
    "double",
    "dyn",
    "dynamic_cast",
    "elif",
    "else",
    "encode",
    "encode_into",
    "enum",
    "errno",
    "except",
    "explicit",
    "export",
    "extern",
    "false",
    "final",
    "finally",
    "float",
    "fn",
    "for",
    "friend",
    "from",
    "gen",
    "global",
    "goto",
    "id",
    "if",
    "impl",
    "import",
    "in",
    "init",
    "inline",
    "int",
    "is",
    "label",
    "lambda",
    "len",
    "let",
    "list",
    "long",
    "loop",
    "macro",
    "main",
    "match",
    "max_bytes",
    "mod",
    "move",
    "mut",
    "mutable",
    "name",
    "namespace",
    "new",
    "noexcept",
    "nonlocal",
    "not",
    "not_eq",
    "nullptr",
    "offsetof",
    "operator",
    "or",
    "or_eq",
    "override",
    "pass",
    "pk",
    "priv",
    "private",
    "protected",
    "pub",
    "public",
    "r",
    "raise",
    "raw",
    "ref",
    "register",
    "reinterpret_cast",
    "requires",
    "restrict",
    "return",
    "self",
    "short",
    "signed",
    "sizeof",
    "static",
    "static_assert",
    "static_cast",
    "stderr",
    "stdin",
    "stdout",
    "str",
    "struct",
    "super",
    "switch",
    "template",
    "this",
    "thread_local",
    "throw",
    "to_bytes",
    "trait",
    "true",
    "try",
    "type",
    "typedef",
    "typeid",
    "typename",
    "typeof",
    "union",
    "unsafe",
    "unsigned",
    "unsized",
    "use",
    "using",
    "value",
    "virtual",
    "void",
    "volatile",
    "w",
    "wchar_t",
    "where",
    "while",
    "with",
    "xor",
    "xor_eq",
    "yield",
];

/// Type names the runtimes or the generated modules define.
pub const RUNTIME_NAMES: &[&str] = &[
    "AlarmMonitor",
    "Any",
    "BitReader",
    "BitWriter",
    "Box",
    "Buffer",
    "Codec",
    "Err",
    "Error",
    "ManagedBuffer",
    "None",
    "Ok",
    "Option",
    "Optional",
    "Packet",
    "PacketError",
    "Result",
    "Self",
    "Some",
    "String",
    "Union",
    "Vec",
];

/// Breaks a name into words where its case changes, as the Python's
/// `([a-z0-9])([A-Z])` substitution does: a `_` between a lower case letter
/// or digit and the capital after it.
///
/// # Arguments
///
/// * `name` - The name.
///
/// # Returns
///
/// The name with the `_`s added.
fn split_case(name: &str) -> String {
    let chars: Vec<char> = name.chars().collect();
    let mut out = String::new();
    for (i, &c) in chars.iter().enumerate() {
        if i > 0 {
            let p = chars[i - 1];
            if (p.is_ascii_lowercase() || p.is_ascii_digit())
                && c.is_ascii_uppercase()
            {
                out.push('_');
            }
        }
        out.push(c);
    }
    out
}

/// Breaks a run of capitals before a capitalized word, as the Python's
/// `([A-Z]+)([A-Z][a-z])` substitution does: `HTTPServer` to
/// `HTTP_Server`.
///
/// # Arguments
///
/// * `s` - The name.
///
/// # Returns
///
/// The name with the `_`s added.
fn split_acronyms(s: &str) -> String {
    let chars: Vec<char> = s.chars().collect();
    let mut out = String::new();
    let mut i = 0;
    while i < chars.len() {
        // A run of capitals, the last of which starts a capitalized word.
        if chars[i].is_ascii_uppercase() {
            let mut j = i;
            while j < chars.len() && chars[j].is_ascii_uppercase() {
                j += 1;
            }
            // chars[i..j] are capitals; the match takes all but the last
            // as the run when a lower case letter follows the last.
            if j - i >= 2 && j < chars.len() && chars[j].is_ascii_lowercase() {
                out.extend(&chars[i..j - 1]);
                out.push('_');
                out.push(chars[j - 1]);
                out.push(chars[j]);
                i = j + 1;
                continue;
            }
            out.extend(&chars[i..j]);
            i = j;
            continue;
        }
        out.push(chars[i]);
        i += 1;
    }
    out
}

/// Replaces each run of characters that are not ASCII letters or digits
/// with one `replacement`, and trims it from both ends.
///
/// # Arguments
///
/// * `s`           - The text.
/// * `replacement` - What each run becomes.
///
/// # Returns
///
/// The words of `s`, joined by `replacement`.
fn words(s: &str, replacement: char) -> String {
    let mut out = String::new();
    let mut gap = false;
    for c in s.chars() {
        if c.is_ascii_alphanumeric() {
            if gap && !out.is_empty() {
                out.push(replacement);
            }
            gap = false;
            out.push(c);
        } else {
            gap = true;
        }
    }
    out
}

/// Converts an XTCE name to a snake_case identifier usable in every target
/// language.
///
/// # Arguments
///
/// * `name` - The name, in any case and with any punctuation.
///
/// # Returns
///
/// Lower case words joined by `_`, `f_` in front if it would start with a
/// digit, and `_` after a reserved word.
pub fn snake(name: &str) -> String {
    let s = split_acronyms(&split_case(name));
    let mut s = words(&s, '_').to_ascii_lowercase();
    if s.is_empty() {
        s = "x".into();
    }
    if s.starts_with(|c: char| c.is_ascii_digit()) {
        s = format!("f_{s}");
    }
    if RESERVED.contains(&s.as_str()) {
        s.push('_');
    }
    s
}

/// Converts an XTCE name to a CamelCase type name.
///
/// # Arguments
///
/// * `name` - The name, in any case and with any punctuation.
///
/// # Returns
///
/// The words of the name, each capitalized, run together; `T` in front if
/// it would start with a digit, and `X` for a name with no letters or
/// digits.
pub fn camel(name: &str) -> String {
    let mut s = String::new();
    for part in words(name, ' ').split(' ').filter(|p| !p.is_empty()) {
        let mut cs = part.chars();
        if let Some(c) = cs.next() {
            s.push(c.to_ascii_uppercase());
            s.extend(cs);
        }
    }
    if s.is_empty() {
        s = "X".into();
    }
    if s.starts_with(|c: char| c.is_ascii_digit()) {
        s = format!("T{s}");
    }
    s
}

/// Converts an enumeration label to an UPPER_CASE constant name.
///
/// # Arguments
///
/// * `label` - The label, in any case and with any punctuation.
///
/// # Returns
///
/// Upper case words joined by `_`; `V_` in front if it would start with a
/// digit, and `V` for a label with no letters or digits.
pub fn upper(label: &str) -> String {
    let mut s = words(&split_case(label), '_').to_ascii_uppercase();
    if s.is_empty() {
        s = "V".into();
    }
    if s.starts_with(|c: char| c.is_ascii_digit()) {
        s = format!("V_{s}");
    }
    s
}

/// Hands out unique identifiers within one namespace.
#[derive(Debug, Default)]
pub struct Namer {
    /// The identifiers given out, and those reserved.
    used: HashSet<String>,
}

impl Namer {
    /// Makes a namer with some identifiers already taken.
    ///
    /// # Arguments
    ///
    /// * `reserved` - The identifiers it must not give out.
    ///
    /// # Returns
    ///
    /// The namer.
    pub fn with_reserved<'a>(
        reserved: impl IntoIterator<Item = &'a str>,
    ) -> Self {
        Namer {
            used: reserved.into_iter().map(String::from).collect(),
        }
    }

    /// Claims the first candidate not yet used.
    ///
    /// # Arguments
    ///
    /// * `candidates` - The names to try, in order of preference; at least one.
    ///
    /// # Returns
    ///
    /// The first unused candidate, or if all are used the last one
    /// followed by the smallest number from 2 up that makes it unique.
    pub fn take(&mut self, candidates: &[&str]) -> String {
        for c in candidates {
            if !self.used.contains(*c) {
                self.used.insert(c.to_string());
                return c.to_string();
            }
        }
        let base = candidates.last().expect("a candidate");
        let mut n = 2;
        while self.used.contains(&format!("{base}{n}")) {
            n += 1;
        }
        let name = format!("{base}{n}");
        self.used.insert(name.clone());
        name
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_are_made_as_the_python_makes_them() {
        assert_eq!(snake("HTTPServer"), "http_server");
        assert_eq!(snake("seqCount"), "seq_count");
        assert_eq!(snake("CCSDS_APID"), "ccsds_apid");
        assert_eq!(snake("2nd"), "f_2nd");
        assert_eq!(snake("type"), "type_");
        assert_eq!(snake("--"), "x");
        assert_eq!(snake("ABCDef"), "abc_def");
        assert_eq!(camel("my-type_name"), "MyTypeName");
        assert_eq!(camel("3d"), "T3d");
        assert_eq!(upper("modeOn"), "MODE_ON");
        assert_eq!(upper("1st"), "V_1ST");
        assert_eq!(upper("!"), "V");
    }

    #[test]
    fn a_namer_numbers_what_is_taken() {
        let mut n = Namer::default();
        assert_eq!(n.take(&["a"]), "a");
        assert_eq!(n.take(&["a"]), "a2");
        assert_eq!(n.take(&["b", "a"]), "b");
        assert_eq!(n.take(&["a"]), "a3");
    }
}
