"""What a HID UPS exposes, independent of how it is read.

Fields come from the parsed report descriptor (feature reports). This module
names them (HID Power Device and Battery System usage tables), turns raw values
into physical values with the HID unit rules, picks the well-known readings
(input voltage, load, charge ...) out of the descriptor, derives the power
status from the PresentStatus bits, and formats values for the tray icons,
tooltips and the details window. It has no Windows dependency, so the unit
tests run it against a recorded descriptor.
"""
import math
from dataclasses import dataclass, field

POWER = 0x84        # HID Power Device page
BATTERY = 0x85      # HID Battery System page
APC = 0xFF86        # APC vendor page

# Collection usages (page 0x84) used to name collection paths.
COLLECTIONS = {
    0x01: "Name", 0x02: "PresentStatus", 0x03: "ChangedStatus", 0x04: "UPS", 0x05: "PowerSupply",
    0x10: "BatterySystem", 0x11: "BatterySystemID", 0x12: "Battery", 0x13: "BatteryID",
    0x14: "Charger", 0x15: "ChargerID", 0x16: "PowerConverter", 0x17: "PowerConverterID",
    0x18: "OutletSystem", 0x19: "OutletSystemID", 0x1A: "Input", 0x1B: "InputID",
    0x1C: "Output", 0x1D: "OutputID", 0x1E: "Flow", 0x1F: "FlowID", 0x20: "Outlet",
    0x21: "OutletID", 0x22: "Gang", 0x23: "GangID", 0x24: "PowerSummary", 0x25: "PowerSummaryID",
}

