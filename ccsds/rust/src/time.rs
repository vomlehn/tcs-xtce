//! The binary time codes of CCSDS 301.0-B-4, *Time Code Formats*: CUC, the
//! CCSDS Unsegmented time Code, and CDS, the CCSDS Day Segmented time code.
//!
//! A time code is an optional P-field (preamble), which says which code it
//! is and how it is laid out, then a T-field holding the time.  A time is
//! given as seconds since the code's epoch, as an `f64`: either the CCSDS
//! epoch (1958-01-01, TAI) or one the agency, that is the mission, defines.
//! Decoding gives how many octets it read and the seconds; encoding writes
//! the octets and gives how many.  Encoding into too small an output writes
//! nothing and gives the number it needs, so the caller can tell that from
//! a time the layout can't hold, for which it gives `None`.
//!
//! Every computation is IEEE double arithmetic that the C and Python
//! versions do the same way, so all three give the same octets and seconds.
//! Encoding rounds to the nearest unit of the layout, halves up, carrying
//! into the seconds or days.
//!
//! A double holds about a quarter of a microsecond at today's times, which
//! is coarser than a CUC time of more than three fine octets or a CDS time
//! of microseconds or picoseconds. So each code also has an exact form, in
//! integers, that holds every time its layouts can: [`CucTime`], whose
//! fraction is in units of 2^-80, and [`CdsTime`], in days, milliseconds
//! and picoseconds. Its decoders and encoders are the `_exact` ones, and
//! the double ones are the exact ones followed by `to_seconds`.

/// The epoch a time code counts from.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Epoch {
    /// 1958-01-01T00:00:00 TAI.
    Ccsds,
    /// One that the agency (the mission) defines, such as the Unix epoch.
    Agency,
}

/// The bits of binary fraction a CUC time can hold: ten fine octets' worth.
pub const CUC_FRACTION_BITS: u32 = 80;

/// A CUC time, exactly: whole seconds since the epoch, and the fraction of
/// a second in units of 2^-80, which holds every layout's fine octets.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Default)]
pub struct CucTime {
    pub seconds: u64,
    /// Less than 2^80: the fine octets, most significant first, with the
    /// unused ones nought.
    pub fraction: u128,
}

impl CucTime {
    /// Gives the time as seconds since the epoch, rounded to a double as
    /// the double decoders do.
    ///
    /// # Returns
    ///
    /// The seconds.
    pub fn to_seconds(&self) -> f64 {
        // The fraction, least significant octet first, so every step is
        // exact until the octets hold more bits than an f64.
        let frac = (0..CUC_FRACTION_BITS / 8).fold(0.0, |v, i| {
            (v + f64::from((self.fraction >> (8 * i)) as u8)) / 256.0
        });
        self.seconds as f64 + frac
    }
}

/// A CDS time, exactly: days since the epoch, milliseconds of the day, and
/// picoseconds of the millisecond, which holds every layout's.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Default)]
pub struct CdsTime {
    pub day: u32,
    /// Less than 86 401 000: a day with a leap second has a thousand more.
    pub ms: u32,
    /// Less than 10^9.
    pub picos: u32,
}

impl CdsTime {
    /// Gives the time as seconds since the epoch, as a double: its
    /// picoseconds divided by 10^12, which is what the double decoder of a
    /// picosecond layout gives.
    ///
    /// # Returns
    ///
    /// The seconds.
    pub fn to_seconds(&self) -> f64 {
        self.whole_ms() + f64::from(self.picos) / 1e12
    }

    /// Gives the days and milliseconds as seconds, as every CDS double
    /// starts from.
    fn whole_ms(&self) -> f64 {
        f64::from(self.day) * 86400.0 + f64::from(self.ms) / 1000.0
    }
}

/// The layout of a CUC time: `coarse` octets (1 to 7) of whole seconds, most
/// significant first, then `fine` octets (0 to 10) of binary fractions of a
/// second, 2^-8 for the first.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Cuc {
    pub coarse: u8,
    pub fine: u8,
    pub epoch: Epoch,
}

impl Cuc {
    /// Makes a layout.
    ///
    /// # Arguments
    ///
    /// * `coarse` - The octets of whole seconds, 1 to 7.
    /// * `fine`   - The octets of fractions of a second, 0 to 10.
    /// * `epoch`  - The epoch the time counts from.
    ///
    /// # Returns
    ///
    /// The layout; `None` unless `coarse` and `fine` are in range.
    pub const fn new(coarse: u8, fine: u8, epoch: Epoch) -> Option<Self> {
        if coarse < 1 || coarse > 7 || fine > 10 {
            return None;
        }
        Some(Self {
            coarse,
            fine,
            epoch,
        })
    }

