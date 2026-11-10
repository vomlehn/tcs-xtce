//! `xtce-edit`: the XTCE editor, and the same checks and conversions from
//! the command line.

use std::process::ExitCode;

/// Runs a command given on the command line, or opens the editor.
///
/// # Returns
///
/// Success, or failure if a command found a problem or could not run.
fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    tcs_xtce_editor::cli::run(&args)
}
