"""The hidden window that owns the notification-area icons, on its own thread.

The app hands it a list of icons (id, HICON, tooltip) whenever a value changes;
this thread adds, modifies and removes the icons with Shell_NotifyIcon, shows the
shared context menu and reports clicks back through callbacks that must only
queue work for the app thread. It also follows Windows' removal notices for the
open UPS handle and closes the device when Windows wants it (driver change,
removal), so that UPS Tray never holds such a change up.
"""
import ctypes
import ctypes.wintypes as wt
import logging
import os
import threading

log = logging.getLogger("upstray")

u32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, WPARAM, LPARAM)
WINEVENTPROC = ctypes.WINFUNCTYPE(None, wt.HANDLE, wt.DWORD, wt.HWND, wt.LONG, wt.LONG, wt.DWORD, wt.DWORD)

WND_CLASS = "UpsTray.Window"
WND_TITLE = "UPS Tray"

WM_NULL, WM_DESTROY, WM_CLOSE, WM_QUERYENDSESSION, WM_ENDSESSION = 0x0000, 0x0002, 0x0010, 0x0011, 0x0016
WM_SETTINGCHANGE, WM_CONTEXTMENU, WM_TIMER = 0x001A, 0x007B, 0x0113
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x0202, 0x0203, 0x0205
WM_POWERBROADCAST, WM_DEVICECHANGE = 0x0218, 0x0219
WM_USER, WM_APP = 0x0400, 0x8000
NIN_SELECT = WM_USER + 0
NIN_KEYSELECT = WM_USER + 1
NIN_BALLOONSHOW, NIN_BALLOONHIDE = WM_USER + 2, WM_USER + 3
NIN_BALLOONTIMEOUT, NIN_BALLOONUSERCLICK = WM_USER + 4, WM_USER + 5
SPI_GETMESSAGEDURATION = 0x2016
ENDSESSION_CLOSEAPP = 0x00000001
PBT_APMRESUMEAUTOMATIC = 0x0012

WM_TRAYCB = WM_APP + 1
MSG_APPLY = WM_APP + 2
MSG_BALLOON = WM_APP + 3
MSG_QUIT = WM_APP + 4            # internal: remove the icons and end the message loop
MSG_OPEN_SETTINGS = WM_APP + 5   # from a second instance
MSG_REQUEST_QUIT = WM_APP + 6    # from --quit
MSG_OPEN_DETAILS = WM_APP + 7   # from a second instance
MSG_WATCH = WM_APP + 8          # internal: follow removal notices for a new device handle

NIM_ADD, NIM_MODIFY, NIM_DELETE, NIM_SETVERSION = 0, 1, 2, 4
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO, NIF_SHOWTIP = 0x01, 0x02, 0x04, 0x10, 0x80
NIIF_INFO, NIIF_WARNING, NIIF_NOSOUND = 0x01, 0x02, 0x10
NOTIFYICON_VERSION_4 = 4

MF_STRING, MF_SEPARATOR, MF_GRAYED, MF_CHECKED, MF_POPUP = 0x0000, 0x0800, 0x0001, 0x0008, 0x0010
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x0002, 0x0100, 0x0080

EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT, WINEVENT_SKIPOWNPROCESS = 0x0000, 0x0002
OBJID_WINDOW = 0

RETRY_TIMER = 0x7A1
BALLOON_TIMER = 0x7A2           # shows the next notice if Windows never reports the current one gone
BALLOON_WAIT_MS = 60000         # not on screen yet: Windows may hold it behind other notifications

DBT_DEVICEQUERYREMOVE, DBT_DEVICEREMOVEPENDING, DBT_DEVICEREMOVECOMPLETE = 0x8001, 0x8003, 0x8004
DBT_DEVTYP_HANDLE = 6
DEVICE_NOTIFY_WINDOW_HANDLE = 0

IDM_DETAILS, IDM_SETTINGS, IDM_AUTOSTART, IDM_LOGS, IDM_EXIT, IDM_STATUS = 1, 2, 3, 4, 5, 10
IDM_SELFTEST, IDM_CALIBRATE, IDM_ABORT_TEST = 11, 12, 13


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]


class DEV_BROADCAST_HDR(ctypes.Structure):
    _fields_ = [("dbch_size", wt.DWORD), ("dbch_devicetype", wt.DWORD), ("dbch_reserved", wt.DWORD)]


