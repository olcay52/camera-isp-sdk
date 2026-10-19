# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 T3 Foundation
"""Unit tests for the camera-isp-sdk tool. Run: python3 -m unittest discover tools"""

import hashlib
import io
import os
import shutil
import struct
import subprocess
import tempfile
import textwrap
import unittest
import urllib.error
from dataclasses import replace
from pathlib import Path
from unittest import mock

from camera_isp_sdk import board as board_mod
from camera_isp_sdk import checks, cli, dcc, descriptor, install, media, overlay, pipeline
from camera_isp_sdk.conf import ConfError

ROOT = Path(__file__).resolve().parents[3]
BOARD = board_mod.load(ROOT / "boards" / "t3-gem-o1")

MINIMAL = """\
[sensor]
id=cam
[mode]
width=1920
height=1080
code=SRGGB10_1X10
[csi]
lanes=2
link-frequency=456000000
[overlay]
ports=csi0
[isp]
dcc-id=1234
[ae]
range0=100;33000;1024;8192;256;256
[gain]
model=inverse
k=256
"""


def write_sensor(tmp, text, name="cam"):
    d = Path(tmp) / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "sensor.conf").write_text(text)
    (d / "overlay.dtsi").write_text("#define CAM_DATA_LANES 1 2\n")
    return d


class DescriptorTest(unittest.TestCase):
    def test_shipped_sensors_load(self):
        sensors = descriptor.discover(ROOT / "sensors")
        self.assertIn("imx477", sensors)
        imx477 = sensors["imx477"]
        self.assertEqual(imx477.mode.gst_format, "rggb12")
        self.assertEqual(imx477.format_msb, 11)
        self.assertEqual(imx477.dcc_path("viss"), "/opt/imaging/imx477/linear/dcc_viss.bin")
        self.assertFalse(imx477.has_ldc)
        self.assertTrue(sensors["imx230"].has_ldc)
        ov5647 = sensors["ov5647"]
        self.assertEqual(ov5647.mode.gst_format, "bggr10")
        self.assertEqual(ov5647.format_msb, 9)

    def test_minimal_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = descriptor.load(write_sensor(tmp, MINIMAL))
            self.assertEqual(s.entity, "cam")
            self.assertEqual(s.mode.fps, 30)
            self.assertEqual(s.format_msb, 9)
            self.assertEqual(s.exposure_model, "lines")
            self.assertEqual(s.mode.media_ctl_format, "[fmt:SRGGB10_1X10/1920x1080 field:none]")

    def test_rejects(self):
        cases = {
            "bad id": MINIMAL.replace("id=cam", "id=../cam"),
            "bad code": MINIMAL.replace("SRGGB10_1X10", "UYVY8_1X16"),
            "no dcc id": MINIMAL.replace("dcc-id=1234\n", ""),
            "gain below 1x": MINIMAL.replace("100;33000;1024", "100;33000;512"),
            "short range": MINIMAL.replace("range0=100;33000;1024;8192;256;256", "range0=1;2;3"),
            "inverse without k": MINIMAL.replace("k=256\n", ""),
            "unknown gain model": MINIMAL.replace("model=inverse", "model=magic"),
            "inline comment": MINIMAL.replace("k=256", "k=256 # sony"),
        }
        for name, text in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(ConfError):
                    descriptor.load(write_sensor(tmp, text))


