==============================
tcs-xtce Run-time Codec Design
==============================

Status: being implemented. The crate ``tcs-xtce-dynamic`` reads and
resolves a mission and looks its names up, as `Looking names up`_
describes. Its interpreter (``interp``) decodes, classifies, encodes
and describes packets by running their items through the tcsxtce
helpers, as `Interpreting`_ describes; on the demo mission's vectors it
agrees with the generated codecs on every packet. ``Mission::pack`` and
``unpack``,
``Store``, ``Scratch`` and ``Datum`` give programs the interface of
`The interface`_, so far with these differences:

- A mission that uses what the codec doesn't run yet is read all the
  same; ``pack`` and ``unpack`` refuse such a packet, naming what it
  uses, and ``Mission::unsupported`` lists every such container and
  meta-command, so that a program can refuse the mission before it
  starts.
- ``Datum`` holds an integer of more than 128 bits as ``Big``, a
  ``WideInt`` (two's complement in 1032 bytes, which hold any integer
  of up to 8192 bits, the most the codec runs; ``to_big`` and
  ``from_big`` convert it).
- ``Datum::Time`` is a time in seconds, which ``Type::time`` says is
  absolute or relative. The model keeps times as floats, saying what
  they are only in the type's description, which the codec reads in one
  place; a time encoded as text or bytes keeps its text or bytes.
- ``Store::set_raw`` leaves the slot without an engineering value until
  one is set or unpacked: ``get`` gives none, and ``get_raw`` the raw
  value.
- Packing, unpacking, ``Store::set`` and ``Store::commit`` allocate
  nothing once the store and scratch are made, integers of more than 64
  bits included, except for the message of a packet that fails.
- The functions an application supplies for custom algorithms,
  custom checksums and transforms are set with
  ``Mission::set_algorithm``, by the algorithm's name, as an
  ``Algorithm`` of the kind its use needs, which is the type the
  generated code's ``hooks`` module takes; ``Mission::algorithms``
  lists them, and a packet that needs one not set fails with
  ``NoAlgorithm``. They are set after the mission is read, not before
  it resolves.
- ``Mission::alarms`` gives the alarm level of each field of the packet
  last unpacked into a scratch, as the generated code's ``alarms()``
  does, and ``Mission::monitor_alarms`` counts the earlier samples a
  ``Monitor`` keeps, as ``monitor()`` does; ``Mission::derived`` gives
  the outputs of the MathAlgorithms and CustomAlgorithms the packet
  triggers, as ``algorithms()`` does. Each calls a function with every
  result rather than returning a list, so that none of them allocates.
- A whole aggregate or array, or a member or element of one, is a
  ``Slots``: the run of slots that hold it, which
  ``Mission::parameter_slots``, ``argument_slots`` and ``entry_slots``
  look up and ``Store::each`` reads, slot by slot.
- ``Mission::unbounded`` lists the string and binary slots whose
  documents give them no maximum size. Rather than room that grows, each
  gets the room the maximum field size gives, in every store and
  scratch, so that packing and unpacking them allocates nothing either;
  a program that must stay small can refuse the mission before it
  starts. An array with no largest count has no slots, and its lookups
  say so.



Until ``StringSpec`` and ``ByteOrder`` gain a lifetime (see `Freeing
what was read`_), the interpreter keeps each distinct string terminator
and byte order list it is given for the rest of the program, once.



.. contents:: Contents
   :depth: 2
   :local:

What it is for
==============

tcs-xtce's packet codecs are generated: the compiler reads a mission's
XTCE once and writes typed code for it. That suits flight and ground
software, which is built for one mission. It doesn't suit a tool that
is given its XTCE when it runs, such as a protocol tester that is
handed a mission's documents and a script that names their containers.
expectb is the first such tool: its design for XTCE types is in its
``docs/xtce.md``.

The run-time codec reads XTCE documents when a program runs, resolves
them, and packs and unpacks their containers and meta-commands, with
values held dynamically instead of in generated structs.

Goals
=====

1. **Read XTCE while running.** A program passes the documents as
   anything that implements ``std::io::Read`` -- a file, a string or a
   buffer -- and gets a resolved mission it can look names up in.
2. **Give the same answers as the generated codecs.** Packing the same
   values gives the same bytes, and unpacking the same bytes gives the
   same values, byte for byte and bit for bit, so that a tool built on
   the run-time codec and a program built from generated code agree.
