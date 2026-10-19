# Sensor descriptor reference

`sensors/<id>/sensor.conf` describes one sensor. The `camera-isp-sdk` tool reads all
of it; the patched `tiovxisp` element reads the `[isp]`, `[ae]`, `[exposure]`
and `[gain]` groups at run time from `/usr/share/camera-isp-sdk/sensors/<id>/` (or
`$CAMERA_ISP_SDK_SENSOR_PATH`), with GLib's key-file parser. To stay readable by
both:

* comments are whole lines starting with `#` (no trailing comments);
* lists are separated by `;`;
* integers may be decimal or `0x` hexadecimal; booleans are `true` or `false`.

`camera-isp-sdk validate` and the plugin apply the same rules, and the unit tests
load every shipped descriptor with both parsers.

## [sensor]

| Key | Required | Meaning |
|-----|----------|---------|
| `id` | yes | Letters, digits, `_`, `-`. Must equal the directory name. Used as `sensor-name` in GStreamer and in file names. |
| `name` | | Human-readable name. |
| `status` | | `supported` (verified on hardware), `experimental` (passes all checks, not run on hardware) or `untested` (default; open hardware TODOs). |
| `module` | | Kernel module providing the subdev. Default: `id`. Checked by `camera-isp-sdk doctor`. |
| `entity` | | Media entity name prefix. The entity is matched as `<entity> <bus>-<addr>`, e.g. `imx477 2-001a`. Default: `id`. |
| `driver` | | Directory of an out-of-tree driver relative to the sensor directory (needs `Kbuild`). `task drivers` builds it with the board's kernel tree and the package carries the modules. |

## [mode]

The mode `camera-isp-sdk setup` configures and the ISP is tuned for.

| Key | Required | Meaning |
|-----|----------|---------|
| `width`, `height` | yes | Output size of the sensor. The width must be a multiple of 8 for RAW10/RAW12 and of 16 for RAW8 (capture DMA). |
| `code` | yes | Media bus code as `media-ctl` spells it: `S{BGGR,GBRG,GRBG,RGGB}{8,10,12}_1X{8,10,12}`. Gives the Bayer order and bit depth; the demosaic (CFA) tuning must be for the same order (`camera-isp-sdk dcc fit-cfa`). |
| `fps` | | Frame rate, default 30. Used for bandwidth and exposure checks and for the GStreamer caps. |

## [csi]

| Key | Required | Meaning |
|-----|----------|---------|
| `lanes` | yes | Data lanes. Both T3 Gemstone O1 connectors have 2. |
| `link-frequency` | yes | CSI-2 link frequency in Hz; must match `link-frequencies` in the overlay. |
| `clock-noncontinuous` | | Informational, default false. The overlay sets the actual property. |

## [overlay]

| Key | Required | Meaning |
|-----|----------|---------|
| `source` | | Overlay fragment, default `overlay.dtsi`. |
| `ports` | yes | Board ports to build overlays for, e.g. `csi0;csi1`. |

## [isp]

| Key | Required | Meaning |
|-----|----------|---------|
| `dcc-id` | yes | Camera module ID of the DCC files (0..65535). Must equal `camera_module_id` in every DCC XML; on a mismatch TI's parser rejects the DCC data ("Invalid Camera Id" in the logs) and the ISP runs on defaults. Pick one not used by another sensor. |
| `dcc-dir` | | Where the DCC binaries are on the board. Default `/opt/imaging/<id>/linear`. |
| `dcc-viss`, `dcc-2a`, `dcc-ldc` | | File names in `dcc-dir`. Defaults `dcc_viss.bin`, `dcc_2a.bin`, and `dcc_ldc.bin` when `[dcc] ldc` is set (otherwise no LDC). |
| `format-msb` | | Index of the most significant data bit, default bit depth − 1. Passed to `tiovxisp format-msb`. |

## [dcc]

