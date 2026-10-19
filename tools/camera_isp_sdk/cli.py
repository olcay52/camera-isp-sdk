# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 T3 Foundation
"""camera-isp-sdk command line interface."""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__, board as board_mod, checks, dcc, descriptor, install, media, overlay, pipeline
from .conf import ConfError

DEFAULT_BOARD = "t3-gem-o1"
# Where the package installs the data root; it has no sensor or overlay sources
INSTALLED_ROOT = Path("/usr/share/camera-isp-sdk")


def data_root():
    env = os.environ.get("CAMERA_ISP_SDK_ROOT")
    if env:
        return Path(env)
    repo = Path(__file__).resolve().parents[2]
    if (repo / "sensors").is_dir() and (repo / "boards").is_dir():
        return repo
    return INSTALLED_ROOT


class Context:
    def __init__(self, args):
        # Absolute paths: overlays #include files by path from a build directory
        self.root = (Path(args.root) if args.root else data_root()).resolve()
        self.build_dir = (Path(args.build_dir) if args.build_dir else self.root / "build").resolve()
        self.board = board_mod.load(self.root / "boards" / args.board)
        self._sensors = None
        self.broken = {}

    @property
    def sensors(self):
        if self._sensors is None:
            self._sensors = descriptor.discover(self.root / "sensors", self.broken)
        return self._sensors

    def select(self, ids):
        if not ids:
            sensors = dict(self.sensors)
            for name, error in self.broken.items():
                print(f"camera-isp-sdk: warning: skipping {name}: {error}", file=sys.stderr)
            return sensors
        for i in ids:
            if i in self.sensors:
                continue
            if i in self.broken:
                raise self.broken[i]
        unknown = [i for i in ids if i not in self.sensors]
        if unknown:
            raise ConfError(f"unknown sensor(s): {', '.join(unknown)} "
                            f"(known: {', '.join(self.sensors) or 'none'})")
        return {i: self.sensors[i] for i in ids}


# --------------------------------------------------------------------------
# Host commands


def cmd_list(ctx, args):
    rows = [("ID", "STATUS", "MODE", "DCC ID", "PORTS", "NAME")]
    for s in ctx.select([]).values():
        m = s.mode
        rows.append((s.id, s.status, f"{m.width}x{m.height}@{m.fps} {m.code}", str(s.dcc_id),
                     ",".join(s.ports), s.name))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]) - 1)]
    for r in rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)) + "  " + r[-1])
    return 0


def cmd_validate(ctx, args):
    failed = False
    all_sensors = ctx.sensors
    for name, error in ctx.broken.items():
        if not args.sensors or name in args.sensors:
            print(f"{name}: FAIL\n  error: {error}")
            failed = True
    if args.sensors:
        selected = ctx.select([n for n in args.sensors if n not in ctx.broken])
    else:
        selected = all_sensors
    for sensor in selected.values():
        errors, warnings = checks.check(sensor, ctx.board, ctx.sensors)
        print(f"{sensor.id}: {'FAIL' if errors else 'ok'}")
        for e in errors:
            print(f"  error: {e}")
        for w in warnings:
            print(f"  warning: {w}")
        failed |= bool(errors)
    return 1 if failed else 0


def cmd_dcc_fetch(ctx, args):
    print(dcc.fetch_tools(args.source))
    return 0


def cmd_dcc_build(ctx, args):
    for sensor in ctx.select(args.sensors).values():
        if sensor.dcc_source is None:
            print(f"{sensor.id}: uses prebuilt DCC in {sensor.dcc_dir}, nothing to build")
            continue
        dcc.build(sensor, ctx.build_dir / "dcc" / sensor.id)
    return 0


def cmd_dcc_inspect(ctx, args):
    status = 0
    for path in args.files:
        components = dcc.inspect(path)
        ids = sorted({c.module_id for c in components})
        print(f"{path}: {len(components)} components, camera module ID(s) {ids}")
        for c in components:
            print(f"  @{c.offset:<6} id={c.module_id:<5} {c.descriptor:<24} {c.size} bytes")
        if args.dcc_id is not None and ids != [args.dcc_id]:
            print(f"  error: expected camera module ID {args.dcc_id} only")
            status = 1
    return status


