"""Batch build test: folder -> identify + look up -> build -> info and logo icon inside the APK.

Run: python -m tests.test_batch   (needs internet the first time, Java and the tools folder)
"""
import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["TIRSO_DATA_DIR"] = tempfile.mkdtemp(prefix="tirso-batchtest-")
CACHE = os.environ.get("TIRSO_GAMEDB_CACHE")  # optional: reuse downloaded game lists
if CACHE and os.path.isdir(CACHE):
    shutil.copytree(CACHE, Path(os.environ["TIRSO_DATA_DIR"]) / "gamedb")

from app.api import Api  # noqa: E402
from tests import make_samples  # noqa: E402

api = Api()
folder = Path(tempfile.mkdtemp(prefix="batch-games-"))
(folder / "sub").mkdir()
# Fake cartridges with real game codes and names (their checksums won't match, so serial/name are used).
big = bytearray(4 * 1024 * 1024)
big[:0x1000] = make_samples.n64(b"SUPER MARIO 64", b"NSME")
(folder / "Super Mario 64 (U) [!].z64").write_bytes(bytes(big))
(folder / "sub" / "Mario Kart DS (USA) EnFrDeEsIt.nds").write_bytes(make_samples.nds(b"MARIOKART DS", b"AMCE"))
(folder / "zelda_minish_cap.gba").write_bytes(make_samples.gba(b"GBAZELDA MC", b"BZME"))
(folder / "Mario Superstar Baseball.iso").write_bytes(make_samples.nintendo_disc(b"GYQE01", b"Mario Superstar Baseball"))
(folder / "notes.txt").write_text("not a game")
(folder / "Unknown Thing.gba").write_bytes(make_samples.gba(b"NOT A GAME", b"ZZZZ"))

r = api.batch_add_folder(str(folder), "phone")
assert r["ok"] and r["added"] == 5, r
fid = r["id"]
t = time.time()
while api.batch_list_folders()[0]["status"] == "scanning":
    assert time.time() - t < 600, "lookup took too long"
    time.sleep(0.5)
items = {i["filename"]: i for i in api.batch_items(fid)}
for name, i in items.items():
    print("%-40s %-9s %-11s %s | %s" % (name, i["state"], i["method"], i["match_name"], i["message"]))
assert items["Super Mario 64 (U) [!].z64"]["match_name"] == "Super Mario 64 (USA)"
assert items["Mario Kart DS (USA) EnFrDeEsIt.nds"]["match_name"].startswith("Mario Kart DS (USA")
assert items["Mario Kart DS (USA) EnFrDeEsIt.nds"]["method"] == "serial"
assert items["zelda_minish_cap.gba"]["match_name"] == "Legend of Zelda, The - The Minish Cap (USA)"
assert items["Mario Superstar Baseball.iso"]["match_name"] == "Mario Superstar Baseball (USA)"
assert items["Mario Superstar Baseball.iso"]["has_logo"] == 1
assert items["Unknown Thing.gba"]["state"] == "notfound"
assert items["zelda_minish_cap.gba"]["title"] == "The Legend of Zelda: The Minish Cap"
detail = api.batch_item_detail(items["zelda_minish_cap.gba"]["id"])
assert detail["info"]["description"] and "box" in detail["pictures"]

# Batch games stay out of the ROM Library and My games
assert api.list_roms() == [] and api.list_games() == []

out = tempfile.mkdtemp(prefix="apks-")
api._db.set_setting("output_dir", out)
api.batch_set_include([i["id"] for i in items.values() if i["filename"] != "zelda_minish_cap.gba"], False)
res = api.batch_build_all(fid)
assert res["ok"] and res["queued"] == 1, res
while api.current_build() or api.queue_length():
    time.sleep(0.5)
it = next(i for i in api.batch_items(fid) if i["filename"] == "zelda_minish_cap.gba")
assert it["build_status"] == "success", (it, api.get_build(it["build_id"])["log"][-2000:])
with zipfile.ZipFile(it["apk_path"]) as z:
    names = z.namelist()
    assert "assets/tirso/info/info.json" in names and "assets/tirso/info/box.png" in names, names
    import json
    info = json.loads(z.read("assets/tirso/info/info.json"))
    assert info["title"] == "The Legend of Zelda: The Minish Cap" and info["description"], info
    print("info:", {k: (v[:60] if isinstance(v, str) else v) for k, v in info.items()})
assert api.list_roms() == [] and api.list_games() == [] and api.list_builds() == []
print("PASSED batch build")
