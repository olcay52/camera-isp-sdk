/* SPDX-License-Identifier: GPL-2.0 */
/*
 * T3 Gemstone O1 CSI1 camera connector: the 22-pin socket closer to the
 * Ethernet jack. Its lanes are shared with DSI0 through a MIPI switch, so a
 * camera on CSI1 cannot be used together with a DSI0 panel overlay.
 *
 * Requires a base DTB that routes GPIO0_72 to the connector and has the
 * corrected CSI2RX1 interrupts (t3gemstone/linux commit ce67c36ba41a).
 *
 * Copyright (C) 2026 T3 Foundation
 */

#define CAM_PORT		csi1

/* Camera control bus (I2C0, shared with DSI0 and on-board sensors) */
#define CAM_I2C			main_i2c0
#define CAM_I2C_PINCTRL		main_i2c0_pins_default
#define CAM_I2C_FREQ		400000

/* CAM_GPIO pin of the connector (power enable on RPi modules) */
#define CAM_GPIO_CTRL		main_gpio0
#define CAM_GPIO		72
#define CAM_GPIO_PINCTRL	csi1_gpio_pins_default

/* MIPI CSI-2 receive path */
#define CAM_CSI_BRIDGE		cdns_csi2rx1
#define CAM_CSI_RX		ti_csi2rx1
#define CAM_DPHY		dphy1

/* Route the shared connector lanes to CSI1 instead of DSI0 */
#define CAM_PORT_MIPI_SWITCH
