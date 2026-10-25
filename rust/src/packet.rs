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
//!
//! Code generated with `--dynamic-allocations false` uses the
//! fixed-capacity types of [`crate::fixed`] for arrays, strings and binary
//! fields, and the `fixed_*` readers here, so it never allocates.  Encode it
//! with a `Codec` made by [`Codec::for_packet`], whose output buffer never
//! grows, and decode it from a slice with [`Packet::decode`].

use crate::buffer::Buffer;
pub use crate::fixed::{CapacityError, FixedBytes, FixedString, FixedVec};
use crate::value::write_double;
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
    /// The bits of `field` are not a value of its encoding (a BCD digit
    /// above 9, or a DEC reserved operand).
    InvalidValue { field: &'static str },
    /// The checksum field `field` does not match the data it checks.
    BadChecksum { field: &'static str },
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
            Self::InvalidValue { .. } => "InvalidValue",
            Self::BadChecksum { .. } => "BadChecksum",
        }
    }
}

impl fmt::Display for PacketError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Truncated { field } => {
                write!(f, "packet too short for {field}")
            }
            Self::BadPosition { field } => {
                write!(f, "{field} is placed before the packet start")
            }
            Self::NoMatch { container } => {
                write!(f, "no container derived from {container} matches")
            }
            Self::Mismatch {
                container,
                criterion,
            } => write!(f, "{container} requires {criterion}"),
            Self::OutOfRange { field } => {
                write!(f, "value of {field} does not fit its encoding")
            }
            Self::InvalidString { field } => {
                write!(f, "invalid string in {field}")
            }
            Self::TooLong { field } => {
                write!(f, "{field} is too long for its encoding")
            }
            Self::Calibration { field } => {
                write!(f, "{field} is outside its calibration range")
            }
            Self::NotInvertible { field } => {
                write!(
                    f,
                    "the calibrator of {field} cannot be inverted for encoding"
                )
            }
            Self::Missing { field } => {
                write!(f, "{field} is required by its include condition")
            }
            Self::BadSize { field } => {
                write!(f, "invalid size or count for {field}")
            }
            Self::BadLength { field } => {
                write!(f, "{field} does not match the length of the packet")
            }
            Self::InvalidValue { field } => {
                write!(f, "the bits of {field} are not a value of its encoding")
            }
            Self::BadChecksum { field } => {
                write!(f, "{field} does not match the data it checks")
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
    pub fn seek(
        &mut self,
        start: usize,
        offset: i64,
        field: &'static str,
    ) -> Result<()> {
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
    pub fn uint_order(
        &mut self,
        bits: u32,
        le: bool,
        field: &'static str,
    ) -> Result<u64> {
        let v = self.uint(bits, field)?;
        Ok(if le { swap_bytes(v, bits) } else { v })
    }

    /// Reads an unsigned field of `bits` (1 to 128) bits in any bit and
    /// byte order.
    ///
    /// The field's bits are taken in groups of 8 (the last may be shorter).
    /// Within a group, `lsb` false numbers the bits of each byte from the
    /// most significant (the usual order), and the group's first bit is its
    /// most significant; `lsb` true numbers them from the least significant,
    /// and the group's first bit is its least significant.  `order` combines
    /// the groups.
    pub fn uint_bits(
        &mut self,
        bits: u32,
        lsb: bool,
        order: ByteOrder,
        field: &'static str,
    ) -> Result<u128> {
        self.need(bits as usize, field)?;
        let mut groups = [(0u8, 0u32); 16];
        let mut q = self.pos;
        let mut left = bits;
        let mut k = 0;
        while left > 0 {
            let n = left.min(8);
            let mut g = 0u8;
            for i in 0..n as usize {
                let byte = self.data[(q + i) / 8];
                if lsb {
                    g |= ((byte >> ((q + i) % 8)) & 1) << i;
                } else {
                    g = (g << 1) | ((byte >> (7 - (q + i) % 8)) & 1);
                }
            }
            groups[k] = (g, n);
            k += 1;
            q += n as usize;
            left -= n;
        }
        self.pos = q;
        self.end = self.end.max(q);
        Ok(combine(&groups[..k], order))
    }

    /// The end of the packet in bits: the end of the last byte read.
    pub fn packet_end(&self) -> usize {
        self.end.div_ceil(8) * 8
    }

    /// The checksum `spec` of the bits from `start` to `end`.
    pub fn checksum(
        &self,
        spec: &Checksum,
        start: usize,
        end: usize,
        field: &'static str,
    ) -> Result<u64> {
        checksum(spec, self.data, start, end, field)
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
    pub fn limit(
        &mut self,
        v: u64,
        adjust: i64,
        field: &'static str,
    ) -> Result<()> {
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
        let mut out = Vec::new();
        self.bytes_into(n, &mut out, field)?;
        Ok(out)
    }

    /// Reads `n` whole bytes into a fixed-capacity vector; fails with
    /// `TooLong` if they do not fit.
    pub fn fixed_bytes<const N: usize>(
        &mut self,
        n: usize,
        field: &'static str,
    ) -> Result<FixedBytes<N>> {
        let mut out = FixedBytes::new();
        self.bytes_into(n, &mut out, field)?;
        Ok(out)
    }

    /// Appends `n` whole bytes to `out`.
    fn bytes_into<S: ByteSink>(
        &mut self,
        n: usize,
        out: &mut S,
        field: &'static str,
    ) -> Result<()> {
        self.need(n.checked_mul(8).unwrap_or(usize::MAX), field)?;
        if self.pos % 8 == 0 {
            let start = self.pos / 8;
            out.append(&self.data[start..start + n], field)?;
            self.pos += n * 8;
            self.end = self.end.max(self.pos);
            return Ok(());
        }
        for _ in 0..n {
            let b = self.uint(8, field)? as u8;
            out.append(&[b], field)?;
        }
        Ok(())
    }
}

/// Where [`BitReader`] puts the bytes it reads: a `Vec`, or a
/// [`FixedBytes`] that never allocates.
trait ByteSink {
    fn append(&mut self, bytes: &[u8], field: &'static str) -> Result<()>;
    fn len(&self) -> usize;
    fn truncate(&mut self, len: usize);
    fn bytes(&self) -> &[u8];
}

impl ByteSink for Vec<u8> {
    fn append(&mut self, bytes: &[u8], _field: &'static str) -> Result<()> {
        self.extend_from_slice(bytes);
        Ok(())
    }
    fn len(&self) -> usize {
        Vec::len(self)
    }
    fn truncate(&mut self, len: usize) {
        Vec::truncate(self, len);
    }
    fn bytes(&self) -> &[u8] {
        self
    }
}

impl<const N: usize> ByteSink for FixedBytes<N> {
    fn append(&mut self, bytes: &[u8], field: &'static str) -> Result<()> {
        self.extend_from_slice(bytes)
            .map_err(|_| PacketError::TooLong { field })
    }
    fn len(&self) -> usize {
        self.as_slice().len()
    }
    fn truncate(&mut self, len: usize) {
        FixedVec::truncate(self, len);
    }
    fn bytes(&self) -> &[u8] {
        self
    }
}

/// What [`BitWriter::stash`] saved while a value split into segments is
/// encoded.
#[derive(Debug, Clone, Copy)]
pub struct Stash {
    pos: usize,
    at: usize,
    len: usize,
    bits: u32,
    saved: u64,
}

/// A reader of a value's `total`-bit encoding, which its segments gathered
/// into `raw` (the encoding's last bit least significant), held in `buf`.
pub fn segment_reader(
    buf: &mut [u8; 8],
    raw: u64,
    total: u32,
) -> BitReader<'_> {
    *buf = (raw << (64 - total)).to_be_bytes();
    BitReader::new(buf)
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

    pub fn seek(
        &mut self,
        start: usize,
        offset: i64,
        field: &'static str,
    ) -> Result<()> {
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

    /// Writes the low `bits` bits of `v` in the bit and byte order that
    /// [`BitReader::uint_bits`] reads.
    pub fn uint_bits(
        &mut self,
        bits: u32,
        lsb: bool,
        order: ByteOrder,
        v: u128,
    ) {
        let end = (self.pos + bits as usize).div_ceil(8);
        if self.buf.len() < end {
            self.buf.resize(end);
        }
        let mut lengths = [0u32; 16];
        let k = bits.div_ceil(8) as usize;
        for (i, n) in lengths.iter_mut().enumerate().take(k) {
            *n = if (i + 1) * 8 <= bits as usize {
                8
            } else {
                bits % 8
            };
        }
        let data = self.buf.as_mut_bytes();
        let mut q = self.pos;
        for j in 0..k {
            let (g, n) = (split(v, &lengths[..k], j, order), lengths[j]);
            for i in 0..n as usize {
                let bit = if lsb {
                    (g >> i) & 1
                } else {
                    (g >> (n as usize - 1 - i)) & 1
                };
                let shift = if lsb { (q + i) % 8 } else { 7 - (q + i) % 8 };
                let b = &mut data[(q + i) / 8];
                *b = (*b & !(1 << shift)) | (bit << shift);
            }
            q += n as usize;
        }
        self.pos = q;
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
        let v = self.buf.len() as i128
            - (at + bits as usize).div_ceil(8) as i128
            + adjust as i128;
        if v < 0 || v > mask(bits) as i128 {
            return Err(PacketError::OutOfRange { field });
        }
        let end = self.pos;
        self.pos = at;
        self.uint_order(bits, le, v as u64);
        self.pos = end;
        Ok(())
    }

    /// The end of the packet written so far, in bits.
    pub fn packet_end(&self) -> usize {
        self.buf.len() * 8
    }

    /// Fills in the checksum field `field` of `bits` bits at bit `at`: the
    /// checksum `spec` of the bits from `start` to `end`, its low `bits`
    /// bits.
    #[allow(clippy::too_many_arguments)]
    pub fn patch_checksum(
        &mut self,
        spec: &Checksum,
        start: usize,
        end: usize,
        at: usize,
        bits: u32,
        le: bool,
        field: &'static str,
    ) -> Result<()> {
        let v = checksum(spec, self.buf.as_bytes(), start, end, field)?;
        let pos = self.pos;
        self.pos = at;
        self.uint_order(bits, le, v & mask(bits));
        self.pos = pos;
        Ok(())
    }

    /// Prepares to encode a value of `bits` bits (at most 64) that is split
    /// into segments: the writer moves to the byte boundary at or before
    /// its position, so the value is encoded as it would be at the start of
    /// a buffer, over bits that [`BitWriter::unstash`] restores.
    pub fn stash(&mut self, bits: u32) -> Stash {
        let at = self.pos / 8 * 8;
        let s = Stash {
            pos: self.pos,
            at,
            len: self.buf.len(),
            bits,
            saved: self.peek(at, bits),
        };
        self.pos = at;
        s
    }

    /// Returns the value encoded since [`BitWriter::stash`], its last bit
    /// least significant, and restores the bits and position it saved.
    pub fn unstash(&mut self, s: Stash) -> u64 {
        let v = self.peek(s.at, s.bits);
        self.pos = s.at;
        self.uint(s.bits, s.saved);
        if self.buf.len() > s.len {
            self.buf.resize(s.len);
        }
        self.pos = s.pos;
        v
    }

    /// The `bits` bits at `pos`; those past the buffer's end are zero.
    fn peek(&self, pos: usize, bits: u32) -> u64 {
        let data = self.buf.as_bytes();
        (pos..pos + bits as usize).fold(0, |v, p| {
            let b = data.get(p / 8).map_or(0, |b| (b >> (7 - p % 8)) & 1);
            (v << 1) | u64::from(b)
        })
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

/// How the groups of 8 bits of a field combine (see
/// [`BitReader::uint_bits`]).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ByteOrder {
    /// The first group is the most significant (big endian).
    Big,
    /// The first group is the least significant (little endian).
    Little,
    /// The significance of each (whole) byte, as an XTCE byte order list
    /// gives it: 0 for the least significant.
    List(&'static [u8]),
}

/// The value of groups of bits ((value, length), in stream order) combined
/// in `order`.
fn combine(groups: &[(u8, u32)], order: ByteOrder) -> u128 {
    let mut v = 0u128;
    match order {
        ByteOrder::Big => {
            for &(g, n) in groups {
                v = (v << n) | g as u128;
            }
        }
        ByteOrder::Little => {
            let mut shift = 0;
            for &(g, n) in groups {
                v |= (g as u128) << shift;
                shift += n;
            }
        }
        ByteOrder::List(sig) => {
            for (&(g, _), &s) in groups.iter().zip(sig) {
                v |= (g as u128) << (8 * s as u32);
            }
        }
    }
    v
}

/// Group `j` of `v`, whose groups have `lengths` in stream order: the
/// inverse of [`combine`].
fn split(v: u128, lengths: &[u32], j: usize, order: ByteOrder) -> u8 {
    let n = lengths[j];
    let shift: u32 = match order {
        ByteOrder::Big => lengths[j + 1..].iter().sum(),
        ByteOrder::Little => lengths[..j].iter().sum(),
        ByteOrder::List(sig) => 8 * sig[j] as u32,
    };
    ((v >> shift) & ((1u128 << n) - 1)) as u8
}

// --- error detection ---------------------------------------------------------

/// An XTCE ErrorDetectCorrect algorithm; the manual describes each.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Checksum {
    /// A CRC of `width` (1 to 64) bits.
    Crc {
        width: u32,
        poly: u64,
        init: u64,
        final_xor: u64,
        reflect_data: bool,
        reflect_remainder: bool,
    },
    /// The sum of the bytes, modulo 2 to the given number of bits.
    Sum(u32),
    /// The BSD `sum` checksum.
    UnixSum,
    /// Fletcher's checksum of the given number of bits.
    Fletcher(u32),
    Adler32,
    /// Check digits of decimal digits, one in every 4 bits.
    Luhn,
    Verhoeff,
    Damm,
    /// The exclusive or of words of the given number of bits.
    Xor(u32),
    /// The parity bit; true for odd parity.
    Parity(bool),
}

const VERHOEFF_D: [[u8; 10]; 10] = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
];
const VERHOEFF_P: [[u8; 10]; 8] = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
];
const VERHOEFF_INV: [u8; 10] = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9];
const DAMM: [[u8; 10]; 10] = [
    [0, 3, 1, 7, 5, 9, 8, 6, 4, 2],
    [7, 0, 9, 2, 1, 5, 4, 8, 6, 3],
    [4, 2, 0, 6, 8, 7, 1, 3, 5, 9],
    [1, 7, 5, 0, 9, 8, 3, 4, 2, 6],
    [6, 1, 2, 3, 0, 4, 5, 9, 7, 8],
    [3, 6, 7, 4, 2, 0, 9, 5, 8, 1],
    [5, 8, 6, 9, 7, 2, 0, 1, 3, 4],
    [8, 9, 4, 5, 3, 6, 2, 0, 1, 7],
    [9, 4, 3, 8, 6, 1, 7, 2, 0, 5],
    [2, 5, 8, 1, 4, 3, 6, 7, 9, 0],
];

/// The bit at position `q` of `data`, most significant bit first.
fn bit_at(data: &[u8], q: usize) -> u64 {
    ((data[q / 8] >> (7 - q % 8)) & 1) as u64
}

/// Calls `f` with each `width`-bit word of the bits from `start` to `end`,
/// and the bits of it that are data; a last partial word is padded with
/// zero bits.
fn for_words(
    data: &[u8],
    start: usize,
    end: usize,
    width: u32,
    mut f: impl FnMut(u64, u32),
) {
    let mut q = start;
    while q < end {
        let n = (end - q).min(width as usize) as u32;
        let mut w = 0u64;
        for i in 0..n as usize {
            w = (w << 1) | bit_at(data, q + i);
        }
        f(w << (width - n), n);
        q += n as usize;
    }
}

fn reflect(v: u64, bits: u32) -> u64 {
    if bits == 0 {
        0
    } else {
        v.reverse_bits() >> (64 - bits)
    }
}

/// The decimal digits, one in every 4 bits, from `start` to `end`.
fn digits(
    data: &[u8],
    start: usize,
    end: usize,
    field: &'static str,
    mut f: impl FnMut(u64),
) -> Result<()> {
    let mut bad = false;
    for_words(data, start, end, 4, |d, _| {
        bad |= d > 9;
        f(d);
    });
    if bad {
        Err(PacketError::InvalidValue { field })
    } else {
        Ok(())
    }
}

/// An XTCE ErrorDetectCorrect algorithm over the bits from `start` to `end`
/// of `data`.
pub fn checksum(
    spec: &Checksum,
    data: &[u8],
    start: usize,
    end: usize,
    field: &'static str,
) -> Result<u64> {
    let end = end.max(start);
    Ok(match *spec {
        Checksum::Crc {
            width,
            poly,
            init,
            final_xor,
            reflect_data,
            reflect_remainder,
        } => {
            let (top, m) = (1u64 << (width - 1), mask(width));
            let mut reg = init;
            for_words(data, start, end, 8, |g, n| {
                let g = g >> (8 - n);
                let g = if reflect_data { reflect(g, n) } else { g };
                for i in (0..n).rev() {
                    let carry = (reg & top != 0) != ((g >> i) & 1 == 1);
                    reg = (reg << 1) & m;
                    if carry {
                        reg ^= poly;
                    }
                }
            });
            if reflect_remainder {
                reg = reflect(reg, width);
            }
            reg ^ final_xor
        }
        Checksum::Sum(bits) => {
            let mut c = 0u64;
            for_words(data, start, end, 8, |b, _| c = c.wrapping_add(b));
            c & mask(bits)
        }
        Checksum::UnixSum => {
            let mut c = 0u64;
            for_words(data, start, end, 8, |b, _| {
                c = ((c >> 1) + ((c & 1) << 15) + b) & 0xffff;
            });
            c
        }
        Checksum::Fletcher(bits) => {
            let half = bits / 2;
            let m = mask(half);
            let (mut a, mut b) = (0u64, 0u64);
            for_words(data, start, end, half, |w, _| {
                a = (a + w) % m;
                b = (b + a) % m;
            });
            (b << half) | a
        }
        Checksum::Adler32 => {
            let (mut a, mut b) = (1u64, 0u64);
            for_words(data, start, end, 8, |w, _| {
                a = (a + w) % 65521;
                b = (b + a) % 65521;
            });
            (b << 16) | a
        }
        Checksum::Luhn => {
            // The digits are counted from the right; there is one in every
            // 4 bits, the last padded.
            let n = (end - start).div_ceil(4);
            let (mut k, mut total) = (0usize, 0u64);
            digits(data, start, end, field, |d| {
                let mut d = d;
                if (n - 1 - k) % 2 == 0 {
                    d *= 2;
                    if d > 9 {
                        d -= 9;
                    }
                }
                total += d;
                k += 1;
            })?;
            (10 - total % 10) % 10
        }
        Checksum::Verhoeff => {
            // Verhoeff walks the digits from the right, reading each where
            // it lies; a last partial digit is padded.
            let n = (end - start).div_ceil(4);
            let mut c = 0usize;
            for i in 0..n {
                let q = start + 4 * (n - 1 - i);
                let mut d = 0usize;
                for b in 0..4 {
                    let bit = if q + b < end { bit_at(data, q + b) } else { 0 };
                    d = (d << 1) | bit as usize;
                }
                if d > 9 {
                    return Err(PacketError::InvalidValue { field });
                }
                c = VERHOEFF_D[c][VERHOEFF_P[(i + 1) % 8][d] as usize] as usize;
            }
            VERHOEFF_INV[c] as u64
        }
        Checksum::Damm => {
            let mut c = 0usize;
            digits(data, start, end, field, |d| {
                c = DAMM[c][(d as usize).min(9)] as usize;
            })?;
            c as u64
        }
        Checksum::Xor(bits) => {
            let mut c = 0u64;
            for_words(data, start, end, bits, |w, _| c ^= w);
            c
        }
        Checksum::Parity(odd) => {
            let mut ones = odd as u64;
            for_words(data, start, end, 8, |b, _| {
                ones += b.count_ones() as u64
            });
            ones & 1
        }
    })
}

// --- integers ----------------------------------------------------------------

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
    let sign = bits < 64 && (raw >> (bits - 1)) & 1 == 1
        || bits == 64 && (raw >> 63) == 1;
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
pub fn from_signed(
    v: i64,
    bits: u32,
    enc: IntEncoding,
    field: &'static str,
) -> Result<u64> {
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

/// The value of a BCD field: packed BCD has a decimal digit in every 4
/// bits, and (unpacked) BCD one in the low 4 bits of every byte, whose high
/// 4 bits are zero; the first digit is the most significant.  Fails with
/// `InvalidValue` for any other bits.
pub fn bcd_decode(
    raw: u64,
    bits: u32,
    packed: bool,
    field: &'static str,
) -> Result<u64> {
    let width = if packed { 4 } else { 8 };
    let mut v = 0u64;
    let mut shift = bits;
    while shift >= width {
        shift -= width;
        let d = (raw >> shift) & mask(width);
        if d > 9 {
            return Err(PacketError::InvalidValue { field });
        }
        v = v * 10 + d;
    }
    Ok(v)
}

/// The BCD field holding `v`; fails with `OutOfRange` unless the field's
/// digits hold it.
pub fn bcd_encode(
    v: u64,
    bits: u32,
    packed: bool,
    field: &'static str,
) -> Result<u64> {
    let width = if packed { 4 } else { 8 };
    let digits = bits / width;
    if digits < 20 && v >= 10u64.pow(digits) {
        return Err(PacketError::OutOfRange { field });
    }
    let (mut raw, mut v) = (0u64, v);
    for i in 0..digits {
        raw |= (v % 10) << (i * width);
        v /= 10;
    }
    Ok(raw)
}

/// Rounds an engineering value (half away from zero) to a raw integer field.
pub fn f64_to_int(
    x: f64,
    bits: u32,
    enc: IntEncoding,
    field: &'static str,
) -> Result<u64> {
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
pub fn round_to_int(
    y: f64,
    lo: f64,
    lim: f64,
    field: &'static str,
) -> Result<f64> {
    let r = y.round();
    if r >= lo && r < lim {
        Ok(r)
    } else {
        Err(PacketError::OutOfRange { field })
    }
}

// --- floats ------------------------------------------------------------------

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

/// A floating-point encoding of XTCE's `FloatDataEncoding`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FloatFormat {
    /// IEEE 754 binary formats: 16, 32, 64, 80 (x87 extended) or 128 bits.
    Ieee,
    /// MIL-STD-1750A: 32 or 48 bits.
    Mil1750a,
    /// DEC (VAX): 32 bits for F_floating, 64 for D_floating.
    Dec,
    /// IBM hexadecimal: 32, 64 or 128 bits.
    Ibm,
    /// TI TMS320C3x: 16, 32 or 40 bits.
    Ti,
}

/// `m / 2**shift` rounded to the nearest integer, ties to even; `m * 2**-shift`
/// when `shift` is negative.
fn round_shift(m: u128, shift: i32) -> u128 {
    if shift <= 0 {
        return m << (-shift) as u32;
    }
    if shift > 128 {
        return 0;
    }
    let (q, r, half) = if shift == 128 {
        (0, m, 1u128 << 127)
    } else {
        (m >> shift, m & ((1u128 << shift) - 1), 1u128 << (shift - 1))
    };
    q + (r > half || (r == half && q & 1 == 1)) as u128
}

/// `(-1)**neg * m * 2**e` rounded to the nearest double, ties to even;
/// infinite when too large.
fn to_f64(neg: bool, m: u128, e: i32) -> f64 {
    let sign = if neg { -1.0 } else { 1.0 };
    if m == 0 {
        return 0.0 * sign;
    }
    let top = e + 127 - m.leading_zeros() as i32; // m * 2**e < 2**(top + 1)
    if top > 1023 {
        return f64::INFINITY * sign;
    }
    let mut low = top.max(-1022) - 52;
    let mut q = round_shift(m, low - e);
    if q >> 53 != 0 {
        q >>= 1;
        low += 1;
    }
    let bits = if q >> 52 == 0 {
        q as u64 // subnormal
    } else {
        let biased = low + 52 + 1023;
        if biased >= 2047 {
            return f64::INFINITY * sign;
        }
        ((biased as u64) << 52) | (q as u64 & ((1 << 52) - 1))
    };
    f64::from_bits(bits) * sign
}

/// `(m, e, power)` for finite nonzero `x`: `|x| == m * 2**e` with `m` of 53
/// bits, and whether `|x|` is a power of two.
fn magnitude(x: f64) -> (u128, i32, bool) {
    let b = x.to_bits();
    let exp = ((b >> 52) & 0x7ff) as i32;
    let frac = (b & ((1 << 52) - 1)) as u128;
    let (mut m, mut e) = if exp == 0 {
        (frac, -1074)
    } else {
        (frac | (1 << 52), exp - 1075)
    };
    let shift = m.leading_zeros() as i32 - 75; // to 53 bits
    m <<= shift;
    e -= shift;
    (m, e, m == 1 << 52)
}

fn signed(raw: u128, bits: u32) -> i128 {
    let shift = 128 - bits;
    ((raw << shift) as i128) >> shift
}

fn ieee_params(bits: u32) -> (u32, u32, bool) {
    match bits {
        16 => (5, 10, false),
        32 => (8, 23, false),
        64 => (11, 52, false),
        80 => (15, 64, true),
        _ => (15, 112, false),
    }
}

fn ieee_decode(raw: u128, bits: u32) -> f64 {
    let (ebits, fbits, explicit) = ieee_params(bits);
    let neg = (raw >> (bits - 1)) & 1 == 1;
    let exp = ((raw >> fbits) & ((1 << ebits) - 1)) as i32;
    let mut frac = raw & ((1u128 << fbits) - 1);
    let bias = (1 << (ebits - 1)) - 1;
    if exp == (1 << ebits) - 1 {
        if explicit {
            frac &= (1 << 63) - 1;
        }
        if frac != 0 {
            return f64::NAN;
        }
        return if neg {
            f64::NEG_INFINITY
        } else {
            f64::INFINITY
        };
    }
    if explicit {
        return to_f64(neg, frac, exp.max(1) - bias - 63);
    }
    if exp == 0 {
        return to_f64(neg, frac, 1 - bias - fbits as i32);
    }
    to_f64(neg, frac | (1 << fbits), exp - bias - fbits as i32)
}

fn ieee_encode(x: f64, bits: u32) -> u128 {
    let (ebits, fbits, explicit) = ieee_params(bits);
    let sign = if x.is_sign_negative() {
        1u128 << (bits - 1)
    } else {
        0
    };
    let top = (1u128 << ebits) - 1;
    let integer = if explicit { 1u128 << 63 } else { 0 };
    if x.is_nan() {
        return sign
            | (top << fbits)
            | integer
            | (1u128 << (fbits - 1 - explicit as u32));
    }
    if x.is_infinite() {
        return sign | (top << fbits) | integer;
    }
    if x == 0.0 {
        return sign;
    }
    let bias = (1i32 << (ebits - 1)) - 1;
    let p = (fbits + !explicit as u32) as i32;
    let (m, e, _) = magnitude(x);
    let mut low = (e + 52).max(1 - bias) - (p - 1);
    let mut q = round_shift(m, low - e);
    if q >> p != 0 {
        q >>= 1;
        low += 1;
    }
    if q >> (p - 1) == 0 {
        return sign | q;
    }
    let biased = (low + p - 1 + bias) as u128;
    if biased >= top {
        return sign | (top << fbits) | integer;
    }
    let f = if explicit { q } else { q - (1u128 << fbits) };
    sign | (biased << fbits) | f
}

fn mil1750a_decode(raw: u128, bits: u32) -> f64 {
    let (mant, exp, mbits) = if bits == 32 {
        (raw >> 8, raw & 0xff, 24)
    } else {
        (((raw >> 24) << 16) | (raw & 0xffff), (raw >> 16) & 0xff, 40)
    };
    let mant = signed(mant, mbits);
    let exp = signed(exp, 8) as i32;
    to_f64(mant < 0, mant.unsigned_abs(), exp - (mbits as i32 - 1))
}

fn mil1750a_encode(x: f64, bits: u32, field: &'static str) -> Result<u128> {
    let mbits = if bits == 32 { 24 } else { 40 };
    let f = mbits - 1;
    if x == 0.0 {
        return Ok(0);
    }
    let (m, e, power) = magnitude(x);
    let top = e + 52;
    let (mant, exp) = if x > 0.0 {
        let mut exp = top + 1;
        let mut mag = round_shift(m, exp - f - e);
        if mag == 1 << f {
            mag >>= 1;
            exp += 1;
        }
        (mag as i128, exp)
    } else {
        // Negative mantissas are normalized to [-1, -1/2).
        let mut exp = if power { top } else { top + 1 };
        let mut mag = round_shift(m, exp - f - e);
        if mag == 1 << (f - 1) {
            mag = 1 << f;
            exp -= 1;
        }
        (-(mag as i128), exp)
    };
    if exp > 127 {
        return Err(PacketError::OutOfRange { field });
    }
    let (mant, exp) = if exp < -128 {
        // Too small to normalize: unnormalized at the smallest exponent.
        let mag = round_shift(m, -128 - f - e) as i128;
        (if x > 0.0 { mag } else { -mag }, -128)
    } else {
        (mant, exp)
    };
    let mant = (mant as u128) & ((1 << mbits) - 1);
    let exp = (exp as u128) & 0xff;
    Ok(if bits == 32 {
        (mant << 8) | exp
    } else {
        ((mant >> 16) << 24) | (exp << 16) | (mant & 0xffff)
    })
}

fn dec_decode(raw: u128, bits: u32, field: &'static str) -> Result<f64> {
    let f = if bits == 32 { 23 } else { 55 };
    let neg = raw >> (bits - 1) == 1;
    let exp = ((raw >> f) & 0xff) as i32;
    if exp == 0 {
        if neg {
            return Err(PacketError::InvalidValue { field }); // reserved
        }
        return Ok(0.0);
    }
    let frac = (raw & ((1 << f) - 1)) | (1 << f);
    Ok(to_f64(neg, frac, exp - 129 - f as i32))
}

fn dec_encode(x: f64, bits: u32, field: &'static str) -> Result<u128> {
    let f = if bits == 32 { 23 } else { 55 };
    if x == 0.0 {
        return Ok(0);
    }
    let (m, e, _) = magnitude(x);
    let mut top = e + 52;
    let mut q = round_shift(m, top - f - e);
    if q >> (f + 1) != 0 {
        q >>= 1;
        top += 1;
    }
    let exp = top + 129;
    if exp > 255 {
        return Err(PacketError::OutOfRange { field });
    }
    if exp < 1 {
        return Ok(0);
    }
    let sign = ((x < 0.0) as u128) << (bits - 1);
    Ok(sign | ((exp as u128) << f) | (q - (1 << f)))
}

fn ibm_decode(raw: u128, bits: u32) -> f64 {
    let neg = raw >> (bits - 1) == 1;
    let hi = if bits == 128 { raw >> 64 } else { raw };
    let exp = ((hi >> (bits.min(64) - 8)) & 0x7f) as i32;
    let (frac, f) = if bits == 128 {
        let m56 = (1u128 << 56) - 1;
        (((hi & m56) << 56) | (raw & m56), 112)
    } else {
        (raw & ((1 << (bits - 8)) - 1), bits as i32 - 8)
    };
    to_f64(neg, frac, 4 * (exp - 64) - f)
}

fn ibm_encode(x: f64, bits: u32, field: &'static str) -> Result<u128> {
    let f = if bits == 128 { 112 } else { bits as i32 - 8 };
    if x == 0.0 {
        return Ok(0);
    }
    let (m, e, _) = magnitude(x);
    let mut h = (e + 52).div_euclid(4) + 1;
    let mut q = round_shift(m, 4 * h - f - e);
    if q >> f != 0 {
        q >>= 4;
        h += 1;
    }
    let mut exp = h + 64;
    if exp > 127 {
        return Err(PacketError::OutOfRange { field });
    }
    if exp < 0 {
        // Too small to normalize: unnormalized at the smallest exponent.
        exp = 0;
        q = round_shift(m, -256 - f - e);
    }
    let sign = (x < 0.0) as u128;
    if bits != 128 {
        return Ok((sign << (bits - 1)) | ((exp as u128) << f) | q);
    }
    // The low half has the sign, and a characteristic 14 less (modulo 128).
    let m56 = (1u128 << 56) - 1;
    let hi = (sign << 63) | ((exp as u128) << 56) | (q >> 56);
    let lo = (sign << 63) | ((((exp - 14) & 0x7f) as u128) << 56) | (q & m56);
    Ok((hi << 64) | lo)
}

fn ti_params(bits: u32) -> (u32, u32) {
    match bits {
        16 => (4, 11),
        32 => (8, 23),
        _ => (8, 31),
    }
}

fn ti_decode(raw: u128, bits: u32) -> f64 {
    let (ebits, f) = ti_params(bits);
    let exp = signed(raw >> (f + 1), ebits) as i32;
    if exp == -(1 << (ebits - 1)) {
        return 0.0;
    }
    let frac = raw & ((1 << f) - 1);
    if (raw >> f) & 1 == 1 {
        to_f64(true, (2u128 << f) - frac, exp - f as i32)
    } else {
        to_f64(false, (1u128 << f) | frac, exp - f as i32)
    }
}

fn ti_encode(x: f64, bits: u32, field: &'static str) -> Result<u128> {
    let (ebits, f) = ti_params(bits);
    let lo = -(1i32 << (ebits - 1)); // the exponent of zero
    let emask = (1u128 << ebits) - 1;
    let zero = ((lo as u128) & emask) << (f + 1);
    if x == 0.0 {
        return Ok(zero);
    }
    let (m, e, power) = magnitude(x);
    let top = e + 52;
    let fi = f as i32;
    let (sign, frac, exp) = if x > 0.0 {
        let mut exp = top;
        let mut q = round_shift(m, exp - fi - e);
        if q >> (f + 1) != 0 {
            q >>= 1;
            exp += 1;
        }
        (0u128, q - (1 << f), exp)
    } else {
        // |x| / 2**exp is in (1, 2]: x == (-2 + frac) * 2**exp.
        let mut exp = if power { top - 1 } else { top };
        let mut q = round_shift(m, exp - fi - e);
        if q == 1 << f {
            exp -= 1;
            q = 2 << f;
        }
        (1u128, (2u128 << f) - q, exp)
    };
    if exp > -lo - 1 {
        return Err(PacketError::OutOfRange { field });
    }
    if exp <= lo {
        return Ok(zero);
    }
    Ok((((exp as u128) & emask) << (f + 1)) | (sign << f) | frac)
}

/// The value of a float field of `bits` bits in `fmt`, rounded to the
/// nearest double.  Fails with `InvalidValue` for a DEC reserved operand.
pub fn float_decode(
    fmt: FloatFormat,
    bits: u32,
    raw: u128,
    field: &'static str,
) -> Result<f64> {
    Ok(match fmt {
        FloatFormat::Ieee => ieee_decode(raw, bits),
        FloatFormat::Mil1750a => mil1750a_decode(raw, bits),
        FloatFormat::Dec => return dec_decode(raw, bits, field),
        FloatFormat::Ibm => ibm_decode(raw, bits),
        FloatFormat::Ti => ti_decode(raw, bits),
    })
}

/// The float field nearest `x` (ties to even), as [`float_decode`] reads
/// it.  IEEE formats overflow to infinity; the others, which have no
/// infinity or NaN, fail with `OutOfRange` for values too large for them
/// and for infinities and NaN, and encode values too small for them as
/// zero.
pub fn float_encode(
    fmt: FloatFormat,
    bits: u32,
    x: f64,
    field: &'static str,
) -> Result<u128> {
    if fmt == FloatFormat::Ieee {
        return Ok(ieee_encode(x, bits));
    }
    if !x.is_finite() {
        return Err(PacketError::OutOfRange { field });
    }
    match fmt {
        FloatFormat::Mil1750a => mil1750a_encode(x, bits, field),
        FloatFormat::Dec => dec_encode(x, bits, field),
        FloatFormat::Ibm => ibm_encode(x, bits, field),
        _ => ti_encode(x, bits, field),
    }
}

// --- calibrators -------------------------------------------------------------

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

/// A spline whose segments have their own orders: the segment from point
/// `i` has order `orders[i]`, and one of order k interpolates through k + 1
/// points, starting at point `i` or as close after it as the points allow.
/// Outside the points, the first or last segment extrapolates.
pub fn spline_orders(
    x: f64,
    points: &[(f64, f64)],
    orders: &[u32],
    extrapolate: bool,
    field: &'static str,
) -> Result<f64> {
    let n = points.len();
    if (x < points[0].0 || x > points[n - 1].0) && !extrapolate {
        return Err(PacketError::Calibration { field });
    }
    let mut i = 0;
    while i < n - 2 && x >= points[i + 1].0 {
        i += 1;
    }
    let k = orders[i] as usize;
    if k == 0 {
        return Ok(if x >= points[n - 1].0 {
            points[n - 1].1
        } else {
            points[i].1
        });
    }
    let k = k.min(n - 1);
    let j = i.min(n - 1 - k);
    let mut y = 0.0;
    for m in j..=j + k {
        let mut t = points[m].1;
        for q in j..=j + k {
            if q != m {
                t *= (x - points[q].0) / (points[m].0 - points[q].0);
            }
        }
        y += t;
    }
    Ok(y)
}

/// An operator of a MathOperationCalibrator, for [`math`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MathOperator {
    Div,
    Mod,
    Pow,
    RevPow,
    Ln,
    Log10,
    Exp,
    Inv,
    Factorial,
    Tan,
    Cos,
    Sin,
    Atan,
    Atan2,
    Acos,
    Asin,
    Tanh,
    Cosh,
    Sinh,
    Atanh,
    Acosh,
    Asinh,
    Shl,
    Shr,
    BitAnd,
    BitOr,
    BitXor,
    BitNot,
    And,
    Or,
    Not,
    Abs,
    EuclidDiv,
    Trunc,
    Gt,
    Ge,
    Lt,
    Le,
    Eq,
    Ne,
    Min,
    Max,
}

// Rust's standard library computes these three with its own formulas, not
// the C library's, which the C and Python runtimes use; they would differ in
// the last bit.  Every other function of `math` calls the C library already.
extern "C" {
    fn asinh(x: f64) -> f64;
    fn acosh(x: f64) -> f64;
    fn atanh(x: f64) -> f64;
}

/// `x` truncated to a 64-bit integer, if it has such a value.
fn to_i64(x: f64) -> Option<i64> {
    if x.is_finite()
        && (-9223372036854775808.0..9223372036854775808.0).contains(&x)
    {
        Some(x.trunc() as i64)
    } else {
        None
    }
}

fn truth(b: bool) -> f64 {
    if b {
        1.0
    } else {
        0.0
    }
}

/// The value of a MathOperationCalibrator operator on the operands `a` and
/// `b` (`b` is ignored by unary operators); identical in every runtime.
/// `+`, `-` and `*` are written as plain arithmetic.
pub fn math(op: MathOperator, a: f64, b: f64) -> f64 {
    use MathOperator::*;
    match op {
        Div => a / b,
        Mod => {
            let mut m = if b != 0.0 && a.is_finite() {
                a % b
            } else {
                f64::NAN
            };
            if m != 0.0 && (m < 0.0) != (b < 0.0) {
                m += b;
            }
            m
        }
        EuclidDiv => {
            if b == 0.0 || !a.is_finite() || !b.is_finite() {
                return f64::NAN;
            }
            let mut r = a % b;
            if r < 0.0 {
                r += b.abs();
            }
            ((a - r) / b).round()
        }
        Pow => a.powf(b),
        RevPow => b.powf(a),
        Ln => a.ln(),
        Log10 => a.log10(),
        Exp => a.exp(),
        Inv => 1.0 / a,
        Factorial => {
            if !(a >= 0.0) || a != a.floor() {
                return f64::NAN;
            }
            if a > 170.0 {
                return f64::INFINITY;
            }
            let mut r = 1.0;
            for i in 2..=(a as u32) {
                r *= i as f64;
            }
            r
        }
        Tan => a.tan(),
        Cos => a.cos(),
        Sin => a.sin(),
        Atan => a.atan(),
        Atan2 => b.atan2(a),
        Acos => a.acos(),
        Asin => a.asin(),
        Tanh => a.tanh(),
        Cosh => a.cosh(),
        Sinh => a.sinh(),
        // SAFETY: the C library's functions take and return a double, and
        // have no other effects.
        Atanh => unsafe { atanh(a) },
        Acosh => unsafe { acosh(a) },
        Asinh => unsafe { asinh(a) },
        Abs => a.abs(),
        Trunc => a.trunc(),
        Not => truth(a == 0.0),
        And => truth(a != 0.0 && b != 0.0),
        Or => truth(a != 0.0 || b != 0.0),
        Gt => truth(a > b),
        Ge => truth(a >= b),
        Lt => truth(a < b),
        Le => truth(a <= b),
        Eq => truth(a == b),
        Ne => truth(a != b),
        Min => {
            if b < a {
                b
            } else {
                a
            }
        }
        Max => {
            if b > a {
                b
            } else {
                a
            }
        }
        BitNot => to_i64(a).map_or(f64::NAN, |i| !i as f64),
        _ => match (to_i64(a), to_i64(b)) {
            (Some(i), Some(j)) => match op {
                Shl | Shr if !(0..64).contains(&j) => f64::NAN,
                Shl => i.wrapping_shl(j as u32) as f64,
                Shr => (i >> j) as f64,
                BitAnd => (i & j) as f64,
                BitOr => (i | j) as f64,
                _ => (i ^ j) as f64,
            },
            _ => f64::NAN,
        },
    }
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

// --- sizes from other fields -------------------------------------------------

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
    if !(y >= 0.0)
        || y.fract() != 0.0
        || (y / unit).fract() != 0.0
        || y / unit > max as f64
    {
        return Err(PacketError::BadSize { field });
    }
    Ok((y / unit) as usize)
}

/// Element `i` of `v`, an array whose count another field gives.  Fails with
/// `BadSize`, naming `field`, if `v` has no element `i`.
pub fn element<'a, T>(
    v: &'a [T],
    i: usize,
    field: &'static str,
) -> Result<&'a T> {
    v.get(i).ok_or(PacketError::BadSize { field })
}

