# Documentation for the examples.
#
#   make            convert README.rst, and each example's, to index.html
#   make html       the same
#   make clean      remove index.html (not the examples')
#
# Each example has its own Makefile that builds and runs it and makes its
# index.html; `make examples` at the top level runs them all.

RST2HTML ?= rst2html
EXAMPLES := $(patsubst %/Makefile,%,$(wildcard */Makefile))

.PHONY: all
all: html

.PHONY: html
html: index.html
	for d in $(EXAMPLES); do \
	    $(MAKE) -C $$d html RST2HTML="$(RST2HTML)" || exit 1; \
	done

# --halt=warning turns docutils warnings into errors, so a malformed README
# fails the build instead of producing a damaged page.
index.html: README.rst
	$(RST2HTML) --halt=warning $< $@

.PHONY: clean
clean:
	rm -f index.html
