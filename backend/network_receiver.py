"""
network_receiver.py
TCP/UDP 网络数据源。

默认假设 Windows 前端作为监听端：
  - UDP:      采集板向 host:port 发送 JSON 数据报
  - TCP:      采集板连接 host:port 后发送换行分隔 JSON 帧

支持的帧格式：
  {"type": "voltage", "timestamp": "...", "channels": {...}, "channel_temperatures": {...}}
  {"type": "process", "timestamp": "...", "sensors": {...}}
  {"type": "twin", "timestamp": "...", "sensors": {...}, "warnings": [...], "recommendations": [...]}
  {"type": "field", "timestamp": "...", "fields": {...}}

也支持 udp(3) 使用的三端口报文：
  - 9090: 1536 字节，ADC0/ADC1/ADC2 各 16 通道交替排列
  - 9091: PT100 CSV 文本，读取 TEMP 后的 16 路温度
  - 9092: AI CSV 文本，读取 VALUE 后的 16 路电压

也支持批量帧：
  {"voltage": {...}, "process": {...}, "twin": {...}, "field": {...}}
"""
from __future__ import annotations

import csv
import io
import json
import socket
import struct
import time
from datetime import datetime
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal

AD7616_COUNT = 3
AD7616_CHANNELS = 16
AD7616_PROCESS_CHANNEL_COUNT = 6
AD7616_BYTES_PER_SAMPLE = 2
AD7616_BLOCK_BYTES = AD7616_CHANNELS * AD7616_BYTES_PER_SAMPLE
AD7616_BLOCKS_PER_PACKET = 48
AD7616_PACKET_BYTES = AD7616_BLOCK_BYTES * AD7616_BLOCKS_PER_PACKET
AD7616_SAMPLES_PER_PACKET = AD7616_BLOCKS_PER_PACKET // AD7616_COUNT
AD7616_EMIT_INTERVAL_SEC = 0.05
AD7616_MAX_PENDING_SAMPLES = 2048
AD7616_MAX_EMIT_SAMPLES = 512
AD7616_SOCKET_RCVBUF = 4 * 1024 * 1024
AD7616_PROCESS_CHANNELS = [
    (0, "oxygen", "O2-01"),
    (1, "hydrogen", "H2-01"),
    (2, "temperature_1", "T-01"),
    (3, "temperature_2", "T-02"),
    (4, "pressure_1", "P-01"),
    (5, "pressure_2", "P-02"),
]
ADC_REFERENCE_VOLTAGE = 5.0
ADC_CURRENT_RESISTANCE_OHM = 100.0
ADC_CURRENT_RANGE_A = (-0.05, 0.05)


def raw_count_to_current_a(channel_index: int, raw_count: float) -> float:
    """按 raw / 32768 * 5 / 100 将 AD7616 原始值换算为电流（A）。"""
    del channel_index
    actual_voltage = float(raw_count) / 32768.0 * ADC_REFERENCE_VOLTAGE
    return actual_voltage / ADC_CURRENT_RESISTANCE_OHM


def convert_process_channel_value(channel_index: int, raw_count: float) -> dict[str, Any]:
    """生成工况页需要的单通道电流数据。"""
    actual_voltage = float(raw_count) / 32768.0 * ADC_REFERENCE_VOLTAGE
    current_a = raw_count_to_current_a(channel_index, raw_count)
    lo, hi = ADC_CURRENT_RANGE_A
    status = "alarm" if current_a < lo or current_a > hi else "normal"
    return {
        "value": round(current_a, 6),
        "unit": "A",
        "decimals": 6,
        "normal": ADC_CURRENT_RANGE_A,
        "status": status,
        "raw_count": int(raw_count),
        "actual_voltage": round(actual_voltage, 6),
        "channel": channel_index + 1,
    }


