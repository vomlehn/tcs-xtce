# Documentation for the examples.
#
#   make            convert README.rst to README.html
#   make html       the same
#   make clean      remove README.html
#
# Each example has its own Makefile that builds and runs it; `make examples`
# at the top level runs them all.

RST2HTML ?= rst2html

.PHONY: all html clean

all: html

html: README.html

# --halt=warning turns docutils warnings into errors, so a malformed README fails
# the build instead of producing a damaged page.
README.html: README.rst
	$(RST2HTML) --halt=warning $< $@

clean:
	rm -f README.html
