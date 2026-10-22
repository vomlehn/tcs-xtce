//! Runtime for packet codecs generated from a mission's XTCE document.
//!
//! `tcs-xtce` (in `generator/`) turns the containers and meta-commands of a
//! mission database into Rust structs that implement [`Packet`].  The
//! generated code reads packets with a [`BitReader`] and writes them with a
//! [`BitWriter`] into a managed [`Buffer`]; this module provides those and
//! the integer, float, string and calibration helpers they call.
//!
//! A [`Codec`] owns one input and one output buffer, preallocated for the
//! largest packet it will see and reused for every packet until the codec is
//! dropped.

use crate::buffer::Buffer;
use crate::value::format_double;
use std::fmt;
use std::io::Read;

/// Why a packet could not be decoded or encoded.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PacketError {
    /// The packet ends before `field` does.
    Truncated { field: &'static str },
    /// A field position falls before the start of the packet.
    BadPosition { field: &'static str },
    /// No container or meta-command in the inheritance tree matches.
    NoMatch { container: &'static str },
    /// The packet does not satisfy a restriction criterion or argument
    /// assignment of the container being decoded or encoded.
    Mismatch {
        container: &'static str,
        criterion: &'static str,
    },
    /// A value does not fit its encoding.
    OutOfRange { field: &'static str },
    /// A string is not valid UTF-8 or contains its termination character.
    InvalidString { field: &'static str },
    /// A string or binary value is longer than its encoding allows.
    TooLong { field: &'static str },
    /// The value is outside the range covered by its spline calibrator.
    Calibration { field: &'static str },
    /// The field's calibrator cannot be inverted, so it cannot be encoded.
    NotInvertible { field: &'static str },
    /// An optional field is `None` although its include condition holds.
    Missing { field: &'static str },
    /// The size or count that another field gives `field` is not a whole
    /// number in range, or `field` does not have that size or count.
    BadSize { field: &'static str },
    /// The packet length field `field` does not match the packet's length.
    BadLength { field: &'static str },
}

impl PacketError {
    /// The error's name, identical in the Rust, C++ and Python runtimes.
    pub fn kind(&self) -> &'static str {
        match self {
            Self::Truncated { .. } => "Truncated",
            Self::BadPosition { .. } => "BadPosition",
            Self::NoMatch { .. } => "NoMatch",
            Self::Mismatch { .. } => "Mismatch",
            Self::OutOfRange { .. } => "OutOfRange",
            Self::InvalidString { .. } => "InvalidString",
            Self::TooLong { .. } => "TooLong",
            Self::Calibration { .. } => "Calibration",
            Self::NotInvertible { .. } => "NotInvertible",
            Self::Missing { .. } => "Missing",
            Self::BadSize { .. } => "BadSize",
            Self::BadLength { .. } => "BadLength",
        }
    }
}

impl fmt::Display for PacketError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Truncated { field } => write!(f, "packet too short for {field}"),
            Self::BadPosition { field } => write!(f, "{field} is placed before the packet start"),
            Self::NoMatch { container } => {
                write!(f, "no container derived from {container} matches")
            }
            Self::Mismatch {
                container,
                criterion,
            } => write!(f, "{container} requires {criterion}"),
            Self::OutOfRange { field } => write!(f, "value of {field} does not fit its encoding"),
            Self::InvalidString { field } => write!(f, "invalid string in {field}"),
            Self::TooLong { field } => write!(f, "{field} is too long for its encoding"),
            Self::Calibration { field } => write!(f, "{field} is outside its calibration range"),
            Self::NotInvertible { field } => {
                write!(
                    f,
                    "the calibrator of {field} cannot be inverted for encoding"
                )
            }
            Self::Missing { field } => {
                write!(f, "{field} is required by its include condition")
            }
            Self::BadSize { field } => write!(f, "invalid size or count for {field}"),
            Self::BadLength { field } => {
                write!(f, "{field} does not match the length of the packet")
            }
        }
    }
}

impl std::error::Error for PacketError {}

pub type Result<T> = std::result::Result<T, PacketError>;

/// A packet type generated from a sequence container or meta-command.
pub trait Packet: Sized {
    /// Qualified XTCE name of the container or meta-command.
    const NAME: &'static str;
    /// Upper bound on the encoded size, used to preallocate buffers.
    const MAX_BYTES: usize;

    /// Decodes a packet that starts at the first byte of `data`.  Bytes after
    /// the packet are ignored.
    fn decode(data: &[u8]) -> Result<Self>;

    /// Writes the packet with `w`.
    fn encode_into(&self, w: &mut BitWriter<'_>) -> Result<()>;

    /// Replaces the contents of `buf` with the encoded packet.
    fn encode(&self, buf: &mut Buffer) -> Result<()> {
        let mut w = BitWriter::new(buf);
        self.encode_into(&mut w)
    }

    /// Encodes into a new vector.
    fn to_bytes(&self) -> Result<Vec<u8>> {
        let mut buf = Buffer::with_capacity(Self::MAX_BYTES);
        self.encode(&mut buf)?;
        Ok(buf.as_bytes().to_vec())
    }
}

/// Owns the managed input and output buffers for one kind of packet.
#[derive(Debug, Clone)]
pub struct Codec {
    input: Buffer,
    output: Buffer,
}

impl Codec {
    /// Creates a codec with both buffers preallocated to `capacity` bytes.
    pub fn with_capacity(capacity: usize) -> Self {
        Self {
            input: Buffer::with_capacity(capacity),
            output: Buffer::with_capacity(capacity),
        }
    }

    /// Creates a codec whose buffers already fit the largest `P`.
    pub fn for_packet<P: Packet>() -> Self {
        Self::with_capacity(P::MAX_BYTES)
    }

    /// Copies `bytes` into the input buffer.
    pub fn load(&mut self, bytes: &[u8]) {
        self.input.load(bytes);
    }

    /// Fills the input buffer from `reader` until end of file.
    pub fn read_from<R: Read>(&mut self, reader: R) -> std::io::Result<usize> {
        self.input.load_from(reader)
    }

    /// The contents of the input buffer.
    pub fn input(&self) -> &[u8] {
        self.input.as_bytes()
    }

    /// Decodes the packet in the input buffer.
    pub fn decode<P: Packet>(&self) -> Result<P> {
        P::decode(self.input.as_bytes())
    }

    /// Encodes `packet` into the output buffer and returns a view of it.
    pub fn encode<P: Packet>(&mut self, packet: &P) -> Result<&[u8]> {
        packet.encode(&mut self.output)?;
        Ok(self.output.as_bytes())
    }

    pub fn input_capacity(&self) -> usize {
        self.input.capacity()
    }

    pub fn output_capacity(&self) -> usize {
        self.output.capacity()
    }
}

fn mask(bits: u32) -> u64 {
    if bits >= 64 {
        u64::MAX
    } else {
        (1u64 << bits) - 1
    }
}

fn position(base: usize, offset: i64, field: &'static str) -> Result<usize> {
    let p = base as i64 + offset;
    if p < 0 {
        Err(PacketError::BadPosition { field })
    } else {
        Ok(p as usize)
    }
}

/// Reads big-endian bit fields from a byte slice.
#[derive(Debug, Clone)]
pub struct BitReader<'a> {
    data: &'a [u8],
    pos: usize,
    end: usize, // the furthest bit read
}

impl<'a> BitReader<'a> {
    pub fn new(data: &'a [u8]) -> Self {
        Self {
            data,
            pos: 0,
            end: 0,
        }
    }

    /// Current position in bits from the start of the packet.
    pub fn position(&self) -> usize {
        self.pos
    }

    /// Moves to `offset` bits from `start`.  Bits skipped over count as
    /// part of the packet, as the writer fills them.
    pub fn seek(&mut self, start: usize, offset: i64, field: &'static str) -> Result<()> {
        self.pos = position(start, offset, field)?;
        self.end = self.end.max(self.pos);
        Ok(())
    }

    /// Moves `offset` bits from the current position.
    pub fn skip(&mut self, offset: i64, field: &'static str) -> Result<()> {
        self.seek(self.pos, offset, field)
    }

    fn need(&self, bits: usize, field: &'static str) -> Result<()> {
        if bits > (self.data.len() * 8).saturating_sub(self.pos) {
            Err(PacketError::Truncated { field })
        } else {
            Ok(())
        }
    }

    /// Reads an unsigned big-endian field of `bits` (1 to 64) bits.
    pub fn uint(&mut self, bits: u32, field: &'static str) -> Result<u64> {
        self.need(bits as usize, field)?;
        let mut v = 0u64;
        let mut left = bits;
        while left > 0 {
            let byte = self.data[self.pos / 8];
            let avail = 8 - (self.pos % 8) as u32;
            let take = avail.min(left);
            let chunk = (byte >> (avail - take)) as u64 & mask(take);
            v = (v << take) | chunk;
            self.pos += take as usize;
            left -= take;
        }
        self.end = self.end.max(self.pos);
        Ok(v)
    }

    /// Reads an unsigned field, little endian when `le` (whole bytes only).
    pub fn uint_order(&mut self, bits: u32, le: bool, field: &'static str) -> Result<u64> {
        let v = self.uint(bits, field)?;
        Ok(if le { swap_bytes(v, bits) } else { v })
    }

    /// Skips `bits` bits of padding, which must be in the packet.
    pub fn pad(&mut self, bits: usize, field: &'static str) -> Result<()> {
        self.need(bits, field)?;
        self.pos += bits;
        self.end = self.end.max(self.pos);
        Ok(())
    }

    /// Ends the packet where the packet length field `field`, whose value
    /// `v` was just read, says: `v - adjust` bytes after the field.  Fails
    /// with `Truncated` if `data` is shorter.
    pub fn limit(&mut self, v: u64, adjust: i64, field: &'static str) -> Result<()> {
        let after = v as i128 - adjust as i128;
        if after < 0 {
            return Err(PacketError::BadLength { field });
        }
        let total = self.pos.div_ceil(8) as i128 + after;
        if total > self.data.len() as i128 {
            return Err(PacketError::Truncated { field });
        }
        self.data = &self.data[..total as usize];
        Ok(())
    }

    /// Checks that the packet's entries reach the end that the packet
    /// length field `field` gave.
    pub fn finish(&self, field: &'static str) -> Result<()> {
        if self.end.div_ceil(8) != self.data.len() {
            return Err(PacketError::BadLength { field });
        }
        Ok(())
    }

    /// The bytes from the current position to the end of the packet, for a
    /// field that takes the rest of it.  Fails with `BadSize` unless they
    /// are whole bytes, at most `max` of them.
    pub fn rest_bytes(&self, max: usize, field: &'static str) -> Result<usize> {
        let bits = (self.data.len() * 8).saturating_sub(self.pos);
        if bits % 8 != 0 || bits / 8 > max {
            return Err(PacketError::BadSize { field });
        }
        Ok(bits / 8)
    }

    /// Reads `n` whole bytes (which need not be byte aligned).
    pub fn bytes(&mut self, n: usize, field: &'static str) -> Result<Vec<u8>> {
        self.need(n.checked_mul(8).unwrap_or(usize::MAX), field)?;
        if self.pos % 8 == 0 {
            let start = self.pos / 8;
            self.pos += n * 8;
            self.end = self.end.max(self.pos);
            return Ok(self.data[start..start + n].to_vec());
        }
        (0..n)
            .map(|_| self.uint(8, field).map(|b| b as u8))
            .collect()
    }
}

/// Writes big-endian bit fields into a managed [`Buffer`].
///
/// The buffer is cleared when the writer is created and grows (zero filled)
/// to cover the furthest bit written; its allocation is reused.
#[derive(Debug)]
pub struct BitWriter<'a> {
    buf: &'a mut Buffer,
    pos: usize,
}

impl<'a> BitWriter<'a> {
    pub fn new(buf: &'a mut Buffer) -> Self {
        buf.clear();
        Self { buf, pos: 0 }
    }

    pub fn position(&self) -> usize {
        self.pos
    }

    pub fn seek(&mut self, start: usize, offset: i64, field: &'static str) -> Result<()> {
        self.pos = position(start, offset, field)?;
        Ok(())
    }

    pub fn skip(&mut self, offset: i64, field: &'static str) -> Result<()> {
        self.seek(self.pos, offset, field)
    }

    /// Writes the low `bits` bits of `v`, big endian.
    pub fn uint(&mut self, bits: u32, v: u64) {
        let end = (self.pos + bits as usize).div_ceil(8);
        if self.buf.len() < end {
            self.buf.resize(end);
        }
        let data = self.buf.as_mut_bytes();
        let mut left = bits;
        while left > 0 {
            let avail = 8 - (self.pos % 8) as u32;
            let take = avail.min(left);
            let chunk = ((v >> (left - take)) & mask(take)) as u8;
            let shift = avail - take;
            let m = (mask(take) as u8) << shift;
            let b = &mut data[self.pos / 8];
            *b = (*b & !m) | (chunk << shift);
            self.pos += take as usize;
            left -= take;
        }
    }

    pub fn uint_order(&mut self, bits: u32, le: bool, v: u64) {
        self.uint(bits, if le { swap_bytes(v, bits) } else { v });
    }

    /// Writes whole bytes (which need not be byte aligned).
    pub fn bytes(&mut self, data: &[u8]) {
        if data.is_empty() {
            return;
        }
        if self.pos % 8 != 0 {
            for &b in data {
                self.uint(8, b as u64);
            }
            return;
        }
        let start = self.pos / 8;
        self.span(start, data.len()).copy_from_slice(data);
        self.pos += data.len() * 8;
    }

    /// Fills in the packet length field `field` of `bits` bits at bit `at`,
    /// once the whole packet is written: the number of bytes after the
    /// field plus `adjust`.
    pub fn patch_length(
        &mut self,
        at: usize,
        bits: u32,
        le: bool,
        adjust: i64,
        field: &'static str,
    ) -> Result<()> {
        let v = self.buf.len() as i128 - (at + bits as usize).div_ceil(8) as i128 + adjust as i128;
        if v < 0 || v > mask(bits) as i128 {
            return Err(PacketError::OutOfRange { field });
        }
        let end = self.pos;
        self.pos = at;
        self.uint_order(bits, le, v as u64);
        self.pos = end;
        Ok(())
    }

    /// Writes `bits` zero bits, overwriting whatever a seek left there.
    pub fn zeros(&mut self, bits: usize) {
        if bits == 0 {
            return;
        }
        let lead = ((8 - self.pos % 8) % 8).min(bits);
        if lead > 0 {
            self.uint(lead as u32, 0);
        }
        let whole = (bits - lead) / 8;
        if whole > 0 {
            let start = self.pos / 8;
            self.span(start, whole).fill(0);
            self.pos += whole * 8;
        }
        let tail = (bits - lead) % 8;
        if tail > 0 {
            self.uint(tail as u32, 0);
        }
    }

    /// Bytes `start..start + n` of the buffer, growing it to cover them.
    fn span(&mut self, start: usize, n: usize) -> &mut [u8] {
        if self.buf.len() < start + n {
            self.buf.resize(start + n);
        }
        &mut self.buf.as_mut_bytes()[start..start + n]
    }
}

fn swap_bytes(v: u64, bits: u32) -> u64 {
    v.swap_bytes() >> (64 - bits)
}

// --- integers -----------------------------------------------------------------

/// How a signed integer is laid out on the wire.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum IntEncoding {
    Unsigned,
    TwosComplement,
    SignMagnitude,
    OnesComplement,
}

/// Converts a raw field to its signed value.
pub fn to_signed(raw: u64, bits: u32, enc: IntEncoding) -> i64 {
    let sign = bits < 64 && (raw >> (bits - 1)) & 1 == 1 || bits == 64 && (raw >> 63) == 1;
    let magnitude = raw & mask(bits - 1);
    match enc {
        IntEncoding::Unsigned => raw as i64,
        IntEncoding::TwosComplement => {
            if sign && bits < 64 {
                (raw | !mask(bits)) as i64
            } else {
                raw as i64
            }
        }
        IntEncoding::SignMagnitude => {
            if sign {
                -(magnitude as i64)
            } else {
                magnitude as i64
            }
        }
        IntEncoding::OnesComplement => {
            if sign {
                -((!raw & mask(bits - 1)) as i64)
            } else {
                magnitude as i64
            }
        }
    }
}

/// Converts a signed value to its raw field, checking that it fits.
pub fn from_signed(v: i64, bits: u32, enc: IntEncoding, field: &'static str) -> Result<u64> {
    let out = Err(PacketError::OutOfRange { field });
    let limit = mask(bits - 1);
    match enc {
        IntEncoding::Unsigned => {
            if v < 0 {
                out
            } else {
                from_unsigned(v as u64, bits, field)
            }
        }
        IntEncoding::TwosComplement => {
            let lo = -(limit as i128) - 1;
            if (v as i128) < lo || (v as i128) > limit as i128 {
                out
            } else {
                Ok(v as u64 & mask(bits))
            }
        }
        IntEncoding::SignMagnitude | IntEncoding::OnesComplement => {
            let m = v.unsigned_abs();
            if m > limit {
                out
            } else if v >= 0 {
                Ok(m)
            } else if enc == IntEncoding::SignMagnitude {
                Ok((1 << (bits - 1)) | m)
            } else {
                Ok(!m & mask(bits))
            }
        }
    }
}

/// Checks that an unsigned value fits in `bits` bits.
pub fn from_unsigned(v: u64, bits: u32, field: &'static str) -> Result<u64> {
    if v > mask(bits) {
        Err(PacketError::OutOfRange { field })
    } else {
        Ok(v)
    }
}

/// Decodes a raw integer as unsigned or signed, as a float.
pub fn int_to_f64(raw: u64, bits: u32, enc: IntEncoding) -> f64 {
    match enc {
        IntEncoding::Unsigned => raw as f64,
        _ => to_signed(raw, bits, enc) as f64,
    }
}

/// Rounds an engineering value (half away from zero) to a raw integer field.
pub fn f64_to_int(x: f64, bits: u32, enc: IntEncoding, field: &'static str) -> Result<u64> {
    let r = x.round();
    if !r.is_finite() || r.abs() >= 18446744073709551616.0 {
        return Err(PacketError::OutOfRange { field });
    }
    if enc == IntEncoding::Unsigned {
        if r < 0.0 {
            return Err(PacketError::OutOfRange { field });
        }
        from_unsigned(r as u64, bits, field)
    } else {
        if r < -9223372036854775808.0 || r >= 9223372036854775808.0 {
            return Err(PacketError::OutOfRange { field });
        }
        from_signed(r as i64, bits, enc, field)
    }
}

/// Rounds a calibrated value (half away from zero) to an integer from `lo`
/// up to but not including `lim`; fails with `OutOfRange` otherwise.
pub fn round_to_int(y: f64, lo: f64, lim: f64, field: &'static str) -> Result<f64> {
    let r = y.round();
    if r >= lo && r < lim {
        Ok(r)
    } else {
        Err(PacketError::OutOfRange { field })
    }
}

// --- floats -------------------------------------------------------------------

pub fn float_from_raw(raw: u64, bits: u32) -> f64 {
    if bits == 32 {
        f32::from_bits(raw as u32) as f64
    } else {
        f64::from_bits(raw)
    }
}

pub fn float_to_raw(v: f64, bits: u32) -> u64 {
    if bits == 32 {
        (v as f32).to_bits() as u64
    } else {
        v.to_bits()
    }
}

// --- calibrators ----------------------------------------------------------------

/// Evaluates a polynomial calibrator given as (coefficient, exponent) terms.
pub fn polynomial(x: f64, terms: &[(f64, i32)]) -> f64 {
    let mut y = 0.0;
    for &(c, e) in terms {
        y += match e {
            0 => c,
            1 => c * x,
            _ => c * x.powf(e as f64),
        };
    }
    y
}

/// Inverts a polynomial calibrator of degree one.
pub fn polynomial_inverse(y: f64, intercept: f64, slope: f64) -> f64 {
    (y - intercept) / slope
}

fn lerp(x: f64, (x0, y0): (f64, f64), (x1, y1): (f64, f64)) -> f64 {
    y0 + (x - x0) * (y1 - y0) / (x1 - x0)
}

/// Evaluates a spline calibrator over (raw, calibrated) points sorted by raw.
pub fn spline(
    x: f64,
    points: &[(f64, f64)],
    order: u32,
    extrapolate: bool,
    field: &'static str,
) -> Result<f64> {
    let n = points.len();
    let (first, last) = (points[0], points[n - 1]);
    if x < first.0 || x > last.0 {
        if !extrapolate {
            return Err(PacketError::Calibration { field });
        }
        if order == 0 {
            return Ok(if x < first.0 { first.1 } else { last.1 });
        }
        return Ok(if x < first.0 {
            lerp(x, first, points[1])
        } else {
            lerp(x, points[n - 2], last)
        });
    }
    for i in 0..n - 1 {
        if x < points[i + 1].0 {
            return Ok(if order == 0 {
                points[i].1
            } else {
                lerp(x, points[i], points[i + 1])
            });
        }
    }
    Ok(last.1)
}

/// Inverts a linear spline whose calibrated values are strictly monotonic.
pub fn spline_inverse(
    y: f64,
    points: &[(f64, f64)],
    extrapolate: bool,
    field: &'static str,
) -> Result<f64> {
    let n = points.len();
    let flip = |(a, b): (f64, f64)| (b, a);
    for i in 0..n - 1 {
        let (a, b) = (points[i].1, points[i + 1].1);
        if (a <= y && y <= b) || (b <= y && y <= a) {
            return Ok(lerp(y, flip(points[i]), flip(points[i + 1])));
        }
    }
    if !extrapolate {
        return Err(PacketError::Calibration { field });
    }
    let increasing = points[n - 1].1 > points[0].1;
    let before = (y < points[0].1) == increasing;
    Ok(if before {
        lerp(y, flip(points[0]), flip(points[1]))
    } else {
        lerp(y, flip(points[n - 2]), flip(points[n - 1]))
    })
}

// --- sizes from other fields -------------------------------------------------------

/// A size or count that another field gives (an XTCE `DynamicValue`):
/// `(slope * x + intercept) / unit`, where `x` is that field's value and
/// `unit` is 8 for a size in bits used in whole bytes.  Fails with `BadSize`
/// unless the result is a whole number from 0 to `max`.
pub fn dynamic_size(
    x: f64,
    slope: f64,
    intercept: f64,
    unit: u32,
    max: usize,
    field: &'static str,
) -> Result<usize> {
    let y = slope * x + intercept;
    let unit = unit as f64;
    if !(y >= 0.0) || y.fract() != 0.0 || (y / unit).fract() != 0.0 || y / unit > max as f64 {
        return Err(PacketError::BadSize { field });
    }
    Ok((y / unit) as usize)
}

// --- strings ----------------------------------------------------------------------

/// Layout of a string field.  Exactly one of `terminator` and `size_tag_bits`
/// is set.
#[derive(Debug, Clone, Copy)]
pub struct StringSpec {
    /// Bits the field always occupies, or `None` for variable-size strings.
    pub fixed_bits: Option<usize>,
    /// Upper bound on the bits the field occupies.
    pub max_bits: usize,
    pub terminator: Option<u8>,
    pub size_tag_bits: u32,
}

impl StringSpec {
    /// The spec of a string whose size another field gives: it occupies
    /// exactly `bytes` bytes.
    pub fn sized(&self, bytes: usize) -> StringSpec {
        StringSpec {
            fixed_bits: Some(bytes * 8),
            max_bits: bytes * 8,
            ..*self
        }
    }
}

/// Whether `n` bytes after a `tag`-bit size tag fit in `bits` bits.  Avoids
/// `n * 8`, which can overflow `usize` for a size tag read from a packet.
fn fits(n: u64, tag: u32, bits: usize) -> bool {
    let tag = tag as usize;
    tag <= bits && n <= ((bits - tag) / 8) as u64
}

pub fn read_string(
    r: &mut BitReader<'_>,
    spec: &StringSpec,
    field: &'static str,
) -> Result<String> {
    let start = r.position();
    let bytes = match (spec.fixed_bits, spec.terminator) {
        (Some(bits), Some(term)) => {
            let mut b = r.bytes(bits / 8, field)?;
            if let Some(i) = b.iter().position(|&c| c == term) {
                b.truncate(i);
            }
            b
        }
        (Some(bits), None) => {
            let n = r.uint(spec.size_tag_bits, field)?;
            if !fits(n, spec.size_tag_bits, bits) {
                return Err(PacketError::TooLong { field });
            }
            let b = r.bytes(n as usize, field)?;
            r.seek(start, bits as i64, field)?;
            b
        }
        (None, Some(term)) => {
            let mut b = Vec::new();
            loop {
                if (b.len() + 1) * 8 > spec.max_bits {
                    return Err(PacketError::TooLong { field });
                }
                let c = r.uint(8, field)? as u8;
                if c == term {
                    break b;
                }
                b.push(c);
            }
        }
        (None, None) => {
            let n = r.uint(spec.size_tag_bits, field)?;
            if !fits(n, spec.size_tag_bits, spec.max_bits) {
                return Err(PacketError::TooLong { field });
            }
            r.bytes(n as usize, field)?
        }
    };
    String::from_utf8(bytes).map_err(|_| PacketError::InvalidString { field })
}

pub fn write_string(
    w: &mut BitWriter<'_>,
    spec: &StringSpec,
    s: &str,
    field: &'static str,
) -> Result<()> {
    let b = s.as_bytes();
    let tag = spec.size_tag_bits as usize;
    match spec.terminator {
        Some(term) => {
            if b.contains(&term) {
                return Err(PacketError::InvalidString { field });
            }
            let room = spec.fixed_bits.unwrap_or(spec.max_bits) / 8;
            let need = if spec.fixed_bits.is_some() {
                b.len()
            } else {
                b.len() + 1
            };
            if need > room {
                return Err(PacketError::TooLong { field });
            }
            w.bytes(b);
            if b.len() < room {
                w.uint(8, term as u64);
            }
            if let Some(bits) = spec.fixed_bits {
                w.zeros((bits / 8).saturating_sub(b.len() + 1) * 8);
            }
        }
        None => {
            let room = spec.fixed_bits.unwrap_or(spec.max_bits);
            if b.len() * 8 + tag > room || b.len() as u64 > mask(spec.size_tag_bits) {
                return Err(PacketError::TooLong { field });
            }
            w.uint(spec.size_tag_bits, b.len() as u64);
            w.bytes(b);
            if let Some(bits) = spec.fixed_bits {
                w.zeros(bits - tag - b.len() * 8);
            }
        }
    }
    Ok(())
}

/// The string a string field encoded as binary holds: all of its bytes,
/// which must be UTF-8.
pub fn string_from_bytes(b: Vec<u8>, field: &'static str) -> Result<String> {
    String::from_utf8(b).map_err(|_| PacketError::InvalidString { field })
}

/// Writes a fixed-size binary field.
pub fn write_binary(w: &mut BitWriter<'_>, b: &[u8], n: usize, field: &'static str) -> Result<()> {
    if b.len() != n {
        return Err(if b.len() > n {
            PacketError::TooLong { field }
        } else {
            PacketError::OutOfRange { field }
        });
    }
    w.bytes(b);
    Ok(())
}

/// Writes a binary field that takes the rest of the packet: all of `b`,
/// which must be at most `max` bytes.
pub fn write_rest(w: &mut BitWriter<'_>, b: &[u8], max: usize, field: &'static str) -> Result<()> {
    if b.len() > max {
        return Err(PacketError::TooLong { field });
    }
    w.bytes(b);
    Ok(())
}

// --- canonical text ---------------------------------------------------------------
//
// `describe` output is identical in every language so that the runtimes can be
// compared byte for byte.

pub fn describe_f64(x: f64, out: &mut String) {
    format_double(x, out);
}

pub fn describe_str(s: &str, out: &mut String) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                out.push_str(&format!("\\x{:02x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

/// Text for an optional field that is absent.
pub const DESCRIBE_NONE: &str = "none";

pub fn describe_bytes(b: &[u8], out: &mut String) {
    out.push_str("0x");
    for x in b {
        out.push_str(&format!("{x:02x}"));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bit_fields_round_trip() {
        let mut buf = Buffer::with_capacity(16);
        let mut w = BitWriter::new(&mut buf);
        w.uint(3, 5);
        w.uint(11, 0x5a5);
        w.uint_order(32, true, 0x0102_0304);
        w.uint(64, u64::MAX - 1);
        let mut r = BitReader::new(buf.as_bytes());
        assert_eq!(r.uint(3, "a").unwrap(), 5);
        assert_eq!(r.uint(11, "b").unwrap(), 0x5a5);
        assert_eq!(r.uint_order(32, true, "c").unwrap(), 0x0102_0304);
        assert_eq!(r.uint(64, "d").unwrap(), u64::MAX - 1);
        assert_eq!(r.uint(8, "e"), Err(PacketError::Truncated { field: "e" }));
    }

    #[test]
    fn signed_encodings() {
        use IntEncoding::*;
        for enc in [TwosComplement, SignMagnitude, OnesComplement] {
            for v in [-127i64, -1, 0, 1, 127] {
                let raw = from_signed(v, 8, enc, "x").unwrap();
                assert_eq!(to_signed(raw, 8, enc), v, "{enc:?} {v}");
            }
            assert!(from_signed(128, 8, enc, "x").is_err());
        }
        assert_eq!(from_signed(-128, 8, TwosComplement, "x"), Ok(0x80));
        assert_eq!(to_signed(u64::MAX, 64, TwosComplement), -1);
    }

    #[test]
    fn bytes_and_zeros_overwrite() {
        // Aligned and unaligned, and over bits written before a seek back.
        let mut buf = Buffer::with_capacity(16);
        let mut w = BitWriter::new(&mut buf);
        w.bytes(&[0xff; 6]);
        w.seek(0, 0, "x").unwrap();
        w.bytes(&[0x12, 0x34]);
        w.uint(3, 0b101);
        w.bytes(&[0xab]);
        w.zeros(13);
        w.uint(4, 0);
        assert_eq!(buf.as_bytes(), &[0x12, 0x34, 0xb5, 0x60, 0x00, 0x0f]);
        let mut w = BitWriter::new(&mut buf);
        w.uint(4, 0xf);
        w.zeros(2);
        // Writing nothing past the end does not extend the packet.
        w.skip(18, "x").unwrap();
        w.zeros(0);
        w.bytes(&[]);
        assert_eq!(buf.as_bytes(), &[0xf0]);
    }

    #[test]
    fn huge_lengths_are_truncated() {
        // n * 8 would overflow usize; this must be an error, not a panic.
        let data = [0u8; 4];
        let mut r = BitReader::new(&data);
        assert_eq!(
            r.bytes(1 << 61, "x"),
            Err(PacketError::Truncated { field: "x" })
        );
        assert_eq!(
            r.bytes(usize::MAX, "x"),
            Err(PacketError::Truncated { field: "x" })
        );
        // A 32-bit size tag claiming 4 GiB is too long for a 64-bit field.
        let data = [0xff, 0xff, 0xff, 0xff, 0, 0, 0, 0];
        let spec = StringSpec {
            fixed_bits: Some(64),
            max_bits: 64,
            terminator: None,
            size_tag_bits: 32,
        };
        assert_eq!(
            read_string(&mut BitReader::new(&data), &spec, "s"),
            Err(PacketError::TooLong { field: "s" })
        );
    }

    #[test]
    fn binary32_rounding() {
        // The C and Python runtimes must give the same bits.
        assert_eq!(float_to_raw(3.4028235e38, 32), 0x7f7f_ffff);
        assert_eq!(float_to_raw(-3.4028235e38, 32), 0xff7f_ffff);
        assert_eq!(float_to_raw(3.402_823_567_797_336_2e38, 32), 0x7f7f_ffff);
        assert_eq!(float_to_raw(3.402_823_567_797_336_6e38, 32), 0x7f80_0000);
        assert_eq!(float_to_raw(1e39, 32), 0x7f80_0000);
        assert_eq!(float_to_raw(-1e39, 32), 0xff80_0000);
    }

    #[test]
    fn dynamic_sizes() {
        assert_eq!(dynamic_size(5.0, 1.0, -1.0, 1, 10, "f"), Ok(4));
        assert_eq!(dynamic_size(24.0, 1.0, 0.0, 8, 3, "f"), Ok(3));
        assert_eq!(dynamic_size(3.0, 8.0, 0.0, 8, 3, "f"), Ok(3));
        for (x, slope, unit, max) in [
            (0.0, 1.0, 1, 10),      // negative after the intercept
            (12.0, 1.0, 1, 10),     // more than max
            (5.0, 0.5, 1, 10),      // not whole
            (12.0, 1.0, 8, 10),     // not whole bytes
            (f64::NAN, 1.0, 1, 10), // not a number
        ] {
            let intercept = if x == 0.0 { -1.0 } else { 0.0 };
            assert_eq!(
                dynamic_size(x, slope, intercept, unit, max, "f"),
                Err(PacketError::BadSize { field: "f" })
            );
        }
    }

    #[test]
    fn splines_invert() {
        let pts = [(0.0, -50.0), (128.0, 0.0), (255.0, 100.0)];
        let y = spline(200.0, &pts, 1, false, "t").unwrap();
        let x = spline_inverse(y, &pts, false, "t").unwrap();
        assert!((x - 200.0).abs() < 1e-9);
        assert!(spline(300.0, &pts, 1, false, "t").is_err());
        assert_eq!(spline(10.0, &pts, 0, false, "t").unwrap(), -50.0);
    }
}
