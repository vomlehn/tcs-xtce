===============
tcs-xtce Design
===============

How tcs-xtce works inside, and why. Each of its two programs has a design
document of its own, as it has a manual of its own:

`The tcs-xtce design <tcs-xtce-design.html>`__
    The code generator and its runtimes: the XML binding, managed buffers,
    the packet codecs and how they are generated, integers of any size, the
    CCSDS time codes of tcsccsds, ``xtce-digest``, the testing strategy,
    and the limitations and the work still to do.

`The tcs-xtcee design <tcs-xtcee-design.html>`__
    The XTCE editor: how it holds a document to its version's schema and
    keeps the rest of the file as it was written.

`tcs-xtce in Rust <rust-migration-design.html>`__
    Proposed: moving the compiler, its emitters, the binding generator
    and the Python, C and C++ runtimes and bindings to Rust, with
    interfaces to it in each language, and proving each step by
    identical output.

`The run-time codec design <runtime-codec-design.html>`__
    Proposed: a codec that reads XTCE while a program runs, resolves it
    as the compiler does, and packs and unpacks its containers and
    meta-commands with dynamic values, for tools such as expectb that
    are given their XTCE when they run.

How to use each is in its manual: see `the tcs-xtce manuals
<manual.html>`__.