def cmd_dcc_fit_h3a(ctx, args):
    sensor = ctx.select([args.sensor])[args.sensor]
    if sensor.dcc_source is None:
        raise ConfError(f"{sensor.id} has no [dcc] sources")
    for xml in dcc.source_xmls(sensor):
        path = sensor.dcc_source / xml
        grid = dcc.read_h3a(path)
        if grid is None:
            continue
        fitted = dcc.fit_h3a(grid, sensor.mode.width, sensor.mode.height)
        if fitted != grid:
            dcc.write_h3a(path, fitted)
            changed = {k: f"{grid[k]}->{fitted[k]}" for k in grid if grid[k] != fitted[k]}
            print(f"{path}: {changed}")
        else:
            print(f"{path}: already fits {sensor.mode.width}x{sensor.mode.height}")
    return 0


def cmd_dcc_fit_cfa(ctx, args):
    sensor = ctx.select([args.sensor])[args.sensor]
    if sensor.dcc_source is None:
        raise ConfError(f"{sensor.id} has no [dcc] sources")
    wanted = sensor.mode.bayer.upper()
    for xml in dcc.source_xmls(sensor):
        path = sensor.dcc_source / xml
        bayer = dcc.read_cfa_bayer(path)
        if bayer is None:
            continue
        if dcc.write_cfa_bayer(path, sensor.mode.bayer):
            print(f"{path}: {bayer.upper()} -> {wanted}")
        else:
            print(f"{path}: already {wanted}")
    return 0


def cmd_overlay_build(ctx, args):
    for sensor in ctx.select(args.sensors).values():
        for port in ([args.port] if args.port else sensor.ports):
            print(overlay.build(ctx.root, ctx.board, sensor, port, ctx.build_dir / "overlays"))
    return 0


def cmd_overlay_check(ctx, args):
    out = ctx.build_dir / "overlay-check"
    out.mkdir(parents=True, exist_ok=True)
    for sensor in ctx.select(args.sensors).values():
        dtbos = []
        for port in sensor.ports:
            dtbo = overlay.build(ctx.root, ctx.board, sensor, port, ctx.build_dir / "overlays")
            overlay.check_apply(args.base_dtb, [dtbo], out / f"{dtbo.stem}.dtb")
            dtbos.append(dtbo)
            print(f"{dtbo.name}: applies to {args.base_dtb}")
        if len(dtbos) > 1:
            overlay.check_apply(args.base_dtb, dtbos, out / f"{sensor.id}-all-ports.dtb")
            print(f"{sensor.id}: all ports apply together")
    return 0


def cmd_driver_build(ctx, args):
    kdir = Path(args.kdir).resolve()
    for sensor in ctx.select(args.sensors).values():
        if sensor.driver_dir is None:
            if args.sensors:
                print(f"{sensor.id}: driver is in the kernel, nothing to build")
            continue
        # Kbuild reads the Kbuild file of the driver directory; it needs no Makefile
        cmd = ["make", "-C", str(kdir), f"M={sensor.driver_dir}", f"ARCH={args.arch}",
               f"CROSS_COMPILE={args.cross_compile}", "modules"]
        if subprocess.call(cmd) != 0:
            print(f"camera-isp-sdk: error: {sensor.id}: building {sensor.driver_dir} failed", file=sys.stderr)
            return 1
        for ko in sorted(sensor.driver_dir.glob("*.ko")):
            print(f"{ko} (kernel {install.module_release(ko)})")
    return 0


