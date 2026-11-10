"""Turns one saved game app into a signed, installable APK.

    template APK ──apktool──► folder ──(package ID, name, icon, game, settings)──►
    folder ──apktool──► unsigned APK ──uber-apk-signer──► signed APK in your output folder
"""
import json
import os
import re
import shutil
import time
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from . import adapters, consoles, paths, toolchain
from .toolchain import BuildCancelled, ToolError

STAGES = [
    ("check", "Checking the game and tools"),
    ("game", "Preparing the game file"),
    ("tools", "Preparing build tools"),
    ("key", "Signing key"),
    ("unpack", "Opening the engine template"),
    ("customise", "Adding the game, name, icon and settings"),
    ("pack", "Building the APK"),
    ("sign", "Signing and verifying"),
    ("save", "Saving to your output folder"),
]
TEMPLATE_PACKAGE = "com.tirso.template"


def safe_filename(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip().rstrip(".")
    return name or "Game"


def android_string(text):
    """Escapes text for an Android strings.xml value."""
    s = text.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
    s = escape(s)
    if s[:1] in ("@", "?"):
        s = "\\" + s
    return s


class ApkBuilder:
    def __init__(self, db, game, rom, build_id, output_dir, emit, version_code):
        self.db = db
        self.game = game
        self.rom = rom
        self.build_id = build_id
        self.output_dir = Path(output_dir)
        self.emit = emit
        self.version_code = version_code
        self.cancel_requested = False
        self.lines = []
        self.stage = None
        self.work = paths.sub("builds") / ("build-%d" % build_id)
        self.adapter = adapters.get(rom["console"])

    # ---------------------------------------------------------------- logging
    def log(self, line):
        line = str(line)
        self.lines.append(line)
        self.emit("build_log", {"id": self.build_id, "line": line})

    def set_stage(self, key):
        self.stage = key
        idx = [s[0] for s in STAGES].index(key)
        self.db.run("UPDATE builds SET stage=? WHERE id=?", (key, self.build_id))
        self.emit("build_stage", {"id": self.build_id, "stage": key, "index": idx, "total": len(STAGES)})
        self.log("")
        self.log("== %s ==" % dict(STAGES)[key])
        if self.cancel_requested:
            raise BuildCancelled()

    def cancelled(self):
        return self.cancel_requested

    # ---------------------------------------------------------------- run
    def run(self):
        started = time.time()
        status, message, apk_path, apk_size = "failed", None, None, None
        try:
            apk_path = self._build()
            apk_size = Path(apk_path).stat().st_size
            status, message = "success", "Built %s" % Path(apk_path).name
        except BuildCancelled:
            status, message = "cancelled", "Build cancelled."
            self.log("Build cancelled.")
        except ToolError as e:
            message = str(e)
            self.log("ERROR: " + message)
        except Exception as e:  # unexpected: keep the details in the log
            import traceback
            message = "Unexpected problem: %s" % e
            self.log(traceback.format_exc())
        finally:
            shutil.rmtree(self.work, ignore_errors=True)
            log_text = "\n".join(self.lines)[-300000:]
            (paths.logs_dir() / ("build-%d.log" % self.build_id)).write_text(log_text, encoding="utf-8")
            self.db.run("""UPDATE builds SET status=?, message=?, finished_at=?, apk_path=?, apk_size=?, log=?, data_path=?
                           WHERE id=?""", (status, message, time.time(), apk_path, apk_size, log_text,
                                           getattr(self, "data_path", None), self.build_id))
            self.emit("build_done", {"id": self.build_id, "status": status, "message": message,
                                     "apk_path": apk_path, "seconds": round(time.time() - started)})
        return status

    def _build(self):
        g, rom, ad = self.game, self.rom, self.adapter
        # 1. check -------------------------------------------------------
        self.set_stage("check")
        if not ad:
            raise ToolError("There's no Android template for this console yet.")
        settings = json.loads(g.get("settings_json") or "{}")
        self.tv = ad.is_tv(settings) if ad else False
        info = ad.template_info(tv=self.tv)
        if not info or not info["present"]:
            raise ToolError("The %s %stemplate is missing from the Tirso Compiler folder (templates\\%s)."
                            % (ad.engine, "TV " if self.tv else "", ad.template))
        self.abis = info["abis"]
        if not Path(rom["path"]).exists():
            raise ToolError("The game file is missing: %s" % rom["path"])
        if Path(rom["path"]).suffix.lower() not in ad.rom_formats:
            raise ToolError("%s files can't be used with %s." % (Path(rom["path"]).suffix, ad.engine))
        import hashlib
        h = hashlib.sha256()
        with open(info["apk_path"], "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if info.get("sha256") and h.hexdigest() != info["sha256"]:
            raise ToolError("The %s template file is damaged or was changed (checksum mismatch). "
                            "Copy the templates folder again." % ad.engine)
        self.bios = None
        if (consoles.get(rom["console"]).get("bios") or {}).get("required"):
            self.bios = (self.db.one("SELECT * FROM bios WHERE id=? AND valid=1", (g.get("bios_id"),)) if g.get("bios_id") else None) \
                or self.db.one("SELECT * FROM bios WHERE console=? AND valid=1 ORDER BY preferred DESC", (rom["console"],))
            if not self.bios or not Path(self.bios["stored_path"]).exists():
                raise ToolError("A %s BIOS is required. Import yours in BIOS Manager." % consoles.get(rom["console"])["short"])
            self.log("BIOS: %s (v%s, %s)" % (self.bios["filename"], self.bios["version"] or "?", self.bios["region"] or "region unknown"))
        self.gpu_driver = None
        driver_slot = {"ps2": "ps2", "gc": "dolphin", "wii": "dolphin"}.get(rom["console"])
        if driver_slot:
            raw = self.db.get_setting("gpu_driver_" + driver_slot)
            if raw:
                try:
                    drv = json.loads(raw)
                except ValueError:
                    drv = None
                if drv and Path(drv.get("stored_path", "")).exists():
                    self.gpu_driver = drv
                    self.log("GPU driver: %s %s (used on phones with an Adreno GPU)" % (drv["name"], drv.get("version", "")))
                elif drv:
                    self.log("GPU driver file is missing; building without it. Add it again in BIOS Manager.")
        self.log("Game: %s (%s, %s bytes)" % (g["app_name"], Path(rom["path"]).name, rom["size"]))
        self.log("Template: %s %s (%s)" % (info["name"], info["version"],
                 "TV and phones: 64-bit + 32-bit" if self.tv else "phones: 64-bit"))

        # 1b. game file ------------------------------------------------------
        self.set_stage("game")
        payload, payload_name = self._prepare_payload()
        payload_size = Path(payload).stat().st_size
        self.embedded = payload_size <= consoles.EMBED_LIMIT_BYTES
        if not self.embedded:
            if not getattr(ad, "supports_data_file", False):
                raise ToolError("This game is %.2f GB even after compression, which is too big for one APK "
                                "(limit 1.5 GB)." % (payload_size / 1024 ** 3))
            self.log("The game is %.2f GB, too big to go inside the APK: it will be saved as a separate "
                     "game data file next to the APK." % (payload_size / 1024 ** 3))

        # 2. tools --------------------------------------------------------
        self.set_stage("tools")
        toolchain.ensure_tools(self.log)
        java = toolchain.find_java()
        tools = toolchain.tools_dir()
        framework = paths.sub("tools") / "apktool-framework"
        self.log("Java: " + java)

        # 3. key ----------------------------------------------------------
        self.set_stage("key")
        key = toolchain.ensure_keystore(self.log)
        self.log("Using your signing key from %s" % key["created"])

        # 4. unpack -------------------------------------------------------
        self.set_stage("unpack")
        shutil.rmtree(self.work, ignore_errors=True)
        self.work.mkdir(parents=True)
        dec = self.work / "app"
        code, _ = toolchain.run([java, "-jar", str(tools / "apktool.jar"), "d", "-f", "-s",
                                 "-p", str(framework), "-o", str(dec), info["apk_path"]],
                                log=self.log, cancelled=self.cancelled)
        if code != 0:
            raise ToolError("The engine template couldn't be opened (apktool exit code %d)." % code)

        # 5. customise ------------------------------------------------------
        self.set_stage("customise")
        manifest = dec / "AndroidManifest.xml"
        text = manifest.read_text(encoding="utf-8")
        if TEMPLATE_PACKAGE not in text:
            raise ToolError("The template's Android manifest isn't what Tirso expects.")
        manifest.write_text(text.replace(TEMPLATE_PACKAGE, g["package_id"]), encoding="utf-8")
        self.log("Package ID: " + g["package_id"])

        label = info.get("label_string", "app_name")
        icon_name = info.get("icon_name", "ic_launcher")
        bg_color = info.get("icon_bg_color", "ic_launcher_bg")
        strings = dec / "res" / "values" / "strings.xml"
        st = strings.read_text(encoding="utf-8")
        st, n = re.subn(r'<string name="%s"( [^>]*)?>.*?</string>' % label,
                        lambda m: '<string name="%s"%s>%s</string>' % (label, m.group(1) or "", android_string(g["app_name"])), st)
        if n != 1:
            raise ToolError("The template's app name entry is missing.")
        strings.write_text(st, encoding="utf-8")
        self.log("App name: " + g["app_name"])

        from . import icons
        color = consoles.get(rom["console"])["color"]
        bg = icons.write_icons(dec / "res", g.get("icon_path") if g.get("icon_path") and Path(g["icon_path"]).exists() else None,
                               g["app_name"], color, icon_name)
        colors = dec / "res" / "values" / "colors.xml"
        ct = colors.read_text(encoding="utf-8")
        ct = re.sub(r'(<color name="%s">)#[0-9a-fA-F]+(</color>)' % bg_color, r"\g<1>#ff%s\g<2>" % bg[1:], ct)
        colors.write_text(ct, encoding="utf-8")
        self.log("Icon: " + ("your image" if g.get("icon_path") else "generated from the name"))
        banner = dec / "res" / "drawable-xhdpi" / "tv_banner.png"
        if banner.exists():
            icons.write_banner(banner, g.get("icon_path") if g.get("icon_path") and Path(g["icon_path"]).exists() else None,
                               g["app_name"], color)
            self.log("TV home-screen banner created.")

        yml = dec / "apktool.yml"
        y = yml.read_text(encoding="utf-8")
        y = re.sub(r"versionCode: .*", "versionCode: %d" % self.version_code, y)
        y = re.sub(r"versionName: .*", "versionName: 1.%d" % self.version_code, y)
        yml.write_text(y, encoding="utf-8")
        self.log("Version: 1.%d (code %d)" % (self.version_code, self.version_code))

        game_dir = dec / "assets" / "tirso"
        game_dir.mkdir(parents=True, exist_ok=True)
        for old in game_dir.glob("game.*"):
            old.unlink()
        build_id = "%s-%d" % (g["package_id"], self.version_code)
        if getattr(ad, "supports_data_file", False):
            cfg = ad.game_json(g, rom, build_id, payload_name, payload_size, embedded=self.embedded, bios=self.bios)
        else:
            cfg = ad.game_json(g, rom, build_id, payload_name, payload_size)
        cfg.setdefault("core", info.get("core"))
        if self.gpu_driver:
            cfg["gpu_driver"] = "gpu_driver.zip"
            cfg["gpu_driver_name"] = self.gpu_driver["name"]
            cfg["gpu_driver_sha1"] = self.gpu_driver["sha1"]
        (game_dir / "game.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        if cfg.get("variables"):
            self.log("Engine settings (%s): %s" % (g["profile"], ", ".join("%s=%s" % kv for kv in cfg["variables"].items())))

        # 6. pack ---------------------------------------------------------
        self.set_stage("pack")
        unsigned = self.work / "unsigned.apk"
        code, _ = toolchain.run([java, "-jar", str(tools / "apktool.jar"), "b", "-f",
                                 "-p", str(framework), "-o", str(unsigned), str(dec)],
                                log=self.log, cancelled=self.cancelled)
        if code != 0 or not unsigned.exists():
            raise ToolError("The APK couldn't be built (apktool exit code %d). See the log for details." % code)
        if self.embedded:
            self._add_game(unsigned, payload, "assets/tirso/" + cfg["rom"])
        if self.bios:
            self.log("Adding your BIOS to the APK…")
            with zipfile.ZipFile(unsigned, "a") as z:
                z.write(self.bios["stored_path"], "assets/tirso/bios.bin", compress_type=zipfile.ZIP_DEFLATED)
        info_dir = Path(g["info_dir"]) if g.get("info_dir") else None
        if info_dir and (info_dir / "info.json").exists():
            self._add_info(unsigned, info_dir)
        if self.gpu_driver:
            self.log("Adding the GPU driver to the APK…")
            with zipfile.ZipFile(unsigned, "a") as z:
                z.write(self.gpu_driver["stored_path"], "assets/tirso/gpu_driver.zip", compress_type=zipfile.ZIP_STORED)

        # 7. sign ---------------------------------------------------------
        self.set_stage("sign")
        signed_dir = self.work / "signed"
        code, out = toolchain.run([java, "-jar", str(tools / "uber-apk-signer.jar"), "-a", str(unsigned),
                                   "--ks", str(toolchain.keystore_path()), "--ksAlias", key["alias"],
                                   "--ksPass", key["password"], "--ksKeyPass", key["password"],
                                   "-o", str(signed_dir), "--allowResign"],
                                  log=self.log, cancelled=self.cancelled)
        signed = next(iter(signed_dir.glob("*.apk")), None) if signed_dir.exists() else None
        if code != 0 or not signed:
            raise ToolError("Signing failed (exit code %d). See the log for details." % code)
        if "VERIFY" in out and re.search(r"VERIFY.*\n.*(FAIL|ERROR)", out):
            raise ToolError("The signed APK failed verification.")
        self._validate(signed, cfg)

        # 8. save ---------------------------------------------------------
        self.set_stage("save")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # TV builds get "(For TV)" in the file name only (the app name on the TV stays the game's name).
        file_stem = safe_filename(g["app_name"]) + (" (For TV)" if self.tv else "")
        final = self.output_dir / (file_stem + ".apk")
        shutil.copyfile(signed, final)
        self.log("Saved: %s (%.1f MB)" % (final, final.stat().st_size / 1048576))
        self.data_path = None
        if not self.embedded:
            # Saved as <output>/<App name> (game data)/<package>/game.cso, so copying by hand is just
            # "copy the <package> folder into Android/obb on the phone".
            data = self.output_dir / ("%s (game data)" % file_stem) / g["package_id"] / payload_name
            data.parent.mkdir(parents=True, exist_ok=True)
            self.log("Saving the game data file (%.2f GB)…" % (payload_size / 1024 ** 3))
            tmp = data.with_name(data.name + ".part")
            shutil.copyfile(payload, tmp)
            os.replace(tmp, data)
            self.data_path = str(data)
            self.log("Saved: %s" % data)
            self.log("Install it with Tirso's Install button, or copy the folder %s into Android/obb on the phone."
                     % g["package_id"])
        return str(final)

    def _prepare_payload(self):
        """Returns (file to embed, its name inside the APK). PSP ISOs become CSO (cached)."""
        rom, ad = self.rom, self.adapter
        src = Path(rom["path"])
        ext = src.suffix.lower()
        track = None
        if getattr(ad, "compress_iso", False) and ext in (".cue", ".bin"):
            from .discs import CueError, parse_cue, raw_track
            if ext == ".cue":
                try:
                    cue = parse_cue(str(src))
                except CueError as e:
                    raise ToolError(str(e))
                if cue["missing"]:
                    raise ToolError("The CUE file lists %s, which isn't next to it. Keep the .cue and .bin files "
                                    "in the same folder." % ", ".join(cue["missing"]))
                track = cue["data"]
                if not track:
                    raise ToolError("This CUE file has no game data track.")
                if cue["audio_tracks"]:
                    self.log("Note: the disc's %d music track(s) are left out (ARMSX2 plays the game data only)."
                             % cue["audio_tracks"])
            else:
                track = raw_track(str(src))
            self.log("BIN/CUE game: data track in %s (%d-byte sectors)." % (Path(track["path"]).name, track["sector"]))
        if getattr(ad, "compress_format", None) == "gcz" and ext in (".iso", ".gcm"):
            from .discs import compress_gcz
            cache = paths.sub("cache") / ("%s.gcz" % (rom["sha1"] or "nohash"))
            if cache.exists() and rom["sha1"]:
                self.log("Using the compressed copy made earlier (%.0f MB)." % (cache.stat().st_size / 1048576))
            else:
                self.log("Compressing the game to GCZ (Dolphin's compressed format; one time only, about 30 s per GB)…")
                mark = {"last": -10}

                def gprog(pct):
                    if pct >= mark["last"] + 10:
                        mark["last"] = pct
                        self.log("  %d%%" % pct)
                size = compress_gcz(src, cache, wii=ad.console == "wii", progress=gprog, cancelled=self.cancelled)
                if size is None:
                    raise BuildCancelled()
                self.log("Compressed %.0f MB to %.0f MB." % (rom["size"] / 1048576, size / 1048576))
            return str(cache), "game.gcz"
        if (ad.console == "psp" or getattr(ad, "compress_iso", False)) and (ext == ".iso" or track):
            from .discs import compress_cso
            cache = paths.sub("cache") / ("%s.cso" % (rom["sha1"] or "nohash"))
            if cache.exists() and rom["sha1"]:
                self.log("Using the compressed copy made earlier (%.0f MB)." % (cache.stat().st_size / 1048576))
            else:
                self.log("Compressing the game to CSO so it takes less space (one time only, about 30 s per GB)…")
                mark = {"last": -10}

                def prog(pct):
                    if pct >= mark["last"] + 10:
                        mark["last"] = pct
                        self.log("  %d%%" % pct)
                size = compress_cso(src, cache, progress=prog, cancelled=self.cancelled, track=track)
                if size is None:
                    raise BuildCancelled()
                self.log("Compressed %.0f MB to %.0f MB." % (rom["size"] / 1048576, size / 1048576))
            return str(cache), "game.cso"
        self.log("Game file: %s (%.1f MB)" % (src.name, src.stat().st_size / 1048576))
        return str(src), ad.rom_asset_name(str(src)) if ad.console not in ("psp", "ps2", "gc", "wii") else "game" + ext

    def _add_info(self, apk, info_dir):
        """Game information for the in-game "Game information" window: assets/tirso/info/."""
        from . import gameinfo
        try:
            info = json.loads((info_dir / "info.json").read_text(encoding="utf-8"))
        except ValueError:
            self.log("The saved game information is damaged; building without it.")
            return
        data = gameinfo.app_info(info)
        with zipfile.ZipFile(apk, "a") as z:
            z.writestr("assets/tirso/info/info.json", json.dumps(data, ensure_ascii=False, indent=1),
                       compress_type=zipfile.ZIP_DEFLATED)
            for kind in data.get("pictures", []):
                pic = info_dir / (kind + ".png")
                if pic.exists():
                    z.write(pic, "assets/tirso/info/%s.png" % kind, compress_type=zipfile.ZIP_STORED)
        self.log("Game information added (%s%s)." % (
            "description from %s, " % data["description_source"] if data.get("description_source") else "no description, ",
            "%d picture(s)" % len(data.get("pictures", []))))

    def _add_game(self, apk, payload, name):
        """Adds the game to the built APK. Already-compressed formats are stored as they are."""
        size = Path(payload).stat().st_size
        stored = Path(payload).suffix.lower() in (".cso", ".chd", ".rvz", ".gcz", ".wbfs", ".zip") or size > 256 * 1024 * 1024
        self.log("Adding the game to the APK (%.1f MB, %s)…" % (size / 1048576, "stored" if stored else "compressed"))
        with zipfile.ZipFile(apk, "a", allowZip64=True) as z:
            z.write(payload, name, compress_type=zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED)
        if self.cancel_requested:
            raise BuildCancelled()

    def _validate(self, apk, cfg):
        with zipfile.ZipFile(apk) as z:
            names = set(z.namelist())
            bad = z.testzip()
            if bad:
                raise ToolError("The APK is damaged (%s)." % bad)
            needed = {"AndroidManifest.xml", "classes.dex", "assets/tirso/game.json"}
            if cfg.get("rom"):
                needed.add("assets/tirso/" + cfg["rom"])
            if cfg.get("bios"):
                needed.add("assets/tirso/bios.bin")
            if cfg.get("gpu_driver"):
                needed.add("assets/tirso/gpu_driver.zip")
            # every processor type must have an engine: the main one, or an alternative
            cores = [cfg["core"]] + [e["core"] for e in cfg.get("alt_engines", [])]
            for abi in self.abis:
                if not any("lib/%s/%s" % (abi, c) in names for c in cores):
                    missing_engine = "lib/%s/(engine)" % abi
                    raise ToolError("The APK has no engine for %s devices (%s)." % (abi, missing_engine))
            missing = needed - names
            if missing:
                raise ToolError("The APK is missing: %s" % ", ".join(sorted(missing)))
            if cfg.get("rom") and z.getinfo("assets/tirso/" + cfg["rom"]).file_size != cfg["rom_size"]:
                raise ToolError("The game inside the APK doesn't match the original file.")
            if not any(n.startswith("META-INF/") and n.endswith((".RSA", ".SF")) for n in names):
                self.log("Note: signed with APK Signature Scheme v2/v3 only.")
        self.log("APK checked: game, engine and settings are all inside, signature verified.")
