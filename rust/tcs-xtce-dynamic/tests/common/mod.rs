//! What the codec's tests share: the demo mission's custom algorithms,
//! the same as the generated codecs' tests supply in
//! `examples/packet_tool.rs`.

use tcs_xtce_dynamic::{Algorithm, Mission};

/// Gives seconds as whole milliseconds.
///
/// # Arguments
///
/// * `s` - The seconds.
///
/// # Returns
///
/// The milliseconds, or `None` outside 0 to 2^32 - 1.
fn ms(s: f64) -> Option<u32> {
    let v = (s * 1000.0 + 0.5).floor();
    (v >= 0.0 && v < 4294967296.0).then_some(v as u32)
}

/// Gives whether the first input is even, IsEven's criterion.
///
/// # Arguments
///
/// * `v` - The inputs.
///
/// # Returns
///
/// Whether it is.
fn is_even(v: &[f64]) -> bool {
    v[0] % 2.0 == 0.0
}

/// Gives the least and greatest inputs, MinMax's outputs.
///
/// # Arguments
///
/// * `v`   - The inputs.
/// * `out` - Where the least and greatest go.
///
/// # Returns
///
/// True: it always gives them.
fn min_max(v: &[f64], out: &mut [f64]) -> bool {
    out[0] = v.iter().copied().fold(f64::INFINITY, f64::min);
    out[1] = v.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    true
}

/// Gives the alarm level of the first input, in bands of 50, Bands's.
///
/// # Arguments
///
/// * `v` - The inputs.
///
/// # Returns
///
/// The level.
fn bands(v: &[f64]) -> u8 {
    (v[0] / 50.0) as u8
}

/// Gives twice the sum of the bytes covered, Twice's checksum.
///
/// # Arguments
///
/// * `d`     - The data.
/// * `start` - The first bit covered.
/// * `end`   - The bit after the last.
///
/// # Returns
///
/// The checksum.
fn twice(d: &[u8], start: usize, end: usize) -> u64 {
    2 * d[start / 8..end / 8]
        .iter()
        .map(|&b| u64::from(b))
        .sum::<u64>()
}

/// Doubles every byte, Expand's transform.
///
/// # Arguments
///
/// * `b`   - The bytes.
/// * `out` - Where the result goes.
///
/// # Returns
///
/// The bytes it makes, or the room it needs.
fn expand(b: &[u8], out: &mut [u8]) -> Option<usize> {
    if 2 * b.len() > out.len() {
        return Some(2 * b.len());
    }
    for (i, &x) in b.iter().enumerate() {
        out[2 * i] = x;
        out[2 * i + 1] = x;
    }
    Some(2 * b.len())
}

/// Takes every other byte, Shrink's transform; nothing when a pair
/// differs.
///
/// # Arguments
///
/// * `b`   - The bytes.
/// * `out` - Where the result goes.
///
/// # Returns
///
/// The bytes it took and those it made, or the room it needs.
fn shrink(b: &[u8], out: &mut [u8]) -> Option<(usize, usize)> {
    if b.len() % 2 != 0 || b.chunks(2).any(|p| p[0] != p[1]) {
        return None;
    }
    if b.len() / 2 > out.len() {
        return Some((b.len(), b.len() / 2));
    }
    for (o, p) in out.iter_mut().zip(b.chunks(2)) {
        *o = p[0];
    }
    Some((b.len(), b.len() / 2))
}

/// XORs every byte with 0x5A, Mask's transform.
///
/// # Arguments
///
/// * `b`   - The bytes.
/// * `out` - Where the result goes.
///
/// # Returns
///
/// The bytes it makes.
fn mask(b: &[u8], out: &mut [u8]) -> Option<usize> {
    for (o, &x) in out.iter_mut().zip(b) {
        *o = x ^ 0x5A;
    }
    Some(b.len())
}

/// XORs every byte with 0x5A, Unmask's transform.
///
/// # Arguments
///
/// * `b`   - The bytes.
/// * `out` - Where the result goes.
///
/// # Returns
///
/// The bytes it took and those it made.
fn unmask(b: &[u8], out: &mut [u8]) -> Option<(usize, usize)> {
    for (o, &x) in out.iter_mut().zip(b) {
        *o = x ^ 0x5A;
    }
    Some((b.len(), b.len()))
}

