"""
view_voltage.py
槽电压监控页。

每个通道同时显示槽电压与通道温度；展开趋势图后可在电压/温度之间切换。
后端可发送如下任一格式：
  1. {"channels": {"CH01": 12.345}, "channel_temperatures": {"CH01": 46.2}}
  2. {"CH01": 12.345, "CH02": 13.210}
  3. {"CH01": {"voltage": 12.345, "temperature": 46.2}}
"""
from __future__ import annotations

import time
from collections import deque
from typing import Any

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .styles import (
    ACCENT_AMBER,
    ACCENT_BLUE,
    ACCENT_RED,
    BG_CARD,
    BG_DEEP,
    BORDER_COLOR,
    CHANNEL_COLORS,
    TEXT_MUTED,
    TEXT_PRIMARY,
)

VOLTAGE_MIN = 0.0
VOLTAGE_MAX = 30.0
TEMP_MIN = -20.0
TEMP_MAX = 120.0
HISTORY_SECS = 60
SAMPLE_INTERVAL = 1.0
MAX_POINTS = int(HISTORY_SECS / SAMPLE_INTERVAL)

NUM_CHANNELS = 16
DEFAULT_CHANNELS = [f"CH{i + 1:02d}" for i in range(NUM_CHANNELS)]


def _status_color(value: float, warn: float, alarm: float) -> str:
    if value >= alarm:
        return ACCENT_RED
    if value >= warn:
        return ACCENT_AMBER
    return ACCENT_BLUE


