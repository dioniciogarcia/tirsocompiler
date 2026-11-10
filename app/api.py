"""Everything the interface can ask the program to do.

Each public method of `Api` is callable from the interface JavaScript as
`window.pywebview.api.<method>(...)`. Methods return plain dicts/lists.
"""
import base64
import json
import hashlib
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

from .core import bios as bios_mod
from .core import adapters as adapters_mod, apkbuild, consoles, identify, package_id, paths, toolchain
from .core.db import Database
from .core.hashing import file_hashes
from .batch import BatchMixin

ICON_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
MAX_ICON_BYTES = 8 * 1024 * 1024
MAX_BIOS_BYTES = 64 * 1024 * 1024


def _human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" if unit == "B" else "%.1f %s") % (n, unit)
        n /= 1024.0


class Api(BatchMixin):
    def __init__(self, db: Database = None, emit=None):
        self._db = db or Database()
        self._emit = emit or (lambda event, data: None)
        self._window = None
        self._jobs = queue.Queue()
        self._worker = threading.Thread(target=self._work, daemon=True)
        self._worker.start()
        self._build = None
        self._build_thread = None
        self._queue = []                 # build ids waiting their turn (oldest first)
        self._qlock = threading.RLock()
        consoles.refresh()
        self._batch_init()
        # Files left in "hashing" from a previous session get re-queued.
        for row in self._db.q("SELECT id FROM roms WHERE status='hashing'"):
            self._jobs.put(row["id"])

    # ------------------------------------------------------------ plumbing
    def attach_window(self, window):
        self._window = window

    def _dialog(self, kind, **kw):
        if not self._window:
            return None
        import webview  # only available in the desktop build
        mapping = {"open": webview.OPEN_DIALOG, "folder": webview.FOLDER_DIALOG}
        result = self._window.create_file_dialog(mapping[kind], **kw)
        return list(result) if result else None

    # ------------------------------------------------------------ overview
    def get_overview(self):
        db = self._db
        engines = self.list_engines()
        return {
            "counts": {
                "roms": db.one("SELECT COUNT(*) n FROM roms WHERE batch=0")["n"],
                "identified": db.one("SELECT COUNT(*) n FROM roms WHERE console IS NOT NULL AND batch=0")["n"],
                "games": db.one("SELECT COUNT(*) n FROM games WHERE batch=0")["n"],
                "apks": db.one("SELECT COUNT(*) n FROM builds WHERE status='success'")["n"],
                "bios": db.one("SELECT COUNT(*) n FROM bios WHERE valid=1")["n"],
                "engines_ready": sum(1 for e in engines if e["ready"]),
                "engines_total": len(engines),
                "hashing": db.one("SELECT COUNT(*) n FROM roms WHERE status='hashing'")["n"],
            },
            "by_console": db.q("SELECT console, COUNT(*) n FROM roms WHERE console IS NOT NULL AND batch=0 GROUP BY console"),
            "recent_roms": self.list_roms()[:5],
            "recent_builds": self.list_builds()[:5],
        }

    def get_consoles(self):
        consoles.refresh()
        return {"consoles": consoles.CONSOLES, "profiles": consoles.PROFILES}

    def list_engines(self):
        seen, out = set(), []
        for c in consoles.CONSOLES:
            key = c["engine"]
            if key in seen or c["phase"] >= 7:
                continue
            seen.add(key)
            out.append({
                "engine": key,
                "consoles": [x["short"] for x in consoles.CONSOLES if x["engine"] == key],
                "source": c["engine_source"],
                "license": c["license"],
                "phase": c["phase"],
                "status": c["status"],
                "ready": c["buildable"],
                "tested": c.get("tested", False),
                "installed_version": c.get("template_version"),
            })
        return out

    # ------------------------------------------------------------ ROM library
    def list_roms(self):
        rows = self._db.q("SELECT * FROM roms WHERE batch=0 ORDER BY added_at DESC, id DESC")
        dups = {}
        for r in rows:
            if r["sha1"]:
                dups.setdefault(r["sha1"], []).append(r["id"])
        for r in rows:
            r["size_h"] = _human(r["size"])
            r["duplicate_of"] = [i for i in dups.get(r["sha1"], []) if i != r["id"]] if r["sha1"] else []
            c = consoles.get(r["console"]) if r["console"] else None
            r["console_name"] = c["name"] if c else None
            r["console_short"] = c["short"] if c else None
            r["color"] = c["color"] if c else "#555b66"
        return rows

    def pick_rom_files(self):
        types = ("Game files (%s)" % ";".join("*" + e for e in consoles.ALL_EXTENSIONS), "All files (*.*)")
        picked = self._dialog("open", allow_multiple=True, file_types=types)
        return self.import_paths(picked) if picked else {"added": 0, "skipped": []}

    def pick_rom_folder(self):
        picked = self._dialog("folder")
        return self.import_paths(picked) if picked else {"added": 0, "skipped": []}

    def import_paths(self, path_list):
        files, skipped, from_folder = [], [], set()
        for p in path_list or []:
            p = str(p)
            if os.path.isdir(p):
                for root, _dirs, names in os.walk(p):
                    for n in names:
                        if os.path.splitext(n)[1].lower() in consoles.ALL_EXTENSIONS:
                            full = os.path.abspath(os.path.join(root, n))
                            files.append(full)
                            from_folder.add(full)
            elif os.path.isfile(p):
                files.append(p)
            else:
                skipped.append({"path": p, "reason": "File not found"})

        # .bin files listed by a .cue are part of that game, not games of their own
        in_cue = {}
        for path in files:
            if path.lower().endswith(".cue"):
                for part in identify.game_files(os.path.abspath(path))[1:]:
                    in_cue[os.path.normcase(os.path.abspath(part))] = os.path.basename(path)

        added = updated = 0
        for path in files:
            path = os.path.abspath(path)
            ext = os.path.splitext(path)[1].lower()
            if ext == ".bin" and (os.path.normcase(path) in in_cue or os.path.exists(os.path.splitext(path)[0] + ".cue")):
                skipped.append({"path": path, "reason": "Part of %s; the game is added through its .cue file"
                                % in_cue.get(os.path.normcase(path), os.path.splitext(os.path.basename(path))[0] + ".cue")})
                continue
            old = self._db.one("SELECT * FROM roms WHERE path=?", (path,))
            if old:
                # Added before (maybe removed from view, moved back, changed, or only used by Batch Build):
                # read it again and keep its entry, so its game projects stay attached.
                info = identify.identify(path)
                manual = old["console_source"] == "manual"
                self._db.run(
                    """UPDATE roms SET batch=0, size=?, format=?, console=?, detected_console=?, console_source=?,
                       title=?, serial=?, region=?, version=?, status='hashing', message=? WHERE id=?""",
                    (sum(os.path.getsize(f) for f in identify.game_files(path)), info["format"],
                     old["console"] if manual else (info["console"] if info["confident"] else old["console"]),
                     info["console"], "manual" if manual else ("header" if info["confident"] else old["console_source"]),
                     info["title"], info["serial"], info["region"], info["version"], info["note"], old["id"]))
                self._jobs.put(old["id"])
                updated += 1
                continue
            info = identify.identify(path)
            if path in from_folder and not info["console"] and info["format"] not in ("chd", "cue") \
                    and not (info["format"] == "bin" and self._looks_like_disc(path)):
                skipped.append({"path": path, "reason": "Not recognised as a game (add it on its own to set the console by hand)"})
                continue
            name = info["title"] or os.path.splitext(os.path.basename(path))[0]
            rid = self._db.run(
                """INSERT INTO roms(path, filename, size, format, console, detected_console, console_source,
                   title, display_name, serial, region, version, status, message, added_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (path, os.path.basename(path), sum(os.path.getsize(f) for f in identify.game_files(path)), info["format"],
                 info["console"] if info["confident"] else None,
                 info["console"], "header" if info["confident"] else None,
                 info["title"], name, info["serial"], info["region"], info["version"],
                 "hashing", info["note"], time.time()),
            )
            self._jobs.put(rid)
            added += 1
        self._emit("library_changed", {})
        return {"added": added, "updated": updated, "skipped": skipped}

    @staticmethod
    def _looks_like_disc(path):
        """A .bin that is a disc image (raw CD sectors, or big enough to be a DVD) and not a BIOS dump."""
        from .core.discs import CD_SYNC
        try:
            with open(path, "rb") as f:
                return f.read(12) == CD_SYNC or os.path.getsize(path) > 64 * 1024 * 1024
        except OSError:
            return False

    def _work(self):
        while True:
            rid = self._jobs.get()
            row = self._db.one("SELECT * FROM roms WHERE id=?", (rid,))
            if not row:
                continue
            try:
                if not os.path.exists(row["path"]):
                    self._db.run("UPDATE roms SET status='missing' WHERE id=?", (rid,))
                else:
                    def prog(pct, rid=rid):
                        self._emit("rom_progress", {"id": rid, "pct": pct})
                    h = file_hashes(self._hash_source(row["path"]), progress=prog)
                    self._db.run("UPDATE roms SET crc32=?, sha1=?, status='ready' WHERE id=?",
                                 (h["crc32"], h["sha1"], rid))
            except Exception as e:  # keep the worker alive whatever happens
                self._db.run("UPDATE roms SET status='error', message=? WHERE id=?",
                             ("Could not read the file: %s" % e, rid))
            self._emit("library_changed", {})

    @staticmethod
    def _hash_source(path):
        """The file whose checksum identifies the game: for a .cue, the .bin holding its data track."""
        if path.lower().endswith(".cue"):
            from .core.discs import parse_cue
            try:
                data = parse_cue(path)["data"]
                if data:
                    return data["path"]
            except Exception:
                pass
        return path

    def set_rom_console(self, rom_id, console_id):
        if console_id and not consoles.get(console_id):
            return {"ok": False, "error": "Unknown console."}
        self._db.run("UPDATE roms SET console=?, console_source=? WHERE id=?",
                     (console_id or None, "manual" if console_id else None, rom_id))
        return {"ok": True}

    def rename_rom(self, rom_id, name):
        name = (name or "").strip()
        if not name:
            return {"ok": False, "error": "Name can't be empty."}
        self._db.run("UPDATE roms SET display_name=? WHERE id=?", (name[:120], rom_id))
        return {"ok": True}

    def remove_rom(self, rom_id, with_projects=False):
        """Removes the library entry (and, if asked, its game projects). Files on disk are never touched:
        not the game file, not apps already built."""
        used = self._db.one("SELECT COUNT(*) n FROM games WHERE rom_id=? AND batch=0", (rom_id,))["n"]
        if used and not with_projects:
            return {"ok": False, "needs_confirm": True, "projects": used,
                    "error": "This game is used by %d project(s) in My Games." % used}
        if self._db.one("""SELECT 1 FROM builds b JOIN games g ON g.id=b.game_id
                           WHERE g.rom_id=? AND b.status IN ('queued','running')""", (rom_id,)):
            return {"ok": False, "error": "This game is being built right now. Try again when the build ends."}
        self._db.run("DELETE FROM games WHERE rom_id=? AND batch=0", (rom_id,))
        if self._db.one("SELECT 1 FROM games WHERE rom_id=?", (rom_id,)):
            self._db.run("UPDATE roms SET batch=1 WHERE id=?", (rom_id,))  # still used by Batch Build: just hide it
        else:
            self._db.run("DELETE FROM roms WHERE id=?", (rom_id,))
        self._emit("library_changed", {})
        return {"ok": True, "projects": used}

    def remove_missing_roms(self, with_projects=False):
        """Removes every library entry whose file is gone (after checking the files again)."""
        self.recheck_roms()
        missing = self._db.q("SELECT id FROM roms WHERE status='missing' AND batch=0")
        if not missing:
            return {"ok": True, "removed": 0, "projects": 0}
        projects = self._db.one("""SELECT COUNT(*) n FROM games WHERE batch=0 AND rom_id IN
                                   (SELECT id FROM roms WHERE status='missing' AND batch=0)""")["n"]
        if projects and not with_projects:
            return {"ok": False, "needs_confirm": True, "count": len(missing), "projects": projects}
        removed = 0
        for r in missing:
            if self.remove_rom(r["id"], with_projects=True).get("ok"):
                removed += 1
        return {"ok": True, "removed": removed, "projects": projects}

    def recheck_roms(self):
        n = 0
        for r in self._db.q("SELECT id, path, status FROM roms WHERE batch=0"):
            exists = os.path.exists(r["path"])
            if not exists and r["status"] != "missing":
                self._db.run("UPDATE roms SET status='missing' WHERE id=?", (r["id"],))
                n += 1
            elif exists and r["status"] == "missing":
                self._db.run("UPDATE roms SET status='hashing' WHERE id=?", (r["id"],))
                self._jobs.put(r["id"])
                n += 1
        return {"changed": n}

    # ------------------------------------------------------------ BIOS
    def list_bios(self):
        rows = self._db.q("SELECT * FROM bios ORDER BY console, preferred DESC, added_at DESC")
        for r in rows:
            r["size_h"] = _human(r["size"])
            r["exists"] = os.path.exists(r["stored_path"])
        return rows

    def pick_bios_files(self, console_id):
        picked = self._dialog("open", allow_multiple=True, file_types=("BIOS files (*.bin;*.rom)", "All files (*.*)"))
        return self.import_bios(console_id, picked) if picked else {"results": []}

    def import_bios(self, console_id, path_list):
        if console_id not in ("ps1", "ps2", "nds"):
            return {"results": [{"file": "", "ok": False, "message": "BIOS isn't used for this console."}]}
        results = []
        for p in path_list or []:
            name = os.path.basename(p)
            try:
                size = os.path.getsize(p)
                if size > MAX_BIOS_BYTES:
                    results.append({"file": name, "ok": False, "message": "File is far too large to be a BIOS."})
                    continue
                with open(p, "rb") as f:
                    data = f.read()
            except OSError as e:
                results.append({"file": name, "ok": False, "message": "Could not read: %s" % e.strerror})
                continue
            info = bios_mod.inspect(console_id, data)
            if not info["valid"]:
                results.append({"file": name, "ok": False, "message": info["message"]})
                continue
            sha = hashlib.sha256(data).hexdigest()
            if self._db.one("SELECT id FROM bios WHERE sha256=?", (sha,)):
                results.append({"file": name, "ok": False, "message": "Already in your BIOS library."})
                continue
            target_dir = paths.bios_dir() / console_id
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / ("%s_%s" % (sha[:12], name))
            target.write_bytes(data)  # a private copy; the original is left untouched
            first = not self._db.one("SELECT id FROM bios WHERE console=? AND valid=1", (console_id,))
            self._db.run(
                """INSERT INTO bios(console, filename, stored_path, size, sha256, version, region, valid, message,
                   preferred, added_at) VALUES(?,?,?,?,?,?,?,1,?,?,?)""",
                (console_id, name, str(target), size, sha, info.get("version"), info.get("region"),
                 info["message"], 1 if first else 0, time.time()),
            )
            results.append({"file": name, "ok": True, "message": info["message"]})
        return {"results": results}

    def set_preferred_bios(self, bios_id):
        row = self._db.one("SELECT console FROM bios WHERE id=?", (bios_id,))
        if not row:
            return {"ok": False}
        self._db.run("UPDATE bios SET preferred=0 WHERE console=?", (row["console"],))
        self._db.run("UPDATE bios SET preferred=1 WHERE id=?", (bios_id,))
        return {"ok": True}

    def remove_bios(self, bios_id):
        row = self._db.one("SELECT * FROM bios WHERE id=?", (bios_id,))
        if not row:
            return {"ok": False}
        self._db.run("DELETE FROM bios WHERE id=?", (bios_id,))
        try:
            os.remove(row["stored_path"])  # only Tirso's private copy
        except OSError:
            pass
        if row["preferred"]:
            nxt = self._db.one("SELECT id FROM bios WHERE console=? ORDER BY added_at DESC", (row["console"],))
            if nxt:
                self.set_preferred_bios(nxt["id"])
        return {"ok": True}

    # ------------------------------------------------------------ GPU drivers
    # "ps2" for PS2 apps, "dolphin" for GameCube and Wii apps
    DRIVER_CONSOLES = ("ps2", "dolphin")

    def get_gpu_driver(self, console_id="ps2"):
        raw = self._db.get_setting("gpu_driver_" + console_id)
        if not raw:
            return None
        try:
            info = json.loads(raw)
        except ValueError:
            return None
        info["exists"] = os.path.exists(info.get("stored_path", ""))
        info["size_h"] = _human(info.get("size", 0))
        return info

    def pick_gpu_driver(self, console_id="ps2"):
        picked = self._dialog("open", allow_multiple=False, file_types=("GPU driver (*.zip)", "All files (*.*)"))
        return self.import_gpu_driver(console_id, picked) if picked else {"ok": False, "message": ""}

    def import_gpu_driver(self, console_id, path_list):
        from .core import gpudriver
        if console_id not in self.DRIVER_CONSOLES or not path_list:
            return {"ok": False, "message": "GPU drivers are only used for PS2, GameCube and Wii games."}
        src = path_list[0]
        try:
            info = gpudriver.inspect(src)
        except OSError as e:
            return {"ok": False, "message": "Could not read: %s" % e.strerror}
        if not info["valid"]:
            return {"ok": False, "message": info["message"]}
        sha = gpudriver.sha1(src)
        target_dir = paths.sub("drivers") / console_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / ("%s_%s" % (sha[:12], os.path.basename(src)))
        shutil.copyfile(src, target)  # a private copy; the original is left untouched
        old = self.get_gpu_driver(console_id)
        if old and old.get("stored_path") and os.path.abspath(old["stored_path"]) != os.path.abspath(str(target)):
            try:
                os.remove(old["stored_path"])
            except OSError:
                pass
        record = {"filename": os.path.basename(src), "stored_path": str(target), "sha1": sha,
                  "size": os.path.getsize(target), "name": info["name"], "version": info["version"],
                  "vendor": info.get("vendor", ""), "library": info["library"], "added_at": time.time()}
        self._db.set_setting("gpu_driver_" + console_id, json.dumps(record))
        who = "PS2" if console_id == "ps2" else "GameCube and Wii"
        return {"ok": True, "message": info["message"] + " New %s builds will include it." % who}

    def remove_gpu_driver(self, console_id="ps2"):
        old = self.get_gpu_driver(console_id)
        if old:
            try:
                os.remove(old["stored_path"])
            except OSError:
                pass
        self._db.run("DELETE FROM settings WHERE key=?", ("gpu_driver_" + console_id,))
        return {"ok": True}

    # ------------------------------------------------------------ compatibility
    def check_compat(self, rom_id, console_id):
        rom = self._db.one("SELECT * FROM roms WHERE id=?", (rom_id,))
        c = consoles.get(console_id)
        checks = []

        def add(level, text):
            checks.append({"level": level, "text": text})

        if not rom or not c:
            add("fail", "Choose a console and a game first.")
            return {"checks": checks, "can_build": False}

        if os.path.exists(rom["path"]):
            add("ok", "Game file found on your PC.")
        else:
            add("fail", "The game file is missing from %s." % rom["path"])

        if rom["detected_console"] == console_id and rom["console_source"] == "header":
            add("ok", "File contents confirm this is a %s game." % c["name"])
        elif rom["detected_console"] and rom["detected_console"] != console_id:
            other = consoles.get(rom["detected_console"])
            add("fail", "File contents say this is a %s game, not %s." % (other["name"] if other else "different", c["name"]))
        else:
            add("warn", "The console couldn't be confirmed from the file itself; you chose %s manually." % c["name"])

        ext = os.path.splitext(rom["path"])[1].lower()
        if ext in c["extensions"]:
            add("ok", "%s format is supported by %s." % (ext.lstrip(".").upper(), c["engine"]))
        else:
            add("fail", "%s files aren't supported for %s." % (ext.lstrip(".").upper() or "These", c["name"]))

        if rom["status"] == "ready":
            if rom["sha1"]:
                add("ok", "Checksum calculated (SHA-1 %s…)." % rom["sha1"][:12])
            else:
                add("ok", "Game identified%s." % (" (CRC %s)" % rom["crc32"] if rom["crc32"] else ""))
        elif rom["status"] == "hashing":
            add("warn", "Checksum still being calculated.")
        else:
            add("fail", rom["message"] or "File could not be read.")

        if c["bios"]:
            have = self._db.one("SELECT * FROM bios WHERE console=? AND valid=1 ORDER BY preferred DESC", (console_id,))
            if have:
                add("ok", "BIOS ready: %s (v%s, %s)." % (have["filename"], have["version"] or "?", have["region"] or "region unknown"))
            elif c["bios"]["required"]:
                add("fail", "A %s BIOS is required. Import yours in BIOS Manager." % c["short"])
            else:
                add("info", c["bios"]["note"])

        ext = os.path.splitext(rom["path"])[1].lower()
        _ad = adapters_mod.get(console_id)
        if console_id == "ps2":
            add("info", "Size %s: the game (ISO, or BIN/CUE) is compressed to CSO first. If the result is over 1.5 GB (most DVD games), "
                "Tirso builds the APK plus a separate game data file, and the Install button sends both."
                % _human(rom["size"]))
        elif _ad and getattr(_ad, "supports_data_file", False):
            add("info", "Size %s: %sIf the game is over 1.5 GB (most Wii games), Tirso builds the APK plus a separate "
                "game data file, and the Install button sends both."
                % (_human(rom["size"]), "plain ISOs are compressed to GCZ first (Dolphin's format, one time only). "
                   if ext in (".iso", ".gcm") else ""))
        elif console_id == "psp" and ext == ".iso":
            add("info", "Size %s: Tirso compresses PSP ISOs to CSO before packaging (about 30 seconds per GB, "
                "one time only). The result must be under 1.5 GB." % _human(rom["size"]))
        elif rom["size"] <= consoles.EMBED_LIMIT_BYTES:
            add("ok", "Size %s: the game fits inside the APK." % _human(rom["size"]))
        else:
            add("fail", "Size %s: too big for one APK (limit 1.5 GB). Support for very large games comes in a "
                "later version." % _human(rom["size"]))

        add("info", "Game-by-game compatibility lists aren't connected yet; the real test is running it on your phone.")

        if c["buildable"]:
            add("ok", "%s Android template %s is installed." % (c["engine"], c.get("template_version", "")))
            if not c.get("tested"):
                add("info", "This engine hasn't been confirmed on a real phone yet. Your first build is that test.")
        else:
            add("warn", "The %s Android template isn't available yet (planned for a later version). You can save "
                "this game now and generate the APK then." % c["engine"])

        can_build = c["buildable"] and not any(x["level"] == "fail" for x in checks)
        return {"checks": checks, "can_build": can_build,
                "can_save": not any(x["level"] == "fail" for x in checks)}

    # ------------------------------------------------------------ game projects
    def suggest_package(self, name):
        base = package_id.default_for(name or "game")
        pkg, n = base, 2
        while self._db.one("SELECT id FROM games WHERE package_id=?", (pkg,)):
            pkg = "%s%d" % (base, n)
            n += 1
        return pkg

    def validate_package(self, pkg, game_id=None):
        err = package_id.validate(pkg)
        if err:
            return {"ok": False, "error": err}
        other = self._db.one("SELECT id, app_name FROM games WHERE package_id=?", (pkg,))
        if other and other["id"] != game_id:
            return {"ok": False, "error": "Already used by “%s”. Each game needs its own ID." % other["app_name"]}
        return {"ok": True}

    def pick_icon(self):
        picked = self._dialog("open", file_types=("Images (*.png;*.jpg;*.jpeg;*.webp)",))
        return self.load_icon(picked[0]) if picked else None

    def load_icon(self, path):
        ext = os.path.splitext(path)[1].lower()
        if ext not in ICON_TYPES:
            return {"ok": False, "error": "Use a PNG, JPG or WEBP image."}
        if os.path.getsize(path) > MAX_ICON_BYTES:
            return {"ok": False, "error": "Image is larger than 8 MB."}
        target = paths.icons_dir() / (uuid.uuid4().hex + ext)
        shutil.copyfile(path, target)
        return {"ok": True, "path": str(target), "data_url": self.icon_data_url(str(target))}

    def icon_data_url(self, path):
        if not path or not os.path.exists(path):
            return None
        mime = ICON_TYPES.get(os.path.splitext(path)[1].lower(), "image/png")
        return "data:%s;base64,%s" % (mime, base64.b64encode(Path(path).read_bytes()).decode())

    def save_game(self, data):
        rom = self._db.one("SELECT * FROM roms WHERE id=?", (data.get("rom_id"),))
        if not rom:
            return {"ok": False, "error": "Choose a game first."}
        name = (data.get("app_name") or "").strip()
        if not name:
            return {"ok": False, "error": "Enter the app name."}
        game_id = data.get("id")
        v = self.validate_package(data.get("package_id", ""), game_id)
        if not v["ok"]:
            return {"ok": False, "error": v["error"]}
        if data.get("orientation") not in ("landscape", "portrait", "auto"):
            return {"ok": False, "error": "Choose a screen orientation."}
        if data.get("profile") not in [p["id"] for p in consoles.PROFILES]:
            return {"ok": False, "error": "Choose a performance profile."}
        if data.get("console") and data["console"] != rom["console"]:
            self.set_rom_console(rom["id"], data["console"])
        now = time.time()
        features = consoles.get(data.get("console") or rom["console"]) or {}
        game_settings = {}
        if "widescreen" in features.get("features", []):
            game_settings["widescreen"] = bool(data.get("widescreen"))
        if "tv" in features.get("features", []):
            game_settings["tv"] = bool(data.get("tv"))
        control_ids = [x["id"] for x in features.get("controls", [])]
        if control_ids:
            game_settings["wii_controls"] = data.get("wii_controls") if data.get("wii_controls") in control_ids else control_ids[0]
        res_ids = [x["id"] for x in features.get("resolutions", [])]
        if res_ids:
            game_settings["resolution"] = data.get("resolution") if data.get("resolution") in res_ids else "auto"
        filter_ids = [x["id"] for x in features.get("screen_filters", [])]
        if filter_ids:
            game_settings["screen_filter"] = data.get("screen_filter") if data.get("screen_filter") in filter_ids \
                else features.get("screen_filter_default", "default")
        layouts = [x["id"] for x in features.get("screen_layouts", [])]
        if layouts:
            game_settings["ds_layout"] = data.get("ds_layout") if data.get("ds_layout") in layouts else layouts[0]
            data["orientation"] = {"stacked": "portrait", "rotate": "auto"}.get(game_settings["ds_layout"], "landscape")
        gfx_ids = [x["id"] for x in features.get("graphics", [])]
        if gfx_ids:
            game_settings["graphics"] = data.get("graphics") if data.get("graphics") in gfx_ids else gfx_ids[0]
        if game_settings.get("tv"):
            data["orientation"] = "landscape"
        args = (rom["id"], name[:50], data["package_id"], data["orientation"], data["profile"],
                data.get("bios_id"), data.get("icon_path"), json.dumps(game_settings))
        if game_id:
            self._db.run("""UPDATE games SET rom_id=?, app_name=?, package_id=?, orientation=?, profile=?,
                            bios_id=?, icon_path=?, settings_json=?, updated_at=? WHERE id=?""", args + (now, game_id))
        else:
            game_id = self._db.run("""INSERT INTO games(rom_id, app_name, package_id, orientation, profile,
                                      bios_id, icon_path, settings_json, created_at, updated_at)
                                      VALUES(?,?,?,?,?,?,?,?,?,?)""", args + (now, now))
        if "info_dir" in data:
            info_dir = data.get("info_dir")
            if info_dir and not (Path(info_dir) / "info.json").exists():
                info_dir = None
            self._db.run("UPDATE games SET info_dir=? WHERE id=?", (info_dir, game_id))
        return {"ok": True, "id": game_id}

    # ------------------------------------------------------------------ game information (single games)
    def lookup_rom_info(self, rom_id, name=None):
        """Finds the game's details, description, pictures and logo icon (Game Builder).
        name: an official game name picked by hand ("Wrong game?")."""
        from .core import gameinfo
        rom = self._db.one("SELECT * FROM roms WHERE id=?", (rom_id,))
        if not rom or not rom["console"]:
            return {"ok": False, "error": "Choose the console first."}
        if rom["console"] not in gameinfo.SYSTEMS:
            return {"ok": False, "error": "Game information isn't available for this console yet."}
        try:
            method = "manual"
            if not name:
                ident = identify.identify(rom["path"]) if os.path.exists(rom["path"]) else {}
                ident = dict(ident or {})
                ident.setdefault("serial", rom["serial"]); ident.setdefault("region", rom["region"])
                m = gameinfo.match_game(rom["console"], rom["path"], ident, crc=rom["crc32"])
                if not m["name"]:
                    return {"ok": False, "not_found": True,
                            "error": "This game isn't in the game lists. Use \u201cFind the game\u201d to search for it."}
                name, method = m["name"], m["method"]
            folder, info = gameinfo.gather(rom["console"], name, crc=rom["crc32"], serial=rom["serial"])
        except gameinfo.Offline:
            return {"ok": False, "error": "Couldn't reach the game lists. Check the internet connection."}
        except Exception as e:
            return {"ok": False, "error": "Lookup failed: %s" % e}
        pics = {}
        for kind in ("icon", "box"):
            p = Path(folder) / (kind + ".png")
            if p.exists():
                pics[kind] = "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()
        return {"ok": True, "name": name, "method": method, "title": info["title"][:50], "info_dir": str(folder),
                "icon_path": str(Path(folder) / "icon.png") if "icon" in pics else None, "icon_kind": info.get("icon"),
                "description": (info.get("description") or "")[:400], "description_source": info.get("description_source"),
                "year": info.get("year"), "developer": info.get("developer"), "genre": info.get("genre"),
                "complete": info.get("complete", True), "pictures": pics}

    def search_game_names(self, console, text):
        from .core import gameinfo
        if console not in gameinfo.SYSTEMS:
            return []
        try:
            return gameinfo.search_names(console, text)
        except gameinfo.Offline:
            return []

    def list_games(self):
        rows = self._db.q("""SELECT g.*, r.display_name rom_name, r.console, r.path rom_path, r.size rom_size,
                             r.status rom_status FROM games g JOIN roms r ON r.id=g.rom_id
                             WHERE g.batch=0 ORDER BY g.updated_at DESC""")
        for g in rows:
            c = consoles.get(g["console"]) or {}
            g["console_name"] = c.get("name")
            g["console_short"] = c.get("short")
            g["color"] = c.get("color", "#555b66")
            g["engine"] = c.get("engine")
            g["buildable"] = c.get("buildable", False)
            g["engine_status"] = c.get("status")
            g["icon"] = self.icon_data_url(g["icon_path"])
            last = self._db.one("SELECT apk_path, finished_at FROM builds WHERE game_id=? AND status='success' "
                                "ORDER BY finished_at DESC LIMIT 1", (g["id"],))
            g["last_apk"] = last["apk_path"] if last else None
            g["last_build_at"] = last["finished_at"] if last else None
            g["settings"] = json.loads(g.get("settings_json") or "{}")
            g["profile_name"] = next((p["name"] for p in consoles.PROFILES if p["id"] == g["profile"]), g["profile"])
        return rows

    def get_game(self, game_id):
        g = next((x for x in self.list_games() if x["id"] == game_id), None)
        return g

    def delete_game(self, game_id):
        """Removes the project only; ROM, BIOS and any built APKs stay on disk."""
        self._db.run("DELETE FROM games WHERE id=?", (game_id,))
        return {"ok": True}

    # ------------------------------------------------------------ builds
    def list_builds(self):
        rows = self._db.q("""SELECT b.id, b.game_id, b.status, b.stage, b.message, b.started_at, b.finished_at,
                             b.apk_path, b.apk_size, b.data_path, g.app_name, r.console FROM builds b
                             LEFT JOIN games g ON g.id=b.game_id LEFT JOIN roms r ON r.id=g.rom_id
                             WHERE COALESCE(g.batch, 0)=0
                             ORDER BY CASE b.status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,
                                      CASE WHEN b.status='queued' THEN b.started_at END ASC, b.started_at DESC LIMIT 80""")
        for b in rows:
            b["apk_size_h"] = _human(b["apk_size"]) if b["apk_size"] else None
            b["apk_exists"] = bool(b["apk_path"]) and os.path.exists(b["apk_path"])
            b["seconds"] = round(b["finished_at"] - b["started_at"]) if b["finished_at"] else None
        return rows

    def get_build(self, build_id):
        b = self._db.one("SELECT * FROM builds WHERE id=?", (build_id,))
        if not b:
            return None
        if self._build and self._build.build_id == build_id:
            b["log"] = "\n".join(self._build.lines[-400:])
        b["stages"] = apkbuild.STAGES
        return b

    def current_build(self):
        if self._build and self._build_thread and self._build_thread.is_alive():
            return self.get_build(self._build.build_id)
        return None

    def generate_apk(self, game_id):
        """Builds a game app's APK, or puts it in the queue when another build is running."""
        g = self._db.one("SELECT * FROM games WHERE id=?", (game_id,))
        if not g:
            return {"ok": False, "error": "Game app not found."}
        rom = self._db.one("SELECT * FROM roms WHERE id=?", (g["rom_id"],))
        rep = self.check_compat(rom["id"], rom["console"])
        if not rep["can_build"]:
            fails = [c["text"] for c in rep["checks"] if c["level"] == "fail"]
            return {"ok": False, "error": fails[0] if fails else "This console can't be built yet."}
        if self._db.one("SELECT id FROM builds WHERE game_id=? AND status IN ('queued', 'running')", (game_id,)):
            return {"ok": False, "error": "%s is already being built or waiting in the queue." % g["app_name"]}
        bid = self._db.run("INSERT INTO builds(game_id, status, stage, started_at, message) VALUES(?, 'queued', NULL, ?, ?)",
                           (game_id, time.time(), "Waiting for the builds before it."))
        with self._qlock:
            self._queue.append(bid)
            ahead = len(self._queue) - 1 + (1 if self.current_build() else 0)
        self._pump()
        self._emit("build_queue", {"id": bid, "waiting": self.queue_length()})
        return {"ok": True, "id": bid, "queued": ahead > 0, "ahead": ahead}

    def queue_length(self):
        with self._qlock:
            return len(self._queue)

    def _pump(self):
        """Starts the next waiting build when nothing is building."""
        with self._qlock:
            if self._build_thread and self._build_thread.is_alive() and self._build_thread is not threading.current_thread():
                return
            while self._queue:
                bid = self._queue.pop(0)
                row = self._db.one("SELECT * FROM builds WHERE id=?", (bid,))
                g = self._db.one("SELECT * FROM games WHERE id=?", (row["game_id"],)) if row else None
                if not row or row["status"] != "queued" or not g:
                    continue
                rom = self._db.one("SELECT * FROM roms WHERE id=?", (g["rom_id"],))
                version = self._db.one("SELECT COUNT(*) n FROM builds WHERE game_id=? AND status='success'",
                                       (g["id"],))["n"] + 1
                self._db.run("UPDATE builds SET status='running', stage='check', started_at=?, message=NULL WHERE id=?",
                             (time.time(), bid))
                # One sub-folder per console: <output folder>\Nintendo 64\Game.apk
                console_folder = (consoles.get(rom["console"]) or {}).get("name") or "Other"
                out_dir = os.path.join(self.get_settings()["output_dir"], console_folder)
                builder = apkbuild.ApkBuilder(self._db, g, rom, bid, out_dir, self._emit, version)
                self._build = builder
                self._build_thread = threading.Thread(target=self._run_build, args=(builder,), daemon=True)
                self._build_thread.start()
                return

    def _run_build(self, builder):
        try:
            builder.run()
        finally:
            self._pump()

    def batch_build(self, data):
        """Several games of one console: sets up a game app for each (same choices) and queues their builds."""
        rom_ids = [int(x) for x in (data.get("rom_ids") or [])]
        if not rom_ids:
            return {"ok": False, "error": "Choose at least one game."}
        roms = [self._db.one("SELECT * FROM roms WHERE id=?", (rid,)) for rid in rom_ids]
        if any(r is None for r in roms):
            return {"ok": False, "error": "A game is no longer in the library."}
        consoles_used = {r["console"] for r in roms}
        if len(consoles_used) != 1 or None in consoles_used:
            return {"ok": False, "error": "Choose games of one console (and set the console of any game that needs it)."}
        names = data.get("names") or {}
        results, queued = [], 0
        for r in roms:
            existing = self._db.one("SELECT * FROM games WHERE rom_id=? ORDER BY updated_at DESC", (r["id"],))
            name = (names.get(str(r["id"])) or (existing or {}).get("app_name") or r["display_name"] or "Game").strip()[:50]
            pkg = existing["package_id"] if existing else self.suggest_package(name)
            game = dict(data.get("options") or {})
            game.update(rom_id=r["id"], console=r["console"], app_name=name, package_id=pkg,
                        orientation=game.get("orientation") or "landscape", profile=game.get("profile") or "balanced",
                        icon_path=(existing or {}).get("icon_path"), id=existing["id"] if existing else None)
            saved = self.save_game(game)
            if not saved.get("ok"):
                results.append({"rom_id": r["id"], "name": name, "ok": False, "error": saved.get("error")})
                continue
            b = self.generate_apk(saved["id"])
            results.append({"rom_id": r["id"], "name": name, "ok": b.get("ok", False), "error": b.get("error"), "build_id": b.get("id")})
            queued += 1 if b.get("ok") else 0
        return {"ok": queued > 0, "queued": queued, "results": results,
                "error": None if queued else (results[0].get("error") if results else "Nothing was queued.")}

    def cancel_build(self, build_id):
        if self._build and self._build.build_id == build_id and self.current_build():
            self._build.cancel_requested = True
            return {"ok": True}
        with self._qlock:
            if build_id in self._queue:
                self._queue.remove(build_id)
                self._db.run("UPDATE builds SET status='cancelled', message='Removed from the queue.', finished_at=? WHERE id=?",
                             (time.time(), build_id))
                self._emit("build_queue", {"id": build_id, "waiting": len(self._queue)})
                return {"ok": True}
        return {"ok": False}

    def delete_build(self, build_id):
        """Removes the history entry only; the APK file stays where it is."""
        if self._build and self._build.build_id == build_id and self.current_build():
            return {"ok": False, "error": "This build is still running."}
        with self._qlock:
            if build_id in self._queue:
                return {"ok": False, "error": "This build is waiting in the queue. Remove it from the queue first."}
        self._db.run("DELETE FROM builds WHERE id=?", (build_id,))
        return {"ok": True}

    def show_in_folder(self, file_path):
        if not file_path or not os.path.exists(file_path):
            return {"ok": False, "error": "That file no longer exists."}
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(file_path)])
            return {"ok": True}
        return self.open_folder("output")

    # ------------------------------------------------------------ devices (ADB)
    def adb_status(self):
        from .core import adb
        return {"installed": adb.installed(), "version": adb.VERSION}

    def setup_adb(self):
        from .core import adb
        try:
            adb.install_tools(progress=lambda pct: self._emit("adb_progress", {"pct": pct}))
            return {"ok": True}
        except toolchain.ToolError as e:
            return {"ok": False, "error": str(e)}

    def list_devices(self):
        from .core import adb
        if not adb.installed():
            return {"installed": False, "devices": []}
        try:
            devs = adb.list_devices()
        except Exception as e:
            return {"installed": True, "devices": [], "error": "Couldn't ask ADB for devices: %s" % e}
        for d in devs:
            d["free_h"] = _human(d["free"]) if d.get("free") else None
        return {"installed": True, "devices": devs}

    def connect_device(self, address):
        from .core import adb
        return adb.connect(address)

    def pair_device(self, address, code):
        from .core import adb
        return adb.pair(address, code)

    def disconnect_device(self, serial):
        from .core import adb
        return adb.disconnect(serial)

    def install_build(self, build_id, serial, launch=True):
        """Installs a built APK on a device in the background; progress comes as events."""
        from .core import adb
        b = self._db.one("""SELECT b.*, g.package_id, g.app_name, r.console FROM builds b LEFT JOIN games g ON g.id=b.game_id
                            LEFT JOIN roms r ON r.id=g.rom_id WHERE b.id=?""", (build_id,))
        if not b or not b["apk_path"] or not os.path.exists(b["apk_path"]):
            return {"ok": False, "error": "That APK file no longer exists. Build the game again."}
        from .core import adapters as _ad
        activity = getattr(_ad.get(b.get("console")), "launch_activity", "com.tirso.player.GameActivity")
        try:
            dev = next((d for d in adb.list_devices() if d["serial"] == serial), None)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        if not dev or dev["state"] != "device":
            return {"ok": False, "error": dev["state_text"] if dev else "The device isn't connected any more."}
        import zipfile as _zip
        with _zip.ZipFile(b["apk_path"]) as z:
            apk_abis = sorted({n.split("/")[1] for n in z.namelist() if n.startswith("lib/") and n.count("/") >= 2})
        data_path = b.get("data_path") if b.get("data_path") and os.path.exists(b["data_path"]) else None
        if b.get("data_path") and not data_path:
            return {"ok": False, "error": "This game's data file is missing from the output folder. Build the game again."}
        total = os.path.getsize(b["apk_path"]) + (os.path.getsize(data_path) / 2.2 if data_path else 0)
        problem = adb.compatibility(dev, apk_abis, total)
        if problem:
            return {"ok": False, "error": problem}

        def work():
            log = lambda line: self._emit("install_log", {"serial": serial, "line": line})
            self._emit("install_log", {"serial": serial, "line": "Sending %s to %s…" % (os.path.basename(b["apk_path"]), dev["name"])})
            res = adb.install(serial, b["apk_path"], log=log)
            if res["ok"] and data_path:
                self._emit("install_log", {"serial": serial, "line": "Copying the game data file (%.1f GB); this can take a few minutes…"
                                           % (os.path.getsize(data_path) / 1024 ** 3)})
                pushed = adb.push_data_file(serial, data_path, b["package_id"], log=log)
                if not pushed["ok"]:
                    pushed.update(serial=serial, device=dev["name"], app_name=b["app_name"])
                    self._emit("install_done", pushed)
                    return
            if res["ok"] and launch and b["package_id"]:
                res["launched"] = adb.launch(serial, b["package_id"], activity)["ok"]
            res.update(serial=serial, device=dev["name"], app_name=b["app_name"], tv=dev.get("is_tv"))
            self._emit("install_done", res)

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True, "device": dev["name"]}

    def uninstall_game(self, game_id, serial):
        from .core import adb
        g = self._db.one("SELECT package_id FROM games WHERE id=?", (game_id,))
        if not g:
            return {"ok": False, "error": "Game app not found."}
        return adb.uninstall(serial, g["package_id"])

    # ------------------------------------------------------------ build tools and signing key
    def tools_status(self):
        return toolchain.status()

    def backup_signing_key(self):
        picked = self._dialog("folder")
        if not picked:
            return None
        try:
            where = toolchain.backup_keystore(os.path.join(picked[0], "Tirso signing key backup"))
            return {"ok": True, "path": where}
        except toolchain.ToolError as e:
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------ settings
    def get_settings(self):
        return {
            "output_dir": self._db.get_setting("output_dir", str(paths.default_output_dir())),
            "data_dir": str(paths.data_dir()),
            "version": "1.1.0",
            "theme": self._db.get_setting("theme", "dark"),
            "key": toolchain.keystore_info() is not None,
            "key_path": str(toolchain.keystore_path()),
        }

    def set_theme(self, theme):
        if theme not in ("dark", "light", "system"):
            return {"ok": False}
        self._db.set_setting("theme", theme)
        return {"ok": True}

    def pick_output_dir(self):
        picked = self._dialog("folder")
        if picked:
            self._db.set_setting("output_dir", picked[0])
        return self.get_settings()

    def open_url(self, url):
        if not str(url).startswith("https://"):
            return {"ok": False}
        import webbrowser
        webbrowser.open(url)
        return {"ok": True}

    def open_folder(self, which):
        target = {"data": paths.data_dir(), "output": Path(self.get_settings()["output_dir"]),
                  "bios": paths.bios_dir()}.get(which)
        if not target:
            return {"ok": False}
        target.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(str(target))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except Exception:
            return {"ok": False, "error": traceback.format_exc(limit=1)}
        return {"ok": True}
