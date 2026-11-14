# tcsccsds

CCSDS standards for flight software, the same in Rust, C, C++ and Python. So
far: the time codes of CCSDS 301.0-B-4, *Time Code Formats*, and TAI and UTC
with their leap seconds.

> **Warning:** tcsccsds is an alpha release. It is not fully tested, it
> is not feature complete, and it is subject to rapid change. Until 1.0,
> any change to its API changes the minor version number (0.1 to 0.2), so
> a release that changes only the patch number never breaks code built
> against it. Feedback is welcome, even at this stage: open an issue at
> https://github.com/vomlehn/tcs-xtce/issues.

## What it does

It reads and writes the two binary time codes most missions use:

* **CUC**, the CCSDS Unsegmented time Code: 1 to 7 octets of whole seconds
  and 0 to 10 octets of binary fractions of a second.
* **CDS**, the CCSDS Day Segmented time code: days in 16 or 24 bits,
  milliseconds of the day, and optionally microseconds or picoseconds of
  the millisecond. Milliseconds may reach a leap second's.

Each reads and writes its P-field, the preamble that says which layout
follows, and counts from the CCSDS epoch (1958-01-01, TAI) or one the
mission defines.

A time comes in two forms:

* **Seconds since the epoch, as a double.** Convenient, but a double holds
  only about a quarter of a microsecond at today's times.
* **Exact, in integers** (`CucTime` and `CdsTime`): every time a layout
  can hold, down to 2^-80 of a second or a picosecond.

Encoding rounds to the layout's unit, halves up, carrying into the seconds
or the day.

It also reads and writes the calendar time codes of the same standard,
which give a date and a time of day, usually UTC, held exactly as a
`CalendarTime`:

* **CCS**, the CCSDS Calendar Segmented time code, in BCD, with a month
  and day or a day of the year and down to a picosecond.
* **ASCII codes A and B**, `YYYY-MM-DDThh:mm:ss.d...dZ` and
  `YYYY-DDDThh:mm:ss.d...dZ`.

And it converts between TAI, which the binary codes count, and UTC, which
the calendar codes give, with a table of leap seconds: the IERS's, built in,
or the mission's own. Times before 1972 or past the table's expiry are
refused rather than guessed at.

## Why use it

* **The same answers everywhere.** Rust, C, C++ and Python give the same
  octets and the same seconds, bit for bit, so ground software in Python
  and flight software in C never disagree about a time. A cross-language
  check holds them to it on hand-worked and random cases.
* **Fit for flight.** The Rust crate is `no_std`, allocates nothing and
  has no dependencies; the C and C++ library is one header.
* **Ready for tcs-xtce.** XTCE cannot describe these time codes itself, so
  a mission database can only say a time is made by an algorithm. The
  decoders and encoders have the shapes of
  [tcs-xtce](https://github.com/vomlehn/tcs-xtce)'s time hooks, so a
  packet codec it generates can use them directly. tcsccsds does not
  depend on tcs-xtce, and any program can use it alone.

## Getting it

| Language | How | Where in this repository |
|----------|-----|--------------------------|
| Rust | `cargo add tcsccsds` | `ccsds/rust/` |
| C and C++ | `#include "tcsccsds-time.h"`; header only | `ccsds/include/` |
| Python | the `tcsccsds` package | `ccsds/python/` |

tcs-xtce's packages and `make install` install the header (with
`tcsccsds.pc` for `pkg-config`) and the Python package. The Rust crate
comes from [crates.io](https://crates.io/crates/tcsccsds).

## Examples

Decoding a CUC time that starts with its P-field, in Rust:

```rust
use tcsccsds::time::{decode_cuc, decode_cuc_exact};

// P-field 0x1e: the CCSDS epoch, four coarse and two fine octets.
let data = [0x1e, 0x00, 0x00, 0x00, 0x01, 0x80, 0x00];
assert_eq!(decode_cuc(&data), Some((7, 1.5)));

// The same time exactly: one second, and a fraction of 0x80 in its
// first octet.
let (_, t) = decode_cuc_exact(&data).unwrap();
assert_eq!((t.seconds, t.fraction >> 72), (1, 0x80));
```

Encoding a CDS time to the picosecond, in C:

```c
#include "tcsccsds-time.h"

ccsds_cds layout = {true, CCSDS_SUB_PICO, CCSDS_EPOCH_AGENCY};
ccsds_cds_time t = {0x123456, 86399999, 999999999};
uint8_t out[12];
size_t n = ccsds_cds_encode_exact(&layout, &t, out, sizeof out);
/* n is 12: the P-field 0x4e, then the day, milliseconds and picoseconds. */
```

And in Python:

```python
from tcsccsds.time import Cds, CdsTime, Epoch, SubMs, decode_cds_exact

layout = Cds(True, SubMs.PICO, Epoch.AGENCY)
octets = layout.encode_exact(CdsTime(0x123456, 86399999, 999999999))
assert decode_cds_exact(octets) == (12, CdsTime(0x123456, 86399999,
                                                999999999))
```

## Documentation and tests

The **CCSDS time codes** chapter of the
[tcs-xtce manual](https://vomlehn.github.io/tcs-xtce/docs/tcs-xtce.html#ccsds-time-codes-tcsccsds)
describes the whole API in each language; the
[crate's documentation](https://docs.rs/tcsccsds) describes the Rust.

From the top of the tcs-xtce repository, `make ccsds` builds the Rust crate
and the C and C++ test tools, and `make ccsds-check` checks that Rust, C,
C++ and Python give the known results of `tests/ccsds/known.txt` and agree
on random cases.

## Not yet

* Leap seconds announced after the built-in table's expiry, 28 June 2027:
  give the conversions a newer table.

## License

MIT or Apache-2.0, at your option.
