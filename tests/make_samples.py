"""Builds tiny synthetic game files with real header layouts (no copyrighted data)."""
import struct
import zlib
from pathlib import Path

from app.core.identify import _crc16

SECTOR = 2048


def n64(title=b"TEST N64 GAME", code=b"NTSE", fmt="z64"):
    h = bytearray(0x1000)
    h[0:4] = b"\x80\x37\x12\x40"
    h[0x20:0x20 + len(title)] = title.ljust(20, b" ")[:20]
    h[0x3B:0x3F] = code
    h[0x3F] = 1
    if fmt == "v64":
        b = bytearray(h)
        b[0::2], b[1::2] = h[1::2], h[0::2]
        return bytes(b)
    if fmt == "n64":
        return b"".join(bytes(h[i:i + 4][::-1]) for i in range(0, len(h), 4))
    return bytes(h)


def nds(title=b"TESTDSGAME", code=b"ATDE"):
    h = bytearray(0x4000)
    h[0:len(title)] = title
    h[0x0C:0x10] = code
    struct.pack_into("<H", h, 0x15C, 0xCF56)
    struct.pack_into("<H", h, 0x15E, _crc16(bytes(h[:0x15E])))
    return bytes(h)


def gba(title=b"TEST GBA", code=b"BTGE"):
    h = bytearray(0x400)
    h[0xA0:0xA0 + len(title)] = title
    h[0xAC:0xB0] = code
    h[0xB0:0xB2] = b"01"
    h[0xB2] = 0x96
    h[0xBD] = (-(sum(h[0xA0:0xBD]) + 0x19)) & 0xFF
    return bytes(h) + bytes(range(256)) * 64


def nintendo_disc(gid=b"GTSE01", name=b"Test Cube Game", wii=False):
    h = bytearray(0x10000)
    h[0:6] = gid
    h[0x20:0x20 + len(name)] = name
    if wii:
        struct.pack_into(">I", h, 0x18, 0x5D1C9EA3)
    else:
        struct.pack_into(">I", h, 0x1C, 0xC2339F3D)
    return bytes(h)


def _dirrec(name: bytes, lba: int, size: int, is_dir: bool) -> bytes:
    ln = 33 + len(name) + (1 - len(name) % 2)
    r = bytearray(ln)
    r[0] = ln
    struct.pack_into("<I", r, 2, lba); struct.pack_into(">I", r, 6, lba)
    struct.pack_into("<I", r, 10, size); struct.pack_into(">I", r, 14, size)
    r[25] = 2 if is_dir else 0
    r[32] = len(name)
    r[33:33 + len(name)] = name
    return bytes(r)


def iso(files: dict, volume=b"TESTVOL"):
    """files: {'SYSTEM.CNF': bytes} or {'PSP_GAME/PARAM.SFO': bytes} (one level of folders)."""
    sectors = {}
    nxt = 18
    root_lba = nxt; nxt += 1
    root_entries = [_dirrec(b"\x00", root_lba, SECTOR, True), _dirrec(b"\x01", root_lba, SECTOR, True)]
    dirs = {}
    for path, data in files.items():
        parts = path.split("/")
        if len(parts) == 2:
            dirs.setdefault(parts[0], []).append((parts[1], data))
        else:
            lba = nxt; nxt += (len(data) + SECTOR - 1) // SECTOR
            sectors[lba] = data
            root_entries.append(_dirrec(parts[0].encode() + b";1", lba, len(data), False))
    for d, items in dirs.items():
        dlba = nxt; nxt += 1
        ents = [_dirrec(b"\x00", dlba, SECTOR, True), _dirrec(b"\x01", root_lba, SECTOR, True)]
        for n, data in items:
            lba = nxt; nxt += (len(data) + SECTOR - 1) // SECTOR
            sectors[lba] = data
            ents.append(_dirrec(n.encode() + b";1", lba, len(data), False))
        sectors[dlba] = b"".join(ents)
        root_entries.append(_dirrec(d.encode(), dlba, SECTOR, True))
    sectors[root_lba] = b"".join(root_entries)
    pvd = bytearray(SECTOR)
    pvd[0] = 1; pvd[1:6] = b"CD001"; pvd[6] = 1
    pvd[40:72] = volume.ljust(32, b" ")
    pvd[156:190] = _dirrec(b"\x00", root_lba, SECTOR, True)
    sectors[16] = bytes(pvd)
    term = bytearray(SECTOR); term[0] = 255; term[1:6] = b"CD001"
    sectors[17] = bytes(term)
    out = bytearray(nxt * SECTOR)
    for lba, data in sectors.items():
        out[lba * SECTOR: lba * SECTOR + len(data)] = data
    return bytes(out)


