# Documentation for the examples.
#
#   make            convert README.rst, and each example's, to README.html
#   make html       the same
#   make clean      remove README.html (not the examples')
#
# Each example has its own Makefile that builds and runs it and makes its
# README.html; `make examples` at the top level runs them all.

RST2HTML ?= rst2html
EXAMPLES := $(patsubst %/Makefile,%,$(wildcard */Makefile))

.PHONY: all
all: html

.PHONY: html
html: README.html
	for d in $(EXAMPLES); do \
	    $(MAKE) -C $$d html RST2HTML="$(RST2HTML)" || exit 1; \
	done

# --halt=warning turns docutils warnings into errors, so a malformed README
# fails the build instead of producing a damaged page.
README.html: README.rst
	$(RST2HTML) --halt=warning $< $@

.PHONY: clean
clean:
	rm -f README.html
