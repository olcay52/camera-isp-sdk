# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 T3 Foundation
"""Key-file helpers.

Descriptors are read both here and by GLib's GKeyFile in the GStreamer
plugin (gst/src/camera_isp_sdk_sensor.c), so only the common subset is accepted:
whole-line '#' comments, key=value pairs, ';'-separated lists.
"""

import configparser
from pathlib import Path


class ConfError(Exception):
    pass


class Conf:
    def __init__(self, path):
        self.path = Path(path)
        self._cp = configparser.ConfigParser(
            interpolation=None,
            comment_prefixes=("#",),
            inline_comment_prefixes=None,
            delimiters=("=",),
            strict=True,
            empty_lines_in_values=False,
        )
        self._cp.optionxform = str
        try:
            with open(self.path, encoding="utf-8") as f:
                self._cp.read_file(f)
        except (OSError, configparser.Error) as e:
            raise ConfError(f"{self.path}: {e}") from None

    def _where(self, section, key):
        return f"{self.path}: [{section}] {key}"

    def sections(self):
        return self._cp.sections()

    def has(self, section, key=None):
        if key is None:
            return self._cp.has_section(section)
        return self._cp.has_option(section, key)

    def keys(self, section):
        return list(self._cp[section].keys()) if self.has(section) else []

    def str(self, section, key, default=None, required=False):
        if not self.has(section, key):
            if required:
                raise ConfError(f"{self._where(section, key)}: required")
            return default
        return self._cp.get(section, key).strip()

    def int(self, section, key, default=None, required=False, lo=None, hi=None):
        raw = self.str(section, key, required=required)
        if raw is None:
            return default
        try:
            value = int(raw, 0)
        except ValueError:
            raise ConfError(f"{self._where(section, key)}: expected an integer, got '{raw}'") from None
        if lo is not None and value < lo:
            raise ConfError(f"{self._where(section, key)}: {value} is below the minimum {lo}")
        if hi is not None and value > hi:
            raise ConfError(f"{self._where(section, key)}: {value} is above the maximum {hi}")
        return value

    def float(self, section, key, default=None, required=False):
        raw = self.str(section, key, required=required)
        if raw is None:
            return default
        try:
            return float(raw)
        except ValueError:
            raise ConfError(f"{self._where(section, key)}: expected a number, got '{raw}'") from None

    def bool(self, section, key, default=False):
        raw = self.str(section, key)
        if raw is None:
            return default
        if raw.lower() in ("true", "1"):
            return True
        if raw.lower() in ("false", "0"):
            return False
        raise ConfError(f"{self._where(section, key)}: expected true or false, got '{raw}'")

    def list(self, section, key, default=None, required=False):
        raw = self.str(section, key, required=required)
        if raw is None:
            return [] if default is None else default
        items = [item.strip() for item in raw.split(";")]
        if items and items[-1] == "":
            items.pop()
        return items

    def int_list(self, section, key, required=False):
        out = []
        for item in self.list(section, key, required=required):
            try:
                out.append(int(item, 0))
            except ValueError:
                raise ConfError(f"{self._where(section, key)}: '{item}' is not an integer") from None
        return out