    /// The octets of its T-field.
    pub const fn t_len(&self) -> usize {
        (self.coarse + self.fine) as usize
    }

    /// Gives the layout's P-field.
    ///
    /// # Returns
    ///
    /// The P-field and its length: one octet, or two when the layout has
    /// more than 4 coarse or 3 fine octets.  An unused second octet is 0.
    pub fn pfield(&self) -> ([u8; 2], usize) {
        let id = match self.epoch {
            Epoch::Ccsds => 0b001,
            Epoch::Agency => 0b010,
        };
        let (c, f) = (self.coarse.min(4), self.fine.min(3));
        let more = self.coarse > 4 || self.fine > 3;
        let first = (u8::from(more) << 7) | (id << 4) | ((c - 1) << 2) | f;
        if !more {
            return ([first, 0], 1);
        }
        (
            [first, ((self.coarse - c) << 5) | ((self.fine - f) << 2)],
            2,
        )
    }

    /// Reads a CUC P-field.
    ///
    /// # Arguments
    ///
    /// * `data` - Octets that start with the P-field.
    ///
    /// # Returns
    ///
    /// The layout the P-field gives, and the P-field's length; `None` if
    /// it isn't a CUC P-field or `data` ends within it.
    pub fn from_pfield(data: &[u8]) -> Option<(Self, usize)> {
        let first = *data.first()?;
        let epoch = match (first >> 4) & 0b111 {
            0b001 => Epoch::Ccsds,
            0b010 => Epoch::Agency,
            _ => return None,
        };
        let (mut coarse, mut fine) = (((first >> 2) & 0b11) + 1, first & 0b11);
        if first & 0x80 == 0 {
            return Some((Self::new(coarse, fine, epoch)?, 1));
        }
        let second = *data.get(1)?;
        // A further extension, or the reserved bits, aren't defined.
        if second & 0x83 != 0 {
            return None;
        }
        coarse += (second >> 5) & 0b11;
        fine += (second >> 2) & 0b111;
        Some((Self::new(coarse, fine, epoch)?, 2))
    }

    /// Decodes a T-field in this layout.
    ///
    /// # Arguments
    ///
    /// * `data` - Octets that start with the T-field.
    ///
    /// # Returns
    ///
    /// The octets read and the seconds since the epoch; `None` if `data`
    /// is shorter than the T-field.
    pub fn decode_t(&self, data: &[u8]) -> Option<(usize, f64)> {
        let (k, t) = self.decode_t_exact(data)?;
        Some((k, t.to_seconds()))
    }

    /// Decodes a T-field in this layout, exactly.
    ///
    /// # Arguments
    ///
    /// * `data` - Octets that start with the T-field.
    ///
    /// # Returns
    ///
    /// The octets read and the time; `None` if `data` is shorter than the
    /// T-field.
    pub fn decode_t_exact(&self, data: &[u8]) -> Option<(usize, CucTime)> {
        let t = data.get(..self.t_len())?;
        let (c, f) = t.split_at(self.coarse as usize);
        let seconds = c.iter().fold(0u64, |v, &b| (v << 8) | u64::from(b));
        let fine = f.iter().fold(0u128, |v, &b| (v << 8) | u128::from(b));
        let fraction = fine << (CUC_FRACTION_BITS - 8 * u32::from(self.fine));
        Some((self.t_len(), CucTime { seconds, fraction }))
    }

