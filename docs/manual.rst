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
     - The ``xtce-digest`` command, which prints a digest of what an XTCE document means (see `xtce-digest`_)
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
   make fmt             # format Rust code
   make fmt-check       # verify Rust formatting (run by every `make rust`)
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

``tcs-xtce`` reads a mission's XTCE document and generates, for one
language:

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
   python3 -m tcs_xtce INPUT OUTPUT --lang LANG

   python3 -m tcs_xtce demo.xml demo.rs --lang rust
   python3 -m tcs_xtce demo.xml demo.h  --lang c
   python3 -m tcs_xtce demo.xml demo.py --lang python

Each run writes one language. Run it once per language you need.

.. list-table::
   :widths: auto

   * - ``INPUT``
     - The mission's XTCE document, read with the ``xtce`` Python package
   * - ``OUTPUT``
     - The file to write (optional with ``--size-report``)
   * - ``--lang LANG``
     - Required, except with ``--size-report``. ``rust`` writes a Rust module, formatted with ``rustfmt``
       (which must be installed). ``python`` writes a Python module.
       ``c`` and ``c++`` write the same file: a header that compiles both
       as C11 and as C++.
   * - ``--prefix P``
     - C/C++ only: prefix of every name the header defines (default: the
       root SpaceSystem name in snake_case followed by ``_``, such as
       ``demo_``)
   * - ``--skip-unsupported``
     - Leave out containers and meta-commands that use unsupported XTCE features, with a warning, instead of failing
   * - ``--size-report``
     - Write a size report instead of code (see `Size report`_), to
       ``OUTPUT`` or standard output
   * - ``--dynamic-allocations true|false``
     - Whether the generated code may allocate heap memory at any time
       (``true``, the default) or only during initialization (``false``).
       See `Code without dynamic allocation`_
   * - ``--source-name NAME``
     - Mission file name recorded in the generated headers

By default the compiler stops if any container or meta-command uses an
unsupported construct, and lists every such construct with its cause.

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

``python3 -m tcs_xtce INPUT --size-report`` writes no code. For each
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
* a little-endian field is rounded up to whole bytes, which its encoding
  requires.

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
     O      /Sat/Open      16      -         no ValidRange
     M      /Sat/Mode       8      3      5  values 0..5
     F      /Sat/Flag       8      1      7  boolean
     N      /Sat/Name      64      -         fixed size: strings shorter than 8 bytes are padded
     ...
     right-sized fields save 67 bits: at most 129 bits, 17 bytes

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
     - ``u8`` … ``u64``, ``i8`` … ``i64``
     - ``uint8_t`` … ``int64_t``
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
``IncludeCondition``, overlap, or take a size or count from another
value. Gaps that locations leave are written as zeros. A
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

When the referenced value does nothing but count an array or give the
size of a binary value, encoding computes it from that data's length,
and it is not a field of the generated struct. In the demo,
``SampleCount``, ``PayloadSize``, ``TargetCount``, ``LogCount``,
``BatchCount``, ``WordCount`` and ``Fill``'s ``Last`` are computed this
way: set ``Readings`` to three values and ``SampleCount`` is written as
3. The value must be an integer without a calibrator, outside any
``IncludeCondition``, and it must give the size of a whole field (not a
member, element or part of one) that is also outside any
``IncludeCondition``. Encoding fails with ``BadSize`` if no value of the
field gives the data's length, or if that length is above the field's
bound (below), and with ``OutOfRange`` if the value doesn't fit the
field.

Otherwise the referenced value stays an ordinary field: when it is also
compared by a criterion, gives a location, picks a
``DiscreteLookupList`` value or sizes a string, or when it is an
aggregate member such as ``RouteHdr.Count``. Encoding then computes the
size or count from the field and fails with ``BadSize`` if the data
doesn't match: set both. When decoding, a size or count that is
negative, not whole, not a whole number of bytes (for sizes in bits of
strings and binary), or above the field's bound fails with
``BadSize``.

