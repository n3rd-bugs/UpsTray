"""Tk windows: Settings (which readings become tray icons, display, notifications)
and Details (every value the UPS reports, live). Both run on the app thread."""
import json
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from . import DISPLAY_NAME, VERSION, model

CHECK_ON, CHECK_OFF = "☑", "☐"
GROUP_ORDER = ["Power", "Battery", "Settings", "Device", "Other"]
GROUP_TITLES = {"Power": "Power", "Battery": "Battery", "Settings": "UPS settings", "Device": "Device",
                "Other": "Other values (advanced)"}
STATE_COLORS = {"online": "#107C10", "battery": "#C27C0E", "low": "#C42B1C", "alert": "#C42B1C",
                "offline": "#767676"}


def scale_of(root):
    try:
        return max(1.0, float(root.winfo_fpixels("1i")) / 96.0)
    except tk.TclError:
        return 1.0


def style_root(root):
    s = scale_of(root)
    style = ttk.Style(root)
    try:
        style.theme_use("vista")
    except tk.TclError:
        pass
    style.configure("Treeview", rowheight=int(22 * s))
    style.configure("Status.TLabel", font=("Segoe UI", 11, "bold"))
    style.configure("Muted.TLabel", foreground="#5F5F5F")
    return s


def _place(win, w, h, s):
    w, h = int(w * s), int(h * s)
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    w, h = min(w, sw - 40), min(h, sh - 80)
    win.geometry("%dx%d+%d+%d" % (w, h, max(0, (sw - w) // 2), max(0, (sh - h) // 3)))
    win.minsize(int(420 * s), int(320 * s))


def device_line(ups):
    if ups is None:
        return "No UPS found. Connect its USB cable; UPS Tray keeps looking."
    i = ups.info
    parts = [i.title()]
    if i.name and i.name.lower() not in i.title().lower():
        parts[0] += " (%s)" % i.name
    if i.serial:
        parts.append("S/N " + i.serial)
    if i.firmware:
        parts.append("firmware " + i.firmware)
    return " · ".join(parts)


class SettingsWindow:
    def __init__(self, app):
        self.app = app
        self.s = dict(app.settings)
        self.win = tk.Toplevel(app.root)
        self.win.title("%s settings" % DISPLAY_NAME)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.win.bind("<Escape>", lambda e: self.close())
        if app.window_icon:
            try:
                self.win.iconbitmap(app.window_icon)
            except tk.TclError:
                pass
        self.scale = scale_of(self.win)
        self.icon_keys = list(self.s["tray_readings"])
        self.tip_keys = list(self.s["tooltip_readings"])
        self.colors = dict(self.s["reading_colors"])
        self._build()
        _place(self.win, 640, 560, self.scale)
        self.reload()

    # -- layout ---------------------------------------------------------------------
    def _build(self):
        pad = int(8 * self.scale)
        outer = ttk.Frame(self.win, padding=pad)
        outer.pack(fill="both", expand=True)
        self.device = ttk.Label(outer, text="", style="Muted.TLabel")
        self.device.pack(anchor="w", pady=(0, pad))
        nb = ttk.Notebook(outer)
        nb.pack(fill="both", expand=True)

        # tray icons tab
        tab = ttk.Frame(nb, padding=pad)
        nb.add(tab, text="Tray icons")
        ttk.Label(tab, text="Tick Icon for each reading to show as its own notification-area icon, and "
                            "Tooltip for the readings every tooltip lists. Double-click a row to toggle "
                            "its icon.", wraplength=int(580 * self.scale), justify="left").pack(anchor="w")
        frame = ttk.Frame(tab)
        frame.pack(fill="both", expand=True, pady=(pad, 0))
        cols = ("icon", "tip", "now", "color")
        self.tree = ttk.Treeview(frame, columns=cols, selectmode="browse")
        self.tree.heading("#0", text="Reading")
        self.tree.heading("icon", text="Icon")
        self.tree.heading("tip", text="Tooltip")
        self.tree.heading("now", text="Now")
        self.tree.heading("color", text="Color")
        s = self.scale
        self.tree.column("#0", width=int(250 * s), stretch=True)
        self.tree.column("icon", width=int(50 * s), anchor="center", stretch=False)
        self.tree.column("tip", width=int(60 * s), anchor="center", stretch=False)
        self.tree.column("now", width=int(110 * s), anchor="e", stretch=False)
        self.tree.column("color", width=int(70 * s), anchor="center", stretch=False)
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<space>", lambda e: self._toggle_selected("icon"))
        btns = ttk.Frame(tab)
        btns.pack(fill="x", pady=(pad, 0))
        ttk.Button(btns, text="Move up", command=lambda: self._move(-1)).pack(side="left")
        ttk.Button(btns, text="Move down", command=lambda: self._move(1)).pack(side="left", padx=(pad, 0))
        ttk.Button(btns, text="Color...", command=self._pick_color).pack(side="left", padx=(pad * 2, 0))
        ttk.Button(btns, text="Default color", command=self._reset_color).pack(side="left", padx=(pad, 0))
        self.order_label = ttk.Label(btns, text="", style="Muted.TLabel")
        self.order_label.pack(side="right")

        # display tab
        tab = ttk.Frame(nb, padding=pad)
        nb.add(tab, text="Display")
        self.v_poll = tk.IntVar(value=self.s["poll_seconds"])
        self.v_temp = tk.StringVar(value=self.s["temperature_unit"])
        self.v_runtime = tk.StringVar(value=self.s["runtime_style"])
        self.v_auto_color = tk.BooleanVar(value=self.s["text_color"] == "auto")
        self.text_color = self.s["text_color"] if self.s["text_color"] != "auto" else "#FFFFFF"
        self.v_status_colors = tk.BooleanVar(value=self.s["status_colors"])
        self.v_badge = tk.BooleanVar(value=self.s["badge"])
        row = 0
        grid = ttk.Frame(tab)
        grid.pack(fill="x", anchor="n")
        ttk.Label(grid, text="Update every").grid(row=row, column=0, sticky="w", pady=pad // 2, padx=(0, pad))
        f = ttk.Frame(grid)
        f.grid(row=row, column=1, sticky="w")
        ttk.Spinbox(f, from_=1, to=60, width=4, textvariable=self.v_poll).pack(side="left")
        ttk.Label(f, text="seconds").pack(side="left", padx=(pad // 2, 0))
        row += 1
        ttk.Label(grid, text="Temperature").grid(row=row, column=0, sticky="w", pady=pad // 2, padx=(0, pad))
        f = ttk.Frame(grid)
        f.grid(row=row, column=1, sticky="w")
        ttk.Radiobutton(f, text="°C", value="C", variable=self.v_temp).pack(side="left")
        ttk.Radiobutton(f, text="°F", value="F", variable=self.v_temp).pack(side="left", padx=(pad, 0))
        row += 1
        ttk.Label(grid, text="Runtime in tooltips").grid(row=row, column=0, sticky="w", pady=pad // 2, padx=(0, pad))
        f = ttk.Frame(grid)
        f.grid(row=row, column=1, sticky="w")
        ttk.Radiobutton(f, text="minutes", value="minutes", variable=self.v_runtime).pack(side="left")
        ttk.Radiobutton(f, text="hours and minutes", value="hm", variable=self.v_runtime).pack(
            side="left", padx=(pad, 0))
        row += 1
        ttk.Label(grid, text="Text color").grid(row=row, column=0, sticky="w", pady=pad // 2, padx=(0, pad))
        f = ttk.Frame(grid)
        f.grid(row=row, column=1, sticky="w")
        ttk.Checkbutton(f, text="Follow the taskbar (white on dark, black on light)",
                        variable=self.v_auto_color, command=self._sync_color_widgets).pack(side="left")
        self.color_btn = ttk.Button(f, text="Choose...", command=self._pick_text_color)
        self.color_btn.pack(side="left", padx=(pad, 0))
        self.color_swatch = tk.Label(f, text="    ", relief="solid", borderwidth=1)
        self.color_swatch.pack(side="left", padx=(pad // 2, 0))
        row += 1
        ttk.Checkbutton(grid, text="Status colors: amber on battery, red when low, grey when the UPS "
                                   "does not answer", variable=self.v_status_colors).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=pad // 2)
        row += 1
        ttk.Checkbutton(grid, text="Draw values on a badge (filled with the status color)",
                        variable=self.v_badge).grid(row=row, column=0, columnspan=2, sticky="w",
                                                    pady=pad // 2)
        row += 1
        ttk.Label(tab, text="Windows 11 hides new notification-area icons. To keep the readings visible, "
                            "open Settings > Personalization > Taskbar > Other system tray icons and turn "
                            "on the UPS Tray entries, or drag the icons from the overflow onto the taskbar.",
                  wraplength=int(580 * self.scale), justify="left", style="Muted.TLabel").pack(
            anchor="w", pady=(pad * 2, 0))
        self._sync_color_widgets()

        # notifications tab
        tab = ttk.Frame(nb, padding=pad)
        nb.add(tab, text="Notifications")
        self.v_n_batt = tk.BooleanVar(value=self.s["notify_on_battery"])
        self.v_n_after = tk.IntVar(value=self.s["notify_after_seconds"])
        self.v_n_rest = tk.BooleanVar(value=self.s["notify_restored"])
        self.v_n_low = tk.BooleanVar(value=self.s["notify_low"])
        self.v_low_pct = tk.IntVar(value=self.s["low_charge_percent"])
        self.v_n_off = tk.BooleanVar(value=self.s["notify_offline"])
        f = ttk.Frame(tab)
        f.pack(anchor="w", pady=pad // 2)
        ttk.Checkbutton(f, text="When the UPS has been on battery for", variable=self.v_n_batt).pack(side="left")
        ttk.Spinbox(f, from_=0, to=3600, width=5, textvariable=self.v_n_after).pack(side="left",
                                                                                    padx=(pad // 2, 0))
        ttk.Label(f, text="seconds").pack(side="left", padx=(pad // 2, 0))
        ttk.Checkbutton(tab, text="When mains power returns (after such a notice)",
                        variable=self.v_n_rest).pack(anchor="w", pady=pad // 2)
        f = ttk.Frame(tab)
        f.pack(anchor="w", pady=pad // 2)
        ttk.Checkbutton(f, text="When the battery runs low; low means below", variable=self.v_n_low).pack(
            side="left")
        ttk.Spinbox(f, from_=0, to=95, width=4, textvariable=self.v_low_pct).pack(side="left",
                                                                                  padx=(pad // 2, 0))
        ttk.Label(f, text="% charge, or the UPS's own low-runtime warning").pack(side="left",
                                                                                padx=(pad // 2, 0))
        ttk.Checkbutton(tab, text="When the UPS stops answering (USB cable or UPS off)",
                        variable=self.v_n_off).pack(anchor="w", pady=pad // 2)
        ttk.Label(tab, text="UPS Tray only shows notices; it never shuts the computer down. Windows' "
                            "battery settings or PowerChute do that.", wraplength=int(580 * self.scale),
                  justify="left", style="Muted.TLabel").pack(anchor="w", pady=(pad * 2, 0))

        # UPS tab
        tab = ttk.Frame(nb, padding=pad)
        nb.add(tab, text="UPS")
        self.info_text = tk.Text(tab, height=13, relief="flat", wrap="word",
                                 background=self.win.cget("background"), font=("Segoe UI", 9))
        self.info_text.pack(fill="x")
        f = ttk.Frame(tab)
        f.pack(anchor="w", pady=(pad, 0))
        ttk.Label(f, text="Rated power").pack(side="left")
        self.v_watts = tk.IntVar(value=self.s["nominal_watts"])
        ttk.Spinbox(f, from_=0, to=100000, increment=10, width=7, textvariable=self.v_watts).pack(
            side="left", padx=(pad // 2, 0))
        ttk.Label(f, text="W (from the UPS's label; 0 = unknown). Turns Load % into a 'Load power' "
                          "reading when the UPS does not report watts.", wraplength=int(420 * self.scale),
                  justify="left").pack(side="left", padx=(pad // 2, 0))

        # bottom
        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(pad, 0))
        self.v_autostart = tk.BooleanVar(value=self.app.autostart_enabled())
        cb = ttk.Checkbutton(bottom, text="Start with Windows", variable=self.v_autostart)
        cb.pack(side="left")
        if not self.app.autostart_possible():
            cb.state(["disabled"])
        ttk.Button(bottom, text="Cancel", command=self.close).pack(side="right")
        ttk.Button(bottom, text="Save", command=self.save).pack(side="right", padx=(0, pad))

    # -- content --------------------------------------------------------------------
    def reload(self):
        ups = self.app.ups
        self.device.configure(text=device_line(ups))
        self.tree.delete(*self.tree.get_children())
        readings = ups.tray_readings() if ups else []
        known = {r.key for r in readings}
        groups = {}
        for g in GROUP_ORDER:
            members = [r for r in readings if r.group == g]
            if not members:
                continue
            groups[g] = self.tree.insert("", "end", iid="g:" + g, text=GROUP_TITLES[g],
                                         open=g != "Other", values=("", "", "", ""))
            for r in members:
                self.tree.insert(groups[g], "end", iid=r.key, text=r.name, values=self._row(r))
        # selections for readings this UPS does not have stay in the settings untouched
        missing = [k for k in self.icon_keys if k not in known]
        if missing and ups:
            node = self.tree.insert("", "end", iid="g:missing", text="Not reported by this UPS", open=True)
            for k in missing:
                self.tree.insert(node, "end", iid="m:" + k, text=k, values=(CHECK_ON, "", "", ""))
        self._update_order_label()
        self._fill_info()

    def _row(self, r):
        snap = self.app.last_snapshot
        value = snap.values.get(r.key) if snap else None
        now = model.format_value(r, value, self.v_temp.get() if hasattr(self, "v_temp") else "C",
                                 self.v_runtime.get() if hasattr(self, "v_runtime") else "minutes") \
            if snap and r.key in snap.values else ""
        color = self.colors.get(r.key, "")
        return (CHECK_ON if r.key in self.icon_keys else CHECK_OFF,
                CHECK_ON if r.key in self.tip_keys else CHECK_OFF, now, color or "default")

    def refresh_values(self):
        ups = self.app.ups
        if not ups or not self.win.winfo_exists():
            return
        for r in ups.tray_readings():
            if self.tree.exists(r.key):
                self.tree.item(r.key, values=self._row(r))

    def _fill_info(self):
        ups = self.app.ups
        t = self.info_text
        t.configure(state="normal")
        t.delete("1.0", "end")
        if ups is None:
            t.insert("end", "No UPS found yet.\n\nUPS Tray reads UPSes that connect by USB as a HID power "
                            "device (APC Smart-UPS and Back-UPS, and most others). It keeps looking while it "
                            "runs.")
        else:
            i = ups.info
            lines = [("Model", i.model or i.product_string), ("Name", i.name), ("Serial", i.serial),
                     ("Manufacturer", i.manufacturer), ("Firmware", i.firmware), ("USB firmware", i.usb_firmware),
                     ("Battery chemistry", i.chemistry),
                     ("USB id", "%04X:%04X" % (i.vendor_id, i.product_id))]
            snap = self.app.last_snapshot
            for key in ("battery_date", "output_nominal_voltage", "battery_nominal_voltage"):
                r = ups.by_key.get(key)
                if r and snap and key in snap.values:
                    lines.append((r.name, model.format_value(r, snap.values[key])))
            for k, v in lines:
                if v:
                    t.insert("end", "%s: %s\n" % (k, v))
        t.configure(state="disabled")

    def _update_order_label(self):
        names = []
        ups = self.app.ups
        for k in self.icon_keys:
            r = ups.by_key.get(k) if ups else None
            names.append(r.name if r else k)
        self.order_label.configure(text=("Icons: " + ", ".join(names)) if names else "No icon chosen: "
                                   "a single status icon is shown")

    # -- interaction ----------------------------------------------------------------
    def _on_click(self, event):
        if self.tree.identify("region", event.x, event.y) != "cell":
            return
        item = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not item or item.startswith("g:"):
            return
        if col == "#1":
            self._toggle(item, "icon")
            return "break"
        if col == "#2":
            self._toggle(item, "tip")
            return "break"
        if col == "#4":
            self.tree.selection_set(item)
            self._pick_color()
            return "break"

    def _on_double(self, event):
        item = self.tree.identify_row(event.y)
        if item and not item.startswith("g:"):
            self._toggle(item, "icon")
        return "break"

    def _toggle_selected(self, what):
        sel = self.tree.selection()
        if sel and not sel[0].startswith("g:"):
            self._toggle(sel[0], what)
        return "break"

    def _toggle(self, item, what):
        key = item[2:] if item.startswith("m:") else item
        keys = self.icon_keys if what == "icon" else self.tip_keys
        if key in keys:
            keys.remove(key)
        else:
            if what == "icon" and len(keys) >= 8:
                return
            keys.append(key)
        if item.startswith("m:"):
            if key not in self.icon_keys:
                self.tree.delete(item)
        else:
            r = self.app.ups.by_key.get(key) if self.app.ups else None
            if r:
                self.tree.item(item, values=self._row(r))
        self._update_order_label()

    def _move(self, delta):
        sel = self.tree.selection()
        if not sel:
            return
        key = sel[0][2:] if sel[0].startswith("m:") else sel[0]
        if key not in self.icon_keys:
            return
        i = self.icon_keys.index(key)
        j = max(0, min(len(self.icon_keys) - 1, i + delta))
        self.icon_keys.insert(j, self.icon_keys.pop(i))
        self._update_order_label()

    def _pick_color(self):
        sel = self.tree.selection()
        if not sel or sel[0].startswith(("g:", "m:")):
            return
        key = sel[0]
        rgb, hexcolor = colorchooser.askcolor(color=self.colors.get(key) or "#FFFFFF", parent=self.win,
                                              title="Color of this reading's icon")
        if hexcolor:
            self.colors[key] = hexcolor.upper()
            r = self.app.ups.by_key.get(key)
            if r:
                self.tree.item(key, values=self._row(r))

    def _reset_color(self):
        sel = self.tree.selection()
        if sel and sel[0] in self.colors:
            self.colors.pop(sel[0])
            r = self.app.ups.by_key.get(sel[0]) if self.app.ups else None
            if r:
                self.tree.item(sel[0], values=self._row(r))

    def _pick_text_color(self):
        rgb, hexcolor = colorchooser.askcolor(color=self.text_color, parent=self.win, title="Icon text color")
        if hexcolor:
            self.text_color = hexcolor.upper()
            self.v_auto_color.set(False)
            self._sync_color_widgets()

    def _sync_color_widgets(self):
        auto = self.v_auto_color.get()
        self.color_swatch.configure(background=self.app.effective_text_color() if auto else self.text_color)

    def _int(self, var, default):
        try:
            return int(var.get())
        except (tk.TclError, ValueError):
            return default

    def save(self):
        s = dict(self.app.settings)
        s.update({
            "tray_readings": list(self.icon_keys),
            "tooltip_readings": list(self.tip_keys),
            "reading_colors": dict(self.colors),
            "poll_seconds": self._int(self.v_poll, s["poll_seconds"]),
            "temperature_unit": self.v_temp.get(),
            "runtime_style": self.v_runtime.get(),
            "text_color": "auto" if self.v_auto_color.get() else self.text_color,
            "status_colors": bool(self.v_status_colors.get()),
            "badge": bool(self.v_badge.get()),
            "notify_on_battery": bool(self.v_n_batt.get()),
            "notify_after_seconds": self._int(self.v_n_after, s["notify_after_seconds"]),
            "notify_restored": bool(self.v_n_rest.get()),
            "notify_low": bool(self.v_n_low.get()),
            "low_charge_percent": self._int(self.v_low_pct, s["low_charge_percent"]),
            "notify_offline": bool(self.v_n_off.get()),
            "nominal_watts": self._int(self.v_watts, s["nominal_watts"]),
            "first_run_done": True,
        })
        self.app.apply_settings(s, autostart=bool(self.v_autostart.get()))
        self.close()

    def close(self):
        if not self.app.settings.get("first_run_done"):
            s = dict(self.app.settings)
            s["first_run_done"] = True
            self.app.apply_settings(s, autostart=None)
        self.app.settings_window = None
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def show(self):
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()


class DetailsWindow:
    def __init__(self, app):
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.title("%s - details" % DISPLAY_NAME)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.win.bind("<Escape>", lambda e: self.close())
        if app.window_icon:
            try:
                self.win.iconbitmap(app.window_icon)
            except tk.TclError:
                pass
        self.scale = scale_of(self.win)
        pad = int(8 * self.scale)
        outer = ttk.Frame(self.win, padding=pad)
        outer.pack(fill="both", expand=True)
        head = ttk.Frame(outer)
        head.pack(fill="x")
        self.dot = tk.Canvas(head, width=int(14 * self.scale), height=int(14 * self.scale),
                             highlightthickness=0, background=self.win.cget("background"))
        self.dot.pack(side="left", padx=(0, pad // 2))
        self.status = ttk.Label(head, text="", style="Status.TLabel")
        self.status.pack(side="left")
        self.device = ttk.Label(outer, text="", style="Muted.TLabel")
        self.device.pack(anchor="w", pady=(pad // 2, pad))
        frame = ttk.Frame(outer)
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=("value", "where"), selectmode="extended")
        self.tree.heading("#0", text="Reading")
        self.tree.heading("value", text="Value")
        self.tree.heading("where", text="Reported in")
        self.tree.column("#0", width=int(260 * self.scale))
        self.tree.column("value", width=int(170 * self.scale), anchor="e")
        self.tree.column("where", width=int(150 * self.scale))
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(pad, 0))
        ttk.Button(bottom, text="Copy all", command=self.copy).pack(side="left")
        ttk.Button(bottom, text="Save device report...", command=self.save_report).pack(side="left",
                                                                                      padx=(pad, 0))
        ttk.Label(bottom, text="%s %s" % (DISPLAY_NAME, VERSION), style="Muted.TLabel").pack(side="left",
                                                                                          padx=(pad * 2, 0))
        ttk.Button(bottom, text="Close", command=self.close).pack(side="right")
        ttk.Button(bottom, text="Settings...", command=self.app.open_settings).pack(side="right",
                                                                                 padx=(0, pad))
        _place(self.win, 640, 600, self.scale)
        self._built_for = None
        self.refresh()

    def _rebuild(self):
        self.tree.delete(*self.tree.get_children())
        ups = self.app.ups
        self._built_for = id(ups)
        if ups is None:
            return
        snap = self.app.last_snapshot
        known = snap.status.known if snap else frozenset()
        self._status_known = known
        for g in GROUP_ORDER:
            if g == "Settings" and known:
                # the power state bits the UPS reports, after the live readings
                self.tree.insert("", "end", iid="g:status", text="Status", open=True)
                for name, label in model.STATUS_NAMES:
                    if name in known:
                        self.tree.insert("g:status", "end", iid="s:" + name, text=label, values=("", ""))
            members = [r for r in ups.readings if r.group == g and not r.key.startswith("_")]
            if not members:
                continue
            self.tree.insert("", "end", iid="g:" + g, text=GROUP_TITLES[g], open=g != "Other")
            for r in members:
                where = "/".join(r.field.path[1:]) if r.field else "computed"
                self.tree.insert("g:" + g, "end", iid=r.key, text=r.name, values=("", where))

    def refresh(self):
        if not self.win.winfo_exists():
            return
        ups = self.app.ups
        snap = self.app.last_snapshot
        if self._built_for != id(ups) or (snap and snap.status.ok
                                           and snap.status.known != getattr(self, "_status_known", None)):
            self._rebuild()
        st = snap.status if snap else model.Status()
        self.status.configure(text=st.summary() if ups else "No UPS found")
        self.device.configure(text=device_line(ups))
        self.dot.delete("all")
        r = int(12 * self.scale)
        self.dot.create_oval(1, 1, r, r, fill=STATE_COLORS.get(st.state if ups else "offline"), outline="")
        if ups is None or snap is None:
            return
        s = self.app.settings
        for name, _ in model.STATUS_NAMES:
            iid = "s:" + name
            if self.tree.exists(iid):
                self.tree.item(iid, values=(("yes" if name in st.flags else "no") if st.ok else "-", ""))
        for rd in ups.readings:
            if self.tree.exists(rd.key) and rd.key in snap.values:
                vals = self.tree.item(rd.key, "values")
                where = vals[1] if vals and len(vals) > 1 else ""
                self.tree.item(rd.key, values=(model.format_value(rd, snap.values[rd.key], s["temperature_unit"],
                                                                  s["runtime_style"]), where))

    def text_report(self):
        lines = [self.status.cget("text"), self.device.cget("text"), ""]

        def walk(parent, depth):
            for iid in self.tree.get_children(parent):
                vals = self.tree.item(iid, "values")
                text = self.tree.item(iid, "text")
                if vals and vals[0] != "":
                    lines.append("%s%s: %s" % ("  " * depth, text, vals[0]))
                else:
                    lines.append("%s%s" % ("  " * depth, text))
                walk(iid, depth + 1)
        walk("", 0)
        return "\n".join(lines)

    def copy(self):
        self.win.clipboard_clear()
        self.win.clipboard_append(self.text_report())

    def save_report(self):
        ups = self.app.ups
        if ups is None:
            return
        path = filedialog.asksaveasfilename(parent=self.win, title="Save device report",
                                            defaultextension=".json", initialfile="ups-report.json",
                                            filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            data = ups.export()
        except RuntimeError as e:
            messagebox.showinfo(DISPLAY_NAME, "No report saved: %s." % e, parent=self.win)
            return
        data["app"] = "%s %s" % (DISPLAY_NAME, VERSION)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)

    def close(self):
        self.app.details_window = None
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def show(self):
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()
