"""Pretends to be adb with two devices, for the self-test (Linux/macOS only)."""
import sys
args = sys.argv[1:]
serial = None
if args[:1] == ["-s"]:
    serial, args = args[1], args[2:]
PROPS = {
    "R5CX1234": {"ro.product.manufacturer": "samsung", "ro.product.model": "SM-S931B", "ro.build.version.release": "15",
                 "ro.build.version.sdk": "35", "ro.product.cpu.abilist": "arm64-v8a", "ro.build.characteristics": "phone"},
    "192.168.1.50:5555": {"ro.product.manufacturer": "Google", "ro.product.model": "Google TV Streamer",
                          "ro.build.version.release": "14", "ro.build.version.sdk": "34",
                          "ro.product.cpu.abilist": "armeabi-v7a,armeabi", "ro.build.characteristics": "tv"},
}
cmd = args[0] if args else ""
if cmd == "devices":
    print("List of devices attached")
    print("R5CX1234               device usb:1-1 product:pa3qxxx model:SM_S931B device:pa3q transport_id:1")
    print("192.168.1.50:5555      device product:kirkwood model:Google_TV_Streamer device:kirkwood transport_id:2")
    print("RZ8T999                unauthorized usb:1-2 transport_id:3")
elif cmd == "shell":
    sub = " ".join(args[1:])
    p = PROPS.get(serial, {})
    if sub == "getprop":
        for k, v in p.items():
            print("[%s]: [%s]" % (k, v))
    elif sub.startswith("pm list features"):
        print("feature:android.hardware.wifi")
        if p.get("ro.build.characteristics") == "tv":
            print("feature:android.software.leanback")
    elif sub.startswith("df"):
        print("Filesystem     1K-blocks    Used Available Use% Mounted on")
        print("/dev/block/dm-48 110000000 50000000 60000000 46% /data" if serial == "R5CX1234" else
              "/dev/block/dm-5 9000000 7000000 2000000 78% /data")
    elif sub.startswith("am start"):
        print("Starting: Intent { cmp=x }")
elif cmd == "install":
    if serial == "192.168.1.50:5555" and "TVFAIL" in " ".join(args):
        print("Performing Streamed Install")
        print("adb: failed to install x.apk: Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: Existing package signatures do not match]")
        sys.exit(1)
    print("Performing Streamed Install")
    print("Success")
elif cmd == "connect":
    print("failed to connect to '%s': Connection refused" % args[1] if args[1].startswith("10.") else "connected to %s" % args[1])
elif cmd == "pair":
    print("Successfully paired to %s [guid=adb-x]" % args[1])
elif cmd in ("disconnect", "uninstall"):
    print("Success")
