# simple-string: an 8-byte string described in XTCE, serialized and
# deserialized from Rust, C, C++ and Python.
#
#   make            generate the code, build and run every program and check
#                   that they print the same thing
#   make generate   rerun tcs-xtce on simple_string.xml
#   make clean      remove build products (keeps the generated code)

TOP     := ../..
PYTHON  ?= python3
CARGO   ?= cargo
CC      ?= gcc
CXX     ?= g++
BUILD   := build

XML     := simple_string.xml
GEN     := rust/src/simple_string.rs c/simple_string.h python/simple_string.py
GEN_SRC := $(wildcard $(TOP)/generator/xtcepkt/*.py $(TOP)/generator/tcs_xtce/*.py)
PKT_H   := $(TOP)/cpp/include/xtce/packet.h
TCS_XTCE := cd $(TOP)/generator && $(PYTHON) -m tcs_xtce ../examples/simple-string/$(XML) \
           --source-name examples/simple-string/$(XML)

.PHONY: all generate rust c python run clean

all: run

generate: $(GEN)

# tcs-xtce reads the XTCE file and writes one language per run.  The C header
# serves both C and C++ (--lang c++ writes the same file).
$(GEN) &: $(XML) $(GEN_SRC)
	$(TCS_XTCE) ../examples/simple-string/rust/src/simple_string.rs --lang rust
	$(TCS_XTCE) ../examples/simple-string/c/simple_string.h --lang c
	$(TCS_XTCE) ../examples/simple-string/python/simple_string.py --lang python
	@touch $(GEN)

rust: $(GEN)
	cd rust && $(CARGO) fmt --check && $(CARGO) build --release --quiet

# The same source is built as C and as C++; the header needs no library.
$(BUILD)/simple_string_c: c/main.c c/simple_string.h $(PKT_H) | $(BUILD)
	$(CC) -std=c11 -O2 -Wall -Wextra -I$(TOP)/cpp/include -Ic $< -lm -o $@

$(BUILD)/simple_string_cxx: c/main.c c/simple_string.h $(PKT_H) | $(BUILD)
	$(CXX) -std=c++20 -O2 -Wall -Wextra -I$(TOP)/cpp/include -Ic -x c++ $< -o $@

c: $(BUILD)/simple_string_c $(BUILD)/simple_string_cxx

python: $(GEN)

run: rust c python | $(BUILD)
	rust/target/release/simple-string >$(BUILD)/rust.txt
	$(BUILD)/simple_string_c >$(BUILD)/c.txt
	$(BUILD)/simple_string_cxx >$(BUILD)/cxx.txt
	PYTHONPATH=$(TOP)/python:python $(PYTHON) python/main.py >$(BUILD)/python.txt
	cat $(BUILD)/rust.txt
	cmp $(BUILD)/rust.txt $(BUILD)/c.txt
	cmp $(BUILD)/rust.txt $(BUILD)/cxx.txt
	cmp $(BUILD)/rust.txt $(BUILD)/python.txt
	@echo "simple-string: Rust, C, C++ and Python output identical"

$(BUILD):
	mkdir -p $@

clean:
	rm -rf $(BUILD) rust/target