/// The value of the field that gives a size or count of `n` (the inverse of
/// [`dynamic_size`] followed by multiplying by `factor`), for encoding a
/// field computed from the length of the value it sizes.  Fails with
/// `BadSize`, naming `field` (the sized value), when no whole number gives
/// exactly `n`.
pub fn size_source(
    n: usize,
    slope: f64,
    intercept: f64,
    unit: u32,
    factor: usize,
    field: &'static str,
) -> Result<i64> {
    let bad = PacketError::BadSize { field };
    if n % factor != 0 {
        return Err(bad);
    }
    let y = ((n / factor) as f64) * unit as f64;
    let v = (y - intercept) / slope;
    if !(v.abs() < 9.2e18) {
        return Err(bad);
    }
    let x = (v + 0.5).floor();
    // Accept only a value that decoding maps back to exactly `n`.
    if slope * x + intercept != y {
        return Err(bad);
    }
    Ok(x as i64)
}

// --- strings -----------------------------------------------------------------

/// A character set of XTCE's `StringDataEncoding`.  UTF-16 and UTF-32
/// without a byte order are read big endian unless they start with a byte
/// order mark, and written big endian without one.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Charset {
    Utf8,
    Ascii,
    Latin1,
    Windows1252,
    Utf16,
    Utf16Be,
    Utf16Le,
    Utf32,
    Utf32Be,
    Utf32Le,
}

