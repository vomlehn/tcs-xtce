//! The C interface to tcs-xtce-dynamic, the run-time codec: a thin layer
//! that holds the Rust codec's objects behind opaque pointers and its
//! handles as plain structs, and does nothing the Rust codec doesn't.
//!
//! Conventions:
//!
//! - A function that can fail returns an `int`: 0 for success, or a
//!   negative `tcs_dynxtce_status`; `tcs_dynxtce_last_error` then gives the
//!   message, which stays valid until the thread's next call.
//! - Text the codec gives is copied into a buffer the caller gives, as
//!   `snprintf` does: the function returns the bytes the whole text
//!   takes, without its NUL, and writes as much as fits, with a NUL.
//! - Names passed to callbacks are a pointer and a length, without a NUL.
//! - A function the application supplies takes a `void *` context first;
//!   a `free` function given with it, if not NULL, is called with the
//!   context once the codec no longer needs it.
//! - Handles (`tcs_dynxtce_space`, `tcs_dynxtce_node`, `tcs_dynxtce_slot`,
//!   `tcs_dynxtce_slots`) are plain data, checked against the mission on every
//!   call.
//! - Everything is single-threaded: a mission and what is made from it
//!   are used on the thread that made them.

// C names, as the header gives them.
#![allow(non_camel_case_types, clippy::missing_safety_doc)]

use std::cell::RefCell;
use std::ffi::{c_char, c_int, c_void, CStr, CString};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::rc::Rc;

use tcs_xtce::buffer::Buffer;
use tcs_xtce_dynamic::{
    AlarmLevel, Algorithm, Datum, Deframer, Mission, NodeId, Scratch, SlotId,
    Slots, SpaceSystemId, Store,
};

/// What a function of the interface gives back.
#[repr(C)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum tcs_dynxtce_status {
    /// It succeeded.
    Ok = 0,
    /// A document couldn't be read, or isn't XTCE the codec resolves.
    Read = -1,
    /// A name wasn't found.
    Lookup = -2,
    /// A packet didn't decode or encode.
    Packet = -3,
    /// A packet uses what the codec doesn't run yet.
    Unsupported = -4,
    /// A value can't be given to a slot.
    Set = -5,
    /// An argument is NULL, or a handle isn't the mission's.
    Argument = -6,
    /// The codec failed inside; this is a bug.
    Internal = -7,
}

/// A SpaceSystem of a mission.
#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct tcs_dynxtce_space {
    /// The mission's number.
    pub mission: u64,
    /// Its place in the mission's list.
    pub index: usize,
}

/// A container or meta-command of a mission.
#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct tcs_dynxtce_node {
    /// The mission's number.
    pub mission: u64,
    /// Its place in the mission's list.
    pub index: usize,
}

/// One scalar value of a mission.
#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct tcs_dynxtce_slot {
    /// The mission's number.
    pub mission: u64,
    /// Its place in the mission's list.
    pub index: usize,
}

/// The slots of a whole value: an aggregate's members, an array's
/// elements, or a scalar's one.
#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct tcs_dynxtce_slots {
    /// The mission's number.
    pub mission: u64,
    /// The first slot's place.
    pub first: usize,
    /// How many.
    pub count: usize,
}

/// The kind of value a `tcs_dynxtce_datum` holds.
#[repr(C)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum tcs_dynxtce_kind {
    /// An integer that an `int64_t` holds, in `i`.
    Int = 0,
    /// A wider integer, in `bytes`: two's complement, least significant
    /// byte first, `len` of them.
    Big = 1,
    /// A float, in `f`.
    Float = 2,
    /// An enumeration's value, in `i`.
    Enum = 3,
    /// A boolean, in `b`.
    Bool = 4,
    /// A string of UTF-8, in `bytes`, `len` of them, without a NUL.
    Str = 5,
    /// Binary data, in `bytes`, `len` of them.
    Binary = 6,
    /// A time in seconds, in `f`.
    Time = 7,
}

/// A value of a slot. What `bytes` points to, when it is given back, is
/// the store's, valid until the store next changes.
#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct tcs_dynxtce_datum {
    /// Which of the others holds the value.
    pub kind: tcs_dynxtce_kind,
    /// An integer or enumeration's value.
    pub i: i64,
    /// A float or time.
    pub f: f64,
    /// A boolean.
    pub b: bool,
    /// A wide integer's, string's or binary value's bytes.
    pub bytes: *const u8,
    /// How many bytes.
    pub len: usize,
}

/// A mission, which its streams keep alive while they exist.
pub struct tcs_dynxtce_mission {
    /// The mission.
    m: Rc<Mission>,
}

/// The current value of every slot of a mission.
pub struct tcs_dynxtce_store {
    /// The store.
    s: Store,
    /// Where packing puts its bytes, kept from one packing to the next.
    out: Buffer,
    /// A wide integer's bytes, as `tcs_dynxtce_store_get` gives them.
    wide: Vec<u8>,
}

/// A packet decoded and not yet committed.
pub struct tcs_dynxtce_scratch {
    /// The scratch.
    s: Scratch,
}

/// The alarm state that change ranges and minimum violations count.
pub struct tcs_dynxtce_monitor {
    /// The monitor.
    m: tcs_xtce_dynamic::Monitor,
}

/// A stream's frame synchronizer.
pub struct tcs_dynxtce_deframer {
    /// The deframer, which borrows the mission below; it is dropped
    /// first.
    d: Deframer<'static>,
    /// The mission, kept alive for as long as the deframer lives.
    _m: Rc<Mission>,
}

thread_local! {
    /// The message of the thread's last failure.
    static LAST: RefCell<CString> = RefCell::new(CString::default());
}

/// Records why a call failed.
///
/// # Arguments
///
/// * `status` - The kind of failure.
/// * `msg`    - Its message.
///
/// # Returns
///
/// The status, as an `int`.
fn fail(status: tcs_dynxtce_status, msg: impl ToString) -> c_int {
    let text = msg.to_string().replace('\0', " ");
    LAST.with(|l| *l.borrow_mut() = CString::new(text).unwrap_or_default());
    status as c_int
}

/// Runs the body of a call, turning a panic into an internal error.
///
/// # Arguments
///
/// * `f` - The body.
///
/// # Returns
///
/// What it returns, or `tcs_dynxtce_status::Internal` if it panics.
fn guard(f: impl FnOnce() -> c_int) -> c_int {
    catch_unwind(AssertUnwindSafe(f)).unwrap_or_else(|_| {
        fail(tcs_dynxtce_status::Internal, "the codec panicked")
    })
}

/// Copies text into a caller's buffer, as `snprintf` does.
///
/// # Arguments
///
/// * `s`   - The text.
/// * `buf` - The buffer, or NULL to learn only the length.
/// * `len` - Its size, NUL included.
///
/// # Returns
///
/// The bytes the text takes, without its NUL.
///
/// # Safety
///
/// `buf`, if not NULL, has room for `len` bytes.
unsafe fn copy_out(s: &str, buf: *mut c_char, len: usize) -> usize {
    if !buf.is_null() && len > 0 {
        let n = s.len().min(len - 1);
        unsafe {
            std::ptr::copy_nonoverlapping(s.as_ptr(), buf as *mut u8, n);
            *buf.add(n) = 0;
        }
    }
    s.len()
}

/// Reads a C string.
///
/// # Arguments
///
/// * `p` - The string, NUL-terminated.
///
/// # Returns
///
/// It, or `None` if it is NULL or not UTF-8.
///
/// # Safety
///
/// `p`, if not NULL, is a NUL-terminated string that outlives `'a`.
unsafe fn text<'a>(p: *const c_char) -> Option<&'a str> {
    if p.is_null() {
        return None;
    }
    unsafe { CStr::from_ptr(p) }.to_str().ok()
}

/// Makes a C slice of bytes a Rust one.
///
/// # Arguments
///
/// * `p` - The first byte, which may be NULL when `n` is 0.
/// * `n` - How many.
///
/// # Returns
///
/// The bytes.
///
/// # Safety
///
/// `p` points to `n` bytes that outlive `'a`, unless `n` is 0.
unsafe fn bytes<'a>(p: *const u8, n: usize) -> &'a [u8] {
    if n == 0 {
        &[]
    } else {
        unsafe { std::slice::from_raw_parts(p, n) }
    }
}

/// Gives the message of the thread's last failure.
///
/// # Returns
///
/// The message, valid until the thread's next call; empty if none.
#[no_mangle]
pub extern "C" fn tcs_dynxtce_last_error() -> *const c_char {
    LAST.with(|l| l.borrow().as_ptr())
}

// --- missions ----------------------------------------------------------

/// Reads and resolves a mission from documents in memory.
///
/// # Arguments
///
/// * `docs` - Each document's text.
/// * `lens` - Each one's bytes.
/// * `n`    - How many documents, in the order they build on one another.
/// * `out`  - Where the mission goes, to free with `tcs_dynxtce_mission_free`.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_READ`.
///
/// # Safety
///
/// `docs` and `lens` hold `n` entries, each document `lens[i]` bytes.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_mission_read(
    docs: *const *const u8,
    lens: *const usize,
    n: usize,
    out: *mut *mut tcs_dynxtce_mission,
) -> c_int {
    guard(|| {
        if out.is_null() || (n > 0 && (docs.is_null() || lens.is_null())) {
            return fail(tcs_dynxtce_status::Argument, "a NULL argument");
        }
        let readers: Vec<&[u8]> = (0..n)
            .map(|i| unsafe { bytes(*docs.add(i), *lens.add(i)) })
            .collect();
        match Mission::read_from(readers) {
            Ok(m) => {
                let b = Box::new(tcs_dynxtce_mission { m: Rc::new(m) });
                unsafe { *out = Box::into_raw(b) };
                0
            }
            Err(e) => fail(tcs_dynxtce_status::Read, e),
        }
    })
}