The bound is the largest value the referenced field's ``ValidRange``
allows, or, without one, its encoding, or the largest of a
``DiscreteLookupList``'s values. It is limited to what fits in
64 KiB on the wire, which keeps C structs bounded. In C this bound is
``N`` in the field's type, so give count fields a ``ValidRange``: the
demo's ``SampleCount`` (``0..6``) makes ``Readings`` a
``demo_int16_list6``, where an 8-bit count without a range would allow
255 elements, and a 32-bit one 32768 of them.

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
     - A value doesn't fit its encoding, or a binary value is too short
   * - ``InvalidString``
     - A string isn't valid UTF-8, or contains its termination character
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
   ``limit(value, adjust, field)`` (``xtce_pk_read_limit``) ends the packet
   where a packet length field that was just read says, and
   ``finish(field)`` (``xtce_pk_read_finish``) checks that decoding
   reached that end.

``BitWriter(buffer)``
   Clears the buffer, then ``uint(bits, value)``,
   ``uint_order(bits, little_endian, value)``, ``bytes(data)``, ``seek``
   and ``skip``. In Rust and C/C++, ``zeros(bits)``
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

Floats
   ``float_from_raw(raw, bits)`` and ``float_to_raw(value, bits)`` for
   IEEE 754 binary32 and binary64. Converting to 32 bits rounds to
   nearest, and overflows to infinity.

Calibrators
   ``polynomial(x, terms)``, ``polynomial_inverse(y, intercept, slope)``,
   ``spline(x, points, order, extrapolate, field)`` and
   ``spline_inverse(y, points, extrapolate, field)``.
   ``round_to_int(y, lo, lim, field)`` rounds a calibrated value half away
   from zero and fails with ``OutOfRange`` unless ``lo <= result < lim``.

Strings and binary
   ``read_string``, ``write_string`` (with a ``StringSpec``: fixed bits,
   maximum bits, terminator, size-tag bits) and ``write_binary``.
   ``StringSpec::sized(bytes)`` (Rust), ``xtce_pk_string_spec_sized``
   (C/C++) and ``sized(spec, bytes)`` (Python) give the spec of a string
   whose size another field gives. The reader's ``pad(bits, field)``
   (``xtce_pk_read_pad``) skips padding that must be present. The
   reader's ``rest_bytes(max, field)``
   (``xtce_pk_read_rest`` in C/C++) gives the size of a binary value that
   takes the rest of the packet, and ``write_rest`` writes one (C/C++
   generates it inline). ``string_from_bytes(bytes, field)`` (Rust,
   Python), ``xtce_pk_read_utf8`` and ``xtce_pk_write_utf8`` (C/C++) read
   and write strings encoded as binary. Python's ``bool_from_string(s,
   one, zero, field)`` decodes a boolean encoded as a string; the other
   languages generate it inline.

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
   RelativeTime, Array and Aggregate. Types that use ``baseType`` are not
   supported.

Ancillary data
   ``tcs-xtce.length`` on a parameter or argument marks a packet length
   field, described under the generated packet types. Other ancillary
   data is ignored.

Aggregates
   Members of any supported type except the aggregate itself, values
   that take the rest of the packet, and values whose size another value
   gives, unless that value is an earlier member of the same aggregate.
   Criteria, lookups and sizes can refer to a member with XTCE's dot
   syntax (``parameterRef="RouteHdr.Count"``, also nested, as in
   ``P.a.b``, and in ``argumentRef``), but not to a whole aggregate.

Array elements
   Criteria, lookups and sizes can refer to an element with XTCE's
   bracket syntax, mixed with members (``Probes[1].Gains[0]``), with
   one index per dimension (``Matrix[1][2]``). Every dimension of the
   array must have a fixed size, and each index must be within its
   dimension, counting from its ``StartingIndex``; for a subset, the
   indexes are the full array's, and must fall inside the subset. When encoding, criteria are checked before any field is
   written, so arrays they index are first checked to have their count,
   and encoding fails with ``BadSize`` (naming the field) otherwise. In
   the demo, ``ProbeOne`` and ``ProbeOther`` are chosen by
   ``Probes[0].Id``, and ``ProbeData`` is sized by ``Probes[1].Gains[0]``.

Arrays
   Elements of any supported type except arrays and values whose size
   another field gives. Each dimension's ``StartingIndex`` is a
   ``FixedValue``; its ``EndingIndex`` is a ``FixedValue`` or, in at most
   one dimension, a ``DynamicValue``. ``ArrayParameterRefEntry`` and
   ``ArrayArgumentRefEntry`` (or a plain ``ParameterRefEntry`` or
   ``ArgumentRefEntry``) use the whole array, or with a ``DimensionList``
   a subset, with as many dimensions as the type and within its bounds
   when they are fixed.

