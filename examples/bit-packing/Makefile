# bit-packing: a record of five small fields, generated with --bit-packing
# and serialized and deserialized from Rust, C, C++ and Python.
#
#   make            generate the code, build and run every program and check
#                   that they print the same thing
#   make generate   rerun tcsxtce on bit_packing.xml
#   make clean      remove build products (keeps the generated code)

TOP     := ../..
PYTHON  ?= python3
CARGO   ?= cargo
CC      ?= gcc
CXX     ?= g++
BUILD   := build

XML     := bit_packing.xml
GEN     := rust/src/bit_packing.rs c/bit_packing.h python/bit_packing.py
GEN_SRC := $(wildcard $(TOP)/generator/xtcepkt/*.py $(TOP)/generator/tcsxtce/*.py)
PKT_H   := $(TOP)/cpp/include/xtce/packet.h
TCSXTCE := cd $(TOP)/generator && $(PYTHON) -m tcsxtce ../examples/bit-packing/$(XML) \
           --source-name examples/bit-packing/$(XML) --bit-packing

.PHONY: all generate rust c python run clean

all: run

generate: $(GEN)

# tcsxtce reads the XTCE file and writes one language per run, packing the
# fields (--bit-packing, in TCSXTCE).  The C header serves both C and C++
# (--lang c++ writes the same file).
$(GEN) &: $(XML) $(GEN_SRC)
	$(TCSXTCE) ../examples/bit-packing/rust/src/bit_packing.rs --lang rust
	$(TCSXTCE) ../examples/bit-packing/c/bit_packing.h --lang c
	$(TCSXTCE) ../examples/bit-packing/python/bit_packing.py --lang python
	@touch $(GEN)

rust: $(GEN)
	cd rust && $(CARGO) fmt --check && $(CARGO) build --release --quiet

# The same source is built as C and as C++; the header needs no library.
$(BUILD)/bit_packing_c: c/main.c c/bit_packing.h $(PKT_H) | $(BUILD)
	$(CC) -std=c11 -O2 -Wall -Wextra -I$(TOP)/cpp/include -Ic $< -lm -o $@

$(BUILD)/bit_packing_cxx: c/main.c c/bit_packing.h $(PKT_H) | $(BUILD)
	$(CXX) -std=c++20 -O2 -Wall -Wextra -I$(TOP)/cpp/include -Ic -x c++ $< -o $@

c: $(BUILD)/bit_packing_c $(BUILD)/bit_packing_cxx

python: $(GEN)

run: rust c python | $(BUILD)
	rust/target/release/bit-packing >$(BUILD)/rust.txt
	$(BUILD)/bit_packing_c >$(BUILD)/c.txt
	$(BUILD)/bit_packing_cxx >$(BUILD)/cxx.txt
	PYTHONPATH=$(TOP)/python:python $(PYTHON) python/main.py >$(BUILD)/python.txt
	cat $(BUILD)/rust.txt
	cmp $(BUILD)/rust.txt $(BUILD)/c.txt
	cmp $(BUILD)/rust.txt $(BUILD)/cxx.txt
	cmp $(BUILD)/rust.txt $(BUILD)/python.txt
	@echo "bit-packing: Rust, C, C++ and Python output identical"

$(BUILD):
	mkdir -p $@

clean:
	rm -rf $(BUILD) rust/target
