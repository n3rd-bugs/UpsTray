"""Find the UPS, describe it once, read snapshots of the values that are needed, and
start or abort a battery test when the user asks for one.

The Ups class talks to a backend with six calls (describe, read_reports, value,
buttons, write_test, close). HidBackend implements them with hid.dll; the unit tests
use a recorded backend, so everything above the backend is tested without hardware.
"""
import logging
import threading
import time
from dataclasses import dataclass, field

from . import model
from .model import APC, BATTERY, POWER, Field, Node

log = logging.getLogger("upstray")

APC_VID = 0x051D

# Vendor string usages worth showing (APC page): usage -> label
APC_STRINGS = {0x0042: "Firmware", 0x0079: "USB firmware"}

# Test usage (0x84:0x58): the values written to it, and what it reads while running / idle
TEST_CLEAR, QUICK_TEST, CALIBRATION, TEST_ABORT = 0, 1, 2, 3
TEST_RUNNING, NO_TEST = 5, 6


class TestRefused(Exception):
    """A battery test was not started or aborted; the message tells the user why."""


@dataclass
class DeviceInfo:
    model: str = ""
    manufacturer: str = ""
    serial: str = ""
    name: str = ""
    firmware: str = ""
    usb_firmware: str = ""
    chemistry: str = ""
    product_string: str = ""
    vendor_id: int = 0
    product_id: int = 0

    def title(self):
        return self.model or self.product_string or "UPS"


@dataclass
class Snapshot:
    time: float
    values: dict = field(default_factory=dict)        # reading key -> value in display units (or None)
    status: model.Status = field(default_factory=model.Status)
    failed_reports: tuple = ()


class HidBackend:
    """Reads one HID UPS collection through hid.dll (feature reports only)."""

    def __init__(self, dev):
        self.dev = dev
        self.path = dev.path

    def describe(self):
        from . import winhid
        d = self.dev
        raw_nodes = d.link_collections()
        nodes = [Node(n.LinkUsagePage, n.LinkUsage, n.Parent, n.Bits & 0xFF) for n in raw_nodes]
        paths = model.collection_paths(nodes)
        fields = []
        for vc in d.value_caps(winhid.HidP_Feature):
            lo, hi = (vc.UsageMin, vc.UsageMax) if vc.IsRange else (vc.UsageMin, vc.UsageMin)
            for usage in range(lo, hi + 1):
                fields.append(Field("value", vc.UsagePage, usage, vc.LinkCollection, vc.ReportID,
                                    vc.BitSize, vc.LogicalMin, vc.LogicalMax, vc.Units, vc.UnitsExp & 0xF,
                                    paths[vc.LinkCollection] if vc.LinkCollection < len(paths) else (),
                                    vc.PhysicalMin, vc.PhysicalMax))
        for bc in d.button_caps(winhid.HidP_Feature):
            lo, hi = (bc.UsageMin, bc.UsageMax) if bc.IsRange else (bc.UsageMin, bc.UsageMin)
            for usage in range(lo, hi + 1):
                fields.append(Field("button", bc.UsagePage, usage, bc.LinkCollection, bc.ReportID, 1, 0, 1,
                                    0, 0, paths[bc.LinkCollection] if bc.LinkCollection < len(paths) else ()))
        info = self._info(fields)
        return nodes, fields, info

    def _info(self, fields):
        d = self.dev
        info = DeviceInfo(manufacturer=d.manufacturer(), serial=d.serial(), product_string=d.product(),
                          vendor_id=d.vendor_id, product_id=d.product_id)
        strings = {}
        for f in fields:
            if f.kind != "value":
                continue
            wanted = (f.page, f.usage) in model.STRING_USAGES or (f.page == APC and f.usage in APC_STRINGS)
            if not wanted:
                continue
            rep = d.get_feature(f.report_id)
            index = d.usage_value(rep, f.page, f.link, f.usage) if rep else None
            text = d.indexed_string(index) if index else ""
            if text:
                strings.setdefault((f.page, f.usage), []).append((len(f.path), text))
        def pick(key, shallow=True):
            vals = sorted(strings.get(key, []))
            if not vals:
                return ""
            return (vals[0] if shallow else vals[-1])[1]
        info.model = pick((POWER, 0xFE))                       # iProduct at the UPS level: "Smart-UPS 1500"
        info.name = pick((POWER, 0x01))                        # iName: the name set on the UPS
        info.chemistry = pick((BATTERY, 0x89))
        info.serial = info.serial or pick((POWER, 0xFF))
        info.manufacturer = info.manufacturer or pick((POWER, 0xFD))
        if d.vendor_id == APC_VID:
            info.firmware = pick((APC, 0x0042))
            info.usb_firmware = pick((APC, 0x0079))
        return info

    def read_reports(self, report_ids):
        return {rid: self.dev.get_feature(rid) for rid in sorted(report_ids)}

    def value(self, report, f):
        return self.dev.usage_value(report, f.page, f.link, f.usage)

    def buttons(self, report, page, link):
        return self.dev.usages_on(report, page, link)

    def write_test(self, f, command):
        self.dev.write_test(f.report_id, f.link, command)

    @property
    def handle(self):
        return self.dev.handle

    def close(self):
        self.dev.close()