USAGES = {
    (POWER, 0x01): "iName", (POWER, 0x30): "Voltage", (POWER, 0x31): "Current",
    (POWER, 0x32): "Frequency", (POWER, 0x33): "ApparentPower", (POWER, 0x34): "ActivePower",
    (POWER, 0x35): "PercentLoad", (POWER, 0x36): "Temperature", (POWER, 0x37): "Humidity",
    (POWER, 0x38): "BadCount", (POWER, 0x40): "ConfigVoltage", (POWER, 0x41): "ConfigCurrent",
    (POWER, 0x42): "ConfigFrequency", (POWER, 0x43): "ConfigApparentPower",
    (POWER, 0x44): "ConfigActivePower", (POWER, 0x45): "ConfigPercentLoad",
    (POWER, 0x46): "ConfigTemperature", (POWER, 0x47): "ConfigHumidity",
    (POWER, 0x50): "SwitchOnControl", (POWER, 0x51): "SwitchOffControl", (POWER, 0x52): "ToggleControl",
    (POWER, 0x53): "LowVoltageTransfer", (POWER, 0x54): "HighVoltageTransfer",
    (POWER, 0x55): "DelayBeforeReboot", (POWER, 0x56): "DelayBeforeStartup",
    (POWER, 0x57): "DelayBeforeShutdown", (POWER, 0x58): "Test", (POWER, 0x59): "ModuleReset",
    (POWER, 0x5A): "AudibleAlarmControl", (POWER, 0x60): "Present", (POWER, 0x61): "Good",
    (POWER, 0x62): "InternalFailure", (POWER, 0x63): "VoltageOutOfRange",
    (POWER, 0x64): "FrequencyOutOfRange", (POWER, 0x65): "Overload", (POWER, 0x66): "OverCharged",
    (POWER, 0x67): "OverTemperature", (POWER, 0x68): "ShutdownRequested",
    (POWER, 0x69): "ShutdownImminent", (POWER, 0x6B): "SwitchOnOff", (POWER, 0x6C): "Switchable",
    (POWER, 0x6D): "Used", (POWER, 0x6E): "Boost", (POWER, 0x6F): "Buck", (POWER, 0x70): "Initialized",
    (POWER, 0x71): "Tested", (POWER, 0x72): "AwaitingPower", (POWER, 0x73): "CommunicationLost",
    (POWER, 0xFD): "iManufacturer", (POWER, 0xFE): "iProduct", (POWER, 0xFF): "iSerialNumber",
    (BATTERY, 0x29): "RemainingCapacityLimit", (BATTERY, 0x2A): "RemainingTimeLimit",
    (BATTERY, 0x2B): "AtRate", (BATTERY, 0x2C): "CapacityMode", (BATTERY, 0x40): "TerminateCharge",
    (BATTERY, 0x41): "TerminateDischarge", (BATTERY, 0x42): "BelowRemainingCapacityLimit",
    (BATTERY, 0x43): "RemainingTimeLimitExpired", (BATTERY, 0x44): "Charging",
    (BATTERY, 0x45): "Discharging", (BATTERY, 0x46): "FullyCharged", (BATTERY, 0x47): "FullyDischarged",
    (BATTERY, 0x48): "ConditioningFlag", (BATTERY, 0x4B): "NeedReplacement",
    (BATTERY, 0x62): "AverageCurrent", (BATTERY, 0x64): "RelativeStateOfCharge",
    (BATTERY, 0x65): "AbsoluteStateOfCharge", (BATTERY, 0x66): "RemainingCapacity",
    (BATTERY, 0x67): "FullChargeCapacity", (BATTERY, 0x68): "RunTimeToEmpty",
    (BATTERY, 0x69): "AverageTimeToEmpty", (BATTERY, 0x6A): "AverageTimeToFull",
    (BATTERY, 0x6B): "CycleCount", (BATTERY, 0x83): "DesignCapacity", (BATTERY, 0x85): "ManufactureDate",
    (BATTERY, 0x86): "SerialNumber", (BATTERY, 0x87): "iManufacturerName", (BATTERY, 0x88): "iDeviceName",
    (BATTERY, 0x89): "iDeviceChemistry", (BATTERY, 0x8B): "Rechargeable",
    (BATTERY, 0x8C): "WarningCapacityLimit", (BATTERY, 0x8D): "CapacityGranularity1",
    (BATTERY, 0x8E): "CapacityGranularity2", (BATTERY, 0x8F): "iOEMInformation",
    (BATTERY, 0xD0): "ACPresent", (BATTERY, 0xD1): "BatteryPresent", (BATTERY, 0xD2): "PowerFail",
    (BATTERY, 0xD3): "AlarmInhibited", (BATTERY, 0xD5): "ThermistorHot", (BATTERY, 0xD6): "ThermistorCold",
    (BATTERY, 0xD8): "VoltageOutOfRange", (BATTERY, 0xD9): "CurrentOutOfRange",
    (BATTERY, 0xDA): "CurrentNotRegulated", (BATTERY, 0xDB): "VoltageNotRegulated",
}

# Status bits shown in the details window, in display order, with plain names.
STATUS_NAMES = [
    ("ACPresent", "Mains present"), ("Charging", "Charging"), ("Discharging", "Discharging"),
    ("FullyCharged", "Fully charged"), ("BatteryPresent", "Battery present"),
    ("BelowRemainingCapacityLimit", "Below capacity limit"), ("RemainingTimeLimitExpired", "Runtime low"),
    ("ShutdownRequested", "Shutdown requested"), ("ShutdownImminent", "Shutdown imminent"),
    ("NeedReplacement", "Replace battery"), ("Overload", "Overload"),
    ("CommunicationLost", "Communication lost"), ("VoltageNotRegulated", "Voltage not regulated"),
    ("Boost", "Boosting low input"), ("Buck", "Trimming high input"),
    ("InternalFailure", "Internal failure"), ("OverTemperature", "Over temperature"),
]

STRING_USAGES = {(POWER, 0x01), (POWER, 0xFD), (POWER, 0xFE), (POWER, 0xFF), (BATTERY, 0x87),
                 (BATTERY, 0x88), (BATTERY, 0x89), (BATTERY, 0x8F)}
# APC vendor usages that hold string indexes (firmware, USB firmware), not values
APC_STRING_USAGES = {(APC, 0x0042), (APC, 0x0079)}


