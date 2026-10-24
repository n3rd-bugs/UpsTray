"""UPS Tray: command line, single instance, the app thread (Tk), the UPS poller,
the tray icons and the notifications.

Threads:
  main    Tk (Settings/Details windows) and all app state; drains a queue
  tray    hidden window + Shell_NotifyIcon (tray.TrayHost)
  poller  finds the UPS and reads snapshots (device.Ups); only posts results
"""
import ctypes
import ctypes.wintypes as wt
import json
import logging
import logging.handlers
import os
import queue
import sys
import threading
import time
import zlib

from . import APP_NAME, DISPLAY_NAME, VERSION, device, model
from . import settings as settings_mod

FROZEN = bool(getattr(sys, "frozen", False))
MUTEX_NAME = "Local\\UpsTray"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "UpsTray"
ERROR_ALREADY_EXISTS = 183
SYNCHRONIZE = 0x00100000

# status text colours for a dark and a light taskbar
STATUS_TEXT_COLORS = {"battery": "#FFB900", "low": "#FF5252", "alert": "#FF5252", "offline": "#9E9E9E"}
STATUS_TEXT_COLORS_LIGHT = {"battery": "#9A5B00", "low": "#C42B1C", "alert": "#C42B1C", "offline": "#6E6E6E"}
BADGE_COLORS = {"online": "#2D6A31", "battery": "#B26A00", "low": "#C42B1C", "alert": "#C42B1C",
                "offline": "#5A5A5A"}
STATUS_UID = 99

log = logging.getLogger("upstray")
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
_k32.CreateMutexW.restype = wt.HANDLE
_k32.OpenMutexW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
_k32.OpenMutexW.restype = wt.HANDLE
_k32.CloseHandle.argtypes = [wt.HANDLE]


# --------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------
def setup_logging():
    os.makedirs(settings_mod.app_dir(), exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(os.path.join(settings_mod.app_dir(), "upstray.log"),
                                                   maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def set_dpi_aware():
    """System DPI awareness, set before any window exists (Tk 8.6 handles this mode well;
    the tray icons are drawn at the matching small-icon size)."""
    try:
        fn = ctypes.windll.user32.SetProcessDpiAwarenessContext
        fn.argtypes = [ctypes.c_void_p]
        fn.restype = wt.BOOL
        if fn(ctypes.c_void_p(-2)):
            return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def taskbar_is_light():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "SystemUsesLightTheme")[0] == 1
    except OSError:
        return False


def resource_path(*parts):
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, *parts)
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        "build", *parts)


def run_command():
    """What HKCU Run should start: the exe, or pythonw with the entry script from source."""
    if FROZEN:
        return '"%s"' % os.path.abspath(sys.executable)
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "upstray_main.pyw")
    return '"%s" "%s"' % (pyw if os.path.exists(pyw) else exe, script)


def get_autostart():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, RUN_VALUE)[0]
    except OSError:
        return None


def set_autostart(enable):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        if enable:
            winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, run_command())
        else:
            try:
                winreg.DeleteValue(k, RUN_VALUE)
            except FileNotFoundError:
                pass


def uid_for(key, taken):
    """Stable notification-icon id per reading, so Windows remembers per reading whether
    the icon is shown on the taskbar."""
    uid = 100 + zlib.crc32(key.encode("utf-8")) % 30000
    while uid in taken:
        uid += 1
    return uid


def build_tooltip(app_title, reading, value_text, status_text, extras):
    lines = []
    if reading is not None:
        lines.append("%s %s" % (reading.name, value_text))
    lines.append(status_text)
    lines.extend(extras)
    lines.append(app_title)
    out = []
    for line in lines:
        if len("\n".join(out + [line])) > 127:
            break
        out.append(line)
    return "\n".join(out)


