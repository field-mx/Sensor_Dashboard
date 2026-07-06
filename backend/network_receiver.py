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

也支持批量帧：
  {"voltage": {...}, "process": {...}, "twin": {...}, "field": {...}}
"""
from __future__ import annotations

import json
import re
import socket
from datetime import datetime
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal

_TEMP_VALUE_PATTERN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
_TEMP_VALUE_RE = re.compile(_TEMP_VALUE_PATTERN)


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
                sock.bind((self.host, self.port))
                sock.settimeout(0.5)
                while self._running:
                    try:
                        packet, _addr = sock.recvfrom(65535)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    self._handle_bytes(packet)
        except OSError as exc:
            self.error_ready.emit(f"UDP 监听失败：{exc}")
        finally:
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
        try:
            text = packet.decode("ascii").strip()
        except UnicodeDecodeError:
            try:
                text = packet.decode("utf-8").strip()
            except UnicodeDecodeError as exc:
                self.error_ready.emit(f"网络帧解码失败：{exc}")
                return
        if not text:
            return

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            if self._dispatch_ascii_temperature(text):
                return
            self.error_ready.emit(f"网络帧解析失败：{exc}")
            return
        self._dispatch(data)

    def _dispatch_ascii_temperature(self, text: str) -> bool:
        channel_temperatures = self._parse_pt100_temperatures(text)
        if not channel_temperatures:
            return False

        self.voltage_data_ready.emit(
            {
                "timestamp": datetime.now(),
                "channels": {},
                "channel_temperatures": channel_temperatures,
            }
        )
        return True

    @staticmethod
    def _parse_pt100_temperatures(text: str) -> dict[str, float]:
        parts = [part.strip() for part in text.replace("；", ";").split(",")]
        temp_index = None
        for index, part in enumerate(parts):
            if part.upper() == "TEMP":
                temp_index = index + 1
                break
        if temp_index is None:
            return {}

        values: list[float] = []
        for part in parts[temp_index:]:
            match = _TEMP_VALUE_RE.search(part)
            if match is None:
                continue
            values.append(round(float(match.group(0)), 1))
            if len(values) == 16:
                break

        if len(values) != 16:
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
