"""Builds the Windows program and its two installers. Run by the GitHub build.

    python installer/build_release.py

Makes, in the "Installer" folder:
    TirsoCompilerSetup.exe    full installer (first install or a new PC)
    TirsoCompilerUpdate.exe   program + Android templates, without the big tools (used by "Update now")
    SHA256SUMS.txt            checksums of both (published in the GitHub Release)
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
OUT = BUILD / "dist" / "Tirso Compiler"
INSTALLER = ROOT / "Installer"


def app_version() -> str:
    text = (ROOT / "app" / "core" / "updater.py").read_text(encoding="utf-8")
    m = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', text, re.M)
    if not m:
        raise SystemExit("APP_VERSION not found in app/core/updater.py")
    return m.group(1)


def run(cmd):
    print("$", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, cwd=str(ROOT))


def find_iscc() -> str:
    candidates = [os.environ.get("ISCC"), shutil.which("ISCC")]
    for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if base:
            candidates.append(str(Path(base) / "Programs" / "Inno Setup 6" / "ISCC.exe"))
            candidates.append(str(Path(base) / "Inno Setup 6" / "ISCC.exe"))
    for c in candidates:
        if c and Path(c).exists():
            return c
    raise SystemExit("Inno Setup (ISCC.exe) was not found.")


def main():
    version = app_version()
    print("Building Tirso Compiler", version, flush=True)
    if BUILD.exists():
        shutil.rmtree(BUILD)
    if INSTALLER.exists():
        shutil.rmtree(INSTALLER)

    sep = os.pathsep  # ";" on Windows
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--log-level", "WARN",
         "--name", "Tirso Compiler",
         "--icon", ROOT / "installer" / "tirso.ico",
         "--paths", ROOT,
         "--add-data", "%s%sapp/ui" % (ROOT / "app" / "ui", sep),
         "--add-data", "%s%sapp/data" % (ROOT / "app" / "data", sep),
         "--collect-submodules", "app",
         "--collect-all", "webview",
         "--collect-all", "clr_loader",
         "--collect-all", "pythonnet",
         "--distpath", BUILD / "dist", "--workpath", BUILD / "work", "--specpath", BUILD,
         ROOT / "app" / "main.py"])
    if not (OUT / "Tirso Compiler.exe").exists():
        raise SystemExit("PyInstaller did not produce Tirso Compiler.exe")

    # templates and tools sit next to the .exe, where the program looks for them
    shutil.copytree(ROOT / "templates", OUT / "templates")
    shutil.copytree(ROOT / "tools", OUT / "tools")
    shutil.copy2(ROOT / "README.txt", OUT / "README.txt")
    shutil.copy2(ROOT / "tools" / "LICENSES.txt", OUT / "LICENSES.txt")

    iss = ROOT / "installer" / "TirsoCompiler.iss"
    iscc = find_iscc()
    run([iscc, "/Q", "/DAppVersion=" + version, "/DOutputName=TirsoCompilerSetup", iss])
    # The update installer leaves out the tools folder: Java, Apktool and the signer never change between versions.
    shutil.rmtree(OUT / "tools")
    run([iscc, "/Q", "/DAppVersion=" + version, "/DOutputName=TirsoCompilerUpdate", iss])

    lines = []
    for name in ("TirsoCompilerSetup.exe", "TirsoCompilerUpdate.exe"):
        f = INSTALLER / name
        if not f.exists():
            raise SystemExit("Missing " + name)
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        print("%s  %s  (%d MB)" % (digest, name, f.stat().st_size // 1048576), flush=True)
        lines.append("%s  %s" % (digest, name))
    (INSTALLER / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if "--version" in sys.argv:
        print(app_version())
    else:
        main()
