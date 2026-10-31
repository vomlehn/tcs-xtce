to-from
=======

Words of varying length, described in XTCE, written by the program in one
language and read by the program in another: Rust, C, C++ or Python.

The data is the standard lorem ipsum paragraph ("Lorem ipsum dolor sit amet,
consectetur adipiscing elit, …"), split at spaces into its 69 words, so
punctuation stays with its word. Each word is one ``Word`` packet.

The example shows three things:

* **Strings of varying length.** A ``Word`` is as long as its text, plus a
  four-byte size tag that XTCE describes.
* **Writing in one language and reading in another.** The code each
  language's generator produces writes the same bytes, and reads the bytes
  any of the others wrote.
* **Checking that both use the same definition.** The writer first sends
  the digest of ``to_from.xtce``, and the reader checks it against the digest
  of its own copy (see `Checking the XTCE definition`_).

Each language's program takes one argument:

``write``
   Writes the 16-byte digest of ``to_from.xtce`` to standard output, then
   serializes each word into a ``Word`` packet and writes the packets, one
   after another.

``read``
   Reads the digest from standard input and checks it against the digest of
   its own ``to_from.xtce``, printing it if they match. It then reads packets
   and deserializes them. It prints each word as it arrives, with a space before every word except the first, and
   a newline when the input ends. It checks every word against the
   paragraph's next word, so that it catches any word that is damaged,
   missing, extra or out of order, and input that ends early or in the
   middle of a word. If it finds one, it prints a message to standard error
   and exits with status 1.

The ``to-from`` script
----------------------

`to-from <to-from>`__, a Python script in this directory, connects a writer
in one language to a reader in another:

.. code-block:: sh

   ./to-from TO FROM

``TO`` is the language of the program that reads, and ``FROM`` the language
of the program that writes. Each is ``c``, ``c++``, ``rust`` or ``python``.
Since it's easy to mix the two up, ``to-from`` first prints which is which,
on one line. It then runs ``FROM``'s program with ``write`` and ``TO``'s
program with ``read``, as two processes, with the writer's standard output
piped to the reader's standard input:

::

   $ ./to-from python c
   to: python from: c
   XTCE digest: ad4767eca527cdc6d62f3bf78ad5ff4f, the same on both sides
   Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.

Here the C program wrote the packets and the Python program read them.
``to-from`` exits with status 1 if either program fails, and with status 2,
after a usage message, if its arguments are wrong. Build the programs with
``make build`` before running it.

The XTCE description
--------------------

`to_from.xtce <to_from.xtce>`__ defines:

* a string type, ``WordType``, of varying length (``Variable``), with a
  32-bit size tag before the string
  (``LeadingSize sizeInBitsOfSizeTag="32"``). The tag and the string
  together occupy at most 288 bits, so a word holds up to 32 bytes of UTF-8;
* a parameter, ``Text``, of that type;
* a container, ``Word``, holding ``Text``.

On the wire a word is its length in bytes, as a 32-bit unsigned number,
most significant byte first, followed by its bytes, with no terminator or
padding. ``"Lorem"`` is ``00000005 4c6f72656d``, and ``"sit"`` is
``00000003 736974``. A word longer than 32 bytes can't be serialized.

The writer's output is the 16-byte digest, then the packets, which follow
each other with nothing between them. Because a ``Word``
packet starts with its size tag, the reader reads those four bytes, then that
many more, and hands the whole packet to the generated decoder.

The file validates against the XTCE 1.3 schema:

.. code-block:: sh

   XML_CATALOG_FILES=../../references/catalog.xml \
       xmllint --noout --nonet --schema ../../references/SpaceSystem.xsd to_from.xtce

Checking the XTCE definition
----------------------------

The packets carry no field names or types, so the reader can read them only
if it was built from the same definition as the writer. To check, the writer sends
the 16-byte MD5 digest of ``to_from.xtce``, before any packet. The reader
computes the digest of its own copy of ``to_from.xtce`` and compares. If they
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

``xtce-digest to_from.xtce`` prints the same digest in hex. Each program reads
``to_from.xtce`` from the current directory, so run it from this one.

Generating the code
-------------------

``make generate`` runs the generator once per language:

.. code-block:: sh

   cd ../../python
   python3 -m tcs_xtce ../examples/to-from/to_from.xtce \
       ../examples/to-from/rust/src/to_from.rs --lang rust
   python3 -m tcs_xtce ../examples/to-from/to_from.xtce \
       ../examples/to-from/c/to_from.h --lang c
   python3 -m tcs_xtce ../examples/to-from/to_from.xtce \
       ../examples/to-from/cxx/to_from.h --lang c++
   python3 -m tcs_xtce ../examples/to-from/to_from.xtce \
       ../examples/to-from/python/to_from.py --lang python

The generated files aren't kept in git; ``make`` runs ``make generate``
first. ``--lang c++`` writes the same header as ``--lang c``. The example
generates both, so that the C program uses ``c/to_from.h`` and the C++
program uses ``cxx/to_from.h``.

Each language gets a ``Word`` structure with a ``text`` string field. In
C/C++ every name starts with ``to_from_``, the mission's prefix.

.. list-table::
   :header-rows: 1
   :widths: auto

   * -
     - Rust
     - C/C++
     - Python
   * - Structure
     - ``struct Word { pub text: String }``
     - ``to_from_Word``, with ``to_from_str32 text``
       (``size_t len; char data[33];``)
     - ``@dataclass class Word: text: str``
   * - Serialize
     - ``Codec::encode(&word)``
     - ``to_from_Word_encode(p, buf, cap, &len, &err)``
     - ``Codec.encode(word)``
   * - Deserialize
     - ``Codec::load(bytes)``, then ``Codec::decode()``
     - ``to_from_Word_decode(&out, data, len, &err)``
     - ``Codec.load(data)``, then ``Codec.decode(Word)``
   * - Largest packet
     - ``Word::MAX_BYTES`` = 36
     - ``to_from_Word_MAX_BYTES`` = 36
     - ``Word.MAX_BYTES`` = 36

Building and running
--------------------

``make`` generates the code, builds every program and checks them:

* each language's writer must write exactly the same bytes;
* ``./to-from`` runs all 16 pairs of reader and writer, and each reader
  must print the same words whichever writer it read from.

::

   $ make
   ...
   to: c from: c
   to: c from: c++
   to: c from: rust
   ...
   to: python from: python
   cat build/c-c.txt
   XTCE digest: ad4767eca527cdc6d62f3bf78ad5ff4f, the same on both sides
   Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor ...
   to-from: the 4 writers wrote the same bytes, and the 16 reader/writer pairs printed the same words

``make build`` only generates the code and builds the programs, for
``./to-from``. `Makefile targets`_ lists the others.

Each program can also be run on its own, from this directory, after
``make build``. Its writer's output is binary, so send it to a file or
a pipe rather than a terminal.

Rust
~~~~

`rust/src/main.rs <rust/src/main.rs>`__ is a stand-alone Cargo crate that
depends on the ``tcs-xtce`` crate in ``../../rust``:

.. code-block:: sh

   rust/target/release/to-from write >words.bin
   rust/target/release/to-from read <words.bin

or ``cargo run --release --manifest-path rust/Cargo.toml -- read`` (or
``write``), which also runs it from this directory. Its ``Codec``
has a buffer preallocated to ``Word::MAX_BYTES`` and reused for every word.

C and C++
~~~~~~~~~

`c/main.c <c/main.c>`__ is built twice, once as C11 and once as C++20. It
needs only the headers ``../../cpp/include/xtce-packet.h`` and
``../../cpp/include/xtce-digest.h`` and the generated header; it links with
no tcs-xtce library.

.. code-block:: sh

   build/to_from_c write >words.bin       # the C build
   build/to_from_cxx read <words.bin      # the C++ build

or, to build them by hand:

.. code-block:: sh

   cc  -std=c11   -I../../cpp/include -Ic   c/main.c -lm -o to_from_c
   c++ -std=c++20 -I../../cpp/include -Icxx -x c++ c/main.c -o to_from_cxx

It serializes into, or deserializes from, a plain array of
``to_from_Word_MAX_BYTES`` bytes.

Python
~~~~~~

`python/main.py <python/main.py>`__ needs Python 3.10 or later and the
``tcs_xtce`` package in ``../../python``:

.. code-block:: sh

   PYTHONPATH=../../python:python python3 python/main.py write >words.bin
   PYTHONPATH=../../python:python python3 python/main.py read <words.bin

Its ``Codec`` has a buffer preallocated to ``Word.MAX_BYTES`` and reused for
every word.

Makefile targets
----------------

``make``, or ``make run``
   Generate the code if needed, build every program, run it and check
   the output, as above.
``make build``
   Generate the code if needed and build every program, without running
   them, so that ``./to-from`` can.
``make generate``
   Run ``tcs-xtce`` on ``to_from.xtce`` for every language, if the XTCE
   file or the generator changed since the code was last generated.
``make rust``
   Check the Rust program's formatting with ``cargo fmt --check`` and build
   ``rust/target/release/to-from``.
``make c``
   Build ``build/to_from_c`` (C11) and ``build/to_from_cxx`` (C++20).
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

   * - ``to_from.xtce``
     - The XTCE description
   * - ``to-from``
     - The script that pipes a writer to a reader
   * - ``rust/Cargo.toml``, ``rust/src/main.rs``
     - The Rust program
   * - ``rust/src/to_from.rs``
     - Generated Rust module
   * - ``c/main.c``
     - The C and C++ program
   * - ``c/to_from.h``, ``cxx/to_from.h``
     - Generated header, from ``--lang c`` and ``--lang c++``
   * - ``python/main.py``
     - The Python program
   * - ``python/to_from.py``
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