/// Frees a mission; its streams keep what they need until they are
/// freed.
///
/// # Arguments
///
/// * `m` - The mission, or NULL.
///
/// # Safety
///
/// `m` came from `tcs_dynxtce_mission_read` and isn't used again.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_mission_free(m: *mut tcs_dynxtce_mission) {
    if !m.is_null() {
        drop(unsafe { Box::from_raw(m) });
    }
}

/// Gives a mission's digest, as xtce-digest computes it.
///
/// # Arguments
///
/// * `m`   - The mission.
/// * `out` - Where its 16 bytes go.
///
/// # Safety
///
/// `out` has room for 16 bytes.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_mission_digest(
    m: *const tcs_dynxtce_mission,
    out: *mut u8,
) {
    let (Some(m), false) = (unsafe { m.as_ref() }, out.is_null()) else {
        return;
    };
    let d = m.m.digest();
    unsafe { std::ptr::copy_nonoverlapping(d.as_ptr(), out, 16) };
}

/// Gives the mission's top SpaceSystem.
///
/// # Arguments
///
/// * `m` - The mission.
///
/// # Returns
///
/// Its handle.
///
/// # Safety
///
/// `m` is a mission.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_top(
    m: *const tcs_dynxtce_mission,
) -> tcs_dynxtce_space {
    let m = unsafe { &*m };
    c_space(m.m.top())
}

/// Gives the Rust handle of a SpaceSystem.
///
/// # Arguments
///
/// * `m` - The mission.
/// * `h` - The handle.
///
/// # Returns
///
/// It, or `None` if it isn't the mission's.
fn space(m: &Mission, h: tcs_dynxtce_space) -> Option<SpaceSystemId> {
    m.space_system_from_raw(h.mission, h.index)
}

/// Gives the Rust handle of a node.
///
/// # Arguments
///
/// * `m` - The mission.
/// * `h` - The handle.
///
/// # Returns
///
/// It, or `None` if it isn't the mission's.
fn node(m: &Mission, h: tcs_dynxtce_node) -> Option<NodeId> {
    m.node_from_raw(h.mission, h.index)
}

/// Gives the Rust handle of a slot.
///
/// # Arguments
///
/// * `m` - The mission.
/// * `h` - The handle.
///
/// # Returns
///
/// It, or `None` if it isn't the mission's.
fn slot(m: &Mission, h: tcs_dynxtce_slot) -> Option<SlotId> {
    m.slot_from_raw(h.mission, h.index)
}

/// Gives a node's C handle.
///
/// # Arguments
///
/// * `n` - The Rust handle.
///
/// # Returns
///
/// The C handle.
fn c_node(n: NodeId) -> tcs_dynxtce_node {
    let (mission, index) = Mission::node_raw(n);
    tcs_dynxtce_node { mission, index }
}

/// Gives a slot's C handle.
///
/// # Arguments
///
/// * `s` - The Rust handle.
///
/// # Returns
///
/// The C handle.
fn c_slot(s: SlotId) -> tcs_dynxtce_slot {
    let (mission, index) = Mission::slot_raw(s);
    tcs_dynxtce_slot { mission, index }
}

/// Gives a run of slots' C handle.
///
/// # Arguments
///
/// * `s` - The Rust handle.
///
/// # Returns
///
/// The C handle.
fn c_slots(s: Slots) -> tcs_dynxtce_slots {
    let (mission, first, count) = Mission::slots_raw(s);
    tcs_dynxtce_slots {
        mission,
        first,
        count,
    }
}

/// Runs a lookup and writes what it finds.
///
/// # Arguments
///
/// * `out` - Where the handle goes.
/// * `f`   - The lookup.
///
/// # Returns
///
/// 0, or the failure's status.
///
/// # Safety
///
/// `out`, if not NULL, points to room for a `T`.
unsafe fn lookup<T>(
    out: *mut T,
    f: impl FnOnce() -> Result<T, String>,
) -> c_int {
    guard(|| {
        if out.is_null() {
            return fail(tcs_dynxtce_status::Argument, "a NULL argument");
        }
        match f() {
            Ok(h) => {
                unsafe { *out = h };
                0
            }
            Err(e) => fail(tcs_dynxtce_status::Lookup, e),
        }
    })
}

/// The message for a handle that isn't the mission's.
const FOREIGN: &str = "a handle of another mission, or not a handle";

/// Gives a SpaceSystem's C handle.
///
/// # Arguments
///
/// * `h` - The Rust handle.
///
/// # Returns
///
/// The C handle.
fn c_space(h: SpaceSystemId) -> tcs_dynxtce_space {
    let (mission, index) = Mission::space_system_raw(h);
    tcs_dynxtce_space { mission, index }
}

/// Looks a SpaceSystem up from a base.
///
/// # Arguments
///
/// * `m`    - The mission.
/// * `base` - The SpaceSystem the path is relative to.
/// * `path` - The path, NUL-terminated.
/// * `out`  - Where the handle goes.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_LOOKUP`.
///
/// # Safety
///
/// `m` is a mission and `path` a C string.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_space_system(
    m: *const tcs_dynxtce_mission,
    base: tcs_dynxtce_space,
    path: *const c_char,
    out: *mut tcs_dynxtce_space,
) -> c_int {
    let m = unsafe { &*m };
    let p = unsafe { text(path) };
    unsafe {
        lookup(out, || {
            let b = space(&m.m, base).ok_or(FOREIGN)?;
            let p = p.ok_or("a NULL or non-UTF-8 path")?;
            m.m.space_system(b, p)
                .map(c_space)
                .map_err(|e| e.to_string())
        })
    }
}

/// Looks a container up from a base.
///
/// # Arguments
///
/// * `m`    - The mission.
/// * `base` - The SpaceSystem the path is relative to.
/// * `path` - The path, NUL-terminated.
/// * `out`  - Where the handle goes.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_LOOKUP`.
///
/// # Safety
///
/// `m` is a mission and `path` a C string.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_container(
    m: *const tcs_dynxtce_mission,
    base: tcs_dynxtce_space,
    path: *const c_char,
    out: *mut tcs_dynxtce_node,
) -> c_int {
    let m = unsafe { &*m };
    let p = unsafe { text(path) };
    unsafe {
        lookup(out, || {
            let b = space(&m.m, base).ok_or(FOREIGN)?;
            let p = p.ok_or("a NULL or non-UTF-8 path")?;
            m.m.container(b, p).map(c_node).map_err(|e| e.to_string())
        })
    }
}

/// Looks a meta-command up from a base.
///
/// # Arguments
///
/// * `m`    - The mission.
/// * `base` - The SpaceSystem the path is relative to.
/// * `path` - The path, NUL-terminated.
/// * `out`  - Where the handle goes.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_LOOKUP`.
///
/// # Safety
///
/// `m` is a mission and `path` a C string.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_meta_command(
    m: *const tcs_dynxtce_mission,
    base: tcs_dynxtce_space,
    path: *const c_char,
    out: *mut tcs_dynxtce_node,
) -> c_int {
    let m = unsafe { &*m };
    let p = unsafe { text(path) };
    unsafe {
        lookup(out, || {
            let b = space(&m.m, base).ok_or(FOREIGN)?;
            let p = p.ok_or("a NULL or non-UTF-8 path")?;
            m.m.meta_command(b, p)
                .map(c_node)
                .map_err(|e| e.to_string())
        })
    }
}

/// Looks a parameter, or a scalar member or element of one, up from a
/// base.
///
/// # Arguments
///
/// * `m`    - The mission.
/// * `base` - The SpaceSystem the path is relative to.
/// * `path` - The path, NUL-terminated.
/// * `out`  - Where the handle goes.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_LOOKUP`.
///
/// # Safety
///
/// `m` is a mission and `path` a C string.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_parameter(
    m: *const tcs_dynxtce_mission,
    base: tcs_dynxtce_space,
    path: *const c_char,
    out: *mut tcs_dynxtce_slot,
) -> c_int {
    let m = unsafe { &*m };
    let p = unsafe { text(path) };
    unsafe {
        lookup(out, || {
            let b = space(&m.m, base).ok_or(FOREIGN)?;
            let p = p.ok_or("a NULL or non-UTF-8 path")?;
            m.m.parameter(b, p).map(c_slot).map_err(|e| e.to_string())
        })
    }
}

