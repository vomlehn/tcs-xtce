//! The calendar time codes of CCSDS 301.0-B-4, *Time Code Formats*: CCS,
//! the CCSDS Calendar Segmented time code, and the ASCII time codes A and B.
//!
//! All three give a time as a date and a time of day, usually UTC, held here
//! exactly as a [`CalendarTime`]: year, month, day, hour, minute, second
//! (60 in a leap second) and picoseconds. Turning one into a count of
//! seconds needs to know which days had leap seconds, which is
//! [`crate::utc`]'s business.
//!
//! * **CCS** is an optional P-field, then a T-field of BCD digits: the year
//!   in two octets, the month and day of the month in one each or the day of
//!   the year in two, then the hour, minute and second in one each, then 0
//!   to 6 octets of two decimal digits each, hundredths of a second for the
//!   first.
//! * **ASCII code A** is `YYYY-MM-DDThh:mm:ss.d...dZ`, and **code B**
//!   `YYYY-DDDThh:mm:ss.d...dZ`, with the day of the year. The fraction of a
//!   second, any number of digits, and the closing `Z` are optional; here a
//!   fraction holds at most twelve digits, a picosecond.
//!
//! The codes check what they decode: a field must be a digit or BCD digits,
//! a date must be in the Gregorian calendar from year 1 to 9999, and a
//! second of 60 only ends a day, at 23:59. Encoding rounds the picoseconds
//! to the layout's unit, halves up, carrying into the second and on through
//! the calendar. A carry out of second 59 goes to the next minute: the codes
//! do not know which days had leap seconds.

/// A date and a time of day, exactly.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Default)]
pub struct CalendarTime {
    /// 1 to 9999.
    pub year: u16,
    /// 1 to 12.
    pub month: u8,
    /// 1 to the days of the month.
    pub day: u8,
    /// 0 to 23.
    pub hour: u8,
    /// 0 to 59.
    pub minute: u8,
    /// 0 to 59, or 60 in a leap second, which only ends a day, at 23:59.
    pub second: u8,
    /// Picoseconds of the second, less than 10^12.
    pub picos: u64,
}

/// Picoseconds in a second.
pub(crate) const PICOS: u64 = 1_000_000_000_000;

/// Says whether a year of the Gregorian calendar is a leap year.
///
/// # Arguments
///
/// * `year` - The year.
///
/// # Returns
///
/// Whether February has 29 days in it.
pub const fn is_leap_year(year: u16) -> bool {
    (year.is_multiple_of(4) && !year.is_multiple_of(100))
        || year.is_multiple_of(400)
}

/// Gives the days of a month.
///
/// # Arguments
///
/// * `year`  - The year.
/// * `month` - The month, 1 to 12.
///
/// # Returns
///
/// Its days; 0 for a month that is not 1 to 12.
pub const fn days_in_month(year: u16, month: u8) -> u8 {
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 if is_leap_year(year) => 29,
        2 => 28,
        _ => 0,
    }
}

