"""
本行用于测试，假设【】【】】【】支改好了，推送回主分支
再次测试，现在在测试分支中的语句
这一行是破坏测试：会在第一行加入乱码
main.py
应用入口
- 初始化 QApplication 并应用深色工业风样式
- 实例化 MainWindow
- 启动 QTimer 每 1000ms 向 VoltageView 推送 16 通道电压仿真数据（0–30V）
"""
import sys
import math
import numpy as np
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

from frontend.main_window import MainWindow
from frontend.styles import DARK_STYLE


# ── 1. 初始化应用 ────────────────────────────────────────────────────────────
app = QApplication(sys.argv)
app.setApplicationName("电解制备氢气监控系统")
app.setOrganizationName("Shanghai Electric")
app.setStyleSheet(DARK_STYLE)

# ── 2. 创建主窗口 ────────────────────────────────────────────────────────────
window = MainWindow()
window.show()


# ── 3. 假数据推送（模拟后端实时推送，16 通道，0–30V，每秒一帧）────────────
rng = np.random.default_rng(2024)
NUM_CHANNELS = 16
_phase = [0.0]   # 可变容器用于在闭包中更新


def _push_voltage():
    """每 1000 ms 向槽电压页面推送一帧新数据（16 通道，0–30V）。"""
    ph = _phase[0]
    data = {}
    for i in range(NUM_CHANNELS):
        phase_offset = i * (2 * math.pi / NUM_CHANNELS)
        # 每通道中心值在 5–25V 分布
        center = 5.0 + i * (20.0 / max(NUM_CHANNELS - 1, 1))
        v = center + 2.0 * math.sin(ph + phase_offset)
        v += float(rng.normal(0, 0.05))
        v = max(0.0, min(30.0, v))
        data[f"CH{i+1:02d}"] = round(v, 3)

    window.update_voltage(data)
    _phase[0] = (ph + 2 * math.pi / 5.0) % (2 * math.pi)  # 5 秒一个周期


timer = QTimer()
timer.timeout.connect(_push_voltage)
timer.start(1000)   # 1000 ms 刷新间隔


# ── 4. 运行事件循环 ──────────────────────────────────────────────────────────
sys.exit(app.exec())
