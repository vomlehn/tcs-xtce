# The tcsxtce RPM, built by `make rpm` from the tree `make stage`
# installed under build/stage/tcsxtce. Nothing is compiled here: %install copies
# that tree, so the RPM and the .deb hold the same files, built once.
# The Makefile passes tcs_version, from pyproject.toml; tcs_release, the
# Debian revision, so that the two are named alike; tcs_abi, from
# cpp/abi-version; tcsccsds_version, from ccsds/rust/Cargo.toml;
# tcs_stage; and tcs_date, the date of the commit built, for the
# %changelog.

%{!?tcs_version: %{error: tcs_version is not defined: build with make rpm}}
%{!?tcs_release: %{error: tcs_release is not defined: build with make rpm}}
%{!?tcs_stage: %{error: tcs_stage is not defined: build with make rpm}}
%{!?tcs_date: %{error: tcs_date is not defined: build with make rpm}}

# The stage is already what is to be installed, stripped or not as `make
# stage` was told with STRIP. RPM's own post-install steps would strip it
# again, byte-compile the Python under /opt/tcs, and split out debugging
# information, none of which the .deb has, so they are all turned off;
# __strip is named as well so that no step that calls it can strip.
%global debug_package %{nil}
%global __os_install_post %{nil}
%global __strip /bin/true
%global _build_id_links none

Name:		tcsxtce
Version:	%{tcs_version}
Release:	%{tcs_release}%{?dist}
Summary:	Packet runtime for code tcs-xtce generates, and CCSDS time codes
Group:		Development/Libraries
License:	MIT OR Apache-2.0
URL:		https://github.com/vomlehn/tcs-xtce
%global tcs_maintainer David VomLehn <dvomlehn@gmail.com>
Packager:	%{tcs_maintainer}
# The Python package needs any Python 3.10 or later; the C and C++
# headers need nothing.
Recommends:	python3 >= 3.10
Suggests:	pkgconf
# The runtime's files were tcs-xtce's before 0.7.1.
Conflicts:	tcs-xtce < 0.7.1

%description
Tcsxtce is what the packet code tcs-xtce generates from a mission's XTCE
runs on, aboard a vehicle and in the mission operations center: the
packet runtime and the digest two programs check each other's XTCE with,
header-only for C and C++ and as a Python 3 package. tcsccsds's CCSDS time
codes for C, C++ and Python come with it.

This package is operational code alone, under /opt/tcs; the compiler that
generates the code is the tcs-xtce package.

%install
cp -a %{tcs_stage}/. %{buildroot}/
# The stage has the .pth where Debian's python3 looks; this one's is
# written when it is installed, by the trigger below.
rm -rf %{buildroot}/usr/lib/python3

# The .pth file goes when the package does ($1 is 0), not on an upgrade.
%preun
if [ "$1" = 0 ]; then
	rm -f /usr/lib/python3*/site-packages/tcsxtce.pth
fi

# The .pth file that puts /opt/tcs/lib/python3/site-packages on python3's
# path. It goes in python3's own site-packages, which on Fedora names its
# minor version (/usr/lib/python3.15/site-packages), so the python3 that is
# installed says where, rather than the one that built the package. This
# runs when the package is installed with a python3 there, and again when
# python3 is installed or upgraded, moving the file to the new version's
# directory; it never fails the transaction.
%triggerin -- python3
rm -f /usr/lib/python3*/site-packages/tcsxtce.pth
site=$(python3 -c 'import sysconfig
print(sysconfig.get_path("purelib", "posix_prefix", {"base": "/usr"}))') \
	&& mkdir -p "$site" \
	&& echo /opt/tcs/lib/python3/site-packages > "$site/tcsxtce.pth" || :

# python3 removed ($2 is 0) takes the .pth with it.
%triggerun -- python3
if [ "$2" = 0 ]; then
	rm -f /usr/lib/python3*/site-packages/tcsxtce.pth
fi

%files
%dir /opt/tcs
%dir /opt/tcs/include
%dir /opt/tcs/lib
%dir /opt/tcs/lib/pkgconfig
%dir /opt/tcs/lib/python3
%dir /opt/tcs/lib/python3/site-packages
%dir /opt/tcs/share
%dir /opt/tcs/share/doc
/opt/tcs/include/tcsxtce-*.h
/opt/tcs/include/tcsxtce-*.hpp
/opt/tcs/include/tcsccsds-time.h
/opt/tcs/lib/pkgconfig/tcsxtce.pc
/opt/tcs/lib/pkgconfig/tcsccsds.pc
/opt/tcs/lib/python3/site-packages/tcsxtce/
/opt/tcs/lib/python3/site-packages/tcsccsds/
/opt/tcs/lib/python3/site-packages/tcsccsds-%{tcsccsds_version}.dist-info/
%doc /opt/tcs/share/doc/tcsxtce/
%config(noreplace) /etc/profile.d/tcsxtce.sh

# One entry, for the version being built, dated by the Makefile from the
# commit it was built from, so that a new version needs no edit here.
%changelog
* %{tcs_date} %{tcs_maintainer} - %{version}-%{release}
- tcsxtce %{version}, as released upstream.
