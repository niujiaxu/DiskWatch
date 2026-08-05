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
