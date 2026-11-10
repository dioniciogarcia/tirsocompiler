"""GPU drivers (adrenotools .zip packs, e.g. Mesa Turnip) the user adds to put inside PS2 apps.

Tirso never downloads drivers; the user imports a .zip they already have. It is checked here,
a private copy is kept, and every PS2 app built afterwards carries it and turns it on by itself
(only on phones with an Adreno GPU, where these drivers work).
"""
import hashlib
import json
import zipfile
from pathlib import Path

MAX_BYTES = 300 * 1024 * 1024


def inspect(path):
    """Checks a driver .zip. Returns dict(valid, name, version, library, message)."""
    p = Path(path)
    if p.suffix.lower() != ".zip":
        return dict(valid=False, message="A GPU driver comes as a .zip file.")
    if p.stat().st_size > MAX_BYTES:
        return dict(valid=False, message="File is far too large to be a GPU driver.")
    try:
        with zipfile.ZipFile(p) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            bad = z.testzip()
            if bad:
                return dict(valid=False, message="The .zip is damaged (%s)." % bad)
            libs = [n for n in names if n.lower().endswith(".so")]
            if not libs:
                return dict(valid=False, message="No driver library (.so) inside. Is this an Adreno driver pack?")
            meta = {}
            metas = [n for n in names if n.rsplit("/", 1)[-1] == "meta.json"]
            if metas:
                try:
                    meta = json.loads(z.read(metas[0]).decode("utf-8", "replace"))
                except ValueError:
                    meta = {}
    except zipfile.BadZipFile:
        return dict(valid=False, message="This isn't a readable .zip file.")
    library = meta.get("libraryName") or libs[0].rsplit("/", 1)[-1]
    name = meta.get("name") or p.stem
    version = meta.get("driverVersion") or ""
    vendor = meta.get("vendor") or ""
    label = name + (" (%s)" % version if version else "")
    return dict(valid=True, name=name, version=version, vendor=vendor, library=library,
                message="%s driver %s." % (vendor or "GPU", label))


def sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
