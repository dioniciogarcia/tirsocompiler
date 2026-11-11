"""Self-update for the installed Tirso Compiler (the Windows Setup version).

How it works
  1. The GitHub build publishes a Release for every version. Besides the full
     installer (TirsoCompilerSetup.exe) it publishes TirsoCompilerUpdate.exe:
     the program and the Android templates, without the big rarely-changing
     tools (Java, Apktool, signer), which stay as they are on this PC.
  2. "Check for updates" asks GitHub for the newest release and compares the
     version numbers as numbers (1.10 is newer than 1.9).
  3. "Update now" downloads TirsoCompilerUpdate.exe into Tirso's own data
     folder, checks it (it comes from our own repo's release address, the size
     matches, the SHA-256 checksum published in the release matches and the file
     really is a Windows program), then starts it silently and closes Tirso.
     The installer replaces the program files and opens Tirso again.

Everything that can go wrong before the installer starts (no internet, wrong
size, wrong checksum, not a Windows program, not enough disk space, a build
running) stops the update with a message and changes nothing.

Your data (library, BIOS copies, signing key, settings, output folder) lives in
%LOCALAPPDATA%\\TirsoCompiler. The installer only writes to the program folder,
so an update never touches your data.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

# Raise this number to publish a new version (see README, "Publishing a new version").
APP_VERSION = "1.1.0"

REPO = "dioniciogarcia/tirsocompiler"
GITHUB_API = "https://api.github.com/repos/%s/releases/latest" % REPO
RELEASES_PAGE = "https://github.com/%s/releases/latest" % REPO
SETUP_DOWNLOAD = "https://github.com/%s/releases/latest/download/TirsoCompilerSetup.exe" % REPO
# Downloads are only accepted from our own repository's release files.
ALLOWED_PREFIX = "https://github.com/%s/releases/download/" % REPO
UPDATE_ASSET = "TirsoCompilerUpdate.exe"

MIN_BYTES = 5 * 1024 * 1024          # anything smaller is not a real installer
MAX_BYTES = 1536 * 1024 * 1024       # 1.5 GB safety ceiling
CHECKSUM_MARKER = "<!-- checksums -->"

_API_TIMEOUT = 20
_DL_TIMEOUT = 60

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
DETACHED = (0x00000008 | 0x00000200) if sys.platform == "win32" else 0  # DETACHED_PROCESS | NEW_PROCESS_GROUP


# ---------------------------------------------------------------- versions
def version_tuple(v: str) -> tuple:
    """"v1.10.2" -> (1, 10, 2). Compared as numbers, so 1.10 is newer than 1.9."""
    head = str(v or "").strip().lstrip("vV").split("-")[0].split("+")[0]
    return tuple(int(n) for n in re.findall(r"\d+", head))


def is_newer(latest: str, current: str) -> bool:
    a, b = version_tuple(latest), version_tuple(current)
    if not a:
        return False
    size = max(len(a), len(b))
    return a + (0,) * (size - len(a)) > b + (0,) * (size - len(b))


def can_self_update() -> bool:
    """Only the installed program (Tirso Compiler.exe) can update itself.
    A copy started from "Start Tirso Compiler.bat" has no installer to replace."""
    return bool(getattr(sys, "frozen", False)) and sys.platform == "win32"


# ---------------------------------------------------------------- checking
def parse_checksums(body: str) -> dict:
    """Reads lines like "<64 hex digits>  TirsoCompilerUpdate.exe" out of the release text."""
    found = {}
    for m in re.finditer(r"^\s*([0-9a-fA-F]{64})\s+\*?([\w.\-]+)\s*$", body or "", re.M):
        found[m.group(2)] = m.group(1).lower()
    return found


def clean_notes(body: str) -> str:
    """The release notes without the checksum block at the bottom."""
    return (body or "").split(CHECKSUM_MARKER)[0].strip()


def _result(**kw) -> dict:
    base = {
        "current_version": APP_VERSION, "latest_version": "", "update_available": False,
        "release_notes": "", "asset_url": "", "asset_size": 0, "checksum": None,
        "can_self_update": can_self_update(), "manual_url": RELEASES_PAGE, "setup_url": SETUP_DOWNLOAD,
        "error": None,
    }
    base.update(kw)
    return base


def check_for_update(api_url: str | None = None) -> dict:
    """Asks GitHub for the newest release. Never raises: problems come back in "error"."""
    try:
        req = urllib.request.Request(api_url or GITHUB_API, headers={
            "Accept": "application/vnd.github+json", "User-Agent": "TirsoCompiler/%s" % APP_VERSION})
        with urllib.request.urlopen(req, timeout=_API_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return _result(error="No version has been published on GitHub yet.")
        if e.code in (403, 429):
            return _result(error="GitHub is limiting requests right now. Try again in a few minutes.")
        return _result(error="GitHub answered with an error (%s)." % e.code)
    except Exception as e:  # no internet, timeout, bad answer...
        return _result(error="Couldn't reach GitHub (%s). Check your internet connection." % (e.reason if hasattr(e, "reason") else e))

    try:
        tag = str(data.get("tag_name") or "")
        if not version_tuple(tag):
            return _result(error="The newest release has a version name I don't understand (%s)." % tag)
        latest = ".".join(str(n) for n in version_tuple(tag))
        body = data.get("body") or ""
        url, size = "", 0
        for a in data.get("assets") or []:
            if a.get("name") == UPDATE_ASSET:
                url = a.get("browser_download_url") or ""
                size = int(a.get("size") or 0)
        newer = is_newer(latest, APP_VERSION)
        err = None
        if newer and not url:
            err = "Version %s exists, but its update file isn't on GitHub yet. Try again in a few minutes." % latest
        return _result(latest_version=latest, update_available=newer and not err, release_notes=clean_notes(body),
                       asset_url=url, asset_size=size, checksum=parse_checksums(body).get(UPDATE_ASSET), error=err)
    except Exception as e:
        return _result(error="The answer from GitHub wasn't what I expected (%s)." % e)


# ---------------------------------------------------------------- checking the file
def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_file(path, expected_size: int, checksum: str) -> None:
    """Raises ValueError with a plain message if the file isn't the one we expect."""
    path = Path(path)
    size = path.stat().st_size
    if expected_size and size != expected_size:
        raise ValueError("The downloaded file has the wrong size (%d bytes, expected %d)." % (size, expected_size))
    if size < MIN_BYTES or size > MAX_BYTES:
        raise ValueError("The downloaded file has an unexpected size (%d bytes)." % size)
    with open(path, "rb") as f:
        if f.read(2) != b"MZ":
            raise ValueError("The downloaded file is not a Windows program.")
    if not checksum:
        raise ValueError("The release doesn't publish a checksum, so I can't verify the file.")
    actual = sha256_file(path)
    if actual != checksum.lower():
        raise ValueError("The downloaded file doesn't match its published checksum, so it was thrown away.")


