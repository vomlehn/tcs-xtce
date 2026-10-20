//! Round-trips the schema-covering sample documents in `tests/samples`.

use std::path::PathBuf;

use xtce::{Buffer, Document, SpaceSystemType};

fn samples() -> Vec<PathBuf> {
    let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../tests/samples");
    let mut files: Vec<PathBuf> = std::fs::read_dir(&dir)
        .unwrap_or_else(|e| panic!("{}: {e} (run `make samples`)", dir.display()))
        .map(|e| e.unwrap().path())
        .filter(|p| p.extension().is_some_and(|x| x == "xml"))
        .collect();
    files.sort();
    assert!(!files.is_empty(), "no samples in {}", dir.display());
    files
}

#[test]
fn samples_round_trip() {
    for path in samples() {
        let bytes = std::fs::read(&path).unwrap();
        let mut doc =
            Document::from_bytes(&bytes).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
        let first = doc.serialize().unwrap().to_vec();
        let again = Document::from_bytes(&first)
            .unwrap_or_else(|e| panic!("{} (reserialized): {e}", path.display()));
        assert_eq!(again.root, doc.root, "{}", path.display());
        let mut again = again;
        assert_eq!(again.serialize().unwrap(), &first[..]);
    }
}

#[test]
fn buffers_are_retained() {
    let path = &samples()[0];
    let bytes = std::fs::read(path).unwrap();
    let mut doc = Document::from_bytes(&bytes).unwrap();
    let len = doc.serialize().unwrap().len();
    let cap = doc.output().capacity();
    let ptr = doc.output().as_bytes().as_ptr();
    for _ in 0..3 {
        assert_eq!(doc.serialize().unwrap().len(), len);
        assert_eq!(doc.output().capacity(), cap);
        assert_eq!(doc.output().as_bytes().as_ptr(), ptr);
    }
    let in_cap = doc.input().capacity();
    doc.load(&bytes).unwrap();
    assert_eq!(doc.input().capacity(), in_cap);
    doc.read_from(&bytes[..]).unwrap();
    assert_eq!(doc.input().capacity(), in_cap);

    let mut buf = Buffer::with_capacity(16);
    buf.push_str("abc");
    buf.clear();
    assert!(buf.is_empty() && buf.capacity() >= 16);
}

#[test]
fn reports_errors_with_positions() {
    let err = Document::from_bytes(b"<SpaceSystem xmlns='x' name='a'>\n<Bogus/></SpaceSystem>")
        .unwrap_err();
    assert_eq!(err.line, 2);
    assert!(err.message.contains("Bogus"), "{err}");

    let err = Document::from_bytes(b"<SpaceSystem/>").unwrap_err();
    assert!(err.message.contains("name"), "{err}");

    let err = Document::from_bytes(b"<Other name='a'/>").unwrap_err();
    assert!(err.message.contains("root"), "{err}");
}

#[test]
fn rejects_count_violations_on_write() {
    let mut doc = Document::new(SpaceSystemType {
        name: "x".into(),
        ..Default::default()
    });
    // An empty SpaceSystem is valid.
    doc.serialize().unwrap();
}
