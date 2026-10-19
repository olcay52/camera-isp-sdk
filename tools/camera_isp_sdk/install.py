# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 T3 Foundation
"""Install tree for the target.

Layout (relative to DESTDIR):

  usr/bin/camera-isp-sdk                         command line tool
  usr/lib/camera-isp-sdk/camera_isp_sdk/                 its Python package
  usr/share/camera-isp-sdk/sensors/<id>/         sensor.conf (read by tiovxisp)
  usr/share/camera-isp-sdk/boards/<board>/       board.conf
  usr/share/camera-isp-sdk/gst/                  build.sh with the plugin patch and sources
  opt/imaging/<id>/linear/dcc_*.bin       DCC tuning, where TI keeps its own
  boot/overlays/*.dtbo                    device tree overlays
  lib/modules/<kernel>/updates/*.ko       out-of-tree drivers built by 'task drivers'

Overlay and driver sources are not packed, only what is built from them.
"""

import re
import shutil
import tarfile
from pathlib import Path

LAUNCHER = """#!/usr/bin/env python3
import sys
sys.path.insert(0, "/usr/lib/camera-isp-sdk")
from camera_isp_sdk.cli import main
sys.exit(main())
"""


def _copy(src, dst, mode=0o644):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    dst.chmod(mode)


def module_release(ko):
    """Kernel release a module was built for, from the vermagic in its .modinfo."""
    m = re.search(rb"(?:^|\0)vermagic=([^\0 ]+)", Path(ko).read_bytes())
    if m is None:
        raise ValueError(f"{ko} has no vermagic; is it a kernel module?")
    return m.group(1).decode()


def stage(root, board, sensors, build_dir, destdir, log=print):
    root, build_dir, destdir = Path(root), Path(build_dir), Path(destdir)
    share = destdir / "usr/share/camera-isp-sdk"

    lib = destdir / "usr/lib/camera-isp-sdk/camera_isp_sdk"
    for py in sorted((root / "tools/camera_isp_sdk").glob("*.py")):
        _copy(py, lib / py.name)
    launcher = destdir / "usr/bin/camera-isp-sdk"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(LAUNCHER)
    launcher.chmod(0o755)

    _copy(board.dir / "board.conf", share / "boards" / board.id / "board.conf")

    gst = root / "gst"
    _copy(gst / "build.sh", share / "gst/build.sh", 0o755)
    for path in sorted([*(gst / "src").glob("*.[ch]"), *(gst / "patches").glob("*.patch")]):
        _copy(path, share / "gst" / path.relative_to(gst))

    for sensor in sensors.values():
        _copy(sensor.path, share / "sensors" / sensor.id / sensor.path.name)

        if sensor.dcc_source is not None:
            built = build_dir / "dcc" / sensor.id
            dcc_dir = destdir / sensor.dcc_dir.lstrip("/")
            for bundle, name in sensor.dcc_files.items():
                if not name:
                    continue
                if not (built / name).is_file():
                    raise FileNotFoundError(f"{built / name} is missing; run 'camera-isp-sdk dcc build {sensor.id}'")
                _copy(built / name, dcc_dir / name)

        for port in sensor.ports:
            name = board.overlay_name(port, sensor.id)
            dtbo = build_dir / "overlays" / f"{name}.dtbo"
            if not dtbo.is_file():
                raise FileNotFoundError(f"{dtbo} is missing; run 'camera-isp-sdk overlay build {sensor.id}'")
            _copy(dtbo, destdir / "boot/overlays" / dtbo.name)

        if sensor.driver_dir is not None:
            modules = sorted(sensor.driver_dir.glob("*.ko"))
            if not modules:
                log(f"{sensor.id}: driver not built, left out of the package (task drivers SENSORS={sensor.id})")
            for ko in modules:
                _copy(ko, destdir / "lib/modules" / module_release(ko) / "updates" / ko.name)

    log(f"staged into {destdir}")
    return destdir


def package(destdir, out_file):
    """Packs a staged tree into a tarball to unpack over / on the board."""
    destdir = Path(destdir)
    with tarfile.open(out_file, "w:gz") as tar:
        for path in sorted(destdir.rglob("*")):
            # No directory entries: unpacked over / as root, they would reset
            # the mode and mtime of /usr, /usr/bin, /opt... to the host's
            # (775 with umask 002). tar creates missing parents itself.
            if path.is_dir():
                continue
            info = tar.gettarinfo(str(path), arcname=str(path.relative_to(destdir)))
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            info.mtime = 0
            with open(path, "rb") as f:
                tar.addfile(info, f)
    return out_file