    /// Encodes a time as a T-field in this layout, exactly where the layout
    /// has the bits and otherwise rounding the fraction to its fine octets,
    /// halves up, carrying into the seconds.
    ///
    /// # Arguments
    ///
    /// * `time` - The time.
    /// * `out`  - Where to write the T-field; nothing is written if it is
    ///            too small.
    ///
    /// # Returns
    ///
    /// The octets written, or needed if `out` is too small; `None` if the
    /// time's fraction is not less than 2^80 or the layout can't hold it.
    pub fn encode_t_exact(
        &self,
        time: &CucTime,
        out: &mut [u8],
    ) -> Option<usize> {
        if time.fraction >> CUC_FRACTION_BITS != 0 {
            return None;
        }
        let drop = CUC_FRACTION_BITS - 8 * u32::from(self.fine);
        let mut n = time.fraction >> drop;
        let mut seconds = time.seconds;
        if drop > 0 && (time.fraction >> (drop - 1)) & 1 == 1 {
            n += 1;
            if n >> (8 * u32::from(self.fine)) != 0 {
                n = 0;
                seconds = seconds.checked_add(1)?;
            }
        }
        if 8 * u32::from(self.coarse) < 64 && seconds >> (8 * self.coarse) != 0
        {
            return None;
        }
        if out.len() < self.t_len() {
            return Some(self.t_len());
        }
        let (c, f) = out[..self.t_len()].split_at_mut(self.coarse as usize);
        for (i, b) in c.iter_mut().rev().enumerate() {
            *b = (seconds >> (8 * i)) as u8;
        }
        for (i, b) in f.iter_mut().rev().enumerate() {
            *b = (n >> (8 * i)) as u8;
        }
        Some(self.t_len())
    }

    /// Encodes a time exactly, with this layout's P-field first.
    ///
    /// # Arguments
    ///
    /// * `time` - The time.
    /// * `out`  - Where to write the P-field and T-field; nothing is
    ///            written if it is too small.
    ///
    /// # Returns
    ///
    /// The octets written, or needed if `out` is too small; `None` if the
    /// layout can't hold the time.
    pub fn encode_exact(
        &self,
        time: &CucTime,
        out: &mut [u8],
    ) -> Option<usize> {
        let (p, n) = self.pfield();
        let t = self.encode_t_exact(time, &mut [])?;
        if out.len() < n + t {
            return Some(n + t);
        }
        out[..n].copy_from_slice(&p[..n]);
        self.encode_t_exact(time, &mut out[n..]).map(|k| n + k)
    }

    /// Encodes a time as a T-field in this layout, rounding to the nearest
    /// 2^-8 to the power of the fine octets.
    ///
    /// # Arguments
    ///
    /// * `seconds` - The time, in seconds since the epoch.
    /// * `out`     - Where to write the T-field; nothing is written if it
    ///               is too small.
    ///
    /// # Returns
    ///
    /// The octets written, or needed if `out` is too small; `None` if the
    /// layout can't hold the time.
    pub fn encode_t(&self, seconds: f64, out: &mut [u8]) -> Option<usize> {
        let limit = (1u64 << (8 * u32::from(self.coarse))) as f64;
        if !(seconds >= 0.0 && seconds < limit) {
            return None;
        }
        let mut whole = seconds as u64;
        let frac = seconds - whole as f64;
        let mut fine = [0u8; 10];
        let f = self.fine as usize;
        if f <= 6 {
            // To the nearest 2^-8f, which may carry a whole second.
            let unit = (1u64 << (8 * f)) as f64;
            let mut n = (frac * unit + 0.5) as u64;
            if n == 1u64 << (8 * f) {
                n = 0;
                whole += 1;
            }
            for (i, b) in fine[..f].iter_mut().rev().enumerate() {
                *b = (n >> (8 * i)) as u8;
            }
        } else {
            // More fine octets than an f64 has bits: the fraction is exact.
            let mut x = frac;
            for b in fine[..f].iter_mut() {
                x *= 256.0;
                *b = x as u8;
                x -= f64::from(*b);
            }
        }
        if (whole as f64) >= limit {
            return None;
        }
        if out.len() < self.t_len() {
            return Some(self.t_len());
        }
        let (c, rest) = out[..self.t_len()].split_at_mut(self.coarse as usize);
        for (i, b) in c.iter_mut().rev().enumerate() {
            *b = (whole >> (8 * i)) as u8;
        }
        rest.copy_from_slice(&fine[..f]);
        Some(self.t_len())
    }

    /// Encodes a time with this layout's P-field first.
    ///
    /// # Arguments
    ///
    /// * `seconds` - The time, in seconds since the epoch.
    /// * `out`     - Where to write the P-field and T-field; nothing is
    ///               written if it is too small.
    ///
    /// # Returns
    ///
    /// The octets written, or needed if `out` is too small; `None` if the
    /// layout can't hold the time.
    pub fn encode(&self, seconds: f64, out: &mut [u8]) -> Option<usize> {
        let (p, n) = self.pfield();
        let total = n + self.t_len();
        let t = self.encode_t(seconds, &mut [])?;
        if out.len() < total {
            return Some(n + t);
        }
        out[..n].copy_from_slice(&p[..n]);
        self.encode_t(seconds, &mut out[n..]).map(|k| n + k)
    }
}

