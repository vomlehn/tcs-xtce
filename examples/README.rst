========
Examples
========

Every example exists in all supported languages: C/C++, Rust and Python 3.
``make examples`` fails if an example is missing one of them.

Each example is a self-contained directory holding:

* an XTCE file describing some data;
* a small program in each language that uses the code ``tcsxtce``
  generates from it.

The generated code isn't kept in git; building an example generates it.

Run ``make`` in an example's directory, or ``make examples`` at the top
level. This generates the code, or regenerates it if the XTCE file or
the generator changed, builds and runs the programs (the C one both as C
and as C++), and checks that their output is identical.

Run ``make`` in this directory to turn this file into ``README.html``.

.. list-table::
   :header-rows: 1
   :widths: auto

   * - Example
     - Shows
   * - `simple-string <simple-string/README.md>`__
     - A structure holding one string of up to 8 bytes, serialized into
       and deserialized from managed buffers
