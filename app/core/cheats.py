"""Cheats packed into each game app, ready in its pause menu ("Cheats").

N64, DS, GBA, PSP and PS1: RetroArch's cheat collection (libretro-database/cht), found by the
game's official name. GameCube and Wii need nothing from here: Dolphin builds the list from its own
game settings when the game starts.

The app gets assets/tirso/cheats.json: [{"name": ..., "code": ...}, ...], all off until you turn them on.
"""
import gzip
import json
import re
import urllib.parse
from pathlib import Path

from . import gameinfo, paths

RAW_URL = "https://raw.githubusercontent.com/libretro/libretro-database/master/cht/{}/{}.cht"
_index = None


def index():
    global _index
    if _index is None:
        with gzip.open(paths.app_root() / "data" / "cheats_index.json.gz", "rb") as f:
            _index = json.loads(f.read().decode("utf-8"))
    return _index


def supported(console):
    return console in index()["names"]


def find_file(console, name, serial=None):
    """The cheat file name for a game: by its disc code when the file names carry one (PSP), else by its
    official name (the same version, else the same game from another region)."""
    names = index()["names"].get(console) or []
    if not names:
        return None
    key = re.sub(r"[^A-Z0-9]", "", (serial or "").upper())
    if key:
        by_code = [n for n in names if any(re.sub(r"[^A-Z0-9]", "", c.upper()) == key
                                             for c in re.findall(r"\[([A-Z]{4}-?\d{5})\]", n))]
        if by_code:
            return sorted(by_code, key=lambda n: ("Demo" in n, len(n)))[0]
        if console == "psp":
            return None  # a PSP game's codes only fit that exact release
    if not name:
        return None
    if name in names:
        return name
    safe = gameinfo.safe_thumb_name(name)
    if safe in names:
        return safe
    want = gameinfo.norm(gameinfo.TAGS.sub("", name))
    same = [n for n in names if gameinfo.norm(gameinfo.TAGS.sub("", n)) == want]
    if not same:
        return None
    m = re.search(r"\((USA|Europe|Japan|World)", name)
    region = m.group(1) if m else None
    order = [r for r in (region, "USA", "World", "Europe", "Japan") if r]

    def score(n):
        s = 0
        for i, r in enumerate(order):
            if "(%s" % r in n or ", %s" % r in n:
                s += 100 - i * 10
                break
        if gameinfo.AVOID.search(n) or "(diff)" in n:
            s -= 200
        if "(GameShark)" in n or "(Action Replay)" in n:
            s += 5  # the code type every engine here reads
        return s
    return max(same, key=score)


def parse_cht(text):
    """RetroArch .cht -> [{"name", "code"}], without headings and empty codes."""
    fields = {}
    for line in text.splitlines():
        m = re.match(r'\s*cheat(\d+)_(desc|code|enable)\s*=\s*"?(.*?)"?\s*$', line)
        if m:
            fields.setdefault(int(m.group(1)), {})[m.group(2)] = m.group(3)
    out = []
    for i in sorted(fields):
        f = fields[i]
        name = (f.get("desc") or "").strip()
        code = (f.get("code") or "").strip()
        if not code or not name:
            continue
        parts = [p.strip() for p in code.split("+") if p.strip()]
        # headings and notes in some files have only zero codes ("_L 0x00000000 0x00000000")
        if all(re.fullmatch(r"(_L\s+)?(0x)?0+\s+(0x)?0+", p, re.I) for p in parts):
            continue
        if re.fullmatch(r"[_\-=\s\[\]<>*#.]+", name):
            continue
        out.append({"name": name[:120], "code": "+".join(parts)})
    return out


def fetch(console, name, serial=None, log=lambda s: None):
    """Cheats for a game (downloaded once, then kept). None when there are none."""
    file_name = find_file(console, name, serial)
    if not file_name:
        return None
    system = index()["systems"][console]
    cache = paths.sub("cheats") / console / (re.sub(r'[<>:"/\\|?*]', "_", file_name) + ".json")
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except ValueError:
            pass
    url = RAW_URL.format(urllib.parse.quote(system), urllib.parse.quote(file_name))
    data = gameinfo.fetch(url, timeout=60)
    if data is None:
        return None
    cheats = parse_cht(data.decode("utf-8", "replace"))
    result = {"source": file_name, "cheats": cheats}
    cache.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def official_name(game, rom):
    """The game's official name: from its saved game information, else worked out from the file."""
    info_dir = game.get("info_dir")
    if info_dir and (Path(info_dir) / "info.json").exists():
        try:
            return json.loads((Path(info_dir) / "info.json").read_text(encoding="utf-8")).get("name")
        except ValueError:
            pass
    console = rom.get("console")
    if console not in gameinfo.SYSTEMS:
        return None
    from . import identify
    ident = identify.identify(rom["path"])
    ident["serial"] = ident.get("serial") or rom.get("serial")
    m = gameinfo.match_game(console, rom["path"], ident, crc=rom.get("crc32"))
    return m.get("name")


