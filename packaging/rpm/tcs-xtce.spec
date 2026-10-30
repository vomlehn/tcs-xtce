# The tcs-xtce RPM, built by `make rpm` from the tree `make stage`
# installed under build/stage/tcs-xtce. Nothing is compiled here: %install copies
# that tree, so the RPM and the .deb hold the same files, built once.
# The Makefile passes tcs_version, from pyproject.toml; tcs_abi, from
# cpp/abi-version; tcs_ccsds_version, from ccsds/rust/Cargo.toml;
# tcs_stage; and tcs_pysite and tcs_pyversion, the site-packages directory
# and version of the python3 it found, which on Fedora are what its
# python3_sitelib and python3_version macros say.

%{!?tcs_version: %{error: tcs_version is not defined: build with make rpm}}
%{!?tcs_stage: %{error: tcs_stage is not defined: build with make rpm}}

# The stage is already what is to be installed, stripped or not as `make
# stage` was told with STRIP. RPM's own post-install steps would strip it
# again, byte-compile the Python under /opt/tcs, and split out debugging
# information, none of which the .deb has, so they are all turned off;
# __strip is named as well so that no step that calls it can strip.
%global debug_package %{nil}
%global __os_install_post %{nil}
%global __strip /bin/true
%global _build_id_links none

Name:		tcs-xtce
Version:	%{tcs_version}
Release:	1%{?dist}
Summary:	XTCE serialization libraries and packet compiler
License:	MIT OR Apache-2.0
URL:		https://github.com/vomlehn/tcs-xtce
Packager:	David VomLehn <dvomlehn@gmail.com>
# The compiler and xtce-digest are Python, so unlike tcslog's this is a
# Requires. Any Python 3.10 or later will do: the package's directory has
# no Python version in it.
Requires:	python3 >= 3.10
# The .pth file that puts /opt/tcs on python3's own path is in the
# directory of one Python version, the one this RPM was built for; the
# commands find the package without it. A Recommends, as tcslog's is.
Recommends:	python(abi) = %{tcs_pyversion}

%description
Tcs-xtce reads and writes OMG XTCE (XML Telemetric and Command Exchange)
documents, and compiles a mission's XTCE into typed telemetry and command
structures with binary encoders and decoders, in Rust, C, C++ and Python,
using managed, preallocated buffers.

This package holds the compiler (tcs-xtce) and xtce-digest, the Python 3
runtime, the C/C++ library (shared and static) with its headers and
pkg-config file, tcs-ccsds's CCSDS time codes for C, C++ and Python, and
the documentation and examples, all under /opt/tcs.

%install
cp -a %{tcs_stage}/. %{buildroot}/
# The stage has the .pth where Debian's python3 looks; this one looks in
# its own site-packages.
pth=%{buildroot}/usr/lib/python3/dist-packages/tcs-xtce.pth
if [ "%{tcs_pysite}" != /usr/lib/python3/dist-packages ]; then
	mkdir -p %{buildroot}%{tcs_pysite}
	mv $pth %{buildroot}%{tcs_pysite}/
	rmdir %{buildroot}/usr/lib/python3/dist-packages
	rmdir %{buildroot}/usr/lib/python3
fi
# RPM finds what a shared library provides only in an executable file,
# where Debian wants one that is not.
chmod 755 %{buildroot}/opt/tcs/lib/libxtce.so.%{tcs_abi}

# On removal, not upgrade, the links tcs-xtce-links may have made go
# first, while it is still there to run, so that none is left pointing at
# nothing. A missing directory or a link not ours does not stop a removal.
%preun
if [ "$1" = 0 ]; then
	for dir in /usr/local/bin /opt/bin; do
		/opt/tcs/sbin/tcs-xtce-links --remove --dir "$dir" \
			> /dev/null 2>&1 || :
	done
fi

%post -p /sbin/ldconfig
%postun -p /sbin/ldconfig

%files
%dir /opt/tcs
%dir /opt/tcs/bin
%dir /opt/tcs/sbin
%dir /opt/tcs/include
%dir /opt/tcs/lib
%dir /opt/tcs/lib/pkgconfig
%dir /opt/tcs/lib/python3
%dir /opt/tcs/lib/python3/site-packages
%dir /opt/tcs/share
%dir /opt/tcs/share/doc
%dir /opt/tcs/share/examples
/opt/tcs/bin/tcs-xtce
/opt/tcs/bin/xtce-digest
/opt/tcs/sbin/tcs-xtce-links
/opt/tcs/include/xtce.h
/opt/tcs/include/xtce-*.h
/opt/tcs/include/xtce-*.hpp
/opt/tcs/include/ccsds-time.h
/opt/tcs/lib/libxtce.so
/opt/tcs/lib/libxtce.so.%{tcs_abi}
/opt/tcs/lib/libxtce.a
/opt/tcs/lib/pkgconfig/xtce.pc
/opt/tcs/lib/pkgconfig/tcs-ccsds.pc
/opt/tcs/lib/python3/site-packages/tcs_xtce/
/opt/tcs/lib/python3/site-packages/tcs_xtce-%{version}.dist-info/
/opt/tcs/lib/python3/site-packages/tcs_ccsds/
/opt/tcs/lib/python3/site-packages/tcs_ccsds-%{tcs_ccsds_version}.dist-info/
%doc /opt/tcs/share/doc/tcs-xtce/
/opt/tcs/share/examples/tcs-xtce/
%config(noreplace) /etc/ld.so.conf.d/tcs-xtce.conf
%config(noreplace) /etc/profile.d/tcs-xtce.sh
%{tcs_pysite}/tcs-xtce.pth