/// Decodes a CUC time that starts with its P-field: the octets read and the
/// seconds since its epoch, which the P-field names.
pub fn decode_cuc(data: &[u8]) -> Option<(usize, f64)> {
    let (k, t) = decode_cuc_exact(data)?;
    Some((k, t.to_seconds()))
}

/// Decodes a CUC time that starts with its P-field, exactly: the octets read
/// and the time since its epoch, which the P-field names.
pub fn decode_cuc_exact(data: &[u8]) -> Option<(usize, CucTime)> {
    let (layout, n) = Cuc::from_pfield(data)?;
    let (k, t) = layout.decode_t_exact(&data[n..])?;
    Some((n + k, t))
}

/// The resolution of a CDS time below a millisecond.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SubMs {
    None,
    /// 16 bits of microseconds of the millisecond.
    Micro,
    /// 32 bits of picoseconds of the millisecond.
    Pico,
}

/// The layout of a CDS time: days since the epoch, in 16 bits or (`day24`)
/// 24, then milliseconds of the day in 32 bits, then `sub`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Cds {
    pub day24: bool,
    pub sub: SubMs,
    pub epoch: Epoch,
}

impl Cds {
    pub const fn new(day24: bool, sub: SubMs, epoch: Epoch) -> Self {
        Self { day24, sub, epoch }
    }

    /// Gives the length of the layout's T-field.
    ///
    /// # Returns
    ///
    /// The octets of its T-field: 6, 7, 8, 9, 10 or 11.
    pub const fn t_len(&self) -> usize {
        (if self.day24 { 3 } else { 2 })
            + 4
            + match self.sub {
                SubMs::None => 0,
                SubMs::Micro => 2,
                SubMs::Pico => 4,
            }
    }

    /// Gives the layout's P-field.
    ///
    /// # Returns
    ///
    /// Its one-octet P-field.
    pub fn pfield(&self) -> u8 {
        let sub = match self.sub {
            SubMs::None => 0,
            SubMs::Micro => 1,
            SubMs::Pico => 2,
        };
        (0b100 << 4)
            | (u8::from(self.epoch == Epoch::Agency) << 3)
            | (u8::from(self.day24) << 2)
            | sub
    }

    /// Reads a CDS P-field.
    ///
    /// # Arguments
    ///
    /// * `data` - Octets that start with the P-field.
    ///
    /// # Returns
    ///
    /// The layout the P-field gives; `None` if `data` is empty or it isn't
    /// a CDS P-field.
    pub fn from_pfield(data: &[u8]) -> Option<Self> {
        let p = *data.first()?;
        if p & 0x80 != 0 || (p >> 4) & 0b111 != 0b100 {
            return None;
        }
        let sub = match p & 0b11 {
            0 => SubMs::None,
            1 => SubMs::Micro,
            2 => SubMs::Pico,
            _ => return None,
        };
        let epoch = if p & 0b1000 != 0 {
            Epoch::Agency
        } else {
            Epoch::Ccsds
        };
        Some(Self::new(p & 0b100 != 0, sub, epoch))
    }

    /// Decodes a T-field in this layout.
    ///
    /// # Arguments
    ///
    /// * `data` - Octets that start with the T-field.
    ///
    /// # Returns
    ///
    /// The octets read and the seconds since the epoch; `None` if `data`
    /// is shorter than the T-field or a field is beyond its range
    /// (milliseconds of a day count a leap second's too).
    pub fn decode_t(&self, data: &[u8]) -> Option<(usize, f64)> {
        let (k, t) = self.decode_t_exact(data)?;
        // In the layout's own unit, so microseconds are divided as such.
        let s = match self.sub {
            SubMs::None => t.whole_ms(),
            SubMs::Micro => t.whole_ms() + f64::from(t.picos / 1_000_000) / 1e6,
            SubMs::Pico => t.to_seconds(),
        };
        Some((k, s))
    }

