//! TAI and UTC, and the leap seconds between them.
//!
//! The binary time codes count seconds of TAI, which has no leap seconds; the
//! calendar codes give dates and times of UTC, which since 1972 has stayed
//! within a second of the Earth's rotation by inserting a leap second at the
//! end of a June or December now and then. Converting between them takes a
//! table of when, which the IERS publishes in Bulletin C and as
//! `leap-seconds.list`.
//!
//! [`LEAP_SECONDS`] is that table as of the list updated through Bulletin C
//! and expiring on 28 June 2027; a mission can give its own
//! [`LeapSeconds`] instead, as a newer bulletin announces more. A time is
//! converted only between 1972-01-01, when UTC took its present form, and
//! the table's expiry: before it UTC's seconds were not TAI's, and after it
//! nobody yet knows whether a leap second will come.
//!
//! A TAI time here is [`TaiTime`]: seconds since the CCSDS epoch,
//! 1958-01-01T00:00:00 TAI, and picoseconds, exactly.

use crate::calendar::{days_from_civil, CalendarTime, PICOS};

/// A time of TAI, exactly: seconds since 1958-01-01T00:00:00 TAI, and
/// picoseconds of the second.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Default)]
pub struct TaiTime {
    pub seconds: u64,
    /// Less than 10^12.
    pub picos: u64,
}

impl TaiTime {
    /// Gives the time as seconds since the CCSDS epoch, as a double, which
    /// is what the binary time codes' double encoders take.
    ///
    /// # Returns
    ///
    /// The seconds.
    pub fn to_seconds(&self) -> f64 {
        self.seconds as f64 + self.picos as f64 / 1e12
    }
}

/// A change in TAI - UTC: from 00:00:00 UTC on this date, it is this many
/// seconds.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LeapSecond {
    pub year: u16,
    pub month: u8,
    pub day: u8,
    pub tai_minus_utc: i32,
}

/// A table of leap seconds: every change in TAI - UTC from 1972-01-01 on,
/// in order, and the date from which nobody yet knows.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LeapSeconds<'a> {
    /// The first is 1972-01-01, when TAI - UTC was 10 seconds.
    pub changes: &'a [LeapSecond],
    /// The first day the table does not cover: a time from 00:00:00 UTC on
    /// it is not converted.
    pub expires: (u16, u8, u8),
}

/// A change, for the table below.
const fn change(year: u16, month: u8, tai_minus_utc: i32) -> LeapSecond {
    LeapSecond {
        year,
        month,
        day: 1,
        tai_minus_utc,
    }
}

/// The leap seconds of the IERS's `leap-seconds.list`, updated through
/// Bulletin C and expiring on 28 June 2027.
pub const LEAP_SECONDS: LeapSeconds<'static> = LeapSeconds {
    changes: &[
        change(1972, 1, 10),
        change(1972, 7, 11),
        change(1973, 1, 12),
        change(1974, 1, 13),
        change(1975, 1, 14),
        change(1976, 1, 15),
        change(1977, 1, 16),
        change(1978, 1, 17),
        change(1979, 1, 18),
        change(1980, 1, 19),
        change(1981, 7, 20),
        change(1982, 7, 21),
        change(1983, 7, 22),
        change(1985, 7, 23),
        change(1988, 1, 24),
        change(1990, 1, 25),
        change(1991, 1, 26),
        change(1992, 7, 27),
        change(1993, 7, 28),
        change(1994, 7, 29),
        change(1996, 1, 30),
        change(1997, 7, 31),
        change(1999, 1, 32),
        change(2006, 1, 33),
        change(2009, 1, 34),
        change(2012, 7, 35),
        change(2015, 7, 36),
        change(2017, 1, 37),
    ],
    expires: (2027, 6, 28),
};

/// Seconds in a day of TAI.
const DAY: i64 = 86_400;

/// Days from 1958-01-01 to a date.
fn days_since_epoch(year: u16, month: u8, day: u8) -> i64 {
    days_from_civil(year, month, day) - days_from_civil(1958, 1, 1)
}

impl LeapSeconds<'_> {
    /// Gives TAI - UTC on a day.
    ///
    /// # Arguments
    ///
    /// * `days` - The day, as days since 1958-01-01.
    ///
    /// # Returns
    ///
    /// The seconds, and the index of the change that set them; `None`
    /// before the first change or from the table's expiry.
    fn on(&self, days: i64) -> Option<(i64, usize)> {
        let (y, m, d) = self.expires;
        if days >= days_since_epoch(y, m, d) {
            return None;
        }
        let i = self
            .changes
            .iter()
            .rposition(|c| days_since_epoch(c.year, c.month, c.day) <= days)?;
        Some((i64::from(self.changes[i].tai_minus_utc), i))
    }

    /// Gives the TAI second a change takes effect at: 00:00:00 UTC on its
    /// day, in seconds since the CCSDS epoch.
    fn at(&self, i: usize) -> i64 {
        let c = &self.changes[i];
        days_since_epoch(c.year, c.month, c.day) * DAY
            + i64::from(c.tai_minus_utc)
    }
}

