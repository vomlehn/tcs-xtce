//! Reads an XTCE document, deserializes it, and serializes it again.
//!
//! Usage: `xtce_roundtrip INPUT [OUTPUT]` (writes to stdout without OUTPUT).

use std::fs::File;
use std::io::{self, BufWriter};
use std::process::ExitCode;

use xtce::Document;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.is_empty() || args.len() > 2 {
        eprintln!("usage: xtce_roundtrip INPUT [OUTPUT]");
        return ExitCode::from(2);
    }
    let mut doc = Document::default();
    let result = File::open(&args[0])
        .map_err(|e| xtce::Error::new(format!("{}: {e}", args[0])))
        .and_then(|f| doc.read_from(f))
        .and_then(|()| match args.get(1) {
            Some(path) => File::create(path)
                .map_err(|e| xtce::Error::new(format!("{path}: {e}")))
                .and_then(|f| doc.write_to(BufWriter::new(f))),
            None => doc.write_to(io::stdout().lock()),
        });
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("{}: {e}", args[0]);
            ExitCode::FAILURE
        }
    }
}
