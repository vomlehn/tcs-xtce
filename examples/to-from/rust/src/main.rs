//! Writes or reads the words of the lorem ipsum paragraph as `Word`
//! packets described by `to_from.xml`, using the code `tcs-xtce` generated
//! into `to_from.rs`.
//!
//! `to-from write` writes one packet per word to standard output.
//! `to-from read` reads packets from standard input and prints the words as
//! they arrive, separated by spaces, and a newline at the end of the input.
//! It checks that every word arrives intact and in order.
//!
//! The C/C++ and Python versions of this program write the same bytes and
//! print the same words, so any of them can read what any other wrote. The
//! `to-from` script in the directory above connects a writer to a reader.

mod to_from;

use std::env;
use std::io::{self, Read, Write};
use std::process;

use tcs_xtce::packet::{Codec, Packet};
use to_from::Word;

const LOREM_IPSUM: &str = "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do \
    eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis \
    nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis aute \
    irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla \
    pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia \
    deserunt mollit anim id est laborum.";

/// The size of a `Word`'s size tag: 32 bits, most significant byte first,
/// as `to_from.xml` gives it.
const TAG_BYTES: usize = 4;

fn fail(msg: &str) -> ! {
    eprintln!("to-from: {msg}");
    process::exit(1);
}

/// Writes every word to standard output, one `Word` packet each.
fn write() {
    let mut out = io::stdout().lock();
    // The codec's output buffer is preallocated to Word::MAX_BYTES (36) and
    // reused for every packet.
    let mut codec = Codec::for_packet::<Word>();
    for text in LOREM_IPSUM.split_whitespace() {
        let word = Word { text: text.into() };
        let bytes = codec
            .encode(&word)
            .unwrap_or_else(|e| fail(&format!("serialize {text:?}: {e}")));
        out.write_all(bytes)
            .unwrap_or_else(|e| fail(&format!("write: {e}")));
    }
    out.flush().unwrap_or_else(|e| fail(&format!("write: {e}")));
}

/// Reads into all of `buf`. Returns false if the input ended before the
/// first byte, and fails if it ended part way through.
fn read_all(input: &mut impl Read, buf: &mut [u8]) -> bool {
    let mut got = 0;
    while got < buf.len() {
        match input.read(&mut buf[got..]) {
            Ok(0) if got == 0 => return false,
            Ok(0) => fail("input ended in the middle of a word"),
            Ok(n) => got += n,
            Err(e) if e.kind() == io::ErrorKind::Interrupted => {}
            Err(e) => fail(&format!("read: {e}")),
        }
    }
    true
}

/// Reads words from standard input until it ends, printing each one and
/// checking it against the next expected word.
fn read() {
    let mut input = io::stdin().lock();
    let mut out = io::stdout();
    let mut expected = LOREM_IPSUM.split_whitespace();
    // The codec's input buffer is preallocated to Word::MAX_BYTES and reused
    // for every packet.
    let mut codec = Codec::for_packet::<Word>();
    let mut packet = [0u8; Word::MAX_BYTES];
    let mut count = 0;

    // A packet is its 32-bit size tag followed by that many bytes of text,
    // so the tag says how much more of the input belongs to this word.
    while read_all(&mut input, &mut packet[..TAG_BYTES]) {
        let mut tag = [0u8; TAG_BYTES];
        tag.copy_from_slice(&packet[..TAG_BYTES]);
        let size = u32::from_be_bytes(tag);
        let len =
            TAG_BYTES + usize::try_from(size).unwrap_or(usize::MAX - TAG_BYTES);
        if len > Word::MAX_BYTES {
            fail(&format!("size tag {size} is too large"));
        }
        if !read_all(&mut input, &mut packet[TAG_BYTES..len]) {
            fail("input ended in the middle of a word");
        }

        // Deserialize from the managed input buffer.
        codec.load(&packet[..len]);
        let word: Word = codec
            .decode()
            .unwrap_or_else(|e| fail(&format!("deserialize: {e}")));
        match expected.next() {
            Some(want) if want == word.text => {}
            Some(want) => fail(&format!(
                "word {} is {:?}, expected {want:?}",
                count + 1,
                word.text
            )),
            None => fail(&format!("unexpected extra word {:?}", word.text)),
        }

        if count > 0 {
            print!(" ");
        }
        print!("{}", word.text);
        let _ = out.flush();
        count += 1;
    }

    if let Some(want) = expected.next() {
        fail(&format!("input ended after {count} words, before {want:?}"));
    }
    println!();
}

fn main() {
    let args: Vec<String> = env::args().skip(1).collect();
    match args.as_slice() {
        [a] if a == "read" => read(),
        [a] if a == "write" => write(),
        _ => {
            eprintln!("usage: to-from read|write");
            process::exit(2);
        }
    }
}
