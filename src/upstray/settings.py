"""Settings in %LOCALAPPDATA%\\UpsTray\\settings.json: defaults, validation, atomic save."""
import json
import os
import re

SCHEMA = 1

DEFAULTS = {
    "schema": SCHEMA,
    "tray_readings": ["input_voltage", "load"],   # one tray icon each, in this order
    "tooltip_readings": ["input_voltage", "output_voltage", "load", "charge", "runtime"],
    "poll_seconds": 2,
    "temperature_unit": "C",          # C or F
    "runtime_style": "minutes",       # minutes or hm
    "text_color": "auto",             # auto (follows the taskbar theme) or #RRGGBB
    "reading_colors": {},             # reading key -> #RRGGBB
    "status_colors": True,            # amber text on battery, red when low, grey when offline
    "badge": False,                   # draw the value on a rounded badge instead of bare text
    "icon_style": "label",            # label (caption + value), large (value only), stacked (two per icon)
    "value_font": "",                 # font family for the values; "" = default (Bahnschrift Condensed)
    "value_weight": 0,                # 0 = the default font's weight, else 400/600/700/900
    "value_size": 100,                # % of the largest size that fits
    "label_font": "",                 # font family for the labels; "" = default (Segoe UI Semibold)
    "label_weight": 0,
    "label_size": 33,                 # label row height, % of the icon
    "nominal_watts": 0,               # rated real power, for 'Load power' when the UPS has none
    "low_charge_percent": 20,         # 'low' below this charge (besides the UPS's own warnings)
    "notify_on_battery": False,
    "notify_restored": False,
    "notify_low": True,
    "notify_offline": False,
    "notify_after_seconds": 10,       # on-battery notice only once this long on battery
    "preferred_serial": "",
    "first_run_done": False,
}

_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
ICON_STYLES = ("label", "large", "stacked")
FONT_WEIGHTS = (0, 400, 600, 700, 900)


def _font_name(v):
    """A font family name as typed or picked: printable, at most 64 characters, else ''."""
    if not isinstance(v, str):
        return ""
    v = v.strip()
    if len(v) > 64 or any(ord(c) < 32 for c in v) or v.startswith("@"):
        return ""
    return v


def app_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "UpsTray")


def settings_path():
    return os.path.join(app_dir(), "settings.json")


def _bool(v, default):
    return v if isinstance(v, bool) else default


def _int(v, default, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return default
    return int(min(hi, max(lo, v)))


def _keys(v, default):
    if not isinstance(v, list):
        return list(default)
    out = []
    for k in v:
        if isinstance(k, str) and 0 < len(k) <= 200 and k not in out:
            out.append(k)
    return out[:16]


def normalize(raw):
    """A complete, valid settings dict from whatever was stored (unknown keys dropped)."""
    s = dict(DEFAULTS)
    if not isinstance(raw, dict):
        return s
    s["tray_readings"] = _keys(raw.get("tray_readings"), DEFAULTS["tray_readings"])
    s["tooltip_readings"] = _keys(raw.get("tooltip_readings"), DEFAULTS["tooltip_readings"])
    s["poll_seconds"] = _int(raw.get("poll_seconds"), DEFAULTS["poll_seconds"], 1, 60)
    s["temperature_unit"] = raw.get("temperature_unit") if raw.get("temperature_unit") in ("C", "F") else "C"
    s["runtime_style"] = raw.get("runtime_style") if raw.get("runtime_style") in ("minutes", "hm") else "minutes"
    s["icon_style"] = raw.get("icon_style") if raw.get("icon_style") in ICON_STYLES else "label"
    for key in ("value_font", "label_font"):
        s[key] = _font_name(raw.get(key))
    for key in ("value_weight", "label_weight"):
        s[key] = raw.get(key) if raw.get(key) in FONT_WEIGHTS and not isinstance(raw.get(key), bool) else 0
    s["value_size"] = _int(raw.get("value_size"), DEFAULTS["value_size"], 40, 100)
    s["label_size"] = _int(raw.get("label_size"), DEFAULTS["label_size"], 20, 50)
    tc = raw.get("text_color")
    s["text_color"] = tc if tc == "auto" or (isinstance(tc, str) and _COLOR.match(tc)) else "auto"
    colors = raw.get("reading_colors")
    s["reading_colors"] = ({k: v for k, v in colors.items()
                            if isinstance(k, str) and isinstance(v, str) and _COLOR.match(v)}
                           if isinstance(colors, dict) else {})
    for key in ("status_colors", "badge", "notify_on_battery", "notify_restored", "notify_low",
                "notify_offline", "first_run_done"):
        s[key] = _bool(raw.get(key), DEFAULTS[key])
    s["nominal_watts"] = _int(raw.get("nominal_watts"), 0, 0, 100000)
    s["low_charge_percent"] = _int(raw.get("low_charge_percent"), DEFAULTS["low_charge_percent"], 0, 95)
    s["notify_after_seconds"] = _int(raw.get("notify_after_seconds"), DEFAULTS["notify_after_seconds"], 0, 3600)
    ps = raw.get("preferred_serial")
    s["preferred_serial"] = ps if isinstance(ps, str) and len(ps) <= 64 else ""
    return s


def load(path=None):
    path = path or settings_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return normalize(json.load(fh)), True
    except FileNotFoundError:
        return normalize({}), False
    except (OSError, ValueError):
        return normalize({}), True


def save(s, path=None):
    path = path or settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(normalize(s), fh, indent=2, sort_keys=True)
    os.replace(tmp, path)
