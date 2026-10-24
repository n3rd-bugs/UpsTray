"""Tray icon images: a value such as '231', optionally with a small label above it
('VIN', 'LOAD') or a second value under it, drawn into a 32-bit icon with alpha.

Each text run is drawn by GDI (white, antialiased, on black) into its own
bitmap and cropped to its ink box, so runs are placed and centred to the pixel
whatever the font metrics say. The coverage becomes alpha with a slight gamma
lift, which keeps dark text on a light taskbar as solid as the system's own
text. The value uses the boldest condensed face available (Bahnschrift Bold
Condensed on Windows 10/11) at the largest size that fits.
"""
import ctypes
import ctypes.wintypes as wt
import struct
import zlib
from typing import NamedTuple

gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

SM_CXSMICON = 49
DIB_RGB_COLORS = 0
BI_RGB = 0
DEFAULT_CHARSET = 1
OUT_TT_PRECIS = 4
CLIP_DEFAULT_PRECIS = 0
ANTIALIASED_QUALITY = 4
DEFAULT_PITCH = 0
TRANSPARENT = 1

# (face, weight) in order of preference; the first one installed is used for every
# icon, so all readings look alike. GDI exposes Bahnschrift's named instances as
# families: 'Bahnschrift Condensed' at weight 700 is Bold Condensed.
VALUE_FACES = (("Bahnschrift Condensed", 700), ("Bahnschrift SemiBold Condensed", 600),
               ("Arial Narrow", 700), ("Segoe UI", 700), ("Arial", 700))
# The label uses the system UI face: at 6-8 px its open letters read better than a
# condensed face (tested at 150 % on light and dark taskbars).
LABEL_FACES = (("Segoe UI Semibold", 600), ("Segoe UI", 600), ("Arial", 700))

LABEL_ALPHA = 1.0        # full strength: anything lighter is unreadable at this size
LABEL_FRACTION = 0.33    # label row height as a share of the icon
VALUE_REFERENCE = "888"  # every value gets the digit height this text fits at (consistent icons)
LABEL_REFERENCE = "VOUT" # and every label the height this one fits at (VIN as tall as LOAD)
GAMMA = 0.75             # alpha = coverage ** GAMMA: firmer antialiased edges
MIN_LABEL_SIZE = 20      # icons smaller than this get no label (it would be unreadable)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
                ("hbmMask", wt.HBITMAP), ("hbmColor", wt.HBITMAP)]


gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(BITMAPINFO), wt.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wt.UINT, wt.UINT, ctypes.c_void_p]
gdi32.CreateBitmap.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.CreateFontW.argtypes = [ctypes.c_int] * 5 + [wt.DWORD] * 8 + [wt.LPCWSTR]
gdi32.CreateFontW.restype = wt.HFONT
gdi32.SetBkMode.argtypes = [wt.HDC, ctypes.c_int]
gdi32.SetTextColor.argtypes = [wt.HDC, wt.COLORREF]
gdi32.TextOutW.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, wt.LPCWSTR, ctypes.c_int]
gdi32.GetTextFaceW.argtypes = [wt.HDC, ctypes.c_int, wt.LPWSTR]
gdi32.GetTextFaceW.restype = ctypes.c_int
gdi32.GdiFlush.restype = wt.BOOL
user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
user32.CreateIconIndirect.restype = wt.HICON
user32.DestroyIcon.argtypes = [wt.HICON]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]


def tray_icon_size():
    """Pixel size of a small icon at the current (system) DPI: 16 at 100 %, 24 at 150 %."""
    size = user32.GetSystemMetrics(SM_CXSMICON)
    return max(16, min(64, size or 16))


def _font(face, weight, px):
    return gdi32.CreateFontW(-px, 0, 0, 0, weight, 0, 0, 0, DEFAULT_CHARSET, OUT_TT_PRECIS,
                             CLIP_DEFAULT_PRECIS, ANTIALIASED_QUALITY, DEFAULT_PITCH, face)


_chosen = {}


def _pick(candidates):
    """First (face, weight) GDI really has (it silently substitutes missing faces)."""
    key = tuple(candidates)
    if key in _chosen:
        return _chosen[key]
    hdc = gdi32.CreateCompatibleDC(None)
    buf = ctypes.create_unicode_buffer(64)
    pick = None
    try:
        for face, weight in candidates:
            font = _font(face, weight, 12)
            if not font:
                continue
            old = gdi32.SelectObject(hdc, font)
            gdi32.GetTextFaceW(hdc, 64, buf)
            gdi32.SelectObject(hdc, old)
            gdi32.DeleteObject(font)
            if buf.value.lower() == face.lower():
                pick = (face, weight)
                break
    finally:
        gdi32.DeleteDC(hdc)
    _chosen[key] = pick or ("Segoe UI", 700)
    return _chosen[key]


