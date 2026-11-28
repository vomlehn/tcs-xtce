//! Checks that a mission, and everything made from it, gives back all
//! the memory it took when it is dropped: nothing is kept for the rest of
//! the program, however many missions are read.

use std::alloc::{GlobalAlloc, Layout, System};
use std::fs::File;
use std::path::PathBuf;
use std::sync::atomic::{AtomicIsize, Ordering};

use tcs_xtce::buffer::Buffer;
use tcs_xtce_dynamic::Mission;

mod common;

/// Counts the bytes allocated and not yet freed.
struct Counting;

/// The bytes allocated and not yet freed.
static LIVE: AtomicIsize = AtomicIsize::new(0);

unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, l: Layout) -> *mut u8 {
        LIVE.fetch_add(l.size() as isize, Ordering::Relaxed);
        unsafe { System.alloc(l) }
    }

    unsafe fn dealloc(&self, p: *mut u8, l: Layout) {
        LIVE.fetch_sub(l.size() as isize, Ordering::Relaxed);
        unsafe { System.dealloc(p, l) }
    }

    unsafe fn realloc(&self, p: *mut u8, l: Layout, n: usize) -> *mut u8 {
        LIVE.fetch_add(n as isize - l.size() as isize, Ordering::Relaxed);
        unsafe { System.realloc(p, l, n) }
    }
}

#[global_allocator]
static GLOBAL: Counting = Counting;

/// Reads the demo mission, runs a packet and a stream through it, and
/// drops it all.
///
/// # Arguments
///
/// * `text` - The mission's document.
fn read_run_and_drop(text: &[u8]) {
    let mut m = Mission::read_from([text]).unwrap();
    common::set_algorithms(&mut m);
    let top = m.top();
    let root = m.container(top, "/Demo/CCSDSPacket").unwrap();
    let hk = "0864c02a001201a04e2805dc040302013fc00000fffe8050fc";
    let data: Vec<u8> = (0..hk.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&hk[i..i + 2], 16).unwrap())
        .collect();
    let mut scratch = m.scratch();
    let got = m.unpack(root, &data, &mut scratch).unwrap();
    let mut store = m.store();
    store.commit(&scratch).unwrap();
    let mut out = Buffer::with_capacity(256);
    m.pack(got, &store, &mut out).unwrap();
    for name in m.streams() {
        let mut s = m.stream(&name).unwrap();
        s.push(&[0x1a, 0xcf, 0xfc, 0x1d, 0x01], |_| {});
    }
}

#[test]
fn dropping_a_mission_frees_everything() {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/mission/demo.xtce");
    let mut text = Vec::new();
    std::io::Read::read_to_end(&mut File::open(path).unwrap(), &mut text)
        .unwrap();
    // The first time, for anything the standard library sets up once.
    read_run_and_drop(&text);
    let before = LIVE.load(Ordering::Relaxed);
    for _ in 0..5 {
        read_run_and_drop(&text);
    }
    let after = LIVE.load(Ordering::Relaxed);
    assert_eq!(after - before, 0, "{} bytes kept", after - before);
}
