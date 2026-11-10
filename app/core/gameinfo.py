"""Game information: which game a file is, plus its details and pictures.

Sources (all free, no account):
  - libretro-database (.rdb): official game names, checksums, serials, developer, genre...
  - OpenVGDB: descriptions (N64, DS, GBA, PSP; details for GameCube)
  - Wikipedia: descriptions for the rest (credited in the app)
  - libretro thumbnails: box art, screenshots, title screens and logos

Everything downloaded is kept in Tirso's data folder, so a second lookup is instant
and works offline.
"""
import difflib
import io
import json
import os
import re
import sqlite3
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from html import unescape
from pathlib import Path

from . import paths, rdb

USER_AGENT = "TirsoCompiler/1.0 (personal game library tool)"
RDB_URL = "https://raw.githubusercontent.com/libretro/libretro-database/master/rdb/{}.rdb"
OPENVGDB_URL = "https://github.com/OpenVGDB/OpenVGDB/releases/latest/download/openvgdb.zip"
THUMBS_URL = "https://thumbnails.libretro.com/{}/{}/"
WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{}"
REFRESH_DAYS = 60

# console id -> (libretro system, OpenVGDB system, name used on Wikipedia)
SYSTEMS = {
    "n64": ("Nintendo - Nintendo 64", "Nintendo 64", "Nintendo 64"),
    "nds": ("Nintendo - Nintendo DS", "Nintendo DS", "Nintendo DS"),
    "gba": ("Nintendo - Game Boy Advance", "Nintendo Game Boy Advance", "Game Boy Advance"),
    "psp": ("Sony - PlayStation Portable", "Sony PlayStation Portable", "PlayStation Portable"),
    "ps2": ("Sony - PlayStation 2", None, "PlayStation 2"),
    "gc": ("Nintendo - GameCube", "Nintendo GameCube", "GameCube"),
    "wii": ("Nintendo - Wii", "Nintendo Wii", "Wii"),
}
PICTURES = {"box": "Named_Boxarts", "snap": "Named_Snaps", "title": "Named_Titles", "logo": "Named_Logos"}
CARTRIDGES = {"n64", "nds", "gba"}

_locks = {}
_lock = threading.Lock()


class Offline(Exception):
    """The internet (or the site) couldn't be reached."""


def _named_lock(name):
    with _lock:
        return _locks.setdefault(name, threading.Lock())


