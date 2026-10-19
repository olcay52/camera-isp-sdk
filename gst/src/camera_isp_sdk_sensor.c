/* SPDX-License-Identifier: Apache-2.0 */
/*
 * Descriptor-driven camera sensor support for the TI tiovxisp/tiovxldc
 * GStreamer elements. See camera_isp_sdk_sensor.h and
 * docs/sensor-descriptor.md.
 *
 * Copyright (C) 2026 T3 Foundation
 */

#include "camera_isp_sdk_sensor.h"

#include <errno.h>
#include <math.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>

#include <linux/v4l2-subdev.h>
#include <linux/videodev2.h>

G_DEFINE_QUARK (camera-isp-sdk-sensor-error-quark, camera_isp_sdk_sensor_error)

typedef struct
{
  GKeyFile *kf;
  const gchar *path;
  GError *error;
} Loader;

static gboolean
is_valid_id (const gchar * name)
{
  const gchar *p;

  if (name == NULL || *name == '\0')
    return FALSE;

  for (p = name; *p != '\0'; p++) {
    if (!g_ascii_isalnum (*p) && *p != '_' && *p != '-')
      return FALSE;
  }

  return TRUE;
}

static void
loader_fail (Loader * ld, const gchar * group, const gchar * key,
    const gchar * format, ...) G_GNUC_PRINTF (4, 5);

static void
loader_fail (Loader * ld, const gchar * group, const gchar * key,
    const gchar * format, ...)
{
  va_list args;
  gchar *msg;

  if (ld->error != NULL)
    return;

  va_start (args, format);
  msg = g_strdup_vprintf (format, args);
  va_end (args);

  g_set_error (&ld->error, CAMERA_ISP_SDK_SENSOR_ERROR,
      CAMERA_ISP_SDK_SENSOR_ERROR_INVALID,
      "%s: [%s] %s: %s", ld->path, group, key, msg);
  g_free (msg);
}

static gchar *
loader_string (Loader * ld, const gchar * group, const gchar * key)
{
  gchar *value;

  if (ld->error != NULL || !g_key_file_has_key (ld->kf, group, key, NULL))
    return NULL;

  value = g_key_file_get_value (ld->kf, group, key, NULL);
  return value ? g_strstrip (value) : NULL;
}

static gint64
parse_int (Loader * ld, const gchar * group, const gchar * key,
    const gchar * str, gint64 min, gint64 max)
{
  gchar *end = NULL;
  gint64 value;

  errno = 0;
  value = g_ascii_strtoll (str, &end, 0);
  if (*str == '\0' || end == NULL || *end != '\0' || errno != 0
      || value < min || value > max) {
    loader_fail (ld, group, key, "expected an integer in [%" G_GINT64_FORMAT
        ", %" G_GINT64_FORMAT "], got '%s'", min, max, str);
    return min;
  }

  return value;
}

static gint64
loader_int (Loader * ld, const gchar * group, const gchar * key,
    gint64 def, gint64 min, gint64 max)
{
  gchar *str = loader_string (ld, group, key);
  gint64 value;

  if (str == NULL)
    return def;

  value = parse_int (ld, group, key, str, min, max);
  g_free (str);
  return value;
}

static gdouble
loader_double (Loader * ld, const gchar * group, const gchar * key,
    gdouble def)
{
  gchar *str = loader_string (ld, group, key);
  gchar *end = NULL;
  gdouble value;

  if (str == NULL)
    return def;

  value = g_ascii_strtod (str, &end);
  if (*str == '\0' || end == NULL || *end != '\0' || !isfinite (value)) {
    loader_fail (ld, group, key, "expected a number, got '%s'", str);
    value = def;
  }

  g_free (str);
  return value;
}

static gboolean
loader_bool (Loader * ld, const gchar * group, const gchar * key,
    gboolean def)
{
  gchar *str = loader_string (ld, group, key);
  gboolean value = def;

  if (str == NULL)
    return def;

  if (g_ascii_strcasecmp (str, "true") == 0 || strcmp (str, "1") == 0)
    value = TRUE;
  else if (g_ascii_strcasecmp (str, "false") == 0 || strcmp (str, "0") == 0)
    value = FALSE;
  else
    loader_fail (ld, group, key, "expected true or false, got '%s'", str);

  g_free (str);
  return value;
}

