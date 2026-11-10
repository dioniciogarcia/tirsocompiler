"""Engine adapters: everything engine-specific lives here, behind one interface.

Each adapter says which template it uses, which ROM formats it takes, and turns
a performance profile into that engine's real settings.
"""
import json
from pathlib import Path

from . import consoles, toolchain


class EngineAdapter:
    supports_data_file = False
    compress_iso = False
    console = None
    engine = None
    template = None          # folder name under templates/
    rom_formats = ()
    pad = None
    launch_activity = "com.tirso.player.GameActivity"   # the screen that opens the game

    def template_dir(self) -> Path:
        return toolchain.templates_dir() / self.template

    def template_info(self, tv=False):
        """Phone template (64-bit) or TV template (64-bit + 32-bit, TV home screen)."""
        meta = self.template_dir() / "template.json"
        if not meta.exists():
            return None
        info = json.loads(meta.read_text(encoding="utf-8"))
        if tv:
            if not info.get("apk_tv"):
                return None
            info["apk"], info["sha256"] = info["apk_tv"], info.get("sha256_tv")
            info["abis"] = ["arm64-v8a", "armeabi-v7a"]
        else:
            info["abis"] = ["arm64-v8a"]
        info["apk_path"] = str(self.template_dir() / info["apk"])
        info["present"] = Path(info["apk_path"]).exists()
        return info

    @staticmethod
    def is_tv(settings):
        return bool((settings or {}).get("tv"))

    def variables(self, profile: str, settings: dict) -> dict:
        raise NotImplementedError

    screen_mode = "overlay"
    aspect = 4 / 3
    system_zip = None

    def rom_asset_name(self, rom_path: str) -> str:
        return "game" + Path(rom_path).suffix.lower()

    def screen(self, settings):
        """(screen_mode, aspect, forced orientation or None)"""
        return self.screen_mode, self.aspect, None

    def game_json(self, game, rom, build_id, payload_name=None, payload_size=None) -> dict:
        settings = json.loads(game.get("settings_json") or "{}")
        mode, aspect, orientation = self.screen(settings)
        if self.is_tv(settings):
            orientation = "landscape"  # TV builds always open sideways
        return {
            "title": game["app_name"],
            "console_name": (consoles.get(self.console) or {}).get("name", ""),
            "rom": payload_name or self.rom_asset_name(rom["path"]),
            "rom_size": payload_size if payload_size is not None else rom["size"],
            "rom_sha1": rom["sha1"] or "",
            "core": self.template_info()["core"],
            "pad": self.pad,
            "orientation": orientation or game["orientation"],
            "screen_mode": mode,
            "aspect": round(aspect, 4),
            "system_zip": self.system_zip or "",
            "shader": json.loads(game.get("settings_json") or "{}").get("screen_filter")
                      or (consoles.get(self.console) or {}).get("screen_filter_default", "default"),
            "auto_resume": True,
            "rumble": True,
            "variables": self.variables(game["profile"], settings),
            "alt_engines": self.alt_engines(game["profile"], settings),
            "build_id": build_id,
        }

    def alt_engines(self, profile, settings):
        """Other engines in the APK, used when the main one isn't installed for the device's processor."""
        return []


