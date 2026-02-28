"""
data_manager.py
---------------
核心调度类，继承自 QObject。

职责
----
1. 接入数据源（当前为 MockDataGenerator，预留真实硬件接口）。
2. 通过 pyqtSignal 向外广播实时场数据和槽电压数据。
3. 在内存中维护最近 5 分钟的历史数据（使用 collections.deque）。
4. 提供查询方法：按 sensor_id 或坐标 (x, y, z) 查询某传感器/通道
   的历史 [时间戳列表, 物理量列表]。

设计原则
--------
- 纯后端逻辑，不含任何 UI 元素。
- 所有对外通信均通过 pyqtSignal 完成。
- 硬件接口通过 set_hardware_source() 替换数据源，无需修改调度逻辑。
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timedelta
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from .mock_generator import MockDataGenerator


# ──────────────────────────────────────────────
# 可配置常量
# ──────────────────────────────────────────────
HISTORY_DURATION_SEC: int = 300        # 历史缓存时长（秒），默认 5 分钟
EMIT_INTERVAL_MS: int = 100            # 与生成器一致，100 ms/帧
MAX_HISTORY_FRAMES: int = (HISTORY_DURATION_SEC * 1000) // EMIT_INTERVAL_MS


# ──────────────────────────────────────────────
# DataManager
# ──────────────────────────────────────────────
class DataManager(QObject):
    """
    核心数据调度类。

    Signals
    -------
    field_data_updated(dict)
        实时场数据，原样转发来自数据源的 field_data_ready 信号。
        结构同 MockDataGenerator.field_data_ready。

    voltage_data_updated(dict)
        实时槽电压数据，原样转发来自数据源的 voltage_data_ready 信号。
        结构同 MockDataGenerator.voltage_data_ready。

    source_status_changed(str)
        数据源状态变化（"mock_running" / "hardware_running" / "stopped"）。
    """

    field_data_updated: pyqtSignal = pyqtSignal(dict)
    voltage_data_updated: pyqtSignal = pyqtSignal(dict)
    source_status_changed: pyqtSignal = pyqtSignal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)

        # ── 历史缓存 ────────────────────────────────
        # _field_history:   deque[ {timestamp, fields} ]，每帧一条
        # _voltage_history: deque[ {timestamp, channels} ]，每帧一条
        self._field_history: deque[dict] = deque(maxlen=MAX_HISTORY_FRAMES)
        self._voltage_history: deque[dict] = deque(maxlen=MAX_HISTORY_FRAMES)
        self._history_lock = threading.Lock()   # 跨线程读写保护

        # ── 数据源 ──────────────────────────────────
        self._source: Optional[MockDataGenerator] = None  # 当前激活数据源
        self._use_mock: bool = False

    # ══════════════════════════════════════════
    # 数据源管理
    # ══════════════════════════════════════════

    def start_mock_source(self) -> None:
        """
        启动内置模拟数据源（MockDataGenerator）。
        若之前已有数据源在运行，先停止。
        """
        self._stop_current_source()
        source = MockDataGenerator(parent=None)   # 不设 QObject parent，避免线程归属问题
        self._connect_source(source)
        source.start()
        self._source = source
        self._use_mock = True
        self.source_status_changed.emit("mock_running")

    def set_hardware_source(self, hardware_source: QObject) -> None:
        """
        接入真实硬件数据源（预留接口）。

        Parameters
        ----------
        hardware_source : QObject
            必须具备以下两个 pyqtSignal：
            - field_data_ready(dict)   —— 与 MockDataGenerator 格式一致
            - voltage_data_ready(dict) —— 与 MockDataGenerator 格式一致

            调用者负责在传入前完成硬件初始化，并在合适时机调用
            hardware_source.start()（若其为 QThread）。

        Notes
        -----
        此方法仅完成信号连接，不调用 start()，以便调用方灵活控制启动时机。
        调用 stop() 时会断开连接；硬件源的生命周期由调用方管理。
        """
        self._stop_current_source()
        self._connect_source(hardware_source)
        self._source = hardware_source
        self._use_mock = False
        self.source_status_changed.emit("hardware_running")

    def stop(self) -> None:
        """停止当前数据源。"""
        self._stop_current_source()
        self.source_status_changed.emit("stopped")

    def _connect_source(self, source: QObject) -> None:
        """将数据源的信号连接到本类的槽函数。"""
        source.field_data_ready.connect(self._on_field_data)
        source.voltage_data_ready.connect(self._on_voltage_data)

    def _disconnect_source(self, source: QObject) -> None:
        """断开数据源信号。"""
        try:
            source.field_data_ready.disconnect(self._on_field_data)
            source.voltage_data_ready.disconnect(self._on_voltage_data)
        except (RuntimeError, TypeError):
            pass  # 信号已断开或对象已销毁，忽略

    def _stop_current_source(self) -> None:
        if self._source is None:
            return
        self._disconnect_source(self._source)
        if self._use_mock and hasattr(self._source, "stop"):
            self._source.stop()   # MockDataGenerator.stop() 会 wait()
        self._source = None
        self._use_mock = False

    # ══════════════════════════════════════════
    # 内部槽函数（接收来自数据源的信号）
    # ══════════════════════════════════════════

    def _on_field_data(self, data: dict) -> None:
        """接收并缓存场数据，然后向外广播。"""
        with self._history_lock:
            self._field_history.append(data)
        self.field_data_updated.emit(data)

    def _on_voltage_data(self, data: dict) -> None:
        """接收并缓存槽电压数据，然后向外广播。"""
        with self._history_lock:
            self._voltage_history.append(data)
        self.voltage_data_updated.emit(data)

    # ══════════════════════════════════════════
    # 历史数据查询接口
    # ══════════════════════════════════════════

    def query_field_history(
        self,
        sensor_id: Optional[str] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
        z: Optional[int] = None,
        field_name: str = "temperature",
        duration_sec: int = HISTORY_DURATION_SEC,
    ) -> dict[str, list]:
        """
        查询指定传感器的场数据历史。

        可通过 sensor_id 或 (x, y, z) 坐标定位传感器（两者提供其一即可）。
        sensor_id 优先级高于坐标。

        Parameters
        ----------
        sensor_id : str, optional
            传感器唯一标识，格式与 MockDataGenerator 一致，例如 "S_000100"。
        x, y, z : int, optional
            传感器的 3D 网格坐标，当未提供 sensor_id 时使用。
        field_name : str
            目标场名称，可选：
            "temperature" | "h2_concentration" | "o2_concentration" | "pressure"
        duration_sec : int
            向前查询的时间窗口（秒），默认查询全部历史（最多 5 分钟）。

        Returns
        -------
        dict with keys:
            "timestamps" : list[datetime]  —— 时间戳列表
            "values"     : list[float]     —— 对应物理量列表
            "sensor_id"  : str             —— 实际查找的 sensor_id
            "field_name" : str             —— 查询的场名称

        Raises
        ------
        ValueError
            若无法确定 sensor_id 且坐标也不完整。
        """
        # 解析 sensor_id
        resolved_id = self._resolve_sensor_id(sensor_id, x, y, z)

        cutoff = datetime.now() - timedelta(seconds=duration_sec)
        timestamps: list[datetime] = []
        values: list[float] = []

        with self._history_lock:
            history_snapshot = list(self._field_history)

        for frame in history_snapshot:
            ts: datetime = frame["timestamp"]
            if ts < cutoff:
                continue
            sensor_list: list[dict] = frame.get("fields", {}).get(field_name, [])
            for reading in sensor_list:
                if reading["sensor_id"] == resolved_id:
                    timestamps.append(ts)
                    values.append(reading["value"])
                    break

        return {
            "timestamps": timestamps,
            "values": values,
            "sensor_id": resolved_id,
            "field_name": field_name,
        }

    def query_voltage_history(
        self,
        channel: str = "CH0",
        duration_sec: int = HISTORY_DURATION_SEC,
    ) -> dict[str, list]:
        """
        查询指定通道的槽电压历史。

        Parameters
        ----------
        channel : str
            通道名，格式 "CH0" … "CH7"。
        duration_sec : int
            向前查询的时间窗口（秒），默认查询全部历史。

        Returns
        -------
        dict with keys:
            "timestamps" : list[datetime]  —— 时间戳列表
            "values"     : list[float]     —— 对应电压值列表
            "channel"    : str             —— 查询的通道名
        """
        cutoff = datetime.now() - timedelta(seconds=duration_sec)
        timestamps: list[datetime] = []
        values: list[float] = []

        with self._history_lock:
            history_snapshot = list(self._voltage_history)

        for frame in history_snapshot:
            ts: datetime = frame["timestamp"]
            if ts < cutoff:
                continue
            voltage = frame.get("channels", {}).get(channel)
            if voltage is not None:
                timestamps.append(ts)
                values.append(voltage)

        return {
            "timestamps": timestamps,
            "values": values,
            "channel": channel,
        }

    def get_latest_field_frame(self) -> Optional[dict]:
        """返回最新一帧场数据，若无数据则返回 None。"""
        with self._history_lock:
            return self._field_history[-1] if self._field_history else None

    def get_latest_voltage_frame(self) -> Optional[dict]:
        """返回最新一帧槽电压数据，若无数据则返回 None。"""
        with self._history_lock:
            return self._voltage_history[-1] if self._voltage_history else None

    def clear_history(self) -> None:
        """清空所有历史缓存。"""
        with self._history_lock:
            self._field_history.clear()
            self._voltage_history.clear()

    # ══════════════════════════════════════════
    # 私有工具
    # ══════════════════════════════════════════

    @staticmethod
    def _resolve_sensor_id(
        sensor_id: Optional[str],
        x: Optional[int],
        y: Optional[int],
        z: Optional[int],
    ) -> str:
        """将 sensor_id 或 (x, y, z) 坐标解析为统一的 sensor_id 字符串。"""
        if sensor_id is not None:
            return sensor_id
        if x is not None and y is not None and z is not None:
            return f"S_{x:02d}{y:02d}{z:02d}"
        raise ValueError(
            "必须提供 sensor_id 或完整的 (x, y, z) 坐标以定位传感器。"
        )

    # ══════════════════════════════════════════
    # 属性
    # ══════════════════════════════════════════

    @property
    def history_frame_count(self) -> int:
        """当前缓存的历史帧数（场数据）。"""
        with self._history_lock:
            return len(self._field_history)

    @property
    def is_running(self) -> bool:
        """数据源是否正在运行。"""
        if self._source is None:
            return False
        if hasattr(self._source, "isRunning"):
            return self._source.isRunning()
        return True  # 非 QThread 的硬件源，由调用方负责管理状态
