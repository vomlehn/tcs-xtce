# Top-level build for the XTCE serialization libraries.
#
#   make                 fetch references (if missing), generate, build, test
#   make references      pull the latest XTCE documents into references/
#   make generate        regenerate the Rust, C/C++ and Python bindings
#   make samples         regenerate the schema-covering test documents
#   make packets         regenerate the demo mission's packet codecs
#   make rust|cpp|python build one library
#   make test            run every language's tests and the cross-language checks
#   make samples-check   check tcs-xtce against public XTCE documents (downloads them)
#   make examples        build and run every example in examples/
#   make fmt             format Rust code;  make fmt-check  verify it
#   make clean           remove build products (keeps generated sources)
#   make distclean       also remove downloaded references

PYTHON  ?= python3
CARGO   ?= cargo
CURL    ?= curl -fsSL --retry 3 --retry-delay 2

XSD     := references/SpaceSystem.xsd
GEN     := $(PYTHON) -m xtcegen --xsd ../$(XSD)
GEN_SRC := $(wildcard generator/xtcegen/*.py)

RUST_GEN := rust/src/generated.rs
CPP_GEN  := cpp/include/xtce/generated.hpp cpp/src/generated.cpp
PY_GEN   := python/xtce/generated.py
SAMPLES  := tests/samples/COVERAGE.txt

RUST_TOOL := rust/target/release/examples/xtce_roundtrip
CPP_TOOL  := cpp/build/xtce_roundtrip

# Packet codecs generated from a mission's XTCE document by tcs-xtce; the demo
# mission exercises them in every language.
MISSION  := tests/mission/demo.xml
PKT      := $(PYTHON) -m tcs_xtce ../$(MISSION) --source-name $(MISSION)
PKT_SRC  := $(wildcard generator/xtcepkt/*.py generator/tcs_xtce/*.py)
PKT_RUST := rust/tests/mission/demo.rs rust/tests/mission/demo_fixed.rs
PKT_CPP  := cpp/tests/mission/demo.h
PKT_PY   := python/tests/mission/demo.py
PKT_GEN  := $(PKT_RUST) $(PKT_CPP) $(PKT_PY)
RUST_PKT_TOOL := rust/target/release/examples/packet_tool
RUST_FIXED_PKT_TOOL := rust/target/release/examples/packet_tool_fixed
C_PKT_TOOL    := cpp/build/packet_tool
CXX_PKT_TOOL  := cpp/build/packet_tool_cxx

.PHONY: all references generate samples packets rust cpp python test test-generator test-rust \
        test-cpp test-python cross-check packet-check samples-check examples fmt fmt-check clean \
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

# tcs-xtce reads the mission with the generated Python binding and writes one
# language per run.
$(PKT_GEN) &: $(MISSION) $(PKT_SRC) $(PY_GEN)
	cd generator && $(PKT) ../rust/tests/mission/demo.rs --lang rust
	cd generator && $(PKT) ../rust/tests/mission/demo_fixed.rs --lang rust --dynamic-allocations false
	cd generator && $(PKT) ../$(PKT_CPP) --lang c
	cd generator && $(PKT) ../$(PKT_PY) --lang python
	@touch $(PKT_GEN)

packets: $(PKT_GEN)

# Rust formatting is checked on every build so unformatted code is caught as
# soon as it is added.
rust: $(RUST_GEN) $(PKT_RUST) fmt-check
	cd rust && $(CARGO) build --release --all-targets

cpp: $(CPP_GEN) $(PKT_CPP)
	$(MAKE) -C cpp

python: $(PY_GEN) $(PKT_PY)
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
	sh tests/packet_check.sh $(RUST_PKT_TOOL) $(RUST_FIXED_PKT_TOOL) $(C_PKT_TOOL) $(CXX_PKT_TOOL)

# Public XTCE documents, pinned to commits in tests/external/sources.txt and
# downloaded on demand; not part of `make test`, which needs no network.
EXTERNAL := tests/external/files

samples-check: rust $(PY_GEN)
	@mkdir -p $(EXTERNAL)
	@grep -v '^#' tests/external/sources.txt | while read -r name sha url; do \
	    [ -n "$$name" ] || continue; \
	    [ -f $(EXTERNAL)/$$name ] && continue; \
	    mkdir -p $$(dirname $(EXTERNAL)/$$name); \
	    echo "fetch $$name"; \
	    $(CURL) -o $(EXTERNAL)/$$name.tmp "$$url" && mv $(EXTERNAL)/$$name.tmp $(EXTERNAL)/$$name || exit 1; \
	done
	$(PYTHON) -I tests/samples_check.py

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

# The examples' Rust code includes their generated codecs, so generate them
# first.
fmt:
	cd rust && $(CARGO) fmt
	for d in $(EXAMPLES); do $(MAKE) -C $$d generate && (cd $$d/rust && $(CARGO) fmt) || exit 1; done

fmt-check:
	cd rust && $(CARGO) fmt --check
	for d in $(EXAMPLES); do $(MAKE) -C $$d generate && (cd $$d/rust && $(CARGO) fmt --check) || exit 1; done

# The documents as HTML: docs/ and the examples' README. Committed beside
# their sources and served by GitHub Pages, so remade before a commit that
# changes one, and left alone by clean.
.PHONY: html
html:
	$(MAKE) -C docs html
	$(MAKE) -C examples html

clean:
	cd rust && $(CARGO) clean
	$(MAKE) -C cpp clean
	for d in $(EXAMPLES); do $(MAKE) -C $$d clean; done
	$(MAKE) -C references clean
	find python generator -name __pycache__ -type d -prune -exec rm -rf {} +

distclean: clean
	$(MAKE) -C references distclean