class N64Adapter(EngineAdapter):
    console = "n64"
    engine = "Mupen64Plus-Next"
    template = "n64"
    rom_formats = (".z64", ".n64", ".v64")
    pad = "n64"

    # Real Mupen64Plus-Next core options (libretro_core_options.h).
    PROFILES = {
        "battery": {"mupen64plus-43screensize": "320x240", "mupen64plus-FrameDuping": "True",
                    "mupen64plus-BilinearMode": "standard"},
        "balanced": {"mupen64plus-43screensize": "640x480", "mupen64plus-FrameDuping": "True"},
        "performance": {"mupen64plus-43screensize": "640x480", "mupen64plus-FrameDuping": "True",
                        "mupen64plus-ThreadedRenderer": "True"},
        "quality": {"mupen64plus-43screensize": "1280x960", "mupen64plus-FrameDuping": "False"},
    }
    COMMON = {"mupen64plus-rdp-plugin": "gliden64", "mupen64plus-cpucore": "dynamic_recompiler",
              "mupen64plus-aspect": "4:3", "mupen64plus-pak1": "memory"}

    # Widescreen hack: GLideN64 "16:9 adjusted" draws the wider view the game
    # would show on a 16:9 TV, at a 16:9 rendering resolution.
    WIDE_SIZE = {"battery": "640x360", "balanced": "960x540", "performance": "960x540", "quality": "1920x1080"}

    # TV devices have weak graphics chips. By default GLideN64 copies the colour and
    # depth buffers back to the CPU every frame ("Software" depth copy is very slow there).
    # Off breaks only a few effects (e.g. Pokemon Snap photos, some sun glare).
    # No threaded renderer on TV: it speeds up drawing but makes the game react a frame later, which on top
    # of the TV's own delay made controls feel slow. (The TV is also asked for its game mode.)
    TV_LIGHT = {"mupen64plus-EnableCopyDepthToRDRAM": "Off", "mupen64plus-EnableCopyColorToRDRAM": "Off",
                "mupen64plus-ThreadedRenderer": "False", "mupen64plus-FrameDuping": "True",
                "mupen64plus-MultiSampling": "0", "mupen64plus-EnableShadersStorage": "True"}
    TV_SIZE = {"battery": "320x240", "balanced": "640x480", "performance": "320x240", "quality": "640x480"}
    TV_WIDE = {"battery": "640x360", "balanced": "640x360", "performance": "640x360", "quality": "960x540"}

    def variables(self, profile, settings=None):
        settings = settings or {}
        v = dict(self.COMMON)
        v.update(self.PROFILES.get(profile, self.PROFILES["balanced"]))
        if settings.get("widescreen"):
            v["mupen64plus-aspect"] = "16:9 adjusted"
            v["mupen64plus-169screensize"] = self.WIDE_SIZE.get(profile, "960x540")
        if self.is_tv(settings):
            v.update(self.TV_LIGHT)
            v["mupen64plus-43screensize"] = self.TV_SIZE.get(profile, "640x480")
            if settings.get("widescreen"):
                v["mupen64plus-169screensize"] = self.TV_WIDE.get(profile, "640x360")
        res = self.RES.get(settings.get("resolution"))
        if res:  # a resolution picked in the builder wins over the profile (and the TV defaults)
            v["mupen64plus-43screensize"], v["mupen64plus-169screensize"] = res
        return v

    # builder choice -> (4:3 size, 16:9 size), all real GLideN64 option values
    RES = {"720p": ("960x720", "1280x720"), "1080p": ("1440x1080", "1920x1080"), "1440p": ("1920x1440", "2560x1440")}


    def screen(self, settings):
        return "overlay", (16 / 9 if settings.get("widescreen") else 4 / 3), None