def usage_name(page, usage):
    name = USAGES.get((page, usage))
    if name:
        return name
    if page == APC:
        return "APC 0x%04X" % usage
    return "0x%04X:0x%04X" % (page, usage)


def _signed4(n):
    n &= 0xF
    return n - 16 if n >= 8 else n


# Exponent signature (length, mass, time, temperature, current) -> unit symbol.
_UNIT_SIGNATURES = {
    (2, 1, -3, 0, -1): "V",
    (0, 0, 0, 0, 1): "A",
    (2, 1, -3, 0, 0): "W",
    (0, 0, 1, 0, 0): "s",
    (0, 0, -1, 0, 0): "Hz",
    (0, 0, 0, 1, 0): "K",
    (0, 0, 1, 0, 1): "As",
    (2, 1, -2, 0, 0): "J",
}


def decode_units(units, exponent_code):
    """(scale, symbol) for a HID unit/exponent pair: physical = raw * scale.

    SI Linear units are expressed in cm, g, s, K, A: a volt is cm^2 g s^-3 A^-1, which
    is 10^7 of those units, so the scale carries 10^(-2*length - 3*mass) besides the
    unit exponent. Unknown or non-SI systems keep just the exponent.
    """
    exp = _signed4(exponent_code)
    system = units & 0xF
    if system == 0 or units == 0:
        return 10.0 ** exp, ""
    sig = tuple(_signed4(units >> (4 * i)) for i in range(1, 6))
    symbol = _UNIT_SIGNATURES.get(sig, "")
    if system == 1:  # SI Linear
        return 10.0 ** (exp - 2 * sig[0] - 3 * sig[1]), symbol
    return 10.0 ** exp, symbol


def decode_hid_date(raw):
    """HID/SBS date word: years since 1980 (7 bits), month (4), day (5) -> 'YYYY-MM-DD'."""
    if not raw:
        return ""
    year, month, day = 1980 + (raw >> 9), (raw >> 5) & 0xF, raw & 0x1F
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return ""
    return "%04d-%02d-%02d" % (year, month, day)


@dataclass
class Node:
    page: int
    usage: int
    parent: int
    kind: int  # 0 physical, 1 application, 2 logical


@dataclass
class Field:
    """One feature-report usage from the descriptor."""
    kind: str                # "value" or "button"
    page: int
    usage: int
    link: int
    report_id: int
    bits: int = 0
    logical_min: int = 0
    logical_max: int = 0
    units: int = 0
    exponent: int = 0
    path: tuple = ()          # collection names from the top, e.g. ("UPS", "Input")
    physical_min: int = 0     # 0/0 = undefined: physical range = logical range
    physical_max: int = 0

    @property
    def name(self):
        return usage_name(self.page, self.usage)

    @property
    def where(self):
        return self.path[-1] if self.path else ""

    def signed(self, raw):
        if raw is None:
            return None
        if self.logical_min < 0 and 0 < self.bits <= 32 and raw & (1 << (self.bits - 1)):
            raw -= 1 << self.bits
        return raw

    def range_ratio(self):
        """Physical units per logical step (1 when no separate physical range is defined)."""
        pmin, pmax, lmin, lmax = self.physical_min, self.physical_max, self.logical_min, self.logical_max
        if (pmin or pmax) and (pmin, pmax) != (lmin, lmax) and lmax != lmin:
            return abs(float(pmax - pmin) / (lmax - lmin))
        return 1.0

    def to_physical_units(self, raw):
        """Signed logical value mapped onto the physical range, when the descriptor
        defines one that differs from the logical range (HID 1.11, 6.2.2.7)."""
        v = self.signed(raw)
        if v is None:
            return None
        pmin, pmax, lmin, lmax = self.physical_min, self.physical_max, self.logical_min, self.logical_max
        if (pmin or pmax) and (pmin, pmax) != (lmin, lmax) and lmax != lmin:
            return pmin + (v - lmin) * float(pmax - pmin) / (lmax - lmin)
        return v


