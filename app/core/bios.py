"""Inspect BIOS / firmware files the user supplies. Nothing is ever downloaded."""
import re

PS2_BIOS_SIZES = {4 * 1024 * 1024, 8 * 1024 * 1024, 12 * 1024 * 1024, 32 * 1024 * 1024}

PS2_REGION = {"J": "Japan", "A": "USA", "E": "Europe", "H": "Asia", "C": "China", "T": "Test unit"}

NDS_FILES = {
    16384: ("bios7", "ARM7 BIOS"),
    4096: ("bios9", "ARM9 BIOS"),
    262144: ("firmware", "Firmware (DS)"),
    131072: ("firmware", "Firmware (DS Lite, iQue)"),
    524288: ("firmware", "Firmware (DSi)"),
}


def inspect_ps2(data: bytes) -> dict:
    """PS2 BIOS images start with a ROMDIR table listing RESET, ROMDIR, ROMVER..."""
    if len(data) not in PS2_BIOS_SIZES:
        return dict(valid=False, message="Wrong size for a PS2 BIOS (%d bytes). Expected 4 MB." % len(data))
    idx = data.find(b"RESET\x00")
    if idx < 0 or idx > 0x4000:
        return dict(valid=False, message="This file doesn't contain a PS2 BIOS directory (RESET entry missing).")
    # Walk ROMDIR: 16-byte entries name[10], ext_info_size(2), file_size(4)
    offset = 0
    pos = idx
    romver = None
    while pos + 16 <= len(data):
        name = data[pos:pos + 10].split(b"\x00")[0]
        if not name:
            break
        size = int.from_bytes(data[pos + 12:pos + 16], "little")
        if name == b"ROMVER":
            romver = data[offset:offset + min(size, 32)].split(b"\x00")[0].decode("ascii", "replace").strip()
        offset += (size + 15) & ~15
        pos += 16
    if not romver:
        return dict(valid=False, message="PS2 BIOS directory found, but its version entry (ROMVER) is missing.")
    m = re.match(r"(\d{2})(\d{2})([A-Z])([A-Z])(\d{8})", romver)
    if not m:
        return dict(valid=True, version=romver, region=None, message="Version text not recognised: %s" % romver)
    major, minor, reg, kind, date = m.groups()
    version = "%d.%s" % (int(major), minor)
    kind_name = {"C": "retail", "D": "development"}.get(kind, kind)
    return dict(valid=True, version=version, region=PS2_REGION.get(reg, reg),
                message="PS2 BIOS v%s, %s, %s console, dated %s-%s-%s."
                % (version, PS2_REGION.get(reg, reg), kind_name, date[:4], date[4:6], date[6:]))


def inspect_nds(data: bytes) -> dict:
    hit = NDS_FILES.get(len(data))
    if not hit:
        return dict(valid=False, message="Size doesn't match any DS BIOS or firmware file (%d bytes)." % len(data))
    kind, label = hit
    if data.count(0) == len(data) or data.count(0xFF) == len(data):
        return dict(valid=False, message="File is empty (all zeros or all FF) — the dump failed.")
    return dict(valid=True, version=kind, region=None, message="%s detected by size." % label)


def inspect(console: str, data: bytes) -> dict:
    if console == "ps2":
        return inspect_ps2(data)
    if console == "nds":
        return inspect_nds(data)
    return dict(valid=False, message="BIOS files aren't needed for this console.")