class NdsAdapter(EngineAdapter):
    console = "nds"
    engine = "melonDS DS"
    template = "nds"
    rom_formats = (".nds",)
    pad = "nds"

    # value for melonds_screen_layout1, screen mode, picture width/height, orientation
    # layout, swapped layout (R3), screen mode, picture width/height, orientation
    LAYOUTS = {
        "stacked": ("top-bottom", "bottom-top", "above", 256 / 384, "portrait"),
        "side": ("left-right", "right-left", "beside", 512 / 192, "landscape"),
        "large": ("hybrid-top", "hybrid-bottom", "beside", 768 / 384, "landscape"),
    }
    # "Turns with the phone": stacked when upright, large top screen when sideways.
    # The player switches between the two live (melonDS DS applies layout changes immediately).
    ROTATE = {"portrait": "stacked", "landscape": "large"}
    COMMON = {
        "melonds_console_mode": "ds",
        "melonds_sysfile_mode": "builtin",
        "melonds_boot_mode": "direct",
        "melonds_touch_mode": "auto",       # finger, or right stick + R2 with a controller
        "melonds_mic_input": "microphone",  # the phone's microphone (the app switches to silence if not allowed)
        "melonds_mic_input_active": "always",
        "melonds_show_cursor": "timeout",   # the pencil shows while the right stick moves it
        "melonds_render_mode": "software",
        "melonds_threaded_renderer": "enabled",
        "melonds_jit_enable": "enabled",
        "melonds_number_of_screen_layouts": "2",  # the player handles L3 (full screen) and R3 (swap) itself
        "melonds_hybrid_ratio": "2",
        "melonds_hybrid_small_screen": "both",  # sideways: small top + touch screen on the right
        "melonds_screen_gap": "0",
    }

    def rotates(self, settings):
        return settings.get("ds_layout") == "rotate" and not self.is_tv(settings)

    def layout(self, settings):
        if self.is_tv(settings):
            return self.LAYOUTS["large"]  # TV: big top screen left; small top + touch screen right
        if self.rotates(settings):
            return self.LAYOUTS[self.ROTATE["landscape"]]
        return self.LAYOUTS.get(settings.get("ds_layout"), self.LAYOUTS["stacked"])

    def variables(self, profile, settings=None):
        v = dict(self.COMMON)
        lay = self.layout(settings or {})
        v["melonds_screen_layout1"], v["melonds_screen_layout2"] = lay[0], lay[1]
        if profile == "battery":
            v["melonds_threaded_renderer"] = "disabled"
        return v

    def screen(self, settings):
        _, _, mode, aspect, orientation = self.layout(settings)
        return mode, aspect, ("auto" if self.rotates(settings) else orientation)

    def game_json(self, game, rom, build_id, payload_name=None, payload_size=None):
        cfg = super().game_json(game, rom, build_id, payload_name, payload_size)
        settings = json.loads(game.get("settings_json") or "{}")
        if self.rotates(settings):
            cfg["rotate_layouts"] = {}
            for turn, name in self.ROTATE.items():
                l1, l2, mode, aspect, _ = self.LAYOUTS[name]
                cfg["rotate_layouts"][turn] = {
                    "screen_mode": mode, "aspect": round(aspect, 4),
                    "variables": {"melonds_screen_layout1": l1, "melonds_screen_layout2": l2},
                }
        return cfg

    # 32-bit TV devices: melonDS DS has no speed booster (JIT) there, DeSmuME does.
    # Same layout and the same controller buttons: right stick = pencil, R2 = touch, L3 = full screen, R3 = swap.
    def alt_engines(self, profile, settings):
        if not self.is_tv(settings):
            return []
        return [{
            "core": "libdesmume_libretro_android.so",
            "label": "DeSmuME",
            "variables": {
                "desmume_cpu_mode": "jit",
                "desmume_num_cores": "2",
                "desmume_frameskip": "1" if profile in ("performance", "battery") else "0",
                "desmume_internal_resolution": "256x192",
                "desmume_screens_layout": "hybrid/top",
                "desmume_hybrid_layout_ratio": "2:1",
                "desmume_hybrid_showboth_screens": "enabled",
                "desmume_pointer_type": "touch",
                "desmume_pointer_mouse": "enabled",
                "desmume_pointer_device_r": "emulated",
                "desmume_pointer_colour": "white",
            },
        }]


class GbaAdapter(EngineAdapter):
    """Game Boy Advance: mGBA (64-bit phones, and 32-bit TV devices with the TV template)."""
    console = "gba"
    engine = "mGBA"
    template = "gba"
    rom_formats = (".gba",)
    pad = "gba"
    aspect = 3 / 2

    # Real mGBA libretro option values (libretro_core_options.h).
    COMMON = {
        "mgba_use_bios": "OFF",                  # built-in BIOS: no BIOS file needed
        "mgba_skip_bios": "ON",
        "mgba_idle_optimization": "Remove Known",
        "mgba_interframe_blending": "mix_smart", # flicker-based transparency looks right
        "mgba_color_correction": "OFF",
    }

    def variables(self, profile, settings=None):
        v = dict(self.COMMON)
        v["mgba_frameskip"] = "auto" if profile in ("battery", "performance") else "disabled"
        return v