/* Splits "a;b;c" (GKeyFile list syntax), dropping a trailing empty item */
static gchar **
loader_list (Loader * ld, const gchar * group, const gchar * key)
{
  gchar *str = loader_string (ld, group, key);
  gchar **items;
  guint n;

  if (str == NULL)
    return NULL;

  items = g_strsplit (str, ";", -1);
  g_free (str);

  n = g_strv_length (items);
  if (n > 0 && *g_strstrip (items[n - 1]) == '\0') {
    g_free (items[n - 1]);
    items[n - 1] = NULL;
  }

  for (n = 0; items[n] != NULL; n++)
    g_strstrip (items[n]);

  return items;
}

static void
load_ae (Loader * ld, CameraIspSdkSensor * s)
{
  const gchar *g = "ae";
  guint i;

  s->target_brightness = loader_int (ld, g, "target-brightness", 45, 0, 255);
  s->target_brightness_min =
      loader_int (ld, g, "target-brightness-min", 40, 0, 255);
  s->target_brightness_max =
      loader_int (ld, g, "target-brightness-max", 50, 0, 255);
  s->threshold = loader_int (ld, g, "threshold", 1, 0, 255);
  s->exposure_step = loader_int (ld, g, "exposure-step", 1, 1, G_MAXUINT32);
  s->enable_blc = loader_bool (ld, g, "enable-blc", TRUE);

  if (ld->error == NULL && (s->target_brightness < s->target_brightness_min
          || s->target_brightness > s->target_brightness_max))
    loader_fail (ld, g, "target-brightness",
        "%u is outside [target-brightness-min, target-brightness-max]",
        s->target_brightness);

  for (i = 0; i < CAMERA_ISP_SDK_MAX_AE_RANGES && ld->error == NULL; i++) {
    gchar key[16];
    gchar **items;
    gint64 v[6];
    guint n;

    g_snprintf (key, sizeof (key), "range%u", i);
    items = loader_list (ld, g, key);
    if (items == NULL)
      break;

    if (g_strv_length (items) != 6) {
      loader_fail (ld, g, key, "expected 6 values: exposure-min-us;"
          "exposure-max-us;again-min;again-max;dgain-min;dgain-max");
      g_strfreev (items);
      break;
    }

    for (n = 0; n < 6; n++)
      v[n] = parse_int (ld, g, key, items[n], 0, G_MAXUINT32);
    g_strfreev (items);

    if (ld->error == NULL && (v[0] > v[1] || v[2] > v[3] || v[4] > v[5]))
      loader_fail (ld, g, key, "every min must be <= its max");
    if (ld->error == NULL && v[2] < CAMERA_ISP_SDK_GAIN_UNITY)
      loader_fail (ld, g, key, "again-min must be >= %d (1x)",
          CAMERA_ISP_SDK_GAIN_UNITY);

    s->ae_ranges[i].exposure_min_us = v[0];
    s->ae_ranges[i].exposure_max_us = v[1];
    s->ae_ranges[i].again_min = v[2];
    s->ae_ranges[i].again_max = v[3];
    s->ae_ranges[i].dgain_min = v[4];
    s->ae_ranges[i].dgain_max = v[5];
    s->n_ae_ranges = i + 1;
  }

  if (ld->error == NULL && s->n_ae_ranges == 0)
    loader_fail (ld, g, "range0", "at least one AE range is required");
}

static void
load_exposure (Loader * ld, CameraIspSdkSensor * s)
{
  const gchar *g = "exposure";
  gchar *model = loader_string (ld, g, "model");

  if (model == NULL || strcmp (model, "lines") == 0) {
    s->exposure_model = CAMERA_ISP_SDK_EXPOSURE_LINES;
    s->line_time_ns = loader_int (ld, g, "line-time-ns", 0, 0, G_MAXUINT32);
  } else if (strcmp (model, "time") == 0) {
    s->exposure_model = CAMERA_ISP_SDK_EXPOSURE_TIME;
    s->exposure_unit_ns = loader_int (ld, g, "unit-ns", 0, 1, G_MAXUINT32);
    if (ld->error == NULL && s->exposure_unit_ns == 0)
      loader_fail (ld, g, "unit-ns", "required for model=time");
  } else {
    loader_fail (ld, g, "model", "expected lines or time, got '%s'", model);
  }

  g_free (model);
}

