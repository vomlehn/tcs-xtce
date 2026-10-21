# tcsxtce: XTCE serialization libraries for Rust, C/C++ and Python
Flight software: a parser that takes XTCE (XML Telemetric and Command Exchange) definitions of commands and telemetry and produces data structures and methods for packing and unpacking data into and from buffers. It is a standard for space applications,equally useful for terrestrial applications, such as drones. Supports C/C++, Rust, and Python.

Includes libraries that serialize and deserialize OMG **XTCE** (XML Telemetric and
Command Exchange) data into managed, preallocated buffers, in two layers:

* **XML binding**: typed data structures for the whole XTCE schema, with
  code that reads and writes XTCE documents. The code for all three
  languages is generated from the official schema, which is downloaded
  into `references/`.
* **Packet codecs**: `tcsxtce` compiles one mission's XTCE document into
  typed telemetry and command structs, with binary encoders and decoders
  for the packets it describes.

The full documentation is in [`docs/manual.rst`](docs/manual.rst) (user
manual, including every data structure and method) and
[`docs/design.rst`](docs/design.rst) (design and rationale), each rendered
to HTML beside it by `make html`: see [See also](#see-also).

Work is under way on integrating tcsxtce with NASA's core Flight System (cFS)
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
| `generator/`  | `xtcegen` (generates the XML binding from the XSD) and `tcsxtce` (compiles a mission's XTCE into packet codecs; compiler in `xtcepkt`) |
| `rust/`       | `xtce` Rust crate |
| `cpp/`        | C++20 library (`libxtce.a`, `libxtce.so`) with a C API (`xtce/xtce.h`), and the header-only C/C++ packet runtime (`xtce/packet.h`) |
| `python/`     | `xtce` Python package (3.10+, standard library only) |
| `tests/`      | The demo mission, packet vectors and the cross-language checks; `make` generates the schema-coverage samples into `tests/samples/` |
| `docs/`       | `manual.rst` and `design.rst` |
| `examples/`   | Example XTCE files and a program per language that uses the code generated from them (start with `simple-string`) |

## Building

```sh
make              # fetch references if missing, generate, build, run all tests
make references   # re-check OMG and pull the latest XTCE documents
make generate     # regenerate the bindings after the schema or generator changes
make packets      # regenerate the demo mission's packet codecs
make test         # per-language tests, both cross-language checks and the examples
make examples     # build and run the examples in examples/
make fmt-check    # verify Rust formatting (also run by every `make rust`)
```

Requirements: `curl`, `xmllint`, Python 3.10+, a Rust toolchain with
`rustfmt`, and a C++20 compiler.

`references/Makefile` finds the latest version on the OMG specification page
(https://www.omg.org/spec/XTCE/) rather than hard-coding it. At the time of
writing that is XTCE 1.3, schema namespace
`http://www.omg.org/spec/XTCE/20250214`.

## How it works

1. **Parse.** `generator/xtcegen/xsd.py` reads `SpaceSystem.xsd` into a
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

Each library has a buffer type (`xtce::Buffer` in Rust and C++,
`xtce.ManagedBuffer` in Python) that is preallocated (64 KiB by default) and
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
use xtce::{Document, SpaceSystemType};

let mut doc = Document::from_bytes(&std::fs::read("sat.xml")?)?;
println!("{}", doc.root.name);
doc.root.short_description = Some("updated".into());
let bytes: &[u8] = doc.serialize()?;      // view into the managed buffer
```

C++:

```cpp
#include "xtce/document.hpp"

xtce::Document doc = xtce::Document::from_bytes(bytes);
doc.root.short_description = "updated";
std::string_view out = doc.serialize();   // view into the managed buffer
```

C:

```c
#include "xtce/xtce.h"

xtce_document* doc = xtce_document_new(0, 0);
if (xtce_document_load(doc, data, len) != XTCE_OK)
    fprintf(stderr, "line %zu: %s\n", xtce_document_error_line(doc), xtce_document_error(doc));
const uint8_t* out; size_t n;
xtce_document_serialize(doc, &out, &n);
xtce_document_free(doc);
```

Python:

```python
import xtce

doc = xtce.Document.from_file("sat.xml")
doc.root.short_description = "updated"
with doc.serialize() as view:             # memoryview of the managed buffer
    sys.stdout.buffer.write(view)
```

## Packet codecs

```sh
cd generator
python3 -m tcsxtce mission.xml mission.rs --lang rust
python3 -m tcsxtce mission.xml mission.h  --lang c        # or --lang c++: same file
python3 -m tcsxtce mission.xml mission.py --lang python
```

`--lang c` and `--lang c++` write the same header, which compiles both as C11
and as C++. It is self-contained (it includes only `xtce/packet.h`), allocates
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

* integer (four signed encodings, either byte order), IEEE float,
  enumerated, boolean, string, binary and time types;
* polynomial and spline calibrators;
* container inheritance with comparison criteria;
* included containers;
* fixed locations;
* include conditions;
* commands with argument assignments and fixed values.

See the manual for the exact list. `tests/packet_check.sh` checks that
Rust, C++ and Python produce identical results on hand-built and random
packets.

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

* [The tcsxtce manual](https://vomlehn.github.io/tcsxtce/docs/manual.html):
  every data structure and method, and the packet codecs.
* [The tcsxtce design](https://vomlehn.github.io/tcsxtce/docs/design.html):
  the design and its rationale.
* [The examples](https://vomlehn.github.io/tcsxtce/examples/README.html):
  what each example shows, and how to build and run it.

## License

Licensed under either of the Apache License, Version 2.0 ([LICENSE-APACHE](https://github.com/vomlehn/tcsxtce/blob/main/LICENSE-APACHE)
or <http://www.apache.org/licenses/LICENSE-2.0>) or the MIT license
([LICENSE-MIT](https://github.com/vomlehn/tcsxtce/blob/main/LICENSE-MIT) or <http://opensource.org/licenses/MIT>), at your option.
This covers all of it: the generator, the Rust crate, the C/C++ library and
the Python package.

Unless you explicitly state otherwise, any contribution intentionally submitted
for inclusion in the work by you, as defined in the Apache-2.0 license, shall be
dual licensed as above, without any additional terms or conditions.
