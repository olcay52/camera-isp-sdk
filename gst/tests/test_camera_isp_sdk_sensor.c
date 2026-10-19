/* SPDX-License-Identifier: Apache-2.0 */
/*
 * Host unit tests for camera_isp_sdk_sensor.c.
 *
 * Usage: test_camera_isp_sdk_sensor [SENSORS_DIR]
 * When SENSORS_DIR is given, every <SENSORS_DIR>/<id>/sensor.conf is also
 * loaded to make sure the C parser accepts all shipped descriptors.
 *
 * Copyright (C) 2026 T3 Foundation
 */

#include <glib.h>
#include <glib/gstdio.h>

#include "camera_isp_sdk_sensor.h"

static const gchar *sensors_dir = NULL;

static const gchar valid_conf[] =
    "[sensor]\n"
    "id=test\n"
    "[isp]\n"
    "dcc-id=477\n"
    "[ae]\n"
    "target-brightness=45\n"
    "range0=100;33000;1024;16384;256;256\n"
    "range1=33000;33000;1024;22528;256;512\n"
    "[exposure]\n"
    "model=lines\n"
    "[gain]\n"
    "model=inverse\n"
    "k=1024\n";

static CameraIspSdkSensor *
load_string (const gchar * contents, GError ** error)
{
  gchar *path = NULL;
  gint fd = g_file_open_tmp ("camera-isp-sdk-test-XXXXXX.conf", &path, NULL);
  CameraIspSdkSensor *s;

  g_assert_cmpint (fd, >=, 0);
  g_assert_true (g_file_set_contents (path, contents, -1, NULL));
  s = camera_isp_sdk_sensor_load_file (path, "fallback", error);
  g_close (fd, NULL);
  g_unlink (path);
  g_free (path);
  return s;
}

static void
test_load_valid (void)
{
  g_autoptr (GError) error = NULL;
  g_autoptr (CameraIspSdkSensor) s = load_string (valid_conf, &error);

  g_assert_no_error (error);
  g_assert_nonnull (s);
  g_assert_cmpstr (s->id, ==, "test");
  g_assert_cmpint (s->dcc_id, ==, 477);
  g_assert_cmpuint (s->n_ae_ranges, ==, 2);
  g_assert_cmpuint (s->ae_ranges[1].again_max, ==, 22528);
  g_assert_cmpuint (s->ae_ranges[1].dgain_max, ==, 512);
  g_assert_cmpuint (s->target_brightness_min, ==, 40);
  g_assert_true (s->enable_blc);
  g_assert_cmpint (s->exposure_model, ==, CAMERA_ISP_SDK_EXPOSURE_LINES);
  g_assert_cmpint (s->gain_model, ==, CAMERA_ISP_SDK_GAIN_INVERSE);
}

static void
test_load_invalid (void)
{
  static const gchar *cases[] = {
    /* missing dcc-id */
    "[ae]\nrange0=1;2;1024;2048;256;256\n[gain]\nmodel=db\nstep-db=0.3\n",
    /* no AE range */
    "[isp]\ndcc-id=1\n[gain]\nmodel=db\nstep-db=0.3\n",
    /* min > max */
    "[isp]\ndcc-id=1\n[ae]\nrange0=9;2;1024;2048;256;256\n"
        "[gain]\nmodel=db\nstep-db=0.3\n",
    /* analog gain below 1x */
    "[isp]\ndcc-id=1\n[ae]\nrange0=1;2;512;2048;256;256\n"
        "[gain]\nmodel=db\nstep-db=0.3\n",
    /* wrong number of range values */
    "[isp]\ndcc-id=1\n[ae]\nrange0=1;2;1024\n[gain]\nmodel=db\nstep-db=0.3\n",
    /* unknown gain model */
    "[isp]\ndcc-id=1\n[ae]\nrange0=1;2;1024;2048;256;256\n[gain]\nmodel=x\n",
    /* inverse without k */
    "[isp]\ndcc-id=1\n[ae]\nrange0=1;2;1024;2048;256;256\n"
        "[gain]\nmodel=inverse\n",
    /* time model without unit */
    "[isp]\ndcc-id=1\n[ae]\nrange0=1;2;1024;2048;256;256\n"
        "[exposure]\nmodel=time\n[gain]\nmodel=db\nstep-db=0.3\n",
    /* not a number */
    "[isp]\ndcc-id=abc\n[ae]\nrange0=1;2;1024;2048;256;256\n"
        "[gain]\nmodel=db\nstep-db=0.3\n",
  };
  guint i;

  for (i = 0; i < G_N_ELEMENTS (cases); i++) {
    g_autoptr (GError) error = NULL;
    g_autoptr (CameraIspSdkSensor) s = load_string (cases[i], &error);

    g_test_message ("case %u: %s", i, error ? error->message : "(none)");
    g_assert_null (s);
    g_assert_nonnull (error);
  }
}

