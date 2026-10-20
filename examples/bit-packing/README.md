# bit-packing

A status record of five small fields, generated with `tcsxtce --bit-packing`
so that the fields share bytes in memory instead of each taking its own.
Serialized and deserialized from Rust, C, C++ and Python.

## The XTCE description

[`bit_packing.xml`](bit_packing.xml) defines a container, `Status`, holding:

| Field | Type | Encoding | Bits |
|---|---|---|---|
| `Mode` | `ModeType`: `Off` 0, `Safe` 1, `Nominal` 2, `Science` 5 | unsigned | 3 |
| `Level` | `U5` | unsigned | 5 |
| `Enabled` | `FlagType` (boolean) | unsigned | 1 |
| `Offset` | `S4` | two's complement | 4 |
| `Count` | `U10` | unsigned | 10 |

The fields add up to 23 bits, so they fit in 3 bytes, both on the wire and,
with bit packing, in memory. Without bit packing, each field is an ordinary
member: in C the struct holds an `int64_t` enumeration, a `uint8_t`, a
`bool`, an `int8_t` and a `uint16_t`, and takes 16 bytes. With bit packing it
takes 3.

The file validates against the XTCE 1.3 schema:

```sh
XML_CATALOG_FILES=../../references/catalog.xml \
    xmllint --noout --nonet --schema ../../references/SpaceSystem.xsd bit_packing.xml
```

## Generating the code

`make generate` runs the generator once per language, with `--bit-packing`:

```sh
cd ../../generator
python3 -m tcsxtce ../examples/bit-packing/bit_packing.xml \
    ../examples/bit-packing/rust/src/bit_packing.rs --lang rust --bit-packing
python3 -m tcsxtce ../examples/bit-packing/bit_packing.xml \
    ../examples/bit-packing/c/bit_packing.h --lang c --bit-packing
python3 -m tcsxtce ../examples/bit-packing/bit_packing.xml \
    ../examples/bit-packing/python/bit_packing.py --lang python --bit-packing
```

Each language gets a `Status` structure whose five fields live in a 3-byte
array and are reached through accessors. In C/C++ every name starts with
`bit_packing_`, the mission's prefix.

| | Rust | C/C++ | Python |
|---|---|---|---|
| Storage | `packed() -> &[u8; 3]` | `uint8_t packed[3]` | `packed` (`bytes`, read-only) |
| Read a field | `level() -> u8` | `bit_packing_Status_level(p)` | `p.level` |
| Set a field | `set_level(v) -> Result<()>` | `bit_packing_Status_set_level(p, v, &err)` | `p.level = v` |
| Set a boolean | `set_enabled(v)` | `bit_packing_Status_set_enabled(p, v)` | `p.enabled = v` |
| Create | `Status::default()`, then setters | `bit_packing_Status_init(&p)`, then setters | `Status(mode=..., level=...)` |

Setters fail with `OutOfRange` when a value doesn't fit its encoding, and
leave the field unchanged. Boolean setters can't fail.

## The layout

The packed storage uses the order XTCE uses for encoded ("packed") data.
Bit 0 is the most significant bit of byte 0. Each field starts where the
previous one ended, its value is stored most significant bit first, and a
field that doesn't fit in the rest of a byte continues in the next one. A
little-endian field would be stored with its bytes swapped, as it is
encoded. No C bit-fields are used, so the layout is the same in every
language and with every compiler.

For `Mode` = `Science` (5), `Level` 19, `Enabled` true, `Offset` -3 and
`Count` 600:

```
byte 0  bit 7 ............ 0      byte 1  bit 7 ............ 0      byte 2  bit 7 ............ 0
        1 0 1 | 1 0 0 1 1                 1 | 1 1 0 1 | 1 0 0               1 0 1 1 0 0 0 | 0
        Mode=5| Level=19                  E | Offset  | Count (high 3)      Count (low 7) | unused
```

* The 3-bit `Mode` and the 5-bit `Level` share byte 0. `E` is `Enabled`.
* `Count` (600 = `1001011000`) starts at bit 13. Its high 3 bits fill the
  bottom of byte 1, and its low 7 bits go into byte 2.
* The last bit is unused and always 0.

Because every field of `Status` is packed and nothing sits between them on
the wire, the storage (`b3 ec b0`) is byte for byte the serialized record.
That isn't true in general: see "Bit packing" in the
[manual](../../docs/manual.rst) for how the two differ.

## The programs

[`rust/src/main.rs`](rust/src/main.rs), [`c/main.c`](c/main.c) and
[`python/main.py`](python/main.py) each do the following. `c/main.c` is
compiled twice, once as C and once as C++.

1. Build a `Status` with the setters (in Python, the constructor).
2. Print the 3 bytes of packed storage in binary.
3. Try to set `Level` to 32, which needs 6 bits, and show that it is
   rejected and `Level` is unchanged.
4. Serialize the record, deserialize it again, and check that the packed
   storage is the same.

```
$ make
...
value: Status{mode=Science, level=19, enabled=true, offset=-3, count=600}
storage: 3 bytes
storage byte 0: 10110011
storage byte 1: 11101100
storage byte 2: 10110000
set level 32 -> error:OutOfRange (value of Level does not fit its encoding); level is still 19
serialize -> b3ecb0
deserialize -> Status{mode=Science, level=19, enabled=true, offset=-3, count=600}
storage after deserializing matches: true
bit-packing: Rust, C, C++ and Python output identical
```

Building needs what the top-level build needs: Rust with `rustfmt`, a C11
compiler, a C++20 compiler, and Python 3.10 or later. The C/C++ program needs
only the header `../../cpp/include/xtce/packet.h`; it links with no library.
