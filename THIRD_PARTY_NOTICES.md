# Third-party notices

## Texas Instruments

* **`gst/patches/`** modifies `ext/tiovx/gsttiovxisp.c`, `gsttiovxldc.c` and
  `meson.build` of [edgeai-gst-plugins](https://github.com/TexasInstruments/edgeai-gst-plugins)
  (commit 39577530481b). Those files are under TI's license in that
  repository; the patch, once applied, is too. `gst/src/camera_isp_sdk_sensor.[ch]`
  are original work under Apache-2.0.
* **DCC tuning sources** (`sensors/*/dcc/`) are derived from TI's imaging
  repository, `REL.IMAGING.11.00.00.00`
  (https://git.ti.com/cgit/processor-sdk/imaging/):
  * `sensors/imx477/dcc/linear/imx477_{rawfe_decompand,h3a_mux_luts,cfa,viss_nsf4,viss_blc}.xml`
    and `lut_{rawfe_pwl_linear,h3a_12b_to_10b,cfa_16to12_g07_linear}.txt` from
    `sensor_drv/src/imx390/dcc_xmls/linear/`, with the camera module ID and
    the black level changed;
  * the remaining XML and LUT files via imx230-dcc-profile (below), whose
    pipeline follows TI's IMX219 10-bit tuning;
  * `sensors/ov5647/dcc/linear/` from `sensors/imx230/dcc/linear/`, with the
    camera module ID, the black level and the phases of the CFA
    coefficients changed.

  They remain subject to TI's terms for that repository.
* **DCC host tools** (`dcc_gen_linux`, `dcc_bin2c`) are downloaded from the
  same tag by `camera-isp-sdk dcc fetch-tools`, verified against pinned SHA-256
  sums, and never stored in or redistributed by this repository:

  ```
  3bc23ebbc8eb37c662eea3b0190b64787ff7cd92e822189f0d09de504d951024  dcc_gen_linux
  3be31b12d74e6dd4b333e1156cb456fb7dc530b8c3bd007886abcd8903d53a93  dcc_bin2c
  ```

## Raspberry Pi

`sensors/imx477/driver/imx477.c` is derived from `drivers/media/i2c/imx477.c`
of [raspberrypi/linux](https://github.com/raspberrypi/linux) (rpi-6.12.y,
commit 43c132e8863c), Copyright (C) 2020 Raspberry Pi (Trading) Ltd,
GPL-2.0. The changes are listed at the top of the file.

## Linux

`include/dt-bindings/gpio/gpio.h` is copied from the Linux kernel,
GPL-2.0 OR BSD-2-Clause.

## T3 Foundation

* `sensors/imx230/dcc/linear/` comes from imx230-dcc-profile (commit 65cf25e),
  except `lut_viss_fcc_gamma.txt`, which is TI's
  `sensor_drv/src/imx219/dcc_xmls/linear_10b/lut_viss_fcc_gamma.txt` at
  `REL.IMAGING.11.00.00.00`.
* `sensors/imx219/overlay.dtsi` and `sensors/imx230/overlay.dtsi` follow the
  `k3-am67a-t3-gem-o1-csi*-imx219.dtso` and
  `k3-am67a-t3-gem-o1-csi0-arducam-pivariety.dtso` overlays of the T3 kernel.
