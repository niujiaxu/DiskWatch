"""Windows NTFS USN Change Journal 启动恢复。

只读取文件系统元数据，不读取文件内容。非 Windows、非 NTFS、权限不足、
Journal 被重建或游标过期时返回明确状态，由调用方决定是否目录补扫。
"""

from __future__ import annotations

import ctypes
import os
import struct
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .classification import CapturePolicy, FileClassifier
from .config import Config
from .filters import safe_stat
from .storage import FileRecord, Storage, make_record

FSCTL_QUERY_USN_JOURNAL = 0x000900F4
FSCTL_READ_USN_JOURNAL = 0x000900BB
USN_REASON_DATA_OVERWRITE = 0x00000001
USN_REASON_DATA_EXTEND = 0x00000002
USN_REASON_DATA_TRUNCATION = 0x00000004
USN_REASON_FILE_CREATE = 0x00000100
USN_REASON_FILE_DELETE = 0x00000200
USN_REASON_RENAME_OLD_NAME = 0x00001000
USN_REASON_RENAME_NEW_NAME = 0x00002000
USN_REASON_CLOSE = 0x80000000
FILE_ATTRIBUTE_DIRECTORY = 0x10
_DATA_REASONS = (
    USN_REASON_DATA_OVERWRITE | USN_REASON_DATA_EXTEND | USN_REASON_DATA_TRUNCATION
)


class UsnUnavailable(RuntimeError):
    """Journal 不可用；message 可直接用于诊断界面。"""


@dataclass(frozen=True)
class UsnCursor:
    journal_id: int
    next_usn: int


@dataclass(frozen=True)
class UsnRecord:
    file_ref: int
    parent_ref: int
    name: str
    reason: int
    usn: int
    occurred_at: float
    is_dir: bool = False


@dataclass(frozen=True)
class RecoveryAction:
    kind: str
    path: str
    occurred_at: float
    old_path: str | None = None
    is_dir: bool = False


@dataclass(frozen=True)
class UsnReadResult:
    cursor: UsnCursor
    records: list[UsnRecord]
    resolve_parent: Callable[[int], str | None]


@dataclass(frozen=True)
class RecoveryReport:
    status: str
    processed: int = 0
    skipped: int = 0
    fallback_roots: tuple[str, ...] = ()
    cancelled: bool = False


def parse_usn_buffer(data: bytes) -> tuple[int, list[UsnRecord]]:
    """解析 DeviceIoControl 返回块；独立函数便于无 Windows 设备单测。"""
    if len(data) < 8:
        raise UsnUnavailable("USN 返回数据过短")
    next_usn = struct.unpack_from("<q", data, 0)[0]
    records: list[UsnRecord] = []
    offset = 8
    header = struct.Struct("<IHHQQqqIIIIHH")
    while offset + header.size <= len(data):
        (
            length,
            major,
            _minor,
            file_ref,
            parent_ref,
            usn,
            filetime,
            reason,
            _source,
            _security,
            attributes,
            name_length,
            name_offset,
        ) = header.unpack_from(data, offset)
        if length < header.size or offset + length > len(data):
            break
        if major == 2 and name_offset + name_length <= length:
            start = offset + name_offset
            name = data[start : start + name_length].decode("utf-16-le", errors="replace")
            occurred_at = max(0.0, filetime / 10_000_000 - 11_644_473_600)
            records.append(
                UsnRecord(
                    file_ref=file_ref,
                    parent_ref=parent_ref,
                    name=name,
                    reason=reason,
                    usn=usn,
                    occurred_at=occurred_at,
                    is_dir=bool(attributes & FILE_ATTRIBUTE_DIRECTORY),
                )
            )
        offset += length
    return next_usn, records


