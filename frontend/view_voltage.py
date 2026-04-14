"""
view_voltage.py
槽电压监控页面 —— 每通道独立卡片，支持折叠 / 展开
折叠态：实时数字显示（0-30V，精确到 0.001V）
展开态：追加显示近 1 分钟（60秒）滚动折线图
"""
from __future__ import annotations

import time
from collections import deque

import numpy as np
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QFrame, QSizePolicy, QPushButton, QScrollArea, QGridLayout
)
from PyQt6.QtGui import QFont

import pyqtgraph as pg

from .styles import (
    BG_DEEP, BG_CARD, BORDER_COLOR, ACCENT_BLUE,
    TEXT_PRIMARY, TEXT_MUTED, CHANNEL_COLORS
)

# ── 常量 ─────────────────────────────────────────────────────────────────────
VOLTAGE_MIN = 0.0
VOLTAGE_MAX = 30.0
HISTORY_SECS = 60          # 展开图显示的时长（秒）
SAMPLE_INTERVAL = 1.0      # 每秒一个采样点
MAX_POINTS = int(HISTORY_SECS / SAMPLE_INTERVAL)   # 60 点

# 默认 16 通道
NUM_CHANNELS = 16
DEFAULT_CHANNELS = [f"CH{i+1:02d}" for i in range(NUM_CHANNELS)]


