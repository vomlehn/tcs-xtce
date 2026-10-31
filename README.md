# tcs-xtce: XTCE serialization libraries for Rust, C/C++ and Python
Flight software: a parser that takes XTCE (XML Telemetric and Command Exchange) definitions of commands and telemetry and produces data structures and methods for packing and unpacking data into and from buffers. It is a standard for space applications,equally useful for terrestrial applications, such as drones. Supports C/C++, Rust, and Python.

Includes libraries that serialize and deserialize OMG **XTCE** (XML Telemetric and
Command Exchange) data into managed, preallocated buffers, in two layers:

* **XML binding**: typed data structures for the whole XTCE schema, with
  code that reads and writes XTCE documents. The code for all three
  languages is generated from the official schema, which is downloaded
  into `references/`.
* **Packet codecs**: `tcs-xtce` compiles one mission's XTCE document, or
  the several documents it is split across, as Yamcs loads them, into
  typed telemetry and command structs, with binary encoders and decoders
  for the packets it describes.

XTCE defines the data structures once, and the code generated for each
language exchanges them: what one language writes, any other reads, byte for
byte. The packets are bit-packed, so a field takes only the bits its encoding
gives, with none of the padding a language's own structures have; the
housekeeping example's status packet is 7 bytes, where the C structure is 40.
Packets carry no field names or tags. Instead, programs check that they were
built from the same definition by comparing digests, which every runtime
(Rust, C/C++ and Python) computes the same way, as does `xtce-digest`: the
sender sends its digest first, and the receiver checks it, which guarantees
that they agree on every field's type and order. That avoids the per-message cost
of self-describing formats: the same packet is 77 bytes as JSON and 29 as
Protocol Buffers.

