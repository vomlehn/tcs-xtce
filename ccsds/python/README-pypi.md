# tcsccsds: CCSDS time codes

CCSDS standards for flight software, the same in Python as in Rust, C and
C++. So far: the time codes of CCSDS 301.0-B-4, *Time Code Formats*, and
TAI and UTC with their leap seconds. It is operational code, run aboard
and in the mission operations center, and it needs nothing but the
standard library.

> **Warning:** tcsccsds is an alpha release. It is not fully tested, it
> is not feature complete, and it is subject to rapid change. Feedback is
> welcome: open an issue at https://github.com/vomlehn/tcs-xtce/issues.

## What it does

* **CUC**, the CCSDS Unsegmented time Code, and **CDS**, the CCSDS Day
  Segmented time code, each with its P-field, from the CCSDS epoch or one
  the mission defines.
* **CCS**, the CCSDS Calendar Segmented time code, and **ASCII codes A
  and B**, which give a date and a time of day.
* Conversion between TAI and UTC, with the IERS's table of leap seconds
  built in, or the mission's own. Times before 1972 or past the table's
  expiry are refused rather than guessed at.

A binary time comes as seconds since the epoch in a float, or exactly, in
integers, down to 2^-80 of a second or a picosecond. Python, Rust, C and
C++ give the same octets and the same seconds, bit for bit.

## Installing

```sh
pip install tcsccsds
```

It needs Python 3.10 or later.

## Using it

```python
from tcsccsds.time import Cds, CdsTime, Epoch, SubMs, decode_cds_exact

layout = Cds(True, SubMs.PICO, Epoch.AGENCY)
octets = layout.encode_exact(CdsTime(0x123456, 86399999, 999999999))
assert decode_cds_exact(octets) == (12, CdsTime(0x123456, 86399999,
                                                999999999))
```

## Documentation

* [The CCSDS time codes chapter](https://vomlehn.github.io/tcs-xtce/docs/tcs-xtce.html#ccsds-time-codes-tcsccsds)
  of the tcs-xtce manual, which describes the whole API in each language.
* [Its README](https://github.com/vomlehn/tcs-xtce/blob/main/ccsds/README.md)
  in the tcs-xtce repository.
* The same in Rust is the [tcsccsds crate](https://crates.io/crates/tcsccsds).

## License

MIT or Apache-2.0, at your option.
