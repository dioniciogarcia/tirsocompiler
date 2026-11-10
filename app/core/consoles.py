"""Console and engine registry.

Every console Tirso knows about is described here once. The rest of the app
reads this table instead of hard-coding console details, so adding a console
later means adding an entry here plus its engine adapter and Android template.

`buildable` stays False until that console's Android template has been built
and tested on a real phone. The interface uses it to label consoles honestly.
"""

CONSOLES = [
    {
        "id": "n64",
        "name": "Nintendo 64",
        "short": "N64",
        "color": "#E4002B",
        "engine": "Mupen64Plus-Next",
        "engine_source": "https://github.com/libretro/mupen64plus-libretro-nx",
        "license": "GPL-2.0",
        "extensions": [".z64", ".n64", ".v64"],
        "bios": None,
        "features": ["widescreen", "tv"],
        "phase": 2,
        "status": "Ready",
        "buildable": False,
    },
    {
        "id": "gba",
        "name": "Game Boy Advance",
        "short": "GBA",
        "color": "#5B4DB5",
        "engine": "mGBA",
        "engine_source": "https://github.com/libretro/mgba",
        "license": "MPL-2.0",
        "extensions": [".gba"],
        "bios": None,
        "features": ["tv"],
        "widescreen_note": "The Game Boy Advance screen is 3:2 and mGBA has no widescreen hack; it already fills most of a phone screen sideways.",
        "phase": 6,
        "status": "Ready",
        "buildable": False,
    },
    {
        "id": "nds",
        "name": "Nintendo DS",
        "short": "DS",
        "color": "#8A8F98",
        "engine": "melonDS DS",
        "engine_source": "https://github.com/JesseTG/melonds-ds",
        "license": "GPL-3.0",
        "extensions": [".nds"],
        "bios": {"required": False, "note": "Not needed: melonDS DS uses its own built-in BIOS. Your own DS BIOS files will be used in a later update."},
        "screen_layouts": [
            {"id": "rotate", "name": "Turns with the phone", "desc": "Upright: stacked screens with controls below. Sideways: big top screen, with a small copy of it and the touch screen beside it. Switches as you turn the phone."},
            {"id": "stacked", "name": "Stacked", "desc": "Both screens one above the other, phone held upright, controls below. Closest to a real DS."},
            {"id": "side", "name": "Side by side", "desc": "Screens next to each other, phone sideways, controls at the sides."},
            {"id": "large", "name": "Large top screen", "desc": "Big top screen, with a small copy of it and the touch screen beside it, phone sideways."},
        ],
        "features": ["tv"],
        "widescreen_note": "The DS has two 4:3 screens and melonDS has no widescreen hack. Choose a screen layout in step 4 instead.",
        "phase": 3,
        "status": "Ready",
        "buildable": False,
    },
    {
        "id": "psp",
        "name": "PlayStation Portable",
        "short": "PSP",
        "color": "#3D7EFF",
        "engine": "PPSSPP",
        "engine_source": "https://github.com/hrydgard/ppsspp",
        "license": "GPL-2.0+",
        "extensions": [".iso", ".cso"],
        "bios": None,
        "features": ["tv"],
        "widescreen_note": "PSP games are already widescreen (16:9), so no hack is needed.",
        "phase": 3,
        "status": "Ready",
        "buildable": False,
    },
    {
        "id": "ps2",
        "name": "PlayStation 2",
        "short": "PS2",
        "color": "#4B5BD7",
        "engine": "ARMSX2",
        "engine_source": "https://github.com/ARMSX2/ARMSX2",
        "license": "GPL-3.0",
        "extensions": [".iso", ".cue", ".bin", ".cso", ".chd"],
        "bios": {"required": True, "note": "Required. Import a BIOS dumped from your own PS2."},
        "features": ["widescreen"],
        "widescreen_how": "ARMSX2's widescreen patches (for games that have one) + a 16:9 picture.",
        "phase": 5,
        "status": "Ready",
        "buildable": False,
    },
    {
        "id": "gc",
        "name": "GameCube",
        "short": "GC",
        "color": "#6A4FC9",
        "engine": "Dolphin",
        "engine_source": "https://github.com/dolphin-emu/dolphin",
        "license": "GPL-2.0+",
        "extensions": [".iso", ".gcm", ".gcz", ".rvz"],
        "bios": None,
        "features": ["widescreen"],
        "widescreen_how": "Dolphin's widescreen hack (draws a wider 16:9 view).",
        "graphics": [
            {"id": "vulkan", "name": "Vulkan", "desc": "Faster on most phones, and uses your custom GPU driver if you added one. Recommended."},
            {"id": "opengl", "name": "OpenGL ES", "desc": "The older way. Pick it if a game shows a black screen or broken graphics with Vulkan."},
        ],

        "phase": 6,
        "status": "Ready",
        "buildable": False,
    },
    {
        "id": "wii",
        "name": "Wii",
        "short": "Wii",
        "color": "#28B5E8",
        "engine": "Dolphin",
        "engine_source": "https://github.com/dolphin-emu/dolphin",
        "license": "GPL-2.0+",
        "extensions": [".iso", ".gcz", ".rvz", ".wbfs"],
        "bios": None,
        "features": ["widescreen"],
        "widescreen_how": "The Wii's own 16:9 mode (most Wii games support it), plus Dolphin's widescreen hack.",
        "graphics": [
            {"id": "vulkan", "name": "Vulkan", "desc": "Faster on most phones, and uses your custom GPU driver if you added one. Recommended."},
            {"id": "opengl", "name": "OpenGL ES", "desc": "The older way. Pick it if a game shows a black screen or broken graphics with Vulkan."},
        ],

        "controls": [
            {"id": "sideways", "name": "Wii Remote sideways", "desc": "Held like an NES pad: D-pad, 1, 2. New Super Mario Bros. Wii, Mario Kart Wii, Donkey Kong Country Returns..."},
            {"id": "nunchuk", "name": "Wii Remote + Nunchuk", "desc": "Nunchuk stick, C, Z, A, B; the right stick moves the pointer. Super Mario Galaxy, Zelda, Metroid Prime 3..."},
            {"id": "classic", "name": "Classic Controller", "desc": "Two sticks and full buttons, for games that support it (many Virtual Console games, Xenoblade, Monster Hunter Tri...)."},
            {"id": "gamecube", "name": "GameCube controller", "desc": "Only for Wii games that support a GameCube controller (Super Smash Bros. Brawl, Mario Kart Wii...)."},
        ],
        "phase": 6,
        "status": "Ready",
        "buildable": False,
    },
    {
        "id": "ps1",
        "name": "PlayStation",
        "short": "PS1",
        "color": "#9AA3AE",
        "engine": "SwanStation",
        "engine_source": "https://github.com/libretro/swanstation",
        "license": "GPL-3.0",
        "extensions": [".cue", ".bin", ".chd", ".pbp", ".iso"],
        "bios": {"required": True, "note": "Required. Import a BIOS dumped from your own PlayStation (for example SCPH-5501)."},
        "features": ["widescreen", "tv"],
        "widescreen_how": "SwanStation's widescreen hack (draws a wider 16:9 view; works in most 3D games).",
        "phase": 5,
        "status": "Ready to test",
        "buildable": False,
    },
]

