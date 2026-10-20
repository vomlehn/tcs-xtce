=================
tcsxtce Design
=================

.. contents:: Contents
   :depth: 2
   :local:

Goals
=====

tcsxtce is flight software, and these goals are set with that in mind.

1. Libraries in Rust, C/C++ and Python that serialize and deserialize
   XTCE data, with data structures and functions in each language.
2. Serialization into **managed buffers**. These are preallocated and
   kept alive for exactly as long as the data structures that use them.
3. Everything comes from the **official XTCE documents**. A top-level
   ``make`` downloads them into ``references/``, and code is generated
   from them rather than written by hand.
4. All Rust code follows standard ``rustfmt`` formatting, checked as
   code is added.

There are two meanings of "serialize XTCE data", and the project
implements both:

* **The XML binding** maps the XTCE *schema* to native types, and reads
  and writes XTCE *documents*.
* **The packet codecs** read *one mission's* XTCE document and generate
  native types, with encoders and decoders for the *binary packets* that
  document describes. This layer is built on the first: the compiler
  reads the mission with the generated Python binding.

Overview
========

::

   references/Makefile ── curl ──> SpaceSystem.xsd, XTCE.pdf, xml.xsd, catalog.xml
                                        │
                     generator/xtcegen  │  (XSD → model → binding → emitters)
                                        ▼
            rust/src/generated.rs   cpp/{include,src}/generated.*   python/xtce/generated.py
                    + hand-written runtimes: buffer, xml reader/writer, document
                                        │
                                        │  mission.xml (any XTCE 1.3 document)
      tcsxtce (generator/xtcepkt)  ▼  (uses python/xtce to read it)
            <mission>.rs            <mission>.h (C and C++)         <mission>.py
                    + hand-written runtimes: xtce::packet / xtce/packet.h / xtce.packet

Each language has two parts:

* a small **hand-written runtime**: buffers, an XML reader and writer,
  and the packet bit reader and writer;
* **generated code** that only calls into the runtime.

The generators are written in Python, using only the standard library.
They emit text for all three languages from a single language-neutral
model, so the three outputs can't drift apart.

Fetching the references
=======================

``references/Makefile`` downloads the OMG XTCE specification page and
picks the highest version it links to. It then downloads that version's
``SpaceSystem.xsd`` and ``XTCE.pdf`` and records the version in
``VERSION``.

The schema imports the W3C ``xml.xsd``. That file is downloaded too, and
``catalog.xml`` maps its URL to the local copy, so ``xmllint`` validates
offline (``--nonet``).

The top-level ``make references`` always checks for a newer release.
Other targets download only when nothing has been downloaded yet.

The XML binding (xtcegen)
=========================

Pipeline
--------

``xsd.py`` — parse
   Reads ``SpaceSystem.xsd`` into a language-neutral model of named
   simple and complex types, elements, attributes, sequences and
   choices. ``complexContent`` extensions are flattened, so a derived
   type holds all inherited content. Any XSD construct the model can't
   represent raises an error. A future schema can therefore fail the
   build, but never produce a binding that silently drops content.

``binding.py`` — bind
   Turns each complex type into a record of fields. A field is an
   attribute, text content, a child element, or a ``<choice>``, and has
   a cardinality: ONE, OPT or MANY. The binding is then checked and
   annotated:

   * **Lookahead.** For every content model, it checks that the next
     element's name alone decides which field it belongs to. This one
     element of lookahead lets the generated readers be simple pull
     parsers with no backtracking.
   * **Recursion.** Strongly connected components of the type graph are
     computed with Tarjan's algorithm. Fields that close a cycle are
     *boxed* (``Box<T>`` in Rust, ``xtce::Box<T>`` in C++) so that types
     have a finite size.
   * **Order.** Types are emitted in dependency order, which C++ needs.
   * **Defaults.** Every required choice gets a default alternative. The
     default is chosen by a cost function (the size of the smallest valid
     instance, with ``min × cost`` for repeated fields), which guarantees
     that default values are finite and schema-valid.

``emit_rust.py``, ``emit_cpp.py``, ``emit_python.py`` — emit
   Generate the types, enumerations with their lexical forms, and one
   reader and one writer per record and choice. The Rust output is run
   through ``rustfmt``.

