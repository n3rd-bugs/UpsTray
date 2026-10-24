"""Unit tests that need no UPS: the descriptor model, readings, status, formatting,
settings, tray helpers and the icon renderer, mostly against a recorded APC
Smart-UPS 1500 (tests/fixtures/smart_ups_1500.json).

    python tests\\test_logic.py
"""
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
               t.MSG_OPEN_DETAILS]
        self.assertEqual(len(ids), len(set(ids)))


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

        def post(self, kind, payload=None):
            self.events.append(kind)

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


class ReadOnly(unittest.TestCase):
    def test_no_write_path_to_the_ups(self):
        """The app must never be able to change a UPS setting or start a test."""
        pkg = os.path.join(SRC, "upstray")
        for name in os.listdir(pkg):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(pkg, name), encoding="utf-8") as fh:
                code = fh.read()
            # strip docstrings/comments that mention the calls on purpose
            code = re.sub(r'"""[\s\S]*?"""', "", code)
            code = re.sub(r"#.*", "", code)
            for call in ("HidD_SetFeature", "HidD_SetOutputReport", "HidP_SetUsageValue", "WriteFile",
                         "DeviceIoControl"):
                self.assertNotIn(call, code, "%s uses %s" % (name, call))


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

    def balloon(self, title, text, warning=False):
        self.balloons.append(title)

    def set_icons(self, icons):
        self.icons = icons


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
                a.settings_window._toggle("output_voltage", "icon")
                a.settings_window.save()
                self.assertIn("output_voltage", a.settings["tray_readings"])
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
