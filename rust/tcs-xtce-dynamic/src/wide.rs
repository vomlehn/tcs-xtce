//! Integers of more than 64 bits. The runtime reads and writes them as
//! integers of a width fixed when the program is compiled, as the code
//! generated for a mission does; the interpreter learns the width only
//! when it reads the mission, so it runs the runtime's helpers at a few
//! widths, each field at the least that holds it, and holds the values
//! as [`WideInt`]s, of a width that holds them all, so that nothing is
//! allocated for them.

use std::cmp::Ordering;

use num_bigint::{BigInt, Sign};
use tcs_xtce::packet::bnum::cast::CastFrom;
use tcs_xtce::packet::{
    self as pk, BitReader, BitWriter, ByteOrder, IntEncoding, PacketError,
};

/// The bytes of a [`WideInt`].
const WIDE: usize = MAX_BYTES + 8;

/// An integer of more than 64 bits, as the codec holds it: two's
/// complement, in more bytes than [`MAX_BYTES`], so that it holds every
/// signed and unsigned integer of up to `8 * MAX_BYTES` bits.
pub type WideInt = pk::Int<WIDE>;

/// Gives an integer as a wide one.
///
/// # Arguments
///
/// * `x` - The integer.
///
/// # Returns
///
/// It.
pub fn from_i128(x: i128) -> WideInt {
    WideInt::cast_from(x)
}

/// Gives a wide integer as an `i128`.
///
/// # Arguments
///
/// * `w` - The integer.
///
/// # Returns
///
/// It, or `None` if an `i128` doesn't hold it.
pub fn to_i128(w: &WideInt) -> Option<i128> {
    let x = i128::cast_from(*w);
    (from_i128(x) == *w).then_some(x)
}

/// Gives a wide integer as an `i64`.
///
/// # Arguments
///
/// * `w` - The integer.
///
/// # Returns
///
/// It, or `None` if an `i64` doesn't hold it.
pub fn to_i64(w: &WideInt) -> Option<i64> {
    to_i128(w).and_then(|x| i64::try_from(x).ok())
}

/// Gives a wide integer as a `u64`.
///
/// # Arguments
///
/// * `w` - The integer.
///
/// # Returns
///
/// It, or `None` if a `u64` doesn't hold it.
pub fn to_u64(w: &WideInt) -> Option<u64> {
    to_i128(w).and_then(|x| u64::try_from(x).ok())
}

/// Gives a wide integer as a float.
///
/// # Arguments
///
/// * `w` - The integer.
///
/// # Returns
///
/// The nearest float.
pub fn to_f64(w: &WideInt) -> f64 {
    f64::cast_from(*w)
}

/// Gives a document's integer as a wide one, without allocating.
///
/// # Arguments
///
/// * `b` - The integer.
///
/// # Returns
///
/// It, or `None` if a [`WideInt`] doesn't hold it.
pub fn from_big(b: &BigInt) -> Option<WideInt> {
    let mut bytes = [0u8; WIDE];
    for (i, d) in b.iter_u64_digits().enumerate() {
        let at = bytes.get_mut(8 * i..8 * i + 8)?;
        at.copy_from_slice(&d.to_le_bytes());
    }
    if bytes[WIDE - 1] & 0x80 != 0 {
        return None;
    }
    let m = WideInt::from_le_bytes(bytes);
    Some(if b.sign() == Sign::Minus { -m } else { m })
}

/// Gives a wide integer as a `BigInt`, which allocates.
///
/// # Arguments
///
/// * `w` - The integer.
///
/// # Returns
///
/// It.
pub fn to_big(w: &WideInt) -> BigInt {
    BigInt::from_signed_bytes_le(&w.to_le_bytes())
}

/// Compares a wide integer with a document's integer, without
/// allocating.
///
/// # Arguments
///
/// * `w` - The wide integer.
/// * `b` - The document's.
///
/// # Returns
///
/// How the first compares with the second.
pub fn cmp_big(w: &WideInt, b: &BigInt) -> Ordering {
    match from_big(b) {
        Some(v) => w.cmp(&v),
        // Past every wide integer.
        None if b.sign() == Sign::Minus => Ordering::Greater,
        None => Ordering::Less,
    }
}

/// The name the interpreter gives the runtime's helpers for a field.
const F: &str = "field";

/// The most bytes a wide integer may take.
pub const MAX_BYTES: usize = 1024;

