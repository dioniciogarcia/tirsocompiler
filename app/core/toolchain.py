"""Android packaging tools that ship inside the Tirso Compiler folder.

    tools/jre-windows-x64.zip   Eclipse Temurin Java runtime (unpacked on first use)
    tools/apktool.jar           opens and rebuilds the game template APK
    tools/uber-apk-signer.jar   aligns, signs and verifies the finished APK

No Android SDK, NDK or Gradle is needed on this PC: the engine templates are
built and tested once, then only filled in with each game.
"""
import json
import os
import secrets
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from . import paths

KEY_ALIAS = "tirso"
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0  # don't flash console windows


def project_root() -> Path:
    """Folder with the templates and tools folders: the project folder, or the installed program's folder."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return paths.app_root().parent


def tools_dir() -> Path:
    return project_root() / "tools"


def templates_dir() -> Path:
    return project_root() / "templates"


def run(cmd, log=None, cancelled=None, cwd=None, env=None):
    """Runs a tool, streaming its output into `log`. Returns (exit_code, output)."""
    if log:
        log("$ " + " ".join(_show(c) for c in cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd, env=env,
                            creationflags=NO_WINDOW, text=True, encoding="utf-8", errors="replace")
    out = []
    for line in proc.stdout:
        line = line.rstrip()
        if line.startswith("Picked up JAVA_TOOL_OPTIONS"):
            continue
        out.append(line)
        if log:
            log(line)
        if cancelled and cancelled():
            proc.kill()
            proc.wait()
            raise BuildCancelled()
    return proc.wait(), "\n".join(out)


def _show(part):
    s = str(part)
    return '"%s"' % s if " " in s else s


class BuildCancelled(Exception):
    pass


class ToolError(Exception):
    """A problem explained in plain language."""


# ---------------------------------------------------------------- Java
def java_home() -> Path:
    return paths.data_dir() / "tools" / "jre"


def find_java():
    exe = "java.exe" if sys.platform == "win32" else "java"
    local = java_home() / "bin" / exe
    if local.exists():
        return str(local)
    return shutil.which("java")


def find_keytool():
    exe = "keytool.exe" if sys.platform == "win32" else "keytool"
    local = java_home() / "bin" / exe
    if local.exists():
        return str(local)
    return shutil.which("keytool")


def install_java(log=None):
    """Unpacks the bundled Java runtime into Tirso's data folder (Windows only)."""
    if sys.platform != "win32":
        if find_java():
            return
        raise ToolError("Java isn't installed on this computer.")
    archive = tools_dir() / "jre-windows-x64.zip"
    if not archive.exists():
        raise ToolError("The Java runtime is missing from the Tirso Compiler folder (tools\\jre-windows-x64.zip).")
    target = java_home()
    tmp = target.with_name("jre-unpacking")
    shutil.rmtree(tmp, ignore_errors=True)
    if log:
        log("Unpacking the Java runtime (one time only)…")
    with zipfile.ZipFile(archive) as z:
        z.extractall(tmp)
    inner = [p for p in tmp.iterdir() if p.is_dir()]
    root = inner[0] if len(inner) == 1 else tmp
    shutil.rmtree(target, ignore_errors=True)
    shutil.move(str(root), str(target))
    shutil.rmtree(tmp, ignore_errors=True)


def status():
    """What's installed, for the Engine Manager."""
    key = keystore_info()
    return {
        "java": bool(find_java()) or (tools_dir() / "jre-windows-x64.zip").exists(),
        "java_ready": bool(find_java()),
        "apktool": (tools_dir() / "apktool.jar").exists(),
        "signer": (tools_dir() / "uber-apk-signer.jar").exists(),
        "key": key is not None,
        "key_path": str(keystore_path()),
        "key_created": key.get("created") if key else None,
    }


def ensure_tools(log=None):
    if not find_java():
        install_java(log)
    if not find_java():
        raise ToolError("Java couldn't be set up. Try deleting the folder %s and building again." % java_home())
    for name in ("apktool.jar", "uber-apk-signer.jar"):
        if not (tools_dir() / name).exists():
            raise ToolError("%s is missing from the Tirso Compiler tools folder." % name)


# ---------------------------------------------------------------- signing key
def keys_dir() -> Path:
    return paths.sub("keys")


def keystore_path() -> Path:
    return keys_dir() / "tirso-signing-key.p12"


def keystore_info():
    meta = keys_dir() / "tirso-signing-key.json"
    if keystore_path().exists() and meta.exists():
        return json.loads(meta.read_text(encoding="utf-8"))
    return None


def ensure_keystore(log=None):
    """Creates the private key every APK is signed with, the first time only.

    Android only installs an update to an app if it's signed with the same key,
    so this key must be kept (and backed up).
    """
    info = keystore_info()
    if info:
        return info
    keytool = find_keytool()
    if not keytool:
        raise ToolError("keytool (part of Java) wasn't found, so the signing key can't be created.")
    password = secrets.token_urlsafe(24)
    while not password[0].isalnum():   # a leading "-" would be read as an option by the signing tools
        password = secrets.token_urlsafe(24)
    if log:
        log("Creating your private signing key (one time only)…")
    code, out = run([keytool, "-genkeypair", "-keystore", str(keystore_path()), "-storetype", "PKCS12",
                     "-alias", KEY_ALIAS, "-keyalg", "RSA", "-keysize", "4096", "-validity", "36500",
                     "-dname", "CN=Tirso Compiler, O=Personal",
                     "-storepass", password, "-keypass", password], log=None)
    if code != 0 or not keystore_path().exists():
        raise ToolError("The signing key couldn't be created:\n" + out[-800:])
    from datetime import date
    info = {"alias": KEY_ALIAS, "password": password, "created": date.today().isoformat()}
    (keys_dir() / "tirso-signing-key.json").write_text(json.dumps(info), encoding="utf-8")
    return info


def backup_keystore(dest_dir):
    """Copies the key and its password file to a folder the user picks."""
    if not keystore_info():
        raise ToolError("No signing key exists yet. It's created with your first APK.")
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    for f in (keystore_path(), keys_dir() / "tirso-signing-key.json"):
        shutil.copy2(f, dest / f.name)
    return str(dest)