# ── 单通道卡片 ───────────────────────────────────────────────────────────────
class _ChannelCard(QFrame):
    """
    单个通道的折叠/展开卡片。

    折叠态：显示通道名 + 当前电压数值
    展开态：额外显示最近 1 分钟折线图
    """

    def __init__(self, ch_name: str, color: str, parent=None):
        super().__init__(parent)
        self._ch   = ch_name
        self._color = color
        self._expanded = False
        self._buf: deque = deque(maxlen=MAX_POINTS)
        self._t_buf: deque = deque(maxlen=MAX_POINTS)   # 相对时间戳
        self._t0 = time.monotonic()
        self._last_value: float = 0.0

        self.setObjectName("channel_card")
        self.setStyleSheet(self._card_style())
        self._build_header()
        self._build_chart()

        # 初始隐藏图表
        self._chart_widget.setVisible(False)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    # ── 样式 ─────────────────────────────────────────────────────────────────
    def _card_style(self) -> str:
        return (
            f"#channel_card {{"
            f"  background-color: {BG_CARD};"
            f"  border: 1px solid {BORDER_COLOR};"
            f"  border-left: 3px solid {self._color};"
            f"  border-radius: 6px;"
            f"  margin: 3px 6px;"
            f"}}"
        )

    # ── 折叠头 ────────────────────────────────────────────────────────────────
    def _build_header(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 头部行（始终可见）
        header = QWidget()
        header.setFixedHeight(48)
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(12, 0, 8, 0)
        h_lay.setSpacing(8)

        # 颜色圆点
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {self._color}; font-size: 16px;")
        dot.setFixedWidth(20)

        # 通道名
        name_lbl = QLabel(self._ch)
        _f_name = QFont("Segoe UI")
        _f_name.setPixelSize(14)
        _f_name.setBold(True)
        name_lbl.setFont(_f_name)
        name_lbl.setStyleSheet(f"color: {TEXT_PRIMARY};")
        name_lbl.setFixedWidth(64)

        # 电压数值（大字）
        self._val_lbl = QLabel("—.—— V")
        _f_val = QFont("Consolas")
        _f_val.setPixelSize(21)
        _f_val.setBold(True)
        self._val_lbl.setFont(_f_val)
        self._val_lbl.setStyleSheet(f"color: {self._color};")
        self._val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        # 量程提示
        range_lbl = QLabel("0–30 V")
        range_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px;")
        range_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        range_lbl.setFixedWidth(48)

        # 展开/折叠按钮
        self._toggle_btn = QPushButton("▶")
        self._toggle_btn.setFixedSize(28, 28)
        self._toggle_btn.setObjectName("expand_btn")
        self._toggle_btn.setStyleSheet(self._btn_style())
        self._toggle_btn.clicked.connect(self._toggle)

        h_lay.addWidget(dot)
        h_lay.addWidget(name_lbl)
        h_lay.addStretch()
        h_lay.addWidget(self._val_lbl)
        h_lay.addWidget(range_lbl)
        h_lay.addWidget(self._toggle_btn)

        # 点击头部区域也可切换
        header.mousePressEvent = lambda e: self._toggle()

        self._root_layout: QVBoxLayout = root
        self._root_layout.addWidget(header)

    def _btn_style(self) -> str:
        return (
            f"QPushButton#expand_btn {{"
            f"  background: transparent;"
            f"  color: {TEXT_MUTED};"
            f"  border: 1px solid {BORDER_COLOR};"
            f"  border-radius: 4px;"
            f"  font-size: 10px;"
            f"}}"
            f"QPushButton#expand_btn:hover {{"
            f"  background: rgba(9,105,218,0.10);"
            f"  color: {ACCENT_BLUE};"
            f"}}"
        )

    # ── 折线图区域 ─────────────────────────────────────────────────────────
    def _build_chart(self):
        self._chart_widget = QWidget()
        self._chart_widget.setFixedHeight(160)
        c_lay = QVBoxLayout(self._chart_widget)
        c_lay.setContentsMargins(8, 4, 8, 8)
        c_lay.setSpacing(0)

        pg.setConfigOptions(antialias=True)
        self._pw = pg.PlotWidget()
        self._pw.setBackground(BG_DEEP)
        self._pw.showGrid(x=True, y=True, alpha=0.20)
        self._pw.setLabel("left",   "V", color=TEXT_MUTED, size="9pt")
        self._pw.setLabel("bottom", "s", color=TEXT_MUTED, size="9pt")
        self._pw.setYRange(VOLTAGE_MIN, VOLTAGE_MAX, padding=0.03)
        tick_font = QFont("Consolas")
        tick_font.setPointSize(9)
        self._pw.getAxis("left").setTickFont(tick_font)
        self._pw.getAxis("bottom").setTickFont(tick_font)
        self._pw.getAxis("left").setTextPen(pg.mkPen(TEXT_MUTED))
        self._pw.getAxis("bottom").setTextPen(pg.mkPen(TEXT_MUTED))
        self._pw.getAxis("left").setPen(pg.mkPen(BORDER_COLOR))
        self._pw.getAxis("bottom").setPen(pg.mkPen(BORDER_COLOR))
        self._pw.getAxis("left").setWidth(36)

        pen = pg.mkPen(color=self._color, width=1.5)
        self._curve = self._pw.plot([], [], pen=pen)
        c_lay.addWidget(self._pw)
        self._root_layout.addWidget(self._chart_widget)

    # ── 折叠/展开切换 ─────────────────────────────────────────────────────
    def _toggle(self):
        self._expanded = not self._expanded
        self._chart_widget.setVisible(self._expanded)
        self._toggle_btn.setText("▼" if self._expanded else "▶")
        # 展开后立刻刷新图表
        if self._expanded:
            self._refresh_curve()
        # 更新父 widget 的 layout
        if self.parent():
            self.parent().updateGeometry()
            self.parent().adjustSize()

    # ── 公共接口：更新电压值 ──────────────────────────────────────────────
    def push_value(self, value: float):
        """追加最新电压值并刷新 UI。"""
        v = max(VOLTAGE_MIN, min(VOLTAGE_MAX, float(value)))
        self._last_value = v
        t = time.monotonic() - self._t0
        self._buf.append(v)
        self._t_buf.append(t)

        # 更新数字显示
        self._val_lbl.setText(f"{v:7.3f} V")
        # 根据电压着色：正常蓝色；>25V 红色
        if v > 25.0:
            self._val_lbl.setStyleSheet(f"color: #f85149;")
        elif v > 20.0:
            self._val_lbl.setStyleSheet(f"color: #d29922;")
        else:
            self._val_lbl.setStyleSheet(f"color: {self._color};")

        if self._expanded:
            self._refresh_curve()

    # ── 内部刷新折线图 ────────────────────────────────────────────────────
    def _refresh_curve(self):
        if not self._buf:
            return
        ts = np.array(self._t_buf)
        vs = np.array(self._buf)
        # x 轴显示"最近 N 秒"的相对偏移（t-t_last 变成负数偏移）
        t_end = ts[-1]
        self._curve.setData(ts - t_end, vs)
        # 动态调整 x 轴范围，显示最近 HISTORY_SECS 秒
        self._pw.setXRange(-HISTORY_SECS, 0, padding=0.02)


# ── 主视图 ───────────────────────────────────────────────────────────────────
class VoltageView(QWidget):
    """
    槽电压监控主页面。

    16 个（可扩展）独立通道卡片，排列在可滚动区域中。
    每行 2 列，折叠态紧凑，展开态显示 1 分钟历史折线图。

    公共接口
    --------
    update_channels(data: dict[str, float])
        {channel_name: voltage_value}，每秒调用一次。
    reset()
        清除所有历史。
    """

    def __init__(self, channels: list[str] | None = None, parent=None):
        super().__init__(parent)
        self._channels = channels or DEFAULT_CHANNELS
        self._n = len(self._channels)
        self._cards: dict[str, _ChannelCard] = {}

        self._build_ui()

    # ── UI 骨架 ───────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 页面头部
        header = QFrame()
        header.setObjectName("page_header")
        header.setFixedHeight(52)
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(20, 0, 20, 0)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        t = QLabel("槽电压监控")
        t.setObjectName("page_title")
        s = QLabel(
            f"共 {self._n} 通道  ·  量程 0–30 V  ·  精度 0.001 V  ·  每秒刷新"
        )
        s.setObjectName("page_subtitle")
        title_col.addWidget(t)
        title_col.addWidget(s)
        h_lay.addLayout(title_col)
        h_lay.addStretch()

        # 全部展开/折叠按钮
        self._expand_all_btn = QPushButton("全部展开")
        self._expand_all_btn.setFixedWidth(90)
        self._expand_all_btn.setObjectName("close_btn")
        self._expand_all_btn.setStyleSheet(
            f"QPushButton{{background:rgba(9,105,218,0.12);color:{ACCENT_BLUE};"
            f"border:1px solid rgba(9,105,218,0.2); border-radius:4px; padding:4px 0;}}"
            f"QPushButton:hover{{background:rgba(9,105,218,0.25);}}"
        )
        self._expand_all_btn.clicked.connect(self._toggle_all)
        self._all_expanded = False
        h_lay.addWidget(self._expand_all_btn)

        root.addWidget(header)

        # 可滚动内容区
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"background: {BG_DEEP};")

        container = QWidget()
        container.setStyleSheet(f"background: {BG_DEEP};")
        grid = QGridLayout(container)
        grid.setContentsMargins(10, 10, 10, 10)
        grid.setSpacing(4)

        # 每行 2 列放置通道卡片
        COLS = 2
        for i, ch in enumerate(self._channels):
            color = CHANNEL_COLORS[i % len(CHANNEL_COLORS)]
            card = _ChannelCard(ch, color)
            self._cards[ch] = card
            row, col = divmod(i, COLS)
            grid.addWidget(card, row, col)

        # 让列等宽
        for c in range(COLS):
            grid.setColumnStretch(c, 1)

        scroll.setWidget(container)
        root.addWidget(scroll, stretch=1)

    # ── 全部展开/折叠 ─────────────────────────────────────────────────────
    def _toggle_all(self):
        self._all_expanded = not self._all_expanded
        self._expand_all_btn.setText("全部折叠" if self._all_expanded else "全部展开")
        for card in self._cards.values():
            if card._expanded != self._all_expanded:
                card._toggle()

    # ── 公共接口：更新各通道电压 ──────────────────────────────────────────
    def update_channels(self, data: dict):
        """
        追加并显示新电压值。

        Parameters
        ----------
        data : dict
            {channel_name: float}  —— 单点更新（每秒调用）
        """
        for ch, val in data.items():
            if ch in self._cards:
                self._cards[ch].push_value(float(val))

    def reset(self):
        """清除所有历史并重置显示。"""
        for card in self._cards.values():
            card._buf.clear()
            card._t_buf.clear()
            card._val_lbl.setText("—.—— V")