def coalesce_usn_records(
    records: list[UsnRecord], resolve_parent: Callable[[int], str | None]
) -> list[RecoveryAction]:
    """把 Journal 原始 reason 合并成创建、修改、删除和移动动作。"""
    old_names: dict[int, tuple[str, UsnRecord]] = {}
    actions: list[RecoveryAction] = []
    last_observe: dict[str, int] = {}

    def full_path(record: UsnRecord) -> str | None:
        parent = resolve_parent(record.parent_ref)
        return str(Path(parent) / record.name) if parent else None

    for record in sorted(records, key=lambda item: item.usn):
        path = full_path(record)
        if not path:
            continue
        reason = record.reason
        if reason & USN_REASON_RENAME_OLD_NAME:
            old_names[record.file_ref] = (path, record)
            continue
        if reason & USN_REASON_RENAME_NEW_NAME:
            old = old_names.pop(record.file_ref, None)
            if old is not None:
                actions.append(
                    RecoveryAction(
                        "move", path, record.occurred_at,
                        old_path=old[0], is_dir=record.is_dir,
                    )
                )
            else:
                actions.append(
                    RecoveryAction("created", path, record.occurred_at, is_dir=record.is_dir)
                )
            continue
        if reason & USN_REASON_FILE_DELETE:
            actions.append(
                RecoveryAction("deleted", path, record.occurred_at, is_dir=record.is_dir)
            )
            continue
        if reason & (USN_REASON_FILE_CREATE | _DATA_REASONS):
            kind = "created" if reason & USN_REASON_FILE_CREATE else "modified"
            action = RecoveryAction(kind, path, record.occurred_at, is_dir=record.is_dir)
            previous = last_observe.get(path.lower())
            if previous is None:
                last_observe[path.lower()] = len(actions)
                actions.append(action)
            else:
                actions[previous] = action
    return actions


