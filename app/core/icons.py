"""Launcher icons for a game app, in every size Android asks for."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

DENSITY = {"mdpi": 1.0, "hdpi": 1.5, "xhdpi": 2.0, "xxhdpi": 3.0, "xxxhdpi": 4.0}


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _edge_color(img):
    """Average colour along the image border, used to fill around the artwork."""
    small = img.convert("RGB").resize((32, 32))
    px = [small.getpixel((x, y)) for x in range(32) for y in range(32) if x in (0, 31) or y in (0, 31)]
    return tuple(sum(c[i] for c in px) // len(px) for i in range(3))


def placeholder(name, color_hex, size=512):
    """An icon made from the game's initials on its console colour."""
    bg = _hex_to_rgb(color_hex)
    img = Image.new("RGB", (size, size), tuple(int(c * 0.55) for c in bg))
    d = ImageDraw.Draw(img)
    text = "".join(w[0] for w in name.split()[:2]).upper() or "?"
    font = None
    for f in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            font = ImageFont.truetype(f, int(size * 0.34))
            break
        except OSError:
            continue
    font = font or ImageFont.load_default()
    box = d.textbbox((0, 0), text, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    d.text(((size - w) / 2 - box[0], (size - h) / 2 - box[1]), text, fill=(240, 236, 226), font=font)
    return img


def write_icons(res_dir: Path, source_image=None, name="Game", color_hex="#555b66", icon_name="ic_launcher"):
    """Replaces ic_launcher / ic_launcher_foreground PNGs and the icon background colour.

    Returns the background colour used (#RRGGBB).
    """
    if source_image:
        art = ImageOps.exif_transpose(Image.open(source_image)).convert("RGBA")
        art = ImageOps.fit(art, (512, 512), Image.LANCZOS) if abs(art.width - art.height) > 2 else art.resize((512, 512), Image.LANCZOS)
    else:
        art = placeholder(name, color_hex).convert("RGBA")
    bg = _edge_color(art)

    for folder in res_dir.glob("mipmap-*"):
        density = next((k for k in DENSITY if folder.name == "mipmap-" + k or folder.name.startswith("mipmap-" + k + "-")), None)
        if not density:
            continue
        s = DENSITY[density]
        # Adaptive foreground: 108dp canvas; Android shows roughly the middle 72dp.
        fg_size = round(108 * s)
        inner = round(80 * s)
        fg = Image.new("RGBA", (fg_size, fg_size), (0, 0, 0, 0))
        fg.paste(art.resize((inner, inner), Image.LANCZOS), ((fg_size - inner) // 2,) * 2)
        if (folder / (icon_name + "_foreground.png")).exists():
            fg.save(folder / (icon_name + "_foreground.png"), optimize=True)
        # Legacy square icon for older launchers.
        legacy = round(48 * s)
        if (folder / (icon_name + ".png")).exists():
            art.resize((legacy, legacy), Image.LANCZOS).save(folder / (icon_name + ".png"), optimize=True)
    return "#%02x%02x%02x" % bg


def _font(size):
    for f in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def write_banner(path: Path, source_image=None, name="Game", color_hex="#555b66"):
    """Android TV home-screen banner (320 x 180): artwork on the left, the game's name on the right."""
    if source_image:
        art = ImageOps.exif_transpose(Image.open(source_image)).convert("RGBA")
        art = ImageOps.fit(art, (140, 140), Image.LANCZOS)
    else:
        art = placeholder(name, color_hex, 140).convert("RGBA")
    bg = tuple(int(c * 0.45) for c in _edge_color(art))
    img = Image.new("RGB", (320, 180), bg)
    mask = Image.new("L", (140, 140), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, 139, 139], radius=18, fill=255)
    img.paste(art, (20, 20), mask)
    d = ImageDraw.Draw(img)

    def wrap(font):
        lines, line = [], ""
        for w in name.split():
            test = (line + " " + w).strip()
            if d.textlength(test, font=font) <= 132 or not line:
                line = test
            else:
                lines.append(line)
                line = w
        return lines + [line]

    for size, max_lines in ((22, 3), (19, 4), (16, 5)):
        font = _font(size)
        lines = wrap(font)
        if len(lines) <= max_lines:
            break
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip() + "…"
    step = size + 6
    y = 90 - len(lines) * step / 2
    for ln in lines:
        while d.textlength(ln, font=font) > 136 and len(ln) > 3:
            ln = ln[:-2].rstrip() + "…"
        d.text((174, y), ln, fill=(240, 236, 226), font=font)
        y += step
    img.save(path, optimize=True)
