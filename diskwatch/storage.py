"""SQLite 持久化：空间变化账本、分层汇总、磁盘采样与历史清理。

读写分连接：WAL 下写库不堵 UI 读；后台写线程独占 write 连接。
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol

from .apps import resolve_app
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

CREATE TABLE IF NOT EXISTS file_state (
    path          TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    ext           TEXT,
    drive         TEXT,
    folder        TEXT,
    size          INTEGER NOT NULL DEFAULT 0,
    category      TEXT NOT NULL DEFAULT 'unclassified',
    exists_now    INTEGER NOT NULL DEFAULT 1,
    first_seen_at REAL NOT NULL,
    last_seen_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_file_state_drive ON file_state(drive, exists_now);
CREATE INDEX IF NOT EXISTS idx_file_state_category ON file_state(category, exists_now);

CREATE TABLE IF NOT EXISTS space_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    path          TEXT NOT NULL,
    old_path      TEXT,
    name          TEXT NOT NULL,
    ext           TEXT,
    drive         TEXT,
    folder        TEXT,
    category      TEXT NOT NULL DEFAULT 'unclassified',
    event_type    TEXT NOT NULL,
    old_size      INTEGER NOT NULL DEFAULT 0,
    new_size      INTEGER NOT NULL DEFAULT 0,
    delta_bytes   INTEGER NOT NULL DEFAULT 0,
    occurred_at   REAL NOT NULL,
    day           TEXT NOT NULL,
    merged_count  INTEGER NOT NULL DEFAULT 1,
    app_key       TEXT NOT NULL DEFAULT '',
    app_sub       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_space_events_time ON space_events(occurred_at);
CREATE INDEX IF NOT EXISTS idx_space_events_day_drive ON space_events(day, drive);
CREATE INDEX IF NOT EXISTS idx_space_events_category ON space_events(day, category);
CREATE INDEX IF NOT EXISTS idx_space_events_path ON space_events(path, occurred_at);

CREATE TABLE IF NOT EXISTS space_hourly (
    hour           TEXT NOT NULL,
    drive          TEXT NOT NULL,
    category       TEXT NOT NULL,
    occupied_bytes INTEGER NOT NULL DEFAULT 0,
    released_bytes INTEGER NOT NULL DEFAULT 0,
    net_bytes      INTEGER NOT NULL DEFAULT 0,
    event_count    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (hour, drive, category)
);
CREATE INDEX IF NOT EXISTS idx_space_hourly_drive_hour
    ON space_hourly(drive, hour);

CREATE TABLE IF NOT EXISTS space_daily (
    day            TEXT NOT NULL,
    drive          TEXT NOT NULL,
    category       TEXT NOT NULL,
    occupied_bytes INTEGER NOT NULL DEFAULT 0,
    released_bytes INTEGER NOT NULL DEFAULT 0,
    net_bytes      INTEGER NOT NULL DEFAULT 0,
    event_count    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, drive, category)
);
CREATE INDEX IF NOT EXISTS idx_space_daily_drive_day
    ON space_daily(drive, day);

CREATE TABLE IF NOT EXISTS disk_samples (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    day         TEXT NOT NULL,
    drive       TEXT NOT NULL,
    free_bytes  INTEGER NOT NULL,
    total_bytes INTEGER NOT NULL,
    sampled_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_disk_samples_drive_time
    ON disk_samples(drive, sampled_at);
CREATE INDEX IF NOT EXISTS idx_disk_samples_day_drive
    ON disk_samples(day, drive, sampled_at);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

_SCHEMA_VERSION = 5

# 动态降噪：同一路径在 FOLD_WINDOW 内的连续变化视为一"串"。一串里前
# FOLD_AFTER 条照常记录明细，第 FOLD_AFTER+1 条起合并为一条
# event_type='churn' 的聚合行（merged_count 记录合并条数）。净字节一条
# 不丢：聚合行 delta 是串内各事件的累加，小时/天汇总仍按原始事件计入。
# 这就是"不给每个软件维护排除清单"的动态方案：无论缓存放在哪个目录，
# 高频抖动都会被自动折叠成一行，安静超过 FOLD_WINDOW 后恢复逐条记录。
FOLD_WINDOW = 600.0      # 串内两次变化的允许间隔（滚动窗口，秒）
FOLD_AFTER = 5           # 一串内前 N 条记明细，之后折叠
FOLD_MAX_PATHS = 20000   # 折叠状态字典上限，超出时清掉过期条目
_FOLD_EVENTS = frozenset({"created", "modified", "deleted", "recreated"})


class _SqlExecutor(Protocol):
    def execute(self, sql: str, parameters=()) -> sqlite3.Cursor: ...


@dataclass(frozen=True)
class FileRecord:
    path: str
    name: str
    ext: str
    drive: str
    folder: str
    size: int
    added_at: float
    category: str = "unclassified"
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
    # 当天所有采样盘的剩余字节合计；None=当天无采样，0=磁盘已满
    total_free: int | None = None


@dataclass(frozen=True)
class SpaceEvent:
    id: int
    path: str
    old_path: str | None
    drive: str
    category: str
    event_type: str
    old_size: int
    new_size: int
    delta_bytes: int
    occurred_at: float
    merged_count: int = 1


@dataclass(frozen=True)
class SpaceDaySummary:
    day: str
    occupied_bytes: int
    released_bytes: int
    net_bytes: int
    event_count: int


@dataclass(frozen=True)
class DiskSample:
    drive: str
    free_bytes: int
    total_bytes: int
    sampled_at: float


@dataclass(frozen=True)
class AttributionSummary:
    drive: str
    actual_delta: int
    attributed_delta: int
    unattributed_delta: int


def today_str() -> str:
    return date.today().isoformat()


def _day_of(added_at: float) -> str:
    """时间戳 → 归属日字符串（与入库时的 day 列口径一致）。"""
    return datetime.fromtimestamp(added_at).date().isoformat()


def _like_escape(s: str) -> str:
    """LIKE 模式的字面量转义：先转义反斜杠，再转义 % 和 _（顺序不可换）。"""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _event_where(event_type: str) -> str:
    """事件类型 → 行过滤片段（与 day 条件 AND 拼接；"all" 用恒真式）。"""
    if event_type == "deleted":
        return "deleted = 1"
    if event_type == "all":
        return "1 = 1"
    return "deleted = 0"


def _query_day_summaries(conn: _SqlExecutor, limit: int) -> list[DaySummary]:
    """按天聚合摘要（新增数/体积/剩余空间），主线程与后台线程共用同一查询。"""
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


def make_record(
    path: str,
    size: int,
    added_at: float | None = None,
    category: str = "unclassified",
) -> FileRecord:
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
        category=category,
    )


class _ReadPool:
    """每个读取线程一条连接，避免跨线程复用 sqlite3.Connection。"""

    def __init__(self, factory) -> None:
        self._factory = factory
        self._lock = threading.Lock()
        self._connections: dict[int, sqlite3.Connection] = {}
        self._closed = False

    def _connection(self) -> sqlite3.Connection:
        thread_id = threading.get_ident()
        with self._lock:
            if self._closed:
                raise sqlite3.ProgrammingError("storage is closed")
            connection = self._connections.get(thread_id)
            if connection is None:
                connection = self._factory()
                connection.execute("PRAGMA busy_timeout=5000")
                self._connections[thread_id] = connection
            return connection

    def execute(self, sql: str, parameters=()):
        return self._connection().execute(sql, parameters)

    def release_current_thread(self) -> None:
        """关闭并移除当前线程的读连接。

        UI 主线程会长期复用连接；短生命周期后台线程应在退出前调用，
        防止线程结束后连接仍被池持有。
        """
        thread_id = threading.get_ident()
        with self._lock:
            connection = self._connections.pop(thread_id, None)
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass

    def close(self) -> None:
        current_thread = threading.get_ident()
        with self._lock:
            self._closed = True
            # sqlite3.Connection 即使 check_same_thread=False，也不能在查询
            # 执行到一半时由另一个线程强制 close；Python 3.14/Windows 下会
            # 直接 access violation。这里只关闭调用 close() 的线程连接，
            # 其他短生命周期线程会在 finally/release_current_thread 中自关。
            connection = self._connections.pop(current_thread, None)
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass


class Storage:
    """写走后台连接；读走独立连接，避免 UI 被批量入库卡住。"""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        db_existed = self._path.exists() and self._path.stat().st_size > 0
        self.last_migration_backup: Path | None = None
        self._write_lock = threading.Lock()
        self._write = self._connect()
        self._write.execute("PRAGMA journal_mode=WAL")
        self._write.execute("PRAGMA synchronous=NORMAL")
        self._write.execute("PRAGMA busy_timeout=5000")
        try:
            with self._write_lock:
                if db_existed:
                    self._backup_before_migration()
                self._write.executescript(SCHEMA)
                self._write.commit()
                self._migrate_schema()
        except Exception:
            try:
                self._write.close()
            finally:
                self._restore_migration_backup()
            raise
        # 写入异常（被 SQLite 抛出的）会进这里，供 UI 展示与排错
        self._write_errors: deque[tuple[float, str]] = deque(maxlen=20)
        # UI 与聚合后台线程各自使用读连接，避免跨线程复用造成原生崩溃。
        self._read = _ReadPool(self._connect)
        # 数据变更计数：每次写事务成功提交 +1，供 UI 判断"是否需要重载"
        self._change_seq = 0
        # 高频折叠状态：path -> (最近事件时间, 串内事件数, 聚合行 id, 归属日)
        self._fold: dict[str, tuple[float, int, int | None, str]] = {}

    @property
    def change_seq(self) -> int:
        """自上次读取以来数据是否变化：两次读数不同说明有新写入。"""
        return self._change_seq

    @property
    def path(self) -> Path:
        return self._path

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
        self._read.close()

    def release_reader(self) -> None:
        """释放调用线程持有的只读连接，供短生命周期后台任务使用。"""
        self._read.release_current_thread()

    # ---------- 迁移 ----------

    def _current_schema_version(self) -> int:
        try:
            row = self._write.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
            return int(row["value"]) if row else 0
        except (sqlite3.Error, ValueError, TypeError):
            return 0

    def _backup_before_migration(self) -> None:
        """结构升级前创建一次 SQLite 一致性备份。"""
        version = self._current_schema_version()
        if version >= _SCHEMA_VERSION:
            return
        backup = self._path.with_name(
            f"{self._path.name}.migration-v{version}-to-v{_SCHEMA_VERSION}.bak"
        )
        if not backup.exists():
            target = sqlite3.connect(str(backup))
            try:
                self._write.backup(target)
            finally:
                target.close()
        self.last_migration_backup = backup

    def _restore_migration_backup(self) -> None:
        """结构升级失败时恢复原库；备份文件保留供人工检查。"""
        backup = self.last_migration_backup
        if backup is None or not backup.exists():
            return
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(self._path) + suffix)
            try:
                sidecar.unlink(missing_ok=True)
            except OSError:
                pass
        shutil.copy2(backup, self._path)

    def _migrate_schema(self) -> None:
        ver = self._current_schema_version()
        if ver < 1:
            try:
                self._write.execute(
                    "ALTER TABLE files ADD COLUMN deleted_at REAL"
                )
            except sqlite3.OperationalError:
                pass  # 列已存在（可能旧 DB 碰巧有）
        if ver < 2:
            # 旧 files 表继续服务当前 UI；file_state 从其最新状态初始化。
            # 不补造历史 space_events，避免把升级前全部存量误算为今日新增。
            self._write.execute(
                """
                INSERT OR IGNORE INTO file_state (
                    path, name, ext, drive, folder, size, category,
                    exists_now, first_seen_at, last_seen_at
                )
                SELECT path, name, ext, drive, folder, size, 'unclassified',
                       CASE WHEN deleted = 0 THEN 1 ELSE 0 END,
                       added_at, COALESCE(deleted_at, added_at)
                FROM files
                """
            )
        if ver < 3:
            # v2 已有精确事件：迁移时只做汇总，不伪造原始事件。
            for table, bucket in (
                ("space_hourly", "strftime('%Y-%m-%dT%H:00', occurred_at, 'unixepoch', 'localtime')"),
                ("space_daily", "day"),
            ):
                column = "hour" if table == "space_hourly" else "day"
                self._write.execute(
                    f"""
                    INSERT OR IGNORE INTO {table} (
                        {column}, drive, category, occupied_bytes,
                        released_bytes, net_bytes, event_count
                    )
                    SELECT {bucket}, COALESCE(drive, ''), category,
                           SUM(CASE WHEN delta_bytes > 0 THEN delta_bytes ELSE 0 END),
                           SUM(CASE WHEN delta_bytes < 0 THEN -delta_bytes ELSE 0 END),
                           SUM(delta_bytes), COUNT(*)
                    FROM space_events
                    GROUP BY {bucket}, COALESCE(drive, ''), category
                    """
                )
            self._write.execute(
                """
                INSERT INTO disk_samples (day, drive, free_bytes, total_bytes, sampled_at)
                SELECT day, drive, free_bytes, total_bytes, sampled_at
                FROM disk_space
                WHERE NOT EXISTS (
                    SELECT 1 FROM disk_samples s
                    WHERE s.drive = disk_space.drive
                      AND s.sampled_at = disk_space.sampled_at
                )
                """
            )
        if ver < 4:
            try:
                self._write.execute(
                    "ALTER TABLE space_events "
                    "ADD COLUMN merged_count INTEGER NOT NULL DEFAULT 1"
                )
            except sqlite3.OperationalError:
                pass  # 列已存在（可能旧 DB 碰巧有）
        if ver < 5:
            # 按应用分组：写入时算好的归属键；老库按路径批量回填一次。
            for column in ("app_key", "app_sub"):
                try:
                    self._write.execute(
                        f"ALTER TABLE space_events ADD COLUMN {column} "
                        "TEXT NOT NULL DEFAULT ''"
                    )
                except sqlite3.OperationalError:
                    pass
            rows = self._write.execute(
                "SELECT DISTINCT path FROM space_events WHERE app_key = ''"
            ).fetchall()
            batch: list[tuple[str, str, str]] = []
            for row in rows:
                key, sub = resolve_app(str(row["path"]))
                batch.append((key, sub, str(row["path"])))
                if len(batch) >= 5000:
                    self._write.executemany(
                        "UPDATE space_events SET app_key = ?, app_sub = ? "
                        "WHERE path = ?",
                        batch,
                    )
                    self._write.commit()
                    batch = []
            if batch:
                self._write.executemany(
                    "UPDATE space_events SET app_key = ?, app_sub = ? WHERE path = ?",
                    batch,
                )
            self._write.execute(
                "CREATE INDEX IF NOT EXISTS idx_space_events_app "
                "ON space_events(app_key)"
            )
        if ver < _SCHEMA_VERSION:
            self._write.execute(
                "INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)",
                (str(_SCHEMA_VERSION),),
            )
            self._write.commit()

    # ---------- 写 ----------

    @contextmanager
    def _write_tx(self, timeout: float | None = None):
        """持锁写入：成功则 commit，抛 sqlite3.Error 时 rollback 并记录。

        裸 sqlite3.Connection 在 execute 后会自动开事务；中途出错但未
        rollback，下一次写入会被并进这个未提交事务，进程一崩整批丢失。

        timeout 给定时最多等这么久拿不到写锁就抛 TimeoutError：后台维护
        任务（如补扫收尾）宁可跳过这批写入，也不能把界面卡在“正在补扫”。
        """
        if timeout is None:
            self._write_lock.acquire()
        elif not self._write_lock.acquire(timeout=timeout):
            raise TimeoutError("写锁等待超时")
        try:
            before = self._write.total_changes
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
                # 只有真正发生了写入（total_changes 增加）才推进数据版本，
                # 纯 SELECT / 空操作事务（move 无匹配等）不该触发 UI 重载
                if self._write.total_changes > before:
                    self._change_seq += 1
        finally:
            self._write_lock.release()

    def recent_errors(self) -> list[tuple[float, str]]:
        """最近的写入错误，[(timestamp, message)]，新到旧。"""
        return list(self._write_errors)[::-1]

    def _rollup_event_locked(
        self, drive: str, category: str, delta: int, occurred_at: float
    ) -> None:
        """把一条事件折算进小时/天汇总（无论是否折叠，都按原始事件计入）。"""
        hour = datetime.fromtimestamp(occurred_at).strftime("%Y-%m-%dT%H:00")
        day = _day_of(occurred_at)
        occupied = max(delta, 0)
        released = max(-delta, 0)
        for table, column, bucket in (
            ("space_hourly", "hour", hour),
            ("space_daily", "day", day),
        ):
            self._write.execute(
                f"""
                INSERT INTO {table} (
                    {column}, drive, category, occupied_bytes,
                    released_bytes, net_bytes, event_count
                ) VALUES (?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT({column}, drive, category) DO UPDATE SET
                    occupied_bytes = occupied_bytes + excluded.occupied_bytes,
                    released_bytes = released_bytes + excluded.released_bytes,
                    net_bytes = net_bytes + excluded.net_bytes,
                    event_count = event_count + 1
                """,
                (bucket, drive, category, occupied, released, delta),
            )

    def _shrink_fold_locked(self) -> None:
        """折叠状态字典超限时清掉过期条目，防止长跑进程内存缓涨。"""
        if len(self._fold) <= FOLD_MAX_PATHS:
            return
        cutoff = time.time() - FOLD_WINDOW * 6
        for path in [p for p, state in self._fold.items() if state[0] < cutoff]:
            del self._fold[path]

    def _insert_space_event_locked(
        self,
        record: FileRecord,
        event_type: str,
        old_size: int,
        new_size: int,
        occurred_at: float,
        *,
        old_path: str | None = None,
        drive: str | None = None,
        delta_bytes: int | None = None,
    ) -> None:
        """写一条空间事件；高频同路径变化在此折叠为单条聚合行。

        - 零字节变化（0B 创建/删除/改写）对空间账本零信息，直接不记。
        - 同路径在 FOLD_WINDOW 内反复变化时，前 FOLD_AFTER 条记明细，
          之后并入 event_type='churn' 的聚合行，净字节不丢。
        """
        delta = new_size - old_size if delta_bytes is None else delta_bytes
        if delta == 0 and event_type != "moved":
            return
        event_drive = drive if drive is not None else record.drive
        day = _day_of(occurred_at)
        merged_count = 1
        fold_state: tuple[float, int, int | None, str] | None = None
        if event_type in _FOLD_EVENTS:
            state = self._fold.get(record.path)
            in_burst = bool(
                state is not None
                and state[3] == day
                and 0.0 <= occurred_at - state[0] <= FOLD_WINDOW
            )
            if in_burst:
                assert state is not None
                _, count, agg_id, _ = state
                count += 1
                if agg_id is not None:
                    # 串内第 FOLD_AFTER+1 条起：并入已有聚合行，不新增行
                    self._write.execute(
                        "UPDATE space_events SET new_size = ?, "
                        "delta_bytes = delta_bytes + ?, occurred_at = ?, "
                        "merged_count = merged_count + 1 WHERE id = ?",
                        (new_size, delta, occurred_at, agg_id),
                    )
                    self._fold[record.path] = (occurred_at, count, agg_id, day)
                    self._rollup_event_locked(
                        event_drive, record.category, delta, occurred_at
                    )
                    return
                if count > FOLD_AFTER:
                    # 首次越阈值：从这条起新开聚合行
                    event_type = "churn"
                    merged_count = count - FOLD_AFTER
                fold_state = (occurred_at, count, None, day)
            else:
                self._shrink_fold_locked()
                fold_state = (occurred_at, 1, None, day)
        cur = self._write.execute(
            """
            INSERT INTO space_events (
                path, old_path, name, ext, drive, folder, category, event_type,
                old_size, new_size, delta_bytes, occurred_at, day, merged_count,
                app_key, app_sub
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.path,
                old_path,
                record.name,
                record.ext,
                event_drive,
                record.folder,
                record.category,
                event_type,
                old_size,
                new_size,
                delta,
                occurred_at,
                day,
                merged_count,
                *resolve_app(record.path),
            ),
        )
        if fold_state is not None:
            # 只有聚合行才记住 id（后续事件并入它）；明细行存 None
            self._fold[record.path] = (
                fold_state[0],
                fold_state[1],
                int(cur.lastrowid or 0) if event_type == "churn" else None,
                fold_state[3],
            )
        self._rollup_event_locked(event_drive, record.category, delta, occurred_at)

    def _observe_record_locked(
        self,
        record: FileRecord,
        *,
        event_type: str | None = None,
        occurred_at: float | None = None,
    ) -> bool:
        """在同一事务内更新兼容表、当前状态和空间账本。"""
        when = occurred_at if occurred_at is not None else record.added_at
        state = self._write.execute(
            "SELECT size, category, exists_now, first_seen_at FROM file_state WHERE path = ?",
            (record.path,),
        ).fetchone()
        was_present = bool(state and state["exists_now"])
        old_size = int(state["size"]) if was_present else 0
        category = record.category
        if category == "unclassified" and state is not None:
            category = str(state["category"] or category)
            record = FileRecord(
                path=record.path,
                name=record.name,
                ext=record.ext,
                drive=record.drive,
                folder=record.folder,
                size=record.size,
                added_at=record.added_at,
                category=category,
            )

        if was_present:
            self._write.execute(
                "UPDATE files SET size = ?, size_final = ?, deleted = 0, deleted_at = NULL "
                "WHERE path = ?",
                (record.size, 1 if record.size > 0 else 0, record.path),
            )
        else:
            self._write.execute(
                """
                INSERT INTO files (
                    path, name, ext, drive, folder, size, added_at, day, size_final, deleted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(path) DO UPDATE SET
                    name       = excluded.name,
                    ext        = excluded.ext,
                    drive      = excluded.drive,
                    folder     = excluded.folder,
                    size       = excluded.size,
                    added_at   = excluded.added_at,
                    day        = excluded.day,
                    size_final = excluded.size_final,
                    deleted    = 0,
                    deleted_at = NULL
                """,
                (
                    record.path,
                    record.name,
                    record.ext,
                    record.drive,
                    record.folder,
                    record.size,
                    record.added_at,
                    _day_of(record.added_at),
                    1 if record.size > 0 else 0,
                ),
            )

        first_seen = float(state["first_seen_at"]) if state is not None else when
        self._write.execute(
            """
            INSERT INTO file_state (
                path, name, ext, drive, folder, size, category,
                exists_now, first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                name         = excluded.name,
                ext          = excluded.ext,
                drive        = excluded.drive,
                folder       = excluded.folder,
                size         = excluded.size,
                category     = excluded.category,
                exists_now   = 1,
                last_seen_at = excluded.last_seen_at
            """,
            (
                record.path,
                record.name,
                record.ext,
                record.drive,
                record.folder,
                record.size,
                category,
                first_seen,
                when,
            ),
        )

        delta = record.size - old_size
        if event_type is None:
            if state is None:
                event_type = "created"
            elif not was_present:
                event_type = "recreated"
            elif delta:
                event_type = "modified"
        if event_type and (not was_present or delta or event_type in {"created", "recreated"}):
            self._insert_space_event_locked(
                record, event_type, old_size, record.size, when
            )
            return True
        return bool(delta)

    def add_files(
        self,
        records: list[FileRecord],
        *,
        event_type: str | None = None,
        occurred_at: float | None = None,
    ) -> int:
        if not records:
            return 0
        with self._write_tx():
            changed = 0
            for record in records:
                changed += int(
                    self._observe_record_locked(
                        record,
                        event_type=event_type,
                        occurred_at=occurred_at,
                    )
                )
            return changed

    def add_events_batch(self, items: list[tuple[FileRecord, str, float]]) -> int:
        """恢复流程专用：一次事务写入多条带各自事件类型/发生时间的记录。

        逐条 add_files 时每条都是一个事务，Journal 大时极慢；攒批写入
        把数百条合并成一个事务。调用方需保证结构性操作（删除/移动）前
        先落库，以维持事件先后顺序。
        """
        if not items:
            return 0
        with self._write_tx():
            changed = 0
            for record, event_type, occurred_at in items:
                changed += int(
                    self._observe_record_locked(
                        record, event_type=event_type, occurred_at=occurred_at
                    )
                )
            return changed

    def backfill_records(
        self, records: list[FileRecord], *, timeout: float | None = None
    ) -> int:
        """启动补扫专用：只插入缺失路径，绝不覆盖已有行的统计。

        - 路径不在库 → 完整插入（added_at 取文件创建时间，落到正确的天）。
        - 路径已存在且 deleted=1（曾删除后又重建）→ 只把 deleted 清 0 复活。
        - 路径已存在且正常 → 什么都不动（实时 watcher 的数据更准，扫描别去覆盖）。
        - timeout：等待写锁的上限，超时抛 TimeoutError（补扫据此跳过本批，
          不让界面卡在“正在补扫”）。
        """
        if not records:
            return 0
        with self._write_tx(timeout):
            changed = 0
            for record in records:
                state = self._write.execute(
                    "SELECT exists_now FROM file_state WHERE path = ?", (record.path,)
                ).fetchone()
                if state is None or not state["exists_now"]:
                    changed += int(
                        self._observe_record_locked(record, event_type="recovered")
                    )
            return changed

    def mark_deleted(self, paths: list[str], deleted_at: float | None = None) -> None:
        if not paths:
            return
        when = deleted_at if deleted_at is not None else time.time()
        with self._write_tx():
            for p in paths:
                self._mark_deleted_locked(p, when)

    def _mark_deleted_locked(self, path: str, when: float) -> int:
        path = path.rstrip("\\/")
        if not path:
            return 0
        if len(path) == 2 and path[1] == ":":
            rows = self._write.execute(
                "SELECT * FROM file_state WHERE path = ? AND exists_now = 1", (path,)
            ).fetchall()
        else:
            esc = _like_escape(path + "\\")
            rows = self._write.execute(
                "SELECT * FROM file_state WHERE exists_now = 1 "
                "AND (path = ? OR path LIKE ? ESCAPE '\\')",
                (path, esc + "%"),
            ).fetchall()
        for row in rows:
            record = FileRecord(
                path=row["path"],
                name=row["name"],
                ext=row["ext"] or "",
                drive=row["drive"] or "",
                folder=row["folder"] or "",
                size=0,
                added_at=when,
                category=row["category"] or "unclassified",
            )
            old_size = int(row["size"])
            self._insert_space_event_locked(
                record,
                "deleted",
                old_size,
                0,
                when,
                delta_bytes=-old_size,
            )
            self._write.execute(
                "UPDATE file_state SET exists_now = 0, last_seen_at = ? "
                "WHERE path = ?",
                (when, row["path"]),
            )
            self._write.execute(
                "UPDATE files SET deleted = 1, deleted_at = ?, size_final = 1 WHERE path = ?",
                (when, row["path"]),
            )
        return len(rows)

    def delete_paths(self, paths: list[str]) -> None:
        if not paths:
            return
        with self._write_tx():
            self._write.executemany(
                "DELETE FROM files WHERE path = ?", [(p,) for p in paths]
            )
            self._write.executemany(
                "DELETE FROM file_state WHERE path = ?", [(p,) for p in paths]
            )

    def _relocate_row(self, old: str, new: str) -> None:
        """把行从 old 平移到 new：重算派生字段（含跨盘移动时的 drive），复位 deleted。"""
        self._write.execute(
            "UPDATE files SET path = ?, name = ?, folder = ?, ext = ?, "
            "drive = ?, deleted = 0, deleted_at = NULL WHERE path = ?",
            (
                new,
                Path(new).name,
                str(Path(new).parent),
                Path(new).suffix.lower(),
                (os.path.splitdrive(new)[0] or "").upper(),
                old,
            ),
        )

    def _move_state_locked(
        self,
        src: str,
        dst: str,
        fallback: FileRecord | None,
        when: float,
    ) -> bool:
        state = self._write.execute(
            "SELECT * FROM file_state WHERE path = ?", (src,)
        ).fetchone()
        if state is None:
            if fallback is None:
                return False
            self._observe_record_locked(fallback, event_type="created", occurred_at=when)
            return True

        # 目标若已被 watchdog 先当作 created 记录，先冲销它，再把源状态搬过去。
        # 这样同盘目录移动即使事件乱序，空间净变化仍为 0。
        self._mark_deleted_locked(dst, when)
        category = (
            fallback.category
            if fallback is not None and fallback.category != "unclassified"
            else str(state["category"] or "unclassified")
        )
        size = int(state["size"])
        was_present = bool(state["exists_now"])
        target = FileRecord(
            path=dst,
            name=Path(dst).name,
            ext=Path(dst).suffix.lower(),
            drive=(os.path.splitdrive(dst)[0] or "").upper(),
            folder=str(Path(dst).parent),
            size=size,
            added_at=when,
            category=category,
        )
        source_drive = str(state["drive"] or "")
        if not was_present:
            self._insert_space_event_locked(
                target,
                "recreated",
                0,
                size,
                when,
                old_path=src,
                delta_bytes=size,
            )
        elif source_drive == target.drive:
            self._insert_space_event_locked(
                target,
                "moved",
                size,
                size,
                when,
                old_path=src,
                delta_bytes=0,
            )
        else:
            source = FileRecord(
                path=src,
                name=state["name"],
                ext=state["ext"] or "",
                drive=source_drive,
                folder=state["folder"] or "",
                size=0,
                added_at=when,
                category=category,
            )
            self._insert_space_event_locked(
                source,
                "moved_out",
                size,
                0,
                when,
                old_path=src,
                drive=source_drive,
                delta_bytes=-size,
            )
            self._insert_space_event_locked(
                target,
                "moved_in",
                0,
                size,
                when,
                old_path=src,
                delta_bytes=size,
            )

        self._write.execute("DELETE FROM files WHERE path = ?", (dst,))
        self._write.execute("DELETE FROM file_state WHERE path = ?", (dst,))
        self._relocate_row(src, dst)
        self._write.execute(
            """
            UPDATE file_state SET
                path = ?, name = ?, ext = ?, drive = ?, folder = ?,
                category = ?, exists_now = 1, last_seen_at = ?
            WHERE path = ?
            """,
            (
                target.path,
                target.name,
                target.ext,
                target.drive,
                target.folder,
                category,
                when,
                src,
            ),
        )
        return True

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
            self._move_state_locked(src, dst, fallback, time.time())

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
        esc = _like_escape(prefix)
        with self._write_tx():
            # SELECT 必须和后续 UPDATE 在同一个锁内：_write 连接被多个
            # 后台线程共用，锁外读会与写入并发，读到不一致快照。
            rows = self._write.execute(
                "SELECT path FROM file_state WHERE exists_now = 1 "
                "AND (path = ? OR path LIKE ? ESCAPE '\\')",
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
                self._move_state_locked(old, new, None, time.time())

    def delete_subtree(self, src: str) -> None:
        """目录整体删除：标记状态并保留历史账本。

        子文件各自的 on_deleted 事件并不可靠（网络盘/事件洪峰可能只到
        目录级通知），按前缀删除避免留下磁盘上已不存在的幽灵记录。
        """
        src = src.rstrip("\\/")
        if not src:
            return
        with self._write_tx():
            self._mark_deleted_locked(src, time.time())

    def record_disk_space(
        self,
        samples: list[tuple[str, str, int, int]],
        *,
        sampled_at: float | None = None,
    ) -> None:
        """保存完整采样序列，同时维护旧 UI 使用的每日最新值。"""
        if not samples:
            return
        now = sampled_at if sampled_at is not None else time.time()
        with self._write_tx():
            self._write.executemany(
                """
                INSERT INTO disk_samples (day, drive, free_bytes, total_bytes, sampled_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [(d, dv, f, t, now) for d, dv, f, t in samples],
            )
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
            now = time.time()
            for path, size in sizes.items():
                state = self._write.execute(
                    "SELECT * FROM file_state WHERE path = ? AND exists_now = 1",
                    (path,),
                ).fetchone()
                if state is None:
                    continue
                record = FileRecord(
                    path=path,
                    name=state["name"],
                    ext=state["ext"] or "",
                    drive=state["drive"] or "",
                    folder=state["folder"] or "",
                    size=size,
                    added_at=now,
                    category=state["category"] or "unclassified",
                )
                self._observe_record_locked(
                    record, event_type="modified", occurred_at=now
                )
                self._write.execute(
                    "UPDATE files SET size_final = 1 WHERE path = ?", (path,)
                )
            for path in missing:
                self._mark_deleted_locked(path, now)

    def _delete_in_chunks(
        self, table: str, where: str, args: tuple, chunk: int = 4000
    ) -> int:
        """分批删除，避免单个大事务长时间持有写锁。

        一个大 DELETE 会把写锁按住几秒（几十万行的库上实测 3.5 秒），期间
        补扫的 flush 只能干等——用户点“取消补扫”后取消标志直到锁释放才被
        检查到，界面就卡在“正在取消”。分批后每批只有几十毫秒。
        """
        removed = 0
        while True:
            with self._write_tx():
                cur = self._write.execute(
                    f"DELETE FROM {table} WHERE rowid IN "
                    f"(SELECT rowid FROM {table} WHERE {where} LIMIT ?)",
                    (*args, chunk),
                )
                count = max(0, cur.rowcount)
            removed += count
            if count < chunk:
                return removed

    def purge_older_than(self, days: int) -> int:
        if days <= 0:
            return 0
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        hourly_cutoff = (date.today() - timedelta(days=365)).isoformat() + "T00:00"
        removed_files = self._delete_in_chunks("files", "day < ?", (cutoff,))
        self._delete_in_chunks("space_events", "day < ?", (cutoff,))
        self._delete_in_chunks("disk_samples", "day < ?", (cutoff,))
        self._delete_in_chunks("space_hourly", "hour < ?", (hourly_cutoff,))
        # disk_space 与 space_daily 是长期每日汇总，不随原始保留期删除。
        return removed_files

    def clear_all(self) -> None:
        with self._write_tx():
            self._write.execute("DELETE FROM files")
            self._write.execute("DELETE FROM disk_space")
            self._write.execute("DELETE FROM file_state")
            self._write.execute("DELETE FROM space_events")
            self._write.execute("DELETE FROM disk_samples")
            self._write.execute("DELETE FROM space_hourly")
            self._write.execute("DELETE FROM space_daily")
            # 这里不做 VACUUM：DELETE 只把页还给空闲列表，文件不会立刻变小；
            # 而 VACUUM 要重写整个库并在此期间持有写锁，不能放在 UI 线程。
            # 调用方（设置页「清空所有记录」）会在后台线程里再调 compact()。

    def compact(self) -> int:
        """VACUUM 回收已删除数据占用的空间，返回压缩后的库文件总字节数。

        SQLite 的 DELETE 不会让文件变小（页只是挂回空闲列表），这就是
        「清空所有记录」后体积纹丝不动的原因。VACUUM 会重写整个库，需要
        约等量临时空间、几十万行通常几秒，期间持有写锁；因此只在明确的
        回收动作（清空数据、手动清理过期记录）里由后台线程调用。
        """
        with self._write_lock:
            # VACUUM 不能在事务里执行：先结束可能存在的隐式事务
            self._write.commit()
            self._write.execute("VACUUM")
            # 关键顺序：WAL 模式下 VACUUM 的结果先落在 WAL，主库文件不会
            # 自己变小；必须在 VACUUM 之后再 checkpoint(TRUNCATE) 一次，
            # 空间才真正还给文件系统（实测 40MB 的空库能降到几十 KB）。
            self._write.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._write.commit()
        return self.database_size()

    def database_size(self) -> int:
        """库文件 + WAL + SHM 的总字节数（与设置页显示口径一致）。"""
        total = 0
        for suffix in ("", "-wal", "-shm"):
            try:
                total += Path(str(self.path) + suffix).stat().st_size
            except OSError:
                pass
        return total

    # ---------- 读（UI 主线程，不抢 write 锁）----------

    def space_events(
        self,
        *,
        since: float | None = None,
        until: float | None = None,
        drive: str | None = None,
        category: str | None = None,
        event_type: str | None = None,
        keyword: str | None = None,
        focus_only: bool = False,
        app_key: str | None = None,
        app_sub: str | None = None,
        sort_order: str = "time_desc",
        limit: int = 1000,
        offset: int = 0,
    ) -> list[SpaceEvent]:
        where, args = self._space_event_where(
            since, until, drive, category, event_type, keyword, focus_only,
            app_key, app_sub,
        )
        order_by = {
            "time_desc": "occurred_at DESC, id DESC",
            "time_asc": "occurred_at ASC, id ASC",
            "delta_desc": "delta_bytes DESC, occurred_at DESC, id DESC",
            "delta_asc": "delta_bytes ASC, occurred_at DESC, id DESC",
            "name_asc": "name COLLATE NOCASE ASC, occurred_at DESC, id DESC",
            "name_desc": "name COLLATE NOCASE DESC, occurred_at DESC, id DESC",
            "type_asc": "event_type ASC, occurred_at DESC, id DESC",
            "type_desc": "event_type DESC, occurred_at DESC, id DESC",
            "category_asc": "category ASC, occurred_at DESC, id DESC",
            "category_desc": "category DESC, occurred_at DESC, id DESC",
        }.get(sort_order, "occurred_at DESC, id DESC")
        args.extend((max(1, limit), max(0, offset)))
        rows = self._read.execute(
            "SELECT id, path, old_path, drive, category, event_type, old_size, "
            "new_size, delta_bytes, occurred_at, merged_count FROM space_events WHERE "
            + " AND ".join(where)
            + f" ORDER BY {order_by} LIMIT ? OFFSET ?",
            args,
        ).fetchall()
        return [
            SpaceEvent(
                id=int(r["id"]),
                path=r["path"],
                old_path=r["old_path"],
                drive=r["drive"] or "",
                category=r["category"],
                event_type=r["event_type"],
                old_size=int(r["old_size"]),
                new_size=int(r["new_size"]),
                delta_bytes=int(r["delta_bytes"]),
                occurred_at=float(r["occurred_at"]),
                merged_count=int(r["merged_count"]),
            )
            for r in rows
        ]

    def space_event_count(
        self,
        *,
        since: float | None = None,
        until: float | None = None,
        drive: str | None = None,
        category: str | None = None,
        event_type: str | None = None,
        keyword: str | None = None,
        focus_only: bool = False,
        app_key: str | None = None,
        app_sub: str | None = None,
    ) -> int:
        where, args = self._space_event_where(
            since, until, drive, category, event_type, keyword, focus_only,
            app_key, app_sub,
        )
        row = self._read.execute(
            "SELECT COUNT(*) count FROM space_events WHERE " + " AND ".join(where),
            args,
        ).fetchone()
        return int(row["count"])

    def app_group_rows(
        self,
        *,
        since: float | None = None,
        until: float | None = None,
        drive: str | None = None,
        category: str | None = None,
        event_type: str | None = None,
        keyword: str | None = None,
        focus_only: bool = False,
    ) -> list[tuple[str, str, int, int, int, float]]:
        """按 (app_key, app_sub) 聚合整个筛选结果。

        返回 [(key, sub, 条数, 净变化, 毛变化, 最近时间)]，供按应用分组
        在 Python 侧做产品级合并与均衡拆分（跨页一致，不受分页影响）。
        """
        where, args = self._space_event_where(
            since, until, drive, category, event_type, keyword, focus_only
        )
        rows = self._read.execute(
            "SELECT app_key, app_sub, COUNT(*) AS c, "
            "COALESCE(SUM(delta_bytes), 0) AS net, "
            "COALESCE(SUM(ABS(delta_bytes)), 0) AS gross, "
            "COALESCE(MAX(occurred_at), 0) AS last_at "
            "FROM space_events WHERE " + " AND ".join(where)
            + " GROUP BY app_key, app_sub",
            args,
        ).fetchall()
        return [
            (
                str(r["app_key"]),
                str(r["app_sub"]),
                int(r["c"]),
                int(r["net"]),
                int(r["gross"]),
                float(r["last_at"]),
            )
            for r in rows
        ]

    def app_group_folders(
        self,
        *,
        since: float | None = None,
        until: float | None = None,
        drive: str | None = None,
        category: str | None = None,
        event_type: str | None = None,
        keyword: str | None = None,
        focus_only: bool = False,
    ) -> list[tuple[str, str, str, int]]:
        """按 (app_key, app_sub, folder) 聚合，给分组挑"主要目录"。"""
        where, args = self._space_event_where(
            since, until, drive, category, event_type, keyword, focus_only
        )
        rows = self._read.execute(
            "SELECT app_key, app_sub, folder, COUNT(*) AS c "
            "FROM space_events WHERE " + " AND ".join(where)
            + " GROUP BY app_key, app_sub, folder",
            args,
        ).fetchall()
        return [
            (str(r["app_key"]), str(r["app_sub"]), str(r["folder"] or ""), int(r["c"]))
            for r in rows
        ]

    def file_event_history(self, path: str, *, limit: int = 100) -> list[SpaceEvent]:
        """返回一个文件跨重命名路径的大小变化历史，最新事件在前。"""
        aliases = {path}
        found: dict[int, sqlite3.Row] = {}
        # 一次移动事件保存在新路径行中，并通过 old_path 指向前驱。因此只需
        # 沿 path 索引向后追溯；不要写 `path IN (...) OR old_path IN (...)`，
        # old_path 未索引时会在每次选中详情时全表扫描，直接卡住 UI。
        for _ in range(16):
            placeholders = ",".join("?" for _ in aliases)
            args = [*aliases, max(limit * 2, 100)]
            rows = self._read.execute(
                "SELECT id, path, old_path, drive, category, event_type, "
                "old_size, new_size, delta_bytes, occurred_at, merged_count "
                "FROM space_events "
                f"WHERE path IN ({placeholders}) "
                "ORDER BY occurred_at DESC, id DESC LIMIT ?",
                args,
            ).fetchall()
            before = len(aliases)
            for row in rows:
                found[int(row["id"])] = row
                aliases.add(str(row["path"]))
                if row["old_path"]:
                    aliases.add(str(row["old_path"]))
            if len(aliases) == before:
                break
        ordered = sorted(
            found.values(),
            key=lambda row: (float(row["occurred_at"]), int(row["id"])),
            reverse=True,
        )[: max(1, limit)]
        return [self._space_event_from_row(row) for row in ordered]

    @staticmethod
    def _space_event_from_row(row: sqlite3.Row) -> SpaceEvent:
        return SpaceEvent(
            id=int(row["id"]),
            path=str(row["path"]),
            old_path=row["old_path"],
            drive=str(row["drive"] or ""),
            category=str(row["category"]),
            event_type=str(row["event_type"]),
            old_size=int(row["old_size"]),
            new_size=int(row["new_size"]),
            delta_bytes=int(row["delta_bytes"]),
            occurred_at=float(row["occurred_at"]),
            merged_count=int(row["merged_count"]),
        )

    @staticmethod
    def _space_event_where(
        since: float | None,
        until: float | None,
        drive: str | None,
        category: str | None,
        event_type: str | None,
        keyword: str | None,
        focus_only: bool = False,
        app_key: str | None = None,
        app_sub: str | None = None,
    ) -> tuple[list[str], list[object]]:
        where = ["1 = 1"]
        args: list[object] = []
        for value, clause in ((since, "occurred_at >= ?"), (until, "occurred_at <= ?")):
            if value is not None:
                where.append(clause)
                args.append(value)
        if drive:
            where.append("drive = ?")
            args.append(drive.upper())
        if category:
            where.append("category = ?")
            args.append(category)
        if event_type:
            where.append("event_type = ?")
            args.append(event_type)
        if keyword:
            where.append("path LIKE ? ESCAPE '\\'")
            args.append("%" + _like_escape(keyword) + "%")
        if focus_only:
            where.append("category IN ('user', 'download')")
        if app_key:
            where.append("app_key = ?")
            args.append(app_key)
            if app_sub:
                where.append("app_sub = ?")
                args.append(app_sub)
        return where, args

    def space_event_filter_values(self) -> tuple[list[str], list[str]]:
        drives = [
            str(row["drive"])
            for row in self._read.execute(
                "SELECT DISTINCT drive FROM space_events WHERE drive <> '' ORDER BY drive"
            ).fetchall()
        ]
        categories = [
            str(row["category"])
            for row in self._read.execute(
                "SELECT DISTINCT category FROM space_events ORDER BY category"
            ).fetchall()
        ]
        return drives, categories

    def database_metrics(self) -> dict[str, int | float]:
        """返回设置页需要的体积、写入速率与每日增长估算。"""
        total_bytes = self.database_size()
        total_events = self.space_event_count()
        now = time.time()
        events_24h = self.space_event_count(since=now - 86400)
        events_60s = self.space_event_count(since=now - 60)
        bytes_per_event = total_bytes / max(total_events, 1)
        return {
            "size_bytes": total_bytes,
            "events_24h": events_24h,
            "write_rate": events_60s / 60.0,
            "estimated_daily_bytes": int(bytes_per_event * events_24h),
        }

    def category_space_totals(
        self, since: float, until: float, *, limit: int = 8
    ) -> list[tuple[str, int]]:
        since_day = datetime.fromtimestamp(since).date().isoformat()
        until_day = datetime.fromtimestamp(until).date().isoformat()
        rows = self._read.execute(
            "SELECT category, COALESCE(SUM(net_bytes), 0) delta "
            "FROM space_daily WHERE day >= ? AND day <= ? "
            "GROUP BY category ORDER BY ABS(delta) DESC LIMIT ?",
            (since_day, until_day, max(1, limit)),
        ).fetchall()
        return [(str(row["category"]), int(row["delta"])) for row in rows]

    def space_day_summaries(self, days: int) -> list[SpaceDaySummary]:
        """近 N 天空间账本汇总，按新到旧返回，供概览时间线使用。"""
        cutoff = (date.today() - timedelta(days=max(1, days) - 1)).isoformat()
        rows = self._read.execute(
            "SELECT day, COALESCE(SUM(occupied_bytes), 0) occupied, "
            "COALESCE(SUM(released_bytes), 0) released, "
            "COALESCE(SUM(net_bytes), 0) net, "
            "COALESCE(SUM(event_count), 0) events "
            "FROM space_daily WHERE day >= ? GROUP BY day ORDER BY day DESC LIMIT ?",
            (cutoff, max(1, days)),
        ).fetchall()
        return [
            SpaceDaySummary(
                day=str(row["day"]),
                occupied_bytes=int(row["occupied"]),
                released_bytes=int(row["released"]),
                net_bytes=int(row["net"]),
                event_count=int(row["events"]),
            )
            for row in rows
        ]

    def top_space_folders(
        self, days: int, limit: int = 10
    ) -> list[tuple[str, int, int]]:
        """在原始账本保留窗口内按目录汇总净变化，绝对影响从大到小。"""
        cutoff = (date.today() - timedelta(days=max(1, days) - 1)).isoformat()
        rows = self._read.execute(
            "SELECT folder, COUNT(*) events, COALESCE(SUM(delta_bytes), 0) net "
            "FROM space_events WHERE day >= ? GROUP BY folder "
            "HAVING net <> 0 ORDER BY ABS(net) DESC, events DESC LIMIT ?",
            (cutoff, max(1, limit)),
        ).fetchall()
        return [
            (str(row["folder"]), int(row["events"]), int(row["net"]))
            for row in rows
        ]

    def top_space_files(
        self, days: int, limit: int = 10
    ) -> list[tuple[str, int, int]]:
        """返回时间范围内净占用增长最大的文件。"""
        cutoff = (date.today() - timedelta(days=max(1, days) - 1)).isoformat()
        rows = self._read.execute(
            "SELECT path, COUNT(*) events, COALESCE(SUM(delta_bytes), 0) net "
            "FROM space_events WHERE day >= ? GROUP BY path "
            "HAVING net > 0 ORDER BY net DESC, events DESC LIMIT ?",
            (cutoff, max(1, limit)),
        ).fetchall()
        return [
            (str(row["path"]), int(row["events"]), int(row["net"]))
            for row in rows
        ]

    def disk_samples(
        self,
        *,
        since: float | None = None,
        until: float | None = None,
        drive: str | None = None,
    ) -> list[DiskSample]:
        where = ["1 = 1"]
        args: list[object] = []
        if since is not None:
            where.append("sampled_at >= ?")
            args.append(since)
        if until is not None:
            where.append("sampled_at <= ?")
            args.append(until)
        if drive:
            where.append("drive = ?")
            args.append(drive.upper())
        rows = self._read.execute(
            "SELECT drive, free_bytes, total_bytes, sampled_at FROM disk_samples WHERE "
            + " AND ".join(where)
            + " ORDER BY sampled_at, id",
            args,
        ).fetchall()
        return [
            DiskSample(
                drive=r["drive"],
                free_bytes=int(r["free_bytes"]),
                total_bytes=int(r["total_bytes"]),
                sampled_at=float(r["sampled_at"]),
            )
            for r in rows
        ]

    def attribution_summary(
        self,
        since: float,
        until: float,
        *,
        drive: str | None = None,
    ) -> list[AttributionSummary]:
        """实际占用增加、文件已归因增加及两者差值，按盘返回。"""
        sample_args: list[object] = [since, until]
        event_args: list[object] = [since, until]
        sample_drive = ""
        event_drive = ""
        if drive:
            sample_drive = " AND drive = ?"
            event_drive = " AND drive = ?"
            sample_args.append(drive.upper())
            event_args.append(drive.upper())
        sample_rows = self._read.execute(
            "SELECT drive, free_bytes, sampled_at FROM disk_samples "
            "WHERE sampled_at >= ? AND sampled_at <= ?" + sample_drive
            + " ORDER BY drive, sampled_at, id",
            sample_args,
        ).fetchall()
        endpoints: dict[str, tuple[int, int]] = {}
        for row in sample_rows:
            key = row["drive"]
            free = int(row["free_bytes"])
            if key not in endpoints:
                endpoints[key] = (free, free)
            else:
                endpoints[key] = (endpoints[key][0], free)
        event_rows = self._read.execute(
            "SELECT drive, COALESCE(SUM(delta_bytes), 0) delta FROM space_events "
            "WHERE occurred_at >= ? AND occurred_at <= ?" + event_drive
            + " GROUP BY drive",
            event_args,
        ).fetchall()
        attributed = {r["drive"] or "": int(r["delta"]) for r in event_rows}
        drives = sorted(set(endpoints) | set(attributed))
        result: list[AttributionSummary] = []
        for key in drives:
            first, last = endpoints.get(key, (0, 0))
            actual = first - last if key in endpoints else 0
            known = attributed.get(key, 0)
            result.append(AttributionSummary(key, actual, known, actual - known))
        return result

    def daily_attribution_summary(
        self, since_day: str, until_day: str
    ) -> list[AttributionSummary]:
        """基于长期每日汇总计算看板口径，不依赖已清理的原始事件。"""
        sample_rows = self._read.execute(
            "SELECT day, drive, free_bytes FROM disk_space "
            "WHERE day >= ? AND day <= ? ORDER BY drive, day",
            (since_day, until_day),
        ).fetchall()
        endpoints: dict[str, tuple[int, int]] = {}
        for row in sample_rows:
            key = str(row["drive"])
            free = int(row["free_bytes"])
            if key not in endpoints:
                endpoints[key] = (free, free)
            else:
                endpoints[key] = (endpoints[key][0], free)
        event_rows = self._read.execute(
            "SELECT drive, COALESCE(SUM(net_bytes), 0) delta FROM space_daily "
            "WHERE day >= ? AND day <= ? GROUP BY drive",
            (since_day, until_day),
        ).fetchall()
        attributed = {str(row["drive"]): int(row["delta"]) for row in event_rows}
        drives = sorted(set(endpoints) | set(attributed))
        return [
            AttributionSummary(
                drive,
                endpoints[drive][0] - endpoints[drive][1] if drive in endpoints else 0,
                attributed.get(drive, 0),
                (
                    endpoints[drive][0] - endpoints[drive][1]
                    if drive in endpoints else 0
                ) - attributed.get(drive, 0),
            )
            for drive in drives
        ]

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
        return _query_day_summaries(self._read, limit)

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
            return _query_day_summaries(conn, limit)
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
            where = f"day = ? AND {_event_where(event_type)}"
            args: list = [day]
            if keyword:
                where += " AND (LOWER(name) LIKE ? OR LOWER(folder) LIKE ?)"
                like = f"%{keyword.lower()}%"
                args += [like, like]

            sql = f"SELECT * FROM files WHERE {where} ORDER BY added_at DESC"
            list_args = list(args)
            if limit is not None:
                # 多取一条用于探测截断：恰好 limit 条时不该误报、也不该丢记录
                sql += " LIMIT ?"
                list_args.append(limit + 1)
            records = [
                _row_to_record(r) for r in conn.execute(sql, list_args).fetchall()
            ]
            truncated = limit is not None and len(records) > limit
            if truncated:
                records = records[:limit]  # type: ignore[operator]

            row = conn.execute(
                f"SELECT COUNT(*) c, COALESCE(SUM(size), 0) s FROM files WHERE {where}",
                args,
            ).fetchone()
            count, size = int(row["c"]), int(row["s"])

            day_total = count
            if keyword:
                dt_where = f"day = ? AND {_event_where(event_type)}"
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
    # 循环在 unit == "TB" 时必然返回，此处不可达
    raise AssertionError("unreachable")
