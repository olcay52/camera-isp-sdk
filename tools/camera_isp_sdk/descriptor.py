# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 T3 Foundation
"""Sensor descriptors: sensors/<id>/sensor.conf.

The format is documented in docs/sensor-descriptor.md. The [isp], [ae],
[exposure] and [gain] groups are also parsed at run time by the patched
tiovxisp element; keep the rules here in sync with gst/src/camera_isp_sdk_sensor.c.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from .conf import Conf, ConfError

ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
GAIN_UNITY = 1024
MAX_AE_RANGES = 10
STATUSES = ("supported", "experimental", "untested")
DCC_BUNDLES = ("viss", "2a", "ldc")

# media bus code -> (bayer order, bits per pixel)
MBUS_CODES = {}
for _bits in (8, 10, 12):
    for _order in ("BGGR", "GBRG", "GRBG", "RGGB"):
        MBUS_CODES[f"S{_order}{_bits}_1X{_bits}"] = (_order.lower(), _bits)


def gst_bayer_format(order, bits):
    """GStreamer video/x-bayer format name as accepted by tiovxisp."""
    return order if bits == 8 else f"{order}{bits}"


@dataclass
class Mode:
    width: int
    height: int
    code: str
    fps: int

    @property
    def bayer(self):
        return MBUS_CODES[self.code][0]

    @property
    def bits(self):
        return MBUS_CODES[self.code][1]

    @property
    def gst_format(self):
        return gst_bayer_format(self.bayer, self.bits)

    @property
    def media_ctl_format(self):
        return f"[fmt:{self.code}/{self.width}x{self.height} field:none]"


@dataclass
class AeRange:
    exposure_min_us: int
    exposure_max_us: int
    again_min: int
    again_max: int
    dgain_min: int
    dgain_max: int


@dataclass
class Sensor:
    id: str
    path: Path
    name: str
    status: str
    entity: str
    module: str
    driver_dir: Path | None
    mode: Mode
    lanes: int
    link_frequency: int
    clock_noncontinuous: bool
    dcc_id: int
    dcc_dir: str
    dcc_files: dict
    format_msb: int
    dcc_source: Path | None
    dcc_bits: int | None
    dcc_bundles: dict
    overlay: Path
    ports: list
    ae_ranges: list
    exposure_model: str
    gain_model: str
    notes: list = field(default_factory=list)

    @property
    def dir(self):
        return self.path.parent

    @property
    def has_ldc(self):
        return bool(self.dcc_files.get("ldc"))

    def dcc_path(self, bundle):
        name = self.dcc_files.get(bundle)
        return f"{self.dcc_dir}/{name}" if name else None


def _ae(conf):
    ranges = []
    for i in range(MAX_AE_RANGES):
        key = f"range{i}"
        if not conf.has("ae", key):
            break
        values = conf.int_list("ae", key)
        if len(values) != 6:
            raise ConfError(f"{conf.path}: [ae] {key}: expected 6 values: exposure-min-us;"
                            "exposure-max-us;again-min;again-max;dgain-min;dgain-max")
        r = AeRange(*values)
        if r.exposure_min_us > r.exposure_max_us or r.again_min > r.again_max or r.dgain_min > r.dgain_max:
            raise ConfError(f"{conf.path}: [ae] {key}: every min must be <= its max")
        if r.again_min < GAIN_UNITY:
            raise ConfError(f"{conf.path}: [ae] {key}: again-min must be >= {GAIN_UNITY} (1x)")
        ranges.append(r)
    if not ranges:
        raise ConfError(f"{conf.path}: [ae] range0: at least one AE range is required")

    lo = conf.int("ae", "target-brightness-min", 40, lo=0, hi=255)
    hi = conf.int("ae", "target-brightness-max", 50, lo=0, hi=255)
    target = conf.int("ae", "target-brightness", 45, lo=0, hi=255)
    if not lo <= target <= hi:
        raise ConfError(f"{conf.path}: [ae] target-brightness: {target} is outside "
                        "[target-brightness-min, target-brightness-max]")
    conf.int("ae", "threshold", 1, lo=0, hi=255)
    conf.int("ae", "exposure-step", 1, lo=1)
    conf.bool("ae", "enable-blc", True)
    return ranges


def _exposure(conf):
    model = conf.str("exposure", "model", "lines")
    if model == "lines":
        conf.int("exposure", "line-time-ns", 0, lo=0)
    elif model == "time":
        conf.int("exposure", "unit-ns", required=True, lo=1)
    else:
        raise ConfError(f"{conf.path}: [exposure] model: expected lines or time, got '{model}'")
    return model


def _gain(conf):
    model = conf.str("gain", "model", required=True)
    if model == "linear":
        if not conf.float("gain", "scale", 0.0):
            raise ConfError(f"{conf.path}: [gain] scale: required and non-zero for model=linear")
        conf.float("gain", "offset", 0.0)
    elif model == "inverse":
        if conf.float("gain", "k", 0.0) <= 0:
            raise ConfError(f"{conf.path}: [gain] k: required and > 0 for model=inverse")
    elif model == "db":
        if conf.float("gain", "step-db", 0.0) <= 0:
            raise ConfError(f"{conf.path}: [gain] step-db: required and > 0 for model=db")
    elif model == "table":
        points = conf.list("gain", "table", required=True)
        if not points:
            raise ConfError(f"{conf.path}: [gain] table: required for model=table")
        for point in points:
            parts = point.split(":")
            if len(parts) != 2:
                raise ConfError(f"{conf.path}: [gain] table: expected gain:code, got '{point}'")
            try:
                int(parts[0], 0), int(parts[1], 0)
            except ValueError:
                raise ConfError(f"{conf.path}: [gain] table: expected gain:code, got '{point}'") from None
    else:
        raise ConfError(f"{conf.path}: [gain] model: expected linear, inverse, db or table, got '{model}'")
    return model


def load(path):
    """Loads sensors/<id>/sensor.conf (or the directory containing it)."""
    path = Path(path)
    if path.is_dir():
        path = path / "sensor.conf"
    conf = Conf(path)
    sensor_dir = path.parent

    sid = conf.str("sensor", "id", required=True)
    if not ID_RE.match(sid):
        raise ConfError(f"{path}: [sensor] id: '{sid}' may only contain letters, digits, '_' and '-'")

    status = conf.str("sensor", "status", "untested")
    if status not in STATUSES:
        raise ConfError(f"{path}: [sensor] status: expected one of {', '.join(STATUSES)}")

    code = conf.str("mode", "code", required=True)
    if code not in MBUS_CODES:
        raise ConfError(f"{path}: [mode] code: unsupported media bus code '{code}' "
                        f"(expected one of {', '.join(sorted(MBUS_CODES))})")
    mode = Mode(
        width=conf.int("mode", "width", required=True, lo=1),
        height=conf.int("mode", "height", required=True, lo=1),
        code=code,
        fps=conf.int("mode", "fps", 30, lo=1),
    )

    driver = conf.str("sensor", "driver")
    driver_dir = (sensor_dir / driver) if driver else None

    dcc_files = {
        "viss": conf.str("isp", "dcc-viss", "dcc_viss.bin"),
        "2a": conf.str("isp", "dcc-2a", "dcc_2a.bin"),
        "ldc": conf.str("isp", "dcc-ldc", ""),
    }

    dcc_source = conf.str("dcc", "source")
    bundles = {}
    if dcc_source is not None:
        for bundle in DCC_BUNDLES:
            bundles[bundle] = conf.list("dcc", bundle)
        if not bundles["viss"] or not bundles["2a"]:
            raise ConfError(f"{path}: [dcc] viss and 2a component lists are required")
        if bundles["ldc"] and not dcc_files["ldc"]:
            dcc_files["ldc"] = "dcc_ldc.bin"

    sensor = Sensor(
        id=sid,
        path=path,
        name=conf.str("sensor", "name", sid),
        status=status,
        entity=conf.str("sensor", "entity", sid),
        module=conf.str("sensor", "module", sid),
        driver_dir=driver_dir,
        mode=mode,
        lanes=conf.int("csi", "lanes", required=True, lo=1, hi=4),
        link_frequency=conf.int("csi", "link-frequency", required=True, lo=1),
        clock_noncontinuous=conf.bool("csi", "clock-noncontinuous", False),
        dcc_id=conf.int("isp", "dcc-id", required=True, lo=0, hi=0xFFFF),
        dcc_dir=conf.str("isp", "dcc-dir", f"/opt/imaging/{sid}/linear"),
        dcc_files=dcc_files,
        format_msb=conf.int("isp", "format-msb", MBUS_CODES[code][1] - 1, lo=1, hi=15),
        dcc_source=(sensor_dir / dcc_source) if dcc_source else None,
        dcc_bits=conf.int("dcc", "bits", None, lo=8, hi=16),
        dcc_bundles=bundles,
        overlay=sensor_dir / conf.str("overlay", "source", "overlay.dtsi"),
        ports=conf.list("overlay", "ports"),
        ae_ranges=_ae(conf),
        exposure_model=_exposure(conf),
        gain_model=_gain(conf),
        notes=conf.list("sensor", "notes"),
    )
    return sensor


def discover(sensors_dir, broken=None):
    """Returns {id: Sensor} for every sensors/<id>/sensor.conf, skipping _template.

    Descriptors that fail to load raise, unless a dict is passed as broken:
    their errors are then collected there by directory name.
    """
    sensors = {}
    for conf in sorted(Path(sensors_dir).glob("*/sensor.conf")):
        if conf.parent.name.startswith("_"):
            continue
        try:
            sensor = load(conf)
            if sensor.id != conf.parent.name:
                raise ConfError(f"{conf}: [sensor] id '{sensor.id}' must match the directory name")
        except ConfError as e:
            if broken is None:
                raise
            broken[conf.parent.name] = e
            continue
        sensors[sensor.id] = sensor
    return sensors
