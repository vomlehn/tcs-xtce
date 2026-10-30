# variable-array: a paragraph described in XTCE as a two-dimensional array
# whose dimensions both vary, sent over TCP from one thread to another in Rust,
# C, C++ and Python.
#
#   make            generate the code, build and run every program and check
#                   that they print the same thing
#   make generate   rerun tcs-xtce on variable_array.xml
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

XML     := variable_array.xml
GEN     := rust/src/variable_array.rs c/variable_array.h cxx/variable_array.h \
           python/variable_array.py
GEN_SRC := $(wildcard $(TOP)/python/tcs_xtce/compiler/*.py \
                     $(TOP)/python/tcs_xtce/__main__.py \
                     $(TOP)/python/tcs_xtce/binding/*.py)
PKT_H   := $(TOP)/cpp/include/xtce-packet.h \
           $(TOP)/cpp/include/xtce-digest.h
TCS_XTCE := cd $(TOP)/python && \
            $(PYTHON) -m tcs_xtce ../examples/variable-array/$(XML) \
           --source-name examples/variable-array/$(XML)

.PHONY: all
all: run

.PHONY: generate
generate: $(GEN)

# tcs-xtce reads the XTCE file and writes one language per run.
$(GEN) &: $(XML) $(GEN_SRC)
	@mkdir -p cxx
	$(TCS_XTCE) ../examples/variable-array/rust/src/variable_array.rs \
		--lang rust
	$(TCS_XTCE) ../examples/variable-array/c/variable_array.h --lang c
	$(TCS_XTCE) ../examples/variable-array/cxx/variable_array.h --lang c++
	$(TCS_XTCE) ../examples/variable-array/python/variable_array.py \
		--lang python
	@touch $(GEN)

.PHONY: rust
rust: $(GEN)
	cd rust && $(CARGO) fmt --check && $(CARGO) build --release --quiet

# The same source is built as C, against c/variable_array.h, and as C++, against
# cxx/variable_array.h.  main.c includes <variable_array.h> so that -Ic or -Icxx
# picks the header.  Neither needs a library beyond POSIX threads and sockets.
$(BUILD)/variable_array_c: c/main.c c/variable_array.h $(PKT_H) | $(BUILD)
	$(CC) -std=c11 -O2 -Wall -Wextra -I$(TOP)/cpp/include -Ic \
		$< -pthread -lm -o $@

$(BUILD)/variable_array_cxx: c/main.c cxx/variable_array.h $(PKT_H) | $(BUILD)
	$(CXX) -std=c++20 -O2 -Wall -Wextra -I$(TOP)/cpp/include -Icxx -x c++ \
		$< -pthread -o $@

.PHONY: c
c: $(BUILD)/variable_array_c $(BUILD)/variable_array_cxx

.PHONY: python
python: $(GEN)

.PHONY: run
run: rust c python | $(BUILD)
	rust/target/release/variable-array >$(BUILD)/rust.txt
	$(BUILD)/variable_array_c >$(BUILD)/c.txt
	$(BUILD)/variable_array_cxx >$(BUILD)/cxx.txt
	PYTHONPATH=$(TOP)/python:python $(PYTHON) python/main.py \
		>$(BUILD)/python.txt
	cat $(BUILD)/rust.txt
	cmp $(BUILD)/rust.txt $(BUILD)/c.txt
	cmp $(BUILD)/rust.txt $(BUILD)/cxx.txt
	cmp $(BUILD)/rust.txt $(BUILD)/python.txt
	@echo "variable-array: Rust, C, C++ and Python output identical"

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