def find_backend(preferred_serial=""):
    """HidBackend for the best UPS present, or None. Prefers the given serial, then APC."""
    from . import winhid
    found = []
    for path in winhid.interface_paths():
        try:
            dev = winhid.HidDevice(path)
        except OSError:
            continue
        if dev.usage_page == POWER and dev.usage in (0x04, 0x05) and dev.caps.NumberFeatureValueCaps:
            found.append(dev)
        else:
            dev.close()
    if not found:
        return None

    def rank(dev):
        serial = ""
        try:
            serial = dev.serial()
        except OSError:
            pass
        return (0 if preferred_serial and serial == preferred_serial else 1,
                0 if dev.vendor_id == APC_VID else 1)

    found.sort(key=rank)
    for dev in found[1:]:
        dev.close()
    return HidBackend(found[0])


class Ups:
    """A described UPS. snapshot(), export() and close() may be called from different
    threads: one lock serialises them, and after close() nothing reaches the device."""

    def __init__(self, backend, nominal_watts=0):
        self.backend = backend
        self._lock = threading.RLock()
        self.closed = False
        self._closing = False             # set by close() without the lock: stops a test start
        try:
            self.nodes, self.fields, self.info = backend.describe()
            self.capacity_mode = self._read_capacity_mode()
        except Exception:
            backend.close()
            raise
        self.nominal_watts = nominal_watts
        self.readings = model.build_readings(self.fields, nominal_watts, self.capacity_mode)
        self.by_key = {r.key: r for r in self.readings}
        self._status = self._status_collection()
        self.test_field = self._writable_test_field()

    def _writable_test_field(self):
        """The Test field behind the 'self_test' reading when UPS Tray may write it: alone in
        its feature report, so that a write cannot change anything else. None otherwise."""
        r = self.by_key.get("self_test")
        f = r.field if r else None
        if f is None or f.kind != "value":
            return None
        if any((g.page, g.usage) != (f.page, f.usage) for g in self.fields if g.report_id == f.report_id):
            return None
        return f

    @property
    def handle(self):
        """The open device handle (for device-removal notices), or None."""
        return None if self.closed else getattr(self.backend, "handle", None)

    def identity(self):
        """Which UPS this is, to tie a confirmed command to it: the same UPS after a USB
        drop-out matches, another UPS does not."""
        i = self.info
        return (i.vendor_id, i.product_id, i.serial or getattr(self.backend, "path", ""), i.model)

    def _read_capacity_mode(self):
        f = next((f for f in self.fields if f.kind == "value" and (f.page, f.usage) == (BATTERY, 0x2C)), None)
        if f is None:
            return None
        rep = self.backend.read_reports({f.report_id}).get(f.report_id)
        return self.backend.value(rep, f) if rep else None

    def _status_collection(self):
        """(report_id, link, [(page, usage)]) of the PresentStatus bits to use: the one in
        PowerSummary if there is one, else the first PresentStatus, else any buttons."""
        buttons = [f for f in self.fields if f.kind == "button"]
        if not buttons:
            return None
        def score(f):
            return (0 if f.path[-2:] == ("PowerSummary", "PresentStatus") else
                    1 if f.where == "PresentStatus" else 2, f.link)
        best = min(buttons, key=score)
        members = [(f.page, f.usage) for f in buttons if f.link == best.link and f.report_id == best.report_id]
        return best.report_id, best.link, members

    def tray_readings(self):
        return [r for r in self.readings if r.tray]

    def snapshot(self, keys=None, low_charge=0):
        with self._lock:
            if self.closed:
                return Snapshot(time=time.time(), status=model.Status(ok=False))
            return self._snapshot(keys, low_charge)

    def _snapshot(self, keys, low_charge):
        if keys is None:
            wanted = list(self.readings)
        else:
            wanted = [self.by_key[k] for k in keys if k in self.by_key]
            if low_charge and "charge" in self.by_key and self.by_key["charge"] not in wanted:
                wanted.append(self.by_key["charge"])
            for r in list(wanted):
                for dep in r.deps:
                    if dep in self.by_key and self.by_key[dep] not in wanted:
                        wanted.insert(0, self.by_key[dep])
        rids = {r.field.report_id for r in wanted if r.field is not None}
        if self._status:
            rids.add(self._status[0])
        reports = self.backend.read_reports(rids)
        values = {}
        for r in wanted:
            if r.field is None:
                continue
            rep = reports.get(r.field.report_id)
            raw = self.backend.value(rep, r.field) if rep else None
            values[r.key] = model.physical(r, raw)
        for r in wanted:
            if r.kind == "derived" and r.derive:
                try:
                    values[r.key] = r.derive(values)
                except Exception:
                    values[r.key] = None
        status = model.Status(ok=False)
        if self._status:
            rid, link, members = self._status
            rep = reports.get(rid)
            if rep:
                on = set()
                for page in sorted({p for p, _ in members}):
                    usages = self.backend.buttons(rep, page, link) or []
                    on.update(model.usage_name(page, u) for u in usages)
                known = frozenset(model.usage_name(p, u) for p, u in members)
                status = model.Status(ok=True, flags=frozenset(on & known), known=known)
        else:
            status = model.Status(ok=any(v is not None for v in values.values()))
        charge = values.get("charge")
        if status.ok and low_charge and charge is not None and charge <= low_charge:
            status.charge_low = True
        failed = tuple(sorted(rid for rid, rep in reports.items() if rep is None))
        return Snapshot(time=time.time(), values=values, status=status, failed_reports=failed)

    # -- battery tests ------------------------------------------------------------------
    def test_refusal(self, kind, snap, strict=True):
        """Why a test of this kind cannot start now, as a sentence for the user, or None.
        strict: snap was read for this check, so a missing self_test, charge or load value
        means the UPS did not answer; otherwise missing values are not judged."""
        if self.test_field is None:
            if "self_test" in self.by_key:
                return ("UPS Tray does not start tests on this UPS: its test control shares a USB report "
                        "with other values.")
            return "This UPS does not offer a battery test over USB."
        if snap is None or not snap.status.ok:
            return "The UPS is not answering."
        if snap.status.on_battery:
            return "The UPS is running on battery. Start the test when it is back on mains power."
        if snap.values.get("self_test") == TEST_RUNNING:
            return "A battery test is already running."
        if strict and snap.values.get("self_test") is None:
            return "The UPS did not report its test state just now. Try again in a moment."
        if kind == CALIBRATION:
            if "charge" not in self.by_key or "load" not in self.by_key:
                return "The UPS does not report the battery charge and the load, which a runtime calibration needs."
            charge, load = snap.values.get("charge"), snap.values.get("load")
            if strict and (charge is None or load is None):
                return "The UPS did not report the battery charge and the load just now. Try again in a moment."
            if charge is not None and charge < 99.95:
                return ("The battery is at %s. A runtime calibration needs a full battery: try again when the "
                        "UPS shows 100 %%." % model.format_value(self.by_key["charge"], charge))
            if load is not None and load < 10:
                return ("The load is %s. A runtime calibration needs at least 10 %%: connect more equipment "
                        "(a lamp will do) and try again." % model.format_value(self.by_key["load"], load))
        return None

    def _test_value(self):
        f = self.test_field
        rep = self.backend.read_reports({f.report_id}).get(f.report_id)
        return f.signed(self.backend.value(rep, f)) if rep else None

    def start_test(self, kind, sleep=time.sleep):
        """Start a battery self-test (QUICK_TEST) or a runtime calibration (CALIBRATION) the
        way apcupsd's apctest does: check that the UPS can run it, abort anything running and
        clear the last result, wait until the UPS reports no test, then send the command.
        Raises TestRefused (with a sentence for the user) or OSError."""
        if kind not in (QUICK_TEST, CALIBRATION):
            raise ValueError("not a test: %r" % (kind,))
        with self._lock:
            if self.closed:
                raise TestRefused("The UPS is not connected.")
            reason = self.test_refusal(kind, self._snapshot(["self_test", "charge", "load"], 0))
            if reason:
                raise TestRefused(reason)
            f = self.test_field
            try:
                self.backend.write_test(f, TEST_ABORT)      # in case a calibration runs, as apctest does
            except OSError as e:
                log.info("test: abort before clearing failed (%s); going on", e)
            self.backend.write_test(f, TEST_CLEAR)
            for _ in range(20):
                self._check_not_closing()
                if self._test_value() == NO_TEST:
                    break
                sleep(0.5)
            else:
                raise TestRefused("The UPS did not clear its last test result, so no test was started.")
            # clearing can take seconds: judge mains, charge and load again right before starting
            reason = self.test_refusal(kind, self._snapshot(["self_test", "charge", "load"], 0))
            if reason:
                raise TestRefused(reason)
            self._check_not_closing()
            self.backend.write_test(f, kind)
            log.info("test: %s requested", model.TEST_NAMES[kind].lower())

    def _check_not_closing(self):
        if self._closing:
            raise TestRefused("The UPS was disconnected (or Windows took it for a driver change), so no test "
                              "was started.")

    def abort_test(self):
        """Ask the UPS to stop a running self-test or calibration."""
        with self._lock:
            if self.closed:
                raise TestRefused("The UPS is not connected.")
            if self.test_field is None:
                raise TestRefused(self.test_refusal(TEST_ABORT, None))
            self.backend.write_test(self.test_field, TEST_ABORT)
            log.info("test: abort requested")

    def export(self):
        """Plain-data description plus current raw values (diagnostics and test fixtures)."""
        with self._lock:
            if self.closed:
                raise RuntimeError("the UPS is not connected")
            return self._export()

    def _export(self):
        rids = {f.report_id for f in self.fields}
        reports = self.backend.read_reports(rids)
        out_fields = []
        for f in self.fields:
            rep = reports.get(f.report_id)
            entry = {"kind": f.kind, "page": f.page, "usage": f.usage, "link": f.link,
                     "report_id": f.report_id, "bits": f.bits, "logical_min": f.logical_min,
                     "logical_max": f.logical_max, "units": f.units, "exponent": f.exponent,
                     "physical_min": f.physical_min, "physical_max": f.physical_max,
                     "path": list(f.path), "name": f.name}
            if f.kind == "value":
                entry["raw"] = self.backend.value(rep, f) if rep else None
            out_fields.append(entry)
        buttons_on = {}
        for f in self.fields:
            if f.kind == "button":
                rep = reports.get(f.report_id)
                key = "%d:%d:%d" % (f.report_id, f.page, f.link)
                if key not in buttons_on:
                    buttons_on[key] = self.backend.buttons(rep, f.page, f.link) if rep else None
        info = self.info.__dict__.copy()
        return {"nodes": [n.__dict__ for n in self.nodes], "fields": out_fields,
                "buttons_on": buttons_on, "info": info}

    def close(self):
        self._closing = True              # a test start holding the lock gives up within 0.5 s
        with self._lock:
            if self.closed:
                return
            self.closed = True
            try:
                self.backend.close()
            except Exception:
                pass


