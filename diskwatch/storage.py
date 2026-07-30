"""SQLite 持久化：记录新增文件、按日汇总、清理过期数据。"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    ext         TEXT,
    drive       TEXT,
    folder      TEXT,
    size        INTEGER DEFAULT 0,
    added_at    REAL NOT NULL,
    day         TEXT NOT NULL,
    size_final  INTEGER DEFAULT 0,
    deleted     INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_files_day ON files(day);
CREATE INDEX IF NOT EXISTS idx_files_added ON files(added_at);
CREATE INDEX IF NOT EXISTS idx_files_pending ON files(size_final, added_at);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


@dataclass(frozen=True)
class FileRecord:
    path: str
    name: str
    ext: str
    drive: str
    folder: str
    size: int
    added_at: float
    deleted: bool = False

    @property
    def added_dt(self) -> datetime:
        return datetime.fromtimestamp(self.added_at)


@dataclass(frozen=True)
class DaySummary:
    day: str
    count: int
    total_size: int


def today_str() -> str:
    return date.today().isoformat()


def make_record(path: str, size: int, added_at: float | None = None) -> FileRecord:
    p = Path(path)
    drive = (os.path.splitdrive(path)[0] or "").upper()
    return FileRecord(
        path=str(p),
        name=p.name,
        ext=p.suffix.lower(),
        drive=drive,
        folder=str(p.parent),
        size=size,
        added_at=added_at if added_at is not None else time.time(),
    )


class Storage:
    """线程安全的轻量封装。写入集中在后台线程，读取来自 UI 线程。"""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------- 写 ----------

    def add_files(self, records: list[FileRecord]) -> int:
        if not records:
            return 0
        rows = [
            (
                r.path,
                r.name,
                r.ext,
                r.drive,
                r.folder,
                r.size,
                r.added_at,
                datetime.fromtimestamp(r.added_at).date().isoformat(),
                1 if r.size > 0 else 0,
            )
            for r in records
        ]
        with self._lock:
            cur = self._conn.executemany(
                """
                INSERT INTO files (path, name, ext, drive, folder, size, added_at, day, size_final, deleted)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(path) DO UPDATE SET
                    size       = excluded.size,
                    added_at   = excluded.added_at,
                    day        = excluded.day,
                    size_final = excluded.size_final,
                    deleted    = 0
                """,
                rows,
            )
            self._conn.commit()
            return cur.rowcount

    def mark_deleted(self, paths: list[str]) -> None:
        if not paths:
            return
        with self._lock:
            self._conn.executemany(
                "UPDATE files SET deleted = 1 WHERE path = ?", [(p,) for p in paths]
            )
            self._conn.commit()

    def delete_paths(self, paths: list[str]) -> None:
        if not paths:
            return
        with self._lock:
            self._conn.executemany(
                "DELETE FROM files WHERE path = ?", [(p,) for p in paths]
            )
            self._conn.commit()

    def rename(self, src: str, dst: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM files WHERE path = ?", (dst,))
            self._conn.execute(
                "UPDATE files SET path = ?, name = ?, folder = ?, ext = ? WHERE path = ?",
                (dst, Path(dst).name, str(Path(dst).parent), Path(dst).suffix.lower(), src),
            )
            self._conn.commit()

    def pending_size_rows(self, older_than: float, limit: int = 500) -> list[str]:
        """刚创建的文件往往还是 0 字节，稍后再回来补真实体积。"""
        with self._lock:
            cur = self._conn.execute(
                "SELECT path FROM files WHERE size_final = 0 AND deleted = 0 "
                "AND added_at < ? ORDER BY added_at LIMIT ?",
                (older_than, limit),
            )
            return [r["path"] for r in cur.fetchall()]

    def update_sizes(self, sizes: dict[str, int], missing: list[str]) -> None:
        with self._lock:
            if sizes:
                self._conn.executemany(
                    "UPDATE files SET size = ?, size_final = 1 WHERE path = ?",
                    [(v, k) for k, v in sizes.items()],
                )
            if missing:
                self._conn.executemany(
                    "UPDATE files SET deleted = 1, size_final = 1 WHERE path = ?",
                    [(p,) for p in missing],
                )
            self._conn.commit()

    def purge_older_than(self, days: int) -> int:
        if days <= 0:
            return 0
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        with self._lock:
            cur = self._conn.execute("DELETE FROM files WHERE day < ?", (cutoff,))
            self._conn.commit()
            return cur.rowcount

    def clear_all(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM files")
            self._conn.commit()
            self._conn.execute("VACUUM")

    # ---------- 读 ----------

    def day_stats(self, day: str) -> tuple[int, int]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) c, COALESCE(SUM(size), 0) s FROM files "
                "WHERE day = ? AND deleted = 0",
                (day,),
            )
            row = cur.fetchone()
            return int(row["c"]), int(row["s"])

    def recent_files(self, day: str | None = None, limit: int = 8) -> list[FileRecord]:
        sql = "SELECT * FROM files WHERE deleted = 0"
        args: list = []
        if day:
            sql += " AND day = ?"
            args.append(day)
        sql += " ORDER BY added_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            cur = self._conn.execute(sql, args)
            return [_row_to_record(r) for r in cur.fetchall()]

    def files_for_day(
        self, day: str, keyword: str = "", include_deleted: bool = False
    ) -> list[FileRecord]:
        sql = "SELECT * FROM files WHERE day = ?"
        args: list = [day]
        if not include_deleted:
            sql += " AND deleted = 0"
        if keyword:
            sql += " AND (LOWER(name) LIKE ? OR LOWER(folder) LIKE ?)"
            like = f"%{keyword.lower()}%"
            args += [like, like]
        sql += " ORDER BY added_at DESC"
        with self._lock:
            cur = self._conn.execute(sql, args)
            return [_row_to_record(r) for r in cur.fetchall()]

    def days_with_data(self, limit: int = 60) -> list[DaySummary]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT day, COUNT(*) c, COALESCE(SUM(size), 0) s FROM files "
                "WHERE deleted = 0 GROUP BY day ORDER BY day DESC LIMIT ?",
                (limit,),
            )
            return [
                DaySummary(r["day"], int(r["c"]), int(r["s"])) for r in cur.fetchall()
            ]

    def top_folders(self, day: str, limit: int = 5) -> list[tuple[str, int, int]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT folder, COUNT(*) c, COALESCE(SUM(size), 0) s FROM files "
                "WHERE day = ? AND deleted = 0 GROUP BY folder "
                "ORDER BY c DESC, s DESC LIMIT ?",
                (day, limit),
            )
            return [(r["folder"], int(r["c"]), int(r["s"])) for r in cur.fetchall()]

    def top_extensions(self, day: str, limit: int = 8) -> list[tuple[str, int, int]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT ext, COUNT(*) c, COALESCE(SUM(size), 0) s FROM files "
                "WHERE day = ? AND deleted = 0 GROUP BY ext "
                "ORDER BY c DESC LIMIT ?",
                (day, limit),
            )
            return [
                (r["ext"] or "(无扩展名)", int(r["c"]), int(r["s"]))
                for r in cur.fetchall()
            ]

    def max_day_count(self, days: int = 7) -> int:
        """近 N 天里单日新增最多是多少，用于给悬浮球的进度环定标。"""
        cutoff = (date.today() - timedelta(days=max(1, days) - 1)).isoformat()
        with self._lock:
            cur = self._conn.execute(
                "SELECT MAX(c) m FROM ("
                "  SELECT COUNT(*) c FROM files WHERE day >= ? AND deleted = 0"
                "  GROUP BY day"
                ")",
                (cutoff,),
            )
            row = cur.fetchone()
            return int(row["m"] or 0)

    def max_day_size(self, days: int = 7) -> int:
        """近 N 天里单日新增体积峰值（字节），用于迷你球进度环。"""
        cutoff = (date.today() - timedelta(days=max(1, days) - 1)).isoformat()
        with self._lock:
            cur = self._conn.execute(
                "SELECT MAX(s) m FROM ("
                "  SELECT COALESCE(SUM(size), 0) s FROM files"
                "  WHERE day >= ? AND deleted = 0 GROUP BY day"
                ")",
                (cutoff,),
            )
            row = cur.fetchone()
            return int(row["m"] or 0)

    def total_count(self) -> int:
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) c FROM files")
            return int(cur.fetchone()["c"])


def _row_to_record(row: sqlite3.Row) -> FileRecord:
    return FileRecord(
        path=row["path"],
        name=row["name"],
        ext=row["ext"] or "",
        drive=row["drive"] or "",
        folder=row["folder"] or "",
        size=int(row["size"] or 0),
        added_at=float(row["added_at"]),
        deleted=bool(row["deleted"]),
    )


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(num)} {unit}"
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"
