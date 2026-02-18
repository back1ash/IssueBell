"""
Generate favicon.ico (multi-size) from static/logo.png.
Usage: python scripts/make_ico.py
"""

from pathlib import Path
from PIL import Image

SRC = Path("static/logo.png")
DST_ICO = Path("static/favicon.ico")
# Extra standalone PNGs for og:image / apple-touch-icon
SIZES_PNG = {
    "static/logo-192.png": 192,
    "static/logo-512.png": 512,
    "static/apple-touch-icon.png": 180,
}


def make_square(img: Image.Image) -> Image.Image:
    """Crop the image to a centered square if it isn't already."""
    w, h = img.size
    if w == h:
        return img
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    return img.crop((left, top, left + side, top + side))


def main() -> None:
    if not SRC.exists():
        print(f"[ERROR] {SRC} not found — save the logo image there first.")
        raise SystemExit(1)

    img = Image.open(SRC).convert("RGBA")
    square = make_square(img)

    # ── favicon.ico (16, 32, 48, 64, 128, 256) ────────────────────────────────
    ico_sizes = [(s, s) for s in (16, 32, 48, 64, 128, 256)]
    frames = [square.resize(s, Image.LANCZOS) for s in ico_sizes]
    frames[0].save(
        DST_ICO,
        format="ICO",
        sizes=ico_sizes,
        append_images=frames[1:],
    )
    print(f"[OK] {DST_ICO}  ({', '.join(str(s[0]) for s in ico_sizes)}px)")

    # ── extra PNGs ─────────────────────────────────────────────────────────────
    for path, size in SIZES_PNG.items():
        out = Path(path)
        square.resize((size, size), Image.LANCZOS).save(out, format="PNG")
        print(f"[OK] {out}  ({size}px)")


if __name__ == "__main__":
    main()