class DEV_BROADCAST_HANDLE(ctypes.Structure):
    _fields_ = [("dbch_size", wt.DWORD), ("dbch_devicetype", wt.DWORD), ("dbch_reserved", wt.DWORD),
                ("dbch_handle", wt.HANDLE), ("dbch_hdevnotify", ctypes.c_void_p), ("dbch_eventguid", GUID),
                ("dbch_nameoffset", wt.LONG), ("dbch_data", ctypes.c_ubyte * 1)]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE),
                ("hIcon", wt.HICON), ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT), ("uFlags", wt.UINT),
                ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
                ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD), ("szInfo", wt.WCHAR * 256),
                ("uVersion", wt.UINT), ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
                ("guidItem", GUID), ("hBalloonIcon", wt.HICON)]


for _name, _args, _ret in [
    ("DefWindowProcW", [wt.HWND, wt.UINT, WPARAM, LPARAM], LRESULT),
    ("RegisterClassExW", [ctypes.c_void_p], wt.ATOM),
    ("UnregisterClassW", [wt.LPCWSTR, wt.HINSTANCE], wt.BOOL),
    ("CreateWindowExW", [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID], wt.HWND),
    ("DestroyWindow", [wt.HWND], wt.BOOL),
    ("IsWindow", [wt.HWND], wt.BOOL),
    ("GetMessageW", [ctypes.c_void_p, wt.HWND, wt.UINT, wt.UINT], ctypes.c_int),
    ("TranslateMessage", [ctypes.c_void_p], wt.BOOL),
    ("DispatchMessageW", [ctypes.c_void_p], LRESULT),
    ("PostMessageW", [wt.HWND, wt.UINT, WPARAM, LPARAM], wt.BOOL),
    ("PostQuitMessage", [ctypes.c_int], None),
    ("RegisterWindowMessageW", [wt.LPCWSTR], wt.UINT),
    ("FindWindowW", [wt.LPCWSTR, wt.LPCWSTR], wt.HWND),
    ("GetWindowThreadProcessId", [wt.HWND, ctypes.POINTER(wt.DWORD)], wt.DWORD),
    ("CreatePopupMenu", [], wt.HMENU),
    ("DestroyMenu", [wt.HMENU], wt.BOOL),
    ("AppendMenuW", [wt.HMENU, wt.UINT, WPARAM, wt.LPCWSTR], wt.BOOL),
    ("TrackPopupMenu", [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND,
                        ctypes.c_void_p], wt.BOOL),
    ("SetForegroundWindow", [wt.HWND], wt.BOOL),
    ("GetForegroundWindow", [], wt.HWND),
    ("GetClassNameW", [wt.HWND, wt.LPWSTR, ctypes.c_int], ctypes.c_int),
    ("GetCursorPos", [ctypes.POINTER(wt.POINT)], wt.BOOL),
    ("SetTimer", [wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p], ctypes.c_size_t),
    ("KillTimer", [wt.HWND, ctypes.c_size_t], wt.BOOL),
    ("DestroyIcon", [wt.HICON], wt.BOOL),
    ("SetWinEventHook", [wt.DWORD, wt.DWORD, wt.HMODULE, WINEVENTPROC, wt.DWORD, wt.DWORD, wt.DWORD],
     wt.HANDLE),
    ("UnhookWinEvent", [wt.HANDLE], wt.BOOL),
    ("RegisterDeviceNotificationW", [wt.HANDLE, ctypes.c_void_p, wt.DWORD], ctypes.c_void_p),
    ("UnregisterDeviceNotification", [ctypes.c_void_p], wt.BOOL),
    ("SystemParametersInfoW", [wt.UINT, wt.UINT, ctypes.c_void_p, wt.UINT], wt.BOOL),
]:
    _fn = getattr(u32, _name)
    _fn.argtypes = _args
    _fn.restype = _ret
k32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
k32.GetModuleHandleW.restype = wt.HMODULE
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wt.BOOL

# Shell windows that hold the foreground while the tray is used; the menu never
# hands the focus back to them.
SHELL_FG_CLASSES = frozenset((
    "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",
    "TopLevelWindowForOverflowXamlIsland", "XamlExplorerHostIslandWindow",
    "Windows.UI.Core.CoreWindow", "Progman", "WorkerW"))


