housekeeping
============

Housekeeping ``Status`` packets, described in XTCE, encoded into one stream
and decoded again from Rust, C, C++ and Python.

A ``Status`` packet reports a temperature, a bus voltage and, when its
heater is on, the heater's power. It shows three things the generated code
does for you:

* **A packet length field.** ``PacketLength`` holds the number of bytes
  after it. Encoding computes it from the packet's size, and decoding checks
  it, so packets stored one after another can be split apart again. It isn't
  a field of the generated structure.
* **Calibrated values.** The structure holds degrees Celsius, volts and
  watts. The packet holds raw counts, which the calibrators in the XTCE
  file convert to and from those units.
* **An optional field.** ``HeaterPower`` is in the packet only when
  ``HeaterOn`` is true, as its include condition says. Packets with the
  heater on are two bytes longer.

Each program does the same thing:

1. It encodes four readings, two with the heater off and two with it on,
   and appends each packet to one stream, as a recorder would store them:
   one after another, with nothing between them.
2. It walks the stream. It reads each packet's ``PacketLength`` from its
   first two bytes, so it knows the packet is ``2 + PacketLength`` bytes
   long, and decodes those bytes. It checks that each packet decodes to the
   reading it encoded, rounded to what the calibrators can represent, and
   that encoding it again gives the same bytes.
3. It shows four packets the generated code rejects: a ``Status`` with the
   heater on but no heater power, a temperature too high for its encoding,
   a packet cut short, and a packet whose ``PacketLength`` is one byte too
   long.

If a check fails, the program prints the problem to standard error and
exits with status 1.

The XTCE description
--------------------

`housekeeping.xml <housekeeping.xml>`__ defines one container, ``Status``:

::

   PacketLength    16 bits   bytes after this field; computed when encoding
   HeaterOn         4 bits   0 for false, anything else for true
   Temperature     12 bits   raw / 16 - 50 degrees C
   BusVoltage       8 bits   raw / 8 volts
   HeaterPower     16 bits   raw / 64 watts, only when HeaterOn is true

``HeaterOn`` is a ``BooleanParameterType`` with a 4-bit
``IntegerDataEncoding``. One bit would do, but with 4, ``HeaterOn`` and the
12-bit ``Temperature`` fill two bytes, so the fields after them start on
byte boundaries and are easy to pick out in a hex dump. Encoding writes 1
for true; decoding reads any value other than 0 as true.

**The length field.** XTCE has no way to say that a field holds the length
of its packet, so tcs-xtce reads it from an ``AncillaryData`` named
``tcs-xtce.length`` on the parameter. Its value, here 0, is added to the
number of bytes after the field. (A CCSDS packet length, the bytes after
the primary header minus one, would use -1.) Other tools ignore the
``AncillaryData``, so the file stays valid XTCE:

.. code-block:: xml

   <Parameter name="PacketLength" parameterTypeRef="LengthType">
     <AncillaryDataSet>
       <AncillaryData name="tcs-xtce.length">0</AncillaryData>
     </AncillaryDataSet>
   </Parameter>

**The calibrators.** ``Temperature``, ``BusVoltage`` and ``HeaterPower``
are ``FloatParameterType``\ s with an ``IntegerDataEncoding`` and a
``PolynomialCalibrator``. Decoding applies the polynomial to the raw count;
encoding applies its inverse and rounds to the nearest count, half away from
zero:

.. code-block:: xml

   <FloatParameterType name="TemperatureType">
     <UnitSet>
       <Unit>degC</Unit>
     </UnitSet>
     <IntegerDataEncoding sizeInBits="12">
       <DefaultCalibrator>
         <PolynomialCalibrator>
           <Term coefficient="-50" exponent="0"/>
           <Term coefficient="0.0625" exponent="1"/>
         </PolynomialCalibrator>
       </DefaultCalibrator>
     </IntegerDataEncoding>
   </FloatParameterType>

A temperature therefore covers -50 to 205.9375 °C in steps of 1/16 °C, a
voltage 0 to 31.875 V in steps of 1/8 V, and a heater power 0 to
1023.984375 W in steps of 1/64 W. The steps are powers of two, so every
value the packet can hold is exact in binary, and prints the same way in
every language. A value between two steps comes back as the nearest one:
21.53 °C is sent as raw count 1144 and comes back as 21.5 °C. A value
outside the range can't be encoded.

