"""
main_window.py
主窗口：左侧 20% 菜单 + 右侧 80% QStackedWidget
- 5 个一级菜单：温度场 / 氢气浓度场 / 压力场 / 氧气浓度场 / 槽电压监控
- 菜单按钮高亮选中状态
- 接收 FieldView3D.point_clicked 信号 → 弹出 HistoryDialog
- 预留 update_field(field_id, data) 公共接口
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QFrame, QLabel, QPushButton, QStackedWidget,
    QSizePolicy, QStatusBar
)
from PyQt6.QtGui import QFont

from .view_3d import FieldView3D
from .view_voltage import VoltageView
from .history_dialog import HistoryDialog
from .styles import (
    BG_SIDEBAR, BORDER_COLOR, ACCENT_BLUE,
    TEXT_PRIMARY, TEXT_MUTED
)

# ── 菜单项定义 ───────────────────────────────────────────────────────────────
_MENU_ITEMS = [
    {
        "label":    "🌡  温度场",
        "type":     "3d",
        "title":    "温度场",
        "subtitle": "电解槽温度分布（三维）",
        "unit":     "℃",
    },
    {
        "label":    "💧  氢气浓度场",
        "type":     "3d",
        "title":    "氢气浓度场",
        "subtitle": "氢气浓度空间分布（三维）",
        "unit":     "% Vol",
    },
    {
        "label":    "🔵  压力场",
        "type":     "3d",
        "title":    "压力场",
        "subtitle": "系统压力分布（三维）",
        "unit":     "kPa",
    },
    {
        "label":    "🟢  氧气浓度场",
        "type":     "3d",
        "title":    "氧气浓度场",
        "subtitle": "氧气浓度空间分布（三维）",
        "unit":     "% Vol",
    },
    {
        "label":    "⚡  槽电压监控",
        "type":     "voltage",
        "title":    "槽电压监控",
        "subtitle": "多通道实时波形",
        "unit":     "V",
    },
]


class MainWindow(QMainWindow):
    """
    监控系统主窗口。

    公共接口
    --------
    update_field(field_id: int, data: list[dict])
        将数据推送到对应 3D 场页面（field_id: 0-3）

    update_voltage(data: dict)
        将数据推送到槽电压页面
        data: {channel_name: float 或 [float, ...]}
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("电解制备氢气监控系统  |  Shanghai Electric")
        self.resize(1400, 860)
        self.setMinimumSize(1000, 640)

        self._pages: list[QWidget]   = []   # QStackedWidget 中的页面
        self._buttons: list[QPushButton] = []
        self._dialogs: dict[str, HistoryDialog] = {}  # 已打开的历史弹窗

        self._build_ui()
        self._build_status_bar()
        self._switch_page(0)            # 默认选中第一项

    # ── 主 UI 骨架 ───────────────────────────────────────────────────────────
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_lay = QHBoxLayout(central)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        # ── 左侧侧边栏（20%）────────────────────────────────────────────────
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setMinimumWidth(200)
        sidebar.setMaximumWidth(260)
        sidebar_lay = QVBoxLayout(sidebar)
        sidebar_lay.setContentsMargins(0, 0, 0, 0)
        sidebar_lay.setSpacing(0)

        # Logo / 系统名称
        logo_frame = QFrame()
        logo_frame.setFixedHeight(80)
        logo_frame.setStyleSheet(
            f"background-color: {BG_SIDEBAR};"
            f"border-bottom: 1px solid {BORDER_COLOR};"
        )
        logo_lay = QVBoxLayout(logo_frame)
        logo_lay.setContentsMargins(16, 12, 16, 12)
        logo_lay.setSpacing(2)

        sys_name = QLabel("氢气监控系统")
        _f_sys = QFont("Microsoft YaHei UI")
        _f_sys.setPixelSize(17)
        _f_sys.setBold(True)
        sys_name.setFont(_f_sys)
        sys_name.setStyleSheet(f"color: {TEXT_PRIMARY};")

        sys_sub = QLabel("ELECTROLYSIS H₂ MONITOR")
        sys_sub.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 10px; letter-spacing: 1px;"
        )

        logo_lay.addWidget(sys_name)
        logo_lay.addWidget(sys_sub)
        sidebar_lay.addWidget(logo_frame)

        # 模块标题
        menu_title = QLabel("监控模块")
        menu_title.setObjectName("sidebar_title")
        menu_title.setFixedHeight(32)
        sidebar_lay.addWidget(menu_title)

        # 菜单按钮
        for i, item in enumerate(_MENU_ITEMS):
            btn = QPushButton(item["label"])
            btn.setObjectName("menu_btn")
            btn.setCheckable(False)
            btn.setProperty("active", "false")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            btn.setFixedHeight(48)
            idx = i  # 捕获循环变量
            btn.clicked.connect(lambda _, n=idx: self._switch_page(n))
            sidebar_lay.addWidget(btn)
            self._buttons.append(btn)

        sidebar_lay.addStretch()

        # 侧边栏底部版本信息
        ver_lbl = QLabel("v1.0.0  Alpha")
        ver_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ver_lbl.setFixedHeight(32)
        ver_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px;")
        sidebar_lay.addWidget(ver_lbl)

        main_lay.addWidget(sidebar)

        # ── 右侧内容区（80%）────────────────────────────────────────────────
        self._stack = QStackedWidget()
        self._stack.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        for item in _MENU_ITEMS:
            if item["type"] == "3d":
                page = FieldView3D(
                    title=item["title"],
                    subtitle=item["subtitle"],
                    unit=item["unit"],
                )
                # 连接传感器点击信号
                page.point_clicked.connect(self._on_sensor_clicked)
            else:
                page = VoltageView()

            self._pages.append(page)
            self._stack.addWidget(page)

        main_lay.addWidget(self._stack, stretch=1)

    # ── 状态栏 ────────────────────────────────────────────────────────────────
    def _build_status_bar(self):
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._status_left  = QLabel("就绪")
        self._status_right = QLabel("2026-02-27  |  上海电气")
        self._status_right.setStyleSheet(f"color: {TEXT_MUTED};")
        sb.addWidget(self._status_left)
        sb.addPermanentWidget(self._status_right)

    # ── 菜单切换 ──────────────────────────────────────────────────────────────
    def _switch_page(self, index: int):
        """切换 QStackedWidget 页面并更新菜单按钮高亮状态。"""
        self._stack.setCurrentIndex(index)
        title = _MENU_ITEMS[index]["title"]
        self._status_left.setText(f"当前：{title}")

        for i, btn in enumerate(self._buttons):
            active = "true" if i == index else "false"
            btn.setProperty("active", active)
            # 强制刷新 QSS
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    # ── 传感器点击处理 ────────────────────────────────────────────────────────
    def _on_sensor_clicked(self, info: dict):
        """收到传感器点击信号后，弹出（或聚焦已有的）历史数据弹窗。"""
        sid = info.get("id", "Unknown")
        if sid in self._dialogs and self._dialogs[sid].isVisible():
            # 已存在，直接激活聚焦
            dlg = self._dialogs[sid]
            dlg.raise_()
            dlg.activateWindow()
        else:
            dlg = HistoryDialog(info, parent=self)
            self._dialogs[sid] = dlg
            dlg.show()

        self._status_left.setText(f"已选中传感器 {sid}  ({info.get('x',0):.1f}, "
                                   f"{info.get('y',0):.1f}, {info.get('z',0):.1f})")

    # ── 公共接口：后端数据推送 ────────────────────────────────────────────────
    def update_field(self, field_id: int, data: list[dict]):
        """
        将后端 3D 场数据推送到对应页面并刷新散点云图。

        Parameters
        ----------
        field_id : int
            0=温度场, 1=氢气浓度场, 2=压力场, 3=氧气浓度场
        data : list[dict]
            [{id, x, y, z, value}, ...]
        """
        if 0 <= field_id <= 3:
            page: FieldView3D = self._pages[field_id]
            page.update_plot(data)

    def update_voltage(self, data: dict):
        """
        将后端槽电压数据推送到电压波形页面。

        Parameters
        ----------
        data : dict
            {channel_name: float} 单点追加，或 {channel_name: [float,...]} 批量追加
        """
        voltage_page: VoltageView = self._pages[4]
        voltage_page.update_channels(data)