class ChecksTest(unittest.TestCase):
    def sensor(self, **mode):
        s = descriptor.discover(ROOT / "sensors")["imx477"]
        return replace(s, mode=replace(s.mode, **mode))

    def test_shipped_sensors_pass(self):
        sensors = descriptor.discover(ROOT / "sensors")
        for s in sensors.values():
            errors, _ = checks.check(s, BOARD, sensors)
            self.assertEqual(errors, [], s.id)

    def test_width_alignment(self):
        self.assertEqual(BOARD.width_alignment(8), 16)
        self.assertEqual(BOARD.width_alignment(12), 8)
        errors, _ = checks.check(self.sensor(width=2028, height=1520, fps=40), BOARD)
        self.assertTrue(any("multiple of 8" in e and "2024" in e for e in errors), errors)

    def test_bandwidth(self):
        errors, _ = checks.check(self.sensor(width=4056, height=3040, fps=60), BOARD)
        self.assertTrue(any("needs" in e and "Mbps" in e for e in errors), errors)

    def test_dcc_bit_depth(self):
        errors, _ = checks.check(self.sensor(code="SRGGB10_1X10"), BOARD)
        self.assertTrue(any("12-bit data" in e for e in errors), errors)

    def test_h3a_outside_frame(self):
        errors, _ = checks.check(self.sensor(width=1280, height=720), BOARD)
        self.assertTrue(any("H3A" in e for e in errors), errors)

    def test_cfa_bayer_order(self):
        errors, _ = checks.check(self.sensor(code="SBGGR12_1X12"), BOARD)
        self.assertTrue(any("CFA coefficients are for RGGB" in e and "fit-cfa" in e for e in errors), errors)

    def test_missing_port_header(self):
        ports = {**BOARD.ports, "csi0": replace(BOARD.ports["csi0"], header=Path("/nonexistent/csi0.h"))}
        errors, _ = checks.check(self.sensor(), replace(BOARD, ports=ports))
        self.assertTrue(any("csi0 header" in e for e in errors), errors)


