variable-array
==============

Two-dimensional arrays whose dimensions both vary, described in XTCE, sent
one after the other over one TCP connection from one thread to another in
Rust, C, C++ and Python.

The data is the standard lorem ipsum paragraph ("Lorem ipsum dolor sit amet,
consectetur adipiscing elit, …") in five lines, as two arrays: the first
three lines, then the last two. In each array, the outer array holds the
lines. Each element of it holds an inner array with one word of that line
per element. The arrays have different numbers of lines, the lines have 12
to 15 words, and the words differ in length. Words are split at spaces, so
punctuation stays with its word.

Each program has two threads:

* The **sender** connects to the receiver and sends the 16-byte digest of
  ``variable_array.xtce``. Then, for each array in turn, it
  serializes the whole array into one ``Paragraph`` packet, writes the
  packet's length as a ``Length`` packet, then writes the ``Paragraph``
  packet with a single write call and
  checks that the call took every byte. After the last array it closes the
  connection.
* The **receiver** accepts the connection, reads the digest and checks it
  against the digest of its own ``variable_array.xtce`` (see `Checking the
  XTCE definition`_). For each array in turn, it then reads
  and deserializes the ``Length`` packet, then reads that many bytes and
  deserializes the ``Paragraph`` packet. It compares
  the packet with the array it expects: the number of lines, the number of
  words in each line, and every word. If they match, it prints each inner
  array as a line, with its words separated by spaces. When the connection
  closes, it checks that both arrays arrived. If anything is wrong, it
  prints the first problem to standard error and exits with status 1.

So the program prints the digest, then five lines: three from the first
array, then two from the second.

The length before each packet
-----------------------------

A TCP stream carries bytes, not messages: the two arrays can arrive
together, or a packet can arrive in pieces. So before each ``Paragraph``
the sender writes a ``Length`` packet giving the ``Paragraph``'s size in
bytes. ``Length`` is described in ``variable_array.xtce`` too, as a
container holding one 32-bit unsigned integer, ``ByteCount``, so it is
encoded and decoded with generated code, like the array itself:

.. code-block:: xml

   <IntegerParameterType name="ByteCountType" signed="false">
     <IntegerDataEncoding sizeInBits="32"/>
   </IntegerParameterType>
   ...
   <SequenceContainer name="Length">
     <EntryList>
       <ParameterRefEntry parameterRef="ByteCount"/>
     </EntryList>
   </SequenceContainer>

A ``Length`` packet is always ``Length::MAX_BYTES``, 4 bytes, so the
receiver reads 4 bytes and decodes them, then reads exactly ``ByteCount``
more, which is the whole ``Paragraph`` and nothing of the next one. A
``Paragraph`` is at most ``MAX_BYTES`` (3049) bytes, so the receiver
rejects a larger ``ByteCount`` before reading it. (A ``ValidRange`` on
``ByteCountType`` wouldn't do this: tcs-xtce uses ranges to bound counts
and sizes, but doesn't check other integers against them.)

On the wire, after the 16-byte digest, the two arrays look like this:

::

   00000197                         Length: 407 bytes
   03 0e 00000005 4c6f72656d ...    array 1: 3 lines, the first has 14 words, "Lorem", ...
   000000fd                         Length: 253 bytes
   02 0c 00000009 766f6c7570 ...    array 2: 2 lines, the first has 12 words, "voluptate", ...

Both threads are in one process. The receiver listens on ``127.0.0.1`` on a
port the system picks, so the programs need no network access and don't
conflict with other programs or with each other.

Checking the XTCE definition
----------------------------

The packets carry no field names or types, so the receiver can read them only
if it was built from the same definition as the sender. To check, the sender sends
the 16-byte MD5 digest of ``variable_array.xtce``, before any packet. The receiver
computes the digest of its own copy of ``variable_array.xtce`` and compares. If they
match, it prints

::

   XTCE digest: <32 hex digits>, the same on both sides

and goes on; if they don't, it prints both digests to standard error and
exits with status 1, before it reads a packet.

The digest is of what the document means, not of its bytes: comments,
whitespace between elements, attribute order, namespace prefixes and the
order of unordered collections such as a ``ParameterSet`` don't change it,
while any change to a type, a field or the order of entries does. Every
runtime computes it the same way:

.. list-table::
   :header-rows: 1
   :widths: auto

   * - Language
     - Function
   * - Rust
     - ``tcs_xtce::digest::digest_file_bytes(path)``, giving ``[u8; 16]``
   * - C, C++
     - ``xtce_digest_file_bytes(path, out, &err)``, from the header
       ``xtce-digest.h``, which needs no library
   * - Python
     - ``tcs_xtce.digest.digest_file_bytes(path)``, giving ``bytes``

``xtce-digest variable_array.xtce`` prints the same digest in hex. Each program reads
``variable_array.xtce`` from the current directory, so run it from this one.

The XTCE description
--------------------

`variable_array.xtce <variable_array.xtce>`__ defines two containers:
``Length``, described above, and ``Paragraph``, laid out like this:

::

   LineCount               8 bits, 0 to 8
   Lines[LineCount]
     WordCount             8 bits, 0 to 20
     Words[WordCount]
       size tag            32 bits: the word's length in bytes
       text                the word's UTF-8 bytes, at most 15

It is built from:

* ``WordType``, a string of varying length with a 32-bit size tag before it
  (``Variable`` with ``LeadingSize``). The tag and the string together
  occupy at most 152 bits, so a word holds up to 15 bytes.
* ``WordsType``, the inner dimension: an array of ``WordType`` whose
  ``EndingIndex`` is a ``DynamicValue``, ``WordCount`` − 1.
* ``Line``, an aggregate of ``WordCount`` and ``Words``. ``WordCount`` comes
  first, so it is known by the time ``Words`` is decoded.
* ``LinesType``, the outer dimension: an array of ``Line`` whose
  ``EndingIndex`` is ``LineCount`` − 1.
* ``LineCount`` and ``Lines``, the ``Paragraph`` container's two
  parameters.

tcs-xtce supports at most one varying dimension in a single
``ArrayParameterType``, and the two dimensions here need to vary
independently, since each line has its own number of words. So the inner
array is a member of an aggregate, and the outer array is an array of that
aggregate. Each line carries its own count.

``LineCountType`` and ``WordCountType`` each have a ``ValidRange``
(0 to 8 lines, 0 to 20 words). Without them an 8-bit count would allow 255
elements. The ranges bound the arrays, and so the most a packet can hold
(``MAX_BYTES``, 3049 bytes) and the size of the structures the C code
declares. Encoding or decoding a count outside its range fails.

The first array encodes to 407 bytes and the second to 253. The first
starts:

::

   03                      LineCount: 3 lines
   0e                      WordCount of line 1: 14 words
   00000005 4c6f72656d     "Lorem"
   00000005 697073756d     "ipsum"
   ...

The file validates against the XTCE 1.3 schema:

.. code-block:: sh

   XML_CATALOG_FILES=../../references/catalog.xml \
       xmllint --noout --nonet --schema ../../references/SpaceSystem.xsd variable_array.xtce

Generating the code
-------------------

``make generate`` runs the generator once per language:

.. code-block:: sh

   cd ../../python
   python3 -m tcs_xtce ../examples/variable-array/variable_array.xtce \
       ../examples/variable-array/rust/src/variable_array.rs --lang rust
   python3 -m tcs_xtce ../examples/variable-array/variable_array.xtce \
       ../examples/variable-array/c/variable_array.h --lang c
   python3 -m tcs_xtce ../examples/variable-array/variable_array.xtce \
       ../examples/variable-array/cxx/variable_array.h --lang c++
   python3 -m tcs_xtce ../examples/variable-array/variable_array.xtce \
       ../examples/variable-array/python/variable_array.py --lang python

The generated files aren't kept in git; ``make`` runs ``make generate`` first.
``--lang c++`` writes the same header as ``--lang c``. The example generates
both, so that the C program uses ``c/variable_array.h`` and the C++ program
uses ``cxx/variable_array.h``.

Each language gets a ``Length`` structure, and a ``Paragraph`` structure holding a list of ``Line``
structures. In C/C++ every name starts with ``variable_array_``, the
mission's prefix.

.. list-table::
   :header-rows: 1
   :widths: auto

   * -
     - Rust
     - C/C++
     - Python
   * - Paragraph
     - ``struct Paragraph { pub lines: Vec<Line> }``
     - ``variable_array_Paragraph``, with ``variable_array_Line_list8 lines``
       (``size_t len; variable_array_Line data[8];``)
     - ``@dataclass class Paragraph: lines: list[Line]``
   * - Line
     - ``struct Line { pub word_count: u8, pub words: Vec<String> }``
     - ``variable_array_Line``, with ``uint8_t word_count`` and
       ``variable_array_str15_list20 words``
     - ``@dataclass class Line: word_count: int; words: list[str]``
   * - Length
     - ``struct Length { pub byte_count: u32 }``
     - ``variable_array_Length``, with ``uint32_t byte_count``
     - ``@dataclass class Length: byte_count: int``
   * - Serialize
     - ``Codec::encode(&paragraph)``
     - ``variable_array_Paragraph_encode(p, buf, cap, &len, &err)``
     - ``Codec.encode(paragraph)``
   * - Deserialize
     - ``Codec::read_from(stream)``, then ``Codec::decode()``
     - ``variable_array_Paragraph_decode(&out, data, len, &err)``
     - ``Codec.read_from(stream)``, then ``Codec.decode(Paragraph)``
   * - Largest packet
     - ``Paragraph::MAX_BYTES`` = 3049
     - ``variable_array_Paragraph_MAX_BYTES`` = 3049
     - ``Paragraph.MAX_BYTES`` = 3049

The generated code fills in ``LineCount`` from the length of ``lines`` when
encoding. ``WordCount`` is a member of ``Line``, so the programs set
``word_count`` to the number of words themselves. Encoding fails with
``BadSize`` if the two disagree.

Running the programs
--------------------

``make`` generates the code, builds and runs all four programs, and checks
that they print the same thing:

::

   $ make
   ...
   XTCE digest: 904beed3fc463cd7addc91dfca67c304, the same on both sides
   Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut
   labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco
   laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in
   voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat
   cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.
   ...
   variable-array: Rust, C, C++ and Python output identical

`Makefile targets`_ lists the other targets.

To run one program on its own, generate the code first with
``make generate``, then run the commands below from this directory. Each
program prints the digest and the five lines, and exits with status 0 if
both arrays arrived intact.

Rust
~~~~

`rust/src/main.rs <rust/src/main.rs>`__ is a stand-alone Cargo crate that
depends on the ``tcs-xtce`` crate in ``../../rust``.

.. code-block:: sh

   cargo run --release --manifest-path rust/Cargo.toml

It sends from a thread started with ``std::thread::spawn`` and receives in
the main thread, over ``std::net::TcpStream``. Each thread has its own
``Codec``, whose buffer is preallocated to ``Paragraph::MAX_BYTES`` and
reused for both arrays.

C and C++
~~~~~~~~~

`c/main.c <c/main.c>`__ is built twice, once as C11 and once as C++20, and
uses POSIX threads and sockets. It needs only the headers
``../../cpp/include/xtce-packet.h`` and ``../../cpp/include/xtce-digest.h``
and the generated header; it links with no tcs-xtce library.

.. code-block:: sh

   make c
   build/variable_array_c       # the C build
   build/variable_array_cxx     # the C++ build

or, by hand:

.. code-block:: sh

   cc  -std=c11   -I../../cpp/include -Ic   c/main.c -pthread -lm -o variable_array_c
   c++ -std=c++20 -I../../cpp/include -Icxx -x c++ c/main.c -pthread -o variable_array_cxx

It sends from a thread started with ``pthread_create`` and receives in the
main thread. The sender serializes into, and the receiver reads into, a
plain array of ``variable_array_Paragraph_MAX_BYTES`` bytes, reused for both
arrays; the receiver's has one byte more, to detect a packet that is too
long.

Python
~~~~~~

`python/main.py <python/main.py>`__ needs Python 3.10 or later and the
``tcs_xtce`` package in ``../../python``.

.. code-block:: sh

   PYTHONPATH=../../python:python python3 python/main.py

It sends from a ``threading.Thread`` and receives in the main thread, over
the ``socket`` module. Each thread has its own ``Codec``, whose buffer is
preallocated to ``Paragraph.MAX_BYTES`` and reused for both arrays.

Makefile targets
----------------

``make``, or ``make run``
   Generate the code if needed, build every program, run it and check
   the output, as above.
``make generate``
   Run ``tcs-xtce`` on ``variable_array.xtce`` for every language, if the XTCE
   file or the generator changed since the code was last generated.
``make rust``
   Check the Rust program's formatting with ``cargo fmt --check`` and build
   ``rust/target/release/variable-array``.
``make c``
   Build ``build/variable_array_c`` (C11) and ``build/variable_array_cxx`` (C++20).
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

   * - ``variable_array.xtce``
     - The XTCE description
   * - ``rust/Cargo.toml``, ``rust/src/main.rs``
     - The Rust program
   * - ``rust/src/variable_array.rs``
     - Generated Rust module
   * - ``c/main.c``
     - The C and C++ program
   * - ``c/variable_array.h``, ``cxx/variable_array.h``
     - Generated header, from ``--lang c`` and ``--lang c++``
   * - ``python/main.py``
     - The Python program
   * - ``python/variable_array.py``
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
compiler, a C++20 compiler, and Python 3.10 or later. The C/C++ program also
needs a POSIX system, for threads and sockets. ``make html`` needs
``rst2html``, from Docutils.
