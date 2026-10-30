# simple-string: an 8-byte string described in XTCE, serialized and
# deserialized from Rust, C, C++ and Python.
#
#   make            generate the code, build and run every program and check
#                   that they print the same thing
#   make generate   rerun tcs-xtce on simple_string.xml
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

XML     := simple_string.xml
GEN     := rust/src/simple_string.rs c/simple_string.h cxx/simple_string.h \
           python/simple_string.py
GEN_SRC := $(wildcard $(TOP)/python/tcs_xtce/compiler/*.py \
                     $(TOP)/python/tcs_xtce/__main__.py \
                     $(TOP)/python/tcs_xtce/binding/*.py)
PKT_H   := $(TOP)/cpp/include/xtce-packet.h
TCS_XTCE := cd $(TOP)/python && \
            $(PYTHON) -m tcs_xtce ../examples/simple-string/$(XML) \
           --source-name examples/simple-string/$(XML)

.PHONY: all
all: run

.PHONY: generate
generate: $(GEN)

# tcs-xtce reads the XTCE file and writes one language per run.
$(GEN) &: $(XML) $(GEN_SRC)
	@mkdir -p cxx
	$(TCS_XTCE) ../examples/simple-string/rust/src/simple_string.rs \
		--lang rust
	$(TCS_XTCE) ../examples/simple-string/c/simple_string.h --lang c
	$(TCS_XTCE) ../examples/simple-string/cxx/simple_string.h --lang c++
	$(TCS_XTCE) ../examples/simple-string/python/simple_string.py \
		--lang python
	@touch $(GEN)

.PHONY: rust
rust: $(GEN)
	cd rust && $(CARGO) fmt --check && $(CARGO) build --release --quiet

# The same source is built as C, against c/simple_string.h, and as C++, against
# cxx/simple_string.h.  main.c includes <simple_string.h> so that -Ic or -Icxx
# picks the header.  Neither needs a library.
$(BUILD)/simple_string_c: c/main.c c/simple_string.h $(PKT_H) | $(BUILD)
	$(CC) -std=c11 -O2 -Wall -Wextra -I$(TOP)/cpp/include -Ic $< -lm -o $@

$(BUILD)/simple_string_cxx: c/main.c cxx/simple_string.h $(PKT_H) | $(BUILD)
	$(CXX) -std=c++20 -O2 -Wall -Wextra -I$(TOP)/cpp/include -Icxx -x c++ \
		$< -o $@

.PHONY: c
c: $(BUILD)/simple_string_c $(BUILD)/simple_string_cxx

.PHONY: python
python: $(GEN)

.PHONY: run
run: rust c python | $(BUILD)
	rust/target/release/simple-string >$(BUILD)/rust.txt
	$(BUILD)/simple_string_c >$(BUILD)/c.txt
	$(BUILD)/simple_string_cxx >$(BUILD)/cxx.txt
	PYTHONPATH=$(TOP)/python:python $(PYTHON) python/main.py \
		>$(BUILD)/python.txt
	cat $(BUILD)/rust.txt
	cmp $(BUILD)/rust.txt $(BUILD)/c.txt
	cmp $(BUILD)/rust.txt $(BUILD)/cxx.txt
	cmp $(BUILD)/rust.txt $(BUILD)/python.txt
	@echo "simple-string: Rust, C, C++ and Python output identical"

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