/// Converts a time of UTC to TAI.
///
/// # Arguments
///
/// * `utc`   - The time.
/// * `table` - The leap seconds.
///
/// # Returns
///
/// The time of TAI; `None` if the time is not valid, is before the table's
/// first change or from its expiry, or has a second of 60 on a day that
/// has no leap second.
pub fn utc_to_tai(utc: &CalendarTime, table: &LeapSeconds) -> Option<TaiTime> {
    if !utc.is_valid() {
        return None;
    }
    let days = days_since_epoch(utc.year, utc.month, utc.day);
    let (offset, _) = table.on(days)?;
    if utc.second == 60 {
        // Only where TAI - UTC goes up at the next midnight.
        let (next, _) = table.on(days + 1).unwrap_or((offset, 0));
        if next <= offset {
            return None;
        }
    }
    let seconds = days * DAY
        + i64::from(utc.hour) * 3600
        + i64::from(utc.minute) * 60
        + i64::from(utc.second)
        + offset;
    Some(TaiTime {
        seconds: u64::try_from(seconds).ok()?,
        picos: utc.picos,
    })
}

/// Converts a time of TAI to UTC.
///
/// # Arguments
///
/// * `tai`   - The time.
/// * `table` - The leap seconds.
///
/// # Returns
///
/// The time of UTC, with a second of 60 in a leap second; `None` if its
/// picoseconds are not less than 10^12, or it is before the table's first
/// change or from its expiry.
pub fn tai_to_utc(tai: &TaiTime, table: &LeapSeconds) -> Option<CalendarTime> {
    if tai.picos >= PICOS || table.changes.is_empty() {
        return None;
    }
    let s = i64::try_from(tai.seconds).ok()?;
    // The last change at or before it.
    let i = (0..table.changes.len()).rev().find(|&i| table.at(i) <= s)?;
    let offset = i64::from(table.changes[i].tai_minus_utc);
    // In the inserted second before the next change, it is 23:59:60 of the
    // day before.
    let leap = table.changes.get(i + 1).is_some_and(|next| {
        i64::from(next.tai_minus_utc) > offset && s >= table.at(i + 1) - 1
    });
    let utc = s - offset - i64::from(leap);
    let days = utc.div_euclid(DAY);
    let of_day = utc.rem_euclid(DAY);
    table.on(days)?;
    let (year, month, day) =
        crate::calendar::civil_from_days(days + days_from_civil(1958, 1, 1))?;
    Some(CalendarTime {
        year,
        month,
        day,
        hour: (of_day / 3600) as u8,
        minute: (of_day / 60 % 60) as u8,
        second: (of_day % 60) as u8 + u8::from(leap),
        picos: tai.picos,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A time of UTC, for the tests.
    fn utc(y: u16, mo: u8, d: u8, h: u8, mi: u8, s: u8) -> CalendarTime {
        CalendarTime {
            year: y,
            month: mo,
            day: d,
            hour: h,
            minute: mi,
            second: s,
            picos: 0,
        }
    }

    #[test]
    fn utc_began_ten_seconds_behind() {
        // 1972-01-01 is 5113 days after 1958-01-01.
        let t = utc_to_tai(&utc(1972, 1, 1, 0, 0, 0), &LEAP_SECONDS).unwrap();
        assert_eq!(t.seconds, 5113 * 86_400 + 10);
        assert_eq!(
            tai_to_utc(&t, &LEAP_SECONDS),
            Some(utc(1972, 1, 1, 0, 0, 0))
        );
        assert_eq!(
            utc_to_tai(&utc(1971, 12, 31, 23, 59, 59), &LEAP_SECONDS),
            None
        );
    }

    #[test]
    fn a_leap_second_is_one_second_of_tai() {
        let before = utc(2016, 12, 31, 23, 59, 59);
        let leap = utc(2016, 12, 31, 23, 59, 60);
        let after = utc(2017, 1, 1, 0, 0, 0);
        let [a, b, c] = [before, leap, after]
            .map(|t| utc_to_tai(&t, &LEAP_SECONDS).unwrap().seconds);
        assert_eq!((b - a, c - b), (1, 1));
        for (t, s) in [(before, a), (leap, b), (after, c)] {
            let tai = TaiTime {
                seconds: s,
                picos: 5,
            };
            assert_eq!(
                tai_to_utc(&tai, &LEAP_SECONDS),
                Some(CalendarTime { picos: 5, ..t })
            );
        }
        // TAI - UTC is 37 seconds after it.
        let t = utc_to_tai(&after, &LEAP_SECONDS).unwrap();
        assert_eq!(
            t.seconds as i64 - days_since_epoch(2017, 1, 1) * 86_400,
            37
        );
        // A second of 60 on a day without a leap second is refused.
        assert_eq!(
            utc_to_tai(&utc(2017, 12, 31, 23, 59, 60), &LEAP_SECONDS),
            None
        );
    }

    #[test]
    fn times_from_the_expiry_are_refused() {
        assert!(
            utc_to_tai(&utc(2027, 6, 27, 23, 59, 59), &LEAP_SECONDS).is_some()
        );
        assert_eq!(utc_to_tai(&utc(2027, 6, 28, 0, 0, 0), &LEAP_SECONDS), None);
        let last =
            utc_to_tai(&utc(2027, 6, 27, 23, 59, 59), &LEAP_SECONDS).unwrap();
        let next = TaiTime {
            seconds: last.seconds + 1,
            picos: 0,
        };
        assert_eq!(tai_to_utc(&next, &LEAP_SECONDS), None);
    }
}
