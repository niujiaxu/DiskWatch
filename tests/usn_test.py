"""USN 二进制解析、动作合并和游标恢复测试。"""

from __future__ import annotations

import struct
import threading
import time

from diskwatch.storage import Storage
from diskwatch.usn import (
    FILE_ATTRIBUTE_DIRECTORY,
    USN_REASON_DATA_EXTEND,
    USN_REASON_FILE_CREATE,
    USN_REASON_FILE_DELETE,
    USN_REASON_RENAME_NEW_NAME,
    USN_REASON_RENAME_OLD_NAME,
    UsnCursor,
    UsnReadResult,
    UsnRecord,
    UsnUnavailable,
    coalesce_usn_records,
    parse_usn_buffer,
    recover_from_usn,
)


def _binary_record(name: str, reason: int, *, attributes: int = 0) -> bytes:
    encoded = name.encode("utf-16-le")
    length = 60 + len(encoded)
    filetime = (1_700_000_000 + 11_644_473_600) * 10_000_000
    header = struct.pack(
        "<IHHQQqqIIIIHH",
        length, 2, 0, 10, 20, 30, filetime, reason, 0, 0,
        attributes, len(encoded), 60,
    )
    return header + encoded


def test_parse_usn_buffer() -> None:
    payload = struct.pack("<q", 99) + _binary_record(
        "hello.txt", USN_REASON_FILE_CREATE
    ) + _binary_record("folder", USN_REASON_FILE_DELETE, attributes=FILE_ATTRIBUTE_DIRECTORY)
    next_usn, records = parse_usn_buffer(payload)
    assert next_usn == 99
    assert [record.name for record in records] == ["hello.txt", "folder"]
    assert records[0].occurred_at == 1_700_000_000
    assert records[1].is_dir


def test_coalesce_usn_rename_and_repeated_modify() -> None:
    now = time.time()
    records = [
        UsnRecord(1, 9, "old.bin", USN_REASON_RENAME_OLD_NAME, 1, now),
        UsnRecord(1, 9, "new.bin", USN_REASON_RENAME_NEW_NAME, 2, now + 1),
        UsnRecord(2, 9, "grow.bin", USN_REASON_DATA_EXTEND, 3, now + 2),
        UsnRecord(2, 9, "grow.bin", USN_REASON_DATA_EXTEND, 4, now + 3),
    ]
    actions = coalesce_usn_records(records, lambda _ref: r"C:\data")
    assert len(actions) == 2
    assert actions[0].kind == "move"
    assert actions[0].old_path == r"C:\data\old.bin"
    assert actions[0].path == r"C:\data\new.bin"
    assert actions[1].kind == "modified"
    assert actions[1].occurred_at == now + 3


class _Config:
    def __init__(self, drive: str) -> None:
        self.data = {
            "capture_mode": "all",
            "usn_cursors": {drive: {"journal_id": 7, "next_usn": 10}},
        }
        self.saved = False

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value

    def save(self) -> None:
        self.saved = True


class _Journal:
    def __init__(self, parent: str) -> None:
        self.parent = parent

    def query(self, _drive: str):
        return UsnCursor(7, 30), 1

    def read(self, _drive: str, _cursor: UsnCursor, **_kwargs):
        record = UsnRecord(
            1, 2, "offline.bin", USN_REASON_FILE_CREATE, 20, time.time()
        )
        return UsnReadResult(UsnCursor(7, 30), [record], lambda _ref: self.parent)


def test_recover_from_usn_updates_cursor_and_ledger(tmp_path) -> None:
    file_path = tmp_path / "offline.bin"
    file_path.write_bytes(b"offline")
    storage = Storage(tmp_path / "usn.db")
    drive = file_path.drive.upper()
    config = _Config(drive)
    try:
        report = recover_from_usn(
            config, storage, [str(tmp_path)], journal=_Journal(str(tmp_path))
        )
        assert report.processed == 1
        assert report.skipped == 0
        assert config.saved
        assert config.data["usn_cursors"][drive]["next_usn"] == 30
        events = storage.space_events()
        assert len(events) == 1 and events[0].path.endswith("offline.bin")
    finally:
        storage.close()


def test_query_failure_does_not_persist_fake_zero_cursor(tmp_path) -> None:
    class FailingJournal:
        def query(self, _drive: str):
            raise UsnUnavailable("permission denied")

    storage = Storage(tmp_path / "failed-usn.db")
    config = _Config("C:")
    try:
        report = recover_from_usn(
            config, storage, [r"C:\Users\me"], journal=FailingJournal()
        )
        assert report.fallback_roots == (r"C:\Users\me",)
        assert "C:" not in config.data["usn_cursors"]
        assert config.saved
    finally:
        storage.close()


def test_recover_from_usn_cancel_stops_and_keeps_cursor(tmp_path) -> None:
    """取消要能立刻中断恢复，且不推进游标（下次启动重放，避免丢数据）。"""
    file_path = tmp_path / "offline.bin"
    file_path.write_bytes(b"offline")
    storage = Storage(tmp_path / "usn-cancel.db")
    drive = file_path.drive.upper()
    config = _Config(drive)
    cancel = threading.Event()

    class CancelDuringRead(_Journal):
        def read(self, drive_: str, cursor, **kwargs):
            result = super().read(drive_, cursor, **kwargs)
            cancel.set()  # 读完就取消，动作循环必须立刻退出
            return result

    try:
        report = recover_from_usn(
            config,
            storage,
            [str(tmp_path)],
            journal=CancelDuringRead(str(tmp_path)),
            cancel_event=cancel,
        )
        assert report.cancelled is True
        assert report.processed == 0
        # 游标保持原值：本次没处理完，下次重放（对已有行幂等）
        assert config.data["usn_cursors"][drive]["next_usn"] == 10
        assert storage.space_events() == []
        assert config.saved
    finally:
        storage.close()
