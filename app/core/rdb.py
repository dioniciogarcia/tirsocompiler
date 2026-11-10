"""Reads RetroArch game databases (.rdb files from libretro-database).

An .rdb file is "RARCHDB\\0", an 8-byte offset, then one MessagePack map per game
(name, crc, md5, sha1, serial, developer, publisher, genre, releaseyear, users, ...),
ending with a nil. Only the small part of MessagePack these files use is decoded here,
so no extra library is needed.
"""
import struct


class RdbError(ValueError):
    pass


class _Reader:
    def __init__(self, data: bytes):
        self.d = data
        self.i = 0

    def take(self, n):
        if self.i + n > len(self.d):
            raise RdbError("truncated database")
        b = self.d[self.i:self.i + n]
        self.i += n
        return b

    def obj(self):
        t = self.take(1)[0]
        if t <= 0x7F:
            return t
        if 0x80 <= t <= 0x8F:
            return self._map(t & 0x0F)
        if 0x90 <= t <= 0x9F:
            return [self.obj() for _ in range(t & 0x0F)]
        if 0xA0 <= t <= 0xBF:
            return self._str(t & 0x1F)
        if t >= 0xE0:
            return t - 0x100
        if t == 0xC0:
            return None
        if t == 0xC2:
            return False
        if t == 0xC3:
            return True
        if t in (0xC4, 0xC5, 0xC6):  # bin 8/16/32 (checksums are stored as raw bytes)
            n = {0xC4: 1, 0xC5: 2, 0xC6: 4}[t]
            return bytes(self.take(int.from_bytes(self.take(n), "big")))
        if t in (0xD9, 0xDA, 0xDB):
            n = {0xD9: 1, 0xDA: 2, 0xDB: 4}[t]
            return self._str(int.from_bytes(self.take(n), "big"))
        if t in (0xCC, 0xCD, 0xCE, 0xCF):
            n = {0xCC: 1, 0xCD: 2, 0xCE: 4, 0xCF: 8}[t]
            return int.from_bytes(self.take(n), "big")
        if t in (0xD0, 0xD1, 0xD2, 0xD3):
            n = {0xD0: 1, 0xD1: 2, 0xD2: 4, 0xD3: 8}[t]
            return int.from_bytes(self.take(n), "big", signed=True)
        if t == 0xDE:
            return self._map(struct.unpack(">H", self.take(2))[0])
        if t == 0xDF:
            return self._map(struct.unpack(">I", self.take(4))[0])
        if t == 0xDC:
            return [self.obj() for _ in range(struct.unpack(">H", self.take(2))[0])]
        if t == 0xDD:
            return [self.obj() for _ in range(struct.unpack(">I", self.take(4))[0])]
        raise RdbError("unsupported value type 0x%02x" % t)

    def _str(self, n):
        return self.take(n).decode("utf-8", "replace")

    def _map(self, n):
        out = {}
        for _ in range(n):
            k = self.obj()
            out[k] = self.obj()
        return out


def read(path):
    """All games in an .rdb file, as dicts with readable values (checksums as lowercase hex)."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:7] != b"RARCHDB":
        raise RdbError("not a RetroArch database")
    r = _Reader(data)
    r.i = 16
    games = []
    while r.i < len(data):
        item = r.obj()
        if not isinstance(item, dict):
            break
        g = {}
        for k, v in item.items():
            if isinstance(v, bytes):
                # checksums stay hex; the serial is text stored as bytes
                v = v.decode("ascii", "replace") if k == "serial" else v.hex()
            g[k] = v
        games.append(g)
    return games
