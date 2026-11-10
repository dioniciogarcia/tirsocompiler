"""Run: python -m tests.run_tests  (from the Tirso Compiler folder)."""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["TIRSO_DATA_DIR"] = tempfile.mkdtemp(prefix="tirso-test-")

from app.api import Api  # noqa: E402
from app.core import identify, package_id  # noqa: E402
from tests import make_samples  # noqa: E402

failures = []


def check(cond, label):
    print(("PASS  " if cond else "FAIL  ") + label)
    if not cond:
        failures.append(label)


samples = make_samples.write_all(Path(tempfile.mkdtemp(prefix="tirso-samples-")))

expect = {
    "Test N64 Game.z64": ("n64", "TEST N64 GAME", "NTSE", "USA"),
    "Test N64 Swapped.v64": ("n64", "SWAPPED GAME", "NSWJ", "Japan"),
    "Test N64 Little.n64": ("n64", "LITTLE GAME", "NLTP", "Europe"),
    "Test DS Game.nds": ("nds", "TESTDSGAME", "ATDE", "USA"),
    "Test Cube.iso": ("gc", "Test Cube Game", "GTSE01", "USA"),
    "Test Wii.iso": ("wii", "Test Wii Game", "RTWE01", "USA"),
    "Test PS2.iso": ("ps2", "TESTPS2", "SLUS-12345", "USA"),
    "Test PSP.iso": ("psp", "Test PSP Game", "ULUS10999", "USA"),
    "Test PSP Compressed.cso": ("psp", "Test PSP Game", "ULUS10999", "USA"),
}
for name, (con, title, serial, region) in expect.items():
    r = identify.identify(str(samples / name))
    check(r["console"] == con and r["confident"], f"{name}: console {r['console']}")
    check(r["title"] == title, f"{name}: title {r['title']!r}")
    check(r["serial"] == serial, f"{name}: serial {r['serial']!r}")
    check(r["region"] == region, f"{name}: region {r['region']!r}")

r = identify.identify(str(samples / "Mystery.chd"))
check(r["console"] is None and r["format"] == "chd", "CHD asks the user to choose")
r = identify.identify(str(samples / "Not a game.iso"))
check(r["console"] is None, "Random .iso is NOT assumed to be a game")

# package IDs
check(package_id.validate("com.tirso.mario64") is None, "valid package id")
check(package_id.validate("com.class.x") is not None, "reserved word rejected")
check(package_id.validate("com..x") is not None, "empty part rejected")
check(package_id.validate("Com.x") is not None, "uppercase rejected")
check(package_id.default_for("Pokémon HeartGold!") == "com.tirso.pokemonheartgold", "accents stripped from package id")

# full API flow
api = Api()
res = api.import_paths([str(samples)])
check(res["added"] == 10, f"folder import added {res['added']} files")
check(len(res["skipped"]) == 3, f"folder import skipped {len(res['skipped'])} non-games (2 BIOS + random iso)")
api.import_paths([str(samples / "Not a game.iso")])
res2 = api.import_paths([str(samples / "Test DS Game.nds")])
check(res2["added"] == 0 and res2["updated"] == 1, "re-adding a game reads it again (keeps one entry)")
deadline = time.time() + 20
while time.time() < deadline and api.get_overview()["counts"]["hashing"]:
    time.sleep(0.1)
roms = api.list_roms()
check(all(r["status"] == "ready" for r in roms), "all files hashed")
by = {r["filename"]: r for r in roms}
check(by["Test PSP.iso"]["crc32"] is not None and len(by["Test PSP.iso"]["sha1"]) == 40, "checksums stored")

mystery = by["Mystery.chd"]
check(mystery["console"] is None, "CHD has no console until chosen")
api.set_rom_console(mystery["id"], "ps2")
rep = api.check_compat(mystery["id"], "ps2")
levels = [c["level"] for c in rep["checks"]]
check("fail" in levels, "PS2 without BIOS fails the check")

b = api.import_bios("ps2", [str(samples / "bios" / "SCPH-TEST.bin"), str(samples / "bios" / "fake.bin")])
ok = [x for x in b["results"] if x["ok"]]
check(len(ok) == 1 and "2.20" in ok[0]["message"], f"real-layout BIOS accepted: {ok[0]['message'] if ok else b}")
check(not b["results"][1]["ok"], "fake BIOS rejected: " + b["results"][1]["message"])
dup = api.import_bios("ps2", [str(samples / "bios" / "SCPH-TEST.bin")])
check(not dup["results"][0]["ok"], "duplicate BIOS rejected")
rep = api.check_compat(by["Test PS2.iso"]["id"], "ps2")
check(rep["can_save"] and rep["can_build"], "PS2 game with a BIOS can be built (ARMSX2 template present)")

