# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 T3 Foundation
"""Static checks of a sensor descriptor against a board.

These catch the failures that otherwise only show up on hardware, often
silently: a width the capture DMA cannot take, a DCC ID the ISP ignores,
an H3A grid outside the frame, a link the D-PHY cannot run.
"""

from . import dcc

# DCC IDs of the sensors built into TI's Edge AI 11.0 stack
TI_DCC_IDS = {219: "IMX219", 233: "AR0233", 390: "IMX390", 820: "AR0820", 2312: "OV2312", 5: "OX05B1S"}


def check(sensor, board, all_sensors=None):
    """Returns (errors, warnings), both lists of strings."""
    errors, warnings = [], []
    mode = sensor.mode

    if mode.bits not in board.bit_depths:
        errors.append(f"{mode.code}: {board.name} captures {', '.join(map(str, board.bit_depths))}-bit RAW only")
    align = board.width_alignment(mode.bits)
    if mode.width % align:
        lower = mode.width // align * align
        errors.append(f"width {mode.width} is not a multiple of {align} pixels; the CSI-2 capture DMA "
                      f"would round it to {lower} and link validation fails (EPIPE). "
                      f"Crop the sensor output to {lower} or {lower + align}")
    if mode.width > board.max_width or mode.height > board.max_height:
        errors.append(f"{mode.width}x{mode.height} exceeds {board.max_width}x{board.max_height}")

    if sensor.format_msb != mode.bits - 1:
        warnings.append(f"format-msb {sensor.format_msb} does not match {mode.bits}-bit data "
                        f"(expected {mode.bits - 1})")

    lane_rate = sensor.link_frequency * 2 / 1e6
    payload = mode.width * mode.height * mode.fps * mode.bits / 1e6
    link = sensor.lanes * lane_rate
    if not sensor.ports:
        errors.append("[overlay] ports is empty")
    for port_name in sensor.ports:
        port = board.ports.get(port_name)
        if port is None:
            errors.append(f"port '{port_name}' does not exist on {board.name} "
                          f"(ports: {', '.join(board.ports)})")
            continue
        if not port.header.is_file():
            errors.append(f"{port_name} header {port.header} does not exist")
        if sensor.lanes > port.lanes:
            errors.append(f"{sensor.lanes} data lanes requested, {port_name} has {port.lanes}")
        if lane_rate > port.max_lane_rate_mbps:
            errors.append(f"{lane_rate:.0f} Mbps per lane exceeds the {port.max_lane_rate_mbps} Mbps "
                          f"D-PHY limit of {port_name}")
    if payload > link:
        errors.append(f"{mode.width}x{mode.height}@{mode.fps} {mode.bits}-bit needs {payload:.0f} Mbps, "
                      f"the link carries {link:.0f} Mbps")
    elif payload > 0.9 * link:
        warnings.append(f"{mode.width}x{mode.height}@{mode.fps} uses {payload / link:.0%} of the CSI-2 "
                        "link; little room is left for blanking")

    if not sensor.overlay.is_file():
        errors.append(f"overlay fragment {sensor.overlay} does not exist")
    if sensor.driver_dir is not None and not (sensor.driver_dir / "Kbuild").is_file():
        errors.append(f"driver directory {sensor.driver_dir} has no Kbuild file")

    frame_us = 1e6 / mode.fps
    for i, r in enumerate(sensor.ae_ranges):
        if r.exposure_max_us > frame_us:
            warnings.append(f"[ae] range{i}: exposure-max-us {r.exposure_max_us} is longer than a "
                            f"{mode.fps} fps frame ({frame_us:.0f} us); the driver will clamp it")

    if sensor.dcc_source is None:
        if sensor.dcc_id not in TI_DCC_IDS:
            warnings.append(f"no [dcc] sources; {sensor.dcc_dir} must already hold DCC files "
                            f"with module ID {sensor.dcc_id}")
    else:
        if sensor.dcc_bits is None:
            warnings.append("[dcc] bits is not set; cannot check that the DCC sources match "
                            f"the {mode.bits}-bit sensor data")
        elif sensor.dcc_bits != mode.bits:
            errors.append(f"DCC sources are for {sensor.dcc_bits}-bit data, the mode is {mode.bits}-bit; "
                          "the decompand, H3A mux and CFA LUTs and the black level depend on it "
                          "(docs/tuning.md)")
        dcc_errors, dcc_warnings = dcc.check_sources(sensor)
        errors += dcc_errors
        warnings += dcc_warnings
        if sensor.dcc_id in TI_DCC_IDS:
            warnings.append(f"dcc-id {sensor.dcc_id} is also used by TI's {TI_DCC_IDS[sensor.dcc_id]} "
                            "tuning; pick a distinct ID to avoid mixing up DCC files")

    for other in (all_sensors or {}).values():
        if other.id != sensor.id and other.dcc_id == sensor.dcc_id and sensor.dcc_source is not None:
            errors.append(f"dcc-id {sensor.dcc_id} is also used by sensor '{other.id}'")

    for path in (sensor.path, sensor.overlay):
        if path.is_file() and "TODO" in path.read_text(errors="replace"):
            warnings.append(f"{path.name} still has TODO markers")

    if sensor.status != "supported":
        warnings.append(f"status is '{sensor.status}': not yet verified end to end on hardware")

    return errors, warnings