``sample.py`` — sample
   Generates test documents from the model. Together they use every
   element slot of every type reachable from ``<SpaceSystem>`` (834 of
   834), and every alternative of every choice. The generator prefers
   alternatives not yet covered, and respects range-restricted numbers,
   fixed values and the schema's ``xs:key`` constraint on message
   names. Every sample is checked with ``xmllint``.

Reading and writing
-------------------

* **Rust and C++** parse *in place*. ``Reader`` is a pull parser over the
  bytes of the document's input buffer. Element names, attribute values
  and text are slices of that buffer, so data is only copied when it is
  stored in a field. Entities are decoded only when a value contains
  them.
* **Python** uses the standard library's expat parser to build a light
  node tree, read through a cursor that follows the same protocol as the
  Rust and C++ readers.
* **Writers** append directly to the output buffer. Attribute and text
  escaping is the same in every language.

Doubles must print identically in every language. They use the
shortest string that reads back to the same value, in Python ``repr``
style:

* Python uses ``repr``.
* C++ uses ``std::to_chars``.
* Rust takes the shortest digit count from ``{:e}``, then re-formats at
  that precision.

The re-formatting step matters when two candidates of that length are
equally close to the exact value. Rust's shortest formatter may then
pick either one, while precision formatting rounds the exact binary
value half to even, as Python and C++ do. The packet tests found such a
case (the binary32 value -830.72296142578125), and a check of 200,000
random doubles now gives identical output in all three languages.

Error handling
--------------

Malformed or schema-invalid input raises ``xtce::Error`` (Rust and C++)
or ``xtce.XtceError`` (Python), with a 1-based line number. The C API
turns exceptions into status codes and keeps the message in the
document.


Managed buffers
===============

A ``Buffer`` (``ManagedBuffer`` in Python) owns one allocation, made in
advance:

* Clearing it resets the length but keeps the memory.
* Loading or writing reuses the existing capacity, and grows it only
  when needed.
* Rust and C++ build ``Buffer`` on ``Vec<u8>`` and ``std::string``,
  whose ``clear()`` keeps capacity. Python uses a ``bytearray`` and its
  own length counter, writing with slice assignment.

Buffers are *owned*, never global:

* A ``Document`` owns an input and an output buffer.
* A packet ``Codec`` owns an input and an output buffer, sized from the
  generated ``MAX_BYTES``.

Their lifetime is therefore that of the owning object. Rust frees them
on ``Drop``, C++ in the destructor, and Python on garbage collection or
explicitly with ``close()`` / ``with``.

Results are **views into the buffers**: ``&[u8]``, ``std::string_view``
or ``std::span``, and ``memoryview``. They stay valid until the owner
next changes. Python's ``ManagedBuffer`` refuses to grow while a view is
held, because a ``bytearray`` can't be resized while exported. This is
why the API hands out views for use in ``with`` blocks.

A test in each language checks that the capacity stays the same across
repeated loads and serializations. One such test found that Python's
``load_from`` grew the buffer on every reload; it now fills the existing
allocation first.


The packet codecs (tcsxtce)
===========================

Code generation or interpretation
---------------------------------

There were two ways to build a packet codec:

* **Interpret** a mission database at run time in each language.
* **Compile** it once into code.

``tcsxtce`` compiles, for three reasons:

* **The difficult part is written once.** Name resolution, container
  inheritance, include flattening, criteria analysis and size bounds are
  done once, in Python. Each language only needs simple straight-line
  code and a small runtime. An interpreter would need all of that logic
  in each of the three languages.
* **The data structures are typed.** Users get ``Housekeeping { mode,
  temperature, ... }`` instead of a map of names to dynamic values.
  Mistakes in field names or types become compile errors in Rust and C++.
* **It fits the original goal** of "data structures and methods per
  language", and flight or ground software can use it without parsing
  XML at run time.

The cost is that a database change requires regenerating the code, as
for the XML binding.

The command line, ``generator/tcsxtce``, writes one language per run
(``tcsxtce INPUT OUTPUT --lang LANG``). The compiler and the emitters it
calls live in ``generator/xtcepkt``.

One header for C and C++
------------------------

``--lang c`` and ``--lang c++`` write the same file: C11 that also
compiles as C++. A separate C++ API would be a second emitter and a
second runtime to keep bit-identical with the others, for little gain.
C++ can call a C API directly, but C can't call a C++ one. Writing that
header means:

* **Names** carry a prefix (``demo_``), because C has no namespaces.
  The generated names never contain ``_`` themselves (types are
  CamelCase, functions lowercase suffixes, labels upper case), so
  ``<prefix><Type>_<member>`` can't collide.
