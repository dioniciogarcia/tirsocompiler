"""End-to-end APK build test. Run: python -m tests.test_build  (needs Java and the tools folder)."""
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["TIRSO_DATA_DIR"] = tempfile.mkdtemp(prefix="tirso-buildtest-")

from app.api import Api  # noqa: E402
from tests import make_samples  # noqa: E402

api = Api()
folder = Path(tempfile.mkdtemp())
data = bytearray(4 * 1024 * 1024)
data[:0x1000] = make_samples.n64(b"BUILD TEST", b"NBTE")
(folder / "Build Test.z64").write_bytes(bytes(data))
api.import_paths([str(folder / "Build Test.z64")])
while api.get_overview()["counts"]["hashing"]:
    time.sleep(0.1)
rom = api.list_roms()[0]
out = tempfile.mkdtemp(prefix="apks-")
api._db.set_setting("output_dir", out)
g = api.save_game(dict(rom_id=rom["id"], app_name="Build Test", package_id="com.tirso.buildtest",
                       orientation="landscape", profile="balanced", widescreen=True))
r = api.generate_apk(g["id"])
assert r["ok"], r
while api.current_build():
    time.sleep(0.3)
b = api.get_build(r["id"])
print(b["status"], b["message"])
assert b["status"] == "success", b["log"][-3000:]
apk = Path(b["apk_path"])
with zipfile.ZipFile(apk) as z:
    assert "assets/tirso/game.z64" in z.namelist()
    import json
    cfg = json.loads(z.read("assets/tirso/game.json"))
    assert cfg["variables"]["mupen64plus-aspect"] == "16:9 adjusted", cfg["variables"]
    print("widescreen in APK:", cfg["variables"]["mupen64plus-aspect"], cfg["variables"]["mupen64plus-169screensize"])
# second build of the same game gets a higher version code
r2 = api.generate_apk(g["id"])
while api.current_build():
    time.sleep(0.3)
assert api.get_build(r2["id"])["status"] == "success"
assert "versionCode: 2" in api.get_build(r2["id"])["log"] or "code 2" in api.get_build(r2["id"])["log"]
print("BUILD TEST PASSED:", apk, apk.stat().st_size, "bytes")


# ---- Nintendo DS and PSP -------------------------------------------------
def build(rom_file, console, name, pkg, **extra):
    api.import_paths([str(rom_file)])
    while api.get_overview()["counts"]["hashing"]:
        time.sleep(0.1)
    rom = [r for r in api.list_roms() if r["path"] == str(rom_file)][0]
    assert rom["console"] == console, rom
    profile = extra.pop("profile", "balanced")
    g = api.save_game(dict(rom_id=rom["id"], console=console, app_name=name, package_id=pkg,
                           orientation="landscape", profile=profile, **extra))
    assert g["ok"], g
    r = api.generate_apk(g["id"])
    assert r["ok"], r
    while api.current_build():
        time.sleep(0.3)
    b = api.get_build(r["id"])
    assert b["status"] == "success", (b["message"], b["log"][-3000:])
    with zipfile.ZipFile(b["apk_path"]) as z:
        cfg = json.loads(z.read("assets/tirso/game.json"))
        names = z.namelist()
        assert "assets/tirso/" + cfg["rom"] in names
        assert z.getinfo("assets/tirso/" + cfg["rom"]).file_size == cfg["rom_size"]
        assert "lib/arm64-v8a/" + cfg["core"] in names
    return b, cfg, names


import json  # noqa: E402

nds = bytearray(make_samples.nds())
nds += bytes(2 * 1024 * 1024 - len(nds))
(folder / "DS Test.nds").write_bytes(bytes(nds))
b, cfg, _ = build(folder / "DS Test.nds", "nds", "DS Test", "com.tirso.dstest", ds_layout="large")
assert cfg["variables"]["melonds_screen_layout1"] == "hybrid-top" and cfg["screen_mode"] == "beside", cfg
assert cfg["orientation"] == "landscape" and cfg["pad"] == "nds"
print("DS BUILD PASSED:", b["apk_path"])

