# Architecture

This page explains where a camera sensor touches TI's Edge AI 11.0 stack on
the AM67A (J722S), what TI hard-codes there, and how camera-isp-sdk replaces each
hard-coded piece with data from the sensor descriptor.

## Data path

```
sensor (I2C control, CSI-2 data)
  │
  ├─ cdns_csi2rx bridge ─ ti_csi2rx (DMA, "context 0") ─ /dev/videoN      kernel, V4L2
  │
  ├─ v4l2src  ─  video/x-bayer, format=rggb10 ...                         GStreamer
  │
  ├─ tiovxisp ─ TIOVX VISS node ─ VPAC VISS hardware                      ISP
  │     ├─ RAW front end: decompand, black level (DCC)
  │     ├─ NSF4 noise filter, CFA demosaic, RGB-to-RGB matrix, gamma (DCC)
  │     ├─ H3A AE/AWB statistics (grid from DCC)
  │     └─ output NV12
  │
  └─ TI 2A (AE + AWB) on the A53, inside tiovxisp's postprocess step
        ├─ reads the H3A statistics of the frame
        ├─ AWB gains go back into the VISS
        └─ exposure (µs) and analog gain (Q10) are converted to sensor
           control values and written to the sensor subdev with VIDIOC_S_CTRL
```

Everything between capture and NV12 runs on hardware. The A53 runs only the
2A algorithms, once every other frame.

## What TI hard-codes, and what camera-isp-sdk does instead

| Concern | Edge AI 11.0 | camera-isp-sdk |
|---------|--------------|---------|
| DCC ID | `tiovx_init_sensor()` in edgeai-tiovx-modules maps six sensor names to IDs. Any other name leaves the ID at 0, and the DCC parser then rejects the sensor's tuning, whose components carry the real ID. | `[isp] dcc-id` |
| AE limits | `get_<sensor>_ae_dyn_params()` in `gsttiovxisp.c`. Unknown sensors get the IMX219 limits. | `[ae]` |
| 2A to sensor controls | `gst_tiovx_isp_map_2A_values()` has one formula per sensor. For any other sensor it logs "Unknown sensor" and the element then writes 0 to both controls. | `[exposure]` and `[gain]` models, with the line time read from the subdev |
| LDC | `tiovxldc` fails on unknown names when a DCC file is given. | `[isp] dcc-id` |
| Sensor query | `tiovx_querry_sensor()` is an empty function on Linux. | Nothing to do: no imaging library or firmware rebuild is needed. |

The patch in `gst/patches/` hooks `camera_isp_sdk_sensor.c` into those places. When
`sensor-name` names a descriptor (looked up in `$CAMERA_ISP_SDK_SENSOR_PATH`,
default `/etc/camera-isp-sdk/sensors:/usr/share/camera-isp-sdk/sensors`), the descriptor
provides all of the above. Any other name, including TI's own, takes TI's
original code path, so existing pipelines behave as before.

### Exposure and gain conversion

TI's 2A outputs exposure in microseconds and analog gain in Q10 (1024 = 1x).
Sensor drivers take neither:

* `V4L2_CID_EXPOSURE` is usually in lines. A line lasts
  `(width + HBLANK) / PIXEL_RATE`, which the plugin reads from the subdev
  (`VIDIOC_SUBDEV_G_FMT`, `V4L2_CID_HBLANK`, `V4L2_CID_PIXEL_RATE`) the first
  time it is needed, so the conversion follows whatever mode is configured.
  Drivers without those controls give a fixed `line-time-ns` instead.
* `V4L2_CID_ANALOGUE_GAIN` is a register code. The descriptor picks one of
  four models (linear, inverse, dB, table) that cover the common sensors.

Both values are clamped to the ranges the driver reports before they are set.

## Kernel side

`camera-isp-sdk` does not patch the kernel. A sensor needs:

* a V4L2 subdev driver with `V4L2_CID_EXPOSURE` and
  `V4L2_CID_ANALOGUE_GAIN`, and ideally `V4L2_CID_PIXEL_RATE` and
  `V4L2_CID_HBLANK`; in tree, or out of tree under `sensors/<id>/driver/`;
* a device tree overlay. A sensor writes one fragment against the port macros
  of `boards/t3-gem-o1/csi{0,1}.h`; `camera-isp-sdk overlay build` combines it with
  `camera-port.dtsi` (CSI-2 bridge endpoint, D-PHY, receiver, CSI1's MIPI
  switch) into one overlay per connector.

`camera-isp-sdk setup` then walks the media graph the same way TI's
`setup_cameras.sh` does for directly attached sensors: sensor pad 0, the
`csi-bridge` sink, the `ticsi2rx` sink, and `ticsi2rx context 0` as the
video node.

## Constraints found while building this

These are checked by `camera-isp-sdk validate`.

* **Capture width alignment.** `j721e-csi2rx` moves 16-byte words and rounds
  the video node width down to a whole number of them. RAW10/RAW12 pixels
  use 16-bit containers, so the width must be a multiple of 8 (RAW8: 16),
  otherwise link validation fails with `EPIPE`. The IMX477's 2028 and 1332
  pixel modes fail this; the ported driver crops them to 1920, 2016 and 1328.
* **DCC module ID.** Every DCC component carries a camera module ID. When it
  differs from the sensor's, TI's parser (`algos/dcc/src/process_binary.c`)
  stops with "Invalid Camera Id" in the logs and the ISP runs on
  defaults; the pipeline does not fail. `camera-isp-sdk dcc inspect` prints the IDs
  inside a binary.
* **DCC bit depth.** The decompand LUT, H3A mux LUT, CFA LUT, noise filter
  thresholds and black level depend on the sensor bit depth. TI's 10-bit
  IMX219 tuning expands the input to 16 bits with a LUT; its 12-bit IMX390
  tuning disables that step. Mixing them up blows out or crushes the image.
* **H3A grid.** The AE/AWB statistics windows are given in pixels and must
  lie inside the frame. `camera-isp-sdk dcc fit-h3a` recomputes them for the mode.
* **Bayer order of the demosaic.** `tiovxisp` passes the caps' Bayer order
  to the 2A only (H3A statistics, white balance gains). The VISS demosaic
  takes a 6x6 kernel per phase of the 2x2 cell from the CFA DCC, made for
  one order: RGGB in TI's IMX219 and IMX390 tunings, GRBG in its AR0233 and
  AR0820 ones. With another order it takes colours from the wrong pixels.
  The four orders are the same mosaic shifted by one pixel, so
  `camera-isp-sdk dcc fit-cfa` only moves each kernel to its pixel's new phase.
* **CSI1 shares its lanes with DSI0** through a MIPI switch. A camera on CSI1
  and a DSI0 panel overlay are mutually exclusive.

## Notes on TI's code

* In 11.0, `gst_tiovx_isp_map_2A_values()` converts the IMX219 exposure with
  `1080 * exposure_time / 33` while `exposure_time` is in microseconds, so the
  computed line count is about 1000 times too large and the driver clamps it
  to the maximum; AE then works through the gain only. TI fixed this after
  11.0 (edgeai-gst-plugins commit fea1221). The descriptor path does not have
  this problem.
* `tiovx_init_sensor()` only knows TI's sensors and logs
  "[SENSOR-MODULE] Invalid sensor name" for any other. For a descriptor
  sensor the patch runs it for a TI sensor to set its defaults and puts in
  the descriptor's name and DCC ID. The message therefore only appears for
  a `sensor-name` that has no descriptor either, and `tiovxisp` then fails
  to start.
* TI removed `tiovxisp` from J722S builds after 11.0 (commit 671fb8a). The
  11.0 vision apps library still exports the 2A wrapper for J722S, so the
  element works there; a newer SDK will need it re-enabled.