Repeated entries
   ``RepeatEntry`` on parameter and argument entries, with a ``Count``
   that is a positive ``FixedValue`` or a ``DynamicValue``, and an
   optional ``Offset`` that is a ``FixedValue`` of zero or more bits. A
   repeated array is not supported.

Sizes and counts from other fields
   ``DynamicValue`` with ``ParameterInstanceRef`` (instance 0) or, in
   argument types, ``ArgumentInstanceRef``, and an optional
   ``LinearAdjustment``, in array ``EndingIndex``, ``RepeatEntry``
   ``Count``, binary ``SizeInBits``, variable string sizes and
   locations. It must refer to an integer value decoded earlier in the
   packet, outside any ``IncludeCondition``, or, inside an aggregate
   type, to an earlier integer member. ``DiscreteLookupList`` works in
   the same places except aggregate members, with comparisons on values
   decoded earlier. See `Generated packet types`_ for the checks and
   bounds.

Integer encoding
   1–64 bits; ``unsigned``, ``twosComplement``, ``signMagnitude`` and
   ``onesComplement``; most significant bit first. Little-endian byte
   order is allowed when the size is a whole number of bytes.

Float encoding
   ``IEEE754_1985`` or ``IEEE754``, 32 or 64 bits, in either byte order.

Calibrators
   ``PolynomialCalibrator`` and ``SplineCalibrator`` of order 0 or 1, as
   the ``DefaultCalibrator`` of an integer or float encoding on a float
   type, or of an integer encoding on an integer type. Encoding uses the
   inverse calibrator. This works for
   polynomials of degree 0 or 1 with a non-zero slope, and for linear
   splines whose calibrated values strictly increase or decrease. Other
   calibrators are decode-only: encoding them fails with
   ``NotInvertible``. Engineering values are rounded half away from zero
   to the nearest raw integer.

   On an integer type the calibrated value is rounded half away from zero
   to an integer of the type's ``sizeInBits`` (default 32) and ``signed``
   (default true); decoding fails with ``OutOfRange`` if it doesn't fit.
   Rounding means a value may not survive encoding: in the demo,
   ``RecordCount`` is ``-4 + raw / 2``, so 1 encodes as raw 10 but raw 9
   decodes as 1 too. The type's ``ValidRange`` applies to the calibrated
   value; with ``validRangeAppliesToCalibrated="false"`` it is ignored. A
   size or count taken from it is bounded through the calibrator.
   Comparisons with ``useCalibratedValue="false"`` are not supported on
   it, as the generated code keeps only the calibrated value.

Strings
   UTF-8 or US-ASCII. The supported layouts are:

   * a fixed size (``SizeInBits/Fixed``) ending at the termination
     character (``00`` by default) or at the end of the field;
   * a fixed size with a leading size tag;
   * a variable size (``Variable``) with a termination character;
   * a variable size with a leading size tag;
   * either of these within a size that another field gives
     (``Variable`` with a ``DynamicValue``).

   Sizes must be whole bytes, and size tags count bytes. When encoding,
   fixed fields are padded with the terminator and then zeros.

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
   value given") takes the rest of the packet, up to 64 KiB. It must be
   the last entry of every packet that contains it, and can't be an array
   element, an aggregate member or repeated. Decoding fails with
   ``BadSize`` unless what is left is whole bytes, and encoding fails
   with ``TooLong`` above 64 KiB. Yamcs also allows such values before
   other entries, where only the sender knows their size; those packets
   are not supported.

Times
   The ``Encoding`` (integer or float, ``units="seconds"``) with
   ``scale`` and ``offset`` gives seconds relative to the
   ``ReferenceTime``. The reference time is recorded in the generated
   documentation but not applied.

Containers
   ``SequenceContainer`` entry lists with ``ParameterRefEntry``,
   ``ArrayParameterRefEntry`` and ``ContainerRefEntry``. Included containers are flattened in, or with
   a ``RepeatEntry`` become a list, and may not have a ``BaseContainer``.
   A ``BinaryEncoding`` with a fixed ``SizeInBits`` pads a telemetry
   container to that size. ``BaseContainer`` inheritance uses
   ``RestrictionCriteria``.

