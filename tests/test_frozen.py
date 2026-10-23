# -*- coding: utf-8 -*-
"""Checks on the built UpsTray.exe that are safe on any machine, CI included.

Only --version and --selftest are run, each with --out (a windowed exe has
no console). No UPS is required. LOCALAPPDATA is redirected to a private
folder inside the repository for these child processes.

    UPSTRAY_FROZEN_EXE      path of UpsTray.exe
                            (default: build\\app\\UpsTray\\UpsTray.exe)
    UPSTRAY_REQUIRE_FROZEN  1 = fail instead of skipping when it is missing
"""
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src", "upstray", "__init__.py")
EXE = os.environ.get("UPSTRAY_FROZEN_EXE") or os.path.join(
    ROOT, "build", "app", "UpsTray", "UpsTray.exe")
REQUIRE = os.environ.get("UPSTRAY_REQUIRE_FROZEN") == "1"
TMP = os.path.join(ROOT, "_testtmp", "frozen_%d" % os.getpid())
CREATE_NO_WINDOW = 0x08000000


def source_version():
    with open(SRC, "r", encoding="utf-8") as fh:
        m = re.search(r'^VERSION\s*=\s*"(\d+\.\d+\.\d+)"\s*$', fh.read(), re.M)
    if not m:
        raise AssertionError('VERSION = "x.y.z" not found in %s' % SRC)
    return m.group(1)


class FrozenExeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(EXE):
            if REQUIRE:
                raise AssertionError("frozen exe not found: %s" % EXE)
            raise unittest.SkipTest("frozen exe not built: %s" % EXE)
        os.makedirs(os.path.join(TMP, "localappdata"), exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP, ignore_errors=True)

    def run_exe(self, mode, name):
        """Run EXE <mode> --out <file>; return (exit code, output text)."""
        self.assertIn(mode, ("--version", "--selftest"))
        out = os.path.join(TMP, name + ".txt")
        if os.path.exists(out):
            os.remove(out)
        env = dict(os.environ)
        env["LOCALAPPDATA"] = os.path.join(TMP, "localappdata")
        p = subprocess.run([EXE, mode, "--out", out], env=env, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180,
                           creationflags=CREATE_NO_WINDOW)
        text = ""
        if os.path.exists(out):
            with open(out, "r", encoding="utf-8") as fh:
                text = fh.read()
        return p.returncode, text

    def test_version(self):
        rc, text = self.run_exe("--version", "version")
        self.assertEqual(rc, 0, text)
        self.assertEqual(text.strip(), "UpsTray %s" % source_version())

    def test_selftest(self):
        rc, text = self.run_exe("--selftest", "selftest")
        self.assertEqual(rc, 0, text)
        result = json.loads(text)
        self.assertIsInstance(result, dict, text)
        self.assertIs(result.get("ok"), True, text)
        self.assertEqual(result.get("version"), source_version(), text)
        self.assertIsInstance(result.get("checks"), dict, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
