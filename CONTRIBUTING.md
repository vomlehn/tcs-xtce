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
three emitters (`generator/xtcepkt/emit_rust.py`, `emit_c.py`,
`emit_python.py`) and their runtimes, with tests. Every example in
`examples/` has a Rust, C/C++ and Python version.

**Don't edit generated files.** Files that start with `@generated` are
rewritten by `make generate` (the XML binding, from `generator/xtcegen`) or
`make packets` (the demo mission's codecs, from `generator/xtcepkt`).
Change the generator and regenerate instead.

**Code generated with `--dynamic-allocations false` never allocates.**
`tests/packet_check.sh` fails if the Rust codecs generated that way allocate
while decoding, encoding or describing a packet. Keep heap use out of those
code paths.

**New XTCE support comes with tests and documentation.** Add tests in
`generator/tests`, and where it affects packets, extend the demo mission
(`tests/mission/demo.xml`) and its vectors (`tests/mission/vectors.txt` and
`vectors.expected`). List the construct under Supported XTCE in
`docs/manual.rst`.

**Keep the documentation current.** The user manual is `docs/manual.rst` and
the design is `docs/design.rst`. Their HTML is committed beside them and
served by GitHub Pages, so run `make html` and commit the HTML with any
change to them.

**Match the code around you.** Rust is formatted with `rustfmt`
(`make fmt`, checked by `make rust`). Python follows the style of the
existing generator. Commit messages have a short summary line and a body
that says why the change was made.

## License

Unless you say otherwise, any contribution you intentionally submit for
inclusion in tcs-xtce, as defined in the Apache-2.0 license, is dual
licensed under the Apache License, Version 2.0 and the MIT license, as the
rest of the project is, without any additional terms or conditions.
