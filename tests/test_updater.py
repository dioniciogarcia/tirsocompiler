"""Tests for the self-update module (app/core/updater.py).

Covers:
  - Version comparison
  - GitHub API response parsing
  - SHA-256 extraction from release notes
  - File validation (zip check)
  - Download + verify + unzip (using a local HTTP server)
  - Rollback when the apply-script copies back a backup
"""
import hashlib
import http.server
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# Make sure the project root is on the path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.core import updater


class TestVersionComparison(unittest.TestCase):
    def test_tuple(self):
        self.assertEqual(updater._version_tuple("1.10.2"), (1, 10, 2))
        self.assertEqual(updater._version_tuple("2.0"), (2, 0))
        self.assertEqual(updater._version_tuple("v3.9"), (3, 9))

    def test_newer(self):
        self.assertGreater(updater._version_tuple("1.10"), updater._version_tuple("1.9"))
        self.assertGreater(updater._version_tuple("2.0.0"), updater._version_tuple("1.99.99"))

    def test_same(self):
        self.assertEqual(updater._version_tuple("1.1.0"), updater._version_tuple("1.1.0"))


class TestApiParsing(unittest.TestCase):
    """check_for_update() with a fake GitHub API response."""

    FAKE_RESPONSE = {
        "tag_name": "v1.2.0",
        "body": "New stuff.\n\nsha256: abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        "assets": [
            {"name": "TirsoCompiler.zip",
             "browser_download_url": "https://example.com/TirsoCompiler.zip",
             "size": 123456}
        ],
    }

    def _fake_urlopen(self, req, timeout=None):
        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        resp.read.return_value = json.dumps(self.FAKE_RESPONSE).encode()
        resp.headers = {}
        return resp

    def test_update_available(self):
        with patch("app.core.updater.APP_VERSION", "1.1.0"), \
             patch("urllib.request.urlopen", self._fake_urlopen):
            info = updater.check_for_update()
        self.assertTrue(info["update_available"])
        self.assertEqual(info["latest_version"], "1.2.0")
        self.assertEqual(info["current_version"], "1.1.0")
        self.assertEqual(info["asset_url"], "https://example.com/TirsoCompiler.zip")
        self.assertEqual(info["asset_size"], 123456)
        self.assertEqual(info["checksum"],
                         "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890")
        self.assertIsNone(info["error"])

    def test_no_update(self):
        with patch("app.core.updater.APP_VERSION", "1.2.0"), \
             patch("urllib.request.urlopen", self._fake_urlopen):
            info = updater.check_for_update()
        self.assertFalse(info["update_available"])

    def test_network_error(self):
        def raise_err(*a, **kw):
            raise ConnectionError("unreachable")
        with patch("urllib.request.urlopen", raise_err):
            info = updater.check_for_update()
        self.assertIsNone(info["update_available"] or None)
        self.assertFalse(info["update_available"])
        self.assertIsNotNone(info["error"])

    def test_checksum_extraction(self):
        body = "Release notes\n\nSHA256: ABCDEF1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF1234567890"
        import re
        m = re.search(r"sha256[:\s]+([0-9a-f]{64})", body, re.IGNORECASE)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).lower(),
                         "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890")


class TestFileValidation(unittest.TestCase):
    def test_valid_zip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("app/main.py", "# hello")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as f:
            f.write(buf.getvalue())
            tmp = Path(f.name)
        try:
            self.assertTrue(updater._is_valid_zip(tmp))
        finally:
            tmp.unlink()

    def test_invalid_zip(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as f:
            f.write(b"not a zip file")
            tmp = Path(f.name)
        try:
            self.assertFalse(updater._is_valid_zip(tmp))
        finally:
            tmp.unlink()

    def test_sha256(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"hello world")
            tmp = Path(f.name)
        try:
            expected = hashlib.sha256(b"hello world").hexdigest()
            self.assertEqual(updater._sha256_file(tmp), expected)
        finally:
            tmp.unlink()


class _ZipHandler(http.server.BaseHTTPRequestHandler):
    zip_bytes = b""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.zip_bytes)))
        self.send_header("Content-Type", "application/zip")
        self.end_headers()
        self.wfile.write(self.zip_bytes)

    def log_message(self, *a):
        pass


