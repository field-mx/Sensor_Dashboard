"""
history_dialog.py
传感器历史数据弹窗
- 接收 point_clicked 信号传来的传感器 dict，显示历史趋势折线图
- load_history(timestamps, values) 接口供后端查询完成后调用
- 内置假数据（随机游走）用于 UI 测试
"""
from __future__ import annotations

import numpy as np

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QSizePolicy, QWidget
)
from PyQt6.QtGui import QFont

import pyqtgraph as pg

from .styles import (
    BG_DEEP, BG_CARD, BORDER_COLOR, ACCENT_BLUE, ACCENT_GREEN,
    ACCENT_AMBER, ACCENT_RED, TEXT_PRIMARY, TEXT_MUTED
)


class HistoryDialog(QDialog):
    """
    传感器历史趋势弹窗。

    用法
    ----
    dialog = HistoryDialog(sensor_info, parent=main_window)
    dialog.show()

    后端数据接口
    -----------
    dialog.load_history(timestamps, values)
        timestamps : list[float] | np.ndarray  # Unix 时间戳 或 相对秒数
        values     : list[float] | np.ndarray
    """

    def __init__(self, sensor_info: dict, parent=None):
        super().__init__(parent)
        self._info = sensor_info
        self._sid  = sensor_info.get("id", "Unknown")
        self._unit = sensor_info.get("unit", "")

        self.setWindowTitle(f"历史趋势 — 传感器 {self._sid}")
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowCloseButtonHint |
            Qt.WindowType.WindowMinimizeButtonHint
        )
        self.resize(720, 500)
        self.setModal(False)   # 非模态，允许同时操作主窗口

        # 历史数据持续时长（5 分钟）
        self.HISTORY_SECS = 300

        self._build_ui()
        self._load_demo_history()

    # ── UI 构建 ────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 头部信息条 ────────────────────────────────────────────────────
        header = QFrame()
        header.setObjectName("page_header")
        header.setFixedHeight(60)
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(20, 0, 20, 0)
        h_lay.setSpacing(32)

        def _info_pair(label: str, value: str, color: str = TEXT_PRIMARY):
            col = QVBoxLayout()
            col.setSpacing(2)
            v_lbl = QLabel(value)
            _f_v = QFont("Segoe UI")
            _f_v.setPixelSize(16)
            _f_v.setBold(True)
            v_lbl.setFont(_f_v)
            v_lbl.setStyleSheet(f"color: {color};")
            k_lbl = QLabel(label)
            k_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px;")
            col.addWidget(v_lbl)
            col.addWidget(k_lbl)
            return col

        h_lay.addLayout(_info_pair("传感器 ID", self._sid, ACCENT_BLUE))

        x = self._info.get("x", 0)
        y = self._info.get("y", 0)
        z = self._info.get("z", 0)
        h_lay.addLayout(_info_pair("坐标 (X, Y, Z)", f"({x:.1f}, {y:.1f}, {z:.1f})"))

        cur_val = self._info.get("value", None)
        h_lay.addLayout(_info_pair(
            f"当前值{' (' + self._unit + ')' if self._unit else ''}",
            f"{cur_val:.3f}" if isinstance(cur_val, float) else str(cur_val),
            ACCENT_GREEN
        ))

        h_lay.addStretch()
        root.addWidget(header)

        # ── 统计摘要条 ───────────────────────────────────────────────────
        stat_frame = QFrame()
        stat_frame.setFixedHeight(48)
        stat_frame.setStyleSheet(
            f"background: {BG_CARD}; border-bottom: 1px solid {BORDER_COLOR};"
        )
        s_lay = QHBoxLayout(stat_frame)
        s_lay.setContentsMargins(20, 0, 20, 0)
        s_lay.setSpacing(28)

        self._stat_labels: dict[str, QLabel] = {}
        for key, display, color in [
            ("min",    "最小值", ACCENT_BLUE),
            ("max",    "最大值", ACCENT_RED),
            ("avg",    "均  值", ACCENT_GREEN),
            ("std",    "标准差", ACCENT_AMBER),
            ("points", "数据点", TEXT_MUTED),
        ]:
            col = QVBoxLayout()
            col.setSpacing(1)
            vl = QLabel("—")
            _f_vl = QFont("Segoe UI")
            _f_vl.setPixelSize(15)
            _f_vl.setBold(True)
            vl.setFont(_f_vl)
            vl.setStyleSheet(f"color: {color};")
            kl = QLabel(display + (f" ({self._unit})" if self._unit and key not in ("std","points") else ""))
            kl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px;")
            col.addWidget(vl)
            col.addWidget(kl)
            self._stat_labels[key] = vl
            s_lay.addLayout(col)

        s_lay.addStretch()
        root.addWidget(stat_frame)

        # ── 折线图 ───────────────────────────────────────────────────────
        chart_container = QWidget()
        chart_container.setStyleSheet(f"background: {BG_DEEP};")
        chart_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        c_lay = QVBoxLayout(chart_container)
        c_lay.setContentsMargins(12, 8, 12, 8)

        pg.setConfigOption("background", BG_DEEP)
        pg.setConfigOption("foreground", TEXT_MUTED)

        self._pw = pg.PlotWidget()
        self._pw.setBackground(BG_DEEP)
        self._pw.showGrid(x=True, y=True, alpha=0.2)
        self._pw.setLabel("left",   f"物理量{'/' + self._unit if self._unit else ''}",
                          color=TEXT_MUTED, size="11pt")
        self._pw.setLabel("bottom", "时间 / s（0−5分钟）", color=TEXT_MUTED, size="11pt")

        tick_font = QFont("Consolas")
        tick_font.setPointSize(10)
        for ax in ("left", "bottom"):
            self._pw.getAxis(ax).setTickFont(tick_font)
            self._pw.getAxis(ax).setPen(pg.mkPen(BORDER_COLOR))
            self._pw.getAxis(ax).setTextPen(pg.mkPen(TEXT_MUTED))

        # 主曲线
        self._curve = self._pw.plot(
            [], [],
            pen=pg.mkPen(color=ACCENT_BLUE, width=2),
            fillLevel=None,
        )
        # 填充区域（视觉效果）
        self._fill_curve = self._pw.plot(
            [], [],
            pen=pg.mkPen(None),
            fillLevel=0,
            brush=pg.mkBrush(color=(88, 166, 255, 30)),
        )

        # 当前值水平参考线
        self._ref_line = pg.InfiniteLine(
            angle=0,
            pen=pg.mkPen(color=ACCENT_GREEN, width=1, style=Qt.PenStyle.DashLine),
            label=f"当前值",
            labelOpts={"color": ACCENT_GREEN, "position": 0.95},
        )
        self._pw.addItem(self._ref_line)

        c_lay.addWidget(self._pw)
        root.addWidget(chart_container, stretch=1)

        # ── 底部按钮 ─────────────────────────────────────────────────────
        btn_bar = QFrame()
        btn_bar.setFixedHeight(48)
        btn_bar.setStyleSheet(
            f"background: {BG_CARD}; border-top: 1px solid {BORDER_COLOR};"
        )
        b_lay = QHBoxLayout(btn_bar)
        b_lay.setContentsMargins(16, 0, 16, 0)
        b_lay.addStretch()

        close_btn = QPushButton("关  闭")
        close_btn.setObjectName("close_btn")
        close_btn.setFixedWidth(100)
        close_btn.clicked.connect(self.close)
        b_lay.addWidget(close_btn)
        root.addWidget(btn_bar)

    # ── 假数据：随机游走 ──────────────────────────────────────────────────
    def _load_demo_history(self):
        rng = np.random.default_rng(int(hash(self._sid)) % 2**32)
        n   = 300   # 5 分钟，每秒一个点
        ts  = np.linspace(0, 299, n)                     # 秒
        cur = self._info.get("value", 50.0)
        if not isinstance(cur, (int, float)):
            cur = 50.0
        noise = rng.normal(0, 2.0, n).cumsum()
        vals  = cur + noise - noise[0]
        # 平滑
        kernel = np.ones(7) / 7
        vals   = np.convolve(vals, kernel, mode="same")
        self.load_history(ts, vals)

    # ── 公共接口：加载历史数据 ─────────────────────────────────────────────
    def load_history(self, timestamps, values):
        """
        绘制历史趋势折线并更新统计摘要。

        Parameters
        ----------
        timestamps : array-like  相对秒数或 Unix 时间戳（将显示最近 5 分钟）
        values     : array-like  物理量数值
        """
        ts   = np.asarray(timestamps, dtype=float)
        vals = np.asarray(values,     dtype=float)

        if len(ts) == 0:
            return

        # 归一化时间戳为相对秒（以第一个点为原点）
        ts = ts - ts[0]

        # 仅显示最近 5 分钟的数据
        mask = ts >= (ts[-1] - 300.0)
        ts   = ts[mask]
        vals = vals[mask]

        self._curve.setData(ts, vals)
        self._fill_curve.setData(ts, vals)
        self._ref_line.setValue(float(vals[-1]))

        # 更新统计
        self._stat_labels["min"].setText(f"{vals.min():.3f}")
        self._stat_labels["max"].setText(f"{vals.max():.3f}")
        self._stat_labels["avg"].setText(f"{vals.mean():.3f}")
        self._stat_labels["std"].setText(f"{vals.std():.3f}")
        self._stat_labels["points"].setText(str(len(vals)))
