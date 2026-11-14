# shellcheck shell=sh
# Puts the tcsxtce package's pkg-config files where a login shell finds
# them. The directory is added only if it is not there already, so this
# file and the other TCS packages' can all be sourced, in any order and any
# number of times, without the path growing.

case ":${PKG_CONFIG_PATH:-}:" in
*:/opt/tcs/lib/pkgconfig:*)
	;;
*)
	PKG_CONFIG_PATH="${PKG_CONFIG_PATH:+${PKG_CONFIG_PATH}:}"
	PKG_CONFIG_PATH="${PKG_CONFIG_PATH}/opt/tcs/lib/pkgconfig"
	;;
esac
export PKG_CONFIG_PATH