def cmd_new(ctx, args):
    if not descriptor.ID_RE.match(args.id):
        raise ConfError(f"'{args.id}' may only contain letters, digits, '_' and '-'")
    if args.code not in descriptor.MBUS_CODES:
        raise ConfError(f"unsupported media bus code '{args.code}'")
    dest = ctx.root / "sensors" / args.id
    if dest.exists():
        raise ConfError(f"{dest} already exists")
    if not 1 <= args.dcc_id <= 0xFFFE:
        raise ConfError("--dcc-id must be within [1, 65534] (65535 matches any camera)")
    template = ctx.select([args.dcc_from])[args.dcc_from]
    if template.dcc_source is None:
        raise ConfError(f"{template.id} has no DCC XML sources to start from")

    bayer, bits = descriptor.MBUS_CODES[args.code]
    renames = dcc.bootstrap(template, args.id, args.dcc_id, dest / "dcc" / "linear",
                            args.width, args.height, bayer)
    bundles = "\n".join(f"{b}={';'.join(renames[x] for x in template.dcc_bundles[b])}"
                        for b in ("viss", "2a"))
    values = {
        "@ID@": args.id,
        "@NAME@": args.name or args.id,
        "@WIDTH@": str(args.width),
        "@HEIGHT@": str(args.height),
        "@CODE@": args.code,
        "@FPS@": str(args.fps),
        "@DCC_ID@": str(args.dcc_id),
        "@DCC_BUNDLES@": bundles,
        "@DCC_FROM@": template.id,
        "@DCC_BITS@": str(template.dcc_bits or ""),
    }
    for name in ("sensor.conf", "overlay.dtsi", "README.md"):
        text = (ctx.root / "sensors" / "_template" / name).read_text()
        for key, value in values.items():
            text = text.replace(key, value)
        (dest / name).write_text(text)

    print(f"created {dest}")
    if template.dcc_bits != bits:
        print(f"  warning: {template.id}'s DCC sources are for {template.dcc_bits}-bit data, {args.code} is "
              f"{bits}-bit. Replace the decompand, H3A mux LUT, CFA, NSF4 and black level components "
              "with ones made for this bit depth (docs/tuning.md), then set [dcc] bits.")
    print(f"  DCC sources copied from {template.id} with camera module ID {args.dcc_id}, the H3A grid")
    print(f"  fitted to {args.width}x{args.height} and the demosaic set for {bayer.upper()}; colour and AWB")
    print(f"  tuning are still {template.id}'s.")
    print("  Next: fill in the TODOs in sensor.conf and overlay.dtsi, then run")
    print(f"  'camera-isp-sdk validate {args.id}' (see docs/adding-a-sensor.md)")
    return 0


def cmd_install(ctx, args):
    install.stage(ctx.root, ctx.board, ctx.select(args.sensors), ctx.build_dir, args.destdir)
    return 0


def cmd_package(ctx, args):
    sensors = ctx.select(args.sensors)
    stage = ctx.build_dir / "stage"
    if stage.exists():
        shutil.rmtree(stage)
    install.stage(ctx.root, ctx.board, sensors, ctx.build_dir, stage)
    out = Path(args.output or ctx.build_dir / f"camera-isp-sdk-{__version__}-{ctx.board.id}.tar.gz")
    install.package(stage, out)
    print(out)
    return 0


# --------------------------------------------------------------------------
# Target commands


def _cameras(ctx, ids=None):
    return media.find_cameras(ctx.select(ids))


def cmd_setup(ctx, args):
    cameras = _cameras(ctx, args.sensors)
    if not cameras:
        print("no known sensor found in the media graphs; is the overlay in /boot/uEnv.txt "
              "and the driver loaded? ('camera-isp-sdk doctor')")
        return 1
    status = 0
    for cam in cameras:
        try:
            media.configure(cam, dry_run=args.dry_run, log=(print if args.verbose or args.dry_run else lambda _: None))
        except media.MediaError as e:
            print(f"error: {e}")
            status = 1
            continue
        s = cam.sensor
        print(f"{s.name}: camera {cam.index}")
        print(f"    device    = {cam.video_link} -> {cam.video}")
        print(f"    subdev    = {cam.subdev_link} -> {cam.subdev}")
        print(f"    format    = {s.mode.media_ctl_format}")
        print(f"    isp       = tiovxisp sensor-name={s.id} (DCC ID {s.dcc_id})")
    return status


