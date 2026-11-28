//! The functions an application supplies for a mission's algorithms, as
//! closures, and the adapters through which the tcsxtce runtime, which
//! takes plain function pointers, calls them.
//!
//! An adapter is a plain function that calls the closure installed in a
//! slot of its own kind for the current thread. The codec installs the
//! closure just before it calls the runtime and puts back what the slot
//! held when that call returns, so calls inside each other each find
//! their own; installing moves a reference count, so it allocates
//! nothing.

use std::cell::Cell;
use std::fmt;
use std::rc::Rc;
use std::thread::LocalKey;

use tcs_xtce::packet as pk;

/// A custom algorithm in a criterion: whether it holds, from its inputs.
pub type CriterionFn = dyn Fn(&[f64]) -> bool;
/// A CustomAlgorithm's outputs, from its inputs, into the slice given;
/// false if it gives none.
pub type OutputsFn = dyn Fn(&[f64], &mut [f64]) -> bool;
/// A custom alarm's level, 0 (normal) to 5 (severe), from its inputs.
pub type AlarmFn = dyn Fn(&[f64]) -> u8;
/// A checksum of the bits from `start` to `end` of the data.
pub type ChecksumFn = dyn Fn(&[u8], usize, usize) -> u64;
/// Decodes a value's bytes into the slice given: the bytes it took and
/// those it made, or the room it needs.
pub type BinaryFromFn = dyn Fn(&[u8], &mut [u8]) -> Option<(usize, usize)>;
/// Encodes a value's bytes into the slice given: the bytes it made, or the
/// room it needs.
pub type BinaryToFn = dyn Fn(&[u8], &mut [u8]) -> Option<usize>;
/// Decodes a time: the bytes it took and the seconds.
pub type TimeFromFn = dyn Fn(&[u8]) -> Option<(usize, f64)>;
/// Encodes a time into the slice given: the bytes it made, or the room it
/// needs.
pub type TimeToFn = dyn Fn(f64, &mut [u8]) -> Option<usize>;
/// A CustomStream's DecodingAlgorithm: gives the function given the bytes
/// it decodes from those it is given.
pub type StreamDecodeFn = dyn Fn(&[u8], &mut dyn FnMut(&[u8]));
/// A stream's InvertAlgorithm: what a bit is once AutoInvert has inverted
/// the stream.
pub type BitInvertFn = dyn Fn(u8) -> u8;

/// A function the application supplies for one of a mission's
/// algorithms, of the kind the algorithm's use needs: the same as the
/// code generated for the mission takes in its `hooks` module, but any
/// closure, so that it can carry what it needs, as a C function and its
/// context or a Python callable does.
#[derive(Clone)]
pub enum Algorithm {
    /// A custom algorithm in a criterion.
    Criterion(Rc<CriterionFn>),
    /// A CustomAlgorithm's outputs.
    Outputs(Rc<OutputsFn>),
    /// A custom alarm's level.
    Alarm(Rc<AlarmFn>),
    /// A custom checksum.
    Checksum(Rc<ChecksumFn>),
    /// A transform's decoding algorithm.
    BinaryFrom(Rc<BinaryFromFn>),
    /// A transform's encoding algorithm.
    BinaryTo(Rc<BinaryToFn>),
    /// A time's decoding algorithm.
    TimeFrom(Rc<TimeFromFn>),
    /// A time's encoding algorithm.
    TimeTo(Rc<TimeToFn>),
    /// A CustomStream's DecodingAlgorithm.
    StreamDecode(Rc<StreamDecodeFn>),
    /// A stream's InvertAlgorithm.
    BitInvert(Rc<BitInvertFn>),
}

impl fmt::Debug for Algorithm {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Algorithm::{}", self.kind())
    }
}

