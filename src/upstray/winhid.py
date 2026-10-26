"""Access to USB HID devices through hid.dll and SetupAPI (ctypes, no driver).

Reading: device enumeration, attributes, strings, the parsed report descriptor
(caps, value caps, button caps, link collections), HidD_GetFeature and the HidP_*
extractors, all on a handle opened without read or write access.

Writing: exactly one thing, HidDevice.write_test(), which sets the Power Device
Test usage (0x84:0x58) to start, clear or abort a battery test. It refuses every
other value and any feature report that carries more than the Test usage, and it
opens a handle with write access for that one command only. Output reports,
WriteFile and DeviceIoControl are not bound, so the app cannot change a UPS
setting or switch the output off.
"""
import ctypes
import ctypes.wintypes as wt

hid = ctypes.WinDLL("hid.dll", use_last_error=True)
setupapi = ctypes.WinDLL("setupapi.dll", use_last_error=True)
k32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)

HidP_Input = 0
HidP_Output = 1
HidP_Feature = 2

HIDP_STATUS_SUCCESS = 0x00110000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
OPEN_EXISTING = 3

DIGCF_PRESENT = 0x2
DIGCF_DEVICEINTERFACE = 0x10

# The one usage UPS Tray writes, and the values it may write to it: 0 clears the last
# result, 1 starts a quick self-test, 2 a deep test (runtime calibration), 3 aborts.
TEST_PAGE, TEST_USAGE = 0x84, 0x58
TEST_COMMANDS = (0, 1, 2, 3)


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("InterfaceClassGuid", GUID), ("Flags", wt.DWORD),
                ("Reserved", ctypes.c_void_p)]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wt.ULONG), ("VendorID", wt.USHORT), ("ProductID", wt.USHORT),
                ("VersionNumber", wt.USHORT)]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [("Usage", wt.USHORT), ("UsagePage", wt.USHORT),
                ("InputReportByteLength", wt.USHORT), ("OutputReportByteLength", wt.USHORT),
                ("FeatureReportByteLength", wt.USHORT), ("Reserved", wt.USHORT * 17),
                ("NumberLinkCollectionNodes", wt.USHORT),
                ("NumberInputButtonCaps", wt.USHORT), ("NumberInputValueCaps", wt.USHORT),
                ("NumberInputDataIndices", wt.USHORT),
                ("NumberOutputButtonCaps", wt.USHORT), ("NumberOutputValueCaps", wt.USHORT),
                ("NumberOutputDataIndices", wt.USHORT),
                ("NumberFeatureButtonCaps", wt.USHORT), ("NumberFeatureValueCaps", wt.USHORT),
                ("NumberFeatureDataIndices", wt.USHORT)]


class HIDP_VALUE_CAPS(ctypes.Structure):
    _fields_ = [("UsagePage", wt.USHORT), ("ReportID", ctypes.c_ubyte), ("IsAlias", ctypes.c_ubyte),
                ("BitField", wt.USHORT), ("LinkCollection", wt.USHORT), ("LinkUsage", wt.USHORT),
                ("LinkUsagePage", wt.USHORT), ("IsRange", ctypes.c_ubyte),
                ("IsStringRange", ctypes.c_ubyte), ("IsDesignatorRange", ctypes.c_ubyte),
                ("IsAbsolute", ctypes.c_ubyte), ("HasNull", ctypes.c_ubyte),
                ("Reserved", ctypes.c_ubyte), ("BitSize", wt.USHORT), ("ReportCount", wt.USHORT),
                ("Reserved2", wt.USHORT * 5), ("UnitsExp", wt.ULONG), ("Units", wt.ULONG),
                ("LogicalMin", wt.LONG), ("LogicalMax", wt.LONG),
                ("PhysicalMin", wt.LONG), ("PhysicalMax", wt.LONG),
                ("UsageMin", wt.USHORT), ("UsageMax", wt.USHORT),
                ("StringMin", wt.USHORT), ("StringMax", wt.USHORT),
                ("DesignatorMin", wt.USHORT), ("DesignatorMax", wt.USHORT),
                ("DataIndexMin", wt.USHORT), ("DataIndexMax", wt.USHORT)]


