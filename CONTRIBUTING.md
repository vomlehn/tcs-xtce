# Contributing to tcs-xtce

tcs-xtce is a very early release. Comments are very welcome, as are people
interested in collaborating on it. You don't need to write code to help:
telling us what is missing or wrong, or which XTCE documents it can't
handle, is just as useful.

## Ways to help

* **Report a bug.** Open an issue with the XTCE document (or the smallest
  part of it that shows the problem), the command you ran, and what
  happened. Packet bugs are easiest to fix with the bytes of the packet in
  hex.
* **Ask for an XTCE feature.** `tcs-xtce` names any construct it doesn't
  support and where it is. Open an issue with that message and, if you
  can, the document that uses it. The supported subset is listed under
  [Supported XTCE](https://vomlehn.github.io/tcs-xtce/docs/manual.html#supported-xtce)
  in the manual.
* **Discuss a larger idea**, such as the cFS and F´ integration, in an
  issue before writing code, so we can agree on the approach first.
* **Send a pull request.** Read the rules below first.

## Building and testing

You need `curl`, `xmllint`, Python 3.10 or later, a Rust toolchain with
`rustfmt`, a C11 compiler and a C++20 compiler. `rst2html` (from docutils)
is only needed to rebuild the documentation.

```sh
make          # fetch the XTCE references if missing, generate, build, test
make test     # run every test again
make fmt      # format the Rust code
```

`make test` must pass before a change is merged. CI runs `make`,
`make samples-check` and a `cargo publish --dry-run` on every push and pull
request, from a clean checkout.

## Rules for changes

**Rust, C/C++ and Python stay in step.** The three languages are generated
from the same model and must give byte-identical results; the tests compare
them on every build. A change to what the generated code does goes into all
three emitters (`python/tcs_xtce/compiler/emit_rust.py`, `emit_c.py`,
`emit_python.py`) and their runtimes, with tests. Every example in
`examples/` has a Rust, C/C++ and Python version, a `README.rst`, and a
`Makefile` that runs it and, with `make html`, makes `README.html`.

**Line width and indentation.** All code, generated and hand-written, in
every language, keeps its lines within 80 columns; so do Makefiles and
scripts. `make test` checks this (`make line-width` checks it alone),
counting a tab as 8 columns, and names each line that is too wide.

* C and C++ indent with 8 spaces, never tabs. `.clang-format` at the top
  level sets this, so format hand-written C and C++ with `clang-format -i`.
* Rust is formatted by `make fmt` (`rustfmt.toml` sets `max_width = 80`).
  Wrap by hand what rustfmt leaves long: comments, string literals (split
  them with a backslash at the end of the line, which keeps their value),
  and code inside `macro_rules!` bodies and macros such as `matches!`.
* Python indents 4 spaces. `make fmt` lays it out
  (`python/tcs_xtce/binding/pyalign.py`, the same code that lays out the generated
  Python) and `make fmt-check` checks it: a long line breaks inside
  brackets, with the continuation lined up under the opening bracket, items
  packed, and the closing bracket at the end of the last line:

  ```python
  self.line(f"XTCE_PK_TRY(xtce_pk_read_utf8(r, n, {f}, "
            f"{dst}.data, &{dst}.len));", depth + 1)
  ```

  When the bracket is too far right for that, when lining up would make a
  staircase of nested brackets, or when the bracket holds comments, it uses
  a hanging indent of 4 instead, still with the closer on the last line. A
  long value after `=` or `return`, or a long `if` condition, goes in
  parentheses so it can break. Don't run black or `ruff format`, which
  would rewrite this style. Keep `# noqa` and `# pragma` comments on the
  line they apply to.
* The generators emit the same indentation and wrap their output at 80
  columns (`python/tcs_xtce/binding/wrap.py`), so generated code needs no
  formatting.

**Every function has a header.** Each function, hand-written or
generated, whose body is more than three lines starts with a comment that
says what it does, lists its parameters with what each means, and says
what it returns (no return section for a function that returns nothing).
The parameters' descriptions, continuation lines included, start in one
column. Each follows its language's conventions: Rust's rustdoc as RFC
1574 and the standard library write it (`# Arguments` with
`` * `name` - Meaning ``, `# Returns`, and `# Errors`, `# Panics` and
`# Safety` where they apply), Doxygen `/** ... */` comments with `@param`
and `@return` in C and C++, and PEP 257 docstrings in Google's style
(`Args:`, `Returns:`, `Raises:`) in Python.
`tests/doc_headers.py` (part of `make test`, or `make doc-headers` alone)
gives the exact formats and the exemptions, such as tests and trait
implementations, and names each function whose header is missing or
wrong. The generators write such a header for every function they emit.

**The shared library's ABI version is raised only for a break.**
`libxtce.so`'s soname is `libxtce.so.N`, N being the whole number in
`cpp/abi-version`, which `cpp/Makefile` and the top-level Makefile both
read. It is not the package's version. Raise it by one, in the same
change, when a program already linked against the library would stop
working: a function of `xtce.h` removed, or given other parameters or
another meaning, or a C++ type of `xtce-*.hpp` changed in size or layout.
Adding a function does not raise it.

**C and C++ headers are named with their library's prefix**, `xtce-` or
`ccsds-`, and live straight in `cpp/include/` or `ccsds/include/`, as they
are installed straight in `/opt/tcs/include`.

**The digest is the same in every runtime.** `python/tcs_xtce/digest.py`,
`rust/src/digest.rs` and `cpp/include/xtce-digest.h` compute the same XTCE
digest, and `tests/digest_check.sh` (part of `make test`) checks that they
agree with each other and with `tests/digest/expected.txt`. A change to what
the digest covers, including the `UNORDERED` list, goes into all three, with
the expected digests updated.

**Don't edit generated files.** Files that start with `@generated` are
rewritten by `make generate` (the XML binding, from `python/tcs_xtce/binding`) or
`make packets` (the demo mission's codecs, from `python/tcs_xtce/compiler`).
Change the generator and regenerate instead.

**Code generated with `--dynamic-allocations false` never allocates.**
`tests/packet_check.sh` fails if the Rust codecs generated that way allocate
while decoding, encoding or describing a packet. Keep heap use out of those
code paths.

**New XTCE support comes with tests and documentation.** Add tests in
`python/tests/compiler`, and where it affects packets, extend the demo mission
(`tests/mission/demo.xml`) and its vectors (`tests/mission/vectors.txt` and
`vectors.expected`). List the construct under Supported XTCE in
`docs/manual.rst`.

**Keep the documentation current.** The user manual is `docs/manual.rst` and
the design is `docs/design.rst`. Their HTML is committed beside them and
served by GitHub Pages, so run `make html` and commit the HTML with any
change to them.

**Match the code around you.** Rust is formatted with `rustfmt`
(`make fmt`, checked by `make rust`), C and C++ with `clang-format`, and
Python with `make fmt` in the style above. Commit messages have a short summary
line and a body that says why the change was made.

## License

Unless you say otherwise, any contribution you intentionally submit for
inclusion in tcs-xtce, as defined in the Apache-2.0 license, is dual
licensed under the Apache License, Version 2.0 and the MIT license, as the
rest of the project is, without any additional terms or conditions.
