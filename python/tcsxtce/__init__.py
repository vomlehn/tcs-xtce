"""The runtime for the packet codecs that tcs-xtce generates from a mission's
OMG XTCE document: flight software, run aboard the vehicle and on the ground
alike.

Generated code packs and unpacks its packets through :mod:`tcsxtce.packet`,
in the managed, preallocated buffers of :class:`ManagedBuffer`. It mirrors
the ``tcsxtce`` crate in Rust and ``tcsxtce-packet.h`` in C and C++.

This package is only what generated code runs on, and needs nothing but the
standard library. Reading and writing XTCE documents, the compiler and the
digest are tcs-xtce's, which is ground software: see :mod:`tcs_xtce`.
"""

from ._buffer import DEFAULT_CAPACITY, ManagedBuffer

__all__ = ["DEFAULT_CAPACITY", "ManagedBuffer"]
