"""Identify a game file by reading its header, never by file extension alone.

Returns a dict:
    console   - console id from consoles.py, or None when the header is unknown
    format    - short format name ("z64", "iso", "cso", "rvz", ...)
    title     - title read from the file, when present
    serial    - game code / disc ID, when present
    region    - "USA", "Europe", "Japan", ... when it can be told
    version   - revision, when present
    confident - True when the header proved the console
    note      - plain-language explanation
"""
import os
import re
import struct

from . import consoles
from .discs import GCZ_MAGIC, CsoReader, CueError, GczReader, Iso9660, IsoReader, TrackReader, parse_cue, parse_sfo

REGION_LETTER = {
    "E": "USA", "J": "Japan", "P": "Europe", "D": "Germany", "F": "France",
    "S": "Spain", "I": "Italy", "K": "Korea", "U": "Australia", "X": "Europe",
    "Y": "Europe", "A": "World", "C": "China", "H": "Netherlands", "N": "Canada",
}


def _clean(b: bytes) -> str:
    return b.split(b"\x00")[0].decode("ascii", "replace").strip()


def _result(console=None, fmt=None, title=None, serial=None, region=None, version=None,
            confident=False, note=""):
    return dict(console=console, format=fmt, title=title or None, serial=serial or None,
                region=region, version=version, confident=confident, note=note)


# ---------------------------------------------------------------- N64
N64_MAGIC = {
    b"\x80\x37\x12\x40": "z64",  # big-endian (native)
    b"\x37\x80\x40\x12": "v64",  # byte-swapped
    b"\x40\x12\x37\x80": "n64",  # little-endian
}


def _n64_to_native(data: bytes, fmt: str) -> bytes:
    if fmt == "v64":
        b = bytearray(data)
        b[0::2], b[1::2] = data[1::2], data[0::2]
        return bytes(b)
    if fmt == "n64":
        n = len(data) - len(data) % 4
        return b"".join(data[i:i + 4][::-1] for i in range(0, n, 4))
    return data


def _n64(head: bytes):
    fmt = N64_MAGIC.get(head[:4])
    if not fmt:
        return None
    h = _n64_to_native(head[:0x40], fmt)
    title = _clean(h[0x20:0x34])
    code = h[0x3B:0x3F].decode("ascii", "replace")
    region = REGION_LETTER.get(code[3:4]) if code.strip("\x00") else None
    version = "1.%d" % h[0x3F]
    return _result("n64", fmt, title, code if code.isalnum() else None, region, version, True,
                   "Nintendo 64 cartridge header found.")


# ---------------------------------------------------------------- NDS
def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _nds(head: bytes):
    if len(head) < 0x160:
        return None
    logo_crc, header_crc = struct.unpack_from("<HH", head, 0x15C)
    if logo_crc != 0xCF56 or _crc16(head[:0x15E]) != header_crc:
        return None
    title = _clean(head[0x00:0x0C])
    code = head[0x0C:0x10].decode("ascii", "replace")
    return _result("nds", "nds", title, code, REGION_LETTER.get(code[3:4]), "1.%d" % head[0x1E], True,
                   "Nintendo DS cartridge header found (checksums valid).")


# ---------------------------------------------------------------- GBA
def _gba(head: bytes):
    """Game Boy Advance cartridge header: fixed 0x96 at 0xB2 and a valid header checksum at 0xBD."""
    if len(head) < 0xC0 or head[0xB2] != 0x96:
        return None
    chk = (-(sum(head[0xA0:0xBD]) + 0x19)) & 0xFF
    if chk != head[0xBD]:
        return None
    code = head[0xAC:0xB0].decode("ascii", "replace")
    return _result("gba", "gba", _clean(head[0xA0:0xAC]), code if code.isalnum() else None,
                   REGION_LETTER.get(code[3:4]), "1.%d" % head[0xBC], True,
                   "Game Boy Advance cartridge header found (checksum valid).")


# ---------------------------------------------------------------- GameCube / Wii
GC_MAGIC, WII_MAGIC = 0xC2339F3D, 0x5D1C9EA3


def _nintendo_disc_header(dh: bytes, fmt: str, note: str):
    wii = struct.unpack_from(">I", dh, 0x18)[0] == WII_MAGIC
    gc = struct.unpack_from(">I", dh, 0x1C)[0] == GC_MAGIC
    if not (wii or gc):
        return None
    gid = dh[0:6].decode("ascii", "replace")
    return _result("wii" if wii else "gc", fmt, _clean(dh[0x20:0x60]), gid, REGION_LETTER.get(gid[3:4]),
                   "1.%d" % dh[7], True, note)


def _rvz(head: bytes):
    if head[:4] not in (b"RVZ\x01", b"WIA\x01"):
        return None
    fmt = "rvz" if head[:3] == b"RVZ" else "wia"
    return _nintendo_disc_header(head[0x58:0xD8], fmt, "Dolphin %s compressed disc." % fmt.upper())


def _wbfs(f, head: bytes):
    if head[:4] != b"WBFS":
        return None
    hd_sec = 1 << head[8]
    f.seek(hd_sec)
    dh = f.read(0x100)
    return _nintendo_disc_header(dh, "wbfs", "WBFS Wii disc image.")


# ---------------------------------------------------------------- PlayStation discs
PS_REGION = [
    (("SLUS", "SCUS", "ULUS", "UCUS", "NPUH", "NPUG"), "USA"),
    (("SLES", "SCES", "SCED", "ULES", "UCES", "NPEH", "NPEG"), "Europe"),
    (("SLPS", "SLPM", "SCPS", "SLKA", "ULJS", "ULJM", "UCJS", "NPJH", "NPJG", "SCAJ", "PAPX"), "Japan"),
    (("ULAS", "UCAS", "SCKA"), "Asia"),
    (("ULKS", "UCKS"), "Korea"),
]


