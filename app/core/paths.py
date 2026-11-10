"""Where Tirso Compiler keeps its files.

User data (database, BIOS copies, icons, signing keys) lives OUTSIDE the
project folder, in the Windows local app-data folder, so ROMs, BIOS files
and keys can never end up in a source repository by accident.
"""
import os
import sys
from pathlib import Path

APP_NAME = "TirsoCompiler"


def app_root() -> Path:
    """Folder that contains the 'app' package (the installed program)."""
    if getattr(sys, "frozen", False):  # installed program (Tirso Compiler.exe)
        return Path(sys._MEIPASS) / "app"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    override = os.environ.get("TIRSO_DATA_DIR")
    if override:
        base = Path(override)
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_NAME
    else:
        base = Path.home() / ".local" / "share" / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def sub(name: str) -> Path:
    p = data_dir() / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return data_dir() / "tirso.db"


def bios_dir() -> Path:
    return sub("bios")


def icons_dir() -> Path:
    return sub("icons")


def logs_dir() -> Path:
    return sub("logs")


def default_output_dir() -> Path:
    docs = Path.home() / "Documents"
    return (docs if docs.exists() else Path.home()) / "Tirso Compiler APKs"