class DccTest(unittest.TestCase):
    def test_fit_reproduces_imx230_grid(self):
        xml = ROOT / "sensors/imx230/dcc/linear/imx230_h3a_aewb_dcc.xml"
        grid = dcc.read_h3a(xml)
        self.assertEqual(dcc.fit_h3a(grid, 1920, 1080), grid)

    def test_fit_other_sizes(self):
        grid = dcc.read_h3a(ROOT / "sensors/imx230/dcc/linear/imx230_h3a_aewb_dcc.xml")
        for w, h in ((2016, 1520), (1328, 990), (4056, 3040), (640, 480)):
            with self.subTest(size=f"{w}x{h}"):
                self.assertEqual(dcc.check_h3a(dcc.fit_h3a(grid, w, h), w, h), [])

    def test_write_h3a_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            xml = Path(tmp) / "h3a.xml"
            shutil.copyfile(ROOT / "sensors/imx230/dcc/linear/imx230_h3a_aewb_dcc.xml", xml)
            fitted = dcc.fit_h3a(dcc.read_h3a(xml), 2016, 1520)
            dcc.write_h3a(xml, fitted)
            self.assertEqual(dcc.read_h3a(xml), fitted)
            self.assertIn("// h_size", xml.read_text().replace("h_size:", "h_size"))

    def test_bootstrap(self):
        template = descriptor.discover(ROOT / "sensors")["imx230"]
        with tempfile.TemporaryDirectory() as tmp:
            renames = dcc.bootstrap(template, "newcam", 4321, tmp, 2016, 1520)
            names = sorted(p.name for p in Path(tmp).iterdir())
            self.assertIn("newcam_cfa_dcc.xml", names)
            self.assertIn("lut_cfa_global.txt", names)
            self.assertNotIn("newcam_mesh_ldc_dcc.xml", names)  # lens specific
            self.assertEqual(renames["imx230_cfa_dcc.xml"], "newcam_cfa_dcc.xml")
            for xml in Path(tmp).glob("*.xml"):
                self.assertEqual(set(dcc.xml_module_ids(xml)), {4321}, xml.name)
            grid = dcc.read_h3a(Path(tmp) / "newcam_h3a_aewb_dcc.xml")
            self.assertEqual(dcc.check_h3a(grid, 2016, 1520), [])
            self.assertEqual(dcc.read_cfa_bayer(Path(tmp) / "newcam_cfa_dcc.xml"), "rggb")

    def test_bootstrap_bayer(self):
        template = descriptor.discover(ROOT / "sensors")["imx230"]
        with tempfile.TemporaryDirectory() as tmp:
            dcc.bootstrap(template, "newcam", 4321, tmp, 1920, 1080, "grbg")
            self.assertEqual(dcc.read_cfa_bayer(Path(tmp) / "newcam_cfa_dcc.xml"), "grbg")

    def test_cfa_bayer(self):
        sensors = descriptor.discover(ROOT / "sensors")
        for sid, name in (("imx230", "imx230_cfa_dcc.xml"), ("imx477", "imx477_cfa.xml"),
                          ("ov5647", "ov5647_cfa_dcc.xml")):
            with self.subTest(sid):
                s = sensors[sid]
                self.assertEqual(dcc.read_cfa_bayer(s.dcc_source / name), s.mode.bayer)
        self.assertIsNone(dcc.read_cfa_bayer(ROOT / "sensors/imx230/dcc/linear/imx230_viss_blc.xml"))

    def test_write_cfa_bayer(self):
        src = ROOT / "sensors/imx230/dcc/linear/imx230_cfa_dcc.xml"

        def kernels(path):
            _, fir = dcc._cfa_fir(path.read_text(), path)
            return [int(m.group(1)) for m in fir]

        with tempfile.TemporaryDirectory() as tmp:
            xml = Path(tmp) / "cfa.xml"
            shutil.copyfile(src, xml)
            rggb = kernels(xml)
            for order in ("bggr", "gbrg", "grbg"):
                with self.subTest(order):
                    self.assertTrue(dcc.write_cfa_bayer(xml, order))
                    self.assertEqual(dcc.read_cfa_bayer(xml), order)
                    self.assertFalse(dcc.write_cfa_bayer(xml, order))
                    # The red pixel's kernel moves to the red phase of the new order
                    moved, red = kernels(xml), dcc.BAYER_RED_PHASE[order]
                    self.assertEqual(moved[2 * 144 + red * 36:2 * 144 + red * 36 + 36], rggb[2 * 144:2 * 144 + 36])
                    self.assertTrue(dcc.write_cfa_bayer(xml, "rggb"))
                    self.assertEqual(xml.read_text(), src.read_text())

    def test_cfa_unknown_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            xml = Path(tmp) / "cfa.xml"
            xml.write_text('<cfg_flxd_cfa_dcc type="cfg_flxd_cfa_vars">{' + "1, " * 1784 + "}</cfg_flxd_cfa_dcc>")
            with self.assertRaisesRegex(dcc.DccError, "cannot tell"):
                dcc.read_cfa_bayer(xml)
            xml.write_text('<cfg_flxd_cfa_dcc type="cfg_flxd_cfa_vars">{1, 2}</cfg_flxd_cfa_dcc>')
            with self.assertRaisesRegex(dcc.DccError, "expected at least"):
                dcc.read_cfa_bayer(xml)

    def test_module_id(self):
        text = "<camera_module_id>     390 </camera_module_id>"
        self.assertEqual(dcc.set_module_id(text, 477), "<camera_module_id>     477 </camera_module_id>")

    def test_inspect(self):
        def component(module_id, descriptor_id, payload):
            size = 80 + len(payload)
            words = [module_id, descriptor_id] + [0] * 17 + [size]
            return struct.pack("<20I", *words) + payload

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dcc.bin"
            path.write_bytes(component(477, 1, b"x" * 12) + component(477, 9, b""))
            parts = dcc.inspect(path)
            self.assertEqual([(c.module_id, c.descriptor, c.size) for c in parts],
                             [(477, "VISS_BLC", 92), (477, "VISS_CFA", 80)])
            path.write_bytes(component(477, 1, b"")[:-4])
            with self.assertRaises(dcc.DccError):
                dcc.inspect(path)