impl Algorithm {
    /// Makes a custom algorithm in a criterion.
    ///
    /// # Arguments
    ///
    /// * `f` - Whether it holds, from its inputs.
    ///
    /// # Returns
    ///
    /// The algorithm.
    pub fn criterion(f: impl Fn(&[f64]) -> bool + 'static) -> Self {
        Algorithm::Criterion(Rc::new(f))
    }

    /// Makes a CustomAlgorithm.
    ///
    /// # Arguments
    ///
    /// * `f` - Its outputs from its inputs; false if it gives none.
    ///
    /// # Returns
    ///
    /// The algorithm.
    pub fn outputs(f: impl Fn(&[f64], &mut [f64]) -> bool + 'static) -> Self {
        Algorithm::Outputs(Rc::new(f))
    }

    /// Makes a custom alarm.
    ///
    /// # Arguments
    ///
    /// * `f` - Its level from its inputs.
    ///
    /// # Returns
    ///
    /// The algorithm.
    pub fn alarm(f: impl Fn(&[f64]) -> u8 + 'static) -> Self {
        Algorithm::Alarm(Rc::new(f))
    }

    /// Makes a custom checksum.
    ///
    /// # Arguments
    ///
    /// * `f` - The checksum of the bits from `start` to `end` of the data.
    ///
    /// # Returns
    ///
    /// The algorithm.
    pub fn checksum(f: impl Fn(&[u8], usize, usize) -> u64 + 'static) -> Self {
        Algorithm::Checksum(Rc::new(f))
    }

    /// Makes a transform's decoding algorithm.
    ///
    /// # Arguments
    ///
    /// * `f` - Decodes bytes: the bytes it took and those it made.
    ///
    /// # Returns
    ///
    /// The algorithm.
    pub fn binary_from(
        f: impl Fn(&[u8], &mut [u8]) -> Option<(usize, usize)> + 'static,
    ) -> Self {
        Algorithm::BinaryFrom(Rc::new(f))
    }

    /// Makes a transform's encoding algorithm.
    ///
    /// # Arguments
    ///
    /// * `f` - Encodes bytes: the bytes it made.
    ///
    /// # Returns
    ///
    /// The algorithm.
    pub fn binary_to(
        f: impl Fn(&[u8], &mut [u8]) -> Option<usize> + 'static,
    ) -> Self {
        Algorithm::BinaryTo(Rc::new(f))
    }

    /// Makes a time's decoding algorithm.
    ///
    /// # Arguments
    ///
    /// * `f` - Decodes a time: the bytes it took and the seconds.
    ///
    /// # Returns
    ///
    /// The algorithm.
    pub fn time_from(
        f: impl Fn(&[u8]) -> Option<(usize, f64)> + 'static,
    ) -> Self {
        Algorithm::TimeFrom(Rc::new(f))
    }

    /// Makes a time's encoding algorithm.
    ///
    /// # Arguments
    ///
    /// * `f` - Encodes a time: the bytes it made.
    ///
    /// # Returns
    ///
    /// The algorithm.
    pub fn time_to(
        f: impl Fn(f64, &mut [u8]) -> Option<usize> + 'static,
    ) -> Self {
        Algorithm::TimeTo(Rc::new(f))
    }

    /// Makes a CustomStream's DecodingAlgorithm.
    ///
    /// # Arguments
    ///
    /// * `f` - Gives its function the bytes it decodes from those it is
    ///         given.
    ///
    /// # Returns
    ///
    /// The algorithm.
    pub fn stream_decode(
        f: impl Fn(&[u8], &mut dyn FnMut(&[u8])) + 'static,
    ) -> Self {
        Algorithm::StreamDecode(Rc::new(f))
    }

    /// Makes a stream's InvertAlgorithm.
    ///
    /// # Arguments
    ///
    /// * `f` - What a bit is once AutoInvert has inverted the stream.
    ///
    /// # Returns
    ///
    /// The algorithm.
    pub fn bit_invert(f: impl Fn(u8) -> u8 + 'static) -> Self {
        Algorithm::BitInvert(Rc::new(f))
    }