def value_face():
    """Default (face, weight) for values."""
    return _pick(VALUE_FACES)


def label_face():
    """Default (face, weight) for labels."""
    return _pick(LABEL_FACES)


_available = {}


def available(face):
    """True if GDI has this font family (it silently substitutes missing ones)."""
    key = face.lower()
    if key not in _available:
        _available[key] = _has_face(face)
    return _available[key]


def _has_face(face):
    hdc = gdi32.CreateCompatibleDC(None)
    buf = ctypes.create_unicode_buffer(64)
    try:
        font = _font(face, 400, 12)
        if not font:
            return False
        old = gdi32.SelectObject(hdc, font)
        gdi32.GetTextFaceW(hdc, 64, buf)
        gdi32.SelectObject(hdc, old)
        gdi32.DeleteObject(font)
        return buf.value.lower() == face.lower()
    finally:
        gdi32.DeleteDC(hdc)


class Fonts(NamedTuple):
    """What the icons are drawn with. Sizes are relative: value_scale shrinks the value
    below the largest size that fits; label_fraction is the label row's share of the icon."""
    value_face: str
    value_weight: int
    label_face: str
    label_weight: int
    value_scale: float = 1.0
    label_fraction: float = LABEL_FRACTION


WEIGHTS = {"Regular": 400, "SemiBold": 600, "Bold": 700, "Black": 900}


def make_fonts(value_face="", value_weight=0, label_face="", label_weight=0, value_size=100, label_size=33):
    """Fonts from user settings: an empty or missing family means the default one, a weight
    of 0 the default weight; sizes are percentages."""
    dv, dl = globals()["value_face"](), globals()["label_face"]()
    vf = value_face if value_face and available(value_face) else dv[0]
    lf = label_face if label_face and available(label_face) else dl[0]
    return Fonts(vf, int(value_weight) or dv[1], lf, int(label_weight) or dl[1],
                 max(0.4, min(1.0, value_size / 100.0)), max(0.2, min(0.5, label_size / 100.0)))


def default_fonts():
    return make_fonts()


_ink_cache = {}


