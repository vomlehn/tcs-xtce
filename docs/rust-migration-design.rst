=========================
tcs-xtce in Rust: Design
=========================

Status: proposed, for review. Nothing here is implemented yet.

.. contents:: Contents
   :depth: 2
   :local:

What changes
============

Today most of tcs-xtce is Python: the compiler, its emitters, the
generator of the XML binding, the Python XML binding and the Python
packet runtime. The C packet runtime and the C/C++ XML binding are
written separately again, in C and C++. This design moves all of it to
Rust, so that each piece is written once, in Rust, and Python, C and
C++ reach it through interfaces:

==============================  ======================  ========================
Piece                           Today                   After
==============================  ======================  ========================
Compiler: analysis and model    Python, ``compiler/``   Rust crate, shared
Emitters (Rust, C/C++, Python)  Python, ``compiler/``   Rust, in the compiler
Binding generator (XSD)         Python, ``binding/``    Rust
``xtce-digest``, ``tcs-xtce``   Python                  Rust programs
Python runtime ``tcsxtce``      pure Python             Python interface
Python binding ``tcs_xtce``     generated Python        Python interface
C runtime ``tcsxtce-*.h``       header-only C           C interface
C/C++ binding ``libtcs-xtce``   generated C++           C and C++ interfaces
Run-time codec                  (new)                   Rust, on the model
==============================  ======================  ========================

Two things motivate it:

- **One analysis.** The run-time codec needs the compiler's analysis
  in Rust. With the compiler in Rust too, there is one analysis and one
  model, not a Python one and a Rust one kept in step by tests.
- **One language to maintain.** The editor is already Rust, and the
  Rust runtime is already the reference the other languages are checked
  against.

What doesn't change:

- **What is generated.** The Rust compiler generates the same code, in
  every language, byte for byte, as the Python compiler does, and the
  Rust binding generator the same bindings. A mission's generated code
  doesn't change when it is rebuilt with the new compiler.
- **The Python APIs.** ``import tcsxtce`` and ``import tcs_xtce`` give
  the same classes and functions, with the same behaviour, so
  generated Python code and programs that use the binding run
  unchanged.
- **The C and C++ APIs.** ``tcsxtce-packet.h``, ``tcsxtce-bnum.h``,
  ``tcsxtce-digest.h``, ``tcs-xtce.h`` and the ``tcs-xtce-*.hpp``
  headers declare the same functions, types and classes, with the same
  behaviour, so generated C and C++ and programs that use the binding
  compile unchanged. What changes is how they are built: see `C and
  C++`_.
- **The digest.** Every language computes the same digest as before.

Goals
=====

1. Every piece above is written in Rust, and Python, C and C++ use it
   through interfaces, not through ports.
2. The change is invisible in what tcs-xtce produces: the generated
   code, the bindings, the digests and the packets are the same.
3. Each step is proved before the next: a Rust piece replaces a Python
   one only when it gives identical results on everything the tests
   cover.
4. The Python packages may now depend on the Rust runtime. They no
   longer need only the standard library.
5. C and C++ programs, flight software among them, link a library
   built from the Rust. They no longer need only a C compiler and the
   headers.

The crates
==========

The Rust workspace in ``rust/`` grows from two crates to seven:

``tcsxtce``
    The packet runtime, as now: operational code, what flies.
``tcs-xtce``
    The XML binding and ``Document``, as now.
``tcs-xtce-model``
    New: the compiler's analysis, from the binding's tree to the
    resolved model, as ``compile.py`` and ``model.py`` do now.
``tcs-xtce-compiler``
    New: the emitters, the size report and the ``tcs-xtce`` program,
    which reads XTCE and writes code, as ``python -m tcs_xtce`` does now.
``tcs-xtce-dynamic``
    New: the run-time codec, on the model; see `the run-time codec
    design <runtime-codec-design.html>`__.
``tcsxtce-c``
    New: the C interface to ``tcsxtce``, built as the static library
    ``libtcsxtce.a``: operational code, what flies in C flight software.
``tcs-xtce-c``
    New: the C interface to ``tcs-xtce``, built as ``libtcs-xtce.a`` and
    ``libtcs-xtce.so``, which the C++ classes are built on.

Each depends only on those above it, except that the two C interfaces
depend only on the crate each one wraps. ``tcs-xtce-model`` is the piece
everything new rests on, and the one ported first.

The Python interfaces are built with PyO3, as extension modules
compiled from Rust:

