//! The runtime for the packet codecs that tcs-xtce generates from a mission's
//! OMG XTCE (XML Telemetric and Command Exchange) document: flight software,
//! run aboard the vehicle and on the ground alike.
//!
//! Generated code packs and unpacks its packets through [`packet`], into the
//! managed, preallocated buffers of [`buffer`]; code generated with
//! `--dynamic-allocations false` holds its arrays, strings and binary fields
//! in the fixed-capacity types of [`fixed`], and so never allocates.
//!
//! This crate is only what generated code runs on. Reading and writing XTCE
//! documents, the compiler and the digest are tcs-xtce's, which is ground
//! software: see the `tcs-xtce` crate.

pub mod buffer;
pub mod fixed;
pub mod packet;
pub mod value;

pub use buffer::{Buffer, DEFAULT_CAPACITY};
pub use packet::{Codec, Packet, PacketError};
pub use value::Value;
