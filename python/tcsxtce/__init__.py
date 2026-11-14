"""The runtime for the packet codecs that tcs-xtce generates from a mission's
OMG XTCE document: flight software, run aboard the vehicle and on the ground
alike.

Generated code packs and unpacks its packets through :mod:`tcsxtce.packet`,
in the managed, preallocated buffers of :class:`ManagedBuffer`. It mirrors
the ``tcsxtce`` crate in Rust and ``tcsxtce-packet.h`` in C and C++.

Two programs that exchange packets check that they were built from the same
XTCE by comparing the digest of it, :mod:`tcsxtce.digest`, which reads only
as much XML as that takes, and computes the same digest in every language.

This package is only what generated code and that check run on, and needs
nothing but the standard library. Reading and writing XTCE documents in
full, and the compiler, are tcs-xtce's, which is ground software: see
:mod:`tcs_xtce`.
"""

from ._buffer import DEFAULT_CAPACITY, ManagedBuffer
from ._error import XtceError

__all__ = ["DEFAULT_CAPACITY", "ManagedBuffer", "XtceError"]
