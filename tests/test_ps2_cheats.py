"""PS2 cheats: the disc's game code and PCSX2 checksum, and the merged .pnach (offline: downloads faked).

Run: python -m tests.test_ps2_cheats
"""
import os
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["TIRSO_DATA_DIR"] = tempfile.mkdtemp(prefix="tirso-ps2cheats-")

from app.core import cheats, gameinfo  # noqa: E402
from tests import make_samples  # noqa: E402

folder = Path(tempfile.mkdtemp(prefix="ps2-cheat-disc-"))
words = [0x464C457F, 0x12345678, 0xDEADBEEF, 0x00000001]
elf = struct.pack("<4I", *words) + b"\x00\x00"  # trailing bytes that don't make a whole word are ignored
crc = 0
for w in words:
    crc ^= w
disc = make_samples.iso({"SYSTEM.CNF": b"BOOT2 = cdrom0:\\SLUS_203.12;1\r\nVER = 1.00\r\n", "SLUS_203.12": elf}, b"PS2CHEAT")
(folder / "game.iso").write_bytes(disc)
key = cheats.ps2_game_key(str(folder / "game.iso"))
assert key == ("SLUS-20312", "%08X" % crc), key
print("PASS  game code and checksum:", key)

# the same disc as a raw CD image with a .cue
(folder / "game.bin").write_bytes(make_samples.raw_cd(disc))
(folder / "game.cue").write_text('FILE "game.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
assert cheats.ps2_game_key(str(folder / "game.cue")) == key
print("PASS  same from .cue/.bin")

hit = "%08X" % crc
files = {
    "a/one": "gametitle=Test Game [SLUS-20312]\n[Infinite Health]\nauthor=x\npatch=1,EE,0010,extended,1\n[Heading only]\ncomment=no code\n",
    "b/two": "gametitle=Test\n[infinite  health]\npatch=1,EE,0010,extended,1\n[Max Money]\n// note\npatch=1,EE,0020,extended,FF\n",
}
cheats._ps2_lists = lambda: {"shadowninja826/pcsx2_pnach_cheats": ["/%s.pnach" % hit, "/00000000.pnach"],
                             "xs1l3n7x/pcsx2_cheats_collection": ["/cheats/SLUS-20312_%s.pnach" % hit]}
real_fetch = gameinfo.fetch
gameinfo.fetch = lambda url, timeout=30: (files["a/one"] if "shadowninja" in url else files["b/two"]).encode()
r = cheats.fetch_ps2(str(folder / "game.iso"))
gameinfo.fetch = real_fetch
assert r and r["file"] == "SLUS-20312_%s.pnach" % hit and r["count"] == 2, r
assert "[Infinite Health]" in r["text"] and "[Max Money]" in r["text"] and "Heading only" not in r["text"], r["text"]
assert r["text"].startswith("gametitle=Test Game"), r["text"]
print("PASS  two collections merged, duplicates and empty groups dropped")
print("ALL PS2 CHEAT TESTS PASSED")
