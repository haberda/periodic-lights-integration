"""Render the configured local-day schedule without touching light services."""

from datetime import timedelta, timezone
from io import BytesIO
from textwrap import wrap

from PIL import Image, ImageDraw, ImageFont

from .curve_model import curve_at


def sample_day(settings, now, cycle):
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    at = start.astimezone(timezone.utc)
    finish = end.astimezone(timezone.utc)
    points = []
    while at <= finish:
        local = at.astimezone(now.tzinfo)
        points.append((local, curve_at(settings, local, cycle)))
        at += timedelta(minutes=5)
    return points


def render_preview(settings, now, cycle):
    points = sample_day(settings, now, cycle)
    canvas = Image.new("RGB", (1000, 640), "#15202b")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=19)
    small = ImageFont.load_default(size=15)
    draw.text(
        (70, 20),
        "Configured daily schedule",
        font=ImageFont.load_default(size=26),
        fill="white",
    )
    shape = str(settings.get("shaping_function", "gamma_sine")).replace("_", " ")
    draw.text(
        (70, 58),
        f"{now:%Y-%m-%d}  |  {shape}  |  shape {settings.get('shaping_param', 1)}",
        font=font,
        fill="#cbd5e1",
    )
    notices = []
    if not settings.get("enabled", True):
        notices.append("Integration disabled")
    if settings.get("bedtime", False):
        notices.append("Bedtime active: live targets use minimums")
    if not settings.get("brightness_enabled", True):
        notices.append("Brightness control disabled")
    if not settings.get("color_temp_enabled", True):
        notices.append("Temperature control disabled")
    if not notices:
        notices.append(
            "Setup-wide targets; individual overrides and light on/off state may differ"
        )
    for line, text in enumerate(wrap(" | ".join(notices), width=110)):
        draw.text((70, 92 + line * 17), text, font=small, fill="#fcd34d")
    start_ts, end_ts = points[0][0].timestamp(), points[-1][0].timestamp()

    def x(at):
        return 80 + 860 * (at.timestamp() - start_ts) / (end_ts - start_ts)

    for top, field, title, unit, color in [
        (160, "brightness", "Brightness", "%", "#fbbf24"),
        (400, "kelvin", "Color temperature", "K", "#67e8f9"),
    ]:
        values = [getattr(point, field) for _, point in points]
        low, high = (0, 100) if field == "brightness" else (min(values), max(values))
        if low == high:
            low, high = low - 100, high + 100

        def y(value, top=top, low=low, high=high):
            return top + 155 - 155 * (value - low) / (high - low)

        draw.text((80, top - 32), f"{title} ({unit})", font=font, fill=color)
        for tick in range(5):
            value = low + tick * (high - low) / 4
            draw.line((80, y(value), 940, y(value)), fill="#334155")
            draw.text((8, y(value) - 8), f"{value:.0f}", font=small, fill="#cbd5e1")
        for hour in range(0, 25, 3):
            at = points[0][0] + timedelta(hours=hour)
            draw.line((x(at), top, x(at), top + 155), fill="#334155")
            draw.text(
                (x(at) - 18, top + 164), f"{hour:02d}:00", font=small, fill="#cbd5e1"
            )
        draw.line(
            [(x(at), y(getattr(point, field))) for at, point in points],
            fill=color,
            width=3,
        )
        for index, label in (
            (values.index(min(values)), "min"),
            (values.index(max(values)), "max"),
        ):
            at, point = points[index]
            px, py = x(at), y(getattr(point, field))
            draw.ellipse((px - 4, py - 4, px + 4, py + 4), fill=color)
            draw.text(
                (max(82, min(825, px + 7)), max(top, py - 22)),
                f"{label} {at:%H:%M}",
                font=small,
                fill=color,
            )
        draw.line((x(now), top, x(now), top + 155), fill="#fb7185", width=2)
        draw.text(
            (max(82, min(825, x(now) + 5)), top + 6),
            f"Now {now:%H:%M}",
            font=small,
            fill="#fb7185",
        )
    draw.text(
        (80, 604),
        f"Local time ({now.tzname()}); markers sampled at five-minute intervals",
        font=small,
        fill="#94a3b8",
    )
    stream = BytesIO()
    canvas.save(stream, format="PNG")
    return stream.getvalue()
