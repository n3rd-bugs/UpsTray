"""Tray icon images: a short text such as '231' or '75%' drawn into an icon.

GDI draws white antialiased text on black into a 32-bit DIB; the brightness of
each pixel becomes its alpha, so the text gets a real alpha channel and looks
right on light, dark and transparent taskbars. Fonts are tried from the most
condensed to the widest and the largest size that fits wins.
"""
import ctypes
import ctypes.wintypes as wt

gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

SM_CXSMICON = 49
DIB_RGB_COLORS = 0
BI_RGB = 0
FW_SEMIBOLD = 600
FW_BOLD = 700
DEFAULT_CHARSET = 1
OUT_TT_PRECIS = 4
CLIP_DEFAULT_PRECIS = 0
ANTIALIASED_QUALITY = 4
DEFAULT_PITCH = 0
TRANSPARENT = 1

# Condensed faces first: three digits must fit a 16 px icon. GDI exposes the named
# instances of the Bahnschrift variable font as separate families (names cut at 31).
FONT_FACES = ("Bahnschrift SemiBold Condensed", "Bahnschrift SemiBold SemiConden",
              "Bahnschrift Condensed", "Arial Narrow", "Segoe UI Semibold", "Segoe UI", "Arial")


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


class TEXTMETRICW(ctypes.Structure):
    _fields_ = [("tmHeight", wt.LONG), ("tmAscent", wt.LONG), ("tmDescent", wt.LONG),
                ("tmInternalLeading", wt.LONG), ("tmExternalLeading", wt.LONG),
                ("tmAveCharWidth", wt.LONG), ("tmMaxCharWidth", wt.LONG), ("tmWeight", wt.LONG),
                ("tmOverhang", wt.LONG), ("tmDigitizedAspectX", wt.LONG),
                ("tmDigitizedAspectY", wt.LONG), ("tmFirstChar", wt.WCHAR), ("tmLastChar", wt.WCHAR),
                ("tmDefaultChar", wt.WCHAR), ("tmBreakChar", wt.WCHAR), ("tmItalic", ctypes.c_ubyte),
                ("tmUnderlined", ctypes.c_ubyte), ("tmStruckOut", ctypes.c_ubyte),
                ("tmPitchAndFamily", ctypes.c_ubyte), ("tmCharSet", ctypes.c_ubyte)]


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
gdi32.GetTextExtentPoint32W.argtypes = [wt.HDC, wt.LPCWSTR, ctypes.c_int, ctypes.POINTER(wt.SIZE)]
gdi32.GetTextMetricsW.argtypes = [wt.HDC, ctypes.POINTER(TEXTMETRICW)]
gdi32.GetTextFaceW.argtypes = [wt.HDC, ctypes.c_int, wt.LPWSTR]
gdi32.GetTextFaceW.restype = ctypes.c_int
gdi32.GdiFlush.restype = wt.BOOL
user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
user32.CreateIconIndirect.restype = wt.HICON
user32.DestroyIcon.argtypes = [wt.HICON]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]

_available_faces = None


def tray_icon_size():
    """Pixel size of a small icon at the current (system) DPI: 16 at 100 %, 24 at 150 %."""
    size = user32.GetSystemMetrics(SM_CXSMICON)
    return max(16, min(64, size or 16))


def _faces(hdc):
    """The FONT_FACES that exist (GDI silently substitutes missing faces)."""
    global _available_faces
    if _available_faces is None:
        found = []
        buf = ctypes.create_unicode_buffer(64)
        for face in FONT_FACES:
            font = gdi32.CreateFontW(-12, 0, 0, 0, FW_SEMIBOLD, 0, 0, 0, DEFAULT_CHARSET, OUT_TT_PRECIS,
                                     CLIP_DEFAULT_PRECIS, ANTIALIASED_QUALITY, DEFAULT_PITCH, face)
            if not font:
                continue
            old = gdi32.SelectObject(hdc, font)
            gdi32.GetTextFaceW(hdc, 64, buf)
            gdi32.SelectObject(hdc, old)
            gdi32.DeleteObject(font)
            if buf.value.lower() == face.lower():
                found.append(face)
        _available_faces = found or ["Segoe UI"]
    return _available_faces


def _make_font(face, px):
    return gdi32.CreateFontW(-px, 0, 0, 0, FW_SEMIBOLD, 0, 0, 0, DEFAULT_CHARSET, OUT_TT_PRECIS,
                             CLIP_DEFAULT_PRECIS, ANTIALIASED_QUALITY, DEFAULT_PITCH, face)


def _measure(hdc, face, px, text):
    """(width, digit height, ascent, internal leading) of text in face at px (em height)."""
    font = _make_font(face, px)
    if not font:
        return None
    old = gdi32.SelectObject(hdc, font)
    try:
        sz = wt.SIZE()
        gdi32.GetTextExtentPoint32W(hdc, text, len(text), ctypes.byref(sz))
        tm = TEXTMETRICW()
        gdi32.GetTextMetricsW(hdc, ctypes.byref(tm))
        # digits reach the cap height, roughly ascent minus internal leading
        cap = tm.tmAscent - tm.tmInternalLeading
        return sz.cx, cap, tm.tmAscent, tm.tmInternalLeading
    finally:
        gdi32.SelectObject(hdc, old)
        gdi32.DeleteObject(font)


_layout_cache = {}


def choose_layout(hdc, texts, size, min_ratio=0.5):
    """Pick (text, face, px) with the tallest digits that fit size x size.

    The first candidate text that reaches min_ratio * size digit height wins; when
    none does, the candidate with the tallest digits is used anyway. Results are
    cached: the same few hundred texts come back all the time.
    """
    key = (tuple(texts), size, min_ratio)
    hit = _layout_cache.get(key)
    if hit is not None:
        return hit
    if len(_layout_cache) > 2048:
        _layout_cache.clear()
    hit = _layout_cache[key] = _choose_layout(hdc, texts, size, min_ratio)
    return hit


