//! What the `tcs-xtce` compiler makes of a mission, asked from the editor.
//!
//! The packet codecs are the compiler's: it decides each field's size and
//! place, what it supports and what it refuses.  So the editor asks it,
//! rather than working any of that out again, which would be a second
//! statement of the same rules to keep in step with the first.  Three
//! things are asked:
//!
//! * whether the mission compiles at all ([`compile`]), which the editor
//!   reports beside the schema's problems;
//! * the size report ([`size_report`]), each packet's fields with the bits
//!   each takes, which the editor draws as the packet's layout;
//! * encoding and decoding ([`Tester`]): the compiler writes the mission's
//!   Python codecs, and a small program run with them encodes values the
//!   person gives, or decodes bytes the person pastes.
//!
//! The compiler reads XTCE 1.3, so a mission in an older version is
//! converted to 1.3 in memory first; nothing is written to its files.

use std::path::{Path, PathBuf};
use std::process::Command;

use crate::convert::{convert, Answers};
use crate::schema::Version;
use crate::tree::Document;

/// The program the tester runs with the generated codecs.  It is given the
/// directory holding them, the module's name, and a request: `list`,
/// `encode PACKET JSON` or `decode PACKET HEX`.  It prints JSON.
const HELPER: &str = r#"
import dataclasses, json, sys
sys.path.insert(0, sys.argv[1])
mod = __import__(sys.argv[2])
from tcsxtce import packet as pk

def packets():
    out = {}
    for v in vars(mod).values():
        if (isinstance(v, type) and issubclass(v, pk.Packet)
                and getattr(v, "NAME", None)):
            out[v.NAME] = v
    return out

def fields(cls):
    if dataclasses.is_dataclass(cls):
        return [[f.name, str(f.type)] for f in dataclasses.fields(cls)]
    return [[k, str(t)] for k, t in getattr(cls, "__annotations__", {}).items()
            if not str(t).startswith("ClassVar")]

def plain(v):
    if dataclasses.is_dataclass(v):
        return {f.name: plain(getattr(v, f.name))
                for f in dataclasses.fields(v)}
    if isinstance(v, (list, tuple)):
        return [plain(x) for x in v]
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    return str(v)

req = sys.argv[3]
try:
    if req == "list":
        # One line a packet, then one a field, tab-separated, which the
        # editor reads without a JSON library.
        for n, c in packets().items():
            print("PACKET\t" + n)
            for f, t in fields(c):
                print("FIELD\t" + f + "\t" + t)
    elif req == "encode":
        cls = packets()[sys.argv[4]]
        p = cls(**json.loads(sys.argv[5]))
        print(json.dumps({"hex": p.to_bytes().hex(), "text": str(p)}))
    elif req == "decode":
        cls = packets()[sys.argv[4]]
        p = cls.decode(bytes.fromhex(sys.argv[5].replace(" ", "")))
        print(json.dumps({"fields": plain(p), "text": str(p)}))
except Exception as e:
    print(json.dumps({"error": f"{type(e).__name__}: {e}"}))
"#;

/// Where the `tcs_xtce` Python package is found.
///
/// # Returns
///
/// The directory to put on `PYTHONPATH`, or `None` to rely on the
/// package being on Python's own path.  `TCS_XTCE_PYTHONPATH` says
/// where, if set; otherwise an installed editor uses the package installed
/// with it, and a build in a checkout the checkout's own, if it has been
/// generated.
fn python_path() -> Option<PathBuf> {
    if let Some(p) = std::env::var_os("TCS_XTCE_PYTHONPATH") {
        return Some(PathBuf::from(p));
    }
    if let Some(p) = crate::tools::installed("lib/python3/site-packages") {
        return Some(p);
    }
    let here = Path::new(env!("CARGO_MANIFEST_DIR")).join("../python");
    here.join("tcs_xtce/generated.py").exists().then_some(here)
}

/// Makes a command running Python with the `tcs_xtce` package on its
/// path.  `-P` and `-s` keep the working directory and the user's own
/// packages off the path, so that only the package meant is imported;
/// `-I` would do that too, but would also ignore `PYTHONPATH`.
///
/// # Returns
///
/// The command, not yet given what to run.
fn python() -> Command {
    let mut c = Command::new("python3");
    c.args(["-P", "-s"]);
    if let Some(p) = python_path() {
        c.env("PYTHONPATH", p);
    }
    c
}

