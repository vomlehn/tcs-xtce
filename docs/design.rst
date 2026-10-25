=================
tcs-xtce Design
=================

.. contents:: Contents
   :depth: 2
   :local:

Goals
=====

tcs-xtce is flight software, and these goals are set with that in mind.

1. Libraries in Rust, C/C++ and Python that serialize and deserialize
   XTCE data, with data structures and functions in each language.
2. **Data structures that programs in different languages can
   exchange**, defined once in XTCE. The packets are bit-packed, with
   nothing in them but the field values, and both ends check that they
   use the same definition by comparing digests (``xtce-digest``),
   rather than describing the data in every message as JSON and
   Protocol Buffers do.
3. Serialization into **managed buffers**. These are preallocated and
   kept alive for exactly as long as the data structures that use them.
4. Everything comes from the **official XTCE documents**. A top-level
   ``make`` downloads them into ``references/``, and code is generated
   from them rather than written by hand.
5. All code keeps its lines within 80 columns, generated or not. Rust
   follows ``rustfmt`` (with ``max_width = 80``), checked as code is
   added; C and C++ indent 8 spaces, as ``.clang-format`` sets.

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
     tcs-xtce (generator/xtcepkt)  ▼  (uses python/xtce to read it)
            <mission>.rs            <mission>.h (C and C++)         <mission>.py
                    + hand-written runtimes: tcs_xtce::packet / xtce/packet.h / xtce.packet

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

``wrap.py`` — wrap
   Keeps generated code within 80 columns, for every language, after the
   emitters (and ``rustfmt``) have run. It breaks long lines inside
   brackets, splits string literals into adjacent literals (Rust: with
   backslash-newline continuations, which ``rustfmt`` keeps), moves
   trailing comments above their line and rewraps comments and
   docstrings. ``tcs-xtce`` uses it too, for the packet codecs.

``pyalign.py`` — lay out Python
   Lays out the Python that ``wrap.py`` has broken, and the hand-written
   Python, in one compact aligned style: continuation lines under the
   bracket they continue, packed items, and closers at the end of the last
   line. It is a pretty-printer in the style of Wadler's: each statement
   becomes a document of text, places to break and groups, and each
   bracket offers two layouts, aligned and hanging. It keeps the aligned
   one unless the hanging one fits where it doesn't or avoids a staircase
   of nested breaks. It also puts long values and conditions in
   parentheses and splits long strings, and it checks that the code's AST
   is unchanged.

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

Malformed or schema-invalid input raises ``tcs_xtce::Error`` (Rust), ``xtce::Error`` (C++)
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


The packet codecs (tcs-xtce)
============================

Code generation or interpretation
---------------------------------

There were two ways to build a packet codec:

* **Interpret** a mission database at run time in each language.
* **Compile** it once into code.

``tcs-xtce`` compiles, for three reasons:

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

The command line, ``generator/tcs_xtce``, writes one language per run
(``tcs-xtce INPUT... OUTPUT --lang LANG``). The compiler and the emitters it
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

``compile.py`` turns the mission's documents, loaded with ``python/xtce``,
into the model in ``model.py``:

**Indexing and name resolution.**
   Parameter types, parameters, argument types, containers and
   meta-commands are indexed by SpaceSystem path. A mission may be split
   across several documents; the root SpaceSystem of each is indexed at
   the top of one tree, as Yamcs loads a list of documents, so a path's
   first name is a document's root SpaceSystem. References are resolved
   in the following order:

   * absolute paths;
   * relative paths that start with ``.`` or ``..``, from the current
     SpaceSystem only;
   * unqualified names, and other relative paths, from the current
     SpaceSystem, then from each enclosing one. Yamcs looks relative
     paths up this way, and missions such as the cFS ones for Yamcs rely
     on it (``cfs-ccsds/cfs-cmd`` from ``/CFE_EVS``).

   The mission is named for its last document, so that the documents it
   builds on can be listed first; otherwise the order of the documents
   changes nothing.

   A type with a ``baseType`` is its base type with the attributes and
   child elements it gives replacing the base's, whole: nothing is merged
   below a child element, which keeps the rule simple to state and
   matches how the PUS mission uses it (a type that only renames
   another). A ``CommandContainer`` without a ``BaseContainer`` in a
   meta-command with a base extends the base meta-command's container, as
   Yamcs reads it; XTCE doesn't say, and the alternative, a command that
   drops its base's header, isn't useful.

