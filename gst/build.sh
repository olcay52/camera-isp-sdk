#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 T3 Foundation
#
# Builds TI's edgeai-gst-plugins with camera-isp-sdk sensor descriptor support and
# installs it over the stock libgsttiovx. Runs on the board (aarch64, Ubuntu
# 22.04, TI Edge AI SDK 11.0), like TI's install_gst_plugins.sh.
#
#   sudo ./build.sh            build and install, keeping a backup
#   sudo ./build.sh --restore  put the stock plugin back
#   ./build.sh --no-install    only build

set -Eeuo pipefail

# TI tags nothing for Edge AI 11.0 on GitHub; this is the commit whose tree
# matches /opt/edgeai-gst-plugins of the 11.0 image.
GST_PLUGINS_URL="https://github.com/TexasInstruments/edgeai-gst-plugins.git"
GST_PLUGINS_REV="39577530481ba0efe5eb0e20becb6442d84d29ac"

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="${SRC_DIR:-/opt/edgeai-gst-plugins-camera-isp-sdk}"
SOC="${SOC:-j722s}"
BUILDTYPE="release"
INSTALL=1
RESTORE=0

usage() {
	sed -n '/^# Builds/,/^$/p' "$0" | sed 's/^# \{0,1\}//'
	echo "Options: --src-dir DIR  --debug  --no-install  --restore"
}

die() {
	echo "build.sh: $*" >&2
	exit 1
}

while [[ $# -gt 0 ]]; do
	case "$1" in
	--src-dir) SRC_DIR="$2"; shift ;;
	--debug) BUILDTYPE="debug" ;;
	--no-install) INSTALL=0 ;;
	--restore) RESTORE=1 ;;
	-h | --help) usage; exit 0 ;;
	*) usage; die "unknown option: $1" ;;
	esac
	shift
done

plugin_file() {
	gst-inspect-1.0 tiovxisp 2>/dev/null | awk '/^  Filename/ { print $2 }'
}

# What the plugin is built from; stored next to it as <plugin>.camera-isp-sdk-sha256
# so that 'camera-isp-sdk doctor' can tell when a new package needs a rebuild.
source_hash() {
	cat "$HERE"/src/camera_isp_sdk_sensor.[ch] "$HERE"/patches/*.patch | sha256sum | cut -d' ' -f1
}

if [[ "$RESTORE" -eq 1 ]]; then
	plugin="$(plugin_file)"
	[[ -n "$plugin" ]] || die "tiovxisp is not installed"
	[[ -f "$plugin.camera-isp-sdk-orig" ]] || die "no backup at $plugin.camera-isp-sdk-orig"
	cp -a "$plugin.camera-isp-sdk-orig" "$plugin"
	rm -f "$plugin.camera-isp-sdk-sha256"
	echo "restored $plugin"
	exit 0
fi

[[ "$(uname -m)" == "aarch64" ]] ||
	die "run this on the board: the build needs the TI Edge AI headers and libraries"

for tool in git meson ninja; do
	command -v "$tool" >/dev/null || die "$tool is required"
done

# The checkout is reset on every run, so only ever use one this script made.
if [[ ! -e "$SRC_DIR" ]]; then
	git clone --quiet "$GST_PLUGINS_URL" "$SRC_DIR"
	touch "$SRC_DIR/.git/camera-isp-sdk"
elif [[ ! -f "$SRC_DIR/.git/camera-isp-sdk" ]]; then
	die "$SRC_DIR was not created by this script; remove it or pass --src-dir"
fi

cd "$SRC_DIR"
git cat-file -e "$GST_PLUGINS_REV^{commit}" 2>/dev/null || git fetch --quiet origin
git checkout --quiet --force -B camera-isp-sdk "$GST_PLUGINS_REV"
git clean --quiet -fdx -e build

cp "$HERE"/src/camera_isp_sdk_sensor.[ch] ext/tiovx/
for patch in "$HERE"/patches/*.patch; do
	git apply --index "$patch" || die "cannot apply $(basename "$patch")"
	echo "applied $(basename "$patch")"
done

rm -rf build
# Install next to the GStreamer the board runs: TI's images have it in /usr,
# the T3 image in /usr/local, and GStreamer only loads plugins from its own
# prefix.
PREFIX="${PREFIX:-$(pkg-config --variable=prefix gstreamer-1.0 2>/dev/null || echo /usr)}"
SOC="$SOC" meson setup build --prefix="$PREFIX" -Dpkg_config_path=pkgconfig \
	--buildtype="$BUILDTYPE"
ninja -C build

if [[ "$INSTALL" -eq 0 ]]; then
	echo "built $SRC_DIR/build (not installed)"
	exit 0
fi

[[ "$(id -u)" -eq 0 ]] || die "installing needs root; rerun with sudo or pass --no-install"

plugin="$(plugin_file)"
if [[ -n "$plugin" && ! -f "$plugin.camera-isp-sdk-orig" ]]; then
	cp -a "$plugin" "$plugin.camera-isp-sdk-orig"
	echo "saved the stock plugin as $plugin.camera-isp-sdk-orig"
fi

ninja -C build install
ldconfig

# Not 'gst-inspect | grep -q': grep exits on the first match, gst-inspect
# gets SIGPIPE and pipefail turns that into a failure.
inspect="$(gst-inspect-1.0 tiovxisp 2>/dev/null || true)"
grep -q "camera-isp-sdk" <<<"$inspect" ||
	die "tiovxisp does not report camera-isp-sdk support after the install"
source_hash >"$(plugin_file).camera-isp-sdk-sha256"
echo "tiovxisp now accepts camera-isp-sdk sensor descriptors"
