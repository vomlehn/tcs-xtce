//! CCSDS standards for flight software: part of tcs-xtce, with matching C,
//! C++ (`ccsds/include/ccsds`) and Python (`tcs_ccsds`) libraries that give
//! the same results.  It allocates nothing and needs nothing but `core`.
//!
//! [`time`] reads and writes the binary time codes of CCSDS 301.0-B-4,
//! *Time Code Formats*: CUC (unsegmented) and CDS (day segmented).  Their
//! decoding and encoding functions have the shapes that tcs-xtce's time
//! transform hooks take, so a packet codec generated from a mission's XTCE
//! document can use them directly.

#![no_std]

pub mod time;