/// Counts the days from 1970-01-01 to a date of the Gregorian calendar.
///
/// # Arguments
///
/// * `year`  - The year.
/// * `month` - The month, 1 to 12.
/// * `day`   - The day of the month.
///
/// # Returns
///
/// The days, negative before 1970.
pub(crate) fn days_from_civil(year: u16, month: u8, day: u8) -> i64 {
    // Howard Hinnant's algorithm, with the year starting in March.
    let y = i64::from(year) - i64::from(month <= 2);
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let m = i64::from(month);
    let doy =
        (153 * (m + if m > 2 { -3 } else { 9 }) + 2) / 5 + i64::from(day) - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

/// Gives the date a count of days from 1970-01-01 falls on.
///
/// # Arguments
///
/// * `days` - The days, negative before 1970.
///
/// # Returns
///
/// The year, month and day; `None` outside years 1 to 9999.
pub(crate) fn civil_from_days(days: i64) -> Option<(u16, u8, u8)> {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    if !(1..=9999).contains(&year) {
        return None;
    }
    Some((year as u16, month as u8, day as u8))
}

impl CalendarTime {
    /// Says whether every field is in its range, as described on each.
    ///
    /// # Returns
    ///
    /// Whether it is a time the calendar codes can hold.
    pub fn is_valid(&self) -> bool {
        (1..=9999).contains(&self.year)
            && (1..=12).contains(&self.month)
            && self.day >= 1
            && self.day <= days_in_month(self.year, self.month)
            && self.hour <= 23
            && self.minute <= 59
            && (self.second <= 59
                || (self.second == 60 && self.hour == 23 && self.minute == 59))
            && self.picos < PICOS
    }

    /// Gives the day of the year.
    ///
    /// # Returns
    ///
    /// 1 for the first of January, up to 366.
    pub fn day_of_year(&self) -> u16 {
        (days_from_civil(self.year, self.month, self.day)
            - days_from_civil(self.year, 1, 1)
            + 1) as u16
    }

    /// Makes a time from a day of the year.
    ///
    /// # Arguments
    ///
    /// * `year` - The year, 1 to 9999.
    /// * `doy`  - The day of the year, 1 to 365, or 366 in a leap year.
    ///
    /// # Returns
    ///
    /// Midnight at the start of that day; `None` for a year or day out of
    /// range.
    pub fn from_day_of_year(year: u16, doy: u16) -> Option<CalendarTime> {
        let days = if is_leap_year(year) { 366 } else { 365 };
        if !(1..=9999).contains(&year) || doy < 1 || doy > days {
            return None;
        }
        let (year, month, day) =
            civil_from_days(days_from_civil(year, 1, 1) + i64::from(doy) - 1)?;
        Some(CalendarTime {
            year,
            month,
            day,
            ..CalendarTime::default()
        })
    }

    /// Rounds the picoseconds to a unit, halves up, carrying into the
    /// second and on through the calendar. A carry out of second 60 ends
    /// the day; out of second 59 it goes to the next minute.
    ///
    /// # Arguments
    ///
    /// * `unit` - The unit, in picoseconds: a power of ten up to 10^12.
    ///
    /// # Returns
    ///
    /// The rounded time, its picoseconds a multiple of `unit`; `None` if
    /// it is not valid or the carry runs past year 9999.
    pub fn rounded(&self, unit: u64) -> Option<CalendarTime> {
        if !self.is_valid() {
            return None;
        }
        let mut t = *self;
        let n = (t.picos + unit / 2) / unit;
        if n * unit < PICOS {
            t.picos = n * unit;
            return Some(t);
        }
        t.picos = 0;
        if t.second < 59 {
            t.second += 1;
            return Some(t);
        }
        // Out of the last second of a minute, or of a leap second.
        t.second = 0;
        if t.minute < 59 {
            t.minute += 1;
            return Some(t);
        }
        t.minute = 0;
        if t.hour < 23 {
            t.hour += 1;
            return Some(t);
        }
        t.hour = 0;
        let (year, month, day) =
            civil_from_days(days_from_civil(t.year, t.month, t.day) + 1)?;
        t.year = year;
        t.month = month;
        t.day = day;
        Some(t)
    }
}

/// Reads one octet of two BCD digits.
fn bcd(b: u8) -> Option<u8> {
    let (hi, lo) = (b >> 4, b & 0x0f);
    (hi <= 9 && lo <= 9).then_some(hi * 10 + lo)
}

/// Writes a number of up to two decimal digits as one octet of BCD.
fn to_bcd(v: u8) -> u8 {
    ((v / 10) << 4) | (v % 10)
}

/// The layout of a CCS time: the date as a month and day of the month or as
/// a day of the year, then `sub` octets (0 to 6) of two decimal digits of a
/// second each.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Ccs {
    pub day_of_year: bool,
    pub sub: u8,
}

impl Ccs {
    /// Makes a layout.
    ///
    /// # Arguments
    ///
    /// * `day_of_year` - Whether the date is a day of the year rather than a
    ///                   month and day.
    /// * `sub`         - The octets of fractions of a second, 0 to 6.
    ///
    /// # Returns
    ///
    /// The layout; `None` unless `sub` is in range.
    pub const fn new(day_of_year: bool, sub: u8) -> Option<Self> {
        if sub > 6 {
            return None;
        }
        Some(Self { day_of_year, sub })
    }

