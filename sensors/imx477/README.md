# Sony IMX477 (Raspberry Pi HQ Camera)

Status: supported

## Driver

`driver/imx477.c` is the Raspberry Pi kernel driver (rpi-6.12.y, commit
43c132e8863c) with three changes for the TI kernel, listed in its header:

- The embedded data pad is removed; `MEDIA_BUS_FMT_SENSOR_DATA` does not
  exist outside the Raspberry Pi kernel, and TI's CSI-2 receiver filters
  embedded data out by data type.
- Modes whose width the capture DMA cannot take are cropped with the
  sensor's digital crop: 2028x1080 to **1920x1080**, 2028x1520 to
  **2016x1520**, 1332x990 to **1328x990**. 4056x3040 and 4056x2160 are
  unchanged.
- `IMX477_XCLR_MIN_DELAY_US` is raised from the datasheet's 8ms to **500ms**.
  With 8ms the first register write of `imx477_start_streaming()` fails with
  `-EREMOTEIO` and `VIDIOC_STREAMON` returns -121. The regulators are not
  described in the device tree, so the driver has no supply-ready signal to
  wait on and the fixed delay has to cover the whole power-up. Values between
  8ms and 500ms were not tested.

It builds without warnings against the T3 6.12.24-ti kernel:

```bash
task drivers SENSORS=imx477 KDIR=/path/to/kernel-build
```

`task package` puts the built `imx477.ko` into the package.

If capture shows corrupted first lines, the sensor's embedded data output
(register `0xbcf1`) is the first thing to look at.

## Frame rate

The driver's `frm_length_default` for this mode is 2197 lines (the same value
the 2028x1520 mode uses upstream), which gives

    pixel_rate / (line_length * frame_length) = 840e6 / (11252 * 2197) = 34 fps

The 50 fps the mode is named for needs `V4L2_CID_VBLANK` set to **413**
(frame length 1493). `camera-isp-sdk setup` does not write it, so the pipeline
runs at 34 fps out of the box. Setting the control by hand gives a measured
**50.12 fps with no dropped frames**:

```bash
v4l2-ctl -d /dev/v4l-imx477-subdev0 --set-ctrl=vertical_blanking=413
```

The CPU cost of the ISP pipeline at 50 fps is about **9% of one core**
(`--sink fake`, 15 s, user+sys over wall time).

## ISP tuning

Bootstrapped, not tuned. 12-bit dependent components (decompand, H3A mux
LUT, CFA, NSF4) come from TI's IMX390 linear tuning, with the black level set
to the IMX477's 256; the colour matrix, gamma and AWB tuning come from
`imx230`.

Measured on hardware, indoors under mixed light:

- **White balance is usable.** With `sink_0::ae-mode=2 sink_0::awb-mode=2` and
  the exposure set by hand, colours are not obviously wrong.
- **The tone curve is the weak point.** The output histogram clips at neither
  end (0.00% at 0 and at 255) but its median is 20/255. Applying 2.2 gamma to
  the output afterwards gives a correct-looking image, so the curve wants to
  be applied inside the ISP, on the 12-bit data.
- **AE converges, but dark.** It reaches the mode's maximum exposure and about
  3x analogue gain and still sits roughly 0.8 EV below a hand-set exposure.
  `target-brightness=45` is about 18% grey, so this may be intended.
- **AE and AWB together collapse.** Either one alone behaves reasonably; with
  both enabled the image goes to near black over a few seconds.
- **The first two frames have no white balance applied** (G/R 1.36, G/B 1.68);
  frame 2 onwards is neutral. Discard them, or expect a green first frame.

## Other modes

Edit `[mode]`, then `camera-isp-sdk dcc fit-h3a imx477` and adjust `[ae] range0` to
the new frame time. Full resolution (4056x3040) runs at 10 fps over the two
lanes.

## Bring-up checklist

- [x] Driver probes: `dmesg | grep imx477`, entity `imx477 5-001a` (CSI0; the
      I2C bus number depends on the kernel, it is 5 on 6.12.24-ti)
- [x] `camera-isp-sdk setup imx477` configures 1920x1080 RAW12
- [x] RAW capture works: `v4l2-ctl -d /dev/video-imx477-cam0 --stream-mmap --stream-count=10`
      (set the format first, `setup` does not write it to the video node:
      `--set-fmt-video=width=1920,height=1080,pixelformat=RG12`)
- [x] `camera-isp-sdk pipeline imx477 --sink fake --run` reaches 50 fps
      (34 fps until `V4L2_CID_VBLANK` is set to 413, see "Frame rate")
- [ ] AE converges (`GST_DEBUG=tiovxisp:6` shows the exposure and gain settling)
      — it converges dark, and collapses when AWB is enabled as well
- [ ] Colours and white balance tuned

Verified on a T3 Gemstone O1 with Ubuntu 22.04 and kernel 6.12.24-ti.
