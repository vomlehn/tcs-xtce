simple-string
=============

A data structure holding a single string of up to 8 bytes, described in XTCE
and serialized and deserialized from Rust, C, C++ and Python.

The XTCE description
--------------------

`simple_string.xml <simple_string.xml>`__ defines:

* a string type, ``String8Type``, whose encoding always occupies 64 bits (8 bytes)
  and ends at a NUL byte (``TerminationChar 00``);
* a parameter, ``Text``, of that type;
* a container, ``SimpleString``, holding ``Text``.

The file validates against the XTCE 1.3 schema:

.. code-block:: sh

   XML_CATALOG_FILES=../../references/catalog.xml \
       xmllint --noout --nonet --schema ../../references/SpaceSystem.xsd simple_string.xml

Generating the code
-------------------

``make generate`` runs the generator once per language:

.. code-block:: sh

   cd ../../generator
   python3 -m tcs_xtce ../examples/simple-string/simple_string.xml \
       ../examples/simple-string/rust/src/simple_string.rs --lang rust
   python3 -m tcs_xtce ../examples/simple-string/simple_string.xml \
       ../examples/simple-string/c/simple_string.h --lang c
   python3 -m tcs_xtce ../examples/simple-string/simple_string.xml \
       ../examples/simple-string/cxx/simple_string.h --lang c++
   python3 -m tcs_xtce ../examples/simple-string/simple_string.xml \
       ../examples/simple-string/python/simple_string.py --lang python

The generated files aren't kept in git; ``make`` runs ``make generate`` first.

``--lang c++`` writes the same header as ``--lang c``; the example generates both
so that the C program uses ``c/simple_string.h`` and the C++ program uses
``cxx/simple_string.h``.

Each language gets a ``SimpleString`` structure with a ``text`` string field. In
C/C++ every name starts with ``simple_string_``, the mission's prefix.

+----------------+----------------------------------------------+------------------------------------------+----------------------------------------------+
|                | Rust                                         | C/C++                                    | Python                                       |
+================+==============================================+==========================================+==============================================+
| Structure      | ``struct SimpleString { pub text: String }`` | ``simple_string_SimpleString``, with     | ``@dataclass class SimpleString: text: str`` |
|                |                                              | ``simple_string_str8 text``              |                                              |
|                |                                              | (``size_t len; char data[9];``)          |                                              |
+----------------+----------------------------------------------+------------------------------------------+----------------------------------------------+
| Serialize into | ``encode(&mut Buffer)``,                     | ``_encode(p, buf, cap, &len, &err)``,    | ``encode(ManagedBuffer)``,                   |
| a buffer       | ``encode_into(&mut BitWriter)``,             | ``_encode_into(p, &writer)``             | ``encode_into(BitWriter)``, ``to_bytes()``   |
|                | ``to_bytes()``                               |                                          |                                              |
+----------------+----------------------------------------------+------------------------------------------+----------------------------------------------+
| Deserialize    | ``SimpleString::decode(&[u8])``              | ``_decode(&out, data, len, &err)``       | ``SimpleString.decode(data)``                |
| from a buffer  |                                              |                                          |                                              |
+----------------+----------------------------------------------+------------------------------------------+----------------------------------------------+
| Size           | ``MAX_BYTES`` = 8                            | ``simple_string_SimpleString_MAX_BYTES`` | ``MAX_BYTES`` = 8                            |
|                |                                              | = 8                                      |                                              |
+----------------+----------------------------------------------+------------------------------------------+----------------------------------------------+
| Text form      | ``Display``                                  | ``_describe(p, buf, cap)``               | ``describe()``                               |
+----------------+----------------------------------------------+------------------------------------------+----------------------------------------------+

On the wire:

* A string shorter than 8 bytes is followed by a NUL and zero padding.
* An 8-byte string fills the field with no terminator.
* A longer string, or one containing a NUL, can't be serialized; the error
  kinds are ``TooLong`` and ``InvalidString``.

The programs
------------

