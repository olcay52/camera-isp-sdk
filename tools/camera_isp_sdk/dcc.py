# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 T3 Foundation
"""DCC (Dynamic Camera Configuration) tuning data.

The VISS and 2A blocks of TI's imaging stack are configured from DCC
binaries generated out of XML files by TI's dcc_gen_linux. Every component
carries the camera module ID in its header and TI's parser rejects
components whose ID differs from the sensor's DCC ID, leaving the ISP on
defaults while the pipeline keeps running, so most of this
module is about keeping that ID, the H3A statistics grid and the Bayer
phases of the demosaic consistent with the sensor descriptor.
"""

import hashlib
import http.client
import os
import re
import shutil
import struct
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

TI_IMAGING_REF = "REL.IMAGING.11.00.00.00"
TI_TOOL = "dcc_gen_linux"
TI_TOOL_URL = (f"https://git.ti.com/cgit/processor-sdk/imaging/plain/tools/dcc_tools/{TI_TOOL}"
               f"?h={TI_IMAGING_REF}")
TI_TOOL_SHA256 = "3bc23ebbc8eb37c662eea3b0190b64787ff7cd92e822189f0d09de504d951024"
# Where the tool sits in an imaging checkout or a Processor SDK RTOS install
TI_TOOL_SUBDIRS = ("", "tools/dcc_tools", "imaging/tools/dcc_tools")
# git.ti.com answers 5xx for minutes at a time; seconds to wait between attempts
DOWNLOAD_RETRY_DELAYS = (2, 5, 15)

# Component descriptor IDs seen in TI's DCC XML headers
DESCRIPTORS = {
    1: "VISS_BLC",
    2: "VISS_H3A_MUX_LUTS_CFG",
    3: "VISS_H3A_AEWB_CFG",
    4: "VISS_RAWFE_DECOMPAND",
    5: "LDC_MESH",
    9: "VISS_CFA",
    10: "VISS_RGB2RGB",
    12: "VISS_GAMMA",
    21: "VISS_NSF4",
    40: "AWB_ALG_TI3_TUNING",
}

HEADER_WORDS = 20
HEADER_SIZE_WORD = 19

MODULE_ID_RE = re.compile(r"(<camera_module_id>\s*)(\d+)(\s*</camera_module_id>)")
H3A_BLOCK_RE = re.compile(r"(<h3a_aewb_dcc\b[^>]*>\s*\{)(.*?)(\}\s*</h3a_aewb_dcc>)", re.S)
H3A_FIELDS = (
    "enable", "mode", "v_start", "h_start", "v_size", "h_size", "v_count", "h_count",
    "v_skip", "h_skip", "saturation_limit", "blk_win_numlines", "blk_row_vpos",
    "sum_shift", "alaw_en", "medfilt_en",
)

# TI's files name the parameter element differently but give it this type
CFA_BLOCK_RE = re.compile(r'<(?P<tag>\w+)\s[^>]*type="cfg_flxd_cfa_vars"[^>]*>\s*\{(?P<body>.*?)\}\s*</(?P=tag)>',
                          re.S)
# Integer literals of a C initializer; comments, strings and #include lines
# match too so that the digits in them are skipped.
C_TOKEN_RE = re.compile(r'//[^\n]*|/\*.*?\*/|"[^"]*"|#[^\n]*|(?<![\w.])(-?\d+)(?![\w.])', re.S)
# cfg_flxd_cfa_vars: 56 values (image_bits, lut_enable, the Set0/Set1 masks
# and thresholds, blendMode, bitMaskSel) precede FirCoefs[12], which hold
# a 6x6 kernel for each phase (y % 2 * 2 + x % 2) of the 2x2 Bayer cell.
CFA_FIR_OFFSET = 56
CFA_SETS, CFA_PHASES, CFA_TAPS = 12, 4, 36
# Kernels are relative to the output pixel, which is tap 14 (row 2, column 2)
CFA_SELF_TAP = 14
# FirCoef 2 (RN) and 8 (BN) take red and blue from the pixel itself only at
# the red and blue phases
CFA_RED_SET, CFA_BLUE_SET = 2, 8
BAYER_RED_PHASE = {"rggb": 0, "grbg": 1, "gbrg": 2, "bggr": 3}


