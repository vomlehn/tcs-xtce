# tcsxtce: the XTCE packet runtime

tcsxtce is what the Python code that
[tcs-xtce](https://pypi.org/project/tcs-xtce/) generates from a mission's
OMG **XTCE** (XML Telemetric and Command Exchange) definitions runs on:
the packing and unpacking of bit-packed packets, in managed, preallocated
buffers. It is operational code, run aboard and in the mission operations
center, and it needs nothing but the standard library.

It also computes the XTCE digest: an MD5 digest of what an XTCE document
means, unchanged by comments, whitespace, attribute order and the order of
unordered collections. Two programs that exchange packets compare digests
to check that they were built from the same definitions, rather than
describing the data in every message. The Rust crate and the C and C++
headers compute the same digest.

> **Warning:** tcsxtce is an alpha release. It is not fully tested, it is
> not feature complete, and it is subject to rapid change. Feedback is
> welcome: open an issue at https://github.com/vomlehn/tcs-xtce/issues.

## Installing

```sh
pip install tcsxtce
```

It needs Python 3.10 or later. A program that only runs generated code
needs this alone; `pip install tcs-xtce` installs the compiler, which
depends on it.

## Using it

Generated code imports it itself: a packet class's `decode` reads a
packet from bytes, and its `to_bytes` writes one.

```python
import mission  # written by: tcs-xtce mission.xtce mission.py --lang python

packet = mission.CCSDSPacketKind.decode(data)
```

The digest:

```python
from tcsxtce.digest import digest_file

print(digest_file("mission.xtce"))
```

## Documentation

* [The tcs-xtce manual](https://vomlehn.github.io/tcs-xtce/docs/tcs-xtce.html),
  which describes the generated code and the runtime in each language.
* [The repository](https://github.com/vomlehn/tcs-xtce).
* The same runtime in Rust is the
  [tcsxtce crate](https://crates.io/crates/tcsxtce).

## License

MIT or Apache-2.0, at your option.
