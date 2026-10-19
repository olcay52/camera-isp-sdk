<p align="center">
    <picture>
        <source media="(prefers-color-scheme: dark)" srcset=".meta/logo-dark.png" width="40%" />
        <source media="(prefers-color-scheme: light)" srcset=".meta/logo-light.png" width="40%" />
        <img alt="T3 Foundation" src=".meta/logo-light.png" width="40%" />
    </picture>
</p>

# T3 Gemstone Camera ISP SDK

 [![T3 Foundation](./.meta/t3-foundation.svg)](https://www.t3vakfi.org/en) [![Documentation](https://img.shields.io/badge/Documentation-gray?style=flat&logo=Mintlify)](https://docs.t3gemstone.org/en/boards/o1/peripherals/camera) [![License](https://img.shields.io/badge/License-Apache_2.0_·_GPL_2.0-blue.svg)](#license) [![Board](https://img.shields.io/badge/Board-T3_Gemstone_O1-0b7285?style=flat)](https://docs.t3gemstone.org/en/boards/o1) [![SoC](https://img.shields.io/badge/SoC-TI_AM67A_·_J722S-d9480f?style=flat&logo=texasinstruments&logoColor=white)](https://www.ti.com/product/AM67A) [![Edge AI SDK](https://img.shields.io/badge/Edge_AI_SDK-11.0-5f3dc4?style=flat)](https://www.ti.com/tool/PROCESSOR-SDK-AM67A) [![Python](https://img.shields.io/badge/Python-3.10+-3776ab?style=flat&logo=python&logoColor=white)](https://www.python.org/) [![Built with Distrobox](https://img.shields.io/badge/Built_with-distrobox-red)](https://github.com/89luca89/distrobox) [![Built with Devbox](https://www.jetify.com/img/devbox/shield_galaxy.svg)](https://www.jetify.com/devbox/docs/contributor-quickstart/) [![Status](https://img.shields.io/badge/Status-experimental-e8590c?style=flat)](#status-of-this-release)

## What is it?

This project runs camera sensors that TI does not support through the hardware
ISP of the TI AM67A (J722S) on the **T3 Gemstone O1**, and lets you add new
ones without touching TI's code.

TI's Edge AI 11.0 stack can only push sensors it knows about through the
VPAC VISS image pipe: `tiovxisp` selects the DCC tuning ID, the auto-exposure
limits and the exposure/gain conversion by comparing `sensor-name` against a
hard-coded list. For any other sensor it falls back to defaults that do not
work, which is why the board's [camera guide](https://docs.t3gemstone.org/en/boards/o1/peripherals/camera)
debayers on the CPU with `bayer2rgb` today.

This repository makes that path data-driven. A sensor is a directory under
`sensors/`:

```
sensors/imx477/
├── sensor.conf      descriptor: mode, CSI-2 link, DCC ID, AE limits, exposure/gain model
├── overlay.dtsi     device tree fragment, written once for every camera port
├── dcc/linear/      ISP tuning sources (XML), compiled into dcc_*.bin
└── driver/          out-of-tree kernel driver, when the kernel has none
```

A one-time patch to TI's GStreamer plugin makes `tiovxisp` and `tiovxldc` read
`sensor.conf` at run time. Adding a sensor needs no TI rebuild: no plugin
patch, no `libtivision_apps`, no R5F/C7x firmware.

### How it works

```
 sensor ──CSI-2──> CSI2RX ──DMA──> v4l2src ──RAW Bayer──> tiovxisp ──NV12──> display / encoder / DL
   ▲                                                       │  VPAC VISS (hardware): black level,
   │                                                       │  demosaic, CCM, gamma, noise filter,
   │                                                       │  H3A statistics
   │                                                       ▼
   └──── V4L2_CID_EXPOSURE / V4L2_CID_ANALOGUE_GAIN ─── TI 2A (AE/AWB) on the A53,
         converted with the descriptor's models         limits from the descriptor
```

Only the 2A control loop runs on the CPU. See [docs/architecture.md](docs/architecture.md)
for where each piece plugs into TI's stack.

### Sensors

| ID | Module | Kernel driver | Default mode | ISP tuning | Status |
|----|--------|---------------|--------------|------------|--------|
| `imx219` | Raspberry Pi Camera v2 | `imx219` | 1920x1080@30 RAW10 | TI's, already on the image | supported |
| `imx230` | Arducam Pivariety IMX230 | `arducam-pivariety`| 1920x1080@30 RAW10 | imx230-dcc-profile + TI gamma, black level 64; colour not tuned | supported |
| `imx477` | Raspberry Pi HQ Camera | [`sensors/imx477/driver`](sensors/imx477/driver) | 1920x1080@50 RAW12 | bootstrapped, not tuned | experimental |
| `ov5647` | Raspberry Pi Camera v1.3 | `kernel-module-ov5647-6.12.24-ti` | 1920x1080@30 RAW10 | bootstrapped from imx230, black level 16; colour not tuned | supported |

*supported*: verified end to end on a board. *experimental*: builds and passes
all checks, not yet run on hardware. *untested*: has open TODOs that need
hardware (see the sensor's `sensor.conf`).

`imx219` is the reference: TI supports it, so running it once as
`sensor-name=imx219` and once as `sensor-name=SENSOR_SONY_IMX219_RPI` checks the
patched plugin against TI's own path with a known-good tuning.

## Installation

The build tools run in a Docker image (Ubuntu 22.04, see the `Dockerfile`),
entered through Distrobox as in the [T3 Gemstone SDK](https://github.com/t3gemstone/sdk).
The host only needs Docker and Jetify Devbox. On the board: the T3 Ubuntu
22.04 image with TI Edge AI SDK 11.0.

#### 1. Install Docker and jetify-devbox on the host computer.

```bash
user@host:$ ./setup.sh
```

If Docker was installed for the first time, log out and back in so that your
`docker` group membership takes effect.

#### 2. Activate the devbox shell, which installs Distrobox and Task, then build the image and enter it.

```bash
user@host:$ devbox shell
📦 devbox:camera-isp-sdk> task box
```

The repository is `/home/workdir` in the box; other host paths are under
`/run/host`. `task destroy` removes the container and the image.

#### 3. Cross-build the sensor drivers the kernel lacks.

Sensors whose driver is not in the T3 kernel (IMX477 today) carry it in
`sensors/<id>/driver/`. Build them against the board's kernel build tree:

```bash
🚀 distrobox:workdir> task drivers KDIR=/run/host/path/to/kernel-build
```

The package built next carries the modules; a driver that was not built is
left out of it.

#### 4. Build the package.

```bash
🚀 distrobox:workdir> task                # list the tasks and their settings
🚀 distrobox:workdir> task build          # validate sensors, build DCC binaries and overlays
🚀 distrobox:workdir> task package        # -> build/camera-isp-sdk-0.1.0-t3-gem-o1.tar.gz
```

`SENSORS="imx477 ov5647"` limits a task to some sensors. `task dcc` downloads
TI's `dcc_gen_linux` from the pinned
`REL.IMAGING.11.00.00.00` tag once and checks its SHA-256. git.ti.com is
sometimes unavailable for a while (HTTP 502/503); the download is retried, and
if it still fails, download the file with a browser and install it with
`task dcc DCC_TOOLS_FROM=~/Downloads/dcc_gen_linux` (an imaging checkout or
a Processor SDK RTOS directory works too). The checksum is checked either way.

#### 5. Install the package on the board and rebuild TI's tiovx plugin once.

`tiovxisp` and TIOVX, which drive the hardware accelerators, come with the
`t3-edgeai-dev` package, and their firmware needs the
`k3-am67a-t3-gem-o1-edgeai-apps.dtbo` overlay in the `overlays=` line of
`/boot/uEnv.txt` (reboot after adding it, see step 7). Run `build.sh` and the
`camera-isp-sdk` commands as root with the Edge AI environment loaded, in every new
shell; without it the hardware accelerators do not work.

```bash
 sudo apt install t3-edgeai-dev
 sudo su
 source /opt/t3-edgeai-env
 tar -C / -xzf camera-isp-sdk-0.1.0-t3-gem-o1.tar.gz
 /usr/share/camera-isp-sdk/gst/build.sh
```

The stock plugin is kept and can be restored with `--restore`. Run
`build.sh` again after installing a package with a new plugin patch;
`camera-isp-sdk doctor` says when.

#### 6. Install the drivers that are not in the image.

Drivers built in step 3 (IMX477) are installed by the package under
`/lib/modules/<kernel>/updates/`; register them:

```bash
 sudo depmod -a
```

Drivers that are in the T3 kernel but not in the image (OV5647) are in the
T3 package repository:

```bash
 sudo apt install kernel-module-ov5647-6.12.24-ti
```

#### 7. Enable the overlay of the connector the camera is on and reboot.

For example, for an IMX477 on CSI0 add `k3-am67a-t3-gem-o1-csi0-imx477.dtbo`
to the `overlays=` line, next to `k3-am67a-t3-gem-o1-edgeai-apps.dtbo`:

```bash
 sudo nano /boot/uEnv.txt && sudo reboot
```

#### 8. Check the setup and start the camera.

As root, with the Edge AI environment loaded (step 5):

```bash
 camera-isp-sdk doctor                  # checks overlay, driver, plugin, DCC IDs
 camera-isp-sdk setup                   # sets the mode, creates /dev/video-imx477-cam0
 camera-isp-sdk pipeline imx477 --run   # camera -> ISP -> HDMI/DSI display
 camera-isp-sdk pipeline imx477 --sink jpeg --output photo.jpg --run
 camera-isp-sdk pipeline imx477 --sink h264 --frames 300 --run
 camera-isp-sdk pipeline imx477         # print the gst-launch-1.0 command instead
```

## Adding a sensor

In the box (`task box`):

```bash
🚀 distrobox:workdir> ./camera-isp-sdk new imx290 --width 1920 --height 1080 --code SRGGB10_1X10 --dcc-id 290
# edit sensors/imx290/{sensor.conf,overlay.dtsi}, add driver/ if needed
🚀 distrobox:workdir> ./camera-isp-sdk validate imx290
🚀 distrobox:workdir> task package SENSORS=imx290
```

`camera-isp-sdk validate` catches most problems that would otherwise only show up
on the board, often silently: widths the capture DMA cannot take, DCC files
whose module ID the ISP ignores, an AE statistics grid outside the frame,
tuning made for another bit depth or Bayer order, links the D-PHY cannot
carry.

The full walkthrough, which is how `sensors/ov5647` was added, is in
[docs/adding-a-sensor.md](docs/adding-a-sensor.md),
the descriptor reference in [docs/sensor-descriptor.md](docs/sensor-descriptor.md),
and ISP tuning in [docs/tuning.md](docs/tuning.md).

## Layout

| Path | Contents |
|------|----------|
| `sensors/` | One directory per sensor; `_template/` is used by `camera-isp-sdk new` |
| `boards/t3-gem-o1/` | Camera port headers (CSI0, CSI1), shared overlay plumbing, capture limits |
| `gst/` | `tiovxisp`/`tiovxldc` patch, descriptor parser, on-board build script |
| `tools/camera_isp_sdk/` | The `camera-isp-sdk` tool (Python standard library only) |
| `include/` | Headers for overlay fragments |
| `docs/` | Architecture, how-tos and references |
| `Taskfile.yml` | Build tasks; `gst/` and `tools/` add their own (`gst:test`, `gst:check-patch`, `tools:test`) |
| `Dockerfile`, `distrobox.ini`, `devbox.json`, `.distrobox/` | The build environment of `task box`, also used by CI |

## Status of this release

Checked without hardware:

- The plugin patch applies to TI's tree at the pinned commit and compiles
  without warnings against the Edge AI 11.0 headers.
- The descriptor parser has unit tests (run under ASan/UBSan) and accepts
  every shipped descriptor; the tool has its own tests (`task test`).
- Overlays generated for IMX219 (CSI0, CSI1) and the Pivariety IMX230 give
  the same device tree as the hand-written kernel overlays once applied with
  `fdtoverlay`, apart from node and label names.
- The IMX230 DCC builds from imx230-dcc-profile with two fixes found on a
  board: TI's gamma LUT instead of a linear one, and black level 64.
- The IMX477 driver builds against the T3 6.12.24-ti kernel.
- The OV5647 overlays apply to the T3 base DTB on both ports, and its DCC
  builds with imx230's demosaic coefficients moved to BGGR. That TI's
  coefficients are per Bayer phase was checked against its RGGB (IMX219,
  IMX390) and GRBG (AR0233, AR0820) tunings.

Run on a board (T3 Gemstone O1, Edge AI 11.0):

- IMX230 end to end: `camera-isp-sdk doctor` and `setup`, `pipeline --run` with and
  without LDC at 30 fps for about 0.2 core, AE converging to the same gain as
  a software AE, and a service streaming from `tiovxisp` across reboots. The
  plugin was built and installed with `gst/build.sh`. See
  [sensors/imx230/README.md](sensors/imx230/README.md). (2026-10-14)
- OV5647 end to end on CSI0: `doctor`, `setup`, RAW capture, `tiovxisp` at
  29.4 fps for about 0.1 core, AE driving exposure and gain. The sensor's
  colour bar pattern confirms the BGGR order through the ISP; with imx230's
  RGGB demosaic red and blue swap. See
  [sensors/ov5647/README.md](sensors/ov5647/README.md). (2026-10-16)

Not yet run on a board: IMX477, which stays *experimental*.

## Limitations

- Pinned to TI Edge AI SDK 11.0. TI dropped `tiovxisp` from J722S builds
  after 11.0 (edgeai-gst-plugins commit 671fb8a); moving to a newer SDK means
  carrying that element too.
- Sensors on a direct CSI-2 link only; FPD-Link/GMSL serializers are not
  handled by `camera-isp-sdk setup`.
- One sensor mode per descriptor. Another mode needs its own H3A grid
  (`camera-isp-sdk dcc fit-h3a`) and usually its own tuning.

## License

Tooling, descriptors and documentation: Apache-2.0. Kernel drivers and device
tree sources: GPL-2.0. The plugin patch modifies TI code and the DCC sources
derive from TI tuning files; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
