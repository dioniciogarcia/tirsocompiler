"""Device install test with a simulated adb. Run: python -m tests.test_devices (Linux/macOS)."""
import os
import stat
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["TIRSO_DATA_DIR"] = tempfile.mkdtemp(prefix="tirso-devtest-")
wrapper = Path(tempfile.mkdtemp()) / "adb"
wrapper.write_text("#!/bin/sh\nexec %s %s \"$@\"\n" % (sys.executable, Path(__file__).with_name("fake_adb.py")))
wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
os.environ["TIRSO_ADB"] = str(wrapper)

from app.api import Api  # noqa: E402
from app.core import adb  # noqa: E402
from tests import make_samples  # noqa: E402

events = []
api = Api(emit=lambda e, d: events.append((e, d)))

devs = api.list_devices()["devices"]
by = {d["serial"]: d for d in devs}
phone, tv, locked = by["R5CX1234"], by["192.168.1.50:5555"], by["RZ8T999"]
assert phone["name"] == "samsung SM-S931B" and phone["has64"] and not phone["is_tv"], phone
assert tv["is_tv"] and not tv["has64"] and tv["has32"] and tv["wifi"], tv
assert locked["state"] == "unauthorized" and "Allow USB debugging" in locked["state_text"]
assert phone["free_h"] and "GB" in phone["free_h"]
print("PASS device list:", phone["name"], "/", tv["name"], "/", locked["state_text"])

assert api.connect_device("192.168.1.50")["ok"]
r = api.connect_device("10.0.0.9")
assert not r["ok"] and "Network debugging" in r["error"], r
assert api.pair_device("192.168.1.50:41235", "123 456")["ok"]
assert not api.pair_device("192.168.1.50:41235", "12")["ok"]
print("PASS connect / pair messages")

# build a phone-only APK, then try both devices
folder = Path(tempfile.mkdtemp())
data = bytearray(1 << 20); data[:0x1000] = make_samples.n64(b"DEV TEST", b"NDTE")
(folder / "Dev Test.z64").write_bytes(bytes(data))
api.import_paths([str(folder / "Dev Test.z64")])
while api.get_overview()["counts"]["hashing"]:
    time.sleep(0.1)
api._db.set_setting("output_dir", tempfile.mkdtemp())
rom = api.list_roms()[0]
g = api.save_game(dict(rom_id=rom["id"], console="n64", app_name="Dev Test", package_id="com.tirso.devtest",
                       orientation="landscape", profile="balanced"))
b = api.generate_apk(g["id"])
while api.current_build():
    time.sleep(0.3)
assert api.get_build(b["id"])["status"] == "success"

r = api.install_build(b["id"], "192.168.1.50:5555")
assert not r["ok"] and "32-bit" in r["error"] and "TV support" in r["error"], r
print("PASS phone APK refused for 32-bit TV:", r["error"])

r = api.install_build(b["id"], "R5CX1234")
assert r["ok"], r
for _ in range(50):
    done = [d for e, d in events if e == "install_done"]
    if done:
        break
    time.sleep(0.1)
assert done and done[0]["ok"] and done[0]["launched"], done
print("PASS installed and opened on phone")

r = api.install_build(b["id"], "RZ8T999")
assert not r["ok"] and "Allow USB debugging" in r["error"]
assert "different key" in adb.explain_install_error("Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: x]")
assert adb.parse_df_free("Filesystem 1K-blocks Used Available Use% Mounted\n/dev/x 100 50 2048 50% /data") == 2048 * 1024
print("PASS error explanations")
print("DEVICE TESTS PASSED")