* **Errors** are returned codes plus an ``xtce_pk_error``, because C has
  no exceptions. Generated code propagates them with ``XTCE_PK_TRY``.
* **Storage** is fixed. Strings are ``{len, data[N + 1]}`` with ``N``
  the encoding's capacity, binary values are ``data[N]``, and optional
  fields have a ``has_`` flag. Encoding writes into a buffer the caller
  supplies, typically ``*_MAX_BYTES`` long. Nothing is allocated, which
  suits flight software.
* **Portability.** The header uses only constructs valid in both
  languages: no designated initializers, compound literals or implicit
  ``void*`` conversions. ``XTCE_PK_ZERO`` gives ``{0}`` in C and ``{}``
  in C++. The tests and the example build every C source both ways, and
  the cross-language check runs both builds.
* **Double formatting** can't use C++'s ``std::to_chars``. The C
  runtime takes the shortest ``%.*e`` precision that round-trips through
  ``strtod``. Like Rust, it then has ``printf`` round the exact value,
  which matches Python's ``repr``.

Compiler pipeline
-----------------

``compile.py`` turns the mission document, loaded with ``python/xtce``,
into the model in ``model.py``:

**Indexing and name resolution.**
   Parameter types, parameters, argument types, containers and
   meta-commands are indexed by SpaceSystem path. References are resolved
   in the following order:

   * absolute paths;
   * relative paths with ``.`` and ``..``;
   * unqualified names in the current SpaceSystem, then in each
     enclosing one.

**Types** (``VType``).
   Each parameter or argument type becomes an engineering kind (``int``,
   ``float``, ``enum``, ``bool``, ``string``, ``binary``), plus:

   * an encoding (``IntEnc``, ``FloatEnc``, ``StringEnc``,
     ``BinaryEnc``);
   * an optional calibrator (``Polynomial`` or ``Spline``).

   Schema defaults are filled in here: 8-bit unsigned integers, 32-bit
   IEEE floats, ``previousEntry`` locations, a ``00`` terminator and
   16-bit size tags. Time types become ``float`` seconds, with
   ``scale``/``offset`` as a linear calibrator.

**Items.**
   A container's entry list becomes a flat list of items:

   * ``Value`` reads or writes one parameter or argument, through a
     ``Local``;
   * ``Seek`` applies a location offset;
   * ``Mark`` records the start of an included container, for
     ``containerStart`` offsets;
   * ``Fixed`` is a constant bit pattern;
   * ``Cond`` is an ``IncludeCondition`` block.

   ``ContainerRefEntry`` is flattened in, so generated structs have no
   nesting to navigate.

**Nodes.**
   Each container or meta-command becomes a ``Node`` with:

   * its base;
   * its own items;
   * the ``Criterion`` list that selects it relative to its base;
   * its children.

   Argument assignments become ``==`` criteria. This turns command
   inheritance into the same structure as telemetry inheritance, so one
   code path handles both.

**Fields.**
   For each concrete node:

   * Every ``==`` criterion along the chain *fixes* its local. Fixed
     locals are not fields: they are written automatically and checked
     when decoding.
   * The remaining locals, in packet order, are the struct's fields.
   * Names are deduplicated per struct.

**Bounds.**
   ``max_bits`` walks the items with the largest size of each value (the
   maximum for variable strings). This gives ``MAX_BYTES``, which
   ``Codec`` uses to preallocate.

**Unsupported constructs**
   raise ``Unsupported`` with the construct and where it occurs. The
   failure applies to that node and its descendants only.
   ``--skip-unsupported`` leaves those nodes out. Otherwise every failure
   is reported and generation stops.

Generated code
--------------

For each concrete node the emitters write:

* the struct;
* ``decode``, which reads the whole chain into numbered locals
  (``v12``), checks every criterion, then builds the struct;
* ``encode_into``, which checks the criteria that involve fields, then
  writes the chain, using fixed values where needed;
* the canonical text form.

For each tree they write a *kind*. Its ``decode`` reads the root's
items once, then descends through nested ``if`` blocks: one per child,
testing the child's criteria in document order. Each child's items are
read only after its criteria pass. Decoding therefore never reads a
field twice and never backtracks, and the first matching child wins.
This is the usual reading of XTCE when several children match.

Locals keep engineering values. Criteria are compared on those values.
Comparisons on raw values are accepted only where raw and engineering
values are numerically equal (uncalibrated integers, enumerations and
booleans).

