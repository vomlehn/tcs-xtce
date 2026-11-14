//! The runtime for the packet codecs that tcs-xtce generates from a mission's
//! OMG XTCE (XML Telemetric and Command Exchange) document: flight software,
//! run aboard the vehicle and on the ground alike.
//!
//! Generated code packs and unpacks its packets through [`packet`], into the
//! managed, preallocated buffers of [`buffer`]; code generated with
//! `--dynamic-allocations false` holds its arrays, strings and binary fields
//! in the fixed-capacity types of [`fixed`], and so never allocates.
//!
//! Two programs that exchange packets check that they were built from the
//! same XTCE by comparing the [`digest`] of it, which reads only as much XML
//! as that takes, and computes the same digest in every language.
//!
//! This crate is only what generated code and that check run on. Reading and
//! writing XTCE documents in full, and the compiler, are tcs-xtce's, which
//! are software tools: see the `tcs-xtce` crate.

pub mod buffer;
pub mod digest;
pub mod error;
pub mod fixed;
pub mod packet;
pub mod value;

pub use buffer::{Buffer, DEFAULT_CAPACITY};
pub use error::{Error, Result};
pub use packet::{Codec, Packet, PacketError};
pub use value::Value;
