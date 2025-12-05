"""
Pure math utilities for the Periodic Lights solar curve.

"""

from __future__ import annotations

import math

# -------------------------------
# Helpers
# -------------------------------

def clamp01(x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    return x

def map_pct_to_range(pct: float, vmin: float, vmax: float) -> float:
    """Map [0,1] percentage to [vmin, vmax]."""
    pct = clamp01(pct)
    return vmin + (vmax - vmin) * pct

# -------------------------------
# Shaping functions
# -------------------------------

def apply_shaping(phase: float, func: str, shaping: float) -> float:
    """Convert a daily phase [0,1] into brightness [0,1].

    phase:   0 = midnight, 0.5 = noon, 1 = next midnight
    func:    "gamma_sine", "time_warped_sine", "triangular", "eased_triangular"
    shaping: global shape parameter
    """

    t = clamp01(phase)
    gamma = max(float(shaping), 0.01)
    func = (func or "gamma_sine").lower()

    # 1) Gamma sine = half-sine ^ gamma
    if func == "gamma_sine":
        base = math.sin(math.pi * t)
        base = clamp01(base)
        return clamp01(base ** gamma)

    # 2) Time-warped sine = warp t then half-sine
    if func == "time_warped_sine":
        t_warp = t ** gamma
        return clamp01(math.sin(math.pi * t_warp))

    # 3) Triangular
    tri = clamp01(1.0 - abs(2.0 * t - 1.0))
    if func == "triangular":
        return tri

    # 4) Eased triangular
    if func == "eased_triangular":
        eased = tri*tri * (3 - 2*tri)
        if abs(gamma - 1.0) > 1e-6:
            eased = clamp01(eased ** gamma)
        return eased

    # Fallback: simple half-sine
    return clamp01(math.sin(math.pi * t))