# --------------------------------------------------------------------------------
# poller
# --------------------------------------------------------------------------------
class Poller(threading.Thread):
    def __init__(self, app):
        super().__init__(name="poller", daemon=True)
        self.app = app
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._reconnect = False
        self._fails = 0
        self._next_scan = 0.0
        self.ups = None

    def stop(self):
        self._stop.set()
        self._wake.set()

    def wake(self, reconnect=False):
        if reconnect:
            self._reconnect = True
        self._wake.set()

    def run(self):
        while not self._stop.is_set():
            self.step()
            self._wake.wait(max(1, int(self.app.settings.get("poll_seconds", 2))))
            self._wake.clear()
        if self.ups is not None:
            self.ups.close()

    def step(self, now=None):
        """One poll: connect if needed, read a snapshot, post what happened."""
        now = time.time() if now is None else now
        s = self.app.settings
        try:
            if self._reconnect and self.ups is not None:
                self._reconnect = False
                self.ups.close()
                self.ups = None
                self._next_scan = 0.0
            if self.ups is None:
                if now < self._next_scan:
                    return
                backend = device.find_backend(s.get("preferred_serial", ""))
                if backend is None:
                    self._next_scan = now + 5.0
                    # keeps the app's offline timer (and notification) running while we look
                    self.app.post("disconnected", None)
                    return
                self.ups = device.Ups(backend, s.get("nominal_watts", 0))
                self._fails = 0
                log.info("UPS found: %s serial=%s readings=%d", self.ups.info.title(),
                         self.ups.info.serial, len(self.ups.readings))
                self.app.post("connected", self.ups)
            snap = self.ups.snapshot(self.app.wanted_keys(), s.get("low_charge_percent", 0))
            self._fails = 0 if snap.status.ok else self._fails + 1
            self.app.post("snapshot", snap)
            if self._fails >= 3:
                log.warning("UPS stopped answering; looking for it again")
                self.ups.close()
                self.ups = None
                self._next_scan = now + 2.0
                self.app.post("disconnected", None)
        except Exception:
            log.exception("poll failed")
            if self.ups is not None:
                try:
                    self.ups.close()
                except Exception:
                    pass
                self.ups = None
                self.app.post("disconnected", None)
            self._next_scan = now + 5.0