def collection_paths(nodes):
    """Name path of every link collection index (duplicates get #2, #3 ...)."""
    names = []
    for node in nodes:
        base = COLLECTIONS.get(node.usage) if node.page == POWER else None
        names.append(base or "0x%04X:0x%04X" % (node.page, node.usage))
    paths = []
    for i, node in enumerate(nodes):
        parts, j, guard = [], i, 0
        while True:
            parts.append(names[j])
            if j == 0 or guard > 64:
                break
            j, guard = nodes[j].parent, guard + 1
        paths.append(tuple(reversed(parts)))
    seen = {}
    unique = []
    for p in paths:
        seen[p] = seen.get(p, 0) + 1
        unique.append(p if seen[p] == 1 else p[:-1] + ("%s#%d" % (p[-1], seen[p]),))
    return unique


# --------------------------------------------------------------------------------
# Readings
# --------------------------------------------------------------------------------
@dataclass
class Reading:
    """Something the user can show: a decoded field or a value derived from fields."""
    key: str
    name: str
    group: str               # Power, Battery, Settings, Device, Other
    unit: str = ""           # display unit after conversion: V, %, Hz, min, °C, W, A, VA, s, ...
    field: Field = None
    scale: float = 1.0
    kind: str = "number"     # number, date, enum, string, runtime, temperature, derived
    tray: bool = True        # offered as a tray icon
    enum: dict = None
    derive: object = None    # callable(values) -> float or None, for derived readings
    resolution: float = 1.0  # smallest step of the physical value (decimals for display)
    deps: tuple = ()         # keys a derived reading needs in the same snapshot
    label: str = ""          # short caption for the tray icon (VIN, LOAD ...); "" = none

    def decimals(self):
        if self.resolution >= 1:
            return 0
        return min(3, max(0, -int(math.floor(math.log10(self.resolution) + 1e-9))))


def _first(fields, page, usage, where=(), kind="value"):
    """First field with page/usage whose innermost named collection is in `where`
    (tried in order); where=() matches any collection."""
    cands = [f for f in fields if f.kind == kind and f.page == page and f.usage == usage]
    if not where:
        return cands[0] if cands else None
    for w in where:
        for f in cands:
            if f.where == w:
                return f
    return None


def _finest(fields, page, usage, where):
    cands = [f for f in fields if f.kind == "value" and f.page == page and f.usage == usage
             and (not where or f.where in where)]
    if not cands:
        return None
    return min(cands, key=lambda f: decode_units(f.units, f.exponent)[0])


# Captions drawn above the value in the default icon style. Short: about 6 px tall
# at 150 % scaling, so four or five narrow capitals at most.
TRAY_LABELS = {
    "input_voltage": "VIN", "output_voltage": "VOUT", "battery_voltage": "VBAT", "load": "LOAD",
    "input_frequency": "IN HZ", "output_frequency": "HZ", "output_current": "AMPS",
    "apparent_power": "VA", "real_power": "WATT", "charge": "BATT", "runtime": "RUN",
    "temperature": "TEMP",
}

TEST_RESULTS = {1: "Passed", 2: "Warning", 3: "Error", 4: "Aborted", 5: "In progress",
                6: "No test initiated"}
TEST_NAMES = {0: "Battery test", 1: "Battery self-test", 2: "Runtime calibration", 3: "Battery test"}
ALARM_STATES = {1: "Disabled", 2: "Enabled", 3: "Muted"}
CAPACITY_MODES = {0: "mAh", 1: "mWh", 2: "%", 3: "Boolean"}


