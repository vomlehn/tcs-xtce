# tcs-xtce: an XTCE library

tcs-xtce is a Python library for OMG **XTCE** (XML Telemetric and Command
Exchange), the XML that describes a mission's commands and telemetry. It
reads, writes and builds XTCE documents as Python objects, one class for
each type of the XTCE schema, and its compiler turns a mission's
documents into the data structures and code that pack and unpack its
packets, in C/C++, Rust and Python. It reads one XTCE document, or the
several a mission is split across, as Yamcs loads them.

What one language writes, any other reads, byte for byte. The packets are
bit-packed, so a field takes only the bits its encoding gives, and they
carry no field names or tags: two programs check that they were built
from the same definitions by comparing a digest of them, which every
language computes the same way.

> **Warning:** tcs-xtce is an alpha release. It is not fully tested, it is
> not feature complete, and it is subject to rapid change. Comments and
> contributions are encouraged, even at this stage: open an issue at
> https://github.com/vomlehn/tcs-xtce/issues, and see
> [CONTRIBUTING.md](https://github.com/vomlehn/tcs-xtce/blob/main/CONTRIBUTING.md)
> for how to send changes.

## Installing

```sh
pip install tcs-xtce
```

This installs the library, `tcs_xtce`, and
[tcsxtce](https://pypi.org/project/tcsxtce/), the packet runtime that the
generated Python code imports. It installs no commands. It needs Python
3.10 or later and nothing else from PyPI.

## Using it

Reading and writing a document:

```python
from tcs_xtce import Document

doc = Document.from_file("mission.xtce")
print(doc.root.name)
for item in doc.root.telemetry_meta_data.parameter_set.items:
    if item.name == "Parameter":
        print(item.value.name, item.value.parameter_type_ref)
doc.root.short_description = "Edited in Python"
with open("edited.xtce", "wb") as f:
    doc.write_to(f)
```

Generating code, with the compiler run as a module:

```sh
python3 -m tcs_xtce mission.xtce mission.py --lang python
python3 -m tcs_xtce mission.xtce mission.h --lang c
python3 -m tcs_xtce mission.xtce mission.rs --lang rust
```

The digest that programs built from the same documents compare is
`python3 -m tcs_xtce.digest_cli mission.xtce`.

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
