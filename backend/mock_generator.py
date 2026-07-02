"""
mock_generator.py
-----------------
模拟数据生成器，继承自 QThread。
每 100 ms 产出一帧数据，包含：
  - 场数据（温度、氢气浓度、氧气浓度、压力）：3D 网格点 + 物理量
  - 槽电压数据：16 个通道的电压浮点数与每通道温度
  - 工况总览数据：氧气、氢气、两路温度、两路压力
  - 数字孪生数据：6 个传感器点位的模型状态、预警、运行建议

不包含任何 UI 元素；通过 pyqtSignal 向外广播数据。
"""

import time
import math
import random
from datetime import datetime

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal


# ──────────────────────────────────────────────
# 可配置常量
# ──────────────────────────────────────────────
EMIT_INTERVAL_MS: int = 1000         # 发射间隔（毫秒），1秒刷新一次
NUM_CHANNELS: int = 16               # 槽电压通道数（可扩展）

# 场数据网格尺寸（X × Y × Z）
GRID_X: int = 4
GRID_Y: int = 4
GRID_Z: int = 3

# 电压范围（0–30V，精确到 0.001V）
VOLTAGE_MIN: float = 0.0
VOLTAGE_MAX: float = 30.0
CHANNEL_TEMP_MIN: float = -20.0
CHANNEL_TEMP_MAX: float = 120.0

# 各场物理量的中心值与波动幅度
FIELD_CONFIGS: dict = {
    "temperature":   {"center": 70.0,  "amplitude": 5.0},   # °C
    "h2_concentration": {"center": 0.03, "amplitude": 0.01}, # 体积分数
    "o2_concentration": {"center": 0.20, "amplitude": 0.02}, # 体积分数
    "pressure":      {"center": 101.3, "amplitude": 2.0},   # kPa
}

PROCESS_SENSOR_CONFIGS: dict = {
    "oxygen": {
        "label": "氧气浓度",
        "sensor_id": "O2-01",
        "unit": "%Vol",
        "center": 20.6,
        "amplitude": 0.8,
        "normal": (18.0, 23.5),
    },
    "hydrogen": {
        "label": "氢气浓度",
        "sensor_id": "H2-01",
        "unit": "%Vol",
        "center": 2.7,
        "amplitude": 0.7,
        "normal": (0.0, 4.0),
    },
    "temperature_1": {
        "label": "温度 1",
        "sensor_id": "T-01",
        "unit": "°C",
        "center": 68.0,
        "amplitude": 4.0,
        "normal": (55.0, 80.0),
    },
    "temperature_2": {
        "label": "温度 2",
        "sensor_id": "T-02",
        "unit": "°C",
        "center": 70.0,
        "amplitude": 4.5,
        "normal": (55.0, 80.0),
    },
    "pressure_1": {
        "label": "压力 1",
        "sensor_id": "P-01",
        "unit": "kPa",
        "center": 101.5,
        "amplitude": 2.4,
        "normal": (95.0, 108.0),
    },
    "pressure_2": {
        "label": "压力 2",
        "sensor_id": "P-02",
        "unit": "kPa",
        "center": 101.0,
        "amplitude": 2.1,
        "normal": (95.0, 108.0),
    },
}