/// Looks the slots of a parameter, or of a member or element of one,
/// up from a base.
///
/// # Arguments
///
/// * `m`    - The mission.
/// * `base` - The SpaceSystem the path is relative to.
/// * `path` - The path, NUL-terminated.
/// * `out`  - Where the handle goes.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_LOOKUP`.
///
/// # Safety
///
/// `m` is a mission and `path` a C string.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_parameter_slots(
    m: *const tcs_dynxtce_mission,
    base: tcs_dynxtce_space,
    path: *const c_char,
    out: *mut tcs_dynxtce_slots,
) -> c_int {
    let m = unsafe { &*m };
    let p = unsafe { text(path) };
    unsafe {
        lookup(out, || {
            let b = space(&m.m, base).ok_or(FOREIGN)?;
            let p = p.ok_or("a NULL or non-UTF-8 path")?;
            m.m.parameter_slots(b, p)
                .map(c_slots)
                .map_err(|e| e.to_string())
        })
    }
}

/// Looks an argument of a meta-command, or a scalar member or element
/// of one, up.
///
/// # Arguments
///
/// * `m`    - The mission.
/// * `n`    - The container or meta-command.
/// * `name` - The name, NUL-terminated.
/// * `out`  - Where the handle goes.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_LOOKUP`.
///
/// # Safety
///
/// `m` is a mission and `name` a C string.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_argument(
    m: *const tcs_dynxtce_mission,
    n: tcs_dynxtce_node,
    name: *const c_char,
    out: *mut tcs_dynxtce_slot,
) -> c_int {
    let m = unsafe { &*m };
    let p = unsafe { text(name) };
    unsafe {
        lookup(out, || {
            let n = node(&m.m, n).ok_or(FOREIGN)?;
            let p = p.ok_or("a NULL or non-UTF-8 name")?;
            m.m.argument(n, p).map(c_slot).map_err(|e| e.to_string())
        })
    }
}

/// Looks the slots of an argument, or of a member or element of one,
/// up.
///
/// # Arguments
///
/// * `m`    - The mission.
/// * `n`    - The container or meta-command.
/// * `name` - The name, NUL-terminated.
/// * `out`  - Where the handle goes.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_LOOKUP`.
///
/// # Safety
///
/// `m` is a mission and `name` a C string.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_argument_slots(
    m: *const tcs_dynxtce_mission,
    n: tcs_dynxtce_node,
    name: *const c_char,
    out: *mut tcs_dynxtce_slots,
) -> c_int {
    let m = unsafe { &*m };
    let p = unsafe { text(name) };
    unsafe {
        lookup(out, || {
            let n = node(&m.m, n).ok_or(FOREIGN)?;
            let p = p.ok_or("a NULL or non-UTF-8 name")?;
            m.m.argument_slots(n, p)
                .map(c_slots)
                .map_err(|e| e.to_string())
        })
    }
}

/// Looks an entry of a container or meta-command, or a scalar member
/// or element of one, up.
///
/// # Arguments
///
/// * `m`    - The mission.
/// * `n`    - The container or meta-command.
/// * `name` - The name, NUL-terminated.
/// * `out`  - Where the handle goes.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_LOOKUP`.
///
/// # Safety
///
/// `m` is a mission and `name` a C string.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_entry(
    m: *const tcs_dynxtce_mission,
    n: tcs_dynxtce_node,
    name: *const c_char,
    out: *mut tcs_dynxtce_slot,
) -> c_int {
    let m = unsafe { &*m };
    let p = unsafe { text(name) };
    unsafe {
        lookup(out, || {
            let n = node(&m.m, n).ok_or(FOREIGN)?;
            let p = p.ok_or("a NULL or non-UTF-8 name")?;
            m.m.entry(n, p).map(c_slot).map_err(|e| e.to_string())
        })
    }
}

/// Looks the slots of an entry, or of a member or element of one, up.
///
/// # Arguments
///
/// * `m`    - The mission.
/// * `n`    - The container or meta-command.
/// * `name` - The name, NUL-terminated.
/// * `out`  - Where the handle goes.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_LOOKUP`.
///
/// # Safety
///
/// `m` is a mission and `name` a C string.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_entry_slots(
    m: *const tcs_dynxtce_mission,
    n: tcs_dynxtce_node,
    name: *const c_char,
    out: *mut tcs_dynxtce_slots,
) -> c_int {
    let m = unsafe { &*m };
    let p = unsafe { text(name) };
    unsafe {
        lookup(out, || {
            let n = node(&m.m, n).ok_or(FOREIGN)?;
            let p = p.ok_or("a NULL or non-UTF-8 name")?;
            m.m.entry_slots(n, p)
                .map(c_slots)
                .map_err(|e| e.to_string())
        })
    }
}

/// Gives one slot of a run.
///
/// # Arguments
///
/// * `s` - The slots.
/// * `i` - The place of the one wanted, from 0; it must be less than
///         `s.count`.
///
/// # Returns
///
/// Its handle.
#[no_mangle]
pub extern "C" fn tcs_dynxtce_slots_get(
    s: tcs_dynxtce_slots,
    i: usize,
) -> tcs_dynxtce_slot {
    tcs_dynxtce_slot {
        mission: s.mission,
        index: s.first + i,
    }
}

/// Gives the absolute path of a SpaceSystem.
///
/// # Arguments
///
/// * `m`   - The mission.
/// * `h`   - The SpaceSystem.
/// * `buf` - Where the path goes, or NULL.
/// * `len` - The buffer's size, NUL included.
///
/// # Returns
///
/// The bytes the path takes, without its NUL; 0 for another mission's
/// handle.
///
/// # Safety
///
/// `m` is a mission and `buf` has room for `len` bytes.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_path_of_space(
    m: *const tcs_dynxtce_mission,
    h: tcs_dynxtce_space,
    buf: *mut c_char,
    len: usize,
) -> usize {
    let m = unsafe { &*m };
    match space(&m.m, h) {
        Some(h) => unsafe { copy_out(&m.m.path_of_space_system(h), buf, len) },
        None => unsafe { copy_out("", buf, len) },
    }
}

/// Gives the absolute path of a container or meta-command.
///
/// # Arguments
///
/// * `m`   - The mission.
/// * `h`   - The node.
/// * `buf` - Where the path goes, or NULL.
/// * `len` - The buffer's size, NUL included.
///
/// # Returns
///
/// The bytes the path takes, without its NUL; 0 for another mission's
/// handle.
///
/// # Safety
///
/// `m` is a mission and `buf` has room for `len` bytes.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_path_of_node(
    m: *const tcs_dynxtce_mission,
    h: tcs_dynxtce_node,
    buf: *mut c_char,
    len: usize,
) -> usize {
    let m = unsafe { &*m };
    let p = node(&m.m, h).map_or("", |h| m.m.path_of_node(h));
    unsafe { copy_out(p, buf, len) }
}

/// Gives the absolute path of a slot, such as `/Demo/Probes[1].Id`.
///
/// # Arguments
///
/// * `m`   - The mission.
/// * `h`   - The slot.
/// * `buf` - Where the path goes, or NULL.
/// * `len` - The buffer's size, NUL included.
///
/// # Returns
///
/// The bytes the path takes, without its NUL; 0 for another mission's
/// handle.
///
/// # Safety
///
/// `m` is a mission and `buf` has room for `len` bytes.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_path_of_slot(
    m: *const tcs_dynxtce_mission,
    h: tcs_dynxtce_slot,
    buf: *mut c_char,
    len: usize,
) -> usize {
    let m = unsafe { &*m };
    let p = slot(&m.m, h).map_or("", |h| m.m.path_of_slot(h));
    unsafe { copy_out(p, buf, len) }
}

/// Gives the kind of a slot's type: `int`, `float`, `enum`, `bool`,
/// `string` or `binary`.
///
/// # Arguments
///
/// * `m`   - The mission.
/// * `h`   - The slot.
/// * `buf` - Where the kind goes, or NULL.
/// * `len` - The buffer's size, NUL included.
///
/// # Returns
///
/// The bytes the kind takes, without its NUL; 0 for another mission's
/// handle.
///
/// # Safety
///
/// `m` is a mission and `buf` has room for `len` bytes.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_slot_kind(
    m: *const tcs_dynxtce_mission,
    h: tcs_dynxtce_slot,
    buf: *mut c_char,
    len: usize,
) -> usize {
    let m = unsafe { &*m };
    match slot(&m.m, h) {
        Some(h) => unsafe { copy_out(m.m.slot_type(h).kind(), buf, len) },
        None => unsafe { copy_out("", buf, len) },
    }
}

/// Says whether a slot is a time, and which kind.
///
/// # Arguments
///
/// * `m` - The mission.
/// * `h` - The slot.
///
/// # Returns
///
/// 1 for an absolute time, 2 for a relative one, 0 for anything else.
///
/// # Safety
///
/// `m` is a mission.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_slot_time(
    m: *const tcs_dynxtce_mission,
    h: tcs_dynxtce_slot,
) -> c_int {
    let m = unsafe { &*m };
    match slot(&m.m, h).and_then(|h| m.m.slot_type(h).time()) {
        Some("absolute") => 1,
        Some(_) => 2,
        None => 0,
    }
}

/// Says whether a container or meta-command is abstract, so that only
/// unpacking classifies a packet as one derived from it.
///
/// # Arguments
///
/// * `m` - The mission.
/// * `h` - The node.
///
/// # Returns
///
/// Whether it is.
///
/// # Safety
///
/// `m` is a mission.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_is_abstract(
    m: *const tcs_dynxtce_mission,
    h: tcs_dynxtce_node,
) -> bool {
    let m = unsafe { &*m };
    node(&m.m, h).is_some_and(|h| m.m.is_abstract(h))
}