class HIDP_BUTTON_CAPS(ctypes.Structure):
    _fields_ = [("UsagePage", wt.USHORT), ("ReportID", ctypes.c_ubyte), ("IsAlias", ctypes.c_ubyte),
                ("BitField", wt.USHORT), ("LinkCollection", wt.USHORT), ("LinkUsage", wt.USHORT),
                ("LinkUsagePage", wt.USHORT), ("IsRange", ctypes.c_ubyte),
                ("IsStringRange", ctypes.c_ubyte), ("IsDesignatorRange", ctypes.c_ubyte),
                ("IsAbsolute", ctypes.c_ubyte), ("ReportCount", wt.USHORT),
                ("Reserved2", wt.USHORT), ("Reserved", wt.ULONG * 9),
                ("UsageMin", wt.USHORT), ("UsageMax", wt.USHORT),
                ("StringMin", wt.USHORT), ("StringMax", wt.USHORT),
                ("DesignatorMin", wt.USHORT), ("DesignatorMax", wt.USHORT),
                ("DataIndexMin", wt.USHORT), ("DataIndexMax", wt.USHORT)]


class HIDP_LINK_COLLECTION_NODE(ctypes.Structure):
    _fields_ = [("LinkUsage", wt.USHORT), ("LinkUsagePage", wt.USHORT), ("Parent", wt.USHORT),
                ("NumberOfChildren", wt.USHORT), ("NextSibling", wt.USHORT),
                ("FirstChild", wt.USHORT), ("Bits", wt.ULONG), ("UserContext", ctypes.c_void_p)]


assert ctypes.sizeof(HIDP_VALUE_CAPS) == 72
assert ctypes.sizeof(HIDP_BUTTON_CAPS) == 72
assert ctypes.sizeof(HIDP_LINK_COLLECTION_NODE) == (24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16)

_PDATA = ctypes.c_void_p

hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(GUID)]
hid.HidD_GetHidGuid.restype = None
hid.HidD_GetAttributes.argtypes = [wt.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetAttributes.restype = wt.BOOLEAN
hid.HidD_GetPreparsedData.argtypes = [wt.HANDLE, ctypes.POINTER(_PDATA)]
hid.HidD_GetPreparsedData.restype = wt.BOOLEAN
hid.HidD_FreePreparsedData.argtypes = [_PDATA]
hid.HidD_FreePreparsedData.restype = wt.BOOLEAN
hid.HidD_GetFeature.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]
hid.HidD_GetFeature.restype = wt.BOOLEAN
hid.HidD_SetFeature.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]
hid.HidD_SetFeature.restype = wt.BOOLEAN
for _name in ("HidD_GetProductString", "HidD_GetManufacturerString", "HidD_GetSerialNumberString"):
    getattr(hid, _name).argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]
    getattr(hid, _name).restype = wt.BOOLEAN
hid.HidD_GetIndexedString.argtypes = [wt.HANDLE, wt.ULONG, ctypes.c_void_p, wt.ULONG]
hid.HidD_GetIndexedString.restype = wt.BOOLEAN
hid.HidP_GetCaps.argtypes = [_PDATA, ctypes.POINTER(HIDP_CAPS)]
hid.HidP_GetCaps.restype = wt.LONG
hid.HidP_GetValueCaps.argtypes = [ctypes.c_int, ctypes.POINTER(HIDP_VALUE_CAPS),
                                  ctypes.POINTER(wt.USHORT), _PDATA]
hid.HidP_GetValueCaps.restype = wt.LONG
hid.HidP_GetButtonCaps.argtypes = [ctypes.c_int, ctypes.POINTER(HIDP_BUTTON_CAPS),
                                   ctypes.POINTER(wt.USHORT), _PDATA]