class DccError(Exception):
    pass


# --------------------------------------------------------------------------
# TI host tools


def tools_dir():
    base = os.environ.get("CAMERA_ISP_SDK_DCC_TOOLS")
    if base:
        return Path(base)
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache / "camera-isp-sdk" / "dcc-tools" / TI_IMAGING_REF


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_tool(source):
    source = Path(source)
    if source.is_file():
        return source
    for sub in TI_TOOL_SUBDIRS:
        path = source / sub / TI_TOOL
        if path.is_file():
            return path
    raise DccError(f"no {TI_TOOL} in {source}")


def _download(url, dest, log):
    # git.ti.com rejects Python's default User-Agent
    request = urllib.request.Request(url, headers={"User-Agent": "camera-isp-sdk"})
    for delay in DOWNLOAD_RETRY_DELAYS + (None,):
        try:
            with urllib.request.urlopen(request, timeout=120) as resp, open(dest, "wb") as out:
                shutil.copyfileobj(resp, out)
            return
        except (OSError, http.client.HTTPException) as e:
            transient = not isinstance(e, urllib.error.HTTPError) or e.code >= 500 or e.code == 429
            if delay is None or not transient:
                raise DccError(
                    f"cannot download {url}: {e}\n"
                    "git.ti.com is sometimes unavailable for a while. Try again later, or "
                    "download the file with a browser and run\n"
                    f"  camera-isp-sdk dcc fetch-tools --from <downloaded {TI_TOOL}>") from None
            log(f"  {e}; retrying in {delay} s")
            time.sleep(delay)


def fetch_tools(source=None, log=print):
    """Installs a verified dcc_gen_linux into tools_dir().

    It is copied from source (the file, or a directory holding it such as an
    imaging checkout) or downloaded from git.ti.com; a verified copy already
    there is kept unless source is given.
    """
    directory = tools_dir()
    target = directory / TI_TOOL
    if source is None and target.is_file() and _sha256(target) == TI_TOOL_SHA256:
        return directory
    directory.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(TI_TOOL + ".part")
    try:
        if source is not None:
            origin = _find_tool(source)
            shutil.copyfile(origin, tmp)
        else:
            origin = TI_TOOL_URL
            log(f"downloading {TI_TOOL} ({TI_IMAGING_REF})")
            _download(origin, tmp, log)
        actual = _sha256(tmp)
        if actual != TI_TOOL_SHA256:
            raise DccError(f"{origin}: checksum mismatch (got {actual}, expected {TI_TOOL_SHA256}); "
                           f"it is not {TI_TOOL} of {TI_IMAGING_REF}")
        tmp.chmod(0o755)
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)
    return directory


def require_tools():
    path = tools_dir() / TI_TOOL
    if not path.is_file():
        raise DccError(f"{path} is missing; run 'camera-isp-sdk dcc fetch-tools' "
                       "or set CAMERA_ISP_SDK_DCC_TOOLS")
    if _sha256(path) != TI_TOOL_SHA256:
        raise DccError(f"{path} does not match the pinned {TI_IMAGING_REF} checksum")
    return path


# --------------------------------------------------------------------------
# Building


def build(sensor, out_dir, log=print):
    """Generates dcc_<bundle>.bin for a sensor into out_dir.

    Each XML is compiled in a scratch copy of the source directory (the TI
    tool writes next to its input), and the components of a bundle are
    concatenated in file name order, like TI's generate_dcc.sh does.
    """
    if sensor.dcc_source is None:
        raise DccError(f"{sensor.id}: no [dcc] source; it uses prebuilt DCC files in {sensor.dcc_dir}")
    gen = require_tools()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    errors, _ = check_sources(sensor)
    if errors:
        raise DccError("\n".join(f"{sensor.id}: {e}" for e in errors))
    outputs = {}
    with tempfile.TemporaryDirectory(prefix=f"camera-isp-sdk-dcc-{sensor.id}-") as tmp:
        work = Path(tmp) / "src"
        shutil.copytree(sensor.dcc_source, work)
        compiled = {}
        for bundle, xmls in sensor.dcc_bundles.items():
            components = []
            for xml in xmls:
                if xml not in compiled:
                    compiled[xml] = _compile(gen, work, xml, sensor.dcc_id)
                components.append(compiled[xml])
            if not components:
                continue
            target = out_dir / sensor.dcc_files[bundle]
            with open(target, "wb") as out:
                for component in sorted(components, key=lambda p: p.name):
                    out.write(component.read_bytes())
            outputs[bundle] = target
            log(f"{target}  {_sha256(target)}")
    return outputs