/// Gives the most bytes a packet of a node takes.
///
/// # Arguments
///
/// * `m` - The mission.
/// * `h` - The node.
///
/// # Returns
///
/// The bytes, or 0 if they aren't known.
///
/// # Safety
///
/// `m` is a mission.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_max_bytes(
    m: *const tcs_dynxtce_mission,
    h: tcs_dynxtce_node,
) -> usize {
    let m = unsafe { &*m };
    node(&m.m, h).and_then(|h| m.m.max_bytes(h)).unwrap_or(0)
}

/// Gives how many string and binary slots have no maximum size of their
/// own (see `tcs_dynxtce_unbounded`).
///
/// # Arguments
///
/// * `m` - The mission.
///
/// # Returns
///
/// How many.
///
/// # Safety
///
/// `m` is a mission.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_unbounded_count(
    m: *const tcs_dynxtce_mission,
) -> usize {
    unsafe { &*m }.m.unbounded().len()
}

/// Gives one of the slots with no maximum size of their own, which the
/// maximum field size bounds instead.
///
/// # Arguments
///
/// * `m` - The mission.
/// * `i` - Its place, less than `tcs_dynxtce_unbounded_count`.
///
/// # Returns
///
/// The slot.
///
/// # Safety
///
/// `m` is a mission.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_unbounded(
    m: *const tcs_dynxtce_mission,
    i: usize,
) -> tcs_dynxtce_slot {
    c_slot(unsafe { &*m }.m.unbounded()[i])
}

/// Gives what the mission's packets use that the codec doesn't run yet,
/// one line for each such container or meta-command.
///
/// # Arguments
///
/// * `m`   - The mission.
/// * `buf` - Where the lines go, each ended by a newline, or NULL.
/// * `len` - The buffer's size, NUL included.
///
/// # Returns
///
/// The bytes the lines take, without the NUL; 0 when the codec runs
/// everything.
///
/// # Safety
///
/// `m` is a mission and `buf` has room for `len` bytes.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_unsupported(
    m: *const tcs_dynxtce_mission,
    buf: *mut c_char,
    len: usize,
) -> usize {
    let lines: String = unsafe { &*m }
        .m
        .unsupported()
        .into_iter()
        .map(|l| l + "\n")
        .collect();
    unsafe { copy_out(&lines, buf, len) }
}

// --- algorithms --------------------------------------------------------

/// Gives how many algorithms the application supplies functions for.
///
/// # Arguments
///
/// * `m` - The mission.
///
/// # Returns
///
/// How many.
///
/// # Safety
///
/// `m` is a mission.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_algorithm_count(
    m: *const tcs_dynxtce_mission,
) -> usize {
    unsafe { &*m }.m.algorithms().len()
}

/// Describes one of the algorithms the application supplies functions
/// for.
///
/// # Arguments
///
/// * `m`        - The mission.
/// * `i`        - Its place, less than `tcs_dynxtce_algorithm_count`.
/// * `name`     - Where its name goes, or NULL.
/// * `name_len` - That buffer's size, NUL included.
/// * `kind`     - Where the kind of function it needs goes, such as
///                `binary_from`, or NULL.
/// * `kind_len` - That buffer's size, NUL included.
///
/// # Returns
///
/// 1 if its function is set, 0 if not, or `TCS_DYNXTCE_STATUS_ARGUMENT` for
/// a place past the last.
///
/// # Safety
///
/// `m` is a mission and the buffers have room for their sizes.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_algorithm(
    m: *const tcs_dynxtce_mission,
    i: usize,
    name: *mut c_char,
    name_len: usize,
    kind: *mut c_char,
    kind_len: usize,
) -> c_int {
    let all = unsafe { &*m }.m.algorithms();
    let Some(&(n, k, set)) = all.get(i) else {
        return fail(tcs_dynxtce_status::Argument, "no such algorithm");
    };
    unsafe {
        copy_out(n, name, name_len);
        copy_out(k, kind, kind_len);
    }
    set as c_int
}

/// A context the application gave with a function, and how to free it.
struct Context {
    /// The context.
    ctx: *mut c_void,
    /// What frees it, if anything.
    free: Option<unsafe extern "C" fn(*mut c_void)>,
}

impl Drop for Context {
    fn drop(&mut self) {
        if let Some(f) = self.free {
            unsafe { f(self.ctx) };
        }
    }
}

/// Supplies the function for an algorithm.
///
/// # Arguments
///
/// * `m`    - The mission.
/// * `name` - The algorithm's name.
/// * `f`    - The function.
///
/// # Returns
///
/// 0, `TCS_DYNXTCE_STATUS_LOOKUP` if the mission has no such algorithm of that
/// kind, or `TCS_DYNXTCE_STATUS_ARGUMENT` if a stream made from the mission
/// still exists.
///
/// # Safety
///
/// `m`, if not NULL, is a mission, and `name` a C string.
unsafe fn set(
    m: *mut tcs_dynxtce_mission,
    name: *const c_char,
    f: Algorithm,
) -> c_int {
    guard(|| {
        let (Some(m), Some(name)) =
            (unsafe { m.as_mut() }, unsafe { text(name) })
        else {
            return fail(tcs_dynxtce_status::Argument, "a NULL argument");
        };
        let Some(mm) = Rc::get_mut(&mut m.m) else {
            return fail(
                tcs_dynxtce_status::Argument,
                "the mission's streams must be freed before its algorithms \
                 are set",
            );
        };
        match mm.set_algorithm(name, f) {
            Ok(()) => 0,
            Err(e) => fail(tcs_dynxtce_status::Lookup, e),
        }
    })
}

/// A custom algorithm in a criterion: whether it holds, from its `n`
/// inputs.
pub type tcs_dynxtce_criterion_fn = unsafe extern "C" fn(
    ctx: *mut c_void,
    inputs: *const f64,
    n: usize,
) -> bool;
/// A CustomAlgorithm: its `m` outputs from its `n` inputs; false if it
/// gives none.
pub type tcs_dynxtce_outputs_fn = unsafe extern "C" fn(
    ctx: *mut c_void,
    inputs: *const f64,
    n: usize,
    outputs: *mut f64,
    m: usize,
) -> bool;
/// A custom alarm: its level, 0 (normal) to 5 (severe), from its inputs.
pub type tcs_dynxtce_alarm_fn =
    unsafe extern "C" fn(ctx: *mut c_void, inputs: *const f64, n: usize) -> u8;
/// A custom checksum of the bits from `start` to `end` of `len` bytes.
pub type tcs_dynxtce_checksum_fn = unsafe extern "C" fn(
    ctx: *mut c_void,
    data: *const u8,
    len: usize,
    start: usize,
    end: usize,
) -> u64;
/// A transform's decoding: decodes `n` bytes into `cap`, giving the bytes
/// it took and those it made (or the room it needs); false if they don't
/// decode.
pub type tcs_dynxtce_binary_from_fn = unsafe extern "C" fn(
    ctx: *mut c_void,
    data: *const u8,
    n: usize,
    out: *mut u8,
    cap: usize,
    took: *mut usize,
    made: *mut usize,
) -> bool;
/// A transform's encoding: encodes `n` bytes into `cap`, giving the bytes
/// it made (or the room it needs); false if they don't encode.
pub type tcs_dynxtce_binary_to_fn = unsafe extern "C" fn(
    ctx: *mut c_void,
    data: *const u8,
    n: usize,
    out: *mut u8,
    cap: usize,
    made: *mut usize,
) -> bool;
/// A time's decoding: the bytes it took of `n` and the seconds; false if
/// they don't decode.
pub type tcs_dynxtce_time_from_fn = unsafe extern "C" fn(
    ctx: *mut c_void,
    data: *const u8,
    n: usize,
    took: *mut usize,
    seconds: *mut f64,
) -> bool;
/// A time's encoding: the bytes it made of the seconds in `cap` (or the
/// room it needs); false if they don't encode.
pub type tcs_dynxtce_time_to_fn = unsafe extern "C" fn(
    ctx: *mut c_void,
    seconds: f64,
    out: *mut u8,
    cap: usize,
    made: *mut usize,
) -> bool;
/// What a CustomStream's decoding calls with each piece of bytes it
/// decodes.
pub type tcs_dynxtce_emit_fn =
    unsafe extern "C" fn(emit_ctx: *mut c_void, data: *const u8, n: usize);
/// A CustomStream's DecodingAlgorithm: decodes `n` bytes, calling `emit`
/// with `emit_ctx` and each piece it decodes.
pub type tcs_dynxtce_stream_decode_fn = unsafe extern "C" fn(
    ctx: *mut c_void,
    data: *const u8,
    n: usize,
    emit: tcs_dynxtce_emit_fn,
    emit_ctx: *mut c_void,
);
/// A stream's InvertAlgorithm: what a bit is once AutoInvert has inverted
/// the stream.
pub type tcs_dynxtce_bit_invert_fn =
    unsafe extern "C" fn(ctx: *mut c_void, bit: u8) -> u8;
/// Frees a context the codec no longer needs.
pub type tcs_dynxtce_free_fn = unsafe extern "C" fn(ctx: *mut c_void);

