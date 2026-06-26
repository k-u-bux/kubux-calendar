"""
Color utilities for Kubux Calendar.

Generates visually distinct colors for calendars that don't have
an explicit color configured.
"""

import random
import re
import colorsys


# CSS named colors (complete set)
_CSS_NAMED_COLORS: dict[str, tuple[int, int, int]] = {
    "aliceblue": (240, 248, 255),
    "antiquewhite": (250, 235, 215),
    "aqua": (0, 255, 255),
    "aquamarine": (127, 255, 212),
    "azure": (240, 255, 255),
    "beige": (245, 245, 220),
    "bisque": (255, 228, 196),
    "black": (0, 0, 0),
    "blanchedalmond": (255, 235, 205),
    "blue": (0, 0, 255),
    "blueviolet": (138, 43, 226),
    "brown": (165, 42, 42),
    "burlywood": (222, 184, 135),
    "cadetblue": (95, 158, 160),
    "chartreuse": (127, 255, 0),
    "chocolate": (210, 105, 30),
    "coral": (255, 127, 80),
    "cornflowerblue": (100, 149, 237),
    "cornsilk": (255, 248, 220),
    "crimson": (220, 20, 60),
    "cyan": (0, 255, 255),
    "darkblue": (0, 0, 139),
    "darkcyan": (0, 139, 139),
    "darkgoldenrod": (184, 134, 11),
    "darkgray": (169, 169, 169),
    "darkgreen": (0, 100, 0),
    "darkgrey": (169, 169, 169),
    "darkkhaki": (189, 183, 107),
    "darkmagenta": (139, 0, 139),
    "darkolivegreen": (85, 107, 47),
    "darkorange": (255, 140, 0),
    "darkorchid": (153, 50, 204),
    "darkred": (139, 0, 0),
    "darksalmon": (233, 150, 122),
    "darkseagreen": (143, 188, 143),
    "darkslateblue": (72, 61, 139),
    "darkslategray": (47, 79, 79),
    "darkturquoise": (0, 206, 209),
    "darkviolet": (148, 0, 211),
    "deeppink": (255, 20, 147),
    "deepskyblue": (0, 191, 255),
    "dimgray": (105, 105, 105),
    "dodgerblue": (30, 144, 255),
    "firebrick": (178, 34, 34),
    "floralwhite": (255, 250, 240),
    "forestgreen": (34, 139, 34),
    "fuchsia": (255, 0, 255),
    "gainsboro": (220, 220, 220),
    "ghostwhite": (248, 248, 255),
    "gold": (255, 215, 0),
    "goldenrod": (218, 165, 32),
    "gray": (128, 128, 128),
    "green": (0, 128, 0),
    "greenyellow": (173, 255, 47),
    "grey": (128, 128, 128),
    "honeydew": (240, 255, 240),
    "hotpink": (255, 105, 180),
    "indianred": (205, 92, 92),
    "indigo": (75, 0, 130),
    "ivory": (255, 255, 240),
    "khaki": (240, 230, 140),
    "lavender": (230, 230, 250),
    "lavenderblush": (255, 240, 245),
    "lawngreen": (124, 252, 0),
    "lemonchiffon": (255, 250, 205),
    "lightblue": (173, 216, 230),
    "lightcoral": (240, 128, 128),
    "lightcyan": (224, 255, 255),
    "lightgoldenrodyellow": (250, 250, 210),
    "lightgray": (211, 211, 211),
    "lightgreen": (144, 238, 144),
    "lightgrey": (211, 211, 211),
    "lightpink": (255, 182, 193),
    "lightsalmon": (255, 160, 122),
    "lightseagreen": (32, 178, 170),
    "lightskyblue": (135, 206, 250),
    "lightslategray": (119, 136, 153),
    "lightsteelblue": (176, 196, 222),
    "lightyellow": (255, 255, 224),
    "lime": (0, 255, 0),
    "limegreen": (50, 205, 50),
    "linen": (250, 240, 230),
    "magenta": (255, 0, 255),
    "maroon": (128, 0, 0),
    "mediumaquamarine": (102, 205, 170),
    "mediumblue": (0, 0, 205),
    "mediumorchid": (186, 85, 211),
    "mediumpurple": (147, 112, 219),
    "mediumseagreen": (60, 179, 113),
    "mediumslateblue": (123, 104, 238),
    "mediumspringgreen": (0, 250, 154),
    "mediumturquoise": (72, 209, 204),
    "mediumvioletred": (199, 21, 133),
    "midnightblue": (25, 25, 112),
    "mintcream": (245, 255, 250),
    "mistyrose": (255, 228, 225),
    "moccasin": (255, 228, 181),
    "navajowhite": (255, 222, 173),
    "navy": (0, 0, 128),
    "oldlace": (253, 245, 230),
    "olive": (128, 128, 0),
    "olivedrab": (107, 142, 35),
    "orange": (255, 165, 0),
    "orangered": (255, 69, 0),
    "orchid": (218, 112, 214),
    "palegoldenrod": (238, 232, 170),
    "palegreen": (152, 251, 152),
    "paleturquoise": (175, 238, 238),
    "palevioletred": (219, 112, 147),
    "papayawhip": (255, 239, 213),
    "peachpuff": (255, 218, 185),
    "peru": (205, 133, 63),
    "pink": (255, 192, 203),
    "plum": (221, 160, 221),
    "powderblue": (176, 224, 230),
    "purple": (128, 0, 128),
    "rebeccapurple": (102, 51, 153),
    "red": (255, 0, 0),
    "rosybrown": (188, 143, 143),
    "royalblue": (65, 105, 225),
    "saddlebrown": (139, 69, 19),
    "salmon": (250, 128, 114),
    "sandybrown": (244, 164, 96),
    "seagreen": (46, 139, 87),
    "seashell": (255, 245, 238),
    "sienna": (160, 82, 45),
    "silver": (192, 192, 192),
    "skyblue": (135, 206, 235),
    "slateblue": (106, 90, 205),
    "slategray": (112, 128, 144),
    "snow": (255, 250, 250),
    "springgreen": (0, 255, 127),
    "steelblue": (70, 130, 180),
    "tan": (210, 180, 140),
    "teal": (0, 128, 128),
    "thistle": (216, 191, 216),
    "tomato": (255, 99, 71),
    "turquoise": (64, 224, 208),
    "violet": (238, 130, 238),
    "wheat": (245, 222, 179),
    "white": (255, 255, 255),
    "whitesmoke": (245, 245, 245),
    "yellow": (255, 255, 0),
    "yellowgreen": (154, 205, 50),
}

