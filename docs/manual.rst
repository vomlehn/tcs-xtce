=======================
tcs-xtce User Manual
=======================

.. contents:: Contents
   :depth: 2
   :local:

Summary
=======

.. note::

   This is a very early release of tcs-xtce. Comments are very welcome,
   as are people interested in collaborating on it. Please open an issue
   or a pull request at https://github.com/vomlehn/tcs-xtce; the
   contributing guide,
   https://github.com/vomlehn/tcs-xtce/blob/main/CONTRIBUTING.md, says
   how.

**XTCE** (XML Telemetric and Command Exchange) is a standard for
describing the telemetry and command messages that a spacecraft,
instrument or ground system exchanges. It is published both by the
Object Management Group (OMG), at https://www.omg.org/spec/XTCE/, and
by the Consultative Committee for Space Data Systems (CCSDS), as
CCSDS 660.0-B-2, at https://ccsds.org/Pubs/660x0b2.pdf. The CCSDS
Blue Book adopts XTCE 1.2; tcs-xtce follows the latest OMG version,
currently 1.3. An
XTCE document is a mission database. It describes each message's
parameters, their types, units and encodings, and how they are laid
out in packets. XTCE describes the messages, not the code that handles
them, so the same specification can be used from any programming
language and shared between organizations and tools.

tcs-xtce turns an XTCE document into code. From a mission's XTCE
document it generates data structures for every packet, along with
methods that encode them to the bytes that go on the wire and decode
them from those bytes. It presently generates code for C/C++, Rust and
Python 3.

The key use of XTCE here is to define data structures that programs
written in different languages can exchange. The code generated for
each language writes exactly the same bytes, and reads the bytes any of
the others wrote. The packets are bit-packed: each field takes the bits
its encoding gives, and nothing more, so they are much smaller than the
languages' own data structures, which pad their fields for alignment.
And when both programs use the same XTCE document, which ``xtce-digest``
can check, each knows every field's type and position in advance, so
nothing on the wire has to describe them. That avoids the overhead of
self-describing formats such as JSON and Google Protocol Buffers. See
`Exchanging data between programs`_.

The generated code is written for flight software, where dynamic
memory allocation is often not allowed. tcs-xtce has a flag,
``--dynamic-allocations false``, that leaves out code that allocates
heap memory after initialization: Rust then gets fixed-capacity arrays,
strings and binary fields. The C/C++ code never allocates memory: the
caller provides the buffers, and the generated constants give the
largest size each packet can need. Python allocates every object on the
heap, so it always needs dynamic allocation. See
`Code without dynamic allocation`_.

Introduction
============

tcs-xtce is flight software. It provides libraries in Rust, C/C++ and Python
for working with OMG **XTCE** (XML Telemetric and Command Exchange) in two
layers:

**The XML binding**
   Typed data structures for the whole XTCE 1.3 schema, with code that
   reads and writes XTCE documents. Use it to load, inspect, edit,
   validate and write a mission database.

**The packet codecs**
   ``tcs-xtce`` compiles one mission's XTCE document into typed packet
   structures with encode and decode functions. Use it to read
   telemetry and build commands in the binary formats that the mission
   database describes.

Both layers serialize into *managed buffers*. These are allocated in
advance, reused for every operation, and released when the object that
owns them goes away.

All three languages are generated from the same model and produce
byte-identical output. The test suite checks this on every build.

Exchanging data between programs
--------------------------------

An XTCE document defines data structures once, for every language. The
packet codecs generated from it are interchangeable: a packet that the
Rust code encodes, the C, C++ or Python code decodes to the same values,
and encoding those values again gives the same bytes. The ``to-from``
example (see `Examples`_) runs each of the four languages' writers
against each of their readers.

**Bit packing.** Each field occupies exactly the bits its XTCE encoding
gives: a 4-bit flag takes 4 bits and a 12-bit raw temperature 12 bits,
packed one after another with no padding between them. A language's own
data structures can't do this. A C compiler pads each field to its
alignment, and sending a structure as it is in memory also ties its
layout to one compiler, byte order and word size. In the ``housekeeping``
example, a ``Status`` with its heater on is 7 bytes on the wire, while
the C structure that holds it is 40 bytes, 14 of them padding.

**Agreeing on the definition.** The encoding has no field names, tags or
lengths in it: it is the field values and nothing else. That works
because both programs are built from the same XTCE document, so each
knows what every field is and where it is. To make sure they are, compare
the digests of their documents. Every runtime computes the digest: Rust,
C, C++ and Python (see `xtce-digest`_), and so does the ``xtce-digest``
command. Two documents get the same digest only when they say the same
thing, and so define the same packets, with the same fields, types and
order. (It errs the other way: some edits that change nothing tcs-xtce
generates still change the digest.) A program checks this once, before
any packets, rather than in every message: the sender sends the 16-byte
digest of its document first, and the receiver compares it with the
digest of its own. The ``to-from``, ``variable-array`` and
``housekeeping`` examples do this.

**Compared with self-describing formats.** JSON and Google Protocol
Buffers carry part of the definition in every message, so a reader can
cope with a writer it wasn't built with. They pay for it in every
message. The same ``Status`` is 77 bytes as compact JSON, which repeats
each field's name and writes numbers as text, and 29 bytes as Protocol
Buffers with ``double`` fields, which put a tag before each field and
use whole bytes for every value. Both must also be parsed field by field
to find what the message holds. With a shared, verified XTCE document,
none of that is needed: the 7 bytes are the whole message, and decoding
reads each field from where the document says it is.

.. list-table::
   :header-rows: 1
   :widths: auto

   * - Directory
     - Contents
   * - ``references/``
     - Downloaded XTCE documents (``SpaceSystem.xsd``, ``XTCE.pdf``, ``VERSION``) plus ``xml.xsd`` and ``catalog.xml`` for offline validation
   * - ``generator/xtcegen/``
     - Generates the XML binding from the schema
   * - ``generator/tcs_xtce/``, ``generator/xtcepkt/``
     - The ``tcs-xtce`` command, and the compiler behind it that turns a mission's XTCE document into packet codecs
   * - ``generator/xtce_digest/``
     - The ``xtce-digest`` command, which prints a digest of what an XTCE document means (see `xtce-digest`_); the digest itself is in each runtime
   * - ``rust/``
     - The ``tcs-xtce`` Rust crate (imported as ``tcs_xtce``)
   * - ``cpp/``
     - The C++20 library (``libxtce.a``, ``libxtce.so``) and its C API
   * - ``python/``
     - The ``xtce`` Python package (3.10+, standard library only)
   * - ``tests/``
     - Schema-coverage samples, the demo mission, packet vectors, the cross-language checks, and the list of public XTCE documents for ``make samples-check``
   * - ``docs/``
     - This manual and the design description
   * - ``examples/``
     - Example XTCE files and programs in each language that use the code generated from them


Building and testing
====================

Requirements: ``curl``, ``xmllint``, Python 3.10 or later, a Rust
toolchain with ``rustfmt``, a C11 compiler, and a C++20 compiler.

.. code-block:: sh

   make                 # fetch references if missing, generate, build, test
   make references      # check OMG for the latest XTCE and download it
   make generate        # regenerate all generated code
   make packets         # regenerate the demo mission's packet codecs
   make rust | cpp | python
   make test            # every language's tests and both cross-language checks
   make samples-check   # check against public XTCE documents (downloads them)
   make fmt             # format Rust and Python code
   make fmt-check       # verify it (Rust is also checked by every `make rust`)
   make clean           # remove build products
   make distclean       # also remove the downloaded references