# --------------------------------------------------------------------------------
# the app
# --------------------------------------------------------------------------------
class App:
    def __init__(self, open_settings=False):
        import tkinter as tk
        from . import glyphs, tray, ui
        self.tk, self.glyphs, self.traymod, self.ui = tk, glyphs, tray, ui
        self.settings, existed = settings_mod.load()
        self.open_settings_on_start = open_settings or not self.settings.get("first_run_done")
        self.root = tk.Tk()
        self.root.withdraw()
        ui.style_root(self.root)
        icon = resource_path("icons", "upstray.ico")
        self.window_icon = icon if os.path.exists(icon) else None
        self.q = queue.Queue()
        self.ups = None
        self.last_snapshot = None
        self.settings_window = None
        self.details_window = None
        self._icon_sig = None
        self._uids = {}
        self._on_battery_since = None
        self._battery_notified = False
        self._low_notified = False
        self._offline_since = None
        self._offline_notified = False
        self._quitting = False
        self._menu = {"status": DISPLAY_NAME, "autostart": False, "autostart_ok": True}
        self.tray = tray.TrayHost(self._from_tray, lambda: dict(self._menu))
        self.poller = Poller(self)

    # -- threads -> app -------------------------------------------------------------
    def post(self, kind, payload=None):
        self.q.put((kind, payload))

    def _from_tray(self, action):
        self.q.put(("tray", action))

    def wanted_keys(self):
        keys = set(self.settings["tray_readings"]) | set(self.settings["tooltip_readings"]) | {"charge"}
        if self.details_window is not None or self.settings_window is not None:
            return None                       # everything
        return sorted(keys)

    # -- run ------------------------------------------------------------------------
    def run(self):
        self.tray.start()
        self.tray.ready.wait(10)
        if self.tray.failed:
            raise RuntimeError("tray window failed: %s" % self.tray.failed)
        self._refresh_menu()
        self.poller.start()
        self.root.after(100, self._drain)
        if self.open_settings_on_start:
            # give the poller a moment so the list shows this UPS's readings
            self.root.after(1500, self.open_settings)
        self.root.mainloop()
        log.info("exit")

    def _drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                self._handle(kind, payload)
        except queue.Empty:
            pass
        except Exception:
            log.exception("event handling failed")
        if not self._quitting:
            self.root.after(100, self._drain)

    def _handle(self, kind, payload):
        if kind == "tray":
            if payload not in ("ready", "theme"):
                log.info("tray action: %s", payload)
            if payload == "details":
                self.open_details()
            elif payload == "settings":
                self.open_settings()
            elif payload == "autostart":
                self._toggle_autostart()
            elif payload == "logs":
                os.startfile(settings_mod.app_dir())
            elif payload in ("exit", "end_session"):
                self.quit()
            elif payload == "theme":
                self._icon_sig = None
                self._update_icons()
            elif payload == "ready":
                self._update_icons()
            elif payload == "resume":
                self.poller.wake()
        elif kind == "connected":
            self.ups = payload
            if payload.info.serial and payload.info.serial != self.settings.get("preferred_serial"):
                s = dict(self.settings)
                s["preferred_serial"] = payload.info.serial
                self._save(s)
            self._icon_sig = None
            if self.settings_window:
                self.settings_window.reload()
        elif kind == "disconnected":
            self.last_snapshot = device.Snapshot(time=time.time(), status=model.Status(ok=False))
            self._after_snapshot()
        elif kind == "snapshot":
            self.last_snapshot = payload
            self._after_snapshot()

    def _after_snapshot(self):
        self._update_icons()
        self._notify()
        self._refresh_menu()
        if self.details_window:
            self.details_window.refresh()
        if self.settings_window:
            self.settings_window.refresh_values()

    # -- icons ----------------------------------------------------------------------
    def taskbar_light(self):
        return taskbar_is_light()

    def effective_text_color(self, s=None):
        tc = (s or self.settings)["text_color"]
        if tc != "auto":
            return tc
        return "#000000" if taskbar_is_light() else "#FFFFFF"

    def _state(self):
        snap = self.last_snapshot
        if snap is None:
            return "offline" if self.ups is None else "online"
        return snap.status.state

    def _colors(self, key, state, s=None):
        s = s or self.settings
        if s["badge"]:
            return "#FFFFFF", BADGE_COLORS.get(state if s["status_colors"] else "online", "#2D6A31")
        if s["status_colors"] and state in STATUS_TEXT_COLORS:
            light = (s["text_color"] == "auto" and taskbar_is_light()) or s["text_color"] == "#000000"
            return (STATUS_TEXT_COLORS_LIGHT if light else STATUS_TEXT_COLORS)[state], None
        return s["reading_colors"].get(key) or self.effective_text_color(s), None

    def _value_text(self, r):
        snap = self.last_snapshot
        if snap is None or r is None:
            return "-"
        return model.format_value(r, snap.values.get(r.key), self.settings["temperature_unit"],
                                  self.settings["runtime_style"])

    def _status_text(self):
        if self.ups is None:
            return "Looking for a UPS..."
        snap = self.last_snapshot
        return snap.status.summary() if snap else "Reading the UPS..."

    def _tooltip_extras(self, skip):
        out = []
        if not self.ups:
            return out
        skip = skip if isinstance(skip, (tuple, list, set)) else (skip,)
        for key in self.settings["tooltip_readings"]:
            if key in skip or key not in self.ups.by_key:
                continue
            r = self.ups.by_key[key]
            out.append("%s %s" % (r.name, self._value_text(r)))
        return out

    def _texts(self, key, s, compact):
        """Candidate texts for one reading's value ('--' when unknown or offline)."""
        r = self.ups.by_key.get(key) if self.ups else None
        snap = self.last_snapshot
        if r is None or (snap is not None and not snap.status.ok):
            return ("--",), r
        value = snap.values.get(key) if snap else None
        return tuple(model.tray_texts(r, value, s["temperature_unit"], compact=compact)), r

    def icon_style(self, s=None, size=None):
        """The style that is drawn: labels and stacked pairs need icons of 20 px or more."""
        s = s or self.settings
        size = size or self.glyphs.tray_icon_size()
        style = s.get("icon_style", "label")
        if style != "large" and not self.glyphs.label_fits(size):
            return "large"
        return style

    def icon_plan(self, s=None, keys=None, size=None):
        """[(uid, texts, fg, bg, tooltip, label, second)] for the tray, or for the Settings
        preview when s/keys are given."""
        s = s or self.settings
        size = size or self.glyphs.tray_icon_size()
        style = self.icon_style(s, size)
        state = self._state()
        title = self.ups.info.title() if self.ups else DISPLAY_NAME
        status_text = self._status_text()
        chosen = s["tray_readings"] if keys is None else keys
        keys = [k for k in chosen if self.ups is None or k in self.ups.by_key]
        plan = []
        if not keys:
            texts = {"online": ("AC",), "battery": ("BAT",), "low": ("LOW",), "alert": ("!",),
                     "offline": ("--",)}.get(state, ("--",))
            fg, bg = self._colors("_status", state, s)
            tip = build_tooltip(title, None, "", status_text, self._tooltip_extras(None))
            plan.append((STATUS_UID, texts, fg, bg, tip, "UPS" if style != "large" else None, None))
            return plan
        groups = [keys[i:i + 2] for i in range(0, len(keys), 2)] if style == "stacked" else [[k] for k in keys]
        taken = set()
        for group in groups:
            gkey = "+".join(group)
            uid = self._uids.get(gkey) or uid_for(gkey, taken | set(self._uids.values()))
            self._uids[gkey] = uid
            taken.add(uid)
            first = group[0]
            r = self.ups.by_key.get(first) if self.ups else None
            label = (r.label or None) if (style == "label" and r is not None) else None
            texts, r = self._texts(first, s, compact=label is not None)
            second = None
            lines = []
            if r is not None:
                lines.append((r, self._value_text(r)))
            if len(group) == 2:
                second, r2 = self._texts(group[1], s, compact=False)
                if r2 is not None:
                    lines.append((r2, self._value_text(r2)))
            fg, bg = self._colors(first, state, s)
            if lines:
                head = "\n".join("%s %s" % (rr.name, vt) for rr, vt in lines[1:])
                tip = build_tooltip(title, lines[0][0], lines[0][1], status_text,
                                    ([head] if head else []) + self._tooltip_extras(group))
            else:
                tip = "%s\n%s" % (status_text, title)
            plan.append((uid, texts, fg, bg, tip, label, second))
        return plan

    def _update_icons(self):
        if self._quitting:
            return
        size = self.glyphs.tray_icon_size()
        plan = self.icon_plan(size=size)
        sig = (size, tuple(plan))
        if sig == self._icon_sig:
            return
        self._icon_sig = sig
        icons = []
        for uid, texts, fg, bg, tip, label, second in plan:
            try:
                hicon = self.glyphs.render(list(texts), fg, bg, size, label, list(second) if second else None)
            except OSError:
                log.exception("icon render failed")
                hicon = None
            icons.append((uid, hicon, tip))
        self.tray.set_icons(icons)

    # -- notifications --------------------------------------------------------------
    def _notify(self, now=None):
        s = self.settings
        snap = self.last_snapshot
        now = time.time() if now is None else now
        st = snap.status if snap else model.Status()
        title = self.ups.info.title() if self.ups else "UPS"
        if st.ok:
            if self._offline_notified:
                self._offline_notified = False
            self._offline_since = None
            if st.on_battery:
                if self._on_battery_since is None:
                    self._on_battery_since = now
                    log.info("on battery")
                if (s["notify_on_battery"] and not self._battery_notified
                        and now - self._on_battery_since >= s["notify_after_seconds"]):
                    self._battery_notified = True
                    self.tray.balloon("%s is on battery" % title, self._battery_summary(), warning=True)
                if st.low and not self._low_notified:
                    self._low_notified = True
                    log.warning("battery low")
                    if s["notify_low"]:
                        self.tray.balloon("%s battery low" % title, self._battery_summary(), warning=True)
            else:
                if self._on_battery_since is not None:
                    secs = int(now - self._on_battery_since)
                    log.info("mains back after %d s on battery", secs)
                    if s["notify_restored"] and self._battery_notified:
                        self.tray.balloon("Mains power restored", "%s ran on battery for %s." % (
                            title, model.runtime_text(secs / 60.0) if secs >= 60 else "%d s" % secs))
                self._on_battery_since = None
                self._battery_notified = False
                self._low_notified = False
        else:
            if self._offline_since is None:
                self._offline_since = now
            if s["notify_offline"] and not self._offline_notified and now - self._offline_since >= 10:
                self._offline_notified = True
                self.tray.balloon("UPS not responding", "%s does not answer over USB. Check the cable and "
                                  "that the UPS is on." % title, warning=True)

    def _battery_summary(self):
        parts = []
        for key in ("charge", "runtime", "load"):
            r = self.ups.by_key.get(key) if self.ups else None
            if r:
                parts.append("%s %s" % (r.name, self._value_text(r)))
        return ", ".join(parts) or "Running on battery."

    # -- menu, autostart --------------------------------------------------------------
    def _refresh_menu(self):
        title = self.ups.info.title() if self.ups else DISPLAY_NAME
        self._menu = {"status": "%s: %s" % (title, self._status_text()),
                      "autostart": self.autostart_enabled(), "autostart_ok": self.autostart_possible()}

    def autostart_possible(self):
        return True

    def autostart_enabled(self):
        cur = get_autostart()
        return bool(cur) and cur.strip().lower() == run_command().lower()

    def _toggle_autostart(self):
        try:
            set_autostart(not self.autostart_enabled())
        except OSError:
            log.exception("autostart change failed")
        self._refresh_menu()

    # -- windows --------------------------------------------------------------------
    def open_settings(self):
        if self._quitting:
            return
        if self.settings_window is None:
            self.settings_window = self.ui.SettingsWindow(self)
            self.poller.wake()
        self.settings_window.show()

    def open_details(self):
        if self._quitting:
            return
        if self.details_window is None:
            self.details_window = self.ui.DetailsWindow(self)
            self.poller.wake()
        self.details_window.show()

    def _save(self, s):
        s = settings_mod.normalize(s)
        try:
            settings_mod.save(s)
        except OSError:
            log.exception("saving settings failed")
        self.settings = s

    def apply_settings(self, s, autostart=None):
        old = self.settings
        self._save(s)
        if autostart is not None and autostart != self.autostart_enabled():
            try:
                set_autostart(autostart)
            except OSError:
                log.exception("autostart change failed")
        reconnect = old.get("nominal_watts") != self.settings.get("nominal_watts")
        self.poller.wake(reconnect=reconnect)
        self._icon_sig = None
        self._update_icons()
        self._refresh_menu()

    def quit(self):
        if self._quitting:
            return
        self._quitting = True
        log.info("quitting")
        self.poller.stop()
        self.tray.stop()
        self.tray.join(3)
        for w in (self.settings_window, self.details_window):
            try:
                if w:
                    w.win.destroy()
            except Exception:
                pass
        self.root.after(0, self.root.quit)