/// A mission written out where the compiler can read it.
#[derive(Debug)]
pub struct Written {
    /// The directory the documents are written in, removed when this is
    /// dropped.
    dir: tempfile::TempDir,
    /// The documents' files, in the mission's order.
    pub files: Vec<PathBuf>,
}

/// Writes a mission's documents, in XTCE 1.3, for the compiler.
///
/// # Arguments
///
/// * `docs` - The documents and their file names, in the mission's order.
///
/// # Returns
///
/// Where they were written.
///
/// # Errors
///
/// Says why, if one cannot be converted to 1.3 without more information,
/// or the files cannot be written.
pub fn write(docs: &[(&str, &Document)]) -> Result<Written, String> {
    let dir = tempfile::Builder::new()
        .prefix("tcs-xtcee-")
        .tempdir()
        .map_err(|e| format!("no temporary directory: {e}"))?;
    let mut files = Vec::new();
    for (name, doc) in docs {
        let text = match Version::of_document(doc)? {
            Version::V1_3 => doc.write(),
            _ => {
                let o = convert(doc, Version::V1_3, &Answers::new());
                match o.doc {
                    Some(d) => d.write(),
                    None => {
                        return Err(format!(
                            "{name} cannot be compiled until it is in XTCE \
                             1.3, and converting it needs answers: use \
                             Version > Upgrade to 1.3 to give them"
                        ))
                    }
                }
            }
        };
        let base = Path::new(name)
            .file_name()
            .map_or("mission.xtce".into(), |f| f.to_string_lossy().to_string());
        let p = dir.path().join(base);
        std::fs::write(&p, text).map_err(|e| e.to_string())?;
        files.push(p);
    }
    Ok(Written { dir, files })
}

/// Runs the compiler.
///
/// # Arguments
///
/// * `args` - Its arguments.
///
/// # Returns
///
/// What it wrote to standard output.
///
/// # Errors
///
/// What it wrote to standard error, or why it could not be run.
fn run(args: &[&str]) -> Result<String, String> {
    let out = python()
        .args(["-m", "tcs_xtce"])
        .args(args)
        .output()
        .map_err(|e| {
            format!(
                "python3 could not be run to ask the tcs-xtce compiler: {e}"
            )
        })?;
    if out.status.success() {
        Ok(String::from_utf8_lossy(&out.stdout).to_string())
    } else {
        let err = String::from_utf8_lossy(&out.stderr);
        // The last lines say why; the rest is a traceback.
        let tail: Vec<&str> = err.lines().rev().take(3).collect();
        Err(tail.into_iter().rev().collect::<Vec<_>>().join("\n"))
    }
}

/// Asks the compiler whether a mission compiles.
///
/// # Arguments
///
/// * `m` - The mission, written out.
///
/// # Errors
///
/// What the compiler says is wrong.
pub fn compile(m: &Written) -> Result<(), String> {
    let mut args: Vec<String> =
        m.files.iter().map(|f| f.display().to_string()).collect();
    args.push(m.dir.path().join("check.py").display().to_string());
    args.extend(["--lang".into(), "python".into()]);
    let a: Vec<&str> = args.iter().map(String::as_str).collect();
    run(&a).map(|_| ())
}

/// Asks the compiler for a mission's size report.
///
/// # Arguments
///
/// * `m` - The mission, written out.
///
/// # Returns
///
/// The report, as the compiler writes it.
///
/// # Errors
///
/// What the compiler says is wrong.
pub fn size_report(m: &Written) -> Result<String, String> {
    let mut args: Vec<String> =
        m.files.iter().map(|f| f.display().to_string()).collect();
    args.extend(["-".into(), "--size-report".into()]);
    let a: Vec<&str> = args.iter().map(String::as_str).collect();
    run(&a)
}

/// One field of a packet, as the size report gives it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Field {
    /// The field's name.
    pub name: String,
    /// Its type's reference.
    pub ty: String,
    /// The bits it takes on the wire.
    pub bits: u64,
    /// What the report says about it beyond its size, such as that it is
    /// there only when a condition holds.
    pub note: String,
}