class PspAdapter(EngineAdapter):
    console = "psp"
    engine = "PPSSPP"
    template = "psp"
    rom_formats = (".iso", ".cso")
    pad = "psp"
    aspect = 16 / 9
    system_zip = "ppsspp_system.zip"

    PROFILES = {
        "battery": {"ppsspp_internal_resolution": "480x272"},
        "balanced": {"ppsspp_internal_resolution": "960x544"},
        "performance": {"ppsspp_internal_resolution": "960x544"},
        "quality": {"ppsspp_internal_resolution": "1440x816"},
    }
    COMMON = {"ppsspp_backend": "opengl", "ppsspp_cpu_core": "JIT", "ppsspp_frame_duplication": "enabled",
              "ppsspp_auto_frameskip": "disabled"}

    def rom_asset_name(self, rom_path):
        return "game.cso"  # ISOs are compressed to CSO before packaging

    RES = {"720p": "1440x816", "1080p": "1920x1088", "1440p": "2400x1360"}  # PPSSPP 3x, 4x, 5x

    def variables(self, profile, settings=None):
        v = dict(self.COMMON)
        v.update(self.PROFILES.get(profile, self.PROFILES["balanced"]))
        res = self.RES.get((settings or {}).get("resolution"))
        if res:
            v["ppsspp_internal_resolution"] = res
        return v


class Ps2Adapter(EngineAdapter):
    """PS2 uses ARMSX2's own Android app with a Tirso boot screen in front (see templates/ps2)."""
    console = "ps2"
    engine = "ARMSX2"
    template = "ps2"
    rom_formats = (".iso", ".cue", ".bin", ".cso", ".chd")
    pad = None
    supports_data_file = True   # games over 1.5 GB ship as APK + data file
    compress_iso = True         # ISO -> CSO before packaging (ARMSX2 reads CSO)
    launch_activity = "com.armsx2.tirso.TirsoBootActivity"

    UPSCALE = {"720p": 2, "1080p": 3, "1440p": 4}  # PCSX2's own labels: 2x ~720p, 3x ~1080p, 4x ~1440p

    def variables(self, profile, settings=None):
        return {}  # ARMSX2 picks its renderer per device; its own menu changes settings

    def game_json(self, game, rom, build_id, payload_name=None, payload_size=None, embedded=True, bios=None):
        settings = json.loads(game.get("settings_json") or "{}")
        return {
            "title": game["app_name"],
            "console_name": (consoles.get(self.console) or {}).get("name", ""),
            "orientation": game.get("orientation") or "landscape",  # landscape / portrait / auto
            "widescreen": bool(settings.get("widescreen")),  # ARMSX2 widescreen patches + 16:9
            "upscale": self.UPSCALE.get(settings.get("resolution"), 1),  # ARMSX2 resolution multiplier
            "rom": payload_name if embedded else "",
            "data_file": "" if embedded else payload_name,
            "rom_size": payload_size,
            "rom_sha1": rom["sha1"] or "",
            "bios": "bios.bin" if bios else "",
            # The size of the file actually packed into the APK (not the size remembered at import).
            "bios_size": _file_size(bios) if bios else -1,
            "build_id": build_id,
        }


def _file_size(bios):
    try:
        return Path(bios["stored_path"]).stat().st_size
    except OSError:
        return bios["size"]