impl Charset {
    /// The bytes in one code unit.
    pub fn unit(self) -> usize {
        match self {
            Charset::Utf16 | Charset::Utf16Be | Charset::Utf16Le => 2,
            Charset::Utf32 | Charset::Utf32Be | Charset::Utf32Le => 4,
            _ => 1,
        }
    }
}

/// Unicode code points of Windows-1252 bytes 0x80 to 0x9f; 0 for the five
/// bytes it leaves undefined.
const WINDOWS_1252: [u16; 32] = [
    0x20ac, 0, 0x201a, 0x0192, 0x201e, 0x2026, 0x2020, 0x2021, 0x02c6, 0x2030,
    0x0160, 0x2039, 0x0152, 0, 0x017d, 0, 0, 0x2018, 0x2019, 0x201c, 0x201d,
    0x2022, 0x2013, 0x2014, 0x02dc, 0x2122, 0x0161, 0x203a, 0x0153, 0, 0x017e,
    0x0178,
];

/// Layout of a string field.  Exactly one of `terminator` and `size_tag_bits`
/// is set.
#[derive(Debug, Clone, Copy)]
pub struct StringSpec {
    /// Bits the field always occupies, or `None` for variable-size strings.
    pub fixed_bits: Option<usize>,
    /// Upper bound on the bits the field occupies.
    pub max_bits: usize,
    /// The bytes that end the string, found only where a code unit starts.
    pub terminator: Option<&'static [u8]>,
    pub size_tag_bits: u32,
    pub charset: Charset,
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

/// The first position of `term` in `b` that starts a code unit of `unit`
/// bytes.
fn find_unit(b: &[u8], term: &[u8], unit: usize) -> Option<usize> {
    (0..=b.len().checked_sub(term.len())?)
        .step_by(unit)
        .find(|&i| &b[i..i + term.len()] == term)
}

pub fn read_string(
    r: &mut BitReader<'_>,
    spec: &StringSpec,
    field: &'static str,
) -> Result<String> {
    let mut wire = Vec::new();
    read_wire(r, spec, &mut wire, field)?;
    let mut b = Vec::new();
    decode_text(spec.charset, &wire, &mut b, field)?;
    String::from_utf8(b).map_err(|_| PacketError::InvalidString { field })
}

/// [`read_string`] into a string of at most `N` bytes, from at most `W`
/// bytes on the wire; fails with `TooLong` if it does not fit.
pub fn read_fixed_string<const N: usize, const W: usize>(
    r: &mut BitReader<'_>,
    spec: &StringSpec,
    field: &'static str,
) -> Result<FixedString<N>> {
    let mut wire = FixedBytes::<W>::new();
    read_wire(r, spec, &mut wire, field)?;
    let mut b = FixedBytes::new();
    decode_text(spec.charset, &wire, &mut b, field)?;
    FixedString::from_utf8(b).map_err(|_| PacketError::InvalidString { field })
}

/// Reads the bytes of a string's content, before its terminator.
fn read_wire<S: ByteSink>(
    r: &mut BitReader<'_>,
    spec: &StringSpec,
    b: &mut S,
    field: &'static str,
) -> Result<()> {
    let start = r.position();
    let unit = spec.charset.unit();
    match (spec.fixed_bits, spec.terminator) {
        (Some(bits), Some(term)) => {
            r.bytes_into(bits / 8, b, field)?;
            if let Some(i) = find_unit(b.bytes(), term, unit) {
                b.truncate(i);
            }
        }
        (Some(bits), None) => {
            let n = r.uint(spec.size_tag_bits, field)?;
            if !fits(n, spec.size_tag_bits, bits) {
                return Err(PacketError::TooLong { field });
            }
            r.bytes_into(n as usize, b, field)?;
            r.seek(start, bits as i64, field)?;
        }
        (None, Some(term)) => loop {
            if (b.len() + unit) * 8 > spec.max_bits {
                return Err(PacketError::TooLong { field });
            }
            r.bytes_into(unit, b, field)?;
            if let Some(k) = b.len().checked_sub(term.len()) {
                if k % unit == 0 && &b.bytes()[k..] == term {
                    b.truncate(k);
                    break;
                }
            }
        },
        (None, None) => {
            let n = r.uint(spec.size_tag_bits, field)?;
            if !fits(n, spec.size_tag_bits, spec.max_bits) {
                return Err(PacketError::TooLong { field });
            }
            r.bytes_into(n as usize, b, field)?;
        }
    }
    Ok(())
}

fn push_char<S: ByteSink>(
    out: &mut S,
    c: Option<char>,
    field: &'static str,
) -> Result<()> {
    let c = c.ok_or(PacketError::InvalidString { field })?;
    let mut buf = [0u8; 4];
    out.append(c.encode_utf8(&mut buf).as_bytes(), field)
}

/// Appends to `out` the UTF-8 of the string whose bytes in `cs` are `b`;
/// fails with `InvalidString` if they aren't valid.
fn decode_text<S: ByteSink>(
    cs: Charset,
    b: &[u8],
    out: &mut S,
    field: &'static str,
) -> Result<()> {
    let bad = PacketError::InvalidString { field };
    match cs {
        Charset::Utf8 => {
            std::str::from_utf8(b).map_err(|_| bad)?;
            out.append(b, field)
        }
        Charset::Ascii => {
            if !b.is_ascii() {
                return Err(bad);
            }
            out.append(b, field)
        }
        Charset::Latin1 => {
            for &c in b {
                push_char(out, Some(c as char), field)?;
            }
            Ok(())
        }
        Charset::Windows1252 => {
            for &c in b {
                let u = match c {
                    0x80..=0x9f => WINDOWS_1252[c as usize - 0x80] as u32,
                    _ => c as u32,
                };
                push_char(
                    out,
                    if u == 0 && c != 0 {
                        None
                    } else {
                        char::from_u32(u)
                    },
                    field,
                )?;
            }
            Ok(())
        }
        Charset::Utf16 | Charset::Utf16Be | Charset::Utf16Le => {
            let (mut b, mut le) = (b, cs == Charset::Utf16Le);
            if cs == Charset::Utf16 && b.len() >= 2 {
                if b[..2] == [0xfe, 0xff] {
                    b = &b[2..];
                } else if b[..2] == [0xff, 0xfe] {
                    b = &b[2..];
                    le = true;
                }
            }
            if b.len() % 2 != 0 {
                return Err(bad);
            }
            let units = b.chunks(2).map(|u| {
                if le {
                    u16::from_le_bytes([u[0], u[1]])
                } else {
                    u16::from_be_bytes([u[0], u[1]])
                }
            });
            for c in char::decode_utf16(units) {
                push_char(out, c.ok(), field)?;
            }
            Ok(())
        }
        _ => {
            let (mut b, mut le) = (b, cs == Charset::Utf32Le);
            if cs == Charset::Utf32 && b.len() >= 4 {
                if b[..4] == [0, 0, 0xfe, 0xff] {
                    b = &b[4..];
                } else if b[..4] == [0xff, 0xfe, 0, 0] {
                    b = &b[4..];
                    le = true;
                }
            }
            if b.len() % 4 != 0 {
                return Err(bad);
            }
            for u in b.chunks(4) {
                let u = [u[0], u[1], u[2], u[3]];
                let v = if le {
                    u32::from_le_bytes(u)
                } else {
                    u32::from_be_bytes(u)
                };
                push_char(out, char::from_u32(v), field)?;
            }
            Ok(())
        }
    }
}

/// The byte of `c` in an 8-bit character set, if it has one.
fn byte_of(cs: Charset, c: char) -> Option<u8> {
    let u = c as u32;
    match cs {
        Charset::Ascii if u < 0x80 => Some(u as u8),
        Charset::Latin1 if u < 0x100 => Some(u as u8),
        Charset::Windows1252 if u < 0x80 || (0xa0..0x100).contains(&u) => {
            Some(u as u8)
        }
        Charset::Windows1252 => WINDOWS_1252
            .iter()
            .position(|&w| w != 0 && w as u32 == u)
            .map(|i| 0x80 + i as u8),
        _ => None,
    }
}

/// Calls `f` with each byte of `s` in `cs`, and returns how many there
/// are; fails with `InvalidString` if `cs` can't hold a character of `s`.
fn wire_bytes(
    cs: Charset,
    s: &str,
    mut f: impl FnMut(u8),
    field: &'static str,
) -> Result<usize> {
    let mut n = 0;
    let mut put = |bytes: &[u8]| {
        for &b in bytes {
            f(b);
        }
        n += bytes.len();
    };
    match cs {
        Charset::Utf8 => put(s.as_bytes()),
        Charset::Ascii | Charset::Latin1 | Charset::Windows1252 => {
            for c in s.chars() {
                let b = byte_of(cs, c)
                    .ok_or(PacketError::InvalidString { field })?;
                put(&[b]);
            }
        }
        Charset::Utf16 | Charset::Utf16Be | Charset::Utf16Le => {
            let mut buf = [0u16; 2];
            for c in s.chars() {
                for &u in c.encode_utf16(&mut buf).iter() {
                    put(&if cs == Charset::Utf16Le {
                        u.to_le_bytes()
                    } else {
                        u.to_be_bytes()
                    });
                }
            }
        }
        _ => {
            for c in s.chars() {
                let u = c as u32;
                put(&if cs == Charset::Utf32Le {
                    u.to_le_bytes()
                } else {
                    u.to_be_bytes()
                });
            }
        }
    }
    Ok(n)
}

/// Whether `term` occurs in `s`'s bytes in `cs` where a code unit starts.
fn contains_term(cs: Charset, s: &str, term: &[u8]) -> Result<bool> {
    let (unit, t) = (cs.unit(), term.len());
    let mut window = [0u8; 16];
    let (mut i, mut found) = (0usize, false);
    wire_bytes(
        cs,
        s,
        |b| {
            window.copy_within(1.., 0);
            window[15] = b;
            i += 1;
            if i >= t && (i - t) % unit == 0 && &window[16 - t..] == term {
                found = true;
            }
        },
        "",
    )?;
    Ok(found)
}

pub fn write_string(
    w: &mut BitWriter<'_>,
    spec: &StringSpec,
    s: &str,
    field: &'static str,
) -> Result<()> {
    let cs = spec.charset;
    let len = wire_bytes(cs, s, |_| {}, field)?;
    let tag = spec.size_tag_bits as usize;
    match spec.terminator {
        Some(term) => {
            if contains_term(cs, s, term).unwrap_or(true) {
                return Err(PacketError::InvalidString { field });
            }
            let room = spec.fixed_bits.unwrap_or(spec.max_bits) / 8;
            // A fixed-size string may fill its field; otherwise the
            // terminator follows it.
            let need = if spec.fixed_bits.is_some() && len == room {
                room
            } else {
                len + term.len()
            };
            if need > room {
                return Err(PacketError::TooLong { field });
            }
            write_wire(w, cs, s);
            if len < room {
                w.bytes(term);
            }
            if spec.fixed_bits.is_some() {
                w.zeros((room - need) * 8);
            }
        }
        None => {
            let room = spec.fixed_bits.unwrap_or(spec.max_bits);
            if len * 8 + tag > room || len as u64 > mask(spec.size_tag_bits) {
                return Err(PacketError::TooLong { field });
            }
            w.uint(spec.size_tag_bits, len as u64);
            write_wire(w, cs, s);
            if let Some(bits) = spec.fixed_bits {
                w.zeros(bits - tag - len * 8);
            }
        }
    }
    Ok(())
}

/// Writes the bytes of `s` in `cs`, which can hold every character of it.
fn write_wire(w: &mut BitWriter<'_>, cs: Charset, s: &str) {
    if cs == Charset::Utf8 {
        w.bytes(s.as_bytes());
    } else {
        let _ = wire_bytes(cs, s, |b| w.uint(8, b as u64), "");
    }
}

/// The string a string field encoded as binary holds: all of its bytes,
/// which must be UTF-8.
pub fn string_from_bytes(b: Vec<u8>, field: &'static str) -> Result<String> {
    String::from_utf8(b).map_err(|_| PacketError::InvalidString { field })
}

/// [`string_from_bytes`] for a string of at most `N` bytes.
pub fn fixed_string_from_bytes<const N: usize>(
    b: FixedBytes<N>,
    field: &'static str,
) -> Result<FixedString<N>> {
    FixedString::from_utf8(b).map_err(|_| PacketError::InvalidString { field })
}

/// Writes a fixed-size binary field.
pub fn write_binary(
    w: &mut BitWriter<'_>,
    b: &[u8],
    n: usize,
    field: &'static str,
) -> Result<()> {
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
pub fn write_rest(
    w: &mut BitWriter<'_>,
    b: &[u8],
    max: usize,
    field: &'static str,
) -> Result<()> {
    if b.len() > max {
        return Err(PacketError::TooLong { field });
    }
    w.bytes(b);
    Ok(())
}

// --- canonical text ----------------------------------------------------------
//
// `describe` output is identical in every language so that the runtimes can be
// compared byte for byte.

/// XTCE's concern levels, least to most.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum AlarmLevel {
    Normal,
    Watch,
    Warning,
    Distress,
    Critical,
    Severe,
}

impl AlarmLevel {
    /// The level with index `i` (0 is `Normal`); larger ones are `Severe`.
    pub fn from_index(i: u8) -> Self {
        match i {
            0 => Self::Normal,
            1 => Self::Watch,
            2 => Self::Warning,
            3 => Self::Distress,
            4 => Self::Critical,
            _ => Self::Severe,
        }
    }