static void
test_gain_models (void)
{
  CameraIspSdkSensor s = { 0 };
  CameraIspSdkGainPoint table[] = { {1024, 0}, {2048, 20}, {4096, 40} };

  /* IMX219: gain = 256 / (256 - code) */
  s.gain_model = CAMERA_ISP_SDK_GAIN_INVERSE;
  s.gain_k = 256;
  g_assert_cmpint (camera_isp_sdk_gain_to_code (&s, 1024), ==, 0);
  g_assert_cmpint (camera_isp_sdk_gain_to_code (&s, 2048), ==, 128);
  g_assert_cmpint (camera_isp_sdk_gain_to_code (&s, 8192), ==, 224);

  /* IMX477: gain = 1024 / (1024 - code) */
  s.gain_k = 1024;
  g_assert_cmpint (camera_isp_sdk_gain_to_code (&s, 4096), ==, 768);

  /* OX05B1S style: code = 16 * gain */
  s.gain_model = CAMERA_ISP_SDK_GAIN_LINEAR;
  s.gain_scale = 16;
  s.gain_offset = 0;
  g_assert_cmpint (camera_isp_sdk_gain_to_code (&s, 1024), ==, 16);
  g_assert_cmpint (camera_isp_sdk_gain_to_code (&s, 2048), ==, 32);

  /* 0.3 dB steps: 2x = 6.02 dB = 20 steps */
  s.gain_model = CAMERA_ISP_SDK_GAIN_DB;
  s.gain_step_db = 0.3;
  g_assert_cmpint (camera_isp_sdk_gain_to_code (&s, 1024), ==, 0);
  g_assert_cmpint (camera_isp_sdk_gain_to_code (&s, 2048), ==, 20);

  s.gain_model = CAMERA_ISP_SDK_GAIN_TABLE;
  s.gain_table = table;
  s.n_gain_table = G_N_ELEMENTS (table);
  g_assert_cmpint (camera_isp_sdk_gain_to_code (&s, 512), ==, 0);
  g_assert_cmpint (camera_isp_sdk_gain_to_code (&s, 1024), ==, 0);
  g_assert_cmpint (camera_isp_sdk_gain_to_code (&s, 3000), ==, 20);
  g_assert_cmpint (camera_isp_sdk_gain_to_code (&s, 99999), ==, 40);
}

static void
test_exposure_lines (void)
{
  /* IMX219 1080p30: HTS 3448 @ 182.4 MP/s -> 18904 ns per line */
  g_assert_cmpint (camera_isp_sdk_exposure_to_lines (33333, 18904), ==, 1763);
  g_assert_cmpint (camera_isp_sdk_exposure_to_lines (0, 18904), ==, 0);
  g_assert_cmpint (camera_isp_sdk_exposure_to_lines (10, 18904), ==, 1);
}

