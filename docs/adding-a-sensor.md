# Adding a sensor

This walks through bringing a new RAW Bayer sensor up on the T3 Gemstone O1
hardware ISP. The example is how `sensors/ov5647` was added, so each step
can be compared with the result in the tree; replace the names with yours.

## 0. Check the sensor fits

* RAW Bayer, 8, 10 or 12 bits, over MIPI CSI-2 D-PHY with at most 2 data
  lanes (both board connectors are 2-lane). YUV sensors do not need the ISP.
* A mode whose width is a multiple of 8 pixels (RAW10/RAW12) or 16 (RAW8).
  If the native width is not, the driver has to crop (see how
  `sensors/imx477/driver/imx477.c` does it with the digital crop registers).
* Pixel rate within the link: width × height × fps × bits below
  lanes × 2 × link frequency.

## 1. Kernel driver

The ISP path needs a V4L2 subdev driver that provides:

| Control | Why |
|---------|-----|
| `V4L2_CID_EXPOSURE` | Set by auto exposure |
| `V4L2_CID_ANALOGUE_GAIN` | Set by auto exposure |
| `V4L2_CID_PIXEL_RATE`, `V4L2_CID_HBLANK` | Exposure conversion; otherwise give `line-time-ns` |

and a single source pad. Embedded-data pads from the Raspberry Pi kernel use
`MEDIA_BUS_FMT_SENSOR_DATA`, which the TI kernel does not have; remove them.

* **In the T3 kernel already** (`grep VIDEO_<SENSOR> t3_gem_o1_defconfig`,
  as for the OV5647): nothing to do.
* **Mainline or Raspberry Pi kernel**: copy the driver to
  `sensors/<id>/driver/` with a `Kbuild` (`obj-m += <id>.o`), and set
  `driver=driver` in the descriptor. Build it against the board kernel:

  ```bash
  task drivers SENSORS=<id> KDIR=/path/to/kernel-build
  ```

  `task package` puts the built `<id>.ko` into the package.

## 2. Scaffold

```bash
./camera-isp-sdk new ov5647 --name "OmniVision OV5647 (Raspberry Pi Camera v1.3)" \
    --width 1920 --height 1080 --code SBGGR10_1X10 --fps 30 \
    --dcc-id 5647 --dcc-from imx230
```

This creates `sensors/ov5647/` with a descriptor, an overlay fragment, a
README checklist, and DCC sources copied from `imx230` with the camera module
ID set to 5647, the AE/AWB statistics grid fitted to 1920x1080 and the
demosaic coefficients moved to the BGGR order of `--code`. Pick a DCC ID
nobody else uses (the sensor's part number works).

`--dcc-from` must name a sensor with the **same bit depth** (imx230: 10-bit,
imx477: 12-bit); the tool warns otherwise. See [tuning.md](tuning.md).

## 3. Overlay fragment

Edit `sensors/ov5647/overlay.dtsi`. It is written once for all ports with
these macros from `boards/t3-gem-o1/csi{0,1}.h`:

| Macro | Meaning |
|-------|---------|
| `CAM_I2C` | I2C bus of the connector |
| `CAM_GPIO_CTRL`, `CAM_GPIO`, `CAM_GPIO_PINCTRL` | The connector's CAM_GPIO pin (power enable on Raspberry Pi modules) |
| `CAM_LABEL(x)` | Label unique per port, e.g. `x_csi0` |
| `CAM_SENSOR_EP`, `CAM_RX_EP` | The two endpoints to link |

and must define `CAM_DATA_LANES` (and `CAM_CLOCK_NONCONTINUOUS` if the sensor
uses a non-continuous clock). Copy the compatible string, I2C address,
clock and supplies from the driver's DT binding. `camera-port.dtsi` adds the
CSI-2 receiver side.

```bash
./camera-isp-sdk overlay build ov5647
task check-overlays SENSORS=ov5647 BASE_DTB=/path/to/k3-am67a-t3-gem-o1.dtb
```

## 4. Descriptor

Fill in the TODOs of `sensors/ov5647/sensor.conf` (reference:
[sensor-descriptor.md](sensor-descriptor.md)):

* `[sensor] module` and `entity`: driver module name and the entity name
  prefix shown by `media-ctl -p`.
* `[csi]`: lanes and the same link frequency as the overlay. A driver
  without `V4L2_CID_LINK_FREQ` leaves the receiver to derive it as
  PIXEL_RATE × bits / (2 × lanes); give that value (OV5647: 204166750).
* `[ae] range0`: exposure up to the frame time, analog gain up to the
  sensor's maximum.
* `[gain]`: the analog gain register formula. OV5647: `model=linear`,
  `scale=16`.

```bash
./camera-isp-sdk validate ov5647
```

## 5. Build and install

```bash
task package SENSORS=ov5647
scp build/camera-isp-sdk-*.tar.gz gemstone:
```

On the board:

```bash
sudo tar -C / -xzf camera-isp-sdk-*.tar.gz
# add k3-am67a-t3-gem-o1-csi0-ov5647.dtbo to overlays= in /boot/uEnv.txt
sudo reboot
sudo camera-isp-sdk doctor
```

## 6. Bring-up on the board

Go through these in order; each step isolates one layer.

```bash
# The driver probed and the sensor is in the media graph
media-ctl -p | grep -A3 ov5647

# Mode set along the pipeline, /dev links created
sudo camera-isp-sdk setup -v ov5647

# RAW frames arrive (no ISP yet)
v4l2-ctl -d /dev/video-ov5647-cam0 --stream-mmap --stream-count=30

# Through the ISP, measuring the frame rate
camera-isp-sdk pipeline ov5647 --sink fake --run

# Auto exposure: exposure and gain should settle within a second or two
GST_DEBUG=tiovxisp:6 camera-isp-sdk pipeline ov5647 --sink fake --run 2>&1 | grep -i "sensor specific"

# A picture
camera-isp-sdk pipeline ov5647 --sink jpeg --output ov5647.jpg --run
```

| Symptom | Likely cause |
|---------|--------------|
| No entity in `media-ctl -p` | Overlay not loaded (`ls /proc/device-tree/chosen/overlays`), wrong I2C address, sensor not powered (CAM_GPIO), driver missing |
| `setup`: driver chose another format | `[mode]` not supported by the driver; check `v4l2-ctl -d /dev/v4l-subdevN --list-subdev-framesizes pad=0,code=...` |
| `v4l2-ctl` streams nothing | Link frequency or lanes wrong in the overlay; `dmesg` for CSI-2 errors |
| `Broken pipe` when streaming | Width not a multiple of 8/16, or setup not run |
| Image pink/green, or very dark | Bayer order in `[mode] code` (fix it, then `camera-isp-sdk dcc fit-cfa`), or DCC module ID mismatch (`camera-isp-sdk dcc inspect`) |
| Red and blue swapped | Same: `[mode] code` and the demosaic agree with each other, not with the sensor |
| Image saturated or flat grey | DCC made for another bit depth, or wrong `format-msb` |
| Brightness oscillates | `[gain]` or `[exposure]` model wrong |

## 7. Tune and share

Tune the ISP ([tuning.md](tuning.md)), tick the README checklist, set
`status=supported` once it has been verified on a board, and send a pull
request with the sensor directory.