``references/Makefile`` reads the OMG specification page
(https://www.omg.org/spec/XTCE/) to find the latest version instead of
using a fixed one. At the time of writing that is XTCE 1.3, schema
namespace ``http://www.omg.org/spec/XTCE/20250214``.

All Rust code must follow standard ``rustfmt`` formatting. Generated Rust
is passed through ``rustfmt``. ``make rust`` runs ``cargo fmt --check``.
In Claude Code sessions, a hook in ``.claude/settings.json`` checks each
``.rs`` file as it is edited.


Managed buffers
===============

Every buffer the libraries serialize into, or parse from, is a managed
buffer:

* It is preallocated when it is created. The default is 64 KiB for
  documents; packet codecs use the size of the largest packet.
* Clearing a buffer resets its length but keeps its allocation, so later
  operations reuse the same memory.
* A buffer grows only when its contents exceed the current allocation.
* It is owned by a ``Document`` or ``Codec`` and is freed with that
  object.

Rust: ``tcs_xtce::Buffer``
--------------------------

.. list-table::
   :widths: auto

   * - ``Buffer::new()``
     - Preallocate ``DEFAULT_CAPACITY`` (65536) bytes
   * - ``Buffer::with_capacity(n)``
     - Preallocate ``n`` bytes
   * - ``clear()``
     - Empty the buffer, keep the allocation
   * - ``reserve(n)``
     - Ensure room for ``n`` more bytes
   * - ``load(&[u8])``
     - Replace the contents
   * - ``load_from(impl Read) -> io::Result<usize>``
     - Replace the contents with a reader's data
   * - ``resize(n)``
     - Set the length, zero filling new bytes
   * - ``push(u8)``, ``extend_from_slice``, ``push_str``
     - Append
   * - ``as_bytes() -> &[u8]``
     - View the contents
   * - ``as_mut_bytes() -> &mut [u8]``
     - Mutable view of the contents
   * - ``len()``, ``is_empty()``, ``capacity()``
     - Sizes

C++: ``xtce::Buffer`` (``xtce/buffer.hpp``)
-------------------------------------------

.. list-table::
   :widths: auto

   * - ``Buffer(std::size_t capacity = kDefaultCapacity)``
     - Preallocate
   * - ``clear()``, ``reserve(n)``
     - As in Rust
   * - ``load(const void*, n)``, ``load(string_view)``
     - Replace the contents
   * - ``load_from(std::istream&)``
     - Replace the contents with a stream's data
   * - ``resize(n)``
     - Set the length, zero filling new bytes
   * - ``push(char)``, ``append(string_view)``
     - Append
   * - ``data()``, ``mutable_data()``
     - Pointers to the bytes
   * - ``view() -> std::string_view``
     - View the contents
   * - ``size()``, ``empty()``, ``capacity()``
     - Sizes

Python: ``xtce.ManagedBuffer``
------------------------------

.. list-table::
   :widths: auto

   * - ``ManagedBuffer(capacity=DEFAULT_CAPACITY)``
     - Preallocate a ``bytearray``
   * - ``clear()``, ``reserve(n)``
     - As in Rust
   * - ``load(data)``, ``write(data)``
     - Replace or append the contents
   * - ``load_from(stream)``
     - Replace the contents; fills the existing allocation before growing it
   * - ``resize(n)``
     - Set the length, zero filling new bytes
   * - ``view() -> memoryview``
     - Zero-copy view; release it before the buffer has to grow
   * - ``getvalue() -> bytes``
     - Copy of the contents
   * - ``len(buf)``, ``buf.capacity``
     - Sizes


The XML binding
===============

Documents
---------

A ``Document`` holds a root ``SpaceSystemType`` together with an input
buffer and an output buffer.

Rust (``tcs_xtce::Document``):

.. list-table::
   :widths: auto

   * - ``root: SpaceSystemType``
     - The document's data (public field)
   * - ``indent: bool``
     - Pretty-print output (default ``true``)
   * - ``Document::new(root)``
     - Wrap ``root``; both buffers get ``DEFAULT_CAPACITY``
   * - ``Document::with_capacity(root, input, output)``
     - Wrap ``root`` with explicit buffer sizes
   * - ``Document::from_bytes(&[u8]) -> Result<Document>``
     - Parse a document
   * - ``load(&mut self, &[u8]) -> Result<()>``
     - Replace ``root`` by parsing, reusing the input buffer
   * - ``read_from(&mut self, impl Read) -> Result<()>``
     - Same, reading from a stream
   * - ``serialize(&mut self) -> Result<&[u8]>``
     - Write ``root`` into the output buffer; the slice is valid until the document changes
   * - ``write_to(&mut self, impl Write) -> Result<()>``
     - Serialize and write to a stream
   * - ``input()``, ``output() -> &Buffer``
     - The managed buffers

``tcs_xtce::to_xml(&value, tag, &mut buf, indent)`` and
``tcs_xtce::from_xml::<T>(bytes, tag)`` read and write any generated type as
a stand-alone element.

Errors are ``tcs_xtce::Error { message, line, column }``. ``line`` is 1-based,
or 0 when the error is not tied to a position in the input.

C++ (``xtce::Document``, ``#include "xtce/document.hpp"``):

.. list-table::
   :widths: auto

   * - ``Document(SpaceSystemType root = {}, size_t in = kDefaultCapacity, size_t out = kDefaultCapacity)``
     - Wrap ``root`` and preallocate both buffers
   * - ``root``, ``indent``
     - Public members, as in Rust
   * - ``static Document from_bytes(std::string_view)``
     - Parse a document
   * - ``load(std::string_view)``, ``read_from(std::istream&)``
     - Replace ``root`` by parsing
   * - ``std::string_view serialize()``
     - View of the output buffer
   * - ``write_to(std::ostream&)``
     - Serialize to a stream
   * - ``input()``, ``output()``
     - The managed buffers

Errors are thrown as ``xtce::Error``, with ``message()``, ``line()`` and
``column()``.

C (``#include "xtce/xtce.h"``, link with ``libxtce`` and the C++
runtime):

.. list-table::
   :widths: auto

   * - ``xtce_document* xtce_document_new(size_t in, size_t out)``
     - Create a document (0 means the default size)
   * - ``void xtce_document_free(xtce_document*)``
     - Free the document and its buffers
   * - ``int xtce_document_load(doc, const void* data, size_t len)``
     - Parse; returns ``XTCE_OK`` or ``XTCE_ERROR``
   * - ``int xtce_document_serialize(doc, const uint8_t** out, size_t* n)``
     - Serialize; ``*out`` points into the output buffer
   * - ``xtce_document_set_indent(doc, int)``
     - Pretty-print on or off
   * - ``xtce_document_name``, ``xtce_document_set_name``
     - Root ``SpaceSystem`` name
   * - ``xtce_document_error``, ``xtce_document_error_line``
     - Last error message and line
   * - ``xtce_document_input_capacity``, ``xtce_document_output_capacity``
     - Buffer sizes
   * - ``xtce_schema_version()``, ``xtce_namespace()``
     - Schema information
   * - ``xtce::Document* xtce_document_cxx(doc)``
     - The C++ object, for typed access (C++ only)

Python (``xtce.Document``):

.. list-table::
   :widths: auto

   * - ``Document(root=None, *, input_capacity=..., output_capacity=..., indent=True)``
     - Wrap ``root`` (an empty ``SpaceSystemType`` by default) and preallocate both buffers
   * - ``root``, ``indent``
     - Attributes
   * - ``Document.from_bytes(data)``, ``Document.from_file(path)``
     - Parse a document
   * - ``load(data)``, ``read_from(stream)``
     - Replace ``root`` by parsing
   * - ``serialize() -> memoryview``
     - View of the output buffer; use it in a ``with`` block
   * - ``to_bytes() -> bytes``, ``write_to(stream)``
     - Serialize to a copy or a stream
   * - ``input``, ``output``
     - The managed buffers (properties)
   * - ``close()``, ``with Document(...) as doc:``
     - Release the buffers early

``xtce.to_xml`` and ``xtce.from_xml`` work like their Rust
counterparts. Errors are raised as ``xtce.XtceError`` (a ``ValueError``)
with ``message`` and ``line`` attributes.

Generated data structures
-------------------------

Every complex type in ``SpaceSystem.xsd`` becomes a record. Type names
match the schema (``SpaceSystemType``, ``IntegerParameterType``, ...).
Field names are the snake_case form of the XML names. Inherited content
(``complexContent`` extensions) is copied into the derived record.

.. list-table::
   :header-rows: 1
   :widths: auto

   * - XSD
     - Rust
     - C++
     - Python
   * - complex type
     - ``struct``
     - ``struct``
     - ``@dataclass``
   * - ``<choice>``
     - ``enum``
     - ``struct`` + ``std::variant``
     - dataclass with ``name`` and ``value``
   * - enumeration
     - ``enum``
     - ``enum class``
     - ``str`` ``Enum``
   * - optional element or attribute
     - ``Option<T>``
     - ``std::optional<T>``
     - ``Optional[T]``
   * - repeated element
     - ``Vec<T>``
     - ``std::vector<T>``
     - ``list[T]``
   * - recursive field
     - ``Box<T>``
     - ``xtce::Box<T>``
     - plain reference
   * - string, dates, durations, hexBinary, unions
     - ``String``
     - ``std::string``
     - ``str``
   * - boolean
     - ``bool``
     - ``bool``
     - ``bool``
   * - long, integer / int / unsignedShort
     - ``i64`` / ``i32`` / ``u16``
     - ``int64_t`` / ``int32_t`` / ``uint16_t``
     - ``int``
   * - double
     - ``f64``
     - ``double``
     - ``float``

Choices:

* **Rust**: an ``enum`` with one variant per alternative, for example
  ``EntryListChoice::ParameterRefEntry(ParameterRefEntryType)``.
* **C++**: a struct holding ``std::variant value``, with ``kind()``,
  ``xml_name()``, ``make_X(v)`` constructors and ``as_X()`` accessors
  that return ``nullptr`` for another alternative.
* **Python**: a dataclass with ``name`` (the element name) and ``value``,
  plus class-method constructors such as
  ``EntryListChoice.parameter_ref_entry(v)``.

Every record and choice has a default value that is schema-valid where
the schema allows. All three languages support equality comparison.
Each record reads and writes itself as XML (``XmlElement`` in Rust,
``write_xml``/``read_xml`` in C++, ``to_xml``/``from_xml`` in Python).
Usually you don't call these directly.

Example (Rust):

.. code-block:: rust

   use tcs_xtce::{Document, SpaceSystemType};

   let mut doc = Document::from_bytes(&std::fs::read("sat.xml")?)?;
   println!("{}", doc.root.name);
   doc.root.short_description = Some("updated".into());
   let bytes: &[u8] = doc.serialize()?;   // view of the managed buffer

Behaviour
---------

* The binding follows XTCE 1.3 strictly. Documents written for older
  versions are rejected wherever the schema changed, and errors give the
  line number.
* Elements are matched by local name; the namespace URI is not checked.
* Pattern, range and ``xs:key`` constraints are not checked. Use
  ``xmllint`` with ``references/catalog.xml`` for full validation.
* Schema defaults are not filled in, so documents round-trip as written.
* Comments, processing instructions and attributes in other namespaces
  (such as ``xsi:schemaLocation``) are not preserved.
* Doubles are written in their shortest round-trip form (Python ``repr``
  style), identically in every language.


Packet codecs
=============

Overview
--------

``tcs-xtce`` reads a mission's XTCE document, or the documents it is split
across, and generates, for one language:

* a **struct per concrete container or meta-command** with one field
  per value the user supplies;
* **decode** and **encode** functions for each struct;
* a **dispatch type** (a *kind*) per inheritance tree, which decodes a
  packet into whichever concrete container or command it matches;
* an **enumeration type** per enumerated parameter or argument type in
  use.

The generated code uses a small runtime: ``tcs_xtce::packet`` in Rust,
``xtce/packet.h`` in C and C++, and ``xtce.packet`` in Python.

Running the compiler
--------------------

.. code-block:: sh

   cd generator
   python3 -m tcs_xtce INPUT... OUTPUT --lang LANG

   python3 -m tcs_xtce demo.xml demo.rs --lang rust
   python3 -m tcs_xtce demo.xml demo.h  --lang c
   python3 -m tcs_xtce demo.xml demo.py --lang python
   python3 -m tcs_xtce dt.xml pus.xml pus17.xml pus.rs --lang rust

Each run writes one language. Run it once per language you need.

.. list-table::
   :widths: auto

   * - ``INPUT...``
     - The mission's XTCE documents, read with the ``xtce`` Python package:
       usually one, or several when the mission is split across documents
       (see `Missions in several documents`_)
   * - ``OUTPUT``
     - The file to write, always the last name; ``-`` writes to standard
       output. It may be left out with ``--size-report`` and one
       ``INPUT``. A name ending in ``.xml`` is refused, so that leaving
       ``OUTPUT`` out can't overwrite the last document
   * - ``--lang LANG``
     - Required, except with ``--size-report``. ``rust`` writes a Rust module, formatted with ``rustfmt``
       (which must be installed). ``python`` writes a Python module.
       ``c`` and ``c++`` write the same file: a header that compiles both
       as C11 and as C++.
   * - ``--prefix P``
     - C/C++ only: prefix of every name the header defines (default: the
       root SpaceSystem name, of the last document when there are several,
       in snake_case followed by ``_``, such as ``demo_``)
   * - ``--skip-unsupported``
     - Leave out containers and meta-commands that use unsupported XTCE features, with a warning, instead of failing
   * - ``--dialect yamcs``
     - Read the documents as the ground system named does where XTCE
       leaves a meaning open, unless they say otherwise (see Dialects)
   * - ``--size-report``
     - Write a size report instead of code (see `Size report`_), to
       ``OUTPUT`` or standard output
   * - ``--dynamic-allocations true|false``
     - Whether the generated code may allocate heap memory at any time
       (``true``, the default) or only during initialization (``false``).
       See `Code without dynamic allocation`_
   * - ``--source-name NAME``
     - Mission file name recorded in the generated headers (default: the
       ``INPUT`` file names)

By default the compiler stops if any container or meta-command uses an
unsupported construct, and lists every such construct with its cause.

Missions in several documents
-----------------------------

Large missions often split their XTCE across documents: one of data types,
one of the packet header, and one per service or application, each building
on the others. Yamcs loads such a mission from a list of documents, and
``tcs-xtce`` reads it the same way. The root SpaceSystem of each document
sits at the top of one tree, so a reference in one document can name a
definition in another (the ``split-mission`` example, under `Examples`_,
is one such mission):

.. code-block:: xml

   <!-- dt.xml -->
   <SpaceSystem name="dt"> ... <IntegerParameterType name="uint16"> ...

   <!-- pus.xml -->
   <SpaceSystem name="PUS"> ... <SequenceContainer name="pus-tm"> ...

   <!-- pus17.xml -->
   <SpaceSystem name="PUS17"> ...
     <SequenceContainer name="are-you-alive-report">
       <BaseContainer containerRef="/PUS/pus-tm"> ...
       <ParameterRefEntry parameterRef="..."/> <!-- of type /dt/uint16 -->

* An absolute reference, such as ``/PUS/pus-tm``, starts with the name of a
  document's root SpaceSystem.
* A relative reference, such as ``cfs-ccsds/cfs-cmd``, is looked up from
  the SpaceSystem it appears in and then from each enclosing one, up to
  the top of the tree, as an unqualified name is. A reference that starts
  with ``.`` or ``..`` is relative to where it appears only.
* Two documents can't have root SpaceSystems with the same name.

Code is generated for the packets of every document, in one file. List the
documents in the order they build on each other (types, headers, then
services), since the mission, the default C ``--prefix`` and the generated
module's description are named for the last; the order changes nothing
else. Two programs agree on the mission's packets when they used the same
set of documents, which the digest of the set checks (see `xtce-digest`_).

Code without dynamic allocation
-------------------------------

Flight software often may not allocate heap memory once it has started.
``--dynamic-allocations false`` generates code for those programs: it
allocates nothing after initialization.

Rust
   Arrays, strings and binary fields become ``FixedVec<T, N>``,
   ``FixedString<N>`` and ``FixedBytes<N>`` (from ``tcs_xtce::fixed``, also
   exported by ``tcs_xtce::packet``). These hold up to ``N`` elements inline
   and dereference to ``[T]``, ``str`` and ``[u8]``. Build them with
   ``try_from`` or ``push``, which fail instead of growing.
   ``Packet::decode``, ``encode`` and ``Display`` then never allocate.
   During initialization, create a ``Codec`` with
   ``Codec::for_packet::<P>()``: its output buffer already fits the
   largest packet, so encoding never grows it. Decode with
   ``P::decode(&bytes)`` rather than ``Codec::load``, which grows the
   input buffer for input longer than it, and avoid
   ``Packet::to_bytes``, which returns a new ``Vec``.
C and C++
   The generated code never allocates, so both values produce the same
   header.
Python
   Every Python object lives on the heap, so ``false`` is rejected for
   ``--lang python``.

``tests/packet_check.sh`` runs the demo mission's codecs generated both
ways and checks that they give identical results. The check fails if the
code generated with ``false`` allocates while decoding, encoding or
describing a packet.

Size report
-----------

``python3 -m tcs_xtce INPUT... --size-report`` writes no code. For each
packet it lists every field with the bits it takes on the wire and the
bits the XTCE document says its values need, to show where the mission
database could make packets smaller. It changes nothing: the codecs
always use the encodings as written.

A field's need comes only from the document:

* an integer needs the fewest bits that hold its ``ValidRange`` (for an
  argument, every range in its ``ValidRangeSet``) in the same encoding,
  or as unsigned when the range has no negative values;
* an enumeration needs the bits for its smallest and largest listed
  values;
* a boolean needs one bit;
* a BCD field needs 8 bits (4 if packed) per decimal digit of its
  largest value;
* a field with a byte order list is rounded up to whole bytes, which its
  encoding requires.

Integers without a ``ValidRange``, floats and binary fields have no
stated need and show ``-``. The ``sizeInBits`` and ``signed`` attributes
of engineering types are not used: XTCE defines them only as hints.
Fixed-size strings are flagged, because shorter strings are padded to
the full size. Bits that ``LocationInContainerInBits`` skips over are
counted separately.

::

   /Sat/Status (container Status): at most 196 bits, 25 bytes
     field  type         bits  needs  saves
     C      /Sat/Count     32     10     22  ValidRange 0..1000
     L      /Sat/Level     16      7      9  ValidRange 0..100, as unsigned
     LE     /Sat/Little    32      9     23  ValidRange 0..300
     O      /Sat/Open      16      -         no ValidRange
     M      /Sat/Mode       8      3      5  values 0..5
     F      /Sat/Flag       8      1      7  boolean
     N      /Sat/Name      64      -         fixed size: strings shorter than 8 bytes are padded
     ...
     right-sized fields save 74 bits: at most 122 bits, 16 bytes

``=`` after a field name marks a constant: a value fixed by a
restriction criterion or argument assignment, or a ``FixedValueEntry``.
A summary at the end names the packets that would be whole bytes
smaller, and the integer types that have no ``ValidRange`` and so could
not be checked. Containers and meta-commands with unsupported
constructs are listed rather than analysed. Savings for packets whose
entries are placed with ``LocationInContainerInBits`` assume the layout
is repacked.

Using the generated code:

* **Rust**: add the file as a module (``mod demo;``) in a crate that
  depends on ``xtce``.
* **C and C++**: include the header with ``cpp/include`` on the include
  path. The header and its runtime ``xtce/packet.h`` are self-contained
  and need no library. In C, link with ``-lm``.
* **Python**: import the module with the ``xtce`` package on the path.

``make packets`` regenerates the demo mission
(``tests/mission/demo.xml``) into ``rust/tests/mission/demo.rs``,
``cpp/tests/mission/demo.h`` and ``python/tests/mission/demo.py``.

Generated packet types
----------------------

Names are derived from XTCE names, and are the same in every language.
In C and C++, every name the header defines also starts with the
prefix (``demo_Housekeeping``, ``demo_Housekeeping_decode``), because C
has no namespaces.

* Structs and enumeration types use CamelCase (``CCSDSPacket`` stays
  ``CCSDSPacket``, ``ccsds_header`` becomes ``CcsdsHeader``).
* Fields use snake_case. A name reserved in any of the three languages,
  or by the generated code (such as ``data``, ``type`` or ``bytes``),
  gets a trailing ``_``.
* Clashes are resolved by qualifying with the SpaceSystem path, then
  with a numeric suffix.

**Packet structs.** There is one struct per non-abstract
``SequenceContainer`` and per non-abstract ``MetaCommand``. Its fields
are, in packet order:

* every value the container's inheritance chain encodes, base container
  first, with included containers (``ContainerRefEntry``) flattened in;
* **except** values fixed by an ``==`` restriction criterion or argument
  assignment along the chain. These are constants: they are written
  automatically when encoding and checked when decoding. For example,
  the demo's ``Housekeeping`` has no ``apid`` field because its
  ``BaseContainer`` requires ``APID == 100``.

Command structs hold the command's arguments, including those inherited
from base meta-commands that are not assigned. They also hold any
parameters the command container includes. ``FixedValueEntry`` bits are
written automatically and ignored when decoding.

Field types:

.. list-table::
   :header-rows: 1
   :widths: auto

   * - Engineering type
     - Rust
     - C/C++
     - Python
   * - integer (by encoded size and sign; with a calibrator, by the
       type's ``sizeInBits`` and ``signed``)
     - ``u8`` … ``u128``, ``i8`` … ``i128``; over 128 bits
       ``pk::Uint<N>`` or ``pk::Int<N>`` (bnum's, of ``N`` bytes)
     - ``uint8_t`` … ``int64_t``; over 64 bits ``demo_uintB`` or
       ``demo_intB``, ``B`` bits in ``uint8_t bytes[B / 8]`` (see `Integers
       of any size`_)
     - ``int``
   * - float (with any encoding), time
     - ``f64``
     - ``double``
     - ``float``
   * - enumerated
     - generated type
     - generated ``int64_t`` typedef
     - generated ``IntEnum``
   * - boolean
     - ``bool``
     - ``bool``
     - ``bool``
   * - string
     - ``String`` (``FixedString<N>``)
     - ``demo_strN``: ``size_t len`` and ``char data[N + 1]``
     - ``str``
   * - binary
     - ``Vec<u8>`` (``FixedBytes<N>``)
     - ``demo_bytesN``: ``uint8_t data[N]``
     - ``bytes``
   * - binary whose size another field gives
     - ``Vec<u8>`` (``FixedBytes<N>``)
     - ``demo_varbytesN``: ``size_t len`` and ``uint8_t data[N]``
     - ``bytes``
   * - array, or entry with a ``RepeatEntry``, of element type ``T``
     - ``Vec<T>`` (``FixedVec<T, N>``)
     - ``demo_<T>_listN``: ``size_t len`` and ``T data[N]``
     - ``list[T]``
   * - aggregate
     - generated struct
     - generated struct
     - generated dataclass
   * - with an ``IncludeCondition``
     - ``Option<T>``
     - ``bool has_<field>`` before the field
     - ``Optional[T]``

The Rust types in parentheses are those generated with
``--dynamic-allocations false``. In them, and in C/C++, ``N`` is the most
bytes (or elements) the field's encoding can hold, so fields need no
allocation. A string's ``data`` always has
room for a terminating NUL, which decoding writes. Encoding uses ``len``,
not the NUL. In the names of list types, ``<T>`` is the element's C type
without the prefix and any ``_t`` (``demo_int16_list6``,
``demo_str8_list4``).

**Aggregates.** An ``AggregateParameterType`` or
``AggregateArgumentType`` becomes a struct named like the type
(``PositionType``, ``demo_PositionType`` in C), with one field per
member, named like packet fields (snake_case, with a trailing ``_`` for
reserved names). Members are encoded one after another in the order of
the ``MemberList`` and may have any supported type, including arrays
and other aggregates. In Rust the struct derives ``Debug``, ``Clone``,
``PartialEq`` and ``Default``. In Python it is a dataclass, and a field
of an aggregate type defaults to a new instance. In C, each type is
declared after the types it contains. Errors in a member name the
packet field that holds it.

**Repeated containers.** A ``ContainerRefEntry`` with a ``RepeatEntry``
becomes one field: a list of a struct that holds one copy of the
included container's entries, named like the container (or
``<Name>Item`` when a packet already has that name). In the demo,
``Log`` has ``log_record``, a list of ``LogRecord {channel, reading}``.
The included container's entries can have locations, but can't have an
``IncludeCondition`` or overlap. An entry can take its size or count
from an earlier integer entry of the same copy, as the demo's
``RecordElement`` takes ``RecData``'s from ``RecLen``, but not from a
value outside the copy. Gaps that locations leave are written as zeros. A
``containerStart`` location is relative to the start of each copy.

**Container sizes.** A ``BinaryEncoding`` with a fixed ``SizeInBits`` on
a container (other than one with a ``BaseContainer``) makes it take
exactly that many bits. The bits its entries leave are padding:
encoding writes zeros, and decoding ignores their value but fails with
``Truncated`` if they are missing. Its entries must have a fixed size
and position, and must fit.

**Arrays and repeated entries.** An ``ArrayParameterType`` or
``ArrayArgumentType``, and a parameter or argument entry with a
``RepeatEntry``, is a list of its elements in the order they are
encoded. An array with several dimensions is flattened: its elements
are in the order of the ``DimensionList``, without further structure.
A ``RepeatEntry`` ``Offset`` puts that many bits between the elements
(not after the last one). Encoding fails with ``BadSize`` unless the list
has exactly the number of elements its type gives, including for a
fixed count, which decoding always fills.

An array entry with a ``DimensionList`` carries a subset of the array:
the elements from each dimension's ``StartingIndex`` to its
``EndingIndex``, in the type's index space, last dimension fastest. Its
field holds just those elements. In the demo, ``MatrixMiddle``'s
``Matrix`` is rows 1..2 and columns 1..2 of a 3×4 array, so 4 elements,
and ``Fill``'s ``Slots`` are elements 2 to ``Last`` of 6.

**Sizes and counts from other fields.** XTCE can take a size or count
from another value of the packet with a ``DynamicValue``: an array
dimension's ``EndingIndex``, a ``RepeatEntry`` ``Count``, a binary
encoding's ``SizeInBits``, or the ``DynamicValue`` of a variable string
(the bits the string occupies; its content ends inside them at the
termination character or after its leading size, like a fixed-size
string). The value is ``slope * x + intercept`` (its
``LinearAdjustment``), where ``x`` is the referenced integer parameter
or argument. That value must be decoded earlier in the same packet,
outside any ``IncludeCondition``.

A ``DiscreteLookupList`` can stand in for the ``DynamicValue`` (or, in
a binary or string encoding, for its ``DynamicValue``): ``x`` is then
the ``value`` of the first ``DiscreteLookup`` whose criteria hold, or
``defaultValue`` if none do. Its
comparisons follow the rules of restriction criteria, and in commands
may name arguments (``ArgumentInstanceRef``). In the demo, ``Frame``'s
``FrameBody`` is 2 bytes when ``FrameKind`` is 1, 4 when it is 2 and
empty otherwise, and ``Tune``'s ``Setting`` depends on its ``Mode``.

The referenced value may be a member of an aggregate parameter or
argument, named with XTCE's dot syntax: in the demo, ``Route``'s
``Stops`` are counted by ``RouteHdr.Count`` and its ``Detour`` is
included when ``RouteHdr.Kind`` is 2, and ``Plan``'s ``Points`` are
counted by its argument ``Leg.Count``. Criteria use the same syntax.

Inside an aggregate type, a reference may instead name an earlier member
of the aggregate, as Yamcs reads it. Each value of the aggregate then
uses its own member: in the demo, each ``BatchType`` in ``Batches`` has
a ``Size`` that counts its ``Values``. The member's name wins over a
parameter or argument with the same name, and other references from
inside an aggregate are not supported.

When the referenced value counts an array or gives the size of a binary
value, encoding computes it from that data's length, and it is not a
field of the generated struct. Criteria, locations and lookups may read
it too: encoding computes it first, and they read the computed value. In the demo,
``SampleCount``, ``PayloadSize``, ``TargetCount``, ``LogCount``,
``BatchCount``, ``WordCount`` and ``Fill``'s ``Last`` are computed this
way: set ``Readings`` to three values and ``SampleCount`` is written as
3. In the demo, ``Tally``'s ``TallyCount`` counts ``Tallies`` and also
decides whether ``TallyMore`` is included, and it is computed all the
same. The value must be an integer without a calibrator, outside any
``IncludeCondition``, and it must give the size of a whole field (not a
member, element or part of one) that is also outside any
``IncludeCondition``. Encoding fails with ``BadSize`` if no value of the
field gives the data's length, or if that length is above the field's
bound (below), and with ``OutOfRange`` if the value doesn't fit the
field.

Otherwise the referenced value stays an ordinary field: when it also
sizes a string, gives the size of a member or element, or sizes a value
that may be absent, or when it is an aggregate member such as
``RouteHdr.Count``. Encoding then computes the
size or count from the field and fails with ``BadSize`` if the data
doesn't match: set both. When decoding, a size or count that is
negative, not whole, not a whole number of bytes (for sizes in bits of
strings and binary), or above the field's bound fails with
``BadSize``.

The bound is the largest value the referenced field's ``ValidRange``
allows, or, without one, its encoding, or the largest of a
``DiscreteLookupList``'s values. It is limited to what fits in the
mission's maximum field size on the wire, 64 KiB unless the document
says otherwise, which keeps C structs bounded. In C this bound is
``N`` in the field's type, so give count fields a ``ValidRange``: the
demo's ``SampleCount`` (``0..6``) makes ``Readings`` a
``demo_int16_list6``, where an 8-bit count without a range would allow
255 elements, and a 32-bit one 32768 of them.

**The maximum field size.** An ``AncillaryData`` named
``tcs-xtce.max-field-bytes`` on a document's root ``SpaceSystem`` sets
it, in bytes, from 1 to 4294967295 (a 32-bit size):

.. code-block:: xml

   <SpaceSystem name="Rover">
     <AncillaryDataSet>
       <AncillaryData name="tcs-xtce.max-field-bytes">1048576</AncillaryData>
     </AncillaryDataSet>
     ...

The setting is in the document, so a sender and a receiver that check
the document's digest also agree on it. When a mission is split over
several documents, every root that gives it must give the same value;
on any other ``SpaceSystem`` it is an error. It bounds every value
whose size, count or location another value gives, and values that
take the rest of the packet. A packet too large to count its bits in a
32-bit ``size_t`` (over 512 MiB) still builds on 64-bit targets, but
its generated C and Rust refuse to compile on 32-bit ones.

**Packet length fields.** XTCE has no way to say that a field holds the
length of its packet, so tcs-xtce reads it from an ``AncillaryData``
named ``tcs-xtce.length`` on the parameter or argument. Its value is an
integer that is added to the number of bytes after the field. The CCSDS
packet length, the bytes after the 6-byte primary header minus 1, is:

.. code-block:: xml

   <Parameter name="PacketLength" parameterTypeRef="U16">
     <AncillaryDataSet>
       <AncillaryData name="tcs-xtce.length">-1</AncillaryData>
     </AncillaryDataSet>
   </Parameter>

Other tools ignore the ``AncillaryData``, so the document stays valid
XTCE. A length field is not a field of the generated struct:

* encoding writes the packet, then fills in the length from its size;
  it fails with ``OutOfRange`` if the length doesn't fit the field;
* decoding ends the packet where the length says. It fails with
  ``Truncated`` if the data is shorter, and with ``BadLength`` if the
  packet's entries end before that. Bytes after the end are ignored, so
  packets that follow one another in a buffer can be decoded one at a
  time.

This matches cFS, whose ``CFE_MSG_SetSize`` sets the CCSDS length from a
message's size and whose applications reject commands of the wrong
length. Decoding a packet as its base container fails with ``BadLength``,
as the base's entries end before the packet does; decode the ``Kind``
instead. A length field must be an unsigned integer without a
calibrator, and at most one per packet. It can't have an
``IncludeCondition`` or ``RepeatEntry``, be compared by a criterion or
set by an argument assignment, give a size or count, or be inside a
repeated container.

A length field inside an included container (a ``ContainerRefEntry``'s,
in telemetry or a command) measures that container instead: the bytes
after it to the container's end, plus its value. Encoding fills it in
where the container ends, and decoding fails with ``BadLength`` there if
the container doesn't end where the field says. So a command can carry
a whole other command, header and all: the demo's ``Forward`` includes
``SetModeContainer``, whose own ``PacketLength`` counts only the
``SetMode`` command.

Integer field widths come from the *encoding*: an 11-bit unsigned field
is a ``u16``, and a 12-bit sign-magnitude field is an ``i16``. Floats
are always 64-bit in memory. 32-bit encodings round when written.
Time types are in seconds, as described below.

**Enumeration types** hold the raw integer, so values the enumeration
doesn't list survive decoding and re-encoding:

* **Rust**: ``pub struct ModeType(pub i64)`` with associated constants
  (``ModeType::NOMINAL``), ``label() -> Option<&'static str>`` and
  ``Display``.
* **C/C++**: ``typedef int64_t demo_ModeType`` with a macro per label
  (``demo_ModeType_NOMINAL``) and ``const char* demo_ModeType_label(demo_ModeType)``,
  which returns ``NULL`` for an unlisted value.
* **Python**: ``class ModeType(enum.IntEnum)`` with a ``label``
  property. ``xtce.packet.enum_value(ModeType, 20)`` builds a value
  even when it isn't listed.

A range entry (``maxValue``) gives one label to all values in the range.
The constant uses the range's first value.

**Kinds.** An inheritance tree whose root has descendants gets a dispatch
type named ``<Root>Kind``. Its members are every concrete container (or
command) in the tree, in document order:

* **Rust**: ``enum CCSDSPacketKind { Housekeeping(Housekeeping), ... }``,
  with ``From<Member>`` for each member.
* **C/C++**: ``struct demo_CCSDSPacketKind { demo_CCSDSPacketKind_tag tag; union { demo_Housekeeping Housekeeping; ... } u; }``,
  where ``tag`` is ``demo_CCSDSPacketKind_Housekeeping_tag``, and so on.
* **Python**: ``class CCSDSPacketKind`` with ``MEMBERS``. Its ``decode``
  returns an instance of the matching member class.

Methods
-------

Every packet struct and kind has the members below. Rust implements the
``tcs_xtce::packet::Packet`` trait. Python classes derive from
``xtce.packet.Packet`` (structs) or ``xtce.packet.PacketKind`` (kinds).
In C/C++ they are macros and ``static inline`` functions named after the
type, shown here for ``T`` = ``demo_Housekeeping``.

.. list-table::
   :header-rows: 1
   :widths: auto

   * - Rust
     - C/C++
     - Python
     - Meaning
   * - ``NAME``
     - ``T_NAME``
     - ``NAME``
     - Qualified XTCE name (the root, for kinds)
   * - ``MAX_BYTES``
     - ``T_MAX_BYTES``
     - ``MAX_BYTES``
     - Upper bound on the encoded size
   * - ``Default``
     - ``T_init(T*)``
     - constructor
     - A packet with every field zero
   * - ``decode(&[u8]) -> Result<Self>``
     - ``int T_decode(T* out, const uint8_t* data, size_t len, xtce_pk_error* err)``
     - ``decode(data)`` (class or static method)
     - Decode a packet that starts at byte 0; trailing bytes are ignored
   * - ``encode_into(&mut BitWriter)``
     - ``int T_encode_into(const T*, xtce_pk_writer*)``
     - ``encode_into(w)``
     - Write with a bit writer
   * - ``encode(&mut Buffer)``
     - ``int T_encode(const T*, uint8_t* buf, size_t cap, size_t* len, xtce_pk_error* err)``
     - ``encode(buf)``
     - Replace a buffer's contents with the packet
   * - ``to_bytes()``
     - —
     - ``to_bytes()``
     - Encode into a new byte vector
   * - ``Display``
     - ``size_t T_describe(const T*, char* buf, size_t cap)``
     - ``describe()``, ``str()``
     - Canonical text (below)
   * - ``PartialEq``, ``Clone``
     - assignment; compare ``T_describe`` output
     - dataclass ``==``
     - Comparison
   * - ``alarms() -> pk::Alarms<N>``, ``ALARMS``
     - ``size_t T_alarms(const T*, xtce_pk_alarm* out)``, ``T_ALARMS``
     - ``alarms()``
     - Each alarmed field's alarm level (see Alarms)
   * - ``monitor(&mut AlarmMonitor, t) -> pk::Alarms<N>``
     - ``size_t T_monitor(const T*, P_AlarmMonitor*, double t, xtce_pk_alarm* out)``
     - ``monitor(m, t)``
     - The same, counting earlier samples
   * - ``algorithms() -> pk::Outputs<N>``, ``ALGORITHMS``
     - ``size_t T_algorithms(const T*, xtce_pk_output* out)``, ``T_ALGORITHMS``
     - ``algorithms()``
     - The output of each MathAlgorithm it triggers

The C/C++ functions return ``0`` on success, or an ``XTCE_PK_*`` error
code after filling in ``*err`` (``err`` may be ``NULL``). ``T_describe``
works like ``snprintf``: it returns the full length of the text and
writes as much as fits in ``cap`` bytes, NUL included.

Decoding a single container (``Housekeeping::decode``) decodes its whole
inheritance chain and checks every restriction criterion along it.
Decoding a kind reads the root's entries, then tries each child in
document order and takes the first whose criteria match. It repeats
this down the tree and returns the deepest match. If the deepest match
is abstract, decoding fails with ``NoMatch``.

Encoding checks every criterion that involves a field (for example the
demo's ``Temperature < 40`` for ``ScienceHousekeeping``). It writes the
fixed values and fails with ``Mismatch`` if a criterion doesn't hold.

The codec
---------

A ``Codec`` owns one input and one output buffer, sized for the largest
packet of its type. It reuses them for every packet until it is dropped.

Rust:

.. code-block:: rust

   use tcs_xtce::packet::{Codec, Packet};

   let mut codec = Codec::for_packet::<demo::CCSDSPacketKind>();
   codec.load(&frame_bytes);                       // copy into the input buffer
   match codec.decode::<demo::CCSDSPacketKind>()? {
       demo::CCSDSPacketKind::Housekeeping(hk) => println!("{hk}"),
       other => println!("{other}"),
   }
   let cmd = demo::SetMode { seq_count: 5, mode: demo::ModeArgType::SCIENCE };
   let wire: &[u8] = codec.encode(&cmd)?;           // view of the output buffer

Rust ``Codec`` methods: ``with_capacity(n)``, ``for_packet::<P>()``,
``load(&[u8])``, ``read_from(impl Read)``, ``input() -> &[u8]``,
``decode::<P>() -> Result<P>``, ``encode(&P) -> Result<&[u8]>``,
``input_capacity()``, ``output_capacity()``.

C and C++ have no codec object. The caller owns the buffers and
nothing is allocated. Size the buffers with the ``*_MAX_BYTES``
constants and reuse them for every packet. The same code compiles as C
and as C++:

.. code-block:: c

   #include "demo.h"

   static uint8_t out[demo_CCSDSCommandKind_MAX_BYTES];

   demo_CCSDSPacketKind packet;
   xtce_pk_error err;
   if (demo_CCSDSPacketKind_decode(&packet, frame, frame_len, &err) != 0) {
           char msg[128];
           xtce_pk_error_message(&err, msg, sizeof msg);
           fprintf(stderr, "%s: %s\n", xtce_pk_error_kind(err.code), msg);
   } else if (packet.tag == demo_CCSDSPacketKind_Housekeeping_tag) {
           printf("%f\n", packet.u.Housekeeping.temperature);
   }

   demo_SetMode cmd;
   size_t len;
   demo_SetMode_init(&cmd);
   cmd.seq_count = 5;
   cmd.mode = demo_ModeArgType_SCIENCE;
   if (demo_SetMode_encode(&cmd, out, sizeof out, &len, &err) == 0) {
           send(sock, out, len, 0);
   }

Python (``xtce.packet.Codec``):

.. code-block:: python

   from xtce.packet import Codec
   import demo

   codec = Codec.for_packet(demo.CCSDSPacketKind)
   codec.load(frame)
   packet = codec.decode(demo.CCSDSPacketKind)     # a demo.Housekeeping, ...
   with codec.encode(demo.SetMode(seq_count=5, mode=demo.ModeArgType.SCIENCE)) as wire:
       sock.send(wire)                              # memoryview of the output buffer

``Codec(capacity=4096)``: ``Codec.for_packet(cls)``, ``load(data)``,
``read_from(stream)``, ``decode(cls)``, ``encode(packet) -> memoryview``,
and the properties ``input``, ``output``, ``input_capacity`` and
``output_capacity``.

Errors
------

Errors are ``tcs_xtce::packet::PacketError`` (a Rust enum) and
``xtce.packet.PacketError`` (a Python ``ValueError``). In C and C++
they are the code a function returns, plus the ``xtce_pk_error`` it
fills in: ``code``, the ``field`` involved and, for ``Mismatch``, the
criterion in ``detail``. Each error carries a *kind* that is the same in
every language: ``kind()`` in Rust, ``.kind`` in Python, and
``xtce_pk_error_kind(code)`` in C/C++. ``xtce_pk_error_message()``
formats the message.

.. list-table::
   :header-rows: 1
   :widths: auto

   * - Kind
     - Meaning
   * - ``Truncated``
     - The packet ends before a field does
   * - ``BadPosition``
     - A location offset places a field before the start of the packet
   * - ``NoMatch``
     - No concrete container or command in the tree matches
   * - ``Mismatch``
     - A restriction criterion or argument assignment doesn't hold
   * - ``OutOfRange``
     - A value doesn't fit its encoding or is outside its type's
       ``ValidRange`` (or an argument's ``ValidRangeSet``), or a binary
       value is too short
   * - ``InvalidString``
     - A string isn't valid in its character set, or contains its
       termination character
   * - ``TooLong``
     - A string or binary value is longer than its encoding allows
   * - ``Calibration``
     - A value is outside the range of a spline calibrator without extrapolation
   * - ``NotInvertible``
     - Encoding a field whose calibrator can't be inverted
   * - ``Missing``
     - An optional field is absent although its include condition holds
   * - ``BadSize``
     - A size or count that another field gives is invalid or above its
       bound, or an array or binary value doesn't have it
   * - ``BadLength``
     - The packet's entries end before the end its packet length field gives
   * - ``InvalidValue``
     - A field's bits are not a value of its encoding: a BCD digit above 9,
       a DEC reserved operand, or a digit above 9 for a check digit
   * - ``BadChecksum``
     - A checksum field (see "Error detection" under `Supported XTCE`_)
       doesn't match the data it checks
   * - ``NoAlgorithm``
     - No function is registered for a custom algorithm the packet needs
       (see "Custom algorithms" under `Supported XTCE`_)
   * - ``NoSpace``
     - C/C++ only: the output buffer is smaller than the packet

Canonical text
--------------

``describe()`` / ``Display`` produce text that is identical in every
language. The cross-language tests compare it directly.

::

   Housekeeping{version=0, sec_hdr_flag=true, seq_flags=Unsegmented, ..., temperature=25.19685039370079}

* Integers are written in decimal, and booleans as ``true`` or ``false``.
* Floats use the shortest round-trip form.
* Enumerations show their label, or the number if the value is unlisted.
* Strings are double-quoted. ``"`` and ``\`` are escaped with a
  backslash, and control characters are written as ``\xNN``.
* Binary values are written as ``0x`` followed by lowercase hex.
* Arrays and repeated entries are written as ``[a, b, c]``, each element
  as above.
* Aggregates are written as ``{x=1, y=-2}``, with the members' field
  names.
* Absent optional fields are written as ``none``.

Runtime reference
-----------------

Generated code calls these, and they are also available for hand-written
codecs. The names are the same in every language unless noted.

In C/C++ (``xtce/packet.h``) every name has the prefix ``xtce_pk_``,
types are structs operated on by functions, and fallible functions
return an error code and their result through a pointer. Examples:
``xtce_pk_reader_init(data, len, err)`` and
``xtce_pk_read_uint(&r, bits, little_endian, field, &value)``;
``xtce_pk_writer_init(buf, cap, err)`` and
``xtce_pk_write_uint(&w, bits, little_endian, value)``.
``xtce_pk_text`` accumulates canonical text into a fixed buffer, like
``snprintf``.

``BitReader(data)``
   ``position()``, ``seek(start, offset, field)``, ``skip(offset, field)``;
   ``uint(bits, field)`` reads 1–64 bits, most significant bit first;
   ``uint_order(bits, little_endian, field)``; ``bytes(n, field)``.
   ``uint_bits(bits, lsb, order, field)`` (``xtce_pk_read_ordered``, and
   ``xtce_pk_read_ordered64`` for at most 64 bits) reads 1–128 bits in any
   bit and byte order (see "Bit and byte order" under `Supported XTCE`_):
   ``lsb`` for least significant bit first, and ``order`` big endian, little endian or a byte
   order list (Rust ``ByteOrder::Big``, ``Little`` or ``List(&[...])``;
   C ``XTCE_PK_BIG``, ``XTCE_PK_LITTLE`` or ``XTCE_PK_LIST`` with the list;
   Python ``"be"``, ``"le"`` or a tuple). Values of more than 64 bits are
   a ``u128`` in Rust and a (``hi``, ``lo``) pair in C. Integers of more
   than 64 bits are read with ``wide::<N>(bits, lsb, order, field)``
   (``xtce_pk_read_wide``) into ``N`` bytes, least significant first,
   and ``wide_to_signed``, ``wide_from_signed``, ``wide_from_unsigned``,
   ``wide_bcd_decode`` and ``wide_bcd_encode`` convert them as their
   64-bit counterparts do. Python's ``uint_bits`` reads any size.
   ``limit(value, adjust, field)`` (``xtce_pk_read_limit``) ends the packet
   where a packet length field that was just read says, and
   ``finish(field)`` (``xtce_pk_read_finish``) checks that decoding
   reached that end.

``BitWriter(buffer)``
   Clears the buffer, then ``uint(bits, value)``,
   ``uint_order(bits, little_endian, value)``,
   ``uint_bits(bits, lsb, order, value)`` (``xtce_pk_write_ordered``),
   ``bytes(data)``, ``seek`` and ``skip``. In Rust and C/C++, ``zeros(bits)``
   (``xtce_pk_write_zeros``) writes a run of zero bits. The buffer grows,
   zero filled, to cover the furthest bit written. Writing over earlier
   bits replaces them. Byte-aligned ``bytes`` and ``zeros`` copy or fill
   whole bytes at once. ``patch_length(at, bits, little_endian, adjust,
   field)`` (``xtce_pk_patch_length``) fills in the packet length field at
   bit ``at`` once the packet is written.

Integers
   ``to_signed(raw, bits, encoding)`` and
   ``from_signed(value, bits, encoding, field)`` handle the unsigned,
   two's-complement, sign-magnitude and ones'-complement encodings.
   ``from_unsigned`` checks unsigned ranges. ``int_to_f64`` (Rust),
   ``xtce_pk_int_to_double`` (C/C++) and ``int_to_float`` (Python) convert raw
   values to floats. ``f64_to_int`` / ``double_to_int`` /
   ``float_to_int`` round half away from zero and check the range.
   ``bcd_decode(raw, bits, packed, field)`` and
   ``bcd_encode(value, bits, packed, field)`` convert BCD and packed BCD;
   decoding fails with ``InvalidValue`` for a digit above 9.

Floats
   ``float_from_raw(raw, bits)`` and ``float_to_raw(value, bits)`` for
   IEEE 754 binary32 and binary64. Converting to 32 bits rounds to
   nearest, and overflows to infinity. ``float_decode(format, bits, raw,
   field)`` and ``float_encode(format, bits, value, field)`` convert every
   float format (see "Float encoding" under `Supported XTCE`_): Rust
   ``FloatFormat::Ieee``,
   ``Mil1750a``, ``Dec``, ``Ibm`` or ``Ti``; C ``XTCE_PK_IEEE``,
   ``XTCE_PK_MIL1750A``, ``XTCE_PK_DEC``, ``XTCE_PK_IBM`` or
   ``XTCE_PK_TI``; Python the XTCE names.

Calibrators
   ``polynomial(x, terms)``, ``polynomial_inverse(y, intercept, slope)``,
   ``spline(x, points, order, extrapolate, field)``,
   ``spline_orders(x, points, orders, extrapolate, field)`` (a spline
   whose segments have their own orders) and
   ``spline_inverse(y, points, extrapolate, field)``. ``math(op, a, b)``
   (Rust, with a ``MathOperator``), ``xtce_pk_math`` (C, with an
   ``XTCE_PK_OP_`` code) and ``math_op(op, a, b)`` (Python, with the XTCE
   operator) apply a ``MathOperationCalibrator`` operator; Python's
   ``not_invertible(field)`` raises ``NotInvertible``.
   ``round_to_int(y, lo, lim, field)`` rounds a calibrated value half away
   from zero and fails with ``OutOfRange`` unless ``lo <= result < lim``.

Strings and binary
   ``read_string``, ``write_string`` (with a ``StringSpec``: fixed bits,
   maximum bits, the terminator's bytes, size-tag bits and character set)
   and ``write_binary``. Rust's ``read_fixed_string::<N, W>`` reads a
   string of at most ``N`` bytes of UTF-8 from at most ``W`` bytes on the
   wire. Python's ``decode_text(bytes, charset, field)`` and
   ``encode_text(string, charset, field)`` convert a character set.
   ``StringSpec::sized(bytes)`` (Rust), ``xtce_pk_string_spec_sized``
   (C/C++) and ``sized(spec, bytes)`` (Python) give the spec of a string
   whose size another field gives. The reader's ``pad(bits, field)``
   (``xtce_pk_read_pad``) skips padding that must be present. The
   reader's ``rest_bytes(max, tail, field)``
   (``xtce_pk_read_rest`` in C/C++) gives the size of a binary value that
   takes the rest of the packet but the ``tail`` bits after it, and ``write_rest`` writes one (C/C++
   generates it inline). ``string_from_bytes(bytes, field)`` (Rust,
   Python), ``xtce_pk_read_utf8`` and ``xtce_pk_write_utf8`` (C/C++) read
   and write strings encoded as binary. Python's ``bool_from_string(s,
   one, zero, field)`` decodes a boolean encoded as a string; the other
   languages generate it inline.

Error detection
   ``checksum(spec, data, start, end, field)`` (``xtce_pk_checksum``)
   computes an ``ErrorDetectCorrect`` algorithm over bits ``start`` to
   ``end`` of ``data``: a Rust ``Checksum``, a C
   ``xtce_pk_checksum_spec``, or a Python tuple (see the runtime's
   documentation). The reader's ``checksum(spec, start, end, field)`` and
   ``packet_end()``, and the writer's ``patch_checksum(spec, start, end,
   at, bits, little_endian, field)`` and ``packet_end()``, are what
   generated code uses.

Sizes from other fields
   ``dynamic_size(x, slope, intercept, unit, max, field)`` computes
   ``(slope * x + intercept) / unit`` and fails with ``BadSize`` unless it
   is a whole number from 0 to ``max``; ``unit`` is 8 for a size in bits
   used in whole bytes. ``size_source(n, slope, intercept, unit, factor,
   field)`` (``xtce_pk_size_source``) is its inverse, for a field that
   encoding computes: the value that gives a size or count of ``n``,
   failing with ``BadSize`` if there is none. Python also has
   ``read_list(r, n, gap, field,
   read)``, ``check_size(value, n, field)`` and ``describe_list(values,
   describe)``, which the other languages generate inline.

Supported XTCE
--------------

The compiler supports the subset below. Anything else is reported as
unsupported, naming the construct and its location.

Parameter and argument types
   Integer, Float, Enumerated, Boolean, String, Binary, AbsoluteTime,
   RelativeTime, Array and Aggregate. A type with a ``baseType``
   inherits every attribute and child element it leaves out from its base
   type, which must be the same kind of type, possibly in another document;
   those it gives replace the base's, whole.

Ancillary data
   ``tcs-xtce.length`` on a parameter or argument marks a packet length
   field, and ``tcs-xtce.max-field-bytes`` on a root ``SpaceSystem`` sets
   the maximum field size, both described under the generated packet
   types. Other ancillary data is ignored.

Aggregates
   Members of any supported type except the aggregate itself, values
   that take the rest of the packet, and values whose size another value
   gives, unless that value is an earlier member of the same aggregate.
   Criteria, lookups and sizes can refer to a member with XTCE's dot
   syntax (``parameterRef="RouteHdr.Count"``, also nested, as in
   ``P.a.b``, and in ``argumentRef``), but not to a whole aggregate. A
   member's calibrator may use other values of the packet decoded before
   the aggregate: the demo's ``ScaledPair`` has a ``Value`` that
   ``ScaleMode`` chooses the calibrator of.

Array elements
   Criteria, lookups and sizes can refer to an element with XTCE's
   bracket syntax, mixed with members (``Probes[1].Gains[0]``), with
   one index per dimension (``Matrix[1][2]``). Each index must be within
   its dimension, counting from its ``StartingIndex``; for a subset, the
   indexes are the full array's, and must fall inside the subset. When encoding, criteria are checked before any field is
   written, so arrays they index are first checked to have their count,
   and encoding fails with ``BadSize`` (naming the field) otherwise. In
   the demo, ``ProbeOne`` and ``ProbeOther`` are chosen by
   ``Probes[0].Id``, and ``ProbeData`` is sized by ``Probes[1].Gains[0]``.

   The array's count may come from another value in any one dimension.
   In a later one it moves every element after the first row, so where
   the element is, as well as whether it is there, is known only from the
   packet. Either way decoding and
   encoding fail with ``BadSize``, naming the array, when it isn't, at
   the point where the criterion or size that reads it is evaluated. In
   the demo, ``Tally``'s ``TallyData`` is sized by ``Tallies[0]`` and its
   ``TallyExtra`` is included when ``Tallies[1]`` is 7, so a ``Tally`` with
   one tally fails, and one with none fails sooner; ``TwoRowBlock`` has
   two rows of ``TwoRowCols`` bytes, and ``TwoRowNote`` is included when
   ``TwoRows[1][2]`` is 9.

Arrays
   Elements of any supported type, arrays among them (an array of
   arrays is a list of lists), and values whose size another field
   gives, which every element then has; an element can't take the rest
   of the packet. Each dimension's ``StartingIndex`` is a
   ``FixedValue``; its ``EndingIndex`` is a ``FixedValue`` or, in at most
   one dimension, a ``DynamicValue``. ``ArrayParameterRefEntry`` and
   ``ArrayArgumentRefEntry`` (or a plain ``ParameterRefEntry`` or
   ``ArgumentRefEntry``) use the whole array, or with a ``DimensionList``
   a subset, with as many dimensions as the type and within its bounds
   when they are fixed.

Repeated entries
   ``RepeatEntry`` on parameter and argument entries, with a ``Count``
   that is a positive ``FixedValue`` or a ``DynamicValue``, and an
   optional ``Offset`` of zero or more bits, a ``FixedValue`` or a
   ``DynamicValue``. A repeated array is an array of arrays, as the
   demo's ``Nested`` shows; so is a repeated value whose size another
   field gives. On a command's ``FixedValueEntry`` the ``Count`` and
   ``Offset`` must be fixed.

Sizes and counts from other fields
   ``DynamicValue`` with ``ParameterInstanceRef`` (instance 0) or, in
   argument types, ``ArgumentInstanceRef``, and an optional
   ``LinearAdjustment``, in array ``EndingIndex``, ``RepeatEntry``
   ``Count`` and ``Offset``, binary ``SizeInBits``, variable string sizes,
   container ``SizeInBits`` and locations. It must refer to an integer value decoded earlier in the
   packet, outside any ``IncludeCondition``, or, inside an aggregate
   type, to an earlier integer member. ``DiscreteLookupList`` works in
   the same places except aggregate members, with comparisons on values
   decoded earlier. See `Generated packet types`_ for the checks and
   bounds.

Integer encoding
   Any number of bits; ``unsigned``, ``twosComplement``,
   ``signMagnitude``, ``onesComplement``, ``BCD`` and ``packedBCD``, in any
   bit and byte order (see "Bit and byte order" below). Packed BCD has a
   decimal digit in every 4 bits; BCD has one in the low 4 bits of every
   byte, whose high 4 bits are zero. Both are unsigned, their size must be
   whole digits, and decoding fails with ``InvalidValue`` for any other
   bits.

   An integer of more than 64 bits is a field of the type the field types
   table gives: a ``u128`` or ``i128`` in Rust up to 128 bits, and past
   that integers of `Integers of any size`_, whose methods compute with
   it. It is decoded, encoded (checking its ``ValidRange``), described and
   compared, with a value or with another integer of any size, in every
   criterion. Nothing computes with it, so it can't be an enumeration, a
   boolean, a float, a time or a calibrated integer, give a size, count or
   location, be used by a calibrator or an algorithm, have an alarm (the
   alarm is left out, with a note), or be a packet length or checksum
   field. The demo's ``Wide`` packet has integers of 65 to 200 bits in
   every encoding and order, and ``SetWide`` command arguments of 96 and
   136 bits.

Float encoding
   ``IEEE754_1985`` and ``IEEE754`` (16, 32, 64, 80 or 128 bits: binary16,
   binary32, binary64, the x87 80-bit extended format and binary128),
   ``MILSTD_1750A`` (32 or 48 bits), ``DEC`` (32 bits for VAX F_floating,
   64 for D_floating), ``IBM`` (hexadecimal, 32, 64 or 128 bits) and ``TI``
   (TMS320C3x, 16, 32 or 40 bits), in any bit and byte order. Engineering
   values are doubles: decoding rounds to the nearest double, and encoding
   to the nearest value of the format, ties to even. IEEE formats overflow
   to infinity; the others have no infinity or NaN, so encoding a value
   too large for them, an infinity or NaN fails with ``OutOfRange``.
   MIL-STD-1750A and IBM values too small to normalize are encoded
   unnormalized at the smallest exponent; DEC and TI values too small for
   them become zero. Decoding a DEC reserved operand (sign set, exponent
   zero) fails with ``InvalidValue``. DEC formats are given as the value
   reads in VAX order; the order of their 16-bit halves in memory is a
   byte order list (``2,3,0,1`` for F_floating), as the demo's ``VaxF``
   shows.

Bit and byte order
   ``bitOrder`` and ``byteOrder`` apply to integer and float encodings of
   any size. A field's bits are taken in groups of 8 from its start (the
   last group may be shorter). With ``mostSignificantBitFirst`` (the
   default), bits are numbered from the most significant bit of each byte
   and a group's first bit is its most significant; with
   ``leastSignificantBitFirst``, they are numbered from the least
   significant bit of each byte and a group's first bit is its least
   significant. ``mostSignificantByteFirst`` (the default) makes the first
   group the most significant, ``leastSignificantByteFirst`` the least,
   and a byte order list (``3,2,1,0`` style, whole bytes only) gives each
   byte's significance, 0 being the least. So
   ``leastSignificantBitFirst`` with ``leastSignificantByteFirst`` lays
   fields out as C bit fields do on a little-endian machine: the demo's
   ``BitA`` and ``BitB`` are a 3-bit and a 13-bit field sharing a 16-bit
   word. Fields that share a byte should use the same bit order, as the
   two orders number its bits differently. String and binary encodings are
   bytes, which have no byte order: ``leastSignificantByteFirst`` on a
   ``UTF-16`` or ``UTF-32`` string makes it ``UTF-16LE`` or ``UTF-32LE``.
   A byte that starts on a byte boundary reads the same in either bit
   order, so ``leastSignificantBitFirst`` strings and binary data must
   start on one (and a string's size tag must be whole bytes). The
   generator works out where each value starts, modulo 8, and reports
   one it can't show to be aligned as not supported.

Calibrators
   ``PolynomialCalibrator``, ``SplineCalibrator`` and
   ``MathOperationCalibrator``, as the ``DefaultCalibrator`` of an integer
   or float encoding on a float type, or of an integer encoding on an
   integer type, and a ``ContextCalibratorList``. Encoding uses the
   inverse calibrator. This works for polynomials of degree 0 or 1 with a
   non-zero slope, and for linear splines whose calibrated values strictly
   increase or decrease. Other calibrators are decode-only: encoding them
   fails with ``NotInvertible``. Engineering values are rounded half away
   from zero to the nearest raw integer.

   A spline's ``order`` applies to every segment, and a ``SplinePoint``'s
   own ``order`` to the segment that starts at it. A segment of order 0 is
   flat at its first point; order 1 interpolates linearly; order *k*
   interpolates with the polynomial through *k* + 1 points, the segment's
   first point and those after it (or as many before it as the end of the
   points needs). Outside the points, the first or last segment
   extrapolates when ``extrapolate`` is true; otherwise decoding fails
   with ``Calibration``. In the demo, ``Curve`` is quadratic and ``Steps``
   flat, then linear, then quadratic.

   A ``MathOperationCalibrator`` is its postfix expression, compiled into
   ordinary code in each language: ``ThisParameterOperand`` is the raw
   value, ``ValueOperand`` a constant, and ``ParameterInstanceRefOperand``
   another value of the packet decoded before this one (a number, an
   enumeration's value or a boolean as 1 or 0), as for a criterion. Every
   operator is supported, with the operands from the bottom of the stack
   first:

   * ``+ - * /``, ``%`` (the modulo, with the sign of the divisor),
     ``div`` (the Euclidean quotient of two operands: the schema's
     notation shows one, but a quotient needs two), ``^`` (``x1**x2``),
     ``y^x`` (``x2**x1``), ``abs``, ``int`` (the integer part), ``min`` and
     ``max`` (``min`` gives the second operand only when it is smaller, so
     a NaN first operand stays), ``1/x`` and ``x!`` (of a whole number;
     NaN otherwise, infinity above 170);
   * ``ln``, ``log`` (base 10), ``e^x``, ``sin``, ``cos``, ``tan``,
     ``asin``, ``acos``, ``atan``, ``atan2`` (``atan2(x2, x1)``, as the
     schema says), ``sinh``, ``cosh``, ``tanh``, ``asinh``, ``acosh`` and
     ``atanh``, from the C library, whose results the IEEE rules give
     outside their domains (NaN, or an infinity at a pole);
   * the comparisons ``> >= < <= == !=`` and the logical ``&& || !``,
     which give 1 or 0 (``!`` takes one operand);
   * the bitwise ``<< >> & | xor`` and ``~`` (which takes one operand) on
     64-bit two's complement integers: the operands are truncated to
     integers, the result is NaN when one has no 64-bit value or a shift
     is outside 0 to 63, ``>>`` is arithmetic, and ``<<`` wraps;
   * ``swap``, ``drop``, ``dup`` and ``over``, which rearrange the stack.

   A ``ContextCalibratorList`` picks the calibrator of the first
   ``ContextCalibrator`` whose ``ContextMatch`` holds, on values decoded
   before this one, and the ``DefaultCalibrator`` (or none) when none
   does. Encoding evaluates the same criteria on the packet's fields and
   inverts the calibrator they pick, failing with ``NotInvertible`` only
   if that one can't be inverted. In the demo, ``Calibrated`` picks by
   ``CalMode`` and ``CalFlag``, and ``Derived`` has math operations on
   other values, an enumeration and a boolean among them.

   Calibrators that use other values must be in the packet's own types or
   array elements, not inside an aggregate type, which would bind them to
   different values in different packets.

   On an integer type the calibrated value is rounded half away from zero
   to an integer of the type's ``sizeInBits`` (default 32) and ``signed``
   (default true); decoding fails with ``OutOfRange`` if it doesn't fit.
   Rounding means a value may not survive encoding: in the demo,
   ``RecordCount`` is ``-4 + raw / 2``, so 1 encodes as raw 10 but raw 9
   decodes as 1 too. The type's ``ValidRange`` applies to the calibrated
   value; with ``validRangeAppliesToCalibrated="false"`` it is ignored. A
   size or count taken from it is bounded through the calibrator.

   Encoding an integer or a float outside its type's ``ValidRange``, or an
   argument outside every range of its ``ValidRangeSet``, fails with
   ``OutOfRange``, as a range check for commanding should. A size or count
   that encoding computes from what it sizes is checked through that
   instead: too many elements fail with ``BadSize``. Decoding takes any
   value the encoding holds, but a size or count above its range.
   Comparisons with ``useCalibratedValue="false"`` are not supported on
   it, as the generated code keeps only the calibrated value.

Strings
   Every character set of XTCE: ``UTF-8``, ``US-ASCII``, ``ISO-8859-1``,
   ``Windows-1252``, ``UTF-16``, ``UTF-16LE``, ``UTF-16BE``, ``UTF-32``,
   ``UTF-32LE`` and ``UTF-32BE``. Generated fields hold UTF-8, and
   decoding and encoding convert. ``UTF-16`` and ``UTF-32`` are read big
   endian unless they start with a byte order mark, and written big endian
   without one. Decoding fails with ``InvalidString`` for bytes that aren't
   valid in the character set (an unpaired surrogate, a byte
   Windows-1252 leaves undefined, a byte above 127 in US-ASCII), and
   encoding for a character the character set can't hold. The supported
   layouts are:

   * a fixed size (``SizeInBits/Fixed``) ending at the termination
     character (``00`` by default) or at the end of the field;
   * a fixed size with a leading size tag;
   * a variable size (``Variable``) with a termination character;
   * a variable size with a leading size tag;
   * either of these within a size that another field gives
     (``Variable`` with a ``DynamicValue``).

   Sizes must be whole code units, and size tags count bytes. The
   termination character may be several bytes (``0D0A``, say), found
   only where a code unit starts; it must be a whole number of code
   units, and ``00`` stands for a zero code unit, so it is ``0000`` in
   UTF-16. When encoding, fixed fields are padded with the terminator and
   then zeros.

   A string type may also have a ``BinaryDataEncoding`` (fixed, or with a
   size another field gives). The field then holds the string's UTF-8
   bytes and nothing else: values must fill it exactly, as binary values
   do, and decoding fails with ``InvalidString`` unless the bytes are
   UTF-8. In the demo, ``Reply`` has ``Code`` (3 bytes) and ``Note``
   (``NoteSize`` bytes).

Booleans
   An ``IntegerDataEncoding`` (zero is false, anything else true), or a
   ``StringDataEncoding`` in any of the layouts above, which holds
   ``oneStringValue`` or ``zeroStringValue`` (``True`` and ``False`` by
   default). Encoding writes the string for the value. Decoding fails
   with ``OutOfRange`` for any other string; a fixed field's content ends
   at its terminator, so bytes after it don't matter. The two strings
   must differ and fit the encoding. In the demo, ``Reply`` has
   ``Answer``, which is ``yes`` or ``no`` in 4 bytes. Comparisons with
   ``useCalibratedValue="false"`` are not supported on these booleans.

Binary
   A fixed size in whole bytes, or a size another field gives. Values
   must have exactly that length.

   A ``SizeInBits`` of ``-1`` (a Yamcs convention for "the size of the
   value given") takes the rest of the packet, up to the maximum field
   size (64 KiB by default). Only entries that always have the same size
   may follow it, outside any ``IncludeCondition``, and it then takes the
   rest but those: a float after it is the packet's last 4 bytes, as in
   the demo's ``Tailed`` command. A packet that contains it must always
   have the same entries after it, and it can't be an array element, an
   aggregate member or repeated. Decoding fails with ``BadSize`` unless
   what is left is whole bytes, and encoding fails with ``TooLong`` above
   that size. Yamcs also allows such values before entries whose size
   varies, where only the sender knows their size; those packets are not
   supported.

Times
   ``AbsoluteTime`` and ``RelativeTime`` types with an integer or float
   ``Encoding`` are seconds, as doubles: the encoding's raw value,
   calibrated if it has a calibrator, then ``scale * value + offset`` in
   the ``Encoding``'s ``units``, converted to seconds. Every unit is
   supported; XTCE leaves months and years to the system, and tcs-xtce
   takes the Gregorian calendar's mean month (2,629,746 seconds) and year
   (365.2425 days). Encoding inverts the whole chain, so it needs an
   invertible calibrator, as for other values. A time's value is a double,
   so an integer encoding of more than 53 bits, such as a 64-bit count of
   nanoseconds, loses its low bits.

   A time with a ``StringDataEncoding`` holds its text, and one with a
   ``BinaryDataEncoding`` its bytes, unconverted, unless that encoding
   has transform algorithms, which give the time in seconds (see
   Transform algorithms). Neither may have ``units``, ``scale`` or
   ``offset``.

   The ``ReferenceTime`` (an ``Epoch``: ``TAI``, ``J2000``, ``UNIX``,
   ``GPS``, a date or a date and time; or an ``OffsetFrom`` another
   parameter) is recorded in the generated documentation and not applied:
   a value is the seconds from its reference, and converting between time
   scales such as TAI and UTC needs leap-second tables, which are outside
   a codec. An epoch that is none of those forms is reported. The demo's
   ``Times`` packet has one time of each kind.

Containers
   ``SequenceContainer`` entry lists with ``ParameterRefEntry``,
   ``ArrayParameterRefEntry`` and ``ContainerRefEntry``. Included
   containers are flattened in, or with a ``RepeatEntry`` become a list.
   An included container with a ``BaseContainer`` is its bases' entries,
   then its own, and its ``RestrictionCriteria`` must hold: decoding and
   encoding fail with ``Mismatch`` otherwise (a repeated one can't have a
   ``BaseContainer``). ``BaseContainer`` inheritance uses
   ``RestrictionCriteria``. One with a ``NextContainer`` also depends on
   the container that follows it in a stream, which a packet codec can't
   see: decoding its base never picks it, so decode it directly.

   A ``BinaryEncoding`` with a ``SizeInBits`` makes a telemetry container,
   whether a packet (with its base containers' entries) or an included
   one, occupy that many bits: a ``FixedValue``, or a ``DynamicValue``
   another field gives. It is padded with zeros after its entries; when
   their size varies, decoding and encoding fail with ``BadSize`` if they
   go past it. The demo's ``Framed`` includes containers of both kinds.

Error detection
   The ``ErrorDetectCorrect`` of a container's ``BinaryEncoding`` (of a
   sequence container, whether a packet, a base container or an included
   one, or of a command container): every ``CRC``, ``Checksum``, ``XOR``
   and ``Parity`` whose ``parameterRef`` names the field that holds its
   value. (One without a ``parameterRef`` only documents the container,
   and is ignored.) Like a packet length field, that field is not a field
   of the generated struct: encoding computes it once the packet is
   written (after the length), and decoding computes it and fails with
   ``BadChecksum`` if the field differs. It must be an uncalibrated
   unsigned binary integer, big or little endian in whole bytes, without
   an ``IncludeCondition``, and nothing may compare it or take a size
   from it. It holds the value's low bits when the algorithm gives more.
   For a check in a base container, the field may be in that container
   or a derived one.

   The bits a value covers: with ``reference="start"`` (the default),
   from ``bitsFromReference`` bits after the start of the container to its
   field, or to the end of the container if the field comes before them;
   with ``reference="end"``, from the start of the container to
   ``bitsFromReference`` bits before its end, as for a checksum in the
   container's last bits. The container's start and end are those of the
   packet for a packet or base container (the packet ends with its last
   byte), and those of the included entries for an included container.
   Values are computed in the order of their fields, so a value may cover
   the fields of those before it, as in the demo's ``Checked``.

   A check in an included container that is there only when an
   ``IncludeCondition`` holds (its own or that of a container including
   it) is checked where the container ends, and computed there when
   encoding, if the container is present: its bits all lie inside it. The
   demo's ``Guarded`` includes ``Block`` that way, directly and inside
   ``BlockWrap``.

   The algorithms, all over the covered bits taken most significant bit
   first, with a last partial word padded with zero bits:

   * ``CRC``: the ``width``-bit CRC (1 to 64) of ``Polynomial`` (without
     its top bit, as XTCE writes it), starting from ``InitRemainder`` and
     ending XORed with ``FinalXOR``, each truncated to the width. With
     ``reflectData`` each byte is taken least significant bit first, and
     with ``reflectRemainder`` the remainder is reflected before the final
     XOR, as in the usual model of CRCs (so CRC-32 is width 32,
     ``04C11DB7``, ``FFFFFFFF`` both ways and both reflections).
     ``direction="leastSignificantBitFirst"`` reflects the data too (both
     together cancel).
   * ``Checksum``: ``sum8``, ``sum16``, ``sum24`` and ``sum32``, the sum of
     the bytes modulo 2 to that many bits; ``unix_sum``, the checksum of
     BSD and GNU ``sum``; ``fletcher4``, ``fletcher8``, ``fletcher16`` and
     ``fletcher32``, Fletcher's checksum of that many bits, on words of
     half as many bits; ``adler32``; and ``luhn``, ``verhoeff`` and
     ``damm``, the check digit of the decimal digits in every 4 bits (a
     digit above 9 fails with ``InvalidValue``). ``custom`` is the
     application's function (see Custom algorithms).
     ``hashSizeInBits`` changes nothing.
   * ``XOR``: the exclusive or of the words the size of the field.
   * ``Parity``: the bit that makes the number of one bits even
     (``Even``) or odd (``Odd``), with the field's bit.

   The names follow Jacksum's, whose ``sum8`` to ``sum32`` and
   ``unix_sum`` they match. The demo's ``Checked`` packet has one of most
   kinds, and an included ``Block`` with its own check; its ``Send``
   command has a Luhn check digit.

Criteria
   ``RestrictionCriteria``, ``IncludeCondition`` and ``DiscreteLookup``
   take a ``Comparison``, a ``ComparisonList`` (all must hold) or a
   ``BooleanExpression``: a ``Condition``, or ``ANDedConditions`` and
   ``ORedConditions`` nested to any depth. Comparisons use the operators
   ``== != < <= > >=`` on calibrated or raw values (only ``==`` and ``!=``
   for booleans, strings and binary), on values that are not arrays or
   aggregates. A ``Condition`` may compare two values of the packet
   instead of a value and a constant: both of the same kind (integer,
   enumeration, float, boolean or string) and calibrated; a signed
   integer may be compared with an unsigned one, by value. In the demo,
   ``Gate``'s ``GateSpan`` is included when ``GateLo < GateHi`` or both
   are zero, ``Open``'s ``Data`` is 2 bytes when its ``Lo < Hi``, and
   ``Compared``'s ``CmpBelow`` is included when the signed ``CmpNeg`` is
   below the unsigned ``CmpPos``.

   A comparison with a constant may read a value that may be absent.
   As with C++'s ``std::optional``, an absent value is below every
   value: it is ``!=``, ``<`` and ``<=`` any constant, and not ``==``,
   ``>`` or ``>=`` it. ``Compared``'s ``CmpFive`` is included when
   ``CmpOpt`` is present and 5, and ``CmpNotFive`` otherwise. Values that
   may be absent can't be compared with another value of the packet or
   used by a calibrator. Only a plain ``==``
   comparison with a constant fixes a field's value, so derived packets
   chosen by other criteria keep those fields, and encoding checks them.

Locations
   ``LocationInContainerInBits`` relative to ``previousEntry`` (the
   default), ``containerStart``, ``containerEnd`` or ``nextEntry``. As
   XTCE says, the first two place the start of the entry and the last two
   its end, so an entry located from either end has a fixed size.

   A ``containerEnd`` location puts the end of the entry that many bits
   before the end of its container. In a container with a fixed
   ``SizeInBits`` that is a place from its start. In a packet whose size
   varies, it is the packet's end, which decoding knows (the end of the
   data, or where its length field says); there it must be the packet's
   last entry, always present. Decoding moves there (failing with
   ``BadPosition`` if that is before the previous entry's end) and treats
   the bits after it as the packet's; encoding writes it after the
   previous entry, moved on by the fewest zero bits that end the packet
   on a byte boundary, then the offset's bits as zeros. Bits that the
   decoded packet had before it are left out, as gaps are. The end of an
   included container whose size varies would depend on where its entries
   are, so an entry there can't be located from it.

   A ``nextEntry`` location puts the end of the entry that many bits
   before the start of the entry after it in the same entry list. When
   that entry is located from its container's start, this one goes back
   from there; otherwise the offset is the gap between them, and the next
   entry's own ``previousEntry`` offset, if it gives one, must be the
   same. The demo's ``Tail`` packet has ``TailFlag`` 4 bits before
   ``TailData``, and ``TailSum`` a byte before the end of a packet whose
   size varies.

   ``containerStart`` is the start of the
   packet for inherited entries, and the inclusion point for entries of
   an included container. A ``FixedValue`` offset may be negative. A
   ``DynamicValue`` or ``DiscreteLookupList`` offset is computed like a
   size, in bits, so it must be a whole number from 0 up to its bound
   (at most the maximum field size), or decoding and encoding fail with
   ``BadSize``; in
   the demo, ``Frame``'s ``FrameEnd`` comes ``FrameGap`` bits after the
   body. Repeated and fixed-size containers need fixed offsets.

Include conditions
   ``IncludeCondition`` (see Criteria) on
   ``ParameterRefEntry`` and ``ContainerRefEntry``, on values decoded
   earlier, which may themselves be conditional. The affected fields
   become optional. They are decoded when the condition holds, and
   encoded when the condition holds on the struct's fields. Encoding
   fails with ``Missing`` if the condition holds but the value is
   absent.

Commands
   ``MetaCommand`` with ``ArgumentList``, ``BaseMetaCommand`` and
   ``ArgumentAssignmentList``, and a ``CommandContainer`` that extends
   the base meta-command's container: through a ``BaseContainer`` naming
   it, or, as Yamcs reads it, with no ``BaseContainer`` at all. The container
   may hold ``ArgumentRefEntry``, ``ArrayArgumentRefEntry``,
   ``ParameterRefEntry``, ``ArrayParameterRefEntry`` and
   ``FixedValueEntry`` (of any size), with ``IncludeCondition`` as in
   telemetry. Argument assignments act like ``==`` restriction criteria:
   they fix the value when encoding and select the command when decoding.
   The ``RestrictionCriteria`` of a ``CommandContainer``'s
   ``BaseContainer`` (which XTCE gives on parameters in the command) act
   the same way. The demo's ``Arm`` has an optional argument and a
   72-bit pattern sent twice.

   A command container may also hold ``ContainerRefEntry``, naming a
   container of a ``CommandContainerSet`` or of telemetry, whose entries
   (and its bases', each base's ``RestrictionCriteria`` checked after its
   entries) are included as in telemetry. A ``CommandContainer`` of a
   meta-command without a ``BaseMetaCommand`` may extend such a container
   through its ``BaseContainer``. A ``SizeInBits`` pads a command, or an
   included container, as in telemetry. The demo's ``Stamp`` includes
   ``Trailer``, which extends ``TrailerBase`` and must have a
   ``TrailerKind`` of 7, and is padded to 16 bytes. A repeated container
   is an array of aggregates, as in telemetry: the demo's ``Pairs`` sends
   ``PairCount`` copies of ``Pair``.

   A ``ContainerRefEntry`` may also name the ``CommandContainer`` of
   another meta-command, often an abstract one that only defines a part of
   commands. Its entries are included with that meta-command's arguments,
   after those of the container it extends (its ``BaseMetaCommand``'s,
   included the same way, or a container of a ``CommandContainerSet``),
   and its ``BaseContainer``'s ``RestrictionCriteria`` are checked. Its
   argument assignments fix what they assign, as a command's own do,
   except in an included container with an ``IncludeCondition``, where
   they are checked instead: decoding and encoding fail with
   ``Mismatch`` when they don't hold. The demo's ``SetLimits`` includes
   ``Limits``' container, which extends ``LimitsBase``'s and assigns
   ``LimitKind`` 7, and a second copy when ``Both`` is true. A length
   field in the included container measures that container (see the
   generated packet types). Repeated, such a
   container is an array of aggregates of its arguments, as a repeated
   container is in telemetry, and its assignments are checked on each
   copy: the demo's ``SetManyLimits`` sends ``LimitsCount`` copies of
   ``Limits``' container, each with a ``LimitKind`` of 7.

Segments
   ``ParameterSegmentRefEntry``, ``ContainerSegmentRefEntry`` and
   ``StreamSegmentEntry``, in telemetry and command containers. When the
   segments of a parameter in one entry list make up its whole encoding,
   of at most 64 bits, the value is assembled: its encoding is the bits
   of its segments, in the order their ``order`` attributes give (all or
   none of them must have one) or else in the order they come, decoded as
   if it started at the beginning of a byte. The value becomes a field
   after its last segment, and later entries may use it. Encoding encodes
   the value and writes each segment's share of its bits.

   XTCE's segments may also be spread over several packets, which a
   packet codec can't put together. So a segment of a parameter that the
   entry list doesn't complete (or that varies in size, or has more than
   64 bits, or has an ``IncludeCondition``), and every container or stream
   segment, is a field of its own that holds the segment's bits as they
   are: an unsigned integer of up to 64 bits, or else whole bytes. It is
   named for what it is part of and its ``order``, or its place among
   that thing's segments in the entry list: ``SegPartSegment0``. The demo's
   ``Segmented`` packet splits a 16-bit word around another value and
   sends a float's halves low half first; ``Split`` sends a command
   parameter's bytes the same way.

   A segment entry repeated a fixed number of times, one right after
   another, is that many segments, so they can still make up a value. One
   whose ``RepeatEntry`` count another value gives, or with an ``Offset``
   between the repeats, is a list of raw segments, like an array. A
   repeated segment located from the end of its container takes its
   repeats' bits and the gaps between them, so its count and ``Offset``
   must be fixed. XTCE doesn't say which parts of a value the repeats of
   an entry with an ``order`` are, so a repeated segment can't have one.
   The demo's ``Repeated`` packet assembles ``RepWord`` from four repeated
   8-bit segments, sends ``RepCount`` 4-bit segments of ``RepTail``, and
   two segments of ``Section`` 4 bits apart. In ``EndSegs``, two 4-bit
   segments of ``EndPair``, 4 bits apart, end the 16-bit ``EndSegBlock``,
   and ``EndWord``'s two 8-bit segments end a byte before the end of the
   packet.

Indirect entries
   ``IndirectParameterRefEntry``, which dwell telemetry uses: the entry
   is the parameter that the value of its ``ParameterInstance`` names.
   That value may be an enumeration whose labels are parameter names.
   With an ``aliasNameSpace``, it may instead be an enumeration whose
   labels, or an integer or string whose values, are aliases in that name
   space, as Yamcs reads it. Each parameter the value can name gives an
   optional field, present when the value names it. An enumeration
   label that names no parameter, such as the demo's ``NONE``, selects
   nothing; a value that is none of the enumeration's labels, or (for an
   integer or string) names no parameter, fails with ``Mismatch``, when
   decoding and when encoding, before anything after it. In the demo's ``Dwell`` packet, ``DwellSel`` chooses
   ``DwellTemp`` or ``DwellVolt``, and ``DwellAliasSel`` chooses them
   again by their ``dwell`` aliases, as ``dwell_temp2`` and
   ``dwell_volt2``.

   Without an ``aliasNameSpace``, a string selector holds a parameter's
   name: any parameter in the mission, by its own name (when that finds
   it from the entry's ``SpaceSystem``, as a reference would) or its full
   path, such as ``DwellTemp`` or ``/Demo/DwellTemp``. Names too long
   for the string are left out. The packet then has an optional field
   for every other parameter, so its generated code grows with the
   mission; an enumeration or an alias name space keeps it to the
   parameters meant. A value naming a parameter that can't be read
   there (one that takes the rest of the packet, or whose size comes
   from a value the packet doesn't have) fails with ``Mismatch``. An
   integer selector likewise holds a name, so it can only name
   parameters whose names are numbers; it is unsupported when there are
   none. The demo's ``DwellByName`` packet has ``DwellName``.

   The packet's own fields keep their names; a parameter the selector
   can choose that has the same name as one of them gets a number
   after it (``dwell_end2``). Only one of the choices is ever there, so
   ``MAX_BYTES`` counts the largest of them.

Alarms
   A parameter type's ``DefaultAlarm`` and ``ContextAlarmList``. The first
   ``ContextAlarm`` whose ``ContextMatch`` holds picks its definition,
   else the ``DefaultAlarm`` applies; the level is the most severe of
   what the definition says:

   * ``StaticAlarmRanges`` of integers, floats and relative times (with
     ``timeUnits``): with ``rangeForm="outside"`` (the default) a value
     outside a level's range raises that level, so the ranges nest, watch
     innermost; with ``"inside"``, a value inside it does. A missing bound
     is infinite, and a NaN is outside every range.
   * ``AlarmMultiRanges``: each ``Range`` raises its own ``level``, by its
     own ``rangeForm``.
   * ``EnumerationAlarmList``: the level of the value's label, else
     ``defaultAlarmLevel``.
   * ``StringAlarmList``: the level of the first ``matchPattern`` that the
     whole string matches, else ``defaultAlarmLevel``. Patterns use
     Python's syntax (as Java's mostly does), without backreferences or
     lookarounds; ``\d``, ``\w`` and ``\s`` are ASCII. The generator
     compiles each to an automaton, so no runtime needs a regular
     expression library.
   * ``AlarmConditions``: each level whose criteria hold, for any type.
   * ``disabled="true"``: no alarm.

   ``alarms()`` lists each field with an alarm and its level, from
   ``normal`` up to ``severe``, or none for a field that is absent. An
   array's level is its worst element's. Criteria may compare any field
   of the packet, before or after the value. Integers are compared with
   the ranges exactly, in every language.

   Some alarms depend on earlier samples of the parameter, which a packet
   doesn't hold: ``alarms()`` judges them by this sample alone, and
   ``monitor(m, t)`` counts the earlier samples, whose state the
   mission's ``AlarmMonitor`` ``m`` keeps (in C ``P_AlarmMonitor``, set up
   with ``P_AlarmMonitor_init``), at time ``t`` in seconds. It keeps one
   state per parameter, whatever packets hold it.

   * ``minViolations``: an alarm is raised only once that many samples in
     a row are in alarm; ``minConformance``: it is cleared only once that
     many in a row are not. In between, the last level raised stands.
   * ``ChangeAlarmRanges``: the ranges apply to how much the value
     changed since the sample ``spanOfInterestInSamples`` before it,
     per sample or (``changePerSecond``, the default) per second between
     the two samples' times, and with ``changeBasis="percentageChange"``
     as a percentage of the earlier value. Until there is such a sample,
     or when the times are equal or the earlier value is 0, the change
     raises nothing.
   * ``ChangePerSecondAlarmRanges`` of relative times: the same, per
     second, in ``timeUnits``.

   The demo's ``Alarmed`` packet has one of each kind that a packet can
   judge alone, and ``Trend`` the others. The test tools keep one monitor
   for all the vectors, each at the time of its index, and print the
   levels after the packet (and the monitor's, when they differ):
   ``... | alarms: al_mode=normal, al_temp=warning, ...``.

   A packet leaves out an alarm that it can't evaluate, and its generated
   documentation says why: one whose criteria use a value the packet
   doesn't hold, or one the packet computes (a size or length field); a
   change range with a ``spanOfInterestInSeconds`` or on an array; and
   alarms inside aggregates and arrays of arrays. A ``CustomAlarm`` is
   its hook's (see Custom algorithms).

Algorithms
   An ``AlgorithmSet``'s ``MathAlgorithm``, a postfix expression like a
   ``MathOperationCalibrator``'s (see Calibrators) that computes its
   ``outputParameterRef``. A packet evaluates it when it triggers it,
   by holding a value of a parameter of an ``OnParameterUpdateTrigger``
   or being, or deriving from, the container of an
   ``OnContainerUpdateTrigger``, and holds every input.
   ``algorithms()`` lists each output, by the output parameter's name, as
   a double, or none when an input is absent. The demo's ``Derive``
   packet triggers ``SpeedToKph`` by ``DrSpeed`` and ``Ratio`` by its
   container (``Ratio``'s ``DrB`` may be absent), and the test tools
   print the outputs: ``... | derived: dr_speed_kph=36.0, dr_ratio=1.5``.

   A packet leaves out an algorithm it triggers but can't evaluate, and
   its generated documentation says why: one whose inputs it doesn't
   hold, or computes, or that uses ``ThisParameterOperand``, the output's
   own earlier value, like the demo's ``Count``. Evaluating algorithms
   across packets, on a ``OnPeriodicRateTrigger``, or at a
   ``triggerRate``, is a processor's work, not a codec's. A
   ``CustomAlgorithm`` is evaluated the same way, by its hook (see
   Custom algorithms).

Transform algorithms
   A ``FromBinaryTransformAlgorithm`` and ``ToBinaryTransformAlgorithm``
   change bits on the wire into others, as compression or encryption
   does, by the application's functions (transform hooks, below):
   decoding gives the bytes on the wire to the first and reads what it
   makes of them, and encoding gives what it writes to the second. A
   document may give only one; the other way then fails with
   ``NotInvertible``, as Yamcs's PUS documents' time types, which only
   decode in telemetry and only encode in commands, do.

   On a binary type's ``BinaryDataEncoding``, the value is what the
   transform makes of its bytes, of any size up to the maximum field
   size. On an absolute or relative time type's, it is the time the
   transform makes of them, in seconds, as a double like any other
   time. Its ``SizeInBits`` is its size on the wire, or ``-1``, which
   offers the decoding transform the rest of the packet; the transform
   says how many of those bytes it takes, and a fixed size must be all
   of them. In standard XTCE ``-1`` leaves nothing for later entries,
   so such a value must be the packet's last. Yamcs reads ``-1`` as a
   size that the algorithm finds, so entries may follow it; documents
   written for Yamcs say so with the ``yamcs`` dialect (see Dialects,
   below). Transformed values may be optional, repeated, array elements
   or aggregate members.

   On a container's ``BinaryEncoding``, its own entries (not a base
   container's) are read from what the transform makes of its bytes,
   and its ``SizeInBits`` is their size on the wire; an included
   container needs one, while a packet's own container without one
   takes the rest of the packet. A transformed container may have an
   ``IncludeCondition``, as the demo's ``OptZip`` does, and may be
   repeated, as in its ``ZipRepeat``: each copy is then transformed on
   its own, and is its ``SizeInBits`` on the wire. It can't hold a length
   field, a checksum or an entry located from the container's end, or be
   included in a repeated container.

   Either way the transformed data starts on a byte boundary, and a
   transform that makes more than fits fails with ``TooLong``, one that
   can't (a function giving nothing) with ``InvalidValue``, and one that
   takes or makes other than the size on the wire with ``BadSize``.

   A Rust or C transform puts what it makes in the room it is given and
   returns its size; when that doesn't fit, it writes nothing and returns
   the size it needs, and when it can't, it returns ``None`` or
   ``XTCE_PK_NO_TRANSFORM``. A decoding transform also says how many of
   the bytes it was given it took. Encoding transforms in the output
   buffer, after what it writes, so ``MAX_BYTES`` counts that room: for
   data that takes the rest of the packet or whose size the transform
   finds, the maximum field size, which ``tcs-xtce.max-field-bytes`` can
   lower. The demo's ``ZipBlock`` doubles every byte of its entries on
   the wire, its ``XorBlock`` has a ``XorBlob`` masked by XOR, and its
   ``StuffBlock`` has bytes framed by their length, a time in
   milliseconds and a delay in a varint, with entries after each.

Dialects
   Where XTCE leaves a meaning open and a ground system settles it, an
   ``AncillaryData`` named ``tcs-xtce.dialect`` on a document's root
   ``SpaceSystem`` says whose reading to follow. The one dialect is
   ``yamcs``, which reads as Yamcs does:

   * a transformed value's ``SizeInBits`` of ``-1``: the algorithm finds
     the size, and entries may follow (see Transform algorithms);
   * a string's ``Variable`` size from ``_yamcs_ignore``: the schema
     requires a size, and this one stands for none, so the
     ``LeadingSize`` or ``TerminationChar`` alone gives it, as for the
     demo's ``RecLabel``;
   * binary data of size ``-1`` whose type has an ``AncillaryData``
     named ``Yamcs:EmbeddedTc``: a complete CCSDS packet, whose packet
     length field gives its size, so entries may follow it. Encoding
     sets that field from the value's size, as Yamcs's command
     post-processor does; the sequence count and any CRC stay as the
     application gives them. The value must be from 7 bytes to the
     maximum field size. The demo's ``Embed`` command carries one;
   * a parameter reference such as ``CCSDS_Packet_ID/Version`` that
     names no parameter: a member of an aggregate parameter, as
     ``CCSDS_Packet_ID.Version`` names it in XTCE.

   .. code-block:: xml

      <SpaceSystem name="Rover">
        <AncillaryDataSet>
          <AncillaryData name="tcs-xtce.dialect">yamcs</AncillaryData>
        </AncillaryDataSet>
        ...

   As with ``tcs-xtce.max-field-bytes``, every root of a mission that
   gives it must agree, and on any other ``SpaceSystem`` it is an error.
   ``--dialect`` gives it for documents that can't be changed; a document
   that gives one must agree with it.

Custom algorithms
   A ``CustomAlgorithm`` names code that the document doesn't hold, so
   the application supplies each one as a function, a *hook*, named in
   snake case from the algorithm's name (``IsEven`` gives ``is_even``):

   * a criterion (a ``CustomAlgorithm`` in a ``RestrictionCriteria``,
     ``IncludeCondition``, ``ContextMatch`` or other match criteria):
     whether it holds for its inputs;
   * a ``CustomAlgorithm`` of an ``AlgorithmSet``: its outputs, which
     ``algorithms()`` gives as it gives a ``MathAlgorithm``'s;
   * a ``CustomAlarm``: the alarm level, 0 (normal) to 5 (severe),
     raised to at least that;
   * a ``Checksum`` named ``custom``, by its ``InputAlgorithm``: the
     value of the bits it covers;
   * a ``FromBinaryTransformAlgorithm``: how many of the bytes given it
     takes and the bytes, or for a time type the seconds, it makes of
     them; a ``ToBinaryTransformAlgorithm``: the bytes it makes of the
     bytes, or the seconds, given (see Transform algorithms).

   Inputs are the ``InputSet``'s values of the packet (integers, floats,
   enumerations and booleans, which must be present) and ``Constant``\ s,
   in order, as doubles. The generated code has a place for each hook:

   .. list-table::
      :header-rows: 1
      :widths: auto

      * - Hook
        - Rust (``hooks::IS_EVEN.set(f)``)
        - C/C++ (``P_hook_is_even = f``)
        - Python (``HOOKS["is_even"] = f``)
      * - criterion
        - ``fn(&[f64]) -> bool``
        - ``bool f(xtce_pk_inputs)``
        - ``f(inputs) -> bool``
      * - algorithm
        - ``fn(&[f64], &mut [f64]) -> bool``
        - ``bool f(xtce_pk_inputs, double* out)``
        - ``f(inputs) -> outputs or None``
      * - alarm
        - ``fn(&[f64]) -> u8``
        - ``int f(xtce_pk_inputs)``
        - ``f(inputs) -> int``
      * - checksum
        - ``fn(&[u8], usize, usize) -> u64``
        - ``uint64_t f(const uint8_t*, size_t, size_t)``
        - ``f(data, start, end) -> int``
      * - from binary
        - ``fn(&[u8], &mut [u8]) -> Option<(usize, usize)>``, how many
          it took and made
        - ``size_t f(const uint8_t* in, size_t n, size_t* consumed,
          uint8_t* out, size_t cap)``
        - ``f(data) -> (taken, bytes) or None``
      * - to binary
        - ``fn(&[u8], &mut [u8]) -> Option<usize>``
        - ``size_t f(const uint8_t* in, size_t n, uint8_t* out, size_t
          cap)``
        - ``f(data) -> bytes or None``
      * - time from binary
        - ``fn(&[u8]) -> Option<(usize, f64)>``, how many it took and
          the seconds
        - ``bool f(const uint8_t* in, size_t n, size_t* consumed,
          double* s)``
        - ``f(data) -> (taken, seconds) or None``
      * - time to binary
        - ``fn(f64, &mut [u8]) -> Option<usize>``
        - ``size_t f(double s, uint8_t* out, size_t cap)``
        - ``f(seconds) -> bytes or None``

   A checksum hook gets the packet's bytes and the range of bits, from
   ``start`` to ``end``; an algorithm hook returns false (or None) when it
   has no outputs. In C, ``xtce_pk_inputs`` holds ``n`` inputs in ``v``,
   and the hooks are variables that one file defines: define
   ``P_HOOKS`` (``DEMO_HOOKS`` for a prefix ``demo_``) before it includes
   the generated header. Decoding or encoding a packet whose criteria or
   checksums need a hook that isn't set fails with ``NoAlgorithm``;
   ``alarms()`` and ``algorithms()`` count a missing hook as raising
   nothing and giving no outputs. The demo's ``Hooked`` packet uses one
   of each, and the test tools supply them. The algorithms' text and
   ``ExternalAlgorithmSet`` only document them.

Streams
   A ``StreamSet``'s ``FixedFrameStream`` and ``VariableFrameStream``
   become stream types (``DownlinkStream`` for a stream ``Downlink``)
   that find frames in a stream of bytes: ``push`` takes the next bytes,
   in pieces of any size, and gives each frame they complete, as bytes
   (padded with zeros to whole bytes), to decode as the stream's
   ``ContainerRef`` (``CONTAINER``), or as one of the containers of its
   ``ServiceRef``'s service (``CONTAINER`` then names the service). In
   Rust ``push(&data, |frame| ...)``; in C ``P_DownlinkStream_init(&s)``
   then ``P_DownlinkStream_push(&s, data, len, fn, ctx)``; in Python
   ``s.push(data)`` returns the frames. Neither allocates.

   Both read the bits most significant first, invert them when
   ``inverted`` says to, and decode the ``pcmType``: ``NRZL`` (the
   level is the bit), ``NRZM`` (a one is a change of level), ``NRZS``
   (a zero is), or a bi-phase code, in which each bit is two half bits:
   ``BiPhaseL`` (Manchester: a one is high then low, a zero low then
   high), ``BiPhaseM`` (every bit starts with a change of level, and a
   one has another in mid bit) or ``BiPhaseS`` (a zero has). A bi-phase
   stream must start on the first half of a bit.

   A fixed stream finds its frames by the ``SyncPattern``
   (``patternLengthInBits`` of ``pattern``, and of ``mask`` when given,
   both truncated from the left; bits of the pattern that a shorter mask
   doesn't reach are compared), at ``bitLocationFromStartOfContainer``
   in each ``frameLengthInBits`` frame, with up to
   ``maxBitErrorsInSyncPattern`` bits wrong. The pattern may have any
   length. It works as the schema
   says: in Search it looks for the pattern at every bit; in Verify it
   needs ``verifyToLockGoodFrames`` frames in a row whose patterns are
   one frame apart, give or take ``syncApertureInBits`` (the nearest
   first, and always after the frame before), and then locks;
   in Lock it gives each frame whose pattern it found, and a missed
   pattern sends it to Check, where ``checkToLockGoodFrames`` good
   patterns in a row lock it again (the missed frame isn't given) and a
   missed one sends it back to Search. With ``AutoInvert``, searching
   ``badFramesToAutoInvert`` frames' worth of bits in vain inverts the
   bits. The demo's ``Downlink`` has 128-bit frames led by the CCSDS
   sync marker; ``Deep`` has Bi-phase-L frames led by an 80-bit pattern
   with a byte masked out, and ``Beacon`` inverted Bi-phase-S frames led
   by a 72-bit one, which may slip up to 100 bits. A frame is given once
   the bits that could hold the next pattern have come.

   A variable stream's frames lie between flags of ``flagSizeInBits``
   ones (or zeros), with a bit of the other kind on either side, as
   HDLC's are with six ones: the bit of the other kind stuffed after
   ``flagSizeInBits - 1`` in a row in a frame is removed, and more in a
   row abort the frame, as does a frame larger than the container's
   largest packet; frames are found again from the next flag. A
   variable stream has no sync states, so its ``SyncStrategy``'s other
   settings don't apply. The demo's ``Uplink`` carries CCSDS packets
   between flags, NRZ-M coded, and ``Relay`` Bi-phase-M coded.

   A ``CustomStream`` is processing XTCE leaves to the application, such
   as descrambling or decompression: its type gives the bytes that the
   application's function for its ``DecodingAlgorithm`` decodes from its
   input. They feed the stream its ``decodedStreamRef`` names, and its
   input is what its ``encodedStreamRef`` stream gives, unless that is
   the custom stream itself. A frame stream's ``StreamRef`` names the
   stream whose output is its input: another stream's frames, or a custom
   stream's decoded bytes. Each stream type takes the raw stream's bytes,
   and holds the types of the streams that feed it, so the application
   pushes bytes into the last stream of a chain. Streams only find
   frames, so an ``EncodingAlgorithm`` is not used.

   An ``InvertAlgorithm`` under ``AutoInvert`` replaces the complement of
   each bit once the stream is inverted: the application's function gives
   each bit instead.

   A stream type whose chain needs functions takes them when it is made,
   named for their streams (``scrambled_decode``, ``clear_invert``):

   .. list-table::
      :header-rows: 1
      :widths: auto

      * - Language
        - Decoding function
        - Inverting function
      * - Rust
        - ``fn(&[u8], &mut dyn FnMut(&[u8]))``: gives the decoded bytes
          to the second argument, in pieces of any size
        - ``fn(u8) -> u8``
      * - C/C++
        - ``xtce_pk_stream_decode_fn``, and a context it is called with:
          ``void f(void* ctx, const uint8_t* data, size_t len, void
          (*out)(void* out_ctx, const uint8_t* bytes, size_t n), void*
          out_ctx)``
        - ``uint8_t (*)(uint8_t bit)``
      * - Python
        - a function from ``bytes`` to the decoded ``bytes``
        - a function from a bit to a bit

   The demo's ``Scrambled`` stream XORs each byte with 0x55; its
   ``Descramble`` gives the ``Clear`` stream, whose frames carry the
   ``Nested`` stream's HDLC frames.

   ``tests/stream_check.sh`` checks that every runtime finds the same
   frames, in ``tests/mission/streams.txt`` and in random streams with
   slips, bit errors, inversions and aborts.

Messages
   A ``MessageSet``'s ``Message`` picks out its container by its
   ``MatchCriteria``. It is generated as a packet derived from that
   container with no entries of its own, so decoding the container's kind
   gives the message when its criteria hold, and ``==`` comparisons fix
   their values when encoding. The demo's ``PulseStart`` and
   ``PulseStop`` are ``Pulse`` packets of two kinds.

Instances
   A ``ParameterInstanceRef`` (in a criterion, a size, a location or a
   calibrator) names the value of a parameter decoded last before it, its
   ``instance`` 0. A negative ``instance`` looks further back: -1 is the
   value decoded before that one, in the same packet. A packet that
   doesn't hold the value that many times before the reference, or a
   positive ``instance`` (a value yet to come), is not supported, since
   that value would come from another packet. The demo's ``History``
   holds ``HistVal`` twice, and the first decides whether ``HistPrev`` is
   there and gives ``HistData``'s size.

Name references
   Absolute (``/Root/Sub/name``), relative (``Sub/name``, ``../name``),
   and unqualified names. An unqualified name, or a relative one that
   doesn't start with ``.`` or ``..``, is looked up from the current
   SpaceSystem, then from each enclosing one. References may cross
   documents (see `Missions in several documents`_).

Not supported
   ``IndirectParameterRefEntry`` chosen by an integer without an
   ``aliasNameSpace`` when no parameter's name is a number, instances of
   values in other packets, repeated
   segment entries with an ``order`` (which XTCE leaves undefined) or
   located from the end of their container with a count or ``Offset``
   that varies, transformed containers included in a repeated container
   or holding a length field, a checksum or an entry located from the
   end,
   ``ErrorDetectCorrect`` in a data encoding (which XTCE deprecates), least-significant-bit-first strings
   and binary data that may not start on a byte boundary, integers of
   more than 64 bits used other than as values (see "Integer encoding"),
   and a location from the end of an included container whose size
   varies.

Known limitations
-----------------

* A length field needs the ``tcs-xtce.length`` ``AncillaryData``; without
  it, it is an ordinary field that the application sets.
* Decoding ignores bytes after the packet. Spare bits and gaps are
  written as zeros, so re-encoding a decoded packet can change them.
* Binary fields default to empty and must be set to their exact size
  before encoding. Arrays default to empty, and must have the number of
  elements their type gives, even when it is fixed.
* A field that gives a size or count is computed when encoding only in
  the cases described under `Generated packet types`_; otherwise set the
  field and the data to agree.
* Enumerated values must fit in a signed 64-bit integer.


Integers of any size
====================

Rust has integers of up to 128 bits, and C and C++ of up to 64. For
wider ones, the Rust runtime uses the `bnum <https://docs.rs/bnum>`_
crate, and the C and C++ runtime its own header-only library that
mirrors bnum, so that all of them hold and compute such integers alike.
Python's ``int`` has no size limit.

The library is two headers in ``cpp/include/xtce``, which need only the
standard library and allocate nothing:

``xtce/bnum.h``
   C11 functions, which also compile as C++, on integers of ``n`` bytes
   held least significant first, two's complement when signed. Each is
   named after the bnum method it mirrors (``xtce_bn_wrapping_add`` is
   bnum's ``wrapping_add``) and takes its integers as byte pointers with
   their size and signedness:

   .. code-block:: c

      #include <xtce/bnum.h>

      XTCE_BN_TYPEDEF(u256, 32);  /* typedef struct { uint8_t bytes[32]; } */

      u256 a, b, sum;
      xtce_bn_from_str_radix(a.bytes, 32, false, "1e40", 4, 16);
      xtce_bn_from_u64(b.bytes, 32, 1);
      if (xtce_bn_overflowing_add(sum.bytes, a.bytes, b.bytes, 32, false))
              /* it overflowed */;

   They cover construction, casts between sizes, byte order, comparison,
   bit operations, shifts and rotations, counting bits, and addition,
   subtraction, multiplication, division (truncating and Euclidean),
   remainders, negation and absolute values, each in bnum's wrapping,
   overflowing, checked and saturating forms, and parsing and formatting
   in any radix from 2 to 36. ``overflowing_`` functions return whether
   the result overflowed, and ``checked_`` ones whether it succeeded.
   Functions that need room to work take a ``scratch`` buffer of ``n``
   bytes; the header says which may not be given the same integer twice.

``xtce/bnum.hpp``
   For C++20, ``xtce::bnum::Integer<S, N>``, like bnum's
   ``Integer<S, N>``: an integer of ``N`` bytes, signed if ``S``, with
   ``Uint<N>`` and ``Int<N>`` for short. It has bnum's methods under the
   same names, ``pow``, ``isqrt`` and the logarithms among them, and its
   operators wrap on overflow, as bnum's do in its ``Wrap`` overflow
   mode. Where bnum panics (dividing by zero, or a ``strict_`` method
   overflowing) it calls ``XTCE_BNUM_PANIC``, which aborts unless defined
   otherwise before the header is included.

   .. code-block:: cpp

      #include <xtce/bnum.hpp>

      using U256 = xtce::bnum::Uint<32>;
      U256 x = U256::one() << 160;
      std::optional<U256> y = x.checked_mul(x);  // none: 2^320 overflows
      std::cout << x.pow(3) << "\n";               // 0: it wraps

Generated packets hold integers of more than 64 bits in these types (see
"Integer encoding" under `Supported XTCE`_): bnum's, past 128 bits, in
Rust, and in C structs declared with ``XTCE_BN_TYPEDEF``, ``demo_uint200``
for a 200-bit unsigned integer, whose ``bytes`` the functions take.

Sizes are whole bytes; bnum's third parameter, a width in bits that
isn't, has no counterpart. ``make bnum-check`` checks the library
against bnum (see `Testing`_). It also found one difference, kept
deliberately: bnum 0.14's ``from_le_slice`` and ``from_be_slice`` give a
signed integer the truncated bytes of a longer negative slice whose
value doesn't fit (``I8::from_le_slice(&[0x05, 0xff])`` is ``Some(5)``,
though the bytes are -251), where the library gives none.


CCSDS time codes (tcs-ccsds)
============================

tcs-ccsds is a second library, in ``ccsds/``, for CCSDS standards that XTCE
knows nothing about. It doesn't depend on tcs-xtce: it is plain functions
over bytes, which a packet codec can use as hooks, or any program can use
by itself. It has the binary time codes of CCSDS 301.0-B-4, *Time Code
Formats*:

CUC (CCSDS Unsegmented time Code)
   1 to 7 coarse octets of whole seconds, then 0 to 10 fine octets of
   binary fractions of a second.

CDS (CCSDS Day Segmented time code)
   Days in 16 or 24 bits, milliseconds of the day in 32, and optionally
   microseconds (16 bits) or picoseconds (32) of the millisecond.

A time is seconds since the code's epoch, as a double: the CCSDS epoch
(1958-01-01, TAI) or the agency's (the mission's), as the code's P-field,
its preamble, says. A layout gives its P-field and decodes and encodes
its T-field; decoding a time that starts with its P-field reads the layout
from it. Decoding gives the octets read and the seconds; encoding gives
the octets, rounding to the layout's unit, halves up, and gives nothing
for a negative time or one the layout can't hold. A CDS time's
milliseconds may reach a leap second's.

.. list-table::
   :header-rows: 1
   :widths: auto

   * - Language
     - Library
     - Decode a time with its P-field
   * - Rust
     - crate ``tcs-ccsds``, ``tcs_ccsds::time``
     - ``decode_cuc(&data) -> Option<(usize, f64)>``
   * - C/C++
     - ``ccsds/include/ccsds/time.h``, ``ccsds_`` names
     - ``bool ccsds_decode_cuc(data, n, &k, &s)``
   * - Python
     - package ``tcs_ccsds``, ``tcs_ccsds.time``
     - ``decode_cuc(data) -> (k, s) or None``

The layouts are ``Cuc`` and ``Cds`` (``ccsds_cuc`` and ``ccsds_cds``),
with ``pfield``, ``from_pfield``, ``decode_t``, ``encode_t`` and
``encode``. In Rust and C, encoding into too small an output writes
nothing and gives the octets it needs, more than the room given.

``make ccsds-check`` runs ``tests/ccsds_check.sh``: Rust, C, C++ and
Python decode and encode the cases of ``tests/ccsds/known.txt``, worked
out by hand from the formats (extended P-fields, a leap second, rounding
that carries, times a layout can't hold), and must give their results;
on random cases from ``tests/ccsds_cases.py`` they must agree with each
other.


xtce-digest
===========

``xtce-digest`` prints an MD5 digest of what each XTCE document given to
it means, so that a change to a mission database that would change the
generated data structures and methods can be noticed, and an edit that
only rearranges the document can be told apart from one that doesn't.

Two programs that exchange packets can use it to check that they were
built from the same definition: if their documents have the same
digest, they agree on every packet's fields, types and order (see
`Exchanging data between programs`_).

.. code-block:: sh

   cd generator
   python3 -m xtce_digest FILE...
   python3 -m xtce_digest --set FILE...

   python3 -m xtce_digest ../tests/mission/demo.xml ../examples/simple-string/simple_string.xml

It reads the files in the order given. For each one it prints the file
name as given, a colon, a space and the digest, in hex:

.. code-block:: text

   ../tests/mission/demo.xml: 6d2d1d775d75a68089fddade8e95c585

Each file gets its own digest, computed afresh, so a file gives the same
digest wherever it appears in the list. A file that can't be read, isn't
well-formed XML or isn't valid XTCE is reported on standard error and
ends the command with status 1; the files before it have been printed,
and the ones after it are not read. Status 0 means every file was
digested.

A mission split across several documents (see `Missions in several
documents`_) has one digest for the whole set, which ``--set`` prints,
alone on a line. It is the MD5 digest of the documents' canonical
encodings (described below), sorted by their bytes and joined. So the
order the files are given in doesn't matter, a set of one document has
that document's digest, and changing any document of the set changes it.
Two programs built from the same set of documents get the same digest
for it, though the digests of the individual documents would not tell
them that a document is missing from one of the sets.

Every runtime computes the same digest, so that a program in any
language can compute its document's digest, send it, and compare it with
the digest it receives. Each has functions that give the digest of a
document in memory or in a file, as 16 bytes (to send) or as 32 lowercase
hex digits (to print):

.. list-table::
   :header-rows: 1
   :widths: auto

   * - Language
     - Functions
     - Errors
   * - Rust (``tcs_xtce::digest``)
     - ``digest_bytes(&[u8]) -> [u8; 16]``, ``digest(&[u8])``,
       ``digest_string(&str)``, ``digest_file_bytes(path)``,
       ``digest_file(path)``, ``hex(&[u8; 16])``; for a set,
       ``digest_set_bytes(&[D])``, ``digest_set(&[D])`` (each ``D`` bytes),
       ``digest_files_bytes(&[P])`` and ``digest_files(&[P])`` (each ``P``
       a path)
     - ``Err(tcs_xtce::Error)``
   * - C and C++ (``xtce/digest.h``, which needs no library)
     - ``xtce_digest_bytes(data, len, out, &err)``,
       ``xtce_digest(data, len, hex, &err)``,
       ``xtce_digest_string(text, hex, &err)``,
       ``xtce_digest_file_bytes(path, out, &err)``,
       ``xtce_digest_file(path, hex, &err)``,
       ``xtce_digest_hex(out, hex)``; for a set,
       ``xtce_digest_set_bytes(docs, lens, n, out, &err)``,
       ``xtce_digest_set(docs, lens, n, hex, &err)``,
       ``xtce_digest_files_bytes(paths, n, out, &err)`` and
       ``xtce_digest_files(paths, n, hex, &err)``; in C++ also
       ``xtce::digest(std::string_view)``,
       ``xtce::digest_file(path)``, ``xtce::digest_set(texts)`` and
       ``xtce::digest_files(paths)`` (each a
       ``std::vector<std::string>``), returning ``std::string``
     - ``-1`` and ``err.message``; C++ throws ``std::runtime_error``
   * - Python (``xtce.digest``)
     - ``digest_bytes(data)``, ``digest(data)``, ``digest_string(text)``,
       ``digest_file_bytes(path)``, ``digest_file(path)``; for a set,
       ``digest_set_bytes(documents)``, ``digest_set(documents)``,
       ``digest_files_bytes(paths)`` and ``digest_files(paths)``
     - ``XtceError``, and ``OSError`` for a file that can't be read

.. code-block:: python

   from xtce.digest import digest_file, digest_file_bytes

   digest_file("tests/mission/demo.xml")        # '6d2d1d775d75a68089fddade8e95c585'
   digest_file_bytes("tests/mission/demo.xml")  # the same 16 bytes

The document must be well-formed XML 1.0 in UTF-8 whose root element is
``SpaceSystem``; a document that declares another encoding, or has a
DOCTYPE, is rejected. The functions don't check the document against the
XTCE schema: ``xtce-digest`` does, before it digests each file. Computing
a digest allocates memory, so programs that must not allocate compute it
at start-up.

The digest covers every element, attribute and piece of text, but not
what leaves the document meaning the same:

* comments and processing instructions;
* whitespace (spaces, tabs, carriage returns and line feeds) between
  elements, and at the start and end of text;
* the order of attributes, and the namespace prefixes used;
* the order of the children of an element that holds an unordered
  collection: the nested ``SpaceSystem`` elements of a
  ``SpaceSystem``, the ``…Set`` elements (``ParameterTypeSet``,
  ``ParameterSet``, ``ContainerSet``, ``MetaCommandSet``,
  ``ArgumentTypeSet``, ``AncillaryDataSet``, ``ValidRangeSet`` and the
  others whose members are named or independent), and
  ``ANDedConditions``, ``ORedConditions``, ``ComparisonList``,
  ``ArgumentAssignmentList``, ``EnumerationList``,
  ``EnumerationAlarmList`` and ``PolynomialCalibrator``. Their children
  are taken in a fixed (sorted) order instead.

The children of every other element keep their order, as it carries
meaning: the entries of an ``EntryList``, the members of a
``MemberList``, the dimensions of a ``DimensionList``, the arguments of
an ``ArgumentList``, spline points, and the cases of a
``DiscreteLookupList``, ``ContextCalibratorList`` or ``ContextAlarmList``,
where the first match wins. The full list is ``UNORDERED`` in
``generator/xtce_digest/__init__.py``.

The digest errs on the side of changing: anything a document says is
covered, including descriptions, aliases and ancillary data, even where
tcs-xtce ignores them. An attribute given its default value explicitly
also changes the digest, though the meaning is the same.


Examples
========

``examples/`` holds self-contained examples. Every example supports all
four languages ``tcs-xtce`` generates: C, C++, Rust and Python 3, one for
each ``--lang`` value. ``make examples`` fails if an example is missing
one. Each has an XTCE file, a program per language that uses the code
``tcs-xtce`` generates from it, a ``README.rst`` that explains the example
and how to run each program, and a ``Makefile``. The C and C++ programs share one source
file, which is compiled once as C11 against the ``--lang c`` header and once
as C++20 against the ``--lang c++`` header. ``make`` in an example's
directory, or ``make examples`` at the top level:

* regenerates the code for all four languages if the XTCE file or the
  generator changed;
* builds and runs the C, C++, Rust and Python programs;
* checks that their output is identical.

Every example that sends packets first sends the 16-byte digest of its
XTCE file, and the side that receives them checks it against the digest
of its own copy before it reads a packet (see `Exchanging data between
programs`_).

``make html`` in an example's directory converts its ``README.rst`` into
``README.html``. ``make`` in ``examples/`` itself does that for every
example and for ``examples/README.rst``, the `index of examples
<../examples/README.html>`__.

The examples are:

* `enum-labels <../examples/enum-labels/README.html>`__: a status whose
  mode is an enumeration that its include conditions, a derived packet
  and an alarm all compare by label, with a label for a range of values
  and a value that no label names.
* `housekeeping <../examples/housekeeping/README.html>`__: status packets
  with a packet length field, calibrated temperatures, voltages and powers,
  and a field present only when a heater is on, stored one after another
  in a stream and split apart again by their length fields.
* `simple-string <../examples/simple-string/README.html>`__: a structure
  holding one string of up to 8 bytes, serialized into and deserialized
  from managed buffers.
* `split-mission <../examples/split-mission/README.html>`__: a rover's
  PUS telemetry described in three documents (data types, packet headers
  and the rover's packets), with types derived by ``baseType``, recorded
  after the digest of the set of documents.
* `to-from <../examples/to-from/README.html>`__: words of varying
  length, with a leading size tag, written by the program in one language
  and read by the program in another, for any pair of C, C++, Rust and
  Python.
* `variable-array <../examples/variable-array/README.html>`__: two
  two-dimensional arrays whose dimensions both vary, the lines and words of
  a paragraph, sent one after the other over one TCP connection from one
  thread to another, each after its length.

Each is summarized below; its README has the details.

``housekeeping``
----------------

``examples/housekeeping/housekeeping.xml`` describes a container,
``Status``:

::

   PacketLength    16 bits   bytes after this field; computed when encoding
   HeaterOn         4 bits   0 for false, anything else for true
   Temperature     12 bits   raw / 16 - 50 degrees C
   BusVoltage       8 bits   raw / 8 volts
   HeaterPower     16 bits   raw / 64 watts, only when HeaterOn is true

``PacketLength`` is a packet length field (see `Generated packet types`_):
its ``tcs-xtce.length`` ``AncillaryData`` makes the generated code compute
it when encoding and check it when decoding, and it isn't a field of the
structure. ``Temperature``, ``BusVoltage`` and ``HeaterPower`` have
``PolynomialCalibrator``\ s, so the structure holds degrees, volts and
watts while the packet holds raw counts. ``HeaterPower`` has an
``IncludeCondition`` on the boolean ``HeaterOn``, so it is optional: ``Option<f64>``
in Rust, ``has_heater_power`` and ``heater_power`` in C/C++, and
``Optional[float]`` in Python.

Each program starts a stream with the digest of ``housekeeping.xml`` and
encodes four readings into it, one packet after another, then walks the
stream: it checks the digest against that of its own copy, then reads each
packet's ``PacketLength`` to
find where the packet ends, decodes it, and checks it against the reading,
rounded to the nearest step of each calibrator. It then shows the errors
for a missing heater power, a temperature out of range, a packet cut short
and a wrong ``PacketLength``:

::

   XTCE digest: 27a384a7bcaa921a001f482d2d82e282, the same on both sides
   decode at 21: 7 bytes -> Status{heater_on=true, temperature=-12.25, bus_voltage=27.5, heater_power=4.5}
   decode at 28: 7 bytes -> Status{heater_on=true, temperature=-20.0, bus_voltage=26.875, heater_power=12.734375}
   encode Status{heater_on=true, temperature=20.0, bus_voltage=28.0, heater_power=none} -> error:Missing (HeaterPower is required by its include condition)

See the `housekeeping README <../examples/housekeeping/README.html>`__ for
more.

``simple-string``
-----------------

``examples/simple-string/simple_string.xml`` describes a container,
``SimpleString``, holding one parameter, ``Text``. ``Text`` is a string
that always occupies 64 bits and ends at a NUL byte, so it holds up to
8 bytes:

.. code-block:: xml

   <StringParameterType name="String8Type">
     <StringDataEncoding encoding="UTF-8">
       <SizeInBits>
         <Fixed><FixedValue>64</FixedValue></Fixed>
         <TerminationChar>00</TerminationChar>
       </SizeInBits>
     </StringDataEncoding>
   </StringParameterType>

``tcs-xtce`` generates a ``SimpleString`` structure in each language:

* Rust: ``struct SimpleString { pub text: String }``
  (``rust/src/simple_string.rs``)
* C/C++: ``simple_string_SimpleString``, whose ``text`` is a
  ``simple_string_str8`` (``c/simple_string.h`` for C,
  ``cxx/simple_string.h`` for C++)
* Python: ``@dataclass class SimpleString`` with ``text: str``
  (``python/simple_string.py``)

Each has the methods described in `Methods`_. The Rust and Python
programs use a ``Codec``, whose two buffers are preallocated to
``MAX_BYTES`` (8). The C program (``c/main.c``) uses two arrays of
``simple_string_SimpleString_MAX_BYTES`` bytes instead. Each program
serializes several strings into the codec's output buffer and
deserializes them from its input buffer. It shows that a 9-byte string
fails with ``TooLong``, and that the buffers were never reallocated:

::

   serialize "Hi" -> 4869000000000000
   deserialize 4869000000000000 -> SimpleString{text="Hi"}
   serialize "ABCDEFGHI" -> error:TooLong (Text is too long for its encoding)
   buffers reused without reallocation: true

See the `simple-string README <../examples/simple-string/README.html>`__
for more.

``to-from``
-----------

``examples/to-from/to_from.xml`` describes a container, ``Word``,
holding one parameter, ``Text``. ``Text`` is a string of varying length
with a 32-bit size tag before it. The tag and the string together occupy
at most 288 bits, so a word holds up to 32 bytes:

.. code-block:: xml

   <StringParameterType name="WordType">
     <StringDataEncoding encoding="UTF-8">
       <Variable maxSizeInBits="288">
         <LeadingSize sizeInBitsOfSizeTag="32"/>
       </Variable>
     </StringDataEncoding>
   </StringParameterType>

The example shows that code generated for one language reads what code
generated for another wrote. Each language's program takes one argument.
With ``write``, it writes the digest of ``to_from.xml``, then serializes
each of the 69 words of the lorem ipsum paragraph into a ``Word`` packet
and writes the packets, to standard output. With ``read``, it checks the
digest against that of its own ``to_from.xml``, then reads packets from
standard input, using each packet's
size tag to find where it ends, and deserializes them. It prints each word
as it arrives, with a space before every word but the first, and a newline
when the input ends. It checks that every word arrives intact and in
order, and exits with status 1 if one doesn't.

The script ``examples/to-from/to-from`` takes two arguments, each ``c``,
``c++``, ``rust`` or ``python``: the language that reads (to), then the
language that writes (from). It prints them back on one line, then pipes
the writer's output to the reader:

::

   $ ./to-from python c
   to: python from: c
   XTCE digest: ad4767eca527cdc6d62f3bf78ad5ff4f, the same on both sides
   Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do ...

``make`` in the example's directory checks that the four writers write the
same bytes, and runs all 16 pairs of reader and writer.

See the `to-from README <../examples/to-from/README.html>`__ for more.

``variable-array``
------------------

``examples/variable-array/variable_array.xml`` describes a container,
``Paragraph``, holding a two-dimensional array whose dimensions both vary:
an array of lines, each an array of words. A count before each array gives
its number of elements, so every line can have its own number of words:

::

   LineCount               8 bits, 0 to 8
   Lines[LineCount]
     WordCount             8 bits, 0 to 20
     Words[WordCount]      strings of up to 15 bytes, each after a 32-bit size tag

An array type can have only one dimension whose size varies (see
"Arrays" under `Supported XTCE`_), so the inner array is a member of an
aggregate, ``Line``, after its count, ``WordCount``, and the outer array is
an array of ``Line``:

.. code-block:: xml

   <AggregateParameterType name="Line">
     <MemberList>
       <Member name="WordCount" typeRef="WordCountType"/>
       <Member name="Words" typeRef="WordsType"/>
     </MemberList>
   </AggregateParameterType>
   <ArrayParameterType name="LinesType" arrayTypeRef="Line">
     <DimensionList>
       <Dimension>
         <StartingIndex><FixedValue>0</FixedValue></StartingIndex>
         <EndingIndex>
           <DynamicValue>
             <ParameterInstanceRef parameterRef="LineCount"/>
             <LinearAdjustment intercept="-1"/>
           </DynamicValue>
         </EndingIndex>
       </Dimension>
     </DimensionList>
   </ArrayParameterType>

``WordsType`` is sized by ``WordCount`` in the same way. ``ValidRange``\ s
on the two counts bound the arrays, and so ``MAX_BYTES`` (3049) and the
size of the C structures.

Each program builds two ``Paragraph``\ s from the lorem ipsum paragraph
in five lines, one word per element: the first three lines, then the last
two. One thread opens a TCP connection on the loopback interface, writes
the digest of ``variable_array.xml``, and, for each array, writes its
length, then the whole ``Paragraph`` in a single write. The length is a second container in the same XTCE file, ``Length``,
holding a 32-bit ``ByteCount``, so it too is encoded and decoded with the
generated code. Another thread checks the digest against that of its own
copy, then reads and decodes each 4-byte ``Length``,
then reads that many bytes, deserializes the ``Paragraph`` and checks
every line and word.
It then prints each inner array as a line, with spaces between the words,
or exits with status 1 if anything differs. The lengths let the receiver
find where each packet ends, since a TCP stream doesn't keep writes apart:

::

   XTCE digest: 904beed3fc463cd7addc91dfca67c304, the same on both sides
   Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut
   labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco
   laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in
   voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat
   cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.

See the `variable-array README <../examples/variable-array/README.html>`__
for more.

``enum-labels``
---------------

``examples/enum-labels/modes.xml`` describes a probe's ``Status``, whose
``Mode`` is an enumeration (``SAFE``, ``IDLE``, ``SCIENCE``,
``DOWNLINK``, and ``TEST`` for the values 16 to 31). Everything that
depends on the mode names a label: ``Target`` is there only in
``SCIENCE`` mode and ``Station`` only in ``DOWNLINK`` mode, a
``SafeStatus`` is a ``Status`` in ``SAFE`` mode (so its structure has no
mode field), and the mode's alarm is a warning in ``SAFE`` mode and a
watch in ``TEST`` mode:

.. code-block:: xml

   <ParameterRefEntry parameterRef="Target">
     <IncludeCondition>
       <Comparison parameterRef="Mode" value="SCIENCE"/>
     </IncludeCondition>
   </ParameterRefEntry>

The programs name labels too, through the generated constants
(``ModeType::SCIENCE``, ``probe_ModeType_SCIENCE``,
``ModeType.SCIENCE``), so renumbering the modes changes the bytes on the
wire and nothing else. They print the labels of a few values, encode a
status in each mode, decode each as a ``StatusKind`` with its alarm, and
decode a value in ``TEST``'s range and one that no label names, which
decoding keeps:

::

   mode 20 is TEST
   mode 40 has no label
   ...
   decode 000901 -> SafeStatus{battery=9, target=none, station=none, reason=LOW_POWER} | alarms: mode=warning
   decode 2850 -> Status{mode=40, battery=80, target=none, station=none} | alarms: mode=normal

See the `enum-labels README <../examples/enum-labels/README.html>`__ for
more.

``split-mission``
-----------------

``examples/split-mission`` describes a rover's PUS telemetry in three
documents, as Yamcs's PUS example lays them out: ``dt.xml`` holds data
types, ``pus.xml`` the CCSDS primary header and PUS telemetry secondary
header, and ``rover.xml`` the rover's housekeeping and event packets,
derived from ``/PUS/pus-tm`` and picked by ``/PUS/pus-service`` and
``/PUS/pus-subservice`` (see `Missions in several documents`_). Its types
derive from ``dt.xml``'s with ``baseType``, adding units and a
``ValidRange``:

.. code-block:: xml

   <IntegerParameterType name="turns" baseType="/dt/uint16">
     <ValidRange minInclusive="0" maxInclusive="1000"/>
   </IntegerParameterType>

The generator reads the three together, the documents others build on
first, and names the mission ``Rover`` for the last. Each program checks
that the digest of the set doesn't depend on the order of the
documents, records three packets after it, and decodes them again as a
``CcsdsKind``; a wheel turned 1001 times can't be encoded:

::

   set digest: 65a49b2e175af6ee41498f1a0b8dd37b (dt.xml, pus.xml, rover.xml)
   set digest: the same for rover.xml, dt.xml, pus.xml
   ...
   decode at 39: 14 bytes -> Event{ccsds_version=0, ccsds_apid=100, ccsds_seq_flags=3, ccsds_seq_count=2, pus_version=2, pus_time_ref=0, pus_counter=2, event=OVERHEAT, event_data=85}
   encode Hk{..., left_turns=1001, right_turns=0} -> error:OutOfRange (value of left-turns is outside its valid range or encoding)

See the `split-mission README <../examples/split-mission/README.html>`__
for more.


Testing
=======

``make test`` runs:

* the unit tests of every language (``cargo test``, ``cpp/tests``,
  ``python -m unittest``);
* the packet compiler's tests (``generator/tests``), which compile small
  XTCE documents and check the model and the size report, including that
  malformed documents are reported as unsupported rather than crashing
  the generator;
* ``tests/cross_check.sh``: 21 generated XML documents that together use
  every element and every choice alternative of the schema. Each must
  validate with ``xmllint``. Each must round-trip through every
  language, with byte-identical output that also validates.
* ``tests/packet_check.sh``, which checks that:

  * the demo mission validates;
  * every language turns the hand-built packets in
    ``tests/mission/vectors.txt`` into exactly
    ``tests/mission/vectors.expected``: their text, re-encodings, alarm
    levels and algorithm outputs;
  * 3000 random packets give byte-identical results in Rust (generated
    both with and without dynamic allocation), Python, and the C tool
    compiled both as C and as C++;
  * the Rust codecs generated with ``--dynamic-allocations false``
    allocate nothing while decoding, encoding or describing a packet;
  * decoding a re-encoded packet gives the same values and the same
    bytes.

* ``tests/stream_check.sh``: every language finds the frames of the
  demo's streams in ``tests/mission/streams.txt``, exactly
  ``tests/mission/streams.expected``, and the same frames as the others in
  random streams with bit errors, slips, inversions and aborts.
* ``tests/digest_check.sh``: the Rust, C, C++ and Python digest functions
  give the same digest for the demo mission, the examples' XTCE files, the
  schema-covering samples and the documents in ``tests/digest``, which
  cover comments, CDATA, references, line ends, namespaces, unordered
  collections and documents that must be rejected; those also have known
  digests, in ``tests/digest/expected.txt``. Each also digests the sets
  of documents in ``tests/digest/sets.txt``.
* ``tests/float_check.sh``: the Rust, C, C++ and Python runtimes decode
  and encode every float format and size, giving the results the
  formats' standards give for the conversions in
  ``tests/float/known.txt`` (MIL-STD-1750A's own examples among them),
  and the same results as each other for random values from
  ``tests/float_cases.py``.
* ``tests/ccsds_check.sh``: tcs-ccsds's time codes give the known results
  and the same results in Rust, C, C++ and Python (see `CCSDS time codes
  (tcs-ccsds)`_).
* ``tests/bnum_check.sh``: the C functions of ``xtce/bnum.h`` (built as
  C and as C++) and the C++ class of ``xtce/bnum.hpp`` give the same
  results as the bnum crate for random cases from
  ``tests/bnum_cases.py``: every operation they have, on signed and
  unsigned integers of 1 to 32 bytes, with each type's edge values.

The ``packet_tool`` programs (``rust/examples``, ``cpp/tests``, built as
``cpp/build/packet_tool`` and ``cpp/build/packet_tool_cxx``, and
``python/tests``) run any vectors file:

.. code-block:: sh

   rust/target/release/examples/packet_tool tests/mission/vectors.txt

``make samples-check`` is not part of ``make test``, because it
downloads files. It checks ``tcs-xtce`` against 60 public XTCE documents:
the test documents and the examples' mission databases of `Yamcs
<https://github.com/yamcs/yamcs>`_ (PUS services, a lander simulation and
more), the mission database of the `Yamcs quickstart
<https://github.com/yamcs/quickstart>`_, the cFS (airliner) mission
databases of `yamcs-cfs <https://github.com/WindhoverLabs/yamcs-cfs>`_,
and the test documents and examples of the `XTCE Toolkit
<https://github.com/CesarCoelho/xtcetools>`_.
``tests/external/sources.txt`` lists them, pinned to commits with a
SHA-256 for each. They are downloaded once into
``tests/external/files/``. ``tests/samples_check.py`` then checks that,
for each document:

* the binding accepts it exactly when ``xmllint`` finds it valid against
  the XTCE 1.3 schema. As the binding doesn't check namespaces, older XTCE
  namespaces and a missing one are read as 1.3; and as it leaves facets
  (patterns and ranges of values) to ``xmllint``, a document that breaks
  only facets may load;
* a document the binding accepts writes back to an equal document;
* the compiler, the size report and every emitter produce output or
  report unsupported constructs, and never crash;
* the generated code compiles, without warnings: the C header as C11 and
  as C++, the Rust module against the ``tcs-xtce`` crate, and the Python
  module;
* the Rust, C, C++ and Python runtimes give it the same XTCE digest (or
  all reject it).

``tests/external/missions.txt`` lists missions split across several of
the documents (Yamcs's PUS example and the cFS event service), which are
compiled together as one, with the same checks of their compiler output,
generated code and set digest.

It prints a summary per document, with the constructs it found
unsupported and its digest, which must equal
``tests/external/expected.txt``. A crash, a disagreement with the schema,
a failed round trip, a failed compile or digests that differ always fails
the check. After an intended change, ``python3 -I
tests/samples_check.py --update`` rewrites ``expected.txt``; review its
diff.

License
=======

tcs-xtce -- the generator, the Rust crate, the C/C++ library and the Python
package -- is licensed under either of the Apache License, Version 2.0
(``LICENSE-APACHE``, or http://www.apache.org/licenses/LICENSE-2.0) or the MIT
license (``LICENSE-MIT``, or http://opensource.org/licenses/MIT), at your
option. The Rust crate and the Python package say so as ``MIT OR
Apache-2.0``.

Unless you explicitly state otherwise, any contribution intentionally submitted
for inclusion in the work by you, as defined in the Apache-2.0 license, shall be
dual licensed as above, without any additional terms or conditions.
