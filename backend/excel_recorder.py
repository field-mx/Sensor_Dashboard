"""后台 Excel 数据记录器。"""
from __future__ import annotations

import math
import os
import queue
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

AUTOSAVE_INTERVAL_SEC = 1.0
VOLTAGE_CHANNELS = [f"CH{index:02d}" for index in range(1, 17)]
PROCESS_COLUMNS = [
    ("oxygen", "氧气浓度", "vol"),
    ("hydrogen", "氢气浓度", "ppm"),
    ("temperature_1", "温度1", "mA"),
    ("temperature_2", "温度2", "mA"),
    ("pressure_1", "压力1", "mA"),
    ("pressure_2", "压力2", "mA"),
]
_STOP = object()


class ExcelDataRecorder(QObject):
    """把两组不同采样率的数据异步写入同一个 xlsx 文件。"""

    recording_started = pyqtSignal(str)
    recording_stopped = pyqtSignal(str)
    progress_ready = pyqtSignal(int, int)
    error_ready = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lock = threading.Lock()
        self._queue: queue.Queue[Any] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._path: Path | None = None
        self._recording = False

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    @pyqtSlot(str)
    def start(self, file_path: str) -> None:
        path = Path(file_path).expanduser()
        if path.suffix.lower() != ".xlsx":
            path = path.with_suffix(".xlsx")

        with self._lock:
            if self._recording:
                return
            self._queue = queue.Queue()
            self._path = path
            self._recording = True
            self._thread = threading.Thread(
                target=self._writer_loop,
                args=(path, self._queue),
                name="excel-data-recorder",
                daemon=True,
            )
            thread = self._thread
        thread.start()

    @pyqtSlot()
    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            if self._recording:
                self._recording = False
                self._queue.put(_STOP)
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join()

    @pyqtSlot(dict)
    def record_voltage_frame(self, frame: dict[str, Any]) -> None:
        timestamp = self._frame_timestamp(frame)
        channels = frame.get("channels") or {}
        temperatures = (
            frame.get("channel_temperatures")
            or frame.get("temperatures")
            or frame.get("channel_temperature")
            or {}
        )
        if not channels and not temperatures:
            return
        temperature_values = (
            [self._temperature_numeric(temperatures.get(ch)) for ch in VOLTAGE_CHANNELS]
            if temperatures
            else [None] * len(VOLTAGE_CHANNELS)
        )
        row = [
            timestamp,
            *[self._numeric(channels.get(ch)) for ch in VOLTAGE_CHANNELS],
            *temperature_values,
        ]
        self._enqueue(("voltage_temperature", [row]))

    @pyqtSlot(dict)
    def record_process_frame(self, frame: dict[str, Any]) -> None:
        sensors = frame.get("sensors", frame)
        if not isinstance(sensors, dict):
            return

        series = [
            self._sensor_series(sensors.get(key))
            for key, _label, _unit in PROCESS_COLUMNS
        ]
        sample_count = max((len(values) for values in series), default=0)
        if sample_count == 0:
            return

        timestamp = self._frame_timestamp(frame)
        rows: list[list[Any]] = []
        for sample_index in range(sample_count):
            row = [timestamp]
            for values in series:
                row.append(values[sample_index] if sample_index < len(values) else None)
            rows.append(row)
        self._enqueue(("process", rows))

    def _enqueue(self, item: Any) -> None:
        with self._lock:
            if not self._recording:
                return
            target_queue = self._queue
        target_queue.put(item)

    def _writer_loop(self, path: Path, target_queue: queue.Queue[Any]) -> None:
        voltage_rows = 0
        process_rows = 0
        dirty = False
        last_save = time.monotonic()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            workbook = self._create_workbook(path)
            self._atomic_save(workbook, path, voltage_rows, process_rows)
            self.recording_started.emit(str(path))

            while True:
                try:
                    item = target_queue.get(timeout=0.2)
                except queue.Empty:
                    item = None

                if item is _STOP:
                    break
                if item is not None:
                    kind, rows = item
                    sheet_name = "电压温度" if kind == "voltage_temperature" else "六通道传感器"
                    sheet = workbook[sheet_name]
                    for row in rows:
                        sheet.append(row)
                        self._format_data_row(sheet, sheet.max_row)
                    if kind == "voltage_temperature":
                        voltage_rows += len(rows)
                    else:
                        process_rows += len(rows)
                    dirty = True
                    self.progress_ready.emit(voltage_rows, process_rows)

                now = time.monotonic()
                if dirty and now - last_save >= AUTOSAVE_INTERVAL_SEC:
                    self._atomic_save(workbook, path, voltage_rows, process_rows)
                    dirty = False
                    last_save = now

            self._atomic_save(workbook, path, voltage_rows, process_rows)
            self.recording_stopped.emit(str(path))
        except Exception as exc:
            self.error_ready.emit(f"Excel 保存失败：{exc}")
        finally:
            with self._lock:
                if self._path == path:
                    self._recording = False
                    self._thread = None

    @staticmethod
    def _create_workbook(path: Path) -> Workbook:
        workbook = Workbook()
        workbook.remove(workbook.active)
        workbook.properties.creator = "Sensor Dashboard"
        workbook.properties.title = path.stem

        meta = workbook.create_sheet("记录信息")
        meta.append(["项目", "内容"])
        meta.append(["开始记录时间", datetime.now()])
        meta.append(["最近保存时间", datetime.now()])
        meta.append(["电压温度记录行数", 0])
        meta.append(["六通道记录行数", 0])
        meta.append(
            ["说明", "电压温度按接收频率记录；六通道每秒取最新采样并记录一次。"]
        )
        meta.append(
            ["ADC换算", "六通道数据均为换算电流：mA = 原始值 / 32768 × 5 / 100 × 1000"]
        )
        meta.append(
            [
                "气体浓度换算",
                "氢气电流≤4mA：O2=(O2电流-H2电流)×1.5625 vol，H2=0 ppm；"
                "氢气电流>4mA：O2=(O2电流-3.8)×1.5625 vol，"
                "H2=(H2电流-4)×62.5 ppm。",
            ]
        )

        voltage_sheet = workbook.create_sheet("电压温度")
        voltage_sheet.append(
            [
                "时间戳",
                *[f"{channel}电压 (V)" for channel in VOLTAGE_CHANNELS],
                *[f"{channel}温度 (°C)" for channel in VOLTAGE_CHANNELS],
            ]
        )

        process_sheet = workbook.create_sheet("六通道传感器")
        process_sheet.append(
            ["时间戳", *[f"{label} ({unit})" for _key, label, unit in PROCESS_COLUMNS]]
        )

        for sheet in workbook.worksheets:
            ExcelDataRecorder._style_sheet(sheet)
        return workbook

    @staticmethod
    def _style_sheet(sheet: Worksheet) -> None:
        header_fill = PatternFill("solid", fgColor="1F4E78")
        for cell in sheet[1]:
            cell.font = Font(name="Microsoft YaHei", bold=True, color="FFFFFF")
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        sheet.column_dimensions["A"].width = 24
        sheet.column_dimensions["B"].width = 18
        if sheet.title == "记录信息":
            sheet.column_dimensions["B"].width = 72
        for column in range(3, sheet.max_column + 1):
            sheet.column_dimensions[sheet.cell(1, column).column_letter].width = 14

    @staticmethod
    def _format_data_row(sheet: Worksheet, row_index: int) -> None:
        sheet.cell(row_index, 1).number_format = "yyyy-mm-dd hh:mm:ss.000"
        for cell in sheet[row_index]:
            cell.font = Font(name="Microsoft YaHei")

    @staticmethod
    def _atomic_save(
        workbook: Workbook,
        path: Path,
        voltage_rows: int,
        process_rows: int,
    ) -> None:
        meta = workbook["记录信息"]
        meta["B3"] = datetime.now()
        meta["B4"] = voltage_rows
        meta["B5"] = process_rows
        meta["B2"].number_format = "yyyy-mm-dd hh:mm:ss.000"
        meta["B3"].number_format = "yyyy-mm-dd hh:mm:ss.000"
        for sheet in workbook.worksheets:
            sheet.auto_filter.ref = sheet.dimensions

        temp_path = path.with_name(f".{path.stem}.{uuid.uuid4().hex}.tmp.xlsx")
        try:
            workbook.save(temp_path)
            os.replace(temp_path, path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    @staticmethod
    def _frame_timestamp(frame: dict[str, Any]) -> datetime:
        timestamp = frame.get("timestamp")
        if isinstance(timestamp, datetime):
            return timestamp
        if isinstance(timestamp, str):
            try:
                return datetime.fromisoformat(timestamp)
            except ValueError:
                pass
        return datetime.now()

    @staticmethod
    def _numeric(value: Any) -> float | int | None:
        if value is None:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        return numeric if math.isfinite(numeric) else None

    @staticmethod
    def _temperature_numeric(value: Any) -> float | int:
        numeric = ExcelDataRecorder._numeric(value)
        return numeric if numeric is not None else 0

    @staticmethod
    def _sensor_series(payload: Any) -> list[float | int | None]:
        if isinstance(payload, dict):
            values = payload.get("values")
            if isinstance(values, (list, tuple)) and values:
                return [ExcelDataRecorder._numeric(value) for value in values]
            value = payload.get("value")
        else:
            value = payload
        numeric = ExcelDataRecorder._numeric(value)
        return [numeric] if numeric is not None else []