3. **Allocate nothing per packet.** Everything is allocated when the
   mission is resolved and its storage is made; packing and unpacking
   then allocate nothing, as the codecs generated with
   ``--dynamic-allocations false`` don't.
4. **Never be silently wrong.** A construct the run-time codec doesn't
   support yet makes resolving the mission fail, naming the construct
   and its line, as the compiler does with ``Unsupported``.
5. **Change nothing for generated code.** The compiler, the emitters,
   the generated code and the ``tcsxtce`` runtime keep their behaviour.
   The one change to an API is ``tcsxtce``'s, where two of its types
   gain a lifetime that generated code fills with ``'static``; see
   `Freeing what was read`_.

Where it fits
=============

The run-time codec is a crate of its own, ``tcs-xtce-dynamic``, one of
those that `tcs-xtce in Rust <rust-migration-design.html>`__ adds. It
reads XML, so it is a software tool, in the sense of
``categories.toml``, and not operational code like ``tcsxtce``. Flight
software still uses generated codecs, and still doesn't parse XML while
it runs.

It is built from three parts:

- the **model**, ``tcs-xtce-model``, the compiler's analysis in Rust,
  which the Rust compiler uses too;
- the **XML binding**, whose ``Document::read_from`` reads each
  document into a ``SpaceSystemType`` tree;
- the **tcsxtce runtime**, whose ``BitReader``, ``BitWriter`` and
  helpers -- integer and float encodings, strings and binary,
  calibrators, checksums and CRCs, sizes -- do the work at the bit
  level, exactly as they do for generated code.

What is new in this crate is the interpreter that runs the model, and
the storage and interfaces around it.

Resolving
=========

Resolving is the model's: ``tcs-xtce-model`` turns the binding's tree
into the resolved model, in which "references point at objects,
defaults are filled in and sizes are in bits", as ``compile.py`` and
``model.py`` do today, stage for stage:

1. **Index** every SpaceSystem into tables keyed by path: parameter
   types, parameters, argument types, containers, command containers,
   meta-commands, messages, algorithms and streams.
2. **Resolve references** with the compiler's rules: absolute paths from
   a document root, unqualified names from the current SpaceSystem
   outwards, ``.`` and ``..``, and other relative paths from each
   ancestor, as Yamcs does. Parameter and argument types stay separate
   namespaces.
3. **Build types**: encodings, byte orders, calibrators, enumerations,
   arrays and aggregates, and types that inherit from a base type.
4. **Build containers and meta-commands** into the model's ``Node`` and
   its list of items.
5. **Finish the mission**: the inheritance trees and their Kinds, fixed
   values, derived sizes, and each node's maximum size in bytes.

The compiler is built on the same model, so the model covers all of
XTCE that the compiler does, from the start: a mission resolves for the
run-time codec exactly when it compiles. The run-time codec adds the
same lookup by path for programs, described in `Looking names up`_.

Looking names up
----------------

A program names a SpaceSystem with a ``SpaceSystemId``, a handle the
mission gives out, and passes one as the base of every lookup:

- ``Mission::top`` gives the top, the SpaceSystem above every
  document's root. A relative path from the top means what the same
  path means as an absolute one, so ``SC/Tlm/Temp`` from the top names
  ``/SC/Tlm/Temp``.
- ``Mission::space_system`` looks a SpaceSystem up from a base and
  gives its handle, so a program reaches ``/SC/Tlm`` either as
  ``/SC/Tlm`` from any base or as ``Tlm`` from ``/SC``.
- ``Mission::container``, ``meta_command`` and ``parameter`` resolve a
  path from a base with the compiler's rules: an absolute path ignores
  the base; an unqualified name is looked for in the base, then in each
  SpaceSystem that encloses it; a path starting with ``.`` or ``..`` is
  taken from the base only; and any other relative path is looked for
  from the base, then from each SpaceSystem that encloses it.
- A lookup that finds nothing returns a ``LookupError`` naming the path
  and each SpaceSystem it was looked for in, in the order they were
  searched, so a program can say exactly why a name wasn't found.
- ``Mission::path_of_node``, ``path_of_slot`` and
  ``path_of_space_system`` give the absolute path of what a lookup
  found, for a program's traces and error messages.

