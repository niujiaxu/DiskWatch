"""配置读写与路径迁移测试。"""

from __future__ import annotations

import json
from pathlib import Path

import diskwatch.config as cfgmod


def _isolated(monkeypatch, tmp_path) -> Path:
    """把 paths 与 home 指向临时目录，避免触碰真实 AppData。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(cfgmod.paths, "config", home / "config.json")
    monkeypatch.setattr(cfgmod.paths, "db", home / "diskwatch.db")
    monkeypatch.setattr(cfgmod, "default_home", lambda: home)
    monkeypatch.setattr(cfgmod, "location_file", lambda: home / "location.json")
    return home


def test_save_and_load(monkeypatch, tmp_path) -> None:
    import diskwatch.config as cfg

    _isolated(monkeypatch, tmp_path)
    c = cfg.Config()
    c.set("min_size_kb", 8)
    c.set("language", "en_US")
    c.save()
    assert (tmp_path / "home" / "config.json").exists()

    c2 = cfg.Config()
    assert c2.get("min_size_kb") == 8
    assert c2.get("language") == "en_US"


def test_defaults_applied(monkeypatch, tmp_path) -> None:
    import diskwatch.config as cfg

    _isolated(monkeypatch, tmp_path)
    c = cfg.Config()
    assert c.get("retention_days") == 90
    assert c.get("min_size_kb") == 0
    assert c.get("language") == "zh_CN"


def test_corrupt_config_falls_back(monkeypatch, tmp_path) -> None:
    import diskwatch.config as cfg

    home = _isolated(monkeypatch, tmp_path)
    (home / "config.json").write_text("{not json!!", encoding="utf-8")
    c = cfg.Config()
    assert c.get("retention_days") == 90


def test_corrupt_filter_version_does_not_crash(monkeypatch, tmp_path) -> None:
    """合法 JSON 但 filter_version 非整数（手改/损坏）不得导致启动崩溃。"""
    import diskwatch.config as cfg

    home = _isolated(monkeypatch, tmp_path)
    (home / "config.json").write_text(
        json.dumps({"filter_version": "abc", "min_size_kb": 8}), encoding="utf-8"
    )
    c = cfg.Config()  # 构造（load）不应抛异常
    assert c.get("min_size_kb") == 8
    assert c.get("filter_version") == cfg.FILTER_VERSION  # 回退后已重置


def test_corrupt_pos_values_do_not_crash_ui(qapp, monkeypatch, tmp_path) -> None:
    """widget_pos / ball_pos 元素非数值（损坏配置）不得导致 UI 构造崩溃。"""
    import diskwatch.config as cfg
    from diskwatch.storage import Storage
    from diskwatch.ui.ball import MiniBall
    from diskwatch.ui.widget import FloatingWidget
    from diskwatch.watcher import FileMonitor

    home = _isolated(monkeypatch, tmp_path)
    (home / "config.json").write_text(
        json.dumps(
            {"widget_pos": ["abc", 100], "ball_pos": [None, 300]}
        ),
        encoding="utf-8",
    )
    config = cfg.Config()
    storage = Storage(home / "t.db")
    try:
        monitor = FileMonitor(config, storage)
        w = FloatingWidget(storage, monitor, config)  # 构造即恢复几何
        b = MiniBall(storage, monitor, config)
        assert w.isVisible() is not None
        assert b.isVisible() is not None
    finally:
        storage.close()


def test_reset_filters(monkeypatch, tmp_path) -> None:
    import diskwatch.config as cfg

    _isolated(monkeypatch, tmp_path)
    c = cfg.Config()
    c.set("exclude_dirs", ["\\custom\\"])
    c.reset_filters()
    assert "\\custom\\" not in c.get("exclude_dirs")
    assert c.get("exclude_dirs") == cfg.DEFAULTS["exclude_dirs"]


def test_location_json_roundtrip(monkeypatch, tmp_path) -> None:
    _isolated(monkeypatch, tmp_path)
    cfgmod._write_location(
        Path(r"D:\cfg\config.json"),
        Path(r"D:\db\diskwatch.db"),
    )
    loc = cfgmod._read_location()
    assert loc == {
        "config_path": r"D:\cfg\config.json",
        "db_path": r"D:\db\diskwatch.db",
    }

    cfgmod._write_location(None, None)
    assert cfgmod._read_location() == {}


def test_apply_paths_migrate(monkeypatch, tmp_path) -> None:
    import diskwatch.config as cfg

    home = _isolated(monkeypatch, tmp_path)
    old_cfg = home / "config.json"
    old_cfg.write_text(json.dumps({"min_size_kb": 5}), encoding="utf-8")

    new_cfg = tmp_path / "new" / "config.json"
    new_db = tmp_path / "new" / "diskwatch.db"
    cfg.apply_paths(config_path=new_cfg, db_path=new_db, migrate=True)

    assert new_cfg.exists()
    assert json.loads(new_cfg.read_text(encoding="utf-8"))["min_size_kb"] == 5
    assert cfgmod.paths.config == new_cfg
    assert cfgmod.paths.db == new_db
    loc = cfgmod._read_location()
    assert loc["config_path"] == str(new_cfg)


def test_reset_paths_to_default(monkeypatch, tmp_path) -> None:
    import diskwatch.config as cfg

    _isolated(monkeypatch, tmp_path)
    cfg.apply_paths(
        config_path=tmp_path / "x" / "config.json",
        db_path=tmp_path / "x" / "diskwatch.db",
        migrate=False,
    )
    cfg.reset_paths_to_default(migrate=False)
    assert cfgmod.paths.config == tmp_path / "home" / "config.json"
    assert cfgmod.paths.db == tmp_path / "home" / "diskwatch.db"