    /// Decodes a T-field in this layout, exactly.
    ///
    /// # Arguments
    ///
    /// * `data` - Octets that start with the T-field.
    ///
    /// # Returns
    ///
    /// The octets read and the time; `None` if `data` is shorter than the
    /// T-field or a field is beyond its range (milliseconds of a day count
    /// a leap second's too).
    pub fn decode_t_exact(&self, data: &[u8]) -> Option<(usize, CdsTime)> {
        let t = data.get(..self.t_len())?;
        let be =
            |b: &[u8]| b.iter().fold(0u32, |v, &x| (v << 8) | u32::from(x));
        let d = if self.day24 { 3 } else { 2 };
        let (day, ms) = (be(&t[..d]), be(&t[d..d + 4]));
        if ms >= 86_401_000 {
            return None;
        }
        let picos = match self.sub {
            SubMs::None => 0,
            SubMs::Micro => {
                let us = be(&t[d + 4..]);
                if us >= 1000 {
                    return None;
                }
                us * 1_000_000
            }
            SubMs::Pico => {
                let ps = be(&t[d + 4..]);
                if ps >= 1_000_000_000 {
                    return None;
                }
                ps
            }
        };
        Some((self.t_len(), CdsTime { day, ms, picos }))
    }

    /// Encodes a time as a T-field in this layout, exactly where the layout
    /// has the resolution and otherwise rounding to its unit, halves up,
    /// carrying into the milliseconds and the days. A time in a leap
    /// second carries into the next day only past the leap second.
    ///
    /// # Arguments
    ///
    /// * `time` - The time.
    /// * `out`  - Where to write the T-field; nothing is written if it is
    ///            too small.
    ///
    /// # Returns
    ///
    /// The octets written, or needed if `out` is too small; `None` if a
    /// field of the time is beyond its range or the layout can't hold it.
    pub fn encode_t_exact(
        &self,
        time: &CdsTime,
        out: &mut [u8],
    ) -> Option<usize> {
        let days: u32 = if self.day24 { 1 << 24 } else { 1 << 16 };
        if time.ms >= 86_401_000 || time.picos >= 1_000_000_000 {
            return None;
        }
        let unit: u32 = match self.sub {
            SubMs::None => 1_000_000_000,
            SubMs::Micro => 1_000_000,
            SubMs::Pico => 1,
        };
        let (mut day, mut ms) = (time.day, time.ms);
        let mut sub = (time.picos + unit / 2) / unit;
        if sub * unit == 1_000_000_000 {
            sub = 0;
            ms += 1;
            // The day ends a second later when this millisecond was in its
            // leap second.
            let end = if time.ms >= 86_400_000 {
                86_401_000
            } else {
                86_400_000
            };
            if ms == end {
                ms = 0;
                day = day.checked_add(1)?;
            }
        }
        if day >= days {
            return None;
        }
        if out.len() < self.t_len() {
            return Some(self.t_len());
        }
        let d = if self.day24 { 3 } else { 2 };
        let put = |out: &mut [u8], v: u32| {
            let n = out.len();
            for (i, b) in out.iter_mut().enumerate() {
                *b = (v >> (8 * (n - 1 - i))) as u8;
            }
        };
        put(&mut out[..d], day);
        put(&mut out[d..d + 4], ms);
        match self.sub {
            SubMs::None => {}
            SubMs::Micro => put(&mut out[d + 4..d + 6], sub),
            SubMs::Pico => put(&mut out[d + 4..d + 8], sub),
        }
        Some(self.t_len())
    }

    /// Encodes a time exactly, with this layout's P-field first.
    ///
    /// # Arguments
    ///
    /// * `time` - The time.
    /// * `out`  - Where to write the P-field and T-field; nothing is
    ///            written if it is too small.
    ///
    /// # Returns
    ///
    /// The octets written, or needed if `out` is too small; `None` if the
    /// layout can't hold the time.
    pub fn encode_exact(
        &self,
        time: &CdsTime,
        out: &mut [u8],
    ) -> Option<usize> {
        let t = self.encode_t_exact(time, &mut [])?;
        if out.len() < 1 + t {
            return Some(1 + t);
        }
        out[0] = self.pfield();
        self.encode_t_exact(time, &mut out[1..]).map(|k| 1 + k)
    }