def cmd_pipeline(ctx, args):
    sensor = ctx.select([args.sensor])[args.sensor]
    video = f"/dev/video-{sensor.id}-cam{args.cam}"
    subdev = f"/dev/v4l-{sensor.id}-subdev{args.cam}"
    scale = None
    if args.scale:
        m = re.fullmatch(r"(\d+)x(\d+)", args.scale)
        if not m:
            raise ConfError(f"--scale expects WIDTHxHEIGHT, got '{args.scale}'")
        scale = (int(m.group(1)), int(m.group(2)))
    frames = args.frames
    if frames is None and args.sink == "jpeg":
        frames = 90
    opts = pipeline.Options(sink=args.sink, output=args.output, frames=frames, scale=scale, ldc=args.ldc)
    cmd = pipeline.command(sensor, video, subdev, opts)
    if not args.run:
        print(pipeline.format_command(cmd))
        return 0
    env = dict(os.environ)
    env.setdefault("CAMERA_ISP_SDK_SENSOR_PATH", str(sensor.dir.parent))
    return subprocess.call(cmd, env=env)


def _check(ok, text):
    print(f"[{'ok' if ok else '!!'}] {text}")
    return ok


def gst_source_hash(gst_dir):
    """sha256 of the sources gst/build.sh builds the plugin from, computed as it does."""
    paths = [*sorted(Path(gst_dir).glob("src/camera_isp_sdk_sensor.[ch]")),
             *sorted(Path(gst_dir).glob("patches/*.patch"))]
    if not paths:
        return None
    h = hashlib.sha256()
    for path in paths:
        h.update(path.read_bytes())
    return h.hexdigest()


def _plugin_source_hash(inspect_output):
    """The hash build.sh stored next to the plugin gst-inspect-1.0 reports."""
    m = re.search(r"^\s*Filename\s+(\S+)", inspect_output, re.M)
    stamp = Path(f"{m.group(1)}.camera-isp-sdk-sha256") if m else None
    return stamp.read_text().strip() if stamp is not None and stamp.is_file() else None


def _module_hint(sensor):
    if sensor.driver_dir is not None:
        return (f"cross-build it with 'task drivers SENSORS={sensor.id}', package again and "
                "run 'depmod -a' after installing")
    return "see 'Install the driver' in the camera-isp-sdk README"


def cmd_doctor(ctx, args):
    healthy = True
    compat = Path("/proc/device-tree/compatible")
    soc = compat.read_bytes().replace(b"\0", b" ").decode(errors="replace").strip() if compat.exists() else ""
    healthy &= _check(f"ti,{ctx.board.soc}" in soc, f"SoC: {soc or 'unknown (not running on the board?)'}")

    chosen = Path("/proc/device-tree/chosen/overlays")
    loaded = sorted(p.name for p in chosen.iterdir()) if chosen.is_dir() else []
    cams = [o for o in loaded if "-csi" in o]
    healthy &= _check(bool(cams), f"camera overlays loaded: {', '.join(cams) or 'none (see /boot/uEnv.txt)'}")

    plugin_ok = False
    build_sh = ctx.root / "gst" / "build.sh"
    if shutil.which("gst-inspect-1.0"):
        res = subprocess.run(["gst-inspect-1.0", "tiovxisp"], capture_output=True, text=True)
        expected = gst_source_hash(ctx.root / "gst")
        if res.returncode != 0:
            detail = "missing (is the TI Edge AI stack installed?)"
        elif "camera-isp-sdk" not in res.stdout:
            detail = f"present but unpatched; run {build_sh}"
        elif expected is not None and _plugin_source_hash(res.stdout) != expected:
            detail = f"built from another version of the patch; run {build_sh}"
        else:
            plugin_ok = True
            detail = "patched for sensor descriptors"
    else:
        detail = "gst-inspect-1.0 not found"
    healthy &= _check(plugin_ok, f"tiovxisp: {detail}")

    try:
        cameras = _cameras(ctx)
    except media.MediaError as e:
        cameras = []
        print(f"[!!] media graph: {e}")
        healthy = False
    healthy &= _check(bool(cameras), f"sensors in media graph: "
                      f"{', '.join(f'{c.sensor.id} ({c.entity})' for c in cameras) or 'none'}")

    # An overlay without its sensor in the graph: no driver, or the probe failed
    found = {c.sensor.id for c in cameras}
    for s in ctx.sensors.values():
        if s.id in found or not any(re.search(rf"-csi\d+-{re.escape(s.id)}\b", o) for o in cams):
            continue
        module = s.module.replace("-", "_")
        installed = subprocess.run(["modinfo", module], capture_output=True).returncode == 0
        healthy &= _check(False, f"{s.id}: overlay loaded, sensor not in the media graph; " +
                          ("check 'dmesg' for probe errors" if installed else
                           f"kernel module {s.module} is not installed; {_module_hint(s)}"))

    for cam in cameras:
        s = cam.sensor
        module = s.module.replace("-", "_")
        healthy &= _check(Path(f"/sys/module/{module}").exists(), f"{s.id}: kernel module {s.module}")
        conf = INSTALLED_ROOT / "sensors" / s.id / "sensor.conf"
        healthy &= _check(conf.is_file() or "CAMERA_ISP_SDK_SENSOR_PATH" in os.environ,
                          f"{s.id}: descriptor visible to tiovxisp ({conf})")
        for bundle in ("viss", "2a"):
            path = s.dcc_path(bundle)
            if not Path(path).is_file():
                healthy &= _check(False, f"{s.id}: {path} missing")
                continue
            ids = sorted({c.module_id for c in dcc.inspect(path)})
            healthy &= _check(ids == [s.dcc_id], f"{s.id}: {path} camera module ID {ids}"
                              f"{'' if ids == [s.dcc_id] else f', descriptor says {s.dcc_id}'}")
        links = Path(cam.video_link).exists() and Path(cam.subdev_link).exists()
        healthy &= _check(links, f"{s.id}: {cam.video_link} ({'present' if links else 'run camera-isp-sdk setup'})")
    return 0 if healthy else 1


