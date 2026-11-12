# The documents as HTML.
#
#   make            convert every document here to HTML
#   make html       the same
#   make clean      remove the HTML
#
# The HTML is committed beside its source, and GitHub Pages serves it at
# https://vomlehn.github.io/tcs-xtce/docs/, which the README's See also links.
# The pre-commit hook (../githooks/pre-commit) remakes it and adds it to a
# commit that changes a document, and ``make html-check`` in the top
# directory fails where it is stale.
#
# manual.rst is the short page that says what each manual covers;
# tcs-xtce.rst is the code generator's manual and tcs-xtcee.rst the
# editor's. design.rst says the same of the design documents,
# tcs-xtce-design.rst and tcs-xtcee-design.rst.

RST2HTML ?= rst2html

.PHONY: all
all: html

.PHONY: html
html: manual.html tcs-xtce.html tcs-xtcee.html design.html \
	tcs-xtce-design.html tcs-xtcee-design.html

# --halt=warning turns docutils warnings into errors, so a malformed document
# fails the build instead of producing a damaged page.
manual.html: manual.rst
	$(RST2HTML) --halt=warning $< $@

tcs-xtce.html: tcs-xtce.rst
	$(RST2HTML) --halt=warning $< $@

tcs-xtcee.html: tcs-xtcee.rst
	$(RST2HTML) --halt=warning $< $@

design.html: design.rst
	$(RST2HTML) --halt=warning $< $@

tcs-xtce-design.html: tcs-xtce-design.rst
	$(RST2HTML) --halt=warning $< $@

tcs-xtcee-design.html: tcs-xtcee-design.rst
	$(RST2HTML) --halt=warning $< $@

.PHONY: clean
clean:
	rm -f manual.html tcs-xtce.html tcs-xtcee.html design.html \
		tcs-xtce-design.html tcs-xtcee-design.html