def download(url: str, dest, expected_size: int, progress: Callable[[int], None]) -> None:
    """Downloads to dest (through a .part file). Raises ValueError/OSError on problems."""
    if not url.startswith(ALLOWED_PREFIX):
        raise ValueError("The download address isn't one of this program's own release files.")
    dest = Path(dest)
    part = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "TirsoCompiler/%s" % APP_VERSION})
    try:
        with urllib.request.urlopen(req, timeout=_DL_TIMEOUT) as resp, open(part, "wb") as out:
            total = int(resp.headers.get("Content-Length") or expected_size or 0)
            limit = expected_size or MAX_BYTES
            done = 0
            while True:
                chunk = resp.read(1 << 18)
                if not chunk:
                    break
                done += len(chunk)
                if done > limit:
                    raise ValueError("The download is bigger than it should be, so it was stopped.")
                out.write(chunk)
                if total:
                    progress(min(99, done * 100 // total))
        os.replace(part, dest)
    finally:
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------- the update itself
def downloads_dir() -> Path:
    from . import paths
    return paths.sub("updates")


def cleanup_downloads() -> None:
    """Removes installers left over from an earlier update (called when the program starts)."""
    try:
        for f in downloads_dir().iterdir():
            if f.is_file():
                f.unlink()
    except OSError:
        pass


def installer_command(setup: Path, log: Path) -> list:
    # /SILENT shows only a small progress window; /relaunch=1 makes the installer open Tirso again afterwards.
    return [str(setup), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS",
            "/relaunch=1", "/LOG=%s" % log]


def apply_update(info: dict, progress: Callable[[str, int], None], busy: Callable[[], str | None],
                 launch: Callable[[list], None] | None = None) -> dict:
    """Downloads, checks and starts the update installer.

    `busy()` returns a message when a job is running (the update is then refused).
    Returns {"ok": True} once the installer has been started (the caller then closes the
    program), or {"ok": False, "error": "..."}; nothing has been changed in that case.
    """
    try:
        if not can_self_update():
            return {"ok": False, "error": "This copy of Tirso Compiler runs from a folder, so it can't update itself. "
                                          "Install it with the Setup file from GitHub instead."}
        if not info or not info.get("update_available"):
            return {"ok": False, "error": "There's no newer version to install."}
        reason = busy()
        if reason:
            return {"ok": False, "error": reason}
        url, size, checksum = info.get("asset_url") or "", int(info.get("asset_size") or 0), info.get("checksum")
        if not checksum:
            return {"ok": False, "error": "The release doesn't publish a checksum, so I can't verify the file."}

        folder = downloads_dir()
        for f in folder.iterdir():
            if f.is_file():
                f.unlink()
        free = shutil.disk_usage(folder).free
        if free < size * 2 + 50 * 1024 * 1024:
            return {"ok": False, "error": "There isn't enough free disk space for the update (about %d MB needed)." % (size * 2 // 1048576 + 50)}

        dest = folder / UPDATE_ASSET
        progress("Downloading the update...", 0)
        download(url, dest, size, lambda pct: progress("Downloading the update... %d%%" % pct, pct))
        progress("Checking the download...", 99)
        verify_file(dest, size, checksum)
        # The program may have started a build while this was downloading.
        reason = busy()
        if reason:
            dest.unlink()
            return {"ok": False, "error": reason}
        progress("Installing. Tirso Compiler will close and open again by itself...", 100)
        cmd = installer_command(dest, folder / "update-install.log")
        if launch:
            launch(cmd)
        else:
            subprocess.Popen(cmd, close_fds=True, creationflags=DETACHED | NO_WINDOW)
        return {"ok": True}
    except (ValueError, OSError, urllib.error.URLError) as e:
        try:
            for f in downloads_dir().iterdir():
                if f.is_file():
                    f.unlink()
        except OSError:
            pass
        return {"ok": False, "error": str(getattr(e, "reason", None) or e)}