def build_readings(fields, nominal_watts=0, capacity_mode=None):
    """The well-known readings this descriptor supports, then every other numeric field.

    nominal_watts: the UPS's rated real power, used to derive 'Load power' when the UPS
    does not report watts itself (0 = not set).
    capacity_mode: the UPS's CapacityMode value (0 mAh, 1 mWh, 2 %), read once at connect;
    absolute capacities are turned into a percentage of the full charge capacity.
    """
    readings = []
    used = set()

    def add(key, name, group, f, unit=None, kind="number", tray=True, enum=None):
        if f is None:
            return None
        scale, symbol = decode_units(f.units, f.exponent)
        r = Reading(key=key, name=name, group=group, unit=symbol if unit is None else unit, field=f,
                    scale=scale, kind=kind, tray=tray, enum=enum, resolution=scale * f.range_ratio(),
                    label=TRAY_LABELS.get(key, ""))
        readings.append(r)
        used.add(id(f))
        return r

    v = (POWER, 0x30)
    add("input_voltage", "Input voltage", "Power", _first(fields, *v, ("Input",)))
    add("output_voltage", "Output voltage", "Power", _first(fields, *v, ("Output",)))
    add("battery_voltage", "Battery voltage", "Battery", _first(fields, *v, ("Battery", "PowerSummary")))
    load = add("load", "Load", "Power", _first(fields, POWER, 0x35, ("Output", "PowerSummary", "UPS")), "%")
    add("input_frequency", "Input frequency", "Power", _first(fields, POWER, 0x32, ("Input",)))
    add("output_frequency", "Output frequency", "Power", _first(fields, POWER, 0x32, ("Output",)))
    add("output_current", "Output current", "Power", _first(fields, POWER, 0x31, ("Output",)))
    add("apparent_power", "Apparent power", "Power",
        _first(fields, POWER, 0x33, ("Output", "PowerSummary")), "VA")
    real = add("real_power", "Load power", "Power", _first(fields, POWER, 0x34, ("Output", "PowerSummary")), "W")
    if real is None and load is not None:
        rated = _first(fields, POWER, 0x44, ("Output", "PowerSummary", "UPS"))
        if rated is not None or nominal_watts:
            def derive(values, rated=rated):
                pct = values.get("load")
                watts = values.get("_rated_watts") if rated is not None else nominal_watts
                if pct is None or not watts:
                    return None
                return pct * watts / 100.0
            readings.append(Reading(key="real_power", name="Load power", group="Power", unit="W",
                                    kind="derived", derive=derive, resolution=1,
                                    deps=("load", "_rated_watts"), label=TRAY_LABELS["real_power"]))
            if rated is not None:
                add("_rated_watts", "Rated power", "Settings", rated, "W", tray=False)
    rsoc = _first(fields, BATTERY, 0x64, ("Battery", "PowerSummary"))
    if rsoc is not None:
        add("charge", "Battery charge", "Battery", rsoc, "%")          # RelativeStateOfCharge is a %
    elif capacity_mode in (0, 1) or (capacity_mode is None and _first(fields, BATTERY, 0x2C) is not None):
        # absolute capacities (mAh/mWh), or a mode we could not read: the ratio to the full
        # charge capacity is a percentage in every mode, so it is always safe
        remaining = _first(fields, BATTERY, 0x66, ("Battery", "PowerSummary"))
        full = None
        if remaining is not None:
            full = (_first(fields, BATTERY, 0x67, (remaining.where,))
                    or _first(fields, BATTERY, 0x67, ("Battery", "PowerSummary")))
        if remaining is not None and full is not None:
            unit = {0: "mAh", 1: "mWh"}.get(capacity_mode, "")
            add("_remaining_capacity", "Remaining capacity", "Battery", remaining, unit, tray=False)
            add("_full_capacity", "Full charge capacity", "Battery", full, unit, tray=False)

            def charge_pct(values):
                rem, tot = values.get("_remaining_capacity"), values.get("_full_capacity")
                if rem is None or not tot:
                    return None
                return max(0.0, min(100.0, rem * 100.0 / tot))
            readings.append(Reading(key="charge", name="Battery charge", group="Battery", unit="%",
                                    kind="derived", derive=charge_pct, resolution=0.1,
                                    deps=("_remaining_capacity", "_full_capacity"),
                                    label=TRAY_LABELS["charge"]))
    else:
        add("charge", "Battery charge", "Battery",
            _finest(fields, BATTERY, 0x66, ("Battery", "PowerSummary")), "%")
    add("runtime", "Runtime remaining", "Battery", _first(fields, BATTERY, 0x68, ("Battery", "PowerSummary")),
        "min", kind="runtime")
    add("temperature", "Temperature", "Battery", _first(fields, POWER, 0x36, ("Battery", "PowerSummary", "UPS")),
        "°C", kind="temperature")
    add("battery_nominal_voltage", "Battery nominal voltage", "Settings",
        _first(fields, POWER, 0x40, ("Battery", "PowerSummary")), tray=False)
    add("output_nominal_voltage", "Output nominal voltage", "Settings",
        _first(fields, POWER, 0x40, ("Output",)), tray=False)
    add("high_transfer", "High transfer voltage", "Settings", _first(fields, POWER, 0x54), tray=False)
    add("low_transfer", "Low transfer voltage", "Settings", _first(fields, POWER, 0x53), tray=False)
    add("low_battery_limit", "Low battery warning at", "Settings",
        _first(fields, BATTERY, 0x29, ("PowerSummary", "Battery")), "%", tray=False)
    add("warning_limit", "Battery warning at", "Settings",
        _first(fields, BATTERY, 0x8C, ("PowerSummary", "Battery")), "%", tray=False)
    add("runtime_limit", "Low runtime warning at", "Settings",
        _first(fields, BATTERY, 0x2A, ("PowerSummary", "Battery")), "s", tray=False)
    add("battery_date", "Battery date", "Device", _first(fields, BATTERY, 0x85, ("Battery",)), "",
        kind="date", tray=False)
    add("ups_date", "UPS date", "Device", _first(fields, BATTERY, 0x85, ("PowerSummary", "UPS")), "",
        kind="date", tray=False)
    add("self_test", "Battery test result", "Battery", _first(fields, POWER, 0x58), "", kind="enum",
        tray=False, enum=TEST_RESULTS)
    add("alarm", "Audible alarm", "Settings", _first(fields, POWER, 0x5A, ("PowerSummary", "UPS")), "",
        kind="enum", tray=False, enum=ALARM_STATES)
    add("shutdown_delay", "Shutdown countdown", "Power",
        _first(fields, POWER, 0x57, ("PowerSummary", "Output")), "s", tray=False)

    # every other numeric field, named by where it lives
    for f in fields:
        if (f.kind != "value" or id(f) in used or (f.page, f.usage) in STRING_USAGES
                or (f.page, f.usage) in APC_STRING_USAGES):
            continue
        scale, symbol = decode_units(f.units, f.exponent)
        place = f.where or "UPS"
        key = "x:%04x:%04x:%s" % (f.page, f.usage, "/".join(f.path))
        kind = "date" if (f.page, f.usage) == (BATTERY, 0x85) else "number"
        readings.append(Reading(key=key, name="%s %s" % (place, f.name), group="Other", unit=symbol,
                                field=f, scale=scale, kind=kind, tray=kind == "number",
                                resolution=scale * f.range_ratio()))
    return readings


