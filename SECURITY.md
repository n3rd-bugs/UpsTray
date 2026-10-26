# Security policy

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub:
**Security > Report a vulnerability** on
<https://github.com/n3rd-bugs/UpsTray/security/advisories/new>.
Do not open a public issue for a vulnerability. If that form is not
available, open an issue titled "Security contact request" without any
details, and you will be contacted.

Include the version (`UpsTray.exe --version --out <file>`), what you did,
what happened, and what you expected. Review logs and self-test output for
personal information or device identifiers before sharing them. This is a
small project maintained in spare time: expect a first answer within a
couple of weeks, and a fix as fast as its severity calls for.

## Threat model in short

UPS Tray runs as the signed-in user with normal rights. It reads readings
from an APC UPS connected by USB through Windows' `hid.dll`. It writes to
the UPS only to start, clear or abort a battery self-test or runtime
calibration, after the user confirms it: one value (the Power Device Test
usage, 0x84:0x58, set to 0-3) in a feature report that must carry nothing
else, through a handle opened for that one command. It never changes the
UPS's configuration or switches its output. It has no network access and
needs no elevation, helper executable, scheduled task, service or driver.

Settings and the log are stored in `%LOCALAPPDATA%\UpsTray`. Optional
autostart uses the current user's `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
value named `UpsTray`. The installer defaults to a per-user installation;
uninstall removes that value and keeps settings and the log.

In scope, for example:

- A way to make UPS Tray write anything to the UPS other than a confirmed
  battery-test command, or change a UPS setting or its output.
- Unsafe handling of USB HID reports, saved settings or command-line input.
- Unexpected network activity or a need for elevated rights.
- A way to make the installer or uninstaller modify unrelated files or
  registry values.

Programs already running as the same user can change that user's settings,
log and autostart entry. The release binaries are not code-signed.