/// Frames bytes with their length, Stuff's transform.
///
/// # Arguments
///
/// * `b`   - The bytes.
/// * `out` - Where the result goes.
///
/// # Returns
///
/// The bytes it makes, or the room it needs; nothing for more than 255.
fn stuff(b: &[u8], out: &mut [u8]) -> Option<usize> {
    if b.len() > 255 {
        return None;
    }
    if b.len() + 1 > out.len() {
        return Some(b.len() + 1);
    }
    out[0] = b.len() as u8;
    out[1..=b.len()].copy_from_slice(b);
    Some(b.len() + 1)
}

/// Takes bytes framed with their length, Unstuff's transform.
///
/// # Arguments
///
/// * `b`   - The bytes.
/// * `out` - Where the result goes.
///
/// # Returns
///
/// The bytes it took and those it made, or the room it needs.
fn unstuff(b: &[u8], out: &mut [u8]) -> Option<(usize, usize)> {
    let n = usize::from(*b.first()?);
    if b.len() <= n {
        return None;
    }
    if n > out.len() {
        return Some((n + 1, n));
    }
    out[..n].copy_from_slice(&b[1..=n]);
    Some((n + 1, n))
}

/// Reads milliseconds in four bytes, MsFrom's.
///
/// # Arguments
///
/// * `b` - The bytes.
///
/// # Returns
///
/// The bytes it took and the seconds.
fn ms_from(b: &[u8]) -> Option<(usize, f64)> {
    let v = u32::from_be_bytes(b.get(..4)?.try_into().ok()?);
    Some((4, f64::from(v) / 1000.0))
}

/// Writes milliseconds in four bytes, MsTo's.
///
/// # Arguments
///
/// * `s`   - The seconds.
/// * `out` - Where the bytes go.
///
/// # Returns
///
/// The bytes it makes, or the room it needs.
fn ms_to(s: f64, out: &mut [u8]) -> Option<usize> {
    let v = ms(s)?;
    if out.len() < 4 {
        return Some(4);
    }
    out[..4].copy_from_slice(&v.to_be_bytes());
    Some(4)
}

/// Reads milliseconds in a varint, VarFrom's.
///
/// # Arguments
///
/// * `b` - The bytes.
///
/// # Returns
///
/// The bytes it took and the seconds.
fn var_from(b: &[u8]) -> Option<(usize, f64)> {
    let mut v = 0u64;
    for (i, &x) in b.iter().take(5).enumerate() {
        v |= u64::from(x & 0x7F) << (7 * i);
        if x & 0x80 == 0 {
            return Some((i + 1, v as f64 / 1000.0));
        }
    }
    None
}

/// Writes milliseconds in a varint, VarTo's.
///
/// # Arguments
///
/// * `s`   - The seconds.
/// * `out` - Where the bytes go.
///
/// # Returns
///
/// The bytes it makes, or the room it needs.
fn var_to(s: f64, out: &mut [u8]) -> Option<usize> {
    let mut v = ms(s)?;
    let mut n = 0;
    let mut buf = [0u8; 5];
    while v >= 0x80 {
        buf[n] = (v & 0x7F) as u8 | 0x80;
        v >>= 7;
        n += 1;
    }
    buf[n] = v as u8;
    n += 1;
    if n > out.len() {
        return Some(n);
    }
    out[..n].copy_from_slice(&buf[..n]);
    Some(n)
}

/// Supplies the demo mission's custom algorithms.
///
/// # Arguments
///
/// * `m` - The demo mission.
pub fn set_algorithms(m: &mut Mission) {
    let all = [
        ("/Demo/IsEven", Algorithm::Criterion(is_even)),
        ("/Demo/MinMax", Algorithm::Outputs(min_max)),
        ("/Demo/Bands", Algorithm::Alarm(bands)),
        ("/Demo/Twice", Algorithm::Checksum(twice)),
        ("Expand", Algorithm::BinaryTo(expand)),
        ("Shrink", Algorithm::BinaryFrom(shrink)),
        ("Mask", Algorithm::BinaryTo(mask)),
        ("Unmask", Algorithm::BinaryFrom(unmask)),
        ("Stuff", Algorithm::BinaryTo(stuff)),
        ("Unstuff", Algorithm::BinaryFrom(unstuff)),
        ("MsFrom", Algorithm::TimeFrom(ms_from)),
        ("MsTo", Algorithm::TimeTo(ms_to)),
        ("VarFrom", Algorithm::TimeFrom(var_from)),
        ("VarTo", Algorithm::TimeTo(var_to)),
    ];
    for (name, f) in all {
        m.set_algorithm(name, f).unwrap();
    }
}