iso = make_samples.iso({"PSP_GAME/PARAM.SFO": make_samples.sfo({"TITLE": "PSP Build", "DISC_ID": "ULUS10998"})})
iso += bytes(3 * 1024 * 1024)  # padding compresses well
(folder / "PSP Test.iso").write_bytes(iso)
b, cfg, names = build(folder / "PSP Test.iso", "psp", "PSP Test", "com.tirso.psptest")
assert cfg["rom"] == "game.cso" and cfg["system_zip"] == "ppsspp_system.zip" and "assets/ppsspp_system.zip" in names
assert cfg["variables"]["ppsspp_backend"] == "opengl"
assert cfg["rom_size"] < len(iso) / 4, cfg["rom_size"]
# the CSO inside must read back as the same PSP game
from app.core import identify  # noqa: E402
with zipfile.ZipFile(b["apk_path"]) as z:
    tmp = Path(tempfile.mkdtemp()) / "check.cso"
    tmp.write_bytes(z.read("assets/tirso/game.cso"))
info = identify.identify(str(tmp))
assert info["console"] == "psp" and info["serial"] == "ULUS10998", info
print("PSP BUILD PASSED (ISO compressed to CSO and verified):", b["apk_path"], cfg["rom_size"], "bytes")


# ---- TV builds (64-bit + 32-bit, TV home screen, always sideways) -------------
for rom_file, console, extra in ((folder / "Build Test.z64", "n64", {}),
                                 (folder / "DS Test.nds", "nds", {"ds_layout": "stacked"}),
                                 (folder / "PSP Test.iso", "psp", {})):
    rom = [r for r in api.list_roms() if r["path"] == str(rom_file)][0]
    g = api.save_game(dict(rom_id=rom["id"], console=console, app_name="TV " + console, package_id="com.tirso.tv" + console,
                           orientation="portrait", profile="balanced", tv=True, **extra))
    assert g["ok"], g
    r = api.generate_apk(g["id"])
    while api.current_build():
        time.sleep(0.3)
    b = api.get_build(r["id"])
    assert b["status"] == "success", b["log"][-3000:]
    with zipfile.ZipFile(b["apk_path"]) as z:
        names = z.namelist()
        cfg = json.loads(z.read("assets/tirso/game.json"))
        assert "lib/arm64-v8a/" + cfg["core"] in names
        v7a_core = cfg["alt_engines"][0]["core"] if cfg.get("alt_engines") else cfg["core"]
        assert "lib/armeabi-v7a/" + v7a_core in names, v7a_core
        assert "res/drawable-xhdpi/tv_banner.png" in names or any(n.endswith("tv_banner.png") for n in names)
    assert cfg["orientation"] == "landscape", cfg["orientation"]
    if console == "nds":
        assert cfg["variables"]["melonds_screen_layout1"] == "hybrid-top"
        assert cfg["variables"]["melonds_screen_layout2"] == "hybrid-bottom"
        assert cfg["variables"]["melonds_touch_mode"] == "auto"
        alt = cfg["alt_engines"][0]
        assert alt["core"] == "libdesmume_libretro_android.so" and alt["variables"]["desmume_screens_layout"] == "hybrid/top"
        assert alt["variables"]["desmume_cpu_mode"] == "jit" and alt["variables"]["desmume_pointer_device_r"] == "emulated"
    if console == "n64":
        assert cfg["variables"]["mupen64plus-EnableCopyDepthToRDRAM"] == "Off"
        assert cfg["variables"]["mupen64plus-EnableCopyColorToRDRAM"] == "Off"
    print("TV BUILD PASSED:", console, b["apk_path"])