/// One packet's layout, as the size report gives it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Layout {
    /// The packet's heading in the report.
    pub name: String,
    /// The packet's size, as the report says it.
    pub size: String,
    /// Its fields, in order on the wire.
    pub fields: Vec<Field>,
}

/// Reads the packets' layouts out of a size report.
///
/// # Arguments
///
/// * `report` - The report, as [`size_report`] gives it.
///
/// # Returns
///
/// Each packet's layout, in the report's order.
pub fn layouts(report: &str) -> Vec<Layout> {
    let mut out: Vec<Layout> = Vec::new();
    let mut in_table = false;
    for line in report.lines() {
        if line.starts_with('/') {
            let (name, size) =
                line.split_once("): ").map_or((line, ""), |(n, s)| {
                    (n.strip_suffix(')').unwrap_or(n), s)
                });
            out.push(Layout {
                name: format!("{name})").replace("))", ")"),
                size: size.to_string(),
                fields: Vec::new(),
            });
            in_table = false;
            continue;
        }
        let t = line.trim_start();
        if t.starts_with("field ") {
            in_table = true;
            continue;
        }
        if !in_table || !line.starts_with("  ") || line.starts_with("   ") {
            continue;
        }
        let words: Vec<&str> = t.split_whitespace().collect();
        if words.len() < 3 {
            continue;
        }
        let Ok(bits) = words[2].parse::<u64>() else {
            in_table = false;
            continue;
        };
        let note = words[3..]
            .iter()
            .skip_while(|w| {
                w.parse::<u64>().is_ok() || **w == "-" || **w == "="
            })
            .copied()
            .collect::<Vec<_>>()
            .join(" ");
        if let Some(l) = out.last_mut() {
            l.fields.push(Field {
                name: words[0].to_string(),
                ty: words[1].to_string(),
                bits,
                note,
            });
        }
    }
    out
}

/// The codecs of a mission, generated in Python, to encode and decode with.
#[derive(Debug)]
pub struct Tester {
    /// The mission as written for the compiler, which holds the codecs too.
    m: Written,
}

impl Tester {
    /// Generates a mission's codecs.
    ///
    /// # Arguments
    ///
    /// * `m` - The mission, written out.
    ///
    /// # Returns
    ///
    /// The tester.
    ///
    /// # Errors
    ///
    /// What the compiler says is wrong.
    pub fn new(m: Written) -> Result<Tester, String> {
        let mut args: Vec<String> =
            m.files.iter().map(|f| f.display().to_string()).collect();
        args.push(m.dir.path().join("mission_codecs.py").display().to_string());
        args.extend(["--lang".into(), "python".into()]);
        let a: Vec<&str> = args.iter().map(String::as_str).collect();
        run(&a)?;
        Ok(Tester { m })
    }

    /// Runs the helper with a request.
    ///
    /// # Arguments
    ///
    /// * `req` - The request and its arguments.
    ///
    /// # Returns
    ///
    /// What the helper printed.
    ///
    /// # Errors
    ///
    /// Why the helper could not be run, or what went wrong in it.
    fn ask(&self, req: &[&str]) -> Result<String, String> {
        let mut c = python();
        c.arg("-c").arg(HELPER);
        c.arg(self.m.dir.path()).arg("mission_codecs").args(req);
        let out = c.output().map_err(|e| format!("python3: {e}"))?;
        let text = String::from_utf8_lossy(&out.stdout).trim().to_string();
        if !out.status.success() || text.is_empty() {
            return Err(String::from_utf8_lossy(&out.stderr).to_string());
        }
        if text.starts_with('{') {
            if let Some(e) = json_string(&text, "error") {
                return Err(e);
            }
        }
        Ok(text)
    }

    /// Lists the packets and their fields.
    ///
    /// # Returns
    ///
    /// A line `PACKET\tname` for each packet, each followed by a line
    /// `FIELD\tname\ttype` for each of its fields.
    ///
    /// # Errors
    ///
    /// Why they could not be listed.
    pub fn list(&self) -> Result<String, String> {
        self.ask(&["list"])
    }

