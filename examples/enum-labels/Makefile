# enum-labels: A probe's status, whose mode is an enumeration that the include
# conditions, a derived packet and an alarm compare by label, encoded and
# decoded again from Rust, C, C++ and Python.
#
#   make            generate the code, build and run every program and check
#                   that they print the same thing
#   make generate   rerun tcs-xtce on modes.xml
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

XML     := modes.xml
GEN     := rust/src/probe.rs c/probe.h cxx/probe.h \
           python/probe.py
GEN_SRC := $(wildcard $(TOP)/python/tcs_xtce/compiler/*.py \
                     $(TOP)/python/tcs_xtce/__main__.py \
                     $(TOP)/python/tcs_xtce/binding/*.py)
PKT_H   := $(TOP)/cpp/include/xtce-packet.h \
           $(TOP)/cpp/include/xtce-digest.h
TCS_XTCE := cd $(TOP)/python && \
            $(PYTHON) -m tcs_xtce ../examples/enum-labels/$(XML) \
           --source-name examples/enum-labels/$(XML)

.PHONY: all
all: run

.PHONY: generate
generate: $(GEN)

# tcs-xtce reads the XTCE file and writes one language per run.
$(GEN) &: $(XML) $(GEN_SRC)
	@mkdir -p cxx
	$(TCS_XTCE) ../examples/enum-labels/rust/src/probe.rs \
		--lang rust
	$(TCS_XTCE) ../examples/enum-labels/c/probe.h --lang c
	$(TCS_XTCE) ../examples/enum-labels/cxx/probe.h --lang c++
	$(TCS_XTCE) ../examples/enum-labels/python/probe.py \
		--lang python
	@touch $(GEN)

.PHONY: rust
rust: $(GEN)
	cd rust && $(CARGO) fmt --check && $(CARGO) build --release --quiet

# The same source is built as C, against c/probe.h, and as C++, against
# cxx/probe.h.  main.c includes <probe.h> so that -Ic or -Icxx
# picks the header.  Neither needs a library.
$(BUILD)/enum_labels_c: c/main.c c/probe.h $(PKT_H) | $(BUILD)
	$(CC) -std=c11 -O2 -Wall -Wextra -I$(TOP)/cpp/include -Ic $< -lm -o $@

$(BUILD)/enum_labels_cxx: c/main.c cxx/probe.h $(PKT_H) | $(BUILD)
	$(CXX) -std=c++20 -O2 -Wall -Wextra -I$(TOP)/cpp/include -Icxx -x c++ \
		$< -o $@

.PHONY: c
c: $(BUILD)/enum_labels_c $(BUILD)/enum_labels_cxx

.PHONY: python
python: $(GEN)

.PHONY: run
run: rust c python | $(BUILD)
	rust/target/release/enum-labels >$(BUILD)/rust.txt
	$(BUILD)/enum_labels_c >$(BUILD)/c.txt
	$(BUILD)/enum_labels_cxx >$(BUILD)/cxx.txt
	PYTHONPATH=$(TOP)/python:python $(PYTHON) python/main.py \
		>$(BUILD)/python.txt
	cat $(BUILD)/rust.txt
	cmp $(BUILD)/rust.txt $(BUILD)/c.txt
	cmp $(BUILD)/rust.txt $(BUILD)/cxx.txt
	cmp $(BUILD)/rust.txt $(BUILD)/python.txt
	@echo "enum-labels: Rust, C, C++ and Python output identical"

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