# ---- PS2 (ARMSX2): BIOS goes inside, small games inside, big games as a data file ----
from app.core import consoles as _consoles  # noqa: E402
(folder / "SCPH-TEST.bin").write_bytes(make_samples.ps2_bios())
res = api.import_bios("ps2", [str(folder / "SCPH-TEST.bin")])
assert any(x.get("ok") or x.get("valid") for x in res["results"]), res
ps2 = make_samples.iso({"SYSTEM.CNF": b"BOOT2 = cdrom0:\\SLUS_987.65;1\r\nVER = 1.00\r\nVMODE = NTSC\r\n"}, b"PS2BUILD")
ps2 += bytes(3 * 1024 * 1024)
(folder / "PS2 Test.iso").write_bytes(ps2)
b, cfg, names = build(folder / "PS2 Test.iso", "ps2", "PS2 Test", "com.tirso.ps2test")
assert cfg["rom"] == "game.cso" and cfg["data_file"] == "" and cfg["bios"] == "bios.bin", cfg
assert cfg["widescreen"] is False
assert "assets/tirso/bios.bin" in names and "lib/arm64-v8a/libemucore_4k.so" in names
with zipfile.ZipFile(b["apk_path"]) as z:
    assert z.read("assets/tirso/bios.bin") == make_samples.ps2_bios()
print("PS2 BUILD PASSED (game + BIOS inside):", b["apk_path"])

# GPU driver pack: rejected when it isn't one, then carried by the next PS2 build
(folder / "not_a_driver.zip").write_bytes(b"nope")
assert not api.import_gpu_driver("ps2", [str(folder / "not_a_driver.zip")])["ok"]
with zipfile.ZipFile(folder / "turnip_test.zip", "w") as z:
    z.writestr("meta.json", json.dumps({"name": "Turnip Test", "driverVersion": "1.0", "vendor": "Mesa", "libraryName": "vulkan.ad07XX.so"}))
    z.writestr("vulkan.ad07XX.so", b"\x7fELF" + bytes(64))
r = api.import_gpu_driver("ps2", [str(folder / "turnip_test.zip")])
assert r["ok"], r
assert api.get_gpu_driver("ps2")["name"] == "Turnip Test"
b2, cfg2, names2 = build(folder / "PS2 Test.iso", "ps2", "PS2 Driver", "com.tirso.ps2driver")
assert cfg2["gpu_driver"] == "gpu_driver.zip" and cfg2["gpu_driver_name"] == "Turnip Test", cfg2
with zipfile.ZipFile(b2["apk_path"]) as z:
    assert z.read("assets/tirso/gpu_driver.zip") == (folder / "turnip_test.zip").read_bytes()
    assert z.getinfo("assets/tirso/gpu_driver.zip").compress_type == zipfile.ZIP_STORED
api.remove_gpu_driver("ps2")
assert api.get_gpu_driver("ps2") is None
print("PS2 GPU DRIVER PASSED:", b2["apk_path"])

# pretend the limit is tiny so the same game is treated as a big one
_old = _consoles.EMBED_LIMIT_BYTES
_consoles.EMBED_LIMIT_BYTES = 1024
try:
    rom = [r for r in api.list_roms() if r["path"] == str(folder / "PS2 Test.iso")][0]
    g = api.save_game(dict(rom_id=rom["id"], console="ps2", app_name="PS2 Big", package_id="com.tirso.ps2big",
                           orientation="landscape", profile="balanced", widescreen=True))
    r = api.generate_apk(g["id"])
    while api.current_build():
        time.sleep(0.3)
    b = api.get_build(r["id"])
    assert b["status"] == "success", b["log"][-3000:]
    with zipfile.ZipFile(b["apk_path"]) as z:
        cfg = json.loads(z.read("assets/tirso/game.json"))
        assert cfg["rom"] == "" and cfg["data_file"] == "game.cso" and cfg["widescreen"] is True, cfg
        assert not any(n.startswith("assets/tirso/game.") and n != "assets/tirso/game.json" for n in z.namelist())
    dp = Path(b["data_path"])
    assert dp.exists() and dp.parent.name == "com.tirso.ps2big" and dp.stat().st_size == cfg["rom_size"], b["data_path"]
    print("PS2 BIG GAME PASSED (APK + data file):", b["apk_path"], dp)
finally:
    _consoles.EMBED_LIMIT_BYTES = _old