static gint
compare_gain_points (gconstpointer a, gconstpointer b)
{
  const CameraIspSdkGainPoint *pa = a;
  const CameraIspSdkGainPoint *pb = b;

  return (pa->gain > pb->gain) - (pa->gain < pb->gain);
}

static void
load_gain_table (Loader * ld, CameraIspSdkSensor * s)
{
  gchar **items = loader_list (ld, "gain", "table");
  guint i, n;

  n = items ? g_strv_length (items) : 0;
  if (n == 0) {
    loader_fail (ld, "gain", "table", "required for model=table");
    g_strfreev (items);
    return;
  }

  s->gain_table = g_new0 (CameraIspSdkGainPoint, n);
  for (i = 0; i < n && ld->error == NULL; i++) {
    gchar **pair = g_strsplit (items[i], ":", 2);

    if (g_strv_length (pair) != 2) {
      loader_fail (ld, "gain", "table", "expected gain:code, got '%s'",
          items[i]);
    } else {
      s->gain_table[i].gain = parse_int (ld, "gain", "table",
          g_strstrip (pair[0]), 1, G_MAXUINT32);
      s->gain_table[i].code = parse_int (ld, "gain", "table",
          g_strstrip (pair[1]), G_MININT32, G_MAXINT32);
    }
    g_strfreev (pair);
  }
  g_strfreev (items);

  s->n_gain_table = n;
  qsort (s->gain_table, n, sizeof (CameraIspSdkGainPoint), compare_gain_points);
}

static void
load_gain (Loader * ld, CameraIspSdkSensor * s)
{
  const gchar *g = "gain";
  gchar *model = loader_string (ld, g, "model");

  if (model == NULL) {
    loader_fail (ld, g, "model", "required (linear, inverse, db or table)");
  } else if (strcmp (model, "linear") == 0) {
    s->gain_model = CAMERA_ISP_SDK_GAIN_LINEAR;
    s->gain_scale = loader_double (ld, g, "scale", 0.0);
    s->gain_offset = loader_double (ld, g, "offset", 0.0);
    if (ld->error == NULL && s->gain_scale == 0.0)
      loader_fail (ld, g, "scale", "required and non-zero for model=linear");
  } else if (strcmp (model, "inverse") == 0) {
    s->gain_model = CAMERA_ISP_SDK_GAIN_INVERSE;
    s->gain_k = loader_double (ld, g, "k", 0.0);
    if (ld->error == NULL && s->gain_k <= 0.0)
      loader_fail (ld, g, "k", "required and > 0 for model=inverse");
  } else if (strcmp (model, "db") == 0) {
    s->gain_model = CAMERA_ISP_SDK_GAIN_DB;
    s->gain_step_db = loader_double (ld, g, "step-db", 0.0);
    if (ld->error == NULL && s->gain_step_db <= 0.0)
      loader_fail (ld, g, "step-db", "required and > 0 for model=db");
  } else if (strcmp (model, "table") == 0) {
    s->gain_model = CAMERA_ISP_SDK_GAIN_TABLE;
    load_gain_table (ld, s);
  } else {
    loader_fail (ld, g, "model",
        "expected linear, inverse, db or table, got '%s'", model);
  }

  g_free (model);
}

CameraIspSdkSensor *
camera_isp_sdk_sensor_load_file (const gchar * path, const gchar * default_id,
    GError ** error)
{
  Loader ld = { NULL, path, NULL };
  CameraIspSdkSensor *s = NULL;

  g_return_val_if_fail (path != NULL, NULL);

  ld.kf = g_key_file_new ();
  if (!g_key_file_load_from_file (ld.kf, path, G_KEY_FILE_NONE, &ld.error)) {
    g_prefix_error (&ld.error, "%s: ", path);
    goto out;
  }

  s = g_new0 (CameraIspSdkSensor, 1);
  s->path = g_strdup (path);
  s->id = loader_string (&ld, "sensor", "id");
  if (s->id == NULL)
    s->id = g_strdup (default_id);

  if (!g_key_file_has_key (ld.kf, "isp", "dcc-id", NULL))
    loader_fail (&ld, "isp", "dcc-id", "required");
  s->dcc_id = loader_int (&ld, "isp", "dcc-id", 0, 0, G_MAXUINT16);

  load_ae (&ld, s);
  load_exposure (&ld, s);
  load_gain (&ld, s);

out:
  g_key_file_unref (ld.kf);

  if (ld.error != NULL) {
    g_propagate_error (error, ld.error);
    g_clear_pointer (&s, camera_isp_sdk_sensor_free);
  }

  return s;
}

