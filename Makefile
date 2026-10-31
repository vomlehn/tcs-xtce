# Top-level build for the XTCE serialization libraries.
#
#   make                 fetch references (if missing), generate, build, test
#   make references      pull the latest XTCE documents into references/
#   make generate        regenerate the Rust, C/C++ and Python bindings
#   make samples         regenerate the schema-covering test documents
#   make packets         regenerate the demo mission's packet codecs
#   make rust|cpp|python build one library
#   make test            run every language's tests, the cross-language
#                        checks (packets and digests) and the line-width
#                        check
#   make bnum-check      check xtce-bnum.h and xtce-bnum.hpp against the
#                        bnum crate
#   make ccsds-check     check tcs-ccsds's time codes in every language
#   make line-width      check that no line of code is wider than 80 columns
#   make samples-check   check tcs-xtce against public XTCE documents
#                        (downloads them)
#   make examples        build and run every example in examples/
#   make fmt             format Rust and Python code;  make fmt-check
#                        verify it
#   make python-dist     build the Python package's wheel and sdist into
#                        dist/ and try them
#   make install         build the packages and install them with apt-get,
#                        dnf or zypper, under sudo; SIMULATE=1 shows what
#                        it would do, PACKAGE_FORMAT=deb|rpm and
#                        INSTALLER=<command> choose otherwise
#   make uninstall       remove the packages with the same installer
#   make install-files   copy the files under PREFIX (/opt/tcs); DESTDIR
#                        stages, HOOKS=1 adds the files in /etc and /usr
#   make uninstall-files remove what install-files put there
#   make links           link the installed programs into LINKDIR
#                        (/usr/local/bin); make unlinks removes the links
#   make stage           install-files into build/stage/tcs-xtce, as the
#                        package will install
#   make deb             build build/packages/tcs-xtce_<version>-1_<arch>.deb
#                        (says so and does nothing without dpkg-deb)
#   make rpm             build build/packages/tcs-xtce-<version>-1.<arch>.rpm
#                        (says so and does nothing without rpmbuild)
#   make packages        both; install-files, stage and the packages strip the
#                        libraries unless STRIP=0 is given
#   make lint-packages   check the packages and their shell scripts with
#                        lintian, rpmlint and shellcheck
#   make html            remake the HTML documents from their sources
#   make help            print this list
#   make clean           remove build products (keeps generated sources)
#   make distclean       also remove downloaded references

PYTHON  ?= python3
CARGO   ?= cargo
CURL    ?= curl -fsSL --retry 3 --retry-delay 2

