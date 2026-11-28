//! Streams: the frame synchronizers of a mission's FixedFrameStreams and
//! VariableFrameStreams, and the decoding of its CustomStreams, run by
//! the runtime's `FrameSync` and `FlagSync` as the code generated for the
//! mission runs them, each stream fed by the one its input comes from.

use std::fmt;

use tcs_xtce::packet::{
    BitInvert, FlagSpec, FlagSync, FrameSpec, FrameSync, Pcm, StreamDecode,
};
use tcs_xtce_model::model::Stream;

/// What runs a stream: a synchronizer of some size.
trait Push: fmt::Debug + Send {
    /// Takes the stream's next bytes, giving `f` each frame they complete.
    ///
    /// # Arguments
    ///
    /// * `data` - The bytes.
    /// * `f`    - Called with each frame.
    fn push(&mut self, data: &[u8], f: &mut dyn FnMut(&[u8]));
}

impl<const B: usize, const Y: usize> Push for FrameSync<'_, B, Y> {
    fn push(&mut self, data: &[u8], f: &mut dyn FnMut(&[u8])) {
        FrameSync::push(self, data, f)
    }
}

impl<const B: usize, const Y: usize> Push for FlagSync<B, Y> {
    fn push(&mut self, data: &[u8], f: &mut dyn FnMut(&[u8])) {
        FlagSync::push(self, data, f)
    }
}

/// What a stream's frames come from.
enum Inner<'m> {
    /// A synchronizer.
    Sync(Box<dyn Push + 'm>),
    /// A CustomStream's DecodingAlgorithm.
    Custom(StreamDecode),
}

impl fmt::Debug for Inner<'_> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Inner::Sync(s) => s.fmt(f),
            Inner::Custom(_) => f.write_str("Custom"),
        }
    }
}

/// A stream of a mission, which takes the stream's bytes as they arrive
/// and gives each frame they complete, as the generated code's stream
/// types do. It borrows its sync pattern from the mission.
#[derive(Debug)]
pub struct Deframer<'m> {
    /// What its frames come from.
    inner: Inner<'m>,
    /// The stream its input comes from, if any.
    feeder: Option<Box<Deframer<'m>>>,
}

impl Deframer<'_> {
    /// Takes the stream's next bytes, and gives `f` each frame they
    /// complete (its bits padded with zeros), or for a CustomStream, the
    /// bytes it decodes.
    ///
    /// # Arguments
    ///
    /// * `data` - The stream's next bytes, in the order they arrive.
    /// * `f`    - Called with the bytes of each frame.
    pub fn push(&mut self, data: &[u8], mut f: impl FnMut(&[u8])) {
        self.push_dyn(data, &mut f);
    }

    /// Takes the stream's next bytes, as [`Deframer::push`] does.
    ///
    /// # Arguments
    ///
    /// * `data` - The bytes.
    /// * `f`    - Called with each frame.
    fn push_dyn(&mut self, data: &[u8], f: &mut dyn FnMut(&[u8])) {
        let inner = &mut self.inner;
        let mut own = |x: &[u8]| match inner {
            Inner::Sync(s) => s.push(x, f),
            Inner::Custom(d) => d(x, f),
        };
        match &mut self.feeder {
            None => own(data),
            Some(fd) => fd.push_dyn(data, &mut own),
        }
    }
}

/// The most bits a synchronizer keeps that the codec runs.
pub(crate) const MAX_BITS: usize = 1 << 22;

/// Gives the runtime's PCM type of a stream's.
///
/// # Arguments
///
/// * `pcm` - The PCM type, as XTCE names it.
///
/// # Returns
///
/// The runtime's.
fn pcm(pcm: &str) -> Pcm {
    match pcm {
        "NRZM" => Pcm::Nrzm,
        "NRZS" => Pcm::Nrzs,
        "BiPhaseL" => Pcm::BiphaseL,
        "BiPhaseM" => Pcm::BiphaseM,
        "BiPhaseS" => Pcm::BiphaseS,
        _ => Pcm::Nrzl,
    }
}