BY_ID = {c["id"]: c for c in CONSOLES}

# Picture sharpness choices per console. "auto" = what the performance profile sets.
# The other ids become each engine's nearest real setting (see adapters.py).
RESOLUTIONS = {
    "n64": [("auto", "Auto", "Set by the performance profile"),
            ("720p", "720p", "960x720 (1280x720 widescreen)"),
            ("1080p", "1080p", "1440x1080 (1920x1080 widescreen)"),
            ("1440p", "1440p", "1920x1440 (2560x1440 widescreen)")],
    "psp": [("auto", "Auto", "Set by the performance profile"),
            ("720p", "720p", "3x: 1440x816"),
            ("1080p", "1080p", "4x: 1920x1088"),
            ("1440p", "1440p", "5x: 2400x1360")],
    "gc": [("auto", "Auto", "Set by the performance profile"),
           ("720p", "720p", "2x the GameCube resolution (Dolphin: 2x Native for 720p)"),
           ("1080p", "1080p", "3x (Dolphin: 3x Native for 1080p)"),
           ("1440p", "1440p", "4x (Dolphin: 4x Native for 1440p)")],
    "wii": [("auto", "Auto", "Set by the performance profile"),
            ("720p", "720p", "2x the Wii resolution (Dolphin: 2x Native for 720p)"),
            ("1080p", "1080p", "3x (Dolphin: 3x Native for 1080p)"),
            ("1440p", "1440p", "4x (Dolphin: 4x Native for 1440p)")],
    "ps1": [("auto", "Auto", "Set by the performance profile"),
            ("720p", "720p", "3x the PlayStation resolution"),
            ("1080p", "1080p", "4x (about 1080p)"),
            ("1440p", "1440p", "6x (about 1440p)")],
    "ps2": [("auto", "Auto", "Original PS2 resolution"),
            ("720p", "720p", "2x the PS2 resolution"),
            ("1080p", "1080p", "3x the PS2 resolution"),
            ("1440p", "1440p", "4x the PS2 resolution")],
}
for _c in CONSOLES:
    _c["resolutions"] = [{"id": i, "label": l, "detail": d} for i, l, d in RESOLUTIONS.get(_c["id"], [])]