CameraIspSdkSensor *
camera_isp_sdk_sensor_find (const gchar * name, GError ** error)
{
  const gchar *search = g_getenv (CAMERA_ISP_SDK_SENSOR_PATH_ENV);
  CameraIspSdkSensor *sensor;
  gchar *path = NULL;
  gchar **dirs;
  guint i, c;

  /* TI names such as SENSOR_SONY_IMX219_RPI are valid ids as well, they
   * simply have no descriptor unless one is installed to override TI. */
  if (!is_valid_id (name))
    return NULL;

  if (search == NULL || *search == '\0')
    search = CAMERA_ISP_SDK_SENSOR_PATH_DEFAULT;

  dirs = g_strsplit (search, ":", -1);
  for (i = 0; dirs[i] != NULL && path == NULL; i++) {
    gchar *candidates[2];
    gchar *conf;

    if (*dirs[i] == '\0')
      continue;

    conf = g_strconcat (name, ".conf", NULL);
    candidates[0] = g_build_filename (dirs[i], name, "sensor.conf", NULL);
    candidates[1] = g_build_filename (dirs[i], conf, NULL);
    g_free (conf);

    for (c = 0; c < G_N_ELEMENTS (candidates) && path == NULL; c++) {
      if (g_file_test (candidates[c], G_FILE_TEST_IS_REGULAR))
        path = g_strdup (candidates[c]);
    }

    g_free (candidates[0]);
    g_free (candidates[1]);
  }
  g_strfreev (dirs);

  if (path == NULL)
    return NULL;

  sensor = camera_isp_sdk_sensor_load_file (path, name, error);
  g_free (path);
  return sensor;
}

void
camera_isp_sdk_sensor_free (CameraIspSdkSensor * sensor)
{
  if (sensor == NULL)
    return;

  g_free (sensor->id);
  g_free (sensor->path);
  g_free (sensor->gain_table);
  g_free (sensor);
}

gint64
camera_isp_sdk_exposure_to_lines (guint exposure_us, guint64 line_time_ns)
{
  g_return_val_if_fail (line_time_ns > 0, 0);

  return ((guint64) exposure_us * 1000 + line_time_ns / 2) / line_time_ns;
}

gint64
camera_isp_sdk_gain_to_code (const CameraIspSdkSensor * s, guint gain)
{
  gdouble x = MAX (gain, 1) / (gdouble) CAMERA_ISP_SDK_GAIN_UNITY;
  guint i;

  g_return_val_if_fail (s != NULL, 0);

  switch (s->gain_model) {
    case CAMERA_ISP_SDK_GAIN_LINEAR:
      return llround (s->gain_offset + s->gain_scale * x);
    case CAMERA_ISP_SDK_GAIN_INVERSE:
      return llround (s->gain_k - s->gain_k / x);
    case CAMERA_ISP_SDK_GAIN_DB:
      return llround (20.0 * log10 (x) / s->gain_step_db);
    case CAMERA_ISP_SDK_GAIN_TABLE:
      for (i = s->n_gain_table; i > 1; i--) {
        if (s->gain_table[i - 1].gain <= gain)
          return s->gain_table[i - 1].code;
      }
      return s->n_gain_table ? s->gain_table[0].code : 0;
  }

  return 0;
}

