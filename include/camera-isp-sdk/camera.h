/* SPDX-License-Identifier: GPL-2.0 OR MIT */
/*
 * Helpers for sensor overlay fragments (sensors/<id>/overlay.dtsi).
 *
 * A board port header (boards/<board>/<port>.h) defines CAM_PORT and the
 * CAM_* node labels of one camera connector. CAM_LABEL() derives labels
 * that are unique per port, so the same sensor can be loaded on two
 * connectors at once.
 *
 * Copyright (C) 2026 T3 Foundation
 */

#ifndef __CAMERA_ISP_SDK_CAMERA_H__
#define __CAMERA_ISP_SDK_CAMERA_H__

#define __CAM_CAT(a, b)		a ## b
#define _CAM_CAT(a, b)		__CAM_CAT(a, b)

/* CAM_LABEL(imx477) -> imx477_csi0 on CSI0 */
#define CAM_LABEL(name)		_CAM_CAT(name, _CAM_CAT(_, CAM_PORT))

/* Endpoints linking the sensor and the CSI-2 bridge of this port */
#define CAM_SENSOR_EP		CAM_LABEL(cam_sensor_ep)
#define CAM_RX_EP		CAM_LABEL(cam_rx_ep)

#endif /* __CAMERA_ISP_SDK_CAMERA_H__ */
