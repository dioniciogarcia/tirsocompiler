"""Install game apps on phones and TVs with Google's ADB tool (USB or Wi-Fi).

ADB is part of Google's Android "platform-tools". Its licence doesn't allow
bundling it, so Tirso downloads the official, pinned version from Google the
first time it's needed and checks its SHA-1 checksum.
"""
import hashlib
import os
import re
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

from . import paths
from .toolchain import NO_WINDOW, ToolError, run

VERSION = "37.0.2"
DOWNLOADS = {
    "win32": ("https://dl.google.com/android/repository/platform-tools_r37.0.2-win.zip",
              "d7ee482b3874f1e0d6d5dca135f6bd58f5716db0", 8239125),
    "linux": ("https://dl.google.com/android/repository/platform-tools_r37.0.2-linux.zip",
              "736a08af10515674e40d8b754fe87390d698da2b", 9541228),
    "darwin": ("https://dl.google.com/android/repository/platform-tools_r37.0.2-darwin.zip",
               "6f86aa58ba42dd4bfe27477f3f11c09edc3ebe3f", 16516152),
}


def tools_dir() -> Path:
    return paths.sub("tools") / "platform-tools"


def adb_path():
    override = os.environ.get("TIRSO_ADB")  # used by the self-tests
    if override:
        return override
    exe = tools_dir() / ("adb.exe" if sys.platform == "win32" else "adb")
    return str(exe) if exe.exists() else None


def installed():
    return adb_path() is not None


def install_tools(progress=None):
    """Downloads and unpacks platform-tools. Returns the adb path."""
    key = "win32" if sys.platform == "win32" else ("darwin" if sys.platform == "darwin" else "linux")
    url, sha1, size = DOWNLOADS[key]
    dest = paths.sub("tools")
    archive = dest / "platform-tools.zip"
    try:
        with urllib.request.urlopen(url, timeout=60) as resp, open(archive, "wb") as out:
            done = 0
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(min(99, int(done * 100 / size)))
    except OSError as e:
        raise ToolError("Couldn't download Google's ADB tools. Check the internet connection and try again. (%s)" % e)
    h = hashlib.sha1(archive.read_bytes()).hexdigest()
    if h != sha1:
        archive.unlink()
        raise ToolError("The downloaded ADB tools didn't match Google's checksum, so they weren't used. Try again.")
    shutil.rmtree(tools_dir(), ignore_errors=True)
    with zipfile.ZipFile(archive) as z:
        z.extractall(dest)
    archive.unlink()
    exe = adb_path()
    if not exe:
        raise ToolError("ADB wasn't found after unpacking Google's tools.")
    if sys.platform != "win32":
        os.chmod(exe, 0o755)
    if progress:
        progress(100)
    return exe


def adb(*args, serial=None, log=None, timeout=None):
    exe = adb_path()
    if not exe:
        raise ToolError("ADB isn't set up yet.")
    cmd = [exe] + (["-s", serial] if serial else []) + list(args)
    return run(cmd, log=log)


def shell(serial, command):
    code, out = adb("shell", command, serial=serial)
    return out.strip() if code == 0 else ""


# ---------------------------------------------------------------- devices
STATE_TEXT = {
    "device": "Connected",
    "unauthorized": "Allow USB debugging on the device screen",
    "offline": "Offline: unplug and reconnect, or reconnect Wi-Fi",
    "no permissions": "Windows needs the device's USB driver",
    "authorizing": "Waiting for permission on the device",
}