/// The widths, in bytes, the interpreter runs the runtime's helpers at:
/// a field is read and written at the least that holds it, the bits
/// beyond its own ignored.
const BUCKETS: [usize; 7] = [16, 32, 64, 128, 256, 512, MAX_BYTES];

/// Gives the width the runtime's helpers run at for a field.
///
/// # Arguments
///
/// * `bytes` - The bytes of the field.
///
/// # Returns
///
/// The least of [`BUCKETS`] that holds them.
fn bucket(bytes: usize) -> usize {
    BUCKETS
        .into_iter()
        .find(|&b| bytes <= b)
        .expect("a width the interpreter supports")
}

/// Runs a function generic over a width in bytes at the width that holds
/// a field of some bytes.
macro_rules! at_width {
    ($n:expr, $f:ident, $($a:expr),*) => {
        match bucket($n) {
            16 => $f::<16>($($a),*),
            32 => $f::<32>($($a),*),
            64 => $f::<64>($($a),*),
            128 => $f::<128>($($a),*),
            256 => $f::<256>($($a),*),
            512 => $f::<512>($($a),*),
            _ => $f::<MAX_BYTES>($($a),*),
        }
    };
}

/// How a wide integer is encoded.
#[derive(Debug, Clone, Copy)]
pub struct Wide {
    /// Its bits.
    pub bits: u32,
    /// Whether its bits are least significant first.
    pub lsb: bool,
    /// Its byte order.
    pub order: ByteOrder,
    /// Its integer encoding.
    pub enc: IntEncoding,
    /// Whether it is BCD, and if so packed.
    pub bcd: Option<bool>,
    /// Whether its value is signed.
    pub signed: bool,
}

/// Gives the bytes of an integer in `N` bytes, least significant first.
///
/// # Arguments
///
/// * `v`      - The integer.
/// * `signed` - Whether it is held as two's complement.
///
/// # Returns
///
/// The bytes.
///
/// # Errors
///
/// OutOfRange, if it doesn't fit, or is negative and not signed.
fn le_bytes<const N: usize>(v: &WideInt, signed: bool) -> pk::Result<[u8; N]> {
    let bad = PacketError::OutOfRange { field: F };
    let b = v.to_le_bytes();
    let negative = b[WIDE - 1] & 0x80 != 0;
    if negative && !signed {
        return Err(bad);
    }
    let fill = if negative { 0xff } else { 0 };
    // What the N bytes don't hold must be the sign's.
    if b[N..].iter().any(|&x| x != fill) {
        return Err(bad);
    }
    if signed && (b[N - 1] & 0x80 != 0) != negative {
        return Err(bad);
    }
    let mut out = [0u8; N];
    out.copy_from_slice(&b[..N]);
    Ok(out)
}

/// Reads a wide integer in `N` bytes.
///
/// # Arguments
///
/// * `r` - The reader.
/// * `e` - How it is encoded.
///
/// # Returns
///
/// The value.
///
/// # Errors
///
/// The runtime's.
fn read_n<const N: usize>(
    r: &mut BitReader<'_>,
    e: Wide,
) -> pk::Result<WideInt> {
    let mut x = r.wide::<N>(e.bits, e.lsb, e.order, F)?;
    if let Some(packed) = e.bcd {
        x = pk::wide_bcd_decode(x, e.bits, packed, F)?;
    }
    if e.signed {
        let v = pk::wide_to_signed(x, e.bits, e.enc);
        Ok(WideInt::cast_from(v))
    } else {
        Ok(WideInt::cast_from(x))
    }
}

/// Writes a wide integer in `N` bytes.
///
/// # Arguments
///
/// * `w` - The writer.
/// * `e` - How it is encoded.
/// * `v` - The value.
///
/// # Errors
///
/// OutOfRange, if the encoding can't hold it.
fn write_n<const N: usize>(
    w: &mut BitWriter<'_>,
    e: Wide,
    v: &WideInt,
) -> pk::Result<()> {
    let raw = if let Some(packed) = e.bcd {
        let u = pk::Uint::<N>::from_le_bytes(le_bytes::<N>(v, false)?);
        pk::wide_bcd_encode(u, e.bits, packed, F)?
    } else if e.signed {
        let s = pk::Int::<N>::from_le_bytes(le_bytes::<N>(v, true)?);
        pk::wide_from_signed(s, e.bits, e.enc, F)?
    } else {
        let u = pk::Uint::<N>::from_le_bytes(le_bytes::<N>(v, false)?);
        pk::wide_from_unsigned(u, e.bits, F)?
    };
    w.wide(e.bits, e.lsb, e.order, &raw.to_le_bytes());
    Ok(())
}