_RE_HEX = re.compile(r"^#?([0-9a-fA-F]+)$")
_RE_RGB = re.compile(r"rgba?\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*[\d.]+)?\s*\)")
_RE_HSL = re.compile(r"hsla?\s*\(\s*([\d.]+)\s*[,\s]\s*([\d.]+)%?\s*[,\s]\s*([\d.]+)%?(?:\s*[,\s]\s*[\d.]+)?\s*\)")


def _parse_color(color_str: str) -> tuple[int, int, int]:
    """Parse any color string to (R, G, B). Returns (0, 0, 0) on failure."""
    s = color_str.strip()

    # Named color
    lower = s.lower()
    rgb = _CSS_NAMED_COLORS.get(lower)
    if rgb is not None:
        return rgb

    # Hex: #4285f4, #fff, #4285f4a0 (with alpha)
    m = _RE_HEX.match(s)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            # short hex: #rgb or #rgba
            h = "".join(c * 2 for c in h)
        if len(h) >= 6:
            try:
                r = int(h[0:2], 16)
                g = int(h[2:4], 16)
                b = int(h[4:6], 16)
                return (r, g, b)
            except ValueError:
                pass

    # rgba() / rgb()
    m = _RE_RGB.match(s)
    if m:
        try:
            return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # hsla() / hsl()
    m = _RE_HSL.match(s)
    if m:
        try:
            h = float(m.group(1)) / 360.0
            s_float = float(m.group(2)) / 100.0
            l = float(m.group(3)) / 100.0
            r, g, b = colorsys.hls_to_rgb(h, l, s_float)
            return (round(r * 255), round(g * 255), round(b * 255))
        except ValueError:
            pass

    return (0, 0, 0)


def _rgb_to_hue(r: int, g: int, b: int) -> float:
    """Convert (R, G, B) to hue in degrees (0-360)."""
    rf = r / 255.0
    gf = g / 255.0
    bf = b / 255.0

    mx = max(rf, gf, bf)
    mn = min(rf, gf, bf)
    delta = mx - mn

    if delta == 0:
        return 0.0
    if mx == rf:
        hue = 60.0 * ((gf - bf) / delta % 6)
    elif mx == gf:
        hue = 60.0 * ((bf - rf) / delta + 2)
    else:
        hue = 60.0 * ((rf - gf) / delta + 4)

    if hue < 0:
        hue += 360.0
    return hue


def color_to_hue(color_str: str) -> float:
    """Extract hue in degrees (0-360) from any color string."""
    r, g, b = _parse_color(color_str)
    return _rgb_to_hue(r, g, b)


def _hsl_to_hex(h: float, s: float, l: float) -> str:
    """Convert HSL values (0-360, 0-100, 0-100) to hex string (#rrggbb)."""
    s /= 100.0
    l /= 100.0
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60.0) % 2 - 1))
    m = l - c / 2.0

    if h < 60:
        r, g, b = c, x, 0
    elif h < 120:
        r, g, b = x, c, 0
    elif h < 180:
        r, g, b = 0, c, x
    elif h < 240:
        r, g, b = 0, x, c
    elif h < 300:
        r, g, b = x, 0, c
    else:
        r, g, b = c, 0, x

    ri = round((r + m) * 255)
    gi = round((g + m) * 255)
    bi = round((b + m) * 255)
    return f"#{ri:02x}{gi:02x}{bi:02x}"


def _angular_distance(h1: float, h2: float) -> float:
    """Shortest angular distance between two hues in degrees."""
    d = abs(h1 - h2)
    return min(d, 360.0 - d)


def get_unused_color(used_colors: list[str], n_candidates: int = 5) -> str:
    """Return a hex color visually distinct from all *used_colors*.

    Generates every hue (0-359) at fixed saturation 70% / lightness 50%,
    scores each by minimum angular distance to any used hue,
    keeps the top *n_candidates*, and picks one randomly.

    If *used_colors* is empty, returns a random hue.
    """
    if not used_colors:
        h = random.randint(0, 359)
        return _hsl_to_hex(h, 70, 50)

    used_hues = [color_to_hue(c) for c in used_colors]

    # Score every candidate hue (0-359) by its minimum distance to used hues
    scored: list[tuple[float, int]] = []
    for h in range(360):
        dist = min(_angular_distance(h, uh) for uh in used_hues)
        scored.append((dist, h))

    # Sort descending by distance
    scored.sort(reverse=True, key=lambda x: x[0])

    # Pick from top N
    top = scored[:n_candidates]
    _, chosen = random.choice(top)

    return _hsl_to_hex(chosen, 70, 50)