    /// Encodes a time as a T-field in this layout, rounding to the nearest
    /// unit of the layout.
    ///
    /// # Arguments
    ///
    /// * `seconds` - The time, in seconds since the epoch.
    /// * `out`     - Where to write the T-field; nothing is written if it
    ///               is too small.
    ///
    /// # Returns
    ///
    /// The octets written, or needed if `out` is too small; `None` if the
    /// layout can't hold the time.
    pub fn encode_t(&self, seconds: f64, out: &mut [u8]) -> Option<usize> {
        let days: u64 = if self.day24 { 1 << 24 } else { 1 << 16 };
        if !(seconds >= 0.0 && seconds < days as f64 * 86400.0) {
            return None;
        }
        // To the nearest unit, which may carry a whole day.
        let per_ms: u64 = match self.sub {
            SubMs::None => 1,
            SubMs::Micro => 1000,
            SubMs::Pico => 1_000_000_000,
        };
        let per_day = 86_400_000 * per_ms;
        let mut day = (seconds / 86400.0) as u64;
        let rem = seconds - day as f64 * 86400.0;
        let mut n = (rem * (1000 * per_ms) as f64 + 0.5) as u64;
        if n >= per_day {
            n -= per_day;
            day += 1;
        }
        if day >= days {
            return None;
        }
        if out.len() < self.t_len() {
            return Some(self.t_len());
        }
        let d = if self.day24 { 3 } else { 2 };
        let put = |out: &mut [u8], v: u64| {
            let n = out.len();
            for (i, b) in out.iter_mut().enumerate() {
                *b = (v >> (8 * (n - 1 - i))) as u8;
            }
        };
        put(&mut out[..d], day);
        put(&mut out[d..d + 4], n / per_ms);
        let sub = n % per_ms;
        match self.sub {
            SubMs::None => {}
            SubMs::Micro => put(&mut out[d + 4..d + 6], sub),
            SubMs::Pico => put(&mut out[d + 4..d + 8], sub),
        }
        Some(self.t_len())
    }

    /// Encodes a time with this layout's P-field first.
    ///
    /// # Arguments
    ///
    /// * `seconds` - The time, in seconds since the epoch.
    /// * `out`     - Where to write the P-field and T-field; nothing is
    ///               written if it is too small.
    ///
    /// # Returns
    ///
    /// The octets written, or needed if `out` is too small; `None` if the
    /// layout can't hold the time.
    pub fn encode(&self, seconds: f64, out: &mut [u8]) -> Option<usize> {
        let t = self.encode_t(seconds, &mut [])?;
        if out.len() < 1 + t {
            return Some(1 + t);
        }
        out[0] = self.pfield();
        self.encode_t(seconds, &mut out[1..]).map(|k| 1 + k)
    }
}

/// Decodes a CDS time that starts with its P-field: the octets read and the
/// seconds since its epoch, which the P-field names.
pub fn decode_cds(data: &[u8]) -> Option<(usize, f64)> {
    let layout = Cds::from_pfield(data)?;
    let (k, s) = layout.decode_t(&data[1..])?;
    Some((1 + k, s))
}