**The include condition.** The entry for ``HeaterPower`` has an
``IncludeCondition`` on ``HeaterOn``:

.. code-block:: xml

   <ParameterRefEntry parameterRef="HeaterPower">
     <IncludeCondition>
       <Comparison parameterRef="HeaterOn" value="true"/>
     </IncludeCondition>
   </ParameterRefEntry>

So a ``Status`` is 5 bytes with the heater off (``PacketLength`` 3) and 7
with it on (``PacketLength`` 5). The heater-on reading -12.25 °C, 27.5 V,
4.5 W is:

::

   0005        PacketLength: 5 bytes follow
   1           HeaterOn: true
   25c         Temperature: raw 604, 604 / 16 - 50 = -12.25
   dc          BusVoltage: raw 220, 220 / 8 = 27.5
   0120        HeaterPower: raw 288, 288 / 64 = 4.5

The file validates against the XTCE 1.3 schema:

.. code-block:: sh

   XML_CATALOG_FILES=../../references/catalog.xml \
       xmllint --noout --nonet --schema ../../references/SpaceSystem.xsd housekeeping.xml

Generating the code
-------------------

``make generate`` runs the generator once per language:

.. code-block:: sh

   cd ../../generator
   python3 -m tcs_xtce ../examples/housekeeping/housekeeping.xml \
       ../examples/housekeeping/rust/src/housekeeping.rs --lang rust
   python3 -m tcs_xtce ../examples/housekeeping/housekeeping.xml \
       ../examples/housekeeping/c/housekeeping.h --lang c
   python3 -m tcs_xtce ../examples/housekeeping/housekeeping.xml \
       ../examples/housekeeping/cxx/housekeeping.h --lang c++
   python3 -m tcs_xtce ../examples/housekeeping/housekeeping.xml \
       ../examples/housekeeping/python/housekeeping.py --lang python

The generated files aren't kept in git; ``make`` runs ``make generate``
first. ``--lang c++`` writes the same header as ``--lang c``. The example
generates both, so that the C program uses ``c/housekeeping.h`` and the C++
program uses ``cxx/housekeeping.h``.

Each language gets a ``Status`` structure. ``PacketLength`` is not a field: the generated code computes
and checks it. In C/C++ every name starts with ``housekeeping_``, the
mission's prefix.

.. list-table::
   :header-rows: 1
   :widths: auto

   * -
     - Rust
     - C/C++
     - Python
   * - Status
     - ``struct Status`` with ``heater_on: bool``,
       ``temperature: f64``, ``bus_voltage: f64`` and
       ``heater_power: Option<f64>``
     - ``housekeeping_Status`` with ``bool heater_on``, ``double
       temperature``, ``double bus_voltage``, ``bool has_heater_power``
       and ``double heater_power``
     - ``@dataclass class Status`` with ``heater_on: bool``,
       ``temperature: float``, ``bus_voltage: float`` and
       ``heater_power: Optional[float]``
   * - Serialize
     - ``Codec::encode(&status)``
     - ``housekeeping_Status_encode(p, buf, cap, &len, &err)``
     - ``Codec.encode(status)``
   * - Deserialize
     - ``Codec::load(bytes)``, then ``Codec::decode()``
     - ``housekeeping_Status_decode(&out, data, len, &err)``
     - ``Codec.load(data)``, then ``Codec.decode(Status)``
   * - Largest packet
     - ``Status::MAX_BYTES`` = 7
     - ``housekeeping_Status_MAX_BYTES`` = 7
     - ``Status.MAX_BYTES`` = 7

When encoding, ``heater_power`` must be present exactly when the heater is
on: if ``heater_on`` is true and it is absent, encoding fails with
``Missing``. When the heater is off it isn't encoded, and decoding leaves it
absent (``None``, or ``has_heater_power`` false).

Decoding stops where ``PacketLength`` says the packet ends and ignores any
bytes after it. It fails with ``Truncated`` if the data is shorter than
that, and with ``BadLength`` if the packet's entries end before it.

Running the programs
--------------------

``make`` generates the code, builds and runs all four programs, and checks
that they print the same thing:

