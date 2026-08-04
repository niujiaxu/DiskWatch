"""统一错误日志：写文件 + 通知 UI 展示。

设计：
- 单进程单实例 `errorlog`；`QObject` 信号 `error_recorded` 供主线程订阅。
- 文件 handler 用 RotatingFileHandler（1MB × 3 备份），避免日志无限涨。
- 后台线程 / 非 Qt 代码可以安全调用 `errorlog.log_exception(...)`，会记录
  完整 traceback 到磁盘；如果 Qt 已初始化（主线程存活），还会发信号。
- UI 启动时调用一次 `setup_logging(path)` 把 file handler 装上。
"""

from __future__ import annotations

import logging
import sys
import threading
import traceback
from collections import deque
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Optional

from PySide6.QtCore import QObject, Signal

from .config import default_home

LOGGER_NAME = "diskwatch"
LOG_FORMAT = "%(asctime)s %(levelname)-5s %(message)s"
MAX_BYTES = 1 * 1024 * 1024
BACKUP_COUNT = 3
MEMORY_CAP = 200  # UI 里能看到的最近条数

_logger: logging.Logger | None = None
_logger_lock = threading.Lock()


class _ErrorBus(QObject):
    error_recorded = Signal(str, str)  # (level, message)


class ErrorLog:
    """单例：线程安全地记录 + 通过 Qt 信号通知主线程。"""

    def __init__(self) -> None:
        self._bus = _ErrorBus()
        self._memory: deque[tuple[str, str]] = deque(maxlen=MEMORY_CAP)
        self._lock = threading.Lock()

    @property
    def bus(self) -> _ErrorBus:
        return self._bus

    def log(self, level: int, msg: str, exc: BaseException | None = None) -> None:
        """记录一条日志。若给了 exc，附加 traceback。"""
        logger = _logger or logging.getLogger(LOGGER_NAME)
        if exc is not None:
            logger.log(level, "%s\n%s", msg, "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ))
        else:
            logger.log(level, msg)
        # 内存里只保留纯文本（够 UI 展示），traceback 太长不进内存
        with self._lock:
            self._memory.append((logging.getLevelName(level), msg))
        self._bus.error_recorded.emit(logging.getLevelName(level), msg)

    def debug(self, msg: str) -> None:
        self.log(logging.DEBUG, msg)

    def info(self, msg: str) -> None:
        self.log(logging.INFO, msg)

    def warning(self, msg: str, exc: BaseException | None = None) -> None:
        self.log(logging.WARNING, msg, exc)

    def error(self, msg: str, exc: BaseException | None = None) -> None:
        self.log(logging.ERROR, msg, exc)

    def log_exception(self, where: str, exc: BaseException) -> None:
        """统一接口：发生异常时调用，记录到日志 + 内存 + 信号。"""
        self.error(f"{where}: {type(exc).__name__}: {exc}", exc)

    def recent(self, n: int = 50) -> list[tuple[str, str]]:
        """最近 n 条 (level, message)，新到旧。"""
        with self._lock:
            return list(self._memory)[-n:][::-1]

    def count(self) -> int:
        with self._lock:
            return len(self._memory)


errorlog = ErrorLog()


def setup_logging(path: Optional[str] = None, *, level: int = logging.INFO) -> None:
    """挂 file handler + 控制台 handler。重复调用幂等。"""
    global _logger
    with _logger_lock:
        if _logger is not None:
            return
        logger = logging.getLogger(LOGGER_NAME)
        logger.setLevel(level)
        logger.propagate = False

        formatter = logging.Formatter(LOG_FORMAT)

        if path is None:
            path = str(default_home() / "diskwatch.log")
        try:
            fh = RotatingFileHandler(
                path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
            )
            fh.setFormatter(formatter)
            logger.addHandler(fh)
        except OSError:
            # 日志目录写不了（比如首次启动且 APPDATA 不可写）：跳过文件 handler
            pass

        if sys.stderr is not None:
            ch = logging.StreamHandler(sys.stderr)
            ch.setFormatter(formatter)
            logger.addHandler(ch)

        _logger = logger
        logger.info("logging started at %s", datetime.now().isoformat(timespec="seconds"))
