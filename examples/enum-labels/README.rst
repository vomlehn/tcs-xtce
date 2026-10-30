enum-labels
===========

A probe's ``Status``, whose mode is an enumeration, described in XTCE and
encoded and decoded from Rust, C, C++ and Python.

Everything that depends on the mode names it by its *label*, never by its
number:

* **Include conditions.** ``Target`` is in the packet only in ``SCIENCE``
  mode, and ``Station`` only in ``DOWNLINK`` mode.
* **A derived packet.** A ``SafeStatus`` is a ``Status`` in ``SAFE``
  mode, with the reason it went there. Decoding picks it by the label, and
  encoding writes the mode for you: the ``SafeStatus`` structure has no
  mode field.
* **An alarm.** The mode's alarm is a warning in ``SAFE`` mode and a watch
  in ``TEST`` mode.

The programs name labels too, through the constants the generated code
gives each one. So the numbers are the document's business alone:
renumbering the modes in ``modes.xml`` changes the bytes on the wire, and
nothing else — not the conditions, not the alarm, not a line of the
programs. The example also shows a label that names a range of values,
and a value that no label names, which decoding keeps as it is.

Each program does the same thing:

1. It prints the label of a few values: ``TEST`` names 16 to 31, so 20
   is ``TEST``, and 40 has no label.
2. It encodes a ``Status`` in ``IDLE``, ``SCIENCE`` and ``DOWNLINK`` mode
   and a ``SafeStatus``.
3. It decodes each packet as a ``StatusKind``, the type that holds either
   kind of status, prints it with its alarm, and checks that it decodes to
   what it encoded. Then it decodes a status in mode 20 and one in mode 40,
   and checks that each encodes again to the same bytes.
4. It shows that a ``Status`` in ``SCIENCE`` mode without a ``Target``
   can't be encoded.

If a check fails, the program prints the problem to standard error and
exits with status 1.

The XTCE description
--------------------

`modes.xml <modes.xml>`__ defines the enumeration ``ModeType``:

::

   SAFE        0    alarm: warning
   IDLE        1
   SCIENCE     2
   DOWNLINK    3
   TEST       16 to 31 (maxValue="31")    alarm: watch

and two containers:

::

   Status       Mode       8 bits
                Battery    8 bits    percent
                Target    16 bits    only in SCIENCE mode
                Station   16 bits    only in DOWNLINK mode
   SafeStatus   a Status in SAFE mode, then
                Reason     8 bits    LOW_POWER, OVER_TEMPERATURE or COMMANDED

Each condition compares ``Mode`` with a label. XTCE takes a comparison's
``value`` as the parameter's engineering value, which for an enumeration
is its label:

.. code-block:: xml

   <ParameterRefEntry parameterRef="Target">
     <IncludeCondition>
       <Comparison parameterRef="Mode" value="SCIENCE"/>
     </IncludeCondition>
   </ParameterRefEntry>

The ``SafeStatus`` restriction and the alarm do the same:

.. code-block:: xml

   <BaseContainer containerRef="Status">
     <RestrictionCriteria>
       <Comparison parameterRef="Mode" value="SAFE"/>
     </RestrictionCriteria>
   </BaseContainer>

   <EnumerationAlarm alarmLevel="warning" enumerationLabel="SAFE"/>

tcs-xtce also takes the number there (``value="2"``), as some documents
have it, but only the label survives renumbering. A ``SafeStatus`` with a
9% battery and the reason ``LOW_POWER`` is:

::

   00          Mode: SAFE (which the SafeStatus fixes)
   09          Battery: 9
   01          Reason: LOW_POWER

The file validates against the XTCE 1.3 schema:

.. code-block:: sh

   XML_CATALOG_FILES=../../references/catalog.xml \
       xmllint --noout --nonet --schema ../../references/SpaceSystem.xsd modes.xml

Generating the code
-------------------

``make generate`` runs the generator once per language:

.. code-block:: sh

   cd ../../python
   python3 -m tcs_xtce ../examples/enum-labels/modes.xml \
       ../examples/enum-labels/rust/src/probe.rs --lang rust
   python3 -m tcs_xtce ../examples/enum-labels/modes.xml \
       ../examples/enum-labels/c/probe.h --lang c
   python3 -m tcs_xtce ../examples/enum-labels/modes.xml \
       ../examples/enum-labels/cxx/probe.h --lang c++
   python3 -m tcs_xtce ../examples/enum-labels/modes.xml \
       ../examples/enum-labels/python/probe.py --lang python

The mission, and so the module and the C prefix, are named for the root
SpaceSystem, ``Probe``. Each language gets an enumeration type for
``ModeType`` with a constant for each label, and a way to find the label
of any value:

.. list-table::
   :header-rows: 1
   :widths: auto

   * -
     - Rust
     - C/C++
     - Python
   * - The type
     - ``struct ModeType(pub i64)``
     - ``probe_ModeType``, an ``int64_t``
     - ``class ModeType(enum.IntEnum)``
   * - A label
     - ``ModeType::SAFE``
     - ``probe_ModeType_SAFE``
     - ``ModeType.SAFE``
   * - The label of a value
     - ``ModeType(20).label()``, ``Some("TEST")``
     - ``probe_ModeType_label(20)``, ``"TEST"``
     - ``ModeType.label_of(20)``, ``"TEST"``
   * - A value without a label
     - ``label()`` gives ``None``
     - ``NULL``
     - ``None``

