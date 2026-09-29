"""浅色、深色和运行时主题切换。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QWidget

from diskwatch.ui.style import (
    DARK_TOKENS,
    LIGHT_TOKENS,
    install_theme,
    panel_qss,
    theme_tokens,
)


class _ThemeProbe(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.applied = 0

    def apply_theme(self) -> None:
        self.applied += 1


def test_light_and_dark_tokens_have_distinct_semantic_colors() -> None:
    assert LIGHT_TOKENS.window != DARK_TOKENS.window
    assert LIGHT_TOKENS.text != DARK_TOKENS.text
    assert LIGHT_TOKENS.accent == "#1677ff"
    assert DARK_TOKENS.accent == "#4b95ff"


def test_runtime_theme_switch_refreshes_open_windows(qapp) -> None:
    probe = _ThemeProbe()
    probe.show()
    try:
        controller = install_theme(qapp, "light")
        qapp.processEvents()
        light_count = probe.applied
        assert theme_tokens().name == "light"
        assert qapp.palette().color(QPalette.Window).name() == LIGHT_TOKENS.window
        assert LIGHT_TOKENS.window in panel_qss()

        controller.set_mode("dark")
        qapp.processEvents()
        assert probe.applied > light_count
        assert theme_tokens().name == "dark"
        assert qapp.palette().color(QPalette.Window).name() == DARK_TOKENS.window
        assert DARK_TOKENS.window in panel_qss()
    finally:
        probe.close()


def test_invalid_theme_mode_falls_back_to_system(qapp) -> None:
    controller = install_theme(qapp, "unknown")
    assert controller.mode == "system"
    assert theme_tokens().name in {"light", "dark"}


def test_main_window_unifies_overview_and_activity(qapp) -> None:
    from diskwatch.ui.main_window import MainWindow

    overview = _ThemeProbe()
    activity = _ThemeProbe()
    window = MainWindow(overview, activity)
    try:
        assert window.windowFlags() & Qt.FramelessWindowHint
        assert window.title_bar.height() == 44
        assert window.title_bar.btn_close.focusPolicy() == Qt.NoFocus
        assert window.pages.count() == 2
        window.show_activity()
        qapp.processEvents()
        assert window.pages.currentWidget() is activity
        assert window.btn_activity.isChecked()
        window.showMaximized()
        qapp.processEvents()
        assert window.isMaximized()
        window.show_activity()
        qapp.processEvents()
        assert window.isMaximized()
        window.show_overview()
        assert window.pages.currentWidget() is overview
        assert window.btn_overview.isChecked()
    finally:
        window.close()


def test_theme_apply_skips_frameless_floating_windows(qapp, monkeypatch) -> None:
    """主题重应用时不得给无边框悬浮窗设置 DWM 圆角/阴影（否则四角出现圆弧）。"""
    from diskwatch.ui import style

    touched: list[QWidget] = []
    monkeypatch.setattr(
        style, "enable_titlebar", lambda w, dark=None: touched.append(w)
    )

    controller = install_theme(qapp, "light")
    framed = _ThemeProbe()
    framed.show()
    frameless = _ThemeProbe()
    frameless.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)
    frameless.show()
    try:
        touched.clear()
        controller.set_mode("dark")
        qapp.processEvents()
        assert framed in touched
        assert frameless not in touched
    finally:
        framed.close()
        frameless.close()


def test_floating_windows_disable_system_shadow(qapp, monkeypatch, tmp_path) -> None:
    """悬浮卡片/迷你球必须带 NoDropShadowWindowHint，避免矩形轮廓的系统阴影。"""
    import diskwatch.config as cfg
    from diskwatch.storage import Storage
    from diskwatch.ui.ball import MiniBall
    from diskwatch.ui.widget import FloatingWidget
    from diskwatch.watcher import FileMonitor

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(cfg.paths, "config", home / "config.json")
    monkeypatch.setattr(cfg.paths, "db", home / "diskwatch.db")
    monkeypatch.setattr(cfg, "default_home", lambda: home)
    monkeypatch.setattr(cfg, "location_file", lambda: home / "location.json")

    config = cfg.Config()
    storage = Storage(home / "t.db")
    try:
        monitor = FileMonitor(config, storage)
        for surface in (
            FloatingWidget(storage, monitor, config),
            MiniBall(storage, monitor, config),
        ):
            flags = surface.windowFlags()
            assert flags & Qt.FramelessWindowHint
            assert flags & Qt.NoDropShadowWindowHint
    finally:
        storage.close()


def test_main_window_resize_hit_codes() -> None:
    """无边框窗口的边缘/四角要映射到正确的 Windows 缩放命中码。"""
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QWidget

    from diskwatch.ui.main_window import MainWindow

    window = MainWindow(QWidget(), QWidget())
    window.resize(800, 600)
    try:
        assert window._resize_hit(QPoint(1, 300)) == 10    # HTLEFT
        assert window._resize_hit(QPoint(799, 300)) == 11  # HTRIGHT
        assert window._resize_hit(QPoint(400, 1)) == 12    # HTTOP
        assert window._resize_hit(QPoint(400, 599)) == 15  # HTBOTTOM
        assert window._resize_hit(QPoint(1, 1)) == 13      # HTTOPLEFT
        assert window._resize_hit(QPoint(799, 1)) == 14    # HTTOPRIGHT
        assert window._resize_hit(QPoint(1, 599)) == 16    # HTBOTTOMLEFT
        assert window._resize_hit(QPoint(799, 599)) == 17  # HTBOTTOMRIGHT
        assert window._resize_hit(QPoint(400, 300)) is None
    finally:
        window.close()


def test_main_window_native_resize_is_safe_to_call(qapp) -> None:
    """_enable_native_resize 必须幂等且不抛异常（真实样式在 Windows 生效）。"""
    from PySide6.QtWidgets import QWidget

    from diskwatch.ui.main_window import MainWindow

    window = MainWindow(QWidget(), QWidget())
    window.show()
    try:
        qapp.processEvents()
        window._enable_native_resize()  # showEvent 里已调过一次，重复调用应无害
        window._enable_native_resize()
    finally:
        window.close()


def test_edge_cursor_filter_handles_resize_hits(qapp) -> None:
    """边缘缩放光标过滤器：Qt 处理之前拦截 WM_SETCURSOR 上的缩放命中码。"""
    import ctypes
    from ctypes import wintypes

    from PySide6.QtWidgets import QWidget

    from diskwatch.ui.main_window import MainWindow

    window = MainWindow(QWidget(), QWidget())
    window.resize(800, 600)
    window.show()
    qapp.processEvents()
    window._sync_edge_filter()
    try:
        assert window._edge_filter is not None
        msg = wintypes.MSG()
        msg.hWnd = wintypes.HWND(int(window.winId()))
        msg.message = 0x0020  # WM_SETCURSOR
        msg.lParam = (0x0200 << 16) | 10  # WM_MOUSEMOVE + HTLEFT
        handled, _ = window._edge_filter.nativeEventFilter(
            b"windows_generic_MSG", ctypes.addressof(msg)
        )
        assert handled is True, "缩放命中码应被过滤器接管（否则会被子控件光标覆盖）"

        msg.message = 0x0005  # WM_SIZE：与光标无关的消息不得拦截
        handled, _ = window._edge_filter.nativeEventFilter(
            b"windows_generic_MSG", ctypes.addressof(msg)
        )
        assert handled is False
    finally:
        window.close()


def test_edge_press_filter_maps_window_edges() -> None:
    """边缘按下兜底：按位置映射到对应的边/角（用于 startSystemResize）。"""
    from PySide6.QtCore import QPoint

    from diskwatch.ui.main_window import _EdgePressFilter

    width, height = 800, 600
    edges = _EdgePressFilter.edges_for
    assert edges(QPoint(2, 300), width, height) == Qt.LeftEdge
    assert edges(QPoint(799, 300), width, height) == Qt.RightEdge
    assert edges(QPoint(400, 2), width, height) == Qt.TopEdge
    assert edges(QPoint(400, 599), width, height) == Qt.BottomEdge
    assert edges(QPoint(2, 2), width, height) == (Qt.LeftEdge | Qt.TopEdge)
    assert edges(QPoint(799, 599), width, height) == (Qt.RightEdge | Qt.BottomEdge)
    assert edges(QPoint(400, 300), width, height) == Qt.Edge(0)


def test_settings_navigation_has_modern_selection_without_focus_outline() -> None:
    qss = panel_qss(LIGHT_TOKENS)
    assert "QListWidget#settingsNav" in qss
    assert "outline: none" in qss
    assert LIGHT_TOKENS.accent_soft in qss