    /// Gives the length of the layout's T-field.
    ///
    /// # Returns
    ///
    /// The octets of its T-field: 7 to 13.
    pub const fn t_len(&self) -> usize {
        7 + self.sub as usize
    }

    /// Gives what the layout's last digit of a second counts.
    ///
    /// # Returns
    ///
    /// The picoseconds of one unit of its last digit.
    const fn unit(&self) -> u64 {
        let mut unit = PICOS;
        let mut i = 0;
        while i < self.sub {
            unit /= 100;
            i += 1;
        }
        unit
    }

    /// Gives the layout's P-field.
    ///
    /// # Returns
    ///
    /// Its one-octet P-field.
    pub fn pfield(&self) -> u8 {
        (0b101 << 4) | (u8::from(self.day_of_year) << 3) | self.sub
    }

    /// Reads a CCS P-field.
    ///
    /// # Arguments
    ///
    /// * `data` - Octets that start with the P-field.
    ///
    /// # Returns
    ///
    /// The layout the P-field gives; `None` if `data` is empty or it isn't
    /// a CCS P-field (an extension, which CCS does not define, or a
    /// resolution of 7, which is reserved).
    pub fn from_pfield(data: &[u8]) -> Option<Self> {
        let p = *data.first()?;
        if p & 0x80 != 0 || (p >> 4) & 0b111 != 0b101 {
            return None;
        }
        Self::new(p & 0b1000 != 0, p & 0b111)
    }

    /// Decodes a T-field in this layout.
    ///
    /// # Arguments
    ///
    /// * `data` - Octets that start with the T-field.
    ///
    /// # Returns
    ///
    /// The octets read and the time; `None` if `data` is shorter than the
    /// T-field, an octet is not two BCD digits, or the time is not valid
    /// (see [`CalendarTime::is_valid`]).
    pub fn decode_t(&self, data: &[u8]) -> Option<(usize, CalendarTime)> {
        let t = data.get(..self.t_len())?;
        let year = u16::from(bcd(t[0])?) * 100 + u16::from(bcd(t[1])?);
        let mut time = if self.day_of_year {
            // Three digits in two octets: the first nibble is nought.
            if t[2] >> 4 != 0 {
                return None;
            }
            let doy = u16::from(t[2] & 0x0f) * 100 + u16::from(bcd(t[3])?);
            if (t[2] & 0x0f) > 9 {
                return None;
            }
            CalendarTime::from_day_of_year(year, doy)?
        } else {
            CalendarTime {
                year,
                month: bcd(t[2])?,
                day: bcd(t[3])?,
                ..CalendarTime::default()
            }
        };
        time.hour = bcd(t[4])?;
        time.minute = bcd(t[5])?;
        time.second = bcd(t[6])?;
        let mut picos = 0u64;
        for &b in &t[7..] {
            picos = picos * 100 + u64::from(bcd(b)?);
        }
        time.picos = picos * self.unit();
        time.is_valid().then_some((self.t_len(), time))
    }

    /// Encodes a time as a T-field in this layout, rounding the picoseconds
    /// to the layout's unit, halves up, carrying through the calendar.
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
    /// time is not valid or rounds past year 9999.
    pub fn encode_t(
        &self,
        time: &CalendarTime,
        out: &mut [u8],
    ) -> Option<usize> {
        let t = time.rounded(self.unit())?;
        if out.len() < self.t_len() {
            return Some(self.t_len());
        }
        out[0] = to_bcd((t.year / 100) as u8);
        out[1] = to_bcd((t.year % 100) as u8);
        if self.day_of_year {
            let doy = t.day_of_year();
            out[2] = (doy / 100) as u8;
            out[3] = to_bcd((doy % 100) as u8);
        } else {
            out[2] = to_bcd(t.month);
            out[3] = to_bcd(t.day);
        }
        out[4] = to_bcd(t.hour);
        out[5] = to_bcd(t.minute);
        out[6] = to_bcd(t.second);
        let mut n = t.picos / self.unit();
        for b in out[7..self.t_len()].iter_mut().rev() {
            *b = to_bcd((n % 100) as u8);
            n /= 100;
        }
        Some(self.t_len())
    }

