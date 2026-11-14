=======================
tcs-xtce Manuals
=======================

.. note::

   This is an alpha release of tcs-xtce. It is not fully tested and not
   feature complete. Comments are very welcome, as are people interested in
   collaborating on it. Please open an issue or a pull request at
   https://github.com/vomlehn/tcs-xtce; the contributing guide,
   https://github.com/vomlehn/tcs-xtce/blob/main/CONTRIBUTING.md, says
   how.

**XTCE** (XML Telemetric and Command Exchange) is a standard for
describing the telemetry and command messages that a spacecraft,
instrument or ground system exchanges, published by the Object Management
Group (OMG), at https://www.omg.org/spec/XTCE/, and by CCSDS, as
CCSDS 660.0-B-2. An XTCE document is a mission database: each message's
parameters, their types, units and encodings, and how they are laid out
in packets.

tcs-xtce works with those documents. It has two programs, each with a
manual of its own:

`The tcs-xtce manual <tcs-xtce.html>`__
    The code generator. From a mission's XTCE document, ``tcs-xtce``
    generates data structures for every packet, with methods that encode
    them to the bytes on the wire and decode them again, in C/C++, Rust
    and Python 3, all writing the same bit-packed bytes. Its code can be
    written without dynamic memory allocation, for flight software. The
    manual also covers installing tcs-xtce, its runtimes, the integers of
    any size, the CCSDS time codes of tcsccsds, ``xtce-digest``, which
    tells whether two programs use the same document, the examples and the
    tests.

`The tcs-xtcee manual <tcs-xtcee.html>`__
    The XTCE editor. ``tcs-xtcee`` is a graphical editor for XTCE 1.1, 1.2
    and 1.3 documents, holding each to the schema OMG published for its
    version and changing a file only where it is edited. It converts
    documents between versions, and shows a packet's layout and encodes
    and decodes packets with the code generator.

`The tcs-xtce design documents <design.html>`__
    How the code generator, its runtimes and the editor work inside, and
    why: each program has a design document of its own.

tcs-xtce is licensed under either of the Apache License, Version 2.0 or
the MIT license, at your option; the manuals say more.