static gboolean
query_line_time_ns (gint fd, guint64 * line_time_ns, GError ** error)
{
  struct v4l2_ext_control rate = {.id = V4L2_CID_PIXEL_RATE };
  struct v4l2_ext_controls ctrls = {
    .which = V4L2_CTRL_WHICH_CUR_VAL,
    .count = 1,
    .controls = &rate,
  };
  struct v4l2_control hblank = {.id = V4L2_CID_HBLANK };
  struct v4l2_subdev_format fmt = {
    .which = V4L2_SUBDEV_FORMAT_ACTIVE,
    .pad = 0,
  };
  guint64 hts;

  if (ioctl (fd, VIDIOC_G_EXT_CTRLS, &ctrls) < 0 || rate.value64 <= 0) {
    g_set_error (error, CAMERA_ISP_SDK_SENSOR_ERROR,
        CAMERA_ISP_SDK_SENSOR_ERROR_V4L2,
        "sensor has no usable V4L2_CID_PIXEL_RATE; set [exposure] "
        "line-time-ns in the descriptor");
    return FALSE;
  }

  if (ioctl (fd, VIDIOC_G_CTRL, &hblank) < 0) {
    g_set_error (error, CAMERA_ISP_SDK_SENSOR_ERROR,
        CAMERA_ISP_SDK_SENSOR_ERROR_V4L2,
        "sensor has no V4L2_CID_HBLANK; set [exposure] line-time-ns in the "
        "descriptor");
    return FALSE;
  }

  if (ioctl (fd, VIDIOC_SUBDEV_G_FMT, &fmt) < 0) {
    g_set_error (error, CAMERA_ISP_SDK_SENSOR_ERROR,
        CAMERA_ISP_SDK_SENSOR_ERROR_V4L2,
        "VIDIOC_SUBDEV_G_FMT failed: %s", g_strerror (errno));
    return FALSE;
  }

  hts = (guint64) fmt.format.width + (guint64) MAX (hblank.value, 0);
  *line_time_ns = (hts * G_GUINT64_CONSTANT (1000000000) +
      (guint64) rate.value64 / 2) / (guint64) rate.value64;

  if (*line_time_ns == 0) {
    g_set_error (error, CAMERA_ISP_SDK_SENSOR_ERROR,
        CAMERA_ISP_SDK_SENSOR_ERROR_V4L2,
        "computed a zero line time (width %u, hblank %d, pixel rate %"
        G_GINT64_FORMAT ")", fmt.format.width, hblank.value,
        (gint64) rate.value64);
    return FALSE;
  }

  return TRUE;
}

static gint32
clamp_to_ctrl (gint fd, guint32 id, gint64 value)
{
  struct v4l2_queryctrl q = {.id = id };

  if (ioctl (fd, VIDIOC_QUERYCTRL, &q) < 0)
    return CLAMP (value, G_MININT32, G_MAXINT32);

  value = CLAMP (value, q.minimum, q.maximum);
  if (q.step > 1)
    value = q.minimum + ((value - q.minimum) / q.step) * q.step;

  return value;
}

gboolean
camera_isp_sdk_sensor_map_2a (CameraIspSdkSensor * s, gint fd,
    guint exposure_us, guint gain, gint32 * exposure_ctrl, gint32 * gain_ctrl,
    GError ** error)
{
  gint64 exposure;

  g_return_val_if_fail (s != NULL, FALSE);
  g_return_val_if_fail (exposure_ctrl != NULL, FALSE);
  g_return_val_if_fail (gain_ctrl != NULL, FALSE);

  if (s->exposure_model == CAMERA_ISP_SDK_EXPOSURE_LINES) {
    guint64 line_time_ns = s->line_time_ns;

    if (line_time_ns == 0) {
      if (s->cached_line_time_ns == 0
          && !query_line_time_ns (fd, &s->cached_line_time_ns, error))
        return FALSE;
      line_time_ns = s->cached_line_time_ns;
    }
    exposure = camera_isp_sdk_exposure_to_lines (exposure_us, line_time_ns);
  } else {
    exposure = ((guint64) exposure_us * 1000 + s->exposure_unit_ns / 2) /
        s->exposure_unit_ns;
  }

  *exposure_ctrl = clamp_to_ctrl (fd, V4L2_CID_EXPOSURE, exposure);
  *gain_ctrl = clamp_to_ctrl (fd, V4L2_CID_ANALOGUE_GAIN,
      camera_isp_sdk_gain_to_code (s, gain));

  return TRUE;
}
