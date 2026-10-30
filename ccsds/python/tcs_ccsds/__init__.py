"""CCSDS standards for flight software: part of tcs-xtce, with the Rust
crate tcs-ccsds and the C/C++ header ccsds/include/ccsds-time.h, which give
the same results.

:mod:`tcs_ccsds.time` reads and writes the binary time codes of CCSDS
301.0-B-4, *Time Code Formats*: CUC (unsegmented) and CDS (day segmented).
"""

from . import time  # noqa: F401
