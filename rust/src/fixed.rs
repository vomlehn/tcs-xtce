//! Fixed-capacity containers for packet codecs that never allocate.
//!
//! `tcs-xtce --dynamic-allocations false` generates packet structs whose
//! arrays, strings and binary fields are these types instead of `Vec` and
//! `String`.  Each holds its elements inline, up to a capacity that the
//! mission database gives, so decoding, encoding and describing a packet
//! never touch the heap.

use std::fmt;
use std::ops::{Deref, DerefMut};

/// The value does not fit in the capacity of a fixed-capacity container.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CapacityError;

impl fmt::Display for CapacityError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("value exceeds the container's capacity")
    }
}

impl std::error::Error for CapacityError {}

/// Up to `N` values of `T`, stored inline.  Dereferences to the slice of
/// the values it holds.
pub struct FixedVec<T, const N: usize> {
    len: usize,
    data: [T; N],
}

/// Up to `N` bytes, stored inline.
pub type FixedBytes<const N: usize> = FixedVec<u8, N>;

impl<T: Default, const N: usize> FixedVec<T, N> {
    pub fn new() -> Self {
        Self {
            len: 0,
            data: std::array::from_fn(|_| T::default()),
        }
    }
}

impl<T, const N: usize> FixedVec<T, N> {
    pub const CAPACITY: usize = N;

    /// Appends `v`, or gives it back if the vector is full.
    pub fn push(&mut self, v: T) -> Result<(), T> {
        if self.len == N {
            return Err(v);
        }
        self.data[self.len] = v;
        self.len += 1;
        Ok(())
    }

    /// Removes every value after the first `len`.
    pub fn truncate(&mut self, len: usize) {
        self.len = self.len.min(len);
    }

    pub fn clear(&mut self) {
        self.len = 0;
    }

    pub fn as_slice(&self) -> &[T] {
        &self.data[..self.len]
    }

    pub fn as_mut_slice(&mut self) -> &mut [T] {
        &mut self.data[..self.len]
    }
}

impl<T: Clone, const N: usize> FixedVec<T, N> {
    /// Appends a copy of each of `values`; on failure the vector is unchanged.
    pub fn extend_from_slice(
        &mut self,
        values: &[T],
    ) -> Result<(), CapacityError> {
        if values.len() > N - self.len {
            return Err(CapacityError);
        }
        self.data[self.len..self.len + values.len()].clone_from_slice(values);
        self.len += values.len();
        Ok(())
    }
}

impl<T: Default, const N: usize> Default for FixedVec<T, N> {
    fn default() -> Self {
        Self::new()
    }
}

impl<T: Clone, const N: usize> Clone for FixedVec<T, N> {
    fn clone(&self) -> Self {
        Self {
            len: self.len,
            data: self.data.clone(),
        }
    }
}

impl<T: Default + Clone, const N: usize> TryFrom<&[T]> for FixedVec<T, N> {
    type Error = CapacityError;

    fn try_from(values: &[T]) -> Result<Self, CapacityError> {
        let mut v = Self::new();
        v.extend_from_slice(values)?;
        Ok(v)
    }
}

impl<T, const N: usize> Deref for FixedVec<T, N> {
    type Target = [T];

    fn deref(&self) -> &[T] {
        self.as_slice()
    }
}

impl<T, const N: usize> DerefMut for FixedVec<T, N> {
    fn deref_mut(&mut self) -> &mut [T] {
        self.as_mut_slice()
    }
}

impl<T: PartialEq, const N: usize, const M: usize> PartialEq<FixedVec<T, M>>
    for FixedVec<T, N>
{
    fn eq(&self, other: &FixedVec<T, M>) -> bool {
        self.as_slice() == other.as_slice()
    }
}

impl<T: Eq, const N: usize> Eq for FixedVec<T, N> {}

impl<T: fmt::Debug, const N: usize> fmt::Debug for FixedVec<T, N> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_list().entries(self.as_slice()).finish()
    }
}

