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
   examples and the schema-coverage samples; ``make model-check``
   compares them, and ``make samples-check`` the public documents it
   downloads too. See `Porting the model`_ for the stages.
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
6. **The Python XML binding.** The extension module
   ``tcs_xtce._native``, from the crate ``tcs-xtce-python``, reads and
   writes XML through the Rust binding, and generated conversions turn
   what it reads into today's Python classes and back (see `The Python
   interfaces`_). Every Python test passed with it, and it and the
   Python reader and writer read the 90 documents here alike and wrote
   the same bytes. Then, ahead of step 9, the Python reader and writer
   were removed, so that Python's errors are Rust's: Python now reads
   and writes XTCE only through the Rust.
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

Steps 1 and 3 are what expectb needs, so step 3 comes before step 2:
the run-time codec needs only the model, and the emitters follow it.
Steps 4 to 9 can follow at their own pace, since nothing in them blocks
the run-time codec.

Porting the model
=================

The model is ported a stage at a time, each a part of what ``compile.py``
does, and each compared on every mission before the next is begun:

1. **Types** (done). Indexing the documents, resolving names, and each
   parameter and argument type: its encoding, calibrators, sizes, array
   dimensions, aggregate members, valid ranges and alarms. The Python
   compiler's ``--dump-types`` writes every type of the documents, each
   resolved alone, or the error it gives, and the warnings; the Rust
   model's ``model_dump --types`` writes the same. They agree on the 28
   missions here and the 67 public documents and missions that can be
   read.
2. **Containers** (done). Telemetry containers and their entries:
   locations, repeats, include conditions and other criteria, segments,
   indirect entries, included and repeated containers, length and
   checksum fields, transforms; the nodes and their inheritance. The
   Python compiler's ``--dump-containers`` writes every telemetry
   container as ``tm_node`` compiles it, before the mission is finished,
   and ``model_dump --containers`` the same; they agree on the same 28
   missions and 67 public ones.
3. **Commands** (done). Meta-commands, their arguments, argument
   assignments and command containers, with argument criteria, argument
   locations, sizes and lookups, fixed-value entries, and the containers
   and other meta-commands' CommandContainers they include or repeat;
   messages. The Python compiler's ``--dump-commands`` writes every
   meta-command and message as ``tc_node`` and ``msg_node`` compile them
   after the telemetry containers, and ``model_dump --commands`` the
   same; they agree on the same 28 missions and 67 public ones.

4. **The mission.** Streams, algorithms, alarms bound to each packet,
   hooks and kinds: the whole of ``--dump-model``.

The canonical text is the same for both, with two rules that let the Rust
hold the model as Rust holds data:

- Only records that the Python model compares by identity (its
  dataclasses with ``eq=False``: nodes, locals, hooks, criteria and the
  like) are numbered when they are reached more than once. Every other
  record is a value, written in full wherever it is reached: whether two
  Python values are one object is an accident of how they were copied,
  which the Rust need not repeat. A value reached again inside itself,
  as a repeated container's aggregate is through the value standing for
  it, is written there as its class name and ``^``.
- An element of the document that the model keeps until a packet binds
  it (a calibrator's ParameterInstanceRef, an alarm's ContextMatch) is
  written as ``xtce`` and its class name: what it says is the document's.

Errors are the Rust's from the first: where the Python's messages quote
Python's own machinery, the Rust says the same thing its own way. The one
case so far is a string alarm's ``matchPattern``, which the Rust parses
with the ``regex-syntax`` crate rather than Python's ``re``: the
patterns both accept the same way give the same automaton, but a pattern
one of them refuses gets that one's message. ``(?i)[^a]`` matches any
character but ``a`` and ``A`` in the Rust; the Python's automaton lets
either through.

The Python interfaces
=====================

- **The same names, the same behaviour.** Classes, functions,
  exceptions and defaults are what the pure-Python packages have now, so
  that existing code doesn't notice the change. Error messages are
  Rust's, which are the standard in every language: the XML binding's
  are already, and the C and C++ ones become Rust's in step 8.
  ``PacketError`` stays a subclass of ``ValueError``, with the same
  fields.
- **Python's own types at the edges.** Integers of any size are Python
  ``int``\s, bytes are ``bytes`` or ``memoryview``\s, and the binding's
  records keep their attribute names and their ``Optional`` and
  ``list`` fields.
- **The XML binding's records** stay the generated dataclasses they are.
  The Rust binding reads and writes the XML, and generated code converts
  between its values and the dataclasses, which the binding generator
  writes from the same model as both bindings. PyO3 classes were the
  other way, but an attribute of one that holds a record gives a copy of
  it, so ``doc.root.header.version = "2"`` would change the copy and not
  the document, and code using ``dataclasses.fields()`` or ``asdict()``
  would break. With dataclasses, nothing that builds, reads or changes
  them notices.
- **Wheels.** The packages become native, so they are built as wheels
  for each platform, with PyO3's stable ABI (``abi3``) from Python 3.10,
  so that one wheel for each platform serves every Python from 3.10 on.
  ``make python-dist`` builds them with maturin.

Packaging
=========

- **The ``tcs-xtce`` command** is a Rust program, which the .deb and the
  RPM install. The Python packages are libraries and install no
  commands: the compiler runs as ``python3 -m tcs_xtce``, as now.
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

Wheel platforms
---------------

The Python packages ``tcsxtce`` and ``tcs-xtce`` each have a wheel for
every platform below, all ``abi3`` from Python 3.10, so that one wheel
for each platform serves every Python from 3.10 on:

==========================  ==============================  ============
Platform                    Wheel tag                       Built on
==========================  ==============================  ============
Linux, x86-64, glibc        ``manylinux_2_28_x86_64``       Linux x86-64
Linux, ARM64, glibc         ``manylinux_2_28_aarch64``      Linux ARM64
Linux, x86-64, musl         ``musllinux_1_2_x86_64``        Linux x86-64
Linux, ARM64, musl          ``musllinux_1_2_aarch64``       Linux ARM64
macOS, Apple silicon        ``macosx_11_0_arm64``           macOS ARM64
macOS, Intel                ``macosx_10_12_x86_64``         macOS ARM64
Windows, x86-64             ``win_amd64``                   Windows
==========================  ==============================  ============

- **Each wheel is tested where it runs.** CI installs every wheel built
  on a machine of its own architecture and runs the Python tests
  against it. The Intel macOS wheel is cross-compiled on Apple silicon
  and tested there under Rosetta.
- **glibc 2.28** is the oldest the Linux wheels need, the
  ``manylinux_2_28`` images' own: RHEL 8, Debian 10 and Ubuntu 20.04
  have it. musl wheels serve Alpine and other musl systems.
- **macOS 11 and 10.12** are the oldest each macOS wheel runs on: the
  first macOS on Apple silicon, and the oldest the Rust toolchain
  supports on Intel.
- **Built with maturin** in CI on GitHub's runners, Linux ones in the
  ``manylinux`` and ``musllinux`` containers so that each wheel links
  only what its tag allows; ``make python-dist`` builds the wheel for
  the machine it runs on, and the source package.
- **Anywhere else** -- 32-bit systems, Windows on ARM, FreeBSD, other
  processors -- pip builds from the source package, which needs a Rust
  toolchain. A platform joins the table when someone needs its wheel.

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
- Rust's error messages are the standard in every language. The Python
  XML binding's reader and writer are removed ahead of step 9, so that
  Python's are Rust's; the C and C++ ones become Rust's in step 8.

- Step 9 follows step 8 directly: the old code is removed once the Rust
  passes every test, without waiting for a version of the Rust pieces
  to have been in use.
- The processors Rust supports are enough for the C runtime: a mission
  that uses it is built for a Rust target.

Questions for review
====================

None remain.
