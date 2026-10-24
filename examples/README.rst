========
Examples
========

Every example supports all four languages ``tcs-xtce`` generates: C, C++,
Rust and Python 3. The C and C++ versions share one source file.
``make examples`` fails if an example is missing a version, or its
``README.rst``.

Each example is a self-contained directory holding:

* an XTCE file describing some data;
* a small program in each language that uses the code ``tcs-xtce``
  generates from it;
* a ``README.rst`` explaining what the example shows and how to run each
  program, and the ``README.html`` made from it;
* a ``Makefile`` that builds and runs the programs and makes
  ``README.html``.

The generated code isn't kept in git; building an example generates it.

Run ``make`` in an example's directory, or ``make examples`` at the top
level. This generates the code, or regenerates it if the XTCE file or
the generator changed, builds and runs the programs (the C one both as C,
against the ``--lang c`` header, and as C++, against the ``--lang c++``
header), and checks that their output is identical.

Run ``make html`` in an example's directory to turn its ``README.rst``
into ``README.html``. Run ``make`` in this directory to do that for this
file and every example's.

.. list-table::
   :header-rows: 1
   :widths: auto

   * - Example
     - Shows
   * - `simple-string <simple-string/README.html>`__
     - A structure holding one string of up to 8 bytes, serialized into
       and deserialized from managed buffers
   * - `to-from <to-from/README.html>`__
     - Words of varying length, with a leading size tag, written by the
       program in one language and read by the program in another, for
       any pair of C, C++, Rust and Python
   * - `variable-array <variable-array/README.html>`__
     - Two two-dimensional arrays whose dimensions both vary, the lines
       and words of a paragraph, sent one after the other over one TCP
       connection from one thread to another, each after its length
