//! Prints the digest of each XTCE document named on the command line, one
//! per line, or `error` for one that can't be read: what
//! `tests/digest_check.sh` compares across languages.  With `--set`, prints
//! the one digest of the set of documents instead.

use tcs_xtce::digest::{digest_file, digest_files};

/// Prints the digests the command line asks for.
fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.first().map(String::as_str) == Some("--set") {
        match digest_files(&args[1..]) {
            Ok(d) => println!("{d}"),
            Err(_) => println!("error"),
        }
        return;
    }
    for path in args {
        match digest_file(&path) {
            Ok(d) => println!("{d}"),
            Err(_) => println!("error"),
        }
    }
}