/// Gives the bytes of a pattern or mask, most significant first, as the
/// runtime takes them.
///
/// # Arguments
///
/// * `v`    - The value.
/// * `bits` - Its bits.
///
/// # Returns
///
/// The bytes.
fn pattern_bytes(v: &num_bigint::BigInt, bits: i64) -> Vec<u8> {
    let n = (bits.max(0) as usize).div_ceil(8);
    let (_, b) = v.to_bytes_be();
    let mut out = vec![0u8; n.saturating_sub(b.len())];
    out.extend_from_slice(&b[b.len().saturating_sub(n)..]);
    out
}

/// The sync patterns and masks of a mission's streams, which its
/// deframers borrow.
#[derive(Debug, Default)]
pub(crate) struct Patterns {
    /// Each FixedFrameStream's pattern and mask, by its qualified name.
    by_stream: std::collections::HashMap<String, (Vec<u8>, Vec<u8>)>,
}

impl Patterns {
    /// Makes the patterns and masks of some streams.
    ///
    /// # Arguments
    ///
    /// * `streams` - The streams.
    ///
    /// # Returns
    ///
    /// Their patterns and masks.
    pub(crate) fn new(streams: &[tcs_xtce_model::dump::Mut<Stream>]) -> Self {
        let mut p = Patterns::default();
        for st in streams {
            let st = st.get();
            if !st.fixed {
                continue;
            }
            let pat = pattern_bytes(&st.pattern, st.pattern_bits);
            let mask = pattern_bytes(&st.mask, st.pattern_bits);
            p.by_stream.insert(st.key.clone(), (pat, mask));
        }
        p
    }
}

/// Makes a synchronizer whose buffers are of the least size that holds a
/// stream's: it keeps `B` bits and makes frames of up to `B / 8 + 8`
/// bytes.
macro_rules! sized {
    ($bits:expr, $make:ident) => {
        match $bits {
            n if n <= 1 << 8 => $make!({ 1 << 8 }, { (1 << 8) / 8 + 8 }),
            n if n <= 1 << 9 => $make!({ 1 << 9 }, { (1 << 9) / 8 + 8 }),
            n if n <= 1 << 10 => $make!({ 1 << 10 }, { (1 << 10) / 8 + 8 }),
            n if n <= 1 << 11 => $make!({ 1 << 11 }, { (1 << 11) / 8 + 8 }),
            n if n <= 1 << 12 => $make!({ 1 << 12 }, { (1 << 12) / 8 + 8 }),
            n if n <= 1 << 13 => $make!({ 1 << 13 }, { (1 << 13) / 8 + 8 }),
            n if n <= 1 << 14 => $make!({ 1 << 14 }, { (1 << 14) / 8 + 8 }),
            n if n <= 1 << 15 => $make!({ 1 << 15 }, { (1 << 15) / 8 + 8 }),
            n if n <= 1 << 16 => $make!({ 1 << 16 }, { (1 << 16) / 8 + 8 }),
            n if n <= 1 << 17 => $make!({ 1 << 17 }, { (1 << 17) / 8 + 8 }),
            n if n <= 1 << 18 => $make!({ 1 << 18 }, { (1 << 18) / 8 + 8 }),
            n if n <= 1 << 19 => $make!({ 1 << 19 }, { (1 << 19) / 8 + 8 }),
            n if n <= 1 << 20 => $make!({ 1 << 20 }, { (1 << 20) / 8 + 8 }),
            n if n <= 1 << 21 => $make!({ 1 << 21 }, { (1 << 21) / 8 + 8 }),
            _ => $make!({ 1 << 22 }, { (1 << 22) / 8 + 8 }),
        }
    };
}

/// Gives the bits a stream's synchronizer keeps, as the generated code
/// sizes it.
///
/// # Arguments
///
/// * `st` - The stream.
///
/// # Returns
///
/// The bits.
pub(crate) fn capacity(st: &Stream) -> usize {
    let bits = if st.fixed {
        (st.verify.max(2) + 2) * st.frame_bits
            + 2 * st.aperture
            + st.location
            + st.pattern_bits
            + 16
    } else {
        st.max_bits + st.flag_bits + 2
    };
    bits.max(0) as usize
}