def _compile(gen, work, xml, dcc_id):
    expected = work / f"cid{dcc_id}_{Path(xml).stem}.bin"
    expected.unlink(missing_ok=True)
    # dcc_gen_linux returns non-zero on some successful runs, so the output
    # file is the success criterion.
    result = subprocess.run([str(gen), xml], cwd=work, capture_output=True, text=True,
                            errors="replace")
    if not expected.is_file() or expected.stat().st_size == 0:
        raise DccError(f"dcc_gen_linux did not produce {expected.name} from {xml} "
                       f"(exit {result.returncode}):\n{result.stdout}{result.stderr}")
    return expected


# --------------------------------------------------------------------------
# Inspection


@dataclass
class Component:
    offset: int
    module_id: int
    descriptor_id: int
    size: int

    @property
    def descriptor(self):
        return DESCRIPTORS.get(self.descriptor_id, f"descriptor {self.descriptor_id}")


def inspect(path):
    """Splits a DCC bundle into its components."""
    data = Path(path).read_bytes()
    components = []
    offset = 0
    while offset < len(data):
        if len(data) - offset < HEADER_WORDS * 4:
            raise DccError(f"{path}: truncated component header at offset {offset}")
        words = struct.unpack_from(f"<{HEADER_WORDS}I", data, offset)
        size = words[HEADER_SIZE_WORD]
        if size < HEADER_WORDS * 4 or offset + size > len(data):
            raise DccError(f"{path}: invalid component size {size} at offset {offset}")
        components.append(Component(offset, words[0], words[1], size))
        offset += size
    return components


# --------------------------------------------------------------------------
# XML sources


def xml_module_ids(xml_path):
    return [int(m.group(2)) for m in MODULE_ID_RE.finditer(Path(xml_path).read_text(errors="replace"))]


def set_module_id(text, dcc_id):
    return MODULE_ID_RE.sub(lambda m: f"{m.group(1)}{dcc_id}{m.group(3)}", text)


def _h3a_numbers(body):
    """Yields (match, value) for each leading integer of a '1, // name' line."""
    for m in re.finditer(r"^(\s*)(-?\d+)(?=\s*,?\s*(//[^\n]*)?$)", body, re.M):
        yield m


def read_h3a(xml_path):
    text = Path(xml_path).read_text(errors="replace")
    block = H3A_BLOCK_RE.search(text)
    if not block:
        return None
    values = [int(m.group(2)) for m in _h3a_numbers(block.group(2))]
    if len(values) != len(H3A_FIELDS):
        raise DccError(f"{xml_path}: expected {len(H3A_FIELDS)} H3A AEWB values, found {len(values)}")
    return dict(zip(H3A_FIELDS, values))


def _even_floor(value):
    return int(value) // 2 * 2


def fit_h3a(grid, width, height):
    """Recomputes window size and start for a frame size, keeping counts.

    Windows are centred with a margin of at least 16 pixels and 8 lines;
    the black row window goes to the bottom of the frame.
    """
    g = dict(grid)
    g["h_size"] = min(_even_floor((width - 32) / g["h_count"]), 254)
    g["v_size"] = min(_even_floor((height - 16) / g["v_count"]), 254)
    g["h_start"] = _even_floor((width - g["h_size"] * g["h_count"]) / 2)
    g["v_start"] = _even_floor((height - g["v_size"] * g["v_count"]) / 2)
    g["blk_row_vpos"] = height - 2 * g["blk_win_numlines"]
    return g


