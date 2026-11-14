//! The error the digest and tcs-xtce's XML reader and writer give: XTCE that
//! cannot be read or written, and where.

use std::fmt;

/// Error raised while reading or writing XML.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Error {
    /// What is wrong.
    pub message: String,
    /// 1-based line in the input, or 0 when not tied to an input position.
    pub line: usize,
    /// 1-based column in the input, or 0 when not tied to an input position.
    pub column: usize,
}

impl Error {
    /// Makes an error not tied to a position in the input.
    ///
    /// # Arguments
    ///
    /// * `message` - What is wrong.
    ///
    /// # Returns
    ///
    /// The error, with line and column 0.
    pub fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            line: 0,
            column: 0,
        }
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        if self.line > 0 {
            write!(f, "{}:{}: {}", self.line, self.column, self.message)
        } else {
            f.write_str(&self.message)
        }
    }
}

impl std::error::Error for Error {}

/// What reading or writing XTCE gives: its value, or the [`Error`] that
/// stopped it.
pub type Result<T> = std::result::Result<T, Error>;