/// Makes a value on a thread of its own with a stack that holds it, so
/// that a large synchronizer never overflows the caller's stack on its
/// way to the heap.
///
/// # Arguments
///
/// * `bytes` - About how large it is.
/// * `f`     - Makes it.
///
/// # Returns
///
/// It, on the heap.
fn on_big_stack<'m>(
    bytes: usize,
    f: impl FnOnce() -> Box<dyn Push + 'm> + Send,
) -> Box<dyn Push + 'm> {
    if bytes < 1 << 16 {
        return f();
    }
    std::thread::scope(|sc| {
        std::thread::Builder::new()
            .stack_size(4 * bytes + (1 << 20))
            .spawn_scoped(sc, f)
            .expect("a thread to make a stream's synchronizer")
            .join()
            .expect("a stream's synchronizer")
    })
}

/// Makes the synchronizer of a FixedFrameStream or VariableFrameStream.
///
/// # Arguments
///
/// * `st`       - The stream.
/// * `patterns` - The mission's sync patterns and masks.
/// * `invert`   - Its InvertAlgorithm's function, if it has one.
///
/// # Returns
///
/// The synchronizer.
fn synchronizer<'m>(
    st: &Stream,
    patterns: &'m Patterns,
    invert: Option<BitInvert>,
) -> Box<dyn Push + 'm> {
    let bits = capacity(st);
    if st.fixed {
        let (pattern, mask) = &patterns.by_stream[&st.key];
        let spec = FrameSpec {
            frame_bits: st.frame_bits.max(0) as usize,
            pattern,
            mask,
            pattern_bits: st.pattern_bits as u32,
            location: st.location.max(0) as usize,
            max_errors: st.max_errors as u32,
            aperture: st.aperture.max(0) as usize,
            verify: st.verify as u32,
            check: st.check as u32,
            invert_after: st.invert_after.max(0) as u64,
            pcm: pcm(&st.pcm),
            inverted: st.inverted,
        };
        macro_rules! make {
            ($b:expr, $y:expr) => {
                on_big_stack(bits + bits / 8, move || {
                    let s = FrameSync::<$b, $y>::new(spec);
                    match invert {
                        Some(f) => Box::new(s.with_invert(f)),
                        None => Box::new(s),
                    }
                })
            };
        }
        sized!(bits, make)
    } else {
        let spec = FlagSpec {
            flag_bits: st.flag_bits as u32,
            flag_ones: st.flag_ones,
            max_bits: st.max_bits.max(0) as usize,
            pcm: pcm(&st.pcm),
            inverted: st.inverted,
        };
        macro_rules! make {
            ($b:expr, $y:expr) => {
                on_big_stack(bits + bits / 8, move || {
                    Box::new(FlagSync::<$b, $y>::new(spec))
                })
            };
        }
        sized!(bits, make)
    }
}

/// Makes a stream, and the streams its input comes from.
///
/// # Arguments
///
/// * `st`       - The stream.
/// * `patterns` - The mission's sync patterns and masks.
/// * `fns`      - Gives the function set for an algorithm: its name and
///                the kind of function, `stream_decode` or `bit_invert`.
///
/// # Returns
///
/// The stream.
///
/// # Errors
///
/// What is missing: the name of an algorithm whose function isn't set,
/// or why a synchronizer is too large to run.
pub(crate) fn deframer<'m>(
    st: &Stream,
    patterns: &'m Patterns,
    fns: &dyn Fn(&str, &str) -> Option<crate::Algorithm>,
) -> Result<Deframer<'m>, String> {
    let feeder = match &st.feeder {
        Some(f) => Some(Box::new(deframer(&f.get(), patterns, fns)?)),
        None => None,
    };
    let inner = if st.custom {
        match fns(&st.decoder, "stream_decode") {
            Some(crate::Algorithm::StreamDecode(d)) => Inner::Custom(d),
            _ => {
                return Err(format!(
                    "{}: its DecodingAlgorithm {} isn't set",
                    st.key, st.decoder
                ))
            }
        }
    } else {
        if capacity(st) > MAX_BITS {
            return Err(format!(
                "{}: a synchronizer of more than {MAX_BITS} bits",
                st.key
            ));
        }
        let invert = if st.inverter.is_empty() {
            None
        } else {
            match fns(&st.inverter, "bit_invert") {
                Some(crate::Algorithm::BitInvert(f)) => Some(f),
                _ => {
                    return Err(format!(
                        "{}: its InvertAlgorithm {} isn't set",
                        st.key, st.inverter
                    ))
                }
            }
        };
        Inner::Sync(synchronizer(st, patterns, invert))
    };
    Ok(Deframer { inner, feeder })
}