# --------------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------------
class Output:
    """Collects text for --out (the windowed exe has no console)."""

    def __init__(self, path):
        self.path = path
        self.lines = []

    def say(self, text=""):
        self.lines.append(text)
        if not self.path and sys.stdout is not None:
            try:
                print(text)
            except Exception:
                pass

    def close(self):
        if self.path:
            with open(self.path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(self.lines) + ("\n" if self.lines else ""))


def selftest():
    """Read-only checks; pass without a UPS. Returns (ok, dict)."""
    checks = {}
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        checks[name] = {"ok": bool(cond), "detail": detail}
        ok = ok and bool(cond)

    check("python", sys.version_info[:2] >= (3, 8), sys.version.split()[0])
    try:
        import tkinter
        lvl = tkinter.Tcl().eval("info patchlevel")
        check("tcl_tk", True, lvl)
    except Exception as e:
        check("tcl_tk", False, repr(e))
    try:
        from . import winhid
        paths = winhid.interface_paths()
        check("hid", True, "%d HID collections" % len(paths))
    except Exception as e:
        check("hid", False, repr(e))
    try:
        from . import glyphs
        h = glyphs.render(["235"], "#FFFFFF", None, 24, label="VIN")
        glyphs.destroy(h)
        check("icon_render", bool(h), "value font %s %d, label font %s %d" % (glyphs.value_face() + glyphs.label_face()))
    except Exception as e:
        check("icon_render", False, repr(e))
    try:
        s = settings_mod.normalize({"poll_seconds": 999, "tray_readings": ["load", "load", 3]})
        check("settings", s["poll_seconds"] == 60 and s["tray_readings"] == ["load"])
    except Exception as e:
        check("settings", False, repr(e))
    if FROZEN:
        tk_dir = resource_path("_tk_data")
        check("tk_data", os.path.isdir(tk_dir) or os.path.isdir(resource_path("tcl", "tk8.6")), "")
    try:
        backend = device.find_backend()
        if backend is None:
            checks["ups"] = {"ok": True, "detail": "no UPS connected (not required)"}
        else:
            ups = device.Ups(backend)
            snap = ups.snapshot()
            checks["ups"] = {"ok": True, "detail": "%s, %d readings, %s" % (
                ups.info.title(), len(ups.readings), snap.status.summary())}
            ups.close()
    except Exception as e:
        checks["ups"] = {"ok": True, "detail": "UPS check skipped: %r" % (e,)}
    return ok, {"ok": ok, "version": VERSION, "frozen": FROZEN, "checks": checks}


