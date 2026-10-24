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

``make html`` turns this file into ``README.html``.

Building needs what the top-level build needs: Rust with ``rustfmt``, a C11
compiler, a C++20 compiler, and Python 3.10 or later. The C/C++ program needs
only the header ``../../cpp/include/xtce/packet.h``; it links with no library.