def parse_devices(text):
    """Parses `adb devices -l`."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        m = re.match(r"(\S+)\s+(device|unauthorized|offline|authorizing|no permissions|recovery|sideload|bootloader)\b(.*)", line)
        if not m:
            continue
        serial, state, rest = m.groups()
        props = dict(re.findall(r"(\w+):(\S+)", rest))
        out.append({
            "serial": serial,
            "state": state,
            "state_text": STATE_TEXT.get(state, state),
            "wifi": bool(re.match(r"^\d+\.\d+\.\d+\.\d+:\d+$", serial)) or "_adb-tls-connect" in serial,
            "model": props.get("model", "").replace("_", " "),
        })
    return out


def parse_df_free(text):
    """Free bytes from `df -k /data`."""
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 2:
        return None
    parts = lines[-1].split()
    try:
        return int(parts[3]) * 1024
    except (IndexError, ValueError):
        return None


def describe(serial):
    """Model, Android version, processor types, TV or phone, free storage."""
    props_raw = shell(serial, "getprop")
    props = dict(re.findall(r"\[([^\]]+)\]: \[([^\]]*)\]", props_raw))
    abilist = props.get("ro.product.cpu.abilist", "") or props.get("ro.product.cpu.abi", "")
    abis = [a for a in abilist.split(",") if a]
    chars = props.get("ro.build.characteristics", "")
    features = shell(serial, "pm list features")
    is_tv = "tv" in chars or "android.software.leanback" in features or "amazon.hardware.fire_tv" in features
    brand = props.get("ro.product.manufacturer", "").strip()
    model = props.get("ro.product.model", "").strip()
    name = model if model.lower().startswith(brand.lower()) else ("%s %s" % (brand, model)).strip()
    return {
        "name": name or serial,
        "android": props.get("ro.build.version.release", "?"),
        "sdk": int(props.get("ro.build.version.sdk", "0") or 0),
        "abis": abis,
        "has64": "arm64-v8a" in abis,
        "has32": "armeabi-v7a" in abis,
        "is_tv": is_tv,
        "is_fire_tv": "amazon.hardware.fire_tv" in features,
        "free": parse_df_free(shell(serial, "df -k /data")),
    }


def list_devices():
    code, out = adb("devices", "-l")
    devs = parse_devices(out)
    for d in devs:
        if d["state"] == "device":
            try:
                d.update(describe(d["serial"]))
            except Exception:
                pass
        d.setdefault("name", d["model"] or d["serial"])
    return devs


def connect(address):
    address = address.strip()
    if ":" not in address:
        address += ":5555"
    code, out = adb("connect", address)
    ok = code == 0 and ("connected to" in out or "already connected" in out) and "failed" not in out.lower()
    if ok:
        return {"ok": True, "message": "Connected to %s." % address}
    if "refused" in out.lower():
        msg = ("The device refused the connection. On the TV, turn on Developer options > "
               "Network debugging / ADB debugging, or use Wireless debugging with a pairing code.")
    elif "timed out" in out.lower() or "no route" in out.lower() or "unreachable" in out.lower():
        msg = "No answer from %s. Check the address and that the TV and this PC are on the same Wi-Fi." % address
    else:
        msg = out.strip() or "Couldn't connect."
    return {"ok": False, "error": msg}


def pair(address, code_):
    address, code_ = address.strip(), re.sub(r"\D", "", str(code_))
    if len(code_) != 6:
        return {"ok": False, "error": "The pairing code has 6 digits."}
    code, out = adb("pair", address, code_)
    if code == 0 and "Successfully paired" in out:
        return {"ok": True, "message": "Paired. Now connect using the address shown under Wireless debugging (not the pairing one)."}
    return {"ok": False, "error": "Pairing failed. Check the pairing address and code; they change each time. (%s)" % out.strip()[-200:]}


def disconnect(serial):
    adb("disconnect", serial)
    return {"ok": True}


# ---------------------------------------------------------------- install
INSTALL_ERRORS = {
    "INSTALL_FAILED_UPDATE_INCOMPATIBLE": "A version of this game signed with a different key is already installed. "
                                          "Uninstall it from the device first (your saves for it will be lost).",
    "INSTALL_FAILED_NO_MATCHING_ABIS": "This APK doesn't have an engine for this device's processor. "
                                       "Turn on TV support for this game and build it again.",
    "INSTALL_FAILED_INSUFFICIENT_STORAGE": "The device doesn't have enough free storage. Free some space and try again.",
    "INSTALL_FAILED_VERSION_DOWNGRADE": "A newer build of this game is already installed. Build it again in Tirso "
                                        "(each build gets a higher version) and install that.",
    "INSTALL_FAILED_USER_RESTRICTED": "The device blocked the install. On Xiaomi/Fire devices, allow 'Install via USB' "
                                      "or 'Apps from Unknown Sources' in Developer options.",
    "INSTALL_PARSE_FAILED": "The device couldn't read this APK. Build it again.",
    "INSTALL_FAILED_OLDER_SDK": "This device's Android version is too old (Android 8 or newer is needed).",
    "INSTALL_FAILED_VERIFICATION_FAILURE": "Google Play Protect blocked the install. On the device, open the Play Store > "
                                           "Play Protect settings, allow it, and try again.",
}


def explain_install_error(output):
    for key, text in INSTALL_ERRORS.items():
        if key in output:
            return text
    m = re.search(r"(INSTALL_\w+)", output)
    return "The device refused the install (%s)." % m.group(1) if m else (output.strip()[-300:] or "Install failed.")


def compatibility(device, apk_abis, apk_size):
    """Problems that would stop the install, checked before sending anything."""
    if device.get("abis") and not set(device["abis"]) & set(apk_abis):
        if device.get("has32") and not device.get("has64"):
            return "This device runs 32-bit Android. Turn on TV support for this game and build it again."
        return "This APK has no engine for this device's processor (%s)." % ", ".join(device["abis"])
    free = device.get("free")
    if free is not None and free < apk_size * 2.2:
        return "Not enough free storage on the device: about %.1f GB needed, %.1f GB free." % (
            apk_size * 2.2 / 1024 ** 3, free / 1024 ** 3)
    return None


def install(serial, apk, log=None):
    code, out = adb("install", "-r", "-g", str(apk), serial=serial, log=log)
    if code == 0 and "Success" in out:
        return {"ok": True}
    return {"ok": False, "error": explain_install_error(out)}


def push_data_file(serial, local_path, package, log=None):
    """Copies a large game's data file to Android/obb/<package>/ (where the game app looks for it)."""
    name = Path(local_path).name
    remote_dir = "/sdcard/Android/obb/%s" % package
    adb("shell", "mkdir", "-p", remote_dir, serial=serial)
    code, out = adb("push", str(local_path), "%s/%s" % (remote_dir, name), serial=serial, log=log)
    if code == 0 and ("pushed" in out or "file pushed" in out):
        return {"ok": True}
    if "No space left" in out:
        return {"ok": False, "error": "The device ran out of storage while copying the game data file."}
    return {"ok": False, "error": "The game data file couldn't be copied (%s)." % (out.strip()[-200:] or "unknown error")}


def launch(serial, package, activity="com.tirso.player.GameActivity"):
    code, out = adb("shell", "am", "start", "-n", "%s/%s" % (package, activity), serial=serial)
    ok = code == 0 and "Error" not in out
    return {"ok": ok, "error": None if ok else "The game was installed but couldn't be opened from here. Open it on the device."}


def uninstall(serial, package):
    code, out = adb("uninstall", package, serial=serial)
    return {"ok": code == 0 and "Success" in out, "error": None if "Success" in out else out.strip()[-200:]}