class MockDataGenerator(QThread):
    """
    模拟数据生成线程。

    Signals
    -------
    field_data_ready(dict)
        每帧场数据，结构：
        {
          "timestamp": datetime,
          "fields": {
            "temperature": [
              {"x": int, "y": int, "z": int, "sensor_id": str, "value": float},
              ...
            ],
            "h2_concentration": [...],
            "o2_concentration": [...],
            "pressure": [...]
          }
        }

    voltage_data_ready(dict)
        每帧槽电压数据，结构：
        {
          "timestamp": datetime,
          "channels": {
            "CH01": float, "CH02": float, ..., "CH16": float
          },
          "channel_temperatures": {
            "CH01": float, "CH02": float, ..., "CH16": float
          }
        }
    """

    field_data_ready: pyqtSignal = pyqtSignal(dict)
    voltage_data_ready: pyqtSignal = pyqtSignal(dict)
    process_data_ready: pyqtSignal = pyqtSignal(dict)
    twin_data_ready: pyqtSignal = pyqtSignal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._running: bool = False
        self._phase: float = 0.0          # 用于生成平滑波动的相位角
        self._grid_points: list[dict] = self._build_grid()

    # ──────────────────────────────────────────
    # 私有工具方法
    # ──────────────────────────────────────────
    def _build_grid(self) -> list[dict]:
        """预先构建 3D 网格坐标列表，每个元素包含 (x, y, z, sensor_id)。"""
        points = []
        for x in range(GRID_X):
            for y in range(GRID_Y):
                for z in range(GRID_Z):
                    sensor_id = f"S_{x:02d}{y:02d}{z:02d}"
                    points.append({"x": x, "y": y, "z": z, "sensor_id": sensor_id})
        return points

    def _generate_field_data(self) -> dict:
        """生成一帧场数据。"""
        ts = datetime.now()
        fields: dict[str, list] = {}

        for field_name, cfg in FIELD_CONFIGS.items():
            center = cfg["center"]
            amp = cfg["amplitude"]
            sensor_readings = []
            for pt in self._grid_points:
                # 空间相位偏移 + 时间相位，产生平滑时空变化
                spatial_offset = (pt["x"] * 0.5 + pt["y"] * 0.3 + pt["z"] * 0.7)
                value = (
                    center
                    + amp * math.sin(self._phase + spatial_offset)
                    + random.gauss(0, amp * 0.05)   # 少量高斯噪声
                )
                sensor_readings.append({
                    "x": pt["x"],
                    "y": pt["y"],
                    "z": pt["z"],
                    "sensor_id": pt["sensor_id"],
                    "value": round(value, 4),
                })
            fields[field_name] = sensor_readings

        return {"timestamp": ts, "fields": fields}

    def _generate_voltage_data(self) -> dict:
        """生成一帧槽电压数据（16 通道，0–30V）与通道温度（0.1°C）。"""
        ts = datetime.now()
        channels: dict[str, float] = {}
        channel_temperatures: dict[str, float] = {}
        for ch in range(NUM_CHANNELS):
            phase_offset = ch * (2 * math.pi / NUM_CHANNELS)  # 通道间相位均匀错开
            # 每通道中心电压在 5–25V 范围内分布，模拟不同槽的工作点
            center = 5.0 + ch * (20.0 / max(NUM_CHANNELS - 1, 1))
            voltage = (
                center
                + 2.0 * math.sin(self._phase + phase_offset)
                + random.gauss(0, 0.05)              # 少量高斯噪声
            )
            voltage = max(VOLTAGE_MIN, min(VOLTAGE_MAX, voltage))
            channels[f"CH{ch+1:02d}"] = round(voltage, 3)

            temp_center = 48.0 + ch * (18.0 / max(NUM_CHANNELS - 1, 1))
            temperature = (
                temp_center
                + 3.5 * math.sin(self._phase * 0.65 + phase_offset * 0.45)
                + random.gauss(0, 0.15)
            )
            temperature = max(CHANNEL_TEMP_MIN, min(CHANNEL_TEMP_MAX, temperature))
            channel_temperatures[f"CH{ch+1:02d}"] = round(temperature, 1)
        return {"timestamp": ts, "channels": channels, "channel_temperatures": channel_temperatures}

    @staticmethod
    def _status_for_value(value: float, normal: tuple[float, float]) -> str:
        low, high = normal
        if value < low or value > high:
            return "alarm"
        margin = (high - low) * 0.12
        if value < low + margin or value > high - margin:
            return "warning"
        return "normal"

    def _generate_process_data(self) -> dict:
        """生成工况总览数据：1 路氧、1 路氢、2 路温度、2 路压力。"""
        ts = datetime.now()
        sensors: dict[str, dict] = {}
        for i, (key, cfg) in enumerate(PROCESS_SENSOR_CONFIGS.items()):
            value = (
                cfg["center"]
                + cfg["amplitude"] * math.sin(self._phase + i * 0.58)
                + random.gauss(0, cfg["amplitude"] * 0.035)
            )
            status = self._status_for_value(value, cfg["normal"])
            decimals = 2 if cfg["unit"] == "%Vol" else 1
            sensors[key] = {
                "label": cfg["label"],
                "sensor_id": cfg["sensor_id"],
                "value": round(value, decimals),
                "unit": cfg["unit"],
                "status": status,
                "normal": cfg["normal"],
            }
        return {"timestamp": ts, "sensors": sensors}

    def _generate_twin_data(self, process_data: dict) -> dict:
        """生成数字孪生模型输出数据，真实后端可直接替换该结构。"""
        sensors = process_data.get("sensors", {})
        warnings = []
        recommendations = []

        for key, payload in sensors.items():
            status = payload.get("status")
            if status in ("warning", "alarm"):
                level_text = "报警" if status == "alarm" else "预警"
                warnings.append({
                    "sensor": payload.get("sensor_id", key),
                    "level": status,
                    "message": f"{payload.get('label', key)}触发{level_text}，当前 {payload.get('value')} {payload.get('unit', '')}",
                })

        temp_values = [
            sensors.get("temperature_1", {}).get("value"),
            sensors.get("temperature_2", {}).get("value"),
        ]
        temp_values = [float(v) for v in temp_values if isinstance(v, (int, float))]
        if temp_values and max(temp_values) > 76:
            recommendations.append({
                "level": "warning",
                "message": "建议提高冷却水流量，并检查堆体两端温差。",
            })
        else:
            recommendations.append({
                "level": "normal",
                "message": "当前热管理状态稳定，维持冷却设定。",
            })

        h2 = sensors.get("hydrogen", {}).get("value")
        if isinstance(h2, (int, float)) and h2 > 3.5:
            recommendations.append({
                "level": "warning",
                "message": "建议提高排风或降低短时负载，等待氢气浓度回落。",
            })

        if not warnings:
            warnings = []

        return {
            "timestamp": process_data.get("timestamp", datetime.now()),
            "sensors": sensors,
            "warnings": warnings,
            "recommendations": recommendations,
            "model": {"name": "electrolyzer_fault_advisor", "version": "mock-1.0"},
        }

    # ──────────────────────────────────────────
    # QThread 接口
    # ──────────────────────────────────────────
    def run(self) -> None:
        """线程主循环：每 100 ms 发射一帧数据。"""
        self._running = True
        interval_s = EMIT_INTERVAL_MS / 1000.0
        phase_increment = 2 * math.pi * interval_s / 5.0  # 5 秒完成一个完整周期

        while self._running:
            loop_start = time.perf_counter()

            # 生成并发射数据
            process_data = self._generate_process_data()
            self.field_data_ready.emit(self._generate_field_data())
            self.voltage_data_ready.emit(self._generate_voltage_data())
            self.process_data_ready.emit(process_data)
            self.twin_data_ready.emit(self._generate_twin_data(process_data))

            # 更新相位
            self._phase = (self._phase + phase_increment) % (2 * math.pi)

            # 精确休眠以维持 100 ms 间隔
            elapsed = time.perf_counter() - loop_start
            sleep_time = interval_s - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def stop(self) -> None:
        """请求停止线程并等待其退出。"""
        self._running = False
        self.wait()
