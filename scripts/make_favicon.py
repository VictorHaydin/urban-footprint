"""Generate the web app favicon: a schematic city map that is also a pie chart.

Pie slices in the app's category colours = city land-use shares; white slice borders double as
radial streets, a white ring is the ring road, and one slice border is a winding blue river.

    python scripts/make_favicon.py

Writes web/favicon.svg, web/favicon.ico (16/32/48 px) and web/apple-touch-icon.png (180 px).
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

WEB = Path(__file__).resolve().parent.parent / "web"
SIZE = 64            # SVG viewBox
C = R = 32           # centre
RADIUS = 30
# slices: (start°, end°, colour) clockwise from 12 o'clock — colours from config/classification.yaml
SLICES = [
    (0, 140, "#3f9b4a"),    # green space
    (140, 235, "#d0473a"),  # buildings
    (235, 300, "#e8c9bd"),  # residential land
    (300, 360, "#5c5c66"),  # roads / grey
]
STREETS = [0, 235, 300]     # straight white radial streets (slice borders)
STREET_W = 3.2
RING_R, RING_W = 15.5, 2.6  # ring road
RIVER = "#3a86c8"
RIVER_W = 5.0
# river: cubic Bézier from the centre out past the 140° border (clipped to the disc)
RIVER_PTS = [(31, 30), (49, 30), (33, 47), (57, 56)]


def polar(deg: float, r: float = RADIUS) -> tuple[float, float]:
    a = math.radians(deg)
    return C + r * math.sin(a), C - r * math.cos(a)


def svg() -> str:
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}">',
             f'<defs><clipPath id="d"><circle cx="{C}" cy="{C}" r="{RADIUS}"/></clipPath></defs>',
             '<g clip-path="url(#d)">']
    for a0, a1, col in SLICES:
        x0, y0 = polar(a0)
        x1, y1 = polar(a1)
        large = 1 if a1 - a0 > 180 else 0
        parts.append(f'<path d="M{C} {C}L{x0:.2f} {y0:.2f}A{RADIUS} {RADIUS} 0 {large} 1 {x1:.2f} {y1:.2f}Z" fill="{col}"/>')
    parts.append(f'<circle cx="{C}" cy="{C}" r="{RING_R}" fill="none" stroke="#fff" stroke-width="{RING_W}"/>')
    for a in STREETS:
        x, y = polar(a, RADIUS + 2)
        parts.append(f'<path d="M{C} {C}L{x:.2f} {y:.2f}" stroke="#fff" stroke-width="{STREET_W}" stroke-linecap="round"/>')
    (p0, p1, p2, p3) = RIVER_PTS
    parts.append(f'<path d="M{p0[0]} {p0[1]}C{p1[0]} {p1[1]} {p2[0]} {p2[1]} {p3[0]} {p3[1]}" fill="none" '
                 f'stroke="{RIVER}" stroke-width="{RIVER_W}" stroke-linecap="round"/>')
    parts.append(f'<circle cx="{C}" cy="{C}" r="3.6" fill="#fff"/>')  # centre square / hub
    parts.append('</g></svg>')
    return "".join(parts)


def bezier(pts, n=80):
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = pts
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        out.append((u**3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t**3 * x3,
                    u**3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t**3 * y3))
    return out


def raster(px: int) -> Image.Image:
    """Same drawing with Pillow (supersampled), for PNG / ICO fallbacks."""
    ss = 8
    k = px * ss / SIZE
    img = Image.new("RGBA", (px * ss, px * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    box = [(C - RADIUS) * k, (C - RADIUS) * k, (C + RADIUS) * k, (C + RADIUS) * k]
    for a0, a1, col in SLICES:
        d.pieslice(box, a0 - 90, a1 - 90, fill=col)   # Pillow: 0° = 3 o'clock
    rb = [(C - RING_R) * k, (C - RING_R) * k, (C + RING_R) * k, (C + RING_R) * k]
    d.ellipse(rb, outline="#ffffff", width=round(RING_W * k))
    for a in STREETS:
        x, y = polar(a, RADIUS + 2)
        d.line([(C * k, C * k), (x * k, y * k)], fill="#ffffff", width=round(STREET_W * k))
    # stamp discs along the curve (Pillow's wide polylines leave gaps at joints)
    half = RIVER_W * k / 2
    for x, y in bezier(RIVER_PTS, n=400):
        d.ellipse([x * k - half, y * k - half, x * k + half, y * k + half], fill=RIVER)
    hub = 3.6 * k
    d.ellipse([C * k - hub, C * k - hub, C * k + hub, C * k + hub], fill="#ffffff")
    # clip everything to the disc
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).ellipse(box, fill=255)
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out.resize((px, px), Image.LANCZOS)


def main() -> None:
    (WEB / "favicon.svg").write_text(svg(), encoding="utf-8")
    raster(48).save(WEB / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    # apple-touch-icon: opaque background (iOS shows transparency as black)
    bg = Image.new("RGBA", (180, 180), "#ffffff")
    icon = raster(150)
    bg.paste(icon, (15, 15), icon)
    bg.convert("RGB").save(WEB / "apple-touch-icon.png")
    print("wrote favicon.svg, favicon.ico, apple-touch-icon.png in", WEB)


if __name__ == "__main__":
    main()