static void
test_find (void)
{
  g_autofree gchar *dir = g_dir_make_tmp ("camera-isp-sdk-XXXXXX", NULL);
  g_autofree gchar *sub = g_build_filename (dir, "mycam", NULL);
  g_autofree gchar *conf = g_build_filename (sub, "sensor.conf", NULL);
  g_autofree gchar *flat = g_build_filename (dir, "flat.conf", NULL);
  g_autofree gchar *search = g_strdup_printf ("/nonexistent::%s", dir);
  g_autoptr (GError) error = NULL;
  CameraIspSdkSensor *s;

  g_assert_cmpint (g_mkdir (sub, 0755), ==, 0);
  g_assert_true (g_file_set_contents (conf, valid_conf, -1, NULL));
  g_assert_true (g_file_set_contents (flat,
          "[isp]\ndcc-id=7\n[ae]\nrange0=1;2;1024;2048;256;256\n"
          "[gain]\nmodel=db\nstep-db=0.3\n", -1, NULL));
  g_setenv (CAMERA_ISP_SDK_SENSOR_PATH_ENV, search, TRUE);

  s = camera_isp_sdk_sensor_find ("mycam", &error);
  g_assert_no_error (error);
  g_assert_nonnull (s);
  g_assert_cmpstr (s->id, ==, "test");
  camera_isp_sdk_sensor_free (s);

  s = camera_isp_sdk_sensor_find ("flat", &error);
  g_assert_no_error (error);
  g_assert_nonnull (s);
  g_assert_cmpstr (s->id, ==, "flat");
  g_assert_cmpint (s->dcc_id, ==, 7);
  camera_isp_sdk_sensor_free (s);

  /* Unknown and unsafe names are "not found", not errors */
  g_assert_null (camera_isp_sdk_sensor_find ("SENSOR_SONY_IMX219_RPI", &error));
  g_assert_no_error (error);
  g_assert_null (camera_isp_sdk_sensor_find ("../mycam", &error));
  g_assert_no_error (error);
  g_assert_null (camera_isp_sdk_sensor_find ("", &error));
  g_assert_no_error (error);

  g_unsetenv (CAMERA_ISP_SDK_SENSOR_PATH_ENV);
  g_unlink (conf);
  g_unlink (flat);
  g_rmdir (sub);
  g_rmdir (dir);
}

static void
test_shipped_descriptors (void)
{
  g_autoptr (GDir) dir = NULL;
  const gchar *name;
  guint count = 0;

  if (sensors_dir == NULL) {
    g_test_skip ("no sensors directory given");
    return;
  }

  dir = g_dir_open (sensors_dir, 0, NULL);
  g_assert_nonnull (dir);

  while ((name = g_dir_read_name (dir)) != NULL) {
    g_autofree gchar *path =
        g_build_filename (sensors_dir, name, "sensor.conf", NULL);
    g_autoptr (GError) error = NULL;
    g_autoptr (CameraIspSdkSensor) s = NULL;

    if (name[0] == '_' || !g_file_test (path, G_FILE_TEST_IS_REGULAR))
      continue;

    s = camera_isp_sdk_sensor_load_file (path, name, &error);
    g_assert_no_error (error);
    g_assert_cmpstr (s->id, ==, name);
    count++;
  }

  g_assert_cmpuint (count, >, 0);
}

int
main (int argc, char **argv)
{
  g_test_init (&argc, &argv, NULL);

  if (argc > 1)
    sensors_dir = argv[1];

  g_test_add_func ("/sensor/load-valid", test_load_valid);
  g_test_add_func ("/sensor/load-invalid", test_load_invalid);
  g_test_add_func ("/sensor/gain-models", test_gain_models);
  g_test_add_func ("/sensor/exposure-lines", test_exposure_lines);
  g_test_add_func ("/sensor/find", test_find);
  g_test_add_func ("/sensor/shipped-descriptors", test_shipped_descriptors);

  return g_test_run ();
}