    /// XTCE's name for the level.
    pub fn name(self) -> &'static str {
        match self {
            Self::Normal => "normal",
            Self::Watch => "watch",
            Self::Warning => "warning",
            Self::Distress => "distress",
            Self::Critical => "critical",
            Self::Severe => "severe",
        }
    }
}

impl fmt::Display for AlarmLevel {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.name())
    }
}

/// The alarm of one field: its level, or `None` when the field is absent.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Alarm {
    pub field: &'static str,
    pub level: Option<AlarmLevel>,
}

/// The alarms of a packet's fields, at most `N` of them.
#[derive(Debug, Clone, Copy)]
pub struct Alarms<const N: usize> {
    items: [Alarm; N],
    len: usize,
}

impl<const N: usize> Default for Alarms<N> {
    fn default() -> Self {
        Self::new()
    }
}

impl<const N: usize> Alarms<N> {
    pub fn new() -> Self {
        Self {
            items: [Alarm {
                field: "",
                level: None,
            }; N],
            len: 0,
        }
    }

    pub fn push(&mut self, field: &'static str, level: Option<AlarmLevel>) {
        self.items[self.len] = Alarm { field, level };
        self.len += 1;
    }

    pub fn as_slice(&self) -> &[Alarm] {
        &self.items[..self.len]
    }

