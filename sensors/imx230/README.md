# Sony IMX230 (Arducam Pivariety)

Status: supported (colour not tuned)

The Pivariety adapter runs the sensor and exposes Arducam's generic register
interface; the kernel side is the `arducam-pivariety` driver in the T3 kernel
(`CONFIG_VIDEO_ARDUCAM_PIVARIETY=m`). DCC sources were imported from
imx230-dcc-profile (commit 65cf25e); the gamma LUT and black level have since
been corrected (see below).

The subdev is named after the driver, so any Pivariety module on the board
matches `entity=arducam-pivariety`.

## Kernel: the stock driver does not stream

The driver has no `V4L2_CID_LINK_FREQ` control. `cdns-csi2rx` then derives the
D-PHY rate from `PIXEL_RATE` (529.6 MHz): 529.6 MHz × 10 bit / 2 lanes =
2648 Mbps per lane, above the 2500 Mbps limit of `cdns-dphy-rx`, and
`STREAMON` fails with `EOPNOTSUPP` ("Failed to configure external DPHY: -95"
in the kernel log). Every sensor mode reports at least 529.6 MHz. The driver
needs a patch that exposes the overlay's `link-frequencies` (493.5 MHz,
987 Mbps per lane) as `V4L2_CID_LINK_FREQ`.

## Measured on a board (T3 Gemstone O1, firmware 0x10001, 2026-10-14)

- **Bayer order:** `SRGGB10_1X10`, as assumed. In raw frames G1 ≈ G2.
- **Exposure:** in lines. The driver reports `PIXEL_RATE` 529.6 MHz and
  `HBLANK` 6272, so the line time is (1920 + 6272) / 529.6 MHz = 15.47 µs;
  the descriptor no longer sets `line-time-ns`. The control's range is
  1074..63568, but the firmware caps the exposure about 100 lines below the
  frame length (1080 + VBLANK 1074): at most ~2054 lines (~31.8 ms), and no
  shorter than 1074 lines (~16.6 ms). Bright scenes cannot be exposed shorter.
- **Analogue gain:** linear, 100 = 1x, range 100..800. The signal is 1.99x at
  200 and 3.97x at 400.
- **Black level:** 64. The darkest raw pixels are 65; the 1st percentile of a
  dim frame is 68.
- **Frame rate:** 29.25 fps with VBLANK 1074, both through V4L2 and through
  `tiovxisp`. CPU load for capture + ISP is about 0.1 core.

## DCC corrections

Both were measured by running the same raw frame through `tiovxisp`, swapping
one component at a time against TI's IMX219 10-bit tuning:

- `lut_viss_fcc_gamma.txt` was a straight line (output = 2 × input), so no
  gamma was applied and midtones came out 2–4x too dark (mean 43 against 58
  with TI's tuning). It is now TI's IMX219 10-bit gamma LUT from
  `REL.IMAGING.11.00.00.00`.
- `imx230_viss_blc.xml` subtracted 0, lifting blacks (5th percentile of the
  output 19.7). It now subtracts 64 in input units, as TI's IMX219 10-bit
  tuning does; the 5th percentile drops to 8.3 without clipping blacks.

The other components (CCM, CFA, NSF4, decompand, H3A) change the output
brightness by less than 5%. The VISS and 2A bundles must use the same H3A
grid: mixing this tuning's grid (`v_size` 66) with TI's (64) gives a strong
green or purple cast.

A slight green cast remains on neutral walls (G about 5 above (R+B)/2): the
colour correction and AWB tables are not tuned for this sensor yet.

## Bring-up checklist

- [x] Open items above resolved
- [x] `camera-isp-sdk setup imx230` configures the mode without a mismatch
- [x] `camera-isp-sdk pipeline imx230 --sink fake --run` reaches 30 fps (29.45 average)
- [x] AE converges (to the same gain as a software AE on the same scene)
- [x] LDC: `camera-isp-sdk pipeline imx230 --ldc --run` runs at 30 fps (29.54 average,
  0.2 core); the mesh comes from imx230-dcc-profile and its accuracy for this
  lens is not measured (needs a calibration grid)
- [ ] Colour tuning (CCM, AWB) for this sensor