def check_h3a(grid, width, height):
    problems = []
    if grid["h_start"] + grid["h_size"] * grid["h_count"] > width:
        problems.append(f"H3A windows end at x={grid['h_start'] + grid['h_size'] * grid['h_count']}, "
                        f"beyond the {width} pixel wide frame")
    if grid["v_start"] + grid["v_size"] * grid["v_count"] > height:
        problems.append(f"H3A windows end at y={grid['v_start'] + grid['v_size'] * grid['v_count']}, "
                        f"beyond the {height} line high frame")
    if grid["blk_row_vpos"] + grid["blk_win_numlines"] > height:
        problems.append(f"H3A black row at y={grid['blk_row_vpos']} is outside the frame")
    for key in ("h_size", "v_size"):
        if grid[key] % 2 or not 2 <= grid[key] <= 254:
            problems.append(f"H3A {key}={grid[key]} must be even and within [2, 254]")
    return problems


def write_h3a(xml_path, grid):
    path = Path(xml_path)
    text = path.read_text()
    block = H3A_BLOCK_RE.search(text)
    if not block:
        raise DccError(f"{path}: no h3a_aewb_dcc block")
    body = block.group(2)
    matches = list(_h3a_numbers(body))
    if len(matches) != len(H3A_FIELDS):
        raise DccError(f"{path}: expected {len(H3A_FIELDS)} H3A AEWB values, found {len(matches)}")
    pieces, last = [], 0
    for m, name in zip(matches, H3A_FIELDS):
        pieces.append(body[last:m.start(2)])
        pieces.append(str(grid[name]))
        last = m.end(2)
    pieces.append(body[last:])
    path.write_text(text[:block.start(2)] + "".join(pieces) + text[block.end(2):])


def _cfa_fir(text, path):
    """Returns the CFA block match and the number tokens of its FirCoefs."""
    block = CFA_BLOCK_RE.search(text)
    if not block:
        return None, None
    numbers = [m for m in C_TOKEN_RE.finditer(block.group("body")) if m.group(1) is not None]
    end = CFA_FIR_OFFSET + CFA_SETS * CFA_PHASES * CFA_TAPS
    if len(numbers) < end:
        raise DccError(f"{path}: expected at least {end} CFA values, found {len(numbers)}")
    return block, numbers[CFA_FIR_OFFSET:end]


def _cfa_index(fir_set, phase, tap=0):
    return (fir_set * CFA_PHASES + phase) * CFA_TAPS + tap


def read_cfa_bayer(xml_path):
    """Bayer order the CFA (demosaic) coefficients were made for.

    None if the file has no CFA block; DccError if the coefficients do not
    follow the layout of TI's tunings.
    """
    _, fir = _cfa_fir(Path(xml_path).read_text(errors="replace"), xml_path)
    if fir is None:
        return None

    def self_phases(fir_set):
        return [p for p in range(CFA_PHASES) if int(fir[_cfa_index(fir_set, p, CFA_SELF_TAP)].group(1))]

    red, blue = self_phases(CFA_RED_SET), self_phases(CFA_BLUE_SET)
    if len(red) != 1 or blue != [red[0] ^ 3]:
        raise DccError(f"{xml_path}: cannot tell which Bayer order the CFA coefficients are for")
    return next(order for order, phase in BAYER_RED_PHASE.items() if phase == red[0])


def write_cfa_bayer(xml_path, order):
    """Moves the CFA kernels to the phases of another Bayer order.

    The four orders are the same mosaic shifted by one pixel horizontally
    and/or vertically, so each pixel keeps its neighbourhood, and its
    kernel, at another phase. The masks and thresholds before FirCoefs stay:
    TI's GRBG tunings (AR0233, AR0820) use those of its RGGB IMX390 one.
    Returns whether the file changed.
    """
    path = Path(xml_path)
    shift = BAYER_RED_PHASE[read_cfa_bayer(path)] ^ BAYER_RED_PHASE[order]
    if not shift:
        return False
    text = path.read_text()
    block, fir = _cfa_fir(text, path)
    body = block.group("body")
    pieces, last = [], 0
    for i, m in enumerate(fir):
        fir_set, rest = divmod(i, CFA_PHASES * CFA_TAPS)
        phase, tap = divmod(rest, CFA_TAPS)
        pieces.append(body[last:m.start(1)])
        pieces.append(fir[_cfa_index(fir_set, phase ^ shift, tap)].group(1))
        last = m.end(1)
    pieces.append(body[last:])
    path.write_text(text[:block.start("body")] + "".join(pieces) + text[block.end("body"):])
    return True