XSD     := references/SpaceSystem.xsd
GEN     := $(PYTHON) -m tcs_xtce.binding --xsd ../$(XSD)
GEN_SRC := $(wildcard python/tcs_xtce/binding/*.py)

RUST_GEN := rust/src/generated.rs
CPP_GEN  := cpp/include/xtce-generated.hpp cpp/src/generated.cpp
PY_GEN   := python/tcs_xtce/generated.py
SAMPLES  := tests/samples/COVERAGE.txt

RUST_TOOL := rust/target/release/examples/xtce_roundtrip
CPP_TOOL  := cpp/build/xtce_roundtrip

# Packet codecs generated from a mission's XTCE document by tcs-xtce; the demo
# mission exercises them in every language.
MISSION  := tests/mission/demo.xtce
PKT      := $(PYTHON) -m tcs_xtce ../$(MISSION) --source-name $(MISSION)
# tcs-xtce also uses tcs_xtce.binding, which formats and wraps what it
# writes.
PKT_SRC  := $(wildcard python/tcs_xtce/compiler/*.py \
                      python/tcs_xtce/__main__.py \
                      python/tcs_xtce/binding/*.py)
PKT_RUST := rust/tests/mission/demo.rs rust/tests/mission/demo_fixed.rs
PKT_CPP  := cpp/tests/mission/demo.h
PKT_PY   := python/tests/mission/demo.py
PKT_GEN  := $(PKT_RUST) $(PKT_CPP) $(PKT_PY)
RUST_PKT_TOOL := rust/target/release/examples/packet_tool
RUST_FIXED_PKT_TOOL := rust/target/release/examples/packet_tool_fixed
C_PKT_TOOL    := cpp/build/packet_tool
CXX_PKT_TOOL  := cpp/build/packet_tool_cxx

.PHONY: all
all: generate rust cpp python test

# The list of targets is the comment that opens this file, so there is one
# list to keep current.
.PHONY: help
help:
	@sed -n '/^#/!q; s/^# \{0,1\}//p' $(firstword $(MAKEFILE_LIST))

# Always re-checks OMG for the latest published version.
.PHONY: references
references:
	$(MAKE) -C references

# Fetch only if nothing has been downloaded yet.
$(XSD):
	$(MAKE) -C references

.PHONY: generate
generate: $(RUST_GEN) $(CPP_GEN) $(PY_GEN) $(SAMPLES) $(PKT_GEN)

$(RUST_GEN): $(XSD) $(GEN_SRC)
	cd python && $(GEN) --rust ../$@
	@touch $@

$(CPP_GEN) &: $(XSD) $(GEN_SRC)
	cd python && $(GEN) --cpp-header ../cpp/include/xtce-generated.hpp \
		--cpp-source ../cpp/src/generated.cpp
	@touch $(CPP_GEN)

$(PY_GEN): $(XSD) $(GEN_SRC)
	cd python && $(GEN) --python ../$@
	@touch $@

$(SAMPLES): $(XSD) $(GEN_SRC)
	cd python && $(GEN) --sample ../tests/samples
	@touch $@

.PHONY: samples
samples: $(SAMPLES)

# tcs-xtce reads the mission with the generated Python binding and writes one
# language per run.
$(PKT_GEN) &: $(MISSION) $(PKT_SRC) $(PY_GEN)
	cd python && $(PKT) ../rust/tests/mission/demo.rs --lang rust
	cd python && $(PKT) ../rust/tests/mission/demo_fixed.rs --lang rust \
		--dynamic-allocations false
	cd python && $(PKT) ../$(PKT_CPP) --lang c
	cd python && $(PKT) ../$(PKT_PY) --lang python
	@touch $(PKT_GEN)

.PHONY: packets
packets: $(PKT_GEN)

# Rust formatting is checked on every build so unformatted code is caught as
# soon as it is added.
.PHONY: rust
rust: $(RUST_GEN) $(PKT_RUST) fmt-check
	cd rust && $(CARGO) build --release --all-targets

.PHONY: cpp
cpp: $(CPP_GEN) $(PKT_CPP)
	$(MAKE) -C cpp

.PHONY: python
python: $(PY_GEN) $(PKT_PY)
	$(PYTHON) -m compileall -q python/tcs_xtce

EXAMPLES := $(patsubst %/Makefile,%,$(wildcard examples/*/Makefile))

.PHONY: test
test: test-rust test-cpp test-python cross-check packet-check \
      stream-check digest-check float-check bnum-check ccsds-check examples \
      line-width doc-headers

# Every non-trivial function, hand-written or generated, has a header that
# describes it, its parameters and what it returns.  Last, with line-width,
# for the same reason.
.PHONY: doc-headers
doc-headers:
	$(PYTHON) -I tests/doc_headers.py

# Every line of code, hand-written or generated, fits in 80 columns.  It comes
# last in `test` so that the code the examples generate exists by then.
.PHONY: line-width
line-width:
	$(PYTHON) -I tests/line_width.py

# The packet compiler reads XTCE with the generated Python binding.
# The compiler's tests alone; test-python runs them too.
.PHONY: test-generator
test-generator: $(PY_GEN)
	cd python && $(PYTHON) -m unittest discover -s tests/compiler -t .

.PHONY: test-rust
test-rust: rust $(SAMPLES)
	cd rust && $(CARGO) test --release

.PHONY: test-cpp
test-cpp: cpp $(SAMPLES)
	$(MAKE) -C cpp test

.PHONY: test-python
test-python: python $(SAMPLES)
	cd python && $(PYTHON) -m unittest discover -s tests -t .

.PHONY: cross-check
cross-check: rust cpp python $(SAMPLES)
	sh tests/cross_check.sh $(RUST_TOOL) $(CPP_TOOL)

.PHONY: packet-check
packet-check: rust cpp python
	sh tests/packet_check.sh $(RUST_PKT_TOOL) $(RUST_FIXED_PKT_TOOL) \
		$(C_PKT_TOOL) $(CXX_PKT_TOOL)

