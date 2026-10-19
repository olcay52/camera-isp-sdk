# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 T3 Foundation
"""GStreamer pipelines that run RAW frames through the hardware ISP.

v4l2src -> tiovxisp (VPAC VISS + TI 2A) [-> tiovxldc] [-> tiovxmultiscaler]
-> sink. All processing between capture and the sink stays on the
VPAC/DMA hardware; the A53 cores only run the 2A (AE/AWB) algorithms.
"""

import shlex
from dataclasses import dataclass

SINKS = ("display", "fake", "h264", "jpeg")


@dataclass
class Options:
    sink: str = "display"
    output: str | None = None
    frames: int | None = None
    scale: tuple | None = None
    ldc: bool = False
    framerate: bool = True


def elements(sensor, video, subdev, opts):
    mode = sensor.mode
    caps = f"video/x-bayer,width={mode.width},height={mode.height},format={mode.gst_format}"
    if opts.framerate:
        caps += f",framerate={mode.fps}/1"

    src = ["v4l2src", f"device={video}", "io-mode=dmabuf-import"]
    if opts.frames:
        src.append(f"num-buffers={opts.frames}")

    chain = [
        src,
        ["queue", "leaky=2"],
        [caps],
        ["tiovxisp",
         f"sensor-name={sensor.id}",
         f"dcc-isp-file={sensor.dcc_path('viss')}",
         f"format-msb={sensor.format_msb}",
         f"sink_0::dcc-2a-file={sensor.dcc_path('2a')}",
         f"sink_0::device={subdev}"],
        ["video/x-raw,format=NV12"],
    ]

    if opts.ldc:
        if not sensor.has_ldc:
            raise ValueError(f"{sensor.id} has no LDC tuning ([isp] dcc-ldc)")
        chain += [
            ["tiovxldc", f"sensor-name={sensor.id}", f"dcc-file={sensor.dcc_path('ldc')}"],
            [f"video/x-raw,format=NV12,width={mode.width},height={mode.height}"],
        ]

    if opts.scale:
        width, height = opts.scale
        chain += [["tiovxmultiscaler"], [f"video/x-raw,format=NV12,width={width},height={height}"]]

    if opts.sink == "display":
        chain.append(["kmssink", "driver-name=tidss", "sync=false"])
    elif opts.sink == "fake":
        chain.append(["fpsdisplaysink", "text-overlay=false", "video-sink=fakesink", "sync=false"])
    elif opts.sink == "h264":
        chain += [["v4l2h264enc"], ["h264parse"], ["mp4mux"],
                  ["filesink", f"location={opts.output or f'{sensor.id}.mp4'}"]]
    elif opts.sink == "jpeg":
        # Keep only the last frame, after AE/AWB had time to converge.
        chain += [["jpegenc"],
                  ["multifilesink", f"location={opts.output or f'{sensor.id}.jpg'}", "max-files=1"]]
    else:
        raise ValueError(f"unknown sink '{opts.sink}' (expected {', '.join(SINKS)})")
    return chain


def command(sensor, video, subdev, opts):
    chain = elements(sensor, video, subdev, opts)
    args = ["gst-launch-1.0", "-e"]
    if opts.sink == "fake":
        args.append("-v")
    for i, element in enumerate(chain):
        if i:
            args.append("!")
        args += element
    return args


def format_command(args):
    """Formats a gst-launch command over several lines for humans."""
    lines, current = [], []
    for arg in args:
        if arg == "!":
            lines.append(" ".join(shlex.quote(a) for a in current) + " !")
            current = []
        else:
            current.append(arg)
    lines.append(" ".join(shlex.quote(a) for a in current))
    return " \\\n    ".join(lines)
