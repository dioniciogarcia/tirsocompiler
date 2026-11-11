"""Self-update logic for Tirso Compiler.

Checks GitHub Releases for a newer version and, on request, downloads
and installs the update zip.

The update zip contains only the program files (app/, requirements.txt,
*.bat).  The data folder (library, BIOS copies, signing key, output
folder) is never touched.

Strategy (folder swap via a tiny Python helper):
  1. Download TirsoCompiler.zip from the release to a temp folder.
  2. Verify SHA-256 against the checksum published in the release body.
  3. Unzip to <program>/.update_staging/.
  4. Write <program>/.apply_update (a small Python script) to disk.
  5. Launch that script detached and exit — it waits for this process to
     die, renames .update_staging → overwrites the live files, and
     relaunches Start Tirso Compiler.bat.
  6. If anything fails at any step, clean up and roll back.

A running build is detected via a lock file; updates are refused while
one is active.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

APP_VERSION = "1.1.0"

GITHUB_API = "https://api.github.com/repos/dioniciogarcia/tirsocompiler/releases/latest"
DOWNLOAD_BASE = "https://github.com/dioniciogarcia/tirsocompiler/releases/latest/download/"
ASSET_NAME = "TirsoCompiler.zip"
# Files/folders inside the zip that are allowed to be updated
UPDATABLE = {"app", "requirements.txt", "Start Tirso Compiler.bat", "Make Installer.bat"}
# Data files/folders that must NEVER be touched by an update
PROTECTED = {"templates", "tools", "installer", ".venv", ".tirso-setup-2.txt"}

_TIMEOUT = 20   # seconds for API/metadata calls
_DL_TIMEOUT = 300  # seconds for the zip download


def _version_tuple(v: str) -> tuple:
    """Turn "1.10.2" into (1, 10, 2) for numeric comparison."""
    parts = re.sub(r"[^0-9.]", "", v).split(".")
    return tuple(int(p) for p in parts if p)


def check_for_update() -> dict:
    """Return info about the latest release.

    Returns:
        {
            "latest_version": str,
            "current_version": str,
            "update_available": bool,
            "release_notes": str,
            "asset_url": str,       # download URL
            "asset_size": int,      # bytes
            "checksum": str | None, # SHA-256 from release body, or None
            "error": str | None,
        }
    """
    try:
        req = urllib.request.Request(
            GITHUB_API,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "TirsoCompiler/%s" % APP_VERSION},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())

        tag = data.get("tag_name", "")
        latest = tag.lstrip("v")
        notes = data.get("body", "")

        # Find the update zip asset
        asset_url = ""
        asset_size = 0
        for a in data.get("assets", []):
            if a.get("name") == ASSET_NAME:
                asset_url = a.get("browser_download_url", "")
                asset_size = a.get("size", 0)
                break

        if not asset_url:
            asset_url = DOWNLOAD_BASE + ASSET_NAME

        # Parse SHA-256 from release body — format: "sha256: <hex>" (case-insensitive)
        checksum = None
        m = re.search(r"sha256[:\s]+([0-9a-f]{64})", notes, re.IGNORECASE)
        if m:
            checksum = m.group(1).lower()

        update_available = bool(latest) and (
            _version_tuple(latest) > _version_tuple(APP_VERSION)
        )

        return {
            "latest_version": latest or "?",
            "current_version": APP_VERSION,
            "update_available": update_available,
            "release_notes": notes,
            "asset_url": asset_url,
            "asset_size": asset_size,
            "checksum": checksum,
            "error": None,
        }
    except Exception as e:
        return {
            "latest_version": "?",
            "current_version": APP_VERSION,
            "update_available": False,
            "release_notes": "",
            "asset_url": "",
            "asset_size": 0,
            "checksum": None,
            "error": str(e),
        }


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 16)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _is_valid_zip(path: Path) -> bool:
    return zipfile.is_zipfile(path)


def _download(url: str, dest: Path, progress: Callable[[int, int], None]):
    """Download url to dest, calling progress(downloaded, total) periodically."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "TirsoCompiler/%s" % APP_VERSION},
    )
    with urllib.request.urlopen(req, timeout=_DL_TIMEOUT) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                progress(done, total)


