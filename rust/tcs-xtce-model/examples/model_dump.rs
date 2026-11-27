//! Writes the model of a mission as canonical text, for comparing with the
//! Python compiler's: `model_dump --types INPUT...` writes what
//! `python3 -m tcs_xtce INPUT... --dump-types` does, `--containers` what
//! `--dump-containers` does, `--commands` what `--dump-commands` does, and
//! `--mission` and `--mission-skipping` the whole mission, as compiled
//! without and with `--skip-unsupported`.
//! It exits with 3 if a
//! document can't be read, and 1 if the documents don't index.

use std::process::ExitCode;

/// The exit status when a document can't be read, which a comparison
/// tells from one the model refuses.
const READ_FAILED: u8 = 3;

use tcs_xtce::Document;
use tcs_xtce_model::compile::Compiler;

/// Reads the documents named on the command line and writes their types.
///
/// # Returns
///
/// Success, or failure after saying why on standard error.
fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let (opts, files): (Vec<&String>, Vec<&String>) =
        args.iter().partition(|a| a.starts_with("--"));
    let dialect = opts
        .iter()
        .find_map(|o| o.strip_prefix("--dialect="))
        .map(String::from);
    let stage = opts.iter().find_map(|o| match o.as_str() {
        "--types" => Some("types"),
        "--containers" => Some("containers"),
        "--commands" => Some("commands"),
        "--mission" => Some("mission"),
        "--mission-skipping" => Some("skipping"),
        _ => None,
    });
    let Some(stage) = stage.filter(|_| !files.is_empty()) else {
        eprintln!(
            "usage: model_dump \
             --types|--containers|--commands|--mission|--mission-skipping \
             [--dialect=D] INPUT..."
        );
        return ExitCode::from(2);
    };
    let mut docs = Vec::new();
    for f in &files {
        let read = std::fs::read(f).map_err(|e| e.to_string());
        match read
            .and_then(|b| Document::from_bytes(&b).map_err(|e| e.to_string()))
        {
            Ok(d) => docs.push(d),
            Err(e) => {
                eprintln!("tcs-xtce: {f}: {e}");
                return ExitCode::from(READ_FAILED);
            }
        }
    }
    let roots = docs.iter().map(|d| &d.root).collect();
    let mut c = match Compiler::new(roots, false, dialect.as_deref()) {
        Ok(c) => c,
        Err(e) => {
            let where_ = if files.len() == 1 {
                format!("{}: ", files[0])
            } else {
                String::new()
            };
            eprintln!("tcs-xtce: {where_}{e}");
            return ExitCode::FAILURE;
        }
    };
    match stage {
        "types" => print!("{}", tcs_xtce_model::dump_types(&mut c)),
        "containers" => print!("{}", tcs_xtce_model::dump_containers(&mut c)),
        "commands" => print!("{}", tcs_xtce_model::dump_commands(&mut c)),
        "mission" => print!("{}", tcs_xtce_model::dump_mission(&mut c, false)),
        _ => print!("{}", tcs_xtce_model::dump_mission(&mut c, true)),
    }
    ExitCode::SUCCESS
}