# ---- PS2 BIN/CUE (CD games): the data track becomes the CSO inside the APK ----
from app.core import discs as _discs  # noqa: E402
cue_dir = folder / "cue"
cue_isos = make_samples.write_ps2_cue_games(cue_dir)
for n, (name, pkg) in enumerate((("NBA Test.cue", "com.tirso.cuenba"), ("Multi Test.cue", "com.tirso.cuemulti"),
                                 ("Lone Raw.bin", "com.tirso.cuelone"))):
    b, cfg, names = build(cue_dir / name, "ps2", "Cue %d" % n, pkg)
    assert cfg["rom"] == "game.cso" and "assets/tirso/bios.bin" in names, cfg
    with zipfile.ZipFile(b["apk_path"]) as z:
        tmp = Path(tempfile.mkdtemp()) / "check.cso"
        tmp.write_bytes(z.read("assets/tirso/game.cso"))
    with open(tmp, "rb") as f:
        r = _discs.CsoReader(f)
        want = cue_isos[name]
        got = r.read(0, len(want))
    assert got == want, "CSO content differs from the disc data for " + name
    assert r.total == (len(want) + 2047) // 2048 * 2048, (r.total, len(want))  # no music track data inside
    assert identify.identify(str(tmp))["serial"] is not None
    if "music" not in b["log"] and name != "Lone Raw.bin" and name != "Oni Test.cue":
        raise AssertionError("music-track note missing from the log")
    print("PS2 BIN/CUE BUILD PASSED:", name, "->", b["apk_path"])


# ---- GameCube / Wii (Dolphin) ----
from app.core import discs as _gd  # noqa: E402
gc_iso = make_samples.nintendo_disc(b"GDKE01", b"Dolphin Test") + bytes(range(256)) * 8000 + bytes(2 * 1024 * 1024)
(folder / "GC Test.iso").write_bytes(gc_iso)
b, cfg, names = build(folder / "GC Test.iso", "gc", "GC Test", "com.tirso.gctest", widescreen=True, resolution="720p", profile="quality")
assert cfg["rom"] == "game.gcz" and cfg["pad"] == "gc" and cfg["system_zip"] == "dolphin_system.zip", cfg
assert "assets/dolphin_system.zip" in names and "lib/arm64-v8a/libdolphin_libretro_android.so" in names
assert cfg["variables"]["dolphin_efb_scale"] == "2" and cfg["variables"]["dolphin_widescreen_hack"] == "enabled"
with zipfile.ZipFile(b["apk_path"]) as z:
    tmpg = Path(tempfile.mkdtemp()) / "g.gcz"
    tmpg.write_bytes(z.read("assets/tirso/game.gcz"))
    assert z.getinfo("assets/tirso/game.gcz").compress_type == zipfile.ZIP_STORED
with open(tmpg, "rb") as f:
    assert _gd.GczReader(f).read(0, len(gc_iso)) == gc_iso, "GCZ inside the APK differs from the disc"
print("GAMECUBE BUILD PASSED (ISO -> GCZ, verified):", b["apk_path"], cfg["rom_size"], "bytes")

# GameCube/Wii GPU driver slot
with zipfile.ZipFile(folder / "turnip_gc.zip", "w") as z:
    z.writestr("meta.json", json.dumps({"name": "Turnip GC", "driverVersion": "2.0", "libraryName": "vulkan.ad08XX.so"}))
    z.writestr("vulkan.ad08XX.so", b"\x7fELF" + bytes(64))
assert api.import_gpu_driver("dolphin", [str(folder / "turnip_gc.zip")])["ok"]
_gc_iso = [r for r in api.list_roms() if r["console"] == "gc"][0]["path"]
b3, cfg3, names3 = build(Path(_gc_iso), "gc", "GC Driver", "com.tirso.gcdriver")
assert cfg3["gpu_driver"] == "gpu_driver.zip" and cfg3["gpu_driver_name"] == "Turnip GC", cfg3
assert "assets/tirso/gpu_driver.zip" in names3
api.remove_gpu_driver("dolphin")
print("GAMECUBE GPU DRIVER PASSED:", b3["apk_path"])

wii_iso = make_samples.nintendo_disc(b"RWIE01", b"Wii Test", wii=True) + bytes(1024 * 1024)
(folder / "Wii Test.iso").write_bytes(wii_iso)
for controls, pad in (("sideways", "wii_sideways"), ("nunchuk", "wii_nunchuk"), ("classic", "wii_classic"), ("gamecube", "gc")):
    b, cfg, names = build(folder / "Wii Test.iso", "wii", "Wii " + controls, "com.tirso.wii" + controls, wii_controls=controls)
    assert cfg["pad"] == pad and cfg["variables"]["dolphin_wiimote_default_device"] == controls, cfg