/// A UTF-8 string of up to `N` bytes, stored inline.  Dereferences to
/// `str`.
#[derive(Clone, Default)]
pub struct FixedString<const N: usize> {
    bytes: FixedBytes<N>,
}

impl<const N: usize> FixedString<N> {
    pub const CAPACITY: usize = N;

    pub fn new() -> Self {
        Self::default()
    }

    /// The string whose UTF-8 encoding is `bytes`.
    pub fn from_utf8(
        bytes: FixedBytes<N>,
    ) -> Result<Self, std::str::Utf8Error> {
        std::str::from_utf8(&bytes)?;
        Ok(Self { bytes })
    }

    pub fn as_str(&self) -> &str {
        // Every way of building one checks that its bytes are UTF-8.
        std::str::from_utf8(&self.bytes).unwrap_or_default()
    }

    /// Appends `s`; on failure the string is unchanged.
    pub fn push_str(&mut self, s: &str) -> Result<(), CapacityError> {
        self.bytes.extend_from_slice(s.as_bytes())
    }

    pub fn clear(&mut self) {
        self.bytes.clear();
    }
}

impl<const N: usize> TryFrom<&str> for FixedString<N> {
    type Error = CapacityError;

    fn try_from(s: &str) -> Result<Self, CapacityError> {
        let mut out = Self::new();
        out.push_str(s)?;
        Ok(out)
    }
}

impl<const N: usize> Deref for FixedString<N> {
    type Target = str;

    fn deref(&self) -> &str {
        self.as_str()
    }
}

impl<const N: usize> fmt::Write for FixedString<N> {
    fn write_str(&mut self, s: &str) -> fmt::Result {
        self.push_str(s).map_err(|_| fmt::Error)
    }
}

impl<const N: usize, const M: usize> PartialEq<FixedString<M>>
    for FixedString<N>
{
    fn eq(&self, other: &FixedString<M>) -> bool {
        self.as_str() == other.as_str()
    }
}

impl<const N: usize> Eq for FixedString<N> {}

impl<const N: usize> PartialEq<str> for FixedString<N> {
    fn eq(&self, other: &str) -> bool {
        self.as_str() == other
    }
}

impl<const N: usize> PartialEq<&str> for FixedString<N> {
    fn eq(&self, other: &&str) -> bool {
        self.as_str() == *other
    }
}

impl<const N: usize, const M: usize> PartialOrd<FixedString<M>>
    for FixedString<N>
{
    fn partial_cmp(
        &self,
        other: &FixedString<M>,
    ) -> Option<std::cmp::Ordering> {
        self.as_str().partial_cmp(other.as_str())
    }
}

impl<const N: usize> PartialOrd<&str> for FixedString<N> {
    fn partial_cmp(&self, other: &&str) -> Option<std::cmp::Ordering> {
        self.as_str().partial_cmp(*other)
    }
}

impl<const N: usize> fmt::Debug for FixedString<N> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        fmt::Debug::fmt(self.as_str(), f)
    }
}

impl<const N: usize> fmt::Display for FixedString<N> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn vec_stops_at_capacity() {
        let mut v = FixedVec::<u16, 2>::new();
        assert_eq!(v.push(1), Ok(()));
        assert_eq!(v.push(2), Ok(()));
        assert_eq!(v.push(3), Err(3));
        assert_eq!(&v[..], &[1, 2]);
        assert_eq!(v.extend_from_slice(&[4]), Err(CapacityError));
        v.truncate(1);
        assert_eq!(v, FixedVec::<u16, 5>::try_from(&[1u16][..]).unwrap());
    }

    #[test]
    fn strings_compare_by_content() {
        let s = FixedString::<8>::try_from("héllo").unwrap();
        assert_eq!(s, "héllo");
        assert_eq!(s.len(), 6);
        assert_eq!(s, FixedString::<16>::try_from("héllo").unwrap());
        assert!(FixedString::<4>::try_from("too long").is_err());
        let bad = FixedBytes::<2>::try_from(&[0xff, 0xfe][..]).unwrap();
        assert!(FixedString::from_utf8(bad).is_err());
    }
}
