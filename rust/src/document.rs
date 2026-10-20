//! XTCE documents bound to managed input and output buffers.

use crate::buffer::{Buffer, DEFAULT_CAPACITY};
use crate::generated::{SpaceSystemType, NAMESPACE, ROOT_ELEMENT};
use crate::xml::{Error, Reader, Result, Writer, XmlElement};

/// Serializes `value` as the element `tag` into `buf`, replacing its contents.
pub fn to_xml<T: XmlElement>(value: &T, tag: &str, buf: &mut Buffer, indent: bool) -> Result<()> {
    buf.clear();
    let mut w = Writer::new(buf, indent);
    w.declaration();
    w.set_root_namespace(NAMESPACE);
    value.write_xml(&mut w, tag)?;
    w.finish();
    Ok(())
}

/// Deserializes an element named `tag` from `bytes`.
pub fn from_xml<T: XmlElement>(bytes: &[u8], tag: &str) -> Result<T> {
    let mut r = Reader::new(bytes)?;
    let root = r.root()?;
    if root.local_name() != tag {
        return Err(Error::new(format!(
            "root element is <{}>, expected <{tag}>",
            root.name
        )));
    }
    let value = T::read_xml(&mut r, &root)?;
    r.finish()?;
    Ok(value)
}

/// An XTCE `SpaceSystem` together with the buffers used to (de)serialize it.
///
/// Both buffers are preallocated when the document is created and are reused
/// by every subsequent [`Document::load`] and [`Document::serialize`]; they are
/// released when the document is dropped.
#[derive(Debug, Clone)]
pub struct Document {
    /// The deserialized (or to-be-serialized) root `SpaceSystem`.
    pub root: SpaceSystemType,
    input: Buffer,
    output: Buffer,
    /// Pretty-print serialized output (the default).
    pub indent: bool,
}

impl Default for Document {
    fn default() -> Self {
        Self::new(SpaceSystemType::default())
    }
}

impl Document {
    /// Wraps `root`, preallocating [`DEFAULT_CAPACITY`] bytes per buffer.
    pub fn new(root: SpaceSystemType) -> Self {
        Self::with_capacity(root, DEFAULT_CAPACITY, DEFAULT_CAPACITY)
    }

    /// Wraps `root` with explicitly sized input and output buffers.
    pub fn with_capacity(root: SpaceSystemType, input: usize, output: usize) -> Self {
        Self {
            root,
            input: Buffer::with_capacity(input),
            output: Buffer::with_capacity(output),
            indent: true,
        }
    }

    /// Deserializes a document; the input buffer is sized to fit `bytes`.
    pub fn from_bytes(bytes: &[u8]) -> Result<Self> {
        let mut doc = Self::with_capacity(
            SpaceSystemType::default(),
            bytes.len().max(DEFAULT_CAPACITY),
            bytes.len().max(DEFAULT_CAPACITY),
        );
        doc.load(bytes)?;
        Ok(doc)
    }

    /// Replaces the root by deserializing `bytes`, reusing the input buffer.
    pub fn load(&mut self, bytes: &[u8]) -> Result<()> {
        self.input.load(bytes);
        self.parse_input()
    }

    /// Replaces the root by deserializing everything `reader` yields.
    pub fn read_from<R: std::io::Read>(&mut self, reader: R) -> Result<()> {
        self.input
            .load_from(reader)
            .map_err(|e| Error::new(format!("read failed: {e}")))?;
        self.parse_input()
    }

    fn parse_input(&mut self) -> Result<()> {
        self.root = from_xml(self.input.as_bytes(), ROOT_ELEMENT)?;
        Ok(())
    }

    /// Serializes the root into the output buffer and returns its bytes.
    /// The slice is valid until the document is next modified.
    pub fn serialize(&mut self) -> Result<&[u8]> {
        to_xml(&self.root, ROOT_ELEMENT, &mut self.output, self.indent)?;
        Ok(self.output.as_bytes())
    }

    /// Serializes the root and writes it to `writer`.
    pub fn write_to<W: std::io::Write>(&mut self, mut writer: W) -> Result<()> {
        let bytes = self.serialize()?;
        writer
            .write_all(bytes)
            .map_err(|e| Error::new(format!("write failed: {e}")))
    }

    /// The bytes most recently loaded.
    pub fn input(&self) -> &Buffer {
        &self.input
    }

    /// The bytes most recently serialized.
    pub fn output(&self) -> &Buffer {
        &self.output
    }
}