class NetworkSensorSource(QThread):
    """兼容 DataManager 的网络数据源。"""

    field_data_ready = pyqtSignal(dict)
    voltage_data_ready = pyqtSignal(dict)
    process_data_ready = pyqtSignal(dict)
    twin_data_ready = pyqtSignal(dict)
    error_ready = pyqtSignal(str)

    def __init__(
        self,
        protocol: str = "udp",
        host: str = "0.0.0.0",
        port: int = 9000,
        parent=None,
    ):
        super().__init__(parent)
        self.protocol = protocol.lower()
        if self.protocol == "tcp":
            self.protocol = "tcp_server"
        self.host = host
        self.port = int(port)
        self._running = False
        self._sock: socket.socket | None = None
        self._client: socket.socket | None = None
        self._ad7616_pending_raw: list[list[int]] = [
            [] for _ in range(AD7616_PROCESS_CHANNEL_COUNT)
        ]
        self._ad7616_last_emit = 0.0

    def run(self) -> None:
        self._running = True
        if self.protocol == "udp":
            self._run_udp()
        elif self.protocol == "tcp_server":
            self._run_tcp_server()
        else:
            self.error_ready.emit(f"不支持的网络协议：{self.protocol}")

    def stop(self) -> None:
        self._running = False
        for sock in (self._client, self._sock):
            if sock is None:
                continue
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        self.wait(1500)

    def _run_udp(self) -> None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                self._sock = sock
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, AD7616_SOCKET_RCVBUF)
                sock.bind((self.host, self.port))
                sock.settimeout(0.05)
                while self._running:
                    try:
                        packet, _addr = sock.recvfrom(65535)
                    except socket.timeout:
                        self._flush_ad7616_pending(force=True)
                        continue
                    except OSError:
                        break
                    self._handle_bytes(packet)
        except OSError as exc:
            self.error_ready.emit(f"UDP 监听失败：{exc}")
        finally:
            self._flush_ad7616_pending(force=True)
            self._sock = None

    def _run_tcp_server(self) -> None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                self._sock = sock
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((self.host, self.port))
                sock.listen(1)
                sock.settimeout(0.5)
                while self._running:
                    try:
                        client, _addr = sock.accept()
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    with client:
                        self._client = client
                        client.settimeout(0.5)
                        self._read_tcp_client(client)
                        self._client = None
        except OSError as exc:
            self.error_ready.emit(f"TCP 监听失败：{exc}")
        finally:
            self._sock = None

    def _read_tcp_client(self, client: socket.socket) -> None:
        buffer = b""
        while self._running:
            try:
                chunk = client.recv(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if line.strip():
                    self._handle_bytes(line)
        if buffer.strip():
            self._handle_bytes(buffer)

    def _handle_bytes(self, packet: bytes) -> None:
        if self._looks_like_text_packet(packet):
            self._handle_text_packet(packet)
            return

        if self._dispatch_ad7616_process(packet):
            return

        self._handle_text_packet(packet)

    @staticmethod
    def _looks_like_text_packet(packet: bytes) -> bool:
        stripped = packet.lstrip()
        return stripped.startswith(
            (b"{", b"[", b"PT100", b"pt100", b"TEMP", b"temp", b"AI", b"ai")
        )

    def _handle_text_packet(self, packet: bytes) -> None:
        text: str | None = None
        try:
            text = packet.decode("ascii").strip()
        except UnicodeDecodeError:
            try:
                text = packet.decode("utf-8").strip()
            except UnicodeDecodeError as exc:
                text = None

        if text is None:
            self.error_ready.emit("网络帧解码失败：无法按 ASCII/UTF-8 解码，也不是 AD7616 数据包")
            return
        if not text:
            return

        self._handle_text_frame(text)

    def _handle_text_frame(self, text: str) -> None:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            if self._dispatch_csv_measurements(text):
                return
            self.error_ready.emit(f"网络帧解析失败：{exc}")
            return
        self._dispatch(data)

    def _dispatch_csv_measurements(self, text: str) -> bool:
        channel_temperatures = self._parse_pt100_temperatures(text)
        channels = self._parse_ai_values(text)
        if not channel_temperatures and not channels:
            return False

        self.voltage_data_ready.emit(
            {
                "timestamp": datetime.now(),
                "channels": channels,
                "channel_temperatures": channel_temperatures,
            }
        )
        return True

    def _dispatch_ad7616_process(self, packet: bytes) -> bool:
        raw_by_channel = self._parse_ad7616_raw_channels(packet)
        if not raw_by_channel:
            return False

        for channel_index, raw_values in enumerate(raw_by_channel):
            pending = self._ad7616_pending_raw[channel_index]
            pending.extend(raw_values)
            if len(pending) > AD7616_MAX_PENDING_SAMPLES:
                del pending[:-AD7616_MAX_PENDING_SAMPLES]

        self._flush_ad7616_pending(force=False)
        return True

    def _flush_ad7616_pending(self, force: bool = False) -> bool:
        if not any(self._ad7616_pending_raw):
            return False

        now = time.perf_counter()
        if not force and now - self._ad7616_last_emit < AD7616_EMIT_INTERVAL_SEC:
            return False

        raw_snapshot = [
            raw_values[-AD7616_MAX_EMIT_SAMPLES:]
            for raw_values in self._ad7616_pending_raw
        ]
        sensors = self._build_ad7616_process_sensors(raw_snapshot)
        self._ad7616_pending_raw = [[] for _ in range(AD7616_PROCESS_CHANNEL_COUNT)]
        self._ad7616_last_emit = now

        if not sensors:
            return False

        self.process_data_ready.emit({"timestamp": datetime.now(), "sensors": sensors})
        return True

    @staticmethod
    def _parse_ad7616_process(packet: bytes) -> dict[str, dict[str, Any]]:
        raw_by_channel = NetworkSensorSource._parse_ad7616_raw_channels(packet)
        return NetworkSensorSource._build_ad7616_process_sensors(raw_by_channel)

    @staticmethod
    def _parse_ad7616_raw_channels(packet: bytes) -> list[list[int]]:
        if len(packet) != AD7616_PACKET_BYTES:
            return []

        raw_by_channel: list[list[int]] = [[] for _ in range(AD7616_PROCESS_CHANNEL_COUNT)]
        try:
            for sample_index in range(AD7616_SAMPLES_PER_PACKET):
                # udp(3) 的块顺序为 ADC0、ADC1、ADC2 循环排列；
                # 工况页使用每组中第一片 ADC（界面 ADC0）的 CH1~CH6。
                adc0_offset = sample_index * AD7616_COUNT * AD7616_BLOCK_BYTES
                frame_first_six = struct.unpack_from("<6h", packet, adc0_offset)
                for channel_index, raw_value in enumerate(frame_first_six):
                    raw_by_channel[channel_index].append(raw_value)
        except struct.error:
            return []

        return raw_by_channel

    @staticmethod
    def _build_ad7616_process_sensors(
        raw_by_channel: list[list[int]],
    ) -> dict[str, dict[str, Any]]:
        sensors: dict[str, dict[str, Any]] = {}
        for channel_index, key, sensor_id in AD7616_PROCESS_CHANNELS:
            if channel_index >= len(raw_by_channel):
                continue
            raw_values = raw_by_channel[channel_index]
            if not raw_values:
                continue
            values = [
                round(raw_count_to_current_a(channel_index, raw_value), 6)
                for raw_value in raw_values
            ]
            payload = convert_process_channel_value(channel_index, raw_values[-1])
            payload["sensor_id"] = sensor_id
            payload["values"] = values
            payload["raw_values"] = raw_values
            payload["sample_count"] = len(raw_values)
            sensors[key] = payload
        return sensors

    @staticmethod
    def _parse_pt100_temperatures(text: str) -> dict[str, float]:
        return NetworkSensorSource._parse_csv_channel_values(text, "TEMP")

    @staticmethod
    def _parse_ai_values(text: str) -> dict[str, float]:
        return NetworkSensorSource._parse_csv_channel_values(text, "VALUE")

    @staticmethod
    def _parse_csv_channel_values(text: str, marker: str) -> dict[str, float]:
        try:
            row = next(csv.reader(io.StringIO(text)))
        except (csv.Error, StopIteration):
            return {}

        marker_index = next(
            (index for index, part in enumerate(row) if part.strip().upper() == marker),
            None,
        )
        if marker_index is None:
            return {}

        raw_values = row[marker_index + 1:marker_index + 17]
        if len(raw_values) != 16:
            return {}

        try:
            values = [float(value.strip()) for value in raw_values]
        except ValueError:
            return {}
        return {f"CH{index:02d}": value for index, value in enumerate(values, start=1)}

    def _dispatch(self, data: Any) -> None:
        if isinstance(data, list):
            for item in data:
                self._dispatch(item)
            return
        if not isinstance(data, dict):
            self.error_ready.emit("网络帧必须是 JSON object 或 object 数组。")
            return

        frame_type = str(data.get("type", "")).lower()
        if frame_type:
            payload = {k: v for k, v in data.items() if k != "type"}
            if frame_type in ("field", "fields"):
                self.field_data_ready.emit(payload)
            elif frame_type in ("voltage", "voltages"):
                self.voltage_data_ready.emit(payload)
            elif frame_type in ("process", "overview"):
                self.process_data_ready.emit(payload)
            elif frame_type in ("twin", "digital_twin", "model"):
                self.twin_data_ready.emit(payload)
            elif frame_type in ("batch", "frame"):
                self._dispatch(payload)
            else:
                self.error_ready.emit(f"未知数据帧类型：{frame_type}")
            return

        if "field" in data:
            self.field_data_ready.emit(data["field"])
        if "voltage" in data:
            self.voltage_data_ready.emit(data["voltage"])
        if "process" in data:
            self.process_data_ready.emit(data["process"])
        if "twin" in data:
            self.twin_data_ready.emit(data["twin"])

        if "fields" in data:
            self.field_data_ready.emit(data)
        if "channels" in data:
            self.voltage_data_ready.emit(data)
        if "sensors" in data:
            if "warnings" in data or "recommendations" in data or "model" in data:
                self.twin_data_ready.emit(data)
            else:
                self.process_data_ready.emit(data)