/// Decodes a CDS time that starts with its P-field, exactly: the octets read
/// and the time since its epoch, which the P-field names.
pub fn decode_cds_exact(data: &[u8]) -> Option<(usize, CdsTime)> {
    let layout = Cds::from_pfield(data)?;
    let (k, t) = layout.decode_t_exact(&data[1..])?;
    Some((1 + k, t))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cuc_round_trip() {
        let c = Cuc::new(4, 2, Epoch::Agency).unwrap();
        let mut b = [0u8; 8];
        assert_eq!(c.encode(1000.5, &mut b), Some(7));
        // 0 010 11 10: agency epoch, 4 coarse and 2 fine octets.
        assert_eq!(b[..7], [0x2e, 0, 0, 0x03, 0xe8, 0x80, 0]);
        assert_eq!(decode_cuc(&b), Some((7, 1000.5)));
        assert_eq!(c.encode(1.0, &mut b[..3]), Some(7));
        assert_eq!(c.encode(-1.0, &mut b), None);
    }

    #[test]
    fn cuc_extended_pfield() {
        let c = Cuc::new(6, 5, Epoch::Ccsds).unwrap();
        let (p, n) = c.pfield();
        assert_eq!(p[..n], [0x9f, 0b0100_1000]);
        assert_eq!(Cuc::from_pfield(&p), Some((c, 2)));
    }

    #[test]
    fn cds_round_trip() {
        let c = Cds::new(false, SubMs::Micro, Epoch::Ccsds);
        let mut b = [0u8; 9];
        // Day 2, 3.004005 seconds into it.
        let s = 2.0 * 86400.0 + 3.004 + 5e-6;
        assert_eq!(c.encode(s, &mut b), Some(9));
        assert_eq!(b, [0x41, 0, 2, 0, 0, 0x0b, 0xbc, 0, 5]);
        assert_eq!(decode_cds(&b), Some((9, 2.0 * 86400.0 + 3.004 + 5e-6)));
    }

    #[test]
    fn cuc_exact_round_trip_keeps_every_bit() {
        // Seven coarse and ten fine octets: more than a double holds.
        let c = Cuc::new(7, 10, Epoch::Ccsds).unwrap();
        let t = CucTime {
            seconds: 0x00ff_eedd_ccbb_aa99,
            fraction: 0x8877_6655_4433_2211_0001,
        };
        let mut b = [0u8; 19];
        assert_eq!(c.encode_exact(&t, &mut b), Some(19));
        assert_eq!(decode_cuc_exact(&b), Some((19, t)));
        // The double decoder gives the double of the same time.
        assert_eq!(decode_cuc(&b), Some((19, t.to_seconds())));
    }

    #[test]
    fn cuc_exact_rounds_half_up_and_carries() {
        let c = Cuc::new(1, 1, Epoch::Agency).unwrap();
        let mut b = [0u8; 2];
        let half = 1u128 << (CUC_FRACTION_BITS - 9);
        // Just under half a unit rounds down, half rounds up.
        let t = CucTime {
            seconds: 5,
            fraction: (0x12 << (CUC_FRACTION_BITS - 8)) + half - 1,
        };
        assert_eq!(c.encode_t_exact(&t, &mut b), Some(2));
        assert_eq!(b, [5, 0x12]);
        let t = CucTime {
            fraction: t.fraction + 1,
            ..t
        };
        c.encode_t_exact(&t, &mut b);
        assert_eq!(b, [5, 0x13]);
        // A fraction rounding up to a whole second carries, and past the
        // coarse octets it is a time the layout can't hold.
        let t = CucTime {
            seconds: 5,
            fraction: (1 << CUC_FRACTION_BITS) - 1,
        };
        c.encode_t_exact(&t, &mut b);
        assert_eq!(b, [6, 0]);
        let t = CucTime { seconds: 255, ..t };
        assert_eq!(c.encode_t_exact(&t, &mut b), None);
        let t = CucTime {
            seconds: 0,
            fraction: 1 << CUC_FRACTION_BITS,
        };
        assert_eq!(c.encode_t_exact(&t, &mut b), None);
    }

    #[test]
    fn cds_exact_keeps_picoseconds() {
        let c = Cds::new(true, SubMs::Pico, Epoch::Agency);
        let t = CdsTime {
            day: 0x12_3456,
            ms: 86_399_999,
            picos: 999_999_999,
        };
        let mut b = [0u8; 12];
        assert_eq!(c.encode_exact(&t, &mut b), Some(12));
        assert_eq!(decode_cds_exact(&b), Some((12, t)));
        assert_eq!(decode_cds(&b), Some((12, t.to_seconds())));
    }

    #[test]
    fn cds_exact_rounds_into_the_next_day_after_any_leap_second() {
        let mut b = [0u8; 7];
        let c = Cds::new(false, SubMs::None, Epoch::Ccsds);
        // The last half millisecond of an ordinary day ends it.
        let t = CdsTime {
            day: 9,
            ms: 86_399_999,
            picos: 500_000_000,
        };
        assert_eq!(c.encode_exact(&t, &mut b), Some(7));
        assert_eq!(
            decode_cds_exact(&b),
            Some((
                7,
                CdsTime {
                    day: 10,
                    ..CdsTime::default()
                }
            ))
        );
        // In a leap second the day goes on a second longer.
        let t = CdsTime {
            ms: 86_400_000,
            ..t
        };
        c.encode_exact(&t, &mut b);
        assert_eq!(
            decode_cds_exact(&b),
            Some((
                7,
                CdsTime {
                    day: 9,
                    ms: 86_400_001,
                    picos: 0
                }
            ))
        );
        let t = CdsTime {
            ms: 86_400_999,
            ..t
        };
        c.encode_exact(&t, &mut b);
        assert_eq!(
            decode_cds_exact(&b),
            Some((
                7,
                CdsTime {
                    day: 10,
                    ..CdsTime::default()
                }
            ))
        );
        // A field out of range is refused.
        let t = CdsTime {
            picos: 1_000_000_000,
            ..t
        };
        assert_eq!(c.encode_exact(&t, &mut b), None);
    }
}
