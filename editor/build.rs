//! Compiles the editor's windows.  The XTCE schemas are not built in: the
//! editor finds or downloads them when it runs (see `src/fetch.rs`).

/// Compiles the windows.
///
/// # Panics
///
/// Panics, with the Slint compiler's message, if they do not compile,
/// since a build script reports a failure by panicking.
fn main() {
    slint_build::compile("ui/editor.slint").expect("the windows compile");
}