wrong = api.check_compat(by["Test N64 Game.z64"]["id"], "psp")
check(not wrong["can_save"], "N64 game checked as PSP is refused")

n64 = by["Test N64 Game.z64"]
pkg = api.suggest_package("Test N64 Game")
g = api.save_game(dict(rom_id=n64["id"], app_name="Test N64 Game", package_id=pkg, orientation="landscape", profile="balanced"))
check(g["ok"], "game app saved")
pkg2 = api.suggest_package("Test N64 Game")
check(pkg2 != pkg, f"second suggestion is unique ({pkg2})")
g2 = api.save_game(dict(rom_id=n64["id"], app_name="Other", package_id=pkg, orientation="landscape", profile="balanced"))
check(not g2["ok"], "duplicate package id refused")
check(not api.remove_rom(n64["id"])["ok"], "ROM used by a game can't be removed")
check(len(api.list_games()) == 1, "My Games lists 1 game")

# widescreen
from app.core import adapters  # noqa: E402
n64a = adapters.get("n64")
check(n64a.variables("balanced", {})["mupen64plus-aspect"] == "4:3", "4:3 by default")
wv = n64a.variables("quality", {"widescreen": True})
check(wv["mupen64plus-aspect"] == "16:9 adjusted" and wv["mupen64plus-169screensize"] == "1920x1080", "widescreen hack settings")
n64b = by["Test N64 Swapped.v64"]
gw = api.save_game(dict(rom_id=n64b["id"], console="n64", app_name="Wide", package_id="com.tirso.wide",
                        orientation="landscape", profile="balanced", widescreen=True))
check([x for x in api.list_games() if x["id"] == gw["id"]][0]["settings"].get("widescreen") is True, "widescreen saved with the game")
ps2g = api.save_game(dict(rom_id=by["Test PS2.iso"]["id"], console="ps2", app_name="Wide PS2", package_id="com.tirso.wideps2",
                          orientation="landscape", profile="balanced", widescreen=True))
check([x for x in api.list_games() if x["id"] == ps2g["id"]][0]["settings"].get("widescreen") is True, "PS2 widescreen choice saved for later")
pspg = api.save_game(dict(rom_id=by["Test PSP.iso"]["id"], console="psp", app_name="PSP", package_id="com.tirso.psp",
                          orientation="landscape", profile="balanced", widescreen=True))
check("widescreen" not in [x for x in api.list_games() if x["id"] == pspg["id"]][0]["settings"], "PSP ignores widescreen (already 16:9)")

# resolution choices
r = n64a.variables("battery", {"resolution": "1080p"})
check(r["mupen64plus-43screensize"] == "1440x1080" and r["mupen64plus-169screensize"] == "1920x1080", "N64 1080p")
r = n64a.variables("battery", {"resolution": "1440p", "tv": True, "widescreen": True})
check(r["mupen64plus-169screensize"] == "2560x1440", "N64 1440p wins over the TV defaults")
check(n64a.variables("battery", {})["mupen64plus-43screensize"] == "320x240", "N64 Auto keeps the profile size")
pspa = adapters.get("psp")
check(pspa.variables("battery", {"resolution": "720p"})["ppsspp_internal_resolution"] == "1440x816", "PSP 720p = 3x")
check(pspa.variables("quality", {"resolution": "1440p"})["ppsspp_internal_resolution"] == "2400x1360", "PSP 1440p = 5x")
check(pspa.variables("quality", {})["ppsspp_internal_resolution"] == "1440x816", "PSP Auto keeps the profile")
psp_r = api.save_game(dict(id=pspg["id"], rom_id=by["Test PSP.iso"]["id"], console="psp", app_name="PSP", package_id="com.tirso.psp",
                           orientation="landscape", profile="quality", resolution="1080p"))
check([x for x in api.list_games() if x["id"] == pspg["id"]][0]["settings"].get("resolution") == "1080p", "resolution saved with the game")
api.save_game(dict(id=pspg["id"], rom_id=by["Test PSP.iso"]["id"], console="psp", app_name="PSP", package_id="com.tirso.psp",
                   orientation="landscape", profile="balanced", resolution="1080p"))
check([x for x in api.list_games() if x["id"] == pspg["id"]][0]["settings"].get("resolution") == "auto", "resolution only applies with High Quality")
nds_rom = by["Test DS Game.nds"]
dsg = api.save_game(dict(rom_id=nds_rom["id"], console="nds", app_name="DS Res", package_id="com.tirso.dsres",
                         orientation="landscape", profile="balanced", resolution="1080p"))