class DolphinAdapter(EngineAdapter):
    """GameCube and Wii: libretro Dolphin inside the Tirso player."""
    engine = "Dolphin"
    template = "dolphin"
    system_zip = "dolphin_system.zip"
    supports_data_file = True     # Wii games are usually over 1.5 GB: APK + data file
    compress_format = "gcz"       # plain ISOs become GCZ (Dolphin reads it); RVZ/WBFS/GCZ are used as they are
    wii = False

    # Real libretro Dolphin option values (Source/Core/DolphinLibretro/Common/Options.cpp).
    COMMON = {
        "dolphin_renderer": "Hardware",
        "dolphin_cpu_core": "4",                    # JIT ARM64
        "dolphin_main_cpu_thread": "enabled",       # dual core
        "dolphin_dsp_hle": "enabled",
        "dolphin_fast_disc_speed": "enabled",
        "dolphin_skip_gc_bios": "enabled",
        # Dolphin's default "specialized" shaders: every effect is drawn. (The async "skip rendering" mode
        # skipped effects drawn only once, e.g. Mario Superstar Baseball's field textures, which then
        # stayed as noise.) Shaders are cached, so the first-time stutter happens once.
        "dolphin_shader_compilation_mode": "0",
        # "Stretch" is what libretro Dolphin is built around: it then reports 4:3 or 16:9 to the app
        # by whether the game runs widescreen (its own 16:9 mode or the widescreen hack).
        "dolphin_aspect_ratio": "3",
        "dolphin_enable_rumble": "enabled",
        "dolphin_osd_enabled": "disabled",
        "dolphin_ir_mode": "1",                     # right stick = Wii pointer (absolute)
    }
    EFB = {"battery": "1", "performance": "1", "balanced": "1", "quality": "2"}
    RES = {"720p": "2", "1080p": "3", "1440p": "4"}

    def rom_asset_name(self, rom_path):
        ext = Path(rom_path).suffix.lower()
        return "game.gcz" if ext in (".iso", ".gcm") else "game" + ext

    def variables(self, profile, settings=None):
        settings = settings or {}
        v = dict(self.COMMON)
        v["dolphin_efb_scale"] = self.RES.get(settings.get("resolution")) or self.EFB.get(profile, "1")
        wide = bool(settings.get("widescreen"))
        v["dolphin_widescreen_hack"] = "enabled" if wide else "disabled"
        if self.wii:
            v["dolphin_widescreen"] = "enabled" if wide else "disabled"   # the Wii's own 16:9 setting
            v["dolphin_wiimote_default_device"] = self.controls(settings)
            v["dolphin_hotkey_sideways_toggle"] = "Disabled"
        return v

    def controls(self, settings):
        return "frontend"

    def screen(self, settings):
        return "overlay", (16 / 9 if settings.get("widescreen") else 4 / 3), None

    def game_json(self, game, rom, build_id, payload_name=None, payload_size=None, embedded=True, bios=None):
        cfg = super().game_json(game, rom, build_id, payload_name, payload_size)
        settings = json.loads(game.get("settings_json") or "{}")
        cfg["pad"] = self.pad_for(settings)
        cfg["controller_type"] = 1     # plug a controller into port 1 (the Wii controller comes from the device option)
        cfg["preserve_gl"] = True      # Dolphin keeps its own OpenGL state between frames
        cfg["graphics"] = settings.get("graphics") if settings.get("graphics") in ("vulkan", "opengl") else "vulkan"
        if not embedded:
            cfg["rom"], cfg["data_file"] = "", payload_name
        return cfg

    def pad_for(self, settings):
        return "gc"


class GcAdapter(DolphinAdapter):
    console = "gc"
    rom_formats = (".iso", ".gcm", ".gcz", ".rvz")
    pad = "gc"


class WiiAdapter(DolphinAdapter):
    console = "wii"
    rom_formats = (".iso", ".gcz", ".rvz", ".wbfs")
    pad = "wii_sideways"
    wii = True
    CONTROLS = ("sideways", "nunchuk", "classic", "gamecube")

    def controls(self, settings):
        c = settings.get("wii_controls")
        return c if c in self.CONTROLS else "sideways"

    def pad_for(self, settings):
        c = self.controls(settings)
        return "gc" if c == "gamecube" else "wii_" + c


ADAPTERS = {a.console: a for a in (N64Adapter(), NdsAdapter(), GbaAdapter(), PspAdapter(), Ps2Adapter(), GcAdapter(), WiiAdapter())}


def get(console):
    return ADAPTERS.get(console)
