//! Serialization and deserialization of OMG XTCE (XML Telemetric and Command
//! Exchange) documents: part of tcs-xtce, which is ground software.
//!
//! The data structures in [`generated`] are produced from the XTCE schema
//! (`references/SpaceSystem.xsd`) by the generator in `generator/`; do not
//! edit them by hand.  Serialization writes into managed [`Buffer`]s that are
//! preallocated and reused for as long as their owner (usually a
//! [`Document`]) is alive.
//!
//! The runtime for the packet codecs that `tcs-xtce` generates from a
//! mission's XTCE document is the `tcsxtce` crate, which flies, as does the
//! digest; its modules [`buffer`], [`digest`], [`fixed`], [`packet`] and
//! [`value`] are re-exported here.
//!
//! ```
//! use tcs_xtce::{Document, SpaceSystemType};
//!
//! let mut doc = Document::new(SpaceSystemType {
//!     name: "Demo".into(),
//!     ..Default::default()
//! });
//! let xml = doc.serialize().unwrap().to_vec();
//! let back = Document::from_bytes(&xml).unwrap();
//! assert_eq!(back.root, doc.root);
//! ```

pub mod document;
pub mod generated;
pub mod xml;

pub use tcsxtce::{buffer, digest, fixed, packet, value};

pub use buffer::{Buffer, DEFAULT_CAPACITY};
pub use document::{from_xml, to_xml, Document};
pub use generated::*;
pub use packet::{Codec, Packet, PacketError};
pub use value::Value;
pub use xml::{Error, Reader, Result, Writer, XmlElement};
