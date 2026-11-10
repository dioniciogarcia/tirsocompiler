"""Read sectors and files from disc images without extracting them.

Supports plain ISO (2048-byte sectors), raw CD images (2352-byte sectors,
used by PS1 .bin files) and PSP .cso compressed images. Only small files
(PARAM.SFO, SYSTEM.CNF) are ever read, so identification is fast even for
multi-gigabyte games.
"""
import struct
import zlib

SECTOR = 2048
CD_SYNC = b"\x00" + b"\xff" * 10 + b"\x00"


class DiscReader:
    def __init__(self, f):
        self.f = f

    def read_sector(self, lba: int) -> bytes:  # pragma: no cover - overridden
        raise NotImplementedError

    def read(self, lba: int, length: int) -> bytes:
        out = bytearray()
        while len(out) < length:
            out += self.read_sector(lba)
            lba += 1
        return bytes(out[:length])


class IsoReader(DiscReader):
    """Plain ISO or raw 2352-byte CD image."""

    def __init__(self, f):
        super().__init__(f)
        f.seek(0)
        head = f.read(16)
        if head[:12] == CD_SYNC:
            self.raw_size = 2352
            mode = head[15]
            self.data_offset = 16 if mode == 1 else 24
        else:
            self.raw_size = SECTOR
            self.data_offset = 0

    def read_sector(self, lba):
        self.f.seek(lba * self.raw_size + self.data_offset)
        data = self.f.read(SECTOR)
        return data.ljust(SECTOR, b"\x00")


class CsoReader(DiscReader):
    """Compressed ISO (CISO v1) used for PSP games."""

    def __init__(self, f):
        super().__init__(f)
        f.seek(0)
        head = f.read(24)
        magic, _hsize, total, block, _ver, align = struct.unpack("<4sIQIBB", head[:22])
        if magic != b"CISO":
            raise ValueError("not a CSO file")
        self.block = block or SECTOR
        self.align = align
        self.total = total
        self.blocks = (total + self.block - 1) // self.block
        f.seek(24)
        raw = f.read(4 * (self.blocks + 1))
        self.index = struct.unpack("<%dI" % (self.blocks + 1), raw)

    def read_block(self, n):
        if n >= self.blocks:
            return b"\x00" * self.block
        a, b = self.index[n], self.index[n + 1]
        plain = a & 0x80000000
        start = (a & 0x7FFFFFFF) << self.align
        end = (b & 0x7FFFFFFF) << self.align
        self.f.seek(start)
        data = self.f.read(end - start)
        if plain:
            return data[: self.block]
        return zlib.decompressobj(-15).decompress(data)[: self.block]

    def read_sector(self, lba):
        pos = lba * SECTOR
        blk, off = divmod(pos, self.block)
        data = self.read_block(blk)[off: off + SECTOR]
        return data.ljust(SECTOR, b"\x00")


# ---------------------------------------------------------------- BIN/CUE
# Sector layouts of CD data tracks: bytes per sector, where the 2048 data bytes start.
CUE_MODES = {"MODE1/2048": (2048, 0), "MODE1/2352": (2352, 16), "MODE2/2352": (2352, 24),
             "MODE2/2336": (2336, 8), "MODE2/2048": (2048, 0)}


class CueError(Exception):
    pass


def _find_file(folder, name):
    """The file a CUE names, matched without caring about upper/lower case."""
    import os
    exact = os.path.join(folder, name)
    if os.path.exists(exact):
        return exact
    base = os.path.basename(name.replace("\\", "/")).lower()
    try:
        for n in os.listdir(folder):
            if n.lower() == base:
                return os.path.join(folder, n)
    except OSError:
        pass
    return None


