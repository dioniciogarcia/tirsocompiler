"""Tests for the self-update module (app/core/updater.py) and the release notes script.

Run: python -m unittest tests.test_updater -v

A small web server on this computer plays the part of GitHub, so the real download,
size check, checksum check and "is it a Windows program" check are all exercised.
"""
import hashlib
import http.server
import json
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "installer"))

from app.core import updater  # noqa: E402
import publish_release  # noqa: E402

FAKE_EXE = b"MZ" + bytes(range(256)) * (updater.MIN_BYTES // 256 + 8)   # starts with MZ, bigger than the minimum
FAKE_SHA = hashlib.sha256(FAKE_EXE).hexdigest()


class FakeGitHub(http.server.BaseHTTPRequestHandler):
    """/api = the "latest release" answer, /dl/<file> = the release files."""
    api_status = 200
    api_body = b"{}"
    files = {}

    def do_GET(self):
        if self.path == "/api":
            self.send_response(self.api_status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(self.api_body)
        elif self.path.startswith("/dl/") and self.path[4:] in self.files:
            data = self.files[self.path[4:]]
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass


class ServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeGitHub.api_status = 200
        FakeGitHub.files = {"TirsoCompilerUpdate.exe": FAKE_EXE}
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.patches = [patch.object(updater, "ALLOWED_PREFIX", self.base + "/dl/"),
                        patch.object(updater, "downloads_dir", lambda: self.dir)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def release(self, tag="v1.2.0", sha=FAKE_SHA, size=len(FAKE_EXE), with_asset=True, body=None):
        body = body if body is not None else (
            "- Faster games.\n- New cheats.\n\n%s\nSHA-256 checksums:\n```\n%s  TirsoCompilerSetup.exe\n%s  TirsoCompilerUpdate.exe\n```\n"
            % (updater.CHECKSUM_MARKER, "a" * 64, sha))
        assets = [{"name": "TirsoCompilerUpdate.exe", "size": size,
                   "browser_download_url": self.base + "/dl/TirsoCompilerUpdate.exe"}] if with_asset else []
        FakeGitHub.api_body = json.dumps({"tag_name": tag, "body": body, "assets": assets}).encode()

    def check(self):
        with patch.object(updater, "APP_VERSION", "1.1.0"):
            return updater.check_for_update(self.base + "/api")


class TestVersions(unittest.TestCase):
    def test_numbers_not_text(self):
        self.assertTrue(updater.is_newer("3.10", "3.9"))
        self.assertFalse(updater.is_newer("3.9", "3.10"))
        self.assertTrue(updater.is_newer("1.10.0", "1.9.9"))

    def test_equal_and_older(self):
        self.assertFalse(updater.is_newer("1.1", "1.1.0"))
        self.assertFalse(updater.is_newer("1.1.0", "1.1"))
        self.assertFalse(updater.is_newer("1.0.9", "1.1.0"))

    def test_tag_formats(self):
        self.assertEqual(updater.version_tuple("v1.2.3"), (1, 2, 3))
        self.assertEqual(updater.version_tuple("1.2.3-beta"), (1, 2, 3))
        self.assertFalse(updater.is_newer("nonsense", "1.0"))


class TestChecking(ServerCase):
    def test_newer_release(self):
        self.release()
        info = self.check()
        self.assertIsNone(info["error"])
        self.assertTrue(info["update_available"])
        self.assertEqual(info["latest_version"], "1.2.0")
        self.assertEqual(info["checksum"], FAKE_SHA)
        self.assertEqual(info["asset_size"], len(FAKE_EXE))
        self.assertEqual(info["release_notes"], "- Faster games.\n- New cheats.")   # no checksum block shown to the user

    def test_same_version_is_not_an_update(self):
        self.release(tag="v1.1.0")
        self.assertFalse(self.check()["update_available"])

    def test_double_digit_version(self):
        self.release(tag="v1.10.0")
        self.assertTrue(self.check()["update_available"])

    def test_release_without_update_file(self):
        self.release(with_asset=False)
        info = self.check()
        self.assertFalse(info["update_available"])
        self.assertIn("update file", info["error"])

    def test_no_release_yet(self):
        FakeGitHub.api_status = 404
        self.assertIn("No version", self.check()["error"])

    def test_rate_limited(self):
        FakeGitHub.api_status = 403
        self.assertIn("limiting", self.check()["error"])

    def test_nothing_listening(self):
        with patch.object(updater, "APP_VERSION", "1.1.0"):
            info = updater.check_for_update("http://127.0.0.1:9/api")
        self.assertTrue(info["error"])
        self.assertFalse(info["update_available"])

    def test_garbage_answer(self):
        FakeGitHub.api_body = b"<html>not json</html>"
        self.assertTrue(self.check()["error"])

    def test_checksum_parsing(self):
        body = "x\n%s  A.exe\n%s *B.exe\n" % ("1" * 64, "2" * 64)
        self.assertEqual(updater.parse_checksums(body), {"A.exe": "1" * 64, "B.exe": "2" * 64})


class TestVerify(ServerCase):
    def write(self, data, name="f.exe"):
        p = self.dir / name
        p.write_bytes(data)
        return p

    def test_good_file(self):
        updater.verify_file(self.write(FAKE_EXE), len(FAKE_EXE), FAKE_SHA)

    def test_wrong_size(self):
        with self.assertRaisesRegex(ValueError, "wrong size"):
            updater.verify_file(self.write(FAKE_EXE), len(FAKE_EXE) + 1, FAKE_SHA)

    def test_too_small(self):
        small = b"MZ" + b"0" * 1000
        with self.assertRaisesRegex(ValueError, "unexpected size"):
            updater.verify_file(self.write(small), len(small), hashlib.sha256(small).hexdigest())

    def test_not_a_windows_program(self):
        data = b"PK" + FAKE_EXE[2:]
        with self.assertRaisesRegex(ValueError, "not a Windows program"):
            updater.verify_file(self.write(data), len(data), hashlib.sha256(data).hexdigest())

    def test_wrong_checksum(self):
        with self.assertRaisesRegex(ValueError, "checksum"):
            updater.verify_file(self.write(FAKE_EXE), len(FAKE_EXE), "0" * 64)

    def test_no_checksum_published(self):
        with self.assertRaisesRegex(ValueError, "checksum"):
            updater.verify_file(self.write(FAKE_EXE), len(FAKE_EXE), None)


class TestDownload(ServerCase):
    def test_download_reports_progress(self):
        seen = []
        updater.download(self.base + "/dl/TirsoCompilerUpdate.exe", self.dir / "u.exe", len(FAKE_EXE), seen.append)
        self.assertEqual((self.dir / "u.exe").read_bytes(), FAKE_EXE)
        self.assertTrue(seen and max(seen) <= 99)
        self.assertFalse((self.dir / "u.exe.part").exists())

    def test_only_our_own_repo(self):
        with self.assertRaisesRegex(ValueError, "own release files"):
            updater.download("https://evil.example.com/TirsoCompilerUpdate.exe", self.dir / "u.exe", 10, lambda p: None)
        self.assertFalse((self.dir / "u.exe").exists())

    def test_bigger_than_announced_is_stopped(self):
        with self.assertRaisesRegex(ValueError, "bigger"):
            updater.download(self.base + "/dl/TirsoCompilerUpdate.exe", self.dir / "u.exe", 1000, lambda p: None)
        self.assertFalse((self.dir / "u.exe").exists())
        self.assertFalse((self.dir / "u.exe.part").exists())


class TestApplyUpdate(ServerCase):
    def setUp(self):
        super().setUp()
        self.release()
        self.info = self.check()
        self.started = []
        self.msgs = []
        p = patch.object(updater, "can_self_update", lambda: True)
        p.start()
        self.addCleanup(p.stop)

    def apply(self, info=None, busy=lambda: None):
        return updater.apply_update(self.info if info is None else info, lambda m, p: self.msgs.append((m, p)),
                                    busy, launch=self.started.append)

    def test_full_success(self):
        r = self.apply()
        self.assertEqual(r, {"ok": True})
        cmd = self.started[0]
        self.assertTrue(cmd[0].endswith("TirsoCompilerUpdate.exe"))
        for flag in ("/SILENT", "/NORESTART", "/CLOSEAPPLICATIONS", "/relaunch=1"):
            self.assertIn(flag, cmd)
        self.assertEqual((self.dir / "TirsoCompilerUpdate.exe").read_bytes(), FAKE_EXE)
        self.assertEqual(self.msgs[-1][1], 100)

    def test_refused_while_a_build_runs(self):
        r = self.apply(busy=lambda: "An app is being built.")
        self.assertFalse(r["ok"])
        self.assertIn("being built", r["error"])
        self.assertEqual(self.started, [])
        self.assertEqual(list(self.dir.iterdir()), [])      # nothing was even downloaded

    def test_build_started_during_download(self):
        answers = iter([None, "A build started."])
        r = self.apply(busy=lambda: next(answers))
        self.assertFalse(r["ok"])
        self.assertEqual(self.started, [])
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_wrong_checksum_is_thrown_away(self):
        r = self.apply(info=dict(self.info, checksum="0" * 64))
        self.assertFalse(r["ok"])
        self.assertIn("checksum", r["error"])
        self.assertEqual(self.started, [])
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_wrong_size_is_thrown_away(self):
        r = self.apply(info=dict(self.info, asset_size=len(FAKE_EXE) - 5))
        self.assertFalse(r["ok"])
        self.assertEqual(self.started, [])
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_not_a_windows_program(self):
        bad = b"ZZ" + FAKE_EXE[2:]
        FakeGitHub.files["TirsoCompilerUpdate.exe"] = bad
        r = self.apply(info=dict(self.info, checksum=hashlib.sha256(bad).hexdigest()))
        self.assertFalse(r["ok"])
        self.assertIn("not a Windows program", r["error"])
        self.assertEqual(self.started, [])

    def test_download_from_another_site_refused(self):
        r = self.apply(info=dict(self.info, asset_url="https://evil.example.com/TirsoCompilerUpdate.exe"))
        self.assertFalse(r["ok"])
        self.assertEqual(self.started, [])

    def test_no_internet_during_download(self):
        FakeGitHub.files = {}
        r = self.apply()
        self.assertFalse(r["ok"])
        self.assertEqual(self.started, [])

    def test_not_enough_disk_space(self):
        with patch.object(updater.shutil, "disk_usage", lambda p: type("U", (), {"free": 1000})()):
            r = self.apply()
        self.assertFalse(r["ok"])
        self.assertIn("disk space", r["error"])
        self.assertEqual(self.started, [])

    def test_nothing_to_install(self):
        r = self.apply(info=dict(self.info, update_available=False))
        self.assertFalse(r["ok"])
        self.assertEqual(self.started, [])

    def test_missing_checksum_refused(self):
        r = self.apply(info=dict(self.info, checksum=None))
        self.assertFalse(r["ok"])
        self.assertEqual(self.started, [])

    def test_folder_copy_cannot_update_itself(self):
        with patch.object(updater, "can_self_update", lambda: False):
            r = self.apply()
        self.assertFalse(r["ok"])
        self.assertIn("runs from a folder", r["error"])
        self.assertEqual(self.started, [])

    def test_old_installers_are_cleaned_up(self):
        (self.dir / "old.exe").write_bytes(b"x")
        updater.cleanup_downloads()
        self.assertEqual(list(self.dir.iterdir()), [])


class TestNeverTouchesData(unittest.TestCase):
    def test_installer_script_leaves_user_data_alone(self):
        iss = (ROOT / "installer" / "TirsoCompiler.iss").read_text(encoding="utf-8")
        # the installer only writes to {app}; the data folder is %LOCALAPPDATA%\TirsoCompiler
        self.assertNotIn("{localappdata}\\TirsoCompiler", iss.replace("{localappdata}\\Programs\\", ""))
        self.assertIn("RestartApplications=no", iss)
        self.assertIn("{param:relaunch|0}", iss)

    def test_update_command_is_silent_and_never_asks_for_a_restart(self):
        cmd = updater.installer_command(Path("x.exe"), Path("log.txt"))
        self.assertIn("/NORESTART", cmd)


class TestReleaseNotes(unittest.TestCase):
    README = "INTRO\n\nCHANGELOG\n=========\n## 1.2.0\n- new thing\n- other thing\n\n## 1.1.0\n- old thing\n"

    def test_section_for_version(self):
        self.assertEqual(publish_release.changelog_section(self.README, "1.2.0"), "- new thing\n- other thing")
        self.assertEqual(publish_release.changelog_section(self.README, "1.1.0"), "- old thing")
        self.assertEqual(publish_release.changelog_section(self.README, "1.10.0"), "")
        self.assertEqual(publish_release.changelog_section(self.README, "1.2"), "")   # 1.2 is not 1.2.0

    def test_readme_has_notes_for_the_current_version(self):
        readme = (ROOT / "README.txt").read_text(encoding="utf-8")
        notes = publish_release.changelog_section(readme, updater.APP_VERSION)
        self.assertTrue(notes, "README.txt needs a '## %s' changelog section" % updater.APP_VERSION)
        self.assertNotIn("INSTALL", notes)      # the section must stop at the next heading

    def test_version_in_code_is_plain(self):
        self.assertRegex(updater.APP_VERSION, r"^\d+(\.\d+)+$")
        text = (ROOT / "app" / "core" / "updater.py").read_text(encoding="utf-8")
        self.assertTrue(re.search(r'^APP_VERSION\s*=\s*"[^"]+"', text, re.M))


if __name__ == "__main__":
    unittest.main()