    /// Gives the kind of use the function is for, as the model names it.
    ///
    /// # Returns
    ///
    /// `criterion`, `algorithm`, `alarm`, `checksum`, `binary_from`,
    /// `binary_to`, `time_from`, `time_to`, `stream_decode` or
    /// `bit_invert`.
    pub fn kind(&self) -> &'static str {
        match self {
            Algorithm::Criterion(_) => "criterion",
            Algorithm::Outputs(_) => "algorithm",
            Algorithm::Alarm(_) => "alarm",
            Algorithm::Checksum(_) => "checksum",
            Algorithm::BinaryFrom(_) => "binary_from",
            Algorithm::BinaryTo(_) => "binary_to",
            Algorithm::TimeFrom(_) => "time_from",
            Algorithm::TimeTo(_) => "time_to",
            Algorithm::StreamDecode(_) => "stream_decode",
            Algorithm::BitInvert(_) => "bit_invert",
        }
    }
}

/// A closure installed in a thread's slot for as long as it lives, after
/// which the slot holds what it held before.
pub(crate) struct Installed<T: ?Sized + 'static> {
    /// The slot.
    slot: &'static LocalKey<Cell<Option<Rc<T>>>>,
    /// What it held before.
    old: Option<Rc<T>>,
}

impl<T: ?Sized + 'static> Drop for Installed<T> {
    fn drop(&mut self) {
        let old = self.old.take();
        self.slot.with(|s| s.set(old));
    }
}

/// Installs a closure in a thread's slot.
///
/// # Arguments
///
/// * `slot` - The slot.
/// * `f`    - The closure.
///
/// # Returns
///
/// What puts the slot back as it was, when it is dropped.
fn install<T: ?Sized + 'static>(
    slot: &'static LocalKey<Cell<Option<Rc<T>>>>,
    f: &Rc<T>,
) -> Installed<T> {
    let old = slot.with(|s| s.replace(Some(f.clone())));
    Installed { slot, old }
}

/// Calls the closure installed in a thread's slot.
///
/// # Arguments
///
/// * `slot` - The slot.
/// * `call` - Calls the closure.
///
/// # Returns
///
/// What it gives, or `None` if none is installed.
fn call<T: ?Sized + 'static, R>(
    slot: &'static LocalKey<Cell<Option<Rc<T>>>>,
    call: impl FnOnce(&T) -> R,
) -> Option<R> {
    // Taken out while it runs, so that it may install others.
    let f = slot.with(|s| s.take());
    let r = f.as_deref().map(call);
    slot.with(|s| s.set(f));
    r
}

thread_local! {
    /// The checksum the runtime is computing.
    static CHECKSUM: Cell<Option<Rc<ChecksumFn>>> = const { Cell::new(None) };
    /// The transform decoding the runtime is running.
    static BINARY_FROM: Cell<Option<Rc<BinaryFromFn>>> =
        const { Cell::new(None) };
    /// The transform encoding the runtime is running.
    static BINARY_TO: Cell<Option<Rc<BinaryToFn>>> = const { Cell::new(None) };
    /// The time decoding the runtime is running.
    static TIME_FROM: Cell<Option<Rc<TimeFromFn>>> = const { Cell::new(None) };
    /// The time encoding the runtime is running.
    static TIME_TO: Cell<Option<Rc<TimeToFn>>> = const { Cell::new(None) };
    /// The bit inversion of the stream being synchronized.
    static BIT_INVERT: Cell<Option<Rc<BitInvertFn>>> =
        const { Cell::new(None) };
}

/// The adapter for a checksum.
///
/// # Arguments
///
/// * `d`     - The data.
/// * `start` - The first bit.
/// * `end`   - The bit after the last.
///
/// # Returns
///
/// What the installed checksum gives, or 0 if none is.
fn checksum_adapter(d: &[u8], start: usize, end: usize) -> u64 {
    call(&CHECKSUM, |f| f(d, start, end)).unwrap_or(0)
}