    /// The most severe level among the fields present, if any is.
    pub fn worst(&self) -> Option<AlarmLevel> {
        self.as_slice().iter().filter_map(|a| a.level).max()
    }
}

/// `field=level` for each alarm, `none` when the field is absent, as every
/// runtime writes it.
impl<const N: usize> fmt::Display for Alarms<N> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        for (i, a) in self.as_slice().iter().enumerate() {
            if i > 0 {
                f.write_str(", ")?;
            }
            match a.level {
                Some(l) => write!(f, "{}={}", a.field, l)?,
                None => write!(f, "{}=none", a.field)?,
            }
        }
        Ok(())
    }
}

/// What an alarm monitor keeps for one parameter: whether it is in alarm,
/// its runs of samples in and out of alarm, and its last `H` values and
/// their times, for change ranges.
#[derive(Debug, Clone, Copy)]
pub struct AlarmState<const H: usize> {
    violations: u32,
    conformances: u32,
    level: u8,
    values: [f64; H],
    times: [f64; H],
    len: usize,
    next: usize,
}

impl<const H: usize> Default for AlarmState<H> {
    fn default() -> Self {
        Self::new()
    }
}

impl<const H: usize> AlarmState<H> {
    pub fn new() -> Self {
        Self {
            violations: 0,
            conformances: 0,
            level: 0,
            values: [0.0; H],
            times: [0.0; H],
            len: 0,
            next: 0,
        }
    }

