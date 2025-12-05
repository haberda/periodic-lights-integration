#!/usr/bin/env python3
"""
Offline test script to plot shapes from Periodic Lights.

Run:
    python test_curve.py
"""

from datetime import datetime, timedelta
import matplotlib.pyplot as plt

from curve_math import (
    apply_shaping,
    map_pct_to_range,
)

# -------------------------
# Configurable parameters
# -------------------------

SUNRISE = 6      # 06:00
SUNSET = 16      # 18:00
N_POINTS = 24 * 12  # every 5 minutes
MIN_BRIGHTNESS = 0
MAX_BRIGHTNESS = 100

TEST_DATE = datetime(2025, 1, 1)

SHAPE_FACTOR = 1.5

SHAPES = [
    ("gamma_sine", 1),
    ("gamma_sine", SHAPE_FACTOR),
    ("triangular", 1),
    ("eased_triangular", SHAPE_FACTOR),
    ("time_warped_sine", SHAPE_FACTOR),
]

LABELS = {"gamma_sine": "Shaped half-sine", "triangular": "Triangular", "eased_triangular": "Eased Triangular", "time_warped_sine": "Time-warped sine"}


# -------------------------
# Helpers
# -------------------------

def solar_cycle(date: datetime):
    base = date.replace(hour=0, minute=0, second=0, microsecond=0)
    sunrise = base + timedelta(hours=SUNRISE)
    sunset = base + timedelta(hours=SUNSET)
    midday = sunrise + (sunset - sunrise)/2
    next_sunrise = sunrise + timedelta(days=1)
    night_midpoint = sunset + (next_sunrise - sunset)/2
    return sunrise, sunset, midday, night_midpoint


def phase_from_time(t: datetime, night_midpoint: datetime):
    sec = (t - night_midpoint).total_seconds()
    phase = (sec / (24*3600)) % 1.0
    if phase < 0: phase += 1
    return phase


# -------------------------
# Main plot
# -------------------------

def main():
    sunrise, sunset, midday, night_mid = solar_cycle(TEST_DATE)

    start = TEST_DATE.replace(hour=0, minute=0)
    dt = timedelta(seconds=24*3600 / N_POINTS)

    plt.figure(figsize=(10, 5))

    for func, shaping in SHAPES:
        times = []
        bright = []

        for i in range(N_POINTS+1):
            t = start + i*dt
            phase = phase_from_time(t, night_mid)
            pct = apply_shaping(phase, func, shaping)
            b = map_pct_to_range(pct, MIN_BRIGHTNESS, MAX_BRIGHTNESS)

            times.append(t)
            bright.append(b)

        # label = f"{func} (shape={shaping})"
        label = f'{LABELS[func]} (shape={shaping})'
        plt.plot(times, bright, label=label)

    plt.title("Shaped Brightness vs Time of Day")
    plt.xlabel("Time")
    plt.ylabel("Brightness (%)")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
