//! CCSDS standards for flight software: part of tcs-xtce, with matching C,
//! C++ (`ccsds/include/ccsds-time.h`) and Python (`tcs_ccsds`) libraries
//! that give the same results.  It allocates nothing and needs nothing but
//! `core`.
//!
//! [`time`] reads and writes the binary time codes of CCSDS 301.0-B-4,
//! *Time Code Formats*: CUC (unsegmented) and CDS (day segmented).  Their
//! decoding and encoding functions have the shapes that tcs-xtce's time
//! transform hooks take, so a packet codec generated from a mission's XTCE
//! document can use them directly.
//!
//! **Warning:** this is an alpha release. It is not fully tested, it is not
//! feature complete, and it is subject to rapid change. Until 1.0, any
//! change to its API changes the minor version number (0.1 to 0.2), so a
//! release that changes only the patch number never breaks code built
//! against it. Feedback is welcome, even at this stage: open an issue at
//! <https://github.com/vomlehn/tcs-xtce/issues>.

//!
//! [`calendar`] reads and writes the calendar time codes of the same
//! standard: CCS (calendar segmented) and the ASCII codes A and B, as dates
//! and times held exactly. [`utc`] converts between TAI, which the binary
//! codes count, and UTC, which the calendar codes give, with a table of leap
//! seconds.

#![no_std]
// Each argument's description in a doc comment, continuation lines and all,
// starts in one column, as the TCS projects' documentation rule has it, which
// is deeper than this lint asks of a list item's continuation.
#![allow(clippy::doc_overindented_list_items)]

pub mod calendar;
pub mod time;
pub mod utc;