print("WII BUILDS PASSED (4 controller types)")

_old = _consoles.EMBED_LIMIT_BYTES
_consoles.EMBED_LIMIT_BYTES = 1024
try:
    rom = [r for r in api.list_roms() if r["path"] == str(folder / "Wii Test.iso")][0]
    g = api.save_game(dict(rom_id=rom["id"], console="wii", app_name="Wii Big", package_id="com.tirso.wiibig",
                           orientation="landscape", profile="balanced", wii_controls="sideways"))
    r = api.generate_apk(g["id"])
    while api.current_build():
        time.sleep(0.3)
    b = api.get_build(r["id"])
    assert b["status"] == "success", b["log"][-3000:]
    with zipfile.ZipFile(b["apk_path"]) as z:
        cfg = json.loads(z.read("assets/tirso/game.json"))
    assert cfg["rom"] == "" and cfg["data_file"] == "game.gcz", cfg
    dp = Path(b["data_path"])
    assert dp.exists() and dp.parent.name == "com.tirso.wiibig" and dp.stat().st_size == cfg["rom_size"]
    print("WII BIG GAME PASSED (APK + data file):", dp)
finally:
    _consoles.EMBED_LIMIT_BYTES = _old


# ---- Game Boy Advance (phone and TV) ----
(folder / "GBA Test.gba").write_bytes(make_samples.gba() + bytes(4 * 1024 * 1024))
b, cfg, names = build(folder / "GBA Test.gba", "gba", "GBA Test", "com.tirso.gbatest")
assert cfg["rom"] == "game.gba" and cfg["pad"] == "gba" and "lib/arm64-v8a/libmgba_libretro_android.so" in names, cfg
b, cfg, names = build(folder / "GBA Test.gba", "gba", "GBA TV", "com.tirso.gbatv", tv=True)
assert "lib/armeabi-v7a/libmgba_libretro_android.so" in names and cfg["orientation"] == "landscape"
print("GBA BUILDS PASSED (phone + TV)")


# ---- Batch: several games of one console, queued and built one after another ----
batch_files = []
for i, title in enumerate((b"BATCH ONE", b"BATCH TWO", b"BATCH THREE", b"BATCH FOUR")):
    f = folder / ("Batch %d.gba" % i)
    f.write_bytes(make_samples.gba(title, b"BB%dE" % i) + bytes(512 * 1024 + i))
    batch_files.append(f)
api.import_paths([str(f) for f in batch_files])
while api.get_overview()["counts"]["hashing"]:
    time.sleep(0.1)
broms = [r for r in api.list_roms() if r["path"] in [str(f) for f in batch_files]]
assert len(broms) == 4 and all(r["console"] == "gba" for r in broms), broms
res = api.batch_build({"rom_ids": [r["id"] for r in broms], "names": {str(broms[0]["id"]): "Renamed One"},
                       "options": {"profile": "balanced", "tv": False}})
assert res["ok"] and res["queued"] == 4, res
last = [x for x in res["results"]][-1]["build_id"]
assert api.cancel_build(last)["ok"]                               # take the last one out of the queue
mixed = api.batch_build({"rom_ids": [broms[0]["id"], [r for r in api.list_roms() if r["console"] == "nds"][0]["id"]], "options": {}})
assert not mixed["ok"] and "one console" in mixed["error"], mixed
while api.current_build() or api.queue_length():
    time.sleep(0.3)
done = {b["id"]: b for b in api.list_builds()}
for x in res["results"]:
    b = done[x["build_id"]]
    if x["build_id"] == last:
        assert b["status"] == "cancelled", b
        continue
    assert b["status"] == "success", (b["status"], b["message"])
    assert Path(b["apk_path"]).parent.name == "Game Boy Advance", b["apk_path"]
assert any(g["app_name"] == "Renamed One" for g in api.list_games())
print("BATCH PASSED: 3 GBA APKs built in a queue (one removed), saved under",
      Path(done[res["results"][0]["build_id"]]["apk_path"]).parent)