.PHONY: stream-check
stream-check: rust cpp python
	sh tests/stream_check.sh rust/target/release/examples/stream_tool \
		cpp/build/stream_tool cpp/build/stream_tool_cxx

.PHONY: digest-check
digest-check: rust cpp
	sh tests/digest_check.sh rust/target/release/examples/digest_tool \
		cpp/build/digest_tool cpp/build/digest_tool_cxx

.PHONY: float-check
float-check: rust cpp
	sh tests/float_check.sh rust/target/release/examples/float_tool \
		cpp/build/float_tool cpp/build/float_tool_cxx

.PHONY: bnum-check
bnum-check: rust cpp
	sh tests/bnum_check.sh rust/target/release/examples/bnum_tool \
		cpp/build/bnum_tool cpp/build/bnum_tool_cxx \
		cpp/build/bnum_tool_hpp

# The Python package -- the runtime and the compiler, with the tcs-xtce and
# xtce-digest commands -- as a wheel and an sdist in dist/, then installed in a
# fresh virtual environment and tried: see pyproject.toml and
# tests/python_dist_check.sh. Built by setuptools in place, without pip's
# isolated build environment, so nothing is downloaded; setuptools 77 or later
# has to be installed. Not part of `make test`. Publishing them is not done
# here. setuptools works in build/lib and build/bdist.*, which are removed
# before and after; the rest of build/ is the packages', and is left alone.
SETUPTOOLS_BUILD = build/lib build/bdist.*
.PHONY: python-dist
python-dist: $(PY_GEN)
	rm -rf dist $(SETUPTOOLS_BUILD) python/tcs_xtce.egg-info
	$(PYTHON) -c 'from setuptools import build_meta as b; \
		b.build_wheel("dist"); b.build_sdist("dist")'
	rm -rf $(SETUPTOOLS_BUILD) python/tcs_xtce.egg-info
	rmdir build 2>/dev/null || true
	sh tests/python_dist_check.sh dist

# Public XTCE documents, pinned to commits in tests/external/sources.txt and
# downloaded on demand; not part of `make test`, which needs no network.
EXTERNAL := tests/external/files

.PHONY: samples-check
samples-check: rust cpp $(PY_GEN)
	@mkdir -p $(EXTERNAL)
	@grep -v '^#' tests/external/sources.txt | \
	while read -r name sha url; do \
	    [ -n "$$name" ] || continue; \
	    [ -f $(EXTERNAL)/$$name ] && continue; \
	    mkdir -p $$(dirname $(EXTERNAL)/$$name); \
	    echo "fetch $$name"; \
	    $(CURL) -o $(EXTERNAL)/$$name.tmp "$$url" && \
	        mv $(EXTERNAL)/$$name.tmp $(EXTERNAL)/$$name || exit 1; \
	done
	$(PYTHON) -I tests/samples_check.py

# Each example regenerates its code if needed, builds and runs it in Rust,
# C, C++ and Python, and checks that the outputs match.
# Every example must exist in all supported languages: C/C++, Rust and
# Python 3, and have a README.rst.
EXAMPLE_FILES := rust/Cargo.toml rust/src/main.rs c/main.c python/main.py \
                 README.rst

.PHONY: examples
examples: cpp python
	@for d in $(EXAMPLES); do \
	    for f in $(EXAMPLE_FILES); do \
	        [ -f $$d/$$f ] || { \
	            echo "$$d: missing $$f (examples need C/C++, Rust" \
	                "and Python versions and a README.rst)" >&2; \
	            exit 1; \
	        }; \
	    done; \
	done
	for d in $(EXAMPLES); do $(MAKE) -C $$d || exit 1; done

# Hand-written Python in git, laid out by tcs_xtce.binding.pyalign as the
# generated Python is.
PY_FILES = $$(git -C .. ls-files '*.py' | sed 's|^|../|') \
           ../examples/to-from/to-from

# The examples' Rust code includes their generated codecs, so generate them
# first.
# tcs-ccsds: the CCSDS time codes, in Rust, C, C++ and Python.
.PHONY: ccsds
ccsds:
	$(MAKE) -C ccsds

.PHONY: ccsds-check
ccsds-check: ccsds
	$(MAKE) -C ccsds test
	sh tests/ccsds_check.sh ccsds/rust/target/release/examples/time_tool \
		ccsds/build/time_tool ccsds/build/time_tool_cxx