check("resolution" not in [x for x in api.list_games() if x["id"] == dsg["id"]][0]["settings"], "DS has no resolution choice")
bad = api.save_game(dict(id=pspg["id"], rom_id=by["Test PSP.iso"]["id"], console="psp", app_name="PSP", package_id="com.tirso.psp",
                         orientation="landscape", profile="quality", resolution="8k"))
check([x for x in api.list_games() if x["id"] == pspg["id"]][0]["settings"].get("resolution") == "auto", "unknown resolution falls back to Auto")
ps2a = adapters.get("ps2")
g2 = {"app_name": "x", "settings_json": '{"resolution": "1080p", "widescreen": true}'}
cfg2 = ps2a.game_json(g2, {"sha1": "a"}, "b", "game.cso", 10, embedded=True, bios=None)
check(cfg2["upscale"] == 3 and cfg2["widescreen"] is True, "PS2 1080p = 3x upscale")
check(ps2a.game_json({"app_name": "x", "settings_json": "{}"}, {"sha1": "a"}, "b", "game.cso", 10)["upscale"] == 1, "PS2 Auto = original resolution")
check(ps2a.game_json({"app_name": "x", "settings_json": "{}", "orientation": "landscape"}, {"sha1": "a"}, "b", "game.cso", 10)["orientation"] == "landscape", "PS2 app gets the chosen orientation")

# DS "turns with the phone"
rot = api.save_game(dict(id=dsg["id"], rom_id=nds_rom["id"], console="nds", app_name="DS Res", package_id="com.tirso.dsres",
                         orientation="landscape", profile="balanced", ds_layout="rotate"))
gr = [x for x in api.list_games() if x["id"] == dsg["id"]][0]
check(gr["settings"].get("ds_layout") == "rotate" and gr["orientation"] == "auto", "DS rotate layout saved, phone turns freely")
ndsa = adapters.get("nds")
cfgr = ndsa.game_json(api._db.one("SELECT * FROM games WHERE id=?", (dsg["id"],)), {"path": "x.nds", "size": 1, "sha1": "a"}, "b")
rl = cfgr.get("rotate_layouts", {})
check(rl.get("portrait", {}).get("variables", {}).get("melonds_screen_layout1") == "top-bottom" and rl["portrait"]["screen_mode"] == "above",
      "DS upright = stacked screens")
check(rl.get("landscape", {}).get("variables", {}).get("melonds_screen_layout1") == "hybrid-top" and rl["landscape"]["screen_mode"] == "beside",
      "DS sideways = large top screen")
check(cfgr["orientation"] == "auto", "DS rotate app follows the phone")
tvr = ndsa.game_json({"app_name": "x", "orientation": "auto", "profile": "balanced", "settings_json": '{"ds_layout": "rotate", "tv": true}'},
                     {"path": "x.nds", "size": 1, "sha1": "a"}, "b")
check("rotate_layouts" not in tvr and tvr["orientation"] == "landscape", "DS on TV stays on the large top screen")

# PS2 CD games as BIN/CUE
cue_dir = Path(tempfile.mkdtemp(prefix="tirso-cue-"))
cue_isos = make_samples.write_ps2_cue_games(cue_dir)
from app.core import identify as _ident  # noqa: E402
for name, serial in (("NBA Test.cue", "SLUS-20140"), ("Oni Test.cue", "SLES-50343"),
                     ("Multi Test.cue", "SLUS-20305"), ("Lone Raw.bin", "SLUS-20181")):
    inf = _ident.identify(str(cue_dir / name))
    check(inf["console"] == "ps2" and inf["confident"] and inf["serial"] == serial, f"BIN/CUE identified: {name} -> {inf['console']} {inf['serial']} ({inf['note']})")
check("music track" in _ident.identify(str(cue_dir / "NBA Test.cue"))["note"], "music tracks noted")
res = api.import_paths([str(cue_dir)])
check(res["added"] == 4, f"BIN/CUE folder import adds 4 games (got {res['added']})")
check(len([x for x in res["skipped"] if "added through its .cue" in x["reason"]]) == 4, "BIN files that belong to a CUE are not separate games")
deadline = time.time() + 20
while time.time() < deadline and api.get_overview()["counts"]["hashing"]:
    time.sleep(0.1)
