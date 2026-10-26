# Changelog

All notable changes to UPS Tray are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[semantic versioning](https://semver.org/).

## [1.3.0] - 2026-10-26

### Added

- Battery self-test and runtime calibration: a Battery test row in the
  Details window and a Battery test submenu in the tray menu start the UPS's
  own tests, and abort a running one. Every test asks for confirmation, and
  UPS Tray checks the UPS again right before it sends the command (on mains,
  no test running; for a calibration a full battery and at least 10 % load).
  A notification gives the result, also for tests the UPS runs by itself.

### Changed

- For this, UPS Tray now writes to the UPS, and only for this: one value
  (the Test control) in a report that carries nothing else, through a handle
  opened for that one command. Unit tests fail if any other code writes.

### Fixed

- UPS Tray no longer holds Windows up when it removes the UPS or changes its
  driver: it closes the device on Windows' notice and finds it again
  afterwards. Before, its open handle made Windows postpone such a change
  until the next restart.
- Two notifications that arrive together are both shown, one after the
  other; before, the second could replace the first.

## [1.2.0] - 2026-10-24

### Added

- Font settings in Settings > Display, for the numbers and for the labels:
  any installed font family (type to filter the list), weight (Regular,
  SemiBold, Bold, Black) and size, all shown in the live preview.
  **Default fonts** goes back to Bahnschrift Condensed and Segoe UI
  Semibold. A font that is no longer installed falls back to the default.

### Changed

- All labels share one height (the height "VOUT" fits at), so a short
  label such as VIN is no taller than LOAD.
- The Settings window keeps Save and Cancel visible however tall a tab
  gets, and opens a little taller.

## [1.1.0] - 2026-10-24

### Changed

- Tray icons are easier to read: a bold condensed value with firmer edges,
  centred to the pixel, and the same digit height on every icon (a unit sign
  is left out rather than shrinking the digits).
- New default icon style: a small label above each value (VIN, VOUT, LOAD,
  BATT, RUN, TEMP ...), so every icon says what it shows.
- Settings > Display offers three icon styles (value with label, value only,
  or two readings stacked in one icon) with a live preview of your icons.

## [1.0.0] - 2026-10-23

First public release.

### Added

- One notification-area icon per chosen UPS reading (input/output voltage,
  load, load power, battery charge, runtime, battery voltage, temperature,
  frequency, and every other numeric value the UPS reports), drawn at the
  display's icon size with a condensed font.
- Status colors: amber on battery, red when low, grey when the UPS does not
  answer; optional fixed colors per reading and a badge style.
- Tooltips with the reading, the power status and the readings you choose.
- Details window with every value and status bit, live, plus Copy all and a
  JSON device report.
- Settings window: icons and tooltips, order, colors, units, update interval,
  notifications, rated power.
- Optional notifications for on battery, mains restored, battery low and UPS
  not answering.
- Values decoded with the HID unit rules, so no per-model tables; developed on
  an APC Smart-UPS 1500.
- Read-only by design: the HID write calls are not bound, and a test enforces it.
- Command line: --settings, --quit, --version, --selftest, --dump, --out.
- Per-user installer without UAC (all-users optional), optional autostart,
  uninstaller that keeps settings and the log.