    /// The level to report for a sample whose own level is `level`: an alarm
    /// is raised after `min_violations` samples in a row are in alarm, and
    /// cleared after `min_conformance` in a row are not; in between, the
    /// last level raised stands.
    pub fn update(
        &mut self,
        level: u8,
        min_violations: u32,
        min_conformance: u32,
    ) -> u8 {
        if level > 0 {
            self.violations += 1;
            self.conformances = 0;
            if self.level > 0 || self.violations >= min_violations {
                self.level = level;
            }
        } else {
            self.conformances += 1;
            self.violations = 0;
            if self.level > 0 && self.conformances >= min_conformance {
                self.level = 0;
            }
        }
        self.level
    }

    /// How much `x` (at time `t`) changed since the value `k` samples before
    /// it: as a percentage of that value when `percent`, then per second
    /// when `per_second`.  `None` when there is no such sample, or no change
    /// can be worked out (no time between them, or a percentage of 0).
    pub fn change(
        &self,
        x: f64,
        t: f64,
        k: usize,
        per_second: bool,
        percent: bool,
    ) -> Option<f64> {
        if k == 0 || self.len < k {
            return None;
        }
        let i = (self.next + H - k) % H;
        let (then, t_then) = (self.values[i], self.times[i]);
        let mut d = x - then;
        if percent {
            if then == 0.0 {
                return None;
            }
            d = d / then.abs() * 100.0;
        }
        if per_second {
            let dt = t - t_then;
            if !(dt > 0.0) {
                return None;
            }
            d /= dt;
        }
        Some(d)
    }