class RecordedBackend:
    """A backend replaying Ups.export() output: used by the tests and --demo."""

    def __init__(self, data):
        self.data = data
        self.raw = {}
        self.on = {}
        self.offline = False
        self.writes = []          # Test commands written, in order
        self.reload(data)

    def reload(self, data):
        self.data = data
        self.raw = {(f["report_id"], f["page"], f["usage"], f["link"]): f.get("raw")
                    for f in data["fields"] if f["kind"] == "value"}
        self.on = {tuple(int(x) for x in k.split(":")): v for k, v in data["buttons_on"].items()}

    def set_raw(self, report_id, page, usage, link, raw):
        self.raw[(report_id, page, usage, link)] = raw

    def set_buttons(self, report_id, page, link, usages):
        self.on[(report_id, page, link)] = list(usages)

    def describe(self):
        nodes = [Node(n["page"], n["usage"], n["parent"], n["kind"]) for n in self.data["nodes"]]
        fields = [Field(f["kind"], f["page"], f["usage"], f["link"], f["report_id"], f["bits"],
                        f["logical_min"], f["logical_max"], f["units"], f["exponent"], tuple(f["path"]),
                        f.get("physical_min", 0), f.get("physical_max", 0))
                  for f in self.data["fields"]]
        known = DeviceInfo.__dataclass_fields__
        info = DeviceInfo(**{k: v for k, v in self.data.get("info", {}).items() if k in known})
        return nodes, fields, info

    def read_reports(self, report_ids):
        return {rid: (None if self.offline else ("rep", rid)) for rid in report_ids}

    def value(self, report, f):
        return self.raw.get((report[1], f.page, f.usage, f.link))

    def buttons(self, report, page, link):
        return list(self.on.get((report[1], page, link)) or [])

    def write_test(self, f, command):
        """Records the command and answers like an APC UPS: clear -> no test, start ->
        running, abort -> aborted (when running)."""
        if self.offline:
            raise OSError(31, "the UPS did not take the command")
        self.writes.append(command)
        key = (f.report_id, f.page, f.usage, f.link)
        if command == TEST_CLEAR:
            self.raw[key] = NO_TEST
        elif command == TEST_ABORT:
            if self.raw.get(key) == TEST_RUNNING:
                self.raw[key] = 4
        else:
            self.raw[key] = TEST_RUNNING

    def close(self):
        pass