The rules are in the model, which the compiler uses for references in
the documents, so a path means to a program what it would mean written
in a document at the base, and no program has to repeat the rules. A
program with nested scopes, such as expectb's ``space_system`` blocks,
keeps a stack of handles: it looks each block's SpaceSystem up from the
handle of the block around it, and passes the innermost handle to every
lookup in the block.

A ``SpaceSystemId`` belongs to the mission that gave it out, as a
``NodeId`` and a ``SlotId`` do. Given to another mission, it makes a
lookup fail with a ``LookupError``, not find the wrong thing.

Interpreting
============

The resolved model is close to an intermediate representation: a node
is a list of items -- seeks, values, pads, fixed values, conditions,
checks, length ends -- that the generated code runs as straight-line
code. The interpreter runs the same list:

- **Unpacking** a node walks its items with a ``BitReader``, as the
  generated ``decode_prefix`` does, calling the same helpers with the
  same arguments, and writes each value into its slot of the storage.
- **Packing** walks them with a ``BitWriter``, as ``encode_into``
  does, including patching length fields and checksums at the end.
- **Classifying** a packet against an inheritance tree decodes the
  root's items once, then tests each child's criteria in document
  order, the first match winning, as the generated ``Kind`` does.

Because both run the same items through the same runtime, the
interpreter and the generated code can only differ where the
interpreter is wrong, which is what the tests look for.

Values
======

