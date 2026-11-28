//! Checks that unpacking, committing and packing the demo mission's
//! packets, and evaluating their alarms and derived values, allocates
//! nothing once the store, scratch and alarm monitor are made, as the
//! codecs generated with `--dynamic-allocations false` don't: every
//! string, binary value and array has the room its largest value needs
//! from the start, and integers of more than 64 bits are held in a fixed
//! width. A packet that fails is the exception, for its error's message.

mod common;

use std::alloc::{GlobalAlloc, Layout, System};
use std::fs::File;
use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};

use tcs_xtce::buffer::Buffer;
use tcs_xtce_dynamic::Mission;

/// Counts the allocations the test makes.
struct Counting;

/// The allocations made so far.
static ALLOCS: AtomicUsize = AtomicUsize::new(0);

unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, l: Layout) -> *mut u8 {
        ALLOCS.fetch_add(1, Ordering::Relaxed);
        unsafe { System.alloc(l) }
    }

    unsafe fn dealloc(&self, p: *mut u8, l: Layout) {
        unsafe { System.dealloc(p, l) }
    }

    unsafe fn realloc(&self, p: *mut u8, l: Layout, n: usize) -> *mut u8 {
        ALLOCS.fetch_add(1, Ordering::Relaxed);
        unsafe { System.realloc(p, l, n) }
    }
}

#[global_allocator]
static GLOBAL: Counting = Counting;

/// Gives a file of the demo mission.
///
/// # Arguments
///
/// * `name` - Its name.
///
/// # Returns
///
/// Its path.
fn mission_file(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/mission")
        .join(name)
}

#[test]
fn packets_unpack_and_pack_without_allocating() {
    let mut m =
        Mission::read_from([File::open(mission_file("demo.xtce")).unwrap()])
            .unwrap();
    common::set_algorithms(&mut m);
    let top = m.top();
    let tm = m.container(top, "/Demo/CCSDSPacket").unwrap();
    let tc = m.meta_command(top, "/Demo/CCSDSCommand").unwrap();
    let hk = m.container(top, "/Demo/Housekeeping").unwrap();
    let vectors = std::fs::read_to_string(mission_file("vectors.txt")).unwrap();
    let packets: Vec<(tcs_xtce_dynamic::NodeId, Vec<u8>, String)> = vectors
        .lines()
        .filter_map(|v| {
            let (code, note) = v.split_once('#').unwrap_or((v, ""));
            let mut w = code.split_whitespace();
            let (kind, h) = (w.next()?, w.next()?);
            let node = match kind {
                "tm" => tm,
                "tc" => tc,
                _ => hk,
            };
            let data = (0..h.len())
                .step_by(2)
                .map(|i| u8::from_str_radix(&h[i..i + 2], 16).unwrap())
                .collect();
            Some((node, data, note.trim().to_string()))
        })
        .collect();
    let mut store = m.store();
    let mut scratch = m.scratch();
    let mut out = Buffer::with_capacity(70_000);
    let mut mon = m.monitor();
    let mut t = 0.0;
    let mut clean = 0;
    let mut allocating = Vec::new();
    for (node, data, note) in &packets {
        let before = ALLOCS.load(Ordering::Relaxed);
        let Ok(got) = m.unpack(*node, data, &mut scratch) else {
            continue;
        };
        store.commit(&scratch).unwrap();
        t += 1.0;
        let mut worst = None;
        m.alarms(&scratch, |_, l| worst = worst.max(l)).unwrap();
        m.monitor_alarms(&scratch, &mut mon, t, |_, l| worst = worst.max(l))
            .unwrap();
        let mut sum = 0.0;
        m.derived(&scratch, |_, v| sum += v.unwrap_or(0.0)).unwrap();
        if m.pack(got, &store, &mut out).is_err() {
            continue;
        }
        let made = ALLOCS.load(Ordering::Relaxed) - before;
        if made == 0 {
            clean += 1;
        } else {
            allocating.push(format!("{note} ({made} allocations)"));
        }
    }
    eprintln!("no_alloc: {clean} packets allocate nothing");
    assert!(
        allocating.is_empty(),
        "these packets allocate:\n{}",
        allocating.join("\n")
    );
    assert!(clean > 120, "only {clean} packets were run");
}