# --------------------------------------------------------------------------


def parser():
    p = argparse.ArgumentParser(prog="camera-isp-sdk", description=(
        "Bring camera sensors up on the TI AM67A hardware ISP (VPAC VISS) of the T3 Gemstone O1."))
    p.add_argument("--version", action="version", version=f"camera-isp-sdk {__version__}")
    p.add_argument("--root", help="data root holding sensors/ and boards/ (default: autodetect)")
    p.add_argument("--board", default=DEFAULT_BOARD, help=f"board id (default: {DEFAULT_BOARD})")
    p.add_argument("--build-dir", help="output directory (default: <root>/build)")
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    s = sub.add_parser("list", help="list sensors")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("validate", help="check descriptors, DCC sources and modes against the board")
    s.add_argument("sensors", nargs="*")
    s.set_defaults(func=cmd_validate, host=True)

    d = sub.add_parser("dcc", help="DCC tuning data").add_subparsers(dest="dcc_command", required=True,
                                                                    metavar="DCC_COMMAND")
    s = d.add_parser("fetch-tools", help="download and verify TI's dcc_gen_linux")
    s.add_argument("--from", dest="source", metavar="PATH",
                   help="install a dcc_gen_linux downloaded by hand, or the one in a directory "
                        "(imaging checkout, Processor SDK RTOS), instead of downloading it")
    s.set_defaults(func=cmd_dcc_fetch, host=True)
    s = d.add_parser("build", help="generate dcc_*.bin from XML sources")
    s.add_argument("sensors", nargs="*")
    s.set_defaults(func=cmd_dcc_build, host=True)
    s = d.add_parser("inspect", help="list the components and module IDs of DCC binaries")
    s.add_argument("files", nargs="+")
    s.add_argument("--dcc-id", type=int, help="fail unless every component has this ID")
    s.set_defaults(func=cmd_dcc_inspect)
    s = d.add_parser("fit-h3a", help="refit the H3A AEWB grid in the XML sources to [mode]")
    s.add_argument("sensor")
    s.set_defaults(func=cmd_dcc_fit_h3a, host=True)
    s = d.add_parser("fit-cfa", help="move the CFA (demosaic) coefficients in the XML sources to "
                                     "the Bayer order of [mode] code")
    s.add_argument("sensor")
    s.set_defaults(func=cmd_dcc_fit_cfa, host=True)

    o = sub.add_parser("overlay", help="device tree overlays").add_subparsers(dest="overlay_command",
                                                                             required=True, metavar="OVERLAY_COMMAND")
    s = o.add_parser("build", help="build .dtbo files")
    s.add_argument("sensors", nargs="*")
    s.add_argument("--port", help="only this port")
    s.set_defaults(func=cmd_overlay_build, host=True)
    s = o.add_parser("check", help="apply overlays to a base DTB with fdtoverlay")
    s.add_argument("--base-dtb", required=True)
    s.add_argument("sensors", nargs="*")
    s.set_defaults(func=cmd_overlay_check, host=True)

    r = sub.add_parser("driver", help="out-of-tree sensor drivers").add_subparsers(dest="driver_command",
                                                                                 required=True, metavar="DRIVER_COMMAND")
    s = r.add_parser("build", help="cross-build sensors/<id>/driver against the board's kernel build tree")
    s.add_argument("sensors", nargs="*")
    s.add_argument("--kdir", required=True, help="configured build tree of the board's kernel")
    s.add_argument("--arch", default="arm64")
    s.add_argument("--cross-compile", default="aarch64-linux-gnu-", help="toolchain prefix")
    s.set_defaults(func=cmd_driver_build, host=True)

    s = sub.add_parser("new", help="scaffold a new sensor")
    s.add_argument("id")
    s.add_argument("--name")
    s.add_argument("--width", type=int, required=True)
    s.add_argument("--height", type=int, required=True)
    s.add_argument("--code", required=True, help="media bus code, e.g. SRGGB10_1X10")
    s.add_argument("--fps", type=int, default=30)
    s.add_argument("--dcc-id", type=int, required=True, help="camera module ID for the DCC files")
    s.add_argument("--dcc-from", default="imx230", help="sensor whose DCC XML sources to start from")
    s.set_defaults(func=cmd_new, host=True)

    s = sub.add_parser("install", help="stage the install tree")
    s.add_argument("--destdir", required=True)
    s.add_argument("sensors", nargs="*")
    s.set_defaults(func=cmd_install, host=True)
    s = sub.add_parser("package", help="stage and pack a tarball to unpack over / on the board")
    s.add_argument("--output")
    s.add_argument("sensors", nargs="*")
    s.set_defaults(func=cmd_package, host=True)

    s = sub.add_parser("setup", help="(board) configure detected cameras and create /dev links")
    s.add_argument("sensors", nargs="*")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(func=cmd_setup)

    s = sub.add_parser("pipeline", help="(board) print or run a capture -> ISP -> sink pipeline")
    s.add_argument("sensor")
    s.add_argument("--cam", type=int, default=0)
    s.add_argument("--sink", choices=pipeline.SINKS, default="display")
    s.add_argument("--output")
    s.add_argument("--frames", type=int)
    s.add_argument("--scale", help="WIDTHxHEIGHT through tiovxmultiscaler")
    s.add_argument("--ldc", action="store_true", help="add lens distortion correction")
    s.add_argument("--run", action="store_true")
    s.set_defaults(func=cmd_pipeline)

    s = sub.add_parser("doctor", help="(board) diagnose the camera and ISP set-up")
    s.set_defaults(func=cmd_doctor)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        ctx = Context(args)
        if getattr(args, "host", False) and ctx.root == INSTALLED_ROOT.resolve():
            command = " ".join(filter(None, (args.command, getattr(args, "dcc_command", None),
                                             getattr(args, "overlay_command", None),
                                             getattr(args, "driver_command", None))))
            print(f"camera-isp-sdk: error: '{command}' works on the camera-isp-sdk source tree; run it on the host "
                  f"(the board has only the installed data in {INSTALLED_ROOT})", file=sys.stderr)
            return 1
        return args.func(ctx, args)
    except (ConfError, dcc.DccError, overlay.OverlayError, media.MediaError, FileNotFoundError, ValueError) as e:
        print(f"camera-isp-sdk: error: {e}", file=sys.stderr)
        return 1
