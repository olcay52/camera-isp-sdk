# @NAME@

Status: untested

## Bring-up checklist

- [ ] Driver probes and the sensor shows up in `media-ctl -p`
- [ ] `camera-isp-sdk validate @ID@` passes
- [ ] `camera-isp-sdk setup @ID@` configures the mode without a format mismatch
- [ ] RAW capture works: `v4l2-ctl -d /dev/video-@ID@-cam0 --stream-mmap --stream-count=10`
- [ ] `camera-isp-sdk pipeline @ID@ --sink fake --run` reaches the mode's frame rate
- [ ] AE converges (`GST_DEBUG=tiovxisp:6` shows the exposure and gain settling)
- [ ] Colours and white balance tuned (see docs/tuning.md)

## Notes

DCC sources were bootstrapped from @DCC_FROM@ with DCC ID @DCC_ID@ and an H3A grid
for @WIDTH@x@HEIGHT@. The colour matrix, gamma, noise filter and AWB tuning are
@DCC_FROM@'s until the sensor is tuned.
