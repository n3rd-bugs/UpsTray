"""Find the UPS, describe it once, and read snapshots of the values that are needed.

The Ups class talks to a backend with five calls (describe, read_reports, value,
buttons, close). HidBackend implements them with hid.dll; the unit tests use a
recorded backend, so everything above the backend is tested without hardware.
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

    def close(self):
        pass
