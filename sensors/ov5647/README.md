# OmniVision OV5647 (Raspberry Pi Camera v1.3)

Status: supported (colour not tuned)

## Hardware

The Raspberry Pi Camera v1.3 has a 15-pin connector; it needs a 15-to-22-pin
cable for the T3 Gemstone O1. The module has its own oscillator and
regulators, enabled by the connector's CAM_GPIO pin. The sensor answers at
I2C address 0x36. The driver only accepts a 25 MHz clock, which the overlay
declares; the module tested runs 4% slower, as with a 24 MHz oscillator
(see below).

## Driver

The mainline `ov5647` driver is enabled in the T3 kernel
(`CONFIG_VIDEO_OV5647=m`), but the image does not ship the module. It is in
the T3 package repository:

```bash
sudo apt install kernel-module-ov5647-6.12.24-ti
```

Without it the overlay loads and `camera-isp-sdk doctor` reports the missing
module.

The descriptor follows the driver's 6.12 version, which differs from
Linux 7.0:

| | 6.12 (T3 kernel) | Linux 7.0 |
|-|------------------|-----------|
| Power | `pwdn-gpios` only: CAM_GPIO is driven as an active-low power-down | adds `avdd`, `dovdd`, `dvdd` supplies; `pwdn-gpios` still works |
| 1080p `PIXEL_RATE` | 81.67 MHz (line 29.58 µs, 30.6 fps) | 87.5 MHz |
| `V4L2_CID_LINK_FREQ` | none | 218.75 MHz |
| Bayer order | `SBGGR10_1X10` (sensor and ISP mirror bits set) | `SGBRG10_1X10` (sensor mirror bit only), changes with `HFLIP`/`VFLIP` |
| Minimum `VBLANK` | 4 (default 24 for 1080p) | 24 |

Without `LINK_FREQ`, `cdns-csi2rx` derives the D-PHY rate from `PIXEL_RATE`:
81.67 MHz × 10 bit / (2 × 2 lanes) = 204.17 MHz, 408 Mbps per lane, and
warns about it in the kernel log. Linux 7.0 declares 218.75 MHz for its
87.5 MHz pixel rate, the same ratio, so the derived rate is what the sensor
sends. The overlay's `link-frequencies` is not read by either version.

On a kernel with the 7.0 driver, `camera-isp-sdk setup` stops with "driver chose
SGBRG10_1X10". Set `[mode] code=SGBRG10_1X10`, then:

```bash
./camera-isp-sdk dcc fit-cfa ov5647     # move the demosaic coefficients to GBRG
./camera-isp-sdk validate ov5647
```

The 6.12 driver allows `VBLANK` down to 4, which can lock the sensor up
(7.0 raised the minimum to 24); leave it at the 1080p default of 24.

## Measured on a board (T3 Gemstone O1, CSI0, 2026-10-16)

- **Probe:** `ov5647 5-0036`, with the overlay's `pwdn-gpios` on CAM_GPIO
  powering the module. The controls match the descriptor: exposure 4..1100
  lines, `HBLANK` 496, `VBLANK` 24, `PIXEL_RATE` 81666700, analogue gain
  16..1023.
- **Bayer order:** `SBGGR10_1X10`, checked with the sensor's colour bar
  pattern (`v4l2-ctl -c test_pattern=1`). In the raw frame, phase 0 is blue
  and phase 3 red, giving the standard white, yellow, cyan, green, magenta,
  red, blue, black. Through `tiovxisp` the bars keep that order with this
  descriptor's DCC; with imx230's unmodified (RGGB) demosaic, yellow and
  cyan and red and blue trade places.
- **Link:** streams without CSI-2 errors at the rate derived from
  `PIXEL_RATE`.
- **Frame rate:** 29.4 fps through V4L2 and through `tiovxisp` (no drops),
  not the 30.6 fps the driver's timings give. The frame time scales with VTS
  (19.6 fps at `VBLANK` 576), so the line time is 30.8 µs instead of
  29.58 µs: the module's clock is 4% below 25 MHz. Exposures are 4% longer
  than the 2A asks for, which its control loop absorbs.
- **ISP:** both DCC bundles accepted (no "Invalid Camera Id"). Capture and
  ISP take about 0.1 core.
- **Auto exposure:** the exposure rises from 4 lines to the 32.5 ms limit in
  about 2 s, then the gain to 8x (code 128). The scene, a black ceiling,
  stays below the target at those limits, so settling inside the range was
  not observed.
- **Black level:** the darkest raw pixels of a dim frame are 12–16,
  consistent with 16; not measured with the lens covered.
- **Image:** neutral whites; mean RGB 96/97/99 on a black and white scene.

## ISP tuning

Bootstrapped from `imx230` (TI's IMX219 10-bit pipeline), not tuned:

- Black level 16 (`ov5647_viss_blc.xml`), from the Raspberry Pi tuning
  (`black_level` 1024 in 16 bits).
- The CFA (demosaic) coefficients are imx230's moved from RGGB to BGGR
  phases (`camera-isp-sdk dcc fit-cfa`). Kept as RGGB, they swap red and blue (see
  above).
- Colour matrix, gamma and AWB tuning are imx230's. Expect correct exposure
  but inaccurate colours until the sensor is tuned (`docs/tuning.md`).
  libcamera's `ov5647.json` has CCMs for this sensor to compare against.

## Bring-up checklist

- [x] Driver probes: `dmesg | grep ov5647`, entity `ov5647 5-0036` (CSI0)
- [x] `camera-isp-sdk setup ov5647` configures 1920x1080 `SBGGR10_1X10`
- [x] RAW capture works: `v4l2-ctl -d /dev/video-ov5647-cam0 --stream-mmap --stream-count=10`
- [x] Bayer order (colour bar test pattern, raw and through the ISP)
- [x] `camera-isp-sdk pipeline ov5647 --sink fake --run` runs at the sensor's rate
  (29.4 fps with this module's clock)
- [ ] AE settles inside its range on a brighter scene (seen only at its limits)
- [ ] Black level measured with the lens covered
- [ ] Colours and white balance tuned