`rust/src/main.rs <rust/src/main.rs>`__, `c/main.c <c/main.c>`__ and
`python/main.py <python/main.py>`__ each do the following. ``c/main.c`` is
compiled twice, once as C and once as C++.

1. Set up an input buffer and an output buffer of 8 bytes. In Rust and
   Python a ``Codec`` owns them. In C they are plain arrays.
2. For each test string, serialize a ``SimpleString`` into the output buffer,
   copy the bytes into the input buffer, deserialize them, and check that
   the result equals the original.
3. Show that a 9-byte string is rejected.
4. Check that neither buffer was reallocated.

::

   $ make
   ...
   serialize "ABCDEFGH" -> 4142434445464748
   deserialize 4142434445464748 -> SimpleString{text="ABCDEFGH"}
   serialize "Hi" -> 4869000000000000
   deserialize 4869000000000000 -> SimpleString{text="Hi"}
   serialize "" -> 0000000000000000
   deserialize 0000000000000000 -> SimpleString{text=""}
   serialize "héllo" -> 68c3a96c6c6f0000
   deserialize 68c3a96c6c6f0000 -> SimpleString{text="héllo"}
   serialize "ABCDEFGHI" -> error:TooLong (Text is too long for its encoding)
   buffers reused without reallocation: true
   simple-string: Rust, C, C++ and Python output identical

Each program can also be run on its own, from this directory, after
``make generate``. Each prints the lines above, without the last, which
``make`` prints, and fails (exits with a nonzero status) if a string
doesn't come back unchanged. Its last line says whether the buffers were
reused.

Rust
~~~~

`rust/src/main.rs <rust/src/main.rs>`__ is a stand-alone Cargo crate that
depends on the ``tcs-xtce`` crate in ``../../rust``:

.. code-block:: sh

   cd rust && cargo run --release

or ``make rust``, then ``rust/target/release/simple-string``. Its ``Codec``
owns both buffers, preallocated to ``SimpleString::MAX_BYTES``.

C and C++
~~~~~~~~~

`c/main.c <c/main.c>`__ is built twice, once as C11 and once as C++20. It
needs only the header ``../../cpp/include/xtce/packet.h`` and the generated
header; it links with no tcs-xtce library.

.. code-block:: sh

   make c
   build/simple_string_c        # the C build
   build/simple_string_cxx      # the C++ build

or, by hand:

.. code-block:: sh

   cc  -std=c11   -I../../cpp/include -Ic   c/main.c -lm -o simple_string_c
   c++ -std=c++20 -I../../cpp/include -Icxx -x c++ c/main.c -o simple_string_cxx

Its two buffers are plain arrays of ``simple_string_SimpleString_MAX_BYTES``
bytes.

Python
~~~~~~

`python/main.py <python/main.py>`__ needs Python 3.10 or later and the
``xtce`` package in ``../../python``:

.. code-block:: sh

   PYTHONPATH=../../python:python python3 python/main.py

Its ``Codec`` owns both buffers, preallocated to ``SimpleString.MAX_BYTES``.

Makefile targets
----------------

``make``, or ``make run``
   Generate the code if needed, build every program, run it and check
   the output, as above.
``make generate``
   Run ``tcs-xtce`` on ``simple_string.xml`` for every language, if the XTCE
   file or the generator changed since the code was last generated.
``make rust``
   Check the Rust program's formatting with ``cargo fmt --check`` and build
   ``rust/target/release/simple-string``.
``make c``
   Build ``build/simple_string_c`` (C11) and ``build/simple_string_cxx`` (C++20).
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

   * - ``simple_string.xml``
     - The XTCE description
   * - ``rust/Cargo.toml``, ``rust/src/main.rs``
     - The Rust program
   * - ``rust/src/simple_string.rs``
     - Generated Rust module
   * - ``c/main.c``
     - The C and C++ program
   * - ``c/simple_string.h``, ``cxx/simple_string.h``
     - Generated header, from ``--lang c`` and ``--lang c++``
   * - ``python/main.py``
     - The Python program
   * - ``python/simple_string.py``
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