/// Reads a wide integer, in the bytes of the field that holds it.
///
/// # Arguments
///
/// * `r`     - The reader.
/// * `e`     - How it is encoded.
/// * `bytes` - The bytes of its field, from 9 to [`MAX_BYTES`].
///
/// # Returns
///
/// The value.
///
/// # Errors
///
/// The runtime's.
pub fn read(
    r: &mut BitReader<'_>,
    e: Wide,
    bytes: usize,
) -> pk::Result<WideInt> {
    at_width!(bytes, read_n, r, e)
}

/// Writes a wide integer, in the bytes of the field that holds it.
///
/// # Arguments
///
/// * `w`     - The writer.
/// * `e`     - How it is encoded.
/// * `bytes` - The bytes of its field, from 9 to [`MAX_BYTES`].
/// * `v`     - The value.
///
/// # Errors
///
/// OutOfRange, if the encoding can't hold it.
pub fn write(
    w: &mut BitWriter<'_>,
    e: Wide,
    bytes: usize,
    v: &WideInt,
) -> pk::Result<()> {
    at_width!(bytes, write_n, w, e, v)
}

/// Reads the raw integer encoding of a float, or of a calibrated
/// integer, of more than 64 bits as a float, in `N` bytes.
///
/// # Arguments
///
/// * `r` - The reader.
/// * `e` - How it is encoded.
///
/// # Returns
///
/// The raw value as a float.
///
/// # Errors
///
/// The runtime's.
fn read_f64_n<const N: usize>(
    r: &mut BitReader<'_>,
    e: Wide,
) -> pk::Result<f64> {
    let mut x = r.wide::<N>(e.bits, e.lsb, e.order, F)?;
    if let Some(packed) = e.bcd {
        x = pk::wide_bcd_decode(x, e.bits, packed, F)?;
    }
    Ok(pk::wide_int_to_f64(x, e.bits, e.enc))
}

/// Writes a float, rounded, as the raw integer encoding of more than 64
/// bits of a float or calibrated integer, in `N` bytes.
///
/// # Arguments
///
/// * `w` - The writer.
/// * `e` - How it is encoded.
/// * `y` - The value, after any inverse calibrator.
///
/// # Errors
///
/// OutOfRange, if the encoding can't hold the rounded value.
fn write_f64_n<const N: usize>(
    w: &mut BitWriter<'_>,
    e: Wide,
    y: f64,
) -> pk::Result<()> {
    let mut raw = pk::f64_to_wide::<N>(y, e.bits, e.enc, F)?;
    if let Some(packed) = e.bcd {
        raw = pk::wide_bcd_encode(raw, e.bits, packed, F)?;
    }
    w.wide(e.bits, e.lsb, e.order, &raw.to_le_bytes());
    Ok(())
}

/// Gives the bytes of the integer the runtime reads a raw field of more
/// than 64 bits into: 16 up to 128 bits, as a `u128` has, and otherwise
/// the whole bytes the bits fill.
///
/// # Arguments
///
/// * `bits` - The bits of the field.
///
/// # Returns
///
/// The bytes.
pub fn raw_bytes(bits: u32) -> usize {
    if bits <= 128 {
        16
    } else {
        bits.div_ceil(8) as usize
    }
}

/// Reads the raw integer encoding of more than 64 bits of a float or
/// calibrated integer as a float.
///
/// # Arguments
///
/// * `r` - The reader.
/// * `e` - How it is encoded.
///
/// # Returns
///
/// The raw value as a float.
///
/// # Errors
///
/// The runtime's.
pub fn read_f64(r: &mut BitReader<'_>, e: Wide) -> pk::Result<f64> {
    at_width!(raw_bytes(e.bits), read_f64_n, r, e)
}

/// Writes a float, rounded, as the raw integer encoding of more than 64
/// bits of a float or calibrated integer.
///
/// # Arguments
///
/// * `w` - The writer.
/// * `e` - How it is encoded.
/// * `y` - The value, after any inverse calibrator.
///
/// # Errors
///
/// OutOfRange, if the encoding can't hold the rounded value.
pub fn write_f64(w: &mut BitWriter<'_>, e: Wide, y: f64) -> pk::Result<()> {
    at_width!(raw_bytes(e.bits), write_f64_n, w, e, y)
}
