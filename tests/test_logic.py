"""Unit tests that need no UPS: the descriptor model, readings, status, formatting,
settings, battery tests, tray helpers and the icon renderer, mostly against a
recorded APC Smart-UPS 1500 (tests/fixtures/smart_ups_1500.json).

    python tests\\test_logic.py
"""
import ast
import copy
import json
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "src")
sys.path.insert(0, SRC)

from upstray import VERSION, device, model, settings  # noqa: E402

import logging  # noqa: E402

logging.getLogger("upstray").addHandler(logging.NullHandler())

FIXTURE = os.path.join(HERE, "fixtures", "smart_ups_1500.json")

# PresentStatus collection of the recorded UPS: report 7, link collection 2
STATUS_RID, STATUS_LINK = 7, 2
AC_PRESENT, CHARGING, DISCHARGING, BATTERY_PRESENT = 0xD0, 0x44, 0x45, 0xD1
BELOW_CAPACITY_LIMIT, RUNTIME_LIMIT_EXPIRED = 0x42, 0x43
# Test usage of the recorded UPS: report 22, link 3 (Battery), alone in its report
TEST_KEY = (22, 0x84, 0x58, 3)


def load_fixture():
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


class CountingBackend(device.RecordedBackend):
    def __init__(self, data):
        super().__init__(data)
        self.reads = []

    def read_reports(self, report_ids):
        self.reads.append(sorted(report_ids))
        return super().read_reports(report_ids)


def make_ups(nominal_watts=0, mutate=None):
    data = load_fixture()
    backend = CountingBackend(data)
    if mutate:
        mutate(backend)
    return device.Ups(backend, nominal_watts), backend


def full_and_loaded(backend, charge_tenths=1000, load_tenths=310):
    backend.set_raw(24, 0x85, 0x66, 3, charge_tenths)        # fine charge (0.1 %)
    backend.set_raw(44, 0x84, 0x35, 5, load_tenths)          # load (0.1 %)


def no_sleep(_seconds):
    pass


