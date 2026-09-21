#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "extension" / "helper"))

import trash_helper as helper  # noqa: E402


def write_desktop(path: Path, body: str) -> Path:
    path.write_text("[Desktop Entry]\n" + body, encoding="utf-8")
    return path


class ClassifyTests(unittest.TestCase):
    def test_split_exec_strips_field_codes(self):
        tokens = helper.split_exec("/opt/Foo.AppImage %F")
        self.assertEqual(tokens, ["/opt/Foo.AppImage"])

    def test_first_real_binary_skips_env(self):
        tokens = helper.split_exec("env BAMF_DESKTOP_FILE_HINT=/tmp/x.desktop snap run firefox")
        self.assertEqual(helper.first_real_binary(tokens), "snap")

    def test_appimage_from_exec_and_magic(self):
        with tempfile.TemporaryDirectory() as tmp:
            appimage = Path(tmp) / "Demo.AppImage"
            payload = bytearray(16)
            payload[8:11] = b"AI\x02"
            appimage.write_bytes(payload)
            desktop = write_desktop(
                Path(tmp) / "demo.desktop",
                f"Name=Demo\nExec={appimage} %F\nType=Application\n",
            )
            meta = helper.parse_desktop_file(str(desktop))
            result = helper.inspect_desktop(meta)
            self.assertTrue(result.ok)
            self.assertEqual(result.kind, helper.KIND_APPIMAGE)
            self.assertIn(str(appimage), result.paths)

    def test_appimage_from_x_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            desktop = write_desktop(
                Path(tmp) / "demo.desktop",
                "Name=Demo\nExec=/tmp/Demo.AppImage\nX-AppImage-Version=1\nType=Application\n",
            )
            meta = helper.parse_desktop_file(str(desktop))
            self.assertTrue(helper.desktop_is_appimage(meta))

    def test_snap_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            desktop = write_desktop(
                Path(tmp) / "firefox_firefox.desktop",
                "Name=Firefox\nExec=/usr/bin/snap run firefox\nX-SnapInstanceName=firefox\nType=Application\n",
            )
            result = helper.inspect_desktop_path(str(desktop))
            self.assertFalse(result.ok)
            self.assertEqual(result.kind, helper.KIND_SNAP)
            self.assertTrue(result.blocked)

    def test_flatpak_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            desktop = write_desktop(
                Path(tmp) / "org.mozilla.firefox.desktop",
                "Name=Firefox\nExec=/usr/bin/flatpak run org.mozilla.firefox\nX-Flatpak=org.mozilla.firefox\nType=Application\n",
            )
            result = helper.inspect_desktop_path(str(desktop))
            self.assertFalse(result.ok)
            self.assertEqual(result.kind, helper.KIND_FLATPAK)

    def test_protected_package_names(self):
        self.assertTrue(helper.is_protected_package("gnome-shell", "", "optional"))
        self.assertTrue(helper.is_protected_package("ubuntu-desktop-minimal", "", "optional"))
        self.assertTrue(helper.is_protected_package("hello", "yes", "optional"))
        self.assertTrue(helper.is_protected_package("login", "", "required"))
        self.assertFalse(helper.is_protected_package("vlc", "", "optional"))

    def test_deb_inspect_uses_dpkg_and_protects_system_rdepends(self):
        with tempfile.TemporaryDirectory() as tmp:
            desktop = write_desktop(
                Path(tmp) / "custom.desktop",
                "Name=Custom\nExec=/usr/bin/custom-app\nType=Application\n",
            )
            meta = helper.parse_desktop_file(str(desktop))
            with patch.object(helper, "dpkg_search", return_value="vlc"), patch.object(
                helper, "dpkg_status", return_value=("", "optional")
            ), patch.object(helper, "package_rdepends", return_value=["ubuntu-desktop"]):
                result = helper.inspect_desktop(meta)
            self.assertFalse(result.ok)
            self.assertEqual(result.kind, helper.KIND_SYSTEM)

    def test_deb_inspect_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            desktop = write_desktop(
                Path(tmp) / "vlc.desktop",
                "Name=VLC\nExec=/usr/bin/vlc %U\nType=Application\n",
            )
            meta = helper.parse_desktop_file(str(desktop))
            with patch.object(helper, "dpkg_search", return_value="vlc"), patch.object(
                helper, "dpkg_status", return_value=("", "optional")
            ), patch.object(helper, "package_rdepends", return_value=[]):
                result = helper.inspect_desktop(meta)
            self.assertTrue(result.ok)
            self.assertEqual(result.kind, helper.KIND_DEB)
            self.assertEqual(result.package, "vlc")
            self.assertEqual(result.action, "pkcon-remove")

    def test_unknown_local_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            desktop = write_desktop(
                Path(tmp) / "local.desktop",
                "Name=Local\nExec=/tmp/not-a-real-app\nType=Application\n",
            )
            with patch.object(helper, "dpkg_search", return_value=None), patch.object(
                helper, "resolve_binary", return_value="/tmp/not-a-real-app"
            ):
                result = helper.inspect_desktop_path(str(desktop))
            self.assertFalse(result.ok)
            self.assertEqual(result.kind, helper.KIND_UNKNOWN)

    def test_invalid_app_id(self):
        result = helper.inspect_app_id("not an id")
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "desktop_not_found")

    def test_regular_file_inspect(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.txt"
            path.write_text("hello", encoding="utf-8")
            result = helper.inspect_uri(path.as_uri())
            self.assertTrue(result.ok)
            self.assertEqual(result.kind, helper.KIND_FILE)
            self.assertEqual(result.paths, [str(path)])

    def test_protected_path_blocked(self):
        result = helper.inspect_uri("file:///etc/passwd")
        self.assertFalse(result.ok)
        self.assertEqual(result.kind, helper.KIND_SYSTEM)

    def test_package_name_validation(self):
        result = helper.remove_deb_package("vlc; rm -rf /")
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "invalid_package")

    def test_cli_inspect_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.txt"
            path.write_text("hello", encoding="utf-8")
            from io import StringIO
            from contextlib import redirect_stdout

            buffer = StringIO()
            with redirect_stdout(buffer):
                code = helper.main(["inspect", "--uri", path.as_uri()])
            self.assertEqual(code, 0)
            payload = json.loads(buffer.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["kind"], "file")

    def test_gi_inspect_stdout_is_pure_json(self):
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO

        desktop = Path("/usr/share/applications/org.gnome.Nautilus.desktop")
        if not desktop.is_file():
            self.skipTest("nautilus desktop missing")
        out = StringIO()
        err = StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            helper.main(["inspect", "--app-id", "org.gnome.Nautilus.desktop"])
        stdout = out.getvalue()
        self.assertNotIn("PyGIDeprecationWarning", stdout)
        self.assertTrue(stdout.lstrip().startswith("{"), stdout)
        payload = json.loads(stdout)
        self.assertIn(payload["kind"], {
            helper.KIND_DEB,
            helper.KIND_SYSTEM,
            helper.KIND_SNAP,
            helper.KIND_FLATPAK,
            helper.KIND_UNKNOWN,
        })
        self.assertEqual(err.getvalue().strip(), stdout.strip())


class ApplyTests(unittest.TestCase):
    def test_apply_blocked_result_is_noop(self):
        blocked = helper.InspectResult(
            ok=False,
            kind=helper.KIND_SNAP,
            blocked=True,
            summary="nope",
        )
        applied = helper.apply_inspect(blocked)
        self.assertIs(applied, blocked)

    def test_apply_deb_rejects_protected(self):
        inspected = helper.InspectResult(
            ok=True,
            kind=helper.KIND_DEB,
            package="gnome-shell",
            action="pkcon-remove",
        )
        applied = helper.apply_inspect(inspected)
        self.assertFalse(applied.ok)
        self.assertEqual(applied.kind, helper.KIND_SYSTEM)

    def test_trash_regular_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.txt"
            path.write_text("hello", encoding="utf-8")
            trash_home = Path(tmp) / "xdg"
            inspected = helper.inspect_uri(path.as_uri())
            with patch.dict(os.environ, {"XDG_DATA_HOME": str(trash_home)}):
                applied = helper.apply_inspect(inspected)
            self.assertTrue(applied.ok, applied.summary)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
