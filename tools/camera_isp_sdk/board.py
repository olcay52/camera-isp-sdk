# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 T3 Foundation
"""Board descriptions: boards/<id>/board.conf."""

from dataclasses import dataclass
from pathlib import Path

from .conf import Conf, ConfError


@dataclass
class Port:
    name: str
    header: Path
    lanes: int
    max_lane_rate_mbps: int
    description: str


@dataclass
class Board:
    id: str
    dir: Path
    name: str
    soc: str
    edgeai_sdk: str
    overlay_prefix: str
    ports: dict
    word_bytes: int
    bit_depths: list
    max_width: int
    max_height: int

    def overlay_name(self, port, sensor_id):
        return f"{self.overlay_prefix}-{port}-{sensor_id}"

    def width_alignment(self, bits):
        """Pixels per capture DMA word for a sensor bit depth."""
        container_bits = 8 if bits <= 8 else 16
        return self.word_bytes * 8 // container_bits


def load(board_dir):
    board_dir = Path(board_dir)
    conf = Conf(board_dir / "board.conf")

    ports = {}
    for name in conf.list("board", "ports", required=True):
        section = f"port:{name}"
        if not conf.has(section):
            raise ConfError(f"{conf.path}: [{section}] is missing")
        ports[name] = Port(
            name=name,
            header=board_dir / conf.str(section, "header", required=True),
            lanes=conf.int(section, "lanes", required=True, lo=1, hi=4),
            max_lane_rate_mbps=conf.int(section, "max-lane-rate-mbps", required=True, lo=1),
            description=conf.str(section, "description", name),
        )

    return Board(
        id=conf.str("board", "id", required=True),
        dir=board_dir,
        name=conf.str("board", "name", required=True),
        soc=conf.str("board", "soc", required=True),
        edgeai_sdk=conf.str("board", "edgeai-sdk", required=True),
        overlay_prefix=conf.str("board", "overlay-prefix", required=True),
        ports=ports,
        word_bytes=conf.int("capture", "word-bytes", required=True, lo=1),
        bit_depths=conf.int_list("capture", "bit-depths", required=True),
        max_width=conf.int("capture", "max-width", required=True, lo=1),
        max_height=conf.int("capture", "max-height", required=True, lo=1),
    )
