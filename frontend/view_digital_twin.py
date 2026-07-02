"""
view_digital_twin.py
电解槽数字孪生页。

界面包含：
  - 电解槽 3D 模型
  - 6 个传感器位置点
  - 点击点位后的传感器数值小窗
  - 故障预警与运行参数建议
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import numpy as np
import pyqtgraph.opengl as gl
from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QFrame,
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
    TEXT_MUTED,
    TEXT_PRIMARY,
)
from .view_process_overview import PROCESS_METRICS

SENSOR_POSITIONS = {
    "oxygen": np.array([0.0, 18.0, 18.0], dtype=float),
    "hydrogen": np.array([0.0, -18.0, 18.0], dtype=float),
    "temperature_1": np.array([-34.0, -9.0, 14.0], dtype=float),
    "temperature_2": np.array([34.0, -9.0, 14.0], dtype=float),
    "pressure_1": np.array([-34.0, 10.0, 9.0], dtype=float),
    "pressure_2": np.array([34.0, 10.0, 9.0], dtype=float),
}

_METRIC_BY_KEY = {m["key"]: m for m in PROCESS_METRICS}


def _status_color(status: str | None) -> str:
    if status == "alarm":
        return ACCENT_RED
    if status == "warning":
        return ACCENT_AMBER
    return ACCENT_GREEN


def _qcolor_to_gl(hex_color: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    color = QColor(hex_color)
    return (color.redF(), color.greenF(), color.blueF(), alpha)


def _normalize_ts(ts: Any) -> str:
    if isinstance(ts, datetime):
        return ts.strftime("%Y-%m-%d %H:%M:%S")
    if ts:
        return str(ts)
    return "--"


def _box_mesh(center, size):
    cx, cy, cz = center
    sx, sy, sz = [v / 2.0 for v in size]
    vertices = np.array(
        [
            [cx - sx, cy - sy, cz - sz],
            [cx + sx, cy - sy, cz - sz],
            [cx + sx, cy + sy, cz - sz],
            [cx - sx, cy + sy, cz - sz],
            [cx - sx, cy - sy, cz + sz],
            [cx + sx, cy - sy, cz + sz],
            [cx + sx, cy + sy, cz + sz],
            [cx - sx, cy + sy, cz + sz],
        ],
        dtype=float,
    )
    faces = np.array(
        [
            [0, 1, 2],
            [0, 2, 3],
            [4, 5, 6],
            [4, 6, 7],
            [0, 1, 5],
            [0, 5, 4],
            [1, 2, 6],
            [1, 6, 5],
            [2, 3, 7],
            [2, 7, 6],
            [3, 0, 4],
            [3, 4, 7],
        ],
        dtype=int,
    )
    return vertices, faces


class _TwinGLView(gl.GLViewWidget):
    sensor_clicked = pyqtSignal(str, QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._keys = list(SENSOR_POSITIONS.keys())
        self._positions = np.array([SENSOR_POSITIONS[k] for k in self._keys], dtype=float)
        self.HIT_RADIUS = 18.0
        self.setMouseTracking(True)

    def _build_mvp(self) -> np.ndarray | None:
        try:
            d = float(self.opts["distance"])
            elev = float(self.opts["elevation"])
            azim = float(self.opts["azimuth"])
            ctr = self.opts["center"]
            fov = float(self.opts["fov"])
            w, h = max(self.width(), 1), max(self.height(), 1)

            def rx(deg):
                r = math.radians(deg)
                c, s = math.cos(r), math.sin(r)
                return np.array([[1, 0, 0, 0], [0, c, -s, 0], [0, s, c, 0], [0, 0, 0, 1]], np.float64)

            def rz(deg):
                r = math.radians(deg)
                c, s = math.cos(r), math.sin(r)
                return np.array([[c, -s, 0, 0], [s, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], np.float64)

            def translate(tx, ty, tz):
                mat = np.eye(4, dtype=np.float64)
                mat[0, 3] = tx
                mat[1, 3] = ty
                mat[2, 3] = tz
                return mat

            cx, cy, cz = ctr.x(), ctr.y(), ctr.z()
            mv = translate(0, 0, -d) @ rx(elev - 90) @ rz(-(azim + 90)) @ translate(-cx, -cy, -cz)
            near, far = d * 0.001, d * 1000.0
            aspect = w / h
            focal = 1.0 / math.tan(math.radians(fov) / 2.0)
            proj = np.array(
                [
                    [focal / aspect, 0, 0, 0],
                    [0, focal, 0, 0],
                    [0, 0, (far + near) / (near - far), 2 * far * near / (near - far)],
                    [0, 0, -1, 0],
                ],
                np.float64,
            )
            return proj @ mv
        except Exception:
            return None

    def _project(self):
        mvp = self._build_mvp()
        if mvp is None:
            return None, None
        ones = np.ones((len(self._positions), 1), np.float64)
        ph = np.hstack([self._positions, ones])
        clip = (mvp @ ph.T).T
        w = clip[:, 3]
        ok = np.abs(w) > 1e-6
        ndx = np.where(ok, clip[:, 0] / w, -9999.0)
        ndy = np.where(ok, clip[:, 1] / w, -9999.0)
        wh, hh = self.width() / 2.0, self.height() / 2.0
        screen = np.zeros((len(self._positions), 2), np.float64)
        screen[:, 0] = (ndx + 1.0) * wh
        screen[:, 1] = (1.0 - ndy) * hh
        return screen, w

    def _nearest_key(self, pos):
        screen, w_clip = self._project()
        if screen is None or w_clip is None:
            return None, float("inf")
        mx, my = float(pos.x()), float(pos.y())
        dist = np.sqrt((screen[:, 0] - mx) ** 2 + (screen[:, 1] - my) ** 2)
        dist[w_clip <= 0] = np.inf
        idx = int(np.argmin(dist))
        return self._keys[idx], float(dist[idx])

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            key, dist = self._nearest_key(event.pos())
            if key is not None and dist <= self.HIT_RADIUS:
                self.sensor_clicked.emit(key, event.pos())
                return
        super().mousePressEvent(event)


class _SensorPopup(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sensor_popup")
        self.setStyleSheet(
            "#sensor_popup {"
            f"background: {BG_CARD}; border: 1px solid {ACCENT_BLUE};"
            "border-radius: 6px;"
            "}"
        )
        self.setFixedWidth(220)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(4)

        self._title = QLabel("--")
        self._title.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 14px; font-weight: 700;")
        self._value = QLabel("--")
        font = QFont("Consolas")
        font.setPixelSize(24)
        font.setBold(True)
        self._value.setFont(font)
        self._value.setStyleSheet(f"color: {ACCENT_GREEN};")
        self._status = QLabel("--")
        self._status.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")

        lay.addWidget(self._title)
        lay.addWidget(self._value)
        lay.addWidget(self._status)
        self.hide()

    def show_sensor(self, key: str, payload: dict[str, Any], at: QPoint):
        metric = _METRIC_BY_KEY.get(key, {})
        label = payload.get("label") or metric.get("label", key)
        value = payload.get("value")
        unit = payload.get("unit") or metric.get("unit", "")
        decimals = int(metric.get("decimals", 2))
        status = payload.get("status", "normal")
        color = _status_color(status)
        status_text = {"normal": "状态正常", "warning": "模型预警", "alarm": "故障报警"}.get(status, "状态正常")

        self._title.setText(f"{label}  {metric.get('sensor_id', '')}")
        if isinstance(value, (int, float)):
            self._value.setText(f"{float(value):.{decimals}f} {unit}")
        else:
            self._value.setText(f"-- {unit}".strip())
        self._value.setStyleSheet(f"color: {color};")
        self._status.setText(status_text)
        self.adjustSize()

        parent = self.parent()
        x, y = at.x() + 14, at.y() + 14
        if parent:
            if x + self.width() > parent.width():
                x = at.x() - self.width() - 10
            if y + self.height() > parent.height():
                y = at.y() - self.height() - 10
        self.move(max(0, x), max(0, y))
        self.show()
        self.raise_()


class DigitalTwinView(QWidget):
    """电解槽数字孪生页面。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sensor_payloads: dict[str, dict[str, Any]] = {}
        self._sensor_scatter = None
        self._build_ui()
        self._build_scene()
        self.update_twin(self._demo_payload())

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
        title = QLabel("数字孪生")
        title.setObjectName("page_title")
        subtitle = QLabel("电解槽 3D 模型 | 6 个传感器点位 | 模型预警与运行建议")
        subtitle.setObjectName("page_subtitle")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        h_lay.addLayout(title_col)
        h_lay.addStretch()
        self._clock_lbl = QLabel("模型帧 --")
        self._clock_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        h_lay.addWidget(self._clock_lbl)
        root.addWidget(header)

        scene_wrap = QWidget()
        scene_wrap.setStyleSheet(f"background: {BG_DEEP};")
        scene_lay = QVBoxLayout(scene_wrap)
        scene_lay.setContentsMargins(0, 0, 0, 0)
        scene_lay.setSpacing(0)

        self._gl_view = _TwinGLView()
        self._gl_view.setBackgroundColor(QColor(BG_DEEP))
        self._gl_view.setCameraPosition(distance=105, elevation=22, azimuth=-35)
        self._gl_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._gl_view.sensor_clicked.connect(self._on_sensor_clicked)
        scene_lay.addWidget(self._gl_view, stretch=1)
        self._popup = _SensorPopup(self._gl_view)

        root.addWidget(scene_wrap, stretch=1)

        bottom = QFrame()
        bottom.setFixedHeight(172)
        bottom.setStyleSheet(f"background: {BG_CARD}; border-top: 1px solid {BORDER_COLOR};")
        b_lay = QHBoxLayout(bottom)
        b_lay.setContentsMargins(16, 10, 16, 10)
        b_lay.setSpacing(12)

        self._warning_panel = self._build_panel("故障预警")
        self._advice_panel = self._build_panel("运行参数建议")
        b_lay.addWidget(self._warning_panel, stretch=1)
        b_lay.addWidget(self._advice_panel, stretch=1)
        root.addWidget(bottom)

    def _build_panel(self, title: str) -> QFrame:
        panel = QFrame()
        panel.setObjectName("twin_panel")
        panel.setStyleSheet(
            "#twin_panel {"
            f"background: {BG_DEEP}; border: 1px solid {BORDER_COLOR}; border-radius: 6px;"
            "}"
        )
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(6)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 13px; font-weight: 700;")
        lay.addWidget(title_lbl)
        content = QVBoxLayout()
        content.setSpacing(4)
        lay.addLayout(content)
        lay.addStretch()
        panel._content_layout = content  # type: ignore[attr-defined]
        return panel

    def _build_scene(self):
        grid = gl.GLGridItem()
        grid.setSize(100, 60)
        grid.setSpacing(10, 10)
        grid.setColor((0.70, 0.73, 0.77, 0.36))
        self._gl_view.addItem(grid)

        self._add_box(center=(0, 0, 8), size=(76, 28, 18), color=(0.72, 0.78, 0.84, 0.52))
        self._add_box(center=(0, 0, 21), size=(70, 22, 8), color=(0.38, 0.52, 0.68, 0.42))
        self._add_box(center=(-42, 0, 8), size=(6, 32, 22), color=(0.16, 0.32, 0.52, 0.70))
        self._add_box(center=(42, 0, 8), size=(6, 32, 22), color=(0.16, 0.32, 0.52, 0.70))

        for x in np.linspace(-28, 28, 8):
            self._add_box(center=(float(x), 0, 8), size=(1.6, 29, 20), color=(0.12, 0.24, 0.36, 0.36))

        for y, color in [(-23, (0.10, 0.44, 0.22, 0.70)), (23, (0.65, 0.18, 0.18, 0.70))]:
            pts = np.array([[-42, y, 14], [42, y, 14]], dtype=float)
            pipe = gl.GLLinePlotItem(pos=pts, color=color, width=5, antialias=True)
            self._gl_view.addItem(pipe)

        self._sensor_scatter = gl.GLScatterPlotItem(
            pos=np.array(list(SENSOR_POSITIONS.values()), dtype=np.float32),
            color=np.ones((len(SENSOR_POSITIONS), 4), dtype=np.float32),
            size=np.full((len(SENSOR_POSITIONS),), 13.0, dtype=np.float32),
            pxMode=True,
        )
        self._sensor_scatter.setGLOptions("translucent")
        self._gl_view.addItem(self._sensor_scatter)
        self._refresh_sensor_points()

    def _add_box(self, center, size, color):
        vertices, faces = _box_mesh(center, size)
        face_colors = np.tile(np.array(color, dtype=float), (len(faces), 1))
        item = gl.GLMeshItem(
            vertexes=vertices,
            faces=faces,
            faceColors=face_colors,
            smooth=False,
            drawEdges=True,
            edgeColor=(0.10, 0.17, 0.24, 0.35),
        )
        item.setGLOptions("translucent")
        self._gl_view.addItem(item)

    def _on_sensor_clicked(self, key: str, gl_pos: QPoint):
        payload = self._sensor_payloads.get(key, {})
        self._popup.show_sensor(key, payload, gl_pos)

    def update_twin(self, data: dict[str, Any]):
        sensors = data.get("sensors", {})
        for key, metric in _METRIC_BY_KEY.items():
            payload = sensors.get(key) or sensors.get(metric["sensor_id"])
            if payload is None:
                payload = self._sensor_payloads.get(key, {})
            if not isinstance(payload, dict):
                payload = {"value": payload}
            merged = {
                "label": metric["label"],
                "unit": metric["unit"],
                "status": "normal",
                **payload,
            }
            self._sensor_payloads[key] = merged

        self._refresh_sensor_points()
        self._render_list(self._warning_panel, data.get("warnings", []), empty="当前无模型预警", warning=True)
        self._render_list(self._advice_panel, data.get("recommendations", []), empty="运行参数保持当前设定", warning=False)
        self._clock_lbl.setText(f"模型帧 {_normalize_ts(data.get('timestamp'))}")

    def update_from_process(self, data: dict[str, Any]):
        self.update_twin({"timestamp": data.get("timestamp"), "sensors": data.get("sensors", data)})

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
            sensors[metric["key"]] = {"value": value, "status": "normal"}
        if sensors:
            self.update_twin({"timestamp": frame.get("timestamp"), "sensors": sensors})

    def _refresh_sensor_points(self):
        if self._sensor_scatter is None:
            return
        keys = list(SENSOR_POSITIONS.keys())
        colors = []
        for key in keys:
            status = self._sensor_payloads.get(key, {}).get("status", "normal")
            colors.append(_qcolor_to_gl(_status_color(status), alpha=0.95))
        self._sensor_scatter.setData(
            pos=np.array([SENSOR_POSITIONS[k] for k in keys], dtype=np.float32),
            color=np.array(colors, dtype=np.float32),
            size=np.full((len(keys),), 13.0, dtype=np.float32),
        )

    def _render_list(self, panel: QFrame, rows: list[Any], empty: str, warning: bool):
        layout = panel._content_layout  # type: ignore[attr-defined]
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not rows:
            rows = [{"message": empty, "level": "normal"}]

        for row in rows[:4]:
            if isinstance(row, str):
                message = row
                level = "warning" if warning else "normal"
            else:
                message = row.get("message") or row.get("text") or row.get("suggestion") or str(row)
                level = row.get("level") or row.get("status") or ("warning" if warning else "normal")
            label = QLabel(message)
            label.setWordWrap(True)
            label.setStyleSheet(
                f"color: {_status_color(level)}; font-size: 11px; padding: 2px 0;"
            )
            layout.addWidget(label)

    def _demo_payload(self) -> dict[str, Any]:
        return {
            "timestamp": datetime.now(),
            "sensors": {
                "oxygen": {"value": 20.6, "status": "normal"},
                "hydrogen": {"value": 2.7, "status": "normal"},
                "temperature_1": {"value": 68.4, "status": "normal"},
                "temperature_2": {"value": 69.1, "status": "normal"},
                "pressure_1": {"value": 101.4, "status": "normal"},
                "pressure_2": {"value": 101.0, "status": "normal"},
            },
            "warnings": [],
            "recommendations": [{"message": "维持当前电流密度与冷却水流量。", "level": "normal"}],
        }
