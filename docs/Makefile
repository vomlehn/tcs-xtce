# The documents as HTML.
#
#   make            convert manual.rst and design.rst to HTML
#   make html       the same
#   make clean      remove the HTML
#
# The HTML is committed beside its source, and GitHub Pages serves it at
# https://vomlehn.github.io/tcs-xtce/docs/, which the README's See also links.
# Remake it before committing a change to either document.

RST2HTML ?= rst2html

.PHONY: all
all: html

.PHONY: html
html: manual.html design.html

# --halt=warning turns docutils warnings into errors, so a malformed document
# fails the build instead of producing a damaged page.
manual.html: manual.rst
	$(RST2HTML) --halt=warning $< $@

design.html: design.rst
	$(RST2HTML) --halt=warning $< $@

.PHONY: clean
clean:
	rm -f manual.html design.html