# ------------------------------------------------------------------ network
def fetch(url, timeout=40):
    """The bytes at url, None when the site says it isn't there; Offline when it can't be reached."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return None
            last = e
            if e.code not in (429, 500, 502, 503, 504):
                break
        except (urllib.error.URLError, OSError) as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise Offline(str(last))


def _fresh(path: Path, days=REFRESH_DAYS):
    return path.exists() and time.time() - path.stat().st_mtime < days * 86400


def data_dir():
    return paths.sub("gamedb")


def _download_file(url, target: Path):
    data = fetch(url, timeout=120)
    if data is None:
        raise Offline("%s is not available" % url)
    tmp = target.with_suffix(target.suffix + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, target)


# ------------------------------------------------------------------ names
TAGS = re.compile(r"\s*[\(\[\{][^\)\]\}]*[\)\]\}]")
LANGS = "en|fr|de|es|it|nl|pt|sv|no|da|fi|ja|zh|ko|ru|pl|ca|eu|gl"
LOOSE = re.compile(
    r"\b(?:rev\s*\d+|v\d+(?:\.\d+)*|(?:%s)(?:[,+ ]?(?:%s))+|usa|europe|japan|eur|jpn|pal|ntsc(?:-[uj])?|"
    r"multi\d+|proper|decrypted|trimmed|nkit|scrubbed)\b" % (LANGS, LANGS), re.I)
REGION_WORDS = {
    "usa": "USA", "us": "USA", "u": "USA", "ntsc-u": "USA", "america": "USA",
    "europe": "Europe", "eur": "Europe", "e": "Europe", "pal": "Europe", "uk": "Europe",
    "japan": "Japan", "jpn": "Japan", "j": "Japan", "ntsc-j": "Japan",
    "world": "World", "korea": "Korea", "australia": "Australia",
}
AVOID = re.compile(r"\((?:[^)]*\b(?:beta|proto|demo|kiosk|sample|debug|alt|pirate|unl|virtual console|switch online|"
                   r"lodgenet|aftermarket|competition|preview|trade demo|e-reader|program)\b[^)]*)\)", re.I)
PLAIN_TAG = re.compile(r"^(?:usa|europe|japan|world|korea|australia|asia|brazil|canada|china|france|germany|italy|"
                       r"spain|netherlands|sweden|uk|[a-z]{2}(?:,[a-z]{2})*|rev \d+|disc \d+|v[\d.]+)(?:, ?.*)?$", re.I)


def norm(text):
    """A name reduced to plain lowercase words, for comparing (\"The\", accents and punctuation ignored)."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = text.lower().replace("&", " and ")
    text = re.sub(r"['’`]", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    words = [w for w in text.split() if w != "the"]
    return " ".join(words)


def base_title(name):
    """'Legend of Zelda, The - Minish Cap (USA) (Rev 1)' -> 'The Legend of Zelda: Minish Cap'"""
    t = TAGS.sub("", name or "").strip()
    m = re.match(r"^(.*?), (The|A|An)(\b.*)$", t)
    if m:
        t = "%s %s%s" % (m.group(2), m.group(1), m.group(3))
    t = t.replace(" - ", ": ")
    return re.sub(r"\s+", " ", t).strip()


def clean_filename(filename):
    """(title words to search for, region hint) from a file name such as
    'Mario Kart DS (USA) (Rev 1) EnEsFr.nds'."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    tags = " ".join(re.findall(r"[\(\[\{]([^\)\]\}]*)[\)\]\}]", stem)).lower()
    region = None
    for word in re.split(r"[,\s]+", tags):
        if word in REGION_WORDS:
            region = REGION_WORDS[word]
            break
    title = TAGS.sub(" ", stem)
    title = re.sub(r"\b(?:v|ver\.?\s*)\d+(?:\.\d+)+\b", " ", title, flags=re.I)
    title = re.sub(r"[_.]+", " ", title)
    if not region:
        m = re.search(r"\b(usa|europe|japan|eur|jpn|pal)\b", title, re.I)
        if m:
            region = REGION_WORDS[m.group(1).lower()]
    title = LOOSE.sub(" ", title)
    return re.sub(r"\s+", " ", title).strip(" -"), region


def _serial_key(s):
    s = (s or "").split("/")[0].upper()
    return re.sub(r"[^A-Z0-9]", "", s)


def safe_thumb_name(name):
    return re.sub(r"[&*/:`<>?\\|\"]", "_", name)


# ------------------------------------------------------------------ libretro database
class ConsoleDb:
    """One console's official game list, indexed by checksum, serial and name."""
    _cache = {}

    @classmethod
    def get(cls, console):
        with _named_lock("rdb-" + console):
            db = cls._cache.get(console)
            if db is None:
                db = cls(console)
                cls._cache[console] = db
            return db

    def __init__(self, console):
        system = SYSTEMS[console][0]
        path = data_dir() / (system + ".rdb")
        if not _fresh(path):
            try:
                _download_file(RDB_URL.format(urllib.parse.quote(system)), path)
            except Offline:
                if not path.exists():
                    raise
        self.console = console
        merged = {}
        for g in rdb.read(path):
            name = g.get("name")
            if not name:
                continue
            m = merged.setdefault(name, {})
            for k, v in g.items():
                if v not in (None, "") and k not in m:
                    m[k] = v
                if k in ("crc", "sha1", "serial") and v:
                    m.setdefault("_" + k + "s", set()).add(v)
        self.games = merged
        self.by_crc, self.by_sha1, self.by_serial, self.by_serial4, self.by_title = {}, {}, {}, {}, {}
        for name, g in merged.items():
            for c in g.get("_crcs", ()):
                self.by_crc.setdefault(c.lower(), []).append(name)
            for s in g.get("_sha1s", ()):
                self.by_sha1.setdefault(s.lower(), []).append(name)
            for s in g.get("_serials", ()):
                key = _serial_key(s)
                if key:
                    self.by_serial.setdefault(key, []).append(name)
                if console in ("gc", "wii"):
                    m = re.match(r"^(?:DL-DOL-|RVL-)?([A-Z0-9]{4})", s.upper())
                    if m:
                        self.by_serial4.setdefault(m.group(1), []).append(name)
            self.by_title.setdefault(norm(TAGS.sub("", name)), []).append(name)
        self._titles = list(self.by_title)

    def entry(self, name):
        g = dict(self.games.get(name) or {})
        for k in [k for k in g if k.startswith("_")]:
            g.pop(k)
        return g

    def pick(self, names, region=None, version=None, disc=None):
        """The best of several official names: wanted region, no beta/demo, matching revision/disc."""
        names = list(dict.fromkeys(names))
        order = [r for r in (region, "USA", "World", "Europe", "Japan") if r]

        def score(n):
            s = 0
            tags = n.lower()
            for i, r in enumerate(order):
                if "(%s" % r.lower() in tags or ", %s" % r.lower() in tags:
                    s += 100 - i * 10
                    break
            if AVOID.search(n):
                s -= 200
            for tag in re.findall(r"\(([^)]*)\)", n):
                if not PLAIN_TAG.match(tag.strip()):
                    s -= 4  # special editions, bundles...
            rev = re.search(r"\(rev (\d+)\)", tags)
            if version and re.match(r"1\.(\d+)$", str(version)):
                want = int(version.split(".")[1])
                s += 20 if (int(rev.group(1)) if rev else 0) == want else -5
            elif rev:
                s -= 3
            d = re.search(r"\(disc (\d+)\)", tags)
            if disc:
                s += 30 if d and int(d.group(1)) == disc else (-30 if d else 0)
            elif d and d.group(1) != "1":
                s -= 30
            return s
        return max(names, key=score) if names else None

    def by_name(self, title, region=None, disc=None):
        key = norm(title)
        if not key:
            return None, 0
        if key in self.by_title:
            return self.pick(self.by_title[key], region, disc=disc), 1.0
        close = difflib.get_close_matches(key, self._titles, n=3, cutoff=0.82)
        if not close:
            return None, 0
        best = close[0]
        return self.pick(self.by_title[best], region, disc=disc), difflib.SequenceMatcher(None, key, best).ratio()


# ------------------------------------------------------------------ fingerprints
def _n64_native(data):
    head = data[:4]
    if head == b"\x37\x80\x40\x12":
        b = bytearray(data)
        b[0::2], b[1::2] = data[1::2], data[0::2]
        return bytes(b)
    if head == b"\x40\x12\x37\x80":
        n = len(data) - len(data) % 4
        return b"".join(data[i:i + 4][::-1] for i in range(0, n, 4))
    return data


def file_crc(path, console=None, cancelled=None):
    """CRC32 of a game file as the official lists record it (N64 always in its native byte order)."""
    if console == "n64" and os.path.getsize(path) <= 128 * 1024 * 1024:
        return "%08x" % (zlib.crc32(_n64_native(Path(path).read_bytes())) & 0xFFFFFFFF)
    crc = 0
    with open(path, "rb") as f:
        while True:
            if cancelled and cancelled():
                return None
            chunk = f.read(4 * 1024 * 1024)
            if not chunk:
                break
            crc = zlib.crc32(chunk, crc)
    return "%08x" % (crc & 0xFFFFFFFF)


def _plain_image(path, fmt):
    """A file whose checksum can be compared with the official lists (not compressed or converted)."""
    return (fmt or "").lower() in ("iso", "gcm", "z64", "v64", "n64", "nds", "gba", "bin", "")


def match_game(console, path, ident, crc=None, cancelled=None):
    """Which official game a file is.

    Returns {"name", "method": "fingerprint" | "serial" | "name", "score", "crc"} or
    {"name": None, ...} when nothing matched.
    """
    db = ConsoleDb.get(console)
    title_hint, region_hint = clean_filename(path)
    region = region_hint or ident.get("region")
    disc = None
    m = re.search(r"\bdis[ck]\s*(\d)\b", os.path.basename(path), re.I)
    if m:
        disc = int(m.group(1))
    fmt = ident.get("format")
    size = os.path.getsize(path) if os.path.exists(path) else 0

    # 1. fingerprint (cartridges and plain discs; big discs only when the serial didn't settle it)
    def by_crc(c):
        names = db.by_crc.get((c or "").lower())
        return db.pick(names, region, ident.get("version"), disc) if names else None

    if crc:
        name = by_crc(crc)
        if name:
            return {"name": name, "method": "fingerprint", "score": 1.0, "crc": crc}
    if console in CARTRIDGES and _plain_image(path, fmt) and size <= 512 * 1024 * 1024:
        crc = file_crc(path, console, cancelled)
        name = by_crc(crc)
        if name:
            return {"name": name, "method": "fingerprint", "score": 1.0, "crc": crc}

    # 2. serial read from the file
    serial = ident.get("serial")
    if serial:
        key = _serial_key(serial)
        names = db.by_serial.get(key)
        if not names and console in ("gc", "wii") and len(key) >= 4:
            names = db.by_serial4.get(key[:4])
        if names:
            if len(set(names)) > 1 and not crc and _plain_image(path, fmt) and console not in CARTRIDGES:
                # several versions share this serial: the fingerprint tells them apart
                crc = file_crc(path, console, cancelled)
                exact = by_crc(crc)
                if exact:
                    return {"name": exact, "method": "fingerprint", "score": 1.0, "crc": crc}
            return {"name": db.pick(names, region, ident.get("version"), disc), "method": "serial", "score": 0.97,
                    "crc": crc}

    # 3. name (file name first, then the title stored inside the file)
    for candidate in (title_hint, ident.get("title")):
        if candidate:
            name, score = db.by_name(candidate, region, disc)
            if name:
                return {"name": name, "method": "name", "score": round(score, 2), "crc": crc}
    return {"name": None, "method": None, "score": 0, "crc": crc}


# ------------------------------------------------------------------ descriptions
class OpenVgdb:
    _db = None

    @classmethod
    def conn(cls):
        with _named_lock("openvgdb"):
            if cls._db is None:
                path = data_dir() / "openvgdb.sqlite"
                if not path.exists():
                    data = fetch(OPENVGDB_URL, timeout=180)
                    if not data:
                        raise Offline("OpenVGDB download failed")
                    with zipfile.ZipFile(io.BytesIO(data)) as z:
                        member = next(n for n in z.namelist() if n.endswith("openvgdb.sqlite") and "__MACOSX" not in n)
                        tmp = path.with_suffix(".part")
                        tmp.write_bytes(z.read(member))
                        os.replace(tmp, path)
                cls._db = sqlite3.connect(str(path), check_same_thread=False)
            return cls._db

    @classmethod
    def lookup(cls, console, crc=None, serial=None, title=None):
        system = SYSTEMS[console][1]
        if not system:
            return None
        db = cls.conn()
        sql = ("SELECT r.releaseTitleName, r.releaseDescription, r.releaseDeveloper, r.releasePublisher, "
               "r.releaseGenre, r.releaseDate FROM ROMs o JOIN RELEASES r ON r.romID=o.romID "
               "JOIN SYSTEMS s ON s.systemID=o.systemID WHERE s.systemName=? AND %s "
               "ORDER BY length(coalesce(r.releaseDescription,'')) DESC LIMIT 1")
        with _named_lock("openvgdb-q"):
            row = None
            if crc:
                row = db.execute(sql % "o.romHashCRC=?", (system, crc.upper())).fetchone()
            if not row and serial:
                row = db.execute(sql % "replace(upper(o.romSerial),'-','')=?", (system, _serial_key(serial))).fetchone()
            if not row and title:
                rows = db.execute(sql.replace("LIMIT 1", "LIMIT 40") % "r.releaseTitleName LIKE ?",
                                  (system, title.split(":")[0][:40] + "%")).fetchall()
                rows = [r for r in rows if norm(r[0]) == norm(title)]
                row = max(rows, key=lambda r: len(r[1] or "")) if rows else None
        if not row:
            return None
        return {"title": row[0], "description": (row[1] or "").strip(), "developer": row[2], "publisher": row[3],
                "genre": (row[4] or "").replace(",", ", "), "date": row[5]}


def wikipedia(title, console):
    """Short description of a game from English Wikipedia: (text, page url) or (None, None)."""
    wiki_console = SYSTEMS[console][2]
    q = urllib.parse.urlencode({"action": "query", "list": "search", "format": "json", "srlimit": 5,
                                "srsearch": "%s %s video game" % (title, wiki_console)})
    data = fetch(WIKI_API + "?" + q)
    if not data:
        return None, None
    results = json.loads(data).get("query", {}).get("search", [])
    want = norm(title)
    for r in results:
        page = r.get("title", "")
        page_key = norm(re.sub(r"\s*\((?:[^)]*video game[^)]*|\d{4})\)$", "", page))
        close = difflib.SequenceMatcher(None, want, page_key).ratio()
        if close < 0.6 and not (page_key and (page_key in want or want in page_key)):
            continue
        summary = fetch(WIKI_SUMMARY.format(urllib.parse.quote(page.replace(" ", "_"), safe="")))
        if not summary:
            continue
        s = json.loads(summary)
        desc = (s.get("description") or "").lower()
        if s.get("type") == "disambiguation" or "game" not in desc:
            continue
        text = (s.get("extract") or "").strip()
        if len(text) > 60:
            url = (s.get("content_urls") or {}).get("desktop", {}).get("page")
            return text, url
    return None, None


# ------------------------------------------------------------------ pictures
def _thumb_index(console, kind):
    """Names of every picture of one kind libretro has for a console (cached for a month)."""
    system = SYSTEMS[console][0]
    folder = PICTURES[kind]
    cache = data_dir() / "thumbs" / ("%s - %s.json" % (system, folder))
    cache.parent.mkdir(parents=True, exist_ok=True)
    with _named_lock("thumbs-" + str(cache)):
        if _fresh(cache, 30):
            return json.loads(cache.read_text(encoding="utf-8"))
        try:
            html = fetch(THUMBS_URL.format(urllib.parse.quote(system), folder), timeout=90)
        except Offline:
            if cache.exists():
                return json.loads(cache.read_text(encoding="utf-8"))
            raise
        names = []
        if html:
            for href in re.findall(r'href="([^"?/][^"]*\.png)"', html.decode("utf-8", "replace")):
                names.append(unescape(urllib.parse.unquote(href))[:-4])
        cache.write_text(json.dumps(names), encoding="utf-8")
        return names


def _thumb_title_map(console, kind):
    key = (console, kind)
    cached = _thumb_title_map.memo.get(key)
    if cached is None:
        cached = {}
        for n in _thumb_index(console, kind):
            cached.setdefault(norm(TAGS.sub("", n)), []).append(n)
        _thumb_title_map.memo[key] = cached
    return cached


_thumb_title_map.memo = {}


def find_picture(console, name, kind, region=None):
    """libretro's picture file name for a game: the exact version, else the same game from another region."""
    try:
        names = set(_thumb_index(console, kind))
    except Offline:
        return None
    exact = safe_thumb_name(name)
    if exact in names:
        return exact
    same = _thumb_title_map(console, kind).get(norm(TAGS.sub("", name)))
    if not same:
        return None
    db = ConsoleDb.get(console)
    m = re.search(r"\((USA|Europe|Japan|World)", name)
    return db.pick(same, region or (m.group(1) if m else None))


def download_picture(console, file_name, kind, target: Path):
    url = THUMBS_URL.format(urllib.parse.quote(SYSTEMS[console][0]), PICTURES[kind]) + urllib.parse.quote(file_name) + ".png"
    data = fetch(url, timeout=60)
    if not data:
        return False
    target.write_bytes(data)
    return True


# ------------------------------------------------------------------ icon
def make_icon(source: Path, target: Path, background=(255, 255, 255), kind="logo"):
    """A 512 x 512 app icon: the logo (or box art) centred on a plain square, sized so round or rounded
    icon shapes never cut it (Android shows roughly a circle of radius 230 px of this square)."""
    from PIL import Image
    img = Image.open(source).convert("RGBA")
    box = img.getchannel("A").getbbox()
    if box:
        img = img.crop(box)
    w, h = img.size
    if not w or not h:
        return False
    limit_r = 214 if kind == "logo" else 222
    scale = min(0.80 * 512 / w, (0.62 if kind == "logo" else 0.84) * 512 / h, 2 * limit_r / (w * w + h * h) ** 0.5)
    img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    canvas = Image.new("RGBA", (512, 512), background + (255,))
    canvas.alpha_composite(img, ((512 - img.width) // 2, (512 - img.height) // 2))
    canvas.convert("RGB").save(target, optimize=True)
    return True


# ------------------------------------------------------------------ everything for one game
def cache_dir_for(console, name):
    safe = re.sub(r"[^A-Za-z0-9 ._()-]+", "_", name)[:120].strip()
    d = paths.sub("gameinfo") / console / safe
    d.mkdir(parents=True, exist_ok=True)
    return d


def gather(console, name, crc=None, serial=None, region=None, log=lambda s: None):
    """Details, description and pictures for an official game name.

    Saves info.json, box.png, snap.png, title.png, logo.png and icon.png in the game's cache folder
    and returns (folder, info).
    """
    folder = cache_dir_for(console, name)
    info_file = folder / "info.json"
    if info_file.exists():
        try:
            info = json.loads(info_file.read_text(encoding="utf-8"))
            if info.get("v") == 1 and info.get("complete", True):
                return folder, info
        except ValueError:
            pass
    db = ConsoleDb.get(console)
    e = db.entry(name)
    title = base_title(name)
    info = {"v": 1, "name": name, "title": title, "console": console,
            "year": e.get("releaseyear"), "month": e.get("releasemonth"),
            "developer": e.get("developer"), "publisher": e.get("publisher"), "genre": e.get("genre"),
            "players": e.get("users"), "region": e.get("region"),
            "description": None, "description_source": None, "source_url": None, "pictures": [], "icon": None}
    desc = e.get("description")
    if desc and norm(desc) != norm(name) and len(desc) > 60:
        info["description"], info["description_source"] = desc.strip(), "libretro"
    complete = True
    try:
        ov = OpenVgdb.lookup(console, crc or e.get("crc"), serial or e.get("serial"), title)
        if ov:
            for k in ("developer", "publisher", "genre"):
                info[k] = info[k] or ov.get(k)
            if not info["year"] and ov.get("date"):
                m = re.search(r"(\d{4})", ov["date"])
                info["year"] = int(m.group(1)) if m else None
            if not info["description"] and len(ov.get("description") or "") > 60:
                info["description"], info["description_source"] = ov["description"], "OpenVGDB"
    except Offline:
        complete = False
        log("OpenVGDB couldn't be downloaded right now.")
    if not info["description"]:
        try:
            text, url = wikipedia(title, console)
            if text:
                info["description"], info["description_source"], info["source_url"] = text, "Wikipedia", url
        except Offline:
            complete = False
            log("Wikipedia couldn't be reached right now.")
    for kind in ("box", "snap", "title", "logo"):
        target = folder / (kind + ".png")
        if target.exists():
            info["pictures"].append(kind)
            continue
        try:
            fname = find_picture(console, name, kind, region)
            if fname and download_picture(console, fname, kind, target):
                info["pictures"].append(kind)
        except Offline:
            complete = False
    for kind in ("logo", "box"):
        if kind in info["pictures"]:
            try:
                if make_icon(folder / (kind + ".png"), folder / "icon.png", kind=kind):
                    info["icon"] = kind
                    break
            except Exception as ex:  # a damaged picture shouldn't stop the lookup
                log("Couldn't make the icon from the %s: %s" % (kind, ex))
    info["complete"] = complete  # looked up again next time when a site couldn't be reached
    info_file.write_text(json.dumps(info, indent=1), encoding="utf-8")
    return folder, info


def search_names(console, text, limit=12):
    """Official names that look like what was typed, for fixing a wrong match by hand."""
    db = ConsoleDb.get(console)
    key = norm(text)
    if not key:
        return []
    hits = [t for t in db._titles if key in t][:limit]
    hits += [t for t in difflib.get_close_matches(key, db._titles, n=limit, cutoff=0.6) if t not in hits]
    out = []
    for t in hits[:limit]:
        for name in sorted(db.by_title[t], key=lambda n: (bool(AVOID.search(n)), n))[:4]:
            out.append(name)
    return out[:limit * 2]


def app_info(info):
    """The part of the information that goes inside the game app (assets/tirso/info/info.json)."""
    keep = ("title", "console", "year", "month", "developer", "publisher", "genre", "players", "region",
            "description", "description_source", "source_url")
    out = {k: info.get(k) for k in keep if info.get(k) not in (None, "", [])}
    out["pictures"] = [k for k in ("box", "title", "snap") if k in info.get("pictures", [])]
    credits = []
    if info.get("description_source"):
        credits.append("Description: " + info["description_source"])
    if out["pictures"]:
        credits.append("Pictures: libretro thumbnails")
    out["credits"] = " · ".join(credits)
    return out