    /// Encodes a packet.
    ///
    /// # Arguments
    ///
    /// * `packet` - The packet's name, as [`Tester::list`] gives it.
    /// * `values` - Its fields' values, as a JSON object.
    ///
    /// # Returns
    ///
    /// The packet's bytes in hexadecimal.
    ///
    /// # Errors
    ///
    /// What the codec says is wrong with the values.
    pub fn encode(&self, packet: &str, values: &str) -> Result<String, String> {
        let out = self.ask(&["encode", packet, values])?;
        json_string(&out, "hex").ok_or(out)
    }

    /// Decodes a packet.
    ///
    /// # Arguments
    ///
    /// * `packet` - The packet's name, as [`Tester::list`] gives it.
    /// * `hex`    - Its bytes in hexadecimal; spaces are ignored.
    ///
    /// # Returns
    ///
    /// The packet as the codec describes it.
    ///
    /// # Errors
    ///
    /// What the codec says is wrong with the bytes.
    pub fn decode(&self, packet: &str, hex: &str) -> Result<String, String> {
        let out = self.ask(&["decode", packet, hex])?;
        json_string(&out, "text").ok_or(out)
    }
}

/// A string member of a small JSON object, without a JSON library: the
/// helper writes the objects, so their form is known.
///
/// # Arguments
///
/// * `json` - The object.
/// * `key`  - The member's name.
///
/// # Returns
///
/// The member's value, unescaped, or `None` if there is no such string.
pub fn json_string(json: &str, key: &str) -> Option<String> {
    let start = json.find(&format!("\"{key}\": \""))? + key.len() + 5;
    let mut out = String::new();
    let mut chars = json[start..].chars();
    while let Some(c) = chars.next() {
        match c {
            '"' => return Some(out),
            '\\' => match chars.next()? {
                'n' => out.push('\n'),
                't' => out.push('\t'),
                'u' => {
                    let hex: String = chars.by_ref().take(4).collect();
                    out.push(char::from_u32(
                        u32::from_str_radix(&hex, 16).ok()?,
                    )?);
                }
                other => out.push(other),
            },
            c => out.push(c),
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Reads a shipped document.
    ///
    /// # Arguments
    ///
    /// * `rel` - Its path from the top of the repository.
    ///
    /// # Returns
    ///
    /// The document.
    fn shipped(rel: &str) -> Document {
        let p = format!("{}/../{rel}", env!("CARGO_MANIFEST_DIR"));
        Document::parse(&std::fs::read_to_string(p).unwrap()).unwrap()
    }

    #[test]
    fn reads_layouts_from_a_size_report() {
        let r = "/H/Status (container Status): at most 56 bits, 7 bytes\n\
                 \x20 field         type    bits  needs  saves\n\
                 \x20 PacketLength  /H/L      16      -         no ValidRange\n\
                 \x20 HeaterOn      /H/B       4      1      3  boolean\n\
                 \x20 right-sized fields save 3 bits\n";
        let l = layouts(r);
        assert_eq!(l.len(), 1);
        assert_eq!(l[0].fields.len(), 2);
        assert_eq!(l[0].fields[1].bits, 4);
        assert_eq!(l[0].fields[1].note, "boolean");
    }

    #[test]
    fn asks_the_compiler() {
        if python_path().is_none() {
            eprintln!("tcs_xtce's Python binding is not generated; skipped");
            return;
        }
        let d = shipped("examples/housekeeping/housekeeping.xtce");
        let m = write(&[("housekeeping.xtce", &d)]).unwrap();
        compile(&m).unwrap();
        let r = size_report(&m).unwrap();
        let l = layouts(&r);
        assert_eq!(l[0].fields.len(), 5, "{r}");
        let t = Tester::new(m).unwrap();
        assert!(t.list().unwrap().contains("/Housekeeping/Status"));
        let hex = t
            .encode(
                "/Housekeeping/Status",
                "{\"heater_on\": false, \
                     \"temperature\": 5.0, \"bus_voltage\": 28.0}",
            )
            .unwrap();
        let text = t.decode("/Housekeeping/Status", &hex).unwrap();
        assert!(text.contains("28"), "{text}");
        assert!(t.decode("/Housekeeping/Status", "00").is_err());
    }
}