/// Wraps a context for a closure.
///
/// # Arguments
///
/// * `ctx`  - The context.
/// * `free` - What frees it.
///
/// # Returns
///
/// What the closure keeps.
fn context(
    ctx: *mut c_void,
    free: Option<unsafe extern "C" fn(*mut c_void)>,
) -> Rc<Context> {
    Rc::new(Context { ctx, free })
}

/// Supplies the function of a custom algorithm in a criterion.
///
/// # Arguments
///
/// * `m`    - The mission.
/// * `name` - The algorithm's name, as `tcs_dynxtce_algorithm` gives it.
/// * `f`    - The function.
/// * `ctx`  - What it is given first.
/// * `free` - What frees `ctx` once the codec no longer needs it, or
///            NULL.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_LOOKUP` or `TCS_DYNXTCE_STATUS_ARGUMENT`.
///
/// # Safety
///
/// `m` is a mission and `name` a C string; `f` is safe to call with
/// `ctx` until `free` is.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_set_criterion(
    m: *mut tcs_dynxtce_mission,
    name: *const c_char,
    f: tcs_dynxtce_criterion_fn,
    ctx: *mut c_void,
    free: Option<unsafe extern "C" fn(*mut c_void)>,
) -> c_int {
    let c = context(ctx, free);
    let a =
        Algorithm::criterion(move |x| unsafe { f(c.ctx, x.as_ptr(), x.len()) });
    unsafe { set(m, name, a) }
}

/// Supplies the function of a CustomAlgorithm.
///
/// # Arguments
///
/// * `m`    - The mission.
/// * `name` - The algorithm's name.
/// * `f`    - The function.
/// * `ctx`  - What it is given first.
/// * `free` - What frees `ctx`, or NULL.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_LOOKUP` or `TCS_DYNXTCE_STATUS_ARGUMENT`.
///
/// # Safety
///
/// As `tcs_dynxtce_set_criterion`'s.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_set_outputs(
    m: *mut tcs_dynxtce_mission,
    name: *const c_char,
    f: tcs_dynxtce_outputs_fn,
    ctx: *mut c_void,
    free: Option<unsafe extern "C" fn(*mut c_void)>,
) -> c_int {
    let c = context(ctx, free);
    let a = Algorithm::outputs(move |x, o| unsafe {
        f(c.ctx, x.as_ptr(), x.len(), o.as_mut_ptr(), o.len())
    });
    unsafe { set(m, name, a) }
}

/// Supplies the function of a custom alarm.
///
/// # Arguments
///
/// * `m`    - The mission.
/// * `name` - The algorithm's name.
/// * `f`    - The function.
/// * `ctx`  - What it is given first.
/// * `free` - What frees `ctx`, or NULL.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_LOOKUP` or `TCS_DYNXTCE_STATUS_ARGUMENT`.
///
/// # Safety
///
/// As `tcs_dynxtce_set_criterion`'s.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_set_alarm(
    m: *mut tcs_dynxtce_mission,
    name: *const c_char,
    f: tcs_dynxtce_alarm_fn,
    ctx: *mut c_void,
    free: Option<unsafe extern "C" fn(*mut c_void)>,
) -> c_int {
    let c = context(ctx, free);
    let a = Algorithm::alarm(move |x| unsafe { f(c.ctx, x.as_ptr(), x.len()) });
    unsafe { set(m, name, a) }
}

/// Supplies the function of a custom checksum.
///
/// # Arguments
///
/// * `m`    - The mission.
/// * `name` - The algorithm's name.
/// * `f`    - The function.
/// * `ctx`  - What it is given first.
/// * `free` - What frees `ctx`, or NULL.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_LOOKUP` or `TCS_DYNXTCE_STATUS_ARGUMENT`.
///
/// # Safety
///
/// As `tcs_dynxtce_set_criterion`'s.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_set_checksum(
    m: *mut tcs_dynxtce_mission,
    name: *const c_char,
    f: tcs_dynxtce_checksum_fn,
    ctx: *mut c_void,
    free: Option<unsafe extern "C" fn(*mut c_void)>,
) -> c_int {
    let c = context(ctx, free);
    let a = Algorithm::checksum(move |d, s, e| unsafe {
        f(c.ctx, d.as_ptr(), d.len(), s, e)
    });
    unsafe { set(m, name, a) }
}

/// Supplies the decoding function of a transform.
///
/// # Arguments
///
/// * `m`    - The mission.
/// * `name` - The algorithm's name.
/// * `f`    - The function.
/// * `ctx`  - What it is given first.
/// * `free` - What frees `ctx`, or NULL.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_LOOKUP` or `TCS_DYNXTCE_STATUS_ARGUMENT`.
///
/// # Safety
///
/// As `tcs_dynxtce_set_criterion`'s.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_set_binary_from(
    m: *mut tcs_dynxtce_mission,
    name: *const c_char,
    f: tcs_dynxtce_binary_from_fn,
    ctx: *mut c_void,
    free: Option<unsafe extern "C" fn(*mut c_void)>,
) -> c_int {
    let c = context(ctx, free);
    let a = Algorithm::binary_from(move |b, o| {
        let (mut took, mut made) = (0, 0);
        unsafe {
            f(
                c.ctx,
                b.as_ptr(),
                b.len(),
                o.as_mut_ptr(),
                o.len(),
                &mut took,
                &mut made,
            )
        }
        .then_some((took, made))
    });
    unsafe { set(m, name, a) }
}

/// Supplies the encoding function of a transform.
///
/// # Arguments
///
/// * `m`    - The mission.
/// * `name` - The algorithm's name.
/// * `f`    - The function.
/// * `ctx`  - What it is given first.
/// * `free` - What frees `ctx`, or NULL.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_LOOKUP` or `TCS_DYNXTCE_STATUS_ARGUMENT`.
///
/// # Safety
///
/// As `tcs_dynxtce_set_criterion`'s.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_set_binary_to(
    m: *mut tcs_dynxtce_mission,
    name: *const c_char,
    f: tcs_dynxtce_binary_to_fn,
    ctx: *mut c_void,
    free: Option<unsafe extern "C" fn(*mut c_void)>,
) -> c_int {
    let c = context(ctx, free);
    let a = Algorithm::binary_to(move |b, o| {
        let mut made = 0;
        unsafe {
            f(
                c.ctx,
                b.as_ptr(),
                b.len(),
                o.as_mut_ptr(),
                o.len(),
                &mut made,
            )
        }
        .then_some(made)
    });
    unsafe { set(m, name, a) }
}

/// Supplies the decoding function of a time.
///
/// # Arguments
///
/// * `m`    - The mission.
/// * `name` - The algorithm's name.
/// * `f`    - The function.
/// * `ctx`  - What it is given first.
/// * `free` - What frees `ctx`, or NULL.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_LOOKUP` or `TCS_DYNXTCE_STATUS_ARGUMENT`.
///
/// # Safety
///
/// As `tcs_dynxtce_set_criterion`'s.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_set_time_from(
    m: *mut tcs_dynxtce_mission,
    name: *const c_char,
    f: tcs_dynxtce_time_from_fn,
    ctx: *mut c_void,
    free: Option<unsafe extern "C" fn(*mut c_void)>,
) -> c_int {
    let c = context(ctx, free);
    let a = Algorithm::time_from(move |b| {
        let (mut took, mut s) = (0, 0.0);
        unsafe { f(c.ctx, b.as_ptr(), b.len(), &mut took, &mut s) }
            .then_some((took, s))
    });
    unsafe { set(m, name, a) }
}

/// Supplies the encoding function of a time.
///
/// # Arguments
///
/// * `m`    - The mission.
/// * `name` - The algorithm's name.
/// * `f`    - The function.
/// * `ctx`  - What it is given first.
/// * `free` - What frees `ctx`, or NULL.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_LOOKUP` or `TCS_DYNXTCE_STATUS_ARGUMENT`.
///
/// # Safety
///
/// As `tcs_dynxtce_set_criterion`'s.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_set_time_to(
    m: *mut tcs_dynxtce_mission,
    name: *const c_char,
    f: tcs_dynxtce_time_to_fn,
    ctx: *mut c_void,
    free: Option<unsafe extern "C" fn(*mut c_void)>,
) -> c_int {
    let c = context(ctx, free);
    let a = Algorithm::time_to(move |s, o| {
        let mut made = 0;
        unsafe { f(c.ctx, s, o.as_mut_ptr(), o.len(), &mut made) }
            .then_some(made)
    });
    unsafe { set(m, name, a) }
}

/// Calls a Rust function with a piece of bytes, for a C decoder.
///
/// # Arguments
///
/// * `emit_ctx` - The Rust function, as a pointer to a `&mut dyn FnMut`.
/// * `data`     - The bytes.
/// * `n`        - How many.
///
/// # Safety
///
/// `emit_ctx` points to a `&mut dyn FnMut(&[u8])`, and `data` to `n`
/// bytes.
unsafe extern "C" fn emit(emit_ctx: *mut c_void, data: *const u8, n: usize) {
    let f = unsafe { &mut *(emit_ctx as *mut &mut dyn FnMut(&[u8])) };
    f(unsafe { bytes(data, n) });
}