**Types** (``VType``).
   Each parameter or argument type becomes an engineering kind (``int``,
   ``float``, ``enum``, ``bool``, ``string``, ``binary``), plus:

   * an encoding (``IntEnc``, ``FloatEnc``, ``StringEnc``,
     ``BinaryEnc``);
   * an optional calibrator (``Polynomial`` or ``Spline``).

   A calibrated integer keeps the ``int`` kind; ``eng`` holds the type's
   ``sizeInBits`` and ``signed``, which size the generated field instead
   of the encoding. A binary value with an XTCE size of -1 has a
   ``BinaryEnc`` with ``rest`` set and a bound of ``MAX_FIELD_BITS``.
   The compiler checks that nothing follows it in any packet, so the end
   of the packet gives its size.

   Schema defaults are filled in here: 8-bit unsigned integers, 32-bit
   IEEE floats, ``previousEntry`` locations, a ``00`` terminator (a zero
   code unit in UTF-16 and UTF-32) and 16-bit size tags. Time types become ``float`` seconds, with
   ``scale``/``offset`` as a linear calibrator.

**Calibrators that use other values.**
   Context calibrators and math operations that read other values of the
   packet are compiled unbound, as part of their type, like sizes from
   other fields. Each packet entry binds a copy to the values decoded
   before it: criteria become terms on those values, and operands
   references to them. Types inside aggregates are shared by every packet
   that uses the aggregate, so a calibrator there can't be bound to one
   packet's values, and the compiler rejects it.

**Items.**
   A container's entry list becomes a flat list of items:

   * ``Value`` reads or writes one parameter or argument, through a
     ``Local``;
   * ``Seek`` applies a location offset;
   * ``Mark`` records the start of an included container, for
     ``containerStart`` offsets;
   * ``Fixed`` is a constant bit pattern;
   * ``Pad`` is the padding up to a container's fixed size;
   * ``Cond`` is an ``IncludeCondition`` block.

   ``ContainerRefEntry`` is flattened in, so generated structs have no
   nesting to navigate. A repeated one is the exception: it becomes a
   ``Value`` holding an array of an aggregate (see Aggregates).

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

Bit and byte order
------------------

XTCE says only that ``bitOrder`` describes "the bit ordering of the
encoded value" and that a byte order list views the value's bytes as
memory, first byte at address 0. Neither defines a field that isn't
whole bytes, or how the two combine. tcs-xtce uses one rule for every
integer and float encoding: take the field's bits in groups of 8 from its
start, read each group in the bit order (the first bit most significant
for ``mostSignificantBitFirst``, least significant otherwise, with the
bits of each byte numbered the same way), and combine the groups in the
byte order. The rule has three properties that make it the right one to
pick:

* the defaults (most significant bit and byte first) give the plain
  big-endian bit string, so nothing that worked before changes;
* whole little-endian bytes are ordinary little-endian values, however
  the field is aligned;
* least significant bit and byte first together give exactly the layout
  of C bit fields on little-endian machines, which is what documents
  describing such structures (like the cFS headers in
  ``tests/external``) mean.

The runtimes keep the existing ``uint_order`` for the common cases and
call the general ``uint_bits`` only for the others, so ordinary packets
pay nothing for it.

Error detection
---------------

XTCE says an ``ErrorDetectCorrect`` algorithm covers "all or part of"
its container, from its start or its end, and that ``parameterRef`` names
the parameter holding the value, but not where the covered bits end.
tcs-xtce takes the reading that fits the way checksums are laid out: from
the start, the bits run up to the value's own field (a trailing CRC
covers everything before it), and from the end, ``bitsFromReference``
bits are left out at the end (where the value sits). Fields are computed
in the order they appear, which lets later values cover earlier ones;
the opposite (a value covering one computed after it) would need the
computation order to be given, which XTCE has no way to say.

A checksum field is computed, not set, like the packet length field: it
is not in the generated struct, and the same rules keep anything else
from depending on it. The encoder writes a zero placeholder and patches
it at the end, after the length field (which the covered bits usually
include). For a check declared on a base container, the field is looked
up per concrete packet, so that each derived container may have its own,
but it must be in the declaring container or below: marking a field of a
base container computed would take it out of sibling packets that don't
have the check.

