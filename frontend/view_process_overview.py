"""
view_process_overview.py
工况总览页：统一显示槽电压之外的 6 个关键物理量。
"""
from __future__ import annotations

from collections import deque
from datetime import datetime
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
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .styles import (
    ACCENT_AMBER,
    ACCENT_BLUE,
    ACCENT_GREEN,
    ACCENT_RED,
    BG_CARD,
    BG_DEEP,
    BORDER_COLOR,
    CHANNEL_COLORS,
    TEXT_MUTED,
    TEXT_PRIMARY,
)

HISTORY_POINTS = 2000

PROCESS_METRICS = [
    {
        "key": "oxygen",
        "label": "氧气浓度",
        "sensor_id": "O2-01",
        "unit": "%Vol",
        "decimals": 2,
        "normal": (18.0, 23.5),
        "source": ("o2_concentration", 0),
        "scale": 100.0,
        "color": "#0969da",
    },
    {
        "key": "hydrogen",
        "label": "氢气浓度",
        "sensor_id": "H2-01",
        "unit": "%Vol",
        "decimals": 2,
        "normal": (0.0, 4.0),
        "source": ("h2_concentration", 0),
        "scale": 100.0,
        "color": "#1a7f37",
    },
    {
        "key": "temperature_1",
        "label": "温度 1",
        "sensor_id": "T-01",
        "unit": "°C",
        "decimals": 1,
        "normal": (55.0, 80.0),
        "source": ("temperature", 0),
        "scale": 1.0,
        "color": "#bf8700",
    },
    {
        "key": "temperature_2",
        "label": "温度 2",
        "sensor_id": "T-02",
        "unit": "°C",
        "decimals": 1,
        "normal": (55.0, 80.0),
        "source": ("temperature", 1),
        "scale": 1.0,
        "color": "#d29922",
    },
    {
        "key": "pressure_1",
        "label": "压力 1",
        "sensor_id": "P-01",
        "unit": "kPa",
        "decimals": 1,
        "normal": (95.0, 108.0),
        "source": ("pressure", 0),
        "scale": 1.0,
        "color": "#8250df",
    },
    {
        "key": "pressure_2",
        "label": "压力 2",
        "sensor_id": "P-02",
        "unit": "kPa",
        "decimals": 1,
        "normal": (95.0, 108.0),
        "source": ("pressure", 1),
        "scale": 1.0,
        "color": "#6f42c1",
    },
]

_METRIC_BY_KEY = {m["key"]: m for m in PROCESS_METRICS}
_ID_TO_KEY = {m["sensor_id"]: m["key"] for m in PROCESS_METRICS}


def _normalize_ts(ts: Any) -> str:
    if isinstance(ts, datetime):
        return ts.strftime("%Y-%m-%d %H:%M:%S")
    if ts:
        return str(ts)
    return "--"


def _level_for(value: float, normal: tuple[float, float]) -> str:
    lo, hi = normal
    span = hi - lo
    if value < lo or value > hi:
        return "alarm"
    if value < lo + 0.12 * span or value > hi - 0.12 * span:
        return "warning"
    return "normal"


def _level_color(level: str) -> str:
    if level == "alarm":
        return ACCENT_RED
    if level == "warning":
        return ACCENT_AMBER
    return ACCENT_GREEN