Determinism across languages
----------------------------

The three emitters produce the same structure. The runtimes are written
to give bit-identical results:

* **Rounding** an engineering value to a raw integer is half away from
  zero. Rust uses ``f64::round``, C ``round``, and Python an
  explicit ``trunc``-based function, because Python's ``round`` rounds
  half to even.
* **Polynomials** are evaluated term by term, in document order. Powers
  above 1 go through the C library ``pow`` in all three languages
  (``powf``, ``pow``, ``math.pow``).
* **Splines** use the same interpolation expression with the same
  evaluation order.
* **binary32** conversion rounds to nearest. Overflow gives infinity in
  all three languages; Python's ``struct`` would raise, so it is caught.
* **UTF-8** is strictly validated in all three: C has its own
  validator that matches Rust and Python, rejecting overlong forms,
  surrogates and values beyond U+10FFFF.
* **Errors** have the same kinds in every language, and ``describe()``
  output is identical. This makes cross-language testing a simple text
  comparison.

Enumerations keep the raw value: a Rust newtype, a C ``int64_t``
typedef with a macro per label, and a Python ``IntEnum`` with
pseudo-members.
Unlisted values therefore round-trip instead of failing. Telemetry from
newer flight software decodes even when the database is out of date.

Runtime
-------

``BitReader`` and ``BitWriter`` handle fields of 1–64 bits, most
significant bit first, at any bit position:

* Little-endian fields are read big-endian and then byte-swapped, so
  they must be a whole number of bytes.
* The writer zero-fills the buffer as it grows, and overwrites bits
  under a mask. Backward ``containerStart`` seeks therefore work.
* In Rust and Python the writer always writes into a managed ``Buffer``.
  ``Packet::encode`` takes the buffer to fill, and ``Codec`` supplies its
  own. In C the writer fills a caller-owned array and fails with
  ``NoSpace`` rather than grow it.

Testing strategy
================

**Schema coverage**
   21 generated sample documents cover every element slot and every
   choice alternative. All are validated with ``xmllint``. Each language
   round-trips each sample (deserialize, serialize, deserialize and
   compare). ``tests/cross_check.sh`` requires the three outputs to be
   byte-identical, and to validate too.

**Packet vectors**
   ``tests/mission/demo.xml`` uses every supported feature, and is
   checked with ``xmllint``. ``vectors.txt`` holds packets built by an
   independent bit-string packer rather than by the generated code.
   ``vectors.expected`` holds their decoded text and re-encodings. Every
   language must reproduce ``vectors.expected`` exactly.

**Random packets**
   ``random_vectors.py`` produces packets with valid headers and random
   payloads. They reach every success path and every error kind. All
   three languages must agree byte for byte. Successful re-encodings are
   fed back in, and must decode to the same values and the same bytes.

**Unit tests**
   Each language tests encoding from structs:

   * constants inherited from criteria;
   * dispatch;
   * range, criteria, calibration and string errors;
   * include conditions;
   * commands;
   * that codec buffers keep their capacity over many packets.

   The C tests (``cpp/tests/packet_test.c``) are built and run both as
   C and as C++.

**Formatting**
   Every ``make rust`` runs ``cargo fmt --check``. A Claude Code
   ``PostToolUse`` hook runs ``rustfmt --check`` on each ``.rs`` file
   as it is written, and rejects the edit if formatting differs.

Limitations and future work
===========================

* **XML binding:**

  * The binding is strict XTCE 1.3. Supporting 1.1/1.2 would need a
    version-tolerant reading mode or one binding per schema version.
  * Namespace URIs aren't checked.
  * Facet and ``xs:key`` constraints are left to ``xmllint``.

* **Packet codecs** — the main missing XTCE features are:

  * ``RepeatEntry`` and array types, which need ``Vec`` fields with
    dynamic counts;
  * ``DynamicValue`` sizes, for example a payload sized by the CCSDS
    length field;
  * aggregate types;
  * ``BooleanExpression`` criteria;
  * container ``BinaryEncoding``;
  * error detection (CRC and checksum generation).

  The model has room for each of these: new ``Item`` kinds, and a
  ``VType`` for structured values.

* **Computed fields:** length and checksum fields are left to the
  application.

* **C API:** generated packet types have no C interface yet. One could
  be emitted as plain C structs, with functions that wrap the C++ code.