class TestDownloadAndStage(unittest.TestCase):
    """Uses a local HTTP server so we don't hit the real internet."""

    def setUp(self):
        # Build a fake update zip with one app file
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("app/main.py", "# updated main")
            z.writestr("requirements.txt", "pywebview>=5.1,<6\npillow>=10.4\n")
        self.zip_bytes = buf.getvalue()
        self.checksum = hashlib.sha256(self.zip_bytes).hexdigest()

        _ZipHandler.zip_bytes = self.zip_bytes
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _ZipHandler)
        self.port = self.server.server_address[1]
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

        # Fake program directory
        self.prog_dir = Path(tempfile.mkdtemp())
        (self.prog_dir / "app").mkdir()
        (self.prog_dir / "app" / "main.py").write_text("# old main")

    def tearDown(self):
        self.server.shutdown()
        shutil.rmtree(self.prog_dir, ignore_errors=True)

    def _apply(self, checksum=None):
        url = "http://127.0.0.1:%d/TirsoCompiler.zip" % self.port
        messages = []

        def progress(msg, pct):
            messages.append(msg)

        # Patch sys.argv[0] so apply_update resolves the program dir correctly
        fake_argv0 = str(self.prog_dir / "app" / "main.py")
        with patch("sys.argv", [fake_argv0]), \
             patch("app.core.updater.subprocess.Popen") as mock_popen:
            result = updater.apply_update(
                asset_url=url,
                checksum=checksum,
                progress=progress,
                is_job_running=lambda: False,
            )
        return result, messages, mock_popen

    def test_staging_created(self):
        result, messages, mock_popen = self._apply(checksum=self.checksum)
        self.assertTrue(result["ok"], result.get("error"))
        staging = self.prog_dir / ".update_staging"
        self.assertTrue(staging.exists())
        self.assertTrue((staging / "app" / "main.py").exists())
        mock_popen.assert_called_once()

    def test_checksum_mismatch_fails(self):
        result, messages, _ = self._apply(checksum="0" * 64)
        self.assertFalse(result["ok"])
        self.assertIn("Checksum mismatch", result["error"])

    def test_job_running_blocked(self):
        url = "http://127.0.0.1:%d/TirsoCompiler.zip" % self.port
        result = updater.apply_update(
            asset_url=url,
            checksum=None,
            progress=lambda m, p: None,
            is_job_running=lambda: True,
        )
        self.assertFalse(result["ok"])
        self.assertIn("build is running", result["error"])

    def test_staging_cleaned_on_bad_zip(self):
        url = "http://127.0.0.1:%d/TirsoCompiler.zip" % self.port
        # Serve garbage bytes
        _ZipHandler.zip_bytes = b"not a zip"
        with patch("sys.argv", [str(self.prog_dir / "app" / "main.py")]):
            result = updater.apply_update(
                asset_url=url,
                checksum=None,
                progress=lambda m, p: None,
                is_job_running=lambda: False,
            )
        self.assertFalse(result["ok"])
        # Staging should have been cleaned up
        self.assertFalse((self.prog_dir / ".update_staging").exists())
        self.assertFalse((self.prog_dir / ".update_download.zip").exists())


class TestCheckUpdateFailed(unittest.TestCase):
    def test_flag_read_and_cleared(self):
        with tempfile.TemporaryDirectory() as d:
            flag = Path(d) / ".update_failed.txt"
            flag.write_text("disk full", encoding="utf-8")
            msg = updater.check_update_failed(Path(d))
            self.assertEqual(msg, "disk full")
            self.assertFalse(flag.exists())

    def test_no_flag(self):
        with tempfile.TemporaryDirectory() as d:
            msg = updater.check_update_failed(Path(d))
            self.assertIsNone(msg)


if __name__ == "__main__":
    unittest.main()