class FetchToolsTest(unittest.TestCase):
    TOOL = b"\x7fELF stand-in for dcc_gen_linux"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.cache = self.tmp / "cache"
        for patch in (mock.patch.dict(os.environ, {"CAMERA_ISP_SDK_DCC_TOOLS": str(self.cache)}),
                      mock.patch.object(dcc, "TI_TOOL_SHA256", hashlib.sha256(self.TOOL).hexdigest()),
                      mock.patch.object(dcc, "DOWNLOAD_RETRY_DELAYS", (0, 0))):
            patch.start()
            self.addCleanup(patch.stop)

    def urlopen(self, *responses):
        patch = mock.patch("urllib.request.urlopen", side_effect=list(responses))
        self.addCleanup(patch.stop)
        return patch.start()

    @staticmethod
    def http_error(code):
        return urllib.error.HTTPError(dcc.TI_TOOL_URL, code, "error", {}, None)

    def fetch(self, source=None):
        return dcc.fetch_tools(source, log=lambda *_: None)

    def assert_installed(self):
        tool = dcc.require_tools()
        self.assertEqual(tool.read_bytes(), self.TOOL)
        self.assertTrue(os.access(tool, os.X_OK))
        self.assertEqual([p.name for p in self.cache.iterdir()], [dcc.TI_TOOL])

    def test_from_checkout(self):
        for layout in ("tools/dcc_tools", "imaging/tools/dcc_tools", ""):
            with self.subTest(layout=layout):
                shutil.rmtree(self.cache, ignore_errors=True)
                src = self.tmp / "src" / layout / dcc.TI_TOOL
                src.parent.mkdir(parents=True, exist_ok=True)
                src.write_bytes(self.TOOL)
                self.fetch(self.tmp / "src")
                self.assert_installed()
                shutil.rmtree(self.tmp / "src")

    def test_from_wrong_file(self):
        src = self.tmp / dcc.TI_TOOL
        src.write_bytes(b"something else")
        with self.assertRaisesRegex(dcc.DccError, "checksum mismatch"):
            self.fetch(src)
        self.assertEqual(list(self.cache.iterdir()), [])
        with self.assertRaisesRegex(dcc.DccError, "no dcc_gen_linux"):
            self.fetch(self.tmp / "empty")

    def test_download_retries_server_errors(self):
        urlopen = self.urlopen(self.http_error(503), OSError("timed out"), io.BytesIO(self.TOOL))
        self.fetch()
        self.assertEqual(urlopen.call_count, 3)
        self.assert_installed()
        self.fetch()  # verified copy is kept
        self.assertEqual(urlopen.call_count, 3)

    def test_download_gives_up(self):
        urlopen = self.urlopen(*[self.http_error(503)] * 3)
        with self.assertRaisesRegex(dcc.DccError, "fetch-tools --from"):
            self.fetch()
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual(list(self.cache.iterdir()), [])

        urlopen = self.urlopen(self.http_error(404))
        with self.assertRaises(dcc.DccError):
            self.fetch()
        self.assertEqual(urlopen.call_count, 1)


TOPOLOGY = textwrap.dedent('''\
    Media controller API version 6.12.24

    Device topology
    - entity 1: 30102000.ticsi2rx (7 pads, 7 links, 1 route)
                type V4L2 subdev subtype Unknown flags 0
                device node name /dev/v4l-subdev0
    \troutes:
    \t\t0/0 -> 1/0 [ACTIVE]
    \tpad0: Sink
    \t\t[stream:0 fmt:SRGGB12_1X12/1920x1080 field:none]
    \t\t<- "cdns_csi2rx.30101000.csi-bridge":1 [ENABLED,IMMUTABLE]
    \tpad1: Source
    \t\t-> "30102000.ticsi2rx context 0":0 [ENABLED,IMMUTABLE]

    - entity 9: cdns_csi2rx.30101000.csi-bridge (5 pads, 2 links, 1 route)
                type V4L2 subdev subtype Unknown flags 0
                device node name /dev/v4l-subdev1
    \tpad0: Sink
    \t\t<- "imx477 2-001a":0 [ENABLED,IMMUTABLE]
    \tpad1: Source
    \t\t-> "30102000.ticsi2rx":0 [ENABLED,IMMUTABLE]

    - entity 15: imx477 2-001a (1 pad, 1 link, 0 routes)
                 type V4L2 subdev subtype Sensor flags 0
                 device node name /dev/v4l-subdev2
    \tpad0: Source
    \t\t[stream:0 fmt:SRGGB12_1X12/4056x3040 field:none colorspace:raw
    \t\t crop:(8,16)/4056x3040]
    \t\t-> "cdns_csi2rx.30101000.csi-bridge":0 [ENABLED,IMMUTABLE]

    - entity 21: 30102000.ticsi2rx context 0 (1 pad, 1 link)
                 type Node subtype V4L flags 0
                 device node name /dev/video2
    \tpad0: Sink
    \t\t<- "30102000.ticsi2rx":1 [ENABLED,IMMUTABLE]
    ''')


