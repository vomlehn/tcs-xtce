# tcs-xtce: an XTCE compiler

tcs-xtce compiles a mission's OMG **XTCE** (XML Telemetric and Command
Exchange) definitions of its commands and telemetry into the data
structures and code that pack and unpack them, in C/C++, Rust and Python.
It reads one XTCE document, or the several a mission is split across, as
Yamcs loads them.

What one language writes, any other reads, byte for byte. The packets are
bit-packed, so a field takes only the bits its encoding gives, and they
carry no field names or tags: two programs check that they were built
from the same definitions by comparing a digest of them, which every
language computes the same way.

> **Warning:** tcs-xtce is an alpha release. It is not fully tested, it is
> not feature complete, and it is subject to rapid change. Feedback is
> welcome: open an issue at https://github.com/vomlehn/tcs-xtce/issues.

## Installing

```sh
pip install tcs-xtce
```

This installs the compiler, `tcs-xtce`; `xtce-digest`, which prints an
XTCE document's digest; and
[tcsxtce](https://pypi.org/project/tcsxtce/), the packet runtime that the
generated Python code imports. It needs Python 3.10 or later and nothing
else from PyPI.

## Using it

```sh
tcs-xtce mission.xtce mission.py --lang python
tcs-xtce mission.xtce mission.h --lang c
tcs-xtce mission.xtce mission.rs --lang rust
```

What the generated code needs:

* **Python**: the `tcsxtce` package, which `pip install tcs-xtce`
  installs. A program that only uses the generated code can install
  `tcsxtce` alone.
* **C and C++**: the header `tcsxtce-packet.h`, which is not on PyPI: it
  is in [`cpp/include`](https://github.com/vomlehn/tcs-xtce/tree/main/cpp/include)
  in the repository, and tcs-xtce's tcsxtce `.deb` and RPM install it.
* **Rust**: the [tcsxtce crate](https://crates.io/crates/tcsxtce). The
  compiler lays out the Rust it writes with **rustfmt**, which pip cannot
  install: with `--lang rust` it stops with an error where `rustfmt` is
  not on `PATH`. A Rust toolchain from [rustup](https://rustup.rs) has it.

## Documentation

* [The manual](https://vomlehn.github.io/tcs-xtce/docs/tcs-xtce.html):
  the command line, the generated code in each language, and every XTCE
  construct the compiler supports.
* [The design](https://vomlehn.github.io/tcs-xtce/docs/tcs-xtce-design.html).
* [The repository](https://github.com/vomlehn/tcs-xtce), which also has
  tcs-xtcee, an XTCE editor, and tcsccsds, the CCSDS time codes.

## License

MIT or Apache-2.0, at your option.