def ink(text, face, weight, px):
    """(width, height, rows) of a text run's ink box at em height px; rows are bytes
    of coverage 0..255 (white text drawn on black)."""
    key = (text, face, weight, px)
    hit = _ink_cache.get(key)
    if hit is not None:
        return hit
    if len(_ink_cache) > 4096:
        _ink_cache.clear()
    w, h = px * (len(text) + 2), px * 2
    hdc = gdi32.CreateCompatibleDC(None)
    if not hdc:
        raise OSError(ctypes.get_last_error(), "CreateCompatibleDC failed")
    hbm = font = None
    try:
        bmi = BITMAPINFO()
        hd = bmi.bmiHeader
        hd.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        hd.biWidth, hd.biHeight, hd.biPlanes, hd.biBitCount, hd.biCompression = w, -h, 1, 32, BI_RGB
        bits = ctypes.c_void_p()
        hbm = gdi32.CreateDIBSection(hdc, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
        if not hbm or not bits:
            raise OSError(ctypes.get_last_error(), "CreateDIBSection failed")
        old_bm = gdi32.SelectObject(hdc, hbm)
        ctypes.memset(bits, 0, w * h * 4)
        font = _font(face, weight, px)
        old_font = gdi32.SelectObject(hdc, font)
        gdi32.SetBkMode(hdc, TRANSPARENT)
        gdi32.SetTextColor(hdc, 0x00FFFFFF)
        gdi32.TextOutW(hdc, px // 2, px // 4, text, len(text))
        gdi32.GdiFlush()
        data = bytes((ctypes.c_ubyte * (w * h * 4)).from_address(bits.value))
        gdi32.SelectObject(hdc, old_font)
        gdi32.SelectObject(hdc, old_bm)
    finally:
        if font:
            gdi32.DeleteObject(font)
        if hbm:
            gdi32.DeleteObject(hbm)
        gdi32.DeleteDC(hdc)
    # coverage = brightest channel; crop to the ink box
    cov = [bytes(max(data[i], data[i + 1], data[i + 2]) for i in range(y * w * 4, (y + 1) * w * 4, 4))
           for y in range(h)]
    ys = [y for y in range(h) if any(cov[y])]
    if not ys:
        res = (0, 0, [])
    else:
        y0, y1 = ys[0], ys[-1]
        cols = [x for x in range(w) if any(cov[y][x] for y in range(y0, y1 + 1))]
        x0, x1 = cols[0], cols[-1]
        res = (x1 - x0 + 1, y1 - y0 + 1, [cov[y][x0:x1 + 1] for y in range(y0, y1 + 1)])
    _ink_cache[key] = res
    return res


_fit_cache = {}


def fit(text, face, weight, max_w, max_h):
    """(px, width, height) for the largest em size whose ink fits max_w x max_h, or None."""
    key = (text, face, weight, max_w, max_h)
    if key in _fit_cache:
        return _fit_cache[key]
    if len(_fit_cache) > 4096:
        _fit_cache.clear()
    best = None
    lo, hi = 4, int(max_h * 2.4) + 2
    while lo <= hi:
        mid = (lo + hi) // 2
        w, h, _ = ink(text, face, weight, mid)
        if 0 < w <= max_w and h <= max_h:
            best = (mid, w, h)
            lo = mid + 1
        else:
            hi = mid - 1
    _fit_cache[key] = best
    return best


def _best_value(texts, max_w, max_h, fonts, min_ratio=0.85):
    """(text, px, w, h): the first candidate whose digits reach min_ratio of the room,
    else the candidate with the tallest digits."""
    face, weight = fonts.value_face, fonts.value_weight
    fallback = None
    for text in texts:
        f = fit(text, face, weight, max_w, max_h)
        if not f:
            continue
        if f[2] >= max_h * min_ratio:
            return (text,) + f
        if fallback is None or f[2] > fallback[3]:
            fallback = (text,) + f
    return fallback


def _blit(canvas, size, rows, x, y, alpha=1.0):
    for dy, row in enumerate(rows):
        yy = y + dy
        if not 0 <= yy < size:
            continue
        line = canvas[yy]
        for dx, c in enumerate(row):
            xx = x + dx
            if c and 0 <= xx < size:
                v = int(c * alpha)
                if v > line[xx]:
                    line[xx] = v


def layout(texts, size, label=None, second=None, fonts=None):
    """Coverage canvas (rows of 0..255) and the value text used.

    label: small caption above the value; the caller decides whether the icon is big
    enough (label_fits), so the drawing and the texts chosen for it always agree.
    second: candidate texts for a second value under the first (two readings per icon).
    fonts: a Fonts spec (default_fonts() when None).
    """
    fonts = fonts or default_fonts()
    canvas = [bytearray(size) for _ in range(size)]
    face, weight = fonts.value_face, fonts.value_weight
    texts = list(texts) or ["--"]
    if second:
        gap = max(1, size // 12)
        line_h = max(5, int((size - gap) // 2 * fonts.value_scale))
        a = _best_value(texts, size, line_h, fonts)
        b = _best_value(list(second), size, line_h, fonts)
        if not a or not b:
            return canvas, texts[0]
        px = min(a[1], b[1])                       # same digit height on both lines
        ia, ib = ink(a[0], face, weight, px), ink(b[0], face, weight, px)
        y = (size - (ia[1] + gap + ib[1]) + 1) // 2
        _blit(canvas, size, ia[2], (size - ia[0]) // 2, y)
        _blit(canvas, size, ib[2], (size - ib[0]) // 2, y + ia[1] + gap)
        return canvas, a[0]
    top = 0
    if label:
        lface, lweight = fonts.label_face, fonts.label_weight
        row_h = max(5, round(size * fonts.label_fraction))
        lref = fit(LABEL_REFERENCE, lface, lweight, size, row_h)
        f = fit(label, lface, lweight, size, min(row_h, lref[2]) if lref else row_h)
        if f:
            _, lw, lh = f
            _blit(canvas, size, ink(label, lface, lweight, f[0])[2], (size - lw) // 2, 0, LABEL_ALPHA)
            top = lh + max(1, size // 12)
    room = size - top
    ref = fit(VALUE_REFERENCE, face, weight, size, room)
    cap = min(room, ref[2]) if ref else room
    best = _best_value(texts, size, max(5, int(cap * fonts.value_scale)), fonts)
    if not best:
        return canvas, texts[0]
    text, px, w, h = best
    _blit(canvas, size, ink(text, face, weight, px)[2], (size - w) // 2, top + (room - h + 1) // 2)
    return canvas, text


def _hex_to_rgb(color):
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _in_round_rect(x, y, size, r):
    if r <= 0:
        return True
    cx = r if x < r else (size - 1 - r if x > size - 1 - r else x)
    cy = r if y < r else (size - 1 - r if y > size - 1 - r else y)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r + r


_GAMMA_LUT = bytes(int(round(255 * (c / 255.0) ** GAMMA)) for c in range(256))


def compose(canvas, size, color, background=None):
    """Premultiplied BGRA: coloured text with alpha from the coverage, or text on an
    opaque rounded badge."""
    fr, fg, fb = _hex_to_rgb(color)
    out = bytearray(size * size * 4)
    if background:
        br, bg, bb = _hex_to_rgb(background)
        radius = max(2, size // 5)
    for y in range(size):
        row = canvas[y]
        for x in range(size):
            i = (y * size + x) * 4
            cov = _GAMMA_LUT[row[x]]
            if background:
                if not _in_round_rect(x, y, size, radius):
                    continue
                out[i] = (fb * cov + bb * (255 - cov)) // 255
                out[i + 1] = (fg * cov + bg * (255 - cov)) // 255
                out[i + 2] = (fr * cov + br * (255 - cov)) // 255
                out[i + 3] = 255
            elif cov:
                out[i] = fb * cov // 255
                out[i + 1] = fg * cov // 255
                out[i + 2] = fr * cov // 255
                out[i + 3] = cov
    return bytes(out)


def label_fits(size):
    """Labels and stacked pairs need icons of MIN_LABEL_SIZE px or more. Decided on the
    icon size (not the badge's inner area), the same test the app uses for its texts."""
    return size >= MIN_LABEL_SIZE


def render_pixels(texts, color="#FFFFFF", background=None, size=None, label=None, second=None, fonts=None):
    """(size, premultiplied BGRA pixels, value text used)."""
    size = size or tray_icon_size()
    if not label_fits(size):
        label = None
    canvas, text = layout(texts, size - (2 if background else 0), label, second, fonts)
    if background:
        inner = size - 2
        padded = [bytearray(size) for _ in range(size)]
        for y in range(inner):
            padded[y + 1][1:inner + 1] = canvas[y]
        canvas = padded
    return size, compose(canvas, size, color, background), text


def make_icon(size, pixels):
    """HICON from premultiplied BGRA pixels; the caller must DestroyIcon it."""
    hdc = gdi32.CreateCompatibleDC(None)
    hbm = hmask = None
    try:
        bmi = BITMAPINFO()
        h = bmi.bmiHeader
        h.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        h.biWidth, h.biHeight, h.biPlanes, h.biBitCount, h.biCompression = size, -size, 1, 32, BI_RGB
        bits = ctypes.c_void_p()
        hbm = gdi32.CreateDIBSection(hdc, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
        if not hbm or not bits:
            raise OSError(ctypes.get_last_error(), "CreateDIBSection failed")
        ctypes.memmove(bits, pixels, size * size * 4)
        hmask = gdi32.CreateBitmap(size, size, 1, 1, None)
        info = ICONINFO(True, 0, 0, hmask, hbm)
        hicon = user32.CreateIconIndirect(ctypes.byref(info))
        if not hicon:
            raise OSError(ctypes.get_last_error(), "CreateIconIndirect failed")
        return hicon
    finally:
        if hbm:
            gdi32.DeleteObject(hbm)
        if hmask:
            gdi32.DeleteObject(hmask)
        if hdc:
            gdi32.DeleteDC(hdc)


def render(texts, color="#FFFFFF", background=None, size=None, label=None, second=None, fonts=None):
    """HICON for one tray icon; the caller must DestroyIcon it."""
    size, pixels, _ = render_pixels(texts, color, background, size, label, second, fonts)
    return make_icon(size, pixels)


def png_bytes(size, pixels):
    """PNG of premultiplied BGRA pixels (previews and tests; no Pillow needed)."""
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            i = (y * size + x) * 4
            b, g, r, a = pixels[i], pixels[i + 1], pixels[i + 2], pixels[i + 3]
            if a:
                r, g, b = min(255, r * 255 // a), min(255, g * 255 // a), min(255, b * 255 // a)
            rows += bytes((r, g, b, a))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (bytes((0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A))
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


def png_on(size, pixels, background, zoom=1):
    """Opaque PNG of the icon over a background colour, scaled up by an integer zoom
    (nearest neighbour): previews in the Settings window."""
    br, bg, bb = _hex_to_rgb(background)
    n = size * zoom
    rows = bytearray()
    for y in range(n):
        rows.append(0)
        sy = y // zoom
        for x in range(n):
            i = (sy * size + x // zoom) * 4
            b, g, r, a = pixels[i], pixels[i + 1], pixels[i + 2], pixels[i + 3]
            rows += bytes((min(255, r + br * (255 - a) // 255), min(255, g + bg * (255 - a) // 255),
                           min(255, b + bb * (255 - a) // 255)))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (bytes((0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A))
            + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 6)) + chunk(b"IEND", b""))


def destroy(hicon):
    if hicon:
        user32.DestroyIcon(hicon)
