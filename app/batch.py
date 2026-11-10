"""Batch build: a whole folder of games turned into apps.

Pick a folder -> every game in it is identified and looked up (details, description, pictures,
logo icon) -> review -> "Build all" queues one build per game.

Folders and their games live in their own tables and only show in the Batch build tab;
the apps they make are kept out of My games and the ROM Library.
"""
import base64
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .core import consoles, gameinfo, identify

LOOKUP_THREADS = 3
FOUND_STATES = ("found", "name", "manual")
DEFAULTS = {"profile": "balanced", "widescreen": True, "resolution": "auto", "ds_layout": "rotate",
            "wii_controls": "sideways", "orientation": "landscape", "graphics": "vulkan"}


class BatchMixin:
    """Batch build methods of the Api (see api.py)."""

    def _batch_init(self):
        self._batch_lock = threading.RLock()
        self._batch_scans = []          # folder ids waiting to be read
        self._batch_thread = None
        self._batch_stop = set()
        self._batch_last_emit = 0
        for f in self._db.q("SELECT id FROM batch_folders WHERE status='stopped'"):
            pass  # stopped folders wait for "Continue"

    # ------------------------------------------------------------ events
    def _batch_changed(self, folder_id, force=False):
        now = time.time()
        if force or now - self._batch_last_emit > 0.8:
            self._batch_last_emit = now
            self._emit("batch_changed", {"folder_id": folder_id})

    # ------------------------------------------------------------ folders
    def batch_list_folders(self):
        rows = self._db.q("SELECT * FROM batch_folders ORDER BY created_at DESC")
        for f in rows:
            f.update(self._batch_counts(f["id"]))
            f["settings"] = self.batch_get_settings(f["id"])
            f["name"] = os.path.basename(f["path"].rstrip("\\/")) or f["path"]
        return rows

    def _batch_counts(self, folder_id):
        items = self._db.q("""SELECT i.state, i.include, b.status build_status FROM batch_items i
                              LEFT JOIN builds b ON b.id=i.build_id WHERE i.folder_id=?""", (folder_id,))
        c = {"total": len(items), "found": 0, "by_name": 0, "not_found": 0, "skipped": 0, "waiting": 0,
             "included": 0, "built": 0, "failed": 0, "in_queue": 0}
        for i in items:
            st = i["state"]
            if st in ("found", "manual"):
                c["found"] += 1
            elif st == "name":
                c["by_name"] += 1
            elif st == "notfound":
                c["not_found"] += 1
            elif st in ("skipped", "error"):
                c["skipped"] += 1
            else:
                c["waiting"] += 1
            if i["include"] and st not in ("skipped", "error", "waiting", "looking"):
                c["included"] += 1
            bs = i["build_status"]
            if bs == "success":
                c["built"] += 1
            elif bs in ("failed", "cancelled"):
                c["failed"] += 1
            elif bs in ("queued", "running"):
                c["in_queue"] += 1
        return c

    def batch_pick_folder(self, target="phone"):
        picked = self._dialog("folder")
        if not picked:
            return {"ok": False, "cancelled": True}
        return self.batch_add_folder(picked[0], target)

    def batch_add_folder(self, path, target="phone"):
        path = os.path.abspath(str(path))
        if not os.path.isdir(path):
            return {"ok": False, "error": "Folder not found."}
        if target not in ("phone", "tv"):
            target = "phone"
        old = self._db.one("SELECT id FROM batch_folders WHERE path=?", (path,))
        now = time.time()
        if old:
            fid = old["id"]
            self._db.run("UPDATE batch_folders SET target=?, updated_at=? WHERE id=?", (target, now, fid))
        else:
            fid = self._db.run("INSERT INTO batch_folders(path, target, status, created_at, updated_at) VALUES(?,?,?,?,?)",
                               (path, target, "scanning", now, now))
        added = self._batch_collect(fid, path)
        self._batch_queue_scan(fid)
        return {"ok": True, "id": fid, "added": added}

    def _batch_collect(self, fid, path):
        """Adds every game file in the folder (and its sub-folders) that isn't listed yet."""
        files = []
        for root, _dirs, names in os.walk(path):
            for n in names:
                if os.path.splitext(n)[1].lower() in consoles.ALL_EXTENSIONS:
                    files.append(os.path.abspath(os.path.join(root, n)))
        in_cue = set()
        for p in files:
            if p.lower().endswith(".cue"):
                for part in identify.game_files(p)[1:]:
                    in_cue.add(os.path.normcase(os.path.abspath(part)))
        added = 0
        for p in sorted(files, key=str.lower):
            if p.lower().endswith(".bin") and (os.path.normcase(p) in in_cue or os.path.exists(os.path.splitext(p)[0] + ".cue")):
                continue
            if self._db.one("SELECT id FROM batch_items WHERE folder_id=? AND path=?", (fid, p)):
                continue
            try:
                size = sum(os.path.getsize(f) for f in identify.game_files(p))
            except OSError:
                continue
            self._db.run("""INSERT INTO batch_items(folder_id, path, filename, size, state)
                            VALUES(?,?,?,?, 'waiting')""", (fid, p, os.path.basename(p), size))
            added += 1
        return added

    def batch_rescan(self, folder_id):
        """Looks for new files and looks up games that weren't found (or were stopped) again."""
        f = self._db.one("SELECT * FROM batch_folders WHERE id=?", (folder_id,))
        if not f:
            return {"ok": False, "error": "Folder not found."}
        if not os.path.isdir(f["path"]):
            return {"ok": False, "error": "The folder %s can't be found. Is the drive connected?" % f["path"]}
        added = self._batch_collect(folder_id, f["path"])
        self._db.run("UPDATE batch_items SET state='waiting', message=NULL WHERE folder_id=? AND state IN ('notfound', 'error')",
                     (folder_id,))
        self._batch_queue_scan(folder_id)
        return {"ok": True, "added": added}

    def batch_get_settings(self, folder_id):
        """The performance settings used for every game of this folder."""
        f = self._db.one("SELECT settings_json FROM batch_folders WHERE id=?", (folder_id,))
        out = dict(DEFAULTS)
        if f and f["settings_json"]:
            try:
                out.update(json.loads(f["settings_json"]))
            except ValueError:
                pass
        return out

    def batch_set_settings(self, folder_id, settings):
        keep = {}
        allowed = {"profile": [p["id"] for p in consoles.PROFILES], "resolution": ["auto", "720p", "1080p", "1440p"],
                   "ds_layout": ["rotate", "stacked", "side", "large"], "wii_controls": ["sideways", "nunchuk", "classic", "gamecube"],
                   "orientation": ["landscape", "portrait", "auto"], "graphics": ["vulkan", "opengl"]}
        for k, ok in allowed.items():
            if (settings or {}).get(k) in ok:
                keep[k] = settings[k]
        keep["widescreen"] = bool((settings or {}).get("widescreen", True))
        if keep.get("profile") != "quality":
            keep["resolution"] = "auto"
        self._db.run("UPDATE batch_folders SET settings_json=? WHERE id=?", (json.dumps(keep), folder_id))
        return {"ok": True, "settings": self.batch_get_settings(folder_id)}

    def batch_set_target(self, folder_id, target):
        if target not in ("phone", "tv"):
            return {"ok": False}
        self._db.run("UPDATE batch_folders SET target=? WHERE id=?", (target, folder_id))
        return {"ok": True}

    def batch_remove_folder(self, folder_id):
        """Forgets the folder. Game files and built apps on disk stay where they are."""
        if self._db.one("""SELECT 1 FROM batch_items i JOIN builds b ON b.id=i.build_id
                           WHERE i.folder_id=? AND b.status IN ('queued','running')""", (folder_id,)):
            return {"ok": False, "error": "Some games of this folder are still being built. Stop them first."}
        with self._batch_lock:
            self._batch_stop.add(folder_id)
        games = [r["game_id"] for r in self._db.q("SELECT game_id FROM batch_items WHERE folder_id=? AND game_id IS NOT NULL",
                                                   (folder_id,))]
        self._db.run("DELETE FROM batch_folders WHERE id=?", (folder_id,))
        for gid in games:
            g = self._db.one("SELECT id, rom_id, batch FROM games WHERE id=?", (gid,))
            if g and g["batch"]:
                self._db.run("DELETE FROM games WHERE id=?", (gid,))
                if not self._db.one("SELECT 1 FROM games WHERE rom_id=?", (g["rom_id"],)):
                    self._db.run("DELETE FROM roms WHERE id=? AND batch=1", (g["rom_id"],))
        self._emit("batch_changed", {"folder_id": folder_id})
        return {"ok": True}

    def batch_stop_scan(self, folder_id):
        with self._batch_lock:
            self._batch_stop.add(folder_id)
            if folder_id in self._batch_scans:
                self._batch_scans.remove(folder_id)
        self._db.run("UPDATE batch_folders SET status='stopped', message='Stopped.' WHERE id=? AND status='scanning'", (folder_id,))
        self._batch_changed(folder_id, True)
        return {"ok": True}

    # ------------------------------------------------------------ reading games
    def _batch_queue_scan(self, fid):
        with self._batch_lock:
            self._batch_stop.discard(fid)
            if fid not in self._batch_scans:
                self._batch_scans.append(fid)
            self._db.run("UPDATE batch_folders SET status='scanning', message=NULL WHERE id=?", (fid,))
            if not (self._batch_thread and self._batch_thread.is_alive()):
                self._batch_thread = threading.Thread(target=self._batch_scan_loop, daemon=True)
                self._batch_thread.start()
        self._batch_changed(fid, True)

    def _batch_scan_loop(self):
        while True:
            with self._batch_lock:
                if not self._batch_scans:
                    return
                fid = self._batch_scans.pop(0)
            items = self._db.q("SELECT id FROM batch_items WHERE folder_id=? AND state='waiting' ORDER BY id", (fid,))
            offline = []
            with ThreadPoolExecutor(LOOKUP_THREADS) as pool:
                for _ in pool.map(lambda r: self._batch_lookup(r["id"], fid, offline), items):
                    pass
            stopped = fid in self._batch_stop
            msg = None
            if offline:
                msg = ("Some sites couldn't be reached, so some details or pictures are missing. "
                       "Press “Look up again” when you're online.")
            if not stopped:
                self._db.run("UPDATE batch_folders SET status='ready', message=?, updated_at=? WHERE id=?",
                             (msg, time.time(), fid))
            self._batch_changed(fid, True)

    def _batch_lookup(self, item_id, fid, offline):
        if fid in self._batch_stop:
            return
        it = self._db.one("SELECT * FROM batch_items WHERE id=?", (item_id,))
        if not it or it["state"] != "waiting":
            return
        self._db.run("UPDATE batch_items SET state='looking' WHERE id=?", (item_id,))
        self._batch_changed(fid)
        try:
            if not os.path.exists(it["path"]):
                self._batch_set(item_id, state="error", message="The file is missing.")
                return
            ident = identify.identify(it["path"])
            console = ident["console"] if ident["confident"] else None
            if not console:
                guess = [c for c in consoles.consoles_for_extension(os.path.splitext(it["path"])[1]) if c in gameinfo.SYSTEMS]
                console = guess[0] if len(guess) == 1 else None
            if not console:
                self._batch_set(item_id, state="skipped", format=ident["format"],
                                message=ident["note"] or "Not recognised as a game.")
                return
            c = consoles.get(console) or {}
            if console not in gameinfo.SYSTEMS or not c.get("buildable"):
                self._batch_set(item_id, state="skipped", console=console, format=ident["format"],
                                message="%s isn't supported yet." % c.get("name", console))
                return
            self._batch_set(item_id, console=console, format=ident["format"], serial=ident["serial"],
                            region=ident["region"])
            try:
                m = gameinfo.match_game(console, it["path"], ident, crc=it["crc32"],
                                        cancelled=lambda: fid in self._batch_stop)
            except gameinfo.Offline:
                offline.append(item_id)
                self._batch_set(item_id, state="waiting", message="Waiting for the internet (game list download).")
                return
            if not m["name"]:
                title = gameinfo.clean_filename(it["path"])[0] or ident["title"] or os.path.splitext(it["filename"])[0]
                self._batch_set(item_id, state="notfound", crc32=m.get("crc"), title=title[:50],
                                message="Not in the game lists. It can still be built, without details or pictures.")
                return
            self._batch_apply_match(item_id, console, m["name"], "found" if m["method"] != "name" else "name",
                                    m["method"], m.get("crc"), ident.get("serial"), offline)
        except Exception as e:  # one bad file must never stop the folder
            self._batch_set(item_id, state="error", message="Couldn't read this file: %s" % e)
        finally:
            self._batch_changed(fid)

    def _batch_apply_match(self, item_id, console, name, state, method, crc, serial, offline):
        def note(s):
            if "couldn't" in s:
                offline.append(item_id)
        folder, info = gameinfo.gather(console, name, crc=crc, serial=serial, log=note)
        if not info.get("complete", True):
            offline.append(item_id)
        title = info["title"][:50]
        self._small_icon(folder)
        self._batch_set(item_id, state=state, method=method, match_name=name, crc32=crc, title=title,
                        info_dir=str(folder), has_logo=1 if info.get("icon") == "logo" else 0,
                        message=None if state != "name" else "Matched by name. Check that it's the right game.")

    @staticmethod
    def _small_icon(folder):
        src, dst = Path(folder) / "icon.png", Path(folder) / "icon_small.png"
        if src.exists() and not dst.exists():
            from PIL import Image
            Image.open(src).convert("RGB").resize((96, 96), Image.LANCZOS).save(dst, optimize=True)

    def _batch_set(self, item_id, **fields):
        if not fields:
            return
        cols = ", ".join("%s=?" % k for k in fields)
        self._db.run("UPDATE batch_items SET %s WHERE id=?" % cols, tuple(fields.values()) + (item_id,))

    # ------------------------------------------------------------ review
    def batch_items(self, folder_id):
        rows = self._db.q("""SELECT i.*, b.status build_status, b.message build_message, b.apk_path
                             FROM batch_items i LEFT JOIN builds b ON b.id=i.build_id
                             WHERE i.folder_id=? ORDER BY i.filename COLLATE NOCASE""", (folder_id,))
        for r in rows:
            c = consoles.get(r["console"]) if r["console"] else None
            r["console_short"] = c["short"] if c else None
            r["color"] = c["color"] if c else "#555b66"
            r["size_h"] = _human(r["size"])
            icon = Path(r["info_dir"]) / "icon_small.png" if r["info_dir"] else None
            r["icon"] = ("data:image/png;base64," + base64.b64encode(icon.read_bytes()).decode()) \
                if icon and icon.exists() else None
        return rows

    def batch_item_detail(self, item_id):
        it = self._db.one("SELECT * FROM batch_items WHERE id=?", (item_id,))
        if not it:
            return None
        out = {"item": it, "info": None, "pictures": {}}
        if it["info_dir"] and (Path(it["info_dir"]) / "info.json").exists():
            d = Path(it["info_dir"])
            out["info"] = json.loads((d / "info.json").read_text(encoding="utf-8"))
            for kind in ("box", "icon", "snap", "title", "logo"):
                p = d / (kind + ".png")
                if p.exists():
                    out["pictures"][kind] = "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()
        return out

    def batch_set_include(self, item_ids, include):
        for i in item_ids or []:
            self._db.run("UPDATE batch_items SET include=? WHERE id=?", (1 if include else 0, int(i)))
        return {"ok": True}

    def batch_rename(self, item_id, title):
        title = (title or "").strip()
        if not title:
            return {"ok": False, "error": "The app name can't be empty."}
        self._batch_set(item_id, title=title[:50])
        return {"ok": True}

    def batch_search(self, item_id, text):
        it = self._db.one("SELECT console FROM batch_items WHERE id=?", (item_id,))
        if not it or it["console"] not in gameinfo.SYSTEMS:
            return []
        try:
            return gameinfo.search_names(it["console"], text)
        except gameinfo.Offline:
            return []

    def batch_set_match(self, item_id, name):
        it = self._db.one("SELECT * FROM batch_items WHERE id=?", (item_id,))
        if not it or not name:
            return {"ok": False, "error": "Choose a game."}

        def work():
            offline = []
            try:
                self._batch_set(item_id, state="looking")
                self._batch_changed(it["folder_id"], True)
                self._batch_apply_match(item_id, it["console"], name, "manual", "manual", it["crc32"], it["serial"], offline)
            except Exception as e:
                self._batch_set(item_id, state="error", message="Lookup failed: %s" % e)
            self._batch_changed(it["folder_id"], True)
        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    # ------------------------------------------------------------ building
    def batch_build_all(self, folder_id, item_ids=None):
        f = self._db.one("SELECT * FROM batch_folders WHERE id=?", (folder_id,))
        if not f:
            return {"ok": False, "error": "Folder not found."}
        sql = "SELECT * FROM batch_items WHERE folder_id=? AND include=1 AND state IN ('found','name','manual','notfound')"
        items = self._db.q(sql, (folder_id,))
        if item_ids:
            wanted = {int(x) for x in item_ids}
            items = [i for i in items if i["id"] in wanted]
        queued, problems = 0, []
        for it in items:
            if it["build_id"]:
                b = self._db.one("SELECT status FROM builds WHERE id=?", (it["build_id"],))
                if b and b["status"] in ("queued", "running"):
                    continue
            res = self._batch_build_one(f, it)
            if res.get("ok"):
                queued += 1
            else:
                problems.append({"id": it["id"], "name": it["title"] or it["filename"], "error": res.get("error")})
                self._batch_set(it["id"], message=res.get("error"))
        self._batch_changed(folder_id, True)
        if not queued and not problems:
            return {"ok": False, "error": "Nothing to build. Tick the games you want first."}
        return {"ok": queued > 0, "queued": queued, "problems": problems,
                "error": None if queued else problems[0]["error"]}

    def _batch_build_one(self, folder, it):
        console = it["console"]
        c = consoles.get(console) or {}
        rom = self._db.one("SELECT * FROM roms WHERE path=?", (it["path"],))
        if not rom:
            ident = identify.identify(it["path"])
            rid = self._db.run(
                """INSERT INTO roms(path, filename, size, format, console, detected_console, console_source, title,
                   display_name, serial, region, version, crc32, status, message, added_at, batch)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                (it["path"], it["filename"], it["size"], it["format"], console, ident["console"], "header",
                 ident["title"], it["title"] or it["filename"], it["serial"], it["region"], ident["version"],
                 it["crc32"], "ready", None, time.time()))
            rom = self._db.one("SELECT * FROM roms WHERE id=?", (rid,))
        elif rom["console"] != console:
            self._db.run("UPDATE roms SET console=?, console_source='manual' WHERE id=?", (console, rom["id"]))
        icon = str(Path(it["info_dir"]) / "icon.png") if it["info_dir"] and (Path(it["info_dir"]) / "icon.png").exists() else None
        existing = self._db.one("SELECT * FROM games WHERE id=?", (it["game_id"],)) if it["game_id"] else None
        existing = existing or self._db.one("SELECT * FROM games WHERE rom_id=? ORDER BY batch ASC, updated_at DESC",
                                            (rom["id"],))
        if existing and not existing["batch"]:
            # a game you set up yourself: keep its settings, add the information (and icon if it had none)
            self._db.run("UPDATE games SET info_dir=?, icon_path=COALESCE(icon_path, ?) WHERE id=?",
                         (it["info_dir"], icon, existing["id"]))
            gid = existing["id"]
        else:
            name = (it["title"] or os.path.splitext(it["filename"])[0])[:50]
            data = self.batch_get_settings(folder["id"])
            data.update(rom_id=rom["id"], console=console, app_name=name, icon_path=icon,
                        package_id=existing["package_id"] if existing else self.suggest_package(name),
                        id=existing["id"] if existing else None,
                        tv=folder["target"] == "tv" and "tv" in c.get("features", []))
            saved = self.save_game(data)
            if not saved.get("ok"):
                return saved
            gid = saved["id"]
            self._db.run("UPDATE games SET batch=1, info_dir=? WHERE id=?", (it["info_dir"], gid))
        self._batch_set(it["id"], game_id=gid)
        res = self.generate_apk(gid)
        if res.get("ok"):
            self._batch_set(it["id"], build_id=res["id"], message=None)
        else:
            err = res.get("error") or "Couldn't start the build."
            if "BIOS" in err:
                err = "Needs a %s BIOS: import yours in BIOS Manager." % c.get("short", console)
            res["error"] = err
        return res

    def batch_stop_builds(self, folder_id):
        n = 0
        for it in self._db.q("""SELECT b.id FROM batch_items i JOIN builds b ON b.id=i.build_id
                                WHERE i.folder_id=? AND b.status='queued'""", (folder_id,)):
            if self.cancel_build(it["id"]).get("ok"):
                n += 1
        self._batch_changed(folder_id, True)
        return {"ok": True, "stopped": n}


def _human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%.0f %s" if unit in ("B", "KB") else "%.1f %s") % (n, unit)
        n /= 1024
