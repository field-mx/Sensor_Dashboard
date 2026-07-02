"""
data_manager.py
后端数据调度层。

职责：
  - 接入模拟数据源或真实硬件/网络数据源
  - 通过 Qt 信号向 UI 转发实时数据
  - 缓存最近一段历史数据，供趋势查询使用

真实数据源只要提供以下信号即可接入：
  - field_data_ready(dict)
  - voltage_data_ready(dict)
  - process_data_ready(dict)  可选
  - twin_data_ready(dict)     可选
"""
from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timedelta
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from .mock_generator import MockDataGenerator

HISTORY_DURATION_SEC: int = 300
EMIT_INTERVAL_MS: int = 1000
MAX_HISTORY_FRAMES: int = (HISTORY_DURATION_SEC * 1000) // EMIT_INTERVAL_MS


class DataManager(QObject):
    """统一数据源管理器。"""

    field_data_updated = pyqtSignal(dict)
    voltage_data_updated = pyqtSignal(dict)
    process_data_updated = pyqtSignal(dict)
    twin_data_updated = pyqtSignal(dict)
    source_status_changed = pyqtSignal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._field_history: deque[dict] = deque(maxlen=MAX_HISTORY_FRAMES)
        self._voltage_history: deque[dict] = deque(maxlen=MAX_HISTORY_FRAMES)
        self._process_history: deque[dict] = deque(maxlen=MAX_HISTORY_FRAMES)
        self._twin_history: deque[dict] = deque(maxlen=MAX_HISTORY_FRAMES)
        self._history_lock = threading.Lock()

        self._source: Optional[QObject] = None
        self._use_mock = False

    def start_mock_source(self) -> None:
        """启动内置模拟数据源。"""
        self._stop_current_source()
        source = MockDataGenerator(parent=None)
        self._connect_source(source)
        source.start()
        self._source = source
        self._use_mock = True
        self.source_status_changed.emit("mock_running")

    def set_hardware_source(self, hardware_source: QObject) -> None:
        """
        接入真实硬件或网络数据源。

        该方法只负责信号连接，不强制调用 start()；调用方可按自己的生命周期启动。
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
        source.field_data_ready.connect(self._on_field_data)
        source.voltage_data_ready.connect(self._on_voltage_data)
        if hasattr(source, "process_data_ready"):
            source.process_data_ready.connect(self._on_process_data)
        if hasattr(source, "twin_data_ready"):
            source.twin_data_ready.connect(self._on_twin_data)

    def _disconnect_source(self, source: QObject) -> None:
        for signal_name, slot in [
            ("field_data_ready", self._on_field_data),
            ("voltage_data_ready", self._on_voltage_data),
            ("process_data_ready", self._on_process_data),
            ("twin_data_ready", self._on_twin_data),
        ]:
            if not hasattr(source, signal_name):
                continue
            try:
                getattr(source, signal_name).disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    def _stop_current_source(self) -> None:
        if self._source is None:
            return
        self._disconnect_source(self._source)
        if self._use_mock and hasattr(self._source, "stop"):
            self._source.stop()
        self._source = None
        self._use_mock = False

    def _on_field_data(self, data: dict) -> None:
        with self._history_lock:
            self._field_history.append(data)
        self.field_data_updated.emit(data)

    def _on_voltage_data(self, data: dict) -> None:
        with self._history_lock:
            self._voltage_history.append(data)
        self.voltage_data_updated.emit(data)

    def _on_process_data(self, data: dict) -> None:
        with self._history_lock:
            self._process_history.append(data)
        self.process_data_updated.emit(data)

    def _on_twin_data(self, data: dict) -> None:
        with self._history_lock:
            self._twin_history.append(data)
        self.twin_data_updated.emit(data)

    def query_field_history(
        self,
        sensor_id: Optional[str] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
        z: Optional[int] = None,
        field_name: str = "temperature",
        duration_sec: int = HISTORY_DURATION_SEC,
    ) -> dict[str, list]:
        """按 sensor_id 或坐标查询历史场数据。"""
        resolved_id = self._resolve_sensor_id(sensor_id, x, y, z)
        cutoff = datetime.now() - timedelta(seconds=duration_sec)
        timestamps: list[datetime] = []
        values: list[float] = []

        with self._history_lock:
            history_snapshot = list(self._field_history)

        for frame in history_snapshot:
            ts = frame.get("timestamp")
            if isinstance(ts, datetime) and ts < cutoff:
                continue
            sensor_list: list[dict] = frame.get("fields", {}).get(field_name, [])
            for reading in sensor_list:
                if reading.get("sensor_id") == resolved_id:
                    timestamps.append(ts)
                    values.append(reading.get("value"))
                    break

        return {
            "timestamps": timestamps,
            "values": values,
            "sensor_id": resolved_id,
            "field_name": field_name,
        }

    def query_voltage_history(
        self,
        channel: str = "CH01",
        duration_sec: int = HISTORY_DURATION_SEC,
    ) -> dict[str, list]:
        """查询指定槽电压通道历史。"""
        cutoff = datetime.now() - timedelta(seconds=duration_sec)
        timestamps: list[datetime] = []
        values: list[float] = []
        temperatures: list[float] = []

        with self._history_lock:
            history_snapshot = list(self._voltage_history)

        for frame in history_snapshot:
            ts = frame.get("timestamp")
            if isinstance(ts, datetime) and ts < cutoff:
                continue
            voltage = frame.get("channels", {}).get(channel)
            temperature = frame.get("channel_temperatures", {}).get(channel)
            if temperature is None:
                temperature = frame.get("temperatures", {}).get(channel)
            if voltage is not None:
                timestamps.append(ts)
                values.append(voltage)
                temperatures.append(temperature)

        return {
            "timestamps": timestamps,
            "values": values,
            "temperatures": temperatures,
            "channel": channel,
        }

    def get_latest_field_frame(self) -> Optional[dict]:
        with self._history_lock:
            return self._field_history[-1] if self._field_history else None

    def get_latest_voltage_frame(self) -> Optional[dict]:
        with self._history_lock:
            return self._voltage_history[-1] if self._voltage_history else None

    def get_latest_process_frame(self) -> Optional[dict]:
        with self._history_lock:
            return self._process_history[-1] if self._process_history else None

    def get_latest_twin_frame(self) -> Optional[dict]:
        with self._history_lock:
            return self._twin_history[-1] if self._twin_history else None

    def clear_history(self) -> None:
        with self._history_lock:
            self._field_history.clear()
            self._voltage_history.clear()
            self._process_history.clear()
            self._twin_history.clear()

    @staticmethod
    def _resolve_sensor_id(
        sensor_id: Optional[str],
        x: Optional[int],
        y: Optional[int],
        z: Optional[int],
    ) -> str:
        if sensor_id is not None:
            return sensor_id
        if x is not None and y is not None and z is not None:
            return f"S_{x:02d}{y:02d}{z:02d}"
        raise ValueError("必须提供 sensor_id 或完整的 (x, y, z) 坐标。")

    @property
    def history_frame_count(self) -> int:
        with self._history_lock:
            return len(self._field_history)

    @property
    def is_running(self) -> bool:
        if self._source is None:
            return False
        if hasattr(self._source, "isRunning"):
            return self._source.isRunning()
        return True
