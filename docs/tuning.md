# ISP tuning (DCC)

The VISS and the 2A algorithms are configured by DCC (Dynamic Camera
Configuration) binaries, compiled from XML sources with TI's `dcc_gen_linux`.
A sensor ships three bundles in `/opt/imaging/<id>/linear/`:

| Bundle | Used by | Contents |
|--------|---------|----------|
| `dcc_viss.bin` | `tiovxisp dcc-isp-file` | Every VISS block below |
| `dcc_2a.bin` | `tiovxisp sink_0::dcc-2a-file` | AWB tuning and the H3A grid |
| `dcc_ldc.bin` | `tiovxldc dcc-file` | Lens distortion mesh (optional, lens specific) |

## What each component depends on

| Component | Depends on | Notes |
|-----------|------------|-------|
| `VISS_BLC` black level | sensor, **bit depth** | `dcoffset_clamp_value` = −black level in input units: IMX219 −64 (10-bit), IMX390 −240, IMX477 −256 (12-bit). 0 leaves black lifted. |
| `VISS_RAWFE_DECOMPAND` | **bit depth** | TI's 10-bit tuning expands to 16 bits through a LUT (`lut_remap_10bto16b.txt`); its 12-bit linear tuning disables the block. |
| `VISS_H3A_MUX_LUTS_CFG` | **bit depth** | Maps the pipeline data to the 10-bit H3A input: `lut_remap_16bto10b.txt` after 10-bit expansion, `lut_h3a_12b_to_10b.txt` for 12-bit. |
| `VISS_CFA` | **bit depth**, **Bayer order** | Demosaic coefficients, a 6x6 kernel per phase of the 2x2 Bayer cell, and the CFA input LUT. `camera-isp-sdk dcc fit-cfa` moves the kernels to the phases of `[mode] code`. |
| `VISS_NSF4` | sensor noise, **bit depth** | Noise thresholds are in pipeline units. |
| `VISS_H3A_AEWB_CFG` | **frame size** | Statistics windows in pixels; `camera-isp-sdk dcc fit-h3a` recomputes them. |
| `VISS_RGB2RGB` | sensor colour response | Colour correction matrix, from a colour chart. |
| `VISS_GAMMA` | taste | Output tone curve. |
| `AWB_ALG_TI3_TUNING` | sensor colour response | Reference illuminants for AWB, from grey card captures. |
| `LDC_MESH` | lens | Only for fixed lenses. |

Every component's header carries `camera_module_id`. It must equal the
descriptor's `[isp] dcc-id` (or be 65535, which TI's parser accepts for any
camera). On a mismatch the parser stops at that component and prints
`Invalid Camera Id` (on the console for the 2A, in the remote core log
`/opt/vision_apps/vx_app_arm_remote_log.out` for the VISS), and the ISP runs
on defaults. The pipeline keeps running, so the only symptom is a bad image.

## Building

```bash
./camera-isp-sdk dcc fetch-tools        # once; pinned REL.IMAGING.11.00.00.00, SHA-256 checked
# git.ti.com down? Download dcc_gen_linux by hand, then:
#   ./camera-isp-sdk dcc fetch-tools --from ~/Downloads/dcc_gen_linux
./camera-isp-sdk dcc build imx477       # -> build/dcc/imx477/dcc_{viss,2a}.bin
./camera-isp-sdk dcc inspect build/dcc/imx477/dcc_viss.bin --dcc-id 477
```

Components are concatenated in file name order, the same way TI's
`generate_dcc.sh` does it. The imx230 sources are those of the
imx230-dcc-profile repository with TI's gamma LUT and black level 64 (see
`sensors/imx230/README.md`), so the binaries no longer match it.

## Bootstrapping a new sensor

`camera-isp-sdk new --dcc-from <sensor>` copies another sensor's sources, rewrites
the module ID, fits the H3A grid and moves the demosaic coefficients to the
new sensor's Bayer order. That gives a first image, but only the
**bit-depth dependent** components can be correct, and only if the template
has the same bit depth:

* 10-bit sensors: start from `imx230` (TI's IMX219 10-bit pipeline).
* 12-bit sensors: start from `imx477`, whose decompand, H3A mux LUT, CFA,
  NSF4 and black level come from TI's 12-bit IMX390 linear tuning.

LDC is not copied: the mesh belongs to the template's lens.

Then set the black level for the sensor (datasheet, or the `black_level` of
the libcamera tuning file divided down to the sensor bit depth). Colours and
white balance stay the template's until the sensor is tuned.

## Tuning properly

TI's DCC tuning tool (available from TI on request, see the "DCC Tuning
Tool" on ti.com) generates every XML above from captures:

1. Capture RAW frames on the board, without the ISP:

   ```bash
   sudo camera-isp-sdk setup imx477
   v4l2-ctl -d /dev/video-imx477-cam0 --stream-mmap --stream-skip=30 \
       --stream-count=1 --stream-to=frame.raw
   # set exposure and gain by hand for each scene:
   v4l2-ctl -d /dev/v4l-imx477-subdev0 -c exposure=1000,analogue_gain=0
   ```

   Frames are 16 bits per pixel, little-endian, `width × height × 2` bytes.

2. Capture what the tool asks for: dark frames (black level, noise), a
   Macbeth chart under several illuminants (CCM, AWB), a flat field (lens
   shading, if used).

3. Generate the XMLs with the tool, set `camera_module_id` to the
   descriptor's `dcc-id`, and replace the files in `sensors/<id>/dcc/linear/`.

4. Rebuild and compare:

   ```bash
   ./camera-isp-sdk validate imx477 && ./camera-isp-sdk dcc build imx477
   ```

When changing the sensor mode, run `camera-isp-sdk dcc fit-h3a <id>` so the
statistics grid matches the new frame size, and after changing `[mode] code`,
`camera-isp-sdk dcc fit-cfa <id>`; `validate` fails otherwise.