XTCE names the checksum algorithms without defining them. The names
(``sum8``, ``unix_sum``, ``fletcher16``, ``luhn`` and the rest) are
Jacksum's, so tcs-xtce uses Jacksum's definitions where it has them: a
``sumN`` is the sum of the bytes, and ``unix_sum`` is BSD ``sum``, which
``tests/float/known.txt`` checks against the real ``sum`` command's
output. Fletcher's checksums take their words most significant bit
first, like the rest of a packet, and the check digits take a decimal
digit per 4 bits, as packed BCD does. CRCs follow the parameter model of
the CRC catalogue, so the catalogue's check values test them.

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
* **Other float formats** are converted with integer arithmetic only:
  a double is split into a 53-bit significand and an exponent, rounded to
  the target format's precision half to even, and put together again;
  decoding rounds the format's significand (up to 113 bits) to a double
  the same way. Python's reference uses ``fractions.Fraction``, whose
  conversion to ``float`` is correctly rounded; Rust uses ``u128``; C,
  which has no 128-bit integer in C11, keeps the top 64 bits and a sticky
  bit, which rounds identically. ``tests/float_check.sh`` holds all four
  builds to the standards' examples and to each other on 60,000 random
  conversions. No library float conversion is used for these formats, so
  nothing depends on the platform's ``long double``.
* **Math operations** are compiled, not interpreted: the compiler turns
  a ``MathOperationCalibrator``'s postfix expression into a tree,
  applying ``swap``, ``drop``, ``dup`` and ``over`` as it goes, and each
  emitter writes it as an expression. ``+``, ``-`` and ``*`` are plain
  arithmetic, identical everywhere; every other operator calls the
  runtime's ``math`` function, which pins down what the schema leaves
  open (the sign of a modulo, NaN in ``min`` and ``max``, out-of-range
  shifts) and what languages do differently (Python raises where C
  returns NaN or infinity, and loses the sign of zero when rounding to an
  integer). The transcendental functions come from the C library in all
  three: Python's ``math`` and C call it, and Rust's standard library
  does too, except for ``asinh``, ``acosh`` and ``atanh``, which it
  computes itself and which the Rust runtime therefore calls from the C
  library directly. ``tests/float_check.sh`` checks every operator on
  special and random operands in all four builds.
* **Character sets** are converted by each runtime's own code (Python's
  codecs, Rust's ``char`` conversions and C's tables), with the same
  rules for invalid bytes. Windows-1252's five undefined bytes are
  errors, as in Python's ``cp1252``.
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

Arrays and sizes from other fields
----------------------------------

Arrays and repeated entries share one representation: a ``VType`` of
kind ``array`` whose ``ArrayEnc`` holds the element type, a count and
the gap between elements. A multi-dimensional array is flattened into
one list in encoding order, because the dimensions add nothing to the
wire format, and nested lists would differ between the languages.

A ``DynamicValue`` becomes a ``DynSize``: ``factor * (slope * value +
intercept)``. Types hold it unbound, because an argument reference only
means something inside a particular meta-command. When an entry is
compiled, the size is bound to the value it reads, which must be decoded
earlier in the packet, and given a bound:

* from the referenced value's ``ValidRange``, or its encoding (through
  its calibrator, for a calibrated integer);
* capped at ``MAX_FIELD_BITS`` (64 KiB on the wire).

The bound becomes ``N`` in the C storage types and in ``MAX_BYTES``.
That is how C keeps fixed-size structs and no allocation while counts
vary.

Binding copies the ``VType``, so the C emitter, which keys constants by
type identity, sees distinct types when two packets bound the same type
differently. ``EndingIndex - StartingIndex + 1`` and several dimensions
fold into the intercept and factor, so one runtime function,
``dynamic_size``, covers every case. It works in ``double`` in every
language, so the three runtimes accept and reject exactly the same
values.

Criteria are lists of terms that must all hold. A term is a
``Criterion`` (one comparison) or, from a ``BooleanExpression``, an
``AllOf`` or ``AnyOf`` of further terms, so ``ComparisonList`` and the
expression trees share one representation. A ``Criterion`` compares a
value with a constant or, with ``other``, with a second value. Each
emitter renders a term recursively with a callback that gives the
expression for a value: the decoded local, the struct field, or the
fixed constant when encoding. Only a top-level ``==`` against a
constant fixes a field, since nothing else pins down one value.