class _MetricCard(QFrame):
    def __init__(self, metric: dict[str, Any], parent=None):
        super().__init__(parent)
        self._metric = metric
        self._buf: deque[float] = deque(maxlen=HISTORY_POINTS)
        self._index_buf: deque[int] = deque(maxlen=HISTORY_POINTS)
        self._tick = 0
        self._current_normal = metric["normal"]

        self.setObjectName("metric_card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(174)
        self.setStyleSheet(
            "#metric_card {"
            f"background: {BG_CARD}; border: 1px solid {BORDER_COLOR};"
            f"border-left: 4px solid {metric['color']}; border-radius: 6px;"
            "}"
        )
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(8)

        top = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        title = QLabel(self._metric["label"])
        title.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 15px; font-weight: 700;")
        sid = QLabel(self._metric["sensor_id"])
        sid.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px;")
        title_col.addWidget(title)
        title_col.addWidget(sid)

        self._status_lbl = QLabel("离线")
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_lbl.setFixedWidth(58)
        self._status_lbl.setStyleSheet(self._pill_style(TEXT_MUTED))

        top.addLayout(title_col)
        top.addStretch()
        top.addWidget(self._status_lbl)
        root.addLayout(top)

        value_row = QHBoxLayout()
        self._value_lbl = QLabel("--")
        font = QFont("Consolas")
        font.setPixelSize(31)
        font.setBold(True)
        self._value_lbl.setFont(font)
        self._value_lbl.setStyleSheet(f"color: {self._metric['color']};")
        self._unit_lbl = QLabel(self._metric["unit"])
        self._unit_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px; padding-top: 12px;")
        value_row.addWidget(self._value_lbl)
        value_row.addWidget(self._unit_lbl)
        value_row.addStretch()
        root.addLayout(value_row)

        lo, hi = self._metric["normal"]
        self._range_lbl = QLabel(f"正常区间 {lo:g}-{hi:g} {self._metric['unit']}")
        self._range_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px;")
        root.addWidget(self._range_lbl)

        pg.setConfigOptions(antialias=True)
        self._spark = pg.PlotWidget()
        self._spark.setBackground(BG_CARD)
        self._spark.setFixedHeight(52)
        self._spark.hideAxis("left")
        self._spark.hideAxis("bottom")
        self._spark.setMouseEnabled(x=False, y=False)
        self._spark.setMenuEnabled(False)
        self._spark.showGrid(x=False, y=True, alpha=0.10)
        self._curve = self._spark.plot([], [], pen=pg.mkPen(self._metric["color"], width=1.6))
        root.addWidget(self._spark)

    def _pill_style(self, color: str) -> str:
        return (
            f"color: {color}; background: rgba(9,105,218,0.06);"
            f"border: 1px solid {BORDER_COLOR}; border-radius: 4px;"
            "font-size: 11px; font-weight: 700; padding: 3px 0;"
        )

    def update_value(
        self,
        value: float,
        status: str | None = None,
        unit: str | None = None,
        normal: tuple[float, float] | None = None,
        decimals: int | None = None,
        values: list[float] | tuple[float, ...] | None = None,
    ):
        series = [float(item) for item in values] if values else [float(value)]
        if not series:
            return
        value = float(series[-1])
        metric = self._metric
        display_unit = unit or metric["unit"]
        display_normal = normal or metric["normal"]
        display_decimals = int(decimals if decimals is not None else metric["decimals"])
        self._current_normal = display_normal
        level = status or _level_for(value, display_normal)
        color = _level_color(level)
        status_text = {"normal": "正常", "warning": "预警", "alarm": "报警"}.get(level, "正常")

        self._status_lbl.setText(status_text)
        self._status_lbl.setStyleSheet(self._pill_style(color))
        self._value_lbl.setText(f"{value:.{display_decimals}f}")
        self._value_lbl.setStyleSheet(f"color: {color};")
        self._unit_lbl.setText(display_unit)
        lo, hi = display_normal
        self._range_lbl.setText(f"正常区间 {lo:g}-{hi:g} {display_unit}")

        for sample in series:
            self._tick += 1
            self._buf.append(sample)
            self._index_buf.append(self._tick)
        self._refresh_curve()

    def _refresh_curve(self):
        if not self._buf:
            return
        xs = np.array(self._index_buf, dtype=float)
        ys = np.array(self._buf, dtype=float)
        self._curve.setData(xs - xs[-1], ys)
        lo, hi = self._current_normal
        ymin = float(ys.min())
        ymax = float(ys.max())
        min_span = max((hi - lo) * 0.03, 0.10)
        if ymax - ymin < min_span:
            center = (ymax + ymin) / 2.0
            ymin = center - min_span / 2.0
            ymax = center + min_span / 2.0
        pad = (ymax - ymin) * 0.18
        self._spark.setYRange(ymin - pad, ymax + pad, padding=0)


class ProcessOverviewView(QWidget):
    """显示氧气、氢气、两路温度、两路压力的统一页面。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cards: dict[str, _MetricCard] = {}
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
        title = QLabel("工况总览")
        title.setObjectName("page_title")
        subtitle = QLabel("氧气浓度、氢气浓度、两路温度、两路压力")
        subtitle.setObjectName("page_subtitle")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        h_lay.addLayout(title_col)
        h_lay.addStretch()

        self._clock_lbl = QLabel("最近更新 --")
        self._clock_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        h_lay.addWidget(self._clock_lbl)
        root.addWidget(header)

        body = QWidget()
        body.setStyleSheet(f"background: {BG_DEEP};")
        grid = QGridLayout(body)
        grid.setContentsMargins(16, 16, 16, 16)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)

        for i, metric in enumerate(PROCESS_METRICS):
            card = _MetricCard(metric)
            self._cards[metric["key"]] = card
            row, col = divmod(i, 3)
            grid.addWidget(card, row, col)

        for col in range(3):
            grid.setColumnStretch(col, 1)
        grid.setRowStretch(2, 1)
        root.addWidget(body, stretch=1)

    def update_process(self, data: dict[str, Any]):
        sensors = data.get("sensors", data)
        for key, metric in _METRIC_BY_KEY.items():
            payload = sensors.get(key) or sensors.get(metric["sensor_id"])
            if payload is None:
                continue
            if isinstance(payload, dict):
                value = payload.get("value")
                status = payload.get("status") or payload.get("level")
                unit = payload.get("unit")
                normal = payload.get("normal")
                decimals = payload.get("decimals")
                values = payload.get("values")
            else:
                value = payload
                status = None
                unit = None
                normal = None
                decimals = None
                values = None
            if value is None:
                continue
            self._cards[key].update_value(
                float(value),
                status=status,
                unit=unit,
                normal=normal,
                decimals=decimals,
                values=values,
            )

        self._clock_lbl.setText(f"最近更新 {_normalize_ts(data.get('timestamp'))}")

    def update_from_field_frame(self, frame: dict[str, Any]):
        fields = frame.get("fields", {})
        sensors: dict[str, dict[str, Any]] = {}

        for metric in PROCESS_METRICS:
            field_name, index = metric["source"]
            readings = fields.get(field_name, [])
            if index >= len(readings):
                continue
            raw = readings[index].get("value")
            if raw is None:
                continue
            value = float(raw) * float(metric.get("scale", 1.0))
            sensors[metric["key"]] = {
                "value": value,
                "status": _level_for(value, metric["normal"]),
            }

        if sensors:
            self.update_process({"timestamp": frame.get("timestamp"), "sensors": sensors})

    @staticmethod
    def sensor_key_from_id(sensor_id: str) -> str | None:
        return _ID_TO_KEY.get(sensor_id)
