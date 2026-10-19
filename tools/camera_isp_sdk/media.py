# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 T3 Foundation
"""Media controller set-up on the target.

Finds sensors described by a descriptor in the media graphs, propagates the
descriptor's mode through the CSI-2 bridge and receiver, and creates the
/dev/video-<id>-camN and /dev/v4l-<id>-subdevN links that TI's Edge AI
applications expect. Mirrors the direct-attach branch of TI's
setup_cameras.sh, driven by descriptors instead of per-sensor code.
"""

import glob
import os
import re
import subprocess
from dataclasses import dataclass, field

ENTITY_RE = re.compile(r"^- entity (\d+): (.+?) \(\d+ pads?")
DEVNODE_RE = re.compile(r"device node name (\S+)")
# media-ctl from v4l-utils 1.28 (the T3 Edge AI image) prints SINK/SOURCE,
# older releases Sink/Source.
PAD_RE = re.compile(r"^\s*pad(\d+): (Sink|Source)", re.IGNORECASE)
LINK_RE = re.compile(r'^\s*(->|<-) "([^"]+)":(\d+) \[([^\]]*)\]')
FMT_RE = re.compile(r"fmt:(\w+)/(\d+)x(\d+)")


class MediaError(Exception):
    pass


@dataclass
class Link:
    direction: str
    entity: str
    pad: int
    flags: str

    @property
    def enabled(self):
        return "ENABLED" in self.flags


@dataclass
class Pad:
    index: int
    kind: str
    fmt: tuple | None = None
    links: list = field(default_factory=list)


@dataclass
class Entity:
    id: int
    name: str
    devnode: str | None = None
    pads: dict = field(default_factory=dict)

    def remote(self, pad=0, direction="->"):
        p = self.pads.get(pad)
        for link in (p.links if p else []):
            if link.direction == direction and link.enabled:
                return link
        return None


def parse_topology(text):
    """Parses `media-ctl -p` output into {name: Entity}."""
    entities = {}
    entity = pad = None
    for line in text.splitlines():
        m = ENTITY_RE.match(line)
        if m:
            entity = Entity(int(m.group(1)), m.group(2))
            entities[entity.name] = entity
            pad = None
            continue
        if entity is None:
            continue
        m = DEVNODE_RE.search(line)
        if m and pad is None:
            entity.devnode = m.group(1)
            continue
        m = PAD_RE.match(line)
        if m:
            pad = Pad(int(m.group(1)), m.group(2).capitalize())
            entity.pads[pad.index] = pad
            continue
        if pad is None:
            continue
        m = LINK_RE.match(line)
        if m:
            pad.links.append(Link(m.group(1), m.group(2), int(m.group(3)), m.group(4)))
            continue
        m = FMT_RE.search(line)
        if m and pad.fmt is None:
            pad.fmt = (m.group(1), int(m.group(2)), int(m.group(3)))
    return entities


def _media_ctl(device, *args):
    try:
        return subprocess.run(["media-ctl", "-d", device, *args], check=True,
                              capture_output=True, text=True).stdout
    except FileNotFoundError:
        raise MediaError("media-ctl not found; install v4l-utils") from None
    except subprocess.CalledProcessError as e:
        raise MediaError(f"media-ctl -d {device} {' '.join(args)}: {e.stderr.strip()}") from None


@dataclass
class Camera:
    sensor: object
    index: int
    media: str
    entity: str
    subdev: str
    bridge: str
    receiver: str
    video: str
    active_fmt: tuple | None = None

    @property
    def video_link(self):
        return f"/dev/video-{self.sensor.id}-cam{self.index}"

    @property
    def subdev_link(self):
        return f"/dev/v4l-{self.sensor.id}-subdev{self.index}"


def find_cameras(sensors, media_devices=None):
    """Finds every descriptor-backed sensor in the media graphs."""
    cameras = []
    counts = {}
    for device in media_devices or sorted(glob.glob("/dev/media*")):
        entities = parse_topology(_media_ctl(device, "-p"))
        for entity in entities.values():
            sensor = next((s for s in sensors.values() if entity.name.startswith(f"{s.entity} ")), None)
            if sensor is None:
                continue
            bridge_link = entity.remote(0)
            if bridge_link is None or "csi-bridge" not in bridge_link.entity:
                raise MediaError(f"{device}: '{entity.name}' is not linked to a CSI-2 bridge; "
                                 "serializer (FPD-Link/GMSL) set-ups are not handled")
            bridge = entities[bridge_link.entity]
            receiver_link = next((link for p in bridge.pads.values() if p.kind == "Source"
                                  for link in p.links if link.enabled and "ticsi2rx" in link.entity), None)
            if receiver_link is None:
                raise MediaError(f"{device}: '{bridge.name}' is not linked to a TI CSI-2 receiver")
            receiver = entities[receiver_link.entity]
            context = entities.get(f"{receiver.name} context 0")
            if context is None or context.devnode is None:
                raise MediaError(f"{device}: '{receiver.name} context 0' video node not found")
            index = counts.get(sensor.id, 0)
            counts[sensor.id] = index + 1
            cameras.append(Camera(sensor, index, device, entity.name, entity.devnode,
                                  bridge.name, receiver.name, context.devnode))
    return cameras


def configure(camera, dry_run=False, log=print):
    """Sets the descriptor mode along sensor -> bridge -> receiver."""
    fmt = camera.sensor.mode.media_ctl_format
    commands = [
        ["--set-v4l2", f'"{camera.entity}":0 {fmt}'],
        ["--set-v4l2", f'"{camera.bridge}":0 {fmt}'],
        ["--set-v4l2", f'"{camera.receiver}":0 {fmt}'],
    ]
    for args in commands:
        log(f"media-ctl -d {camera.media} {args[0]} '{args[1]}'")
        if not dry_run:
            _media_ctl(camera.media, *args)

    if dry_run:
        camera.active_fmt = (camera.sensor.mode.code, camera.sensor.mode.width, camera.sensor.mode.height)
    else:
        out = _media_ctl(camera.media, "--get-v4l2", f'"{camera.entity}":0')
        m = FMT_RE.search(out)
        camera.active_fmt = (m.group(1), int(m.group(2)), int(m.group(3))) if m else None

    for link, target in ((camera.video_link, camera.video), (camera.subdev_link, camera.subdev)):
        log(f"ln -snf {target} {link}")
        if not dry_run:
            tmp = f"{link}.tmp"
            if os.path.lexists(tmp):
                os.unlink(tmp)
            os.symlink(target, tmp)
            os.replace(tmp, link)

    wanted = (camera.sensor.mode.code, camera.sensor.mode.width, camera.sensor.mode.height)
    if camera.active_fmt != wanted:
        raise MediaError(f"{camera.entity}: driver chose {camera.active_fmt}, descriptor wants {wanted}; "
                         f"fix [mode] in {camera.sensor.path}")
    return camera
