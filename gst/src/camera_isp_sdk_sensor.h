/* SPDX-License-Identifier: Apache-2.0 */
/*
 * Descriptor-driven camera sensor support for the TI tiovxisp/tiovxldc
 * GStreamer elements.
 *
 * TI's elements only know the sensors that are hard-coded in
 * gsttiovxisp.c and tiovx_sensor_module.c. This module lets a sensor be
 * described by a key file (sensor.conf) instead, so adding a sensor does
 * not require touching the plugin again.
 *
 * Copyright (C) 2026 T3 Foundation
 */

#ifndef __CAMERA_ISP_SDK_SENSOR_H__
#define __CAMERA_ISP_SDK_SENSOR_H__

#include <glib.h>

G_BEGIN_DECLS

#define CAMERA_ISP_SDK_SENSOR_PATH_ENV "CAMERA_ISP_SDK_SENSOR_PATH"
#define CAMERA_ISP_SDK_SENSOR_PATH_DEFAULT \
  "/etc/camera-isp-sdk/sensors:/usr/share/camera-isp-sdk/sensors"

/* Same limit as MAX_AE_DYN_PARAMS in TI's iss_sensors.h */
#define CAMERA_ISP_SDK_MAX_AE_RANGES 10

/* TI 2A expresses analog gain in Q10: 1024 = 1x */
#define CAMERA_ISP_SDK_GAIN_UNITY 1024

#define CAMERA_ISP_SDK_SENSOR_ERROR (camera_isp_sdk_sensor_error_quark ())

typedef enum
{
  CAMERA_ISP_SDK_SENSOR_ERROR_INVALID,
  CAMERA_ISP_SDK_SENSOR_ERROR_V4L2,
} CameraIspSdkSensorError;

typedef enum
{
  /* V4L2_CID_EXPOSURE counts sensor lines (most Sony/OmniVision drivers) */
  CAMERA_ISP_SDK_EXPOSURE_LINES,
  /* V4L2_CID_EXPOSURE counts fixed time steps of exposure_unit_ns */
  CAMERA_ISP_SDK_EXPOSURE_TIME,
} CameraIspSdkExposureModel;

typedef enum
{
  /* code = offset + scale * gain */
  CAMERA_ISP_SDK_GAIN_LINEAR,
  /* code = k - k / gain (Sony IMX219: k = 256, IMX477: k = 1024) */
  CAMERA_ISP_SDK_GAIN_INVERSE,
  /* code = 20 * log10 (gain) / step_db */
  CAMERA_ISP_SDK_GAIN_DB,
  /* explicit gain -> code points, picks the largest gain not above target */
  CAMERA_ISP_SDK_GAIN_TABLE,
} CameraIspSdkGainModel;

typedef struct
{
  guint exposure_min_us;
  guint exposure_max_us;
  guint again_min;              /* Q10, 1024 = 1x */
  guint again_max;
  guint dgain_min;              /* ISP digital gain, 256 = 1x */
  guint dgain_max;
} CameraIspSdkAeRange;

typedef struct
{
  guint gain;                   /* Q10, 1024 = 1x */
  gint code;
} CameraIspSdkGainPoint;

typedef struct
{
  gchar *id;
  gchar *path;
  gint dcc_id;

  /* [ae] */
  guint target_brightness;
  guint target_brightness_min;
  guint target_brightness_max;
  guint threshold;
  guint exposure_step;
  gboolean enable_blc;
  CameraIspSdkAeRange ae_ranges[CAMERA_ISP_SDK_MAX_AE_RANGES];
  guint n_ae_ranges;

  /* [exposure] */
  CameraIspSdkExposureModel exposure_model;
  guint64 line_time_ns;         /* 0: derive from the V4L2 subdev */
  guint exposure_unit_ns;

  /* [gain] */
  CameraIspSdkGainModel gain_model;
  gdouble gain_k;
  gdouble gain_scale;
  gdouble gain_offset;
  gdouble gain_step_db;
  CameraIspSdkGainPoint *gain_table;
  guint n_gain_table;

  /* Line time read from the subdev on first use, valid for one stream */
  guint64 cached_line_time_ns;
} CameraIspSdkSensor;

/*
 * Looks up "<dir>/<name>/sensor.conf" and "<dir>/<name>.conf" in
 * $CAMERA_ISP_SDK_SENSOR_PATH (or CAMERA_ISP_SDK_SENSOR_PATH_DEFAULT).
 *
 * Returns NULL without setting @error when no descriptor exists, so the
 * caller can fall back to TI's built-in sensors. Returns NULL and sets
 * @error when a descriptor exists but is invalid.
 */
CameraIspSdkSensor *camera_isp_sdk_sensor_find (const gchar * name,
    GError ** error);

GQuark camera_isp_sdk_sensor_error_quark (void);

CameraIspSdkSensor *camera_isp_sdk_sensor_load_file (const gchar * path,
    const gchar * default_id, GError ** error);

void camera_isp_sdk_sensor_free (CameraIspSdkSensor * sensor);

gint64 camera_isp_sdk_exposure_to_lines (guint exposure_us,
    guint64 line_time_ns);

gint64 camera_isp_sdk_gain_to_code (const CameraIspSdkSensor * sensor,
    guint gain);

/*
 * Converts the TI 2A output (exposure in microseconds, analog gain in Q10)
 * into V4L2_CID_EXPOSURE / V4L2_CID_ANALOGUE_GAIN values for the sensor
 * subdev open on @fd, clamped to the ranges the driver reports.
 */
gboolean camera_isp_sdk_sensor_map_2a (CameraIspSdkSensor * sensor, gint fd,
    guint exposure_us, guint gain, gint32 * exposure_ctrl,
    gint32 * gain_ctrl, GError ** error);

G_DEFINE_AUTOPTR_CLEANUP_FUNC (CameraIspSdkSensor, camera_isp_sdk_sensor_free)

G_END_DECLS

#endif /* __CAMERA_ISP_SDK_SENSOR_H__ */