``tcsxtce`` (Python)
    The packet runtime's API, as the pure-Python ``tcsxtce`` has it now,
    implemented by the Rust runtime.
``tcs_xtce`` (Python)
    The XML binding's classes and ``Document``, implemented by the Rust
    binding; and the compiler, so that ``python -m tcs_xtce`` and the
    ``tcs-xtce`` command keep working; and the run-time codec.

PyO3 rather than ``ctypes``: generated Python calls the runtime once or
more for every field of every packet, and a call through ``ctypes``
costs far more than one into a PyO3 module. With the packages no longer
limited to the standard library, there is no reason left for
``ctypes``. The run-time codec's Python interface is built the same
way, which supersedes the ``ctypes`` interface its design proposed.

The order of work
=================

Each step ends with the Python piece it replaces still in place, and
the two compared. A Python piece is removed only at the end.

1. **The model.** Port the analysis to ``tcs-xtce-model``, stage by
   stage. The Python compiler gains ``--dump-model``, which writes the
   resolved model in a canonical text form, and the Rust model writes
   the same form. The two must be identical for the demo mission, the
   examples and the schema-coverage samples.
2. **The emitters.** Port the three emitters and the size report to
   ``tcs-xtce-compiler``. Its output must be byte-identical to the
   Python compiler's for every mission the tests compile, in every
   language and with ``--dynamic-allocations`` both ways.
3. **The run-time codec,** on the model, tested against the generated
   codecs; see its design.
4. **The binding generator.** Port ``binding/`` -- reading the XSD,
   binding its types, emitting Rust, C++ and Python, the samples and
   the documentation -- to a Rust program. Its output must be
   byte-identical to today's, for the XSD versions the project builds;
   the C++ it emits changes only in step 8.
5. **The Python runtime.** A PyO3 module with the pure-Python
   ``tcsxtce``'s API, on the Rust runtime. Every Python test, and every
   cross-language check with generated Python, must pass unchanged.
6. **The Python XML binding.** A PyO3 module with the classes and
   ``Document`` of today's generated ``tcs_xtce``. The round-trip
   tests, and the byte-identical output across languages, must pass
   unchanged.
7. **The C runtime.** ``tcsxtce`` builds without the standard library,
   and ``tcsxtce-c`` gives ``libtcsxtce.a`` the functions the C headers
   declare. Every C and C++ test, every example and every
   cross-language check with generated C and C++ must pass unchanged,
   built against the library.
8. **The C/C++ XML binding.** ``tcs-xtce-c`` gives the functions of
   ``tcs-xtce.h``, and the C++ classes are rebuilt on them. The
   round-trip tests and ``c_api_test`` must pass unchanged.
9. **Retire the old code.** Remove the Python compiler, emitters,
   binding generator, runtime and binding, the C definitions in the
   runtime's headers, the C++ in ``cpp/src/``, and the comparisons that
   tested the Rust against them. The tests that remain test the Rust
   and its interfaces.

Steps 1 to 3 are what expectb needs. Steps 4 to 9 can follow at their
own pace, since nothing in them blocks the run-time codec.

The Python interfaces
=====================

- **The same names, the same behaviour.** Classes, functions,
  exceptions, defaults and error messages are what the pure-Python
  packages have now, so that existing code doesn't notice the change.
  ``PacketError`` stays a subclass of ``ValueError``, with the same
  fields.
- **Python's own types at the edges.** Integers of any size are Python
  ``int``\s, bytes are ``bytes`` or ``memoryview``\s, and the binding's
  records keep their attribute names and their ``Optional`` and
  ``list`` fields.
- **The XML binding's records.** Today they are dataclasses. As PyO3
  classes they keep their attributes, their constructors with keyword
  arguments and their equality, so code that builds or reads them
  doesn't change. Code that relies on their being dataclasses, through
  ``dataclasses.fields()`` or ``asdict()``, does notice; the
  interface provides both for these classes, so that it doesn't.
- **Wheels.** The packages become native, so they are built as wheels
  for each platform, with PyO3's stable ABI (``abi3``) from Python 3.10,
  so that one wheel for each platform serves every Python from 3.10 on.
  ``make python-dist`` builds them with maturin.

Packaging
=========

- **The ``tcs-xtce`` command** is a Rust program. The Python package
  installs it as well, as an entry point that calls the compiler in its
  extension module, so ``pip install`` still gives the command.
- **``make install-tools``** gains maturin and cbindgen, and loses
  nothing it needs for building C, C++ and Rust. The README's list of
  tools changes with it.
