//! The editor's commands for use without its windows, as from a Makefile
//! or a CI job.  They do what the editor's menus do, through the same code.

use std::process::ExitCode;

use crate::tree::Document;
use crate::validate::{check, Severity};

/// What `xtce-edit --help` prints.
const USAGE: &str = "\
usage: xtce-edit [FILE...]             open the files in the editor
       xtce-edit check FILE...         check each file against its XTCE
                                       version's schema
";

/// Runs the command a command line gives.
///
/// # Arguments
///
/// * `args` - The arguments, without the program's name.
///
/// # Returns
///
/// Success, or failure if the command found a problem or could not run.
pub fn run(args: &[String]) -> ExitCode {
    match args.first().map(String::as_str) {
        Some("check") => check_files(&args[1..]),
        Some("-h" | "--help") => {
            print!("{USAGE}");
            ExitCode::SUCCESS
        }
        _ => {
            eprint!("{USAGE}");
            ExitCode::FAILURE
        }
    }
}

/// Checks files and prints each problem with its file and line.
///
/// # Arguments
///
/// * `files` - The files.
///
/// # Returns
///
/// Success if no file has an error.
fn check_files(files: &[String]) -> ExitCode {
    let mut failed = false;
    for f in files {
        let bytes = match std::fs::read(f) {
            Ok(b) => b,
            Err(e) => {
                eprintln!("{f}: {e}");
                failed = true;
                continue;
            }
        };
        let doc = match Document::from_bytes(&bytes) {
            Ok(d) => d,
            Err(e) => {
                println!("{f}:{}: error: {}", e.place.line, e.message);
                failed = true;
                continue;
            }
        };
        let report = check(&doc);
        for p in &report.problems {
            let line = doc.line_of(p.id).unwrap_or(1);
            let sev = match p.severity {
                Severity::Error => {
                    failed = true;
                    "error"
                }
                Severity::Warning => "warning",
            };
            println!("{f}:{line}: {sev}: {}", p.message);
        }
    }
    if failed {
        ExitCode::FAILURE
    } else {
        ExitCode::SUCCESS
    }
}