class WindowsUsnJournal:
    """Win32 DeviceIoControl 的窄封装。"""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise UsnUnavailable("当前平台不支持 NTFS USN")
        from ctypes import wintypes

        self.wintypes = wintypes
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.CreateFileW.restype = wintypes.HANDLE
        self.kernel32.OpenFileById.restype = wintypes.HANDLE
        self.kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        self.kernel32.DeviceIoControl.restype = wintypes.BOOL

    @staticmethod
    def _drive_name(drive: str) -> str:
        value = drive.rstrip("\\/").upper()
        if len(value) == 1:
            value += ":"
        return value

    def _filesystem(self, drive: str) -> str:
        fs = ctypes.create_unicode_buffer(32)
        root = self._drive_name(drive) + "\\"
        ok = self.kernel32.GetVolumeInformationW(
            root, None, 0, None, None, None, fs, len(fs)
        )
        if not ok:
            raise UsnUnavailable(f"无法读取 {root} 文件系统: {ctypes.get_last_error()}")
        return fs.value.upper()

    def _open_volume(self, drive: str):
        if self._filesystem(drive) != "NTFS":
            raise UsnUnavailable(f"{self._drive_name(drive)} 不是 NTFS")
        handle = self.kernel32.CreateFileW(
            rf"\\.\{self._drive_name(drive)}",
            0x80000000,
            0x00000001 | 0x00000002 | 0x00000004,
            None,
            3,
            0x02000000,
            None,
        )
        if handle in (None, ctypes.c_void_p(-1).value):
            raise UsnUnavailable(
                f"无法打开 {self._drive_name(drive)} USN Journal: {ctypes.get_last_error()}"
            )
        return handle

    def _device_io(self, handle, code: int, input_value, output_size: int) -> bytes:
        output = ctypes.create_string_buffer(output_size)
        returned = self.wintypes.DWORD()
        input_ptr = ctypes.byref(input_value) if input_value is not None else None
        input_size = ctypes.sizeof(input_value) if input_value is not None else 0
        ok = self.kernel32.DeviceIoControl(
            handle, code, input_ptr, input_size, output, output_size,
            ctypes.byref(returned), None,
        )
        if not ok:
            raise UsnUnavailable(f"DeviceIoControl 失败: {ctypes.get_last_error()}")
        return output.raw[: returned.value]

    def query(self, drive: str) -> tuple[UsnCursor, int]:
        handle = self._open_volume(drive)
        try:
            data = self._device_io(handle, FSCTL_QUERY_USN_JOURNAL, None, 80)
            if len(data) < 32:
                raise UsnUnavailable("USN Journal 状态数据不完整")
            journal_id, first_usn, next_usn, lowest_valid = struct.unpack_from(
                "<Qqqq", data, 0
            )
            return UsnCursor(journal_id, next_usn), max(first_usn, lowest_valid)
        finally:
            self.kernel32.CloseHandle(handle)

    def read(
        self,
        drive: str,
        cursor: UsnCursor,
        limit: int = 100_000,
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> UsnReadResult:
        from ctypes import wintypes

        class ReadData(ctypes.Structure):
            _fields_ = [
                ("StartUsn", ctypes.c_longlong),
                ("ReasonMask", wintypes.DWORD),
                ("ReturnOnlyOnClose", wintypes.DWORD),
                ("Timeout", ctypes.c_ulonglong),
                ("BytesToWaitFor", ctypes.c_ulonglong),
                ("UsnJournalID", ctypes.c_ulonglong),
            ]

        current, lowest = self.query(drive)
        if current.journal_id != cursor.journal_id or cursor.next_usn < lowest:
            raise UsnUnavailable("USN Journal 已重建或游标已过期")
        handle = self._open_volume(drive)
        parent_cache: dict[int, str | None] = {}

        def resolve_parent(file_ref: int) -> str | None:
            if file_ref not in parent_cache:
                parent_cache[file_ref] = self._path_for_id(handle, file_ref)
            return parent_cache[file_ref]

        records: list[UsnRecord] = []
        start = cursor.next_usn
        try:
            while start < current.next_usn and len(records) < limit:
                if should_cancel is not None and should_cancel():
                    break
                request = ReadData(
                    start,
                    0xFFFFFFFF,
                    0,
                    0,
                    0,
                    cursor.journal_id,
                )
                data = self._device_io(handle, FSCTL_READ_USN_JOURNAL, request, 1024 * 1024)
                next_usn, batch = parse_usn_buffer(data)
                records.extend(batch[: max(0, limit - len(records))])
                if next_usn <= start:
                    break
                start = next_usn
            # resolver 持有 volume handle，只能在返回前把父路径解析完并固化。
            # 取消时不必解析剩余的父路径（未解析的记录会被上层跳过）。
            parent_ids = {record.parent_ref for record in records}
            for parent_id in parent_ids:
                if should_cancel is not None and should_cancel():
                    break
                resolve_parent(parent_id)
            snapshot = dict(parent_cache)
        finally:
            self.kernel32.CloseHandle(handle)
        return UsnReadResult(
            UsnCursor(cursor.journal_id, start),
            records,
            snapshot.get,
        )

    def _path_for_id(self, volume, file_ref: int) -> str | None:
        class FileIdDescriptor(ctypes.Structure):
            _fields_ = [
                ("dwSize", ctypes.c_ulong),
                ("Type", ctypes.c_int),
                ("FileId", ctypes.c_longlong),
            ]

        desc = FileIdDescriptor(ctypes.sizeof(FileIdDescriptor), 0, file_ref)
        handle = self.kernel32.OpenFileById(
            volume,
            ctypes.byref(desc),
            0x80,
            0x00000001 | 0x00000002 | 0x00000004,
            None,
            0x02000000,
        )
        if handle in (None, ctypes.c_void_p(-1).value):
            return None
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            length = self.kernel32.GetFinalPathNameByHandleW(
                handle, buffer, len(buffer), 0
            )
            if not length or length >= len(buffer):
                return None
            return buffer.value.removeprefix("\\\\?\\")
        finally:
            self.kernel32.CloseHandle(handle)


def recover_from_usn(
    config: Config,
    storage: Storage,
    roots: list[str],
    *,
    journal: WindowsUsnJournal | None = None,
    cancel_event: threading.Event | None = None,
) -> RecoveryReport:
    """读取各 NTFS 盘离线变化并写入现有空间账本。

    cancel_event 置位时尽快返回；被打断的磁盘不推进游标，下次启动会
    重新读取并重放（重放对已有行只产生 0 字节增量，幂等），避免丢数据。
    """

    def cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    drives = sorted(
        {
            os.path.splitdrive(root)[0].upper()
            for root in roots
            if os.path.splitdrive(root)[0]
        }
    )
    if not drives:
        return RecoveryReport("没有可恢复的本地磁盘", fallback_roots=tuple(roots))
    try:
        source = journal or WindowsUsnJournal()
    except UsnUnavailable as exc:
        return RecoveryReport(str(exc), fallback_roots=tuple(roots))

    stored = config.get("usn_cursors", {})
    cursors = dict(stored) if isinstance(stored, dict) else {}
    policy = CapturePolicy(config, storage_path=storage.path)
    classifier = FileClassifier()
    processed = 0
    skipped = 0
    fallback: list[str] = []
    statuses: list[str] = []
    was_cancelled = False
    # 连续的新增/修改攒批写入：一次事务写数百条，比逐条 add_files 快得多
    pending: list[tuple[FileRecord, str, float]] = []

    def flush_pending() -> None:
        if pending:
            storage.add_events_batch(pending)
            pending.clear()

    for drive in drives:
        if cancelled():
            was_cancelled = True
            break
        saved = cursors.get(drive)
        current: UsnCursor | None = None
        try:
            current, _lowest = source.query(drive)
            if not isinstance(saved, dict):
                cursors[drive] = {
                    "journal_id": current.journal_id,
                    "next_usn": current.next_usn,
                }
                statuses.append(f"{drive} 已建立 USN 基线")
                continue
            cursor = UsnCursor(int(saved["journal_id"]), int(saved["next_usn"]))
            result = source.read(drive, cursor, should_cancel=cancelled)
            actions = coalesce_usn_records(result.records, result.resolve_parent)
            interrupted = False
            for action in actions:
                if cancelled():
                    interrupted = True
                    break
                if action.kind == "deleted":
                    # 结构性操作前先把攒批落库，保持事件先后顺序
                    flush_pending()
                    if action.is_dir:
                        storage.delete_subtree(action.path)
                    else:
                        storage.mark_deleted([action.path], deleted_at=action.occurred_at)
                    processed += 1
                    continue
                if action.kind == "move" and action.old_path:
                    flush_pending()
                    if action.is_dir:
                        storage.move_subtree(action.old_path, action.path)
                    else:
                        st = safe_stat(action.path)
                        fallback_record = None
                        if (
                            policy.accepts_path(action.path)
                            and policy.is_candidate(st)
                            and st is not None
                        ):
                            fallback_record = make_record(
                                action.path, st.st_size, action.occurred_at,
                                classifier.classify(action.path),
                            )
                        storage.move_file(action.old_path, action.path, fallback_record)
                    processed += 1
                    continue
                if action.is_dir or not policy.accepts_path(action.path):
                    skipped += 1
                    continue
                st = safe_stat(action.path)
                if st is None or not policy.is_candidate(st) or not policy.meets_size(st.st_size):
                    skipped += 1
                    continue
                record = make_record(
                    action.path, st.st_size, action.occurred_at,
                    classifier.classify(action.path),
                )
                pending.append((record, action.kind, action.occurred_at))
                processed += 1
                if len(pending) >= 200:
                    flush_pending()
            flush_pending()
            if interrupted or cancelled():
                # 不推进游标：本次没处理完，下次启动重放（幂等）
                was_cancelled = True
                continue
            cursors[drive] = {
                "journal_id": result.cursor.journal_id,
                "next_usn": result.cursor.next_usn,
            }
            statuses.append(f"{drive} USN 恢复 {len(actions)} 项")
        except (UsnUnavailable, KeyError, TypeError, ValueError) as exc:
            statuses.append(f"{drive} {exc}")
            fallback.extend(root for root in roots if root.upper().startswith(drive))
            if current is not None:
                # 游标过期或 Journal 重建：本次无法补齐历史，但可从当前点重建基线。
                cursors[drive] = {
                    "journal_id": current.journal_id,
                    "next_usn": current.next_usn,
                }
            else:
                # 查询本身失败时不要写入 0/0 伪游标；下次恢复应重新建立基线。
                cursors.pop(drive, None)

    flush_pending()
    config.set("usn_cursors", cursors)
    config.set("last_usn_status", "；".join(statuses))
    config.save()
    return RecoveryReport(
        "；".join(statuses), processed, skipped, tuple(fallback), was_cancelled
    )