def _choose_layout(hdc, texts, size, min_ratio):
    best_any = None
    for text in texts:
        best = None
        for face in _faces(hdc):
            for px in range(int(size * 1.6), 5, -1):
                m = _measure(hdc, face, px, text)
                if not m:
                    break
                width, cap, _, _ = m
                if width <= size and cap <= size - 1:
                    if best is None or cap > best[3]:
                        best = (text, face, px, cap)
                    break
        if best is None:
            continue
        if best[3] >= size * min_ratio:
            return best[:3]
        if best_any is None or best[3] > best_any[3]:
            best_any = best
    if best_any:
        return best_any[:3]
    return (texts[-1] if texts else "", _faces(hdc)[0], max(6, size // 2))


def _hex_to_rgb(color):
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def render_pixels(texts, color="#FFFFFF", background=None, size=None):
    """(size, BGRA premultiplied pixel bytes, chosen text) for the best-fitting text."""
    size = size or tray_icon_size()
    hdc = gdi32.CreateCompatibleDC(None)
    if not hdc:
        raise OSError(ctypes.get_last_error(), "CreateCompatibleDC failed")
    hbm = None
    try:
        bmi = BITMAPINFO()
        h = bmi.bmiHeader
        h.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        h.biWidth, h.biHeight, h.biPlanes, h.biBitCount, h.biCompression = size, -size, 1, 32, BI_RGB
        bits = ctypes.c_void_p()
        hbm = gdi32.CreateDIBSection(hdc, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
        if not hbm or not bits:
            raise OSError(ctypes.get_last_error(), "CreateDIBSection failed")
        old_bm = gdi32.SelectObject(hdc, hbm)
        ctypes.memset(bits, 0, size * size * 4)
        text, face, px = choose_layout(hdc, list(texts) or ["--"], size - (2 if background else 0))
        font = _make_font(face, px)
        old_font = gdi32.SelectObject(hdc, font)
        gdi32.SetBkMode(hdc, TRANSPARENT)
        gdi32.SetTextColor(hdc, 0x00FFFFFF)
        sz = wt.SIZE()
        gdi32.GetTextExtentPoint32W(hdc, text, len(text), ctypes.byref(sz))
        tm = TEXTMETRICW()
        gdi32.GetTextMetricsW(hdc, ctypes.byref(tm))
        cap = tm.tmAscent - tm.tmInternalLeading
        x = (size - sz.cx) // 2
        y = (size - cap + 1) // 2 - tm.tmInternalLeading
        gdi32.TextOutW(hdc, x, y, text, len(text))
        gdi32.GdiFlush()
        gdi32.SelectObject(hdc, old_font)
        gdi32.DeleteObject(font)
        gdi32.SelectObject(hdc, old_bm)
        n = size * size * 4
        src = bytes((ctypes.c_ubyte * n).from_address(bits.value))
    finally:
        if hbm:
            gdi32.DeleteObject(hbm)
        gdi32.DeleteDC(hdc)
    return size, compose(center(src, size), size, color, background), text


def center(src, size):
    """Shift the drawn text so its ink box is centred (font metrics put digits a pixel
    or two off-centre, and differently per font)."""
    xs, ys = [], []
    for i in range(0, len(src), 4):
        if src[i] or src[i + 1] or src[i + 2]:
            p = i // 4
            xs.append(p % size)
            ys.append(p // size)
    if not xs:
        return src
    dx = int(round((size - 1) / 2.0 - (min(xs) + max(xs)) / 2.0))
    dy = int(round((size - 1) / 2.0 - (min(ys) + max(ys)) / 2.0))
    if not dx and not dy:
        return src
    out = bytearray(len(src))
    row = size * 4
    for y in range(size):
        sy = y - dy
        if not 0 <= sy < size:
            continue
        for x in range(size):
            sx = x - dx
            if 0 <= sx < size:
                d, s = y * row + x * 4, sy * row + sx * 4
                out[d:d + 4] = src[s:s + 4]
    return bytes(out)


def compose(coverage_bgra, size, color, background=None):
    """Turn white-on-black text pixels into premultiplied BGRA: coloured text with
    alpha = coverage, or light text on an opaque rounded badge."""
    n = size * size * 4
    fr, fg, fb = _hex_to_rgb(color)
    out = bytearray(n)
    if background:
        br, bg, bb = _hex_to_rgb(background)
        radius = max(2, size // 5)
    src = coverage_bgra
    for i in range(0, n, 4):
        cov = max(src[i], src[i + 1], src[i + 2])
        if background:
            p = i // 4
            if not _in_round_rect(p % size, p // size, size, radius):
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


def render(texts, color="#FFFFFF", background=None, size=None):
    """HICON showing the best-fitting candidate text; the caller must DestroyIcon it.

    color/background are '#RRGGBB'; background None = transparent (text only), else a
    rounded badge behind the text.
    """
    size, pixels, _ = render_pixels(texts, color, background, size)
    return make_icon(size, pixels)


def png_bytes(size, pixels):
    """PNG of premultiplied BGRA pixels (previews and tests; no Pillow needed)."""
    import struct
    import zlib
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
    return (bytes((0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A)) + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


def _in_round_rect(x, y, size, r):
    if r <= 0:
        return True
    cx = r if x < r else (size - 1 - r if x > size - 1 - r else x)
    cy = r if y < r else (size - 1 - r if y > size - 1 - r else y)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r + r


def destroy(hicon):
    if hicon:
        user32.DestroyIcon(hicon)