.PHONY: fmt
fmt:
	cd rust && $(CARGO) fmt
	cd ccsds/rust && $(CARGO) fmt
	for d in $(EXAMPLES); do \
	    $(MAKE) -C $$d generate && \
	        (cd $$d/rust && $(CARGO) fmt) || exit 1; \
	done
	cd python && $(PYTHON) -m tcs_xtce.binding.pyalign $(PY_FILES)

.PHONY: fmt-check
fmt-check:
	cd rust && $(CARGO) fmt --check
	cd ccsds/rust && $(CARGO) fmt --check
	for d in $(EXAMPLES); do \
	    $(MAKE) -C $$d generate && \
	        (cd $$d/rust && $(CARGO) fmt --check) || exit 1; \
	done
	cd python && $(PYTHON) -m tcs_xtce.binding.pyalign --check $(PY_FILES)

# The documents as HTML: docs/ and the examples' READMEs. Committed beside
# their sources and served by GitHub Pages, so remade before a commit that
# changes one, and left alone by clean.
.PHONY: html
html:
	$(MAKE) -C docs html
	$(MAKE) -C examples html

# Installing, and the .deb and .rpm packages, as tcslog, tcslog-tools and
# tcspecial do them.
#
# The version is the Python package's, which rust/Cargo.toml keeps in step
# by hand; install checks that they agree. The shared library's soname
# carries its ABI version, from cpp/abi-version, which cpp/Makefile reads
# too and which is not the package's version: see cpp/Makefile for when it
# changes. tcs-ccsds has a version of its own.
VERSION := $(shell sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
RUST_VERSION := $(shell sed -n '1,/^version/s/^version = "\(.*\)"/\1/p' \
                        rust/Cargo.toml)
CCSDS_VERSION := $(shell sed -n 's/^version = "\(.*\)"/\1/p' \
                         ccsds/python/pyproject.toml)
ABI     := $(shell cat cpp/abi-version)
SONAME  := libxtce.so.$(ABI)

# Where `make install-files` puts everything: /opt/tcs, the tree the TCS
# packages share under FHS 3.0's /opt/<package>, so that one PATH entry
# and one loader directory serve them all. DESTDIR stages the install
# for packaging. A developer installing somewhere of their own sets
# PREFIX, such as PREFIX=$HOME.
PREFIX  ?= /opt/tcs
DESTDIR ?=
# Whether install-files also writes the hooks outside $(PREFIX) that make an
# installed /opt/tcs usable without being told where it is: the loader's
# directory in /etc/ld.so.conf.d, PATH and PKG_CONFIG_PATH in
# /etc/profile.d, and Python's path in a .pth file. Off unless asked
# for, so that an install touches nothing in /etc or /usr that its
# installer did not mean it to; `make stage` asks, the packages being
# where these belong. Each is named after this package, so no two TCS
# packages own the same file.
HOOKS   ?= 0
# Whether install-files strips what it installs: the shared library of what
# only a debugger needs (--strip-unneeded) and the static one of its
# debugging information (--strip-debug), which keeps the symbol table a
# link needs. On unless STRIP=0 is given, for an installed tree or a
# package to debug; `make stage`, and so the packages, follow it too.
STRIP   ?= 1
# tcs-helpers, the packaging scripts the TCS projects share: a checkout
# of https://github.com/vomlehn/tcs-helpers, beside this one unless
# TCS_HELPERS names another, used where it is rather than installed.
# Installing and packaging check it first: see the tcs-helpers target.
TCS_HELPERS ?= $(abspath $(CURDIR)/../tcs-helpers)
TCS_HELPERS_REQUIRED := 0.4.0
BINDIR  := $(DESTDIR)$(PREFIX)/bin
SBINDIR := $(DESTDIR)$(PREFIX)/sbin
INCDIR  := $(DESTDIR)$(PREFIX)/include
LIBDIR  := $(DESTDIR)$(PREFIX)/lib
PCDIR   := $(LIBDIR)/pkgconfig
DOCDIR  := $(DESTDIR)$(PREFIX)/share/doc/tcs-xtce
EXDIR   := $(DESTDIR)$(PREFIX)/share/examples/tcs-xtce
# Where the Python packages go: the same directory for every Python 3, so
# the path has no minor version in it to go stale when Python is upgraded.
# Python does not search it unless told to, by PYTHONPATH or by the .pth
# file HOOKS=1 installs; bin/tcs-xtce and bin/xtce-digest find it
# themselves.
PYDIR   ?= $(PREFIX)/lib/python3/site-packages
# What install puts there besides the libraries and the Python. The
# examples are their sources as git has them, without what building them
# leaves behind; their Makefiles expect to be in the repository, so the
# installed copies are to read and to build by hand.
DOCS    := README.md CONTRIBUTING.md LICENSE-APACHE LICENSE-MIT \
           docs/manual.html docs/design.html
COMMANDS := packaging/bin/tcs-xtce packaging/bin/xtce-digest
# The headers go straight into include, each named with its library's
# prefix, xtce- or ccsds-, so that none can be mistaken for another
# library's. The generated one is named because it may not have been
# generated yet when this is read.
HEADERS := $(sort $(wildcard cpp/include/*.h cpp/include/*.hpp \
                             ccsds/include/*.h) \
                  cpp/include/xtce-generated.hpp)
# The hooks HOOKS=1 installs name /opt/tcs, so they are refused with any
# other PREFIX rather than installed pointing at the wrong tree.
ifeq ($(HOOKS),1)
ifneq ($(PREFIX),/opt/tcs)
$(error HOOKS=1 installs files that name /opt/tcs; give PREFIX=/opt/tcs \
	or leave HOOKS unset)
endif
endif

# The hooks HOOKS=1 installs, outside $(PREFIX). The .pth file goes
# where Debian's python3 looks; `make rpm` moves it to where the RPM's
# Python looks.
LDCONF  := $(DESTDIR)/etc/ld.so.conf.d/tcs-xtce.conf
PROFILE := $(DESTDIR)/etc/profile.d/tcs-xtce.sh
PTH     := $(DESTDIR)/usr/lib/python3/dist-packages/tcs-xtce.pth

# Installs one Python package with pip, so that it has the metadata pip
# and importlib.metadata read, from a copy of its sources made for the
# purpose: setuptools builds in the directory it is given, and a build left
# in the source tree by `sudo make install-files` would be root's.
# --no-index keeps it off the network, and --target puts it in $(PYDIR)
# whatever Python the build machine has. pip --target writes the console scripts
# into $(PYDIR)/bin, with the build machine's interpreter in them and no
# way to find the package, so they are removed, bin/ having its own; so is
# the direct_url.json naming the copy, which is gone by then. Called with
# the distribution's name and version, then the files to copy.
define pyinstall
	set -e; \
	tmp=$$(mktemp -d -t tcs-xtce-py.XXXXXXXXXX); \
	trap 'rm -rf "$$tmp"' EXIT; \
	cp -R $(3) $$tmp/; \
	find $$tmp -name __pycache__ -type d -prune -exec rm -rf {} +; \
	$(PYTHON) -m pip install --quiet --no-deps --no-build-isolation \
		--no-index --no-cache-dir --no-compile --upgrade \
		--root-user-action=ignore \
		--target $(DESTDIR)$(PYDIR) $$tmp; \
	dist=$(DESTDIR)$(PYDIR)/$(1)-$(2).dist-info; \
	rm -f $$dist/direct_url.json; \
	sed -i -e '/^$(1)-[^/]*\.dist-info\/direct_url\.json,/d' \
		-e '/^\.\.\/\.\.\/bin\//d' $$dist/RECORD; \
	rm -rf $(DESTDIR)$(PYDIR)/bin
endef

# Stop, saying what to do, unless the tcs-helpers checkout is there and
# at least TCS_HELPERS_REQUIRED.
.PHONY: tcs-helpers
tcs-helpers:
	@if [ ! -x "$(TCS_HELPERS)/bin/tcs-helpers" ]; then \
	    echo "No tcs-helpers checkout at $(TCS_HELPERS): clone" \
	        "https://github.com/vomlehn/tcs-helpers beside this" \
	        "repository, or set TCS_HELPERS to where it is." >&2; \
	    exit 1; \
	fi
	@"$(TCS_HELPERS)/bin/tcs-helpers" --require $(TCS_HELPERS_REQUIRED)

# Copy everything into place under $(DESTDIR)$(PREFIX): what `make
# install` once did, kept for development and for `make stage`, which
# cuts the packages from it; `make install` installs the packages. What
# is copied: the two commands, and in sbin
# tcs-xtce-links, which links them into /usr/local/bin; the headers,
# straight into include; the shared library under its soname,
# with its development name linked to it, and the static one; pkg-config
# files naming the real prefix; the Python packages tcs_xtce and
# tcs_ccsds; and the documents and examples. The Rust crates are not
# installed, a Rust program taking them through cargo. The HTML is
# installed as committed, not remade, so that installing needs no
# rst2html; `make html` before a commit keeps it current. An example that
# starts with "#!" is a script to run, so it is installed executable and
# the rest are not.
.PHONY: install-files
install-files: $(PY_GEN) $(CPP_GEN) tcs-helpers
	@[ "$(VERSION)" = "$(RUST_VERSION)" ] || { \
	    echo "pyproject.toml is $(VERSION) and rust/Cargo.toml" \
	        "$(RUST_VERSION); make them agree" >&2; \
	    exit 1; \
	}
	@echo "Installing tcs-xtce $(VERSION) to $(DESTDIR)$(PREFIX)..."
	$(MAKE) -C cpp lib
	install -D -m 755 -t $(BINDIR) $(COMMANDS)
	install -d $(SBINDIR)
	sed -e 's|@PACKAGE@|tcs-xtce|g' -e 's|@PREFIX@|$(PREFIX)|g' \
		-e 's|@PROGRAMS@|$(notdir $(COMMANDS))|g' \
		$(TCS_HELPERS)/packaging/links.sh.in > $(SBINDIR)/tcs-xtce-links
	chmod 755 $(SBINDIR)/tcs-xtce-links
	install -D -m 644 -t $(INCDIR) $(HEADERS)
	install -D -m 644 cpp/build/libxtce.so $(LIBDIR)/$(SONAME)
	ln -sf $(SONAME) $(LIBDIR)/libxtce.so
	install -D -m 644 cpp/build/libxtce.a $(LIBDIR)/libxtce.a
	if [ "$(STRIP)" = 1 ]; then \
	    strip --strip-unneeded $(LIBDIR)/$(SONAME); \
	    strip --strip-debug $(LIBDIR)/libxtce.a; \
	fi
	install -d $(PCDIR)
	sed -e 's|@PREFIX@|$(PREFIX)|' -e 's|@VERSION@|$(VERSION)|' \
		packaging/xtce.pc.in > $(PCDIR)/xtce.pc
	sed -e 's|@PREFIX@|$(PREFIX)|' -e 's|@VERSION@|$(CCSDS_VERSION)|' \
		packaging/tcs-ccsds.pc.in > $(PCDIR)/tcs-ccsds.pc
	chmod 644 $(PCDIR)/xtce.pc $(PCDIR)/tcs-ccsds.pc
	$(call pyinstall,tcs_xtce,$(VERSION),pyproject.toml README.md \
		LICENSE-APACHE LICENSE-MIT python)
	$(call pyinstall,tcs_ccsds,$(CCSDS_VERSION), \
		ccsds/python/pyproject.toml ccsds/python/tcs_ccsds)
	install -D -m 644 -t $(DOCDIR) $(DOCS)
	files=$$(git ls-files examples) && [ -n "$$files" ] || { \
	    echo "install takes the examples from git, so run it in a" \
	        "clone of the repository" >&2; \
	    exit 1; \
	}; \
	for f in $$files; do \
	    mode=644; \
	    [ "$$(head -c 2 $$f)" != '#!' ] || mode=755; \
	    install -D -m $$mode $$f $(EXDIR)/$${f#examples/} || exit 1; \
	done
	if [ "$(HOOKS)" = 1 ]; then \
	    install -D -m 644 packaging/hooks/tcs-xtce.conf $(LDCONF); \
	    install -D -m 644 packaging/hooks/tcs-xtce.sh $(PROFILE); \
	    install -D -m 644 packaging/hooks/tcs-xtce.pth $(PTH); \
	fi
	@echo "[OK] Installed under $(DESTDIR)$(PREFIX)"
	@echo "     Run $(PREFIX)/bin/tcs-xtce; compile against it with:"
	@echo "       cc  prog.c   \$$(pkg-config --cflags --libs xtce)"
	@echo "       c++ prog.cpp \$$(pkg-config --cflags --libs xtce)"
	@echo "     with PKG_CONFIG_PATH=$(PREFIX)/lib/pkgconfig, and from"
	@echo "     Python 3, with $(PYDIR) on PYTHONPATH:"
	@echo "       import tcs_xtce"

# Removes what install-files put there, honouring the same variables,
# HOOKS included. Nothing else in those directories is touched. The links `make
# links` made in LINKDIR go first, while tcs-xtce-links is there to remove
# them; that it cannot, because they or LINKDIR are not there, is no reason
# not to uninstall.
.PHONY: uninstall-files
uninstall-files:
	@echo "Removing tcs-xtce from $(DESTDIR)$(PREFIX)..."
	-$(SBINDIR)/tcs-xtce-links --remove --dir $(LINKDIR)
	rm -f $(addprefix $(BINDIR)/,$(notdir $(COMMANDS))) \
		$(SBINDIR)/tcs-xtce-links
	rm -f $(addprefix $(INCDIR)/,$(notdir $(HEADERS)))
	rm -f $(LIBDIR)/$(SONAME) $(LIBDIR)/libxtce.so $(LIBDIR)/libxtce.a \
		$(PCDIR)/xtce.pc $(PCDIR)/tcs-ccsds.pc
	rm -rf $(DESTDIR)$(PYDIR)/tcs_xtce \
		$(DESTDIR)$(PYDIR)/tcs_xtce-$(VERSION).dist-info \
		$(DESTDIR)$(PYDIR)/tcs_ccsds \
		$(DESTDIR)$(PYDIR)/tcs_ccsds-$(CCSDS_VERSION).dist-info
	rm -rf $(DOCDIR) $(EXDIR)
	if [ "$(HOOKS)" = 1 ]; then \
	    rm -f $(LDCONF) $(PROFILE) $(PTH); \
	fi
	@echo "[OK] Uninstalled from $(DESTDIR)$(PREFIX)"

# Where `make links` links the installed programs, and from where
# uninstall-files removes them: see tcs-helpers' packaging.mk.
LINKDIR ?= /usr/local/bin

# The packaging targets -- install, uninstall, stage, deb, rpm, packages,
# lint-packages, links and unlinks -- are tcs-helpers' packaging.mk's,
# shared by the TCS projects; see it for what each does. This repository
# has the one package, built from packaging/deb/tcs-xtce and
# packaging/rpm/tcs-xtce.spec, which is also given the library's ABI
# version, tcs-ccsds's version and where the RPM's Python looks for
# packages. Without a tcs-helpers checkout new enough to have
# install-packages.sh, which came with the install target in 0.4.0, there
# is nothing to include that has every target here, and the targets stop
# as the tcs-helpers target says.
PACKAGES    := tcs-xtce
RPM_DEFINES  = --define "tcs_abi $(ABI)" \
               --define "tcs_ccsds_version $(CCSDS_VERSION)" \
               --define "tcs_pysite $$($(PYTHON) -c 'import sysconfig; \
                   print(sysconfig.get_path("purelib", "posix_prefix", \
                   vars={"base": "/usr"}))')" \
               --define "tcs_pyversion $$($(PYTHON) -c 'import sysconfig; \
                   print(sysconfig.get_python_version())')"
ifneq ($(wildcard $(TCS_HELPERS)/packaging/install-packages.sh),)
include $(TCS_HELPERS)/packaging/packaging.mk
else
.PHONY: install
install: tcs-helpers
.PHONY: uninstall
uninstall: tcs-helpers
.PHONY: stage
stage: tcs-helpers
.PHONY: deb
deb: tcs-helpers
.PHONY: rpm
rpm: tcs-helpers
.PHONY: packages
packages: tcs-helpers
.PHONY: lint-packages
lint-packages: tcs-helpers
.PHONY: links
links: tcs-helpers
.PHONY: unlinks
unlinks: tcs-helpers
endif

.PHONY: clean
clean:
	cd rust && $(CARGO) clean
	$(MAKE) -C cpp clean
	$(MAKE) -C ccsds clean
	for d in $(EXAMPLES); do $(MAKE) -C $$d clean; done
	$(MAKE) -C references clean
	find python -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf dist build python/tcs_xtce.egg-info

.PHONY: distclean
distclean: clean
	$(MAKE) -C references distclean