# How the picture is scaled to the phone's screen (the player's video library does it; the game's own
# resolution doesn't change). For GBA and DS this is the "sharper picture" option. Not for GameCube/Wii/PS2,
# whose engines draw at a higher resolution themselves.
SCREEN_FILTERS = [
    ("default", "Original", "Plain scaling, a little soft."),
    ("sharp", "Sharp pixels", "Crisp, evenly sized pixels."),
    ("smooth", "Smooth upscale", "Smooths jagged edges so pixel art looks closer to a high-resolution picture."),
    ("smooth2", "Smooth upscale, strong", "Stronger smoothing; uses more battery."),
    ("lcd", "Handheld LCD", "Shows the pixel grid like the original handheld screen."),
    ("crt", "CRT TV", "Scanlines like an old TV."),
]
FILTER_DEFAULT = {"gba": "sharp", "nds": "sharp", "n64": "default", "psp": "default", "ps1": "default"}
for _c in CONSOLES:
    if _c["id"] in FILTER_DEFAULT:
        _c["screen_filters"] = [{"id": i, "name": n, "desc": d} for i, n, d in SCREEN_FILTERS]
        _c["screen_filter_default"] = FILTER_DEFAULT[_c["id"]]


ALL_EXTENSIONS = sorted({e for c in CONSOLES for e in c["extensions"]})

# Compressed size above which a game goes in a separate data file instead of
# inside the APK (many phones refuse very large APKs).
EMBED_LIMIT_BYTES = int(1.5 * 1024 ** 3)

PROFILES = [
    {"id": "battery", "name": "Battery Saver", "desc": "Native resolution, capped frame rate, lowest power use."},
    {"id": "balanced", "name": "Balanced", "desc": "Good picture at a steady frame rate. Recommended."},
    {"id": "performance", "name": "Performance", "desc": "Prioritises smooth frame rate over picture quality."},
    {"id": "quality", "name": "High Quality", "desc": "Higher rendering resolution. Best on Galaxy S24 / S25."},
]


def get(console_id):
    return BY_ID.get(console_id)


def consoles_for_extension(ext: str):
    ext = ext.lower()
    return [c["id"] for c in CONSOLES if ext in c["extensions"]]


def refresh():
    """Marks a console buildable when its engine template is in the templates folder."""
    from . import adapters
    for c in CONSOLES:
        ad = adapters.get(c["id"])
        info = ad.template_info() if ad else None
        if info and info["present"]:
            c["buildable"] = True
            c["tested"] = bool(info.get("tested"))
            c["template_version"] = info["version"]
            c["status"] = "Ready" if c["tested"] else "Ready to test on a phone"
