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

/// The epoch a time code counts from.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Epoch {
    /// 1958-01-01T00:00:00 TAI.
    Ccsds,
    /// One that the agency (the mission) defines, such as the Unix epoch.
    Agency,
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
    /// A layout; `None` unless `coarse` is 1 to 7 and `fine` 0 to 10.
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

    /// Its P-field and its length: one octet, or two when it has more than
    /// 4 coarse or 3 fine octets.
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

    /// The layout that the P-field at the start of `data` gives, and the
    /// P-field's length; `None` if it isn't a CUC P-field.
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

    /// Decodes a T-field in this layout at the start of `data`: the octets
    /// read and the seconds since the epoch.
    pub fn decode_t(&self, data: &[u8]) -> Option<(usize, f64)> {
        let t = data.get(..self.t_len())?;
        let (c, f) = t.split_at(self.coarse as usize);
        let whole = c.iter().fold(0u64, |v, &b| (v << 8) | u64::from(b));
        // The fraction, least significant octet first, so every step is
        // exact until the octets hold more bits than an f64.
        let frac = f.iter().rev().fold(0.0, |v, &b| (v + f64::from(b)) / 256.0);
        Some((self.t_len(), whole as f64 + frac))
    }

    /// Encodes `seconds` as a T-field in this layout into `out`; see the
    /// module for what it gives.
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

    /// Encodes `seconds` with this layout's P-field first.
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
    let (layout, n) = Cuc::from_pfield(data)?;
    let (k, s) = layout.decode_t(&data[n..])?;
    Some((n + k, s))
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

    /// The octets of its T-field.
    pub const fn t_len(&self) -> usize {
        (if self.day24 { 3 } else { 2 })
            + 4
            + match self.sub {
                SubMs::None => 0,
                SubMs::Micro => 2,
                SubMs::Pico => 4,
            }
    }

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

    /// The layout that the P-field at the start of `data` gives; `None` if
    /// it isn't a CDS P-field.
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

    /// Decodes a T-field in this layout at the start of `data`: the octets
    /// read and the seconds since the epoch.  `None` if a field is beyond
    /// its range (milliseconds of a day count a leap second's too).
    pub fn decode_t(&self, data: &[u8]) -> Option<(usize, f64)> {
        let t = data.get(..self.t_len())?;
        let be =
            |b: &[u8]| b.iter().fold(0u64, |v, &x| (v << 8) | u64::from(x));
        let d = if self.day24 { 3 } else { 2 };
        let (day, ms) = (be(&t[..d]), be(&t[d..d + 4]));
        if ms >= 86_401_000 {
            return None;
        }
        let mut s = day as f64 * 86400.0 + ms as f64 / 1000.0;
        match self.sub {
            SubMs::None => {}
            SubMs::Micro => {
                let us = be(&t[d + 4..]);
                if us >= 1000 {
                    return None;
                }
                s += us as f64 / 1e6;
            }
            SubMs::Pico => {
                let ps = be(&t[d + 4..]);
                if ps >= 1_000_000_000 {
                    return None;
                }
                s += ps as f64 / 1e12;
            }
        }
        Some((self.t_len(), s))
    }

    /// Encodes `seconds` as a T-field in this layout into `out`; see the
    /// module for what it gives.
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

    /// Encodes `seconds` with this layout's P-field first.
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
}
