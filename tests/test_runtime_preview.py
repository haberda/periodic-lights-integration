import unittest
from datetime import datetime
from io import BytesIO
from itertools import pairwise
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from PIL import Image
from runtime_support import load_runtime


class PreviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = load_runtime()
        self.now = datetime(2026, 9, 6, 15, tzinfo=ZoneInfo("America/Los_Angeles"))
        self.cycle = SimpleNamespace(
            night_midpoint=self.now.replace(hour=0), midday=self.now.replace(hour=12)
        )

    async def test_preview_and_live_shaping_match(self):
        for shape in (
            "gamma_sine",
            "time_warped_sine",
            "triangular",
            "eased_triangular",
        ):
            data = {"shaping_function": shape, "shaping_param": 1.5}
            point = self.runtime.package.curve_model.curve_at(
                data, self.now, self.cycle
            )
            self.assertEqual(
                point.shaped,
                self.runtime.control.apply_shaping(point.phase, shape, 1.5),
            )

    async def test_fixed_time_minimum_and_peak(self):
        settings = {"use_fixed_min_time": True, "fixed_min_time": "03:00:00"}
        low = self.runtime.package.curve_model.curve_at(
            settings, self.now.replace(hour=3), self.cycle
        )
        high = self.runtime.package.curve_model.curve_at(settings, self.now, self.cycle)
        self.assertEqual(low.brightness, 1)
        self.assertEqual(high.brightness, 100)

    async def test_daylight_saving_days_include_every_actual_five_minutes(self):
        for month, day, hours in [(3, 8, 23), (11, 1, 25), (9, 6, 24)]:
            now = self.now.replace(month=month, day=day)
            samples = self.runtime.package.preview.sample_day({}, now, self.cycle)
            self.assertEqual(len(samples), hours * 12 + 1)
            self.assertTrue(
                all(
                    b[0].timestamp() - a[0].timestamp() == 300
                    for a, b in pairwise(samples)
                )
            )

    async def test_png_render_and_flat_temperature_range(self):
        png = self.runtime.package.preview.render_preview(
            {"min_kelvin": 3000, "max_kelvin": 3000}, self.now, self.cycle
        )
        image = Image.open(BytesIO(png))
        self.assertEqual(image.size, (1000, 640))
        image.verify()

    async def test_image_cache_invalidates_on_settings_change(self):
        hass = SimpleNamespace(
            data={"periodic_lights": {"entry": {}}},
            async_add_executor_job=AsyncMock(return_value=b"png"),
        )
        image = self.runtime.package.image.PeriodicLightsCurveImage(
            hass, "entry", "Room"
        )
        self.runtime.package.image.daily_pct = lambda hass: (0, self.cycle)
        self.assertEqual(await image.async_image(), b"png")
        await image.async_image()
        self.assertEqual(hass.async_add_executor_job.await_count, 1)
        image._invalidate()
        await image.async_image()
        self.assertEqual(hass.async_add_executor_job.await_count, 2)

    async def test_separate_temperature_settings_are_reflected_in_preview(self):
        settings = {
            "separate_temperature_curve": True,
            "temperature_use_fixed_min_time": True,
            "temperature_fixed_min_time": "15:00:00",
        }
        point = self.runtime.package.curve_model.curve_at(
            settings, self.now, self.cycle
        )
        self.assertEqual(point.kelvin, 2500)
        self.assertGreater(point.brightness, 50)
