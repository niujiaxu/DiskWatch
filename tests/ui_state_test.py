"""验证卡片 / 迷你球 / 隐藏 三种状态的切换逻辑。"""

import pytest

from diskwatch.ui.style import apply_dark_theme


@pytest.fixture
def dw_app(qapp, monkeypatch, tmp_path):
    """构造完整应用，先钉死起点状态，结束后恢复用户配置。"""
    from diskwatch.app import DiskWatchApp
    from diskwatch.config import Config

    apply_dark_theme(qapp)
    import diskwatch.config as cfgmod

    monkeypatch.setattr(cfgmod.paths, "config", tmp_path / "cfg.json")
    monkeypatch.setattr(cfgmod.paths, "db", tmp_path / "t.db")

    seed = Config()
    original = {
        k: seed.get(k) for k in ("collapsed", "widget_visible", "start_minimized", "scan_on_startup")
    }
    seed.update(
        {
            "collapsed": False,
            "widget_visible": True,
            "start_minimized": False,
            "scan_on_startup": False,
        }
    )
    seed.save()

    app = DiskWatchApp(qapp)
    qapp.processEvents()
    yield app

    try:
        app.monitor.stop()
        app.storage.close()
        app.tray.hide()
        app.config.update(original)
        app.config.save()
    except Exception:
        pass


def state(app) -> str:
    return f"card={app.widget.isVisible()} ball={app.ball.isVisible()}"


def test_initial_card_state(dw_app, qapp) -> None:
    assert dw_app.widget.isVisible(), state(dw_app)
    assert not dw_app.ball.isVisible()


def test_collapse_to_ball(dw_app, qapp) -> None:
    dw_app.collapse()
    qapp.processEvents()
    assert dw_app.ball.isVisible(), state(dw_app)
    assert not dw_app.widget.isVisible()
    assert dw_app.config.get("collapsed") is True
    assert dw_app.act_ball.isChecked() and dw_app.act_widget.isChecked()


def test_collapse_idempotent(dw_app, qapp) -> None:
    dw_app.collapse()
    qapp.processEvents()
    dw_app.collapse()
    qapp.processEvents()
    assert dw_app.ball.isVisible() and not dw_app.widget.isVisible(), state(dw_app)


def test_expand_from_ball(dw_app, qapp) -> None:
    dw_app.collapse()
    qapp.processEvents()
    dw_app.ball.expand_requested.emit()
    qapp.processEvents()
    assert dw_app.widget.isVisible(), state(dw_app)
    assert not dw_app.ball.isVisible()
    assert dw_app.config.get("collapsed") is False


def test_hide_and_restore_via_tray(dw_app, qapp) -> None:
    dw_app.collapse()
    qapp.processEvents()
    dw_app._toggle_widget(False)
    qapp.processEvents()
    assert not dw_app.widget.isVisible() and not dw_app.ball.isVisible(), state(dw_app)
    assert not dw_app.act_widget.isChecked()
    dw_app._toggle_widget(True)
    qapp.processEvents()
    assert dw_app.ball.isVisible() and not dw_app.widget.isVisible(), state(dw_app)


def test_tray_switch_back_to_card(dw_app, qapp) -> None:
    dw_app.collapse()
    qapp.processEvents()
    dw_app.act_ball.trigger()
    qapp.processEvents()
    assert dw_app.widget.isVisible() and not dw_app.ball.isVisible(), state(dw_app)


def test_compact_size() -> None:
    from diskwatch.ui.ball import _compact_size

    cases = [
        (0, "0B"),
        (512, "512B"),
        (1536, "1.5K"),
        (10240, "10K"),
        (2_800_000, "2.7M"),
        (1_500_000_000, "1.4G"),
    ]
    for n, want in cases:
        assert _compact_size(n) == want, n