class MediaTest(unittest.TestCase):
    def test_parse(self):
        entities = media.parse_topology(TOPOLOGY)
        sensor = entities["imx477 2-001a"]
        self.assertEqual(sensor.devnode, "/dev/v4l-subdev2")
        self.assertEqual(sensor.pads[0].fmt, ("SRGGB12_1X12", 4056, 3040))
        self.assertEqual(sensor.remote(0).entity, "cdns_csi2rx.30101000.csi-bridge")
        self.assertEqual(entities["30102000.ticsi2rx context 0"].devnode, "/dev/video2")

    def test_parse_uppercase_pads(self):
        # v4l-utils 1.28 media-ctl spells the pad direction in upper case.
        upper = TOPOLOGY.replace("pad0: Sink", "pad0: SINK").replace(
            "pad0: Source", "pad0: SOURCE").replace("pad1: Source", "pad1: SOURCE")
        self.assertNotIn(": Source", upper)
        entities = media.parse_topology(upper)
        self.assertEqual(entities["imx477 2-001a"].pads[0].kind, "Source")
        self.assertEqual(entities["imx477 2-001a"].remote(0).entity,
                         "cdns_csi2rx.30101000.csi-bridge")

    def test_find_cameras(self):
        sensors = descriptor.discover(ROOT / "sensors")
        original = media._media_ctl
        media._media_ctl = lambda device, *args: TOPOLOGY
        try:
            cams = media.find_cameras(sensors, ["/dev/media0"])
        finally:
            media._media_ctl = original
        self.assertEqual(len(cams), 1)
        cam = cams[0]
        self.assertEqual((cam.sensor.id, cam.bridge, cam.receiver, cam.video),
                         ("imx477", "cdns_csi2rx.30101000.csi-bridge", "30102000.ticsi2rx", "/dev/video2"))
        self.assertEqual(cam.video_link, "/dev/video-imx477-cam0")


class PipelineTest(unittest.TestCase):
    def test_display(self):
        s = descriptor.discover(ROOT / "sensors")["imx477"]
        cmd = " ".join(pipeline.command(s, "/dev/video-imx477-cam0", "/dev/v4l-imx477-subdev0",
                                        pipeline.Options()))
        self.assertIn("video/x-bayer,width=1920,height=1080,format=rggb12,framerate=50/1", cmd)
        self.assertIn("tiovxisp sensor-name=imx477 dcc-isp-file=/opt/imaging/imx477/linear/dcc_viss.bin "
                      "format-msb=11", cmd)
        self.assertIn("sink_0::device=/dev/v4l-imx477-subdev0", cmd)
        self.assertTrue(cmd.endswith("kmssink driver-name=tidss sync=false"))

    def test_ldc_requires_tuning(self):
        s = descriptor.discover(ROOT / "sensors")["imx477"]
        with self.assertRaises(ValueError):
            pipeline.command(s, "v", "s", pipeline.Options(ldc=True))


VERMAGIC = b"\0vermagic=6.12.24-ti SMP preempt_rt mod_unload aarch64\0"


