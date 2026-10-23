# -*- coding: utf-8 -*-
"""Draw UPS Tray's program icon at build time (Pillow, from the build venv).

Writes upstray.ico into --out for the exe, installer and Settings window.
Every size is drawn on its own at 8x and scaled down, so the small sizes
stay crisp. Frames are stored as BMP, which every Windows icon loader and
resource tool accepts. The release bundles the icon, so the app itself
does not need Pillow.
"""
import argparse
import os
import sys

from PIL import Image, ImageDraw

APP_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
SUPERSAMPLE = 8

BLUE = (37, 99, 160, 255)
WHITE = (255, 255, 255, 255)


def _box(s, x0, y0, x1, y1):
    """A box given in 0..1 units on an s x s canvas."""
    return [round(x0 * s), round(y0 * s), round(x1 * s) - 1, round(y1 * s) - 1]


def _finish(img, size):
    return img.resize((size, size), Image.Resampling.LANCZOS)


def draw_app(size):
    s = size * SUPERSAMPLE
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(_box(s, 0.03, 0.03, 0.97, 0.97),
                        radius=round(0.22 * s), fill=BLUE)
    # A battery with a broad terminal and a lightning cutout.
    d.rounded_rectangle(_box(s, 0.41, 0.15, 0.59, 0.30),
                        radius=round(0.035 * s), fill=WHITE)
    d.rounded_rectangle(_box(s, 0.26, 0.25, 0.74, 0.85),
                        radius=round(0.07 * s), fill=WHITE)
    bolt = ((0.52, 0.33), (0.36, 0.58), (0.48, 0.58),
            (0.44, 0.77), (0.65, 0.48), (0.53, 0.48), (0.59, 0.33))
    d.polygon([(round(x * s), round(y * s)) for x, y in bolt], fill=BLUE)
    return _finish(img, size)


def save_ico(path, frames):
    frames = sorted(frames, key=lambda im: im.size[0])
    base = frames[-1]
    base.save(path, format="ICO", sizes=[im.size for im in frames],
              append_images=frames[:-1], bitmap_format="bmp")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="output folder")
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    frames = [draw_app(n) for n in APP_SIZES]
    path = os.path.join(args.out, "upstray.ico")
    save_ico(path, frames)
    with Image.open(path) as check:
        got = sorted(check.info.get("sizes", set()))
    want = sorted(f.size for f in frames)
    if got != want:
        print("ERROR: upstray.ico has sizes %r, expected %r" % (got, want), file=sys.stderr)
        return 1
    print("wrote %s (%d bytes, sizes %s)" % (path, os.path.getsize(path),
                                            ", ".join(str(w) for w, _h in got)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
