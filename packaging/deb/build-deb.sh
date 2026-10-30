#!/bin/sh
# Builds one .deb from the tree `make stage` installed that package into.
#
#   packaging/deb/build-deb.sh <package> <stage> <work> <out> <version> <arch>
#
# <package> is tcs-xtce, the one package here; <stage> the
# directory `make stage` installed it into, by itself; <work> a directory
# this may fill and empty; <out> where the .deb goes; <version> the Debian
# version, upstream and revision; <arch> what `dpkg --print-architecture`
# says. `make deb` runs it once per package.
#
# Without a debian/ tree, because the packages are cut from what `make
# install` puts in place rather than built by debhelper: the stage is the one
# account of what is installed where, and a second one in debian/ would be a
# second list to keep right.
set -eu

package=$1
stage=$2
work=$3
out=$4
version=$5
arch=$6

here=$(dirname "$0")
template=$here/$package/DEBIAN
root=$work/$package

rm -rf "$root"
mkdir -p "$root" "$out"
cp -a "$stage/." "$root/"
mkdir "$root/DEBIAN"

# The shared libraries the package's programs and libraries are linked with,
# as the packages that hold them. dpkg-shlibdeps reads debian/control, which
# a package built without a debian/ tree does not have, so it is given one
# that names this package and says nothing else.
elves=$(find "$root" -path "$root/DEBIAN" -prune -o -type f -print |
	while read -r file; do
		if [ "$(head -c 4 "$file" | od -An -c | tr -d ' ')" = 177ELF ]
		then
			echo "$file"
		fi
	done)
if [ -z "$elves" ]; then
	echo "build-deb.sh: $package has no programs or libraries in it:" \
	     "run make stage first" >&2
	exit 1
fi
shlibs=$work/shlibdeps-$package
rm -rf "$shlibs"
mkdir -p "$shlibs/debian"
printf 'Source: tcs-xtce\n\nPackage: %s\nArchitecture: any\n' "$package" \
	> "$shlibs/debian/control"
# shellcheck disable=SC2086 # One ELF file per word.
depends=$(cd "$shlibs" && dpkg-shlibdeps -O -l"$root/opt/tcs/lib" $elves |
	sed -n 's/^shlibs:Depends=//p')
if [ -z "$depends" ]; then
	echo "build-deb.sh: dpkg-shlibdeps found no dependencies for" \
	     "$package, which links with libc at least" >&2
	exit 1
fi

size=$(du -sk --exclude=DEBIAN "$root" | cut -f1)
sed -e "s|@VERSION@|$version|g" -e "s|@ARCH@|$arch|g" \
    -e "s|@SIZE@|$size|g" -e "s|@SHLIBS@|$depends|g" \
	"$template/control" > "$root/DEBIAN/control"

for script in prerm postinst postrm; do
	if [ -f "$template/$script" ]; then
		install -m 755 "$template/$script" "$root/DEBIAN/$script"
	fi
done

# A shlibs entry for each shared library the package holds under its
# soname, so that a package built against one -- with dpkg-shlibdeps -l
# /opt/tcs/lib -- depends on this package at this version or later. Found
# from the files, as the conffiles are, rather than listed.
for library in "$root"/opt/tcs/lib/lib*.so.[0-9]*; do
	[ -f "$library" ] || continue
	soname=${library##*/}
	echo "${soname%%.so.*} ${soname##*.so.} $package (>= ${version%-*})"
done > "$work/shlibs-$package"
if [ -s "$work/shlibs-$package" ]; then
	install -m 644 "$work/shlibs-$package" "$root/DEBIAN/shlibs"
fi

# Every file under /etc is a conffile, so that an upgrade never overwrites
# one somebody has edited. Found rather than listed, so that a hook added to
# `make install` is a conffile without a second list to remember it in.
if [ -d "$root/etc" ]; then
	(cd "$root" && find etc -type f | sed 's|^|/|' | sort) \
		> "$root/DEBIAN/conffiles"
fi

dpkg-deb --root-owner-group --build "$root" \
	"$out/${package}_${version}_${arch}.deb"
