"""UPS Tray entry point: pythonw src/upstray_main.pyw, or the frozen UpsTray.exe."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from upstray.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
