# Builds tcs-ccsds: the Rust crate and its time tool, and the C/C++ time tool
# from the header-only library in include/.  The top-level Makefile runs the
# cross-language check, tests/ccsds_check.sh.

CC       ?= gcc
CXX      ?= g++
CARGO    ?= cargo
CFLAGS   ?= -O2 -g
CXXFLAGS ?= -O2 -g
WARN     := -Wall -Wextra
BUILD    := build
HEADERS  := $(wildcard include/ccsds/*.h)

.PHONY: all
all: rust $(BUILD)/time_tool $(BUILD)/time_tool_cxx

.PHONY: rust
rust:
	cd rust && $(CARGO) build --release --all-targets

.PHONY: test
test: rust
	cd rust && $(CARGO) test --release

$(BUILD)/time_tool: tests/time_tool.c $(HEADERS) | $(BUILD)
	$(CC) -std=c11 $(CFLAGS) $(WARN) -Iinclude $< -o $@

$(BUILD)/time_tool_cxx: tests/time_tool.c $(HEADERS) | $(BUILD)
	$(CXX) -std=c++20 $(CXXFLAGS) $(WARN) -Iinclude -x c++ $< -o $@

$(BUILD):
	mkdir -p $@

.PHONY: clean
clean:
	rm -rf $(BUILD)
	cd rust && $(CARGO) clean
