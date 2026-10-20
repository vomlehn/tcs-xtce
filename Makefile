# Top-level build for the XTCE serialization libraries.
#
#   make                 fetch references (if missing), generate, build, test
#   make references      pull the latest XTCE documents into references/
#   make generate        regenerate the Rust, C/C++ and Python bindings
#   make samples         regenerate the schema-covering test documents
#   make packets         regenerate the demo mission's packet codecs
#   make rust|cpp|python build one library
#   make test            run every language's tests and the cross-language checks
#   make examples        build and run every example in examples/
#   make fmt             format Rust code;  make fmt-check  verify it
#   make clean           remove build products (keeps generated sources)
#   make distclean       also remove downloaded references

PYTHON  ?= python3
CARGO   ?= cargo

XSD     := references/SpaceSystem.xsd
GEN     := $(PYTHON) -m xtcegen --xsd ../$(XSD)
GEN_SRC := $(wildcard generator/xtcegen/*.py)

RUST_GEN := rust/src/generated.rs
CPP_GEN  := cpp/include/xtce/generated.hpp cpp/src/generated.cpp
PY_GEN   := python/xtce/generated.py
SAMPLES  := tests/samples/COVERAGE.txt

RUST_TOOL := rust/target/release/examples/xtce_roundtrip
CPP_TOOL  := cpp/build/xtce_roundtrip

# Packet codecs generated from a mission's XTCE document by tcsxtce; the demo
# mission exercises them in every language.
MISSION  := tests/mission/demo.xml
PKT      := $(PYTHON) -m tcsxtce ../$(MISSION) --source-name $(MISSION)
PKT_SRC  := $(wildcard generator/xtcepkt/*.py generator/tcsxtce/*.py)
PKT_RUST := rust/tests/mission/demo.rs
PKT_CPP  := cpp/tests/mission/demo.h
PKT_PY   := python/tests/mission/demo.py
# The same mission generated with --bit-packing.
PKT_RUST_PACKED := rust/tests/mission/demo_packed.rs
PKT_CPP_PACKED  := cpp/tests/mission/demo_packed.h
PKT_PY_PACKED   := python/tests/mission/demo_packed.py
PKT_GEN  := $(PKT_RUST) $(PKT_CPP) $(PKT_PY) $(PKT_RUST_PACKED) $(PKT_CPP_PACKED) $(PKT_PY_PACKED)
RUST_PKT_TOOL := rust/target/release/examples/packet_tool
C_PKT_TOOL    := cpp/build/packet_tool
CXX_PKT_TOOL  := cpp/build/packet_tool_cxx

.PHONY: all references generate samples packets rust cpp python test test-generator test-rust \
        test-cpp test-python cross-check packet-check examples fmt fmt-check clean \
        distclean

all: generate rust cpp python test

# Always re-checks OMG for the latest published version.
references:
	$(MAKE) -C references

# Fetch only if nothing has been downloaded yet.
$(XSD):
	$(MAKE) -C references

generate: $(RUST_GEN) $(CPP_GEN) $(PY_GEN) $(SAMPLES) $(PKT_GEN)

$(RUST_GEN): $(XSD) $(GEN_SRC)
	cd generator && $(GEN) --rust ../$@
	@touch $@

$(CPP_GEN) &: $(XSD) $(GEN_SRC)
	cd generator && $(GEN) --cpp-header ../cpp/include/xtce/generated.hpp --cpp-source ../cpp/src/generated.cpp
	@touch $(CPP_GEN)

$(PY_GEN): $(XSD) $(GEN_SRC)
	cd generator && $(GEN) --python ../$@
	@touch $@

$(SAMPLES): $(XSD) $(GEN_SRC)
	cd generator && $(GEN) --sample ../tests/samples
	@touch $@

samples: $(SAMPLES)

# tcsxtce reads the mission with the generated Python binding and writes one
# language per run.
$(PKT_GEN) &: $(MISSION) $(PKT_SRC) $(PY_GEN)
	cd generator && $(PKT) ../$(PKT_RUST) --lang rust
	cd generator && $(PKT) ../$(PKT_CPP) --lang c
	cd generator && $(PKT) ../$(PKT_PY) --lang python
	cd generator && $(PKT) ../$(PKT_RUST_PACKED) --lang rust --bit-packing
	cd generator && $(PKT) ../$(PKT_CPP_PACKED) --lang c --bit-packing --prefix demo_packed_
	cd generator && $(PKT) ../$(PKT_PY_PACKED) --lang python --bit-packing
	@touch $(PKT_GEN)

packets: $(PKT_GEN)

# Rust formatting is checked on every build so unformatted code is caught as
# soon as it is added.
rust: $(RUST_GEN) $(PKT_RUST) $(PKT_RUST_PACKED) fmt-check
	cd rust && $(CARGO) build --release --all-targets

cpp: $(CPP_GEN) $(PKT_CPP) $(PKT_CPP_PACKED)
	$(MAKE) -C cpp

python: $(PY_GEN) $(PKT_PY) $(PKT_PY_PACKED)
	$(PYTHON) -m compileall -q python/xtce

EXAMPLES := $(patsubst %/Makefile,%,$(wildcard examples/*/Makefile))

test: test-generator test-rust test-cpp test-python cross-check packet-check examples

# The packet compiler reads XTCE with the generated Python binding.
test-generator: $(PY_GEN)
	cd generator && $(PYTHON) -m unittest discover -s tests -t .

test-rust: rust $(SAMPLES)
	cd rust && $(CARGO) test --release

test-cpp: cpp $(SAMPLES)
	$(MAKE) -C cpp test

test-python: python $(SAMPLES)
	cd python && $(PYTHON) -m unittest discover -s tests -t .

cross-check: rust cpp python $(SAMPLES)
	sh tests/cross_check.sh $(RUST_TOOL) $(CPP_TOOL)

packet-check: rust cpp python
	sh tests/packet_check.sh $(RUST_PKT_TOOL) $(C_PKT_TOOL) $(CXX_PKT_TOOL)

# Each example regenerates its code if needed, builds and runs it in Rust,
# C, C++ and Python, and checks that the outputs match.
# Every example must exist in all supported languages: C/C++, Rust and
# Python 3.
examples: cpp python
	@for d in $(EXAMPLES); do \
	    for f in rust/Cargo.toml rust/src/main.rs c/main.c python/main.py; do \
	        [ -f $$d/$$f ] || { echo "$$d: missing $$f (examples need C/C++, Rust and Python versions)" >&2; exit 1; }; \
	    done; \
	done
	for d in $(EXAMPLES); do $(MAKE) -C $$d || exit 1; done

fmt:
	cd rust && $(CARGO) fmt
	for d in $(EXAMPLES); do (cd $$d/rust && $(CARGO) fmt) || exit 1; done

fmt-check:
	cd rust && $(CARGO) fmt --check
	for d in $(EXAMPLES); do (cd $$d/rust && $(CARGO) fmt --check) || exit 1; done

clean:
	cd rust && $(CARGO) clean
	$(MAKE) -C cpp clean
	for d in $(EXAMPLES); do $(MAKE) -C $$d clean; done
	$(MAKE) -C examples clean
	$(MAKE) -C references clean
	find python generator -name __pycache__ -type d -prune -exec rm -rf {} +

distclean: clean
	$(MAKE) -C references distclean
