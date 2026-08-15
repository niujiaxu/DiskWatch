"""SQLite 持久化：记录新增文件、按日汇总、清理过期数据。

读写分连接：WAL 下写库不堵 UI 读；后台写线程独占 write 连接。
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .errorlog import errorlog
from .i18n import tr

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
    deleted     INTEGER DEFAULT 0,
    deleted_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_files_day ON files(day);
CREATE INDEX IF NOT EXISTS idx_files_day_added ON files(day, added_at);
CREATE INDEX IF NOT EXISTS idx_files_added ON files(added_at);
CREATE INDEX IF NOT EXISTS idx_files_pending ON files(size_final, added_at);

CREATE TABLE IF NOT EXISTS disk_space (
    day         TEXT NOT NULL,
    drive       TEXT NOT NULL,
    free_bytes  INTEGER NOT NULL,
    total_bytes INTEGER NOT NULL,
    sampled_at  REAL NOT NULL,
    PRIMARY KEY (day, drive)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

_SCHEMA_VERSION = 1


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
    deleted_at: float | None = None

    @property
    def added_dt(self) -> datetime:
        return datetime.fromtimestamp(self.added_at)

    @property
    def deleted_dt(self) -> datetime | None:
        if self.deleted_at is not None:
            return datetime.fromtimestamp(self.deleted_at)
        return None


@dataclass(frozen=True)
class DaySummary:
    day: str
    count: int
    total_size: int
    total_free: int | None = None  # None=当天无空间采样；0=磁盘已满


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
    """写走后台连接；读走独立连接，避免 UI 被批量入库卡住。"""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._write_lock = threading.Lock()
        self._write = self._connect()
        self._write.execute("PRAGMA journal_mode=WAL")
        self._write.execute("PRAGMA synchronous=NORMAL")
        self._write.execute("PRAGMA busy_timeout=5000")
        with self._write_lock:
            self._write.executescript(SCHEMA)
            self._write.commit()
            self._migrate_schema()
        # 写入异常（被 SQLite 抛出的）会进这里，供 UI 展示与排错
        self._write_errors: deque[tuple[float, str]] = deque(maxlen=20)
        # 仅供 Qt 主线程读：不与 write 抢同一把 Python 锁
        self._read = self._connect()
        self._read.execute("PRAGMA busy_timeout=5000")
        # 数据变更计数：每次写事务成功提交 +1，供 UI 判断"是否需要重载"
        self._change_seq = 0

    @property
    def change_seq(self) -> int:
        """自上次读取以来数据是否变化：两次读数不同说明有新写入。"""
        return self._change_seq

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self._path),
            check_same_thread=False,
            timeout=5.0,
        )
        conn.row_factory = sqlite3.Row
        return conn

    def close(self) -> None:
        with self._write_lock:
            try:
                self._write.close()
            except sqlite3.Error:
                pass
        try:
            self._read.close()
        except sqlite3.Error:
            pass

    # ---------- 迁移 ----------

    def _migrate_schema(self) -> None:
        try:
            row = self._write.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
            ver = int(row["value"]) if row else 0
        except (ValueError, TypeError):
            ver = 0
        if ver < _SCHEMA_VERSION:
            try:
                self._write.execute(
                    "ALTER TABLE files ADD COLUMN deleted_at REAL"
                )
            except sqlite3.OperationalError:
                pass  # 列已存在（可能旧 DB 碰巧有）
            self._write.execute(
                "INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)",
                (str(_SCHEMA_VERSION),),
            )
            self._write.commit()

    # ---------- 写 ----------

    @contextmanager
    def _write_tx(self):
        """持锁写入：成功则 commit，抛 sqlite3.Error 时 rollback 并记录。

        裸 sqlite3.Connection 在 execute 后会自动开事务；中途出错但未
        rollback，下一次写入会被并进这个未提交事务，进程一崩整批丢失。
        """
        with self._write_lock:
            try:
                yield
            except BaseException as exc:
                # 任何异常都回滚：不仅 sqlite3.Error，代码 bug 抛的异常
                # 也会让连接停留在未提交事务，必须一并回滚。
                self._write.rollback()
                if isinstance(exc, sqlite3.Error):
                    self._write_errors.append(
                        (time.time(), f"{type(exc).__name__}: {exc}")
                    )
                    errorlog.log_exception("storage", exc)
                raise
            else:
                self._write.commit()
                self._change_seq += 1

    def recent_errors(self) -> list[tuple[float, str]]:
        """最近的写入错误，[(timestamp, message)]，新到旧。"""
        return list(self._write_errors)[::-1]

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
        with self._write_tx():
            cur = self._write.executemany(
                """
                INSERT INTO files (path, name, ext, drive, folder, size, added_at, day, size_final, deleted)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(path) DO UPDATE SET
                    size       = excluded.size,
                    added_at   = excluded.added_at,
                    day        = excluded.day,
                    size_final = excluded.size_final,
                    deleted    = 0,
                    deleted_at = NULL
                """,
                rows,
            )
            return cur.rowcount

    def backfill_records(self, records: list[FileRecord]) -> int:
        """启动补扫专用：只插入缺失路径，绝不覆盖已有行的统计。

        - 路径不在库 → 完整插入（added_at 取文件创建时间，落到正确的天）。
        - 路径已存在且 deleted=1（曾删除后又重建）→ 只把 deleted 清 0 复活。
        - 路径已存在且正常 → 什么都不动（实时 watcher 的数据更准，扫描别去覆盖）。
        """
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
        with self._write_tx():
            cur = self._write.executemany(
                """
                INSERT INTO files (path, name, ext, drive, folder, size, added_at, day, size_final, deleted)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(path) DO UPDATE SET deleted = 0, deleted_at = NULL
                """,
                rows,
            )
            return cur.rowcount

    def mark_deleted(self, paths: list[str], deleted_at: float | None = None) -> None:
        if not paths:
            return
        when = deleted_at if deleted_at is not None else time.time()
        with self._write_tx():
            for p in paths:
                p = p.rstrip("\\/")
                if not p:
                    continue
                esc = (p + "\\").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                self._write.execute(
                    "UPDATE files SET deleted = 1, deleted_at = ? WHERE path = ? OR path LIKE ? ESCAPE '\\'",
                    (when, p, esc + "%"),
                )

    def delete_paths(self, paths: list[str]) -> None:
        if not paths:
            return
        with self._write_tx():
            self._write.executemany(
                "DELETE FROM files WHERE path = ?", [(p,) for p in paths]
            )

    def move_file(self, src: str, dst: str, fallback: FileRecord | None) -> None:
        """处理单文件改名 / 移动（watchdog 的 on_moved 是唯一事件，不补发 on_created）。

        - src 已入库：把旧行整体挪到 dst（保留原 added_at / size），并清掉 dst 处可能存在的旧行。
        - src 未入库：重命名本身就是「新增」（最常见的下载 .part/.crdownload → 正式名，
          临时名通常被扩展名过滤挡掉、从未入库），直接登记 dst。
        - fallback 为 None 表示 dst 也没通过过滤，无事可做。
        """
        src = src.replace("/", "\\")
        dst = dst.replace("/", "\\")
        if src == dst:
            return
        with self._write_tx():
            tracked = self._write.execute(
                "SELECT 1 FROM files WHERE path = ?", (src,)
            ).fetchone()
            if tracked:
                self._write.execute("DELETE FROM files WHERE path = ?", (dst,))
                # 文件既然搬到了 dst 就还活着，重置 deleted，防止同批级联标删先执行。
                self._write.execute(
                    "UPDATE files SET path = ?, name = ?, folder = ?, ext = ?, deleted = 0, deleted_at = NULL WHERE path = ?",
                    (dst, Path(dst).name, str(Path(dst).parent), Path(dst).suffix.lower(), src),
                )
            elif fallback is not None:
                self._write.execute(
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
                    (
                        fallback.path,
                        fallback.name,
                        fallback.ext,
                        fallback.drive,
                        fallback.folder,
                        fallback.size,
                        fallback.added_at,
                        datetime.fromtimestamp(fallback.added_at).date().isoformat(),
                        1 if fallback.size > 0 else 0,
                    ),
                )

    def move_subtree(self, src: str, dst: str) -> None:
        """目录整体移动（watchdog 的 DirMovedEvent）。

        移动时 watchdog 会给新位置下的文件补发 on_created（新路径已入库），
        旧路径行就成了残留：
        - 目标路径已存在 → 旧行是幽灵，删掉（避免同一文件出现新旧两条）
        - 目标路径不存在 → 把旧行整体平移到新路径（兜底，事件没补全时也不丢记录）
        """
        src = src.rstrip("\\/")
        dst = dst.rstrip("\\/")
        if not src or not dst or src.lower() == dst.lower():
            return
        prefix = src + "\\"
        nprefix = dst + "\\"
        # LIKE 通配符转义：路径里的 % _ 都要当字面量
        esc = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self._write_tx():
            # SELECT 必须和后续 UPDATE 在同一个锁内：_write 连接被多个
            # 后台线程共用，锁外读会与写入并发，读到不一致快照。
            rows = self._write.execute(
                "SELECT path FROM files WHERE path = ? OR path LIKE ? ESCAPE '\\'",
                (src, esc + "%"),
            ).fetchall()
            if not rows:
                return  # 无 DML，纯 SELECT 不开启事务，直接返回无需 commit
            mapping = [
                (p, dst if p == src else nprefix + p[len(prefix) :])
                for p in (r["path"] for r in rows)
            ]
            for old, new in mapping:
                if new == old:
                    continue
                if self._write.execute(
                    "SELECT 1 FROM files WHERE path = ?", (new,)
                ).fetchone():
                    # 新路径已有行（on_created 补发的）→ 旧行是残留
                    self._write.execute("DELETE FROM files WHERE path = ?", (old,))
                else:
                    # 目录搬到新路径后子文件仍存在，重置 deleted，防级联标删竞态
                    self._write.execute(
                        "UPDATE files SET path = ?, name = ?, folder = ?, ext = ?, deleted = 0, deleted_at = NULL WHERE path = ?",
                        (new, Path(new).name, str(Path(new).parent), Path(new).suffix.lower(), old),
                    )

    def record_disk_space(self, samples: list[tuple[str, str, int, int]]) -> None:
        """(day, drive, free_bytes, total_bytes) 按天+盘符 upsert，只保留最新采样。"""
        if not samples:
            return
        now = time.time()
        with self._write_tx():
            self._write.executemany(
                """
                INSERT INTO disk_space (day, drive, free_bytes, total_bytes, sampled_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(day, drive) DO UPDATE SET
                    free_bytes  = excluded.free_bytes,
                    total_bytes = excluded.total_bytes,
                    sampled_at  = excluded.sampled_at
                """,
                [(d, dv, f, t, now) for d, dv, f, t in samples],
            )

    def pending_size_rows(self, older_than: float, limit: int = 500) -> list[str]:
        """刚创建的文件往往还是 0 字节，稍后再回来补真实体积。"""
        with self._write_lock:
            cur = self._write.execute(
                "SELECT path FROM files WHERE size_final = 0 AND deleted = 0 "
                "AND added_at < ? ORDER BY added_at LIMIT ?",
                (older_than, limit),
            )
            return [r["path"] for r in cur.fetchall()]

    def update_sizes(self, sizes: dict[str, int], missing: list[str]) -> None:
        if not sizes and not missing:
            return
        with self._write_tx():
            if sizes:
                self._write.executemany(
                    "UPDATE files SET size = ?, size_final = 1 WHERE path = ?",
                    [(v, k) for k, v in sizes.items()],
                )
            if missing:
                self._write.executemany(
                    "UPDATE files SET deleted = 1, size_final = 1 WHERE path = ?",
                    [(p,) for p in missing],
                )

    def purge_older_than(self, days: int) -> int:
        if days <= 0:
            return 0
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        with self._write_tx():
            cur = self._write.execute("DELETE FROM files WHERE day < ?", (cutoff,))
            self._write.execute("DELETE FROM disk_space WHERE day < ?", (cutoff,))
            return cur.rowcount

    def clear_all(self) -> None:
        with self._write_tx():
            self._write.execute("DELETE FROM files")
            self._write.execute("DELETE FROM disk_space")
            # 不做 VACUUM：会长时间锁库，点「清空」时容易把界面卡死

    # ---------- 读（UI 主线程，不抢 write 锁）----------

    def disk_space_for_day(self, day: str) -> list[tuple[str, int, int]]:
        """某天记录的磁盘剩余空间：[(drive, free_bytes, total_bytes)]，按盘符排序。"""
        cur = self._read.execute(
            "SELECT drive, free_bytes, total_bytes FROM disk_space "
            "WHERE day = ? ORDER BY drive",
            (day,),
        )
        return [
            (r["drive"], int(r["free_bytes"]), int(r["total_bytes"]))
            for r in cur.fetchall()
        ]

    def day_stats(self, day: str) -> tuple[int, int]:
        cur = self._read.execute(
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
        cur = self._read.execute(sql, args)
        return [_row_to_record(r) for r in cur.fetchall()]

    def files_for_day(
        self,
        day: str,
        keyword: str = "",
        include_deleted: bool = False,
        limit: int | None = None,
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
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        cur = self._read.execute(sql, args)
        return [_row_to_record(r) for r in cur.fetchall()]

    def days_with_data(self, limit: int = 60) -> list[DaySummary]:
        cur = self._read.execute(
            """
            WITH days AS (
                SELECT DISTINCT day FROM files WHERE deleted = 0
                UNION
                SELECT DISTINCT day FROM disk_space
            )
            SELECT d.day,
                   COALESCE(f.c, 0) AS c,
                   COALESCE(f.s, 0) AS s,
                   ds.free AS free
            FROM days d
            LEFT JOIN (SELECT day, COUNT(*) c, COALESCE(SUM(size), 0) s
                       FROM files WHERE deleted = 0 GROUP BY day) f ON f.day = d.day
            LEFT JOIN (SELECT day, SUM(free_bytes) free
                       FROM disk_space GROUP BY day) ds ON ds.day = d.day
            ORDER BY d.day DESC LIMIT ?
            """,
            (limit,),
        )
        return [
            DaySummary(
                r["day"], int(r["c"]), int(r["s"]),
                int(r["free"]) if r["free"] is not None else None,
            )
            for r in cur.fetchall()
        ]

    def top_folders(self, day: str, limit: int = 5) -> list[tuple[str, int, int]]:
        cur = self._read.execute(
            "SELECT folder, COUNT(*) c, COALESCE(SUM(size), 0) s FROM files "
            "WHERE day = ? AND deleted = 0 GROUP BY folder "
            "ORDER BY c DESC, s DESC LIMIT ?",
            (day, limit),
        )
        return [(r["folder"], int(r["c"]), int(r["s"])) for r in cur.fetchall()]

    def top_extensions(self, day: str, limit: int = 8) -> list[tuple[str, int, int]]:
        cur = self._read.execute(
            "SELECT ext, COUNT(*) c, COALESCE(SUM(size), 0) s FROM files "
            "WHERE day = ? AND deleted = 0 GROUP BY ext "
            "ORDER BY c DESC LIMIT ?",
            (day, limit),
        )
        return [
            (r["ext"] or tr("(无扩展名)"), int(r["c"]), int(r["s"]))
            for r in cur.fetchall()
        ]

    def max_day_count(self, days: int = 7) -> int:
        """近 N 天里单日新增最多是多少，用于给悬浮球的进度环定标。"""
        cutoff = (date.today() - timedelta(days=max(1, days) - 1)).isoformat()
        cur = self._read.execute(
            "SELECT MAX(c) m FROM ("
            "  SELECT COUNT(*) c FROM files WHERE day >= ? AND deleted = 0"
            "  GROUP BY day"
            ")",
            (cutoff,),
        )
        row = cur.fetchone()
        return int(row["m"] or 0)

    def period_total_size(self, days: int = 7) -> int:
        """近 N 天（含今天）新增文件体积合计，用于迷你球进度环。"""
        cutoff = (date.today() - timedelta(days=max(1, days) - 1)).isoformat()
        cur = self._read.execute(
            "SELECT COALESCE(SUM(size), 0) s FROM files "
            "WHERE day >= ? AND deleted = 0",
            (cutoff,),
        )
        return int(cur.fetchone()["s"] or 0)

    def max_day_size(self, days: int = 7) -> int:
        """近 N 天里单日新增体积峰值（字节）。"""
        cutoff = (date.today() - timedelta(days=max(1, days) - 1)).isoformat()
        cur = self._read.execute(
            "SELECT MAX(s) m FROM ("
            "  SELECT COALESCE(SUM(size), 0) s FROM files"
            "  WHERE day >= ? AND deleted = 0 GROUP BY day"
            ")",
            (cutoff,),
        )
        row = cur.fetchone()
        return int(row["m"] or 0)

    def total_count(self) -> int:
        cur = self._read.execute("SELECT COUNT(*) c FROM files")
        return int(cur.fetchone()["c"])

    def fetch_days_with_data(self, limit: int = 60) -> list[DaySummary]:
        """后台线程可用：自建短连接，不碰 UI 的 _read。"""
        conn = self._connect()
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            cur = conn.execute(
                """
                WITH days AS (
                    SELECT DISTINCT day FROM files WHERE deleted = 0
                    UNION
                    SELECT DISTINCT day FROM disk_space
                )
                SELECT d.day,
                       COALESCE(f.c, 0) AS c,
                       COALESCE(f.s, 0) AS s,
                       ds.free AS free
                FROM days d
                LEFT JOIN (SELECT day, COUNT(*) c, COALESCE(SUM(size), 0) s
                           FROM files WHERE deleted = 0 GROUP BY day) f ON f.day = d.day
                LEFT JOIN (SELECT day, SUM(free_bytes) free
                           FROM disk_space GROUP BY day) ds ON ds.day = d.day
                ORDER BY d.day DESC LIMIT ?
                """,
                (limit,),
            )
            return [
                DaySummary(
                    r["day"], int(r["c"]), int(r["s"]),
                    int(r["free"]) if r["free"] is not None else None,
                )
                for r in cur.fetchall()
            ]
        finally:
            conn.close()

    def top_folders_range(self, days: int, limit: int = 10) -> list[tuple[str, int, int]]:
        """近 N 天（含今天）按目录聚合 TOP，体积降序，后台线程可用。"""
        cutoff = (date.today() - timedelta(days=max(1, days) - 1)).isoformat()
        conn = self._connect()
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            cur = conn.execute(
                "SELECT folder, COUNT(*) c, COALESCE(SUM(size), 0) s FROM files "
                "WHERE day >= ? AND deleted = 0 GROUP BY folder "
                "ORDER BY s DESC, c DESC LIMIT ?",
                (cutoff, limit),
            )
            return [(r["folder"], int(r["c"]), int(r["s"])) for r in cur.fetchall()]
        finally:
            conn.close()

    def top_extensions_range(self, days: int, limit: int = 8) -> list[tuple[str, int, int]]:
        """近 N 天（含今天）按扩展名聚合 TOP，体积降序，后台线程可用。"""
        cutoff = (date.today() - timedelta(days=max(1, days) - 1)).isoformat()
        conn = self._connect()
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            cur = conn.execute(
                "SELECT ext, COUNT(*) c, COALESCE(SUM(size), 0) s FROM files "
                "WHERE day >= ? AND deleted = 0 GROUP BY ext "
                "ORDER BY s DESC, c DESC LIMIT ?",
                (cutoff, limit),
            )
            return [
                (r["ext"] or tr("(无扩展名)"), int(r["c"]), int(r["s"]))
                for r in cur.fetchall()
            ]
        finally:
            conn.close()

    def disk_space_trend(self, days: int) -> list[tuple[str, str, int]]:
        """近 N 天每盘剩余空间采样序列 (day, drive, free_bytes)，后台线程可用。

        disk_space 只保留每天每盘最新采样，天然是趋势序列；缺采样的天不出现。
        """
        cutoff = (date.today() - timedelta(days=max(1, days) - 1)).isoformat()
        conn = self._connect()
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            cur = conn.execute(
                "SELECT day, drive, free_bytes FROM disk_space "
                "WHERE day >= ? ORDER BY day, drive",
                (cutoff,),
            )
            return [
                (r["day"], r["drive"], int(r["free_bytes"])) for r in cur.fetchall()
            ]
        finally:
            conn.close()

    def fetch_day_view(
        self,
        day: str,
        keyword: str = "",
        limit: int | None = None,
        event_type: str = "added",
    ) -> dict:
        """后台线程打包一天详情所需的全部查询结果。

        keyword 非空时，列表与顶部统计（数量/体积/目录/类型）都按同一条件筛选。
        event_type: "added"（默认）/ "deleted" / "all"
        limit 为 None 时返回全量（详情面板默认全量展示，排序/分组在后台线程完成）。
        """
        conn = self._connect()
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            if event_type == "deleted":
                where = "day = ? AND deleted = 1"
            elif event_type == "all":
                where = "day = ?"
            else:
                where = "day = ? AND deleted = 0"
            args: list = [day]
            if keyword:
                where += " AND (LOWER(name) LIKE ? OR LOWER(folder) LIKE ?)"
                like = f"%{keyword.lower()}%"
                args += [like, like]

            sql = f"SELECT * FROM files WHERE {where} ORDER BY added_at DESC"
            list_args = list(args)
            if limit is not None:
                sql += " LIMIT ?"
                list_args.append(limit)
            records = [
                _row_to_record(r) for r in conn.execute(sql, list_args).fetchall()
            ]
            truncated = limit is not None and len(records) >= limit
            if truncated:
                records = records[: limit - 1]  # type: ignore[operator]

            row = conn.execute(
                f"SELECT COUNT(*) c, COALESCE(SUM(size), 0) s FROM files WHERE {where}",
                args,
            ).fetchone()
            count, size = int(row["c"]), int(row["s"])

            day_total = count
            if keyword:
                if event_type == "deleted":
                    dt_where = "day = ? AND deleted = 1"
                elif event_type == "all":
                    dt_where = "day = ?"
                else:
                    dt_where = "day = ? AND deleted = 0"
                day_total = int(
                    conn.execute(
                        f"SELECT COUNT(*) c FROM files WHERE {dt_where}",
                        (day,),
                    ).fetchone()["c"]
                )

            folders = [
                (r["folder"], int(r["c"]), int(r["s"]))
                for r in conn.execute(
                    f"SELECT folder, COUNT(*) c, COALESCE(SUM(size), 0) s FROM files "
                    f"WHERE {where} GROUP BY folder "
                    f"ORDER BY c DESC, s DESC LIMIT 1",
                    args,
                ).fetchall()
            ]
            exts = [
                (r["ext"] or tr("(无扩展名)"), int(r["c"]), int(r["s"]))
                for r in conn.execute(
                    f"SELECT ext, COUNT(*) c, COALESCE(SUM(size), 0) s FROM files "
                    f"WHERE {where} GROUP BY ext "
                    f"ORDER BY c DESC LIMIT 1",
                    args,
                ).fetchall()
            ]
            spaces = [
                (r["drive"], int(r["free_bytes"]), int(r["total_bytes"]))
                for r in conn.execute(
                    "SELECT drive, free_bytes, total_bytes FROM disk_space "
                    "WHERE day = ? ORDER BY drive",
                    (day,),
                ).fetchall()
            ]
            return {
                "day": day,
                "keyword": keyword,
                "records": records,
                "truncated": truncated,
                "count": count,
                "size": size,
                "day_total": day_total,
                "folders": folders,
                "exts": exts,
                "spaces": spaces,
                "event_type": event_type,
                "seq": self._change_seq,
            }
        finally:
            conn.close()


def _row_to_record(row: sqlite3.Row) -> FileRecord:
    return FileRecord(
        path=row["path"],
        name=row["name"],
        ext=row["ext"] or "",
        drive=row["drive"] or "",
        folder=row["folder"] or "",
        size=row["size"],
        added_at=row["added_at"],
        deleted=bool(row["deleted"]),
        deleted_at=row["deleted_at"] if row["deleted_at"] is not None else None,
    )


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(num)} {unit}"
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"