The full documentation is in [`docs/manual.rst`](https://github.com/vomlehn/tcs-xtce/blob/main/docs/manual.rst) (user
manual, including every data structure and method) and
[`docs/design.rst`](https://github.com/vomlehn/tcs-xtce/blob/main/docs/design.rst) (design and rationale), each rendered
to HTML beside it by `make html`: see [See also](#see-also).

Work is under way on integrating tcs-xtce with NASA's core Flight System (cFS)
and JPL's F´ (F Prime): generating cFS messages and F´ FPP from XTCE, or
reading their definitions into it, and dictionaries for their ground systems.
The plan, shared with [tcspecial](https://github.com/vomlehn/tcspecial) and
[tcslog](https://github.com/vomlehn/tcslog), is in tcspecial's
[`docs/third-party.rst`](https://github.com/vomlehn/tcspecial/blob/main/docs/third-party.rst).

> **Note:** integration with cFS and F´ is a work in progress. None of it is
> built yet, and the plan may change as it is.

| Directory     | Contents |
|---------------|----------|
| `references/` | Downloaded XTCE documents: `SpaceSystem.xsd`, `XTCE.pdf`, `VERSION`, plus `xml.xsd` and `catalog.xml` for offline validation |
| `rust/`       | `tcs-xtce` Rust crate |
| `cpp/`        | C++20 library (`libxtce.a`, `libxtce.so`) with a C API (`xtce.h`), and the header-only C/C++ packet runtime (`xtce-packet.h`) |
| `python/`     | `tcs_xtce` Python package (3.10+, standard library only): the Python runtime, `tcs_xtce.binding` (generates the XML binding from the XSD), `tcs-xtce` (compiles a mission's XTCE into packet codecs; compiler in `tcs_xtce.compiler`) and `xtce-digest` (an MD5 digest of what an XTCE document means, to spot changes) |
| `tests/`      | The demo mission, packet vectors and the cross-language checks; `make` generates the schema-coverage samples into `tests/samples/` |
| `docs/`       | `manual.rst` and `design.rst` |
| `examples/`   | Example XTCE files and a program per language that uses the code generated from them (start with `simple-string`) |

## Building

```sh
make              # fetch references if missing, generate, build, run all tests
make references   # re-check OMG and pull the latest XTCE documents
make generate     # regenerate the bindings after the schema or generator changes
make packets      # regenerate the demo mission's packet codecs
make test         # per-language tests, both cross-language checks, the examples
                  # and the 80-column check
make examples     # build and run the examples in examples/
make fmt-check    # verify Rust and Python formatting (Rust is also checked by
                  # every `make rust`)
```

Requirements: `curl`, `xmllint`, Python 3.10+, a Rust toolchain with
`rustfmt`, and a C++20 compiler.

`references/Makefile` finds the latest version on the OMG specification page
(https://www.omg.org/spec/XTCE/) rather than hard-coding it. At the time of
writing that is XTCE 1.3, schema namespace
`http://www.omg.org/spec/XTCE/20250214`.

## Installing

Everything below installs under `/opt/tcs`, the tree the TCS packages
share: the `tcs-xtce` and `xtce-digest` commands in `bin/`, the C/C++
headers in `include/`, `libxtce.so` and `libxtce.a` with `xtce.pc` (and
`tcs-ccsds.pc`, for the header-only time codes) in `lib/`, and the Python
packages `tcs_xtce` and `tcs_ccsds` in `lib/python3/site-packages/`.

**Packages.** `make deb` and `make rpm` (or `make packages`, both) build
one package, `tcs-xtce`, version `<version>-1`, into `build/packages/`; it needs `python3` 3.10 or
later. The package also puts `/opt/tcs/bin` on `PATH` and
`/opt/tcs/lib/pkgconfig` on `PKG_CONFIG_PATH` for login shells, tells the
loader about `/opt/tcs/lib`, and puts the Python packages on `python3`'s
path.

Installing and packaging use
[tcs-helpers](https://github.com/vomlehn/tcs-helpers), the packaging scripts
and make rules the TCS projects share, from a checkout of it beside this
one, so clone it there first. It is used where it is, never installed;
`TCS_HELPERS=<path>` names a checkout elsewhere, and `make tcs-helpers`
checks that it is there and new enough, which installing and packaging do
first.

```sh
git clone https://github.com/vomlehn/tcs-helpers.git ../tcs-helpers
make install              # build the packages, install this system's
make install SIMULATE=1   # show what install would do; change nothing
make uninstall            # remove the package
make uninstall SIMULATE=1 # show what uninstall would do
```

`make install` builds both packages as you, and then installs the one
this system takes with its usual installer: the .deb with `apt-get` on
Debian and Ubuntu, the RPM with `dnf` (or `yum`) on Fedora and Red
Hat's, or with `zypper` on SUSE's, each of which installs what the
package depends on too. Only that step runs as root, under `sudo`, which
asks for your password then; `SUDO=<command>` names another, and none is
used when make runs as root. `make uninstall` removes the package with
the same installer. `SIMULATE=1` has the installer show what it would do
-- `apt-get -s`, `dnf --assumeno`, `zypper --dry-run` -- without sudo,
changing nothing.

The system is known by the `ID` and `ID_LIKE` of `/etc/os-release`, not
by which tools it has. On one it does not know, `make install` says so
and stops; there, or to install the other kind, `PACKAGE_FORMAT=deb` or
`PACKAGE_FORMAT=rpm` chooses the kind, and `INSTALLER=<command>` the
installer (`apt-get`, `apt`, `dpkg`, `dnf`, `yum`, `zypper` or `rpm`).
If the kind it needs was not built, because `dpkg-deb` or `rpmbuild` is
missing, it says which to install; `make deb` and `make rpm` themselves
say so and carry on without the tool, so that `make packages` builds
what it can.

`/etc/profile.d` is read only by login shells, so `sudo`, cron and other
minimal environments do not see `/opt/tcs/bin`. To make `tcs-xtce` and
`xtce-digest` available everywhere, link them into `/usr/local/bin`:

```sh
sudo /opt/tcs/sbin/tcs-xtce-links             # make the links
sudo /opt/tcs/sbin/tcs-xtce-links --remove    # take them away again
```

`--dir DIR` links into another directory, such as `/opt/bin`. Only the
package's own programs are linked, and anything already there that is not
one of its links is left alone and named. Removing the package removes
the links from `/usr/local/bin` and `/opt/bin`; installing it never makes
them. After `make install`, `sudo make links` and `sudo make unlinks` do
the same (`LINKDIR` gives the directory).

**From the source tree.** `sudo make install-files` copies the same files
into place without a package, for development, which is what `make
install` once did, but nothing outside `/opt/tcs`; `make install-files
PREFIX=$HOME` installs under your home instead, `DESTDIR` stages it, and
`make uninstall-files`, with the same variables, removes them. `make
install` takes neither variable, and stops if given one. `make stage`
copies into `build/stage/tcs-xtce/`, from which the packages are made.

`make install-files`, `make stage` and so the packages strip the
libraries they install: `libxtce.so` of all a debugger needs (`strip
--strip-unneeded`), `libxtce.a` of its debugging information (`strip
--strip-debug`). Give `STRIP=0`, as in `make packages STRIP=0`, to keep
everything as built.

The .deb also carries what Debian expects of every package, outside
`/opt/tcs` as the hooks are: `/usr/share/doc/tcs-xtce/copyright`, in
Debian's machine-readable format, and `changelog.Debian.gz`, both made as
the package is built from `LICENSE-MIT` and the version; lintian's
overrides in `/usr/share/lintian/overrides/tcs-xtce`; and a trigger that
has dpkg run `ldconfig` once the library is installed or removed. The RPM
says the same in its own `License`, `%changelog`, `%post` and `%postun`.
`make lint-packages` checks the packages that `make packages` built with
lintian and rpmlint, and their shell scripts with shellcheck. It skips,
in a line, any of the three that is not installed, and fails on any
finding that is not overridden; each override says why it is intended, in
`packaging/deb/tcs-xtce/lintian-overrides` and
`packaging/rpm/rpmlint.toml`.

**Python.** `pip install .` from the top of the repository, or the wheel
`make python-dist` builds in `dist/`, installs `tcs_xtce` and both
commands.

**Rust.** `cargo add tcs-xtce` (and `cargo add tcs-ccsds`); the crates
come from crates.io and nothing here installs them.

**C and C++.** With `/opt/tcs/lib/pkgconfig` on `PKG_CONFIG_PATH`:

```sh
cc  prog.c   $(pkg-config --cflags --libs xtce)
c++ prog.cpp $(pkg-config --cflags --libs xtce)
cc  prog.c   $(pkg-config --cflags --libs --static xtce)   # libxtce.a
```

The shared library's soname is `libxtce.so.N`, where N is the ABI version
in `cpp/abi-version`, which changes only when a change breaks programs
already linked against it; it is not the package's version.

## How it works

1. **Parse.** `python/tcs_xtce/binding/xsd.py` reads `SpaceSystem.xsd` into a
   language-neutral model and flattens type inheritance (`complexContent`
   extensions). XSD constructs it does not support raise an error, so schema
   changes cannot silently produce incomplete bindings.
2. **Bind.** `binding.py` maps every complex type to a record with
   attribute, text, element and `<choice>` fields. It checks that one element
   of lookahead is enough to decode each content model, boxes fields that
   would otherwise make recursive types infinitely large, orders types by
   dependency, and picks a finite default for every required choice.
3. **Emit.** `emit_rust.py`, `emit_cpp.py` and `emit_python.py` generate the
   types and their readers and writers. Generated Rust is run through
   `rustfmt`.
4. **Sample.** `sample.py` generates documents that use every element of
   every type reachable from `<SpaceSystem>`, and every alternative of every
   choice (see `tests/samples/COVERAGE.txt`).

The tests check that:

* every sample validates against the official XSD (`xmllint`);
* each library round-trips every sample (deserialize, serialize, deserialize,
  then compare);
* the Rust, C++ and Python outputs are **byte-identical**; and
* the serialized output also validates against the XSD.

### Type mapping

| XSD                                   | Rust             | C++                     | Python           |
|---------------------------------------|------------------|-------------------------|------------------|
| complex type                          | `struct`         | `struct`                | `@dataclass`     |
| `<choice>`                            | `enum`           | `struct` + `std::variant` | `@dataclass` (`name`, `value`) |
| enumeration                           | `enum`           | `enum class`            | `str` `Enum`     |
| optional element or attribute         | `Option<T>`      | `std::optional<T>`      | `Optional[T]`    |
| repeated element                      | `Vec<T>`         | `std::vector<T>`        | `list[T]`        |
| recursive field                       | `Box<T>`         | `xtce::Box<T>`          | (plain reference) |
| `string`, dates, durations, `hexBinary`, unions | `String` | `std::string`     | `str`            |
| `boolean` / `long`, `integer` / `int` / `unsignedShort` / `double` | `bool` / `i64` / `i32` / `u16` / `f64` | `bool` / `int64_t` / `int32_t` / `uint16_t` / `double` | `bool` / `int` / `float` |

Type names match the schema (`SpaceSystemType`, `IntegerParameterType`, ...),
and field names are the snake_case form of the XML names.

### Managed buffers

Each library has a buffer type (`tcs_xtce::Buffer` in Rust, `xtce::Buffer` in C++,
`tcs_xtce.ManagedBuffer` in Python) that is preallocated (64 KiB by default) and
keeps its allocation between uses. A `Document` owns one input buffer and one
output buffer alongside the root `SpaceSystemType`:

* `load` and `read_from` copy the input into the input buffer and parse it
  in place; the Rust and C++ readers work on slices of that buffer and only
  copy data into the fields they fill;
* `serialize` writes into the output buffer and returns a borrowed view of
  it, without allocating a new buffer;
* both buffers are released when the `Document` is dropped, destroyed or
  garbage-collected (Python also has `close()` and `with`).

## Usage

Rust:

```rust
use tcs_xtce::{Document, SpaceSystemType};

let mut doc = Document::from_bytes(&std::fs::read("sat.xtce")?)?;
println!("{}", doc.root.name);
doc.root.short_description = Some("updated".into());
let bytes: &[u8] = doc.serialize()?;      // view into the managed buffer
```

C++:

```cpp
#include "xtce-document.hpp"

xtce::Document doc = xtce::Document::from_bytes(bytes);
doc.root.short_description = "updated";
std::string_view out = doc.serialize();   // view into the managed buffer
```

C:

```c
#include "xtce.h"

xtce_document* doc = xtce_document_new(0, 0);
if (xtce_document_load(doc, data, len) != XTCE_OK)
        fprintf(stderr, "line %zu: %s\n", xtce_document_error_line(doc),
                xtce_document_error(doc));
const uint8_t* out; size_t n;
xtce_document_serialize(doc, &out, &n);
xtce_document_free(doc);
```

Python:

```python
import tcs_xtce

doc = tcs_xtce.Document.from_file("sat.xtce")
doc.root.short_description = "updated"
with doc.serialize() as view:             # memoryview of the managed buffer
    sys.stdout.buffer.write(view)
```

## Packet codecs

```sh
cd python
python3 -m tcs_xtce mission.xtce mission.rs --lang rust
python3 -m tcs_xtce mission.xtce mission.h  --lang c        # or --lang c++: same file
python3 -m tcs_xtce mission.xtce mission.py --lang python
```

Or install the Python package, runtime and compiler (see
[Installing](#installing)), and use the `tcs-xtce` command from anywhere.
It is not on PyPI yet:

```sh
make python-dist
pip install dist/tcs_xtce-*.whl
tcs-xtce mission.xtce mission.rs --lang rust
```

`--lang c` and `--lang c++` write the same header, which compiles both as C11
and as C++. It is self-contained (it includes only `xtce-packet.h`), allocates
nothing, and reports errors as return codes.

For each concrete `SequenceContainer` and `MetaCommand`, this generates a
struct with `decode` and `encode` functions. Each inheritance tree also gets
a `<Root>Kind` type that decodes a packet into whichever container or
command it matches. Values fixed by restriction criteria or argument
assignments (such as an APID or opcode) are written automatically and are
not fields. Packets are encoded into managed buffers. A `Codec` owns an
input buffer and an output buffer, preallocated to the largest packet size.

```rust
let mut codec = Codec::for_packet::<demo::CCSDSPacketKind>();
codec.load(&frame);
if let demo::CCSDSPacketKind::Housekeeping(hk) = codec.decode::<demo::CCSDSPacketKind>()? {
    println!("{} degC", hk.temperature);
}
let wire = codec.encode(&demo::SetMode { mode: demo::ModeArgType::SCIENCE, ..Default::default() })?;
```

Supported features:

* every XTCE data type: integers (any size, every signed encoding and
  BCD; past 64 bits they are held with the bnum crate in Rust and with
  `xtce-bnum.h`, a C and C++ library that mirrors it), floats (IEEE 754
  16 to 128 bits, MIL-STD-1750A, DEC, IBM, TI), strings in every
  character set, binary, enumerations, booleans, times, arrays and
  aggregates, in any bit and byte order, with valid ranges checked when
  encoding;
* polynomial, spline, math operation and context calibrators, also in
  aggregate members;
* container inheritance with comparisons and boolean expressions,
  included and repeated containers, locations from either end and from
  the next entry, include conditions, container sizes, parameter
  segments (repeated too), indirect entries, messages, and transform
  algorithms the application supplies;
* packet length fields, also of included containers, sizes and counts
  from other values, and error detection: CRCs, checksums, XOR and
  parity;
* commands, including containers from a `CommandContainerSet` and
  other meta-commands' containers, even whole commands;
* missions split across several documents, with one digest for the set;
* alarms, including the ones that depend on earlier samples, and
  `MathAlgorithm`s;
* fixed and variable frame streams, with frame synchronization, every
  PCM code including bi-phase, and custom streams that chain into them;
* custom algorithms, which the application supplies as functions.

tcs-ccsds, in `ccsds/`, is a separate library of CCSDS standards that XTCE
doesn't define, in the same four languages: so far the CUC and CDS time
codes.

See the manual for the exact list. `tests/packet_check.sh` checks that
Rust, C, C++ and Python produce identical results on hand-built and
random packets.

## Behaviour and limitations

* The bindings follow XTCE 1.3 strictly. Documents written for older
  versions are rejected wherever the schema changed. For example, 1.3 no
  longer allows the `rangeAppliesToCalibrated` attribute, and
  `FixedValueEntry` now requires `sizeInBits`. Errors give the line number.
* Elements are matched by local name; the namespace URI is not checked.
* Values that the schema constrains only by patterns, ranges or `xs:key`
  are stored but not validated. Use `xmllint` with `references/catalog.xml`
  for full validation.
* Schema default values are not filled in: an attribute that was not
  present stays `None` / empty, so documents round-trip as written. The
  defaults appear in the generated documentation comments.
* Comments, processing instructions and attributes in other namespaces
  (such as `xsi:schemaLocation`) are not preserved. Doubles are written in
  their shortest round-trip form.
* `xs:integer` is stored as a 64-bit integer.

## See also

* [The tcs-xtce manual](https://vomlehn.github.io/tcs-xtce/docs/manual.html):
  every data structure and method, and the packet codecs.
* [The tcs-xtce design](https://vomlehn.github.io/tcs-xtce/docs/design.html):
  the design and its rationale.
* [The examples](https://vomlehn.github.io/tcs-xtce/examples/README.html):
  what each example shows, and how to build and run it.
* [The Rust crate on crates.io](https://crates.io/crates/tcs-xtce):
  `cargo add tcs-xtce`.

## Incompatible changes

**0.4.0: the C and C++ headers were renamed.** They are no longer
in the directories `xtce/` and `ccsds/` but straight in the include
directory, each with its library's prefix, so that installed in a shared
`include/` none is mistaken for another library's, and `ccsds-time.h`
cannot shadow the system's `<time.h>`. Change each `#include` as below;
code that `tcs-xtce` generated has to be generated again.

| Was                  | Now                   |
|----------------------|-----------------------|
| `xtce/xtce.h`        | `xtce.h`              |
| `xtce/packet.h`      | `xtce-packet.h`       |
| `xtce/digest.h`      | `xtce-digest.h`       |
| `xtce/bnum.h`        | `xtce-bnum.h`         |
| `xtce/bnum.hpp`      | `xtce-bnum.hpp`       |
| `xtce/buffer.hpp`    | `xtce-buffer.hpp`     |
| `xtce/xml.hpp`       | `xtce-xml.hpp`        |
| `xtce/document.hpp`  | `xtce-document.hpp`   |
| `xtce/generated.hpp` | `xtce-generated.hpp`  |
| `ccsds/time.h`       | `ccsds-time.h`        |

The functions, types and macros in them keep their names.

## Contributing

This is a very early release, and comments and collaborators are very
welcome. See [CONTRIBUTING.md](https://github.com/vomlehn/tcs-xtce/blob/main/CONTRIBUTING.md)
for how to report problems, ask for XTCE features, and build, test and send
changes.

## License

Licensed under either of the Apache License, Version 2.0 ([LICENSE-APACHE](https://github.com/vomlehn/tcs-xtce/blob/main/LICENSE-APACHE)
or <http://www.apache.org/licenses/LICENSE-2.0>) or the MIT license
([LICENSE-MIT](https://github.com/vomlehn/tcs-xtce/blob/main/LICENSE-MIT) or <http://opensource.org/licenses/MIT>), at your option.
This covers all of it: the generator, the Rust crate, the C/C++ library and
the Python package.

Unless you explicitly state otherwise, any contribution intentionally submitted
for inclusion in the work by you, as defined in the Apache-2.0 license, shall be
dual licensed as above, without any additional terms or conditions.