    /// Encodes a time with this layout's P-field first.
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
    /// time is not valid or rounds past year 9999.
    pub fn encode(&self, time: &CalendarTime, out: &mut [u8]) -> Option<usize> {
        let t = self.encode_t(time, &mut [])?;
        if out.len() < 1 + t {
            return Some(1 + t);
        }
        out[0] = self.pfield();
        self.encode_t(time, &mut out[1..]).map(|k| 1 + k)
    }
}

/// Decodes a CCS time that starts with its P-field: the octets read and the
/// time.
pub fn decode_ccs(data: &[u8]) -> Option<(usize, CalendarTime)> {
    let layout = Ccs::from_pfield(data)?;
    let (k, t) = layout.decode_t(&data[1..])?;
    Some((1 + k, t))
}

/// Which ASCII time code: with a month and day, or with a day of the year.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AsciiForm {
    /// Code A: `YYYY-MM-DDThh:mm:ss`.
    A,
    /// Code B: `YYYY-DDDThh:mm:ss`.
    B,
}

/// The most digits of a fraction of a second the ASCII codes hold here.
pub const ASCII_MAX_DIGITS: u8 = 12;

/// Reads a fixed number of decimal digits.
///
/// # Arguments
///
/// * `text` - The characters.
///
/// # Returns
///
/// Their value; `None` if one is not a digit.
fn digits(text: &[u8]) -> Option<u64> {
    text.iter().try_fold(0u64, |v, &c| {
        c.is_ascii_digit().then(|| v * 10 + u64::from(c - b'0'))
    })
}

/// Parses an ASCII time code, A or B.
///
/// # Arguments
///
/// * `text` - Characters that start with the time code; any after it are
///            left.
///
/// # Returns
///
/// The characters read, which code it is, and the time; `None` if `text`
/// does not start with one, its fraction has more than
/// [`ASCII_MAX_DIGITS`] digits, or the time is not valid.
pub fn parse_ascii(text: &[u8]) -> Option<(usize, AsciiForm, CalendarTime)> {
    let at = |i: usize| text.get(i).copied();
    if at(4) != Some(b'-') {
        return None;
    }
    let year = digits(text.get(..4)?)? as u16;
    // Code A has a second dash two digits on; code B a T three digits on.
    let (form, mut time, mut i) = if at(7) == Some(b'-') {
        let t = CalendarTime {
            year,
            month: digits(text.get(5..7)?)? as u8,
            day: digits(text.get(8..10)?)? as u8,
            ..CalendarTime::default()
        };
        (AsciiForm::A, t, 10)
    } else {
        let doy = digits(text.get(5..8)?)? as u16;
        (AsciiForm::B, CalendarTime::from_day_of_year(year, doy)?, 8)
    };
    if at(i) != Some(b'T') || at(i + 3) != Some(b':') || at(i + 6) != Some(b':')
    {
        return None;
    }
    time.hour = digits(text.get(i + 1..i + 3)?)? as u8;
    time.minute = digits(text.get(i + 4..i + 6)?)? as u8;
    time.second = digits(text.get(i + 7..i + 9)?)? as u8;
    i += 9;
    if at(i) == Some(b'.') {
        let n = text[i + 1..]
            .iter()
            .take_while(|c| c.is_ascii_digit())
            .count();
        if n == 0 || n > usize::from(ASCII_MAX_DIGITS) {
            return None;
        }
        let frac = digits(&text[i + 1..i + 1 + n])?;
        time.picos = frac * 10u64.pow(12 - n as u32);
        i += 1 + n;
    }
    if at(i) == Some(b'Z') {
        i += 1;
    }
    time.is_valid().then_some((i, form, time))
}

