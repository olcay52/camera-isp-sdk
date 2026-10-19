# Sony IMX477 (Raspberry Pi HQ Camera)

Status: experimental

## Driver

`driver/imx477.c` is the Raspberry Pi kernel driver (rpi-6.12.y, commit
43c132e8863c) with two changes for the TI kernel, listed in its header:

- The embedded data pad is removed; `MEDIA_BUS_FMT_SENSOR_DATA` does not
  exist outside the Raspberry Pi kernel, and TI's CSI-2 receiver filters
  embedded data out by data type.
- Modes whose width the capture DMA cannot take are cropped with the
  sensor's digital crop: 2028x1080 to **1920x1080**, 2028x1520 to
  **2016x1520**, 1332x990 to **1328x990**. 4056x3040 and 4056x2160 are
  unchanged.

It builds without warnings against the T3 6.12.24-ti kernel:

```bash
task drivers SENSORS=imx477 KDIR=/path/to/kernel-build
```

`task package` puts the built `imx477.ko` into the package.

If capture shows corrupted first lines, the sensor's embedded data output
(register `0xbcf1`) is the first thing to look at.

## ISP tuning

Bootstrapped, not tuned. 12-bit dependent components (decompand, H3A mux
LUT, CFA, NSF4) come from TI's IMX390 linear tuning, with the black level set
to the IMX477's 256; the colour matrix, gamma and AWB tuning come from
`imx230`. Expect correct exposure and brightness but inaccurate colours until
the sensor is tuned (`docs/tuning.md`).

## Other modes

Edit `[mode]`, then `camera-isp-sdk dcc fit-h3a imx477` and adjust `[ae] range0` to
the new frame time. Full resolution (4056x3040) runs at 10 fps over the two
lanes.

## Bring-up checklist

- [ ] Driver probes: `dmesg | grep imx477`, entity `imx477 2-001a` (CSI0)
- [ ] `camera-isp-sdk setup imx477` configures 1920x1080 RAW12
- [ ] RAW capture works: `v4l2-ctl -d /dev/video-imx477-cam0 --stream-mmap --stream-count=10`
- [ ] `camera-isp-sdk pipeline imx477 --sink fake --run` reaches 50 fps
- [ ] AE converges (`GST_DEBUG=tiovxisp:6` shows the exposure and gain settling)
- [ ] Colours and white balance tuned