def _ps_region(serial):
    if not serial:
        return None
    for prefixes, name in PS_REGION:
        if serial[:4].upper() in prefixes:
            return name
    return None


def _from_iso9660(reader, fmt):
    try:
        fs = Iso9660(reader)
    except Exception:
        return None
    sfo = fs.read_file("PSP_GAME/PARAM.SFO")
    if sfo is not None:
        info = parse_sfo(sfo)
        serial = info.get("DISC_ID")
        return _result("psp", fmt, info.get("TITLE"), serial, _ps_region(serial),
                       info.get("DISC_VERSION"), True, "PSP disc (PARAM.SFO found).")
    cnf = fs.read_file("SYSTEM.CNF", 4096)
    if cnf is not None:
        text = cnf.decode("ascii", "replace")
        m2 = re.search(r"BOOT2\s*=\s*cdrom0:\\?([^;\s]+)", text, re.I)
        m1 = re.search(r"BOOT\s*=\s*cdrom:\\?([^;\s]+)", text, re.I)
        ver = re.search(r"VER\s*=\s*([\w.]+)", text, re.I)
        if m2:
            serial = _serial(m2.group(1))
            return _result("ps2", fmt, fs.volume_id or None, serial, _ps_region(serial),
                           ver.group(1) if ver else None, True, "PlayStation 2 disc (SYSTEM.CNF with BOOT2).")
        if m1:
            serial = _serial(m1.group(1))
            return _result("ps1", fmt, fs.volume_id or None, serial, _ps_region(serial), None, True,
                           "PlayStation disc (SYSTEM.CNF with BOOT).")
    if fs.find("UMD_DATA.BIN"):
        return _result("psp", fmt, fs.volume_id or None, None, None, None, True, "PSP disc (UMD_DATA.BIN found).")
    return _result(None, fmt, fs.volume_id or None, None, None, None, False,
                   "Disc image, but no known console files were found.")


def _serial(boot_name: str):
    name = boot_name.split("\\")[-1].split("/")[-1].upper()
    m = re.match(r"([A-Z]{4})[_-]?(\d{3})\.?(\d{2})", name)
    return "%s-%s%s" % m.groups() if m else name


def _cue(path):
    """A .cue sheet: the game is in the .bin file(s) it lists, read through its data track."""
    try:
        cue = parse_cue(path)
    except CueError as e:
        return _result(None, "cue", note=str(e))
    if cue["missing"]:
        return _result(None, "cue", note="The CUE file lists %s, which isn't in the same folder. Keep the .cue and "
                       ".bin files together." % ", ".join(cue["missing"]))
    if not cue["data"]:
        return _result(None, "cue", note="This CUE file has no data track (audio CD?).")
    with open(cue["data"]["path"], "rb") as bf:
        res = _from_iso9660(TrackReader(bf, cue["data"]), "cue")
    if not res:
        return _result(None, "cue", note="The CUE file's data track couldn't be read as a disc.")
    res["note"] += " BIN/CUE (%d file%s)." % (len(cue["files"]), "" if len(cue["files"]) == 1 else "s")
    if cue["audio_tracks"]:
        res["note"] += " Has %d music track(s)." % cue["audio_tracks"]
    return res


def game_files(path):
    """Every file that makes up a game: a .cue plus its .bin files, or just the file itself."""
    if path.lower().endswith(".cue"):
        try:
            return [path] + parse_cue(path)["files"]
        except (CueError, OSError):
            return [path]
    return [path]


# ---------------------------------------------------------------- entry point
def identify(path: str) -> dict:
    ext = os.path.splitext(path)[1].lower()
    try:
        with open(path, "rb") as f:
            head = f.read(0x8000)
            if len(head) < 0x40:
                return _result(None, ext.lstrip("."), note="File is too small to be a game.")

            for check in (_n64, _nds, _gba, _rvz):
                res = check(head)
                if res:
                    return res
            res = _wbfs(f, head)
            if res:
                return res
            res = _nintendo_disc_header(head[:0x100], ext.lstrip(".") or "iso", "GameCube/Wii disc image.")
            if res:
                return res
            if head[:8] == b"MComprHD":
                guess = consoles.consoles_for_extension(".chd")
                return _result(None, "chd", note="CHD compressed disc. Its console can't be read without "
                               "decompressing it, so please choose it (%s)." % ", ".join(g.upper() for g in guess))
            if len(head) >= 4 and struct.unpack_from("<I", head, 0)[0] == GCZ_MAGIC:
                try:
                    dh = GczReader(f).read(0, 0x100)
                except Exception:
                    return _result(None, "gcz", note="Unreadable GCZ file.")
                return _nintendo_disc_header(dh, "gcz", "Dolphin GCZ compressed disc.") or \
                    _result(None, "gcz", note="GCZ file, but no GameCube/Wii disc header inside.")
            if head[:4] == b"CISO":
                return _from_iso9660(CsoReader(f), "cso") or _result(None, "cso", note="Unreadable CSO file.")
            if head[:4] == b"\x00PBP":
                return _result("ps1", "pbp", confident=False,
                               note="PBP package (usually PlayStation 1). Please confirm the console.")
            if ext == ".cue":
                return _cue(path)
            res = _from_iso9660(IsoReader(f), ext.lstrip(".") or "iso")
            if res:
                return res
    except OSError as e:
        return _result(None, ext.lstrip("."), note="Could not read the file: %s" % e.strerror)
    return _result(None, ext.lstrip("."), note="Unknown file. The console could not be identified from its contents.")
