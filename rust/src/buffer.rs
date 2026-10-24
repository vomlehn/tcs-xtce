//! Managed byte buffers.
//!
//! A [`Buffer`] is preallocated when it is created and keeps its allocation
//! across uses: clearing it only resets the length.  Buffers are owned by the
//! data structures that use them (for example [`crate::Document`]), so the
//! memory lives exactly as long as those structures and is released when they
//! are dropped.

/// Capacity used when no explicit capacity is requested.
pub const DEFAULT_CAPACITY: usize = 64 * 1024;

/// A growable byte buffer that retains its allocation between uses.
#[derive(Debug, Clone)]
pub struct Buffer {
    data: Vec<u8>,
}

impl Default for Buffer {
    fn default() -> Self {
        Self::new()
    }
}

impl Buffer {
    /// Creates a buffer preallocated with [`DEFAULT_CAPACITY`] bytes.
    pub fn new() -> Self {
        Self::with_capacity(DEFAULT_CAPACITY)
    }

    /// Creates a buffer preallocated with `capacity` bytes.
    pub fn with_capacity(capacity: usize) -> Self {
        Self {
            data: Vec::with_capacity(capacity),
        }
    }

    /// Discards the contents but keeps the allocation.
    pub fn clear(&mut self) {
        self.data.clear();
    }

    /// Ensures room for at least `additional` more bytes.
    pub fn reserve(&mut self, additional: usize) {
        self.data.reserve(additional);
    }

    /// Replaces the contents with `bytes`, reusing the allocation if it fits.
    pub fn load(&mut self, bytes: &[u8]) {
        self.data.clear();
        self.data.extend_from_slice(bytes);
    }

    /// Fills the buffer from a reader, reusing the allocation.
    pub fn load_from<R: std::io::Read>(
        &mut self,
        mut reader: R,
    ) -> std::io::Result<usize> {
        self.data.clear();
        reader.read_to_end(&mut self.data)
    }

    pub fn push(&mut self, byte: u8) {
        self.data.push(byte);
    }

    pub fn extend_from_slice(&mut self, bytes: &[u8]) {
        self.data.extend_from_slice(bytes);
    }

    pub fn push_str(&mut self, s: &str) {
        self.data.extend_from_slice(s.as_bytes());
    }

    /// Sets the length to `len`, zero filling any new bytes.
    pub fn resize(&mut self, len: usize) {
        self.data.resize(len, 0);
    }

    pub fn as_mut_bytes(&mut self) -> &mut [u8] {
        &mut self.data
    }

    pub fn as_bytes(&self) -> &[u8] {
        &self.data
    }

    pub fn len(&self) -> usize {
        self.data.len()
    }

    pub fn is_empty(&self) -> bool {
        self.data.is_empty()
    }

    /// Bytes currently allocated (always at least [`Buffer::len`]).
    pub fn capacity(&self) -> usize {
        self.data.capacity()
    }
}

impl AsRef<[u8]> for Buffer {
    fn as_ref(&self) -> &[u8] {
        &self.data
    }
}