def source_xmls(sensor):
    names = []
    for xmls in sensor.dcc_bundles.values():
        for xml in xmls:
            if xml not in names:
                names.append(xml)
    return names


def check_sources(sensor):
    """Returns (errors, warnings) for a sensor's DCC XML sources."""
    errors, warnings = [], []
    if sensor.dcc_source is None:
        return errors, warnings
    if not sensor.dcc_source.is_dir():
        return [f"[dcc] source {sensor.dcc_source} does not exist"], warnings
    for xml in source_xmls(sensor):
        path = sensor.dcc_source / xml
        if not path.is_file():
            errors.append(f"DCC source {path} does not exist")
            continue
        ids = set(xml_module_ids(path))
        if ids != {sensor.dcc_id}:
            errors.append(f"{path.name}: camera_module_id {sorted(ids)} differs from [isp] dcc-id "
                          f"{sensor.dcc_id}; TI's parser would reject it")
        for inc in re.findall(r'#include\s+"([^"]+)"', path.read_text(errors="replace")):
            if not (sensor.dcc_source / inc).is_file():
                errors.append(f"{path.name}: included file {inc} does not exist")
        grid = read_h3a(path)
        if grid:
            for problem in check_h3a(grid, sensor.mode.width, sensor.mode.height):
                errors.append(f"{path.name}: {problem} (run 'camera-isp-sdk dcc fit-h3a {sensor.id}')")
        try:
            bayer = read_cfa_bayer(path)
        except DccError as e:
            warnings.append(f"{e}; check the colours on the board")
            bayer = None
        if bayer and bayer != sensor.mode.bayer:
            errors.append(f"{path.name}: the CFA coefficients are for {bayer.upper()}, [mode] code "
                          f"{sensor.mode.code} is {sensor.mode.bayer.upper()}; the demosaic would take "
                          f"colours from the wrong pixels (run 'camera-isp-sdk dcc fit-cfa {sensor.id}')")
    return errors, warnings


def bootstrap(template, new_id, dcc_id, dest, width, height, bayer=None, bundles=("viss", "2a")):
    """Copies a template sensor's DCC sources for a new sensor.

    Copies the XML files of the given bundles and the files they include.
    Files named <template>_*.xml are renamed to <new_id>_*.xml, the camera
    module ID is rewritten, the H3A grid is fitted to the new frame size
    and the CFA coefficients are moved to the phases of the bayer order.
    Everything else (colour matrices, AWB tuning, noise filter) is the
    template's and must be tuned for the new sensor. LDC is left out by
    default: its mesh belongs to the template's lens.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    xmls = []
    for bundle in bundles:
        xmls += [x for x in template.dcc_bundles.get(bundle, []) if x not in xmls]
    files = list(xmls)
    for xml in xmls:
        for inc in re.findall(r'#include\s+"([^"]+)"', (template.dcc_source / xml).read_text(errors="replace")):
            if inc not in files:
                files.append(inc)

    renames = {}
    for src in (template.dcc_source / f for f in sorted(files)):
        name = src.name
        if name.startswith(f"{template.id}_"):
            name = f"{new_id}_{name[len(template.id) + 1:]}"
        renames[src.name] = name
        target = dest / name
        if src.suffix == ".xml":
            target.write_text(set_module_id(src.read_text(), dcc_id))
            if read_h3a(target):
                write_h3a(target, fit_h3a(read_h3a(target), width, height))
            try:
                if bayer and read_cfa_bayer(target):
                    write_cfa_bayer(target, bayer)
            except DccError:
                pass  # unknown CFA layout; validate warns about it
        else:
            shutil.copyfile(src, target)
    return renames