def signed16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def tray_event_action(evt, v4):
    """'menu', 'details' or None for one tray callback event.

    With NOTIFYICON_VERSION_4 one right-click sends WM_RBUTTONUP and WM_CONTEXTMENU,
    so the menu opens on WM_CONTEXTMENU only (also what the keyboard sends). Balloon
    and popup notifications (NIN_BALLOON*, NIN_POPUP*) never open anything.
    """
    if evt == WM_CONTEXTMENU:
        return "menu" if v4 else None
    if evt == WM_RBUTTONUP:
        return None if v4 else "menu"
    if evt in (NIN_SELECT, NIN_KEYSELECT) and v4:
        return "details"
    if evt == WM_LBUTTONUP and not v4:
        return "details"
    return None


def notice_display_ms():
    """How long Windows keeps a notice on screen (Settings > Accessibility > 'Dismiss
    notifications after'), plus a margin."""
    seconds = wt.ULONG(5)
    if not u32.SystemParametersInfoW(SPI_GETMESSAGEDURATION, 0, ctypes.byref(seconds), 0):
        seconds.value = 5
    return (max(5, seconds.value) + 5) * 1000


def battery_menu(state):
    """Items of the Battery test submenu: (command id, text, enabled)."""
    start, abort = bool(state.get("test_start")), bool(state.get("test_abort"))
    return [(IDM_SELFTEST, "Run self-test...", start), (IDM_CALIBRATE, "Runtime calibration...", start),
            (IDM_ABORT_TEST, "Abort test...", abort)]


def clip_tip(text):
    """Tooltips hold 127 characters; cut at a line end when possible."""
    if len(text) <= 127:
        return text
    cut = text.rfind("\n", 0, 127)
    return text[:cut] if cut > 40 else text[:126] + "…"


def find_instance_window():
    return u32.FindWindowW(WND_CLASS, None)


def post_to_instance(msg):
    h = find_instance_window()
    return bool(h) and bool(u32.PostMessageW(h, msg, 0, 0))


