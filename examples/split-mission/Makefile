# split-mission: A rover's PUS telemetry, described in three XTCE documents
# (data types, packet headers and the rover's packets), recorded in one
# stream after the digest of the set, and decoded again from Rust, C, C++
# and Python.
#
#   make            generate the code, build and run every program and check
#                   that they print the same thing
#   make generate   rerun tcs-xtce on the three documents
#   make html       convert README.rst to README.html
#   make clean      remove build products (keeps the generated code and
#                   README.html)

TOP     := ../..
PYTHON  ?= python3
CARGO   ?= cargo
CC      ?= gcc
CXX     ?= g++
BUILD   := build
RST2HTML ?= rst2html

XML     := dt.xml pus.xml rover.xml
GEN     := rust/src/rover.rs c/rover.h cxx/rover.h \
           python/rover.py
GEN_SRC := $(wildcard $(TOP)/python/tcs_xtce/compiler/*.py \
                     $(TOP)/python/tcs_xtce/__main__.py \
                     $(TOP)/python/tcs_xtce/binding/*.py)
PKT_H   := $(TOP)/cpp/include/xtce/packet.h \
           $(TOP)/cpp/include/xtce/digest.h
# The documents that others build on come first: the mission, and so the
# generated files and the C prefix, are named for the last, Rover.
TCS_XTCE := cd $(TOP)/python && \
            $(PYTHON) -m tcs_xtce $(XML:%=../examples/split-mission/%)

.PHONY: all
all: run

.PHONY: generate
generate: $(GEN)

# tcs-xtce reads the XTCE files and writes one language per run.
$(GEN) &: $(XML) $(GEN_SRC)
	@mkdir -p cxx
	$(TCS_XTCE) ../examples/split-mission/rust/src/rover.rs \
		--lang rust
	$(TCS_XTCE) ../examples/split-mission/c/rover.h --lang c
	$(TCS_XTCE) ../examples/split-mission/cxx/rover.h --lang c++
	$(TCS_XTCE) ../examples/split-mission/python/rover.py \
		--lang python
	@touch $(GEN)

.PHONY: rust
rust: $(GEN)
	cd rust && $(CARGO) fmt --check && $(CARGO) build --release --quiet

# The same source is built as C, against c/rover.h, and as C++, against
# cxx/rover.h.  main.c includes <rover.h> so that -Ic or -Icxx
# picks the header.  Neither needs a library.
$(BUILD)/split_mission_c: c/main.c c/rover.h $(PKT_H) | $(BUILD)
	$(CC) -std=c11 -O2 -Wall -Wextra -I$(TOP)/cpp/include -Ic $< -lm -o $@

$(BUILD)/split_mission_cxx: c/main.c cxx/rover.h $(PKT_H) | $(BUILD)
	$(CXX) -std=c++20 -O2 -Wall -Wextra -I$(TOP)/cpp/include -Icxx -x c++ \
		$< -o $@

.PHONY: c
c: $(BUILD)/split_mission_c $(BUILD)/split_mission_cxx

.PHONY: python
python: $(GEN)

.PHONY: run
run: rust c python | $(BUILD)
	rust/target/release/split-mission >$(BUILD)/rust.txt
	$(BUILD)/split_mission_c >$(BUILD)/c.txt
	$(BUILD)/split_mission_cxx >$(BUILD)/cxx.txt
	PYTHONPATH=$(TOP)/python:python $(PYTHON) python/main.py \
		>$(BUILD)/python.txt
	cat $(BUILD)/rust.txt
	cmp $(BUILD)/rust.txt $(BUILD)/c.txt
	cmp $(BUILD)/rust.txt $(BUILD)/cxx.txt
	cmp $(BUILD)/rust.txt $(BUILD)/python.txt
	@echo "split-mission: Rust, C, C++ and Python output identical"

$(BUILD):
	mkdir -p $@

# README.html is committed beside README.rst, like the other documents.
# --halt=warning turns docutils warnings into errors, so a malformed README
# fails the build instead of producing a damaged page.
.PHONY: html
html: README.html

README.html: README.rst
	$(RST2HTML) --halt=warning $< $@

.PHONY: clean
clean:
	rm -rf $(BUILD) rust/target
