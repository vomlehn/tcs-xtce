//! Prints the digest of each XTCE document named on the command line, one
//! per line, or `error` for one that can't be read: what
//! `tests/digest_check.sh` compares across languages.

use tcs_xtce::digest::digest_file;

fn main() {
    for path in std::env::args().skip(1) {
        match digest_file(&path) {
            Ok(d) => println!("{d}"),
            Err(_) => println!("error"),
        }
    }
}