- **The C runtime's package** ships ``libtcsxtce.a`` beside the
  headers, built for each target it supports, and the C/C++ binding's
  package ships its libraries built from the Rust.
- **Versions.** Each crate and each Python package changes its minor
  version when its API changes, as now. The compiler's move doesn't
  change generated code, so it doesn't by itself change any runtime's
  API.

C and C++
=========

The C and C++ pieces become interfaces to the Rust, as the Python ones
do, so that every language runs the one implementation.

The C packet runtime
--------------------

- **The headers stay.** ``tcsxtce-packet.h``, ``tcsxtce-bnum.h`` and
  ``tcsxtce-digest.h`` keep every declaration they have -- functions,
  types, constants and macros -- so generated C and C++ compile
  unchanged and the emitters' output doesn't change.
- **The definitions move to Rust.** The functions the headers now
  define, ``static inline``, become declarations of functions with C
  linkage that ``tcsxtce-c`` defines, in ``libtcsxtce.a``. A type that
  C code lays out itself, such as a bit reader or writer, is
  ``#[repr(C)]`` in Rust, with the same layout. The headers are written
  by hand, not by cbindgen, so they stay as readable as they are, and a
  test checks them against what cbindgen would give.
- **Flight software links the library.** A C mission now links
  ``libtcsxtce.a``, built by Rust for its target, as well as including
  the headers. ``tcsxtce`` and ``tcsxtce-c`` build with ``#![no_std]``
  and without ``alloc``, so the library needs no operating system and
  allocates nothing; reading files, which tools use, moves behind a
  ``std`` feature, off by default for the C library.
- **Inlining.** The runtime's small helpers are no longer inlined into
  generated code by the C compiler alone. With clang, cross-language
  link-time optimization (``-flto`` with Rust's
  ``-Clinker-plugin-lto``) inlines them again; ``make`` builds the
  examples both ways, and the size report compares them.

The C/C++ XML binding
---------------------

- **The C API stays.** ``tcs-xtce.h`` keeps its functions, handles,
  status codes and buffer rules, defined now by ``tcs-xtce-c`` over the
  Rust ``Document``. ``libtcs-xtce.a`` and ``libtcs-xtce.so`` are built
  from it.
- **The C++ classes stay.** ``tcs-xtce-document.hpp`` and the other
  headers keep their classes and members. The binding generator emits
  them, and the C functions that fill them from the Rust tree and read
  them back, in place of the C++ parser and writer in ``cpp/src/``.
- **ABI.** The shared library's soname changes, from
  ``libtcs-xtce.so.1`` to ``libtcs-xtce.so.2``, since its definitions
  are new even where its declarations are the same.

Testing
=======

- **Byte-identical output** is the test for every port that generates
  something: the model dump, the emitters and the binding generator.
  Any difference, even in a comment, is a failure.
- **The existing tests stay.** ``tests/packet_check.sh``,
  ``cross_check.sh``, ``digest_check.sh``, the round-trip and sample
  tests, the C and C++ tests in ``cpp/tests/``, the examples and the
  Python tests all run against the new pieces unchanged.
- **Allocation.** The rule that the fixed codecs allocate nothing while
  decoding, encoding or describing a packet applies to generated code
  as now, in C linked with ``libtcsxtce.a`` too, which a build without
  ``alloc`` guarantees. The Python interfaces allocate as Python does.
- **Flight targets.** ``libtcsxtce.a`` is built and its tests run for
  each target the C runtime's package supports, under an emulator
  where the host can't run it.
- **The documentation's checks** -- function headers, line width and
  the HTML being current -- apply to the new Rust as to the old Python.

Settled in review
=================

- The compiler, its emitters and the binding generator move to Rust.
- The Python packet runtime and the Python XML binding become
  interfaces to the Rust, and may depend on it.
- The Python packages are native wheels with PyO3's ``abi3``, from
  Python 3.10.
- The C packet runtime and the C/C++ XML binding become interfaces to
  the Rust too, keeping their headers' declarations. C flight software
  links ``libtcsxtce.a``, built from the Rust without ``std`` or
  ``alloc``.

- Step 9 follows step 8 directly: the old code is removed once the Rust
  passes every test, without waiting for a version of the Rust pieces
  to have been in use.
- The processors Rust supports are enough for the C runtime: a mission
  that uses it is built for a Rust target.

Questions for review
====================

None remain.
