"""
view_3d.py
3D 传感器散点云图页面

交互逻辑：
  - 鼠标悬停接近传感器点 → 显示 _HoverCard（轻量浮动信息卡）
  - 左键单击接近传感器点 → 创建/切换 _PinnedCard
      · _PinnedCard 是固定在 3D 点屏幕投影位置的叠加卡片
      · 固定像素大小，不随 3D 缩放变化；版面始终朝向用户（2D Qt 控件）
      · 每帧重新投影锚点 3D 坐标，随旋转/平移跟随移动
      · 再次单击同一点 → 取消固定
  - 右侧色条（ColorBar）：实时渐变图例，顶部最大值，底部最小值
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QTimer, QRectF, QPointF
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QSizePolicy, QPushButton
)
from PyQt6.QtGui import (
    QFont, QPainter, QColor, QPen, QBrush, QPainterPath,
    QLinearGradient
)

import pyqtgraph.opengl as gl
import pyqtgraph as pg

from .styles import (
    BG_DEEP, BG_CARD, BORDER_COLOR, ACCENT_BLUE, ACCENT_GREEN,
    TEXT_PRIMARY, TEXT_MUTED, scalar_to_rgba, hex_to_gl,
    CHANNEL_COLORS
)


# ── 悬停浮动信息卡 ──────────────────────────────────────────────────────────
class _HoverCard(QFrame):
    """鼠标悬停在传感器点上时显示的轻量浮动卡片。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("hover_card")
        self.setStyleSheet(
            "#hover_card {"
            f"  background: {BG_CARD};"
            f"  border: 1px solid {ACCENT_BLUE};"
            f"  border-radius: 6px;"
            "}"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedWidth(180)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(3)

        self._id_lbl = QLabel()
        _f_id = QFont("Segoe UI")
        _f_id.setPixelSize(13)
        _f_id.setBold(True)
        self._id_lbl.setFont(_f_id)
        self._id_lbl.setStyleSheet(f"color: {ACCENT_BLUE};")

        self._val_lbl = QLabel()
        _f_val = QFont("Consolas")
        _f_val.setPixelSize(18)
        _f_val.setBold(True)
        self._val_lbl.setFont(_f_val)
        self._val_lbl.setStyleSheet(f"color: {ACCENT_GREEN};")

        self._pos_lbl = QLabel()
        self._pos_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px;")

        self._hint_lbl = QLabel("🖱 单击固定历史图表")
        self._hint_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 9px;")

        lay.addWidget(self._id_lbl)
        lay.addWidget(self._val_lbl)
        lay.addWidget(self._pos_lbl)
        lay.addWidget(self._hint_lbl)
        self.hide()

    def show_for(self, info: dict, cursor_pos: QPoint):
        sid  = info.get("id", "?")
        val  = info.get("value", 0.0)
        unit = info.get("unit", "")
        x, y, z = info.get("x", 0), info.get("y", 0), info.get("z", 0)

        self._id_lbl.setText(f"传感器  {sid}")
        self._val_lbl.setText(
            f"{val:.3f} {unit}" if isinstance(val, float) else str(val)
        )
        self._pos_lbl.setText(f"({x:.1f}, {y:.1f}, {z:.1f})")
        self.adjustSize()

        parent = self.parent()
        px, py = cursor_pos.x() + 14, cursor_pos.y() + 14
        if parent:
            if px + self.width()  > parent.width():  px = cursor_pos.x() - self.width() - 8
            if py + self.height() > parent.height(): py = cursor_pos.y() - self.height() - 8
        self.move(px, py)
        self.show()
        self.raise_()


# ── 固定历史折线图卡片（QPainter 自绘，固定像素大小，始终朝向用户）──────────
class _PinnedCard(QWidget):
    """
    单击传感器点后固定在其屏幕投影位置的历史折线图卡片。

    设计要点
    --------
    - 用 QPainter 自绘，避免嵌套 GL 上下文冲突
    - setFixedSize：固定像素大小，不随 3D 缩放变化
    - 作为 _ClickableGLView 的子 QWidget，绘制在 GL 画面之上（2D 覆盖层）
    - 始终朝向用户：因为是 2D Qt 控件而非 GL 对象
    - 每帧由父 View 重新投影锚点 3D 坐标来更新像素位置（跟随旋转/平移）
    - 再次单击同一点 → 父 View 调用 closed 信号删除本卡片
    """

    CARD_W = 240
    CARD_H = 190
    HISTORY_LEN = 300     # 5 分钟 × 60 秒

    closed = pyqtSignal()

    def __init__(self, sensor_info: dict, color: str, parent=None):
        super().__init__(parent)
        self._info  = sensor_info
        self._sid   = sensor_info.get("id", "?")
        self._unit  = sensor_info.get("unit", "")
        self._color = QColor(color)
        self._buf: deque = deque(maxlen=self.HISTORY_LEN)

        self.setFixedSize(self.CARD_W, self.CARD_H)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAutoFillBackground(False)

        self._close_rect = QRectF(self.CARD_W - 28, 4, 24, 24)

        # 预置演示历史数据
        self._load_demo()
        self.show()

    # ── 演示数据 ──────────────────────────────────────────────────────────
    def _load_demo(self):
        rng = np.random.default_rng(int(hash(self._sid)) % 2**31)
        cur = float(self._info.get("value", 50.0))
        n   = self.HISTORY_LEN
        noise = rng.normal(0, 1.5, n).cumsum()
        vals  = cur + noise - noise[0]
        kernel = np.ones(9) / 9
        vals   = np.convolve(vals, kernel, mode="same")
        for v in vals:
            self._buf.append(float(v))

    # ── 公共接口 ──────────────────────────────────────────────────────────
    def push_value(self, val: float):
        """追加新值并触发重绘。"""
        self._buf.append(float(val))
        self.update()

    # ── 绘制 ──────────────────────────────────────────────────────────────
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.CARD_W, self.CARD_H

        # 背景（圆角，半透明深色）
        bg_path = QPainterPath()
        bg_path.addRoundedRect(QRectF(0, 0, W, H), 8, 8)
        p.fillPath(bg_path, QColor(0x1c, 0x21, 0x28, 230))

        # 彩色边框
        p.setPen(QPen(self._color, 1.8))
        p.drawPath(bg_path)

        HEADER_H = 32

        # 传感器 ID
        p.setPen(QColor(ACCENT_BLUE))
        f_id = QFont("Segoe UI")
        f_id.setPixelSize(12)
        f_id.setBold(True)
        p.setFont(f_id)
        p.drawText(QRectF(10, 4, W - 44, HEADER_H - 4),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"📌 {self._sid}")

        # 当前值
        if self._buf:
            cur = self._buf[-1]
            p.setPen(self._color)
            f_cur = QFont("Consolas")
            f_cur.setPixelSize(13)
            f_cur.setBold(True)
            p.setFont(f_cur)
            p.drawText(QRectF(W // 2, 4, W // 2 - 36, HEADER_H - 4),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{cur:.3f}{' ' + self._unit if self._unit else ''}")

        # 关闭按钮 ×
        f_x = QFont("Segoe UI")
        f_x.setPixelSize(16)
        p.setPen(QColor(TEXT_MUTED))
        p.setFont(f_x)
        p.drawText(self._close_rect, Qt.AlignmentFlag.AlignCenter, "×")

        # 分隔线
        p.setPen(QPen(QColor(BORDER_COLOR), 1))
        p.drawLine(QPointF(8, HEADER_H), QPointF(W - 8, HEADER_H))

        # ── 折线图区域 ──────────────────────────────────────────────────
        ML, MR = 34, 8
        MT, MB = HEADER_H + 6, 18
        cw = W - ML - MR
        ch = H - MT - MB

        chart_rect = QRectF(ML, MT, cw, ch)
        p.fillRect(chart_rect, QColor(0x0d, 0x11, 0x17, 200))

        if len(self._buf) >= 2:
            vals = np.array(self._buf, dtype=float)
            vmin, vmax = vals.min(), vals.max()
            if vmax == vmin:
                vmin -= 0.5; vmax += 0.5
            v_range = vmax - vmin
            n = len(vals)

            # 横向参考线
            p.setPen(QPen(QColor(0x30, 0x36, 0x3d, 160), 0.8))
            for frac in (0.25, 0.5, 0.75):
                gy = MT + ch * (1.0 - frac)
                p.drawLine(QPointF(ML, gy), QPointF(ML + cw, gy))

            # Y 轴刻度
            f_tick = QFont("Consolas")
            f_tick.setPixelSize(9)
            p.setPen(QColor(TEXT_MUTED))
            p.setFont(f_tick)
            for frac in (0.0, 0.5, 1.0):
                v_label = vmin + frac * v_range
                ly = MT + ch * (1.0 - frac)
                p.drawText(QRectF(0, ly - 7, ML - 2, 14),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                           f"{v_label:.1f}")

            # X 轴时间标注
            f_xlabel = QFont("Consolas")
            f_xlabel.setPixelSize(8)
            p.setFont(f_xlabel)
            p.drawText(QRectF(ML, H - MB + 2, cw // 2, 14),
                       Qt.AlignmentFlag.AlignLeft, "−5min")
            p.drawText(QRectF(ML, H - MB + 2, cw - 2, 14),
                       Qt.AlignmentFlag.AlignRight, "now")

            # 折线
            pen_line = QPen(self._color, 1.6)
            pen_line.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen_line)

            pts = []
            for i, v in enumerate(vals):
                px_ = ML + (i / (n - 1)) * cw
                py_ = MT + ch * (1.0 - (v - vmin) / v_range)
                pts.append(QPointF(px_, py_))

            line_path = QPainterPath()
            line_path.moveTo(pts[0])
            for pt in pts[1:]:
                line_path.lineTo(pt)
            p.drawPath(line_path)

            # 折线下方填充
            fill_path = QPainterPath(line_path)
            fill_path.lineTo(QPointF(pts[-1].x(), MT + ch))
            fill_path.lineTo(QPointF(pts[0].x(),  MT + ch))
            fill_path.closeSubpath()
            fc = QColor(self._color)
            fc.setAlpha(30)
            p.fillPath(fill_path, fc)

        else:
            f_wait = QFont("Segoe UI")
            f_wait.setPixelSize(11)
            p.setPen(QColor(TEXT_MUTED))
            p.setFont(f_wait)
            p.drawText(chart_rect, Qt.AlignmentFlag.AlignCenter, "等待数据...")

        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._close_rect.contains(QPointF(event.pos())):
                self.closed.emit()
                return
        super().mousePressEvent(event)


# ── 色条图例（右侧纵向渐变，实时显示 max/min）──────────────────────────────
class _ColorBar(QWidget):
    """右侧渐变色条图例，顶部=最大值（黄），底部=最小值（深紫）。"""

    _CTRL = [
        (0.267, 0.005, 0.329),
        (0.190, 0.408, 0.663),
        (0.128, 0.566, 0.551),
        (0.478, 0.757, 0.297),
        (0.993, 0.906, 0.144),
    ]

    def __init__(self, unit: str = "", parent=None):
        super().__init__(parent)
        self._unit = unit
        self._vmin: float = 0.0
        self._vmax: float = 100.0
        self.setFixedWidth(72)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def update_range(self, vmin: float, vmax: float):
        if vmin != self._vmin or vmax != self._vmax:
            self._vmin = vmin
            self._vmax = vmax
            self.update()

    @staticmethod
    def _t_to_qcolor(t: float) -> QColor:
        ctrl = _ColorBar._CTRL
        n = len(ctrl) - 1
        idx = min(int(t * n), n - 1)
        frac = t * n - idx
        r1, g1, b1 = ctrl[idx]
        r2, g2, b2 = ctrl[idx + 1]
        return QColor(int((r1 + (r2 - r1) * frac) * 255),
                      int((g1 + (g2 - g1) * frac) * 255),
                      int((b1 + (b2 - b1) * frac) * 255))

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        TITLE_H = 22
        p.setPen(QColor(TEXT_MUTED))
        f_title = QFont("Segoe UI")
        f_title.setPixelSize(11)
        p.setFont(f_title)
        p.drawText(QRectF(0, 2, W, TITLE_H),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                   self._unit if self._unit else "值")

        BAR_TOP    = TITLE_H + 24
        BAR_BOTTOM = H - 24
        BAR_H = max(BAR_BOTTOM - BAR_TOP, 1)
        BAR_X = (W - 18) // 2
        BAR_W = 18

        grad = QLinearGradient(BAR_X, BAR_TOP, BAR_X, BAR_BOTTOM)
        for i in range(9):
            frac = i / 8
            grad.setColorAt(frac, self._t_to_qcolor(1.0 - frac))
        p.fillRect(int(BAR_X), int(BAR_TOP), BAR_W, int(BAR_H), grad)
        p.setPen(QPen(QColor(BORDER_COLOR), 1))
        p.drawRect(int(BAR_X), int(BAR_TOP), BAR_W, int(BAR_H))

        f_val = QFont("Consolas")
        f_val.setPixelSize(11)
        f_val.setBold(True)
        f_mid = QFont("Consolas")
        f_mid.setPixelSize(10)

        p.setPen(QColor(ACCENT_GREEN))
        p.setFont(f_val)
        p.drawText(QRectF(0, BAR_TOP - 20, W, 18),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                   f"{self._vmax:.2f}")

        p.setPen(QColor(ACCENT_BLUE))
        p.drawText(QRectF(0, BAR_BOTTOM + 2, W, 18),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                   f"{self._vmin:.2f}")

        mid_y   = (BAR_TOP + BAR_BOTTOM) / 2
        mid_val = (self._vmin + self._vmax) / 2
        p.setPen(QPen(QColor(BORDER_COLOR), 1))
        p.drawLine(int(BAR_X - 3), int(mid_y), int(BAR_X + BAR_W + 3), int(mid_y))
        p.setPen(QColor(TEXT_MUTED))
        p.setFont(f_mid)
        p.drawText(QRectF(0, mid_y - 7, W, 14),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                   f"{mid_val:.1f}")
        p.end()


# ── 可交互 GLViewWidget ─────────────────────────────────────────────────────
class _ClickableGLView(gl.GLViewWidget):
    """
    GLViewWidget 扩展：
      悬停 → _HoverCard 浮现
      单击 → _PinnedCard 固定到传感器 3D 位置（跟随旋转，固定像素大小，始终朝向用户）
      再次单击同一点 → 取消固定
    """

    point_clicked  = pyqtSignal(dict)
    # 悬停信号：(传感器信息, GL视图内的鼠标像素坐标)
    point_hovered  = pyqtSignal(dict, QPoint)
    point_unhovered = pyqtSignal()

    def __init__(self, point_size: float = 8.0, parent=None):
        super().__init__(parent)
        self.HIT_RADIUS = point_size * 2
        self._sensor_data: list[dict] = []
        self._positions:   np.ndarray | None = None
        # { sid: (_PinnedCard, point_index) }
        self._pinned: dict[str, tuple[_PinnedCard, int]] = {}
        self.setMouseTracking(True)

        # 每 33ms 重新投影，更新所有 PinnedCard 的屏幕位置
        self._pos_timer = QTimer(self)
        self._pos_timer.timeout.connect(self._update_pinned_positions)
        self._pos_timer.start(33)

   

    # ── 数据 ──────────────────────────────────────────────────────────────
    def set_sensor_data(self, data: list[dict]):
        self._sensor_data = data
        if data:
            self._positions = np.array(
                [[d["x"], d["y"], d["z"]] for d in data], dtype=np.float64
            )
        else:
            self._positions = None

    # ── MVP 矩阵（三级降级）────────────────────────────────────────────────
    def _build_mvp(self) -> np.ndarray | None:
        try:
            d    = float(self.opts["distance"])
            elev = float(self.opts["elevation"])
            azim = float(self.opts["azimuth"])
            ctr  = self.opts["center"]
            fov  = float(self.opts["fov"])
            w, h = max(self.width(), 1), max(self.height(), 1)

            def Rx(deg):
                r = math.radians(deg); c, s = math.cos(r), math.sin(r)
                return np.array([[1,0,0,0],[0,c,-s,0],[0,s,c,0],[0,0,0,1]], np.float64)
            def Rz(deg):
                r = math.radians(deg); c, s = math.cos(r), math.sin(r)
                return np.array([[c,-s,0,0],[s,c,0,0],[0,0,1,0],[0,0,0,1]], np.float64)
            def T(tx, ty, tz):
                m = np.eye(4, dtype=np.float64); m[0,3]=tx; m[1,3]=ty; m[2,3]=tz
                return m

            cx, cy, cz = ctr.x(), ctr.y(), ctr.z()
            mv = T(0,0,-d) @ Rx(elev-90) @ Rz(-(azim+90)) @ T(-cx,-cy,-cz)
            near, far = d * 0.001, d * 1000.0
            asp = w / h
            f   = 1.0 / math.tan(math.radians(fov) / 2.0)
            proj = np.array([
                [f/asp, 0, 0,                        0],
                [0,     f, 0,                        0],
                [0,     0, (far+near)/(near-far), 2*far*near/(near-far)],
                [0,     0, -1,                       0],
            ], np.float64)
            return proj @ mv
        except Exception:
            return None

    # ── 批量投影 ──────────────────────────────────────────────────────────
    def _project(self):
        if self._positions is None or len(self._positions) == 0:
            return None, None
        mvp = self._build_mvp()
        if mvp is None:
            return None, None
        N    = len(self._positions)
        ones = np.ones((N, 1), np.float64)
        ph   = np.hstack([self._positions, ones])
        clip = (mvp @ ph.T).T
        w    = clip[:, 3]
        ok   = np.abs(w) > 1e-6
        ndx  = np.where(ok, clip[:, 0] / w, -9999.0)
        ndy  = np.where(ok, clip[:, 1] / w, -9999.0)
        wh, hh = self.width() / 2.0, self.height() / 2.0
        sc = np.zeros((N, 2), np.float64)
        sc[:, 0] = (ndx + 1.0) * wh
        sc[:, 1] = (1.0 - ndy) * hh
        return sc, w

    def _nearest(self, pos):
        sc, w_clip = self._project()
        if sc is None or w_clip is None:
            return None, float("inf")
        mx = float(pos.x())
        my = float(pos.y())
        dx   = sc[:, 0] - mx
        dy   = sc[:, 1] - my
        dist = np.sqrt(dx**2 + dy**2)
        dist[w_clip <= 0] = np.inf
        idx  = int(np.argmin(dist))
        return idx, float(dist[idx])

    # ── 每帧更新 PinnedCard 的 2D 屏幕位置 ───────────────────────────────
    def _update_pinned_positions(self):
        if not self._pinned:
            return
        sc, w_clip = self._project()
        if sc is None:
            return
        for sid, (card, idx) in list(self._pinned.items()):
            if w_clip is not None and w_clip[idx] <= 0:
                card.hide()
                continue
            card.show()
            card.raise_()
            sx, sy = int(sc[idx, 0]), int(sc[idx, 1])
            cx = sx - card.CARD_W // 2
            cy = sy - card.CARD_H - 14     # 卡片底边中央对齐锚点正下方
            cx = max(0, min(cx, self.width()  - card.CARD_W))
            cy = max(0, min(cy, self.height() - card.CARD_H))
            card.move(cx, cy)

    def _remove_pinned(self, sid: str):
        if sid in self._pinned:
            card, _ = self._pinned.pop(sid)
            card.deleteLater()

    # ── 事件处理 ──────────────────────────────────────────────────────────
    def mouseMoveEvent(self, event):
        if self._positions is not None:
            idx, dist = self._nearest(event.pos())
            if idx is not None and dist < self.HIT_RADIUS:
                sid = self._sensor_data[idx].get("id", str(idx))
                if sid not in self._pinned:
                    # 发射信号，由父控件（普通 QWidget）负责显示悬浮卡
                    self.point_hovered.emit(self._sensor_data[idx], event.pos())
                else:
                    self.point_unhovered.emit()
            else:
                self.point_unhovered.emit()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.point_unhovered.emit()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton
                and self._positions is not None):
            idx, dist = self._nearest(event.pos())
            if idx is not None and dist < self.HIT_RADIUS:
                info = self._sensor_data[idx]
                sid  = info.get("id", str(idx))
                if sid in self._pinned:
                    self._remove_pinned(sid)       # 再次单击 → 取消固定
                else:
                    color_idx = idx % len(CHANNEL_COLORS)
                    card = _PinnedCard(info, CHANNEL_COLORS[color_idx], parent=self)
                    card.closed.connect(lambda s=sid: self._remove_pinned(s))
                    self._pinned[sid] = (card, idx)
                    self._update_pinned_positions()

                self.point_clicked.emit(info)
                self.point_unhovered.emit()
                return
        super().mousePressEvent(event)


# ── 统计信息条 ──────────────────────────────────────────────────────────────
class _StatBar(QWidget):
    def __init__(self, unit: str = "", parent=None):
        super().__init__(parent)
        self._unit = unit
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(24)
        self._labels: dict[str, QLabel] = {}

        for key, display in [("n", "传感器数"), ("min", "最小值"),
                              ("max", "最大值"), ("avg", "均值")]:
            frame = QFrame()
            frame.setObjectName("stat_card")
            fl = QVBoxLayout(frame)
            fl.setContentsMargins(10, 6, 10, 6)
            fl.setSpacing(2)

            val_lbl = QLabel("—")
            val_lbl.setObjectName("stat_value")
            _f_stat = QFont("Segoe UI")
            _f_stat.setPixelSize(19)
            _f_stat.setBold(True)
            val_lbl.setFont(_f_stat)
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            key_lbl = QLabel(display + (f" ({unit})" if unit and key != "n" else ""))
            key_lbl.setObjectName("stat_label")
            key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            fl.addWidget(val_lbl)
            fl.addWidget(key_lbl)
            self._labels[key] = val_lbl
            lay.addWidget(frame)

        lay.addStretch()

    def update_stats(self, values: np.ndarray):
        if len(values) == 0:
            return
        self._labels["n"].setText(str(len(values)))
        self._labels["min"].setText(f"{values.min():.3f}")
        self._labels["max"].setText(f"{values.max():.3f}")
        self._labels["avg"].setText(f"{values.mean():.3f}")


# ── 主 3D 视图页面 ───────────────────────────────────────────────────────────
class FieldView3D(QWidget):
    """
    三维场数据散点云图页面。

    公共接口
    --------
    update_plot(data: list[dict])
        data 列表：每元素 {id, x, y, z, value[, unit]}
    """

    point_clicked = pyqtSignal(dict)

    def __init__(
        self,
        title:      str   = "场数据",
        subtitle:   str   = "",
        unit:       str   = "",
        point_size: float = 8.0,
        parent=None,
    ):
        super().__init__(parent)
        self._title      = title
        self._subtitle   = subtitle
        self._unit       = unit
        self._point_size = point_size
        self._data: list[dict] = []

        self._build_ui()
        self._build_scene()
        self._load_demo_data()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame()
        header.setObjectName("page_header")
        header.setFixedHeight(52)
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(20, 0, 20, 0)

        title_lbl = QLabel(self._title)
        title_lbl.setObjectName("page_title")
        sub_lbl = QLabel(self._subtitle or "实时三维传感器分布")
        sub_lbl.setObjectName("page_subtitle")

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_col.addWidget(title_lbl)
        title_col.addWidget(sub_lbl)
        h_lay.addLayout(title_col)
        h_lay.addStretch()

        hint = QLabel("· 拖拽旋转  · 悬停预览  · 单击固定历史图卡  · 再次单击取消")
        hint.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        h_lay.addWidget(hint)
        root.addWidget(header)

        self._stat_bar = _StatBar(unit=self._unit)
        root.addWidget(self._stat_bar)

        # 3D 视图 + 右侧色条
        body = QWidget()
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)

        self._gl_view = _ClickableGLView(point_size=self._point_size)
        self._gl_view.setBackgroundColor(BG_DEEP)
        self._gl_view.setCameraPosition(distance=120, elevation=25, azimuth=45)
        self._gl_view.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._gl_view.point_clicked.connect(self.point_clicked)
        # 悬浮卡信号：由 FieldView3D（普通 QWidget）接收，避免 GL 层覆盖绘制
        self._gl_view.point_hovered.connect(self._on_point_hover)
        self._gl_view.point_unhovered.connect(lambda: self._hover_card.hide())
        body_lay.addWidget(self._gl_view, stretch=1)

        # 悬浮卡作为 FieldView3D 的直接子控件（普通 QWidget，不受 GL 层覆盖）
        self._hover_card = _HoverCard(self)

        colorbar_wrap = QWidget()
        colorbar_wrap.setFixedWidth(80)
        colorbar_wrap.setStyleSheet(f"background: {BG_DEEP};")
        cb_lay = QVBoxLayout(colorbar_wrap)
        cb_lay.setContentsMargins(4, 12, 4, 12)
        self._colorbar = _ColorBar(unit=self._unit)
        cb_lay.addWidget(self._colorbar)
        body_lay.addWidget(colorbar_wrap)

        root.addWidget(body, stretch=1)

    def _build_scene(self):
        grid = gl.GLGridItem()
        grid.setSize(100, 100)
        grid.setSpacing(10, 10)
        grid.setColor((*[c/255 for c in bytes.fromhex(BORDER_COLOR.lstrip("#"))], 0.4))
        self._gl_view.addItem(grid)

        axis_len = 50
        for direction, color in [
            ([axis_len, 0, 0], (1.0, 0.35, 0.35, 0.8)),
            ([0, axis_len, 0], (0.35, 1.0, 0.35, 0.8)),
            ([0, 0, axis_len], (0.35, 0.35, 1.0, 0.8)),
        ]:
            pts = np.array([[0, 0, 0], direction], dtype=float)
            line = gl.GLLinePlotItem(pos=pts, color=color, width=2, antialias=True)
            self._gl_view.addItem(line)

        self._scatter = gl.GLScatterPlotItem(
            pos=np.zeros((1, 3)),
            color=np.ones((1, 4), dtype=np.float32),
            size=self._point_size,
            pxMode=True,
        )
        self._gl_view.addItem(self._scatter)

    def _load_demo_data(self):
        """加载 40 个演示传感器点位。"""
        rng  = np.random.default_rng(42)
        n    = 40
        xs   = rng.uniform(5, 90, n)
        ys   = rng.uniform(5, 90, n)
        zs   = rng.uniform(0, 50, n)
        vals = rng.uniform(20, 100, n)
        data = [
            {"id": f"S{i+1:03d}", "x": float(xs[i]), "y": float(ys[i]),
             "z": float(zs[i]), "value": float(vals[i]), "unit": self._unit}
            for i in range(n)
        ]
        self.update_plot(data)

    def update_plot(self, data: list[dict]):
        if not data:
            return
        self._data = data
        positions = np.array([[d["x"], d["y"], d["z"]] for d in data], dtype=np.float32)
        values    = np.array([d["value"] for d in data], dtype=np.float32)
        colors    = scalar_to_rgba(values)
        self._scatter.setData(pos=positions, color=colors, size=self._point_size)
        self._gl_view.set_sensor_data(data)
        self._stat_bar.update_stats(values)
        self._colorbar.update_range(float(values.min()), float(values.max()))

    def _on_point_hover(self, info: dict, gl_pos: QPoint):
        """将 GL 视图内的鼠标坐标映射到 FieldView3D 坐标，再显示悬浮卡。"""
        pos_in_self = self._gl_view.mapTo(self, gl_pos)
        self._hover_card.show_for(info, pos_in_self)
        self._hover_card.raise_()
