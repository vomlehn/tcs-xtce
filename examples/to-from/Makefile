# to-from: words of varying length described in XTCE, written by the program
# in one language and read by the program in another: Rust, C, C++ or Python.
#
#   make            generate the code, build every program, then run the
#                   reader in each language on what the writer in each
#                   language wrote, and check that they print the same thing
#   make build      generate the code and build every program, so that
#                   ./to-from TO FROM can run them
#   make generate   rerun tcs-xtce on to_from.xml
#   make html       convert README.rst to README.html
#   make clean      remove build products (keeps the generated code and
#                   README.html)

TOP     := ../..
PYTHON  ?= python3
CARGO   ?= cargo
CC      ?= gcc
CXX     ?= g++
BUILD   := build
LANGS   := c c++ rust python
RST2HTML ?= rst2html

XML     := to_from.xml
GEN     := rust/src/to_from.rs c/to_from.h cxx/to_from.h \
           python/to_from.py
GEN_SRC := $(wildcard $(TOP)/python/tcs_xtce/compiler/*.py \
                     $(TOP)/python/tcs_xtce/__main__.py \
                     $(TOP)/python/tcs_xtce/binding/*.py)
PKT_H   := $(TOP)/cpp/include/xtce/packet.h \
           $(TOP)/cpp/include/xtce/digest.h
TCS_XTCE := cd $(TOP)/python && \
            $(PYTHON) -m tcs_xtce ../examples/to-from/$(XML) \
           --source-name examples/to-from/$(XML)

.PHONY: all
all: run

.PHONY: generate
generate: $(GEN)

# tcs-xtce reads the XTCE file and writes one language per run.
$(GEN) &: $(XML) $(GEN_SRC)
	@mkdir -p cxx
	$(TCS_XTCE) ../examples/to-from/rust/src/to_from.rs --lang rust
	$(TCS_XTCE) ../examples/to-from/c/to_from.h --lang c
	$(TCS_XTCE) ../examples/to-from/cxx/to_from.h --lang c++
	$(TCS_XTCE) ../examples/to-from/python/to_from.py --lang python
	@touch $(GEN)

.PHONY: rust
rust: $(GEN)
	cd rust && $(CARGO) fmt --check && $(CARGO) build --release --quiet

# The same source is built as C, against c/to_from.h, and as C++, against
# cxx/to_from.h.  main.c includes <to_from.h> so that -Ic or -Icxx picks the
# header.  Neither needs a library.
#
# The build directory has no rule of its own, since "build" is the phony
# target below; the recipes that write into it create it.
$(BUILD)/to_from_c: c/main.c c/to_from.h $(PKT_H)
	@mkdir -p $(@D)
	$(CC) -std=c11 -O2 -Wall -Wextra -I$(TOP)/cpp/include -Ic $< -lm -o $@

$(BUILD)/to_from_cxx: c/main.c cxx/to_from.h $(PKT_H)
	@mkdir -p $(@D)
	$(CXX) -std=c++20 -O2 -Wall -Wextra -I$(TOP)/cpp/include -Icxx -x c++ \
		$< -o $@

.PHONY: c
c: $(BUILD)/to_from_c $(BUILD)/to_from_cxx

.PHONY: python
python: $(GEN)

.PHONY: build
build: rust c python

# Every writer must write the same bytes, and every reader must print the
# same words whichever writer it reads from.  ./to-from prints which pair it
# runs on its first line; the rest is the reader's output, compared with the
# first pair's (c reading from c).
.PHONY: run
run: build
	@mkdir -p $(BUILD)
	rust/target/release/to-from write >$(BUILD)/rust.bin
	$(BUILD)/to_from_c write >$(BUILD)/c.bin
	$(BUILD)/to_from_cxx write >$(BUILD)/c++.bin
	PYTHONPATH=$(TOP)/python:python $(PYTHON) python/main.py write \
		>$(BUILD)/python.bin
	for lang in c c++ python; do \
	    cmp $(BUILD)/rust.bin $(BUILD)/$$lang.bin || exit 1; \
	done
	@for to in $(LANGS); do \
	    for from in $(LANGS); do \
	        ./to-from $$to $$from >$(BUILD)/out.txt || exit 1; \
	        head -n 1 $(BUILD)/out.txt; \
	        tail -n +2 $(BUILD)/out.txt >$(BUILD)/$$to-$$from.txt; \
	        cmp $(BUILD)/c-c.txt $(BUILD)/$$to-$$from.txt || exit 1; \
	    done; \
	done
	cat $(BUILD)/c-c.txt
	@echo "to-from: the 4 writers wrote the same bytes, and the 16" \
		"reader/writer pairs printed the same words"

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
