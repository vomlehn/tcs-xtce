to-from
=======

Words of varying length, described in XTCE, written by the program in one
language and read by the program in another: Rust, C, C++ or Python.

The data is the standard lorem ipsum paragraph ("Lorem ipsum dolor sit amet,
consectetur adipiscing elit, …"), split at spaces into its 69 words, so
punctuation stays with its word. Each word is one ``Word`` packet.

The example shows two things:

* **Strings of varying length.** A ``Word`` is as long as its text, plus a
  four-byte size tag that XTCE describes.
* **Writing in one language and reading in another.** The code each
  language's generator produces writes the same bytes, and reads the bytes
  any of the others wrote.

Each language's program takes one argument:

``write``
   Serializes each word into a ``Word`` packet and writes the packets, one
   after another, to standard output.

``read``
   Reads packets from standard input and deserializes them. It prints each
   word as it arrives, with a space before every word except the first, and
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
   Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.

Here the C program wrote the packets and the Python program read them.
``to-from`` exits with status 1 if either program fails, and with status 2,
after a usage message, if its arguments are wrong. Build the programs with
``make build`` before running it.

The XTCE description
--------------------

`to_from.xml <to_from.xml>`__ defines:

* a string type, ``WordType``, of varying length (``Variable``), with an
  32-bit size tag before the string
  (``LeadingSize sizeInBitsOfSizeTag="32"``). The tag and the string
  together occupy at most 288 bits, so a word holds up to 32 bytes of UTF-8;
* a parameter, ``Text``, of that type;
* a container, ``Word``, holding ``Text``.

On the wire a word is its length in bytes, as a 32-bit unsigned number,
most significant byte first, followed by its bytes, with no terminator or
padding. ``"Lorem"`` is ``00000005 4c6f72656d``, and ``"sit"`` is
``00000003 736974``. A word longer than 32 bytes can't be serialized.

The packets follow each other with nothing between them. Because a ``Word``
packet starts with its size tag, the reader reads those four bytes, then that
many more, and hands the whole packet to the generated decoder.

The file validates against the XTCE 1.3 schema:

.. code-block:: sh

   XML_CATALOG_FILES=../../references/catalog.xml \
       xmllint --noout --nonet --schema ../../references/SpaceSystem.xsd to_from.xml

Generating the code
-------------------

``make generate`` runs the generator once per language:

.. code-block:: sh

   cd ../../generator
   python3 -m tcs_xtce ../examples/to-from/to_from.xml \
       ../examples/to-from/rust/src/to_from.rs --lang rust
   python3 -m tcs_xtce ../examples/to-from/to_from.xml \
       ../examples/to-from/c/to_from.h --lang c
   python3 -m tcs_xtce ../examples/to-from/to_from.xml \
       ../examples/to-from/cxx/to_from.h --lang c++
   python3 -m tcs_xtce ../examples/to-from/to_from.xml \
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
   Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor ...
   to-from: the 4 writers wrote the same bytes, and the 16 reader/writer pairs printed the same words

``make build`` only generates the code and builds the programs, for
``./to-from``. ``make html`` turns this file into ``README.html``.

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

or ``cd rust && cargo run --release -- read`` (or ``write``). Its ``Codec``
has a buffer preallocated to ``Word::MAX_BYTES`` and reused for every word.

C and C++
~~~~~~~~~

`c/main.c <c/main.c>`__ is built twice, once as C11 and once as C++20. It
needs only the header ``../../cpp/include/xtce/packet.h`` and the generated
header; it links with no tcs-xtce library.

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
``xtce`` package in ``../../python``:

.. code-block:: sh

   PYTHONPATH=../../python:python python3 python/main.py write >words.bin
   PYTHONPATH=../../python:python python3 python/main.py read <words.bin

Its ``Codec`` has a buffer preallocated to ``Word.MAX_BYTES`` and reused for
every word.

Requirements
------------

Building needs what the top-level build needs: Rust with ``rustfmt``, a C11
compiler, a C++20 compiler, and Python 3.10 or later. ``make html`` needs
``rst2html``, from Docutils.