def sfo(entries: dict):
    keys = b""; data = b""; index = b""
    for k, v in entries.items():
        raw = v.encode() + b"\x00"
        size = (len(raw) + 3) & ~3
        index += struct.pack("<HHIII", len(keys), 0x0204, len(raw), size, len(data))
        keys += k.encode() + b"\x00"
        data += raw.ljust(size, b"\x00")
    key_start = 20 + len(index)
    while (key_start + len(keys)) % 4:
        keys += b"\x00"
    return struct.pack("<4sIIII", b"\x00PSF", 0x101, key_start, key_start + len(keys), len(entries)) + index + keys + data


def cso(iso_bytes: bytes, block=SECTOR):
    blocks = (len(iso_bytes) + block - 1) // block
    header = struct.pack("<4sIQIBB2x", b"CISO", 24, len(iso_bytes), block, 1, 0)
    offset = 24 + 4 * (blocks + 1)
    index, body = [], b""
    for i in range(blocks):
        chunk = iso_bytes[i * block:(i + 1) * block]
        c = zlib.compressobj(9, zlib.DEFLATED, -15)
        comp = c.compress(chunk) + c.flush()
        index.append(offset + len(body))
        body += comp
    index.append(offset + len(body))
    return header + struct.pack("<%dI" % len(index), *index) + body


def ps2_bios(romver=b"0220HC20060905\n"):
    data = bytearray(4 * 1024 * 1024)
    romdir = 0x2700
    entries = [(b"RESET", romdir), (b"ROMDIR", 0x50), (b"EXTINFO", 0x10), (b"ROMVER", 0x10)]
    for i, (name, size) in enumerate(entries):
        e = bytearray(16)
        e[0:len(name)] = name
        struct.pack_into("<I", e, 12, size)
        data[romdir + i * 16: romdir + i * 16 + 16] = e
    data[0x2750 + 0x10: 0x2750 + 0x10 + len(romver)] = romver  # after RESET(0x2700)+ROMDIR(0x50)+EXTINFO(0x10)
    return bytes(data)


