"""设置对话框：监控范围、过滤规则、外观、数据管理。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..autostart import is_enabled as autostart_enabled
from ..autostart import set_enabled as set_autostart
from ..config import Config, default_home, paths
from ..i18n import SUPPORTED_LOCALES, tr
from ..storage import Storage
from ..watcher import list_drives
from .panel import DayPicker
from .style import PANEL_QSS, apply_window_icon, enable_dark_titlebar


class SettingsDialog(QDialog):
    def __init__(self, config: Config, storage: Storage, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._storage = storage
        # 路径变更在 accept 时应用；需要重启时由调用方处理
        self.paths_changed = False
        self._pending_config_path = ""
        self._pending_db_path = ""
        self.setWindowTitle(tr("设置"))
        # 只关帮助按钮。切勿写 `& ~Qt.WindowContextHelpButtonHint`：
        # PySide6 里对 WindowType 做 ~ 得到的是残缺掩码（约 0x1feffff），
        # 会顺带清掉 WindowCloseButtonHint，标题栏 ✕ 看起来在但点不了。
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        apply_window_icon(self)
        self.setStyleSheet(PANEL_QSS)
        self.setMinimumSize(640, 600)
        self._build()
        self._load()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_window_icon(self)
        enable_dark_titlebar(self)

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        tabs = QTabWidget()
        tabs.addTab(self._tab_scope(), tr("监控范围"))
        tabs.addTab(self._tab_filters(), tr("过滤规则"))
        tabs.addTab(self._tab_appearance(), tr("外观与启动"))
        tabs.addTab(self._tab_data(), tr("数据"))
        root.addWidget(tabs)

        # objectName 必须在构造时给定：样式表已经应用过之后再改 objectName，
        # Qt 不会自动重新 polish，#primary 的配色就不会生效。
        btn_ok = QPushButton(tr("保存并应用"), objectName="primary")
        btn_cancel = QPushButton(tr("取消"))
        buttons = QDialogButtonBox(parent=self)
        buttons.addButton(btn_ok, QDialogButtonBox.AcceptRole)
        buttons.addButton(btn_cancel, QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ---------- 各页 ----------

    def _tab_scope(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)

        lay.addWidget(QLabel(tr("勾选要监控的磁盘："), objectName="dim"))
        self.drive_list = QListWidget()
        self.drive_list.setMaximumHeight(140)
        lay.addWidget(self.drive_list)

        self.chk_removable = QCheckBox(tr("同时监控可移动磁盘（U 盘 / 移动硬盘）"))
        lay.addWidget(self.chk_removable)

        lay.addWidget(QLabel(tr("额外监控的文件夹（可选，留空表示只按磁盘监控）："), objectName="dim"))
        self.folder_list = QListWidget()
        self.folder_list.setMaximumHeight(110)
        lay.addWidget(self.folder_list)

        row = QHBoxLayout()
        btn_add = QPushButton(tr("添加文件夹…"))
        btn_del = QPushButton(tr("移除选中"))
        btn_add.clicked.connect(self._add_folder)
        btn_del.clicked.connect(self._remove_folder)
        row.addWidget(btn_add)
        row.addWidget(btn_del)
        row.addStretch(1)
        lay.addLayout(row)

        self.chk_folders_only = QCheckBox(tr("只监控上面这些文件夹（忽略磁盘勾选）"))
        lay.addWidget(self.chk_folders_only)
        lay.addStretch(1)
        return w

    def _tab_filters(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(8)

        lay.addWidget(QLabel(tr("排除的路径片段（每行一条，路径里包含即忽略，不区分大小写）："), objectName="dim"))
        self.txt_dirs = QPlainTextEdit()
        lay.addWidget(self.txt_dirs, 3)

        lay.addWidget(QLabel(tr("排除的扩展名（每行一条，含点号）："), objectName="dim"))
        self.txt_exts = QPlainTextEdit()
        self.txt_exts.setMaximumHeight(90)
        lay.addWidget(self.txt_exts, 1)

        lay.addWidget(QLabel(tr("排除的文件名（每行一条，支持 * 通配）："), objectName="dim"))
        self.txt_names = QPlainTextEdit()
        self.txt_names.setMaximumHeight(80)
        lay.addWidget(self.txt_names, 1)

        form = QFormLayout()
        self.spin_min = QSpinBox()
        self.spin_min.setRange(0, 1024 * 1024)
        self.spin_min.setSuffix(" KB")
        self.spin_min.setSpecialValueText(tr("不限制"))
        form.addRow(tr("最小体积"), self.spin_min)
        self.chk_hidden = QCheckBox(tr("忽略隐藏文件与系统文件"))
        form.addRow("", self.chk_hidden)
        self.chk_dot_dirs = QCheckBox(tr("忽略点号开头的目录（.git / .venv / .idea / .cursor 等）"))
        form.addRow("", self.chk_dot_dirs)
        lay.addLayout(form)

        btn_reset = QPushButton(tr("恢复默认过滤规则"))
        btn_reset.clicked.connect(self._reset_filters)
        lay.addWidget(btn_reset, alignment=Qt.AlignLeft)

        presets_row = QHBoxLayout()
        presets_row.setSpacing(8)
        self.cmb_preset = DayPicker()
        self.cmb_preset.addItem(tr("开发目录过滤"), "dev")
        self.cmb_preset.setToolTip(
            tr("把 __pycache__ / node_modules / .git / .pytest_cache 等"
               "常见开发目录加入排除列表")
        )
        presets_row.addWidget(self.cmb_preset, 1)
        btn_apply = QPushButton(tr("应用模板"))
        btn_apply.clicked.connect(self._apply_preset)
        presets_row.addWidget(btn_apply)
        lay.addLayout(presets_row)
        return w

    def _apply_preset(self) -> None:
        key = self.cmb_preset.currentData()
        if key == "dev":
            dev_dirs = [
                "\\__pycache__\\",
                "\\node_modules\\",
                "\\dist\\",
                "\\build\\",
                "\\.pytest_cache\\",
                "\\.mypy_cache\\",
                "\\.ruff_cache\\",
                "\\.coverage\\",
                "\\.tox\\",
            ]
            current = _lines(self.txt_dirs.toPlainText())
            added = 0
            for d in dev_dirs:
                if d not in current:
                    current.append(d)
                    added += 1
            if added:
                self.txt_dirs.setPlainText("\n".join(current))
                QMessageBox.information(
                    self,
                    tr("已应用"),
                    tr("已添加 {n} 条开发目录过滤规则。", n=added),
                )

    def _tab_appearance(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)

        self.slider_opacity = QSlider(Qt.Horizontal)
        self.slider_opacity.setRange(40, 100)
        self.lbl_opacity = QLabel("95%")
        self.slider_opacity.valueChanged.connect(
            lambda v: self.lbl_opacity.setText(f"{v}%")
        )
        row = QHBoxLayout()
        row.addWidget(self.slider_opacity)
        row.addWidget(self.lbl_opacity)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow(tr("组件透明度"), holder)

        self.chk_top = QCheckBox(tr("始终置顶"))
        form.addRow("", self.chk_top)
        self.chk_autostart = QCheckBox(tr("开机自动启动"))
        form.addRow("", self.chk_autostart)
        self.chk_start_min = QCheckBox(tr("启动时只显示托盘图标，不显示悬浮组件"))
        form.addRow("", self.chk_start_min)

        self.cmb_language = DayPicker()
        for code, name in SUPPORTED_LOCALES.items():
            self.cmb_language.addItem(name, code)
        self.cmb_language.setToolTip(tr("修改语言后即时生效"))
        form.addRow(tr("界面语言"), self.cmb_language)

        self.chk_scan = QCheckBox(tr("启动时补扫最近创建的文件（补回程序没在跑期间遗漏的记录）"))
        form.addRow("", self.chk_scan)
        self.spin_scan_days = QSpinBox()
        self.spin_scan_days.setRange(1, 30)
        self.spin_scan_days.setSuffix(tr(" 天"))
        self.spin_scan_days.setToolTip(tr("只补创建时间落在最近 N 天内的文件"))
        form.addRow(tr("补扫回看窗口"), self.spin_scan_days)
        self.chk_scan.toggled.connect(self.spin_scan_days.setEnabled)
        return w

    def _tab_data(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(12)

        form = QFormLayout()
        self.spin_retention = QSpinBox()
        self.spin_retention.setRange(0, 3650)
        self.spin_retention.setSuffix(tr(" 天"))
        self.spin_retention.setSpecialValueText(tr("永久保留"))
        form.addRow(tr("历史数据保留"), self.spin_retention)
        lay.addLayout(form)

        self.lbl_total = QLabel("", objectName="dim")
        lay.addWidget(self.lbl_total)

        btn_clear = QPushButton(tr("清空所有记录"))
        btn_clear.clicked.connect(self._clear_data)
        lay.addWidget(btn_clear, alignment=Qt.AlignLeft)

        lay.addWidget(QLabel(tr("文件位置（可改到其他盘；保存后需重启生效）"), objectName="dim"))

        self.edit_config = QLineEdit()
        self.edit_config.setPlaceholderText(tr("配置文件路径（.json）"))
        btn_cfg = QPushButton(tr("浏览…"))
        btn_cfg.clicked.connect(self._browse_config)
        row_cfg = QHBoxLayout()
        row_cfg.addWidget(self.edit_config, 1)
        row_cfg.addWidget(btn_cfg)
        lay.addWidget(QLabel(tr("配置文件"), objectName="dim"))
        lay.addLayout(row_cfg)

        self.edit_db = QLineEdit()
        self.edit_db.setPlaceholderText(tr("数据库路径（.db）"))
        btn_db = QPushButton(tr("浏览…"))
        btn_db.clicked.connect(self._browse_db)
        row_db = QHBoxLayout()
        row_db.addWidget(self.edit_db, 1)
        row_db.addWidget(btn_db)
        lay.addWidget(QLabel(tr("数据库"), objectName="dim"))
        lay.addLayout(row_db)

        row_reset = QHBoxLayout()
        btn_default = QPushButton(tr("恢复默认位置"))
        btn_default.setToolTip(tr("默认目录：{home}", home=default_home()))
        btn_default.clicked.connect(self._reset_paths)
        row_reset.addWidget(btn_default)
        row_reset.addStretch(1)
        lay.addLayout(row_reset)

        tip = QLabel(
            tr("引导文件始终留在 AppData\\DiskWatch\\location.json，"
               "用来记住你自定义的路径。改位置时会自动拷贝现有文件。"),
            objectName="dim",
        )
        tip.setWordWrap(True)
        lay.addWidget(tip)
        lay.addStretch(1)
        return w

    # ---------- 读写配置 ----------

    def _load(self) -> None:
        cfg = self._config
        excluded = {d.rstrip("\\/").upper() for d in cfg.get("excluded_drives", [])}
        self.drive_list.clear()
        for root in list_drives(include_removable=True):
            from PySide6.QtWidgets import QListWidgetItem

            item = QListWidgetItem(root)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(
                Qt.Unchecked if root.rstrip("\\/").upper() in excluded else Qt.Checked
            )
            self.drive_list.addItem(item)

        self.chk_removable.setChecked(bool(cfg.get("include_removable")))
        self.folder_list.clear()
        self.folder_list.addItems(cfg.get("watch_folders", []))
        self.chk_folders_only.setChecked(cfg.get("watch_mode") == "folders")

        self.txt_dirs.setPlainText("\n".join(cfg.get("exclude_dirs", [])))
        self.txt_exts.setPlainText("\n".join(cfg.get("exclude_exts", [])))
        self.txt_names.setPlainText("\n".join(cfg.get("exclude_names", [])))
        self.spin_min.setValue(int(cfg.get("min_size_kb", 0)))
        self.chk_hidden.setChecked(bool(cfg.get("ignore_hidden", True)))
        self.chk_dot_dirs.setChecked(bool(cfg.get("ignore_dot_dirs", True)))

        opacity = cfg.get("widget_opacity", 0.95)
        try:
            opacity = float(opacity)
        except (TypeError, ValueError):
            opacity = 0.95  # 配置损坏时回退默认，避免设置对话框打不开
        self.slider_opacity.setValue(int(opacity * 100))
        self.chk_top.setChecked(bool(cfg.get("always_on_top", True)))
        self.chk_start_min.setChecked(bool(cfg.get("start_minimized", False)))
        self.chk_autostart.setChecked(autostart_enabled())
        self.chk_scan.setChecked(bool(cfg.get("scan_on_startup", True)))
        self.spin_scan_days.setValue(int(cfg.get("scan_lookback_days", 3)))
        self.spin_scan_days.setEnabled(self.chk_scan.isChecked())

        self.spin_retention.setValue(int(cfg.get("retention_days", 90)))
        self.lbl_total.setText(
            tr("当前已记录 {count} 条文件记录", count=self._storage.total_count())
        )
        lang = cfg.get("language", "zh_CN")
        idx = self.cmb_language.findData(lang)
        if idx >= 0:
            self.cmb_language.setCurrentIndex(idx)
        self.edit_config.setText(str(paths.config))
        self.edit_db.setText(str(paths.db))
        self._orig_config = str(paths.config)
        self._orig_db = str(paths.db)

    def result_values(self) -> dict:
        excluded = []
        for i in range(self.drive_list.count()):
            item = self.drive_list.item(i)
            if item.checkState() != Qt.Checked:
                excluded.append(item.text().rstrip("\\/"))

        folders = [
            self.folder_list.item(i).text() for i in range(self.folder_list.count())
        ]
        return {
            "excluded_drives": excluded,
            "include_removable": self.chk_removable.isChecked(),
            "watch_folders": folders,
            "watch_mode": "folders" if self.chk_folders_only.isChecked() else "drives",
            "exclude_dirs": _lines(self.txt_dirs.toPlainText()),
            "exclude_exts": _lines(self.txt_exts.toPlainText()),
            "exclude_names": _lines(self.txt_names.toPlainText()),
            "min_size_kb": self.spin_min.value(),
            "ignore_hidden": self.chk_hidden.isChecked(),
            "ignore_dot_dirs": self.chk_dot_dirs.isChecked(),
            "widget_opacity": self.slider_opacity.value() / 100.0,
            "always_on_top": self.chk_top.isChecked(),
            "start_minimized": self.chk_start_min.isChecked(),
            "retention_days": self.spin_retention.value(),
            "scan_on_startup": self.chk_scan.isChecked(),
            "scan_lookback_days": self.spin_scan_days.value(),
            "language": self.cmb_language.currentData(),
        }

    def accept(self) -> None:
        if self.chk_folders_only.isChecked() and self.folder_list.count() == 0:
            QMessageBox.warning(self, tr("还没选文件夹"), tr("选择了只监控文件夹，但列表是空的。"))
            return

        new_cfg = self.edit_config.text().strip()
        new_db = self.edit_db.text().strip()
        if not new_cfg or not new_db:
            QMessageBox.warning(self, tr("路径为空"), tr("配置文件和数据库路径都不能为空。"))
            return

        if new_cfg != self._orig_config or new_db != self._orig_db:
            reply = QMessageBox.question(
                self,
                tr("更改文件位置"),
                tr("将把现有配置/数据库复制到新位置，并在下次启动时使用新路径。\n"
                   "应用需要重启才能生效，是否继续？"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply != QMessageBox.Yes:
                return  # 用户取消：不保存任何设置，autostart 也不改
            # 先把当前界面里的设置写进内存，迁移动作由调用方在关闭存储后执行
            self._pending_config_path = new_cfg
            self._pending_db_path = new_db
            self.paths_changed = True

        # 确认完成（路径迁移未取消）后才真正写注册表
        set_autostart(self.chk_autostart.isChecked())
        super().accept()

    # ---------- 动作 ----------

    def _browse_config(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("选择配置文件位置"),
            self.edit_config.text() or str(default_home() / "config.json"),
            "JSON (*.json)",
        )
        if path:
            if not path.lower().endswith(".json"):
                path += ".json"
            self.edit_config.setText(path)

    def _browse_db(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("选择数据库位置"),
            self.edit_db.text() or str(default_home() / "diskwatch.db"),
            "SQLite (*.db)",
        )
        if path:
            if not path.lower().endswith(".db"):
                path += ".db"
            self.edit_db.setText(path)

    def _reset_paths(self) -> None:
        home = default_home()
        self.edit_config.setText(str(home / "config.json"))
        self.edit_db.setText(str(home / "diskwatch.db"))

    def pending_paths(self) -> tuple[str, str] | None:
        if not self.paths_changed:
            return None
        return self._pending_config_path, self._pending_db_path

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("选择要监控的文件夹"))
        if folder:
            existing = {
                self.folder_list.item(i).text() for i in range(self.folder_list.count())
            }
            if folder not in existing:
                self.folder_list.addItem(folder)

    def _remove_folder(self) -> None:
        for item in self.folder_list.selectedItems():
            self.folder_list.takeItem(self.folder_list.row(item))

    def _reset_filters(self) -> None:
        from ..config import (
            DEFAULT_EXCLUDE_DIRS,
            DEFAULT_EXCLUDE_EXTS,
            DEFAULT_EXCLUDE_NAMES,
        )

        self.txt_dirs.setPlainText("\n".join(DEFAULT_EXCLUDE_DIRS))
        self.txt_exts.setPlainText("\n".join(DEFAULT_EXCLUDE_EXTS))
        self.txt_names.setPlainText("\n".join(DEFAULT_EXCLUDE_NAMES))

    def _clear_data(self) -> None:
        ok = QMessageBox.question(
            self,
            tr("确认清空"),
            tr("将删除全部历史记录，且不可恢复。继续？"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ok == QMessageBox.Yes:
            self._storage.clear_all()
            self.lbl_total.setText(tr("当前已记录 {count} 条文件记录", count=0))


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]
