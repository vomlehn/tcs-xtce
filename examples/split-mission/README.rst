split-mission
=============

A rover's PUS telemetry, described in three XTCE documents, recorded in
one stream and decoded again from Rust, C, C++ and Python.

Missions often split their XTCE into documents that build on each other,
the way Yamcs's PUS example does: shared data types in one, the packet
headers of a standard in another, and the mission's own packets in a
third. tcs-xtce reads them together, as one mission, and the example
shows what that takes:

* **References across documents.** Each document's root SpaceSystem sits
  at the top of one tree, so ``rover.xtce`` names the base container
  ``/PUS/pus-tm`` in ``pus.xtce``, the parameter ``/PUS/pus-service`` that
  picks its packets, and the type ``/dt/uint16`` in ``dt.xtce``.
* **Derived types.** ``rover.xtce``'s types build on ``dt.xtce``'s with
  ``baseType``, adding units and a valid range: a wheel's turn count is a
  ``/dt/uint16`` that may not exceed 1000, which encoding enforces.
* **The digest of the set.** The stream starts with one digest for the
  three documents together, which doesn't depend on the order they are
  listed in, so that whatever reads the stream can check that it was built
  from the same definitions.

Each program does the same thing:

1. It computes the digest of the three documents, and again with them in
   another order, and checks that the two are the same.
2. It starts a stream with the digest, then encodes three packets, two
   housekeeping packets and an event, and appends each to the stream.
3. It walks the stream. It checks the digest against that of its own
   documents, then reads each packet's CCSDS length from bytes 4 and 5,
   so it knows the packet is the length plus 7 bytes long, decodes those
   bytes, and checks that each decodes to the packet it encoded.
4. It shows that a housekeeping packet with a wheel turned 1001 times
   can't be encoded.

If a check fails, the program prints the problem to standard error and
exits with status 1.

The XTCE documents
------------------

The three documents, each validating on its own against the XTCE 1.3
schema:

.. list-table::
   :header-rows: 1
   :widths: auto

   * - Document
     - Root SpaceSystem
     - Holds
   * - `dt.xtce <dt.xtce>`__
     - ``dt``
     - Parameter types: ``bool1``, ``uint2`` to ``uint16`` and ``float32``
   * - `pus.xtce <pus.xtce>`__
     - ``PUS``
     - The CCSDS primary header, in the abstract container ``ccsds``, and
       the PUS telemetry secondary header, in ``pus-tm``, which derives
       from ``ccsds`` for telemetry packets with a secondary header
   * - `rover.xtce <rover.xtce>`__
     - ``Rover``
     - The rover's packets: ``hk``, housekeeping (service 3, subservice
       25), and ``event`` (service 5, subservice 1), both derived from
       ``/PUS/pus-tm``

``pus.xtce`` uses ``dt.xtce``'s types by absolute reference:

.. code-block:: xml

   <Parameter name="pus-service" parameterTypeRef="/dt/uint8"/>

and ``rover.xtce`` derives its packets from ``pus.xtce``'s, picking them by
the service and subservice ``pus.xtce`` defines:

.. code-block:: xml

   <SequenceContainer name="hk">
     ...
     <BaseContainer containerRef="/PUS/pus-tm">
       <RestrictionCriteria>
         <ComparisonList>
           <Comparison parameterRef="/PUS/pus-service" value="3"/>
           <Comparison parameterRef="/PUS/pus-subservice" value="25"/>
         </ComparisonList>
       </RestrictionCriteria>
     </BaseContainer>
   </SequenceContainer>

Its types derive from ``dt.xtce``'s: ``baseType`` takes everything from
the base type, here its encoding, and the derived type adds to it or
replaces parts of it:

.. code-block:: xml

   <IntegerParameterType name="turns" baseType="/dt/uint16">
     <ValidRange minInclusive="0" maxInclusive="1000"/>
   </IntegerParameterType>

A housekeeping packet holds the primary header, the secondary header,
then the battery voltage and motor temperature (32-bit floats) and the
left and right wheels' turns (16 bits each). The first one is:

::

   0864        version 0, telemetry, secondary header, APID 100
   c001        sequence flags 3 (unsegmented), count 1
   0010        CCSDS length: 17 bytes after the header, minus one
   20          PUS version 2, time reference 0
   03 19       service 3, subservice 25
   0001        message counter 1
   41e40000    battery: 28.5
   42250000    motor temperature: 41.25
   0078 0076   turns: 120 left, 118 right