class _ChannelCard(QFrame):
    """单个槽电压通道卡片，支持电压与温度两条历史曲线。"""

    def __init__(self, ch_name: str, color: str, parent=None):
        super().__init__(parent)
        self._ch = ch_name
        self._color = color
        self._expanded = False
        self._mode = "voltage"
        self._t0 = time.monotonic()

        self._voltage_buf: deque[float] = deque(maxlen=MAX_POINTS)
        self._temperature_buf: deque[float] = deque(maxlen=MAX_POINTS)
        self._t_buf: deque[float] = deque(maxlen=MAX_POINTS)
        self._last_voltage: float | None = None
        self._last_temperature: float | None = None

        self.setObjectName("channel_card")
        self.setStyleSheet(self._card_style())
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        self._build_header()
        self._build_chart()
        self._chart_widget.setVisible(False)

    def _card_style(self) -> str:
        return (
            "#channel_card {"
            f"background-color: {BG_CARD};"
            f"border: 1px solid {BORDER_COLOR};"
            f"border-left: 3px solid {self._color};"
            "border-radius: 6px;"
            "margin: 3px 6px;"
            "}"
        )

    def _metric_style(self, color: str, px: int = 20) -> str:
        return f"color: {color}; font-family: Consolas; font-size: {px}px; font-weight: 700;"

    def _build_header(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._root_layout = root

        header = QWidget()
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        header.setMinimumHeight(66)
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(12, 6, 8, 6)
        h_lay.setSpacing(8)

        dot = QLabel("●")
        dot.setStyleSheet(f"color: {self._color}; font-size: 16px;")
        dot.setFixedWidth(20)

        name_col = QVBoxLayout()
        name_col.setSpacing(3)
        name_lbl = QLabel(self._ch)
        font = QFont("Segoe UI")
        font.setPixelSize(14)
        font.setBold(True)
        name_lbl.setFont(font)
        name_lbl.setStyleSheet(f"color: {TEXT_PRIMARY};")

        range_lbl = QLabel("0-30 V | 0.1 °C")
        range_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px;")
        name_col.addWidget(name_lbl)
        name_col.addWidget(range_lbl)

        value_col = QVBoxLayout()
        value_col.setSpacing(2)

        voltage_row = QHBoxLayout()
        voltage_row.setSpacing(6)
        voltage_tag = QLabel("电压")
        voltage_tag.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px;")
        self._voltage_lbl = QLabel("--.--- V")
        self._voltage_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._voltage_lbl.setStyleSheet(self._metric_style(self._color, 20))
        voltage_row.addWidget(voltage_tag)
        voltage_row.addWidget(self._voltage_lbl, stretch=1)

        temp_row = QHBoxLayout()
        temp_row.setSpacing(6)
        temp_tag = QLabel("温度")
        temp_tag.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px;")
        self._temperature_lbl = QLabel("--.- °C")
        self._temperature_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._temperature_lbl.setStyleSheet(self._metric_style(ACCENT_BLUE, 17))
        temp_row.addWidget(temp_tag)
        temp_row.addWidget(self._temperature_lbl, stretch=1)

        value_col.addLayout(voltage_row)
        value_col.addLayout(temp_row)

        mode_col = QVBoxLayout()
        mode_col.setSpacing(4)
        self._voltage_mode_btn = QPushButton("V")
        self._temp_mode_btn = QPushButton("°C")
        for btn in (self._voltage_mode_btn, self._temp_mode_btn):
            btn.setFixedSize(34, 24)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setObjectName("mode_btn")
            btn.clicked.connect(lambda _, b=btn: self._set_mode("temperature" if b is self._temp_mode_btn else "voltage"))
        mode_col.addWidget(self._voltage_mode_btn)
        mode_col.addWidget(self._temp_mode_btn)

        self._toggle_btn = QPushButton("▾")
        self._toggle_btn.setFixedSize(28, 28)
        self._toggle_btn.setObjectName("expand_btn")
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.setStyleSheet(self._button_style())
        self._toggle_btn.clicked.connect(self._toggle)

        h_lay.addWidget(dot)
        h_lay.addLayout(name_col)
        h_lay.addStretch()
        h_lay.addLayout(value_col, stretch=1)
        h_lay.addLayout(mode_col)
        h_lay.addWidget(self._toggle_btn)

        header.mousePressEvent = lambda _event: self._toggle()
        root.addWidget(header)
        self._refresh_mode_buttons()

    def _button_style(self) -> str:
        return (
            "QPushButton#expand_btn {"
            "background: transparent;"
            f"color: {TEXT_MUTED};"
            f"border: 1px solid {BORDER_COLOR};"
            "border-radius: 4px;"
            "font-size: 11px;"
            "}"
            "QPushButton#expand_btn:hover {"
            "background: rgba(9,105,218,0.10);"
            f"color: {ACCENT_BLUE};"
            "}"
        )

    def _mode_style(self, active: bool) -> str:
        if active:
            return (
                f"background: rgba(9,105,218,0.16); color: {ACCENT_BLUE};"
                f"border: 1px solid rgba(9,105,218,0.32); border-radius: 4px;"
                "font-weight: 700;"
            )
        return (
            "background: transparent;"
            f"color: {TEXT_MUTED}; border: 1px solid {BORDER_COLOR};"
            "border-radius: 4px;"
        )

    def _build_chart(self):
        self._chart_widget = QWidget()
        self._chart_widget.setFixedHeight(168)
        c_lay = QVBoxLayout(self._chart_widget)
        c_lay.setContentsMargins(8, 4, 8, 8)
        c_lay.setSpacing(0)

        pg.setConfigOptions(antialias=True)
        self._pw = pg.PlotWidget()
        self._pw.setBackground(BG_DEEP)
        self._pw.showGrid(x=True, y=True, alpha=0.20)
        self._pw.setLabel("bottom", "s", color=TEXT_MUTED, size="9pt")

        tick_font = QFont("Consolas")
        tick_font.setPointSize(9)
        for axis in ("left", "bottom"):
            self._pw.getAxis(axis).setTickFont(tick_font)
            self._pw.getAxis(axis).setTextPen(pg.mkPen(TEXT_MUTED))
            self._pw.getAxis(axis).setPen(pg.mkPen(BORDER_COLOR))
        self._pw.getAxis("left").setWidth(40)

        self._curve = self._pw.plot([], [], pen=pg.mkPen(color=self._color, width=1.7))
        c_lay.addWidget(self._pw)
        self._root_layout.addWidget(self._chart_widget)
        self._configure_chart_axis()

    def _toggle(self):
        self._expanded = not self._expanded
        self._chart_widget.setVisible(self._expanded)
        self._toggle_btn.setText("▴" if self._expanded else "▾")
        if self._expanded:
            self._refresh_curve()
        if self.parent():
            self.parent().updateGeometry()
            self.parent().adjustSize()

    def _set_mode(self, mode: str):
        self._mode = mode
        self._refresh_mode_buttons()
        self._configure_chart_axis()
        self._refresh_curve()

    def _refresh_mode_buttons(self):
        self._voltage_mode_btn.setStyleSheet(self._mode_style(self._mode == "voltage"))
        self._temp_mode_btn.setStyleSheet(self._mode_style(self._mode == "temperature"))

    def _configure_chart_axis(self):
        if self._mode == "temperature":
            self._pw.setLabel("left", "°C", color=TEXT_MUTED, size="9pt")
            self._pw.setYRange(TEMP_MIN, TEMP_MAX, padding=0.03)
            self._curve.setPen(pg.mkPen(color=ACCENT_BLUE, width=1.7))
        else:
            self._pw.setLabel("left", "V", color=TEXT_MUTED, size="9pt")
            self._pw.setYRange(VOLTAGE_MIN, VOLTAGE_MAX, padding=0.03)
            self._curve.setPen(pg.mkPen(color=self._color, width=1.7))

    def push_value(self, voltage: float | None = None, temperature: float | None = None):
        now = time.monotonic() - self._t0
        if not self._t_buf or now != self._t_buf[-1]:
            self._t_buf.append(now)

        if (
            voltage is None
            and temperature is not None
            and self._last_voltage is None
            and self._mode != "temperature"
        ):
            self._set_mode("temperature")

        if voltage is not None:
            v = max(VOLTAGE_MIN, min(VOLTAGE_MAX, float(voltage)))
            self._last_voltage = v
            self._voltage_buf.append(v)
            v_color = _status_color(v, warn=20.0, alarm=25.0)
            if v < 20.0:
                v_color = self._color
            self._voltage_lbl.setText(f"{v:7.3f} V")
            self._voltage_lbl.setStyleSheet(self._metric_style(v_color, 20))

        if temperature is not None:
            t = max(TEMP_MIN, min(TEMP_MAX, float(temperature)))
            self._last_temperature = t
            self._temperature_buf.append(t)
            t_color = _status_color(t, warn=75.0, alarm=85.0)
            self._temperature_lbl.setText(f"{t:5.1f} °C")
            self._temperature_lbl.setStyleSheet(self._metric_style(t_color, 17))

        if self._expanded:
            self._refresh_curve()

    def _aligned_series(self) -> tuple[np.ndarray, np.ndarray] | None:
        values = self._temperature_buf if self._mode == "temperature" else self._voltage_buf
        if not values:
            return None
        ts = np.array(list(self._t_buf)[-len(values):], dtype=float)
        vs = np.array(values, dtype=float)
        if len(ts) != len(vs):
            n = min(len(ts), len(vs))
            ts = ts[-n:]
            vs = vs[-n:]
        return ts, vs

    def _refresh_curve(self):
        series = self._aligned_series()
        if series is None:
            self._curve.setData([], [])
            return
        ts, vs = series
        t_end = ts[-1]
        self._curve.setData(ts - t_end, vs)
        self._pw.setXRange(-HISTORY_SECS, 0, padding=0.02)

    def reset(self):
        self._voltage_buf.clear()
        self._temperature_buf.clear()
        self._t_buf.clear()
        self._last_voltage = None
        self._last_temperature = None
        self._voltage_lbl.setText("--.--- V")
        self._temperature_lbl.setText("--.- °C")
        self._curve.setData([], [])


class VoltageView(QWidget):
    """16 通道槽电压与通道温度监控。"""

    def __init__(self, channels: list[str] | None = None, parent=None):
        super().__init__(parent)
        self._channels = channels or DEFAULT_CHANNELS
        self._cards: dict[str, _ChannelCard] = {}
        self._all_expanded = False
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame()
        header.setObjectName("page_header")
        header.setFixedHeight(58)
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(20, 0, 20, 0)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel("槽电压监控")
        title.setObjectName("page_title")
        subtitle = QLabel(
            f"{len(self._channels)} 通道 | 电压分辨率 0.001 V | 温度分辨率 0.1 °C | 支持 TCP/UDP 实时帧"
        )
        subtitle.setObjectName("page_subtitle")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        h_lay.addLayout(title_col)
        h_lay.addStretch()

        self._expand_all_btn = QPushButton("全部展开")
        self._expand_all_btn.setFixedWidth(92)
        self._expand_all_btn.setObjectName("close_btn")
        self._expand_all_btn.setStyleSheet(
            f"QPushButton{{background:rgba(9,105,218,0.12);color:{ACCENT_BLUE};"
            "border:1px solid rgba(9,105,218,0.2); border-radius:4px; padding:4px 0;}}"
            "QPushButton:hover{background:rgba(9,105,218,0.25);}"
        )
        self._expand_all_btn.clicked.connect(self._toggle_all)
        h_lay.addWidget(self._expand_all_btn)

        root.addWidget(header)

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

        cols = 2
        for i, ch in enumerate(self._channels):
            color = CHANNEL_COLORS[i % len(CHANNEL_COLORS)]
            card = _ChannelCard(ch, color)
            self._cards[ch] = card
            row, col = divmod(i, cols)
            grid.addWidget(card, row, col)

        for c in range(cols):
            grid.setColumnStretch(c, 1)

        scroll.setWidget(container)
        root.addWidget(scroll, stretch=1)

    def _toggle_all(self):
        self._all_expanded = not self._all_expanded
        self._expand_all_btn.setText("全部折叠" if self._all_expanded else "全部展开")
        for card in self._cards.values():
            if card._expanded != self._all_expanded:
                card._toggle()

    def _parse_payload(self, data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        if "channels" in data:
            channels = data.get("channels") or {}
            temperatures = (
                data.get("channel_temperatures")
                or data.get("temperatures")
                or data.get("channel_temperature")
                or {}
            )
            return channels, temperatures

        channels: dict[str, Any] = {}
        temperatures: dict[str, Any] = {}
        for ch, payload in data.items():
            if ch not in self._cards:
                continue
            if isinstance(payload, dict):
                channels[ch] = payload.get("voltage", payload.get("value"))
                temperatures[ch] = payload.get("temperature", payload.get("temp"))
            else:
                channels[ch] = payload
        return channels, temperatures

    def update_channels(self, data: dict):
        channels, temperatures = self._parse_payload(data)
        for ch, card in self._cards.items():
            voltage = channels.get(ch)
            temperature = temperatures.get(ch)
            if isinstance(voltage, dict):
                temperature = voltage.get("temperature", voltage.get("temp", temperature))
                voltage = voltage.get("voltage", voltage.get("value"))
            if voltage is None and temperature is None:
                continue
            card.push_value(voltage=voltage, temperature=temperature)

    def reset(self):
        for card in self._cards.values():
            card.reset()