Criteria
   ``RestrictionCriteria``, ``IncludeCondition`` and ``DiscreteLookup``
   take a ``Comparison``, a ``ComparisonList`` (all must hold) or a
   ``BooleanExpression``: a ``Condition``, or ``ANDedConditions`` and
   ``ORedConditions`` nested to any depth. Comparisons use the operators
   ``== != < <= > >=`` on calibrated or raw values (only ``==`` and ``!=``
   for booleans, strings and binary), on values that are not arrays or
   aggregates. A ``Condition`` may compare two values of the packet
   instead of a value and a constant: both of the same kind (integer,
   enumeration, float, boolean or string), calibrated, and for integers
   both signed or both unsigned. In the demo, ``Gate``'s ``GateSpan`` is
   included when ``GateLo < GateHi`` or both are zero, and ``Open``'s
   ``Data`` is 2 bytes when its ``Lo < Hi``. Only a plain ``==``
   comparison with a constant fixes a field's value, so derived packets
   chosen by other criteria keep those fields, and encoding checks them.

Locations
   ``LocationInContainerInBits`` relative to ``previousEntry`` (the
   default) or ``containerStart``. ``containerStart`` is the start of the
   packet for inherited entries, and the inclusion point for entries of
   an included container. A ``FixedValue`` offset may be negative. A
   ``DynamicValue`` or ``DiscreteLookupList`` offset is computed like a
   size, in bits, so it must be a whole number from 0 up to its bound
   (at most 64 KiB), or decoding and encoding fail with ``BadSize``; in
   the demo, ``Frame``'s ``FrameEnd`` comes ``FrameGap`` bits after the
   body. Repeated and fixed-size containers need fixed offsets.

Include conditions
   ``IncludeCondition`` (see Criteria) on
   ``ParameterRefEntry`` and ``ContainerRefEntry``, on values decoded
   earlier that are not themselves conditional. The affected fields
   become optional. They are decoded when the condition holds, and
   encoded when the condition holds on the struct's fields. Encoding
   fails with ``Missing`` if the condition holds but the value is
   absent.

Commands
   ``MetaCommand`` with ``ArgumentList``, ``BaseMetaCommand`` and
   ``ArgumentAssignmentList``, and a ``CommandContainer`` whose
   ``BaseContainer`` is the base meta-command's container. The container
   may hold ``ArgumentRefEntry``, ``ArrayArgumentRefEntry``,
   ``ParameterRefEntry``, ``ArrayParameterRefEntry`` and
   ``FixedValueEntry`` (at most 64 bits). Argument assignments act like
   ``==`` restriction criteria: they fix the value when encoding and
   select the command when decoding.

Name references
   Absolute (``/Root/Sub/name``), relative (``Sub/name``, ``../name``),
   and unqualified names. An unqualified name is looked up in the
   current SpaceSystem, then in each enclosing one.

Not supported
   references to elements of arrays whose count varies, arrays of arrays, ``IndirectParameterRefEntry``, stream and segment entries,
   ``RepeatEntry`` offsets that other values give, other instances than 0,
   ``CustomAlgorithm`` criteria, ``NextContainer``, a container
   ``BinaryEncoding`` on command containers, with a size from a
   ``DynamicValue`` or with transform algorithms, ``ErrorDetectCorrect``, context calibrators,
   ``MathOperationCalibrator``, BCD encodings, MIL-STD-1750A floats,
   byte orders other than big and little endian, least-significant-bit-first
   bit order, and include conditions in command containers.

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


xtce-digest
===========

``xtce-digest`` prints an MD5 digest of what each XTCE document given to
it means, so that a change to a mission database that would change the
generated data structures and methods can be noticed, and an edit that
only rearranges the document can be told apart from one that doesn't.

.. code-block:: sh

   cd generator
   python3 -m xtce_digest FILE...

   python3 -m xtce_digest ../tests/mission/demo.xml ../examples/simple-string/simple_string.xml

It reads the files in the order given. For each one it prints the file
name as given, a colon, a space and the digest, in hex:

