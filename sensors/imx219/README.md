# Sony IMX219 (Raspberry Pi Camera v2)

Status: supported

Reference sensor. TI supports it in Edge AI 11.0 and ships its tuning in
`/opt/imaging/imx219/linear/`, so this descriptor builds no DCC files and uses
the mainline `imx219` driver. Its purpose is to validate the descriptor path
of the patched `tiovxisp` against TI's built-in one on known-good hardware.

## Comparing against TI's path

```bash
sudo camera-isp-sdk setup imx219

# camera-isp-sdk descriptor path
camera-isp-sdk pipeline imx219 --sink jpeg --output descriptor.jpg --run

# TI's built-in path, same tuning
gst-launch-1.0 -e v4l2src device=/dev/video-imx219-cam0 io-mode=dmabuf-import num-buffers=90 ! \
    queue leaky=2 ! video/x-bayer,width=1920,height=1080,format=rggb10,framerate=30/1 ! \
    tiovxisp sensor-name=SENSOR_SONY_IMX219_RPI \
        dcc-isp-file=/opt/imaging/imx219/linear/dcc_viss_10b_1920x1080.bin format-msb=9 \
        sink_0::dcc-2a-file=/opt/imaging/imx219/linear/dcc_2a_10b_1920x1080.bin \
        sink_0::device=/dev/v4l-imx219-subdev0 ! \
    video/x-raw,format=NV12 ! jpegenc ! multifilesink location=ti.jpg max-files=1
```

Colours should match. Exposure may not: TI's 11.0 conversion of the IMX219
exposure is about 1000 times too large (fixed upstream after 11.0), so on its
path the exposure sits at the maximum and only the gain moves. The
descriptor path derives lines from `PIXEL_RATE` and `HBLANK`.

## Bring-up checklist

- [ ] `camera-isp-sdk doctor` passes
- [ ] `camera-isp-sdk setup imx219` configures 1920x1080 RAW10
- [ ] `camera-isp-sdk pipeline imx219 --sink fake --run` reaches 30 fps
- [ ] Descriptor and TI paths give matching colours
- [ ] AE converges with both exposure and gain moving