hid.HidP_GetButtonCaps.restype = wt.LONG
hid.HidP_GetLinkCollectionNodes.argtypes = [ctypes.POINTER(HIDP_LINK_COLLECTION_NODE),
                                            ctypes.POINTER(wt.ULONG), _PDATA]
hid.HidP_GetLinkCollectionNodes.restype = wt.LONG
hid.HidP_GetUsageValue.argtypes = [ctypes.c_int, wt.USHORT, wt.USHORT, wt.USHORT,
                                   ctypes.POINTER(wt.ULONG), _PDATA, ctypes.c_void_p, wt.ULONG]
hid.HidP_GetUsageValue.restype = wt.LONG
hid.HidP_GetUsages.argtypes = [ctypes.c_int, wt.USHORT, wt.USHORT, ctypes.POINTER(wt.USHORT),
                               ctypes.POINTER(wt.ULONG), _PDATA, ctypes.c_void_p, wt.ULONG]
hid.HidP_GetUsages.restype = wt.LONG
hid.HidP_SetUsageValue.argtypes = [ctypes.c_int, wt.USHORT, wt.USHORT, wt.USHORT, wt.ULONG, _PDATA,
                                   ctypes.c_void_p, wt.ULONG]
hid.HidP_SetUsageValue.restype = wt.LONG
hid.HidP_MaxUsageListLength.argtypes = [ctypes.c_int, wt.USHORT, _PDATA]
hid.HidP_MaxUsageListLength.restype = wt.ULONG

setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wt.LPCWSTR, wt.HWND, wt.DWORD]
setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(GUID),
                                                 wt.DWORD, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiEnumDeviceInterfaces.restype = wt.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [ctypes.c_void_p,
                                                      ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
                                                      ctypes.c_void_p, wt.DWORD,
                                                      ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wt.BOOL
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
setupapi.SetupDiDestroyDeviceInfoList.restype = wt.BOOL

k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD,
                            wt.HANDLE]
k32.CreateFileW.restype = wt.HANDLE
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.CloseHandle.restype = wt.BOOL


class HidError(OSError):
    pass


def test_report_alone(entries, report_id):
    """True when feature report `report_id` carries the Test usage and nothing else, so
    writing it cannot touch another value. entries: (page, usage_min, usage_max, report_id)
    of every feature value and button cap."""
    found = False
    for page, lo, hi, rid in entries:
        if rid != report_id:
            continue
        if (page, lo, hi) != (TEST_PAGE, TEST_USAGE, TEST_USAGE):
            return False
        found = True
    return found


def interface_paths():
    """Device interface paths of every present HID collection."""
    guid = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(guid))
    hdev = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None,
                                         DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if not hdev or hdev == INVALID_HANDLE_VALUE:
        return []
    paths = []
    try:
        index = 0
        while True:
            ifd = SP_DEVICE_INTERFACE_DATA()
            ifd.cbSize = ctypes.sizeof(ifd)
            if not setupapi.SetupDiEnumDeviceInterfaces(hdev, None, ctypes.byref(guid), index,
                                                        ctypes.byref(ifd)):
                break
            index += 1
            need = wt.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(ifd), None, 0,
                                                      ctypes.byref(need), None)
            if not need.value:
                continue
            buf = ctypes.create_string_buffer(need.value)
            # cbSize of SP_DEVICE_INTERFACE_DETAIL_DATA_W: 8 on 64-bit, 6 on 32-bit
            ctypes.cast(buf, ctypes.POINTER(wt.DWORD))[0] = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            if setupapi.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(ifd), buf, need, None, None):
                paths.append(ctypes.wstring_at(ctypes.addressof(buf) + 4))
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return paths