.. code-block:: text

   ../tests/mission/demo.xml: 4c4a919fe4ac21b2dadf9c03ba547347

Each file gets its own digest, computed afresh, so a file gives the same
digest wherever it appears in the list. A file that can't be read, isn't
well-formed XML or isn't valid XTCE is reported on standard error and
ends the command with status 1; the files before it have been printed,
and the ones after it are not read. Status 0 means every file was
digested.

The digest covers every element, attribute and piece of text, but not
what leaves the document meaning the same:

* comments and processing instructions;
* whitespace between elements, and at the start and end of text;
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

``make html`` in an example's directory converts its ``README.rst`` into
``README.html``. ``make`` in ``examples/`` itself does that for every
example and for ``examples/README.rst``, the `index of examples
<../examples/README.html>`__.

The examples are:

* `simple-string <../examples/simple-string/README.html>`__: a structure
  holding one string of up to 8 bytes, serialized into and deserialized
  from managed buffers.
* `to-from <../examples/to-from/README.html>`__: words of varying
  length, with a leading size tag, written by the program in one language
  and read by the program in another, for any pair of C, C++, Rust and
  Python.
* `variable-array <../examples/variable-array/README.html>`__: two
  two-dimensional arrays whose dimensions both vary, the lines and words of
  a paragraph, sent one after the other over one TCP connection from one
  thread to another, each after its length.

Each is summarized below; its README has the details.

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
With ``write``, it serializes each of the 69 words of the lorem ipsum
paragraph into a ``Word`` packet and writes the packets to standard output.
With ``read``, it reads packets from standard input, using each packet's
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
two. One thread opens a TCP connection on the loopback interface and, for
each array, writes its length, then the whole ``Paragraph`` in a single
write. The length is a second container in the same XTCE file, ``Length``,
holding a 32-bit ``ByteCount``, so it too is encoded and decoded with the
generated code. Another thread reads and decodes each 4-byte ``Length``,
then reads that many bytes, deserializes the ``Paragraph`` and checks
every line and word.
It then prints each inner array as a line, with spaces between the words,
or exits with status 1 if anything differs. The lengths let the receiver
find where each packet ends, since a TCP stream doesn't keep writes apart:

::

   Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut
   labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco
   laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in
   voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat
   cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.

See the `variable-array README <../examples/variable-array/README.html>`__
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
    ``tests/mission/vectors.expected``;
  * 3000 random packets give byte-identical results in Rust (generated
    both with and without dynamic allocation), Python, and the C tool
    compiled both as C and as C++;
  * the Rust codecs generated with ``--dynamic-allocations false``
    allocate nothing while decoding, encoding or describing a packet;
  * decoding a re-encoded packet gives the same values and the same
    bytes.

The ``packet_tool`` programs (``rust/examples``, ``cpp/tests``, built as
``cpp/build/packet_tool`` and ``cpp/build/packet_tool_cxx``, and
``python/tests``) run any vectors file:

.. code-block:: sh

   rust/target/release/examples/packet_tool tests/mission/vectors.txt

``make samples-check`` is not part of ``make test``, because it
downloads files. It checks ``tcs-xtce`` against 34 public XTCE documents:
the test documents of `Yamcs <https://github.com/yamcs/yamcs>`_ and the
test documents and examples of the `XTCE Toolkit
<https://github.com/CesarCoelho/xtcetools>`_.
``tests/external/sources.txt`` lists them, pinned to commits with a
SHA-256 for each. They are downloaded once into
``tests/external/files/``. ``tests/samples_check.py`` then checks that,
for each document:

* the binding accepts it exactly when ``xmllint`` finds it valid against
  the XTCE 1.3 schema (older XTCE namespaces are read as 1.3);
* a document the binding accepts writes back to an equal document;
* the compiler, the size report and every emitter produce output or
  report unsupported constructs, and never crash;
* the generated code compiles, without warnings: the C header as C11 and
  as C++, the Rust module against the ``tcs-xtce`` crate, and the Python
  module.

It prints a summary per document, with the constructs it found
unsupported, which must equal ``tests/external/expected.txt``. A crash, a
disagreement with the schema, a failed round trip or a failed compile
always fails the check. After an intended change, ``python3 -I
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