    /// Remembers a sample, for change ranges.
    pub fn push(&mut self, x: f64, t: f64) {
        if H == 0 {
            return;
        }
        self.values[self.next] = x;
        self.times[self.next] = t;
        self.next = (self.next + 1) % H;
        self.len = (self.len + 1).min(H);
    }
}

/// How a PCM stream's levels become data bits.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Pcm {
    /// The level is the bit.
    Nrzl,
    /// A one is a change of level.
    Nrzm,
    /// A zero is a change of level.
    Nrzs,
}

#[derive(Debug, Clone, Copy)]
struct LineDecoder {
    pcm: Pcm,
    inverted: bool,
    prev: u8,
}

impl LineDecoder {
    fn new(pcm: Pcm, inverted: bool) -> Self {
        Self {
            pcm,
            inverted,
            prev: 0,
        }
    }

    fn decode(&mut self, raw: u8) -> u8 {
        let level = raw ^ u8::from(self.inverted);
        let bit = match self.pcm {
            Pcm::Nrzl => level,
            Pcm::Nrzm => level ^ self.prev,
            Pcm::Nrzs => 1 ^ level ^ self.prev,
        };
        self.prev = level;
        bit
    }
}

/// Packs `bits`, most significant first, into the start of `out`, padded
/// with zeros, and returns the bytes used.
fn pack_bits(bits: impl Iterator<Item = u8>, out: &mut [u8]) -> usize {
    let mut n = 0;
    for (i, b) in bits.enumerate() {
        if i % 8 == 0 {
            out[i / 8] = 0;
        }
        out[i / 8] |= b << (7 - i % 8);
        n = i + 1;
    }
    n.div_ceil(8)
}

/// The parameters of a FixedFrameStream; see [`FrameSync`].
#[derive(Debug, Clone, Copy)]
pub struct FrameSpec {
    pub frame_bits: usize,
    pub pattern: u64,
    pub mask: u64,
    pub pattern_bits: u32,
    pub location: usize,
    pub max_errors: u32,
    pub aperture: usize,
    pub verify: u32,
    pub check: u32,
    pub invert_after: u64,
    pub pcm: Pcm,
    pub inverted: bool,
}

const SEARCH: u8 = 0;
const VERIFY: u8 = 1;
const LOCK: u8 = 2;
const CHECK: u8 = 3;

/// Finds the frames of a FixedFrameStream in a stream of bytes, by the
/// state machine XTCE describes: in Search, it looks for the sync pattern at
/// every bit; in Verify, it needs `verify` frames in a row whose patterns are
/// where the frame length (give or take `aperture` bits) says, then it
/// locks; in Lock, it gives each frame whose pattern it found, and a missed
/// pattern sends it to Check, where `check` good patterns in a row lock it
/// again and a missed one sends it back to Search.  Searching `invert_after`
/// bits (when not 0) without finding a pattern inverts the bits.  It keeps
/// up to `BITS` bits and gives frames of `BYTES` bytes; the generated stream
/// types choose both.
#[derive(Debug, Clone)]
pub struct FrameSync<const BITS: usize, const BYTES: usize> {
    spec: FrameSpec,
    line: LineDecoder,
    bits: [u8; BITS],
    len: usize,
    base: usize,
    state: u8,
    pos: usize,
    first: usize,
    cur: usize,
    cur_good: bool,
    last: usize,
    good: u32,
    searched: u64,
    flip: u8,
    frame: [u8; BYTES],
}

impl<const BITS: usize, const BYTES: usize> FrameSync<BITS, BYTES> {
    pub fn new(spec: FrameSpec) -> Self {
        Self {
            spec,
            line: LineDecoder::new(spec.pcm, spec.inverted),
            bits: [0; BITS],
            len: 0,
            base: 0,
            state: SEARCH,
            pos: 0,
            first: 0,
            cur: 0,
            cur_good: false,
            last: 0,
            good: 0,
            searched: 0,
            flip: 0,
            frame: [0; BYTES],
        }
    }

    /// Takes the next bytes of the stream, and gives `f` each frame they
    /// complete (its bits padded with zeros).
    pub fn push(&mut self, data: &[u8], mut f: impl FnMut(&[u8])) {
        for &byte in data {
            for k in (0..8).rev() {
                let b = self.line.decode((byte >> k) & 1);
                if self.len == BITS {
                    // Never, with the capacity the generator gives; start
                    // again rather than overrun.
                    self.base += self.len;
                    self.len = 0;
                    self.state = SEARCH;
                    self.pos = self.base;
                }
                self.bits[self.len] = b;
                self.len += 1;
            }
            self.run(&mut f);
        }
    }

    fn bit(&self, i: usize) -> u8 {
        self.bits[i - self.base] ^ self.flip
    }

    fn matches(&self, s: usize) -> bool {
        let n = self.spec.pattern_bits;
        let at = s + self.spec.location;
        let mut errors = 0;
        for j in 0..n {
            let shift = n - 1 - j;
            if (self.spec.mask >> shift) & 1 == 1
                && u64::from(self.bit(at + j as usize))
                    != (self.spec.pattern >> shift) & 1
            {
                errors += 1;
                if errors > self.spec.max_errors {
                    return false;
                }
            }
        }
        true
    }

    fn emit(&mut self, s: usize, f: &mut impl FnMut(&[u8])) {
        let mut frame = [0u8; BYTES];
        let n = pack_bits(
            (0..self.spec.frame_bits).map(|i| self.bit(s + i)),
            &mut frame,
        );
        self.frame = frame;
        f(&self.frame[..n]);
    }

    fn run(&mut self, f: &mut impl FnMut(&[u8])) {
        let span = self.spec.location + self.spec.pattern_bits as usize;
        loop {
            let end = self.base + self.len;
            if self.state == SEARCH {
                if self.pos + span > end {
                    break;
                }
                if self.matches(self.pos) {
                    self.first = self.pos;
                    self.last = self.pos;
                    self.cur = self.pos;
                    self.cur_good = true;
                    self.good = 1;
                    self.searched = 0;
                    self.state =
                        if self.spec.verify <= 1 { LOCK } else { VERIFY };
                } else {
                    self.pos += 1;
                    self.searched += 1;
                    if self.spec.invert_after != 0
                        && self.searched >= self.spec.invert_after
                    {
                        self.flip ^= 1;
                        self.searched = 0;
                    }
                }
                continue;
            }
            let next = self.cur + self.spec.frame_bits;
            if next + self.spec.aperture + span > end {
                break;
            }
            let mut found = None;
            'search: for d in 0..=self.spec.aperture {
                for at in [next - d, next + d] {
                    if self.matches(at) {
                        found = Some(at);
                        break 'search;
                    }
                    if d == 0 {
                        break;
                    }
                }
            }
            if let Some(at) = found {
                if self.state != LOCK {
                    self.good += 1;
                    let need = if self.state == VERIFY {
                        self.spec.verify
                    } else {
                        self.spec.check
                    };
                    if self.good >= need {
                        self.state = LOCK;
                    }
                }
                if self.state == LOCK && self.cur_good {
                    self.emit(self.cur, f);
                }
                self.cur = at;
                self.last = at;
                self.cur_good = true;
            } else if self.state == LOCK {
                if self.cur_good {
                    self.emit(self.cur, f);
                }
                self.state = CHECK;
                self.good = 0;
                self.cur = next;
                self.cur_good = false;
            } else {
                self.pos = if self.state == VERIFY {
                    self.first
                } else {
                    self.last
                } + 1;
                self.state = SEARCH;
                self.searched = 0;
            }
        }
        let keep = match self.state {
            SEARCH => self.pos,
            VERIFY => self.first,
            _ => self.last,
        };
        let drop = keep - self.base;
        self.bits.copy_within(drop..self.len, 0);
        self.len -= drop;
        self.base = keep;
    }
}

/// The parameters of a VariableFrameStream; see [`FlagSync`].
#[derive(Debug, Clone, Copy)]
pub struct FlagSpec {
    pub flag_bits: u32,
    pub flag_ones: bool,
    pub max_bits: usize,
    pub pcm: Pcm,
    pub inverted: bool,
}

/// Finds the frames of a VariableFrameStream in a stream of bytes: frames
/// lie between flags, each `flag_bits` ones (or zeros, unless `flag_ones`)
/// with a bit of the other kind on either side, and a bit of the other kind
/// is stuffed into a frame after `flag_bits - 1` in a row, which this
/// removes.  More than `flag_bits` in a row abort the frame, as does a frame
/// of more than `max_bits`; frames are found again from the next flag.  It
/// keeps up to `BITS` bits (`max_bits + flag_bits + 2`) and gives frames of
/// up to `BYTES` bytes.
#[derive(Debug, Clone)]
pub struct FlagSync<const BITS: usize, const BYTES: usize> {
    spec: FlagSpec,
    line: LineDecoder,
    bits: [u8; BITS],
    len: usize,
    run: u32,
    hunting: bool,
    frame: [u8; BYTES],
}