def instance_running():
    h = _k32.OpenMutexW(SYNCHRONIZE, False, MUTEX_NAME)
    if h:
        _k32.CloseHandle(h)
        return True
    return False


def cli_quit(timeout=10.0):
    from . import tray
    if not tray.find_instance_window() and not instance_running():
        return 0
    tray.post_to_instance(tray.MSG_REQUEST_QUIT)
    end = time.time() + timeout
    while time.time() < end:
        if not tray.find_instance_window() and not instance_running():
            return 0
        time.sleep(0.2)
    return 1


USAGE = """UPS Tray %s - APC UPS readings in the Windows notification area

  UpsTray.exe                 start (or show the details of the running copy)
  UpsTray.exe --settings      open Settings (in the running copy, or at start)
  UpsTray.exe --quit          ask the running copy to exit
  UpsTray.exe --version       print the version
  UpsTray.exe --selftest      read-only checks (no UPS needed)
  UpsTray.exe --dump          print a JSON report of the connected UPS
  --out <file>                write the output of --version/--selftest/--dump to a file
""" % VERSION


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    out_path = None
    if "--out" in args:
        i = args.index("--out")
        if i + 1 >= len(args):
            print("--out needs a file name")
            return 2
        out_path = args[i + 1]
        del args[i:i + 2]
    out = Output(out_path)
    try:
        if "--help" in args or "-h" in args or "/?" in args:
            out.say(USAGE)
            return 0
        if "--version" in args:
            out.say("%s %s" % (APP_NAME, VERSION))
            return 0
        if "--selftest" in args:
            ok, data = selftest()
            out.say(json.dumps(data, indent=2))
            return 0 if ok else 1
        if "--dump" in args:
            backend = device.find_backend()
            if backend is None:
                out.say(json.dumps({"error": "no UPS found"}))
                return 1
            ups = device.Ups(backend)
            data = ups.export()
            data["app"] = "%s %s" % (APP_NAME, VERSION)
            out.say(json.dumps(data, indent=1))
            ups.close()
            return 0
        if "--quit" in args:
            return cli_quit()
    finally:
        out.close()

    # the tray app -------------------------------------------------------------------
    from . import tray
    mutex = _k32.CreateMutexW(None, False, MUTEX_NAME)
    if mutex and ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        # already running: show its live details (or Settings with --settings) and leave
        tray.post_to_instance(tray.MSG_OPEN_SETTINGS if "--settings" in args else tray.MSG_OPEN_DETAILS)
        _k32.CloseHandle(mutex)
        return 0
    setup_logging()
    log.info("%s %s starting (frozen=%s)", APP_NAME, VERSION, FROZEN)
    set_dpi_aware()
    try:
        App(open_settings="--settings" in args).run()
    except Exception:
        log.exception("fatal")
        raise
    finally:
        if mutex:
            _k32.CloseHandle(mutex)
    return 0
