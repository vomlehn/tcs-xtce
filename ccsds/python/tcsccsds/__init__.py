"""CCSDS standards for flight software: part of tcs-xtce, with the Rust
crate tcsccsds and the C/C++ header ccsds/include/tcsccsds-time.h, which give
the same results.

:mod:`tcsccsds.time` reads and writes the binary time codes of CCSDS
301.0-B-4, *Time Code Formats*: CUC (unsegmented) and CDS (day segmented).
:mod:`tcsccsds.calendar` reads and writes its calendar codes, CCS and the
ASCII codes A and B, and :mod:`tcsccsds.utc` converts between TAI and UTC
with a table of leap seconds.

Warning: this is an alpha release.  It is not fully tested, it is not
feature complete, and it is subject to rapid change.  Until 1.0, any change
to its API changes the minor version number (0.1 to 0.2), so a release that
changes only the patch number never breaks code built against it.  Feedback
is welcome, even at this stage: open an issue at
https://github.com/vomlehn/tcs-xtce/issues.
"""

from . import calendar, time, utc  # noqa: F401