class TrayHost(threading.Thread):
    """Owns the hidden window. Callbacks run on this thread and must be quick:
    on_action(name) with name in details, settings, autostart, logs, exit, ready,
    theme, resume, end_session, selftest, calibrate, abort_test. on_release() closes
    the UPS device and returns once it is closed."""

    def __init__(self, on_action, menu_state, on_release=None):
        super().__init__(name="tray", daemon=True)
        self.on_action = on_action
        self.menu_state = menu_state        # callable -> dict(status, autostart, autostart_ok, test_start, test_abort)
        self.on_release = on_release
        self.hwnd = None
        self.hinst = None
        self.v4 = {}
        self._wndproc = None
        self._fg_proc = None
        self._fg_hook = None
        self._app_fg = None
        self._taskbar_created = 0
        self._lock = threading.Lock()
        self._pending = None                # [(uid, hicon, tip)] waiting for MSG_APPLY
        self._balloons = []                 # notices waiting to be shown, oldest first
        self._balloon_up = False            # a notice is on screen (tray thread only)
        self._shown = {}                    # uid -> (hicon, tip) currently in the tray
        self._added = set()
        self._in_menu = False
        self._retrying = False
        self._watch_handle = None           # device handle to follow (set by the app thread)
        self._notify_handle = None          # its HDEVNOTIFY (tray thread only)
        self.ready = threading.Event()
        self.failed = None

    # -- called from the app thread -------------------------------------------------
    def set_icons(self, icons):
        """icons: list of (uid, hicon, tip). Ownership of the HICONs passes to the tray."""
        with self._lock:
            old, self._pending = self._pending, list(icons)
        if old:
            # never applied: free those handles here
            for _, hicon, _ in old:
                if hicon and not any(hicon == h for _, h, _ in icons):
                    u32.DestroyIcon(hicon)
        self._post(MSG_APPLY)

    def balloon(self, title, text, warning=False):
        with self._lock:
            self._balloons.append((title[:63], text[:255], warning))
            del self._balloons[:-4]         # a burst keeps its latest few
        self._post(MSG_BALLOON)

    def watch(self, handle):
        """Follow Windows' removal notices for this open device handle (None: stop)."""
        with self._lock:
            self._watch_handle = handle
        self._post(MSG_WATCH)

    def stop(self):
        self._post(MSG_QUIT)

    def _post(self, msg):
        if self.hwnd:
            u32.PostMessageW(self.hwnd, msg, 0, 0)

    # -- thread ---------------------------------------------------------------------
    def run(self):
        try:
            self.hinst = k32.GetModuleHandleW(None)
            self._wndproc = WNDPROC(self._wnd_proc)
            self._taskbar_created = u32.RegisterWindowMessageW("TaskbarCreated")
            cls = WNDCLASSEXW(cbSize=ctypes.sizeof(WNDCLASSEXW), lpfnWndProc=self._wndproc,
                              hInstance=self.hinst, lpszClassName=WND_CLASS)
            if not u32.RegisterClassExW(ctypes.byref(cls)):
                raise OSError("RegisterClassExW failed gle=%d" % ctypes.get_last_error())
            # a normal (hidden) top-level window: message-only windows miss TaskbarCreated
            self.hwnd = u32.CreateWindowExW(0, WND_CLASS, WND_TITLE, 0, 0, 0, 0, 0, None, None,
                                            self.hinst, None)
            if not self.hwnd:
                raise OSError("CreateWindowExW failed gle=%d" % ctypes.get_last_error())
            self._fg_proc = WINEVENTPROC(self._on_foreground)
            self._fg_hook = u32.SetWinEventHook(EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND, None,
                                                self._fg_proc, 0, 0,
                                                WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS)
        except Exception as e:
            self.failed = e
            log.exception("tray window failed")
            self.ready.set()
            return
        self.ready.set()
        self.on_action("ready")
        msg = wt.MSG()
        while True:
            r = u32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r in (0, -1):
                break
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))
        log.info("tray thread ended")

    def _on_foreground(self, _hook, _event, hwnd, id_object, _child, _thread, _time):
        try:
            if hwnd and id_object == OBJID_WINDOW and hwnd != self.hwnd:
                buf = ctypes.create_unicode_buffer(128)
                u32.GetClassNameW(hwnd, buf, 128)
                pid = wt.DWORD(0)
                u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if buf.value not in SHELL_FG_CLASSES and pid.value != os.getpid():
                    self._app_fg = hwnd
        except Exception:
            pass

    # -- icons ----------------------------------------------------------------------
    def _nid(self, uid, flags=0):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = uid
        nid.uFlags = flags
        return nid

    def _add(self, uid, hicon, tip):
        nid = self._nid(uid, NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP)
        nid.uCallbackMessage = WM_TRAYCB
        nid.hIcon = hicon
        nid.szTip = clip_tip(tip)
        ok = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))
        if not ok:
            # a leftover icon with this id (after a crash) or an add that timed out
            ok = bool(shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)))
        if ok:
            ver = self._nid(uid)
            ver.uVersion = NOTIFYICON_VERSION_4
            self.v4[uid] = bool(shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(ver)))
            self._added.add(uid)
        return ok

    def _apply(self):
        with self._lock:
            pending, self._pending = self._pending, None
        if pending is None:
            return
        wanted = {uid: (hicon, tip) for uid, hicon, tip in pending}
        failed = False
        for uid in list(self._shown):
            if uid not in wanted:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid(uid)))
                self._added.discard(uid)
                old = self._shown.pop(uid)[0]
                if old:
                    u32.DestroyIcon(old)
        for uid, hicon, tip in pending:
            prev = self._shown.get(uid)
            if uid not in self._added:
                ok = self._add(uid, hicon, tip)
            else:
                nid = self._nid(uid, NIF_ICON | NIF_TIP | NIF_SHOWTIP)
                nid.hIcon = hicon
                nid.szTip = clip_tip(tip)
                ok = bool(shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)))
                if not ok:
                    self._added.discard(uid)
                    ok = self._add(uid, hicon, tip)
            self._shown[uid] = (hicon, tip)
            if prev and prev[0] and prev[0] != hicon:
                u32.DestroyIcon(prev[0])
            failed = failed or not ok
        if failed:
            log.warning("tray: Shell_NotifyIcon failed; retrying every 3 s")
            if not self._retrying:
                self._retrying = bool(u32.SetTimer(self.hwnd, RETRY_TIMER, 3000, None))
        elif self._retrying:
            u32.KillTimer(self.hwnd, RETRY_TIMER)
            self._retrying = False

    def _readd_all(self):
        """Explorer restarted (or an add failed): put every icon back."""
        current = [(uid, hicon, tip) for uid, (hicon, tip) in self._shown.items()]
        for uid in list(self._added):
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid(uid)))
        self._added.clear()
        with self._lock:
            if self._pending is None:
                self._pending = current
        self._apply()

    def _remove_all(self):
        for uid in list(self._added):
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid(uid)))
        self._added.clear()
        for hicon, _ in self._shown.values():
            if hicon:
                u32.DestroyIcon(hicon)
        self._shown.clear()

    def _show_balloon(self):
        """Show the next waiting notice. A new notice from the same icon replaces the one on
        screen, so the next waits until Windows reports this one gone. Fallbacks, for when
        Windows reports nothing: 60 s until it is shown, then its display time."""
        if self._balloon_up:
            return
        with self._lock:
            if not self._added:
                self._balloons.clear()              # no icon to show them on
            if not self._balloons:
                return
            title, text, warning = self._balloons.pop(0)
        nid = self._nid(min(self._added), NIF_INFO)
        nid.szInfo = text
        nid.szInfoTitle = title
        nid.dwInfoFlags = (NIIF_WARNING if warning else NIIF_INFO)
        if shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)):
            self._balloon_up = True
            u32.SetTimer(self.hwnd, BALLOON_TIMER, BALLOON_WAIT_MS, None)

    def _balloon_done(self):
        self._balloon_up = False
        u32.KillTimer(self.hwnd, BALLOON_TIMER)
        self._show_balloon()

    # -- device notices -------------------------------------------------------------
    def _rewatch(self):
        with self._lock:
            handle = self._watch_handle
        self._unwatch()
        if not handle or not self.hwnd:
            return
        f = DEV_BROADCAST_HANDLE()
        f.dbch_size = ctypes.sizeof(f)
        f.dbch_devicetype = DBT_DEVTYP_HANDLE
        f.dbch_handle = handle
        self._notify_handle = u32.RegisterDeviceNotificationW(self.hwnd, ctypes.byref(f), DEVICE_NOTIFY_WINDOW_HANDLE)
        if not self._notify_handle:
            log.warning("tray: RegisterDeviceNotification failed gle=%d", ctypes.get_last_error())

    def _unwatch(self):
        h, self._notify_handle = self._notify_handle, None
        if h:
            u32.UnregisterDeviceNotification(h)

    def _device_change(self, event, lparam):
        """Windows is about to remove the UPS device (or reload its driver), or has removed it:
        close it now. An open handle would make Windows refuse the change."""
        if event not in (DBT_DEVICEQUERYREMOVE, DBT_DEVICEREMOVEPENDING, DBT_DEVICEREMOVECOMPLETE) or not lparam:
            return
        if DEV_BROADCAST_HDR.from_address(lparam).dbch_devicetype != DBT_DEVTYP_HANDLE:
            return
        info = DEV_BROADCAST_HANDLE.from_address(lparam)
        if not self._notify_handle or info.dbch_hdevnotify != self._notify_handle:
            return
        log.info("tray: Windows is removing the UPS device (0x%X); closing it", event)
        if self.on_release:
            try:
                self.on_release()
            except Exception:
                log.exception("closing the UPS failed")

    # -- menu -----------------------------------------------------------------------
    @staticmethod
    def build_menu(state):
        """The context menu for a menu state, or None; the caller destroys it (which also
        destroys the Battery test submenu)."""
        hmenu = u32.CreatePopupMenu()
        if not hmenu:
            return None
        status = state.get("status") or "UPS Tray"
        u32.AppendMenuW(hmenu, MF_STRING | MF_GRAYED, IDM_STATUS, status[:120])
        u32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        u32.AppendMenuW(hmenu, MF_STRING, IDM_DETAILS, "Details")
        u32.AppendMenuW(hmenu, MF_STRING, IDM_SETTINGS, "Settings...")
        items = battery_menu(state)
        sub = u32.CreatePopupMenu()
        if sub:
            for cid, text, enabled in items:
                u32.AppendMenuW(sub, MF_STRING | (0 if enabled else MF_GRAYED), cid, text)
            flags = MF_POPUP | (0 if any(enabled for _, _, enabled in items) else MF_GRAYED)
            if not u32.AppendMenuW(hmenu, flags, sub, "Battery test"):
                u32.DestroyMenu(sub)
        flags = MF_STRING | (MF_CHECKED if state.get("autostart") else 0)
        if not state.get("autostart_ok", True):
            flags |= MF_GRAYED
        u32.AppendMenuW(hmenu, flags, IDM_AUTOSTART, "Start with Windows")
        u32.AppendMenuW(hmenu, MF_STRING, IDM_LOGS, "Open log folder")
        u32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        u32.AppendMenuW(hmenu, MF_STRING, IDM_EXIT, "Exit")
        return hmenu

    def _show_menu(self, x=None, y=None):
        if self._in_menu or not self.hwnd:
            return
        self._in_menu = True
        hmenu = None
        cmd = 0
        try:
            state = {}
            try:
                state = self.menu_state() or {}
            except Exception:
                log.exception("menu state")
            hmenu = self.build_menu(state)
            if not hmenu:
                return
            if x is None or y is None:
                pt = wt.POINT()
                u32.GetCursorPos(ctypes.byref(pt))
                x, y = pt.x, pt.y
            prev = self._app_fg
            u32.SetForegroundWindow(self.hwnd)          # per the TrackPopupMenu remarks
            cmd = u32.TrackPopupMenu(hmenu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY, x, y, 0,
                                     self.hwnd, None)
            u32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
            if cmd in (0, IDM_AUTOSTART, IDM_LOGS, IDM_EXIT) and prev and u32.IsWindow(prev):
                u32.SetForegroundWindow(prev)
        finally:
            if hmenu:
                u32.DestroyMenu(hmenu)
            self._in_menu = False
        action = {IDM_DETAILS: "details", IDM_SETTINGS: "settings", IDM_AUTOSTART: "autostart",
                  IDM_LOGS: "logs", IDM_EXIT: "exit", IDM_SELFTEST: "selftest", IDM_CALIBRATE: "calibrate",
                  IDM_ABORT_TEST: "abort_test"}.get(cmd)
        if action:
            self.on_action(action)

    # -- window procedure -----------------------------------------------------------
    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAYCB:
                # version 4: LOWORD(lParam) = event, HIWORD(lParam) = icon id, wParam = anchor;
                # classic: lParam = mouse message, wParam = icon id
                evt = lparam & 0xFFFF
                if evt == NIN_BALLOONSHOW and self._balloon_up:
                    u32.SetTimer(self.hwnd, BALLOON_TIMER, notice_display_ms(), None)
                    return 0
                if evt in (NIN_BALLOONHIDE, NIN_BALLOONTIMEOUT, NIN_BALLOONUSERCLICK):
                    self._balloon_done()
                    return 0
                v4 = bool(self.v4.get((lparam >> 16) & 0xFFFF))
                act = tray_event_action(evt, v4)
                if act == "menu":
                    if v4:
                        self._show_menu(signed16(wparam & 0xFFFF), signed16((wparam >> 16) & 0xFFFF))
                    else:
                        self._show_menu()
                elif act == "details":
                    self.on_action("details")
                return 0
            if msg == MSG_APPLY:
                self._apply()
                return 0
            if msg == MSG_BALLOON:
                self._show_balloon()
                return 0
            if msg == MSG_OPEN_SETTINGS:
                self.on_action("settings")
                return 0
            if msg == MSG_OPEN_DETAILS:
                self.on_action("details")
                return 0
            if msg == MSG_REQUEST_QUIT:
                self.on_action("exit")
                return 0
            if msg == MSG_WATCH:
                self._rewatch()
                return 0
            if msg == WM_DEVICECHANGE:
                self._device_change(wparam, lparam)
                return 1
            if msg == MSG_QUIT:
                self._unwatch()
                self._remove_all()
                if self._fg_hook:
                    u32.UnhookWinEvent(self._fg_hook)
                    self._fg_hook = None
                h, self.hwnd = self.hwnd, None
                if h:
                    u32.DestroyWindow(h)
                return 0
            if msg == WM_TIMER and wparam == RETRY_TIMER:
                self._readd_all()
                return 0
            if msg == WM_TIMER and wparam == BALLOON_TIMER:
                self._balloon_done()
                return 0
            if msg == WM_SETTINGCHANGE:
                if lparam:
                    try:
                        if ctypes.wstring_at(lparam) == "ImmersiveColorSet":
                            self.on_action("theme")
                    except Exception:
                        pass
                return 0
            if msg == WM_POWERBROADCAST:
                if wparam == PBT_APMRESUMEAUTOMATIC:
                    self.on_action("resume")
                return 1
            if msg == WM_QUERYENDSESSION:
                return 1
            if msg == WM_ENDSESSION:
                if wparam:
                    self._remove_all()
                    self.on_action("end_session")
                return 0
            if msg == WM_CLOSE:
                # taskkill without /f: exit cleanly instead of orphaning the icons
                self.on_action("exit")
                return 0
            if msg == WM_DESTROY:
                u32.PostQuitMessage(0)
                return 0
            if self._taskbar_created and msg == self._taskbar_created:
                self._readd_all()
                return 0
        except Exception:
            log.exception("wndproc error msg=0x%X", msg)
        return u32.DefWindowProcW(hwnd, msg, wparam, lparam)
