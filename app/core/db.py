"""Local SQLite database: ROM library, BIOS library, game projects, builds, settings."""
import json
import sqlite3
import threading
import time

from . import paths

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS roms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    size INTEGER NOT NULL,
    format TEXT,
    console TEXT,              -- confirmed console (detected or chosen by user)
    detected_console TEXT,     -- what the file header said (NULL if unknown)
    console_source TEXT,       -- 'header' | 'manual' | NULL
    title TEXT,                -- title read from the file
    display_name TEXT,         -- name the user sees / edits
    serial TEXT,
    region TEXT,
    version TEXT,
    crc32 TEXT,
    sha1 TEXT,
    status TEXT NOT NULL,      -- 'hashing' | 'ready' | 'missing' | 'error'
    message TEXT,
    added_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS bios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    console TEXT NOT NULL,
    filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    version TEXT,
    region TEXT,
    valid INTEGER NOT NULL,
    message TEXT,
    preferred INTEGER NOT NULL DEFAULT 0,
    added_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rom_id INTEGER NOT NULL REFERENCES roms(id) ON DELETE CASCADE,
    app_name TEXT NOT NULL,
    package_id TEXT NOT NULL UNIQUE,
    orientation TEXT NOT NULL,
    profile TEXT NOT NULL,
    bios_id INTEGER REFERENCES bios(id) ON DELETE SET NULL,
    icon_path TEXT,
    settings_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS builds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER REFERENCES games(id) ON DELETE SET NULL,
    status TEXT NOT NULL,
    stage TEXT,
    started_at REAL NOT NULL,
    finished_at REAL,
    apk_path TEXT,
    apk_size INTEGER,
    log TEXT
);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- Batch build: folders scanned in the Batch build tab and the games found in them.
-- These never show in My games or the ROM Library.
CREATE TABLE IF NOT EXISTS batch_folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT 'phone',  -- 'phone' | 'tv'
    status TEXT NOT NULL,                  -- 'scanning' | 'ready' | 'building' | 'stopped'
    message TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS batch_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_id INTEGER NOT NULL REFERENCES batch_folders(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    filename TEXT NOT NULL,
    size INTEGER NOT NULL,
    console TEXT,
    format TEXT,
    serial TEXT,
    region TEXT,
    crc32 TEXT,
    state TEXT NOT NULL,          -- 'waiting' | 'looking' | 'found' | 'name' | 'notfound' | 'skipped' | 'error'
    method TEXT,                  -- 'fingerprint' | 'serial' | 'name' | 'manual'
    match_name TEXT,              -- official game name
    title TEXT,                   -- app name
    info_dir TEXT,
    has_logo INTEGER NOT NULL DEFAULT 0,
    include INTEGER NOT NULL DEFAULT 1,
    game_id INTEGER REFERENCES games(id) ON DELETE SET NULL,
    build_id INTEGER,
    message TEXT,
    UNIQUE(folder_id, path)
);
"""


class Database:
    def __init__(self, path=None):
        self.path = str(path or paths.db_path())
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),)
            )
            cols = {r[1] for r in self.conn.execute("PRAGMA table_info(builds)")}
            if "message" not in cols:
                self.conn.execute("ALTER TABLE builds ADD COLUMN message TEXT")
            if "data_path" not in cols:
                self.conn.execute("ALTER TABLE builds ADD COLUMN data_path TEXT")
            for table, col, decl in (("roms", "batch", "INTEGER NOT NULL DEFAULT 0"),
                                     ("games", "batch", "INTEGER NOT NULL DEFAULT 0"),
                                     ("games", "info_dir", "TEXT"),
                                     ("batch_folders", "settings_json", "TEXT")):
                if col not in {r[1] for r in self.conn.execute("PRAGMA table_info(%s)" % table)}:
                    self.conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, decl))
            self.conn.execute("UPDATE batch_items SET state='waiting' WHERE state='looking'")
            self.conn.execute("UPDATE batch_folders SET status='stopped', message='Tirso was closed while this folder was being read.' "
                              "WHERE status='scanning'")
            self.conn.execute("UPDATE builds SET status='failed', message='Tirso was closed during the build.' "
                              "WHERE status='running'")
            self.conn.execute("UPDATE builds SET status='cancelled', message='Tirso was closed before this build started.' "
                              "WHERE status='queued'")
            self.conn.commit()

    # -- helpers -----------------------------------------------------------
    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def run(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur.lastrowid

    # -- settings ----------------------------------------------------------
    def get_setting(self, key, default=None):
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def set_setting(self, key, value):
        self.run(
            "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    @staticmethod
    def now():
        return time.time()