/// Writes a number in a fixed number of decimal digits.
///
/// # Arguments
///
/// * `out` - Where the digits go, one ASCII digit to an octet, as many
///           as it holds, with leading zeros; higher digits of `v` that
///           do not fit are left out.
/// * `v`   - The number.
fn put_digits(out: &mut [u8], mut v: u64) {
    for c in out.iter_mut().rev() {
        *c = b'0' + (v % 10) as u8;
        v /= 10;
    }
}

/// Formats a time as an ASCII time code, rounding the picoseconds to the
/// digits asked for, halves up, carrying through the calendar.
///
/// # Arguments
///
/// * `time`   - The time.
/// * `form`   - Code A or code B.
/// * `digits` - The digits of a fraction of a second, 0 to
///              [`ASCII_MAX_DIGITS`]; with none, no `.` is written.
/// * `z`      - Whether to end it with `Z`.
/// * `out`    - Where to write it; nothing is written if it is too small.
///
/// # Returns
///
/// The characters written, or needed if `out` is too small; `None` if
/// `digits` is too many, the time is not valid, or it rounds past year
/// 9999.
pub fn format_ascii(
    time: &CalendarTime,
    form: AsciiForm,
    digits: u8,
    z: bool,
    out: &mut [u8],
) -> Option<usize> {
    if digits > ASCII_MAX_DIGITS {
        return None;
    }
    let unit = 10u64.pow(12 - u32::from(digits));
    let t = time.rounded(unit)?;
    let date = if form == AsciiForm::A { 10 } else { 8 };
    let len = date
        + 9
        + if digits > 0 {
            1 + usize::from(digits)
        } else {
            0
        }
        + usize::from(z);
    if out.len() < len {
        return Some(len);
    }
    put_digits(&mut out[..4], u64::from(t.year));
    out[4] = b'-';
    if form == AsciiForm::A {
        put_digits(&mut out[5..7], u64::from(t.month));
        out[7] = b'-';
        put_digits(&mut out[8..10], u64::from(t.day));
    } else {
        put_digits(&mut out[5..8], u64::from(t.day_of_year()));
    }
    out[date] = b'T';
    put_digits(&mut out[date + 1..date + 3], u64::from(t.hour));
    out[date + 3] = b':';
    put_digits(&mut out[date + 4..date + 6], u64::from(t.minute));
    out[date + 6] = b':';
    put_digits(&mut out[date + 7..date + 9], u64::from(t.second));
    let mut i = date + 9;
    if digits > 0 {
        out[i] = b'.';
        put_digits(
            &mut out[i + 1..i + 1 + usize::from(digits)],
            t.picos / unit,
        );
        i += 1 + usize::from(digits);
    }
    if z {
        out[i] = b'Z';
    }
    Some(len)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Makes a time of the calendar, for the tests.
    ///
    /// # Arguments
    ///
    /// * `year`  - The year.
    /// * `month` - The month, from 1.
    /// * `day`   - The day of the month, from 1.
    /// * `hms`   - The hour, minute and second.
    /// * `picos` - The picoseconds into the second.
    ///
    /// # Returns
    ///
    /// The time.
    fn at(
        year: u16,
        month: u8,
        day: u8,
        hms: (u8, u8, u8),
        picos: u64,
    ) -> CalendarTime {
        CalendarTime {
            year,
            month,
            day,
            hour: hms.0,
            minute: hms.1,
            second: hms.2,
            picos,
        }
    }

    #[test]
    fn days_count_through_the_calendar() {
        assert_eq!(days_from_civil(1970, 1, 1), 0);
        assert_eq!(days_from_civil(1958, 1, 1), -4383);
        assert_eq!(days_from_civil(2000, 3, 1), 11017);
        for d in [-719_162, -4383, 0, 11016, 11017, 2_932_896] {
            let (y, m, dd) = civil_from_days(d).unwrap();
            assert_eq!(days_from_civil(y, m, dd), d);
        }
        assert_eq!(civil_from_days(-719_163), None);
        assert_eq!(civil_from_days(2_932_897), None);
    }

    #[test]
    fn validity_follows_the_calendar() {
        assert!(at(2024, 2, 29, (0, 0, 0), 0).is_valid());
        assert!(!at(2023, 2, 29, (0, 0, 0), 0).is_valid());
        assert!(!at(1900, 2, 29, (0, 0, 0), 0).is_valid());
        assert!(at(2000, 2, 29, (0, 0, 0), 0).is_valid());
        assert!(at(2016, 12, 31, (23, 59, 60), 0).is_valid());
        assert!(!at(2016, 12, 31, (23, 58, 60), 0).is_valid());
        assert!(!at(2016, 12, 31, (0, 0, 0), PICOS).is_valid());
        assert_eq!(at(2024, 12, 31, (0, 0, 0), 0).day_of_year(), 366);
        assert_eq!(
            CalendarTime::from_day_of_year(2024, 60),
            Some(at(2024, 2, 29, (0, 0, 0), 0))
        );
        assert_eq!(CalendarTime::from_day_of_year(2023, 366), None);
    }

    #[test]
    fn rounding_carries_through_the_calendar() {
        let t = at(9998, 12, 31, (23, 59, 59), 999_999_999_999);
        assert_eq!(t.rounded(1), Some(t));
        assert_eq!(t.rounded(10), Some(at(9999, 1, 1, (0, 0, 0), 0)));
        let leap = at(2016, 12, 31, (23, 59, 60), 600_000_000_000);
        assert_eq!(leap.rounded(PICOS), Some(at(2017, 1, 1, (0, 0, 0), 0)));
        let last = at(9999, 12, 31, (23, 59, 59), 500_000_000_000);
        assert_eq!(last.rounded(PICOS), None);
    }

    #[test]
    fn ccs_round_trip() {
        // P-field 0x52: month and day, two octets of hundredths and
        // ten-thousandths of a second.
        let c = Ccs::new(false, 2).unwrap();
        let t = at(2026, 11, 1, (12, 34, 56), 789_000_000_000);
        let mut b = [0u8; 10];
        assert_eq!(c.encode(&t, &mut b), Some(10));
        assert_eq!(
            b,
            [0x52, 0x20, 0x26, 0x11, 0x01, 0x12, 0x34, 0x56, 0x78, 0x90]
        );
        assert_eq!(decode_ccs(&b), Some((10, t)));
        // As a day of the year, 305.
        let c = Ccs::new(true, 0).unwrap();
        let mut b = [0u8; 8];
        c.encode(&t, &mut b);
        assert_eq!(b, [0x58, 0x20, 0x26, 0x03, 0x05, 0x12, 0x34, 0x57]);
        // A nibble that is not a digit, or a month of 13, is refused.
        assert_eq!(
            decode_ccs(&[0x58, 0x20, 0x2a, 3, 5, 0x12, 0x34, 0x57]),
            None
        );
        assert_eq!(decode_ccs(&[0x50, 0x20, 0x26, 0x13, 1, 0, 0, 0]), None);
    }

    #[test]
    fn ascii_round_trip() {
        let t = at(2016, 12, 31, (23, 59, 60), 250_000_000_000);
        let mut b = [0u8; 40];
        let n = format_ascii(&t, AsciiForm::A, 3, true, &mut b).unwrap();
        assert_eq!(&b[..n], b"2016-12-31T23:59:60.250Z");
        assert_eq!(parse_ascii(&b[..n]), Some((n, AsciiForm::A, t)));
        let n = format_ascii(&t, AsciiForm::B, 0, false, &mut b).unwrap();
        assert_eq!(&b[..n], b"2016-366T23:59:60");
        assert_eq!(
            parse_ascii(b"2016-366T23:59:60 rest"),
            Some((17, AsciiForm::B, CalendarTime { picos: 0, ..t }))
        );
        assert_eq!(parse_ascii(b"2016-12-31T23:59:61"), None);
        assert_eq!(parse_ascii(b"2016-12-31T23:59:59."), None);
        assert_eq!(parse_ascii(b"2016-12-31T23:59:59.1234567890123"), None);
    }
}