def on_battery(backend, charge_tenths=None):
    backend.set_buttons(STATUS_RID, 0x85, STATUS_LINK, [DISCHARGING, BATTERY_PRESENT])
    backend.set_raw(32, 0x84, 0x30, 4, 0)                     # input voltage 0
    if charge_tenths is not None:
        backend.set_raw(24, 0x85, 0x66, 3, charge_tenths)     # fine charge (0.1 %)
        backend.set_raw(12, 0x85, 0x66, 1, charge_tenths // 10)


class Units(unittest.TestCase):
    VOLT = 0x00F0D121

    def test_volts_follow_si_cgs_scaling(self):
        # APC: battery 2727 exp 5 -> 27.27 V; input 2318 exp 6 -> 231.8 V; nominal 220 exp 7 -> 220 V
        self.assertAlmostEqual(model.decode_units(self.VOLT, 5)[0] * 2727, 27.27)
        self.assertAlmostEqual(model.decode_units(self.VOLT, 6)[0] * 2318, 231.8)
        self.assertAlmostEqual(model.decode_units(self.VOLT, 7)[0] * 220, 220.0)
        self.assertEqual(model.decode_units(self.VOLT, 6)[1], "V")

    def test_other_units(self):
        self.assertEqual(model.decode_units(0x00010001, 0xF), (0.1, "K"))
        scale, sym = model.decode_units(0x0000F001, 0xE)
        self.assertAlmostEqual(scale, 0.01)
        self.assertEqual(sym, "Hz")
        self.assertEqual(model.decode_units(0x00001001, 0), (1.0, "s"))
        self.assertEqual(model.decode_units(0, 0xF), (0.1, ""))
        self.assertEqual(model.decode_units(0x00100001, 0)[1], "A")
        self.assertEqual(model.decode_units(0x0000D121, 7), (1.0, "W"))

    def test_hid_dates(self):
        self.assertEqual(model.decode_hid_date(21898), "2022-12-10")
        self.assertEqual(model.decode_hid_date(23790), "2026-07-14")
        self.assertEqual(model.decode_hid_date(0), "")
        self.assertEqual(model.decode_hid_date(0xFFFF), "")

    def test_signed_fields(self):
        f = model.Field("value", 0x84, 0x57, 1, 18, bits=16, logical_min=-1, logical_max=32767)
        self.assertEqual(f.signed(0xFFFF), -1)
        self.assertEqual(f.signed(30), 30)
        g = model.Field("value", 0x85, 0x66, 1, 12, bits=8, logical_min=0, logical_max=100)
        self.assertEqual(g.signed(200), 200)

    def test_collection_paths_disambiguate(self):
        nodes = [model.Node(0x84, 0x04, 0, 1), model.Node(0x84, 0x12, 0, 0), model.Node(0x84, 0x12, 0, 0),
                 model.Node(0x84, 0x02, 1, 2)]
        self.assertEqual(model.collection_paths(nodes),
                         [("UPS",), ("UPS", "Battery"), ("UPS", "Battery#2"), ("UPS", "Battery", "PresentStatus")])


class Readings(unittest.TestCase):
    def setUp(self):
        self.ups, self.backend = make_ups()
        self.snap = self.ups.snapshot()

    def test_info(self):
        i = self.ups.info
        self.assertEqual(i.model, "Smart-UPS 1500")
        self.assertEqual(i.firmware, "653.13.I")
        self.assertEqual(i.chemistry, "PbAc")
        self.assertEqual(i.title(), "Smart-UPS 1500")

    def test_well_known_readings(self):
        keys = {r.key for r in self.ups.readings}
        for key in ("input_voltage", "output_voltage", "battery_voltage", "load", "output_frequency",
                    "charge", "runtime", "temperature", "battery_date", "self_test", "high_transfer"):
            self.assertIn(key, keys)
        v = self.snap.values
        self.assertTrue(200 < v["input_voltage"] < 260, v["input_voltage"])
        self.assertTrue(200 < v["output_voltage"] < 260)
        self.assertTrue(20 < v["battery_voltage"] < 30)
        self.assertTrue(0 <= v["load"] <= 100)
        self.assertAlmostEqual(v["output_frequency"], 50.0, delta=1.0)
        self.assertTrue(20 < v["temperature"] < 50, v["temperature"])        # Celsius, not Kelvin
        self.assertTrue(10 < v["runtime"] < 200, v["runtime"])               # minutes
        self.assertEqual(model.format_value(self.ups.by_key["battery_date"], v["battery_date"]), "2026-07-14")
        self.assertEqual(model.format_value(self.ups.by_key["self_test"], v["self_test"]), "No test initiated")

    def test_charge_uses_the_finest_field(self):
        r = self.ups.by_key["charge"]
        self.assertEqual(r.field.report_id, 24)          # 0.1 % steps, not the 1 % field
        self.assertEqual(r.decimals(), 1)

    def test_no_derived_power_without_rating(self):
        self.assertNotIn("real_power", self.ups.by_key)

    def test_derived_power_with_rating(self):
        ups, _ = make_ups(nominal_watts=980)
        snap = ups.snapshot(["real_power"])
        load = snap.values["load"]
        self.assertAlmostEqual(snap.values["real_power"], load * 9.8, places=3)

    def test_other_values_skip_strings_and_known_fields(self):
        others = [r for r in self.ups.readings if r.group == "Other"]
        self.assertTrue(others)
        for r in others:
            self.assertTrue(r.key.startswith("x:"))
            self.assertNotIn((r.field.page, r.field.usage), model.STRING_USAGES)
            self.assertNotIn((r.field.page, r.field.usage), model.APC_STRING_USAGES)
        keys = [r.key for r in self.ups.readings]
        self.assertEqual(len(keys), len(set(keys)))

    def test_snapshot_reads_only_what_it_needs(self):
        self.backend.reads.clear()
        self.ups.snapshot(["input_voltage", "load"])
        self.assertEqual(self.backend.reads, [[7, 32, 44]])   # status, input, load reports only

    def test_settings_readings_are_not_tray_icons(self):
        tray = {r.key for r in self.ups.tray_readings()}
        self.assertIn("input_voltage", tray)
        self.assertNotIn("battery_date", tray)
        self.assertNotIn("high_transfer", tray)


class StatusTests(unittest.TestCase):
    def test_on_mains_charging(self):
        ups, _ = make_ups()
        st = ups.snapshot().status
        self.assertTrue(st.ok)
        self.assertFalse(st.on_battery)
        self.assertEqual(st.state, "online")
        self.assertEqual(st.summary(), "On mains, charging")
        self.assertIn("ACPresent", st.known)

    def test_on_battery(self):
        ups, _ = make_ups(mutate=lambda b: on_battery(b, 800))
        st = ups.snapshot(None, low_charge=20).status
        self.assertTrue(st.on_battery)
        self.assertEqual(st.state, "battery")
        self.assertEqual(st.summary(), "On battery")

    def test_low_by_charge(self):
        ups, _ = make_ups(mutate=lambda b: on_battery(b, 150))
        st = ups.snapshot(["load"], low_charge=20).status   # charge read even when not requested
        self.assertEqual(st.state, "low")
        self.assertEqual(st.summary(), "On battery - LOW")

    def test_low_by_runtime_limit(self):
        def m(b):
            on_battery(b, 800)
            b.set_buttons(STATUS_RID, 0x85, STATUS_LINK, [DISCHARGING, BATTERY_PRESENT, RUNTIME_LIMIT_EXPIRED])
        ups, _ = make_ups(mutate=m)
        self.assertEqual(ups.snapshot(None, low_charge=20).status.state, "low")

    def test_capacity_limit_bit_alone_is_not_low(self):
        # Windows' HID battery driver moves the UPS's capacity limit to just below the charge
        def m(b):
            on_battery(b, 800)
            b.set_buttons(STATUS_RID, 0x85, STATUS_LINK, [DISCHARGING, BATTERY_PRESENT, BELOW_CAPACITY_LIMIT])
        ups, _ = make_ups(mutate=m)
        self.assertEqual(ups.snapshot(None, low_charge=20).status.state, "battery")

    def test_offline(self):
        ups, backend = make_ups()
        backend.offline = True
        snap = ups.snapshot()
        self.assertFalse(snap.status.ok)
        self.assertEqual(snap.status.state, "offline")
        self.assertEqual(snap.status.summary(), "UPS not responding")
        self.assertIsNone(snap.values["input_voltage"])
        self.assertTrue(snap.failed_reports)


class Formatting(unittest.TestCase):
    def setUp(self):
        self.ups, _ = make_ups()
        self.r = self.ups.by_key

    def test_tray_texts(self):
        t = model.tray_texts
        self.assertEqual(t(self.r["input_voltage"], 231.8), ["232"])
        self.assertEqual(t(self.r["battery_voltage"], 27.27), ["27.3", "27"])
        self.assertEqual(t(self.r["load"], 74.7), ["75%", "75"])
        self.assertEqual(t(self.r["charge"], 100.0), ["100%", "100"])
        self.assertEqual(t(self.r["runtime"], 57.4), ["57"])
        self.assertEqual(t(self.r["runtime"], 1500), ["25h"])
        self.assertEqual(t(self.r["temperature"], 34.6), ["35°", "35"])
        self.assertEqual(t(self.r["temperature"], 34.6, "F")[0], "94°")
        self.assertEqual(t(self.r["output_frequency"], 50.0), ["50.0", "50"])
        self.assertEqual(t(self.r["load"], None), ["--"])
        for text in sum((t(self.r[k], v) for k, v in (("input_voltage", 1234.5), ("load", 100))), []):
            self.assertLessEqual(len(text), 4)

    def test_other_value_tray_text_respects_resolution(self):
        other = next(r for r in self.ups.readings if r.group == "Other" and r.resolution >= 1)
        self.assertEqual(model.tray_texts(other, 2), ["2"])

    def test_format_value(self):
        f = model.format_value
        self.assertEqual(f(self.r["input_voltage"], 231.8), "231.8 V")
        self.assertEqual(f(self.r["load"], 74.7), "74.7 %")
        self.assertEqual(f(self.r["runtime"], 57.0), "57 min")
        self.assertEqual(f(self.r["runtime"], 125.0), "2 h 05 min")
        self.assertEqual(f(self.r["runtime"], 75.0, runtime_style="hm"), "1 h 15 min")
        self.assertEqual(f(self.r["temperature"], 34.56), "34.6 °C")
        self.assertEqual(f(self.r["temperature"], 34.56, "F"), "94.2 °F")
        self.assertEqual(f(self.r["shutdown_delay"], -1), "not counting down")
        self.assertEqual(f(self.r["input_voltage"], None), "-")


class SettingsTests(unittest.TestCase):
    def test_defaults_and_clamping(self):
        s = settings.normalize({"poll_seconds": 0, "tray_readings": ["load", "load", 7, ""],
                                "text_color": "red", "reading_colors": {"load": "#12ab34", "x": "blue"},
                                "temperature_unit": "K", "low_charge_percent": 120, "unknown": 1})
        self.assertEqual(s["poll_seconds"], 1)
        self.assertEqual(s["tray_readings"], ["load"])
        self.assertEqual(s["text_color"], "auto")
        self.assertEqual(s["reading_colors"], {"load": "#12ab34"})
        self.assertEqual(s["temperature_unit"], "C")
        self.assertEqual(s["low_charge_percent"], 95)
        self.assertNotIn("unknown", s)
        self.assertEqual(settings.normalize("garbage"), settings.normalize({}))

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sub", "settings.json")
            s, existed = settings.load(p)
            self.assertFalse(existed)
            s["tray_readings"] = ["charge", "runtime"]
            s["badge"] = True
            settings.save(s, p)
            back, existed = settings.load(p)
            self.assertTrue(existed)
            self.assertEqual(back["tray_readings"], ["charge", "runtime"])
            self.assertTrue(back["badge"])
            with open(p, "w") as fh:
                fh.write("{broken")
            broken, existed = settings.load(p)
            self.assertEqual(broken, settings.normalize({}))


class TrayHelpers(unittest.TestCase):
    def setUp(self):
        from upstray import tray
        self.tray = tray

    def test_event_mapping(self):
        t = self.tray
        a = t.tray_event_action
        self.assertEqual(a(t.WM_CONTEXTMENU, True), "menu")
        self.assertIsNone(a(t.WM_RBUTTONUP, True))
        self.assertEqual(a(t.WM_RBUTTONUP, False), "menu")
        self.assertEqual(a(t.NIN_SELECT, True), "details")
        self.assertEqual(a(t.NIN_KEYSELECT, True), "details")
        self.assertEqual(a(t.WM_LBUTTONUP, False), "details")
        for balloon in (0x402, 0x403, 0x404, 0x405, 0x406, 0x407):
            self.assertIsNone(a(balloon, True))

    def test_clip_tip(self):
        self.assertEqual(self.tray.clip_tip("short"), "short")
        long_text = "\n".join("line %02d with some text" % i for i in range(20))
        self.assertLessEqual(len(self.tray.clip_tip(long_text)), 127)

    def test_message_ids_distinct(self):
        t = self.tray
        ids = [t.WM_TRAYCB, t.MSG_APPLY, t.MSG_BALLOON, t.MSG_QUIT, t.MSG_OPEN_SETTINGS, t.MSG_REQUEST_QUIT,
               t.MSG_OPEN_DETAILS, t.MSG_WATCH]
        self.assertEqual(len(ids), len(set(ids)))
        cmds = [t.IDM_DETAILS, t.IDM_SETTINGS, t.IDM_AUTOSTART, t.IDM_LOGS, t.IDM_EXIT, t.IDM_STATUS,
                t.IDM_SELFTEST, t.IDM_CALIBRATE, t.IDM_ABORT_TEST]
        self.assertEqual(len(cmds), len(set(cmds)))

    def test_battery_menu(self):
        t = self.tray
        items = t.battery_menu({"test_start": True, "test_abort": False})
        self.assertEqual([(i, e) for i, _, e in items],
                         [(t.IDM_SELFTEST, True), (t.IDM_CALIBRATE, True), (t.IDM_ABORT_TEST, False)])
        items = t.battery_menu({"test_start": False, "test_abort": True})
        self.assertEqual([e for _, _, e in items], [False, False, True])
        self.assertFalse(any(e for _, _, e in t.battery_menu({})))

    def test_menu_has_the_battery_test_submenu(self):
        import ctypes
        import ctypes.wintypes as wt
        t = self.tray
        u32 = ctypes.WinDLL("user32")
        u32.GetMenuItemCount.argtypes = [wt.HMENU]
        u32.GetSubMenu.argtypes = [wt.HMENU, ctypes.c_int]
        u32.GetSubMenu.restype = wt.HMENU
        u32.GetMenuStringW.argtypes = [wt.HMENU, wt.UINT, wt.LPWSTR, ctypes.c_int, wt.UINT]
        u32.GetMenuState.argtypes = [wt.HMENU, wt.UINT, wt.UINT]
        MF_BYPOSITION = 0x400

        def text(menu, pos):
            buf = ctypes.create_unicode_buffer(128)
            u32.GetMenuStringW(menu, pos, buf, 128, MF_BYPOSITION)
            return buf.value

        for state, sub_grayed, items_grayed in (
                ({"status": "x", "test_start": True}, False, [False, False, True]),
                ({"status": "x", "test_abort": True}, False, [True, True, False]),
                ({"status": "x"}, True, [True, True, True])):
            menu = t.TrayHost.build_menu(state)
            try:
                labels = [text(menu, i) for i in range(u32.GetMenuItemCount(menu))]
                pos = labels.index("Battery test")
                self.assertEqual(labels[pos - 1], "Settings...")
                self.assertEqual(bool(u32.GetMenuState(menu, pos, MF_BYPOSITION) & t.MF_GRAYED), sub_grayed)
                sub = u32.GetSubMenu(menu, pos)
                self.assertTrue(sub)
                self.assertEqual([text(sub, i) for i in range(3)],
                                 ["Run self-test...", "Runtime calibration...", "Abort test..."])
                self.assertEqual([bool(u32.GetMenuState(sub, i, MF_BYPOSITION) & t.MF_GRAYED) for i in range(3)],
                                 items_grayed)
            finally:
                u32.DestroyMenu(menu)

    def test_notices_wait_for_each_other(self):
        t = self.tray
        host = t.TrayHost(lambda a: None, lambda: {})
        for n in range(6):
            host.balloon("title %d" % n, "text", warning=False)
        self.assertEqual([b[0] for b in host._balloons], ["title 2", "title 3", "title 4", "title 5"])
        shown, timers = [], []
        saved = t.shell32.Shell_NotifyIconW, t.u32.SetTimer, t.u32.KillTimer
        t.shell32.Shell_NotifyIconW = lambda msg, nid: shown.append(nid._obj.szInfoTitle) or 1
        t.u32.SetTimer = lambda hwnd, tid, ms, proc: timers.append(ms) or 1
        t.u32.KillTimer = lambda *a: 1
        try:
            host._added = {123}
            host._show_balloon()
            host._show_balloon()                       # one is waiting/on screen: the next waits
            self.assertEqual(shown, ["title 2"])
            self.assertEqual(timers, [t.BALLOON_WAIT_MS])          # Windows may queue it first
            host._wnd_proc(None, t.WM_TRAYCB, 0, (123 << 16) | t.NIN_BALLOONSHOW)
            self.assertEqual(timers[-1], t.notice_display_ms())    # now on screen: its display time
            self.assertGreaterEqual(timers[-1], 10000)
            self.assertEqual(shown, ["title 2"])
            host._wnd_proc(None, t.WM_TRAYCB, 0, (123 << 16) | t.NIN_BALLOONTIMEOUT)
            self.assertEqual(shown, ["title 2", "title 3"])
            host._wnd_proc(None, t.WM_TIMER, t.BALLOON_TIMER, 0)   # Windows never said: fallback
            host._wnd_proc(None, t.WM_TRAYCB, 0, (123 << 16) | t.NIN_BALLOONUSERCLICK)
            host._wnd_proc(None, t.WM_TRAYCB, 0, (123 << 16) | t.NIN_BALLOONHIDE)
            self.assertEqual(shown, ["title 2", "title 3", "title 4", "title 5"])
            self.assertFalse(host._balloon_up)
        finally:
            t.shell32.Shell_NotifyIconW, t.u32.SetTimer, t.u32.KillTimer = saved

    def test_device_notice_layout(self):
        import ctypes
        t = self.tray
        self.assertEqual(ctypes.sizeof(t.DEV_BROADCAST_HANDLE), 56 if ctypes.sizeof(ctypes.c_void_p) == 8 else 40)
        self.assertEqual(t.DEV_BROADCAST_HANDLE.dbch_hdevnotify.offset,
                         24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16)


class AppHelpers(unittest.TestCase):
    def test_tooltip_fits(self):
        from upstray import app
        ups, _ = make_ups()
        r = ups.by_key["input_voltage"]
        extras = ["Line %d 123.4 V" % i for i in range(12)]
        tip = app.build_tooltip("Smart-UPS 1500", r, "231.8 V", "On mains, charging", extras)
        self.assertLessEqual(len(tip), 127)
        self.assertTrue(tip.startswith("Input voltage 231.8 V\nOn mains, charging"))

    def test_uids_stable_and_unique(self):
        from upstray import app
        a = app.uid_for("load", set())
        self.assertEqual(a, app.uid_for("load", set()))
        self.assertNotEqual(app.uid_for("load", {a}), a)
        self.assertNotEqual(a, app.STATUS_UID)

    def test_selftest_passes_without_hardware_assumptions(self):
        from upstray import app
        ok, data = app.selftest()
        self.assertTrue(ok, data)
        self.assertEqual(data["version"], VERSION)
        for name in ("python", "tcl_tk", "hid", "icon_render", "settings", "ups"):
            self.assertIn(name, data["checks"])

    def test_version_format(self):
        self.assertRegex(VERSION, r"^\d+\.\d+\.\d+$")


class PollerTests(unittest.TestCase):
    """Poller.step(): connect, read, give up after three failed reads, find the UPS again."""

    class FakeApp:
        def __init__(self):
            self.settings = settings.normalize({"nominal_watts": 980})
            self.events = []
            self.payloads = []

        def post(self, kind, payload=None):
            self.events.append(kind)
            self.payloads.append(payload)

        def wanted_keys(self):
            return ["input_voltage", "load", "charge"]

    def setUp(self):
        from upstray import app as appmod
        self.appmod = appmod
        self.backend = device.RecordedBackend(load_fixture())
        self.found = [self.backend]
        self._orig = device.find_backend
        device.find_backend = lambda serial="": self.found[0]
        self.app = self.FakeApp()
        self.poller = appmod.Poller(self.app)

    def tearDown(self):
        device.find_backend = self._orig

    def test_connect_then_snapshots(self):
        self.poller.step(now=100)
        self.assertEqual(self.app.events, ["connected", "snapshot"])
        self.assertIn("real_power", self.poller.ups.by_key)        # nominal watts passed through
        self.poller.step(now=102)
        self.assertEqual(self.app.events[-1], "snapshot")

    def test_three_failures_disconnect_and_rescan_later(self):
        self.poller.step(now=100)
        self.backend.offline = True
        for t in (102, 104, 106):
            self.poller.step(now=t)
        self.assertEqual(self.app.events[-2:], ["snapshot", "disconnected"])
        self.assertIsNone(self.poller.ups)
        self.found[0] = None                       # unplugged
        n = len(self.app.events)
        self.poller.step(now=107)                  # before the rescan time: nothing
        self.assertEqual(len(self.app.events), n)
        self.poller.step(now=109)                  # rescans, finds nothing, waits 5 s
        self.assertEqual(self.app.events[n:], ["disconnected"])   # keeps the offline timer going
        self.backend.offline = False
        self.found[0] = self.backend               # plugged back in
        self.poller.step(now=113)
        self.assertEqual(self.app.events[-1], "disconnected")      # still inside the 5 s back-off
        self.poller.step(now=115)
        self.assertEqual(self.app.events[-2:], ["connected", "snapshot"])

    def test_test_requests(self):
        self.poller.step(now=100)
        ups_id = self.poller.ups.identity()
        self.poller.request("test", device.QUICK_TEST, ups_id)
        self.poller.step(now=102)
        self.assertEqual(self.app.events[-2:], ["test_sent", "snapshot"])
        self.assertEqual(self.backend.writes, [3, 0, 1])
        self.poller.request("test", device.CALIBRATION, ups_id)  # one is running already
        self.poller.step(now=104)
        i = self.app.events.index("test_refused")
        self.assertEqual(self.app.payloads[i][:2], ("test", device.CALIBRATION))
        self.assertIn("already running", self.app.payloads[i][2])
        self.poller.request("abort", 0, ups_id)
        self.poller.step(now=106)
        self.assertEqual(self.app.events[-2:], ["test_sent", "snapshot"])
        self.assertEqual(self.backend.writes, [3, 0, 1, 3])

    def test_command_goes_only_to_the_confirmed_ups(self):
        self.poller.step(now=100)
        other = (0x051D, 0x0002, "AS9999999999", "Smart-UPS 1500")
        self.poller.request("test", device.QUICK_TEST, other)
        self.poller.step(now=102)
        i = self.app.events.index("test_refused")
        self.assertIn("different UPS", self.app.payloads[i][2])
        self.assertEqual(self.backend.writes, [])

    def test_requests_while_disconnected_are_refused(self):
        self.found[0] = None
        self.poller.request("test", device.QUICK_TEST, None)
        self.poller.step(now=100)
        self.assertEqual(self.app.events, ["disconnected", "test_refused"])
        self.assertIn("not connected", self.app.payloads[1][2])
        self.poller.request("abort", 0, None)
        self.poller.step(now=101)                                 # inside the rescan back-off
        self.assertEqual(self.app.events[-1], "test_refused")

    def test_unexpected_error_is_still_answered(self):
        self.poller.step(now=100)
        def boom(kind, sleep=None):
            raise RuntimeError("bug")
        self.poller.ups.start_test = boom
        self.poller.request("test", device.QUICK_TEST, self.poller.ups.identity())
        self.poller.step(now=102)
        self.assertIn("test_refused", self.app.events)
        self.assertIsNotNone(self.poller.ups)                   # the UPS stays connected
        self.assertEqual(self.app.events[-1], "snapshot")

    def test_release_for_windows(self):
        self.poller.step(now=100)
        ups = self.poller.ups
        self.poller.release()                                     # tray thread: a device removal notice
        self.assertTrue(ups.closed)
        self.poller.step(now=101)
        self.assertEqual(self.app.events[-1], "disconnected")
        self.assertIsNone(self.poller.ups)
        n = len(self.app.events)
        self.poller.step(now=104)                                 # leaves Windows time for the change
        self.assertEqual(len(self.app.events), n)
        self.poller.step(now=106)
        self.assertEqual(self.app.events[-2:], ["connected", "snapshot"])

    def test_reconnect_request_rebuilds_readings(self):
        self.poller.step(now=100)
        first = self.poller.ups
        self.app.settings = settings.normalize({"nominal_watts": 0})
        self.poller.wake(reconnect=True)
        self.poller.step(now=102)
        self.assertIsNot(self.poller.ups, first)
        self.assertNotIn("real_power", self.poller.ups.by_key)


class ReviewRegressions(unittest.TestCase):
    """Findings of the pre-release review."""

    def test_signed_32_bit(self):
        f = model.Field("value", 0x84, 0x57, 1, 18, bits=32, logical_min=-1, logical_max=0x7FFFFFFF)
        self.assertEqual(f.signed(0xFFFFFFFF), -1)
        self.assertEqual(f.signed(5), 5)

    def test_physical_range_is_applied(self):
        f = model.Field("value", 0x84, 0x35, 5, 44, bits=8, logical_min=0, logical_max=255,
                        physical_min=0, physical_max=100)
        self.assertAlmostEqual(f.to_physical_units(128), 50.196, places=3)
        same = model.Field("value", 0x84, 0x35, 5, 44, bits=8, logical_min=0, logical_max=255)
        self.assertEqual(same.to_physical_units(128), 128)          # 0/0 = undefined

    def test_absolute_capacity_becomes_a_percentage(self):
        def mah(b):
            b.set_raw(6, 0x85, 0x2C, 1, 0)        # CapacityMode = mAh
            b.set_raw(24, 0x85, 0x66, 3, 500)     # remaining 50.0 mAh (exp -1)
            b.set_raw(14, 0x85, 0x67, 1, 200)     # full charge 200 mAh
        ups, _ = make_ups(mutate=mah)
        self.assertEqual(ups.capacity_mode, 0)
        r = ups.by_key["charge"]
        self.assertEqual(r.kind, "derived")
        self.assertFalse(ups.by_key["_remaining_capacity"].tray)
        snap = ups.snapshot(["charge"], low_charge=30)
        self.assertAlmostEqual(snap.values["charge"], 25.0)
        self.assertTrue(snap.status.charge_low)

    def test_unreadable_capacity_mode_never_assumes_percent(self):
        def unknown(b):
            b.set_raw(6, 0x85, 0x2C, 1, None)     # CapacityMode read fails at connect
            b.set_raw(24, 0x85, 0x66, 3, 1000)    # remaining 100.0 (units unknown)
            b.set_raw(14, 0x85, 0x67, 1, 200)     # full charge 200
        ups, _ = make_ups(mutate=unknown)
        self.assertIsNone(ups.capacity_mode)
        snap = ups.snapshot(["charge"], low_charge=20)
        self.assertAlmostEqual(snap.values["charge"], 50.0)   # 100/200, not "100 %"

    def test_physical_range_keeps_its_precision(self):
        f = model.Field("value", 0x84, 0x35, 5, 44, bits=8, logical_min=0, logical_max=255,
                        physical_min=0, physical_max=100, path=("UPS", "Output"))
        r = next(r for r in model.build_readings([f]) if r.key == "load")
        self.assertEqual(model.format_value(r, model.physical(r, 128)), "50.2 %")

    def test_percent_mode_unchanged(self):
        ups, _ = make_ups()
        self.assertEqual(ups.capacity_mode, 2)
        self.assertEqual(ups.by_key["charge"].field.report_id, 24)

    def test_closed_ups_never_touches_the_device(self):
        ups, backend = make_ups()
        ups.close()
        backend.reads.clear()
        self.assertFalse(ups.snapshot().status.ok)
        with self.assertRaises(RuntimeError):
            ups.export()
        self.assertEqual(backend.reads, [])
        ups.close()                                   # twice is fine

    def test_closed_hid_device_is_inert(self):
        from upstray import winhid
        d = winhid.HidDevice.__new__(winhid.HidDevice)
        d.handle, d._pdata, d.feature_len = None, winhid._PDATA(), 8
        self.assertTrue(d.closed)
        self.assertIsNone(d.get_feature(1))
        self.assertIsNone(d.usage_value(b"\x01\x00", 0x84, 0, 0x30))
        self.assertIsNone(d.usages_on(b"\x07\x00", 0x85, 2))
        self.assertEqual(d.product(), "")

    def test_offline_notice_while_searching(self):
        tk_or_skip(self)
        from upstray import app as appmod
        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("LOCALAPPDATA")
            os.environ["LOCALAPPDATA"] = d
            try:
                a = appmod.App()
                a.tray = FakeTray()
                a.settings.update({"notify_offline": True})
                a.ups, _ = make_ups()
                a.last_snapshot = device.Snapshot(time=0, status=model.Status(ok=False))
                for t in (100, 104, 109):
                    a._notify(now=t)
                self.assertEqual(a.tray.balloons, [])
                a._notify(now=111)
                self.assertEqual(a.tray.balloons, ["UPS not responding"])
                a._notify(now=130)
                self.assertEqual(len(a.tray.balloons), 1)
                a.root.destroy()
            finally:
                if old is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = old


class WritePath(unittest.TestCase):
    """The one write to the UPS: the Test usage, from winhid.HidDevice.write_test only."""

    WRITE_NAMES = ("HidD_SetFeature", "HidP_SetUsageValue", "GENERIC_WRITE")
    NEVER = ("HidD_SetOutputReport", "WriteFile", "DeviceIoControl")

    @staticmethod
    def _source(name):
        with open(os.path.join(SRC, "upstray", name), encoding="utf-8") as fh:
            return fh.read()

    def test_no_other_write_calls(self):
        for name in sorted(os.listdir(os.path.join(SRC, "upstray"))):
            if not name.endswith(".py"):
                continue
            # strip docstrings/comments that mention the calls on purpose
            code = re.sub(r'"""[\s\S]*?"""', "", self._source(name))
            code = re.sub(r"#.*", "", code)
            for call in self.NEVER:
                self.assertNotIn(call, code, "%s uses %s" % (name, call))
            if name != "winhid.py":
                for call in self.WRITE_NAMES:
                    self.assertNotIn(call, code, "%s uses %s" % (name, call))

    def test_writes_happen_only_in_write_test(self):
        tree = ast.parse(self._source("winhid.py"))
        allowed = set()
        for node in tree.body:
            # the ctypes bindings (argtypes/restype) and the GENERIC_WRITE constant
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                t = node.targets[0]
                if ((isinstance(t, ast.Attribute) and t.attr in ("argtypes", "restype"))
                        or (isinstance(t, ast.Name) and t.id == "GENERIC_WRITE")):
                    allowed.update(id(n) for n in ast.walk(node))
            if isinstance(node, ast.ClassDef) and node.name == "HidDevice":
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == "write_test":
                        allowed.update(id(n) for n in ast.walk(item))
        uses = 0
        for node in ast.walk(tree):
            name = node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else None
            if name in self.WRITE_NAMES:
                uses += 1
                self.assertIn(id(node), allowed, "winhid.py line %d uses %s" % (node.lineno, name))
        self.assertGreaterEqual(uses, 4)

    def test_report_must_carry_test_alone(self):
        from upstray import winhid
        alone = winhid.test_report_alone
        self.assertTrue(alone([(0x84, 0x58, 0x58, 22), (0x84, 0x30, 0x30, 7)], 22))
        self.assertFalse(alone([(0x84, 0x58, 0x58, 22), (0x84, 0x57, 0x57, 22)], 22))   # shutdown delay too
        self.assertFalse(alone([(0x84, 0x55, 0x58, 22)], 22))                           # a usage range
        self.assertFalse(alone([(0x84, 0x30, 0x30, 22)], 22))                           # no Test at all
        self.assertFalse(alone([], 22))

    def test_write_test_refuses_before_opening_anything(self):
        from upstray import winhid
        d = winhid.HidDevice.__new__(winhid.HidDevice)
        d.close = lambda: None                    # never free the fake preparsed data
        d.path = "\\\\?\\hid#no-such-device#{00000000-0000-0000-0000-000000000000}"
        d.handle, d._pdata, d.feature_len = 1, winhid._PDATA(1), 5
        d._feature_entries = lambda: [(0x84, 0x58, 0x58, 22)]
        for bad in (4, 5, 6, -1, True, 2.0, None):
            with self.assertRaises(ValueError):
                d.write_test(22, 3, bad)
        d._feature_entries = lambda: [(0x84, 0x58, 0x58, 22), (0x85, 0x58, 0x58, 22)]   # with BUPHibernate
        with self.assertRaises(ValueError):
            d.write_test(22, 3, 1)
        d._feature_entries = lambda: [(0x84, 0x58, 0x58, 22)]
        with self.assertRaises(winhid.HidError):
            d.write_test(22, 3, 1)                # passes the checks, then the device does not exist
        d.handle = None
        with self.assertRaises(winhid.HidError):
            d.write_test(22, 3, 1)                # closed


class BatteryTests(unittest.TestCase):
    """Ups.start_test / abort_test against the recording (writes go to the backend's log)."""

    def test_writable_test_field(self):
        ups, _ = make_ups()
        self.assertEqual((ups.test_field.report_id, ups.test_field.link), (22, 3))

    def test_self_test_follows_apctest(self):
        ups, backend = make_ups()
        ups.start_test(device.QUICK_TEST, sleep=no_sleep)
        self.assertEqual(backend.writes, [3, 0, 1])           # abort, clear, then the quick test
        self.assertEqual(ups.snapshot(["self_test"]).values["self_test"], device.TEST_RUNNING)

    def test_calibration_with_full_battery_and_load(self):
        ups, backend = make_ups(mutate=full_and_loaded)
        ups.start_test(device.CALIBRATION, sleep=no_sleep)
        self.assertEqual(backend.writes, [3, 0, 2])

    def test_calibration_refused_below_full_charge(self):
        ups, backend = make_ups()                             # the recording is at 99.0 %
        with self.assertRaises(device.TestRefused) as cm:
            ups.start_test(device.CALIBRATION, sleep=no_sleep)
        self.assertIn("99.0 %", str(cm.exception))
        self.assertEqual(backend.writes, [])
        ups.start_test(device.QUICK_TEST, sleep=no_sleep)      # a self-test needs no full charge
        self.assertEqual(backend.writes, [3, 0, 1])

    def test_calibration_refused_with_little_load(self):
        ups, backend = make_ups(mutate=lambda b: full_and_loaded(b, load_tenths=60))
        with self.assertRaises(device.TestRefused) as cm:
            ups.start_test(device.CALIBRATION, sleep=no_sleep)
        self.assertIn("at least 10 %", str(cm.exception))
        self.assertEqual(backend.writes, [])

    def test_refused_on_battery_or_while_running_or_offline(self):
        ups, backend = make_ups(mutate=lambda b: on_battery(b, 1000))
        with self.assertRaises(device.TestRefused):
            ups.start_test(device.QUICK_TEST, sleep=no_sleep)
        ups, backend = make_ups(mutate=lambda b: b.set_raw(*TEST_KEY, device.TEST_RUNNING))
        with self.assertRaises(device.TestRefused) as cm:
            ups.start_test(device.QUICK_TEST, sleep=no_sleep)
        self.assertIn("already running", str(cm.exception))
        ups, backend = make_ups()
        backend.offline = True
        with self.assertRaises(device.TestRefused):
            ups.start_test(device.QUICK_TEST, sleep=no_sleep)
        self.assertEqual(backend.writes, [])
        ups.close()
        with self.assertRaises(device.TestRefused):
            ups.start_test(device.QUICK_TEST, sleep=no_sleep)

    def test_no_start_when_the_result_does_not_clear(self):
        class Stuck(CountingBackend):
            def write_test(self, f, command):
                self.writes.append(command)          # the UPS keeps reporting 'aborted'
                self.raw[TEST_KEY] = 4
        backend = Stuck(load_fixture())
        ups = device.Ups(backend)
        sleeps = []
        with self.assertRaises(device.TestRefused):
            ups.start_test(device.QUICK_TEST, sleep=sleeps.append)
        self.assertEqual(backend.writes, [3, 0])               # never the start command
        self.assertEqual(sum(sleeps), 10)

    def test_failed_abort_before_clearing_is_ignored(self):
        class NoAbort(CountingBackend):
            def write_test(self, f, command):
                if command == device.TEST_ABORT:
                    raise OSError(31, "stall")
                super().write_test(f, command)
        backend = NoAbort(load_fixture())
        ups = device.Ups(backend)
        ups.start_test(device.QUICK_TEST, sleep=no_sleep)
        self.assertEqual(backend.writes, [0, 1])

    def test_conditions_checked_again_before_the_start_command(self):
        class MainsFailsWhileClearing(CountingBackend):
            def write_test(self, f, command):
                super().write_test(f, command)
                if command == device.TEST_CLEAR:
                    on_battery(self)
        backend = MainsFailsWhileClearing(load_fixture())
        ups = device.Ups(backend)
        with self.assertRaises(device.TestRefused) as cm:
            ups.start_test(device.QUICK_TEST, sleep=no_sleep)
        self.assertIn("on battery", str(cm.exception))
        self.assertEqual(backend.writes, [3, 0])              # cleared, never started

    def test_close_stops_a_start_in_progress(self):
        class SlowClear(CountingBackend):
            def write_test(self, f, command):
                self.writes.append(command)
                self.raw[TEST_KEY] = 4                         # not cleared yet
        backend = SlowClear(load_fixture())
        ups = device.Ups(backend)
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            ups._closing = True                                # what close() sets from the tray thread
        with self.assertRaises(device.TestRefused) as cm:
            ups.start_test(device.QUICK_TEST, sleep=sleep)
        self.assertIn("disconnected", str(cm.exception))
        self.assertEqual(sleeps, [0.5])                        # gave up at the next check
        self.assertEqual(backend.writes, [3, 0])

    def test_identity(self):
        a, _ = make_ups()
        b, _ = make_ups()
        self.assertEqual(a.identity(), b.identity())          # the same UPS after a reconnect
        data = load_fixture()
        data["info"]["serial"] = "AS0000000001"
        self.assertNotEqual(device.Ups(device.RecordedBackend(data)).identity(), a.identity())

    def test_abort(self):
        ups, backend = make_ups(mutate=lambda b: b.set_raw(*TEST_KEY, device.TEST_RUNNING))
        ups.abort_test()
        self.assertEqual(backend.writes, [3])
        self.assertEqual(ups.snapshot(["self_test"]).values["self_test"], 4)

    def test_no_writes_when_test_shares_its_report(self):
        def share(data):
            extra = dict(next(f for f in data["fields"] if f["usage"] == 0x57 and f["page"] == 0x84))
            extra["report_id"] = 22                     # DelayBeforeShutdown in the Test report
            data["fields"].append(extra)
        data = load_fixture()
        share(data)
        backend = CountingBackend(data)
        ups = device.Ups(backend)
        self.assertIsNone(ups.test_field)
        self.assertIn("self_test", ups.by_key)          # still shown, just never written
        for call in (lambda: ups.start_test(device.QUICK_TEST, sleep=no_sleep), ups.abort_test):
            with self.assertRaises(device.TestRefused) as cm:
                call()
            self.assertIn("shares a USB report", str(cm.exception))
        self.assertEqual(backend.writes, [])

    def test_lenient_check_on_a_partial_snapshot(self):
        ups, backend = make_ups(mutate=full_and_loaded)
        partial = ups.snapshot(["input_voltage"])               # no charge, load or test state
        self.assertIsNone(ups.test_refusal(device.CALIBRATION, partial, strict=False))
        self.assertIn("did not report", ups.test_refusal(device.CALIBRATION, partial))
        backend.set_raw(44, 0x84, 0x35, 5, None)                # the load read fails right then
        with self.assertRaises(device.TestRefused) as cm:
            ups.start_test(device.CALIBRATION, sleep=no_sleep)
        self.assertIn("did not report the battery charge and the load", str(cm.exception))
        self.assertEqual(backend.writes, [])

    def test_no_test_usage(self):
        data = load_fixture()
        data["fields"] = [f for f in data["fields"] if (f["page"], f["usage"]) != (0x84, 0x58)]
        ups = device.Ups(CountingBackend(data))
        self.assertIsNone(ups.test_field)
        self.assertIn("does not offer", ups.test_refusal(device.QUICK_TEST, ups.snapshot()))


class Glyphs(unittest.TestCase):
    def setUp(self):
        from upstray import glyphs
        self.g = glyphs

    def test_pixels_fit_and_are_centered(self):
        for size in (16, 20, 24, 32):
            for texts in (["235"], ["75%", "75"], ["27.3", "27"], ["--"]):
                s, px, chosen = self.g.render_pixels(texts, "#FFFFFF", None, size)
                self.assertEqual(s, size)
                self.assertEqual(len(px), size * size * 4)
                self.assertIn(chosen, texts)
                xs = [(i // 4) % size for i in range(0, len(px), 4) if px[i + 3]]
                ys = [(i // 4) // size for i in range(0, len(px), 4) if px[i + 3]]
                self.assertTrue(xs and ys, (size, texts))
                self.assertLessEqual(abs((min(xs) + max(xs)) / 2 - (size - 1) / 2), 1)
                self.assertLessEqual(abs((min(ys) + max(ys)) / 2 - (size - 1) / 2), 1)

    def test_badge_is_opaque_inside(self):
        s, px, _ = self.g.render_pixels(["57"], "#FFFFFF", "#2D6A31", 24)
        center = ((s // 2) * s + s // 2) * 4
        self.assertEqual(px[center + 3], 255)

    def test_png_and_icon(self):
        s, px, _ = self.g.render_pixels(["235"], "#FFFFFF", None, 16)
        png = self.g.png_bytes(s, px)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        h = self.g.render(["235"], "#000000", None, 24)
        self.assertTrue(h)
        self.g.destroy(h)


class FakeTray:
    def __init__(self):
        self.balloons = []
        self.icons = []
        self.watched = []

    def balloon(self, title, text, warning=False):
        self.balloons.append(title)

    def set_icons(self, icons):
        self.icons = icons

    def watch(self, handle):
        self.watched.append(handle)


class temp_appdata:
    """LOCALAPPDATA pointed at a temporary folder (settings and log stay out of the profile)."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = self.tmp.name
        return self.tmp.name

    def __exit__(self, *exc):
        if self.old is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = self.old
        self.tmp.cleanup()


def tk_or_skip(test):
    try:
        import tkinter
        tkinter.Tk().destroy()
    except Exception as e:              # no display (rare on Windows runners)
        test.skipTest("Tk unavailable: %r" % (e,))


class Notifications(unittest.TestCase):
    def test_battery_low_restored_sequence(self):
        tk_or_skip(self)
        from upstray import app as appmod
        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("LOCALAPPDATA")
            os.environ["LOCALAPPDATA"] = d
            try:
                a = appmod.App()
                a.tray = FakeTray()
                a.settings.update({"notify_on_battery": True, "notify_after_seconds": 0,
                                   "notify_restored": True, "notify_low": True, "notify_offline": True})
                ups, backend = make_ups()
                a.ups = ups

                def feed():
                    a.last_snapshot = ups.snapshot(None, a.settings["low_charge_percent"])
                    a._after_snapshot()

                feed()
                self.assertEqual(a.tray.balloons, [])
                on_battery(backend, 800)
                feed()
                self.assertEqual(len(a.tray.balloons), 1)
                self.assertIn("on battery", a.tray.balloons[-1])
                feed()                                          # no repeat
                self.assertEqual(len(a.tray.balloons), 1)
                on_battery(backend, 150)
                feed()
                self.assertIn("battery low", a.tray.balloons[-1])
                backend.set_buttons(STATUS_RID, 0x85, STATUS_LINK, [AC_PRESENT, CHARGING, BATTERY_PRESENT])
                feed()
                self.assertEqual(a.tray.balloons[-1], "Mains power restored")
                # icons: one per chosen reading, never more than chosen
                self.assertEqual(len(a.tray.icons), len(a.settings["tray_readings"]))
                a.root.destroy()
            finally:
                if old is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = old


class TestTracking(unittest.TestCase):
    """The app follows a battery test: asked for, sent, running, result notice."""

    def setUp(self):
        tk_or_skip(self)
        from upstray import app as appmod
        self.appmod = appmod
        self.env = temp_appdata()
        self.env.__enter__()
        self.a = appmod.App()
        self.a.tray = FakeTray()
        self.a.ups, self.backend = make_ups(mutate=full_and_loaded)
        self.feed()

    def tearDown(self):
        self.a.root.update_idletasks()
        self.a.root.destroy()
        self.env.__exit__()

    def feed(self, now=None):
        self.a.last_snapshot = self.a.ups.snapshot(self.a.wanted_keys(), 20)
        self.a._track_test(now=now)

    def test_self_test_passes(self):
        a = self.a
        self.assertEqual(a.test_controls(), (True, False))
        self.assertEqual(a.test_text(), "Last result: none")
        a.request_test(device.QUICK_TEST, a.ups.identity())
        self.assertEqual(a.poller._requests, [("test", device.QUICK_TEST, a.ups.identity())])
        self.assertEqual(a.test_controls(), (False, False))          # waiting for the poller
        self.assertEqual(a.test_text(), "Starting the battery self-test...")
        a._test_sent("test", device.QUICK_TEST, now=1000)
        self.backend.set_raw(*TEST_KEY, device.TEST_RUNNING)
        self.feed(now=1002)
        self.assertEqual(a.test_controls(), (False, True))
        self.assertIn("battery self-test running", a._status_text())
        self.assertTrue(a.test_text().startswith("Battery self-test running for"))
        self.backend.set_raw(*TEST_KEY, 1)
        self.feed(now=1010)
        self.assertEqual(a.tray.balloons, ["Battery self-test passed"])
        self.assertIsNone(a._test)
        self.assertEqual(a.test_text(), "Last result: passed")
        self.assertEqual(a.test_controls(), (True, False))

    def test_result_without_seeing_it_run(self):
        self.a._test_sent("test", device.QUICK_TEST, now=1000)
        self.backend.set_raw(*TEST_KEY, 3)
        self.feed(now=1004)
        self.assertEqual(self.a.tray.balloons, ["Battery self-test failed"])

    def test_quick_test_on_battery_with_no_test_reading(self):
        # what a Smart-UPS 1500 does: Test stays 6 while on battery, then reads the result
        a = self.a
        a._test_sent("test", device.QUICK_TEST, now=1000)
        self.feed(now=1001)
        self.assertIn("Waiting for the UPS", a.test_text())
        on_battery(self.backend)
        for t in range(1003, 1100, 2):                              # longer than the start timeout
            self.feed(now=t)
        self.assertTrue(a.test_text().startswith("Battery self-test running for"))
        self.assertIn("battery self-test running", a._status_text())
        self.assertEqual(a.tray.balloons, [])
        self.backend.set_buttons(STATUS_RID, 0x85, STATUS_LINK, [AC_PRESENT, CHARGING, BATTERY_PRESENT])
        self.backend.set_raw(*TEST_KEY, 1)
        self.feed(now=1101)
        self.assertEqual(a.tray.balloons, ["Battery self-test passed"])

    def test_quick_test_that_ends_without_a_result(self):
        a = self.a
        a._test_sent("test", device.QUICK_TEST, now=1000)
        on_battery(self.backend)
        self.feed(now=1004)
        self.backend.set_buttons(STATUS_RID, 0x85, STATUS_LINK, [AC_PRESENT, CHARGING, BATTERY_PRESENT])
        self.feed(now=1030)
        self.assertEqual(a.tray.balloons, [])
        self.feed(now=1000 + self.appmod.TEST_START_TIMEOUT + 1)
        self.assertEqual(a.tray.balloons, ["Battery self-test ended"])
        self.assertIsNone(a._test)

    def test_result_of_a_test_the_ups_ran_by_itself(self):
        a = self.a
        self.backend.set_raw(*TEST_KEY, 1)
        self.feed(now=1000)                                          # a new result
        self.assertEqual(a.tray.balloons, ["Battery test passed"])
        self.feed(now=1002)                                          # unchanged: no repeat
        self.assertEqual(len(a.tray.balloons), 1)

    def test_ups_that_never_starts(self):
        a = self.a
        a._test_sent("test", device.CALIBRATION, now=1000)
        self.feed(now=1030)                                          # still 'no test': wait
        self.assertEqual(a.tray.balloons, [])
        self.assertIn("Waiting for the UPS", a.test_text())
        self.feed(now=1000 + self.appmod.TEST_START_TIMEOUT + 1)
        self.assertEqual(a.tray.balloons, ["Runtime calibration did not start"])
        self.assertIsNone(a._test)

    def test_calibration_aborted_from_here(self):
        a = self.a
        a._test_sent("test", device.CALIBRATION, now=1000)
        self.backend.set_raw(*TEST_KEY, device.TEST_RUNNING)
        self.feed(now=1010)
        a.request_abort(a.ups.identity())
        self.assertEqual(a.poller._requests[-1], ("abort", 0, a.ups.identity()))
        a._test_sent("abort", 0)
        self.backend.set_raw(*TEST_KEY, 4)
        self.feed(now=1500)
        self.assertEqual(a.tray.balloons, ["Runtime calibration aborted"])

    def test_calibration_finishes(self):
        a = self.a
        a._test_sent("test", device.CALIBRATION, now=1000)
        self.backend.set_raw(*TEST_KEY, device.TEST_RUNNING)
        self.feed(now=1010)
        self.backend.set_raw(*TEST_KEY, 1)
        self.feed(now=1000 + 2 * 3600)
        self.assertEqual(a.tray.balloons, ["Runtime calibration finished"])

    def test_no_notice_for_the_result_found_at_start(self):
        self.backend.set_raw(*TEST_KEY, 1)
        self.a._last_test_value = None                                # as on the first snapshot
        self.feed(now=1000)
        self.assertEqual(self.a.tray.balloons, [])

    def test_test_started_at_the_ups_is_followed(self):
        a = self.a
        self.backend.set_raw(*TEST_KEY, device.TEST_RUNNING)
        self.feed(now=1000)
        self.assertEqual(a.test_controls(), (False, True))
        self.assertEqual(a.test_refusal(device.QUICK_TEST), "A battery test is already running.")
        self.backend.set_raw(*TEST_KEY, 1)
        self.feed(now=1012)
        self.assertEqual(a.tray.balloons, ["Battery test passed"])

    def test_refusal_without_details_window_is_a_notice(self):
        self.a._test_pending = ("test", device.QUICK_TEST)
        self.a._test_refused("test", device.QUICK_TEST, "The UPS is not connected.")
        self.assertEqual(self.a.tray.balloons, ["Battery self-test not started"])
        self.assertIsNone(self.a._test_pending)

    def test_another_ups_does_not_finish_the_test(self):
        a = self.a
        a._handle("connected", a.ups)
        a._test_sent("test", device.CALIBRATION, now=1000)
        self.backend.set_raw(*TEST_KEY, device.TEST_RUNNING)
        self.feed(now=1010)
        a._handle("connected", a.ups)                                 # the same UPS after a drop-out
        self.assertIsNotNone(a._test)
        data = load_fixture()
        data["info"]["serial"] = "AS0000000001"
        other = CountingBackend(data)
        other.set_raw(*TEST_KEY, 1)                                   # an old result of its own
        a._handle("connected", device.Ups(other))
        self.assertIsNone(a._test)
        a.last_snapshot = a.ups.snapshot(a.wanted_keys(), 20)
        a._track_test(now=1100)
        self.assertEqual(a.tray.balloons, [])

    def test_offline_pauses_the_test(self):
        a = self.a
        a._test_sent("test", device.CALIBRATION, now=1000)
        self.backend.set_raw(*TEST_KEY, device.TEST_RUNNING)
        self.feed(now=1010)
        self.backend.offline = True
        self.feed(now=5000)                                          # the USB link drops out
        self.assertIsNotNone(a._test)
        self.backend.offline = False
        self.backend.set_raw(*TEST_KEY, 1)
        self.feed(now=6000)
        self.assertEqual(a.tray.balloons, ["Runtime calibration finished"])


def ink_rows(px, size):
    """Indexes of icon rows that carry ink."""
    return [y for y in range(size) if any(px[(y * size + x) * 4 + 3] for x in range(size))]


def bands(rows):
    """Consecutive runs of inked rows: [(first, last), ...]."""
    out = []
    for y in rows:
        if out and y == out[-1][1] + 1:
            out[-1] = (out[-1][0], y)
        else:
            out.append((y, y))
    return out


class IconStyles(unittest.TestCase):
    def setUp(self):
        from upstray import glyphs
        self.g = glyphs

    def test_label_sits_above_the_value(self):
        size = 24
        _, px, text = self.g.render_pixels(["239"], "#000000", None, size, label="VIN")
        b = bands(ink_rows(px, size))
        self.assertEqual(len(b), 2, b)                     # label band, gap, value band
        label_band, value_band = b
        self.assertEqual(label_band[0], 0)                 # label starts at the top
        self.assertGreater(value_band[0], label_band[1] + 1)
        self.assertGreaterEqual(value_band[1] - value_band[0] + 1, 10)   # value stays big

    def test_badge_keeps_its_label_at_20px(self):
        # review finding: the badge's 18 px inner area must not drop the label the app planned for
        _, px, _ = self.g.render_pixels(["41"], "#FFFFFF", "#2D6A31", 20, label="LOAD")
        coverage = [y for y in range(20)
                    if any(px[(y * 20 + x) * 4 + 1] > 200 for x in range(2, 18))]   # light text pixels
        self.assertEqual(len(bands(coverage)), 2, bands(coverage))

    def test_no_label_on_small_icons(self):
        _, px, _ = self.g.render_pixels(["239"], "#000000", None, 16, label="VIN")
        self.assertEqual(len(bands(ink_rows(px, 16))), 1)

    def test_values_share_one_digit_height(self):
        heights = set()
        for texts in (["239"], ["41%", "41"], ["57"], ["100%", "100"]):
            _, px, _ = self.g.render_pixels(texts, "#000000", None, 24)
            rows = ink_rows(px, 24)
            heights.add(rows[-1] - rows[0] + 1)
        self.assertLessEqual(max(heights) - min(heights), 1, heights)

    def test_stacked_draws_two_lines(self):
        size = 24
        _, px, _ = self.g.render_pixels(["239"], "#000000", None, size, second=["41%", "41"])
        b = bands(ink_rows(px, size))
        self.assertEqual(len(b), 2, b)
        top_h, bottom_h = b[0][1] - b[0][0], b[1][1] - b[1][0]
        self.assertLessEqual(abs(top_h - bottom_h), 1)      # same digit height on both lines

    def test_preview_png(self):
        size, px, _ = self.g.render_pixels(["239"], "#000000", None, 24, label="VIN")
        png = self.g.png_on(size, px, "#EEEEEE", zoom=3)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_compact_texts_drop_the_percent_sign(self):
        ups, _ = make_ups()
        self.assertEqual(model.tray_texts(ups.by_key["load"], 41.2, compact=True), ["41"])
        self.assertEqual(model.tray_texts(ups.by_key["load"], 41.2), ["41%", "41"])
        self.assertEqual(ups.by_key["input_voltage"].label, "VIN")
        self.assertEqual(ups.by_key["load"].label, "LOAD")
        other = next(r for r in ups.readings if r.group == "Other")
        self.assertEqual(other.label, "")

    def test_style_setting(self):
        self.assertEqual(settings.normalize({})["icon_style"], "label")
        self.assertEqual(settings.normalize({"icon_style": "stacked"})["icon_style"], "stacked")
        self.assertEqual(settings.normalize({"icon_style": "fancy"})["icon_style"], "label")

    def test_icon_plan_per_style(self):
        tk_or_skip(self)
        from upstray import app as appmod
        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("LOCALAPPDATA")
            os.environ["LOCALAPPDATA"] = d
            try:
                a = appmod.App()
                a.tray = FakeTray()
                a.ups, _ = make_ups()
                a.last_snapshot = a.ups.snapshot()
                keys = ["input_voltage", "load", "charge"]
                s = dict(a.settings, icon_style="label")
                plan = a.icon_plan(s=s, keys=keys, size=24)
                self.assertEqual([p[5] for p in plan], ["VIN", "LOAD", "BATT"])
                self.assertEqual(plan[1][1], (str(round(a.last_snapshot.values["load"])),))   # no % with a label
                s["icon_style"] = "large"
                plan = a.icon_plan(s=s, keys=keys, size=24)
                self.assertEqual([p[5] for p in plan], [None, None, None])
                s["icon_style"] = "stacked"
                plan = a.icon_plan(s=s, keys=keys, size=24)
                self.assertEqual(len(plan), 2)                       # pairs, plus one single
                self.assertIsNotNone(plan[0][6])
                self.assertIsNone(plan[1][6])
                self.assertIn("Load", plan[0][4])                    # tooltip names both readings
                plan = a.icon_plan(s=s, keys=keys, size=16)          # too small for two rows
                self.assertEqual(len(plan), 3)
                a.root.destroy()
            finally:
                if old is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = old


class Fonts(unittest.TestCase):
    def setUp(self):
        from upstray import glyphs
        self.g = glyphs

    def test_settings_validation(self):
        s = settings.normalize({"value_font": " Consolas ", "label_font": "@Vertical", "value_weight": 700,
                                "label_weight": 500, "value_size": 10, "label_size": 99})
        self.assertEqual(s["value_font"], "Consolas")
        self.assertEqual(s["label_font"], "")                 # vertical (@) families are not offered
        self.assertEqual(s["value_weight"], 700)
        self.assertEqual(s["label_weight"], 0)                 # not one of the offered weights
        self.assertEqual((s["value_size"], s["label_size"]), (40, 50))
        self.assertEqual(settings.normalize({"value_font": "x" * 65})["value_font"], "")
        self.assertEqual(settings.normalize({"value_weight": True})["value_weight"], 0)

    def test_defaults_and_missing_fonts(self):
        d = self.g.default_fonts()
        self.assertEqual((d.value_face, d.label_face), (self.g.value_face()[0], self.g.label_face()[0]))
        f = self.g.make_fonts("No Such Font 123", 0, "", 0)
        self.assertEqual(f.value_face, d.value_face)           # missing family -> default
        f = self.g.make_fonts("Arial", 400, "Arial", 700, 80, 40)
        self.assertEqual((f.value_face, f.value_weight, f.label_weight), ("Arial", 400, 700))
        self.assertAlmostEqual(f.value_scale, 0.8)
        self.assertAlmostEqual(f.label_fraction, 0.4)

    def test_value_size_shrinks_digits(self):
        def height(fonts):
            _, px, _ = self.g.render_pixels(["239"], "#000000", None, 24, fonts=fonts)
            rows = ink_rows(px, 24)
            return rows[-1] - rows[0] + 1
        self.assertLess(height(self.g.make_fonts(value_size=60)), height(self.g.make_fonts()))

    def test_labels_share_one_height(self):
        fonts = self.g.make_fonts(label_size=45)
        heights = []
        for label in ("VIN", "LOAD", "RUN"):
            _, px, _ = self.g.render_pixels(["57"], "#000000", None, 32, label=label, fonts=fonts)
            band = bands(ink_rows(px, 32))[0]
            heights.append(band[1] - band[0])
        self.assertLessEqual(max(heights) - min(heights), 1, heights)

    def test_custom_font_changes_the_pixels(self):
        a = self.g.render_pixels(["239"], "#000000", None, 24, fonts=self.g.default_fonts())[1]
        b = self.g.render_pixels(["239"], "#000000", None, 24, fonts=self.g.make_fonts("Consolas", 700))[1]
        self.assertNotEqual(a, b)


class UiSmoke(unittest.TestCase):
    """Builds the Settings and Details windows from the recording (no tray, no poller)."""

    def test_windows_build(self):
        try:
            import tkinter
            tkinter.Tk().destroy()
        except Exception as e:          # no display (rare on Windows runners)
            self.skipTest("Tk unavailable: %r" % (e,))
        from upstray import app as appmod
        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("LOCALAPPDATA")
            os.environ["LOCALAPPDATA"] = d
            try:
                a = appmod.App()
                a.ups, _ = make_ups(980)
                a.last_snapshot = a.ups.snapshot()
                a.open_details()
                a.open_settings()
                a.root.update()
                text = a.details_window.text_report()
                self.assertIn("Input voltage", text)
                self.assertIn("Mains present: yes", text)
                # review finding: Save must stay on screen even when the content is taller
                win = a.settings_window.win
                win.geometry("%dx%d" % (win.winfo_width(), max(300, win.winfo_height() // 2)))
                a.root.update()
                buttons = []

                def walk(w):
                    for c in w.winfo_children():
                        if c.winfo_class() == "TButton" and c.cget("text") == "Save":
                            buttons.append(c)
                        walk(c)
                walk(win)
                save = buttons[0]
                self.assertTrue(save.winfo_ismapped())
                self.assertLessEqual(save.winfo_rooty() + save.winfo_height(), win.winfo_rooty() + win.winfo_height())
                # the battery test row stays on screen too, and its buttons follow the UPS state
                details = a.details_window
                details.win.geometry("%dx%d" % (details.win.winfo_width(), max(300, details.win.winfo_height() // 2)))
                a.root.update()
                self.assertTrue(details.self_btn.winfo_ismapped())
                self.assertEqual(details.self_btn.instate(["disabled"]), False)
                self.assertEqual(details.abort_btn.instate(["disabled"]), True)
                from upstray import ui
                asked, shown = [], []
                saved = ui.messagebox.askokcancel, ui.messagebox.showinfo
                ui.messagebox.askokcancel = lambda *args, **kw: asked.append(args[1]) or True
                ui.messagebox.showinfo = lambda *args, **kw: shown.append(args[1])
                try:
                    details.ask_test(device.CALIBRATION)          # the recording is at 99 %: refused
                    self.assertEqual(asked, [])
                    self.assertIn("99.0 %", shown[0])
                    details.ask_test(device.QUICK_TEST)
                    self.assertEqual(asked, ["Run a battery self-test now?"])
                    self.assertEqual(a.poller._requests, [("test", device.QUICK_TEST, a.ups.identity())])
                    self.assertTrue(details.self_btn.instate(["disabled"]))
                finally:
                    ui.messagebox.askokcancel, ui.messagebox.showinfo = saved
                a.settings_window._toggle("output_voltage", "icon")
                rows = a.settings_window.font_rows
                rows["value"][0].set("arial")                 # typed in lower case
                rows["value"][1].set("Regular")
                rows["label"][2].set(40)
                a.settings_window.save()
                self.assertIn("output_voltage", a.settings["tray_readings"])
                self.assertEqual((a.settings["value_font"], a.settings["value_weight"]), ("Arial", 400))
                self.assertEqual(a.settings["label_size"], 40)
                self.assertEqual(a.fonts().value_face, "Arial")
                self.assertTrue(os.path.exists(os.path.join(d, "UpsTray", "settings.json")))
                a.details_window.close()
                a.root.destroy()
            finally:
                if old is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = old


if __name__ == "__main__":
    unittest.main(verbosity=1)
