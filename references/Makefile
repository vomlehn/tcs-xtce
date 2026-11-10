# Pulls the latest published OMG XTCE documents into this directory.
#
# The latest version is discovered from the OMG specification landing page,
# so re-running `make` picks up new releases without editing this file.
#
# Outputs:
#   SpaceSystem.xsd   - the normative XTCE XML schema (machine readable)
#   XTCE.pdf          - the XTCE specification document
#   VERSION           - the XTCE version and schema date that were fetched
#   xml.xsd           - W3C schema imported by SpaceSystem.xsd (for validation)
#   catalog.xml       - XML catalog mapping the import URL to xml.xsd, use with
#                       XML_CATALOG_FILES=references/catalog.xml xmllint --nonet
#
# `make editor` also fetches, once, the schema and specification of each
# XTCE version the editor holds documents to, by fixed address, since the
# editor has rules for exactly those versions and no others:
#
#   xtce-<version>.xsd, XTCE-<version>.pdf   for 1.1, 1.2 and 1.3

XTCE_SPEC_URL ?= https://www.omg.org/spec/XTCE/
CURL          ?= curl -fsSL --retry 3 --retry-delay 2 \
                 -A "Mozilla/5.0 (tcs-xtce)"
LANDING       := .landing.html

# The links on the landing page to the newest schema and specification.
XSD_RE        := https://www\.omg\.org/spec/XTCE/[0-9]+/SpaceSystem\.xsd
PDF_RE        := https://www\.omg\.org/spec/XTCE/[0-9]+(\.[0-9]+)+/PDF
# SpaceSystem.xsd's import of xml.xsd, and the namespace of catalog.xml.
IMPORT_RE     := schemaLocation="[^"]*xml\.xsd"
CATALOG_NS    := urn:oasis:names:tc:entity:xmlns:xml:catalog

.PHONY: all
all: fetch

# The versions the editor knows, and where OMG publishes each one's schema.
# XTCE 1.0's schema was published only inside its specification.
EDITOR_VERSIONS := 1.1 1.2 1.3
XSD_URL_1.1     := https://www.omg.org/spec/XTCE/20061101/06-11-06.xsd
XSD_URL_1.2     := https://www.omg.org/spec/XTCE/20180204/SpaceSystem.xsd
XSD_URL_1.3     := https://www.omg.org/spec/XTCE/20250214/SpaceSystem.xsd
EDITOR_FILES    := $(foreach v,$(EDITOR_VERSIONS),xtce-$(v).xsd XTCE-$(v).pdf)

.PHONY: editor
editor: $(EDITOR_FILES)

xtce-%.xsd:
	@echo "  $(XSD_URL_$*)"
	@$(CURL) -o $@.tmp "$(XSD_URL_$*)"
	@xmllint --noout $@.tmp
	@mv $@.tmp $@

XTCE-%.pdf:
	@echo "  $(XTCE_SPEC_URL)$*/PDF"
	@$(CURL) -o $@.tmp "$(XTCE_SPEC_URL)$*/PDF"
	@head -c 5 $@.tmp | grep -q '%PDF-' || \
		{ echo "error: $@ is not a PDF" >&2; rm -f $@.tmp; exit 1; }
	@mv $@.tmp $@

# Always re-check the landing page so that "latest" really is the latest.
.PHONY: fetch
fetch:
	@$(CURL) -o $(LANDING).tmp "$(XTCE_SPEC_URL)"
	@mv $(LANDING).tmp $(LANDING)
	@xsd_url=$$(grep -oE '$(XSD_RE)' $(LANDING) | \
		sort -u | sort -t/ -k6,6n | tail -1); \
	version=$$(grep -oE '$(PDF_RE)' $(LANDING) | \
		sed -E 's#.*/XTCE/([0-9.]+)/PDF#\1#' | sort -u -V | tail -1); \
	if [ -z "$$xsd_url" ] || [ -z "$$version" ]; then \
		echo "error: could not locate XTCE documents on" \
			"$(XTCE_SPEC_URL)" >&2; \
		exit 1; \
	fi; \
	schema_date=$$(echo "$$xsd_url" | sed -E 's#.*/XTCE/([0-9]+)/.*#\1#'); \
	echo "XTCE $$version (schema $$schema_date)"; \
	echo "  $$xsd_url"; \
	$(CURL) -o SpaceSystem.xsd.tmp "$$xsd_url" && \
		mv SpaceSystem.xsd.tmp SpaceSystem.xsd; \
	echo "  $(XTCE_SPEC_URL)$$version/PDF"; \
	$(CURL) -o XTCE.pdf.tmp "$(XTCE_SPEC_URL)$$version/PDF" && \
		mv XTCE.pdf.tmp XTCE.pdf; \
	printf 'version=%s\nschema_date=%s\nschema_url=%s\npdf_url=%s\n' \
		"$$version" "$$schema_date" "$$xsd_url" \
		"$(XTCE_SPEC_URL)$$version/PDF" > VERSION; \
	import_url=$$(grep -oE '$(IMPORT_RE)' SpaceSystem.xsd | \
		head -1 | sed -E 's/.*="([^"]*)"/\1/'); \
	echo "  $$import_url"; \
	$(CURL) -o xml.xsd.tmp "$$import_url" && mv xml.xsd.tmp xml.xsd; \
	printf '%s\n' '<?xml version="1.0"?>' \
		'<catalog xmlns="$(CATALOG_NS)">' \
		"  <system systemId=\"$$import_url\" uri=\"xml.xsd\"/>" \
		"  <uri name=\"$$import_url\" uri=\"xml.xsd\"/>" \
		'</catalog>' > catalog.xml
	@$(MAKE) --no-print-directory check

# Sanity-check the downloads.
.PHONY: check
check:
	@xmllint --noout SpaceSystem.xsd xml.xsd catalog.xml
	@head -c 5 XTCE.pdf | grep -q '%PDF-' || \
		{ echo "error: XTCE.pdf is not a PDF" >&2; exit 1; }
	@echo "references OK: $$(tr '\n' ' ' < VERSION)"

.PHONY: clean
clean:
	rm -f $(LANDING) *.tmp

.PHONY: distclean
distclean: clean
	rm -f SpaceSystem.xsd XTCE.pdf VERSION xml.xsd catalog.xml \
		$(EDITOR_FILES)
