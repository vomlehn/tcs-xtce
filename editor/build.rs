//! Checks that the XTCE schemas the editor is built with have been
//! fetched, and says how to fetch them if not, rather than leaving the
//! compiler to say a file is missing.

use std::path::Path;

/// Checks that each schema is in `references/`, and has Cargo build the
/// editor again when one changes.
///
/// # Panics
///
/// Panics, saying how to fetch the schemas, if one is missing, since a
/// build script reports a failure by panicking.
fn main() {
    for v in ["1.1", "1.2", "1.3"] {
        let p = format!("../references/xtce-{v}.xsd");
        println!("cargo:rerun-if-changed={p}");
        if !Path::new(&p).exists() {
            panic!(
                "{p} is missing: run `make -C references editor` at the top \
                 of tcs-xtce to fetch the XTCE schemas the editor is built \
                 with"
            );
        }
    }
}