def _open(path):
    """Open for feature-report reads. Access 0 is enough for IOCTL_HID_GET_FEATURE
    (FILE_ANY_ACCESS) and never conflicts with other readers; GENERIC_READ is the
    fallback for stacks that insist on it."""
    share = FILE_SHARE_READ | FILE_SHARE_WRITE
    for access in (0, GENERIC_READ):
        h = k32.CreateFileW(path, access, share, None, OPEN_EXISTING, 0, None)
        if h and h != INVALID_HANDLE_VALUE:
            return h
    raise HidError(ctypes.get_last_error(), "cannot open HID device", path)


class HidDevice:
    """One open HID top-level collection with its parsed descriptor (feature side)."""

    def __init__(self, path):
        self.path = path
        self.handle = _open(path)
        self._pdata = _PDATA()
        try:
            attrs = HIDD_ATTRIBUTES()
            attrs.Size = ctypes.sizeof(attrs)
            if not hid.HidD_GetAttributes(self.handle, ctypes.byref(attrs)):
                raise HidError(ctypes.get_last_error(), "HidD_GetAttributes failed", path)
            self.vendor_id, self.product_id, self.version = attrs.VendorID, attrs.ProductID, attrs.VersionNumber
            if not hid.HidD_GetPreparsedData(self.handle, ctypes.byref(self._pdata)):
                raise HidError(ctypes.get_last_error(), "HidD_GetPreparsedData failed", path)
            self.caps = HIDP_CAPS()
            if hid.HidP_GetCaps(self._pdata, ctypes.byref(self.caps)) & 0xFFFFFFFF != HIDP_STATUS_SUCCESS:
                raise HidError(0, "HidP_GetCaps failed", path)
            self.usage_page, self.usage = self.caps.UsagePage, self.caps.Usage
            self.feature_len = self.caps.FeatureReportByteLength
        except Exception:
            self.close()
            raise

    # -- descriptor -----------------------------------------------------------------
    def value_caps(self, report_type=HidP_Feature):
        count = {HidP_Input: self.caps.NumberInputValueCaps, HidP_Output: self.caps.NumberOutputValueCaps,
                 HidP_Feature: self.caps.NumberFeatureValueCaps}[report_type]
        if not count:
            return []
        n = wt.USHORT(count)
        arr = (HIDP_VALUE_CAPS * count)()
        if hid.HidP_GetValueCaps(report_type, arr, ctypes.byref(n), self._pdata) & 0xFFFFFFFF != HIDP_STATUS_SUCCESS:
            return []
        return list(arr)[:n.value]

    def button_caps(self, report_type=HidP_Feature):
        count = {HidP_Input: self.caps.NumberInputButtonCaps, HidP_Output: self.caps.NumberOutputButtonCaps,
                 HidP_Feature: self.caps.NumberFeatureButtonCaps}[report_type]
        if not count:
            return []
        n = wt.USHORT(count)
        arr = (HIDP_BUTTON_CAPS * count)()
        if hid.HidP_GetButtonCaps(report_type, arr, ctypes.byref(n), self._pdata) & 0xFFFFFFFF != HIDP_STATUS_SUCCESS:
            return []
        return list(arr)[:n.value]

    def link_collections(self):
        count = self.caps.NumberLinkCollectionNodes
        if not count:
            return []
        n = wt.ULONG(count)
        arr = (HIDP_LINK_COLLECTION_NODE * count)()
        if hid.HidP_GetLinkCollectionNodes(arr, ctypes.byref(n), self._pdata) & 0xFFFFFFFF != HIDP_STATUS_SUCCESS:
            return []
        return list(arr)[:n.value]

    # -- strings --------------------------------------------------------------------
    def _string(self, func, *args):
        if not self.handle:
            return ""
        buf = ctypes.create_unicode_buffer(256)
        if func(self.handle, *args, buf, ctypes.sizeof(buf)):
            return buf.value.strip()
        return ""

    def product(self):
        return self._string(hid.HidD_GetProductString)

    def manufacturer(self):
        return self._string(hid.HidD_GetManufacturerString)

    def serial(self):
        return self._string(hid.HidD_GetSerialNumberString)

    def indexed_string(self, index):
        if not index:
            return ""
        return self._string(hid.HidD_GetIndexedString, index)

    # -- reports --------------------------------------------------------------------
    @property
    def closed(self):
        return not self.handle or not self._pdata

    def get_feature(self, report_id):
        """Raw feature report (bytes) or None when the device refuses it (or is closed)."""
        if self.closed:
            return None
        buf = (ctypes.c_ubyte * self.feature_len)()
        buf[0] = report_id
        if not hid.HidD_GetFeature(self.handle, buf, self.feature_len):
            return None
        return bytes(buf)

    def usage_value(self, report, page, link, usage, report_type=HidP_Feature):
        """Unsigned raw value of one usage in a report previously read, or None."""
        if self.closed or not report:
            return None
        buf = (ctypes.c_ubyte * len(report)).from_buffer_copy(report)
        value = wt.ULONG(0)
        status = hid.HidP_GetUsageValue(report_type, page, link, usage, ctypes.byref(value),
                                        self._pdata, buf, len(report)) & 0xFFFFFFFF
        return value.value if status == HIDP_STATUS_SUCCESS else None

    def usages_on(self, report, page, link, report_type=HidP_Feature):
        """Usages of buttons that are set in a report, or None on a parse error."""
        if self.closed or not report:
            return None
        maxlen = hid.HidP_MaxUsageListLength(report_type, page, self._pdata)
        if not maxlen:
            return []
        buf = (ctypes.c_ubyte * len(report)).from_buffer_copy(report)
        arr = (wt.USHORT * maxlen)()
        n = wt.ULONG(maxlen)
        status = hid.HidP_GetUsages(report_type, page, link, arr, ctypes.byref(n), self._pdata,
                                    buf, len(report)) & 0xFFFFFFFF
        if status != HIDP_STATUS_SUCCESS:
            return None
        return list(arr)[:n.value]

    def _feature_entries(self):
        caps = self.value_caps(HidP_Feature) + self.button_caps(HidP_Feature)
        return [(c.UsagePage, c.UsageMin, c.UsageMax if c.IsRange else c.UsageMin, c.ReportID) for c in caps]

    def write_test(self, report_id, link, command):
        """Set the Test usage in feature report `report_id`: the only report UPS Tray sends.
        ValueError for any other value or a report that carries more than Test; HidError
        when Windows or the UPS refuses."""
        if type(command) is not int or command not in TEST_COMMANDS:
            raise ValueError("not a test command: %r" % (command,))
        if self.closed:
            raise HidError(0, "the UPS is not open", self.path)
        if not test_report_alone(self._feature_entries(), report_id):
            raise ValueError("feature report %d carries more than the Test usage" % report_id)
        handle = k32.CreateFileW(self.path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                 None, OPEN_EXISTING, 0, None)
        if not handle or handle == INVALID_HANDLE_VALUE:
            raise HidError(ctypes.get_last_error(), "cannot open the UPS for writing", self.path)
        try:
            # Test is alone in its report, so the report is its id and the Test value
            buf = (ctypes.c_ubyte * self.feature_len)()
            buf[0] = report_id
            status = hid.HidP_SetUsageValue(HidP_Feature, TEST_PAGE, link, TEST_USAGE, command, self._pdata,
                                            buf, self.feature_len) & 0xFFFFFFFF
            if status != HIDP_STATUS_SUCCESS:
                raise HidError(0, "HidP_SetUsageValue status 0x%08X" % status, self.path)
            if not hid.HidD_SetFeature(handle, buf, self.feature_len):
                raise HidError(ctypes.get_last_error(), "the UPS did not take the command", self.path)
        finally:
            k32.CloseHandle(handle)

    def close(self):
        if getattr(self, "_pdata", None):
            hid.HidD_FreePreparsedData(self._pdata)
            self._pdata = _PDATA()
        h, self.handle = getattr(self, "handle", None), None
        if h and h != INVALID_HANDLE_VALUE:
            k32.CloseHandle(h)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