The CCSDS length is computed: ``pus.xtce`` marks it with the
``AncillaryData`` ``tcs-xtce.length`` of -1, since it counts the bytes
after the header minus one, so it isn't a field of the generated
structures. Nor are the packet type, secondary header flag, service and
subservice, which the restriction criteria fix.

The documents validate against the XTCE 1.3 schema:

.. code-block:: sh

   for f in dt.xtce pus.xtce rover.xtce; do
       XML_CATALOG_FILES=../../references/catalog.xml \
           xmllint --noout --nonet --schema ../../references/SpaceSystem.xsd $f
   done

Generating the code
-------------------

``make generate`` runs the generator once per language, on the three
documents together:

.. code-block:: sh

   cd ../../python
   python3 -m tcs_xtce ../examples/split-mission/dt.xtce \
       ../examples/split-mission/pus.xtce ../examples/split-mission/rover.xtce \
       ../examples/split-mission/rust/src/rover.rs --lang rust

and the same with ``c/rover.h --lang c``, ``cxx/rover.h --lang c++`` and
``python/rover.py --lang python``. List the documents that others build on
first: the mission, and so the module and the C prefix (``rover_``), are
named for the root SpaceSystem of the last one. Code is written for the
packets of every document.

Each language gets a structure for ``hk`` (``Hk``) and ``event``
(``Event``), and a type for either, named for the root of their tree,
``ccsds``:

.. list-table::
   :header-rows: 1
   :widths: auto

   * -
     - Rust
     - C/C++
     - Python
   * - A packet
     - ``Hk``, ``Event``
     - ``rover_Hk``, ``rover_Event``
     - ``Hk``, ``Event``
   * - Either
     - ``enum CcsdsKind``
     - ``rover_CcsdsKind``, with ``tag`` and union ``u``
     - ``CcsdsKind.decode()`` returns an ``Hk`` or ``Event``
   * - Encode
     - ``p.to_bytes()``
     - ``rover_CcsdsKind_encode(&k, buf, cap, &len, &err)``
     - ``p.to_bytes()``
   * - Decode
     - ``CcsdsKind::decode(bytes)``
     - ``rover_CcsdsKind_decode(&k, data, len, &err)``
     - ``CcsdsKind.decode(data)``

and every runtime computes the digest of a set of documents the same way:

.. list-table::
   :header-rows: 1
   :widths: auto

   * - Language
     - Function
   * - Rust
     - ``tcs_xtce::digest::digest_files_bytes(&paths)``, giving
       ``[u8; 16]``
   * - C, C++
     - ``xtce_digest_files_bytes(paths, n, out, &err)``, from the header
       ``xtce-digest.h``, which needs no library
   * - Python
     - ``tcs_xtce.digest.digest_files_bytes(paths)``, giving ``bytes``

