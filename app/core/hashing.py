"""Checksums computed in one pass, with progress callbacks for big files."""
import hashlib
import zlib

CHUNK = 4 * 1024 * 1024


def file_hashes(path, algos=("crc32", "sha1"), progress=None, cancelled=None):
    hashers = {a: hashlib.new(a) for a in algos if a != "crc32"}
    crc = 0
    done = 0
    with open(path, "rb") as f:
        f.seek(0, 2)
        total = f.tell() or 1
        f.seek(0)
        last = -1
        while True:
            if cancelled and cancelled():
                return None
            chunk = f.read(CHUNK)
            if not chunk:
                break
            if "crc32" in algos:
                crc = zlib.crc32(chunk, crc)
            for h in hashers.values():
                h.update(chunk)
            done += len(chunk)
            pct = int(done * 100 / total)
            if progress and pct != last:
                last = pct
                progress(pct)
    out = {a: h.hexdigest() for a, h in hashers.items()}
    if "crc32" in algos:
        out["crc32"] = "%08x" % (crc & 0xFFFFFFFF)
    return out