/// Supplies the function of a CustomStream's DecodingAlgorithm.
///
/// # Arguments
///
/// * `m`    - The mission.
/// * `name` - The algorithm's name.
/// * `f`    - The function.
/// * `ctx`  - What it is given first.
/// * `free` - What frees `ctx`, or NULL.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_LOOKUP` or `TCS_DYNXTCE_STATUS_ARGUMENT`.
///
/// # Safety
///
/// As `tcs_dynxtce_set_criterion`'s; `f` calls `emit` only while it runs.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_set_stream_decode(
    m: *mut tcs_dynxtce_mission,
    name: *const c_char,
    f: tcs_dynxtce_stream_decode_fn,
    ctx: *mut c_void,
    free: Option<unsafe extern "C" fn(*mut c_void)>,
) -> c_int {
    let c = context(ctx, free);
    let a = Algorithm::stream_decode(move |b, out| {
        let mut out: &mut dyn FnMut(&[u8]) = out;
        let p = &mut out as *mut &mut dyn FnMut(&[u8]) as *mut c_void;
        unsafe { f(c.ctx, b.as_ptr(), b.len(), emit, p) }
    });
    unsafe { set(m, name, a) }
}

/// Supplies the function of a stream's InvertAlgorithm.
///
/// # Arguments
///
/// * `m`    - The mission.
/// * `name` - The algorithm's name.
/// * `f`    - The function.
/// * `ctx`  - What it is given first.
/// * `free` - What frees `ctx`, or NULL.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_LOOKUP` or `TCS_DYNXTCE_STATUS_ARGUMENT`.
///
/// # Safety
///
/// As `tcs_dynxtce_set_criterion`'s.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_set_bit_invert(
    m: *mut tcs_dynxtce_mission,
    name: *const c_char,
    f: tcs_dynxtce_bit_invert_fn,
    ctx: *mut c_void,
    free: Option<unsafe extern "C" fn(*mut c_void)>,
) -> c_int {
    let c = context(ctx, free);
    let a = Algorithm::bit_invert(move |b| unsafe { f(c.ctx, b) });
    unsafe { set(m, name, a) }
}

// --- stores and scratches ----------------------------------------------

/// Makes a store for a mission's slots, none with a value.
///
/// # Arguments
///
/// * `m` - The mission.
///
/// # Returns
///
/// The store, to free with `tcs_dynxtce_store_free`.
///
/// # Safety
///
/// `m` is a mission.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_store_new(
    m: *const tcs_dynxtce_mission,
) -> *mut tcs_dynxtce_store {
    let m = unsafe { &*m };
    Box::into_raw(Box::new(tcs_dynxtce_store {
        s: m.m.store(),
        out: Buffer::with_capacity(4096),
        wide: Vec::new(),
    }))
}

/// Frees a store.
///
/// # Arguments
///
/// * `s` - The store, or NULL.
///
/// # Safety
///
/// `s` came from `tcs_dynxtce_store_new` and isn't used again.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_store_free(s: *mut tcs_dynxtce_store) {
    if !s.is_null() {
        drop(unsafe { Box::from_raw(s) });
    }
}

/// Makes a scratch to unpack a mission's packets into.
///
/// # Arguments
///
/// * `m` - The mission.
///
/// # Returns
///
/// The scratch, to free with `tcs_dynxtce_scratch_free`.
///
/// # Safety
///
/// `m` is a mission.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_scratch_new(
    m: *const tcs_dynxtce_mission,
) -> *mut tcs_dynxtce_scratch {
    let m = unsafe { &*m };
    Box::into_raw(Box::new(tcs_dynxtce_scratch { s: m.m.scratch() }))
}

/// Frees a scratch.
///
/// # Arguments
///
/// * `s` - The scratch, or NULL.
///
/// # Safety
///
/// `s` came from `tcs_dynxtce_scratch_new` and isn't used again.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_scratch_free(s: *mut tcs_dynxtce_scratch) {
    if !s.is_null() {
        drop(unsafe { Box::from_raw(s) });
    }
}

/// Gives the status of a failure to pack or unpack.
///
/// # Arguments
///
/// * `e` - The failure.
///
/// # Returns
///
/// Its status, as an `int`.
fn run_error(e: tcs_xtce_dynamic::RunError) -> c_int {
    use tcs_xtce_dynamic::RunError as E;
    let status = match &e {
        E::Packet(_) => tcs_dynxtce_status::Packet,
        E::Unsupported(_) => tcs_dynxtce_status::Unsupported,
        E::Foreign => tcs_dynxtce_status::Argument,
    };
    fail(status, e)
}

/// Unpacks a packet into a scratch; for an abstract node, the packet is
/// classified as the first derived from it whose criteria match.
///
/// # Arguments
///
/// * `m`       - The mission.
/// * `n`       - The container or meta-command.
/// * `data`    - The packet.
/// * `len`     - Its bytes.
/// * `scratch` - Where its values go.
/// * `out`     - Where the concrete node it is goes, or NULL.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_PACKET`, `TCS_DYNXTCE_STATUS_UNSUPPORTED` or
/// `TCS_DYNXTCE_STATUS_ARGUMENT`.
///
/// # Safety
///
/// `m` is a mission, `scratch` one of its scratches, and `data` has `len`
/// bytes.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_unpack(
    m: *const tcs_dynxtce_mission,
    n: tcs_dynxtce_node,
    data: *const u8,
    len: usize,
    scratch: *mut tcs_dynxtce_scratch,
    out: *mut tcs_dynxtce_node,
) -> c_int {
    guard(|| {
        let (m, Some(s)) = (unsafe { &*m }, unsafe { scratch.as_mut() }) else {
            return fail(tcs_dynxtce_status::Argument, "a NULL scratch");
        };
        let Some(n) = node(&m.m, n) else {
            return fail(tcs_dynxtce_status::Argument, FOREIGN);
        };
        match m.m.unpack(n, unsafe { bytes(data, len) }, &mut s.s) {
            Ok(got) => {
                if !out.is_null() {
                    unsafe { *out = c_node(got) };
                }
                0
            }
            Err(e) => run_error(e),
        }
    })
}

/// Makes the values a scratch holds a store's current ones.
///
/// # Arguments
///
/// * `s`       - The store.
/// * `scratch` - What unpacking gave.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_ARGUMENT` for another mission's scratch.
///
/// # Safety
///
/// `s` is a store and `scratch` a scratch.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_commit(
    s: *mut tcs_dynxtce_store,
    scratch: *const tcs_dynxtce_scratch,
) -> c_int {
    guard(|| {
        let (Some(s), Some(sc)) =
            (unsafe { s.as_mut() }, unsafe { scratch.as_ref() })
        else {
            return fail(tcs_dynxtce_status::Argument, "a NULL argument");
        };
        match s.s.commit(&sc.s) {
            Ok(()) => 0,
            Err(e) => fail(tcs_dynxtce_status::Argument, e),
        }
    })
}

/// Packs a packet of a concrete node from a store's current values.
///
/// # Arguments
///
/// * `m`   - The mission.
/// * `n`   - The container or meta-command.
/// * `s`   - The store; its bytes are kept in it.
/// * `out` - Where a pointer to the bytes goes, valid until the store
///           packs again or is freed.
/// * `len` - Where how many bytes goes.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_PACKET`, `TCS_DYNXTCE_STATUS_UNSUPPORTED` or
/// `TCS_DYNXTCE_STATUS_ARGUMENT`.
///
/// # Safety
///
/// `m` is a mission and `s` one of its stores.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_pack(
    m: *const tcs_dynxtce_mission,
    n: tcs_dynxtce_node,
    s: *mut tcs_dynxtce_store,
    out: *mut *const u8,
    len: *mut usize,
) -> c_int {
    guard(|| {
        let (m, Some(s)) = (unsafe { &*m }, unsafe { s.as_mut() }) else {
            return fail(tcs_dynxtce_status::Argument, "a NULL store");
        };
        if out.is_null() || len.is_null() {
            return fail(tcs_dynxtce_status::Argument, "a NULL argument");
        }
        let Some(n) = node(&m.m, n) else {
            return fail(tcs_dynxtce_status::Argument, FOREIGN);
        };
        match m.m.pack(n, &s.s, &mut s.out) {
            Ok(b) => {
                unsafe {
                    *out = b.as_ptr();
                    *len = b.len();
                }
                0
            }
            Err(e) => run_error(e),
        }
    })
}

/// Writes a datum for C.
///
/// # Arguments
///
/// * `d`    - The datum.
/// * `wide` - Where a wide integer's bytes are kept.
///
/// # Returns
///
/// The C datum.
fn c_datum(d: Datum<'_>, wide: &mut Vec<u8>) -> tcs_dynxtce_datum {
    let mut c = tcs_dynxtce_datum {
        kind: tcs_dynxtce_kind::Int,
        i: 0,
        f: 0.0,
        b: false,
        bytes: std::ptr::null(),
        len: 0,
    };
    let mut big = |b: &[u8]| {
        wide.clear();
        wide.extend_from_slice(b);
    };
    match d {
        Datum::Int(i) => match i64::try_from(i) {
            Ok(i) => c.i = i,
            Err(_) => {
                big(&i.to_le_bytes());
                c.kind = tcs_dynxtce_kind::Big;
            }
        },
        Datum::Big(w) => {
            big(&w.to_le_bytes());
            c.kind = tcs_dynxtce_kind::Big;
        }
        Datum::Float(x) => {
            c.kind = tcs_dynxtce_kind::Float;
            c.f = x;
        }
        Datum::Time(x) => {
            c.kind = tcs_dynxtce_kind::Time;
            c.f = x;
        }
        Datum::Enum(i) => {
            c.kind = tcs_dynxtce_kind::Enum;
            c.i = i as i64;
        }
        Datum::Bool(b) => {
            c.kind = tcs_dynxtce_kind::Bool;
            c.b = b;
        }
        Datum::Str(s) => {
            c.kind = tcs_dynxtce_kind::Str;
            c.bytes = s.as_ptr();
            c.len = s.len();
        }
        Datum::Binary(b) => {
            c.kind = tcs_dynxtce_kind::Binary;
            c.bytes = b.as_ptr();
            c.len = b.len();
        }
    }
    if c.kind == tcs_dynxtce_kind::Big {
        c.bytes = wide.as_ptr();
        c.len = wide.len();
    }
    c
}

