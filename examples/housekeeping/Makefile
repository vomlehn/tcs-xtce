# housekeeping: Status packets described in XTCE, with a packet length field,
# calibrated values and a field present only when the heater is on, encoded
# into one stream and decoded again from Rust, C, C++ and Python.
#
#   make            generate the code, build and run every program and check
#                   that they print the same thing
#   make generate   rerun tcs-xtce on housekeeping.xml
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

XML     := housekeeping.xml
GEN     := rust/src/housekeeping.rs c/housekeeping.h cxx/housekeeping.h \
           python/housekeeping.py
GEN_SRC := $(wildcard $(TOP)/generator/xtcepkt/*.py \
                     $(TOP)/generator/tcs_xtce/*.py \
                     $(TOP)/generator/xtcegen/*.py)
PKT_H   := $(TOP)/cpp/include/xtce/packet.h
TCS_XTCE := cd $(TOP)/generator && \
            $(PYTHON) -m tcs_xtce ../examples/housekeeping/$(XML) \
           --source-name examples/housekeeping/$(XML)

.PHONY: all
all: run

.PHONY: generate
generate: $(GEN)

# tcs-xtce reads the XTCE file and writes one language per run.
$(GEN) &: $(XML) $(GEN_SRC)
	@mkdir -p cxx
	$(TCS_XTCE) ../examples/housekeeping/rust/src/housekeeping.rs \
		--lang rust
	$(TCS_XTCE) ../examples/housekeeping/c/housekeeping.h --lang c
	$(TCS_XTCE) ../examples/housekeeping/cxx/housekeeping.h --lang c++
	$(TCS_XTCE) ../examples/housekeeping/python/housekeeping.py \
		--lang python
	@touch $(GEN)

.PHONY: rust
rust: $(GEN)
	cd rust && $(CARGO) fmt --check && $(CARGO) build --release --quiet

# The same source is built as C, against c/housekeeping.h, and as C++, against
# cxx/housekeeping.h.  main.c includes <housekeeping.h> so that -Ic or -Icxx
# picks the header.  Neither needs a library.
$(BUILD)/housekeeping_c: c/main.c c/housekeeping.h $(PKT_H) | $(BUILD)
	$(CC) -std=c11 -O2 -Wall -Wextra -I$(TOP)/cpp/include -Ic $< -lm -o $@

$(BUILD)/housekeeping_cxx: c/main.c cxx/housekeeping.h $(PKT_H) | $(BUILD)
	$(CXX) -std=c++20 -O2 -Wall -Wextra -I$(TOP)/cpp/include -Icxx -x c++ \
		$< -o $@

.PHONY: c
c: $(BUILD)/housekeeping_c $(BUILD)/housekeeping_cxx

.PHONY: python
python: $(GEN)

.PHONY: run
run: rust c python | $(BUILD)
	rust/target/release/housekeeping >$(BUILD)/rust.txt
	$(BUILD)/housekeeping_c >$(BUILD)/c.txt
	$(BUILD)/housekeeping_cxx >$(BUILD)/cxx.txt
	PYTHONPATH=$(TOP)/python:python $(PYTHON) python/main.py \
		>$(BUILD)/python.txt
	cat $(BUILD)/rust.txt
	cmp $(BUILD)/rust.txt $(BUILD)/c.txt
	cmp $(BUILD)/rust.txt $(BUILD)/cxx.txt
	cmp $(BUILD)/rust.txt $(BUILD)/python.txt
	@echo "housekeeping: Rust, C, C++ and Python output identical"

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
