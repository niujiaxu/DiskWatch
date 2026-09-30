"""按应用分组：归属根推断与均衡聚类测试。"""

from __future__ import annotations

from diskwatch.apps import OTHER_KEY, build_groups, label_for_key, resolve_app


def test_resolve_app_known_anchors() -> None:
    assert (
        resolve_app(
            r"C:\Users\niu\Documents\Tencent Files\2991799732\nt_qq\nt_data\log\x.qqxlog"
        )[0]
        == "tencent.qq"
    )
    assert (
        resolve_app(r"C:\Users\niu\AppData\Roaming\Tencent\QQ\Temp\x.dat")[0]
        == "tencent.qq"
    )
    assert (
        resolve_app(r"C:\Users\niu\AppData\Roaming\Tencent\WeChat\x.dat")[0]
        == "tencent.wechat"
    )
    assert resolve_app(r"C:\Program Files\Zotero\zotero.exe")[0] == "zotero"
    assert resolve_app(r"C:\Users\niu\Documents\Zotero\storage\x.pdf")[0] == "zotero"
    assert resolve_app(r"C:\Users\niu\AppData\Local\Temp\abc\x.tmp")[0] == "special.temp"
    assert resolve_app(r"C:\Users\niu\Downloads\a.zip")[0] == "special.downloads"
    assert resolve_app(r"C:\Users\niu\Desktop\b.txt")[0] == "special.desktop"
    assert resolve_app(r"C:\Windows\System32\drivers\x.sys")[0] == "special.system"
    assert resolve_app(r"C:\Users\niu\.codex\logs\x.json")[0] == "codex"
    assert resolve_app(r"C:\Users\niu\.cursor\extensions\x.js")[0] == "cursor"
    assert resolve_app(r"C:\Users\niu\AppData\Roaming\Oray\AweSun\log\x.log")[0] == "oray.awesun"
    assert resolve_app(r"D:\Games\SomeGame\data\cache\x.bin")[0] == "SomeGame"
    assert resolve_app(r"C:\file.txt")[0] == "special.loose"


def test_resolve_app_sub_for_split() -> None:
    assert resolve_app(
        r"C:\Users\niu\Documents\Tencent Files\2991799732\nt_qq\nt_data\log\x.log"
    ) == ("tencent.qq", "2991799732")
    assert resolve_app(r"C:\Users\niu\AppData\Roaming\Tencent\QQ\Temp\x.dat") == (
        "tencent.qq",
        "QQ",
    )


def test_labels_fallback_to_vendor_then_name() -> None:
    assert label_for_key("tencent.qq") == "QQ"
    assert label_for_key("tencent") == "腾讯"
    assert label_for_key("unknown-app") == "unknown-app"


def test_build_groups_merges_products_and_splits_big_groups() -> None:
    rows = [
        ("tencent.qq", "2991799732", 40, 4000, 5000, 300.0),
        ("tencent.qq", "88888888", 30, 2000, 2000, 200.0),
        ("tencent.qq", "QQ", 30, 1000, 1000, 100.0),
        ("zotero", "", 20, 500, 500, 150.0),
    ]
    folder_rows = [
        ("tencent.qq", "2991799732", r"C:\Users\niu\Documents\Tencent Files", 40),
        ("tencent.qq", "88888888", r"C:\Users\niu\Documents\Tencent Files", 30),
        ("tencent.qq", "QQ", r"C:\Users\niu\AppData\Roaming\Tencent", 30),
        ("zotero", "", r"C:\Program Files\Zotero", 20),
    ]
    groups = build_groups(rows, folder_rows)
    # QQ 100 条 > 25%（30）→ 按账号/子目录拆分；Zotero 20 条不拆
    qq = [g for g in groups if g.key == "tencent.qq"]
    assert len(qq) == 3
    assert all(g.sub for g in qq)
    assert sum(g.count for g in qq) == 100
    zotero = [g for g in groups if g.key == "zotero"]
    assert len(zotero) == 1 and zotero[0].folder == r"C:\Program Files\Zotero"
    # 净字节一条不丢
    assert sum(g.net for g in groups) == 4000 + 2000 + 1000 + 500


def test_build_groups_long_tail_goes_to_other() -> None:
    rows = [
        ("tencent.qq", "", 200, 1000, 1000, 100.0),
        ("app.a", "", 1, 10, 10, 50.0),
        ("app.b", "", 1, 20, 20, 40.0),
        ("app.c", "", 2, 30, 30, 30.0),
    ]
    groups = build_groups(rows, [])
    others = [g for g in groups if g.key == OTHER_KEY]
    assert len(others) == 1
    assert others[0].count == 4
    assert others[0].net == 60
    # 「其它位置」永远排在最后
    assert groups[-1].key == OTHER_KEY


def test_special_groups_never_merged_into_other() -> None:
    rows = [
        ("special.temp", "", 1, 5, 5, 10.0),
        ("app.a", "", 1, 10, 10, 20.0),
        ("app.b", "", 1, 20, 20, 30.0),
        ("big.app", "", 100, 1000, 1000, 40.0),
    ]
    groups = build_groups(rows, [])
    assert any(g.key == "special.temp" for g in groups)
    others = [g for g in groups if g.key == OTHER_KEY]
    assert len(others) == 1 and others[0].count == 2