# ------------------------------------------------------------------ PlayStation 2 (.pnach, ARMSX2)
PS2_SOURCES = (
    ("shadowninja826/pcsx2_pnach_cheats", "main"),
    ("xs1l3n7x/pcsx2_cheats_collection", "main"),
)
PS2_LIST_URL = "https://data.jsdelivr.com/v1/packages/gh/{}@{}?structure=flat"
PS2_RAW_URL = "https://raw.githubusercontent.com/{}/{}{}"


def ps2_game_key(path):
    """(serial, CRC) of a PS2 disc: the CRC is PCSX2's game CRC (all 32-bit words of the boot program XORed).
    None when the disc can't be read here (CHD)."""
    import os
    import struct
    from . import discs
    ext = os.path.splitext(path)[1].lower()
    with open(path, "rb") as f:
        if ext == ".cso":
            reader = discs.CsoReader(f)
        elif ext == ".cue":
            cue = discs.parse_cue(path)
            if not cue["data"]:
                return None
            f2 = open(cue["data"]["path"], "rb")
            try:
                return _ps2_key_from(discs.Iso9660(discs.TrackReader(f2, cue["data"])))
            finally:
                f2.close()
        elif ext == ".bin":
            reader = discs.TrackReader(f, discs.raw_track(path))
        elif ext == ".iso":
            reader = discs.IsoReader(f)
        else:
            return None
        return _ps2_key_from(discs.Iso9660(reader))


def _ps2_key_from(fs):
    import struct
    cnf = fs.read_file("SYSTEM.CNF", 4096)
    if not cnf:
        return None
    m = re.search(r"BOOT2\s*=\s*cdrom0:\\?([^;\s]+)", cnf.decode("ascii", "replace"), re.I)
    if not m:
        return None
    elf_path = m.group(1).replace("\\", "/")
    elf = fs.read_file(elf_path, 64 << 20)
    if not elf:
        return None
    n = len(elf) // 4
    crc = 0
    for (w,) in struct.iter_unpack("<I", elf[:n * 4]):
        crc ^= w
    stem = elf_path.split("/")[-1]
    s = re.match(r"([A-Z]{4})[_-](\d{3})\.?(\d{2})", stem.upper())
    serial = "%s-%s%s" % s.groups() if s else None
    return serial, "%08X" % crc


def _ps2_lists():
    """File lists of the PS2 cheat collections (through jsDelivr: no GitHub limits), kept for a week."""
    cache = paths.sub("cheats") / "ps2_lists.json"
    if cache.exists() and gameinfo._fresh(cache, 7):
        return json.loads(cache.read_text(encoding="utf-8"))
    lists = {}
    for repo, branch in PS2_SOURCES:
        data = gameinfo.fetch(PS2_LIST_URL.format(repo, branch), timeout=90)
        if data:
            lists[repo] = [f["name"] for f in json.loads(data).get("files", []) if f["name"].lower().endswith(".pnach")]
    if lists:
        cache.write_text(json.dumps(lists), encoding="utf-8")
    elif cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    return lists


def fetch_ps2(path, log=lambda s: None):
    """PS2 cheats for a disc as one .pnach text, plus its file name (<SERIAL>_<CRC>.pnach). None if none."""
    key = ps2_game_key(path)
    if not key:
        return None
    serial, crc = key
    cache = paths.sub("cheats") / "ps2" / ("%s_%s.json" % (serial or "NOSERIAL", crc))
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8")) or None
        except ValueError:
            pass
    sections, seen, title = [], set(), ""
    for repo, branch in PS2_SOURCES:
        names = _ps2_lists().get(repo) or []
        hits = [n for n in names if crc in n.upper().rsplit("/", 1)[-1]]
        for n in hits[:4]:
            data = gameinfo.fetch(PS2_RAW_URL.format(repo, branch, urllib.parse.quote(n)), timeout=60)
            if not data:
                continue
            text = data.decode("utf-8", "replace")
            m = re.search(r"^\s*gametitle\s*=\s*(.+)$", text, re.M | re.I)
            title = title or (m.group(1).strip() if m else "")
            for name, body in _pnach_sections(text):
                k = re.sub(r"\s+", " ", name.strip().lower())
                if k not in seen:
                    seen.add(k)
                    sections.append((name, body))
    result = None
    if sections:
        text = "gametitle=%s\n\n" % (title or serial or crc)
        text += "\n".join("[%s]\n%s\n" % (n, b.strip()) for n, b in sections)
        result = {"file": "%s_%s.pnach" % (serial, crc) if serial else "%s.pnach" % crc,
                  "count": len(sections), "text": text}
    cache.write_text(json.dumps(result), encoding="utf-8")
    return result


def _pnach_sections(text):
    """[Name] groups of a .pnach (patch=... lines under each), skipping the ones with no code."""
    out, name, body = [], None, []
    for line in text.splitlines():
        m = re.match(r"^\s*\[(.+?)\]\s*$", line)
        if m:
            if name and any(re.match(r"\s*patch\s*=", b, re.I) for b in body):
                out.append((name, "\n".join(body)))
            name, body = m.group(1), []
        elif name is not None:
            if re.match(r"\s*(patch|author|description|comment)\s*=", line, re.I) or line.strip().startswith("//"):
                body.append(line.rstrip())
    if name and any(re.match(r"\s*patch\s*=", b, re.I) for b in body):
        out.append((name, "\n".join(body)))
    return out
