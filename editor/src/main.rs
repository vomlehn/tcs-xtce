//! `tcs-xtcee`: the XTCE editor, and the same checks and conversions from
//! the command line.

use std::process::ExitCode;

/// Runs a command given on the command line, or opens the editor on the
/// files given.
///
/// # Returns
///
/// Success, or failure if a command found a problem or the editor could
/// not run.
fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if tcs_xtce_editor::cli::is_command(&args) {
        return tcs_xtce_editor::cli::run(&args);
    }
    match tcs_xtce_editor::gui::run(&args) {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("tcs-xtcee: {e}");
            ExitCode::FAILURE
        }
    }
}
