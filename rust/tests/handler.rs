//! Reads documents with the callback interface: the `Tree` handler gives
//! the tree the binding's readers give, and an `Observer` sees every
//! element and can stop reading.

use std::path::PathBuf;

use tcs_xtce::handler::{parse, Observer, Observing, Tree};
use tcs_xtce::{Document, Error, ParameterType, Result};

/// Lists the documents the tests read: the schema-covering samples and
/// the demo mission.
///
/// # Returns
///
/// Their paths, sorted.
fn documents() -> Vec<PathBuf> {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..");
    let dir = root.join("tests/samples");
    let mut files: Vec<PathBuf> = std::fs::read_dir(&dir)
        .unwrap_or_else(|e| {
            panic!("{}: {e} (run `make samples`)", dir.display())
        })
        .map(|e| e.unwrap().path())
        .filter(|p| p.extension().is_some_and(|x| x == "xtce"))
        .collect();
    files.sort();
    files.push(root.join("tests/mission/demo.xtce"));
    files
}

#[test]
fn tree_gives_the_binding_tree() {
    for path in documents() {
        let bytes = std::fs::read(&path).unwrap();
        let doc = Document::from_bytes(&bytes)
            .unwrap_or_else(|e| panic!("{}: {e}", path.display()));
        let tree = parse(&bytes, &mut Tree)
            .unwrap_or_else(|e| panic!("{}: {e}", path.display()));
        assert_eq!(tree, doc.root, "{}", path.display());
    }
}

/// An observer that collects the names of the parameters it sees, and
/// fails at one name if it is given one.
#[derive(Default)]
struct Names {
    /// The names seen, in the order seen.
    seen: Vec<String>,
    /// The name to fail at, if any.
    stop_at: Option<String>,
}

impl Observer for Names {
    fn parameter_type(&mut self, v: &ParameterType) -> Result<()> {
        if self.stop_at.as_deref() == Some(&v.name) {
            return Err(Error::new(format!("stopped at {}", v.name)));
        }
        self.seen.push(v.name.clone());
        Ok(())
    }
}

/// Counts the `Parameter` elements of a document's text.
///
/// # Arguments
///
/// * `text` - The document.
///
/// # Returns
///
/// How many start tags named `Parameter`, with any prefix, it has.
fn parameter_tags(text: &str) -> usize {
    text.match_indices('<')
        .filter(|(i, _)| {
            let rest = &text[i + 1..];
            let name = rest
                .split(|c: char| c.is_whitespace() || c == '>' || c == '/')
                .next()
                .unwrap_or("");
            name.rsplit(':').next() == Some("Parameter")
        })
        .count()
}

#[test]
fn observer_sees_every_parameter() {
    let path = documents().pop().unwrap();
    let bytes = std::fs::read(&path).unwrap();
    let mut h = Observing(Names::default());
    let tree = parse(&bytes, &mut h).unwrap();
    assert_eq!(tree, Document::from_bytes(&bytes).unwrap().root);
    let text = String::from_utf8(bytes).unwrap();
    assert!(!h.0.seen.is_empty());
    assert_eq!(h.0.seen.len(), parameter_tags(&text));
}

#[test]
fn observer_error_stops_reading() {
    let path = documents().pop().unwrap();
    let bytes = std::fs::read(&path).unwrap();
    let mut all = Observing(Names::default());
    parse(&bytes, &mut all).unwrap();
    let second = all.0.seen[1].clone();
    let mut h = Observing(Names {
        seen: Vec::new(),
        stop_at: Some(second.clone()),
    });
    let e = parse(&bytes, &mut h).unwrap_err();
    assert!(
        e.to_string().contains(&format!("stopped at {second}")),
        "{e}"
    );
    assert_eq!(h.0.seen, all.0.seen[..1]);
}