/// Gives a slot's current value, or its raw value.
///
/// # Arguments
///
/// * `m`   - The mission.
/// * `s`   - The store.
/// * `h`   - The slot.
/// * `raw` - Whether to give the raw value.
/// * `out` - Where the value goes.
///
/// # Returns
///
/// 1 if it has a value, 0 if not, or `TCS_DYNXTCE_STATUS_ARGUMENT`.
///
/// # Safety
///
/// `m` is a mission, `s` one of its stores, and `out` room for a datum.
unsafe fn get(
    m: *const tcs_dynxtce_mission,
    s: *mut tcs_dynxtce_store,
    h: tcs_dynxtce_slot,
    raw: bool,
    out: *mut tcs_dynxtce_datum,
) -> c_int {
    guard(|| {
        let (m, Some(s)) = (unsafe { &*m }, unsafe { s.as_mut() }) else {
            return fail(tcs_dynxtce_status::Argument, "a NULL store");
        };
        let (Some(h), false) = (slot(&m.m, h), out.is_null()) else {
            return fail(tcs_dynxtce_status::Argument, FOREIGN);
        };
        let d = if raw { s.s.get_raw(h) } else { s.s.get(h) };
        match d {
            Some(d) => {
                unsafe { *out = c_datum(d, &mut s.wide) };
                1
            }
            None => 0,
        }
    })
}

/// Gives a slot's current value.
///
/// # Arguments
///
/// * `m`   - The mission.
/// * `s`   - The store.
/// * `h`   - The slot.
/// * `out` - Where the value goes; its bytes are the store's, valid until
///           the store next changes.
///
/// # Returns
///
/// 1 if it has a value, 0 if not, or `TCS_DYNXTCE_STATUS_ARGUMENT`.
///
/// # Safety
///
/// `m` is a mission and `s` one of its stores.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_store_get(
    m: *const tcs_dynxtce_mission,
    s: *mut tcs_dynxtce_store,
    h: tcs_dynxtce_slot,
    out: *mut tcs_dynxtce_datum,
) -> c_int {
    unsafe { get(m, s, h, false, out) }
}

/// Gives a slot's raw value: before its calibrator, for a calibrated
/// slot.
///
/// # Arguments
///
/// * `m`   - The mission.
/// * `s`   - The store.
/// * `h`   - The slot.
/// * `out` - Where the value goes.
///
/// # Returns
///
/// 1 if it has a value, 0 if not, or `TCS_DYNXTCE_STATUS_ARGUMENT`.
///
/// # Safety
///
/// `m` is a mission and `s` one of its stores.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_store_get_raw(
    m: *const tcs_dynxtce_mission,
    s: *mut tcs_dynxtce_store,
    h: tcs_dynxtce_slot,
    out: *mut tcs_dynxtce_datum,
) -> c_int {
    unsafe { get(m, s, h, true, out) }
}

/// Gives a slot a value, or a raw value.
///
/// # Arguments
///
/// * `m`   - The mission.
/// * `s`   - The store.
/// * `h`   - The slot.
/// * `raw` - Whether the value is raw.
/// * `d`   - The value.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_SET` or `TCS_DYNXTCE_STATUS_ARGUMENT`.
///
/// # Safety
///
/// `m` is a mission, `s` one of its stores, and `d` a datum whose bytes,
/// if it has any, are `len` long.
unsafe fn put(
    m: *const tcs_dynxtce_mission,
    s: *mut tcs_dynxtce_store,
    h: tcs_dynxtce_slot,
    raw: bool,
    d: *const tcs_dynxtce_datum,
) -> c_int {
    guard(|| {
        let (m, Some(s), Some(d)) =
            (unsafe { &*m }, unsafe { s.as_mut() }, unsafe { d.as_ref() })
        else {
            return fail(tcs_dynxtce_status::Argument, "a NULL argument");
        };
        let Some(h) = slot(&m.m, h) else {
            return fail(tcs_dynxtce_status::Argument, FOREIGN);
        };
        let b = unsafe { bytes(d.bytes, d.len) };
        let wide;
        let datum = match d.kind {
            tcs_dynxtce_kind::Int => Datum::Int(d.i as i128),
            tcs_dynxtce_kind::Enum => Datum::Enum(d.i as i128),
            tcs_dynxtce_kind::Float => Datum::Float(d.f),
            tcs_dynxtce_kind::Time => Datum::Time(d.f),
            tcs_dynxtce_kind::Bool => Datum::Bool(d.b),
            tcs_dynxtce_kind::Binary => Datum::Binary(b),
            tcs_dynxtce_kind::Str => match std::str::from_utf8(b) {
                Ok(t) => Datum::Str(t),
                Err(_) => {
                    return fail(
                        tcs_dynxtce_status::Set,
                        "a string not of UTF-8",
                    )
                }
            },
            tcs_dynxtce_kind::Big => match tcs_xtce_dynamic::from_le_bytes(b) {
                Some(w) => {
                    wide = w;
                    Datum::Big(&wide)
                }
                None => {
                    return fail(tcs_dynxtce_status::Set, "an integer too wide")
                }
            },
        };
        let r = if raw {
            s.s.set_raw(h, datum)
        } else {
            s.s.set(h, datum)
        };
        match r {
            Ok(()) => 0,
            Err(e) => fail(tcs_dynxtce_status::Set, e),
        }
    })
}

/// Gives a slot a value, checked against its type.
///
/// # Arguments
///
/// * `m` - The mission.
/// * `s` - The store.
/// * `h` - The slot.
/// * `d` - The value.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_SET` or `TCS_DYNXTCE_STATUS_ARGUMENT`.
///
/// # Safety
///
/// `m` is a mission, `s` one of its stores, and `d` a datum whose bytes,
/// if it has any, are `len` long.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_store_set(
    m: *const tcs_dynxtce_mission,
    s: *mut tcs_dynxtce_store,
    h: tcs_dynxtce_slot,
    d: *const tcs_dynxtce_datum,
) -> c_int {
    unsafe { put(m, s, h, false, d) }
}

/// Gives a slot a raw value, which packing writes as it is.
///
/// # Arguments
///
/// * `m` - The mission.
/// * `s` - The store.
/// * `h` - The slot.
/// * `d` - The raw value.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_SET` or `TCS_DYNXTCE_STATUS_ARGUMENT`.
///
/// # Safety
///
/// As `tcs_dynxtce_store_set`'s.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_store_set_raw(
    m: *const tcs_dynxtce_mission,
    s: *mut tcs_dynxtce_store,
    h: tcs_dynxtce_slot,
    d: *const tcs_dynxtce_datum,
) -> c_int {
    unsafe { put(m, s, h, true, d) }
}

/// Takes a slot's value away.
///
/// # Arguments
///
/// * `m` - The mission.
/// * `s` - The store.
/// * `h` - The slot.
///
/// # Safety
///
/// `m` is a mission and `s` one of its stores.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_store_clear(
    m: *const tcs_dynxtce_mission,
    s: *mut tcs_dynxtce_store,
    h: tcs_dynxtce_slot,
) {
    let (m, Some(s)) = (unsafe { &*m }, unsafe { s.as_mut() }) else {
        return;
    };
    if let Some(h) = slot(&m.m, h) {
        s.s.clear(h);
    }
}

// --- alarms and algorithms ---------------------------------------------

/// Called with each alarmed field's name, its length, and its level: 0
/// (normal) to 5 (severe), or -1 when the field is absent.
pub type tcs_dynxtce_level_fn = unsafe extern "C" fn(
    ctx: *mut c_void,
    name: *const c_char,
    len: usize,
    level: c_int,
);
/// Called with each output's name, its length, whether it has a value,
/// and the value.
pub type tcs_dynxtce_output_fn = unsafe extern "C" fn(
    ctx: *mut c_void,
    name: *const c_char,
    len: usize,
    present: bool,
    value: f64,
);

/// Gives a level as C does.
///
/// # Arguments
///
/// * `l` - The level.
///
/// # Returns
///
/// 0 to 5, or -1 for none.
fn level(l: Option<AlarmLevel>) -> c_int {
    l.map_or(-1, |l| l as c_int)
}