A reference to an aggregate member (XTCE's ``P.a.b``) is split into the
parameter or argument and the member names; names can't contain dots,
so that is unambiguous. Binding a ``DynSize``, or compiling a
``Criterion``, walks the names through the aggregate types to the
members' field names (``path``) and the member's type. Emitters append
``.field`` for each to the expression for the value, which works in
every language because generated aggregates are plain structs. An
``==`` criterion on a member doesn't fix the value, since there is no
field that holds only the member.

An ``ArrayEnc`` keeps each dimension's first index (``starts``) and,
when all are fixed, its size (``shape``). An array entry with a
``DimensionList`` replaces the value's type with one for the subset,
built by the same code as array types, so the subset is just a shorter
array to every later stage. Its ``starts`` stay in the full array's
index space, so element references mean the same with or without one.

Array elements (``P[2]``, ``P[1][2]``) extend the same paths with
``Index`` steps, one per reference and flattened over the dimensions,
which emitters render as ``[i]`` (``.data[i]`` in C). Only fixed counts
are allowed, so an index is checked against the count when compiling,
and decoded arrays always have every element. Encoding indexes the
caller's data, though, and restriction criteria run before anything is
written. ``term_indexes`` lists the arrays a criterion indexes, and
encoding checks their lengths first, failing with the same ``BadSize``
that writing them would. Every other reference is to a value written
earlier, whose length has already been checked.

A ``DiscreteLookupList`` is a ``DynSize`` too: instead of a ``local``,
binding compiles its comparisons into ``lookup`` (criteria and a value
per case, plus ``default``), and that value is ``x``. The emitters
render it as a chain of conditions where they would use the field, so
the size checks stay in ``dynamic_size``. A location from another value
is a ``Seek`` whose ``offset`` is such a ``DynSize`` rather than a
number, with ``unit`` 1 and a bound that ``max_bits`` uses. Layouts that
must be known when compiling (repeated and fixed-size containers) reject
it.

A referenced field that does nothing but count an array or size a
binary value is computed when encoding and left out of ``Node.fields``,
like a packet length, so it can't disagree with the data and ``encode``
never changes its input. ``derived_sizes`` finds these: an uncalibrated
integer outside any include condition, every use of which is the
``DynSize`` of a whole array or binary field, also unconditional. Any
other use (a criterion, a location, a lookup, a string's size, a member
or element path, a size inside another type) keeps it a field, since the
encoder could not tell what value the application wants there.
``Node.derived`` maps each to the field it sizes. Encoding first computes
it with ``size_source``, the inverse of ``dynamic_size``: it rounds
``((n / factor) * unit - intercept) / slope`` to an integer and accepts
it only if the forward formula gives ``n`` back exactly, so a decoded
value always round-trips. The emitters then use the result wherever they
would use the field, including when writing it, and the size check that
follows catches data above the field's bound.

A referenced field that stays a field is set by the caller. The encoder
checks that the data matches it and fails with ``BadSize`` if not.

Aggregates
----------

An aggregate is a ``VType`` of kind ``aggregate`` whose
``AggregateEnc`` lists its members. Generated code reads and writes the
members in place, recursing through member types, so aggregates nest in
arrays and in each other without new runtime functions. Each language
gets a named struct, so values can be built and compared as a unit.
``Mission.aggregates`` lists them inner first, which is the order C
needs to declare them in.

A member can take its size or count from an earlier member of the same
aggregate. XTCE has no clear way to say this: references name parameters
and arguments. Yamcs reads a reference to a member's name as that member
of the same value, and its test documents depend on it, so the compiler
does the same. Inside an aggregate type, a ``DynamicValue`` whose
reference, as written, is the name of an earlier member binds to that
member, even if a parameter or argument has the same name. Any other
reference from inside an aggregate is still unsupported, since one type
can be used in packets where the referenced value is not decoded first.

The bound ``DynSize`` has ``member`` (the member's field name) rather
than ``local``. The emitters pass the whole ``DynSize`` to the callback
that gives a size's value, and an aggregate wraps that callback, mapping
its members to the value being read or written. Reading stores the
members in locals (Rust) or names them with ``:=`` (Python), since the
struct doesn't exist until all of them are read. C reads into the struct
in place.

A repeated ``ContainerRefEntry`` reuses this. The included container's
entries are compiled on their own, then turned into members, each with
the bits to skip before it (from locations), and the aggregate gets the
padding after its last member (from a container size). That keeps the
emitters' item code unchanged: an array of aggregates already reads and
writes in a loop. It needs the layout of one copy to be known when
compiling, which rules out overlapping entries and include conditions,
as it does for a fixed container size. ``static_end`` works out that
layout, and ``Pad`` covers the rest of a fixed-size container that is
not repeated.

Packet length fields
--------------------

XTCE 1.3 can say that a field's size comes from another value, but not
that a value is the size of its packet. Its algorithms could compute
one, but have no operand for the packet's size, and tools such as Yamcs
fill in the CCSDS length with code written for that header. tcs-xtce
reads an ``AncillaryData`` named ``tcs-xtce.length`` instead, the
standard place for tool-specific notes, so the document stays valid
XTCE. The CCSDS Electronic Data Sheets that cFS can use take the same
approach with a ``LengthEntry``.

The compiler sets ``Local.length`` to the adjustment, and
``Node.length_field()`` finds the packet's length field. Its value
depends on everything written after it, so encoding writes zeros in
its place, remembers the position, and ``patch_length`` fills it in once
the packet is written, from the size of the buffer. Decoding reads it
like any field, then ``limit`` shortens the data to the packet, so a
value that takes the rest of the packet gets the right size and bytes
after the packet are never read. ``finish`` checks, just before the
struct is built, that the furthest bit read reaches the end. The reader
counts bits skipped by seeks as read, because the writer fills them.

The length is left out of ``Node.fields``, like a value fixed by its
inheritance, so it can't be set wrong. The checks in
``check_length`` keep the code simple: it must be written once, from a
known position, and nothing may depend on its value while the packet is
being encoded.

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

The digest (xtce-digest)
========================

Packets carry no field names, tags or lengths, so two programs can read
each other's packets only if they were built from documents that define
the same packets. ``xtce-digest`` gives them a way to check: an MD5
digest of what a document means, which they can compare once, when they
start talking, instead of describing the data in every message.

The digest is computed over a canonical encoding of the document's
elements, attributes and text, with comments and processing instructions
dropped and elements and attributes named by namespace URI rather than
prefix. Whitespace around text is dropped, attributes
are sorted, and the children of elements that hold unordered collections
(``UNORDERED``: the ``…Set`` elements, ``ComparisonList`` and the like)
are sorted by their encoding, so reordering a ``ParameterSet`` changes
nothing. Everything else keeps its order, since entry and member order
is the packet layout. Each item of the encoding is length-prefixed, so
different documents can't run together into the same bytes.

The digest errs on the side of changing: anything that changes the
document's meaning changes it, and so do some edits that change nothing
tcs-xtce generates, such as a description. A false alarm costs a look at
the document; a missed change would cost corrupted data. MD5 is used for
its speed and ubiquity, not for security: it detects accidental
differences, not deliberate collisions.

Each runtime computes the digest itself, so a program in any language
can send its digest and check the one it receives: ``xtce.digest`` in
Python, ``tcs_xtce::digest`` in Rust, and ``xtce/digest.h`` for C and C++,
header-only like ``xtce/packet.h`` so that C programs still link no
library. The encoding is specified in ``python/xtce/digest.py``, and each
runtime has its own small XML reader for it: the readers the XML bindings
use are driven by the schema and don't keep namespaces, and an
implementation per language keeps each runtime free of dependencies
(Rust and C have no MD5 either, so each has one, from RFC 1321, with the
constant table written out rather than computed with ``sin``).

Byte-identical digests need every reader to agree on details XML leaves
to the parser, so the encoding pins them down: the document must be
UTF-8 and may not declare another encoding; DOCTYPEs are rejected, so no
reader has to expand entities from a DTD; text is trimmed of the four XML
whitespace characters only, not of everything a language calls
whitespace; and line ends and attribute values are normalized as XML
requires. ``tests/digest_check.sh`` runs all four runtimes on the test
documents, including edge cases in ``tests/digest``, and checks them
against each other and against known digests. The ``UNORDERED`` list is
written out in each runtime and must stay the same in all of them.

A mission split across several documents has one digest for the set:
the MD5 digest of the documents' encodings, sorted by their bytes and
joined. Each encoding is self-delimiting (it is one length-prefixed
element), so joining them is unambiguous; sorting makes the order of the
files irrelevant, as it is to the compiler; and a set of one document has
that document's digest, so nothing changes for single-document missions.
``tests/digest/sets.txt`` pins sets' digests, which every runtime must
reproduce.

The runtimes don't check documents against the schema; the
``xtce-digest`` command, in ``generator/xtce_digest``, does, then digests
each file with the Python runtime. Computing a digest allocates memory,
so it belongs at start-up, not in code that must not allocate.

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

**Float formats**
   ``tests/float_check.sh`` decodes and encodes every float format and
   size in all four builds: the conversions in ``tests/float/known.txt``
   must give the results the formats' standards give, and random values
   must give the same results in every build.

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
   * arrays, repeated entries and sizes from other fields, including
     data that doesn't match its count or size;
   * aggregates, nested and in arrays;
   * that codec buffers keep their capacity over many packets.

   The C tests (``cpp/tests/packet_test.c``) are built and run both as
   C and as C++.

**Public documents**
   ``make samples-check`` runs the binding and compiler on 60 public
   XTCE documents from Yamcs, its quickstart, yamcs-cfs and the XTCE
   Toolkit, pinned by commit and
   SHA-256. Generated code is compiled in every language, every runtime
   digests each document, and each document's summary, digest included,
   is compared with ``tests/external/expected.txt``. The missions in
   ``tests/external/missions.txt``, each split across several of the
   documents, are compiled and digested as sets the same way.
   This catches crashes and disagreements with ``xmllint``, which
   hand-written tests can miss, and shows which unsupported constructs
   real documents use.

**Formatting**
   Every ``make rust`` runs ``cargo fmt --check``. A Claude Code
   ``PostToolUse`` hook runs ``rustfmt --check`` on each ``.rs`` file
   as it is written, and rejects the edit if formatting differs.
   Code keeps its lines within 80 columns, so that it reads the same in
   any editor or terminal and side by side in a diff:

   * Rust: ``rustfmt`` with ``max_width = 80`` (``rustfmt.toml``), and by
     hand where ``rustfmt`` leaves a line long, such as string literals
     and macro bodies.
   * C and C++: 8-space indents and 80 columns, set by ``.clang-format``.
   * Python: laid out by ``generator/xtcegen/pyalign.py``, with
     continuation lines lined up under the bracket they continue, by
     ``make fmt`` and checked by ``make fmt-check``. Black and
     ``ruff format`` aren't used, since they would put every item on its
     own line.
   * Makefiles and scripts: wrapped by hand.
   * Generated code: wrapped by ``wrap.py`` after the emitters run, so
     that it needs no formatting. ``generator/tests/test_wrap.py`` checks
     that wrapped code fits in 80 columns and keeps its meaning.

   ``tests/line_width.py``, the last step of ``make test``, enforces the
   limit: it checks every source file, Makefile and script in git, and
   the generated code, and fails naming each line that is too wide.
   Prose and data (reStructuredText, Markdown, XML, TOML) aren't
   checked.

Limitations and future work
============================

* **XML binding:**

  * The binding is strict XTCE 1.3. Supporting 1.1/1.2 would need a
    version-tolerant reading mode or one binding per schema version.
  * Namespace URIs aren't checked.
  * Facet and ``xs:key`` constraints are left to ``xmllint``.

* **Packet codecs** — the constructs the manual lists under "Not
  supported" are still missing; among them, references to elements of
  arrays whose count varies in a dimension other than the first, which
  would need the flattened index computed at run time. The model has
  room for them: new ``Item`` kinds, and ``VType`` kinds for structured
  values, as arrays have.

  A reference to an element of an array whose count varies (in its
  first dimension) can't be checked when the code is generated, as one
  to a fixed-size array is: the element may be missing. Rust and Python
  read it through ``element()``, which fails with ``BadSize``; C, which
  has no way to leave an expression early, checks with
  ``xtce_pk_element()`` before each statement that reads it.

  Error detection (``ErrorDetectCorrect``: CRCs and checksums) is left
  out on purpose: the packets travel over a link that already detects
  errors. Documents that use it are reported as unsupported.

* **Computed fields:** packet length fields are computed when the
  document marks them, and sizes and counts when what they size is a
  whole value that is always present; criteria, locations and lookups
  that read them see the computed value. A size or count that also sizes
  a string, a member or an element, or a value that may be absent, is
  still set by the application, which must make it agree with the data:
  a string's size can't be worked out from its content, and a member's
  field is part of an aggregate the application builds.