Only for sensors whose DCC files are built here (omit for prebuilt ones,
such as TI's IMX219 tuning).

| Key | Required | Meaning |
|-----|----------|---------|
| `source` | yes | Directory of the XML and LUT sources, relative to the sensor directory. |
| `bits` | | Bit depth the sources were made for. `validate` fails if it differs from `[mode]`. |
| `viss` | yes | XML files of the VISS bundle (`dcc_viss.bin`). |
| `2a` | yes | XML files of the 2A bundle (`dcc_2a.bin`): AWB tuning and the H3A grid. |
| `ldc` | | XML files of the LDC bundle (`dcc_ldc.bin`). |

## [ae]

Limits for TI's auto exposure. Gains are Q10 (1024 = 1x analog gain) and
256 = 1x for the ISP digital gain.

| Key | Default | Meaning |
|-----|---------|---------|
| `target-brightness` | 45 | Target mean brightness (0..255). |
| `target-brightness-min`, `target-brightness-max` | 40, 50 | Accepted brightness band. |
| `threshold` | 1 | Change beyond which brightness history is reset. |
| `exposure-step` | 1 | Exposure step size. |
| `enable-blc` | true | Backlight compensation. |
| `range0` … `range9` | `range0` required | `exposure-min-us;exposure-max-us;again-min;again-max;dgain-min;dgain-max`. AE walks the ranges in order, so a second range can raise the gain only once the exposure is at its maximum. |

Keep `exposure-max-us` below the frame time (1e6 / fps) and `again-max`
below the sensor's maximum analog gain.

## [exposure]

How the 2A exposure in microseconds becomes a `V4L2_CID_EXPOSURE` value.

| Key | Meaning |
|-----|---------|
| `model=lines` | Default. The control counts lines. The line time is `(width + HBLANK) / PIXEL_RATE`, read from the subdev. |
| `line-time-ns` | With `model=lines`: fixed line time in ns for drivers without `V4L2_CID_PIXEL_RATE` or `V4L2_CID_HBLANK`. |
| `model=time` | The control counts fixed time units… |
| `unit-ns` | …of this many ns (e.g. 100000 for `V4L2_CID_EXPOSURE` in 100 µs units). |

## [gain]

How the 2A analog gain (gain *g*, 1.0 = 1x) becomes a
`V4L2_CID_ANALOGUE_GAIN` code. The result is clamped to the control's range.

| `model` | Keys | Code | Sensors |
|---------|------|------|---------|
| `linear` | `scale`, `offset` (0) | `offset + scale × g` | many OmniVision sensors (`scale=16`) |
| `inverse` | `k` | `k − k / g` | Sony IMX219 (`k=256`), IMX477/IMX378 (`k=1024`), many Sony |
| `db` | `step-db` | `20 log10(g) / step-db` | Sony IMX290/IMX327/IMX462 (`step-db=0.3`) |
| `table` | `table=gain:code;...` | code of the largest table gain not above `g × 1024` | anything else |

The datasheet's analog gain register description or the sensor's camera
helper in libcamera (`src/ipa/rpi/cam_helper/cam_helper_<sensor>.cpp`) gives
the formula.

## Example

```ini
[sensor]
id=imx477
name=Sony IMX477 (Raspberry Pi HQ Camera)
status=experimental
module=imx477
entity=imx477
driver=driver

[mode]
width=1920
height=1080
code=SRGGB12_1X12
fps=50

[csi]
lanes=2
link-frequency=450000000

[overlay]
ports=csi0;csi1

[isp]
dcc-id=477

[dcc]
source=dcc/linear
bits=12
viss=imx477_rgb2rgb_dcc.xml;imx477_h3a_aewb_dcc.xml;...
2a=imx477_awb_alg_ti3_tuning.xml;imx477_h3a_aewb_dcc.xml

[ae]
range0=100;19800;1024;16384;256;256

[exposure]
model=lines

[gain]
model=inverse
k=1024
```