/// The adapter for a transform's decoding.
///
/// # Arguments
///
/// * `b`   - The bytes.
/// * `out` - Where the result goes.
///
/// # Returns
///
/// What the installed function gives.
fn binary_from_adapter(b: &[u8], out: &mut [u8]) -> Option<(usize, usize)> {
    call(&BINARY_FROM, |f| f(b, out)).flatten()
}

/// The adapter for a transform's encoding.
///
/// # Arguments
///
/// * `b`   - The bytes.
/// * `out` - Where the result goes.
///
/// # Returns
///
/// What the installed function gives.
fn binary_to_adapter(b: &[u8], out: &mut [u8]) -> Option<usize> {
    call(&BINARY_TO, |f| f(b, out)).flatten()
}

/// The adapter for a time's decoding.
///
/// # Arguments
///
/// * `b` - The bytes.
///
/// # Returns
///
/// What the installed function gives.
fn time_from_adapter(b: &[u8]) -> Option<(usize, f64)> {
    call(&TIME_FROM, |f| f(b)).flatten()
}

/// The adapter for a time's encoding.
///
/// # Arguments
///
/// * `s`   - The seconds.
/// * `out` - Where the bytes go.
///
/// # Returns
///
/// What the installed function gives.
fn time_to_adapter(s: f64, out: &mut [u8]) -> Option<usize> {
    call(&TIME_TO, |f| f(s, out)).flatten()
}

/// The plain function a stream's synchronizer takes for its
/// InvertAlgorithm, which calls the one installed while it runs.
pub(crate) const BIT_INVERT_ADAPTER: pk::BitInvert = bit_invert_adapter;

/// The adapter for a stream's bit inversion.
///
/// # Arguments
///
/// * `bit` - The bit.
///
/// # Returns
///
/// What the installed function gives, or the complement if none is.
fn bit_invert_adapter(bit: u8) -> u8 {
    call(&BIT_INVERT, |f| f(bit)).unwrap_or(bit ^ 1)
}

/// A plain function for the runtime, and the closure it calls installed
/// for as long as this lives.
pub(crate) struct Adapted<F, T: ?Sized + 'static> {
    /// The plain function, or `None` where there is no closure.
    pub f: Option<F>,
    /// The closure's installation.
    _installed: Option<Installed<T>>,
}

/// Gives the runtime a plain function that calls a closure of a kind.
macro_rules! adapt {
    ($name:ident, $slot:ident, $ty:ty, $ptr:ty, $adapter:ident) => {
        /// Installs a closure and gives the plain function that calls it.
        ///
        /// # Arguments
        ///
        /// * `f` - The closure, or `None`.
        ///
        /// # Returns
        ///
        /// The plain function, valid while what is returned lives.
        pub(crate) fn $name(f: Option<&Rc<$ty>>) -> Adapted<$ptr, $ty> {
            match f {
                Some(f) => Adapted {
                    f: Some($adapter as $ptr),
                    _installed: Some(install(&$slot, f)),
                },
                None => Adapted {
                    f: None,
                    _installed: None,
                },
            }
        }
    };
}

adapt!(
    checksum,
    CHECKSUM,
    ChecksumFn,
    fn(&[u8], usize, usize) -> u64,
    checksum_adapter
);
adapt!(
    binary_from,
    BINARY_FROM,
    BinaryFromFn,
    pk::BinaryFromFn,
    binary_from_adapter
);
adapt!(
    binary_to,
    BINARY_TO,
    BinaryToFn,
    pk::BinaryToFn,
    binary_to_adapter
);
adapt!(
    time_from,
    TIME_FROM,
    TimeFromFn,
    pk::TimeFromFn,
    time_from_adapter
);
adapt!(time_to, TIME_TO, TimeToFn, pk::TimeToFn, time_to_adapter);
adapt!(
    bit_invert,
    BIT_INVERT,
    BitInvertFn,
    pk::BitInvert,
    bit_invert_adapter
);