impl<const BITS: usize, const BYTES: usize> FlagSync<BITS, BYTES> {
    pub fn new(spec: FlagSpec) -> Self {
        Self {
            spec,
            line: LineDecoder::new(spec.pcm, spec.inverted),
            bits: [0; BITS],
            len: 0,
            run: 0,
            hunting: true,
            frame: [0; BYTES],
        }
    }

    /// Takes the next bytes of the stream, and gives `f` each frame they
    /// complete (its bits padded with zeros).
    pub fn push(&mut self, data: &[u8], mut f: impl FnMut(&[u8])) {
        for &byte in data {
            for k in (0..8).rev() {
                let b = self.line.decode((byte >> k) & 1);
                self.bit(b, &mut f);
            }
        }
    }

    fn bit(&mut self, b: u8, f: &mut impl FnMut(&[u8])) {
        let flag = u8::from(self.spec.flag_ones);
        let fb = self.spec.flag_bits;
        if b == flag {
            self.run += 1;
            if self.run > fb {
                self.hunting = true;
            }
            if !self.hunting {
                if self.len < BITS {
                    self.bits[self.len] = b;
                }
                self.len += 1;
                if self.len > self.spec.max_bits + fb as usize + 1 {
                    self.hunting = true;
                }
            }
            if self.hunting {
                self.len = 0;
            }
            return;
        }
        if self.run == fb {
            if !self.hunting {
                let mut n = self.len - fb as usize;
                if n > 0 && self.bits[n - 1] != flag {
                    n -= 1;
                }
                if n > 0 {
                    let mut frame = [0u8; BYTES];
                    let bytes =
                        pack_bits(self.bits[..n].iter().copied(), &mut frame);
                    self.frame = frame;
                    f(&self.frame[..bytes]);
                }
            }
            self.hunting = false;
            self.len = 0;
        } else if self.run == fb - 1 && !self.hunting {
            // a stuffed bit
        } else if !self.hunting {
            if self.len < BITS {
                self.bits[self.len] = b;
            }
            self.len += 1;
            if self.len > self.spec.max_bits + fb as usize + 1 {
                self.hunting = true;
                self.len = 0;
            }
        }
        self.run = 0;
    }
}

/// The output of one MathAlgorithm: its value, or `None` when an input is
/// absent.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Output {
    pub name: &'static str,
    pub value: Option<f64>,
}

/// The outputs of the MathAlgorithms a packet evaluates, at most `N`.
#[derive(Debug, Clone, Copy)]
pub struct Outputs<const N: usize> {
    items: [Output; N],
    len: usize,
}

impl<const N: usize> Default for Outputs<N> {
    fn default() -> Self {
        Self::new()
    }
}

impl<const N: usize> Outputs<N> {
    pub fn new() -> Self {
        Self {
            items: [Output {
                name: "",
                value: None,
            }; N],
            len: 0,
        }
    }

    pub fn push(&mut self, name: &'static str, value: Option<f64>) {
        self.items[self.len] = Output { name, value };
        self.len += 1;
    }

    pub fn as_slice(&self) -> &[Output] {
        &self.items[..self.len]
    }
}

/// `name=value` for each output, `none` when it is absent, as every runtime
/// writes it.
impl<const N: usize> fmt::Display for Outputs<N> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        for (i, o) in self.as_slice().iter().enumerate() {
            if i > 0 {
                f.write_str(", ")?;
            }
            write!(f, "{}=", o.name)?;
            match o.value {
                Some(v) => write_double(v, f)?,
                None => f.write_str("none")?,
            }
        }
        Ok(())
    }
}

/// Whether `x` lies in an alarm range; a bound of `None` is infinite.
pub fn in_band(
    x: f64,
    lo: Option<f64>,
    lo_incl: bool,
    hi: Option<f64>,
    hi_incl: bool,
) -> bool {
    lo.is_none_or(|lo| if lo_incl { x >= lo } else { x > lo })
        && hi.is_none_or(|hi| if hi_incl { x <= hi } else { x < hi })
}

/// The level of enumeration value `x`: that of the first (first value,
/// last value, level) in `labels` holding it, else `default`.
pub fn enum_alarm(x: i64, labels: &[(i64, i64, u8)], default: u8) -> u8 {
    labels
        .iter()
        .find(|&&(lo, hi, _)| lo <= x && x <= hi)
        .map_or(default, |l| l.2)
}

/// An automaton over UTF-8 bytes that a string alarm's pattern compiles to:
/// state `q`'s transitions are `trans[starts[q]..starts[q + 1]]`, each
/// (first byte, last byte, next state).
pub struct Dfa {
    pub starts: &'static [u16],
    pub trans: &'static [(u8, u8, u16)],
    pub accept: &'static [bool],
}

/// Whether `dfa` accepts all of `data`.
pub fn dfa_match(dfa: &Dfa, data: &[u8]) -> bool {
    let mut q = 0usize;
    for &b in data {
        let row =
            &dfa.trans[dfa.starts[q] as usize..dfa.starts[q + 1] as usize];
        match row.iter().find(|&&(lo, hi, _)| lo <= b && b <= hi) {
            Some(&(_, _, next)) => q = next as usize,
            None => return false,
        }
    }
    dfa.accept[q]
}

/// The level of string `s`: that of the first (automaton, level) in
/// `patterns` it matches, else `default`.
pub fn pattern_alarm(s: &str, patterns: &[(&Dfa, u8)], default: u8) -> u8 {
    patterns
        .iter()
        .find(|(d, _)| dfa_match(d, s.as_bytes()))
        .map_or(default, |p| p.1)
}

pub fn describe_f64<W: fmt::Write + ?Sized>(
    x: f64,
    out: &mut W,
) -> fmt::Result {
    write_double(x, out)
}

pub fn describe_str<W: fmt::Write + ?Sized>(
    s: &str,
    out: &mut W,
) -> fmt::Result {
    out.write_char('"')?;
    for c in s.chars() {
        match c {
            '"' => out.write_str("\\\"")?,
            '\\' => out.write_str("\\\\")?,
            c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                write!(out, "\\x{:02x}", c as u32)?
            }
            c => out.write_char(c)?,
        }
    }
    out.write_char('"')
}

/// Text for an optional field that is absent.
pub const DESCRIBE_NONE: &str = "none";

pub fn describe_bytes<W: fmt::Write + ?Sized>(
    b: &[u8],
    out: &mut W,
) -> fmt::Result {
    out.write_str("0x")?;
    for x in b {
        write!(out, "{x:02x}")?;
    }
    Ok(())
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
            charset: Charset::Utf8,
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
    fn float_formats_match_their_standards() {
        use FloatFormat::*;
        let u = 2f64.powi(-23);
        // MIL-STD-1750A's own examples.
        for (x, raw) in [
            ((1.0 - u) * 2f64.powi(127), 0x7fff_ff7f),
            (0.625 * 16.0, 0x5000_0004),
            (0.25, 0x4000_00ff),
            (0.5 * 2f64.powi(-128), 0x4000_0080),
            (-1.0, 0x8000_0000),
            (-(0.5 + u) * 2f64.powi(-128), 0xbfff_ff80),
            (-(0.75 + u) * 16.0, 0x9fff_ff04),
        ] {
            assert_eq!(float_encode(Mil1750a, 32, x, "f"), Ok(raw), "{x}");
            assert_eq!(float_decode(Mil1750a, 32, raw, "f"), Ok(x));
        }
        assert_eq!(float_encode(Mil1750a, 48, -1.0, "f"), Ok(0x8000_0000_0000));
        assert_eq!(float_encode(Ibm, 32, -118.625, "f"), Ok(0xc276_a000));
        assert_eq!(float_encode(Dec, 32, 1.0, "f"), Ok(0x4080_0000));
        assert_eq!(
            float_decode(Dec, 32, 0x8000_0000, "f"),
            Err(PacketError::InvalidValue { field: "f" })
        );
        assert_eq!(float_encode(Ti, 32, -1.0, "f"), Ok(0xff80_0000));
        assert_eq!(float_encode(Ti, 32, 0.0, "f"), Ok(0x8000_0000));
        assert_eq!(
            float_encode(Ieee, 80, 1.0, "f"),
            Ok(0x3fff_8000_0000_0000_0000)
        );
        assert_eq!(float_encode(Ieee, 16, 65520.0, "f"), Ok(0x7c00));
        for x in [1.0 / 3.0, -2.5e-310, 1e300] {
            for bits in [80, 128] {
                let raw = float_encode(Ieee, bits, x, "f").unwrap();
                assert_eq!(float_decode(Ieee, bits, raw, "f"), Ok(x));
            }
        }
    }

    #[test]
    fn byte_and_bit_orders() {
        let mut buf = Buffer::with_capacity(16);
        let mut w = BitWriter::new(&mut buf);
        // Least significant bit first, little endian: C bit fields.
        w.uint_bits(3, true, ByteOrder::Little, 0b101);
        w.uint_bits(13, true, ByteOrder::Little, 0x1abc);
        w.uint_bits(32, false, ByteOrder::List(&[2, 3, 0, 1]), 0x0a0b_0c0d);
        assert_eq!(buf.as_bytes(), &[0xe5, 0xd5, 0x0b, 0x0a, 0x0d, 0x0c]);
        let mut r = BitReader::new(buf.as_bytes());
        assert_eq!(r.uint_bits(3, true, ByteOrder::Little, "a"), Ok(0b101));
        assert_eq!(r.uint_bits(13, true, ByteOrder::Little, "b"), Ok(0x1abc));
        assert_eq!(
            r.uint_bits(32, false, ByteOrder::List(&[2, 3, 0, 1]), "c"),
            Ok(0x0a0b_0c0d)
        );
    }

    #[test]
    fn bcd() {
        assert_eq!(bcd_decode(0x1234, 16, true, "x"), Ok(1234));
        assert_eq!(bcd_decode(0x0102, 16, false, "x"), Ok(12));
        assert_eq!(
            bcd_decode(0x1a, 8, true, "x"),
            Err(PacketError::InvalidValue { field: "x" })
        );
        assert_eq!(bcd_encode(1234, 16, true, "x"), Ok(0x1234));
        assert_eq!(bcd_encode(99, 16, false, "x"), Ok(0x0909));
        assert!(bcd_encode(100, 16, false, "x").is_err());
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