/// Gives the alarm levels of the fields of the packet last unpacked into
/// a scratch, on that packet alone.
///
/// # Arguments
///
/// * `m`       - The mission.
/// * `scratch` - What unpacking gave.
/// * `f`       - Called with each field's level.
/// * `ctx`     - What `f` is given first.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_ARGUMENT`.
///
/// # Safety
///
/// `m` is a mission and `scratch` one of its scratches.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_alarms(
    m: *const tcs_dynxtce_mission,
    scratch: *const tcs_dynxtce_scratch,
    f: tcs_dynxtce_level_fn,
    ctx: *mut c_void,
) -> c_int {
    guard(|| {
        let (m, Some(sc)) = (unsafe { &*m }, unsafe { scratch.as_ref() })
        else {
            return fail(tcs_dynxtce_status::Argument, "a NULL scratch");
        };
        let r = m.m.alarms(&sc.s, |n, l| unsafe {
            f(ctx, n.as_ptr() as *const c_char, n.len(), level(l))
        });
        match r {
            Ok(()) => 0,
            Err(e) => run_error(e),
        }
    })
}

/// Makes an alarm monitor with no samples.
///
/// # Arguments
///
/// * `m` - The mission.
///
/// # Returns
///
/// The monitor, to free with `tcs_dynxtce_monitor_free`.
///
/// # Safety
///
/// `m` is a mission.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_monitor_new(
    m: *const tcs_dynxtce_mission,
) -> *mut tcs_dynxtce_monitor {
    Box::into_raw(Box::new(tcs_dynxtce_monitor {
        m: unsafe { &*m }.m.monitor(),
    }))
}

/// Frees an alarm monitor.
///
/// # Arguments
///
/// * `mon` - The monitor, or NULL.
///
/// # Safety
///
/// `mon` came from `tcs_dynxtce_monitor_new` and isn't used again.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_monitor_free(
    mon: *mut tcs_dynxtce_monitor,
) {
    if !mon.is_null() {
        drop(unsafe { Box::from_raw(mon) });
    }
}

/// Gives the alarm levels of the fields of the packet last unpacked into
/// a scratch, counting the earlier samples a monitor keeps.
///
/// # Arguments
///
/// * `m`       - The mission.
/// * `scratch` - What unpacking gave.
/// * `mon`     - The alarm monitor.
/// * `t`       - The packet's time, in seconds.
/// * `f`       - Called with each field's level.
/// * `ctx`     - What `f` is given first.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_ARGUMENT`.
///
/// # Safety
///
/// `m` is a mission, and `scratch` and `mon` are its.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_monitor_alarms(
    m: *const tcs_dynxtce_mission,
    scratch: *const tcs_dynxtce_scratch,
    mon: *mut tcs_dynxtce_monitor,
    t: f64,
    f: tcs_dynxtce_level_fn,
    ctx: *mut c_void,
) -> c_int {
    guard(|| {
        let (m, Some(sc), Some(mon)) =
            (unsafe { &*m }, unsafe { scratch.as_ref() }, unsafe {
                mon.as_mut()
            })
        else {
            return fail(tcs_dynxtce_status::Argument, "a NULL argument");
        };
        let r = m.m.monitor_alarms(&sc.s, &mut mon.m, t, |n, l| unsafe {
            f(ctx, n.as_ptr() as *const c_char, n.len(), level(l))
        });
        match r {
            Ok(()) => 0,
            Err(e) => run_error(e),
        }
    })
}

/// Gives the outputs of the MathAlgorithms and CustomAlgorithms the
/// packet last unpacked into a scratch triggers.
///
/// # Arguments
///
/// * `m`       - The mission.
/// * `scratch` - What unpacking gave.
/// * `f`       - Called with each output.
/// * `ctx`     - What `f` is given first.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_ARGUMENT`.
///
/// # Safety
///
/// `m` is a mission and `scratch` one of its scratches.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_derived(
    m: *const tcs_dynxtce_mission,
    scratch: *const tcs_dynxtce_scratch,
    f: tcs_dynxtce_output_fn,
    ctx: *mut c_void,
) -> c_int {
    guard(|| {
        let (m, Some(sc)) = (unsafe { &*m }, unsafe { scratch.as_ref() })
        else {
            return fail(tcs_dynxtce_status::Argument, "a NULL scratch");
        };
        let r = m.m.derived(&sc.s, |n, v| unsafe {
            f(
                ctx,
                n.as_ptr() as *const c_char,
                n.len(),
                v.is_some(),
                v.unwrap_or(0.0),
            )
        });
        match r {
            Ok(()) => 0,
            Err(e) => run_error(e),
        }
    })
}

// --- streams -----------------------------------------------------------

/// Gives how many streams a mission has.
///
/// # Arguments
///
/// * `m` - The mission.
///
/// # Returns
///
/// How many.
///
/// # Safety
///
/// `m` is a mission.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_stream_count(
    m: *const tcs_dynxtce_mission,
) -> usize {
    unsafe { &*m }.m.streams().len()
}

/// Gives the qualified name of one of a mission's streams.
///
/// # Arguments
///
/// * `m`   - The mission.
/// * `i`   - Its place, less than `tcs_dynxtce_stream_count`.
/// * `buf` - Where the name goes, or NULL.
/// * `len` - The buffer's size, NUL included.
///
/// # Returns
///
/// The bytes the name takes, without its NUL.
///
/// # Safety
///
/// `m` is a mission and `buf` has room for `len` bytes.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_stream_name(
    m: *const tcs_dynxtce_mission,
    i: usize,
    buf: *mut c_char,
    len: usize,
) -> usize {
    let names = unsafe { &*m }.m.streams();
    unsafe { copy_out(names.get(i).map_or("", |n| n.as_str()), buf, len) }
}

/// Makes a stream's frame synchronizer; it keeps the mission alive until
/// it is freed.
///
/// # Arguments
///
/// * `m`    - The mission.
/// * `path` - The stream's qualified name.
/// * `out`  - Where the stream goes, to free with `tcs_dynxtce_deframer_free`.
///
/// # Returns
///
/// 0, or `TCS_DYNXTCE_STATUS_LOOKUP`.
///
/// # Safety
///
/// `m` is a mission and `path` a C string.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_stream(
    m: *const tcs_dynxtce_mission,
    path: *const c_char,
    out: *mut *mut tcs_dynxtce_deframer,
) -> c_int {
    guard(|| {
        let (m, Some(p)) = (unsafe { &*m }, unsafe { text(path) }) else {
            return fail(tcs_dynxtce_status::Argument, "a NULL path");
        };
        if out.is_null() {
            return fail(tcs_dynxtce_status::Argument, "a NULL argument");
        }
        match m.m.stream(p) {
            Ok(d) => {
                // The mission outlives the deframer: the deframer is
                // dropped first, and the Rc it is boxed with keeps the
                // mission alive until then.
                let d: Deframer<'static> = unsafe { std::mem::transmute(d) };
                let b = Box::new(tcs_dynxtce_deframer { d, _m: m.m.clone() });
                unsafe { *out = Box::into_raw(b) };
                0
            }
            Err(e) => fail(tcs_dynxtce_status::Lookup, e),
        }
    })
}

/// Frees a stream's frame synchronizer.
///
/// # Arguments
///
/// * `d` - The stream, or NULL.
///
/// # Safety
///
/// `d` came from `tcs_dynxtce_stream` and isn't used again.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_deframer_free(
    d: *mut tcs_dynxtce_deframer,
) {
    if !d.is_null() {
        drop(unsafe { Box::from_raw(d) });
    }
}

/// Called with each frame a stream completes.
pub type tcs_dynxtce_frame_fn =
    unsafe extern "C" fn(ctx: *mut c_void, frame: *const u8, len: usize);

/// Takes a stream's next bytes, calling a function with each frame they
/// complete.
///
/// # Arguments
///
/// * `d`    - The stream.
/// * `data` - The bytes, in the order they arrive.
/// * `len`  - How many.
/// * `f`    - Called with each frame.
/// * `ctx`  - What `f` is given first.
///
/// # Safety
///
/// `d` is a stream and `data` has `len` bytes.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_deframer_push(
    d: *mut tcs_dynxtce_deframer,
    data: *const u8,
    len: usize,
    f: tcs_dynxtce_frame_fn,
    ctx: *mut c_void,
) {
    let Some(d) = (unsafe { d.as_mut() }) else {
        return;
    };
    d.d.push(unsafe { bytes(data, len) }, |fr| unsafe {
        f(ctx, fr.as_ptr(), fr.len())
    });
}

/// Gives the container each frame of a stream is.
///
/// # Arguments
///
/// * `m`    - The mission.
/// * `path` - The stream's qualified name.
/// * `out`  - Where the container goes.
///
/// # Returns
///
/// 1 if its frames are a container's, 0 for a CustomStream, or
/// `TCS_DYNXTCE_STATUS_LOOKUP`.
///
/// # Safety
///
/// `m` is a mission and `path` a C string.
#[no_mangle]
pub unsafe extern "C" fn tcs_dynxtce_stream_container(
    m: *const tcs_dynxtce_mission,
    path: *const c_char,
    out: *mut tcs_dynxtce_node,
) -> c_int {
    guard(|| {
        let (m, Some(p)) = (unsafe { &*m }, unsafe { text(path) }) else {
            return fail(tcs_dynxtce_status::Argument, "a NULL path");
        };
        match m.m.stream_container(p) {
            Ok(Some(n)) => {
                if !out.is_null() {
                    unsafe { *out = c_node(n) };
                }
                1
            }
            Ok(None) => 0,
            Err(e) => fail(tcs_dynxtce_status::Lookup, e),
        }
    })
}