def write_all(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    files = {
        "Test N64 Game.z64": n64(),
        "Test N64 Swapped.v64": n64(b"SWAPPED GAME", b"NSWJ", "v64"),
        "Test N64 Little.n64": n64(b"LITTLE GAME", b"NLTP", "n64"),
        "Test DS Game.nds": nds(),
        "Test Cube.iso": nintendo_disc(),
        "Test Wii.iso": nintendo_disc(b"RTWE01", b"Test Wii Game", wii=True),
        "Test PS2.iso": iso({"SYSTEM.CNF": b"BOOT2 = cdrom0:\\SLUS_123.45;1\r\nVER = 1.01\r\nVMODE = NTSC\r\n"}, b"TESTPS2"),
        "Test PSP.iso": iso({"PSP_GAME/PARAM.SFO": sfo({"TITLE": "Test PSP Game", "DISC_ID": "ULUS10999", "DISC_VERSION": "1.00"})}),
        "Mystery.chd": b"MComprHD" + b"\x00" * 200,
        "Not a game.iso": b"\x11" * 70000,
    }
    files["Test PSP Compressed.cso"] = cso(files["Test PSP.iso"])
    for name, data in files.items():
        (folder / name).write_bytes(data)
    (folder / "bios").mkdir(exist_ok=True)
    (folder / "bios" / "SCPH-TEST.bin").write_bytes(ps2_bios())
    (folder / "bios" / "fake.bin").write_bytes(b"\x01" * 4 * 1024 * 1024)
    return folder


# ---- PS2 CD games as BIN/CUE (raw 2352-byte sectors, like Redump dumps) ----
CD_SYNC = b"\x00" + b"\xff" * 10 + b"\x00"


def raw_cd(iso_bytes: bytes, mode=2) -> bytes:
    """ISO -> raw CD sectors (Mode 2 Form 1: 24-byte header, Mode 1: 16-byte header)."""
    out = bytearray()
    for i in range(0, len(iso_bytes), SECTOR):
        lba = i // SECTOR + 150
        m, s, f = lba // 4500, (lba // 75) % 60, lba % 75
        bcd = lambda v: (v // 10) * 16 + v % 10
        hdr = CD_SYNC + bytes([bcd(m), bcd(s), bcd(f), mode])
        if mode == 2:
            hdr += b"\x00\x00\x08\x00" * 2
        out += hdr + iso_bytes[i:i + SECTOR].ljust(SECTOR, b"\x00")
        out += b"\x00" * (2352 - len(hdr) - SECTOR)
    return bytes(out)


def ps2_cd_iso(serial=b"SLUS_201.40", volume=b"PS2CD"):
    data = iso({"SYSTEM.CNF": b"BOOT2 = cdrom0:\\" + serial + b";1\r\nVER = 1.00\r\nVMODE = NTSC\r\n"}, volume)
    return data + b"\x5a" * (2 * 1024 * 1024)  # some game data


def write_ps2_cue_games(folder: Path):
    """Three BIN/CUE layouts plus a lone raw .bin. Returns {name: iso bytes} for checking."""
    folder.mkdir(parents=True, exist_ok=True)
    out = {}
    # 1. Redump style: one .bin per track, names unlike the .cue
    a = ps2_cd_iso(b"SLUS_201.40", b"NBASTREET")
    (folder / "NBA Test (Track 1).bin").write_bytes(raw_cd(a))
    (folder / "NBA Test (Track 2).bin").write_bytes(b"\x01\x02" * 2352 * 30)
    (folder / "NBA Test.cue").write_text(
        'FILE "NBA Test (Track 1).bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'
        'FILE "NBA Test (Track 2).bin" BINARY\n  TRACK 02 AUDIO\n    INDEX 00 00:00:00\n    INDEX 01 00:02:00\n')
    out["NBA Test.cue"] = a
    # 2. single track, lower-case file name in the cue
    b = ps2_cd_iso(b"SLES_503.43", b"ONI")
    (folder / "Oni Test.bin").write_bytes(raw_cd(b))
    (folder / "Oni Test.cue").write_text('FILE "oni test.bin" BINARY\r\n  TRACK 01 MODE2/2352\r\n    INDEX 01 00:00:00\r\n')
    out["Oni Test.cue"] = b
    # 3. one .bin holding the data track followed by a music track
    c = ps2_cd_iso(b"SLUS_203.05", b"EXTERMINATION")
    raw = raw_cd(c)
    sectors = len(raw) // 2352
    (folder / "Multi.bin").write_bytes(raw + b"\x07" * 2352 * 200)
    mm, ss, ff = sectors // 4500, (sectors // 75) % 60, sectors % 75
    (folder / "Multi Test.cue").write_text(
        'FILE "Multi.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'
        '  TRACK 02 AUDIO\n    INDEX 01 %02d:%02d:%02d\n' % (mm, ss, ff))
    out["Multi Test.cue"] = c
    # 4. a raw .bin on its own (no .cue)
    d = ps2_cd_iso(b"SLUS_201.81", b"SFEX3")
    (folder / "Lone Raw.bin").write_bytes(raw_cd(d))
    out["Lone Raw.bin"] = d
    return out
