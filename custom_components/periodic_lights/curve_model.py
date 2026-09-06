"""Shared, timestamp-aware evaluation for live targets and schedule previews."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .const import (
    ATTR_FIXED_MIN_TIME,
    ATTR_SHAPING_FUNCTION,
    ATTR_SHAPING_PARAM,
    ATTR_USE_FIXED_MIN_TIME,
    CONF_MAX_BRIGHTNESS,
    CONF_MAX_KELVIN,
    CONF_MIN_BRIGHTNESS,
    CONF_MIN_KELVIN,
    DEFAULT_MAX_BRIGHTNESS,
    DEFAULT_MAX_KELVIN,
    DEFAULT_MIN_BRIGHTNESS,
    DEFAULT_MIN_KELVIN,
    DEFAULT_SHAPING_FUNCTION,
    DEFAULT_SHAPING_PARAM,
)
from .curve_math import apply_shaping, map_pct_to_range
from .temperature_curve import temperature_curve_settings


@dataclass(frozen=True)
class CurvePoint:
    phase: float
    shaped: float
    brightness: float
    kelvin: float


def fixed_seconds(raw: Any) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        parts = str(raw or "00:00").split(":")
        if len(parts) in (2, 3):
            try:
                return (
                    int(parts[0]) * 3600
                    + int(parts[1]) * 60
                    + (int(parts[2]) if len(parts) == 3 else 0)
                )
            except ValueError:
                pass
        return 0.0


def curve_for_phase(settings: dict, phase: float) -> CurvePoint:
    shaped = apply_shaping(
        phase,
        settings.get(ATTR_SHAPING_FUNCTION, DEFAULT_SHAPING_FUNCTION),
        settings.get(ATTR_SHAPING_PARAM, DEFAULT_SHAPING_PARAM),
    )
    brightness = map_pct_to_range(
        shaped,
        settings.get(CONF_MIN_BRIGHTNESS, DEFAULT_MIN_BRIGHTNESS),
        settings.get(CONF_MAX_BRIGHTNESS, DEFAULT_MAX_BRIGHTNESS),
    )
    kelvin = map_pct_to_range(
        shaped,
        settings.get(CONF_MIN_KELVIN, DEFAULT_MIN_KELVIN),
        settings.get(CONF_MAX_KELVIN, DEFAULT_MAX_KELVIN),
    )
    return CurvePoint(phase, shaped, max(0, min(100, brightness)), kelvin)


def phase_at(settings: dict, at: datetime, cycle) -> float:
    """Evaluate the configured schedule in local wall time, as live control does."""
    minimum = cycle.night_midpoint
    if settings.get(ATTR_USE_FIXED_MIN_TIME, False):
        minimum = at.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
            seconds=fixed_seconds(settings.get(ATTR_FIXED_MIN_TIME, 0))
        )
    phase = ((at - minimum).total_seconds() / 86400) % 1.0
    return phase


def curve_at(settings: dict, at: datetime, cycle) -> CurvePoint:
    brightness = curve_for_phase(settings, phase_at(settings, at, cycle))
    temperature_settings = temperature_curve_settings(settings)
    temperature = curve_for_phase(
        temperature_settings, phase_at(temperature_settings, at, cycle)
    )
    return CurvePoint(
        brightness.phase, brightness.shaped, brightness.brightness, temperature.kelvin
    )