``xtce-digest --set dt.xtce pus.xtce rover.xtce`` prints the same digest in
hex. The digest of a set is the MD5 digest of its documents' canonical
encodings (the manual's "xtce-digest" describes them), sorted and joined,
so the order doesn't matter; a set of one document has that document's
digest. Each program reads the documents from the current
directory, so run it from this one.

Running the programs
--------------------

``make`` generates the code, builds and runs all four programs, and checks
that they print the same thing:

::

   $ make
   ...
   set digest: 65a49b2e175af6ee41498f1a0b8dd37b (dt.xtce, pus.xtce, rover.xtce)
   set digest: the same for rover.xtce, dt.xtce, pus.xtce
   encode Hk{ccsds_version=0, ccsds_apid=100, ccsds_seq_flags=3, ccsds_seq_count=1, pus_version=2, pus_time_ref=0, pus_counter=1, battery=28.5, motor_temp=41.25, left_turns=120, right_turns=118} -> 0864c0010010200319000141e400004225000000780076
   encode Event{ccsds_version=0, ccsds_apid=100, ccsds_seq_flags=3, ccsds_seq_count=2, pus_version=2, pus_time_ref=0, pus_counter=2, event=OVERHEAT, event_data=85} -> 0864c00200072005010002020055
   encode Hk{ccsds_version=0, ccsds_apid=100, ccsds_seq_flags=3, ccsds_seq_count=3, pus_version=2, pus_time_ref=0, pus_counter=3, battery=27.75, motor_temp=38.5, left_turns=0, right_turns=3} -> 0864c0030010200319000341de0000421a000000000003
   stream: the set digest, then 3 packets, 76 bytes
   set digest: the stream's matches
   decode at 16: 23 bytes -> Hk{ccsds_version=0, ccsds_apid=100, ccsds_seq_flags=3, ccsds_seq_count=1, pus_version=2, pus_time_ref=0, pus_counter=1, battery=28.5, motor_temp=41.25, left_turns=120, right_turns=118}
   decode at 39: 14 bytes -> Event{ccsds_version=0, ccsds_apid=100, ccsds_seq_flags=3, ccsds_seq_count=2, pus_version=2, pus_time_ref=0, pus_counter=2, event=OVERHEAT, event_data=85}
   decode at 53: 23 bytes -> Hk{ccsds_version=0, ccsds_apid=100, ccsds_seq_flags=3, ccsds_seq_count=3, pus_version=2, pus_time_ref=0, pus_counter=3, battery=27.75, motor_temp=38.5, left_turns=0, right_turns=3}
   encode Hk{ccsds_version=0, ccsds_apid=100, ccsds_seq_flags=3, ccsds_seq_count=4, pus_version=2, pus_time_ref=0, pus_counter=4, battery=27.5, motor_temp=30.0, left_turns=1001, right_turns=0} -> error:OutOfRange (value of left-turns is outside its valid range or encoding)
   split-mission: Rust, C, C++ and Python output identical

`Makefile targets`_ lists the other targets.

To run one program on its own, generate the code first with
``make generate``, then run the commands below from this directory.

Rust
~~~~

`rust/src/main.rs <rust/src/main.rs>`__ is a stand-alone Cargo crate that
depends on the ``tcs-xtce`` crate in ``../../rust``:

.. code-block:: sh

   cargo run --release --manifest-path rust/Cargo.toml

or ``make rust``, then ``rust/target/release/split-mission``.

C and C++
~~~~~~~~~

`c/main.c <c/main.c>`__ is built twice, once as C11 and once as C++20. It
needs only the headers ``../../cpp/include/xtce-packet.h`` and
``../../cpp/include/xtce-digest.h`` and the generated header; it links with
no tcs-xtce library.

.. code-block:: sh

   make c
   build/split_mission_c        # the C build
   build/split_mission_cxx      # the C++ build

or, by hand:

.. code-block:: sh

   cc  -std=c11   -I../../cpp/include -Ic   c/main.c -lm -o split_mission_c
   c++ -std=c++20 -I../../cpp/include -Icxx -x c++ c/main.c -o split_mission_cxx

Python
~~~~~~

`python/main.py <python/main.py>`__ needs Python 3.10 or later and the
``tcs_xtce`` package in ``../../python``:

.. code-block:: sh

   PYTHONPATH=../../python:python python3 python/main.py

Makefile targets
----------------

``make``, or ``make run``
   Generate the code if needed, build every program, run it and check
   the output, as above.
``make generate``
   Run ``tcs-xtce`` on the three documents for every language, if a
   document or the generator changed since the code was last generated.
``make rust``
   Check the Rust program's formatting with ``cargo fmt --check`` and build
   ``rust/target/release/split-mission``.
``make c``
   Build ``build/split_mission_c`` (C11) and ``build/split_mission_cxx``
   (C++20).
``make python``
   Generate the Python module. The Python program needs no building.
``make html``
   Convert this file, ``README.rst``, to ``README.html``.
``make clean``
   Remove ``build/`` and ``rust/target/``. The generated code and
   ``README.html`` stay.

Files
-----

The generated files aren't kept in git; building generates them.

.. list-table::
   :widths: auto

   * - ``dt.xtce``, ``pus.xtce``, ``rover.xtce``
     - The XTCE documents
   * - ``rust/Cargo.toml``, ``rust/src/main.rs``
     - The Rust program
   * - ``rust/src/rover.rs``
     - Generated Rust module
   * - ``c/main.c``
     - The C and C++ program
   * - ``c/rover.h``, ``cxx/rover.h``
     - Generated header, from ``--lang c`` and ``--lang c++``
   * - ``python/main.py``
     - The Python program
   * - ``python/rover.py``
     - Generated Python module
   * - ``Makefile``
     - Generates, builds, runs and checks; see above
   * - ``README.rst``, ``README.html``
     - This document
   * - ``build/``
     - The C and C++ programs and the programs' output

Requirements
------------

Building needs what the top-level build needs: Rust with ``rustfmt``, a C11
compiler, a C++20 compiler, and Python 3.10 or later. ``make html`` needs
``rst2html``, from Docutils.
