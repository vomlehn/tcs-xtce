"""CCSDS standards for flight software: part of tcs-xtce, with the Rust
crate tcs-ccsds and the C/C++ header ccsds/include/ccsds-time.h, which give
the same results.

:mod:`tcs_ccsds.time` reads and writes the binary time codes of CCSDS
301.0-B-4, *Time Code Formats*: CUC (unsegmented) and CDS (day segmented).

Warning: this is very early software and is subject to rapid change.  Until
1.0, any change to its API changes the minor version number (0.1 to 0.2), so
a release that changes only the patch number never breaks code built against
it.  Feedback is welcome, even at this stage: open an issue at
https://github.com/vomlehn/tcs-xtce/issues.
"""

from . import time  # noqa: F401
