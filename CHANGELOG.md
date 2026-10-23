# Changelog

All notable changes to UPS Tray are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[semantic versioning](https://semver.org/).

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
