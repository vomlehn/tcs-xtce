# The tcs-xtce RPM, built by `make rpm` from the tree `make stage`
# installed under build/stage/tcs-xtce. Nothing is compiled here: %install copies
# that tree, so the RPM and the .deb hold the same files, built once.
# The Makefile passes tcs_version, from pyproject.toml; tcs_release, the
# Debian revision, so that the two are named alike; tcs_abi, from
# cpp/abi-version; tcsccsds_version, from ccsds/rust/Cargo.toml;
# tcs_stage; tcs_date, the date of the commit built, for the %changelog;
# and tcs_pysite and tcs_pyversion, the site-packages directory
# and version of the python3 it found, which on Fedora are what its
# python3_sitelib and python3_version macros say.

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

Name:		tcs-xtce
Version:	%{tcs_version}
Release:	%{tcs_release}%{?dist}
Summary:	XTCE serialization libraries, packet compiler and editor
Group:		Development/Libraries
License:	MIT OR Apache-2.0
URL:		https://github.com/vomlehn/tcs-xtce
%global tcs_maintainer David VomLehn <dvomlehn@gmail.com>
Packager:	%{tcs_maintainer}
# The compiler and xtce-digest are Python, and the editor runs the
# compiler, so unlike tcslog's this is a Requires. Any Python 3.10 or later
# will do: the package's directory has no Python version in it. The
# libraries the editor links with RPM finds for itself.
Requires:	python3 >= 3.10
# The editor opens its windows through libraries it loads when it runs,
# which RPM cannot see that it needs: the keyboard, X11 or Wayland, EGL
# and OpenGL, and D-Bus for the desktop's Open and Save dialogs.  The
# .deb names the same ones.
Requires:	libxkbcommon.so.0()(64bit)
Requires:	libxkbcommon-x11.so.0()(64bit)
Requires:	libX11.so.6()(64bit)
Requires:	libX11-xcb.so.1()(64bit)
Requires:	libXcursor.so.1()(64bit)
Requires:	libXi.so.6()(64bit)
Requires:	libXrender.so.1()(64bit)
Requires:	libEGL.so.1()(64bit)
Requires:	libGL.so.1()(64bit)
Requires:	libwayland-client.so.0()(64bit)
Requires:	libwayland-egl.so.1()(64bit)
Requires:	libdbus-1.so.3()(64bit)
# The editor downloads OMG's schemas and specifications with curl, when
# the person agrees, since OMG does not allow them to be packaged.
Requires:	curl
# The .pth file that puts /opt/tcs on python3's own path is in the
# directory of one Python version, the one this RPM was built for; the
# commands find the package without it. A Recommends, as tcslog's is.
Recommends:	python(abi) = %{tcs_pyversion}
# What the .deb suggests: rustfmt, which the compiler runs the Rust it
# generates through, and pkgconf, which finds the C and C++ libraries
# through /opt/tcs/lib/pkgconfig. Neither is needed to install it.
Suggests:	rustfmt
Suggests:	pkgconf

%description
Tcs-xtce reads and writes OMG XTCE (XML Telemetric and Command Exchange)
documents, and compiles a mission's XTCE into typed telemetry and command
structures with binary encoders and decoders, in Rust, C, C++ and Python,
using managed, preallocated buffers.

This package holds the compiler (tcs-xtce), xtce-digest, the graphical XTCE
editor (tcs-xtcee), which keeps a document valid for its XTCE version and
converts between versions 1.1, 1.2 and 1.3, the packet runtime tcsxtce for
Python 3 and for C and C++ (header-only), the C/C++ XTCE library
libtcs-xtce (shared and static) with its headers, the pkg-config files,
tcsccsds's CCSDS time codes for C, C++ and Python, and the documentation
and examples, all under /opt/tcs.

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
chmod 755 %{buildroot}/opt/tcs/lib/libtcs-xtce.so.%{tcs_abi}

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
/opt/tcs/bin/tcs-xtcee
/opt/tcs/sbin/tcs-xtce-links
/opt/tcs/include/tcs-xtce.h
/opt/tcs/include/tcs-xtce-*.hpp
/opt/tcs/include/tcsxtce-*.h
/opt/tcs/include/tcsxtce-*.hpp
/opt/tcs/include/tcsccsds-time.h
/opt/tcs/lib/libtcs-xtce.so
/opt/tcs/lib/libtcs-xtce.so.%{tcs_abi}
/opt/tcs/lib/libtcs-xtce.a
/opt/tcs/lib/pkgconfig/tcs-xtce.pc
/opt/tcs/lib/pkgconfig/tcsxtce.pc
/opt/tcs/lib/pkgconfig/tcsccsds.pc
/opt/tcs/lib/python3/site-packages/tcsxtce/
/opt/tcs/lib/python3/site-packages/tcs_xtce/
/opt/tcs/lib/python3/site-packages/tcs_xtce-%{version}.dist-info/
/opt/tcs/lib/python3/site-packages/tcsccsds/
/opt/tcs/lib/python3/site-packages/tcsccsds-%{tcsccsds_version}.dist-info/
%doc /opt/tcs/share/doc/tcs-xtce/
/opt/tcs/share/examples/tcs-xtce/
%config(noreplace) /etc/ld.so.conf.d/tcs-xtce.conf
%config(noreplace) /etc/profile.d/tcs-xtce.sh
%{tcs_pysite}/tcs-xtce.pth

# One entry, for the version being built, dated by the Makefile from the
# commit it was built from, so that a new version needs no edit here.
%changelog
* %{tcs_date} %{tcs_maintainer} - %{version}-%{release}
- tcs-xtce %{version}, as released upstream.