nba = [r for r in api.list_roms() if r["filename"] == "NBA Test.cue"][0]
import hashlib as _h  # noqa: E402
check(nba["sha1"] == _h.sha1((cue_dir / "NBA Test (Track 1).bin").read_bytes()).hexdigest(), "BIN/CUE checksum is the game data BIN")
check(nba["size"] > (cue_dir / "NBA Test (Track 1).bin").stat().st_size, "BIN/CUE size counts all its files")
rep = api.check_compat(nba["id"], "ps2")
check(rep["can_build"] and not [c for c in rep["checks"] if c["level"] == "fail"], "BIN/CUE PS2 game can be built")
(cue_dir / "Broken.cue").write_text('FILE "missing.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
check("same folder" in _ident.identify(str(cue_dir / "Broken.cue"))["note"], "CUE with a missing BIN explained")

# GameCube / Wii (Dolphin)
from app.core import discs as _d  # noqa: E402
gtmp = Path(tempfile.mkdtemp())
gc_iso = make_samples.nintendo_disc() + bytes(range(256)) * 4000 + b"\x00" * 300000
(gtmp / "cube.iso").write_bytes(gc_iso)
size = _d.compress_gcz(gtmp / "cube.iso", gtmp / "cube.gcz")
check(size == (gtmp / "cube.gcz").stat().st_size and size < len(gc_iso) / 2, f"GCZ compresses ({len(gc_iso)} -> {size} bytes)")
with open(gtmp / "cube.gcz", "rb") as f:
    gr = _d.GczReader(f)
    check(gr.read(0, len(gc_iso)) == gc_iso and gr.size == len(gc_iso), "GCZ reads back byte for byte")
gi = identify.identify(str(gtmp / "cube.gcz"))
check(gi["console"] == "gc" and gi["serial"] == "GTSE01" and gi["format"] == "gcz", f"GCZ identified as GameCube: {gi}")
_d.compress_gcz(samples / "Test Wii.iso", gtmp / "wii.gcz", wii=True)
check(identify.identify(str(gtmp / "wii.gcz"))["console"] == "wii", "GCZ identified as Wii")
gca, wiia = adapters.get("gc"), adapters.get("wii")
gv = gca.variables("balanced", {"widescreen": True, "resolution": "1080p"})
check(gv["dolphin_efb_scale"] == "3" and gv["dolphin_widescreen_hack"] == "enabled" and gv["dolphin_aspect_ratio"] == "3" and gv["dolphin_shader_compilation_mode"] == "0", "GameCube 1080p + widescreen")
check(gca.variables("quality", {})["dolphin_efb_scale"] == "2" and "dolphin_wiimote_default_device" not in gca.variables("quality", {}), "GameCube Auto profile, no Wii settings")
wv = wiia.variables("balanced", {"wii_controls": "nunchuk", "widescreen": True})
check(wv["dolphin_wiimote_default_device"] == "nunchuk" and wv["dolphin_widescreen"] == "enabled", "Wii Nunchuk + 16:9")
check(wiia.variables("balanced", {})["dolphin_wiimote_default_device"] == "sideways", "Wii defaults to the sideways Remote")
check(wiia.pad_for({"wii_controls": "classic"}) == "wii_classic" and wiia.pad_for({"wii_controls": "gamecube"}) == "gc", "Wii touch layouts follow the controller")
wii_rom = by["Test Wii.iso"]
wg = api.save_game(dict(rom_id=wii_rom["id"], console="wii", app_name="Wii Game", package_id="com.tirso.wiigame",
                        orientation="portrait", profile="balanced", wii_controls="nunchuk"))
check([x for x in api.list_games() if x["id"] == wg["id"]][0]["settings"].get("wii_controls") == "nunchuk", "Wii controller saved with the game")

# Game Boy Advance
gtmp2 = Path(tempfile.mkdtemp())
(gtmp2 / "Test GBA.gba").write_bytes(make_samples.gba())
ga = identify.identify(str(gtmp2 / "Test GBA.gba"))
check(ga["console"] == "gba" and ga["serial"] == "BTGE" and ga["title"] == "TEST GBA" and ga["region"] == "USA", f"GBA identified: {ga}")
bad = bytearray(make_samples.gba()); bad[0xBD] ^= 0xFF
(gtmp2 / "Broken.gba").write_bytes(bytes(bad))
check(identify.identify(str(gtmp2 / "Broken.gba"))["console"] is None, "GBA with a wrong header checksum isn't trusted")
gbaa = adapters.get("gba")
check(gbaa.variables("battery", {})["mgba_frameskip"] == "auto" and gbaa.variables("quality", {})["mgba_use_bios"] == "OFF", "GBA settings")

print()
print("ALL TESTS PASSED" if not failures else f"{len(failures)} FAILED")
sys.exit(1 if failures else 0)