class InstallTest(unittest.TestCase):
    def test_module_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            ko = Path(tmp) / "cam.ko"
            ko.write_bytes(b"\x7fELF\0license=GPL" + VERMAGIC)
            self.assertEqual(install.module_release(ko), "6.12.24-ti")
            ko.write_bytes(b"\x7fELF\0license=GPL\0")
            with self.assertRaises(ValueError):
                install.module_release(ko)

    def test_stage(self):
        s = descriptor.discover(ROOT / "sensors")["imx219"]
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for port in s.ports:
                dtbo = tmp / "build/overlays" / f"{BOARD.overlay_name(port, s.id)}.dtbo"
                dtbo.parent.mkdir(parents=True, exist_ok=True)
                dtbo.write_bytes(b"")
            (tmp / "driver").mkdir()
            (tmp / "driver/imx219.ko").write_bytes(VERMAGIC)
            sensors = {s.id: replace(s, driver_dir=tmp / "driver")}
            install.stage(ROOT, BOARD, sensors, tmp / "build", tmp / "dest", log=lambda *a: None)

            share = tmp / "dest/usr/share/camera-isp-sdk"
            self.assertEqual(
                sorted(str(p.relative_to(share)) for p in share.rglob("*") if p.is_file()),
                ["boards/t3-gem-o1/board.conf", "gst/build.sh",
                 *(f"gst/patches/{p.name}" for p in sorted((ROOT / "gst/patches").glob("*.patch"))),
                 "gst/src/camera_isp_sdk_sensor.c", "gst/src/camera_isp_sdk_sensor.h", "sensors/imx219/sensor.conf"])
            self.assertTrue((tmp / "dest/lib/modules/6.12.24-ti/updates/imx219.ko").is_file())


class CliTest(unittest.TestCase):
    def test_host_commands_refused_on_installed_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            # The installed data root: board.conf without the port headers
            (Path(tmp) / "boards/t3-gem-o1").mkdir(parents=True)
            shutil.copy(ROOT / "boards/t3-gem-o1/board.conf", Path(tmp) / "boards/t3-gem-o1")
            (Path(tmp) / "sensors").mkdir()
            err = io.StringIO()
            with mock.patch.object(cli, "INSTALLED_ROOT", Path(tmp)), mock.patch("sys.stderr", err), \
                    mock.patch("sys.stdout", io.StringIO()):
                self.assertEqual(cli.main(["--root", tmp, "overlay", "build"]), 1)
                self.assertEqual(cli.main(["--root", tmp, "list"]), 0)
            self.assertIn("'overlay build' works on the camera-isp-sdk source tree", err.getvalue())

    def test_driver_build(self):
        out = io.StringIO()
        with mock.patch.object(cli.subprocess, "call", return_value=0) as call, mock.patch("sys.stdout", out):
            self.assertEqual(cli.main(["--root", str(ROOT), "driver", "build", "--kdir", "/k", "imx477", "ov5647"]), 0)
        call.assert_called_once_with(["make", "-C", "/k", f"M={ROOT / 'sensors/imx477/driver'}", "ARCH=arm64",
                                      "CROSS_COMPILE=aarch64-linux-gnu-", "modules"])
        self.assertIn("ov5647: driver is in the kernel", out.getvalue())

    def test_plugin_source_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = Path(tmp) / "libgsttiovx.so"
            inspect = f"Plugin Details:\n  Name                     tiovx\n  Filename                 {plugin}\n"
            self.assertIsNone(cli._plugin_source_hash(inspect))
            Path(f"{plugin}.camera-isp-sdk-sha256").write_text("abc\n")
            self.assertEqual(cli._plugin_source_hash(inspect), "abc")

    @unittest.skipUnless(shutil.which("bash") and shutil.which("sha256sum"), "bash and sha256sum are required")
    def test_gst_source_hash_matches_build_sh(self):
        script = 'source <(sed -n "/^source_hash()/,/^}/p" "$1"); source_hash'
        res = subprocess.run(["bash", "-c", script, "bash", str(ROOT / "gst/build.sh")],
                             env={**os.environ, "HERE": str(ROOT / "gst")},
                             capture_output=True, text=True, check=True)
        self.assertEqual(res.stdout.strip(), cli.gst_source_hash(ROOT / "gst"))


@unittest.skipUnless(shutil.which("cpp") and shutil.which("dtc"), "cpp and dtc are required")
class OverlayTest(unittest.TestCase):
    def test_build_all(self):
        sensors = descriptor.discover(ROOT / "sensors")
        with tempfile.TemporaryDirectory() as tmp:
            for s in sensors.values():
                for port in s.ports:
                    dtbo = overlay.build(ROOT, BOARD, s, port, tmp)
                    self.assertGreater(dtbo.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