The enumeration holds any value its encoding does, so a value with no
label, such as one a newer probe sends, decodes and encodes again
unchanged; describing it gives the number (``mode=40``). A range label's
constant is its first value (``ModeType::TEST`` is 16).

The packets are ``Status`` and ``SafeStatus``, and ``StatusKind`` holds
either: decoding it reads a ``Status`` and returns a ``SafeStatus`` when
the mode is ``SAFE``. Each has ``alarms()``, which gives the mode's alarm
level, and ``SafeStatus`` has it too, for the mode it fixes.

.. list-table::
   :header-rows: 1
   :widths: auto

   * -
     - Rust
     - C/C++
     - Python
   * - Either status
     - ``enum StatusKind``
     - ``probe_StatusKind``, with ``tag`` and union ``u``
     - ``StatusKind.decode()`` returns a ``Status`` or ``SafeStatus``
   * - Decode
     - ``StatusKind::decode(bytes)``
     - ``probe_StatusKind_decode(&k, data, len, &err)``
     - ``StatusKind.decode(data)``
   * - Alarms
     - ``p.alarms()``, displayed as ``mode=warning``
     - ``probe_StatusKind_alarms(&k, out)``
     - ``p.alarms()``, a list of (field, level)

Running the programs
--------------------

``make`` generates the code, builds and runs all four programs, and checks
that they print the same thing:

::

   $ make
   ...
   mode 0 is SAFE
   mode 2 is SCIENCE
   mode 20 is TEST
   mode 40 has no label
   encode Status{mode=IDLE, battery=80, target=none, station=none} -> 0150
   encode Status{mode=SCIENCE, battery=75, target=1234, station=none} -> 024b04d2
   encode Status{mode=DOWNLINK, battery=70, target=none, station=7} -> 03460007
   encode SafeStatus{battery=9, target=none, station=none, reason=LOW_POWER} -> 000901
   decode 0150 -> Status{mode=IDLE, battery=80, target=none, station=none} | alarms: mode=normal
   decode 024b04d2 -> Status{mode=SCIENCE, battery=75, target=1234, station=none} | alarms: mode=normal
   decode 03460007 -> Status{mode=DOWNLINK, battery=70, target=none, station=7} | alarms: mode=normal
   decode 000901 -> SafeStatus{battery=9, target=none, station=none, reason=LOW_POWER} | alarms: mode=warning
   decode 1463 -> Status{mode=TEST, battery=99, target=none, station=none} | alarms: mode=watch
   decode 2850 -> Status{mode=40, battery=80, target=none, station=none} | alarms: mode=normal
   encode Status{mode=SCIENCE, battery=50, target=none, station=none} -> error:Missing (Target is required by its include condition)
   enum-labels: Rust, C, C++ and Python output identical

A ``SafeStatus`` keeps the ``Target`` and ``Station`` fields it inherits
from ``Status``, which are always absent: in ``SAFE`` mode neither
condition holds.

To see that the numbers don't matter, change the values in ``ModeType``
(say ``SAFE`` to 4 and ``SCIENCE`` to 7) and run ``make`` again. Every
program still runs, the four still agree, and every packet still means
what it did; only the bytes change (``074b04d2`` for the ``SCIENCE``
status, ``040901`` for the ``SafeStatus``), and so do the first two lines,
which print the labels of the numbers 0 and 2, which no longer have any.

`Makefile targets`_ lists the other targets.

To run one program on its own, generate the code first with
``make generate``, then run the commands below from this directory.

Rust
~~~~

`rust/src/main.rs <rust/src/main.rs>`__ is a stand-alone Cargo crate that
depends on the ``tcs-xtce`` crate in ``../../rust``:

.. code-block:: sh

   cargo run --release --manifest-path rust/Cargo.toml

or ``make rust``, then ``rust/target/release/enum-labels``.

C and C++
~~~~~~~~~

`c/main.c <c/main.c>`__ is built twice, once as C11 and once as C++20. It
needs only the header ``../../cpp/include/xtce-packet.h`` and the
generated header; it links with no tcs-xtce library.

.. code-block:: sh

   make c
   build/enum_labels_c          # the C build
   build/enum_labels_cxx        # the C++ build

or, by hand:

.. code-block:: sh

   cc  -std=c11   -I../../cpp/include -Ic   c/main.c -lm -o enum_labels_c
   c++ -std=c++20 -I../../cpp/include -Icxx -x c++ c/main.c -o enum_labels_cxx

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
   Run ``tcs-xtce`` on ``modes.xml`` for every language, if the XTCE file
   or the generator changed since the code was last generated.
``make rust``
   Check the Rust program's formatting with ``cargo fmt --check`` and build
   ``rust/target/release/enum-labels``.
``make c``
   Build ``build/enum_labels_c`` (C11) and ``build/enum_labels_cxx``
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

   * - ``modes.xml``
     - The XTCE description
   * - ``rust/Cargo.toml``, ``rust/src/main.rs``
     - The Rust program
   * - ``rust/src/probe.rs``
     - Generated Rust module
   * - ``c/main.c``
     - The C and C++ program
   * - ``c/probe.h``, ``cxx/probe.h``
     - Generated header, from ``--lang c`` and ``--lang c++``
   * - ``python/main.py``
     - The Python program
   * - ``python/probe.py``
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