def parse_cue(path):
    """Reads a .cue sheet.

    Returns {"files": [bin paths, in order], "missing": [names], "audio_tracks": n,
             "data": {"path", "start", "sector", "offset", "sectors"} or None}.
    "data" is the first data track: where it is and how its sectors are laid out.
    """
    import os
    import re
    folder = os.path.dirname(os.path.abspath(path))
    try:
        with open(path, "rb") as f:
            raw = f.read(256 * 1024)
    except OSError as e:
        raise CueError("Could not read the CUE file: %s" % e.strerror)
    text = raw.decode("utf-8", "replace") if raw[:3] != b"\xef\xbb\xbf" else raw[3:].decode("utf-8", "replace")
    files, missing, tracks = [], [], []
    current = None
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r'FILE\s+"([^"]+)"', line, re.I) or re.match(r"FILE\s+(\S+)", line, re.I)
        if m:
            current = _find_file(folder, m.group(1))
            if current:
                if current not in files:
                    files.append(current)
            else:
                missing.append(m.group(1))
            continue
        m = re.match(r"TRACK\s+(\d+)\s+(\S+)", line, re.I)
        if m:
            tracks.append({"num": int(m.group(1)), "mode": m.group(2).upper(), "file": current, "index": None})
            continue
        m = re.match(r"INDEX\s+(\d+)\s+(\d+):(\d+):(\d+)", line, re.I)
        if m and tracks:
            t = tracks[-1]
            frames = (int(m.group(2)) * 60 + int(m.group(3))) * 75 + int(m.group(4))
            if int(m.group(1)) == 1 or t["index"] is None:
                t["index"] = frames
    if not tracks and not files:
        raise CueError("This CUE file doesn't list any tracks.")
    data = None
    for i, t in enumerate(tracks):
        if t["mode"] == "AUDIO" or not t["file"]:
            continue
        if t["mode"] not in CUE_MODES:
            raise CueError("Track %d uses a disc layout Tirso can't read (%s)." % (t["num"], t["mode"]))
        sector, offset = CUE_MODES[t["mode"]]
        start = (t["index"] or 0) * sector
        end = os.path.getsize(t["file"])
        nxt = next((n for n in tracks[i + 1:] if n["file"] == t["file"] and n["index"] is not None), None)
        if nxt:
            end = min(end, nxt["index"] * sector)  # same BIN holds the next track too
        data = {"path": t["file"], "start": start, "sector": sector, "offset": offset,
                "sectors": max(0, (end - start) // sector)}
        break
    return {"files": files, "missing": missing, "data": data,
            "audio_tracks": sum(1 for t in tracks if t["mode"] == "AUDIO")}


def raw_track(path):
    """The data layout of a lone .bin: raw 2352-byte CD sectors, or plain 2048-byte ISO sectors."""
    import os
    with open(path, "rb") as f:
        head = f.read(16)
    size = os.path.getsize(path)
    if head[:12] == CD_SYNC:
        offset = 16 if head[15] == 1 else 24
        return {"path": str(path), "start": 0, "sector": 2352, "offset": offset, "sectors": size // 2352}
    return {"path": str(path), "start": 0, "sector": SECTOR, "offset": 0, "sectors": (size + SECTOR - 1) // SECTOR}


class TrackReader(DiscReader):
    """Reads 2048-byte data sectors from a CD data track (inside a BIN file)."""

    def __init__(self, f, track):
        super().__init__(f)
        self.t = track

    def read_sector(self, lba):
        t = self.t
        self.f.seek(t["start"] + lba * t["sector"] + t["offset"])
        return self.f.read(SECTOR).ljust(SECTOR, b"\x00")


def compress_cso(src, dst, level=6, progress=None, cancelled=None, track=None):
    """Writes a CISO v1 file (what PPSSPP and ARMSX2 read). Returns its size.

    src is a plain ISO, or with track= (from parse_cue/raw_track) the data track of a
    CD image: only the 2048 data bytes of each sector are kept, which is what an ISO holds.
    """
    block = SECTOR
    import os
    if track:
        src = track["path"]
        total = track["sectors"] * SECTOR
    else:
        total = os.path.getsize(src)
    blocks = (total + block - 1) // block
    tmp = str(dst) + ".part"
    with open(src, "rb") as fin, open(tmp, "wb") as fout:
        if track:
            reader = TrackReader(fin, track)

            class _Seq:
                n = 0

                def read(self, _size):
                    d = reader.read_sector(self.n)
                    self.n += 1
                    return d
            fin = _Seq()
        fout.write(struct.pack("<4sIQIBB2x", b"CISO", 24, total, block, 1, 0))
        fout.write(b"\x00" * 4 * (blocks + 1))
        index = []
        pos = 24 + 4 * (blocks + 1)
        last = -1
        for i in range(blocks):
            data = fin.read(block)
            c = zlib.compressobj(level, zlib.DEFLATED, -15)
            comp = c.compress(data) + c.flush()
            if len(comp) >= len(data):
                index.append(pos | 0x80000000)
                fout.write(data)
                pos += len(data)
            else:
                index.append(pos)
                fout.write(comp)
                pos += len(comp)
            if pos >= 0x80000000:
                raise ValueError("game too large for CSO")
            if i % 2048 == 0:
                if cancelled and cancelled():
                    fout.close()
                    os.remove(tmp)
                    return None
                pct = int(i * 100 / blocks)
                if progress and pct != last:
                    last = pct
                    progress(pct)
        index.append(pos)
        fout.seek(24)
        fout.write(struct.pack("<%dI" % len(index), *index))
    os.replace(tmp, dst)
    return pos


class Iso9660:
    """Minimal ISO-9660 filesystem walker (enough to find files by path)."""

    def __init__(self, reader: DiscReader):
        self.r = reader
        pvd = reader.read_sector(16)
        if pvd[1:6] != b"CD001":
            raise ValueError("no ISO-9660 filesystem")
        self.volume_id = pvd[40:72].decode("ascii", "replace").strip()
        root = pvd[156:190]
        self.root = (struct.unpack_from("<I", root, 2)[0], struct.unpack_from("<I", root, 10)[0])

    def _list(self, lba, size):
        data = self.r.read(lba, size)
        pos = 0
        while pos < len(data):
            ln = data[pos]
            if ln == 0:  # padding to next sector
                pos = (pos // SECTOR + 1) * SECTOR
                continue
            rec = data[pos: pos + ln]
            ext = struct.unpack_from("<I", rec, 2)[0]
            sz = struct.unpack_from("<I", rec, 10)[0]
            flags = rec[25]
            nlen = rec[32]
            name = rec[33: 33 + nlen].decode("ascii", "replace")
            if name not in ("\x00", "\x01"):
                yield name.split(";")[0].rstrip(".").upper(), ext, sz, bool(flags & 2)
            pos += ln

    def find(self, path: str):
        lba, size = self.root
        parts = [p for p in path.replace("\\", "/").upper().split("/") if p]
        for i, part in enumerate(parts):
            for name, ext, sz, is_dir in self._list(lba, size):
                if name == part:
                    lba, size = ext, sz
                    if i < len(parts) - 1 and not is_dir:
                        return None
                    break
            else:
                return None
        return lba, size

    def read_file(self, path: str, limit: int = 1 << 20):
        hit = self.find(path)
        if not hit:
            return None
        lba, size = hit
        return self.r.read(lba, min(size, limit))


def parse_sfo(data: bytes) -> dict:
    """Parse a PSP/PS3 PARAM.SFO into a dict."""
    if not data or data[:4] != b"\x00PSF":
        return {}
    _magic, _ver, key_start, data_start, count = struct.unpack_from("<4sIIII", data, 0)
    out = {}
    for i in range(count):
        koff, fmt, ln, _mx, doff = struct.unpack_from("<HHIII", data, 20 + i * 16)
        kend = data.index(b"\x00", key_start + koff)
        key = data[key_start + koff: kend].decode("ascii", "replace")
        raw = data[data_start + doff: data_start + doff + ln]
        if fmt == 0x0404:
            out[key] = struct.unpack("<I", raw[:4])[0]
        else:
            out[key] = raw.split(b"\x00")[0].decode("utf-8", "replace")
    return out


# ---------------------------------------------------------------- GCZ (Dolphin's compressed GameCube/Wii image)
GCZ_MAGIC = 0xB10BC001
GCZ_BLOCK = 0x8000
_GCZ_RAW = 1 << 63


def compress_gcz(src, dst, wii=False, progress=None, cancelled=None, block=GCZ_BLOCK, level=6):
    """Writes a GCZ file (Dolphin reads it directly) from a plain GameCube/Wii ISO. Returns its size.

    Layout (Dolphin's CompressedBlob): 32-byte header, u64 block offsets (top bit = stored
    uncompressed), u32 Adler-32 of each stored block, then the blocks (zlib streams).
    """
    import os
    total = os.path.getsize(src)
    blocks = (total + block - 1) // block
    header_size = 32 + 12 * blocks
    tmp = str(dst) + ".part"
    offsets, hashes = [], []
    pos, last = 0, -1
    with open(src, "rb") as fin, open(tmp, "wb") as fout:
        fout.write(b"\x00" * header_size)
        for i in range(blocks):
            data = fin.read(block)
            if len(data) < block:
                data = data.ljust(block, b"\x00")
            comp = zlib.compress(data, level)
            if len(comp) > block - 10:          # Dolphin's rule: keep it plain when it doesn't shrink
                stored, flag = data, _GCZ_RAW
            else:
                stored, flag = comp, 0
            offsets.append(pos | flag)
            hashes.append(zlib.adler32(stored) & 0xFFFFFFFF)
            fout.write(stored)
            pos += len(stored)
            if i % 1024 == 0:
                if cancelled and cancelled():
                    fout.close()
                    os.remove(tmp)
                    return None
                pct = int(i * 100 / blocks)
                if progress and pct != last:
                    last = pct
                    progress(pct)
        fout.seek(0)
        fout.write(struct.pack("<IIQQII", GCZ_MAGIC, 1 if wii else 0, pos, total, block, blocks))
        fout.write(struct.pack("<%dQ" % blocks, *offsets))
        fout.write(struct.pack("<%dI" % blocks, *hashes))
    os.replace(tmp, dst)
    return header_size + pos


class GczReader:
    """Reads bytes from a GCZ file (used to identify games and in the self-tests)."""

    def __init__(self, f):
        self.f = f
        f.seek(0)
        magic, self.sub_type, self.comp_size, self.size, self.block, self.blocks = struct.unpack("<IIQQII", f.read(32))
        if magic != GCZ_MAGIC:
            raise ValueError("not a GCZ file")
        self.offsets = struct.unpack("<%dQ" % self.blocks, f.read(8 * self.blocks))
        self.hashes = struct.unpack("<%dI" % self.blocks, f.read(4 * self.blocks))
        self.data_start = 32 + 12 * self.blocks

    def read_block(self, n):
        start = self.offsets[n] & ~_GCZ_RAW
        end = (self.offsets[n + 1] & ~_GCZ_RAW) if n + 1 < self.blocks else self.comp_size
        self.f.seek(self.data_start + start)
        raw = self.f.read(end - start)
        if zlib.adler32(raw) & 0xFFFFFFFF != self.hashes[n]:
            raise ValueError("GCZ block %d is damaged" % n)
        return raw if self.offsets[n] & _GCZ_RAW else zlib.decompress(raw)

    def read(self, offset, length):
        out = bytearray()
        while length > 0 and offset < self.size:
            n, inner = divmod(offset, self.block)
            chunk = self.read_block(n)[inner: inner + length]
            out += chunk
            offset += len(chunk)
            length -= len(chunk)
        return bytes(out)
