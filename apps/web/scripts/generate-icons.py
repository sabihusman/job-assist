"""Generate the PWA app icons from a single SVG source (PR feat/pwa-tier1-installable).

Produces:
  * apps/web/public/icon.svg        — vector source
  * apps/web/public/icon-192.png    — manifest icon (small)
  * apps/web/public/icon-512.png    — manifest icon (large, splash)
  * apps/web/public/apple-touch-icon.png — iOS home-screen icon (180×180)

Design:
  Bold white "JA" monogram on a solid #3b8fa9 background — the app's
  ``--primary`` token from globals.css (light-mode ``oklch(60% 0.11 215)``).
  The letterforms sit inside the central 80% safe zone so Android's
  maskable circle crop never clips them. The icons advertise
  ``purpose: "maskable any"`` in the manifest so the same PNG serves
  both contexts.

Why a regen script rather than mystery binaries: the brand color and
letterforms are likely to drift. Committing the generator means
re-running it after a token change keeps the icons in lock-step
without an external designer round-trip.

Run locally:
    cd apps/web
    python scripts/generate-icons.py

Requires: Pillow (pip install Pillow).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ── Brand ───────────────────────────────────────────────────────────────────
BG_COLOR = "#3b8fa9"  # globals.css --primary @ light, oklch(60% 0.11 215)
FG_COLOR = "#ffffff"
MONOGRAM = "JA"

# Maskable safe zone: Android applies a circular/square/teardrop mask to
# the central 80% of the icon. Anchoring the monogram to the central
# ~64% keeps it comfortably inside every mask shape.
SAFE_ZONE_RATIO = 0.64

# ── Output paths ────────────────────────────────────────────────────────────
PUBLIC = Path(__file__).resolve().parents[1] / "public"
SVG_OUT = PUBLIC / "icon.svg"
PNG_192 = PUBLIC / "icon-192.png"
PNG_512 = PUBLIC / "icon-512.png"
PNG_APPLE = PUBLIC / "apple-touch-icon.png"  # iOS recommends 180×180


def _build_svg() -> str:
    """Build a 512×512 SVG with the monogram centered.

    Letters render as text (font-family Inter, the app's sans) — at the
    PNG-rasterization step the script falls back to Pillow's text-path
    drawing which doesn't need Inter installed. The SVG itself is for
    humans + future regeneration; the PNGs are what browsers consume.
    """
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">
  <title>Job Assist</title>
  <rect width="512" height="512" fill="{BG_COLOR}"/>
  <text
    x="256" y="256"
    text-anchor="middle"
    dominant-baseline="central"
    font-family="Inter, system-ui, sans-serif"
    font-weight="800"
    font-size="240"
    fill="{FG_COLOR}"
  >{MONOGRAM}</text>
</svg>
"""


def _find_bold_font(target_pt: int) -> ImageFont.FreeTypeFont:
    """Locate a bold sans-serif font at the requested point size.

    Walks a small list of system paths likely to hold a heavy-weight
    sans; falls back to Pillow's default bitmap font if nothing is
    found. The default is ugly but produces SOMETHING so a sandboxed
    CI run can still rasterize.
    """
    candidates = [
        # Windows
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        # Linux / GitHub Actions runners
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        # macOS
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, target_pt)
            except OSError:
                continue
    # Last-ditch fallback — Pillow's tiny bitmap font.
    return ImageFont.load_default()  # type: ignore[return-value]


def _render_png(size: int) -> Image.Image:
    """Render the monogram at ``size`` × ``size`` PNG resolution.

    The font size is chosen so the rendered glyph bounding box fills
    the configured safe-zone width — measured then scaled in one pass
    so the JA height ends up at ~64% of the canvas regardless of which
    font Pillow ended up loading.
    """
    img = Image.new("RGB", (size, size), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Iteratively size the font so the rendered glyph height matches
    # the safe zone. Two passes is enough — first guess, then correct.
    target_h = int(size * SAFE_ZONE_RATIO)
    pt = int(target_h * 0.95)  # start from a plausible point size
    for _ in range(2):
        font = _find_bold_font(pt)
        bbox = draw.textbbox((0, 0), MONOGRAM, font=font)
        rendered_h = bbox[3] - bbox[1]
        if rendered_h <= 0:
            break
        pt = max(1, int(pt * (target_h / rendered_h)))

    font = _find_bold_font(pt)
    bbox = draw.textbbox((0, 0), MONOGRAM, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    # ``textbbox`` returns a bbox with non-zero left/top offsets for some
    # fonts; subtract them so the glyph centers on the canvas regardless
    # of font metrics.
    x = (size - text_w) // 2 - bbox[0]
    y = (size - text_h) // 2 - bbox[1]
    draw.text((x, y), MONOGRAM, fill=FG_COLOR, font=font)
    return img


def main() -> None:
    PUBLIC.mkdir(parents=True, exist_ok=True)

    SVG_OUT.write_text(_build_svg(), encoding="utf-8")
    print(f"wrote {SVG_OUT.relative_to(PUBLIC.parent)}")

    for size, path in ((192, PNG_192), (512, PNG_512), (180, PNG_APPLE)):
        img = _render_png(size)
        img.save(path, format="PNG", optimize=True)
        print(f"wrote {path.relative_to(PUBLIC.parent)}  ({size}×{size})")


if __name__ == "__main__":
    main()