# --------------------------------------------------------------------------------
# Values and status
# --------------------------------------------------------------------------------
def physical(reading, raw):
    """Raw field value -> the value in the reading's display unit (None if unknown)."""
    if raw is None:
        return None
    f = reading.field
    if reading.kind in ("date", "enum", "string"):
        return f.signed(raw) if f else raw
    raw = f.to_physical_units(raw) if f else raw
    value = raw * reading.scale
    if reading.kind == "runtime":
        return None if raw < 0 else value / 60.0
    if reading.kind == "temperature":
        return value - 273.15 if (f and decode_units(f.units, f.exponent)[1] == "K") else value
    return value


@dataclass
class Status:
    ok: bool = False                     # the UPS answered
    flags: frozenset = frozenset()       # names of the status bits that are set
    known: frozenset = frozenset()       # names of the status bits the UPS reports at all
    charge_low: bool = False             # charge at or below the user's low threshold

    @property
    def on_battery(self):
        if not self.ok:
            return False
        if "ACPresent" in self.known:
            return "ACPresent" not in self.flags
        return "Discharging" in self.flags

    @property
    def low(self):
        # BelowRemainingCapacityLimit is not used: with Windows' HID battery driver attached,
        # Windows rewrites the UPS's capacity limit to just below the current charge so that it
        # gets notified of every change, which keeps that bit flickering on.
        return self.ok and (self.charge_low or bool(self.flags & {"RemainingTimeLimitExpired",
                                                                  "ShutdownImminent"}))

    @property
    def alert(self):
        return self.ok and bool(self.flags & {"Overload", "NeedReplacement", "InternalFailure",
                                              "OverTemperature"})

    @property
    def state(self):
        if not self.ok:
            return "offline"
        if self.on_battery:
            return "low" if self.low else "battery"
        return "alert" if self.alert else "online"

    def summary(self):
        if not self.ok:
            return "UPS not responding"
        if self.on_battery:
            text = "On battery"
            if self.low:
                text += " - LOW"
        else:
            text = "On mains"
            if "Charging" in self.flags:
                text += ", charging"
        extras = [label for name, label in STATUS_NAMES
                  if name in self.flags and name in ("Overload", "NeedReplacement", "InternalFailure",
                                                     "OverTemperature", "ShutdownImminent")]
        if extras:
            text += " (" + ", ".join(extras) + ")"
        return text


