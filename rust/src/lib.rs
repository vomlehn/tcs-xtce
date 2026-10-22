//! Serialization and deserialization of OMG XTCE (XML Telemetric and Command
//! Exchange) documents: part of tcs-xtce, which is flight software.
//!
//! The data structures in [`generated`] are produced from the XTCE schema
//! (`references/SpaceSystem.xsd`) by the generator in `generator/`; do not
//! edit them by hand.  Serialization writes into managed [`Buffer`]s that are
//! preallocated and reused for as long as their owner (usually a
//! [`Document`]) is alive.
//!
//! The [`packet`] module is the runtime for packet codecs that `tcs-xtce`
//! generates from a mission's XTCE document.
//!
//! ```
//! use xtce::{Document, SpaceSystemType};
//!
//! let mut doc = Document::new(SpaceSystemType {
//!     name: "Demo".into(),
//!     ..Default::default()
//! });
//! let xml = doc.serialize().unwrap().to_vec();
//! let back = Document::from_bytes(&xml).unwrap();
//! assert_eq!(back.root, doc.root);
//! ```

pub mod buffer;
pub mod document;
pub mod generated;
pub mod packet;
pub mod value;
pub mod xml;

pub use buffer::{Buffer, DEFAULT_CAPACITY};
pub use document::{from_xml, to_xml, Document};
pub use generated::*;
pub use packet::{Codec, Packet, PacketError};
pub use value::Value;
pub use xml::{Error, Reader, Result, Writer, XmlElement};
