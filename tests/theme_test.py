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
        window.show_overview()
        assert window.pages.currentWidget() is overview
        assert window.btn_overview.isChecked()
    finally:
        window.close()


def test_settings_navigation_has_modern_selection_without_focus_outline() -> None:
    qss = panel_qss(LIGHT_TOKENS)
    assert "QListWidget#settingsNav" in qss
    assert "outline: none" in qss
    assert LIGHT_TOKENS.accent_soft in qss