# --------------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------------
def _num(value, decimals):
    return ("%%.%df" % decimals) % value


def runtime_text(minutes, style="minutes"):
    total = int(round(minutes))
    if style == "hm" or total >= 120:
        return "%d h %02d min" % divmod(total, 60)
    return "%d min" % total


def to_fahrenheit(celsius):
    return celsius * 9.0 / 5.0 + 32.0


def format_value(reading, value, temperature_unit="C", runtime_style="minutes"):
    """Full text for tooltips and the details window, e.g. '231.8 V' or '1 h 05 min'."""
    if value is None:
        return "-"
    if reading.kind == "date":
        return decode_hid_date(int(value)) or str(int(value))
    if reading.kind == "enum":
        return (reading.enum or {}).get(int(value), str(int(value)))
    if reading.kind == "runtime":
        return runtime_text(value, runtime_style)
    if reading.kind == "temperature":
        if temperature_unit == "F":
            return "%.1f °F" % to_fahrenheit(value)
        return "%.1f °C" % value
    if reading.key == "shutdown_delay" and value < 0:
        return "not counting down"
    decimals = reading.decimals()
    text = _num(value, decimals)
    unit = reading.unit
    if unit == "%":
        return text + " %"
    return (text + " " + unit).strip()


def _short_number(value, decimals=2):
    """At most 4 characters for an arbitrary number, with no more decimals than the
    value's resolution."""
    a = abs(value)
    if a >= 10000:
        return ["%dk" % round(value / 1000.0)]
    for d in range(decimals, -1, -1):
        text = _num(value, d)
        if len(text) <= 4:
            return [text]
    return ["%d" % round(value)]


def tray_texts(reading, value, temperature_unit="C", compact=False):
    """Candidate texts for a tray icon, most informative first; the icon renderer
    uses the first one that fits at a readable size. compact: the icon has a label
    (LOAD, BATT ...), so a % sign would only make the digits smaller."""
    if value is None:
        return ["--"]
    unit = reading.unit
    if compact and unit == "%":
        return ["%d" % round(value)]
    if reading.kind == "runtime":
        m = int(round(value))
        return [str(m)] if m < 1000 else ["%dh" % (m // 60)]
    if reading.kind == "temperature":
        t = to_fahrenheit(value) if temperature_unit == "F" else value
        return ["%d°" % round(t), "%d" % round(t)]
    if unit == "%":
        return ["%d%%" % round(value), "%d" % round(value)]
    if unit in ("V", "Hz", "A"):
        if abs(value) >= 100:
            return ["%d" % round(value)]
        return [_num(value, 1), "%d" % round(value)]
    if unit in ("W", "VA"):
        if abs(value) < 1000:
            return ["%d" % round(value)]
        return ["%.1fk" % (value / 1000.0), "%dk" % round(value / 1000.0)]
    return _short_number(value, reading.decimals())
