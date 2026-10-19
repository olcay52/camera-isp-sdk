/* SPDX-License-Identifier: GPL-2.0 */
/*
 * T3 Gemstone O1 CSI0 camera connector: the 22-pin socket farther from the
 * Ethernet jack. Pin-compatible with Raspberry Pi 2-lane camera modules.
 *
 * Copyright (C) 2026 T3 Foundation
 */

#define CAM_PORT		csi0

/* Camera control bus (I2C2, exposed as hat/csi0) */
#define CAM_I2C			main_i2c2
#define CAM_I2C_PINCTRL		main_i2c2_pins_default
#define CAM_I2C_FREQ		400000

/* CAM_GPIO pin of the connector (power enable on RPi modules) */
#define CAM_GPIO_CTRL		mcu_gpio0
#define CAM_GPIO		15
#define CAM_GPIO_PINCTRL	csi0_gpio_pins_default

/* MIPI CSI-2 receive path */
#define CAM_CSI_BRIDGE		cdns_csi2rx0
#define CAM_CSI_RX		ti_csi2rx0
#define CAM_DPHY		dphy0