::

   $ make
   ...
   encode Status{heater_on=false, temperature=21.53, bus_voltage=28.3, heater_power=none} -> 00030478e2
   encode Status{heater_on=true, temperature=-12.25, bus_voltage=27.5, heater_power=4.5} -> 0005125cdc0120
   encode Status{heater_on=true, temperature=-20.01, bus_voltage=26.9, heater_power=12.74} -> 000511e0d7032f
   encode Status{heater_on=false, temperature=5.0, bus_voltage=28.0, heater_power=none} -> 00030370e0
   stream: 4 packets, 24 bytes
   decode at 0: 5 bytes -> Status{heater_on=false, temperature=21.5, bus_voltage=28.25, heater_power=none}
   decode at 5: 7 bytes -> Status{heater_on=true, temperature=-12.25, bus_voltage=27.5, heater_power=4.5}
   decode at 12: 7 bytes -> Status{heater_on=true, temperature=-20.0, bus_voltage=26.875, heater_power=12.734375}
   decode at 19: 5 bytes -> Status{heater_on=false, temperature=5.0, bus_voltage=28.0, heater_power=none}
   encode Status{heater_on=true, temperature=20.0, bus_voltage=28.0, heater_power=none} -> error:Missing (HeaterPower is required by its include condition)
   encode Status{heater_on=false, temperature=300.0, bus_voltage=28.0, heater_power=none} -> error:OutOfRange (value of Temperature does not fit its encoding)
   decode 0005125c (cut short) -> error:Truncated (packet too short for PacketLength)
   decode 00040478e200 (PacketLength one too many) -> error:BadLength (PacketLength does not match the length of the packet)
   housekeeping: Rust, C, C++ and Python output identical

The ``encode`` lines show the values as given, and the ``decode`` lines the
values the packet holds: 21.53 °C became 21.5, 28.3 V became 28.25, and
12.74 W became 12.734375, the nearest step of each.

`Makefile targets`_ lists the other targets.

To run one program on its own, generate the code first with
``make generate``, then run the commands below from this directory. Each
prints the lines above, without the last, which ``make`` prints.

Rust
~~~~

`rust/src/main.rs <rust/src/main.rs>`__ is a stand-alone Cargo crate that
depends on the ``tcs-xtce`` crate in ``../../rust``:

.. code-block:: sh

   cd rust && cargo run --release

or ``make rust``, then ``rust/target/release/housekeeping``. Its ``Codec``
has buffers preallocated to ``Status::MAX_BYTES`` and reused for every
packet.

C and C++
~~~~~~~~~

`c/main.c <c/main.c>`__ is built twice, once as C11 and once as C++20. It
needs only the header ``../../cpp/include/xtce/packet.h`` and the generated
header; it links with no tcs-xtce library.

.. code-block:: sh

   make c
   build/housekeeping_c         # the C build
   build/housekeeping_cxx       # the C++ build

or, by hand:

.. code-block:: sh

   cc  -std=c11   -I../../cpp/include -Ic   c/main.c -lm -o housekeeping_c
   c++ -std=c++20 -I../../cpp/include -Icxx -x c++ c/main.c -o housekeeping_cxx

It encodes into a plain array of ``housekeeping_Status_MAX_BYTES`` bytes and
keeps the stream in an array with room for four packets of that size.

Python
~~~~~~

`python/main.py <python/main.py>`__ needs Python 3.10 or later and the
``xtce`` package in ``../../python``:

.. code-block:: sh

   PYTHONPATH=../../python:python python3 python/main.py

Its ``Codec`` has buffers preallocated to ``Status.MAX_BYTES`` and reused
for every packet.

Makefile targets
----------------

``make``, or ``make run``
   Generate the code if needed, build every program, run it and check
   the output, as above.
``make generate``
   Run ``tcs-xtce`` on ``housekeeping.xml`` for every language, if the XTCE
   file or the generator changed since the code was last generated.
``make rust``
   Check the Rust program's formatting with ``cargo fmt --check`` and build
   ``rust/target/release/housekeeping``.
``make c``
   Build ``build/housekeeping_c`` (C11) and ``build/housekeeping_cxx``
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

   * - ``housekeeping.xml``
     - The XTCE description
   * - ``rust/Cargo.toml``, ``rust/src/main.rs``
     - The Rust program
   * - ``rust/src/housekeeping.rs``
     - Generated Rust module
   * - ``c/main.c``
     - The C and C++ program
   * - ``c/housekeeping.h``, ``cxx/housekeeping.h``
     - Generated header, from ``--lang c`` and ``--lang c++``
   * - ``python/main.py``
     - The Python program
   * - ``python/housekeeping.py``
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