A decoded value is held in a ``Datum``, a closed enum::

    pub enum Datum<'s> {
        Int(i128),          // integers of up to 128 bits
        Float(f64),
        Enum(i128),         // the raw value; its label is in the type
        Bool(bool),
        Str(&'s str),       // borrowed from the storage
        Binary(&'s [u8]),   // borrowed from the storage
        Time(f64),          // seconds, as the compiler represents times
    }

Aggregates and arrays aren't ``Datum``\s: each member and element is a
slot of its own, named by its path, ``Attitude.q[2]``, so a program
reaches every scalar the same way.

Storage
-------

``Store`` holds the current value of every parameter, as a ground
system's table of current values does:

- one slot for each scalar parameter, member and element, which
  records whether it has a value;
- one arena for every string and binary value, each given the room its
  encoding's maximum allows, with the compiler's rules for those
  maximums (``wire_cap``, ``str_cap``, ``binary_cap``);
- arrays with a dynamic size get room for their largest size.

``Mission::store`` allocates it, once. A string, binary value or array
with no maximum size can't be given room in advance; for those, the
mission reports which parameters they are, and ``Mission::store`` gives
them room that grows, so a program that must not allocate can refuse
the mission before it starts.

Unpacking is transactional: a packet is decoded into a ``Scratch``, the
size of the largest node, and only copied into the ``Store`` when it
decodes completely. A packet that fails a check, or a try that loses,
leaves every current value as it was.

The interface
=============

::

    impl Mission {
        // Read and resolve one or more documents
        pub fn read_from<I, R>(readers: I) -> Result<Mission, Error>
        where I: IntoIterator<Item = R>, R: std::io::Read;
        pub fn from_documents(docs: &[Document]) -> Result<Mission, Error>;

        // Look up, by XTCE path from a base SpaceSystem
        pub fn top(&self) -> SpaceSystemId;
        pub fn space_system(&self, base: SpaceSystemId, path: &str)
                            -> Result<SpaceSystemId, LookupError>;
        pub fn container(&self, base: SpaceSystemId, path: &str)
                         -> Result<NodeId, LookupError>;
        pub fn meta_command(&self, base: SpaceSystemId, path: &str)
                            -> Result<NodeId, LookupError>;
        pub fn parameter(&self, base: SpaceSystemId, path: &str)
                         -> Result<SlotId, LookupError>;
        pub fn argument(&self, cmd: NodeId, name: &str)
                        -> Result<SlotId, LookupError>;
        pub fn entry(&self, node: NodeId, name: &str)
                     -> Result<SlotId, LookupError>;

        // Name what was found, by its absolute path
        pub fn path_of_space_system(&self, ss: SpaceSystemId) -> &str;
        pub fn path_of_node(&self, node: NodeId) -> &str;
        pub fn path_of_slot(&self, slot: SlotId) -> &str;

        // Describe
        pub fn slot_type(&self, slot: SlotId) -> &Type;
        pub fn max_bytes(&self, node: NodeId) -> Option<usize>;
        pub fn is_abstract(&self, node: NodeId) -> bool;
        pub fn unbounded(&self) -> &[SlotId];
        pub fn digest(&self) -> [u8; 16];

        // Make storage
        pub fn store(&self) -> Store;
        pub fn scratch(&self) -> Scratch;

        // Run
        pub fn unpack(&self, node: NodeId, bytes: &[u8],
                      scratch: &mut Scratch) -> Result<NodeId, PacketError>;
        pub fn pack<'b>(&self, node: NodeId, values: &Store,
                        out: &'b mut Buffer) -> Result<&'b [u8], PacketError>;
    }

    impl Store {
        pub fn get(&self, slot: SlotId) -> Option<Datum<'_>>;
        pub fn set(&mut self, slot: SlotId, value: Datum<'_>)
                  -> Result<(), PacketError>;
        pub fn get_raw(&self, slot: SlotId) -> Option<Datum<'_>>;
        pub fn set_raw(&mut self, slot: SlotId, value: Datum<'_>)
                      -> Result<(), PacketError>;
        pub fn commit(&mut self, scratch: &Scratch);
    }

- ``SpaceSystemId``, ``NodeId`` and ``SlotId`` are small copyable
  handles, valid only with the mission that gave them out; see
  `Looking names up`_.
- ``unpack`` of an abstract node classifies the packet and returns the
  concrete node it matched.
- ``set`` checks a value against its type -- range, enumeration
  labels, string and binary sizes -- as encoding does, so a bad value
  is caught where it is given.
- ``Type`` says what a program needs to check values before running:
  the kind, the valid range, the enumeration labels, the maximum size
  and the calibrator, if any. expectb uses it to check a script's
  literals when it parses the script.
- Values are engineering values, after calibration. ``Store::get_raw``
  and ``Store::set_raw`` give and take raw values.
- ``digest`` is the digest ``xtce-digest`` computes for the same
  documents.

Freeing what was read
---------------------

Both the XML tree and the resolved mission can be freed:

- **The XML tree.** Resolving copies what the mission needs out of each
  ``Document``, and the mission keeps no reference into them, so a
  program can drop its documents as soon as ``Mission::from_documents``
  returns. ``Mission::read_from`` never keeps them at all.
- **The mission.** A ``Mission`` owns everything it holds, names
  included, so dropping it frees all of it. Nothing is leaked, so a
  program can resolve many missions in turn.

The tcsxtce runtime names fields with ``&'static str``, in its helpers'
arguments and in every ``PacketError``, and takes two pieces of
encoding data as ``'static``: ``ByteOrder::List`` and a string's
terminator. Generated code has all of these as constants. The run-time
codec deals with them so:

- **Field names.** The interpreter passes the helpers a fixed
  placeholder name. When a helper returns a ``PacketError``, the
  interpreter knows which item it was running, so it returns an error
  of its own that names the field from the mission. tcsxtce and
  generated code are unchanged by this.
- **Encoding data.** ``ByteOrder`` and ``StringSpec`` gain a lifetime,
  ``ByteOrder<'a>`` and ``StringSpec<'a>``, so that they can borrow
  from the mission. Generated code passes ``'static`` constants, which
  still fit, and the emitters write ``<'static>`` where a constant
  names the type. This changes tcsxtce's API, so tcsxtce's minor
  version changes, and tcs-xtce's with it.

Phases
======

The model is complete from the start; what comes in phases is what the
interpreter can run. Each phase is a set of XTCE constructs. A mission
that uses one from a later phase fails when it is loaded into the
run-time codec, naming the construct and its line.

1. **Packets of fixed shape.** Integer, float (every format), boolean,
   enumeration, string and binary types, of up to 128 bits for
   integers, in every byte and bit order; polynomial and spline
   calibrators; containers with inheritance and restriction criteria,
   comparisons and boolean expressions; includes; locations; fixed
   repeats; aggregates, and arrays of a fixed size; meta-commands with
   argument assignments and fixed values; missions split across
   documents.
2. **Packets that size themselves.** Sizes and counts taken from other
   values, packet length fields, error detection (CRCs, checksums, XOR,
   parity), entries included under a condition, arrays whose size comes
   from another value, segments, and time types.
3. **The rest of what the generated codecs support.** Indirect entries,
   messages, math operation and context calibrators, integers of more
   than 128 bits, command containers from other meta-commands, streams
   with frame synchronization, alarms and ``MathAlgorithm``\s.

Transform algorithms and custom algorithms are supplied by the
application to generated code. The run-time codec takes them as
functions set on the mission once it is read (see the status above).

Testing
=======

The generated codecs are the reference. The demo mission
(``tests/mission/demo.xtce``) and its packet vectors already exercise
every construct the compiler supports, so:

- **The model** is tested where it is ported, by comparing its
  canonical text with the Python compiler's (see `tcs-xtce in Rust
  <rust-migration-design.html>`__).
- **Packets.** For every vector, the run-time codec must unpack the
  same values as the generated Rust codec, printed in the canonical text
  form generated code's ``Display`` already writes in every language,
  and pack the same values into the same bytes. This joins the
  cross-language checks in ``tests/packet_check.sh``.
- **No allocation.** As for the codecs generated with
  ``--dynamic-allocations false``, the check fails if ``unpack``,
  ``pack``, ``Store::set`` or ``Store::commit`` allocates.
- **Unsupported constructs.** Each one, in each phase, makes resolving
  fail with its name and line.

The interpretation question
===========================

The tcs-xtce design chose to compile rather than interpret, for three
reasons: the analysis would be needed in three languages, users want
typed structs rather than names mapped to dynamic values, and flight
software shouldn't parse XML while it runs. None of these changes for
the programs that choice was made for. The run-time codec is for a
different kind of program, one that is given its XTCE when it runs, and:

- it is written once, in Rust, and other languages use it through
  interfaces (see `Interfaces to other languages`_);
- dynamic values are what such a program wants, since it doesn't know
  the mission when it is built;
- it is a software tool, never flown.

So the section of the tcs-xtce design, "Code generation or
interpretation", gains a paragraph saying that the generated codecs
remain the codecs for flight and for programs built for one mission,
and that the run-time codec exists for tools that read XTCE while they
run, with a link here.

Interfaces to other languages
-----------------------------

The run-time codec is written once, in Rust, and other languages reach
it through interfaces to that one implementation, not through ports of
it:

- **C**: functions with C linkage over opaque handles, such as
  ``xtce_mission``, ``xtce_store`` and ``xtce_scratch``, in the style of
  the C API of the XML binding: return codes, an error message and the
  line it is about, and a function to free each handle.
  ``SpaceSystemId``, ``NodeId`` and ``SlotId`` are passed as plain
  integer handles, and every lookup takes a base SpaceSystem, as in
  Rust. The header is generated from the Rust with cbindgen.
- **C++**: a header-only wrapper over the C API, with classes that free
  their handles when destroyed, as ``tcs-xtce-document.hpp`` wraps the
  document.
- **Python**: a PyO3 extension module, part of the ``tcs_xtce`` Python
  package, as `tcs-xtce in Rust <rust-migration-design.html>`__
  proposes for all of tcs-xtce's Python. It calls the Rust directly,
  not through the C API.

Every language gets the same operations, and its answers are the Rust
codec's. CONTRIBUTING.md's rule that Rust, C/C++ and Python stay in
step is kept by the interfaces: a change to the run-time codec changes
all three of them, with tests in each.

Versions
========

The new crate starts at 0.8.0, the version the rest of tcs-xtce moves
to. ``ByteOrder`` and ``StringSpec`` gaining a lifetime changes
tcsxtce's API, so tcsxtce moves to 0.8.0 too, as any change to its API
changes its minor version.

Settled in review
=================

- The run-time codec is written only in Rust, with interfaces to C and
  C++ over one C API, and to Python through PyO3.
- It is built on ``tcs-xtce-model``, the compiler's analysis moved to
  Rust and shared with the Rust compiler, not on a port of its own.
- Aggregates and arrays of a fixed size are in the first phase.
- The XML tree and the resolved mission can both be freed. Nothing is
  leaked; field names are mapped in the interpreter's errors, and
  ``ByteOrder`` and ``StringSpec`` gain a lifetime.
- ``Mission`` resolves relative paths, with XTCE's rules, from a base
  the program gives as a ``SpaceSystemId`` handle; programs don't build
  absolute paths themselves. A failed lookup names every SpaceSystem
  it searched.

Questions for review
====================

None remain.
