//! Sends two arrays of lorem ipsum lines from one thread to another over one
//! TCP connection, each as a `Paragraph` packet described by
//! `variable_array.xml`, using the code `tcs-xtce` generated into
//! `variable_array.rs`.
//!
//! A `Paragraph` is a two-dimensional array whose dimensions both vary: an
//! array of lines, each an array of words. Before each packet the sender
//! writes a `Length` packet, also described by `variable_array.xml`, giving
//! its size in bytes, so the receiver knows how many bytes to read; it then
//! writes the whole `Paragraph` in a single write. The receiver checks that every
//! line and word arrived intact and in order, then prints each line with its
//! words separated by spaces. The C/C++ and Python versions of this program
//! print identical output.

mod variable_array;

use std::io::{self, Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::process;
use std::thread;

use tcs_xtce::packet::{Codec, Packet};
use variable_array::{Length, Line, Paragraph};

/// The lorem ipsum paragraph in five lines, as two arrays: three lines, then
/// two.
const PARAGRAPHS: [&[&str]; 2] = [
    &[
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut",
        "labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco",
        "laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in",
    ],
    &[
        "voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat",
        "cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.",
    ],
];

fn fail(msg: &str) -> ! {
    eprintln!("variable-array: {msg}");
    process::exit(1);
}

/// Array `p` as a `Paragraph`: one `Line` per line, one word per element.
fn paragraph(p: usize) -> Paragraph {
    let lines = PARAGRAPHS[p]
        .iter()
        .map(|line| {
            let words: Vec<String> =
                line.split(' ').map(String::from).collect();
            // WordCount precedes Words and gives their number.
            let word_count = u8::try_from(words.len())
                .unwrap_or_else(|_| fail("too many words"));
            Line { word_count, words }
        })
        .collect();
    Paragraph { lines }
}

/// Describes the first difference between `got` and `want`, if any.
fn difference(got: &Paragraph, want: &Paragraph) -> Option<String> {
    if got.lines.len() != want.lines.len() {
        return Some(format!(
            "received {} lines, expected {}",
            got.lines.len(),
            want.lines.len()
        ));
    }
    for (i, (g, w)) in got.lines.iter().zip(&want.lines).enumerate() {
        if g.words.len() != w.words.len() {
            return Some(format!(
                "line {} has {} words, expected {}",
                i + 1,
                g.words.len(),
                w.words.len()
            ));
        }
        for (j, (gw, ww)) in g.words.iter().zip(&w.words).enumerate() {
            if gw != ww {
                return Some(format!(
                    "line {} word {} is {gw:?}, expected {ww:?}",
                    i + 1,
                    j + 1
                ));
            }
        }
    }
    None
}

/// Connects to `addr` and sends each array: a `Length` packet, then the
/// `Paragraph` packet in a single write. Then closes the connection.
fn send(addr: SocketAddr) {
    let mut stream = TcpStream::connect(addr)
        .unwrap_or_else(|e| fail(&format!("connect: {e}")));
    // The codecs' output buffers are preallocated to Paragraph::MAX_BYTES
    // and Length::MAX_BYTES (4) and reused for both arrays.
    let mut codec = Codec::for_packet::<Paragraph>();
    let mut length_codec = Codec::for_packet::<Length>();
    for p in 0..PARAGRAPHS.len() {
        let bytes = codec.encode(&paragraph(p)).unwrap_or_else(|e| {
            fail(&format!("serialize array {}: {e}", p + 1))
        });
        let length = Length {
            byte_count: u32::try_from(bytes.len())
                .unwrap_or_else(|_| fail("packet too long")),
        };
        let length_bytes = length_codec
            .encode(&length)
            .unwrap_or_else(|e| fail(&format!("serialize length: {e}")));
        stream
            .write_all(length_bytes)
            .unwrap_or_else(|e| fail(&format!("send: {e}")));
        let n = stream
            .write(bytes)
            .unwrap_or_else(|e| fail(&format!("send: {e}")));
        if n != bytes.len() {
            fail(&format!("one write sent {n} of {} bytes", bytes.len()));
        }
    }
    // Dropping the stream closes it, which tells the receiver there are no
    // more arrays.
}

/// Reads into all of `buf`. Returns false if the stream ended before the
/// first byte, and fails if it ended part way through.
fn read_all(stream: &mut TcpStream, buf: &mut [u8]) -> bool {
    let mut got = 0;
    while got < buf.len() {
        match stream.read(&mut buf[got..]) {
            Ok(0) if got == 0 => return false,
            Ok(0) => fail("stream closed in the middle of an array"),
            Ok(n) => got += n,
            Err(e) if e.kind() == io::ErrorKind::Interrupted => {}
            Err(e) => fail(&format!("receive: {e}")),
        }
    }
    true
}

/// Accepts the connection and receives arrays until it closes: for each, it
/// reads and decodes the `Length` packet, then reads that many bytes,
/// decodes the `Paragraph`, checks it and prints it.
fn receive(listener: TcpListener) {
    let (mut stream, _) = listener
        .accept()
        .unwrap_or_else(|e| fail(&format!("accept: {e}")));
    // The codecs' input buffers are preallocated to Paragraph::MAX_BYTES and
    // Length::MAX_BYTES (4) and reused for both arrays.
    let mut codec = Codec::for_packet::<Paragraph>();
    let mut length_codec = Codec::for_packet::<Length>();
    let mut packet = [0u8; Paragraph::MAX_BYTES];
    let mut length_packet = [0u8; Length::MAX_BYTES];
    let mut p = 0;

    while read_all(&mut stream, &mut length_packet) {
        length_codec.load(&length_packet);
        let length: Length = length_codec
            .decode()
            .unwrap_or_else(|e| fail(&format!("deserialize length: {e}")));
        let len = usize::try_from(length.byte_count).unwrap_or(usize::MAX);
        if len > Paragraph::MAX_BYTES {
            fail(&format!(
                "length {len} is more than a Paragraph's {}",
                Paragraph::MAX_BYTES
            ));
        }
        if !read_all(&mut stream, &mut packet[..len]) {
            fail("stream closed in the middle of an array");
        }
        if p == PARAGRAPHS.len() {
            fail("received more arrays than were sent");
        }

        // Deserialize from the managed input buffer.
        codec.load(&packet[..len]);
        let got: Paragraph = codec.decode().unwrap_or_else(|e| {
            fail(&format!("deserialize array {}: {e}", p + 1))
        });
        if let Some(diff) = difference(&got, &paragraph(p)) {
            fail(&format!("array {}: {diff}", p + 1));
        }

        for line in &got.lines {
            println!("{}", line.words.join(" "));
        }
        p += 1;
    }

    if p != PARAGRAPHS.len() {
        fail(&format!(
            "stream closed after {p} of {} arrays",
            PARAGRAPHS.len()
        ));
    }
}

fn main() {
    // Listen on a port the system chooses, on the loopback interface only.
    let listener = TcpListener::bind("127.0.0.1:0")
        .unwrap_or_else(|e| fail(&format!("listen: {e}")));
    let addr = listener
        .local_addr()
        .unwrap_or_else(|e| fail(&format!("listen: {e}")));

    let sender = thread::spawn(move || send(addr));
    receive(listener);
    if sender.join().is_err() {
        fail("sender thread panicked");
    }
}
