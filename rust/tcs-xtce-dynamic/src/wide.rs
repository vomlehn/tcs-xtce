//! Integers of more than 64 bits. The runtime reads and writes them as
//! integers of a width fixed when the program is compiled, as the code
//! generated for a mission does; the interpreter learns the width only
//! when it reads the mission, so it runs the runtime's helpers at each
//! width from 9 to 64 bytes, and holds the values as `BigInt`s.

use num_bigint::{BigInt, Sign};
use tcs_xtce::packet::{
    self as pk, BitReader, BitWriter, ByteOrder, IntEncoding, PacketError,
};

/// The name the interpreter gives the runtime's helpers for a field.
const F: &str = "field";

/// The most bytes a wide integer may take.
pub const MAX_BYTES: usize = 64;

/// Runs a function generic over a width in bytes, from 9 to 64, at the
/// width given.
macro_rules! at_width {
    ($n:expr, $f:ident, $($a:expr),*) => {
        match $n {
            9 => $f::<9>($($a),*),
            10 => $f::<10>($($a),*),
            11 => $f::<11>($($a),*),
            12 => $f::<12>($($a),*),
            13 => $f::<13>($($a),*),
            14 => $f::<14>($($a),*),
            15 => $f::<15>($($a),*),
            16 => $f::<16>($($a),*),
            17 => $f::<17>($($a),*),
            18 => $f::<18>($($a),*),
            19 => $f::<19>($($a),*),
            20 => $f::<20>($($a),*),
            21 => $f::<21>($($a),*),
            22 => $f::<22>($($a),*),
            23 => $f::<23>($($a),*),
            24 => $f::<24>($($a),*),
            25 => $f::<25>($($a),*),
            26 => $f::<26>($($a),*),
            27 => $f::<27>($($a),*),
            28 => $f::<28>($($a),*),
            29 => $f::<29>($($a),*),
            30 => $f::<30>($($a),*),
            31 => $f::<31>($($a),*),
            32 => $f::<32>($($a),*),
            33 => $f::<33>($($a),*),
            34 => $f::<34>($($a),*),
            35 => $f::<35>($($a),*),
            36 => $f::<36>($($a),*),
            37 => $f::<37>($($a),*),
            38 => $f::<38>($($a),*),
            39 => $f::<39>($($a),*),
            40 => $f::<40>($($a),*),
            41 => $f::<41>($($a),*),
            42 => $f::<42>($($a),*),
            43 => $f::<43>($($a),*),
            44 => $f::<44>($($a),*),
            45 => $f::<45>($($a),*),
            46 => $f::<46>($($a),*),
            47 => $f::<47>($($a),*),
            48 => $f::<48>($($a),*),
            49 => $f::<49>($($a),*),
            50 => $f::<50>($($a),*),
            51 => $f::<51>($($a),*),
            52 => $f::<52>($($a),*),
            53 => $f::<53>($($a),*),
            54 => $f::<54>($($a),*),
            55 => $f::<55>($($a),*),
            56 => $f::<56>($($a),*),
            57 => $f::<57>($($a),*),
            58 => $f::<58>($($a),*),
            59 => $f::<59>($($a),*),
            60 => $f::<60>($($a),*),
            61 => $f::<61>($($a),*),
            62 => $f::<62>($($a),*),
            63 => $f::<63>($($a),*),
            64 => $f::<64>($($a),*),
            _ => unreachable!("a width the interpreter supports"),
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
fn le_bytes<const N: usize>(v: &BigInt, signed: bool) -> pk::Result<[u8; N]> {
    let bad = PacketError::OutOfRange { field: F };
    let (b, fill) = if signed {
        let fill = if v.sign() == Sign::Minus { 0xff } else { 0 };
        (v.to_signed_bytes_le(), fill)
    } else {
        if v.sign() == Sign::Minus {
            return Err(bad);
        }
        (v.to_bytes_le().1, 0)
    };
    if b.len() > N {
        return Err(bad);
    }
    let mut out = [fill; N];
    out[..b.len()].copy_from_slice(&b);
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
) -> pk::Result<BigInt> {
    let mut x = r.wide::<N>(e.bits, e.lsb, e.order, F)?;
    if let Some(packed) = e.bcd {
        x = pk::wide_bcd_decode(x, e.bits, packed, F)?;
    }
    if e.signed {
        let v = pk::wide_to_signed(x, e.bits, e.enc);
        Ok(BigInt::from_signed_bytes_le(&v.to_le_bytes()))
    } else {
        Ok(BigInt::from_bytes_le(Sign::Plus, &x.to_le_bytes()))
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
    v: &BigInt,
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
) -> pk::Result<BigInt> {
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
    v: &BigInt,
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