def apply_update(
    asset_url: str,
    checksum: str | None,
    progress: Callable[[str, int], None],  # (message, pct)
    is_job_running: Callable[[], bool],
) -> dict:
    """Download and stage the update, then launch the apply-script and return.

    Returns {"ok": True} if staging succeeded and the apply-script was launched,
    or {"ok": False, "error": "..."} on failure.
    """
    if is_job_running():
        return {"ok": False, "error": "A build is running. Finish it first."}

    program_dir = Path(sys.argv[0]).resolve().parent.parent  # project root
    staging = program_dir / ".update_staging"
    zip_tmp = program_dir / ".update_download.zip"

    # Clean up any leftover staging from a previous failed attempt
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    if zip_tmp.exists():
        zip_tmp.unlink(missing_ok=True)

    try:
        # 1. Download
        progress("Downloading update…", 0)

        def _prog(done, total):
            if total:
                progress("Downloading… %d%%" % int(done * 100 / total),
                         int(done * 100 / total))

        _download(asset_url, zip_tmp, _prog)
        progress("Download complete. Verifying…", 90)

        # 2. Verify
        if not _is_valid_zip(zip_tmp):
            raise ValueError("Downloaded file is not a valid zip.")
        if checksum:
            actual = _sha256_file(zip_tmp)
            if actual != checksum.lower():
                raise ValueError(
                    "Checksum mismatch (expected %s, got %s)." % (checksum, actual)
                )

        # 3. Unzip to staging
        progress("Unpacking…", 92)
        staging.mkdir(exist_ok=True)
        with zipfile.ZipFile(zip_tmp) as z:
            for member in z.infolist():
                # Safety: skip anything outside UPDATABLE
                top = Path(member.filename).parts[0] if member.filename else ""
                if top not in UPDATABLE and member.filename not in UPDATABLE:
                    continue
                z.extract(member, staging)

        zip_tmp.unlink(missing_ok=True)

        # 4. Write the apply-script (pure stdlib, no venv needed)
        apply_script = program_dir / ".apply_update.py"
        bat = program_dir / "Start Tirso Compiler.bat"
        apply_script.write_text(
            textwrap.dedent("""\
                # Auto-generated by Tirso Compiler updater — do not edit.
                import os, shutil, subprocess, sys, time
                from pathlib import Path

                pid       = int(sys.argv[1])
                prog_dir  = Path(sys.argv[2])
                staging   = prog_dir / ".update_staging"
                bat       = prog_dir / "Start Tirso Compiler.bat"
                backup    = prog_dir / ".update_backup"

                # Wait for the main process to exit (up to 30 s)
                import ctypes
                SYNCHRONIZE = 0x100000
                try:
                    h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
                    if h:
                        ctypes.windll.kernel32.WaitForSingleObject(h, 30000)
                        ctypes.windll.kernel32.CloseHandle(h)
                except Exception:
                    time.sleep(5)

                # Back up current updatable files
                if backup.exists():
                    shutil.rmtree(backup, ignore_errors=True)
                backup.mkdir()
                for name in os.listdir(staging):
                    src = prog_dir / name
                    if src.exists():
                        dst = backup / name
                        if src.is_dir():
                            shutil.copytree(src, dst)
                        else:
                            shutil.copy2(src, dst)

                try:
                    # Copy staged files over live files
                    for name in os.listdir(staging):
                        src = staging / name
                        dst = prog_dir / name
                        if src.is_dir():
                            if dst.exists():
                                shutil.rmtree(dst)
                            shutil.copytree(src, dst)
                        else:
                            shutil.copy2(src, dst)

                    shutil.rmtree(staging, ignore_errors=True)
                    shutil.rmtree(backup, ignore_errors=True)

                    # Relaunch
                    if bat.exists():
                        subprocess.Popen(['cmd', '/c', 'start', '', str(bat)],
                                         shell=False, cwd=str(prog_dir))
                except Exception as e:
                    # Roll back
                    for name in os.listdir(backup):
                        src = backup / name
                        dst = prog_dir / name
                        if src.is_dir():
                            if dst.exists():
                                shutil.rmtree(dst, ignore_errors=True)
                            shutil.copytree(src, dst)
                        else:
                            shutil.copy2(src, dst)
                    shutil.rmtree(staging, ignore_errors=True)
                    shutil.rmtree(backup, ignore_errors=True)
                    # Write a flag so the program shows the error on next start
                    (prog_dir / ".update_failed.txt").write_text(str(e))

                # Clean up self
                try:
                    Path(__file__).unlink(missing_ok=True)
                except Exception:
                    pass
            """),
            encoding="utf-8",
        )

        progress("Launching installer…", 97)

        # 5. Launch apply-script detached (using the system Python, not the venv)
        sys_python = _find_system_python(program_dir)
        subprocess.Popen(
            [sys_python, str(apply_script), str(os.getpid()), str(program_dir)],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 8),
            close_fds=True,
        )

        progress("Done. Restarting…", 100)
        return {"ok": True}

    except Exception as e:
        # Clean up
        if zip_tmp.exists():
            zip_tmp.unlink(missing_ok=True)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        return {"ok": False, "error": str(e)}


def _find_system_python(program_dir: Path) -> str:
    """Return path to a usable Python 3 outside the venv."""
    # Try the same interpreter we're running (may be venv; that's OK for the applier)
    return sys.executable


def check_update_failed(program_dir: Path) -> str | None:
    """Return an error message if the previous update apply failed, then clear it."""
    flag = program_dir / ".update_failed.txt"
    if flag.exists():
        msg = flag.read_text(encoding="utf-8").strip()
        flag.unlink(missing_ok=True)
        return msg
    return None
